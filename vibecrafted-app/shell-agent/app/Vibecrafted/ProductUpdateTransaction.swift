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
  case helperAdmitted
  case appReplaced
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
      capturePath: nil)
  }
}

struct ProductUpdateHandoffRecord: Equatable, Sendable {
  var schema: String
  var helperPID: Int32
  var waitPID: Int32
  var waitStart: String
  var receiptURL: String
  var destination: String
  var candidateGeneration: String
  var installedGeneration: String
  var capturePath: String?
  var packURL: String?
  var sourceRevision: String
  var terminalRevision: String
  var frameRevision: String

  static let schemaID = "io.vetcoders.vibecrafted.product-update-handoff.v1"
}

func productUpdatePendingDirectory(home: URL) -> URL {
  home.appendingPathComponent("product-update", isDirectory: true)
}

func productUpdatePendingHandoffURL(home: URL) -> URL {
  productUpdatePendingDirectory(home: home).appendingPathComponent("pending-handoff.json")
}

func writeProductUpdateHandoff(_ record: ProductUpdateHandoffRecord, to url: URL) throws {
  try FileManager.default.createDirectory(
    at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
  let object: [String: Any] = [
    "schema": record.schema,
    "helper_pid": record.helperPID,
    "wait_pid": record.waitPID,
    "wait_start": record.waitStart,
    "receipt_url": record.receiptURL,
    "destination": record.destination,
    "candidate_generation": record.candidateGeneration,
    "installed_generation": record.installedGeneration,
    "capture_path": record.capturePath as Any,
    "pack_url": record.packURL as Any,
    "source_revision": record.sourceRevision,
    "terminal_revision": record.terminalRevision,
    "frame_revision": record.frameRevision,
  ]
  let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .prettyPrinted])
  try data.write(to: url, options: .atomic)
}

func readProductUpdateHandoff(from url: URL) throws -> ProductUpdateHandoffRecord {
  let data = try Data(contentsOf: url)
  let object = try JSONSerialization.jsonObject(with: data)
  guard let root = object as? [String: Any],
    let schema = root["schema"] as? String, schema == ProductUpdateHandoffRecord.schemaID,
    let helperPID = int32(root["helper_pid"]),
    let waitPID = int32(root["wait_pid"]),
    let waitStart = root["wait_start"] as? String,
    let receiptURL = root["receipt_url"] as? String,
    let destination = root["destination"] as? String,
    let candidate = root["candidate_generation"] as? String,
    let installed = root["installed_generation"] as? String,
    let sourceRevision = root["source_revision"] as? String,
    let terminalRevision = root["terminal_revision"] as? String,
    let frameRevision = root["frame_revision"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update handoff record is malformed")
  }
  return ProductUpdateHandoffRecord(
    schema: schema,
    helperPID: helperPID,
    waitPID: waitPID,
    waitStart: waitStart,
    receiptURL: receiptURL,
    destination: destination,
    candidateGeneration: candidate,
    installedGeneration: installed,
    capturePath: root["capture_path"] as? String,
    packURL: root["pack_url"] as? String,
    sourceRevision: sourceRevision,
    terminalRevision: terminalRevision,
    frameRevision: frameRevision)
}

func productUpdateObservedReplacementReceipt(at url: URL) -> ProductUpdateReplacementReceipt? {
  guard let data = try? Data(contentsOf: url) else { return nil }
  return decodeReplacementReceipt(data)
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
    capturePath: root["capture_path"] as? String)
}

private func int32(_ value: Any?) -> Int32? {
  if let number = value as? Int { return Int32(number) }
  if let number = value as? Int32 { return number }
  if let number = value as? NSNumber { return number.int32Value }
  return nil
}
