import Darwin
import Foundation

/// Real Foundation.Process falsifiers for the App's extracted installer runner.
/// This compiles with NativeInstallerProcess.swift, not a copied imitation.
@main
@MainActor
struct NativeInstallerProcessTests {
  struct Failure: Error { let message: String }

  static func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw Failure(message: message) }
  }

  static func writeExecutable(_ url: URL, _ body: String) throws {
    try body.write(to: url, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
  }

  static func wait(seconds: TimeInterval = 5, until condition: @escaping () -> Bool) throws {
    let deadline = Date().addingTimeInterval(seconds)
    while !condition() {
      guard Date() < deadline else { throw Failure(message: "timed out waiting for native process") }
      RunLoop.current.run(until: Date().addingTimeInterval(0.01))
    }
  }

  static func process(_ script: URL) -> Process {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/bin/bash")
    task.arguments = [script.path]
    return task
  }

  static func testPipeOverflowKeepsMainActorResponsive(_ root: URL) throws {
    let script = root.appendingPathComponent("overflow.sh")
    try writeExecutable(script, """
      #!/usr/bin/env bash
      set -eu
      head -c 262144 /dev/zero | tr '\\0' o
      head -c 262144 /dev/zero | tr '\\0' e >&2
      printf '{"status":"complete"}\\n'
      """)
    var result: BoundedProcessResult?
    var mainActorTicked = false
    DispatchQueue.main.async { mainActorTicked = true }
    try NativeInstallerProcess.run(process(script), timeout: 3, label: "overflow",
      stdoutLimit: 4096, stderrLimit: 4096, onTimeout: { _ in
        fatalError("overflow process unexpectedly timed out")
      }) { finished in
        precondition(Thread.isMainThread)
        result = finished
      }
    try wait { result != nil && mainActorTicked }
    try require(result?.clean == true && result?.terminationStatus == 0, "overflow did not complete")
    try require(result?.stdout.count == 4096 && result?.stderr.count == 4096,
      "output capture was not bounded while draining both pipes")
    try require(String(data: result!.stdout, encoding: .utf8)?.contains("complete") == true,
      "bounded capture did not retain the receipt tail")
  }

  static func testTimeoutWaitsForOwnedChildSettlement(_ root: URL) throws {
    let marker = root.appendingPathComponent("post-completion-mutation")
    let childPID = root.appendingPathComponent("child.pid")
    let child = root.appendingPathComponent("child.sh")
    let parent = root.appendingPathComponent("parent.sh")
    try writeExecutable(child, """
      #!/usr/bin/env bash
      trap '' TERM
      printf '%s' "$$" > "${CHILD_PID}"
      # Pre-mutation window: settlement (TERM ignored -> 1 s grace -> KILL)
      # lands ~1.6 s after spawn on a fast host; loaded CI runners need the
      # wider margin or the child wins the race and the contract reads red.
      sleep 8
      printf mutation > "${MARKER}"
      sleep 20
      """)
    try writeExecutable(parent, """
      #!/usr/bin/env bash
      set -eu
      child_pid=""
      settle() {
        kill -TERM "$child_pid" 2>/dev/null || true
        for _ in {1..10}; do kill -0 "$child_pid" 2>/dev/null || break; sleep 0.1; done
        kill -KILL "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
        exit 143
      }
      trap settle TERM
      "${CHILD}" &
      child_pid=$!
      wait "$child_pid"
      """)
    var result: BoundedProcessResult?
    let task = process(parent)
    var environment = ProcessInfo.processInfo.environment
    environment["CHILD"] = child.path
    environment["CHILD_PID"] = childPID.path
    environment["MARKER"] = marker.path
    task.environment = environment
    try NativeInstallerProcess.run(task, timeout: 0.5, label: "parent child",
      onTimeout: { _ in }) { finished in
        precondition(Thread.isMainThread)
        result = finished
      }
    try wait { FileManager.default.fileExists(atPath: childPID.path) }
    try wait { result != nil }
    try require(result?.timedOut == true, "timeout was not reported")
    try require(!FileManager.default.fileExists(atPath: marker.path), "child mutated before settlement")
    Thread.sleep(forTimeInterval: 2.2)
    try require(!FileManager.default.fileExists(atPath: marker.path), "orphaned child mutated after completion")
    guard let pid = Int32(try String(contentsOf: childPID, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines))
    else { throw Failure(message: "child PID was not recorded") }
    errno = 0
    try require(kill(pid, 0) == -1 && errno == ESRCH, "owned child survived timeout settlement")
  }

  static func testRetainedPipeDescriptorCompletesWithinGrace(_ root: URL) throws {
    let script = root.appendingPathComponent("retained-pipe.sh")
    try writeExecutable(script, """
      #!/usr/bin/env bash
      set -eu
      printf retained-before-parent-exit
      sleep 4 &
      exit 0
      """)
    var result: BoundedProcessResult?
    let started = Date()
    try NativeInstallerProcess.run(process(script), timeout: 0.2, label: "retained pipe",
      onTimeout: { _ in fatalError("exited parent unexpectedly timed out") }) { finished in
        precondition(Thread.isMainThread)
        result = finished
      }
    try wait(seconds: 1.5) { result != nil }
    let elapsed = Date().timeIntervalSince(started)
    try require(elapsed < 1.5, "retained descriptor blocked completion for \(elapsed)s")
    try require(result?.clean == true && result?.terminationStatus == 0,
      "retained descriptor changed the parent outcome")
    try require(result?.timedOut == false, "parent exit incorrectly reported a process timeout")
    try require(result?.pipeDrainTimedOut == true,
      "retained descriptor was not reported as a bounded post-exit drain timeout")
    try require(String(data: result!.stdout, encoding: .utf8)?.contains("retained-before-parent-exit") == true,
      "available bytes were not retained before ending the post-exit drain")
  }

  static func testSpawnFailureDoesNotDeliverLateCallback() throws {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/definitely/not/an/executable")
    var callback = false
    do {
      try NativeInstallerProcess.run(task, timeout: 1, label: "missing executable",
        onTimeout: { _ in }) { _ in callback = true }
      throw Failure(message: "missing executable unexpectedly launched")
    } catch { }
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    try require(!callback, "failed spawn retained a reader or completion callback")
  }

  static func testResolverTimeoutThenRecovery(_ root: URL) throws {
    let script = root.appendingPathComponent("resolver.sh")
    try writeExecutable(script, "#!/bin/bash\nwhile true; do :; done\n")
    var first: BoundedProcessResult?
    try NativeInstallerProcess.run(process(script), timeout: 0.1, label: "resolver fixture",
      onTimeout: { _ in }) { first = $0 }
    try wait { first != nil }
    try require(first!.timedOut, "resolver did not time out")
    let failed: RuntimeResolution<String> = decodeRuntimeResolution(stdout: first!.stdout,
      stderr: first!.stderr, terminationStatus: first!.terminationStatus,
      clean: first!.clean && !first!.timedOut)
    let identity = RuntimeIdentityFingerprint(home: root.path, pointer: nil, receipt: nil)
    let cached = RuntimeResolutionCache(fingerprint: identity, value: failed,
      expires: runtimeResolutionCacheLifetime(failed, consecutiveFailures: 1))
    try require(!cached.reusable(for: identity, now: 5), "unchanged identity stranded timeout")
    try writeExecutable(script, """
      #!/bin/bash
      printf '%s' '{"schema":"vibecrafted.runtime-resolution.v1","status":"ready","runtime":"recovered"}'
      """)
    var second: BoundedProcessResult?
    try NativeInstallerProcess.run(process(script), timeout: 1, label: "resolver recovery",
      onTimeout: { _ in }) { second = $0 }
    try wait { second != nil }
    let recovered: RuntimeResolution<String> = decodeRuntimeResolution(stdout: second!.stdout,
      stderr: second!.stderr, terminationStatus: second!.terminationStatus,
      clean: second!.clean && !second!.timedOut)
    guard case .ready("recovered") = recovered else {
      throw Failure(message: "retry did not recover with unchanged installation identity")
    }
  }

  static func main() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    try testPipeOverflowKeepsMainActorResponsive(root)
    try testTimeoutWaitsForOwnedChildSettlement(root)
    try testRetainedPipeDescriptorCompletesWithinGrace(root)
    try testSpawnFailureDoesNotDeliverLateCallback()
    try testResolverTimeoutThenRecovery(root)
    print("NativeInstallerProcessTests passed")
  }
}
