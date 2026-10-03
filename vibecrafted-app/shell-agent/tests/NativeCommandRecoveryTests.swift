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

  static func main() throws {
    try testRejectedNativePayloads()
    try testExactLaunchSpecification()
    try testNativeConfirmationAndPathBoundary()
    try testBoundedRegistrationObservation()
    print("NativeCommandRecoveryTests passed")
  }
}
