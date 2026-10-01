import Darwin
import Foundation

enum ProductUpdateHelperMode: String, Equatable, Sendable {
  case replace
  case restore
  case recover
}

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
  var admissionURL: URL?
  var journalURL: URL?
  var transactionID: String?
  var mode: ProductUpdateHelperMode
  var resume: Bool

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
    transactionURL: URL? = nil,
    admissionURL: URL? = nil,
    journalURL: URL? = nil,
    transactionID: String? = nil,
    mode: ProductUpdateHelperMode = .replace,
    resume: Bool = false
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
    self.admissionURL = admissionURL
    self.journalURL = journalURL
    self.transactionID = transactionID
    self.mode = mode
    self.resume = resume
  }
}

struct ProductUpdateReplacementReceipt: Equatable, Sendable {
  var replaced: Bool
  var relaunched: Bool
  var destination: String
  var detail: String
  var capture: String?
  var transaction: String?
  var journal: String? = nil
  var mode: String? = nil
  var operation: String? = nil
  var phase: String? = nil
  var sourceIdentity: String? = nil
  var priorIdentity: String? = nil
}

/// Helper-owned READY after preflight. Process.isRunning is not admission.
struct ProductUpdateReplacementAdmission: Equatable, Sendable {
  var helperPID: Int32
  var waitIdentity: ProductUpdateProcessIdentity?
  var receiptURL: URL
  var transactionURL: URL?
  var transactionID: String
  var admissionURL: URL
  var journalURL: URL?
  var destination: String
  var candidateIdentifier: String
  var candidateTeamID: String
  var ready: Bool
  var candidateIdentity: String

  init(
    helperPID: Int32,
    waitIdentity: ProductUpdateProcessIdentity?,
    receiptURL: URL,
    transactionURL: URL?,
    transactionID: String = "",
    admissionURL: URL? = nil,
    journalURL: URL? = nil,
    destination: String = "",
    candidateIdentifier: String = productUpdateExpectedBundleIdentifier,
    candidateTeamID: String = productUpdateExpectedTeamID,
    ready: Bool = false,
    candidateIdentity: String = ""
  ) {
    self.helperPID = helperPID
    self.waitIdentity = waitIdentity
    self.receiptURL = receiptURL
    self.transactionURL = transactionURL
    self.transactionID = transactionID
    self.admissionURL = admissionURL ?? URL(fileURLWithPath: receiptURL.path + ".admission.json")
    self.journalURL = journalURL
    self.destination = destination
    self.candidateIdentifier = candidateIdentifier
    self.candidateTeamID = candidateTeamID
    self.ready = ready
    self.candidateIdentity = candidateIdentity
  }
}

struct ProductUpdateHelperAdmissionRecord: Equatable, Sendable {
  var status: String
  var transaction: String
  var destination: String
  var identifier: String
  var teamID: String
  var parentPID: String
  var parentStart: String
  var journal: String
  var capture: String
  var receipt: String
  var detail: String
  var sourceIdentity: String
  var mode: String
}

enum ProductUpdateReplacementError: Error, Equatable {
  case sourceMissing
  case destinationBusy
  case replaceFailed(String)
  case helperMissing
  case helperFailed(String)
  case receiptMissing
  case admissionRejected(String)
  case admissionTimeout
  case staleAdmission

  var localizedDescription: String {
    switch self {
    case .sourceMissing: return "The prepared app is missing, so nothing was replaced."
    case .destinationBusy: return "The running app is still live, so nothing was replaced."
    case .replaceFailed(let reason): return reason
    case .helperMissing: return "The update helper is missing, so the app was not replaced."
    case .helperFailed(let reason): return reason
    case .receiptMissing:
      return "The update helper did not write a replacement receipt, so the app was not marked replaced."
    case .admissionRejected(let reason):
      return reason
    case .admissionTimeout:
      return "The update helper did not admit the replacement in time. Your current version stays installed."
    case .staleAdmission:
      return "The update helper admission did not match this transaction, so the window stayed open."
    }
  }
}

func productUpdateAdmissionURL(for request: ProductUpdateReplacementRequest) -> URL {
  request.admissionURL ?? URL(fileURLWithPath: request.receiptURL.path + ".admission.json")
}

func productUpdateJournalURL(for request: ProductUpdateReplacementRequest) -> URL {
  request.journalURL ?? URL(fileURLWithPath: request.receiptURL.path + ".journal.json")
}

func productUpdateHelperArguments(_ request: ProductUpdateReplacementRequest) -> [String] {
  var arguments = [
    "--source", request.sourceApp.path,
    "--destination", request.destinationApp.path,
    "--receipt", request.receiptURL.path,
    "--admission", productUpdateAdmissionURL(for: request).path,
    "--journal", productUpdateJournalURL(for: request).path,
    "--mode", request.mode.rawValue,
    "--expected-identifier", request.expectedIdentifier,
    "--expected-team", request.expectedTeamID,
    "--wait-timeout", String(Int(request.waitTimeout.rounded(.up))),
  ]
  if let transaction = request.transactionID, !transaction.isEmpty {
    arguments += ["--transaction", transaction]
  }
  if let pid = request.waitPID {
    arguments += ["--wait-pid", String(pid)]
  }
  if let start = request.waitStart, !start.isEmpty {
    arguments += ["--wait-start", start]
  }
  if request.relaunch {
    arguments.append("--relaunch")
  }
  if request.resume {
    arguments.append("--resume")
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

func decodeProductUpdateHelperAdmission(_ data: Data) -> ProductUpdateHelperAdmissionRecord? {
  guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
    let schema = root["schema"] as? String,
    schema == "io.vetcoders.vibecrafted.app-replacement-admission.v1",
    let status = root["status"] as? String,
    let transaction = root["transaction"] as? String,
    let destination = root["destination"] as? String,
    let identifier = root["identifier"] as? String,
    let teamID = root["team_id"] as? String
  else { return nil }
  return ProductUpdateHelperAdmissionRecord(
    status: status,
    transaction: transaction,
    destination: destination,
    identifier: identifier,
    teamID: teamID,
    parentPID: root["parent_pid"] as? String ?? "",
    parentStart: root["parent_start"] as? String ?? "",
    journal: root["journal"] as? String ?? "",
    capture: root["capture"] as? String ?? "",
    receipt: root["receipt"] as? String ?? "",
    detail: root["detail"] as? String ?? "",
    sourceIdentity: root["source_identity"] as? String ?? "",
    mode: root["mode"] as? String ?? "")
}

func productUpdateAdmissionMatches(
  _ record: ProductUpdateHelperAdmissionRecord,
  request: ProductUpdateReplacementRequest
) -> Bool {
  if record.destination != request.destinationApp.path { return false }
  if record.identifier != request.expectedIdentifier { return false }
  if record.teamID != request.expectedTeamID { return false }
  if record.mode != request.mode.rawValue { return false }
  if record.transaction.isEmpty { return false }
  if let transaction = request.transactionID, !transaction.isEmpty,
    record.transaction != transaction
  {
    return false
  }
  if let pid = request.waitPID, record.parentPID != String(pid) { return false }
  if let start = request.waitStart, !start.isEmpty, record.parentStart != start { return false }
  return true
}

func terminateProductUpdateHelper(_ process: Process) {
  guard process.isRunning else { return }
  let pid = process.processIdentifier
  kill(-pid, SIGTERM)
  let deadline = Date().addingTimeInterval(2)
  while process.isRunning && Date() < deadline {
    Thread.sleep(forTimeInterval: 0.05)
  }
  if process.isRunning {
    kill(-pid, SIGKILL)
    process.waitUntilExit()
  }
}

/// Observe helper-owned READY. This is not a sleep-for-success gate: the
/// admission file or helper exit is the signal.
func waitForProductUpdateHelperAdmission(
  process: Process,
  request: ProductUpdateReplacementRequest,
  timeout: TimeInterval = 20
) -> Result<ProductUpdateReplacementAdmission, ProductUpdateReplacementError> {
  let admissionURL = productUpdateAdmissionURL(for: request)
  let deadline = Date().addingTimeInterval(timeout)
  while Date() < deadline {
    if let data = try? Data(contentsOf: admissionURL),
      let record = decodeProductUpdateHelperAdmission(data)
    {
      if record.status == "rejected" {
        terminateProductUpdateHelper(process)
        return .failure(.admissionRejected(record.detail.isEmpty ? "helper rejected admission" : record.detail))
      }
      if record.status == "ready" {
        guard productUpdateAdmissionMatches(record, request: request) else {
          terminateProductUpdateHelper(process)
          return .failure(.staleAdmission)
        }
        return .success(
          ProductUpdateReplacementAdmission(
            helperPID: process.processIdentifier,
            waitIdentity: request.waitPID.map {
              ProductUpdateProcessIdentity(pid: $0, startTime: request.waitStart ?? "")
            },
            receiptURL: request.receiptURL,
            transactionURL: request.transactionURL,
            transactionID: record.transaction,
            admissionURL: admissionURL,
            journalURL: URL(fileURLWithPath: record.journal),
            destination: record.destination,
            candidateIdentifier: record.identifier,
            candidateTeamID: record.teamID,
            ready: true,
            candidateIdentity: record.sourceIdentity))
      }
    }
    if !process.isRunning {
      if let data = try? Data(contentsOf: admissionURL),
        let record = decodeProductUpdateHelperAdmission(data), record.status == "rejected"
      {
        return .failure(
          .admissionRejected(record.detail.isEmpty ? "helper rejected admission" : record.detail))
      }
      return .failure(.helperFailed("helper exited before READY admission"))
    }
    Thread.sleep(forTimeInterval: 0.05)
  }
  terminateProductUpdateHelper(process)
  return .failure(.admissionTimeout)
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

func admitProductUpdateHelper(
  _ request: ProductUpdateReplacementRequest,
  admissionTimeout: TimeInterval = 20
) -> Result<ProductUpdateReplacementAdmission, ProductUpdateReplacementError> {
  guard FileManager.default.fileExists(atPath: request.sourceApp.path) else {
    return .failure(.sourceMissing)
  }
  do {
    let process = try spawnProductUpdateHelper(request)
    return waitForProductUpdateHelperAdmission(
      process: process, request: request, timeout: admissionTimeout)
  } catch let error as ProductUpdateReplacementError {
    return .failure(error)
  } catch {
    return .failure(.helperFailed(error.localizedDescription))
  }
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
      if result.status == 13 {
        return .failure(.replaceFailed("another update transaction is already using this app"))
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
    let destination = root["destination"] as? String, !destination.isEmpty,
    let transaction = root["transaction"] as? String, !transaction.isEmpty
  else { return nil }
  let operation = (root["operation"] as? String).flatMap { $0.isEmpty ? nil : $0 }
  let mode = (root["mode"] as? String).flatMap { $0.isEmpty ? nil : $0 }
  guard let boundOperation = operation ?? mode else { return nil }
  return ProductUpdateReplacementReceipt(
    replaced: (root["replaced"] as? Bool) ?? false,
    relaunched: (root["relaunched"] as? Bool) ?? false,
    destination: destination,
    detail: (root["detail"] as? String) ?? "",
    capture: root["capture"] as? String,
    transaction: transaction,
    journal: root["journal"] as? String,
    mode: mode ?? boundOperation,
    operation: boundOperation,
    phase: root["phase"] as? String,
    sourceIdentity: root["source_identity"] as? String,
    priorIdentity: root["prior_identity"] as? String)
}

/// Dispose rollback bytes only through the existing helper, which re-observes
/// installer publication and signed identity under the destination flock.
/// Exit 13 is a live replacement helper, not permission to bypass its lock.
func settleProductUpdateCapture(
  handoff: ProductUpdateHandoffRecord, helper: URL?, runtimeHome: URL
) -> Result<Void, ProductUpdateReplacementError> {
  guard let helper else { return .failure(.helperMissing) }
  let arguments = [
    helper.path, "--mode", "settle",
    "--source", handoff.destination,
    "--destination", handoff.destination,
    "--receipt", handoff.receiptURL,
    "--admission", handoff.admissionURL,
    "--journal", handoff.journalURL,
    "--transaction", handoff.transactionID,
  ]
  return runProductUpdateCaptureSettlement(arguments: arguments, runtimeHome: runtimeHome)
}

/// Same-generation UI repair can complete a CLI replacement that previously
/// retained recovery. Only our unique transaction registry is a discovery
/// surface; the helper still validates every receipt, inode and publication.
func settleProductUpdateLocalCaptures(
  home: URL, destination: URL, helper: URL?, runtimeHome: URL
) -> Result<Void, ProductUpdateReplacementError> {
  let registry = home.appendingPathComponent("vibecrafted-product-update/transactions")
  let latest = home.appendingPathComponent("vibecrafted-product-update/app-capture-latest.json")
  guard FileManager.default.fileExists(atPath: registry.path)
    || FileManager.default.fileExists(atPath: latest.path)
  else { return .success(()) }
  guard let helper, let identity = productUpdateContentIdentityToken(at: destination) else {
    return .failure(.helperMissing)
  }
  do {
    var references: [(URL, String)] = []
    if FileManager.default.fileExists(atPath: latest.path) {
      let data = try Data(contentsOf: latest)
      guard let projection = try JSONSerialization.jsonObject(with: data) as? [String: Any],
        let receipt = projection["receipt"] as? String,
        let admission = projection["admission"] as? String
      else { return .failure(.receiptMissing) }
      references.append((URL(fileURLWithPath: receipt), admission))
    }
    let transactions = FileManager.default.fileExists(atPath: registry.path)
      ? try FileManager.default.contentsOfDirectory(at: registry, includingPropertiesForKeys: nil)
      : []
    for transaction in transactions {
      for name in ["receipt.json", "replacement-receipt.json"] {
        let url = transaction.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: url.path) {
          references.append((url, name == "receipt.json"
            ? transaction.appendingPathComponent("admission.json").path
            : url.path + ".admission.json"))
        }
      }
    }
    var seen: Set<String> = []
    for (url, admission) in references {
      guard seen.insert(url.path).inserted else { continue }
      guard let receipt = productUpdateObservedReplacementReceipt(at: url) else {
        return .failure(.receiptMissing)
      }
      guard receipt.destination == destination.path, receipt.sourceIdentity == identity,
        receipt.operation == "replace"
      else { continue }
      guard let journal = receipt.journal, let txn = receipt.transaction else {
        return .failure(.receiptMissing)
      }
      let result = runProductUpdateCaptureSettlement(arguments: [
        helper.path, "--mode", "settle", "--source", destination.path,
        "--destination", destination.path, "--receipt", url.path,
        "--admission", admission, "--journal", journal, "--transaction", txn,
      ], runtimeHome: runtimeHome)
      if case .failure = result { return result }
    }
    return .success(())
  } catch {
    return .failure(.helperFailed(error.localizedDescription))
  }
}

private func runProductUpdateCaptureSettlement(
  arguments: [String], runtimeHome: URL
) -> Result<Void, ProductUpdateReplacementError> {
  let deadline = Date().addingTimeInterval(20)
  repeat {
    switch runProductUpdateBoundProcess(
      executable: "/bin/bash", arguments: arguments, timeout: 120,
      extraEnvironment: ["VIBECRAFTED_RUNTIME_HOME": runtimeHome.path])
    {
    case .failure:
      return .failure(.helperFailed("The capture settlement helper could not start."))
    case .success(let result):
      if result.status == 13 && !result.timedOut {
        Thread.sleep(forTimeInterval: 0.1)
        continue
      }
      guard !result.timedOut && result.status == 0 else {
        return .failure(.helperFailed(
          String(data: result.stderr, encoding: .utf8)
            ?? "Capture cleanup did not finish; the transaction remains retryable."))
      }
      return .success(())
    }
  } while Date() < deadline
  return .failure(.destinationBusy)
}
