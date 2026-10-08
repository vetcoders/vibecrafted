import Foundation

struct RuntimeActivitySummary: Decodable, Equatable {
  let lanes: Int
  let worktrees: Int
}

private struct RuntimeActivitySnapshot: Decodable {
  let schemaVersion: String
  let summary: RuntimeActivitySummary

  enum CodingKeys: String, CodingKey {
    case schemaVersion = "schema_version"
    case summary
  }
}

enum RuntimeActivityTruth: Equatable {
  case available(RuntimeActivitySummary)
  case unavailable(String)
}

func decodeRuntimeActivityTruth(data: Data, terminationStatus: Int32) -> RuntimeActivityTruth {
  guard terminationStatus == 0 else {
    return .unavailable("lifecycle query exited with status \(terminationStatus)")
  }
  do {
    let snapshot = try JSONDecoder().decode(RuntimeActivitySnapshot.self, from: data)
    guard snapshot.schemaVersion == "vibecrafted.lifecycle-activity.v1" else {
      return .unavailable("lifecycle query returned an unsupported schema")
    }
    guard snapshot.summary.lanes >= 0, snapshot.summary.worktrees >= 0,
      snapshot.summary.worktrees <= snapshot.summary.lanes
    else {
      return .unavailable("lifecycle query returned impossible counts")
    }
    return .available(snapshot.summary)
  } catch {
    return .unavailable("lifecycle query returned malformed JSON")
  }
}
