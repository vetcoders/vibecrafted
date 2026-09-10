import Foundation

/// W1 authored contract, NOT_ASSESSED under COMPILE_EMBARGO.
/// Compiles with the product-update Foundation owner — not a copied imitation.
@main
@MainActor
struct ProductUpdatePolicyTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func wait(seconds: TimeInterval = 2, until condition: @escaping () -> Bool) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while !condition() {
      guard Date() < deadline else { throw Failure(message: "timed out waiting for update coordinator") }
      RunLoop.current.run(until: Date().addingTimeInterval(0.01))
    }
  }

  static let source = "0a5eaaea607d07c3dcac1bb321c502d41273e1e0"
  static let generation = "4.4.0+g0a5eaaea"
  static let previous = "4.3.1+ge37be2c9"
  static let terminal = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  static let frame = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

  static func provisionedChannel() -> ProductUpdateChannel {
    let key = writeTemp("vibecrafted-signing-v1.pub", Data("not-a-real-key\n".utf8))
    return resolveProductUpdateChannel(
      feedURLString: "https://updates.example.test/release-output.json",
      publicKeyURL: key,
      helperURL: URL(fileURLWithPath: "/usr/bin/true"))
  }

  static func candidate(
    generation: String = generation,
    sourceRevision: String = source,
    terminalRevision: String = terminal,
    frameRevision: String = frame,
    keyID: String = productUpdateExpectedKeyID
  ) -> ProductUpdateCandidate {
    ProductUpdateCandidate(
      generation: generation,
      sourceRevision: sourceRevision,
      terminalRevision: terminalRevision,
      frameRevision: frameRevision,
      keyID: keyID,
      algorithm: productUpdateExpectedAlgorithm,
      spkiSHA256: productUpdateExpectedSPKI,
      packRelativePath: "Vibecrafted_RuntimePack_4.4.0.tar.gz",
      appRelativePath: "Vibecrafted_4.4.0.dmg",
      packSHA256: String(repeating: "ab", count: 32),
      appSHA256: String(repeating: "cd", count: 32),
      packSize: 16,
      appSize: 16)
  }

  static func validProof(
    source: String = source,
    terminal: String = terminal,
    frame: String = frame
  ) -> ProductUpdateProof {
    ProductUpdateProof(
      signatureVerifiedOverExactBytes: true,
      verifierOwner: productUpdateExpectedVerifierOwner,
      payloadHashesMatch: true,
      codesignIdentifier: productUpdateExpectedBundleIdentifier,
      codesignTeamID: productUpdateExpectedTeamID,
      notarizedAndStapled: true,
      packIdentityMatches: true,
      observedSourceRevision: source,
      observedTerminalRevision: terminal,
      observedFrameRevision: frame)
  }

  static func helperScript() throws -> URL {
    guard let url = productUpdateRepositoryHelperScript() else {
      throw Failure(message: "scripts/vc-app-update.sh is missing")
    }
    return url
  }

  static func matchingInstalled(pack: String? = generation) -> ProductUpdateIdentity {
    ProductUpdateIdentity(
      appGeneration: generation, packGeneration: pack, sourceRevision: source,
      terminalRevision: terminal, frameRevision: frame)
  }

  static func previousInstalled() -> ProductUpdateIdentity {
    ProductUpdateIdentity(
      appGeneration: previous, packGeneration: previous,
      sourceRevision: "e37be2c9fb1c5f23d749fbdd786cd08cdf888617")
  }

  static func writeTemp(_ name: String, _ data: Data) -> URL {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-update-\(UUID().uuidString)-\(name)")
    try! data.write(to: url)
    return url
  }

  static func feedJSON(
    signatureValid: Bool = true,
    ticket: Bool = true,
    bundle: String? = "io.vetcoders.codescribe",
    sourceRevision: String = source,
    terminalRevision: String = terminal,
    frameRevision: String = frame
  ) -> Data {
    let bundleField = bundle.map { ", \"bundle_identifier\": \"\($0)\"" } ?? ""
    return """
      {
        "schema": "io.vetcoders.vibecrafted.release-output.v1",
        "signature_policy": {"algorithm": "rsa-pkcs1v15-sha256", "key_id": "vibecrafted-signing-v1", "spki_sha256": "\(productUpdateExpectedSPKI)"},
        "product": {"version": "4.4.0"},
        "source_revisions": {"vibecrafted": "\(sourceRevision)", "vc-terminal": "\(terminalRevision)", "vc-frame": "\(frameRevision)"},
        "runtime_pack": {"path": "Vibecrafted_RuntimePack_4.4.0.tar.gz", "sha256": "\(String(repeating: "ab", count: 32))", "size": 16},
        "dmg": {"path": "Vibecrafted_4.4.0.dmg", "sha256": "\(String(repeating: "cd", count: 32))", "size": 16},
        "notarization": {"app": {"ticket": \(ticket), "gatekeeper": \(ticket)}, "dmg": {"ticket": \(ticket), "gatekeeper": \(ticket)}},
        "signature_valid": \(signatureValid)
        \(bundleField)
      }
      """.data(using: .utf8)!
  }

  static func testStagedRelativePathRejectsBuildAuthority() throws {
    try require(
      productUpdateStagedRelativePath("/tmp/Vibecrafted.app") == nil,
      "absolute path became local authority")
    try require(
      productUpdateStagedRelativePath("../Vibecrafted.app") == nil,
      "parent path became local authority")
    try require(
      productUpdateStagedRelativePath("Vibecrafted_4.4.0.dmg") == "Vibecrafted_4.4.0.dmg",
      "signed relative dmg locator was refused")
  }

  static func testMissingFeedIsUnavailable() throws {
    let channel = resolveProductUpdateChannel(
      feedURLString: nil, publicKeyURL: writeTemp("key.pub", Data("k".utf8)), helperURL: nil)
    try require(channel.feedURL == nil, "empty feed became a URL")
    switch admitProductUpdateCandidate(channel: channel, candidate: candidate(), proof: validProof()) {
    case .unavailable(let reason):
      try require(reason.contains("not configured") || reason.contains("not available"), reason)
    default:
      throw Failure(message: "missing feed was not unavailable")
    }
  }

  static func testHTTPFeedIsRejected() throws {
    let key = writeTemp("key.pub", Data("k".utf8))
    let channel = resolveProductUpdateChannel(
      feedURLString: "http://updates.example.test/release-output.json",
      publicKeyURL: key, helperURL: nil)
    try require(channel.feedURL == nil, "http feed was accepted in production")
  }

  static func testFixtureFileURLRequiresExplicitEnv() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-update-fixture-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try Data("{}".utf8).write(to: root.appendingPathComponent("release-output.json"))
    try Data(repeating: 1, count: 256).write(to: root.appendingPathComponent("release-output.json.sig"))
    let key = writeTemp("key.pub", Data("k".utf8))
    let denied = resolveProductUpdateChannel(
      feedURLString: nil, publicKeyURL: key, helperURL: nil,
      environment: [productUpdateFixtureRootKey: root.path])
    try require(denied.feedURL == nil, "fixture root leaked into production")
    let allowed = resolveProductUpdateChannel(
      feedURLString: nil, publicKeyURL: key, helperURL: nil,
      environment: [
        productUpdateFixtureFlag: "1",
        productUpdateFixtureRootKey: root.path,
      ])
    try require(allowed.feedURL?.isFileURL == true, "explicit fixture did not admit file URL")
    try require(allowed.fixtureAllowed, "fixture flag was ignored")
  }

  static func testForgedSignatureValidAndTicketsAreIgnored() throws {
    let forged = try decodeProductUpdateFeed(feedJSON(signatureValid: true, ticket: true))
    try require(forged.keyID == productUpdateExpectedKeyID, "key id lost")
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: nil)
    {
    case .refuse(let reason):
      try require(reason.contains("not verified") || reason.contains("signing"), reason)
    default:
      throw Failure(message: "forged signature_valid without proof was admitted")
    }
    var wrongOwner = validProof()
    wrongOwner.verifierOwner = "self-attested"
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: wrongOwner)
    {
    case .refuse:
      break
    default:
      throw Failure(message: "a non-owner verifier proof was admitted")
    }
    var unsigned = ProductUpdateProof.unsigned()
    unsigned.signatureVerifiedOverExactBytes = false
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: unsigned)
    {
    case .refuse:
      break
    default:
      throw Failure(message: "unsigned proof was admitted because JSON said signature_valid")
    }
    var ticketsOnly = validProof()
    ticketsOnly.notarizedAndStapled = false
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: ticketsOnly)
    {
    case .refuse(let reason):
      try require(reason.contains("notarized"), reason)
    default:
      throw Failure(message: "JSON ticket booleans were treated as notarization")
    }
    var foreign = validProof()
    foreign.codesignIdentifier = "io.vetcoders.codescribe"
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: foreign)
    {
    case .refuse:
      break
    default:
      throw Failure(message: "JSON bundle_identifier defaulted the identity")
    }
    var foreignTeam = validProof()
    foreignTeam.codesignTeamID = "XXXXXXXXXX"
    switch admitProductUpdateCandidate(
      channel: provisionedChannel(), candidate: forged, proof: foreignTeam)
    {
    case .refuse:
      break
    default:
      throw Failure(message: "a foreign Team ID was admitted")
    }
  }

  static func testTamperedSignatureBytesAreRefused() throws {
    let payload = feedJSON()
    let bogus = Data(repeating: 7, count: 256)
    let key = writeTemp("key.pub", Data("-----BEGIN PUBLIC KEY-----\nMIIB\n-----END PUBLIC KEY-----\n".utf8))
    switch verifyDetachedReleaseSignature(payload: payload, signature: bogus, publicKeyPath: key.path)
    {
    case .failure(.signatureInvalid), .failure(.opensslUnavailable), .failure(.missingPublicKey):
      break
    case .failure(let other):
      throw Failure(message: "unexpected trust error \(other)")
    case .success:
      throw Failure(message: "random 256-byte signature verified")
    }
    let wrongSize = Data(repeating: 1, count: 16)
    switch verifyDetachedReleaseSignature(payload: payload, signature: wrongSize, publicKeyPath: key.path)
    {
    case .failure(.invalidSignatureSize):
      break
    default:
      throw Failure(message: "wrong-size signature was not rejected before openssl")
    }
  }

  static func testMixedDonorRevisionsAreRefused() throws {
    let admitted = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(),
      proof: validProof(terminal: "cccccccccccccccccccccccccccccccccccccccc"))
    guard case .refuse(let reason) = admitted else {
      throw Failure(message: "mixed terminal donor was admitted")
    }
    try require(reason.contains("Terminal") || reason.contains("does not match"), reason)
    let mixedFrame = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(),
      proof: validProof(frame: "dddddddddddddddddddddddddddddddddddddddd"))
    guard case .refuse = mixedFrame else {
      throw Failure(message: "mixed Frame donor was admitted")
    }
    let mixedSource = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(),
      proof: validProof(source: "ffffffffffffffffffffffffffffffffffffffff"))
    guard case .refuse = mixedSource else {
      throw Failure(message: "mixed vibecrafted donor was admitted")
    }
  }

  static func testIdentityMismatchIsRefused() throws {
    let admitted = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(generation: "4.4.0+gdeadbeef", sourceRevision: source),
      proof: validProof())
    guard case .refuse = admitted else {
      throw Failure(message: "mismatched App/pack identity was admitted")
    }
  }

  static func testHealthyRequiresMatchingIdentity() throws {
    try require(
      productUpdateClaimsHealthy(installed: matchingInstalled(), candidate: candidate()),
      "matching identity was not healthy")
    try require(
      !productUpdateClaimsHealthy(installed: previousInstalled(), candidate: candidate()),
      "mixed generation claimed healthy")
  }

  static func testRetentionRequiresReceiptAndRecoveredState() throws {
    let previous = previousInstalled()
    try require(
      !productUpdateRetentionJustified(receipt: nil, recovered: previous, previous: previous),
      "nil receipt was treated as retained")
    var cancelledOnly = ProductUpdateTransactionReceipt.start(installed: previous)
    cancelledOnly.terminal = .inFlight
    cancelledOnly.cancelledWhileOwnedProcessLive = true
    try require(
      !productUpdateRetentionJustified(
        receipt: cancelledOnly, recovered: previous, previous: previous),
      "in-flight cancel was treated as retained")
    var published = cancelledOnly
    published.terminal = .retained
    published.packPublished = true
    published.cancelledWhileOwnedProcessLive = true
    try require(
      !productUpdateRetentionJustified(
        receipt: published, recovered: matchingInstalled(), previous: previous),
      "published mix was reported retained")
    var clean = ProductUpdateTransactionReceipt.start(installed: previous)
    clean.terminal = .retained
    try require(
      productUpdateRetentionJustified(receipt: clean, recovered: previous, previous: previous),
      "verified unchanged receipt was not retained")
  }

  static func makeCoordinator(
    channel: @escaping () -> ProductUpdateChannel = { provisionedChannel() },
    installed: @escaping () -> ProductUpdateIdentity = { previousInstalled() },
    feed: Data = feedJSON(),
    signature: Data = Data(repeating: 3, count: 256),
    proof: ProductUpdateProof = validProof(),
    verifySignature: Result<Void, Error> = .success(()),
    installPack: @escaping (ProductUpdateCandidate, URL, (Result<ProductUpdateIdentity, Error>) -> Void) -> Void = { _, _, _ in
      fatalError("pack must not publish")
    },
    replaceApp: @escaping (ProductUpdateReplacementRequest, (Result<ProductUpdateReplacementAdmission, Error>) -> Void) -> Void = { _, _ in
      fatalError("app must not replace")
    },
    closeUI: @escaping () -> Void = {},
    timeout: TimeInterval = 15,
    homeURL: URL? = nil
  ) -> ProductUpdateCoordinator {
    let staging = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-update-stage-\(UUID().uuidString)", isDirectory: true)
    let home: URL
    if let homeURL {
      home = homeURL
    } else {
      home = FileManager.default.temporaryDirectory.appendingPathComponent(
        "vc-update-home-\(UUID().uuidString)", isDirectory: true)
      try! FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    }
    try! FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
    let pack = staging.appendingPathComponent("pack.tar.gz")
    let app = staging.appendingPathComponent("Vibecrafted.app")
    try! Data(repeating: 9, count: 16).write(to: pack)
    try! FileManager.default.createDirectory(at: app, withIntermediateDirectories: true)
    return ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: channel,
        installed: installed,
        stagingRoot: { staging },
        home: { home },
        fetchBytes: { url, completion in
          if url.path.hasSuffix(".sig") || url.lastPathComponent.hasSuffix(".sig") {
            completion(.success(signature))
          } else {
            completion(.success(feed))
          }
          return {}
        },
        downloadFile: { _, destination, completion in
          try? FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
          if destination.pathExtension == "app" {
            try? FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
          } else {
            try? Data(repeating: 9, count: 16).write(to: destination)
          }
          completion(.success(destination))
          return {}
        },
        verifyFeedSignature: { _, _, _, completion in
          completion(verifySignature)
          return {}
        },
        verifyCandidate: { _, _, _, completion in
          completion(.success(proof))
          return {}
        },
        installPack: { candidate, url, completion in
          installPack(candidate, url, completion)
          return {}
        },
        replaceApp: { request, completion in
          replaceApp(request, completion)
          return {}
        },
        extractApp: { _, destination, completion in
          try? FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
          completion(.success(destination))
          return {}
        },
        closeUIAfterHelperArmed: closeUI,
        checkTimeout: timeout))
  }

  static func testCoordinatorMissingFeedNeverInstalls() throws {
    var packs = 0
    let coordinator = makeCoordinator(
      channel: {
        resolveProductUpdateChannel(
          feedURLString: nil, publicKeyURL: writeTemp("k.pub", Data("k".utf8)), helperURL: nil)
      },
      installPack: { _, _, _ in packs += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .unavailable }
    try require(packs == 0, "missing feed reached the installer")
    try require(!coordinator.progress.claimsHealthy, "unavailable claimed healthy")
    try require(!coordinator.progress.canInstall, "unavailable offered Install Update")
  }

  static func testCoordinatorForgedFeedNeverInstalls() throws {
    var packs = 0
    var replaces = 0
    let coordinator = makeCoordinator(
      verifySignature: .failure(ProductUpdateTrustError.signatureInvalid),
      installPack: { _, _, _ in packs += 1 },
      replaceApp: { _, _ in replaces += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .refused }
    try require(packs == 0 && replaces == 0, "forged signature reached mutation")
    try require(!coordinator.progress.canInstall, "refused offered Install Update")
  }

  static func testCoordinatorReadyOffersInstallNotQuit() throws {
    let coordinator = makeCoordinator()
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .ready }
    try require(coordinator.progress.canInstall, "ready did not offer Install Update")
    try require(!coordinator.progress.claimsHealthy, "ready claimed healthy")
    try require(
      coordinator.progress.installedGeneration == previous, "ready lost the previous version")
    try require(
      coordinator.progress.candidateGeneration == generation, "ready lost the candidate version")
  }

  static func testCoordinatorInstallsNewerAppViaReplacement() throws {
    var packs = 0
    var replaces = 0
    var closed = 0
    var current = previousInstalled()
    let coordinator = makeCoordinator(
      installed: { current },
      installPack: { _, _, completion in
        packs += 1
        completion(.success(matchingInstalled()))
      },
      replaceApp: { request, completion in
        replaces += 1
        completion(
          .success(
            ProductUpdateReplacementAdmission(
              helperPID: 4242,
              waitIdentity: ProductUpdateProcessIdentity(pid: 1, startTime: "test"),
              receiptURL: request.receiptURL,
              transactionURL: request.transactionURL,
              transactionID: request.transactionID ?? "test-txn",
              ready: true)))
      },
      closeUI: { closed += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .restarting }
    try require(replaces == 1, "newer app did not call the replacement owner")
    try require(closed == 1, "replacement did not close the UI after the helper was armed")
    try require(packs == 0, "old app published a pack after helper admission")
    try require(coordinator.hasAdmittedHelperHandoff, "helper admission was not recorded")
    try require(!coordinator.progress.claimsHealthy, "parent synthesized a healthy replace")
  }

  static func testCoordinatorSameAppRepairPublishesPack() throws {
    var packs = 0
    var replaces = 0
    let coordinator = makeCoordinator(
      installed: { matchingInstalled(pack: previous) },
      installPack: { admitted, _, completion in
        packs += 1
        completion(
          .success(
            ProductUpdateIdentity(
              appGeneration: admitted.generation, packGeneration: admitted.generation,
              sourceRevision: admitted.sourceRevision)))
      },
      replaceApp: { _, _ in replaces += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .success }
    try require(packs == 1, "same-app repair did not use the installer")
    try require(replaces == 0, "same-app repair replaced the UI")
    try require(coordinator.progress.claimsHealthy, "matching repair did not claim healthy")
  }

  static func testCoordinatorHelperUnableToReplaceIsRetained() throws {
    var packs = 0
    let coordinator = makeCoordinator(
      installPack: { _, _, _ in packs += 1 },
      replaceApp: { _, completion in
        completion(.failure(ProductUpdateReplacementError.replaceFailed("destination locked")))
      })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .retained }
    try require(packs == 0, "failed replace published a pack")
    try require(!coordinator.progress.claimsHealthy, "failed replace claimed healthy")
    try require(coordinator.progress.installedGeneration == previous, "failed replace lost previous version")
  }

  static func testCoordinatorInterruptDuringDownloadRetains() throws {
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        stagingRoot: { FileManager.default.temporaryDirectory },
        home: { FileManager.default.temporaryDirectory },
        fetchBytes: { _, _ in {} },
        downloadFile: { _, _, _ in {} },
        verifyFeedSignature: { _, _, _, _ in {} },
        verifyCandidate: { _, _, _, _ in {} },
        installPack: { _, _, _ in {} },
        replaceApp: { _, _ in {} },
        extractApp: nil,
        closeUIAfterHelperArmed: {},
        checkTimeout: 30))
    coordinator.checkForUpdates()
    try require(coordinator.progress.phase == .checking || coordinator.progress.phase == .downloading,
      "check did not start")
    coordinator.interrupt()
    try require(coordinator.progress.phase == .retained, "interrupt did not retain")
    try require(!coordinator.isBusy, "interrupt left the coordinator busy")
    try require(
      productUpdateRetentionJustified(
        receipt: coordinator.receipt, recovered: previousInstalled(), previous: previousInstalled()),
      "interrupt retain was not receipt-backed")
  }

  static func testCoordinatorInterruptDuringPackDoesNotLie() throws {
    var cancelCount = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { matchingInstalled(pack: previous) },
        stagingRoot: { FileManager.default.temporaryDirectory },
        home: { FileManager.default.temporaryDirectory },
        fetchBytes: { url, completion in
          if url.path.hasSuffix(".sig") {
            completion(.success(Data(repeating: 3, count: 256)))
          } else {
            completion(.success(feedJSON()))
          }
          return {}
        },
        downloadFile: { _, destination, completion in
          try? Data(repeating: 9, count: 16).write(to: destination)
          completion(.success(destination))
          return {}
        },
        verifyFeedSignature: { _, _, _, completion in
          completion(.success(()))
          return {}
        },
        verifyCandidate: { _, _, _, completion in
          completion(.success(validProof()))
          return {}
        },
        installPack: { _, _, _ in
          { cancelCount += 1 }
        },
        replaceApp: { _, _ in {} },
        extractApp: { _, destination, completion in
          try? FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
          completion(.success(destination))
          return {}
        },
        closeUIAfterHelperArmed: {},
        checkTimeout: 30))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .installing }
    coordinator.interrupt()
    try require(cancelCount == 1, "pack install was not cancelled")
    try require(
      coordinator.progress.phase == .retained || coordinator.progress.phase == .error,
      "pack interrupt did not settle")
    try require(!coordinator.progress.claimsHealthy, "interrupted pack claimed healthy")
  }

  static func testCoordinatorFeedTimeoutIsBounded() throws {
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        stagingRoot: { FileManager.default.temporaryDirectory },
        home: { FileManager.default.temporaryDirectory },
        fetchBytes: { _, _ in {} },
        downloadFile: { _, _, _ in {} },
        verifyFeedSignature: { _, _, _, _ in {} },
        verifyCandidate: { _, _, _, _ in {} },
        installPack: { _, _, _ in {} },
        replaceApp: { _, _ in {} },
        extractApp: nil,
        closeUIAfterHelperArmed: {},
        checkTimeout: 0.05))
    coordinator.checkForUpdates()
    try wait(seconds: 2) {
      coordinator.progress.phase == .error || coordinator.progress.phase == .retained
    }
    try require(!coordinator.progress.claimsHealthy, "timeout claimed healthy")
    try require(coordinator.progress.canRetry, "timeout must be retryable")
  }

  static func testCoordinatorUIShutdownDoesNotCancelAdmittedHelper() throws {
    var cancelled = 0
    let coordinator = makeCoordinator(
      replaceApp: { request, completion in
        completion(
          .success(
            ProductUpdateReplacementAdmission(
              helperPID: 4242,
              waitIdentity: ProductUpdateProcessIdentity(pid: 1, startTime: "test"),
              receiptURL: request.receiptURL,
              transactionURL: request.transactionURL,
              transactionID: request.transactionID ?? "test-txn",
              ready: true)))
        return { cancelled += 1 }
      })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.hasAdmittedHelperHandoff }
    coordinator.interrupt()
    coordinator.noteUIShutdownPreservingHandoff()
    try require(cancelled == 0, "UI shutdown cancelled the admitted helper")
    try require(coordinator.progress.phase == .restarting, "UI shutdown left restarting")
    try require(coordinator.hasAdmittedHelperHandoff, "handoff was dropped on UI shutdown")
    try require(!coordinator.progress.claimsHealthy, "parent claimed replace without a receipt")
  }

  static func testReplacementOwnerRequiresExplicitHelper() throws {
    let source = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-nohelper-src-\(UUID().uuidString).app", isDirectory: true)
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-nohelper-dst-\(UUID().uuidString).app")
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-nohelper-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
    switch replaceProductUpdateApp(
      ProductUpdateReplacementRequest(
        sourceApp: source, destinationApp: dest, relaunch: false,
        receiptURL: receipt, helperURL: nil))
    {
    case .failure(.helperMissing):
      break
    default:
      throw Failure(message: "a compile-time source path was used as helper authority")
    }
  }

  static func testReplacementOwnerRefusesMissingSource() throws {
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-missing-dest-\(UUID().uuidString).app")
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-missing-receipt-\(UUID().uuidString).json")
    let request = ProductUpdateReplacementRequest(
      waitPID: nil,
      sourceApp: URL(fileURLWithPath: "/tmp/does-not-exist-\(UUID().uuidString).app"),
      destinationApp: dest,
      relaunch: false,
      receiptURL: receipt,
      helperURL: try helperScript())
    switch replaceProductUpdateApp(request) {
    case .failure(.sourceMissing):
      break
    default:
      throw Failure(message: "missing source was not a replace failure")
    }
    try require(!FileManager.default.fileExists(atPath: dest.path), "failed replace created a destination")
  }

  static func testReplacementOwnerRefusesUnsignedSource() throws {
    let source = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-src-\(UUID().uuidString).app", isDirectory: true)
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-dst-\(UUID().uuidString).app", isDirectory: true)
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
    try Data("previous".utf8).write(to: dest.appendingPathComponent("marker.txt"))
    try Data("payload".utf8).write(to: source.appendingPathComponent("Contents.txt"))
    let request = ProductUpdateReplacementRequest(
      sourceApp: source, destinationApp: dest, relaunch: false,
      receiptURL: receipt, helperURL: try helperScript())
    switch replaceProductUpdateApp(request) {
    case .success:
      throw Failure(message: "unsigned source was replaced")
    case .failure:
      break
    }
    try require(
      (try String(contentsOf: dest.appendingPathComponent("marker.txt"), encoding: .utf8)) == "previous",
      "unsigned replace mutated the destination")
    try require(!FileManager.default.fileExists(atPath: receipt.path), "unsigned replace wrote a receipt")
  }

  static func testReplacementOwnerTimesOutLiveParent() throws {
    let sleeper = Process()
    sleeper.executableURL = URL(fileURLWithPath: "/bin/sleep")
    sleeper.arguments = ["30"]
    try sleeper.run()
    defer {
      if sleeper.isRunning { sleeper.terminate() }
      sleeper.waitUntilExit()
    }
    guard let identity = captureProductUpdateProcessIdentity(pid: sleeper.processIdentifier) else {
      throw Failure(message: "could not bind sleeper identity")
    }
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-live-dest-\(UUID().uuidString).app", isDirectory: true)
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-live-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
    try Data("keep".utf8).write(to: dest.appendingPathComponent("marker.txt"))
    let request = ProductUpdateReplacementRequest(
      waitPID: identity.pid,
      waitStart: identity.startTime,
      waitTimeout: 1,
      sourceApp: dest,
      destinationApp: dest,
      relaunch: false,
      receiptURL: receipt,
      helperURL: try helperScript())
    switch replaceProductUpdateApp(request) {
    case .failure(.destinationBusy), .failure(.helperFailed), .failure(.admissionRejected):
      break
    default:
      throw Failure(
        message:
          "a live unsigned parent must fail before mutation (preflight 9 or identity timeout 5)")
    }
    try require(
      (try String(contentsOf: dest.appendingPathComponent("marker.txt"), encoding: .utf8)) == "keep",
      "timed-out parent was replaced")
    try require(!FileManager.default.fileExists(atPath: receipt.path), "timeout wrote a replacement receipt")
  }

  static func testReplacementOwnerKeepsPreviousCapture() throws {
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-capture-dest-\(UUID().uuidString).app", isDirectory: true)
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-capture-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
    try Data("keep".utf8).write(to: dest.appendingPathComponent("marker.txt"))
    let old = dest.deletingLastPathComponent().appendingPathComponent(
      ".vc-update-capture-oldid", isDirectory: true)
    try FileManager.default.createDirectory(
      at: old.appendingPathComponent("prior.app", isDirectory: true), withIntermediateDirectories: true)
    try Data("old".utf8).write(
      to: old.appendingPathComponent("prior.app").appendingPathComponent("keep.txt"))
    let sleeper = Process()
    sleeper.executableURL = URL(fileURLWithPath: "/bin/sleep")
    sleeper.arguments = ["30"]
    try sleeper.run()
    defer {
      if sleeper.isRunning { sleeper.terminate() }
      sleeper.waitUntilExit()
    }
    guard let identity = captureProductUpdateProcessIdentity(pid: sleeper.processIdentifier) else {
      throw Failure(message: "could not bind sleeper identity")
    }
    let request = ProductUpdateReplacementRequest(
      waitPID: identity.pid,
      waitStart: identity.startTime,
      waitTimeout: 1,
      sourceApp: dest,
      destinationApp: dest,
      relaunch: false,
      receiptURL: receipt,
      helperURL: try helperScript())
    _ = replaceProductUpdateApp(request)
    try require(
      (try String(
        contentsOf: old.appendingPathComponent("prior.app").appendingPathComponent("keep.txt"),
        encoding: .utf8)) == "old",
      "previous capture was deleted during interruption")
  }

  static func testReplacementOwnerDoesNotSynthesizeReceipt() throws {
    let stub = writeTemp("fake-helper.sh", Data("#!/bin/bash\nexit 0\n".utf8))
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: stub.path)
    let source = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-stub-src-\(UUID().uuidString).app", isDirectory: true)
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-stub-dst-\(UUID().uuidString).app", isDirectory: true)
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-stub-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: dest, withIntermediateDirectories: true)
    switch replaceProductUpdateApp(
      ProductUpdateReplacementRequest(
        sourceApp: source, destinationApp: dest, relaunch: false,
        receiptURL: receipt, helperURL: stub))
    {
    case .failure(.receiptMissing):
      break
    default:
      throw Failure(message: "helper exit 0 without a receipt was treated as replaced")
    }
    try require(!FileManager.default.fileExists(atPath: receipt.path), "missing receipt was invented")
  }

  static func sampleHandoff(
    destination: String = "/Applications/Vibecrafted.app",
    transaction: String = "txn-1",
    helperPID: Int32 = 9,
    helperStart: String = "start",
    mode: String = ProductUpdateHelperMode.replace.rawValue,
    receiptURL: String = "/tmp/replacement-receipt.json"
  ) -> ProductUpdateHandoffRecord {
    ProductUpdateHandoffRecord(
      schema: ProductUpdateHandoffRecord.schemaID,
      helperPID: helperPID,
      helperStart: helperStart,
      waitPID: 1,
      waitStart: "parent",
      receiptURL: receiptURL,
      admissionURL: receiptURL + ".admission.json",
      journalURL: receiptURL + ".journal.json",
      destination: destination,
      candidateGeneration: generation,
      installedGeneration: previous,
      capturePath: "/tmp/.vc-update-capture-txn-1",
      packURL: "/tmp/pack.tar.gz",
      sourceRevision: source,
      terminalRevision: terminal,
      frameRevision: frame,
      transactionID: transaction,
      mode: mode,
      phase: "helper_ready",
      candidateIdentity: "cdhash:candidate",
      priorIdentity: "cdhash:prior")
  }

  static func boundEvidence(
    transaction: String = "txn-1",
    operation: String = ProductUpdateHelperMode.replace.rawValue,
    phase: String = "receipt_written",
    running: String = "cdhash:candidate",
    candidate: String = "cdhash:candidate",
    restore: String = "cdhash:prior",
    pack: ProductUpdatePackPublicationState = .unpublished,
    packGeneration: String = "",
    packDetail: String = ""
  ) -> ProductUpdateRuntimeEvidence {
    ProductUpdateRuntimeEvidence(
      runningAppIdentity: running,
      expectedCandidateIdentity: candidate,
      expectedRestoreIdentity: restore,
      journalPhase: phase,
      journalTransaction: transaction,
      journalOperation: operation,
      packPublication: pack,
      packGeneration: packGeneration,
      packDetail: packDetail)
  }

  static func boundReceipt(
    replaced: Bool = true,
    destination: String = "/Applications/Vibecrafted.app",
    detail: String = "replaced",
    transaction: String? = "txn-1",
    mode: String? = ProductUpdateHelperMode.replace.rawValue,
    phase: String? = "receipt_written"
  ) -> ProductUpdateReplacementReceipt {
    ProductUpdateReplacementReceipt(
      replaced: replaced,
      relaunched: false,
      destination: destination,
      detail: detail,
      capture: "/tmp/.vc-update-capture-txn-1",
      transaction: transaction,
      journal: "/tmp/replacement-receipt.json.journal.json",
      mode: mode,
      operation: mode,
      phase: phase,
      sourceIdentity: "cdhash:candidate",
      priorIdentity: "cdhash:prior")
  }

  static func testHandoffMissingReceiptWithLiveHelperWaits() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: nil, helperLive: true,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .awaitReceipt:
      break
    default:
      throw Failure(message: "live helper without a receipt must wait, not delete")
    }
  }

  static func testHandoffMissingReceiptWithDeadHelperRetains() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: nil, helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .retain(let reason):
      try require(reason.contains("receipt"), reason)
    default:
      throw Failure(message: "dead helper without a receipt must retain evidence")
    }
  }

  static func testHandoffCorruptOrStaleReceiptIsNotAdopted() throws {
    let foreign = boundReceipt(transaction: "other-txn")
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: foreign, helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .stale:
      break
    default:
      throw Failure(message: "a receipt from another transaction was adopted")
    }
    let wrongDest = boundReceipt(destination: "/tmp/Other.app")
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: wrongDest, helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .stale:
      break
    default:
      throw Failure(message: "a receipt for another destination was adopted")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: nil, helperLive: false,
      runningDestination: "/tmp/Other.app",
      evidence: boundEvidence())
    {
    case .stale:
      break
    default:
      throw Failure(message: "a handoff for another running app was adopted")
    }
    let emptyTxn = boundReceipt(transaction: "")
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: emptyTxn, helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .stale:
      break
    default:
      throw Failure(message: "a receipt with an empty transaction was adopted")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(transaction: ""), replacement: boundReceipt(), helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(transaction: ""))
    {
    case .stale:
      break
    default:
      throw Failure(message: "a handoff with an empty transaction was adopted")
    }
  }

  static func testHandoffVerifiedReceiptPublishesPack() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: boundReceipt(), helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .publishPack(let observed):
      try require(observed.replaced, "verified receipt was not publishable")
    default:
      throw Failure(message: "a bound replaced receipt did not continue to pack publish")
    }
  }

  static func testHandoffRestoreReceiptDoesNotPublishPack() throws {
    let receipt = boundReceipt(
      detail: "restored",
      mode: ProductUpdateHelperMode.restore.rawValue)
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(
        mode: ProductUpdateHelperMode.restore.rawValue,
        receiptURL: "/tmp/restore-receipt.json"),
      replacement: receipt, helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(
        operation: ProductUpdateHelperMode.restore.rawValue,
        running: "cdhash:prior"))
    {
    case .rolledBack(let reason):
      try require(reason.contains("previous"), reason)
    default:
      throw Failure(message: "a restore receipt must not republish the failed pack")
    }
  }

  static func testHandoffUnresolvedPackKeepsRecoveryOpen() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: boundReceipt(), helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(pack: .unresolved))
    {
    case .retain(let reason):
      try require(reason.contains("unresolved"), reason)
    default:
      throw Failure(message: "unresolved installer state was treated as committed")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(mode: ProductUpdateHelperMode.restore.rawValue),
      replacement: boundReceipt(detail: "restored", mode: ProductUpdateHelperMode.restore.rawValue),
      helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(
        operation: ProductUpdateHelperMode.restore.rawValue,
        running: "cdhash:prior",
        pack: .unresolved))
    {
    case .retain(let reason):
      try require(reason.contains("unresolved"), reason)
    default:
      throw Failure(message: "app-only restore claimed rolledBack while pack state was unresolved")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(mode: ProductUpdateHelperMode.restore.rawValue),
      replacement: boundReceipt(detail: "restored", mode: ProductUpdateHelperMode.restore.rawValue),
      helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(
        operation: ProductUpdateHelperMode.restore.rawValue,
        running: "cdhash:prior",
        pack: .published))
    {
    case .retain(let reason):
      try require(reason.contains("published"), reason)
    default:
      throw Failure(message: "app-only restore claimed whole-tuple rollback after pack publish")
    }
  }

  static func testHandoffRequiresExactIdentityAndPhase() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: boundReceipt(), helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(running: "cdhash:other"))
    {
    case .retain:
      break
    default:
      throw Failure(message: "path-only identity was treated as the candidate")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(), replacement: boundReceipt(phase: "displacing"), helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .retain:
      break
    default:
      throw Failure(message: "an unvalidated receipt phase was treated as committed")
    }
  }

  static func testRestoreRequestBindsSameTransactionAndWaitsForUI() throws {
    let prior = URL(fileURLWithPath: "/tmp/.vc-update-capture-txn-1/prior.app")
    let request = productUpdateRestoreRequest(
      handoff: sampleHandoff(),
      priorApp: prior,
      waitPID: 77,
      waitStart: "now",
      helperURL: URL(fileURLWithPath: "/tmp/helper"),
      receiptURL: URL(fileURLWithPath: "/tmp/restore-receipt.json"))
    try require(request.mode == .restore, "restore used replace mode")
    try require(request.relaunch, "restore did not relaunch the previous app")
    try require(request.waitPID == 77, "restore overwrote a running app without waiting")
    try require(request.transactionID == "txn-1", "restore started a new transaction")
    try require(request.sourceApp == prior, "restore source was not the owned prior.app")
  }

  static func testRecoverRequestUsesInstallerOwnerMode() throws {
    let prior = URL(fileURLWithPath: "/tmp/.vc-update-capture-txn-1/prior.app")
    let request = productUpdateRecoverRequest(
      handoff: sampleHandoff(),
      priorApp: prior,
      waitPID: 77,
      waitStart: "now",
      helperURL: URL(fileURLWithPath: "/tmp/helper"),
      receiptURL: URL(fileURLWithPath: "/tmp/recover-receipt.json"))
    try require(request.mode == .recover, "whole-tuple recover used app-only restore")
    try require(request.relaunch, "recover did not relaunch the previous app")
    try require(request.waitPID == 77, "recover overwrote a running app without waiting")
    try require(request.transactionID == "txn-1", "recover started a new transaction")
    try require(request.sourceApp == prior, "recover source was not the owned prior.app")
    let arguments = productUpdateHelperArguments(request)
    try require(arguments.contains("--mode"), "recover helper omitted mode")
    try require(
      arguments.contains(ProductUpdateHelperMode.recover.rawValue),
      "recover helper did not invoke --mode recover")
  }

  static func testHandoffRecoverWithPublishedPackKeepsRecoveryOpen() throws {
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(mode: ProductUpdateHelperMode.recover.rawValue),
      replacement: boundReceipt(
        detail: "recovered",
        mode: ProductUpdateHelperMode.recover.rawValue),
      helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(
        operation: ProductUpdateHelperMode.recover.rawValue,
        running: "cdhash:prior",
        pack: .published))
    {
    case .retain(let reason):
      try require(reason.contains("published"), reason)
    default:
      throw Failure(message: "recover receipt claimed whole-tuple success while candidate pack stayed published")
    }
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(mode: ProductUpdateHelperMode.recover.rawValue),
      replacement: boundReceipt(
        detail: "recovered",
        mode: ProductUpdateHelperMode.recover.rawValue),
      helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence(
        operation: ProductUpdateHelperMode.recover.rawValue,
        running: "cdhash:prior"))
    {
    case .rolledBack(let reason):
      try require(reason.contains("Runtime Pack"), reason)
    default:
      throw Failure(message: "recover receipt with prior pack was not whole-tuple rolledBack")
    }
  }

  static func testOwnedCaptureRejectsForeignPaths() throws {
    try require(
      productUpdateOwnedPriorApp(at: "/tmp/arbitrary/prior.app") == nil,
      "a foreign path was treated as an owned capture")
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-owned-\(UUID().uuidString)", isDirectory: true)
    let capture = root.appendingPathComponent(".vc-update-capture-abc", isDirectory: true)
    let prior = capture.appendingPathComponent("prior.app", isDirectory: true)
    try FileManager.default.createDirectory(at: prior, withIntermediateDirectories: true)
    try require(
      productUpdateOwnedPriorApp(at: capture.path) == prior,
      "an owned capture was not recognized")
  }

  static func testAdmissionRecordRequiresBinding() throws {
    let request = ProductUpdateReplacementRequest(
      waitPID: 5,
      waitStart: "birth",
      sourceApp: URL(fileURLWithPath: "/tmp/src.app"),
      destinationApp: URL(fileURLWithPath: "/tmp/dst.app"),
      relaunch: true,
      receiptURL: URL(fileURLWithPath: "/tmp/receipt.json"),
      transactionID: "txn-bind")
    let ready = """
      {"schema":"io.vetcoders.vibecrafted.app-replacement-admission.v1","status":"ready","transaction":"txn-bind","destination":"/tmp/dst.app","identifier":"\(productUpdateExpectedBundleIdentifier)","team_id":"\(productUpdateExpectedTeamID)","parent_pid":"5","parent_start":"birth","journal":"/tmp/j","capture":"/tmp/c","receipt":"/tmp/receipt.json","detail":"ok","source_identity":"x","mode":"replace"}
      """.data(using: .utf8)!
    guard let record = decodeProductUpdateHelperAdmission(ready) else {
      throw Failure(message: "valid READY admission did not decode")
    }
    try require(productUpdateAdmissionMatches(record, request: request), "bound READY was rejected")
    let stale = """
      {"schema":"io.vetcoders.vibecrafted.app-replacement-admission.v1","status":"ready","transaction":"other","destination":"/tmp/dst.app","identifier":"\(productUpdateExpectedBundleIdentifier)","team_id":"\(productUpdateExpectedTeamID)","parent_pid":"5","parent_start":"birth","journal":"/tmp/j","capture":"/tmp/c","receipt":"/tmp/receipt.json","detail":"ok","source_identity":"x","mode":"replace"}
      """.data(using: .utf8)!
    guard let staleRecord = decodeProductUpdateHelperAdmission(stale) else {
      throw Failure(message: "stale READY did not decode")
    }
    try require(
      !productUpdateAdmissionMatches(staleRecord, request: request),
      "a stale transaction was admitted")
  }

  static func testHandoffReadRefusesDefaultReplaceMode() throws {
    let url = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-handoff-nomode-\(UUID().uuidString).json")
    let object: [String: Any] = [
      "schema": ProductUpdateHandoffRecord.schemaID,
      "helper_pid": 1,
      "helper_start": "s",
      "wait_pid": 2,
      "wait_start": "p",
      "receipt_url": "/tmp/r.json",
      "admission_url": "/tmp/a.json",
      "journal_url": "/tmp/j.json",
      "destination": "/Applications/Vibecrafted.app",
      "candidate_generation": generation,
      "installed_generation": previous,
      "source_revision": source,
      "terminal_revision": terminal,
      "frame_revision": frame,
      "transaction_id": "txn-1",
      "candidate_identity": "cdhash:candidate",
      "prior_identity": "cdhash:prior",
    ]
    let data = try JSONSerialization.data(withJSONObject: object)
    try data.write(to: url)
    do {
      _ = try readProductUpdateHandoff(from: url)
      throw Failure(message: "handoff missing mode/phase defaulted to replace")
    } catch {
      try require(
        String(describing: error).contains("malformed")
          || String(describing: error).contains("handoff"),
        "missing mode was not a malformed handoff")
    }
  }

  static func testObserveInstallerPublicationIgnoresCallerEnum() throws {
    let runtime = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-observe-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true)
    let home = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-observe-home-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    try writeProductUpdatePackEvidence(
      transaction: "txn-1",
      observation: ProductUpdateInstallerPublication(
        generation: "",
        receiptVersion: "",
        pointerPresent: false,
        receiptPresent: false,
        pending: false,
        readable: true,
        rolledBack: false,
        detail: "caller note"),
      derived: .published,
      priorGeneration: previous,
      candidateGeneration: generation,
      to: productUpdatePackEvidenceURL(home: home))
    let observed = productUpdateObserveInstallerPublication(runtimeHome: runtime)
    try require(!observed.pointerPresent && !observed.receiptPresent, "empty runtime was not absent")
    try require(
      productUpdateDerivePackPublication(
        observed, priorGeneration: previous, candidateGeneration: generation) == .unpublished,
      "absent installer docs were not unpublished")
    let pointer = activeRuntimePointerURL(runtimeHome: runtime)
    let receipt = runtimeInstallReceiptURL(runtimeHome: runtime)
    try """
      {"schema":"vibecrafted.active-runtime.v1","version":"\(generation)","runtime_root":"\(runtime.path)/releases/\(generation)"}
      """.write(to: pointer, atomically: true, encoding: .utf8)
    try """
      {"schema":"vibecrafted.runtime-install.v1","version":"\(generation)"}
      """.write(to: receipt, atomically: true, encoding: .utf8)
    let published = productUpdateObserveInstallerPublication(runtimeHome: runtime)
    try require(published.generation == generation, published.detail)
    try require(
      productUpdateDerivePackPublication(
        published, priorGeneration: previous, candidateGeneration: generation) == .published,
      "installer generation was not published")
    try """
      {"schema":"vibecrafted.active-runtime.v1","version":"mystery","runtime_root":"\(runtime.path)/releases/mystery"}
      """.write(to: pointer, atomically: true, encoding: .utf8)
    try """
      {"schema":"vibecrafted.runtime-install.v1","version":"mystery"}
      """.write(to: receipt, atomically: true, encoding: .utf8)
    let mystery = productUpdateObserveInstallerPublication(runtimeHome: runtime)
    try require(
      productUpdateDerivePackPublication(
        mystery, priorGeneration: previous, candidateGeneration: generation) == .unresolved,
      "an unmatched installer generation defaulted to unpublished or replace")
  }

  static func testObserveInstallerPublicationRejectsIncompleteStates() throws {
    let runtime = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-observe-pending-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true)
    let pointer = activeRuntimePointerURL(runtimeHome: runtime)
    let receipt = runtimeInstallReceiptURL(runtimeHome: runtime)
    try """
      {"schema":"vibecrafted.active-runtime.v1","version":"\(generation)","runtime_root":"\(runtime.path)/releases/\(generation)"}
      """.write(to: pointer, atomically: true, encoding: .utf8)
    let pendingBodies = [
      "{\"schema\":\"vibecrafted.runtime-install.v1\",\"version\":\"\(generation)\",\"config_pending\":{\"staged\":true}}",
      "{\"schema\":\"vibecrafted.runtime-install.v1\",\"version\":\"\(generation)\",\"uninstall_pending\":true}",
      "{\"schema\":\"vibecrafted.runtime-install.v1\",\"version\":\"\(generation)\",\"config_transaction\":{}}",
      "{\"schema\":\"vibecrafted.runtime-install.v1\",\"version\":\"\(generation)\",\"config_conflicts\":{\"keep\":\"x\"}}",
      "{\"schema\":\"vibecrafted.runtime-install.v1\",\"version\":\"\"}",
    ]
    for body in pendingBodies {
      try body.write(to: receipt, atomically: true, encoding: .utf8)
      let observed = productUpdateObserveInstallerPublication(runtimeHome: runtime)
      try require(observed.pending, "incomplete publication was accepted: \(body)")
      try require(
        productUpdateDerivePackPublication(
          observed, priorGeneration: previous, candidateGeneration: generation) == .unresolved,
        "incomplete publication was not unresolved: \(body)")
    }
  }

  static func testReplacementReceiptRequiresTransactionAndOperation() throws {
    let missing = """
      {"schema":"io.vetcoders.vibecrafted.app-replacement.v1","destination":"/Applications/Vibecrafted.app","replaced":true,"detail":"replaced"}
      """.data(using: .utf8)!
    try require(
      decodeReplacementReceipt(missing) == nil,
      "a helper receipt without transaction/operation was accepted")
    let bound = """
      {"schema":"io.vetcoders.vibecrafted.app-replacement.v1","destination":"/Applications/Vibecrafted.app","replaced":true,"detail":"replaced","transaction":"txn-1","operation":"replace","mode":"replace","phase":"receipt_written"}
      """.data(using: .utf8)!
    guard let decoded = decodeReplacementReceipt(bound) else {
      throw Failure(message: "a bound replacement receipt did not decode")
    }
    try require(decoded.transaction == "txn-1", "bound receipt dropped its transaction")
    try require(decoded.operation == "replace", "bound receipt dropped its operation")
    switch decideProductUpdateHandoff(
      handoff: sampleHandoff(),
      replacement: ProductUpdateReplacementReceipt(
        replaced: true,
        relaunched: false,
        destination: "/Applications/Vibecrafted.app",
        detail: "replaced",
        capture: nil,
        transaction: "txn-1",
        journal: nil,
        mode: nil,
        operation: nil,
        phase: "receipt_written"),
      helperLive: false,
      runningDestination: "/Applications/Vibecrafted.app",
      evidence: boundEvidence())
    {
    case .stale(let reason):
      try require(reason.contains("operation"), reason)
    default:
      throw Failure(message: "a receipt without operation defaulted to replace")
    }
  }

  static func testPersistedTransactionRequiresBinding() throws {
    let start = try encodeProductUpdateTransaction(
      ProductUpdateTransactionReceipt.start(installed: previousInstalled()))
    _ = try decodeProductUpdateTransaction(start)
    var retained = ProductUpdateTransactionReceipt.start(installed: previousInstalled())
    retained.boundary = .committed
    retained.terminal = .retained
    let unbound = try encodeProductUpdateTransaction(retained)
    do {
      _ = try decodeProductUpdateTransaction(unbound)
      throw Failure(message: "a retained transaction without an id decoded")
    } catch {
      try require(
        String(describing: error).contains("transaction"),
        "missing transaction id was not a malformed receipt")
    }
    retained.transactionID = "txn-1"
    let bound = try decodeProductUpdateTransaction(try encodeProductUpdateTransaction(retained))
    try require(bound.transactionID == "txn-1", "bound transaction id was dropped")
  }

  static func testCoordinatorDoesNotCloseUIWhenHandoffWriteFails() throws {
    var closed = 0
    let blocked = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-handoff-blocked-\(UUID().uuidString)")
    FileManager.default.createFile(atPath: blocked.path, contents: Data(), attributes: nil)
    let coordinator = makeCoordinator(
      replaceApp: { request, completion in
        completion(
          .success(
            ProductUpdateReplacementAdmission(
              helperPID: 4242,
              waitIdentity: ProductUpdateProcessIdentity(pid: 1, startTime: "test"),
              receiptURL: request.receiptURL,
              transactionURL: request.transactionURL,
              transactionID: request.transactionID ?? "test-txn",
              ready: true)))
      },
      closeUI: { closed += 1 },
      homeURL: blocked)
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .error }
    try require(closed == 0, "UI closed after a failed handoff persist")
    try require(coordinator.hasAdmittedHelperHandoff, "failed persist dropped the admitted helper")
    try require(
      coordinator.progress.summary.contains("handoff")
        || coordinator.progress.summary.contains("window stays open"),
      coordinator.progress.summary)
  }

  static func testCoordinatorDoesNotCloseUIWithoutReadyAdmission() throws {
    var closed = 0
    let coordinator = makeCoordinator(
      replaceApp: { request, completion in
        completion(
          .success(
            ProductUpdateReplacementAdmission(
              helperPID: 7,
              waitIdentity: nil,
              receiptURL: request.receiptURL,
              transactionURL: request.transactionURL,
              ready: false)))
      },
      closeUI: { closed += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .retained }
    try require(closed == 0, "UI closed before helper-owned READY")
    try require(!coordinator.hasAdmittedHelperHandoff, "Process.isRunning was treated as admission")
  }

  static func testProgressCopyHasNoArchitectureJargon() throws {
    let progress = deriveProductUpdateProgress(
      phase: .ready, installed: previousInstalled(), candidate: candidate())
    try require(progress.canInstall, "ready must offer Install Update")
    try require(!progress.summary.lowercased().contains("sparkle"), progress.summary)
    try require(!progress.summary.lowercased().contains("receipt transaction"), progress.summary)
    try require(!progress.summary.lowercased().contains("helper"), progress.summary)
    try require(!progress.title.lowercased().contains("runtime pack owner"), progress.title)
  }

  static func main() throws {
    try testStagedRelativePathRejectsBuildAuthority()
    try testMissingFeedIsUnavailable()
    try testHTTPFeedIsRejected()
    try testFixtureFileURLRequiresExplicitEnv()
    try testForgedSignatureValidAndTicketsAreIgnored()
    try testTamperedSignatureBytesAreRefused()
    try testMixedDonorRevisionsAreRefused()
    try testIdentityMismatchIsRefused()
    try testHealthyRequiresMatchingIdentity()
    try testRetentionRequiresReceiptAndRecoveredState()
    try testCoordinatorMissingFeedNeverInstalls()
    try testCoordinatorForgedFeedNeverInstalls()
    try testCoordinatorReadyOffersInstallNotQuit()
    try testCoordinatorInstallsNewerAppViaReplacement()
    try testCoordinatorSameAppRepairPublishesPack()
    try testCoordinatorHelperUnableToReplaceIsRetained()
    try testCoordinatorInterruptDuringDownloadRetains()
    try testCoordinatorInterruptDuringPackDoesNotLie()
    try testCoordinatorFeedTimeoutIsBounded()
    try testCoordinatorUIShutdownDoesNotCancelAdmittedHelper()
    try testReplacementOwnerRequiresExplicitHelper()
    try testReplacementOwnerRefusesMissingSource()
    try testReplacementOwnerRefusesUnsignedSource()
    try testReplacementOwnerTimesOutLiveParent()
    try testReplacementOwnerKeepsPreviousCapture()
    try testReplacementOwnerDoesNotSynthesizeReceipt()
    try testHandoffMissingReceiptWithLiveHelperWaits()
    try testHandoffMissingReceiptWithDeadHelperRetains()
    try testHandoffCorruptOrStaleReceiptIsNotAdopted()
    try testHandoffVerifiedReceiptPublishesPack()
    try testHandoffRestoreReceiptDoesNotPublishPack()
    try testHandoffUnresolvedPackKeepsRecoveryOpen()
    try testHandoffRequiresExactIdentityAndPhase()
    try testRestoreRequestBindsSameTransactionAndWaitsForUI()
    try testRecoverRequestUsesInstallerOwnerMode()
    try testHandoffRecoverWithPublishedPackKeepsRecoveryOpen()
    try testOwnedCaptureRejectsForeignPaths()
    try testAdmissionRecordRequiresBinding()
    try testHandoffReadRefusesDefaultReplaceMode()
    try testObserveInstallerPublicationIgnoresCallerEnum()
    try testObserveInstallerPublicationRejectsIncompleteStates()
    try testReplacementReceiptRequiresTransactionAndOperation()
    try testPersistedTransactionRequiresBinding()
    try testCoordinatorDoesNotCloseUIWhenHandoffWriteFails()
    try testCoordinatorDoesNotCloseUIWithoutReadyAdmission()
    try testProgressCopyHasNoArchitectureJargon()
    print("ProductUpdatePolicyTests passed")
  }
}
