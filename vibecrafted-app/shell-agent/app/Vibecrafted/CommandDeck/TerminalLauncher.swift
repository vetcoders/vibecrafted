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
  }

  /// Spawn evidence only: a PID does not prove that the window became ready.
  /// Keep in memory; the specification contains the owner's environment and
  /// must not be dumped into logs or web responses.
  struct Receipt: Equatable, Sendable {
    let launchID = UUID()
    let processIdentifier: Int32
    let specification: Specification
  }

  final class Launch {
    let receipt: Receipt
    private let process: Process

    fileprivate init(process: Process, specification: Specification) {
      self.process = process
      receipt = Receipt(processIdentifier: process.processIdentifier, specification: specification)
    }

    var isRunning: Bool { process.isRunning }
    var exitStatus: Int32? { process.isRunning ? nil : process.terminationStatus }
    // Intentionally no cancellation, stop, or deinit action. Closing a client
    // never terminates this process, its terminal children, or the server.
  }

  /// Uses the exact generation wrapper (which execs the native host in place).
  /// No shell string, LaunchServices lookup, undrained pipe, or UI lifetime hook.
  static func launch(_ specification: Specification) throws -> Launch {
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(
      atPath: specification.workingDirectory.path, isDirectory: &isDirectory), isDirectory.boolValue
    else { throw LaunchError.workingDirectoryUnavailable }

    let process = Process()
    process.executableURL = specification.executable
    process.arguments = specification.arguments
    process.currentDirectoryURL = specification.workingDirectory
    process.environment = specification.environment
    // Detached from the App's stdio lifetime as well as its window lifetime.
    process.standardInput = FileHandle.nullDevice
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    try process.run()
    return Launch(process: process, specification: specification)
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
