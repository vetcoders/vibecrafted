import Foundation

// The tray renders ONE caretaker truth. The Python runtime fuses server
// identity, liveness, receipt freshness, resume backlog and control-plane
// upkeep into the versioned `vibecrafted.caretaker.v1` envelope and derives
// the verdict once; this file decodes that envelope and maps it onto menu
// state verbatim. A tray that re-fused raw receipts and service probes into
// its own health call would be a second truth with pixels on — that fusion
// used to live here and was removed deliberately.

enum ServerLifecycleAction: String {
  case start
  case stop
  case restart
}

enum TrayServerHealth: String {
  case checking
  case healthy
  case transitioning
  case failed
  case neutral
}

struct ServerMenuState {
  let header: String
  let detail: String
  let health: TrayServerHealth
  let canStart: Bool
  let canStop: Bool
  let canRestart: Bool
}

/// The `vibecrafted.caretaker.v1` envelope as the tray consumes it. Every
/// field is optional-lean: a partial or older envelope must degrade into an
/// honest menu state, never crash the status item.
struct CaretakerEnvelope: Decodable {
  struct Endpoint: Decodable {
    let host: String?
    let port: Int?
    let url: String?
  }

  struct ManagedPair: Decodable {
    let guardianPID: Int?
    let serverPID: Int?

    enum CodingKeys: String, CodingKey {
      case guardianPID = "guardian_pid"
      case serverPID = "server_pid"
    }
  }

  struct Receipt: Decodable {
    let path: String?
    let present: Bool?
    let stale: Bool?
  }

  struct Liveness: Decodable {
    let probed: Bool?
    let reachable: Bool?
    let reason: String?
    let version: String?
  }

  struct LogProjection: Decodable {
    let available: Bool
    let directory: String
    let stdout: String
    let stderr: String
    let reason: String?
  }

  struct Server: Decodable {
    let available: Bool?
    let reason: String?
    let state: String?
    let supervisorPID: Int?
    let lastError: String?
    let endpoint: Endpoint?
    let receipt: Receipt?
    let liveness: Liveness?
    let managedPair: ManagedPair?
    let logs: LogProjection?

    enum CodingKeys: String, CodingKey {
      case available
      case reason
      case state
      case supervisorPID = "supervisor_pid"
      case lastError = "last_error"
      case endpoint
      case receipt
      case liveness
      case managedPair = "managed_pair"
      case logs
    }
  }

  struct Finding: Decodable {
    let code: String
    let severity: String
    let detail: String
  }

  struct Verdict: Decodable {
    let health: String
    let serverHealth: String?
    let serverState: String?
    let header: String
    let detail: String
    let findings: [Finding]?

    enum CodingKeys: String, CodingKey {
      case health
      case serverHealth = "server_health"
      case serverState = "server_state"
      case header
      case detail
      case findings
    }
  }

  struct Action: Decodable {
    let enabled: Bool
    let reason: String?
    let url: String?
  }

  struct Actions: Decodable {
    let start: Action?
    let stop: Action?
    let restart: Action?
    let openConsole: Action?
    let openLogs: Action?

    enum CodingKeys: String, CodingKey {
      case start
      case stop
      case restart
      case openConsole = "open_console"
      case openLogs = "open_logs"
    }
  }

  let schema: String?
  let generatedAt: String?
  let controlPlane: String?
  let server: Server?
  let verdict: Verdict?
  let actions: Actions?

  enum CodingKeys: String, CodingKey {
    case schema
    case generatedAt = "generated_at"
    case controlPlane = "control_plane"
    case server
    case verdict
    case actions
  }
}

struct ServerLogLocations: Decodable {
  let directory: URL
  let stdout: URL
  let stderr: URL

  enum CodingKeys: String, CodingKey {
    case directory
    case stdout
    case stderr
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)
    func absoluteURL(_ key: CodingKeys) throws -> URL {
      let path = try container.decode(String.self, forKey: key)
      guard path.hasPrefix("/") else {
        throw DecodingError.dataCorruptedError(
          forKey: key, in: container,
          debugDescription: "server service returned a non-absolute log path")
      }
      return URL(fileURLWithPath: path)
    }
    directory = try absoluteURL(.directory)
    stdout = try absoluteURL(.stdout)
    stderr = try absoluteURL(.stderr)
  }
}

func serverActionArguments(for action: ServerLifecycleAction) -> [String] {
  ["server", "service", action.rawValue]
}

/// The one status subprocess the tray runs: build the caretaker envelope,
/// publish it for every other reader, and print it. Polling this verb keeps
/// `GET /api/control/caretaker` fresh for the whole host.
func serverCaretakerArguments() -> [String] {
  ["server", "caretaker", "--json"]
}

func decodeCaretakerEnvelope(data: Data?) -> CaretakerEnvelope? {
  guard let data, !data.isEmpty else { return nil }
  return try? JSONDecoder().decode(CaretakerEnvelope.self, from: data)
}

func decodeServerLogs(data: Data) -> ServerLogLocations? {
  try? JSONDecoder().decode(ServerLogLocations.self, from: data)
}

private func conciseCaretakerLine(_ value: String?) -> String? {
  guard let line = value?.split(whereSeparator: \.isNewline).first else { return nil }
  let plain = String(line)
    .replacingOccurrences(of: "\u{001B}[31m", with: "")
    .replacingOccurrences(of: "\u{001B}[0m", with: "")
    .trimmingCharacters(in: .whitespacesAndNewlines)
  guard !plain.isEmpty else { return nil }
  return plain.count > 96 ? "\(plain.prefix(93))…" : plain
}

/// Map the derived verdict onto tray tone. `serverState` — not header string
/// matching — carries the stopped-versus-down distinction: an intentional stop
/// is neutral, a silent endpoint needs attention.
func trayHealth(for verdict: CaretakerEnvelope.Verdict) -> TrayServerHealth {
  switch verdict.health {
  case "healthy":
    return .healthy
  case "degraded":
    return .transitioning
  case "unknown":
    return .checking
  case "unavailable":
    return verdict.serverState == "stopped" ? .neutral : .failed
  default:
    return .failed
  }
}

func deriveServerMenuState(
  caretakerData: Data?,
  actionInFlight: ServerLifecycleAction?,
  runtimeReady: Bool
) -> ServerMenuState {
  if !runtimeReady {
    return ServerMenuState(
      header: "VC Server: WAITING FOR RUNTIME",
      detail: "Runtime onboarding has not completed",
      health: .checking,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  if let actionInFlight {
    let transition: String
    switch actionInFlight {
    case .start: transition = "STARTING"
    case .stop: transition = "STOPPING"
    case .restart: transition = "RESTARTING"
    }
    return ServerMenuState(
      header: "VC Server: \(transition)…",
      detail: "Waiting for the installed service owner",
      health: .transitioning,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  guard let envelope = decodeCaretakerEnvelope(data: caretakerData) else {
    return ServerMenuState(
      header: "VC Server: CARETAKER UNAVAILABLE",
      detail: "The canonical caretaker did not answer — the runtime may be missing or broken",
      health: .failed,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  guard let verdict = envelope.verdict else {
    return ServerMenuState(
      header: "VC Server: UNKNOWN",
      detail: "The caretaker envelope carries no verdict",
      health: .checking,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  return ServerMenuState(
    header: verdict.header,
    detail: verdict.detail,
    health: trayHealth(for: verdict),
    canStart: envelope.actions?.start?.enabled ?? false,
    canStop: envelope.actions?.stop?.enabled ?? false,
    canRestart: envelope.actions?.restart?.enabled ?? false)
}

/// The diagnostics alert renders the same envelope the menu did — verdict,
/// server leg, findings — never a second read of raw receipt fields.
func caretakerDiagnosticsLines(data: Data?) -> [String] {
  guard let envelope = decodeCaretakerEnvelope(data: data) else {
    return ["The caretaker has not published a reading for the installed runtime."]
  }
  var lines: [String] = []
  if let verdict = envelope.verdict {
    lines.append(verdict.header)
    if !verdict.detail.isEmpty {
      lines.append(verdict.detail)
    }
  }
  if let server = envelope.server {
    lines.append("State: \((server.state ?? "unknown").uppercased())")
    lines.append("Supervisor PID: \(server.supervisorPID.map(String.init) ?? "—")")
    lines.append("Server PID: \(server.managedPair?.serverPID.map(String.init) ?? "—")")
    lines.append("Guardian PID: \(server.managedPair?.guardianPID.map(String.init) ?? "—")")
    if let endpoint = server.endpoint, let host = endpoint.host, let port = endpoint.port {
      lines.append("Endpoint: \(host):\(port)")
    }
    if let reason = conciseCaretakerLine(server.lastError) {
      lines.append("Last error: \(reason)")
    }
    if let path = server.receipt?.path, !path.isEmpty {
      lines.append("Status receipt: \(path)")
    }
  }
  for finding in envelope.verdict?.findings ?? [] {
    lines.append("[\(finding.severity)] \(finding.code): \(finding.detail)")
  }
  return lines
}
