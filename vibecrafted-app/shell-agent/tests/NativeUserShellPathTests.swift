import Foundation

/// Execute the real native shared API adapter and bounded process runner. The fixture
/// interpreter stands in for Python, so no CI/user shell startup is consulted.
@main
@MainActor
struct NativeUserShellPathTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func wait(until condition: () -> Bool) throws {
    let deadline = Date().addingTimeInterval(5)
    while !condition() {
      try require(Date() < deadline, "native PATH completion timed out")
      RunLoop.current.run(until: Date().addingTimeInterval(0.01))
    }
  }

  static func interpreter(_ generation: URL, body: String) throws {
    let executable = generation.appendingPathComponent("bin/python3")
    try body.write(to: executable, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
  }

  static func main() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(
      "native path '$(false)' \(UUID().uuidString)")
    let generation = root.appendingPathComponent("generation")
    let home = root.appendingPathComponent("home")
    try FileManager.default.createDirectory(at: generation.appendingPathComponent("bin"),
      withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    var host = ["HOME": home.path, "SHELL": "/bin/zsh", "PATH": "/usr/bin:/bin",
      "SSH_AUTH_SOCK": "/private/test-agent.sock", "PYTHONPATH": "/foreign/modules",
      "PYTHONHOME": "/foreign/python", "__PYVENV_LAUNCHER__": "/foreign/launcher"]
    var launches = 0
    var forceTimeout = false
    let adapter = NativeUserShellPath { process, timeout, completion in
      launches += 1
      try require(timeout == 7, "native timeout changed")
      try require(process.executableURL == generation.appendingPathComponent("bin/python3"),
        "interpreter lookup was not absolute and generation-owned")
      try require(process.arguments == [
        "-c",
        "import json,os,sys; sys.path.insert(0,sys.argv[1]); "
          + "from vibecrafted_core.product_contract import resolve_login_shell_path; "
          + "print(json.dumps({'path':resolve_login_shell_path(os.environ)}))",
        generation.appendingPathComponent("vibecrafted-core").path],
        "shared product contract API was bypassed or generation path interpolated")
      try require(process.environment?["PYTHONPATH"] == nil
        && process.environment?["PYTHONHOME"] == nil
        && process.environment?["__PYVENV_LAUNCHER__"] == nil, "Python deny-list leaked")
      try require(process.environment?["SSH_AUTH_SOCK"] == host["SSH_AUTH_SOCK"],
        "guest environment was amputated")
      try NativeInstallerProcess.run(process, timeout: forceTimeout ? 0.05 : timeout,
        label: "native-user-path-test", onTimeout: { _ in }, completion: completion)
    }
    try interpreter(generation, body: """
      #!/bin/sh
      /bin/sleep 0.05
      printf '{"path":"/user/cargo/bin:/user/local/bin:/usr/bin"}'
      """)
    var answers: [String] = []
    var uiTicked = false
    DispatchQueue.main.async { uiTicked = true }
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try require(launches == 1 && answers.isEmpty, "probe blocked or was not coalesced")
    try wait { answers.count == 2 && uiTicked }
    try require(answers == Array(repeating: "/user/cargo/bin:/user/local/bin:/usr/bin", count: 2),
      "shared API PATH was not delivered to both waiters")
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try require(launches == 1 && answers.count == 3, "completed probe was not cached")

    // Creating and then changing startup files invalidates cached shell truth.
    let rc = home.appendingPathComponent(".zshrc")
    try "first".write(to: rc, atomically: true, encoding: .utf8)
    try require(adapter.cachedPath(generation: generation, environment: host) == nil,
      "new rc file did not invalidate PATH")
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 4 }
    try "a changed startup file".write(to: rc, atomically: true, encoding: .utf8)
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 5 }
    try require(launches == 3, "rc modification did not trigger exactly one new probe")

    // Nonzero/malformed results preserve PATH and cache the failure too.
    try interpreter(generation, body: "#!/bin/sh\nprintf '{invalid}'\nexit 1\n")
    host["PATH"] = "/fallback/failure"
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 6 }
    try require(answers.last == host["PATH"], "nonzero API process lost inherited PATH")
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try require(launches == 4 && answers.count == 7, "probe failure was not cached")

    try interpreter(generation, body: "#!/bin/sh\nprintf '{invalid}'\n")
    host["PATH"] = "/fallback/malformed"
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 8 }
    try require(answers.last == host["PATH"], "invalid JSON did not fall back")

    try interpreter(generation, body: "#!/bin/sh\nexec /bin/sleep 20\n")
    forceTimeout = true
    host["PATH"] = "/fallback/timeout"
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 9 }
    try require(answers.last == host["PATH"], "timeout did not preserve inherited PATH")
    try require(!FileManager.default.fileExists(atPath: home.appendingPathComponent(".zprofile").path),
      "adapter synthesized shell startup files")

    let userDotdir = root.appendingPathComponent("custom-zdotdir")
    try FileManager.default.createDirectory(at: userDotdir, withIntermediateDirectories: true)
    let userRC = userDotdir.appendingPathComponent(".zprofile")
    try "user configuration".write(to: userRC, atomically: true, encoding: .utf8)
    host["ZDOTDIR"] = home.appendingPathComponent(".config/vibecrafted/vc-terminal").path
    host["VIBECRAFTED_USER_ZDOTDIR"] = userDotdir.path
    forceTimeout = false
    try interpreter(generation, body: "#!/bin/sh\nprintf '{\"path\":\"/custom/user/bin\"}'\n")
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 10 }
    try require(answers.last == "/custom/user/bin", "custom user shell context was not resolved")
    try "changed custom user configuration".write(to: userRC, atomically: true, encoding: .utf8)
    try require(adapter.cachedPath(generation: generation, environment: host) == nil,
      "effective user ZDOTDIR rc edit did not invalidate cache")
    adapter.resolve(generation: generation, environment: host) { answers.append($0) }
    try wait { answers.count == 11 }
    var changedHost = host
    changedHost["HOME"] = root.appendingPathComponent("other-home").path
    try require(adapter.cachedPath(generation: generation, environment: changedHost) == nil,
      "changing HOME reused another user's PATH")
    changedHost = host
    changedHost["SHELL"] = "/bin/bash"
    try require(adapter.cachedPath(generation: generation, environment: changedHost) == nil,
      "changing SHELL reused another shell's PATH")
    try require(adapter.cachedPath(generation: root.appendingPathComponent("other-generation"),
      environment: host) == nil, "changing generation reused an old contract helper")

    // Run the fixed Python script itself against a fixture API. A generation
    // containing spaces, quotes and shell-looking text must remain one argv.
    if let python = ProcessInfo.processInfo.environment["NATIVE_TEST_PYTHON"] {
      let module = generation.appendingPathComponent("vibecrafted-core/vibecrafted_core")
      try FileManager.default.createDirectory(at: module, withIntermediateDirectories: true)
      try "".write(to: module.appendingPathComponent("__init__.py"), atomically: true,
        encoding: .utf8)
      try "def resolve_login_shell_path(host):\n    return host['PATH'] + ':/shared/api/tool'\n"
        .write(to: module.appendingPathComponent("product_contract.py"), atomically: true,
          encoding: .utf8)
      let executable = generation.appendingPathComponent("bin/python3")
      try FileManager.default.removeItem(at: executable)
      try FileManager.default.createSymbolicLink(atPath: executable.path, withDestinationPath: python)
      host["PATH"] = "/real/python/api"
      adapter.resolve(generation: generation, environment: host) { answers.append($0) }
      try wait { answers.count == 12 }
      try require(answers.last == "/real/python/api:/shared/api/tool",
        "fixed script did not import the selected generation API")
    }

    // Starting the interpreter can fail before a bounded result exists.
    let refused = NativeUserShellPath { _, _, _ in throw Failure(message: "start refused") }
    var refusalPath: String?
    refused.resolve(generation: generation, environment: host) { refusalPath = $0 }
    try require(refusalPath == host["PATH"], "start error did not preserve inherited PATH")
    print("NativeUserShellPathTests passed: asynchronous shared API, coalescing, cache, rc invalidation, failure/timeout fallback")
  }
}
