import Foundation

/// W1 authored contract, NOT_ASSESSED under COMPILE_EMBARGO.
/// Compiles with ProductUpdatePolicy.swift, ProductUpdateCoordinator.swift and
/// RuntimePackMenuPolicy.swift — not a copied imitation.
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

  static func provisionedChannel() -> ProductUpdateChannel {
    resolveProductUpdateChannel(
      feedURLString: "https://updates.example.test/release-output.json",
      publicKeyPresent: true,
      appReplacementHelperPresent: true)
  }

  static func candidate(
    generation: String = generation,
    sourceRevision: String = source,
    keyID: String = "vibecrafted-signing-v1",
    signatureValid: Bool = true,
    notarized: Bool = true,
    bundle: String = "io.vetcoders.vibecrafted",
    packPath: String = "/tmp/staged/Vibecrafted_RuntimePack_4.4.0.tar.gz",
    appPath: String = "/tmp/staged/Vibecrafted.app"
  ) -> ProductUpdateCandidate {
    ProductUpdateCandidate(
      generation: generation,
      sourceRevision: sourceRevision,
      terminalRevision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      frameRevision: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      bundleIdentifier: bundle,
      keyID: keyID,
      signatureValid: signatureValid,
      notarized: notarized,
      packPath: packPath,
      appPath: appPath)
  }

  static func existsStaged(_ path: String) -> Bool {
    path.hasSuffix(".tar.gz") || path.hasSuffix(".app")
  }

  static func matchingInstalled(pack: String? = generation) -> ProductUpdateIdentity {
    ProductUpdateIdentity(
      appGeneration: generation, packGeneration: pack, sourceRevision: source)
  }

  static func previousInstalled() -> ProductUpdateIdentity {
    ProductUpdateIdentity(
      appGeneration: previous, packGeneration: previous, sourceRevision: "e37be2c9fb1c5f23d749fbdd786cd08cdf888617")
  }

  static func testMissingFeedIsUnavailable() throws {
    let channel = resolveProductUpdateChannel(
      feedURLString: nil, publicKeyPresent: true, appReplacementHelperPresent: true)
    try require(channel.feedURL == nil, "empty feed became a URL")
    switch admitProductUpdateCandidate(channel: channel, candidate: candidate(), fileExists: existsStaged)
    {
    case .unavailable(let reason):
      try require(reason.contains("VCUpdateFeedURL"), "missing feed did not name the plist key")
    default:
      throw Failure(message: "missing feed was not unavailable")
    }
    let progress = deriveProductUpdateProgress(
      phase: .unavailable,
      installed: previousInstalled(),
      candidate: nil,
      detail: channel.provisioningGap)
    try require(progress.phase == .unavailable && !progress.claimsHealthy, "unavailable claimed healthy")
    try require(progress.canRetry, "unavailable must be retryable")
    try require(!progress.summary.contains("spinner"), "copy invented a spinner")
  }

  static func testMissingHelperIsUnavailable() throws {
    let channel = resolveProductUpdateChannel(
      feedURLString: "https://updates.example.test/release-output.json",
      publicKeyPresent: true,
      appReplacementHelperPresent: false)
    switch admitProductUpdateCandidate(channel: channel, candidate: candidate(), fileExists: existsStaged)
    {
    case .unavailable(let reason):
      try require(reason.contains("vc-app-update"), "missing helper did not name the helper")
    default:
      throw Failure(message: "missing helper was admitted")
    }
  }

  static func testIdentityMismatchIsRefused() throws {
    let admitted = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(generation: "4.4.0+gdeadbeef", sourceRevision: source),
      fileExists: existsStaged)
    guard case .refuse(let reason) = admitted else {
      throw Failure(message: "mismatched App/pack identity was admitted")
    }
    try require(reason.contains("unchanged"), "refusal did not retain the installed generation")
    let unsigned = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(signatureValid: false),
      fileExists: existsStaged)
    guard case .refuse = unsigned else {
      throw Failure(message: "unsigned candidate was admitted")
    }
    let foreignKey = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(keyID: "sparkle-ed25519"),
      fileExists: existsStaged)
    guard case .refuse = foreignKey else {
      throw Failure(message: "foreign key was admitted")
    }
    let unnotarized = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(notarized: false),
      fileExists: existsStaged)
    guard case .refuse = unnotarized else {
      throw Failure(message: "unnotarized candidate was admitted")
    }
    let foreignBundle = admitProductUpdateCandidate(
      channel: provisionedChannel(),
      candidate: candidate(bundle: "io.vetcoders.codescribe"),
      fileExists: existsStaged)
    guard case .refuse = foreignBundle else {
      throw Failure(message: "foreign bundle was admitted")
    }
  }

  static func testInterruptedUpdateIsRetained() throws {
    let progress = deriveProductUpdateProgress(
      phase: .retained, installed: previousInstalled(), candidate: candidate())
    try require(progress.phase == .retained && !progress.claimsHealthy, "retention claimed healthy")
    try require(progress.installedGeneration == previous, "retention lost the previous generation")
    try require(progress.summary.contains("previous working generation"), "retention copy is unclear")
  }

  static func testHealthyRequiresMatchingIdentity() throws {
    try require(
      productUpdateClaimsHealthy(installed: matchingInstalled(), candidate: candidate()),
      "matching identity was not healthy")
    try require(
      !productUpdateClaimsHealthy(installed: previousInstalled(), candidate: candidate()),
      "mixed generation claimed healthy")
    try require(
      !productUpdateRunningAppMatchesCandidate(installed: previousInstalled(), candidate: candidate()),
      "older App was treated as the candidate")
    try require(
      productUpdateRunningAppMatchesCandidate(
        installed: matchingInstalled(pack: previous), candidate: candidate()),
      "same App with drifted pack should still match the running App")
  }

  static func feedJSON(signatureValid: Bool, pack: String, app: String) -> Data {
    """
    {
      "schema": "io.vetcoders.vibecrafted.release-output.v1",
      "signature_policy": {"algorithm": "rsa-pkcs1v15-sha256", "key_id": "vibecrafted-signing-v1", "spki_sha256": "521ed59d3c446c540afe1557c2dbc39c9c190775f99896b2b65206c32814b25b"},
      "product": {"version": "4.4.0"},
      "source_revisions": {"vibecrafted": "\(source)", "vc-terminal": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "vc-frame": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
      "runtime_pack": {"path": "Vibecrafted_RuntimePack_4.4.0.tar.gz"},
      "dmg": {"path": "Vibecrafted_4.4.0.dmg"},
      "notarization": {"app": {"ticket": true, "gatekeeper": true}, "dmg": {"ticket": true, "gatekeeper": true}},
      "signature_valid": \(signatureValid),
      "assets": {"runtime_pack": "\(pack)", "app": "\(app)"}
    }
    """.data(using: .utf8)!
  }

  static func testCoordinatorMissingFeedNeverInstalls() throws {
    var installedCalls = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: {
          resolveProductUpdateChannel(
            feedURLString: nil, publicKeyPresent: true, appReplacementHelperPresent: true)
        },
        installed: {
          installedCalls += 1
          return previousInstalled()
        },
        fileExists: existsStaged,
        fetchFeed: { _, _ in fatalError("missing feed must not fetch") },
        installCandidate: { _, _ in fatalError("missing feed must not install") },
        requestUIOnlyQuit: { fatalError("unavailable must not quit") },
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .unavailable }
    try require(!coordinator.progress.claimsHealthy, "unavailable claimed healthy")
    try require(installedCalls >= 1, "installed identity was not read")
  }

  static func testCoordinatorRefuseDoesNotInstall() throws {
    var installs = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        fileExists: existsStaged,
        fetchFeed: { _, completion in
          completion(.success(feedJSON(signatureValid: false, pack: "/tmp/staged/pack.tar.gz", app: "/tmp/staged/Vibecrafted.app")))
        },
        installCandidate: { _, _ in installs += 1 },
        requestUIOnlyQuit: {},
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .refused }
    try require(installs == 0, "refused candidate reached the installer")
    try require(!coordinator.progress.claimsHealthy, "refusal claimed healthy")
  }

  static func testCoordinatorNewerCandidateDoesNotPublishPack() throws {
    var installs = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        fileExists: existsStaged,
        fetchFeed: { _, completion in
          completion(.success(feedJSON(
            signatureValid: true,
            pack: "/tmp/staged/Vibecrafted_RuntimePack_4.4.0.tar.gz",
            app: "/tmp/staged/Vibecrafted.app")))
        },
        installCandidate: { _, _ in installs += 1 },
        requestUIOnlyQuit: {},
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .readyToReplace }
    try require(installs == 0, "a newer App published a pack under the old App")
    try require(!coordinator.progress.claimsHealthy, "ready-to-replace claimed healthy")
    try require(coordinator.progress.requestsUIOnlyQuit, "replacement did not offer UI-only quit")
    try require(coordinator.progress.installedGeneration == previous, "ready-to-replace lost the previous generation")
  }

  static func testCoordinatorRetainsFailedSameAppRepair() throws {
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { matchingInstalled(pack: previous) },
        fileExists: existsStaged,
        fetchFeed: { _, completion in
          completion(.success(feedJSON(
            signatureValid: true,
            pack: "/tmp/staged/Vibecrafted_RuntimePack_4.4.0.tar.gz",
            app: "/tmp/staged/Vibecrafted.app")))
        },
        installCandidate: { _, completion in
          completion(.failure(NSError(domain: "test", code: 1, userInfo: [NSLocalizedDescriptionKey: "lease recovered"])))
        },
        requestUIOnlyQuit: {},
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .retained }
    try require(coordinator.progress.installedGeneration == previous, "failure replaced the previous generation")
    try require(!coordinator.progress.claimsHealthy, "failed install claimed healthy")
  }

  static func testCoordinatorInterruptRetains() throws {
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        fileExists: existsStaged,
        fetchFeed: { _, _ in },
        installCandidate: { _, _ in fatalError("interrupted check must not install") },
        requestUIOnlyQuit: {},
        checkTimeout: 30))
    coordinator.checkForUpdates()
    try require(coordinator.progress.phase == .checking, "check did not start")
    coordinator.interrupt()
    try require(coordinator.progress.phase == .retained, "interrupt did not retain")
    try require(!coordinator.isBusy, "interrupt left the coordinator busy")
  }

  static func testCoordinatorFeedTimeoutIsBounded() throws {
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { previousInstalled() },
        fileExists: existsStaged,
        fetchFeed: { _, _ in },
        installCandidate: { _, _ in fatalError("timed-out check must not install") },
        requestUIOnlyQuit: {},
        checkTimeout: 0.05))
    coordinator.checkForUpdates()
    try wait(seconds: 2) { coordinator.progress.phase == .error }
    try require(!coordinator.progress.claimsHealthy, "timeout claimed healthy")
    try require(coordinator.progress.canRetry, "timeout must be retryable")
    try require(coordinator.progress.summary.contains("15s") || coordinator.progress.summary.contains("0s")
      || coordinator.progress.summary.contains("did not answer"),
      "timeout copy was not bounded")
  }

  static func testUIOnlyQuitIsRequestedOnlyAfterHealthy() throws {
    var quit = 0
    var installs = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { matchingInstalled() },
        fileExists: existsStaged,
        fetchFeed: { _, completion in
          completion(.success(feedJSON(
            signatureValid: true,
            pack: "/tmp/staged/Vibecrafted_RuntimePack_4.4.0.tar.gz",
            app: "/tmp/staged/Vibecrafted.app")))
        },
        installCandidate: { _, completion in
          installs += 1
          completion(.success(matchingInstalled()))
        },
        requestUIOnlyQuit: { quit += 1 },
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .success }
    try require(installs == 0, "already-healthy identity re-published the pack")
    try require(coordinator.progress.claimsHealthy, "matching install did not claim healthy")
    try require(coordinator.progress.requestsUIOnlyQuit, "success did not offer UI-only quit")
    coordinator.quitUIOnly()
    try require(quit == 1, "UI-only quit was not requested")
  }

  static func testSameAppRepairCanBecomeHealthy() throws {
    var quit = 0
    let coordinator = ProductUpdateCoordinator(
      dependencies: ProductUpdateCoordinator.Dependencies(
        channel: { provisionedChannel() },
        installed: { matchingInstalled(pack: previous) },
        fileExists: existsStaged,
        fetchFeed: { _, completion in
          completion(.success(feedJSON(
            signatureValid: true,
            pack: "/tmp/staged/Vibecrafted_RuntimePack_4.4.0.tar.gz",
            app: "/tmp/staged/Vibecrafted.app")))
        },
        installCandidate: { admitted, completion in
          completion(.success(ProductUpdateIdentity(
            appGeneration: admitted.generation,
            packGeneration: admitted.generation,
            sourceRevision: admitted.sourceRevision)))
        },
        requestUIOnlyQuit: { quit += 1 },
        checkTimeout: 15))
    coordinator.checkForUpdates()
    try wait { coordinator.progress.phase == .success }
    try require(coordinator.progress.claimsHealthy, "matching repair did not claim healthy")
    coordinator.quitUIOnly()
    try require(quit == 1, "healthy repair did not offer UI-only quit")
  }

  static func main() throws {
    try testMissingFeedIsUnavailable()
    try testMissingHelperIsUnavailable()
    try testIdentityMismatchIsRefused()
    try testInterruptedUpdateIsRetained()
    try testHealthyRequiresMatchingIdentity()
    try testCoordinatorMissingFeedNeverInstalls()
    try testCoordinatorRefuseDoesNotInstall()
    try testCoordinatorNewerCandidateDoesNotPublishPack()
    try testCoordinatorRetainsFailedSameAppRepair()
    try testCoordinatorInterruptRetains()
    try testCoordinatorFeedTimeoutIsBounded()
    try testUIOnlyQuitIsRequestedOnlyAfterHealthy()
    try testSameAppRepairCanBecomeHealthy()
    print("ProductUpdatePolicyTests passed")
  }
}
