import Foundation

/// Mutation boundaries owned by the in-app update transaction.
///
/// `retained` is justified only by this receipt plus a recovered identity — never
/// by a cancelled callback alone. A process that may still publish cannot be
/// reported as unchanged. An admitted helper is not an owned UI process.
enum ProductUpdateMutationBoundary: String, Equatable, Sendable {
  case none
  case downloadedFeed
  case downloadedPayloads
  case verified
  case packPublishing
  case packPublished
  case appReplacing
  case helperReady
  case helperAdmitted
  case appReplaced
  case restoring
  case committed
}

enum ProductUpdateTransactionTerminal: String, Equatable, Sendable {
  case inFlight = "in_flight"
  case committed
  case rolledBack = "rolled_back"
  case retained
}

struct ProductUpdateTransactionReceipt: Equatable, Sendable {
  var schema: String
  var boundary: ProductUpdateMutationBoundary
  var terminal: ProductUpdateTransactionTerminal
  var installedGeneration: String
  var candidateGeneration: String?
  var packPublished: Bool
  var appReplaced: Bool
  var helperArmed: Bool
  var helperAdmitted: Bool
  var cancelledWhileOwnedProcessLive: Bool
  var helperPID: Int32?
  var receiptPath: String?
  var capturePath: String?
  var transactionID: String?
  var journalPath: String?

  static let schemaID = "io.vetcoders.vibecrafted.product-update-transaction.v1"

  static func start(installed: ProductUpdateIdentity) -> ProductUpdateTransactionReceipt {
    ProductUpdateTransactionReceipt(
      schema: schemaID,
      boundary: .none,
      terminal: .inFlight,
      installedGeneration: installed.packGeneration ?? installed.appGeneration,
      candidateGeneration: nil,
      packPublished: false,
      appReplaced: false,
      helperArmed: false,
      helperAdmitted: false,
      cancelledWhileOwnedProcessLive: false,
      helperPID: nil,
      receiptPath: nil,
      capturePath: nil,
      transactionID: nil,
      journalPath: nil)
  }
}

struct ProductUpdateHandoffRecord: Equatable, Sendable {
  var schema: String
  var helperPID: Int32
  var helperStart: String
  var waitPID: Int32
  var waitStart: String
  var receiptURL: String
  var admissionURL: String
  var journalURL: String
  var destination: String
  var candidateGeneration: String
  var installedGeneration: String
  var capturePath: String?
  var packURL: String?
  var sourceRevision: String
  var terminalRevision: String
  var frameRevision: String
  var transactionID: String
  var mode: String
  var phase: String
  var candidateIdentity: String
  var priorIdentity: String

  static let schemaID = "io.vetcoders.vibecrafted.product-update-handoff.v2"
}

enum ProductUpdatePackPublicationState: String, Equatable, Sendable {
  case unpublished
  case published
  case rolledBack = "rolled_back"
  case unresolved
}

struct ProductUpdateRuntimeEvidence: Equatable, Sendable {
  var runningAppIdentity: String
  var expectedCandidateIdentity: String
  var expectedRestoreIdentity: String
  var journalPhase: String
  var journalTransaction: String
  var journalOperation: String
  var packPublication: ProductUpdatePackPublicationState

  static func unbound() -> ProductUpdateRuntimeEvidence {
    ProductUpdateRuntimeEvidence(
      runningAppIdentity: "",
      expectedCandidateIdentity: "",
      expectedRestoreIdentity: "",
      journalPhase: "",
      journalTransaction: "",
      journalOperation: "",
      packPublication: .unresolved)
  }
}

struct ProductUpdateJournalBinding: Equatable, Sendable {
  var schema: String
  var transaction: String
  var operation: String
  var destination: String
  var parent: String
  var source: String
  var sourceIdentity: String
  var priorIdentity: String
  var phase: String
}

let productUpdateValidatedReceiptPhases: Set<String> = [
  "receipt_written", "adopted", "relaunched",
]

enum ProductUpdateHandoffDecision: Equatable, Sendable {
  case awaitReceipt
  case publishPack(ProductUpdateReplacementReceipt)
  case restorePrevious(URL)
  case rolledBack(String)
  case retain(String)
  case stale(String)
}

enum ProductUpdateHandoffAdoption: Equatable, Sendable {
  case none
  case waiting
  case publishing
  case restoring
  case retained
}

func productUpdatePendingDirectory(home: URL) -> URL {
  home.appendingPathComponent("product-update", isDirectory: true)
}

func productUpdatePendingHandoffURL(home: URL) -> URL {
  productUpdatePendingDirectory(home: home).appendingPathComponent("pending-handoff.json")
}

func productUpdateRecoveryURL(home: URL) -> URL {
  productUpdatePendingDirectory(home: home).appendingPathComponent("recovery.json")
}

func productUpdatePackEvidenceURL(home: URL) -> URL {
  productUpdatePendingDirectory(home: home).appendingPathComponent("pack-evidence.json")
}

func writeProductUpdateHandoff(_ record: ProductUpdateHandoffRecord, to url: URL) throws {
  try FileManager.default.createDirectory(
    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
  let object: [String: Any] = [
    "schema": record.schema,
    "helper_pid": record.helperPID,
    "helper_start": record.helperStart,
    "wait_pid": record.waitPID,
    "wait_start": record.waitStart,
    "receipt_url": record.receiptURL,
    "admission_url": record.admissionURL,
    "journal_url": record.journalURL,
    "destination": record.destination,
    "candidate_generation": record.candidateGeneration,
    "installed_generation": record.installedGeneration,
    "capture_path": record.capturePath as Any,
    "pack_url": record.packURL as Any,
    "source_revision": record.sourceRevision,
    "terminal_revision": record.terminalRevision,
    "frame_revision": record.frameRevision,
    "transaction_id": record.transactionID,
    "mode": record.mode,
    "phase": record.phase,
    "candidate_identity": record.candidateIdentity,
    "prior_identity": record.priorIdentity,
  ]
  let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .prettyPrinted])
  try data.write(to: url, options: .atomic)
}

func readProductUpdateHandoff(from url: URL) throws -> ProductUpdateHandoffRecord {
  let data = try Data(contentsOf: url)
  let object = try JSONSerialization.jsonObject(with: data)
  guard let root = object as? [String: Any],
    let schema = root["schema"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update handoff record is malformed")
  }
  if schema != ProductUpdateHandoffRecord.schemaID {
    throw ProductUpdateFeedError.malformed("update handoff schema is stale")
  }
  guard let helperPID = int32(root["helper_pid"]),
    let waitPID = int32(root["wait_pid"]),
    let waitStart = root["wait_start"] as? String,
    let receiptURL = root["receipt_url"] as? String,
    let admissionURL = root["admission_url"] as? String,
    let journalURL = root["journal_url"] as? String,
    let destination = root["destination"] as? String,
    let candidate = root["candidate_generation"] as? String,
    let installed = root["installed_generation"] as? String,
    let sourceRevision = root["source_revision"] as? String,
    let terminalRevision = root["terminal_revision"] as? String,
    let frameRevision = root["frame_revision"] as? String,
    let transactionID = root["transaction_id"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update handoff record is malformed")
  }
  return ProductUpdateHandoffRecord(
    schema: schema,
    helperPID: helperPID,
    helperStart: root["helper_start"] as? String ?? "",
    waitPID: waitPID,
    waitStart: waitStart,
    receiptURL: receiptURL,
    admissionURL: admissionURL,
    journalURL: journalURL,
    destination: destination,
    candidateGeneration: candidate,
    installedGeneration: installed,
    capturePath: root["capture_path"] as? String,
    packURL: root["pack_url"] as? String,
    sourceRevision: sourceRevision,
    terminalRevision: terminalRevision,
    frameRevision: frameRevision,
    transactionID: transactionID,
    mode: root["mode"] as? String ?? ProductUpdateHelperMode.replace.rawValue,
    phase: root["phase"] as? String ?? "helper_ready",
    candidateIdentity: root["candidate_identity"] as? String ?? "",
    priorIdentity: root["prior_identity"] as? String ?? "")
}

func productUpdateObservedReplacementReceipt(at url: URL) -> ProductUpdateReplacementReceipt? {
  guard let data = try? Data(contentsOf: url) else { return nil }
  return decodeReplacementReceipt(data)
}

func productUpdateOwnedPriorApp(at capturePath: String?) -> URL? {
  guard let capturePath, !capturePath.isEmpty else { return nil }
  let capture = URL(fileURLWithPath: capturePath)
  guard capture.lastPathComponent.hasPrefix(".vc-update-capture-") else { return nil }
  let prior = capture.appendingPathComponent("prior.app")
  var isDirectory: ObjCBool = false
  guard FileManager.default.fileExists(atPath: prior.path, isDirectory: &isDirectory),
    isDirectory.boolValue
  else { return nil }
  return prior
}

func productUpdateHelperIdentityLive(pid: Int32, start: String) -> Bool {
  guard pid > 0 else { return false }
  if let current = captureProductUpdateProcessIdentity(pid: pid) {
    return start.isEmpty || current.startTime == start
  }
  return false
}

func writeProductUpdatePackEvidence(
  transaction: String,
  state: ProductUpdatePackPublicationState,
  to url: URL
) throws {
  try FileManager.default.createDirectory(
    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
  let object: [String: Any] = [
    "schema": "io.vetcoders.vibecrafted.product-update-pack-evidence.v1",
    "transaction": transaction,
    "state": state.rawValue,
  ]
  let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .prettyPrinted])
  try data.write(to: url, options: .atomic)
}

func readProductUpdatePackEvidence(from url: URL, transaction: String)
  -> ProductUpdatePackPublicationState?
{
  guard let data = try? Data(contentsOf: url),
    let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
    root["schema"] as? String == "io.vetcoders.vibecrafted.product-update-pack-evidence.v1",
    let stored = root["transaction"] as? String, !stored.isEmpty,
    stored == transaction,
    let raw = root["state"] as? String,
    let state = ProductUpdatePackPublicationState(rawValue: raw)
  else { return nil }
  return state
}

func decodeProductUpdateJournalBinding(_ data: Data) -> ProductUpdateJournalBinding? {
  guard let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
    let schema = root["schema"] as? String,
    schema == "io.vetcoders.vibecrafted.app-update-journal.v1",
    let transaction = root["transaction"] as? String, !transaction.isEmpty,
    let destination = root["destination"] as? String, !destination.isEmpty,
    let parent = root["parent"] as? String, !parent.isEmpty,
    let source = root["source"] as? String, !source.isEmpty,
    let sourceIdentity = root["source_identity"] as? String, !sourceIdentity.isEmpty,
    let phase = root["phase"] as? String, !phase.isEmpty
  else { return nil }
  guard let operation = root["operation"] as? String, !operation.isEmpty else { return nil }
  return ProductUpdateJournalBinding(
    schema: schema,
    transaction: transaction,
    operation: operation,
    destination: destination,
    parent: parent,
    source: source,
    sourceIdentity: sourceIdentity,
    priorIdentity: root["prior_identity"] as? String ?? "",
    phase: phase)
}

func productUpdateContentIdentityToken(at app: URL) -> String? {
  switch runProductUpdateBoundProcess(
    executable: "/usr/bin/codesign",
    arguments: ["--display", "--verbose=4", app.path],
    timeout: 10)
  {
  case .success(let result):
    let text =
      (String(data: result.stderr, encoding: .utf8) ?? "")
      + "\n" + (String(data: result.stdout, encoding: .utf8) ?? "")
    for line in text.split(whereSeparator: \.isNewline) {
      if line.hasPrefix("CDHash=") {
        return "cdhash:\(line.dropFirst("CDHash=".count))"
      }
    }
    return nil
  default:
    return nil
  }
}

func productUpdateObservePackPublication(
  handoff: ProductUpdateHandoffRecord,
  home: URL
) -> ProductUpdatePackPublicationState {
  if let stored = readProductUpdatePackEvidence(
    from: productUpdatePackEvidenceURL(home: home), transaction: handoff.transactionID)
  {
    return stored
  }
  switch handoff.phase {
  case "publishing", "packPublishing", "pack_publishing":
    return .unresolved
  default:
    return .unpublished
  }
}

func productUpdateObserveRuntimeEvidence(
  handoff: ProductUpdateHandoffRecord,
  runningApp: URL,
  home: URL
) -> ProductUpdateRuntimeEvidence {
  let running = productUpdateContentIdentityToken(at: runningApp) ?? ""
  var journalPhase = ""
  var journalTransaction = ""
  var journalOperation = ""
  var candidate = handoff.candidateIdentity
  var prior = handoff.priorIdentity
  if let data = try? Data(contentsOf: URL(fileURLWithPath: handoff.journalURL)),
    let journal = decodeProductUpdateJournalBinding(data)
  {
    journalPhase = journal.phase
    journalTransaction = journal.transaction
    journalOperation = journal.operation
    if candidate.isEmpty { candidate = journal.sourceIdentity }
    if prior.isEmpty { prior = journal.priorIdentity }
  }
  return ProductUpdateRuntimeEvidence(
    runningAppIdentity: running,
    expectedCandidateIdentity: candidate,
    expectedRestoreIdentity: prior,
    journalPhase: journalPhase,
    journalTransaction: journalTransaction,
    journalOperation: journalOperation,
    packPublication: productUpdateObservePackPublication(handoff: handoff, home: home))
}

func decideProductUpdateHandoff(
  handoff: ProductUpdateHandoffRecord,
  replacement: ProductUpdateReplacementReceipt?,
  helperLive: Bool,
  runningDestination: String,
  evidence: ProductUpdateRuntimeEvidence
) -> ProductUpdateHandoffDecision {
  if handoff.transactionID.isEmpty {
    return .stale("the pending update is missing its transaction")
  }
  if handoff.mode != ProductUpdateHelperMode.replace.rawValue
    && handoff.mode != ProductUpdateHelperMode.restore.rawValue
  {
    return .stale("the pending update has an unknown operation")
  }
  if handoff.destination != runningDestination {
    return .stale("the pending update belongs to a different app location")
  }
  if !evidence.journalTransaction.isEmpty, evidence.journalTransaction != handoff.transactionID {
    return .stale("the journal transaction does not belong to this update")
  }
  if !evidence.journalOperation.isEmpty, evidence.journalOperation != handoff.mode {
    return .stale("the journal operation does not belong to this update")
  }
  if handoff.mode == ProductUpdateHelperMode.restore.rawValue {
    if let replacement {
      guard let transaction = replacement.transaction, !transaction.isEmpty else {
        return .stale("the restore receipt is missing its transaction")
      }
      if transaction != handoff.transactionID {
        return .stale("the restore receipt does not belong to this update")
      }
      let operation = replacement.operation ?? replacement.mode ?? ""
      if operation != ProductUpdateHelperMode.restore.rawValue {
        return .stale("the restore receipt is not a restore operation")
      }
      if replacement.destination != handoff.destination {
        return .stale("the restore receipt names a different app")
      }
      if replacement.replaced {
        return decideRestoredHandoff(replacement: replacement, evidence: evidence)
      }
      return .retain("the restore helper left a receipt that does not mark the previous app restored")
    }
    if helperLive {
      return .awaitReceipt
    }
    return .retain(
      "the restore helper stopped before writing a restore receipt; recovery files were kept")
  }
  if let replacement {
    guard let transaction = replacement.transaction, !transaction.isEmpty else {
      return .stale("the replacement receipt is missing its transaction")
    }
    if transaction != handoff.transactionID {
      return .stale("the replacement receipt does not belong to this update")
    }
    let operation = replacement.operation ?? replacement.mode ?? ProductUpdateHelperMode.replace.rawValue
    if operation != ProductUpdateHelperMode.replace.rawValue {
      return .stale("the replacement receipt is not a replace operation")
    }
    if replacement.destination != handoff.destination {
      return .stale("the replacement receipt names a different app")
    }
    if replacement.replaced {
      return decideReplacedHandoff(replacement: replacement, evidence: evidence)
    }
    return .retain("the helper left a receipt that does not mark the app replaced")
  }
  if helperLive {
    return .awaitReceipt
  }
  return .retain(
    "the update helper stopped before writing a replacement receipt; recovery files were kept")
}

private func productUpdatePhaseValidated(_ receipt: ProductUpdateReplacementReceipt) -> Bool {
  guard let phase = receipt.phase, productUpdateValidatedReceiptPhases.contains(phase) else {
    return false
  }
  return true
}

private func decideReplacedHandoff(
  replacement: ProductUpdateReplacementReceipt,
  evidence: ProductUpdateRuntimeEvidence
) -> ProductUpdateHandoffDecision {
  if !productUpdatePhaseValidated(replacement) {
    return .retain("the replacement receipt phase is not a validated terminal phase")
  }
  if evidence.expectedCandidateIdentity.isEmpty || evidence.runningAppIdentity.isEmpty
    || evidence.runningAppIdentity != evidence.expectedCandidateIdentity
  {
    return .retain("the running app is not the exact candidate identity from the journal")
  }
  if evidence.packPublication == .unresolved {
    return .retain("the Runtime Pack installer reports unresolved publication state")
  }
  if evidence.packPublication == .published {
    return .retain("the Runtime Pack is already published; recovery stays open for inspection")
  }
  return .publishPack(replacement)
}

private func decideRestoredHandoff(
  replacement: ProductUpdateReplacementReceipt,
  evidence: ProductUpdateRuntimeEvidence
) -> ProductUpdateHandoffDecision {
  if !productUpdatePhaseValidated(replacement) {
    return .retain("the restore receipt phase is not a validated terminal phase")
  }
  if evidence.expectedRestoreIdentity.isEmpty || evidence.runningAppIdentity.isEmpty
    || evidence.runningAppIdentity != evidence.expectedRestoreIdentity
  {
    return .retain("the running app is not the original preserved capture identity")
  }
  switch evidence.packPublication {
  case .unresolved:
    return .retain(
      "the previous app is back, but the Runtime Pack installer reports unresolved state")
  case .published:
    return .retain(
      "the previous app is back, but the newer Runtime Pack is still published; recovery stays open")
  case .unpublished, .rolledBack:
    return .rolledBack("the previous working version was restored")
  }
}

func productUpdateRestoreRequest(
  handoff: ProductUpdateHandoffRecord,
  priorApp: URL,
  waitPID: Int32?,
  waitStart: String?,
  helperURL: URL?,
  receiptURL: URL
) -> ProductUpdateReplacementRequest {
  ProductUpdateReplacementRequest(
    waitPID: waitPID,
    waitStart: waitStart,
    sourceApp: priorApp,
    destinationApp: URL(fileURLWithPath: handoff.destination),
    relaunch: true,
    receiptURL: receiptURL,
    helperURL: helperURL,
    transactionURL: nil,
    admissionURL: URL(fileURLWithPath: receiptURL.path + ".admission.json"),
    journalURL: URL(fileURLWithPath: handoff.journalURL),
    transactionID: handoff.transactionID,
    mode: .restore,
    resume: false)
}

func writeProductUpdateRecovery(
  handoff: ProductUpdateHandoffRecord,
  reason: String,
  to url: URL
) throws {
  try FileManager.default.createDirectory(
    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
  let object: [String: Any] = [
    "schema": "io.vetcoders.vibecrafted.product-update-recovery.v1",
    "transaction_id": handoff.transactionID,
    "destination": handoff.destination,
    "journal_url": handoff.journalURL,
    "receipt_url": handoff.receiptURL,
    "capture_path": handoff.capturePath as Any,
    "reason": reason,
  ]
  let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .prettyPrinted])
  try data.write(to: url, options: .atomic)
}

func productUpdateRetentionJustified(
  receipt: ProductUpdateTransactionReceipt?,
  recovered: ProductUpdateIdentity,
  previous: ProductUpdateIdentity
) -> Bool {
  guard let receipt, receipt.schema == ProductUpdateTransactionReceipt.schemaID else {
    return false
  }
  if receipt.cancelledWhileOwnedProcessLive && (receipt.packPublished || receipt.appReplaced) {
    return false
  }
  if receipt.terminal != .retained && receipt.terminal != .rolledBack {
    return false
  }
  let previousLabel = previous.packGeneration ?? previous.appGeneration
  let recoveredLabel = recovered.packGeneration ?? recovered.appGeneration
  if receipt.packPublished || receipt.appReplaced {
    return recoveredLabel == previousLabel
      && recovered.appGeneration == previous.appGeneration
  }
  return recoveredLabel == previousLabel
}

func encodeProductUpdateTransaction(_ receipt: ProductUpdateTransactionReceipt) throws -> Data {
  let object: [String: Any] = [
    "schema": receipt.schema,
    "boundary": receipt.boundary.rawValue,
    "terminal": receipt.terminal.rawValue,
    "installed_generation": receipt.installedGeneration,
    "candidate_generation": receipt.candidateGeneration as Any,
    "pack_published": receipt.packPublished,
    "app_replaced": receipt.appReplaced,
    "helper_armed": receipt.helperArmed,
    "helper_admitted": receipt.helperAdmitted,
    "cancelled_while_owned_process_live": receipt.cancelledWhileOwnedProcessLive,
    "helper_pid": receipt.helperPID as Any,
    "receipt_path": receipt.receiptPath as Any,
    "capture_path": receipt.capturePath as Any,
    "transaction_id": receipt.transactionID as Any,
    "journal_path": receipt.journalPath as Any,
  ]
  return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

func decodeProductUpdateTransaction(_ data: Data) throws -> ProductUpdateTransactionReceipt {
  let object = try JSONSerialization.jsonObject(with: data)
  guard let root = object as? [String: Any],
    let schema = root["schema"] as? String,
    let boundaryRaw = root["boundary"] as? String,
    let boundary = ProductUpdateMutationBoundary(rawValue: boundaryRaw),
    let terminalRaw = root["terminal"] as? String,
    let terminal = ProductUpdateTransactionTerminal(rawValue: terminalRaw),
    let installed = root["installed_generation"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update transaction receipt is malformed")
  }
  return ProductUpdateTransactionReceipt(
    schema: schema,
    boundary: boundary,
    terminal: terminal,
    installedGeneration: installed,
    candidateGeneration: root["candidate_generation"] as? String,
    packPublished: (root["pack_published"] as? Bool) ?? false,
    appReplaced: (root["app_replaced"] as? Bool) ?? false,
    helperArmed: (root["helper_armed"] as? Bool) ?? false,
    helperAdmitted: (root["helper_admitted"] as? Bool) ?? false,
    cancelledWhileOwnedProcessLive: (root["cancelled_while_owned_process_live"] as? Bool) ?? false,
    helperPID: int32(root["helper_pid"]),
    receiptPath: root["receipt_path"] as? String,
    capturePath: root["capture_path"] as? String,
    transactionID: root["transaction_id"] as? String,
    journalPath: root["journal_path"] as? String)
}

private func int32(_ value: Any?) -> Int32? {
  if let number = value as? Int { return Int32(number) }
  if let number = value as? Int32 { return number }
  if let number = value as? NSNumber { return number.int32Value }
  return nil
}
