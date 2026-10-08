import Foundation

/// Behavioral harness for App-instance ownership. Compiles with the product
/// Foundation helper only — no AppKit, no process killing.
@main
struct AppInstanceOwnershipTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func main() throws {
    try displacedCaptureIsNeverPreferredOverApplications()
    try concurrentEqualPathsPreferLiveLockThenLowestPid()
    try staleLockOwnerIsIgnored()
    try specialCliModesSkipPeerHandoff()
    try lockRecordRoundTripAndMalformedStayHonest()
    print("AppInstanceOwnershipTests passed")
  }

  static func displacedCaptureIsNeverPreferredOverApplications() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let displaced = URL(
      fileURLWithPath:
        "/Applications/.vc-update-capture-release-85fb-260913-approved-clean/displaced.app")
    try require(
      AppInstanceOwnership.classifyBundleURL(displaced) == .displaced,
      "updater capture bundle must classify as displaced")
    try require(
      AppInstanceOwnership.classifyBundleURL(applications) == .canonical,
      "/Applications/Vibecrafted.app must classify as canonical")
    try require(
      !AppInstanceOwnership.isDisplacedBundlePath(applications.path),
      "canonical Applications path must not be treated as a backup")

    let fromBackup = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 900,
        selfBundleURL: displaced,
        arguments: ["/Applications/.vc-update-capture-x/displaced.app/Contents/MacOS/Vibecrafted"],
        peers: [AppInstancePeer(pid: 25372, bundleURL: applications)],
        lockOwnerPid: 900,
        livePids: [900, 25372]))
    try require(
      fromBackup == .activateExisting(pid: 25372, bundleURL: applications),
      "later launch of a displaced bundle must activate the proper Applications instance")

    let fromCanonical = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 400,
        selfBundleURL: applications,
        arguments: ["/Applications/Vibecrafted.app/Contents/MacOS/Vibecrafted"],
        peers: [AppInstancePeer(pid: 25372, bundleURL: displaced)],
        lockOwnerPid: 25372,
        livePids: [400, 25372]))
    try require(
      fromCanonical == .becomeOwner,
      "canonical Applications launch must own the user instance even if a displaced peer is live")
  }

  static func concurrentEqualPathsPreferLiveLockThenLowestPid() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let locked = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 80,
        selfBundleURL: applications,
        arguments: [],
        peers: [AppInstancePeer(pid: 50, bundleURL: applications)],
        lockOwnerPid: 50,
        livePids: [50, 80]))
    try require(
      locked == .activateExisting(pid: 50, bundleURL: applications),
      "concurrent canonical launches must yield to the live lock owner")

    let lowestPid = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 80,
        selfBundleURL: applications,
        arguments: [],
        peers: [AppInstancePeer(pid: 50, bundleURL: applications)],
        lockOwnerPid: nil,
        livePids: [50, 80]))
    try require(
      lowestPid == .activateExisting(pid: 50, bundleURL: applications),
      "without a lock, concurrent canonical launches must yield to the lower pid")
  }

  static func staleLockOwnerIsIgnored() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let decision = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 12,
        selfBundleURL: applications,
        arguments: [],
        peers: [],
        lockOwnerPid: 99999,
        livePids: [12]))
    try require(
      decision == .becomeOwner,
      "a stale lock pid that is not live must not block the current launch")
  }

  static func specialCliModesSkipPeerHandoff() throws {
    let displaced = URL(
      fileURLWithPath: "/Applications/.vc-update-capture-x/displaced.app")
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    for flag in ["--uninstall", "--bootstrap-only"] {
      let decision = AppInstanceOwnership.decide(
        AppInstanceContext(
          selfPid: 8,
          selfBundleURL: displaced,
          arguments: ["Vibecrafted", flag],
          peers: [AppInstancePeer(pid: 1, bundleURL: applications)],
          lockOwnerPid: 1,
          livePids: [8, 1]))
      try require(
        decision == .becomeOwner,
        "\(flag) must not hand off to another GUI instance")
    }
  }

  static func lockRecordRoundTripAndMalformedStayHonest() throws {
    let url = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let body = AppInstanceOwnership.lockRecord(pid: 42, bundleURL: url)
    let parsed = AppInstanceOwnership.parseLockRecord(body)
    try require(parsed?.pid == 42, "lock pid must round-trip")
    try require(
      parsed?.path == "/Applications/Vibecrafted.app",
      "lock path must round-trip")
    try require(
      AppInstanceOwnership.parseLockRecord("not a lock") == nil,
      "malformed lock records must not invent an owner")
    let home = URL(fileURLWithPath: "/tmp/fake-home")
    let lock = AppInstanceOwnership.lockFileURL(
      bundleIdentifier: "io.vetcoders.vibecrafted", home: home)
    try require(
      lock.path.hasSuffix("Library/Application Support/io.vetcoders.vibecrafted/instance.lock"),
      "lock path must stay inside the bundle's application-support namespace")
  }
}
