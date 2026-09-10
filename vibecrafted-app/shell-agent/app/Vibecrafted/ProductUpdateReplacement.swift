import Darwin
import Foundation

struct ProductUpdateReplacementRequest: Equatable, Sendable {
  var waitPID: Int32?
  var waitStart: String?
  var waitTimeout: TimeInterval
  var sourceApp: URL
  var destinationApp: URL
  var relaunch: Bool
  var receiptURL: URL
  var helperURL: URL?
  var expectedIdentifier: String
  var expectedTeamID: String
  var transactionURL: URL?

  init(
    waitPID: Int32? = nil,
    waitStart: String? = nil,
    waitTimeout: TimeInterval = 30,
    sourceApp: URL,
    destinationApp: URL,
    relaunch: Bool,
    receiptURL: URL,
    helperURL: URL? = nil,
    expectedIdentifier: String = productUpdateExpectedBundleIdentifier,
    expectedTeamID: String = productUpdateExpectedTeamID,
    transactionURL: URL? = nil
  ) {
    self.waitPID = waitPID
    self.waitStart = waitStart
    self.waitTimeout = waitTimeout
    self.sourceApp = sourceApp
    self.destinationApp = destinationApp
    self.relaunch = relaunch
    self.receiptURL = receiptURL
    self.helperURL = helperURL
    self.expectedIdentifier = expectedIdentifier
    self.expectedTeamID = expectedTeamID
    self.transactionURL = transactionURL
  }
}

struct ProductUpdateReplacementReceipt: Equatable, Sendable {
  var replaced: Bool
  var relaunched: Bool
  var destination: String
  var detail: String
  var capture: String?
  var transaction: String?
}

/// Helper is armed and detached. This is not a replacement receipt.
struct ProductUpdateReplacementAdmission: Equatable, Sendable {
  var helperPID: Int32
  var waitIdentity: ProductUpdateProcessIdentity?
  var receiptURL: URL
  var transactionURL: URL?
}

enum ProductUpdateReplacementError: Error, Equatable {
  case sourceMissing
  case destinationBusy
  case replaceFailed(String)
  case helperMissing
  case helperFailed(String)
  case receiptMissing

  var localizedDescription: String {
    switch self {
    case .sourceMissing: return "The prepared app is missing, so nothing was replaced."
    case .destinationBusy: return "The running app is still live, so nothing was replaced."
    case .replaceFailed(let reason): return reason
    case .helperMissing: return "The update helper is missing, so the app was not replaced."
    case .helperFailed(let reason): return reason
    case .receiptMissing:
      return "The update helper did not write a replacement receipt, so the app was not marked replaced."
    }
  }
}

func productUpdateHelperArguments(_ request: ProductUpdateReplacementRequest) -> [String] {
  var arguments = [
    "--source", request.sourceApp.path,
    "--destination", request.destinationApp.path,
    "--receipt", request.receiptURL.path,
    "--expected-identifier", request.expectedIdentifier,
    "--expected-team", request.expectedTeamID,
    "--wait-timeout", String(Int(request.waitTimeout.rounded(.up))),
  ]
  if let pid = request.waitPID {
    arguments += ["--wait-pid", String(pid)]
  }
  if let start = request.waitStart, !start.isEmpty {
    arguments += ["--wait-start", start]
  }
  if request.relaunch {
    arguments.append("--relaunch")
  }
  return arguments
}

func productUpdateRepositoryHelperScript(
  startingAt filePath: String = #filePath
) -> URL? {
  var directory = URL(fileURLWithPath: filePath).deletingLastPathComponent()
  for _ in 0..<8 {
    let candidate = directory.appendingPathComponent("scripts/vc-app-update.sh")
    if FileManager.default.isReadableFile(atPath: candidate.path) {
      return candidate
    }
    directory.deleteLastPathComponent()
  }
  return nil
}

func resolveProductUpdateHelperURL(_ request: ProductUpdateReplacementRequest) -> URL? {
  guard let helper = request.helperURL else { return nil }
  if FileManager.default.isExecutableFile(atPath: helper.path)
    || FileManager.default.isReadableFile(atPath: helper.path)
  {
    return helper
  }
  return nil
}

/// Launch the script helper. There is no in-process Swift mutation twin.
func replaceProductUpdateApp(
  _ request: ProductUpdateReplacementRequest
) -> Result<ProductUpdateReplacementReceipt, ProductUpdateReplacementError> {
  guard FileManager.default.fileExists(atPath: request.sourceApp.path) else {
    return .failure(.sourceMissing)
  }
  guard let helper = resolveProductUpdateHelperURL(request) else {
    return .failure(.helperMissing)
  }
  return runProductUpdateHelper(helper, request: request)
}

func spawnProductUpdateHelper(
  _ request: ProductUpdateReplacementRequest
) throws -> Process {
  guard let helper = resolveProductUpdateHelperURL(request) else {
    throw ProductUpdateReplacementError.helperMissing
  }
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/bin/bash")
  process.arguments = [helper.path] + productUpdateHelperArguments(request)
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  try process.run()
  setpgid(process.processIdentifier, process.processIdentifier)
  return process
}

private func runProductUpdateHelper(
  _ helper: URL,
  request: ProductUpdateReplacementRequest
) -> Result<ProductUpdateReplacementReceipt, ProductUpdateReplacementError> {
  switch runProductUpdateBoundProcess(
    executable: "/bin/bash",
    arguments: [helper.path] + productUpdateHelperArguments(request),
    timeout: request.waitTimeout + 60)
  {
  case .failure:
    return .failure(.helperFailed("the update helper could not start"))
  case .success(let result):
    if result.timedOut {
      return .failure(.destinationBusy)
    }
    if result.status != 0 {
      let detail = String(data: result.stderr, encoding: .utf8) ?? ""
      if result.status == 5 {
        return .failure(.destinationBusy)
      }
      if result.status == 3 {
        return .failure(.sourceMissing)
      }
      return .failure(
        .helperFailed(detail.isEmpty ? "update helper exited \(result.status)" : detail))
    }
    guard let data = try? Data(contentsOf: request.receiptURL),
      let decoded = decodeReplacementReceipt(data), decoded.replaced
    else {
      return .failure(.receiptMissing)
    }
    return .success(decoded)
  }
}

func decodeReplacementReceipt(_ data: Data) -> ProductUpdateReplacementReceipt? {
  guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
    let destination = root["destination"] as? String
  else { return nil }
  return ProductUpdateReplacementReceipt(
    replaced: (root["replaced"] as? Bool) ?? false,
    relaunched: (root["relaunched"] as? Bool) ?? false,
    destination: destination,
    detail: (root["detail"] as? String) ?? "",
    capture: root["capture"] as? String,
    transaction: root["transaction"] as? String)
}
