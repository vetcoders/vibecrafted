import Foundation

/// Host for one in-app update check and install.
///
/// Downloads the signed feed and payloads, verifies them through the established
/// owner, then exposes **Install Update**. Publication of a matching Runtime Pack
/// goes through the existing installer. A newer App is replaced by the script
/// helper. After that helper is admitted, this process must not cancel it.
/// Interrupt cancels owned download/verify/pack work only.
@MainActor
final class ProductUpdateCoordinator {
  struct Dependencies {
    var channel: () -> ProductUpdateChannel
    var installed: () -> ProductUpdateIdentity
    var stagingRoot: () -> URL
    var home: () -> URL
    var fetchBytes: (URL, @escaping ProductUpdateDataCompletion) -> ProductUpdateCancel
    var downloadFile: (URL, URL, @escaping ProductUpdateURLCompletion) -> ProductUpdateCancel
    var verifyFeedSignature:
      (Data, Data, URL, @escaping @MainActor @Sendable (Result<Void, Error>) -> Void) ->
      ProductUpdateCancel
    var verifyCandidate:
      (ProductUpdateCandidate, URL, Data, @escaping @MainActor @Sendable (Result<ProductUpdateProof, Error>) -> Void) ->
      () -> Void
    var installPack:
      (ProductUpdateCandidate, URL, @escaping @MainActor @Sendable (Result<ProductUpdateIdentity, Error>) -> Void) ->
      () -> Void
    var replaceApp:
      (ProductUpdateReplacementRequest, @escaping @MainActor @Sendable (Result<ProductUpdateReplacementAdmission, Error>) -> Void)
      -> () -> Void
    var extractApp: ((URL, URL, @escaping ProductUpdateURLCompletion) -> ProductUpdateCancel)?
    var closeUIAfterHelperArmed: () -> Void
    var checkTimeout: TimeInterval
  }

  private let dependencies: Dependencies
  private(set) var progress: ProductUpdateProgress
  private(set) var receipt: ProductUpdateTransactionReceipt
  private(set) var staged: ProductUpdateStagedTuple?
  private(set) var admittedHandoff: ProductUpdateHandoffRecord?
  private var generation: UInt64 = 0
  private var busy = false
  private var cancelInFlight: (() -> Void)?
  var onProgress: ((ProductUpdateProgress) -> Void)?

  init(dependencies: Dependencies) {
    self.dependencies = dependencies
    let installed = dependencies.installed()
    self.progress = deriveProductUpdateProgress(phase: .idle, installed: installed, candidate: nil)
    self.receipt = .start(installed: installed)
  }

  var isBusy: Bool { busy }
  var hasAdmittedHelperHandoff: Bool { receipt.helperAdmitted || admittedHandoff != nil }

  func checkForUpdates() {
    if busy { return }
    busy = true
    generation += 1
    let token = generation
    let installed = dependencies.installed()
    receipt = .start(installed: installed)
    staged = nil
    admittedHandoff = nil
    publish(deriveProductUpdateProgress(phase: .checking, installed: installed, candidate: nil))
    let channel = dependencies.channel()
    if let gap = channel.provisioningGap {
      finish(
        deriveProductUpdateProgress(
          phase: .unavailable, installed: installed, candidate: nil, detail: gap),
        token: token, terminal: .retained)
      return
    }
    guard let feed = channel.feedURL, let signatureURL = channel.signatureURL,
      let publicKey = channel.publicKeyURL
    else {
      finish(
        deriveProductUpdateProgress(
          phase: .unavailable, installed: installed, candidate: nil,
          detail: "The update feed is not reachable from this App. Your current version stays installed."),
        token: token, terminal: .retained)
      return
    }
    publish(
      deriveProductUpdateProgress(
        phase: .downloading, installed: installed, candidate: nil,
        detail: "Downloading the signed update list."))
    let cancelled = ProductUpdateCancelFlag()
    let cancelFeed = dependencies.fetchBytes(feed) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token, !cancelled.marked else { return }
        switch result {
        case .failure(let error):
          self.finish(
            deriveProductUpdateProgress(
              phase: .error, installed: installed, candidate: nil,
              detail: "The update list could not be downloaded. \(error.localizedDescription)"),
            token: token, terminal: .retained)
        case .success(let payload):
          self.receipt.boundary = .downloadedFeed
          self.fetchSignature(
            signatureURL, payload: payload, publicKey: publicKey, channel: channel,
            installed: installed, token: token)
        }
      }
    }
    armTimeout(token: token, installed: installed)
    cancelInFlight = {
      cancelled.mark()
      cancelFeed()
    }
  }

  func installUpdate() {
    guard progress.canInstall, let staged, !busy else { return }
    busy = true
    generation += 1
    let token = generation
    let installed = dependencies.installed()
    publish(
      deriveProductUpdateProgress(
        phase: .installing, installed: installed, candidate: staged.candidate))
    if productUpdateRunningAppMatchesCandidate(installed: installed, candidate: staged.candidate) {
      publishPack(staged, installed: installed, token: token)
      return
    }
    replaceRunningApp(staged, installed: installed, token: token)
  }

  /// Cancel owned download/verify/pack work. An admitted helper is not owned
  /// by this process and is not terminated here.
  func interrupt() {
    if hasAdmittedHelperHandoff {
      noteUIShutdownPreservingHandoff()
      return
    }
    guard busy else { return }
    let previous = dependencies.installed()
    let ownedStillLive = cancelInFlight != nil
    cancelInFlight?()
    cancelInFlight = nil
    generation += 1
    var next = receipt
    next.cancelledWhileOwnedProcessLive = ownedStillLive && (next.packPublished || next.appReplaced
      || next.boundary == .packPublishing || next.boundary == .appReplacing)
    next.terminal = .retained
    receipt = next
    let recovered = dependencies.installed()
    if productUpdateRetentionJustified(receipt: next, recovered: recovered, previous: previous) {
      finish(
        deriveProductUpdateProgress(
          phase: .retained, installed: recovered, candidate: staged?.candidate,
          detail:
            "The update was stopped. The previous working version \(next.installedGeneration) is still installed."),
        token: generation, terminal: .retained, alreadyFinished: true)
    } else {
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: recovered, candidate: staged?.candidate,
          detail:
            "The update was stopped while files were still changing. Open Check for Updates again to see the current version."),
        token: generation, terminal: .retained, alreadyFinished: true)
    }
  }

  func presentFinishing(candidate: ProductUpdateCandidate?) {
    let installed = dependencies.installed()
    publish(deriveProductUpdateProgress(phase: .finishing, installed: installed, candidate: candidate))
  }

  /// Persist the admitted helper handoff. Failure must not be ignored: the UI
  /// stays up so the helper is not abandoned without a durable record.
  func persistAdmittedHandoff() throws {
    guard let handoff = admittedHandoff else {
      throw ProductUpdateFeedError.malformed("no admitted update handoff to persist")
    }
    try writeProductUpdateHandoff(
      handoff, to: productUpdatePendingHandoffURL(home: dependencies.home()))
  }

  /// Normal UI shutdown after the helper was admitted. Does not terminate the helper.
  /// A failed persist keeps the current transaction and does not claim restarting.
  func noteUIShutdownPreservingHandoff() {
    cancelInFlight = nil
    do {
      try persistAdmittedHandoff()
    } catch {
      let installed = dependencies.installed()
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: staged?.candidate,
          detail:
            "Could not save the update handoff. The window stays open so the helper is not abandoned. \(error.localizedDescription)"),
        token: generation, terminal: .inFlight)
      return
    }
    if busy {
      let installed = dependencies.installed()
      finish(
        deriveProductUpdateProgress(
          phase: .restarting, installed: installed, candidate: staged?.candidate),
        token: generation, terminal: .inFlight)
    }
  }

  private func fetchSignature(
    _ signatureURL: URL,
    payload: Data,
    publicKey: URL,
    channel: ProductUpdateChannel,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    let cancelSig = dependencies.fetchBytes(signatureURL) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch result {
        case .failure(let error):
          self.finish(
            deriveProductUpdateProgress(
              phase: .error, installed: installed, candidate: nil,
              detail: "The update signature could not be downloaded. \(error.localizedDescription)"),
            token: token, terminal: .retained)
        case .success(let signature):
          self.verifyAndStage(
            payload: payload, signature: signature, publicKey: publicKey, channel: channel,
            installed: installed, token: token)
        }
      }
    }
    cancelInFlight = cancelSig
  }

  private func verifyAndStage(
    payload: Data,
    signature: Data,
    publicKey: URL,
    channel: ProductUpdateChannel,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    publish(
      deriveProductUpdateProgress(
        phase: .verifying, installed: installed, candidate: nil,
        detail: "Checking the update list signature."))
    let cancelSig = dependencies.verifyFeedSignature(payload, signature, publicKey) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch result {
        case .failure:
          self.finish(
            deriveProductUpdateProgress(
              phase: .refused, installed: installed, candidate: nil,
              detail: "The update list signature did not match the bundled signing key. Your current version stays installed."),
            token: token, terminal: .retained)
        case .success:
          self.stagePayloads(
            payload: payload, signature: signature, channel: channel, installed: installed,
            token: token)
        }
      }
    }
    cancelInFlight = cancelSig
  }

  private func stagePayloads(
    payload: Data,
    signature: Data,
    channel: ProductUpdateChannel,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    let candidate: ProductUpdateCandidate
    do {
      candidate = try decodeProductUpdateFeed(payload)
    } catch {
      finish(
        deriveProductUpdateProgress(
          phase: .refused, installed: installed, candidate: nil,
          detail: "The update list was not a signed release. Your current version stays installed."),
        token: token, terminal: .retained)
      return
    }
    guard let packRelative = productUpdateStagedRelativePath(candidate.packRelativePath),
      let appRelative = productUpdateStagedRelativePath(candidate.appRelativePath)
    else {
      finish(
        deriveProductUpdateProgress(
          phase: .refused, installed: installed, candidate: candidate,
          detail: "The update named a file path this App will not use as local authority."),
        token: token, terminal: .retained)
      return
    }
    publish(
      deriveProductUpdateProgress(
        phase: .downloading, installed: installed, candidate: candidate))
    let root = dependencies.stagingRoot()
    let staging = root.appendingPathComponent("product-update-\(candidate.generation)", isDirectory: true)
    do {
      try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
      try payload.write(to: staging.appendingPathComponent("release-output.json"), options: .atomic)
      try signature.write(to: staging.appendingPathComponent("release-output.json.sig"), options: .atomic)
    } catch {
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: candidate,
          detail: "Could not prepare a staging folder. Your current version stays installed."),
        token: token, terminal: .retained)
      return
    }
    guard let feed = channel.feedURL else {
      finish(
        deriveProductUpdateProgress(
          phase: .unavailable, installed: installed, candidate: candidate),
        token: token, terminal: .retained)
      return
    }
    let packURL = feed.deletingLastPathComponent().appendingPathComponent(packRelative)
    let appURL = feed.deletingLastPathComponent().appendingPathComponent(appRelative)
    let packDest = staging.appendingPathComponent(packRelative)
    let appDest = staging.appendingPathComponent(appRelative)
    let cancelPack = dependencies.downloadFile(packURL, packDest) { [weak self] packResult in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch packResult {
        case .failure(let error):
          self.finish(
            deriveProductUpdateProgress(
              phase: .error, installed: installed, candidate: candidate,
              detail: "The Runtime Pack could not be downloaded. \(error.localizedDescription)"),
            token: token, terminal: .retained)
        case .success(let packFile):
          let cancelApp = self.dependencies.downloadFile(appURL, appDest) { [weak self] appResult in
            Task { @MainActor in
              guard let self, self.generation == token else { return }
              switch appResult {
              case .failure(let error):
                self.finish(
                  deriveProductUpdateProgress(
                    phase: .error, installed: installed, candidate: candidate,
                    detail: "The app update could not be downloaded. \(error.localizedDescription)"),
                  token: token, terminal: .retained)
              case .success(let appFile):
                self.receipt.boundary = .downloadedPayloads
                self.verifyStaged(
                  candidate: candidate, staging: staging, pack: packFile, app: appFile,
                  payload: payload, installed: installed, token: token)
              }
            }
          }
          self.cancelInFlight = cancelApp
        }
      }
    }
    cancelInFlight = cancelPack
  }

  private func verifyStaged(
    candidate: ProductUpdateCandidate,
    staging: URL,
    pack: URL,
    app: URL,
    payload: Data,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    publish(
      deriveProductUpdateProgress(
        phase: .verifying, installed: installed, candidate: candidate))
    let beginVerify: (URL) -> Void = { [weak self] preparedApp in
      guard let self, self.generation == token else { return }
      let cancel = self.dependencies.verifyCandidate(candidate, staging, payload) { [weak self] result in
        Task { @MainActor in
          guard let self, self.generation == token else { return }
          switch result {
          case .failure(let error):
            self.finish(
              deriveProductUpdateProgress(
                phase: .refused, installed: installed, candidate: candidate,
                detail: "This update was refused. \(error.localizedDescription)"),
              token: token, terminal: .retained)
          case .success(let proof):
            switch admitProductUpdateCandidate(
              channel: self.dependencies.channel(), candidate: candidate, proof: proof)
            {
            case .unavailable(let reason):
              self.finish(
                deriveProductUpdateProgress(
                  phase: .unavailable, installed: installed, candidate: candidate, detail: reason),
                token: token, terminal: .retained)
            case .refuse(let reason):
              self.finish(
                deriveProductUpdateProgress(
                  phase: .refused, installed: installed, candidate: candidate, detail: reason),
                token: token, terminal: .retained)
            case .admit(let admitted):
              self.receipt.boundary = .verified
              self.receipt.candidateGeneration = admitted.generation
              self.staged = ProductUpdateStagedTuple(
                candidate: admitted, staging: staging, pack: pack, appOrDMG: preparedApp, proof: proof)
              if productUpdateClaimsHealthy(installed: installed, candidate: admitted) {
                self.finish(
                  deriveProductUpdateProgress(
                    phase: .success, installed: installed, candidate: admitted),
                  token: token, terminal: .committed)
                return
              }
              self.busy = false
              self.cancelInFlight = nil
              self.publish(
                deriveProductUpdateProgress(
                  phase: .ready, installed: installed, candidate: admitted))
            }
          }
        }
      }
      self.cancelInFlight = cancel
    }
    if app.pathExtension.lowercased() == "app" {
      beginVerify(app)
      return
    }
    guard let extract = dependencies.extractApp else {
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: candidate,
          detail: "The downloaded update is a disk image, but it could not be opened."),
        token: token, terminal: .retained)
      return
    }
    let extracted = staging.appendingPathComponent("Vibecrafted.app")
    let cancel = extract(app, extracted) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch result {
        case .failure(let error):
          self.finish(
            deriveProductUpdateProgress(
              phase: .error, installed: installed, candidate: candidate,
              detail: "The downloaded disk image could not be opened. \(error.localizedDescription)"),
            token: token, terminal: .retained)
        case .success(let prepared):
          beginVerify(prepared)
        }
      }
    }
    cancelInFlight = cancel
  }

  private func publishPack(
    _ staged: ProductUpdateStagedTuple,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    receipt.boundary = .packPublishing
    let cancel = dependencies.installPack(staged.candidate, staged.pack) { [weak self] outcome in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch outcome {
        case .failure(let error):
          self.receipt.packPublished = false
          let recovered = self.dependencies.installed()
          if productUpdateRetentionJustified(
            receipt: self.receipt.retainedCopy(), recovered: recovered, previous: installed)
          {
            self.finish(
              deriveProductUpdateProgress(
                phase: .retained, installed: recovered, candidate: staged.candidate,
                detail:
                  "The Runtime Pack was not published. The previous working version remains. \(error.localizedDescription)"),
              token: token, terminal: .retained)
          } else {
            self.finish(
              deriveProductUpdateProgress(
                phase: .error, installed: recovered, candidate: staged.candidate,
                detail: "The Runtime Pack install did not finish cleanly. \(error.localizedDescription)"),
              token: token, terminal: .retained)
          }
        case .success(let published):
          self.receipt.packPublished = true
          self.receipt.boundary = .packPublished
          if productUpdateClaimsHealthy(installed: published, candidate: staged.candidate) {
            self.finish(
              deriveProductUpdateProgress(
                phase: .success, installed: published, candidate: staged.candidate),
              token: token, terminal: .committed)
          } else {
            self.finish(
              deriveProductUpdateProgress(
                phase: .refused, installed: published, candidate: staged.candidate,
                detail:
                  "The installer did not leave matching app and Runtime Pack versions. The receipt remains the authority."),
              token: token, terminal: .retained)
          }
        }
      }
    }
    cancelInFlight = cancel
  }

  private func replaceRunningApp(
    _ staged: ProductUpdateStagedTuple,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    let extract = dependencies.extractApp
    let proceed: (URL) -> Void = { [weak self] sourceApp in
      guard let self, self.generation == token else { return }
      self.receipt.boundary = .appReplacing
      let identity = captureProductUpdateProcessIdentity(
        pid: ProcessInfo.processInfo.processIdentifier)
      let receiptURL = staged.staging.appendingPathComponent("replacement-receipt.json")
      let transactionID = UUID().uuidString.lowercased()
      let request = ProductUpdateReplacementRequest(
        waitPID: identity?.pid ?? ProcessInfo.processInfo.processIdentifier,
        waitStart: identity?.startTime,
        sourceApp: sourceApp,
        destinationApp: Bundle.main.bundleURL,
        relaunch: true,
        receiptURL: receiptURL,
        helperURL: self.dependencies.channel().helperURL,
        transactionURL: productUpdatePendingHandoffURL(home: self.dependencies.home()),
        admissionURL: URL(fileURLWithPath: receiptURL.path + ".admission.json"),
        journalURL: URL(fileURLWithPath: receiptURL.path + ".journal.json"),
        transactionID: transactionID,
        mode: .replace)
      var admitted = false
      let cancel = self.dependencies.replaceApp(request) { [weak self] outcome in
        Task { @MainActor in
          guard let self, self.generation == token else { return }
          switch outcome {
          case .failure(let error):
            self.receipt.appReplaced = false
            self.receipt.helperArmed = false
            self.receipt.helperAdmitted = false
            self.finish(
              deriveProductUpdateProgress(
                phase: .retained, installed: self.dependencies.installed(),
                candidate: staged.candidate,
                detail:
                  "The app could not be replaced. Your current version stays installed. \(error.localizedDescription)"),
              token: token, terminal: .retained)
          case .success(let admission):
            guard admission.ready else {
              self.receipt.appReplaced = false
              self.receipt.helperArmed = false
              self.receipt.helperAdmitted = false
              self.finish(
                deriveProductUpdateProgress(
                  phase: .retained, installed: self.dependencies.installed(),
                  candidate: staged.candidate,
                  detail:
                    "The update helper started but did not admit the replacement. Your current version stays installed."),
                token: token, terminal: .retained)
              return
            }
            admitted = true
            self.cancelInFlight = nil
            self.receipt.helperArmed = true
            self.receipt.helperAdmitted = true
            self.receipt.boundary = .helperReady
            self.receipt.helperPID = admission.helperPID
            self.receipt.receiptPath = admission.receiptURL.path
            self.receipt.transactionID = admission.transactionID
            self.receipt.journalPath = admission.journalURL?.path
            let helperStart =
              captureProductUpdateProcessIdentity(pid: admission.helperPID)?.startTime ?? ""
            let handoff = ProductUpdateHandoffRecord(
              schema: ProductUpdateHandoffRecord.schemaID,
              helperPID: admission.helperPID,
              helperStart: helperStart,
              waitPID: request.waitPID ?? ProcessInfo.processInfo.processIdentifier,
              waitStart: request.waitStart ?? "",
              receiptURL: admission.receiptURL.path,
              admissionURL: admission.admissionURL.path,
              journalURL: admission.journalURL?.path ?? productUpdateJournalURL(for: request).path,
              destination: request.destinationApp.path,
              candidateGeneration: staged.candidate.generation,
              installedGeneration: installed.packGeneration ?? installed.appGeneration,
              capturePath: nil,
              packURL: staged.pack.path,
              sourceRevision: staged.candidate.sourceRevision,
              terminalRevision: staged.candidate.terminalRevision,
              frameRevision: staged.candidate.frameRevision,
              transactionID: admission.transactionID,
              mode: request.mode.rawValue,
              phase: "helper_ready",
              candidateIdentity: admission.candidateIdentity,
              priorIdentity: productUpdateContentIdentityToken(at: request.destinationApp) ?? "")
            self.admittedHandoff = handoff
            do {
              try self.persistAdmittedHandoff()
            } catch {
              self.finish(
                deriveProductUpdateProgress(
                  phase: .error, installed: self.dependencies.installed(),
                  candidate: staged.candidate,
                  detail:
                    "Could not save the update handoff. The window stays open so the helper is not abandoned. \(error.localizedDescription)"),
                token: token, terminal: .inFlight)
              return
            }
            var closing = self.progress
            closing.willCloseUIForReplacement = true
            closing.phase = .restarting
            closing.summary =
              "Installing \(staged.candidate.generation). The window will close and reopen. Frame, terminals, agents and sessions stay running."
            self.publish(closing)
            self.dependencies.closeUIAfterHelperArmed()
            self.finish(
              deriveProductUpdateProgress(
                phase: .restarting, installed: installed, candidate: staged.candidate),
              token: token, terminal: .inFlight)
          }
        }
      }
      if !admitted {
        self.cancelInFlight = cancel
      }
    }
    if staged.appOrDMG.pathExtension.lowercased() == "app" {
      proceed(staged.appOrDMG)
      return
    }
    guard let extract else {
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: staged.candidate,
          detail: "The downloaded update is a disk image, but it could not be opened."),
        token: token, terminal: .retained)
      return
    }
    let extracted = staged.staging.appendingPathComponent("Vibecrafted.app")
    let cancel = extract(staged.appOrDMG, extracted) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        switch result {
        case .failure(let error):
          self.finish(
            deriveProductUpdateProgress(
              phase: .error, installed: installed, candidate: staged.candidate,
              detail: "The downloaded disk image could not be opened. \(error.localizedDescription)"),
            token: token, terminal: .retained)
        case .success(let app):
          proceed(app)
        }
      }
    }
    cancelInFlight = cancel
  }

  private func armTimeout(token: UInt64, installed: ProductUpdateIdentity) {
    let timeout = dependencies.checkTimeout
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak self] in
      guard let self, self.generation == token, self.busy else { return }
      if self.hasAdmittedHelperHandoff { return }
      self.interrupt()
      if self.progress.phase == .retained || self.progress.phase == .error { return }
      self.generation += 1
      self.finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: nil,
          detail: "The update did not answer in time. Your current version stays installed."),
        token: self.generation, terminal: .retained)
    }
  }

  private func publish(_ value: ProductUpdateProgress) {
    progress = value
    onProgress?(value)
  }

  private func finish(
    _ value: ProductUpdateProgress,
    token: UInt64,
    terminal: ProductUpdateTransactionTerminal,
    alreadyFinished: Bool = false
  ) {
    guard generation == token else { return }
    if !hasAdmittedHelperHandoff {
      cancelInFlight = nil
    }
    busy = false
    receipt.terminal = terminal
    if terminal == .committed {
      receipt.boundary = .committed
    }
    publish(value)
  }
}

struct ProductUpdateStagedTuple: Equatable, Sendable {
  var candidate: ProductUpdateCandidate
  var staging: URL
  var pack: URL
  var appOrDMG: URL
  var proof: ProductUpdateProof
}

private extension ProductUpdateTransactionReceipt {
  func retainedCopy() -> ProductUpdateTransactionReceipt {
    var copy = self
    copy.terminal = .retained
    return copy
  }
}
