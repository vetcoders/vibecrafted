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
      sleep 2
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

  static func main() throws {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    try testPipeOverflowKeepsMainActorResponsive(root)
    try testTimeoutWaitsForOwnedChildSettlement(root)
    try testSpawnFailureDoesNotDeliverLateCallback()
    print("NativeInstallerProcessTests passed")
  }
}
