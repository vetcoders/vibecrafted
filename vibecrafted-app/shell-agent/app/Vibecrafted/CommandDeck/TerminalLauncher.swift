import Foundation

/// Consumes one already-adopted CanonicalRuntimeInstall and its environment.
/// AppDelegate supplies these values together; this leaf never resolves an active pointer,
/// selects a host, composes PATH, or substitutes the App bundle's generation.
struct TerminalLauncher {
  struct Specification: Equatable, Sendable {
    let generationRoot: URL
    let terminalHost: URL
    let executable: URL
    let arguments: [String]
    let workingDirectory: URL
    let environment: [String: String]

    init(
      generationRoot: URL, terminal: URL, terminalHost: URL,
      primaryShell: URL, start: URL, workingDirectory: URL,
      environment: [String: String]
    ) throws {
      let paths = [generationRoot, terminal, terminalHost, primaryShell, start, workingDirectory]
      guard paths.allSatisfy({
        $0.isFileURL && $0.baseURL == nil && $0.path.hasPrefix("/")
          && !$0.path.utf8.contains(0)
          && ($0.host == nil || $0.host == "" || $0.host == "localhost")
          && $0.query == nil && $0.fragment == nil
      }) else { throw LaunchError.invalidPath }
      // Consistency checks on the owner's answer, not a second resolver.
      guard environment["VIBECRAFTED_RUNTIME_ROOT"] == generationRoot.path,
        environment["VIBECRAFTED_ROOT"] == generationRoot.path,
        environment["VIBECRAFTED_TERMINAL_HOST"] == terminalHost.path
      else { throw LaunchError.inconsistentOwnerValues }
      guard environment.allSatisfy({ key, value in
        !key.isEmpty && !key.contains("=") && !key.utf8.contains(0) && !value.utf8.contains(0)
      }) else { throw LaunchError.invalidEnvironment }

      self.terminalHost = terminalHost
      self.generationRoot = generationRoot
      executable = terminal
      arguments = ["-e", primaryShell.path]
      self.workingDirectory = workingDirectory
      self.environment = environment
    }
  }

  enum LaunchError: Error {
    case invalidPath
    case inconsistentOwnerValues
    case invalidEnvironment
    case workingDirectoryUnavailable
    case invalidHostReceipt
  }

  /// Spawn evidence only: a PID does not prove that the window became ready.
  /// Keep in memory; the specification contains the owner's environment and
  /// must not be dumped into logs or web responses.
  struct Receipt: Equatable, Sendable {
    let launchID = UUID()
    let processIdentifier: Int32
    let specification: Specification
  }

  /// The one launch receipt the generation wrapper's admission helper writes.
  /// `fresh`: the wrapper PID execs into the single native host. `reused`: the
  /// admitted host received one `create-window` request. Neither proves a
  /// window, and neither ever proves that an agent started.
  struct HostReceipt: Equatable, Sendable {
    enum Kind: String, Equatable, Sendable { case fresh, reused }
    let kind: Kind
    let hostProcessIdentifier: Int32
    let hostStartToken: String

    static let schema = "vibecrafted.terminal-launch-receipt.v1"

    init(data: Data, terminalHost: URL) throws {
      guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
        object["schema"] as? String == Self.schema,
        let kind = (object["kind"] as? String).flatMap(Kind.init(rawValue:)),
        let pid = object["host_pid"] as? Int, pid > 0, pid <= Int(Int32.max),
        let start = object["host_start"] as? String, !start.isEmpty,
        object["executable"] as? String == terminalHost.path,
        object["agent_start_proven"] as? Bool == false
      else { throw LaunchError.invalidHostReceipt }
      self.kind = kind
      hostProcessIdentifier = Int32(pid)
      hostStartToken = start
    }
  }

  /// Which process currently answers for one launch. Pure: the kernel start
  /// token lookup is injected, so reuse is revalidated on every observation.
  enum HostState: Equatable, Sendable {
    /// The launcher itself may still become (or already be) the fresh host.
    case launching(Int32)
    /// The launcher exited 0 after delivering to this exact, still-live host.
    case reused(Int32)
    /// Nothing answers: launcher refused (status), host gone, or receipt unproven.
    case ended(Int32?)

    var hostProcessIdentifier: Int32? {
      switch self {
      case .launching(let pid), .reused(let pid): return pid
      case .ended: return nil
      }
    }
  }

  static func hostState(launcherPID: Int32, launcherRunning: Bool, launcherStatus: Int32?,
                        receipt: HostReceipt?, startToken: (Int32) -> String?) -> HostState {
    if let receipt, receipt.kind == .fresh, receipt.hostProcessIdentifier != launcherPID {
      return .ended(nil)
    }
    if launcherRunning { return .launching(launcherPID) }
    guard launcherStatus == 0 else { return .ended(launcherStatus) }
    // A successful IPC child exit is not a GUI end: the reused host answers,
    // but only while its kernel identity still matches the receipt.
    if let receipt, receipt.kind == .reused,
      startToken(receipt.hostProcessIdentifier) == receipt.hostStartToken
    {
      return .reused(receipt.hostProcessIdentifier)
    }
    return .ended(launcherStatus)
  }

  /// Kernel process start token, formatted exactly as the admission helper does.
  static func kernelStartToken(_ pid: Int32) -> String? {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    guard pid > 0, proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, size) == size,
      info.pbi_pid == UInt32(pid)
    else { return nil }
    return "\(info.pbi_start_tvsec)." + String(format: "%06llu", info.pbi_start_tvusec)
  }

  static let receiptEnvironmentKey = "VIBECRAFTED_TERMINAL_LAUNCH_RECEIPT"

  final class Launch {
    let receipt: Receipt
    private let process: Process
    private let receiptURL: URL?
    private var parsedHostReceipt: HostReceipt?

    fileprivate init(process: Process, specification: Specification, receiptURL: URL?) {
      self.process = process
      self.receiptURL = receiptURL
      receipt = Receipt(processIdentifier: process.processIdentifier, specification: specification)
    }

    /// Reads the wrapper's private receipt once; refuses foreign or shared files.
    var hostReceipt: HostReceipt? {
      if let parsedHostReceipt { return parsedHostReceipt }
      guard let receiptURL,
        let attributes = try? FileManager.default.attributesOfItem(atPath: receiptURL.path),
        attributes[.type] as? FileAttributeType == .typeRegular,
        (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
        let mode = (attributes[.posixPermissions] as? NSNumber)?.intValue, mode & 0o077 == 0,
        let data = try? Data(contentsOf: receiptURL),
        let parsed = try? HostReceipt(data: data, terminalHost: receipt.specification.terminalHost)
      else { return nil }
      parsedHostReceipt = parsed
      try? FileManager.default.removeItem(at: receiptURL.deletingLastPathComponent())
      return parsed
    }

    var hostState: HostState {
      let running = process.isRunning
      return TerminalLauncher.hostState(launcherPID: receipt.processIdentifier,
        launcherRunning: running, launcherStatus: running ? nil : process.terminationStatus,
        receipt: hostReceipt, startToken: TerminalLauncher.kernelStartToken)
    }

    /// True while a host answers for this launch, including a reused host.
    var isRunning: Bool { hostState.hostProcessIdentifier != nil }
    /// Non-zero only for a refused or failed launcher; a reused host's own end has no status here.
    var exitStatus: Int32? {
      if case .ended(let status) = hostState { return status }
      return nil
    }
    // Intentionally no cancellation, stop, or deinit action. Closing a client
    // never terminates this process, its terminal children, or the server.
  }

  /// Uses the exact generation wrapper (which execs the native host in place,
  /// or delivers one window to the single admitted host and exits 0).
  /// No shell string, LaunchServices lookup, undrained pipe, or UI lifetime hook.
  static func launch(_ specification: Specification) throws -> Launch {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(
      atPath: specification.workingDirectory.path, isDirectory: &isDirectory), isDirectory.boolValue
    else { throw LaunchError.workingDirectoryUnavailable }

    let receiptDirectory = FileManager.default.temporaryDirectory
      .appendingPathComponent("vc-terminal-launch-\(UUID().uuidString)", isDirectory: true)
    try FileManager.default.createDirectory(at: receiptDirectory, withIntermediateDirectories: false,
      attributes: [.posixPermissions: 0o700])
    let receiptURL = receiptDirectory.appendingPathComponent("receipt.json")
    var environment = specification.environment
    environment[receiptEnvironmentKey] = receiptURL.path

    let process = Process()
    process.executableURL = specification.executable
    process.arguments = specification.arguments
    process.currentDirectoryURL = specification.workingDirectory
    process.environment = environment
    // Detached from the App's stdio lifetime as well as its window lifetime.
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
    } catch {
      try? FileManager.default.removeItem(at: receiptDirectory)
      throw error
    }
    return Launch(process: process, specification: specification, receiptURL: receiptURL)
  }
}

/// Pure, injectable registration observation. Time and registration are supplied
/// by AppKit; this policy cannot launch, resolve, focus, or terminate a process.
struct TerminalRegistrationObservation {
  enum Outcome: Equatable { case waiting, ready, timedOut, ended, superseded }
  let launchID: UUID
  let deadline: TimeInterval

  init(receipt: TerminalLauncher.Receipt, now: TimeInterval, timeout: TimeInterval = 10) {
    launchID = receipt.launchID
    deadline = now + timeout
  }

  func observe(now: TimeInterval, currentLaunchID: UUID?, isRunning: Bool,
               isRegistered: Bool) -> Outcome {
    guard currentLaunchID == launchID else { return .superseded }
    guard isRunning else { return .ended }
    guard now < deadline else { return .timedOut }
    return isRegistered ? .ready : .waiting
  }
}
