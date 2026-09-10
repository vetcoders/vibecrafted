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
    makeVerifiedProductUpdateProof(
      codesignIdentifier: productUpdateExpectedBundleIdentifier,
      notarizedAndStapled: true,
      packIdentityMatches: true,
      observedSourceRevision: source,
      observedTerminalRevision: terminal,
      observedFrameRevision: frame)
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
    replaceApp: @escaping (ProductUpdateReplacementRequest, (Result<ProductUpdateReplacementReceipt, Error>) -> Void) -> Void = { _, _ in
      fatalError("app must not replace")
    },
    closeUI: @escaping () -> Void = {},
    timeout: TimeInterval = 15
  ) -> ProductUpdateCoordinator {
    let staging = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-update-stage-\(UUID().uuidString)", isDirectory: true)
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
        verifyFeedSignature: { _, _, _ in verifySignature },
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
        extractApp: nil,
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
      replaceApp: { _, completion in
        replaces += 1
        current = matchingInstalled()
        completion(
          .success(
            ProductUpdateReplacementReceipt(
              replaced: true, relaunched: true, destination: "/tmp/Vibecrafted.app", detail: "test")))
      },
      closeUI: { closed += 1 })
    coordinator.checkForUpdates()
    try wait { coordinator.progress.canInstall }
    coordinator.installUpdate()
    try wait { coordinator.progress.phase == .success || coordinator.progress.phase == .restarting }
    try require(replaces == 1, "newer app did not call the replacement owner")
    try require(closed == 1, "replacement did not close the UI after the helper was armed")
    try require(packs == 0 || coordinator.progress.claimsHealthy, "pack published under the old app unexpectedly")
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
        fetchBytes: { _, _ in {} },
        downloadFile: { _, _, _ in {} },
        verifyFeedSignature: { _, _, _ in .success(()) },
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
        verifyFeedSignature: { _, _, _ in .success(()) },
        verifyCandidate: { _, _, _, completion in
          completion(.success(validProof()))
          return {}
        },
        installPack: { _, _, _ in
          { cancelCount += 1 }
        },
        replaceApp: { _, _ in {} },
        extractApp: nil,
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
        fetchBytes: { _, _ in {} },
        downloadFile: { _, _, _ in {} },
        verifyFeedSignature: { _, _, _ in .success(()) },
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
      helperURL: nil)
    switch replaceProductUpdateApp(request) {
    case .failure(.sourceMissing):
      break
    default:
      throw Failure(message: "missing source was not a replace failure")
    }
    try require(!FileManager.default.fileExists(atPath: dest.path), "failed replace created a destination")
  }

  static func testReplacementOwnerCopiesFixtureApp() throws {
    let source = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-src-\(UUID().uuidString).app", isDirectory: true)
    let dest = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-dst-\(UUID().uuidString).app", isDirectory: true)
    let receipt = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-receipt-\(UUID().uuidString).json")
    try FileManager.default.createDirectory(at: source, withIntermediateDirectories: true)
    try Data("payload".utf8).write(to: source.appendingPathComponent("Contents.txt"))
    let request = ProductUpdateReplacementRequest(
      waitPID: nil, sourceApp: source, destinationApp: dest, relaunch: false,
      receiptURL: receipt, helperURL: nil)
    switch replaceProductUpdateApp(request) {
    case .success(let result):
      try require(result.replaced, "replace did not report replaced")
      try require(
        FileManager.default.fileExists(atPath: dest.appendingPathComponent("Contents.txt").path),
        "replace did not copy payload")
    case .failure(let error):
      throw Failure(message: error.localizedDescription)
    }
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
    try testReplacementOwnerRefusesMissingSource()
    try testReplacementOwnerCopiesFixtureApp()
    try testProgressCopyHasNoArchitectureJargon()
    print("ProductUpdatePolicyTests passed")
  }
}
