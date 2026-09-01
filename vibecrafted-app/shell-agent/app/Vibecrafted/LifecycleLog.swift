import AppKit
import Darwin
import os.log

let installLog = Logger(subsystem: "io.vetcoders.vibecrafted", category: "install")

/// Durable lifecycle trail. `os_log` is not retained by the unified log store
/// for this app (2026-08-28: two App deaths left zero entries), so every
/// launch/quit/signal/child-exit also lands in `<crafted home>/logs/app-lifecycle.log`.
///
/// This is supervision evidence, not installer behavior: the only directory it
/// creates is the app's own log folder under the crafted home.
func lifecycleLog(_ event: String) {
  let stamp = ISO8601DateFormatter().string(from: Date())
  let line = "\(stamp) pid=\(getpid()) \(event)\n"
  installLog.notice("\(event, privacy: .public)")
  let host = ProcessInfo.processInfo.environment
  let home = host["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
  let crafted = host["VIBECRAFTED_HOME"] ?? "\(home)/.vibecrafted"
  let logDir = URL(fileURLWithPath: crafted, isDirectory: true).appendingPathComponent("logs")
  let logURL = logDir.appendingPathComponent("app-lifecycle.log")
  do {
    try FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
    if !FileManager.default.fileExists(atPath: logURL.path) {
      FileManager.default.createFile(atPath: logURL.path, contents: nil)
    }
    let handle = try FileHandle(forWritingTo: logURL)
    defer { try? handle.close() }
    try handle.seekToEnd()
    try handle.write(contentsOf: Data(line.utf8))
  } catch {
    installLog.error("lifecycle log write failed: \(error.localizedDescription, privacy: .public)")
  }
}

@MainActor var lifecycleSignalSources: [DispatchSourceSignal] = []

/// SIGTERM/SIGHUP/SIGINT bypass `applicationShouldTerminate`; without this the
/// App dies silently and its vc-terminal child goes with it. Log the signal,
/// then quit through AppKit so `applicationWillTerminate` still runs.
@MainActor func installLifecycleSignalHandlers() {
  for (signalNumber, name) in [(SIGTERM, "SIGTERM"), (SIGHUP, "SIGHUP"), (SIGINT, "SIGINT")] {
    signal(signalNumber, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
    source.setEventHandler {
      lifecycleLog("signal \(name) received; terminating via AppKit")
      NSApp.terminate(nil)
    }
    source.resume()
    lifecycleSignalSources.append(source)
  }
}
