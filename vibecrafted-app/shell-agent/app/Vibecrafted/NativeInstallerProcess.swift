import Foundation

/// Bounded output captured from one native subprocess invocation.
///
/// The type is intentionally Foundation-only: it is shared by AppDelegate and
/// the executable Swift lifecycle falsifier without copying process behavior
/// into a test fixture.
struct BoundedProcessResult {
  let stdout: Data
  let stderr: Data
  let terminationStatus: Int32
  let clean: Bool
  let timedOut: Bool
}

final class BoundedOutputSink: @unchecked Sendable {
  private let lock = NSLock()
  private let limit: Int
  private var storage = Data()

  init(limit: Int) { self.limit = limit }

  func absorb(_ chunk: Data) {
    guard !chunk.isEmpty else { return }
    lock.lock()
    defer { lock.unlock() }
    if chunk.count >= limit {
      storage = Data(chunk.suffix(limit))
    } else {
      storage.append(chunk)
      if storage.count > limit { storage.removeFirst(storage.count - limit) }
    }
  }

  var collected: Data {
    lock.lock()
    defer { lock.unlock() }
    return storage
  }
}

private final class ProcessTimeoutFlag: @unchecked Sendable {
  private let lock = NSLock()
  private var value = false

  func mark() {
    lock.lock()
    value = true
    lock.unlock()
  }

  var marked: Bool {
    lock.lock()
    defer { lock.unlock() }
    return value
  }
}

private func drainRemainder(_ handle: FileHandle, into sink: BoundedOutputSink) {
  handle.readabilityHandler = nil
  var tail = handle.availableData
  while !tail.isEmpty {
    sink.absorb(tail)
    tail = handle.availableData
  }
}

/// Starts a configured process, drains both output pipes from launch, and
/// reports one terminal result on MainActor. The caller retains the Process
/// while it is in flight.
enum NativeInstallerProcess {
  static func run(
    _ process: Process,
    timeout: TimeInterval,
    label: String,
    stdoutLimit: Int = 1 << 20,
    stderrLimit: Int = 1 << 16,
    onTimeout: @escaping @Sendable (String) -> Void,
    completion: @escaping @MainActor @Sendable (BoundedProcessResult) -> Void
  ) throws {
    let output = Pipe()
    let errors = Pipe()
    let stdout = BoundedOutputSink(limit: stdoutLimit)
    let stderr = BoundedOutputSink(limit: stderrLimit)
    let timeoutFlag = ProcessTimeoutFlag()
    process.standardOutput = output
    process.standardError = errors
    output.fileHandleForReading.readabilityHandler = { handle in
      let chunk = handle.availableData
      if chunk.isEmpty { handle.readabilityHandler = nil } else { stdout.absorb(chunk) }
    }
    errors.fileHandleForReading.readabilityHandler = { handle in
      let chunk = handle.availableData
      if chunk.isEmpty { handle.readabilityHandler = nil } else { stderr.absorb(chunk) }
    }
    process.terminationHandler = { finished in
      drainRemainder(output.fileHandleForReading, into: stdout)
      drainRemainder(errors.fileHandleForReading, into: stderr)
      let result = BoundedProcessResult(
        stdout: stdout.collected, stderr: stderr.collected,
        terminationStatus: finished.terminationStatus,
        clean: finished.terminationReason == .exit,
        timedOut: timeoutFlag.marked)
      DispatchQueue.main.async {
        MainActor.assumeIsolated { completion(result) }
      }
    }
    do {
      try process.run()
    } catch {
      output.fileHandleForReading.readabilityHandler = nil
      errors.fileHandleForReading.readabilityHandler = nil
      process.terminationHandler = nil
      throw error
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak process] in
      guard let process, process.isRunning else { return }
      timeoutFlag.mark()
      onTimeout("\(label) exceeded \(Int(timeout))s; terminating pid=\(process.processIdentifier)")
      process.terminate()
      DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak process] in
        guard let process, process.isRunning else { return }
        kill(process.processIdentifier, SIGKILL)
      }
    }
  }
}
