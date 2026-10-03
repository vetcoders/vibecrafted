import Darwin
import Foundation

/// PID plus `ps` start time. A reused PID with a different start time is not
/// the process this update asked to wait for.
struct ProductUpdateProcessIdentity: Equatable, Sendable {
  var pid: Int32
  var startTime: String
}

struct ProductUpdateBoundProcessResult: Equatable, Sendable {
  var stdout: Data
  var stderr: Data
  var status: Int32
  var timedOut: Bool
}

enum ProductUpdateProcessError: Error, Equatable {
  case executableMissing
  case startFailed
  case timedOut
}

final class ProductUpdateCancelFlag: @unchecked Sendable {
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

private final class ProductUpdateOutputSink: @unchecked Sendable {
  private let lock = NSLock()
  private let limit: Int
  private var storage = Data()

  init(limit: Int) { self.limit = limit }

  func absorb(_ chunk: Data) {
    guard !chunk.isEmpty else { return }
    lock.lock()
    defer { lock.unlock() }
    storage.append(chunk)
    if storage.count > limit {
      storage.removeFirst(storage.count - limit)
    }
  }

  var collected: Data {
    lock.lock()
    defer { lock.unlock() }
    return storage
  }
}

/// Basename-preserving relative path for a signed locator. Absolute build
/// paths and `..` segments cannot become local authority.
func productUpdateStagedRelativePath(_ relative: String) -> String? {
  let trimmed = relative.trimmingCharacters(in: .whitespacesAndNewlines)
  guard !trimmed.isEmpty else { return nil }
  if trimmed.hasPrefix("/") || trimmed.contains("://") { return nil }
  let parts = trimmed.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
  if parts.contains("..") || parts.contains(".") || parts.contains("") { return nil }
  return parts.joined(separator: "/")
}

func captureProductUpdateProcessIdentity(
  pid: Int32,
  psPath: String = "/bin/ps"
) -> ProductUpdateProcessIdentity? {
  guard FileManager.default.isExecutableFile(atPath: psPath) else { return nil }
  switch runProductUpdateBoundProcess(
    executable: psPath,
    arguments: ["-p", String(pid), "-o", "lstart="],
    timeout: 2)
  {
  case .failure:
    return nil
  case .success(let result):
    guard !result.timedOut, result.status == 0 else { return nil }
    let start = String(data: result.stdout, encoding: .utf8)?
      .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard !start.isEmpty else { return nil }
    return ProductUpdateProcessIdentity(pid: pid, startTime: start)
  }
}

/// Bounded subprocess with concurrent pipe drain. Callers must not run this on
/// the UI actor for hdiutil/openssl/verifier work.
func runProductUpdateBoundProcess(
  executable: String,
  arguments: [String],
  stdin: Data? = nil,
  timeout: TimeInterval,
  extraEnvironment: [String: String] = [:]
) -> Result<ProductUpdateBoundProcessResult, ProductUpdateProcessError> {
  guard FileManager.default.isExecutableFile(atPath: executable) else {
    return .failure(.executableMissing)
  }
  let process = Process()
  process.executableURL = URL(fileURLWithPath: executable)
  process.arguments = arguments
  var environment = ProcessInfo.processInfo.environment
  environment.removeValue(forKey: "VIBECRAFTED_PYTHON")
  extraEnvironment.forEach { environment[$0] = $1 }
  process.environment = environment
  let stdout = Pipe()
  let stderr = Pipe()
  let stdoutSink = ProductUpdateOutputSink(limit: 1 << 20)
  let stderrSink = ProductUpdateOutputSink(limit: 1 << 16)
  process.standardOutput = stdout
  process.standardError = stderr
  if stdin != nil {
    process.standardInput = Pipe()
  }
  let readers = DispatchGroup()
  for (handle, sink) in [
    (stdout.fileHandleForReading, stdoutSink),
    (stderr.fileHandleForReading, stderrSink),
  ] {
    readers.enter()
    DispatchQueue.global(qos: .utility).async {
      defer { readers.leave() }
      while true {
        let chunk = handle.availableData
        guard !chunk.isEmpty else { return }
        sink.absorb(chunk)
      }
    }
  }
  do { try process.run() } catch {
    return .failure(.startFailed)
  }
  if let stdin, let input = process.standardInput as? Pipe {
    input.fileHandleForWriting.write(stdin)
    input.fileHandleForWriting.closeFile()
  }
  let deadline = Date().addingTimeInterval(timeout)
  while process.isRunning && Date() < deadline {
    Thread.sleep(forTimeInterval: 0.05)
  }
  var timedOut = false
  if process.isRunning {
    timedOut = true
    process.terminate()
    let reap = Date().addingTimeInterval(2)
    while process.isRunning && Date() < reap {
      Thread.sleep(forTimeInterval: 0.05)
    }
    if process.isRunning {
      kill(process.processIdentifier, SIGKILL)
    }
  }
  process.waitUntilExit()
  readers.wait()
  return .success(
    ProductUpdateBoundProcessResult(
      stdout: stdoutSink.collected,
      stderr: stderrSink.collected,
      status: process.terminationStatus,
      timedOut: timedOut))
}
