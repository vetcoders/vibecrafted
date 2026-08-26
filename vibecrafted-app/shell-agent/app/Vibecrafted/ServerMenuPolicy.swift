import Foundation

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

struct ServerSupervisorSnapshot: Decodable {
  struct Endpoint: Decodable {
    let host: String
    let port: Int
  }

  struct ManagedPair: Decodable {
    let guardianPID: Int?
    let serverPID: Int?

    enum CodingKeys: String, CodingKey {
      case guardianPID = "guardian_pid"
      case serverPID = "server_pid"
    }
  }

  let state: String
  let lastError: String?
  let supervisorPID: Int?
  let managedPair: ManagedPair?
  let endpoint: Endpoint?

  enum CodingKeys: String, CodingKey {
    case state
    case lastError = "last_error"
    case supervisorPID = "supervisor_pid"
    case managedPair = "managed_pair"
    case endpoint
  }
}

private struct ServerServiceSnapshot: Decodable {
  let installed: Bool
  let loaded: Bool
  let supervisorLive: Bool
  let supervisorVerified: Bool
  let supervisorServiceManaged: Bool
  let buildCurrent: Bool
  let pairHealthy: Bool
  let supervisorPID: Int?

  enum CodingKeys: String, CodingKey {
    case installed
    case loaded
    case supervisorLive = "supervisor_live"
    case supervisorVerified = "supervisor_verified"
    case supervisorServiceManaged = "supervisor_service_managed"
    case buildCurrent = "build_current"
    case pairHealthy = "pair_healthy"
    case supervisorPID = "supervisor_pid"
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

func decodeServerLogs(data: Data) -> ServerLogLocations? {
  try? JSONDecoder().decode(ServerLogLocations.self, from: data)
}

private func conciseServerFailure(_ value: String?) -> String? {
  guard let line = value?.split(whereSeparator: \.isNewline).first else { return nil }
  let plain = String(line)
    .replacingOccurrences(of: "\u{001B}[31m", with: "")
    .replacingOccurrences(of: "\u{001B}[0m", with: "")
    .trimmingCharacters(in: .whitespacesAndNewlines)
  guard !plain.isEmpty else { return nil }
  return plain.count > 96 ? "\(plain.prefix(93))…" : plain
}

private func endpointSuffix(_ snapshot: ServerSupervisorSnapshot?) -> String {
  guard let endpoint = snapshot?.endpoint else { return "" }
  return " · \(endpoint.host):\(endpoint.port)"
}

func deriveServerMenuState(
  supervisorData: Data?,
  serviceData: Data?,
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

  let decoder = JSONDecoder()
  let supervisor = supervisorData.flatMap {
    try? decoder.decode(ServerSupervisorSnapshot.self, from: $0)
  }
  guard let service = serviceData.flatMap({
    try? decoder.decode(ServerServiceSnapshot.self, from: $0)
  }) else {
    return ServerMenuState(
      header: "VC Server: UNAVAILABLE\(endpointSuffix(supervisor))",
      detail: conciseServerFailure(supervisor?.lastError) ?? "Canonical service status is unavailable",
      health: .failed,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  guard service.installed else {
    return ServerMenuState(
      header: "VC Server: NOT INSTALLED\(endpointSuffix(supervisor))",
      detail: "Install the canonical VC Server service first",
      health: .failed,
      canStart: false,
      canStop: false,
      canRestart: false)
  }

  if !service.loaded {
    return ServerMenuState(
      header: "VC Server: STOPPED\(endpointSuffix(supervisor))",
      detail: "Service is intentionally stopped",
      health: .neutral,
      canStart: true,
      canStop: false,
      canRestart: false)
  }

  let healthy = service.supervisorLive && service.supervisorVerified
    && service.supervisorServiceManaged && service.buildCurrent && service.pairHealthy
  if healthy {
    return ServerMenuState(
      header: "VC Server: HEALTHY\(endpointSuffix(supervisor))",
      detail: "Supervisor PID \(service.supervisorPID.map(String.init) ?? "—")",
      health: .healthy,
      canStart: false,
      canStop: true,
      canRestart: true)
  }

  return ServerMenuState(
    header: "VC Server: NEEDS ATTENTION\(endpointSuffix(supervisor))",
    detail: conciseServerFailure(supervisor?.lastError) ?? "Installed service is not healthy",
    health: .failed,
    canStart: false,
    canStop: true,
    canRestart: true)
}
