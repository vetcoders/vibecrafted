import Foundation

/// Behavioral harness for App-instance ownership. Compiles with the product
/// role model only — no AppKit, no runtime signature probes or process killing.
@main
struct AppInstanceOwnershipTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func main() throws {
    try terminalCannotOwnMain()
    try mainAndTerminalShareBundleWithoutSharingOwnership()
    try missingAndForeignRoleEvidenceFailsClosed()
    try reusedLockPidCannotConferMainRole()
    try devIdentityKeepsCanonicalPreference()
    try displacedCaptureIsNeverPreferredOverApplications()
    try concurrentEqualPathsPreferLiveLockThenLowestPid()
    try staleLockOwnerIsIgnored()
    try specialCliModesSkipPeerHandoff()
    try lockRecordRoundTripAndMalformedStayHonest()
    print("AppInstanceOwnershipTests passed")
  }

  static func terminalCannotOwnMain() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let decision = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 60000,
        selfBundleURL: applications,
        arguments: [],
        peers: [AppInstancePeer(
          pid: 14649, bundleURL: applications,
          executableURL: applications.appendingPathComponent("Contents/MacOS/vc-terminal"),
          signedMainExecutableURL: applications.appendingPathComponent("Contents/MacOS/Vibecrafted"))],
        lockOwnerPid: 14649,
        livePids: [14649, 60000]))
    try require(decision == .becomeOwner, "shared-bundle Terminal must not own Main")
  }

  static func mainAndTerminalShareBundleWithoutSharingOwnership() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    var terminal = mainPeer(pid: 5, bundleURL: applications)
    terminal.executableURL = applications.appendingPathComponent("Contents/MacOS/vc-terminal")
    for lock in [nil, 5, 50] as [Int32?] {
      let decision = AppInstanceOwnership.decide(
        mainContext(
          selfPid: 80, selfBundleURL: applications, arguments: [],
          peers: [terminal, mainPeer(pid: 50, bundleURL: applications)],
          lockOwnerPid: lock, livePids: [5, 50, 80]))
      try require(
        decision == .activateExisting(pid: 50, bundleURL: applications),
        "only a legitimate Main may win shared-ID deduplication, including with a Terminal lock")
    }
    let selfLock = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80, selfBundleURL: applications, arguments: [],
        peers: [mainPeer(pid: 50, bundleURL: applications)],
        lockOwnerPid: 80, livePids: [50, 80]))
    try require(selfLock == .becomeOwner, "a verified current Main retains its live lock preference")
  }

  static func missingAndForeignRoleEvidenceFailsClosed() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let valid = mainPeer(pid: 5, bundleURL: applications)
    var missingExecutable = valid
    missingExecutable.executableURL = nil
    var missingSignature = valid
    missingSignature.signedMainExecutableURL = nil
    var foreignExecutable = valid
    foreignExecutable.executableURL = URL(fileURLWithPath: "/tmp/Vibecrafted")
    var foreignSignature = valid
    foreignSignature.signedMainExecutableURL = URL(
      fileURLWithPath: "/tmp/Other.app/Contents/MacOS/Vibecrafted")
    var outsideBundle = valid
    outsideBundle.executableURL = URL(fileURLWithPath: "/tmp/Vibecrafted")
    outsideBundle.signedMainExecutableURL = outsideBundle.executableURL
    var malformed = valid
    malformed.executableURL = URL(string: "https://example.invalid/Vibecrafted")
    var remote = valid
    remote.executableURL = URL(string: "file://foreign/Applications/Vibecrafted.app/Contents/MacOS/Vibecrafted")
    var invalidPid = valid
    invalidPid.pid = -1
    for peer in [
      AppInstancePeer(pid: 5, bundleURL: applications), missingExecutable, missingSignature,
      foreignExecutable, foreignSignature, outsideBundle, malformed, remote, invalidPid,
    ] {
      try require(!AppInstanceOwnership.isMainHost(peer), "incomplete/foreign role evidence must be rejected")
      let decision = AppInstanceOwnership.decide(
        mainContext(
          selfPid: 80, selfBundleURL: applications, arguments: [], peers: [peer],
          lockOwnerPid: peer.pid, livePids: [peer.pid, 80]))
      try require(decision == .becomeOwner, "invalid evidence cannot own Main even with a live lock")
    }
    let unknownSelf = AppInstanceOwnership.decide(
      AppInstanceContext(
        selfPid: 80, selfBundleURL: applications, arguments: [], peers: [],
        lockOwnerPid: nil, livePids: [80]))
    try require(unknownSelf == .refuseOwnership, "unknown current executable must not claim Main")
  }

  static func reusedLockPidCannotConferMainRole() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let deadMain = mainPeer(pid: 5, bundleURL: applications)
    let missingPeer = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80, selfBundleURL: applications, arguments: [], peers: [],
        lockOwnerPid: 5, livePids: [5, 80]))
    try require(missingPeer == .becomeOwner, "a reused PID with no admitted Main peer is not an owner")
    let deadPeer = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80, selfBundleURL: applications, arguments: [], peers: [deadMain],
        lockOwnerPid: 5, livePids: [80]))
    try require(deadPeer == .becomeOwner, "a dead Main with old lock evidence cannot own")
  }

  static func devIdentityKeepsCanonicalPreference() throws {
    let dev = URL(fileURLWithPath: "/Applications/VibecraftedDev.app")
    let other = URL(fileURLWithPath: "/tmp/VibecraftedDev.app")
    let decision = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80, selfBundleURL: dev, arguments: [],
        peers: [mainPeer(pid: 5, bundleURL: other)], lockOwnerPid: 5, livePids: [5, 80]))
    try require(decision == .becomeOwner, "canonical Dev identity retains its existing ranking")
  }

  static func mainPeer(pid: Int32, bundleURL: URL) -> AppInstancePeer {
    let executable = bundleURL.appendingPathComponent("Contents/MacOS/Vibecrafted")
    return AppInstancePeer(
      pid: pid, bundleURL: bundleURL, executableURL: executable,
      signedMainExecutableURL: executable)
  }

  static func mainContext(
    selfPid: Int32, selfBundleURL: URL, arguments: [String], peers: [AppInstancePeer],
    lockOwnerPid: Int32?, livePids: Set<Int32>
  ) -> AppInstanceContext {
    let executable = selfBundleURL.appendingPathComponent("Contents/MacOS/Vibecrafted")
    return AppInstanceContext(
      selfPid: selfPid, selfBundleURL: selfBundleURL, arguments: arguments,
      peers: peers, lockOwnerPid: lockOwnerPid, livePids: livePids,
      selfExecutableURL: executable, selfSignedMainExecutableURL: executable)
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
      mainContext(
        selfPid: 900,
        selfBundleURL: displaced,
        arguments: ["/Applications/.vc-update-capture-x/displaced.app/Contents/MacOS/Vibecrafted"],
        peers: [mainPeer(pid: 25372, bundleURL: applications)],
        lockOwnerPid: 900,
        livePids: [900, 25372]))
    try require(
      fromBackup == .activateExisting(pid: 25372, bundleURL: applications),
      "later launch of a displaced bundle must activate the proper Applications instance")

    let fromCanonical = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 400,
        selfBundleURL: applications,
        arguments: ["/Applications/Vibecrafted.app/Contents/MacOS/Vibecrafted"],
        peers: [mainPeer(pid: 25372, bundleURL: displaced)],
        lockOwnerPid: 25372,
        livePids: [400, 25372]))
    try require(
      fromCanonical == .becomeOwner,
      "canonical Applications launch must own the user instance even if a displaced peer is live")
  }

  static func concurrentEqualPathsPreferLiveLockThenLowestPid() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let locked = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80,
        selfBundleURL: applications,
        arguments: [],
        peers: [mainPeer(pid: 50, bundleURL: applications)],
        lockOwnerPid: 50,
        livePids: [50, 80]))
    try require(
      locked == .activateExisting(pid: 50, bundleURL: applications),
      "concurrent canonical launches must yield to the live lock owner")

    let lowestPid = AppInstanceOwnership.decide(
      mainContext(
        selfPid: 80,
        selfBundleURL: applications,
        arguments: [],
        peers: [mainPeer(pid: 50, bundleURL: applications)],
        lockOwnerPid: nil,
        livePids: [50, 80]))
    try require(
      lowestPid == .activateExisting(pid: 50, bundleURL: applications),
      "without a lock, concurrent canonical launches must yield to the lower pid")
  }

  static func staleLockOwnerIsIgnored() throws {
    let applications = URL(fileURLWithPath: "/Applications/Vibecrafted.app")
    let decision = AppInstanceOwnership.decide(
      mainContext(
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
        mainContext(
          selfPid: 8,
          selfBundleURL: displaced,
          arguments: ["Vibecrafted", flag],
          peers: [mainPeer(pid: 1, bundleURL: applications)],
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
