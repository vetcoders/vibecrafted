import Foundation

/// Mutation boundaries owned by the in-app update transaction.
///
/// `retained` is justified only by this receipt plus a recovered identity — never
/// by a cancelled callback alone. A process that may still publish cannot be
/// reported as unchanged.
enum ProductUpdateMutationBoundary: String, Equatable, Sendable {
  case none
  case downloadedFeed
  case downloadedPayloads
  case verified
  case packPublishing
  case packPublished
  case appReplacing
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
  var cancelledWhileOwnedProcessLive: Bool

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
      cancelledWhileOwnedProcessLive: false)
  }
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
    "cancelled_while_owned_process_live": receipt.cancelledWhileOwnedProcessLive,
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
    cancelledWhileOwnedProcessLive: (root["cancelled_while_owned_process_live"] as? Bool) ?? false)
}
