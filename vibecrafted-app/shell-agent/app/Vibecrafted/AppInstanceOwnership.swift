import Foundation

/// Process-wide user App instance ownership for one bundle identifier.
///
/// `StatusItemController.install()` only prevents a second status item on the
/// same controller. Two bundles can share `io.vetcoders.vibecrafted`
/// (`/Applications/Vibecrafted.app` and an updater capture `displaced.app`).
/// This helper ranks live peers and tells the host whether to become the
/// owner or activate an existing proper instance. It never kills another
/// process and never deletes backup bundles.

struct AppInstancePeer: Equatable {
  var pid: Int32
  var bundleURL: URL
}

enum AppBundleRole: Int, Comparable {
  case canonical = 0
  case other = 1
  case displaced = 2

  static func < (lhs: AppBundleRole, rhs: AppBundleRole) -> Bool {
    lhs.rawValue < rhs.rawValue
  }
}

enum AppInstanceDecision: Equatable {
  case becomeOwner
  case activateExisting(pid: Int32, bundleURL: URL)
}

struct AppInstanceContext: Equatable {
  var selfPid: Int32
  var selfBundleURL: URL
  var arguments: [String]
  var peers: [AppInstancePeer]
  var lockOwnerPid: Int32?
  var livePids: Set<Int32>
}

enum AppInstanceOwnership {
  static let productBundleIdentifier = "io.vetcoders.vibecrafted"
  static let specialLaunchFlags = ["--uninstall", "--bootstrap-only"]

  static func isSpecialLaunch(arguments: [String]) -> Bool {
    arguments.contains(where: { specialLaunchFlags.contains($0) })
  }

  static func classifyBundleURL(_ url: URL) -> AppBundleRole {
    let path = url.standardizedFileURL.path
    if isDisplacedBundlePath(path) {
      return .displaced
    }
    if isCanonicalApplicationsPath(path) {
      return .canonical
    }
    return .other
  }

  static func isDisplacedBundlePath(_ path: String) -> Bool {
    let standardized = (path as NSString).standardizingPath
    if standardized.hasSuffix("/displaced.app") || standardized.hasSuffix("/displaced.app/") {
      return true
    }
    return standardized.contains("/.vc-update-capture-")
      || standardized.contains("/.vc-update-capture")
  }

  static func isCanonicalApplicationsPath(_ path: String) -> Bool {
    let standardized = (path as NSString).standardizingPath
    return standardized == "/Applications/Vibecrafted.app"
      || standardized == "/Applications/VibecraftedDev.app"
  }

  static func decide(_ context: AppInstanceContext) -> AppInstanceDecision {
    if isSpecialLaunch(arguments: context.arguments) {
      return .becomeOwner
    }

    let livePeers = context.peers.filter { peer in
      peer.pid != context.selfPid && context.livePids.contains(peer.pid)
    }
    let lockOwnerIsLive = context.lockOwnerPid.map { context.livePids.contains($0) } ?? false
    let lockPid = lockOwnerIsLive ? context.lockOwnerPid : nil

    var candidates = livePeers
    candidates.append(AppInstancePeer(pid: context.selfPid, bundleURL: context.selfBundleURL))

    let winner = candidates.min { lhs, rhs in
      let leftRole = classifyBundleURL(lhs.bundleURL)
      let rightRole = classifyBundleURL(rhs.bundleURL)
      if leftRole != rightRole {
        return leftRole < rightRole
      }
      let leftLock = lockPid == lhs.pid
      let rightLock = lockPid == rhs.pid
      if leftLock != rightLock {
        return leftLock && !rightLock
      }
      if lhs.pid != rhs.pid {
        return lhs.pid < rhs.pid
      }
      return lhs.bundleURL.path < rhs.bundleURL.path
    }

    guard let winner, winner.pid != context.selfPid else {
      return .becomeOwner
    }
    return .activateExisting(pid: winner.pid, bundleURL: winner.bundleURL)
  }

  static func lockRecord(pid: Int32, bundleURL: URL) -> String {
    "pid=\(pid)\npath=\(bundleURL.standardizedFileURL.path)\n"
  }

  static func parseLockRecord(_ body: String) -> (pid: Int32, path: String)? {
    var pid: Int32?
    var path: String?
    for line in body.split(whereSeparator: \.isNewline) {
      let text = String(line)
      if let value = text.stripPrefix("pid=") {
        pid = Int32(value)
      } else if let value = text.stripPrefix("path=") {
        path = value
      }
    }
    guard let pid, let path, !path.isEmpty else { return nil }
    return (pid, path)
  }

  static func lockFileURL(bundleIdentifier: String, home: URL) -> URL {
    home
      .appendingPathComponent("Library/Application Support", isDirectory: true)
      .appendingPathComponent(bundleIdentifier, isDirectory: true)
      .appendingPathComponent("instance.lock")
  }
}

private extension String {
  func stripPrefix(_ prefix: String) -> String? {
    guard hasPrefix(prefix) else { return nil }
    return String(dropFirst(prefix.count))
  }
}
