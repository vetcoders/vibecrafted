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
  /// A capture limit discarded older output while preserving the newest receipt.
  let outputTruncated: Bool
  /// A descendant retained an inherited pipe past the bounded post-exit grace.
  let pipeDrainTimedOut: Bool
}

final class BoundedOutputSink: @unchecked Sendable {
  private let lock = NSLock()
  private let limit: Int
  private var storage = Data()
  private var truncated = false

  init(limit: Int) { self.limit = limit }

  func absorb(_ chunk: Data) {
    guard !chunk.isEmpty else { return }
    lock.lock()
    defer { lock.unlock() }
    if chunk.count >= limit {
      truncated = truncated || chunk.count > limit || !storage.isEmpty
      storage = Data(chunk.suffix(limit))
    } else {
      storage.append(chunk)
      if storage.count > limit {
        truncated = true
        storage.removeFirst(storage.count - limit)
      }
    }
  }

  var collected: Data {
    lock.lock()
    defer { lock.unlock() }
    return storage
  }

  var wasTruncated: Bool {
    lock.lock()
    defer { lock.unlock() }
    return truncated
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

/// One pipe has exactly one reader queue.  In particular, a readability handler
/// never races a blocking post-exit `availableData` loop: descendants may keep
/// inherited descriptors open after their direct parent has terminated.
private final class SerializedPipeDrain: @unchecked Sendable {
  private let handle: FileHandle
  private let sink: BoundedOutputSink
  private let queue: DispatchQueue
  private var reachedEOF = false
  private var settled = false
  private var settlement: ((Bool) -> Void)?
  private var deadlineTimer: DispatchSourceTimer?

  init(_ handle: FileHandle, into sink: BoundedOutputSink, label: String) {
    self.handle = handle
    self.sink = sink
    queue = DispatchQueue(label: "vetcoders.native-installer-pipe.\(label)")
    handle.readabilityHandler = { [weak self] _ in
      // Read while FileHandle has positively signalled readability, but take
      // the same queue used by post-exit settlement.  Deferring the read can
      // turn a stale signal into a blocking read after another consumer drains
      // the pipe.
      self?.queue.sync { self?.drainReadableChunk() }
    }
  }

  private func drainReadableChunk() {
    guard !settled else { return }
    // This queue is reached only by FileHandle's readability notification, so
    // this one read cannot wait for a quiet retained descriptor.
    let chunk = handle.availableData
    if chunk.isEmpty {
      reachedEOF = true
      settle(reachedEOF: true)
    } else {
      sink.absorb(chunk)
    }
  }

  func settleAfterExit(grace: TimeInterval, _ completion: @escaping (Bool) -> Void) {
    queue.async { [self] in
      guard !self.settled else { completion(self.reachedEOF); return }
      self.settlement = completion
      if self.reachedEOF {
        self.settle(reachedEOF: true)
        return
      }
      let timer = DispatchSource.makeTimerSource(queue: self.queue)
      timer.schedule(deadline: .now() + grace)
      // The timer deliberately retains its drain until it fires; `settle`
      // cancels it and breaks that cycle deterministically.
      timer.setEventHandler { [self] in self.settle(reachedEOF: false) }
      self.deadlineTimer = timer
      timer.resume()
    }
  }

  func cancel() {
    queue.async { self.settle(reachedEOF: self.reachedEOF) }
  }

  private func settle(reachedEOF: Bool) {
    guard !settled else { return }
    settled = true
    deadlineTimer?.cancel()
    deadlineTimer = nil
    handle.readabilityHandler = nil
    handle.closeFile()
    let completion = settlement
    settlement = nil
    completion?(reachedEOF)
  }
}

/// Starts a configured process, drains both output pipes from launch, and
/// reports one terminal result on MainActor. The caller retains the Process
/// while it is in flight.
enum NativeInstallerProcess {
  private static let postExitPipeDrainGrace: TimeInterval = 0.2
  private static let timeoutSettlementGrace: TimeInterval = 5

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
    let pipeDrainTimeoutFlag = ProcessTimeoutFlag()
    process.standardOutput = output
    process.standardError = errors
    let outputReader = SerializedPipeDrain(
      output.fileHandleForReading, into: stdout, label: "stdout")
    let errorReader = SerializedPipeDrain(
      errors.fileHandleForReading, into: stderr, label: "stderr")
    process.terminationHandler = { finished in
      let readers = DispatchGroup()
      readers.enter()
      outputReader.settleAfterExit(grace: postExitPipeDrainGrace) { reachedEOF in
        if !reachedEOF { pipeDrainTimeoutFlag.mark() }
        readers.leave()
      }
      readers.enter()
      errorReader.settleAfterExit(grace: postExitPipeDrainGrace) { reachedEOF in
        if !reachedEOF { pipeDrainTimeoutFlag.mark() }
        readers.leave()
      }
      readers.notify(queue: .global()) {
        let result = BoundedProcessResult(
          stdout: stdout.collected, stderr: stderr.collected,
          terminationStatus: finished.terminationStatus,
          clean: finished.terminationReason == .exit,
          timedOut: timeoutFlag.marked,
          outputTruncated: stdout.wasTruncated || stderr.wasTruncated,
          pipeDrainTimedOut: pipeDrainTimeoutFlag.marked)
        DispatchQueue.main.async {
          MainActor.assumeIsolated { completion(result) }
        }
      }
    }
    do {
      try process.run()
    } catch {
      outputReader.cancel()
      errorReader.cancel()
      process.terminationHandler = nil
      throw error
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak process] in
      guard let process, process.isRunning else { return }
      timeoutFlag.mark()
      onTimeout("\(label) exceeded \(Int(timeout))s; terminating pid=\(process.processIdentifier)")
      process.terminate()
      DispatchQueue.main.asyncAfter(deadline: .now() + timeoutSettlementGrace) { [weak process] in
        guard let process, process.isRunning else { return }
        kill(process.processIdentifier, SIGKILL)
      }
    }
  }
}
