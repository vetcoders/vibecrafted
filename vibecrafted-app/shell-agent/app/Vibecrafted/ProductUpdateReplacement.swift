import Foundation

struct ProductUpdateReplacementRequest: Equatable, Sendable {
  var waitPID: Int32?
  var sourceApp: URL
  var destinationApp: URL
  var relaunch: Bool
  var receiptURL: URL
  var helperURL: URL?
}

struct ProductUpdateReplacementReceipt: Equatable, Sendable {
  var replaced: Bool
  var relaunched: Bool
  var destination: String
  var detail: String
}

enum ProductUpdateReplacementError: Error, Equatable {
  case sourceMissing
  case destinationBusy
  case replaceFailed(String)
  case helperMissing
  case helperFailed(String)

  var localizedDescription: String {
    switch self {
    case .sourceMissing: return "The prepared app is missing, so nothing was replaced."
    case .destinationBusy: return "The running app could not be replaced."
    case .replaceFailed(let reason): return reason
    case .helperMissing: return "The update helper is missing, so the app was not replaced."
    case .helperFailed(let reason): return reason
    }
  }
}

func productUpdateHelperArguments(_ request: ProductUpdateReplacementRequest) -> [String] {
  var arguments = [
    "--source", request.sourceApp.path,
    "--destination", request.destinationApp.path,
    "--receipt", request.receiptURL.path,
  ]
  if let pid = request.waitPID {
    arguments += ["--wait-pid", String(pid)]
  }
  if request.relaunch {
    arguments.append("--relaunch")
  }
  return arguments
}

/// Replace one `.app` bundle with a staged candidate. Does not stop Frame, PTYs,
/// workers, or rewrite PATH / MCP. Tests call this in-process against a temp
/// destination. The live App spawns `Contents/Helpers/vc-app-update`.
func replaceProductUpdateApp(
  _ request: ProductUpdateReplacementRequest
) -> Result<ProductUpdateReplacementReceipt, ProductUpdateReplacementError> {
  if let helper = request.helperURL {
    return runProductUpdateHelper(helper, request: request)
  }
  return replaceProductUpdateAppInProcess(request)
}

func replaceProductUpdateAppInProcess(
  _ request: ProductUpdateReplacementRequest
) -> Result<ProductUpdateReplacementReceipt, ProductUpdateReplacementError> {
  let files = FileManager.default
  guard files.fileExists(atPath: request.sourceApp.path) else {
    return .failure(.sourceMissing)
  }
  if let pid = request.waitPID {
    waitForProcessExit(pid, timeout: 30)
  }
  let parent = request.destinationApp.deletingLastPathComponent()
  do {
    try files.createDirectory(at: parent, withIntermediateDirectories: true)
    if files.fileExists(atPath: request.destinationApp.path) {
      let backup = parent.appendingPathComponent(
        "\(request.destinationApp.lastPathComponent).vibecrafted-update-backup")
      if files.fileExists(atPath: backup.path) {
        try files.removeItem(at: backup)
      }
      try files.moveItem(at: request.destinationApp, to: backup)
      do {
        try ditto(from: request.sourceApp, to: request.destinationApp)
        try files.removeItem(at: backup)
      } catch {
        if files.fileExists(atPath: request.destinationApp.path) {
          try? files.removeItem(at: request.destinationApp)
        }
        try? files.moveItem(at: backup, to: request.destinationApp)
        return .failure(.replaceFailed(error.localizedDescription))
      }
    } else {
      try ditto(from: request.sourceApp, to: request.destinationApp)
    }
  } catch {
    return .failure(.replaceFailed(error.localizedDescription))
  }
  var relaunched = false
  if request.relaunch {
    relaunched = relaunchProductUpdateApp(at: request.destinationApp)
  }
  let receipt = ProductUpdateReplacementReceipt(
    replaced: true,
    relaunched: relaunched,
    destination: request.destinationApp.path,
    detail: "replaced")
  writeReplacementReceipt(receipt, to: request.receiptURL)
  return .success(receipt)
}

private func runProductUpdateHelper(
  _ helper: URL,
  request: ProductUpdateReplacementRequest
) -> Result<ProductUpdateReplacementReceipt, ProductUpdateReplacementError> {
  guard FileManager.default.isExecutableFile(atPath: helper.path) else {
    return .failure(.helperMissing)
  }
  let process = Process()
  process.executableURL = helper
  process.arguments = productUpdateHelperArguments(request)
  let stdout = Pipe()
  let stderr = Pipe()
  process.standardOutput = stdout
  process.standardError = stderr
  do { try process.run() } catch {
    return .failure(.helperFailed(error.localizedDescription))
  }
  process.waitUntilExit()
  if process.terminationStatus != 0 {
    let detail = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    return .failure(.helperFailed(detail.isEmpty ? "update helper exited \(process.terminationStatus)" : detail))
  }
  if let data = try? Data(contentsOf: request.receiptURL),
    let decoded = decodeReplacementReceipt(data)
  {
    return .success(decoded)
  }
  return .success(
    ProductUpdateReplacementReceipt(
      replaced: true, relaunched: request.relaunch, destination: request.destinationApp.path,
      detail: "helper"))
}

func spawnProductUpdateHelper(
  _ request: ProductUpdateReplacementRequest
) throws -> Process {
  guard let helper = request.helperURL,
    FileManager.default.isExecutableFile(atPath: helper.path)
  else {
    throw ProductUpdateReplacementError.helperMissing
  }
  let process = Process()
  process.executableURL = helper
  process.arguments = productUpdateHelperArguments(request)
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  try process.run()
  return process
}

private func ditto(from source: URL, to destination: URL) throws {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
  process.arguments = [source.path, destination.path]
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  try process.run()
  process.waitUntilExit()
  if process.terminationStatus != 0 {
    throw ProductUpdateReplacementError.replaceFailed("ditto failed")
  }
}

private func relaunchProductUpdateApp(at url: URL) -> Bool {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
  process.arguments = ["-n", url.path]
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  do {
    try process.run()
    process.waitUntilExit()
    return process.terminationStatus == 0
  } catch {
    return false
  }
}

private func waitForProcessExit(_ pid: Int32, timeout: TimeInterval) {
  let deadline = Date().addingTimeInterval(timeout)
  while Date() < deadline {
    if kill(pid, 0) != 0 { return }
    Thread.sleep(forTimeInterval: 0.05)
  }
}

private func writeReplacementReceipt(
  _ receipt: ProductUpdateReplacementReceipt, to url: URL
) {
  let object: [String: Any] = [
    "replaced": receipt.replaced,
    "relaunched": receipt.relaunched,
    "destination": receipt.destination,
    "detail": receipt.detail,
  ]
  if let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
    try? data.write(to: url, options: .atomic)
  }
}

private func decodeReplacementReceipt(_ data: Data) -> ProductUpdateReplacementReceipt? {
  guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
    let destination = root["destination"] as? String
  else { return nil }
  return ProductUpdateReplacementReceipt(
    replaced: (root["replaced"] as? Bool) ?? false,
    relaunched: (root["relaunched"] as? Bool) ?? false,
    destination: destination,
    detail: (root["detail"] as? String) ?? "")
}
