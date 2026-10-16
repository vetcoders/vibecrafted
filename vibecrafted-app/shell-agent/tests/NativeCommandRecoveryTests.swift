import Foundation

// W1 authored contract, NOT_ASSESSED under COMPILE_EMBARGO_CODING.
// Standalone harness, matching the repository's swiftc policy-harness pattern.
// After W2 closure, compile this file with the two CommandDeck leaves (not the
// App's main.swift). No test calls TerminalLauncher.launch or starts a process.
@main
@MainActor
struct NativeCommandRecoveryTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func rejects(_ body: () throws -> Void) throws {
    do { try body() } catch { return }
    throw Failure(message: "Expected rejection")
  }

  static func testRejectedNativePayloads() throws {
    for value in ["relative", "//server/path", "/tmp/../private", "/tmp/a\u{0}b", "file:///tmp"] {
      try rejects { _ = try NativeCommand.PathPayload(path: value) }
    }
    for value in ["javascript:alert(1)", "file:///tmp/a", "data:text/html,x", "https://",
                  "https://user:password@example.com/", "https://example.com/\n",
                  "https://example.com/%ZZ", "mailto:person@example.com"] {
      try rejects { _ = try NativeCommand.ExternalURLPayload(value: value) }
    }
    let url = try NativeCommand.ExternalURLPayload(value: "https://example.com/a?b=c#d")
    try require(url.url.absoluteString == "https://example.com/a?b=c#d", "URL changed")
  }

  static func testExactLaunchSpecification() throws {
    // Spaces, apostrophes and shell metacharacters stay literal argv/path data.
    let root = URL(fileURLWithPath: "/fixture/releases/selected generation")
    let terminal = root.appendingPathComponent("bin/vc-terminal")
    let host = root.appendingPathComponent("libexec/vc-terminal")
    let shell = root.appendingPathComponent("bin/zsh")
    let start = root.appendingPathComponent("shell/start's $literal.sh")
    let cwd = URL(fileURLWithPath: "/fixture/workspace with spaces/$(literal)")
    let environment = [
      "VIBECRAFTED_RUNTIME_ROOT": root.path, "VIBECRAFTED_ROOT": root.path,
      "VIBECRAFTED_TERMINAL_HOST": host.path,
      "PATH": "/custom/bin:/usr/bin", "HOME": "/fixture/home",
      "VC_FRAME_SOCKET_DIR": "/fixture/socket", "OWNER_VALUE": "spaces ' $() = kept",
    ]
    let specification = try TerminalLauncher.Specification(
      generationRoot: root, terminal: terminal, terminalHost: host, primaryShell: shell,
      start: start, workingDirectory: cwd, environment: environment)
    try require(specification.generationRoot == root, "Generation changed")
    try require(specification.executable == terminal, "Wrapper was substituted")
    try require(specification.arguments == ["-e", shell.path], "argv changed")
    try require(specification.workingDirectory == cwd, "cwd changed")
    try require(specification.environment == environment, "Environment merged or rewritten")
    var mismatched = environment
    mismatched["VIBECRAFTED_RUNTIME_ROOT"] = "/fixture/releases/new-pointer"
    try rejects {
      _ = try TerminalLauncher.Specification(
        generationRoot: root, terminal: terminal, terminalHost: host, primaryShell: shell,
        start: start, workingDirectory: cwd, environment: mismatched)
    }
    var malformed = environment
    malformed["BAD=KEY"] = "value"
    try rejects {
      _ = try TerminalLauncher.Specification(
        generationRoot: root, terminal: terminal, terminalHost: host, primaryShell: shell,
        start: start, workingDirectory: cwd, environment: malformed)
    }
  }

  static func testNativeConfirmationAndPathBoundary() throws {
    let files = FileManager.default
    let base = files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    let allowed = base.appendingPathComponent("workspace")
    let outside = base.appendingPathComponent("workspace-other")
    try files.createDirectory(at: allowed, withIntermediateDirectories: true)
    defer { try? files.removeItem(at: base) }
    try files.createDirectory(at: outside, withIntermediateDirectories: true)
    let link = allowed.appendingPathComponent("escape")
    try files.createSymbolicLink(at: link, withDestinationURL: outside)
    var events: [String] = []
    var confirmed = false
    let bridge = NativeCommandBridge(allowedPathRoots: [allowed], actions: .init(
      openTerminal: { events.append("terminal:\($0.path)") },
      openExternalURL: { events.append("url:\($0.absoluteString)") },
      revealPath: { events.append("reveal:\($0.path)") },
      retryConnection: { events.append("retry") },
      confirmRuntimeStop: { events.append("confirm"); return confirmed },
      stopRuntime: { events.append("stop") }))
    try require(events.isEmpty, "Construction performed an action")
    try bridge.perform(.retryConnection)
    try bridge.perform(.requestRuntimeStop)
    try require(events == ["retry", "confirm"], "Cancel stopped runtime")
    confirmed = true
    try bridge.perform(.requestRuntimeStop)
    try require(events == ["retry", "confirm", "confirm", "stop"], "Stop bypassed confirmation")
    let before = events
    for path in [outside.path, link.path, allowed.appendingPathComponent("missing").path] {
      try rejects { try bridge.perform(.revealPath(try .init(path: path))) }
    }
    try require(events == before, "Rejected request reached native actions")
    try bridge.perform(.openTerminal(.init(workingDirectory: try .init(path: allowed.path))))
    try require(events.last == "terminal:\(allowed.resolvingSymlinksInPath().path)", "Wrong cwd routed")
  }

  static func testBoundedRegistrationObservation() throws {
    let root = URL(fileURLWithPath: "/fixture/generation")
    let host = root.appendingPathComponent("libexec/terminal")
    let specification = try TerminalLauncher.Specification(generationRoot: root,
      terminal: root.appendingPathComponent("bin/vc-terminal"), terminalHost: host,
      primaryShell: root.appendingPathComponent("bin/zsh"), start: root.appendingPathComponent("bin/vc-start"),
      workingDirectory: root, environment: ["VIBECRAFTED_ROOT": root.path,
        "VIBECRAFTED_RUNTIME_ROOT": root.path, "VIBECRAFTED_TERMINAL_HOST": host.path])
    let receipt = TerminalLauncher.Receipt(processIdentifier: 42, specification: specification)
    let observation = TerminalRegistrationObservation(receipt: receipt, now: 100, timeout: 10)
    try require(observation.observe(now: 100, currentLaunchID: receipt.launchID,
      isRunning: true, isRegistered: false) == .waiting, "Spawn mistaken for registration")
    try require(observation.observe(now: 101, currentLaunchID: receipt.launchID,
      isRunning: true, isRegistered: true) == .ready, "Late registration not observed")
    try require(observation.observe(now: 110, currentLaunchID: receipt.launchID,
      isRunning: true, isRegistered: false) == .timedOut, "Observation is unbounded")
    try require(observation.observe(now: 111, currentLaunchID: receipt.launchID,
      isRunning: true, isRegistered: true) == .timedOut, "Late focus escaped the deadline")
    try require(observation.observe(now: 101, currentLaunchID: UUID(),
      isRunning: true, isRegistered: true) == .superseded, "Another launch inherited focus")
    try require(observation.observe(now: 101, currentLaunchID: receipt.launchID,
      isRunning: false, isRegistered: true) == .ended, "Exited process considered ready")
  }

  static func testHostReceiptAndReuseState() throws {
    let host = URL(fileURLWithPath: "/fixture/Vibecrafted.app/Contents/MacOS/vc-terminal")
    func receipt(_ fields: [String: Any]) throws -> TerminalLauncher.HostReceipt {
      var object: [String: Any] = ["schema": TerminalLauncher.HostReceipt.schema, "kind": "reused",
        "host_pid": 4242, "host_start": "1760000000.000123", "executable": host.path,
        "delivery": "create_window_request_written", "agent_start_proven": false]
      object.merge(fields) { $1 }
      return try TerminalLauncher.HostReceipt(
        data: try JSONSerialization.data(withJSONObject: object), terminalHost: host)
    }
    let reused = try receipt([:])
    try require(reused.kind == .reused && reused.hostProcessIdentifier == 4242, "Receipt changed")
    for bad: [String: Any] in [["schema": "other"], ["kind": "window"], ["host_pid": 0],
      ["host_start": ""], ["executable": "/Applications/Other.app/vc-terminal"],
      ["agent_start_proven": true]] {
      try rejects { _ = try receipt(bad) }
    }
    let fresh = try receipt(["kind": "fresh", "host_pid": 77])
    let live: (Int32) -> String? = { $0 == 4242 ? "1760000000.000123" : nil }
    let swapped: (Int32) -> String? = { _ in "1760000999.000001" }
    typealias State = TerminalLauncher.HostState
    func state(_ running: Bool, _ status: Int32?, _ receipt: TerminalLauncher.HostReceipt?,
               _ token: @escaping (Int32) -> String? = live) -> State {
      TerminalLauncher.hostState(launcherPID: 77, launcherRunning: running,
        launcherStatus: status, receipt: receipt, startToken: token)
    }
    // Fresh: the launcher PID is the host (wrapper execs in place).
    try require(state(true, nil, nil) == .launching(77), "Admission phase lost")
    try require(state(true, nil, fresh) == .launching(77), "Fresh host not the launcher")
    try require(state(false, 0, fresh) == .ended(0), "Fresh host end mistaken for reuse")
    // Reused: a successful IPC child exit is not a GUI end.
    try require(state(true, nil, reused) == .launching(77), "Reuse before launcher exit")
    try require(state(false, 0, reused) == .reused(4242), "IPC child exit 0 reported ended")
    try require(state(false, 0, reused).hostProcessIdentifier == 4242, "Reused host not observed")
    try require(state(false, 0, reused, swapped) == .ended(0), "Recycled host PID adopted")
    try require(state(false, 0, reused, { _ in nil }) == .ended(0), "Exited host adopted")
    try require(state(false, 75, reused) == .ended(75), "Unconfirmed delivery reported ready")
    try require(state(false, 0, nil) == .ended(0), "Missing receipt fabricated a host")
    let foreignFresh = try receipt(["kind": "fresh", "host_pid": 99])
    try require(state(true, nil, foreignFresh) == .ended(nil), "Fresh receipt for another PID")
    // Registration still gates on the resolved host, never on agent start.
    let root = URL(fileURLWithPath: "/fixture/generation")
    let specification = try TerminalLauncher.Specification(generationRoot: root,
      terminal: root.appendingPathComponent("bin/vc-terminal"), terminalHost: host,
      primaryShell: root.appendingPathComponent("bin/zsh"), start: root.appendingPathComponent("bin/vc-start"),
      workingDirectory: root, environment: ["VIBECRAFTED_ROOT": root.path,
        "VIBECRAFTED_RUNTIME_ROOT": root.path, "VIBECRAFTED_TERMINAL_HOST": host.path])
    let launch = TerminalLauncher.Receipt(processIdentifier: 77, specification: specification)
    let observation = TerminalRegistrationObservation(receipt: launch, now: 0, timeout: 10)
    let reusedState = state(false, 0, reused)
    try require(observation.observe(now: 1, currentLaunchID: launch.launchID,
      isRunning: reusedState.hostProcessIdentifier != nil, isRegistered: true) == .ready,
      "Reused host registration not ready")
    try require(observation.observe(now: 1, currentLaunchID: launch.launchID,
      isRunning: state(false, 75, reused).hostProcessIdentifier != nil, isRegistered: true) == .ended,
      "Refused launch focused a host")
    try require(TerminalLauncher.kernelStartToken(getpid()) != nil, "Own kernel identity unreadable")
    try require(TerminalLauncher.kernelStartToken(-1) == nil, "Invalid PID has an identity")
  }

  static func main() throws {
    try testRejectedNativePayloads()
    try testExactLaunchSpecification()
    try testNativeConfirmationAndPathBoundary()
    try testBoundedRegistrationObservation()
    try testHostReceiptAndReuseState()
    print("NativeCommandRecoveryTests passed")
  }
}
