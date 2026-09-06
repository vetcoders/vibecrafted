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

struct ServerNavigationState {
  let server: URL?
  let workspaces: URL?
  let unavailableReason: String?

  var isAvailable: Bool {
    server != nil && workspaces != nil
  }
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

private func validatedServerOrigin(_ value: String) -> URL? {
  guard var components = URLComponents(string: value),
    let scheme = components.scheme?.lowercased(),
    ["http", "https"].contains(scheme),
    let host = components.host,
    !host.isEmpty,
    components.user == nil,
    components.password == nil,
    components.path.isEmpty || components.path == "/",
    components.query == nil,
    components.fragment == nil,
    components.port.map({ (1...65_535).contains($0) }) ?? true
  else {
    return nil
  }
  components.scheme = scheme
  components.path = ""
  return components.url
}

private func serverPageURL(origin: URL, path: String) -> URL? {
  guard path.hasPrefix("/"), !path.contains("?"), !path.contains("#"),
    var components = URLComponents(url: origin, resolvingAgainstBaseURL: false)
  else {
    return nil
  }
  components.path = path
  return components.url
}

/// The caretaker's `open_console` action is the sole URL authority for the
/// tray. It already combines configured server identity with liveness. Swift
/// only validates that origin and derives known routes from it.
func resolveServerNavigation(caretakerData: Data?) -> ServerNavigationState {
  guard let envelope = decodeCaretakerEnvelope(data: caretakerData) else {
    return ServerNavigationState(
      server: nil, workspaces: nil,
      unavailableReason: "The canonical caretaker has not published a server address.")
  }
  guard let action = envelope.actions?.openConsole else {
    return ServerNavigationState(
      server: nil, workspaces: nil,
      unavailableReason: "The caretaker did not provide a server navigation action.")
  }
  guard action.enabled else {
    return ServerNavigationState(
      server: nil, workspaces: nil,
      unavailableReason: conciseCaretakerLine(action.reason)
        ?? "The configured VC Server is unavailable.")
  }
  guard let value = action.url, let origin = validatedServerOrigin(value),
    let workspaces = serverPageURL(origin: origin, path: "/workspaces")
  else {
    return ServerNavigationState(
      server: nil, workspaces: nil,
      unavailableReason: "The caretaker returned a malformed server URL.")
  }
  return ServerNavigationState(
    server: origin, workspaces: workspaces, unavailableReason: nil)
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

// MARK: - Installed runtime resolution

// Opening the App must render the runtime the Founder actually installed, not
// the carrier this bundle happens to ship. The installer publishes two
// versioned artifacts inside one transaction: `vibecrafted.active-runtime.v1`
// names the active generation, `vibecrafted.runtime-install.v1` records the
// roots that installation owns. This file decodes both and derives the launch
// contract from them. It is a reader: it never writes either document, never
// materializes a path, and never becomes a second installer. `tools/
// vibecrafted-current` stays a compatibility projection — the pointer of
// record is `active.json`.

/// The `vibecrafted.active-runtime.v1` pointer as the App consumes it.
struct ActiveRuntimeDocument: Decodable {
  let schema: String?
  let version: String?
  let runtimeRoot: String?
  let appRoot: String?

  enum CodingKeys: String, CodingKey {
    case schema
    case version
    case runtimeRoot = "runtime_root"
    case appRoot = "app_root"
  }
}

/// The `vibecrafted.runtime-install.v1` receipt, narrowed to the roots the
/// launch contract is derived from. Unknown keys are ignored on purpose: a
/// newer installer may record more ownership without invalidating this read.
struct RuntimeInstallReceiptDocument: Decodable {
  struct Roots: Decodable {
    let runtimeHome: String?
    let configHome: String?
    let productConfig: String?
    let craftedHome: String?
    let launcherHome: String?

    enum CodingKeys: String, CodingKey {
      case runtimeHome = "runtime_home"
      case configHome = "config_home"
      case productConfig = "product_config"
      case craftedHome = "crafted_home"
      case launcherHome = "launcher_home"
    }
  }

  let schema: String?
  let version: String?
  let roots: Roots?
}

/// Absolute launch entries of one installed generation. Mirrors the installer's
/// `vibecrafted.runtime-install-result.v1` shape for the fields the App needs,
/// minus the terminal host: the canvas helper is bundle-owned, so the App
/// supplies it rather than reading it back out of the installation.
struct InstalledRuntimeLayout {
  let generation: URL
  let version: String
  let launcher: URL
  let terminal: URL
  let frame: URL
  let start: URL
  let primaryShell: URL
  let terminalConfig: URL
  let frameConfig: URL
  let runtimeHome: URL
  let configHome: URL
  let craftedHome: URL
}

/// What a normal open is allowed to do with the current installation.
///
/// The distinction is the whole point of this type. `absent` means nothing is
/// installed yet, so publishing the bundled carrier is onboarding. `unusable`
/// means an installation exists but disagrees with itself — replacing it from
/// the bundled carrier would silently take the Founder's runtime backwards, so
/// repair stays an explicit action instead.
enum InstalledRuntimeDisposition {
  case ready(InstalledRuntimeLayout)
  case absent(String)
  case unusable(String)
}

let activeRuntimeSchema = "vibecrafted.active-runtime.v1"
let runtimeInstallReceiptSchema = "vibecrafted.runtime-install.v1"

/// Location of the active-generation pointer inside a runtime home.
func activeRuntimeDocumentURL(runtimeHome: URL) -> URL {
  runtimeHome.appendingPathComponent("active.json")
}

/// Location of the ownership receipt inside a runtime home.
func runtimeInstallReceiptURL(runtimeHome: URL) -> URL {
  runtimeHome.appendingPathComponent("install-receipt.json")
}

/// The runtime home a normal open reads, resolved by the same precedence the
/// installer uses: explicit override, then XDG data home, then its default.
func resolvedRuntimeHome(environment: [String: String], homeDirectory: String) -> URL {
  if let explicit = environment["VIBECRAFTED_RUNTIME_HOME"], explicit.hasPrefix("/") {
    return URL(fileURLWithPath: explicit, isDirectory: true)
  }
  if let dataHome = environment["XDG_DATA_HOME"], dataHome.hasPrefix("/") {
    return URL(fileURLWithPath: dataHome, isDirectory: true)
      .appendingPathComponent("vibecrafted", isDirectory: true)
  }
  return URL(fileURLWithPath: homeDirectory, isDirectory: true)
    .appendingPathComponent(".local/share/vibecrafted", isDirectory: true)
}

private func absoluteDirectory(_ value: String?) -> URL? {
  guard let value, value.hasPrefix("/") else { return nil }
  return URL(fileURLWithPath: value, isDirectory: true)
}

/// True when `candidate` is the releases directory of `runtimeHome` or below
/// it. A generation pointer that escapes its own runtime home is refused
/// rather than launched.
func generationBelongsToRuntimeHome(generation: URL, runtimeHome: URL) -> Bool {
  let releases =
    runtimeHome
    .standardizedFileURL
    .appendingPathComponent("releases", isDirectory: true)
    .path
  let candidate = generation.standardizedFileURL.path
  return candidate.hasPrefix(releases + "/")
}

/// Derive the launch contract of the currently installed generation, or say
/// precisely why it cannot be derived. Pure: all filesystem reads happen in the
/// caller, existence of the launch entries is checked there too.
func installedRuntimeDisposition(
  activeData: Data?,
  receiptData: Data?,
  runtimeHome: URL
) -> InstalledRuntimeDisposition {
  guard let activeData, !activeData.isEmpty else {
    return .absent("no active runtime pointer under \(runtimeHome.path)")
  }
  guard let receiptData, !receiptData.isEmpty else {
    return .absent("no runtime install receipt under \(runtimeHome.path)")
  }
  guard
    let active = try? JSONDecoder().decode(ActiveRuntimeDocument.self, from: activeData)
  else {
    return .unusable("the active runtime pointer is not readable JSON")
  }
  guard
    let receipt = try? JSONDecoder().decode(
      RuntimeInstallReceiptDocument.self, from: receiptData)
  else {
    return .unusable("the runtime install receipt is not readable JSON")
  }
  guard active.schema == activeRuntimeSchema else {
    return .unusable(
      "the active runtime pointer carries schema \(active.schema ?? "none")")
  }
  guard receipt.schema == runtimeInstallReceiptSchema else {
    return .unusable(
      "the runtime install receipt carries schema \(receipt.schema ?? "none")")
  }
  guard let version = active.version, !version.isEmpty else {
    return .unusable("the active runtime pointer names no version")
  }
  // Both documents are written in one installer transaction. Disagreement means
  // a half-applied or hand-edited installation, never something to launch.
  guard receipt.version == version else {
    return .unusable(
      "active generation \(version) disagrees with the install receipt "
        + "(\(receipt.version ?? "none"))")
  }
  guard let generation = absoluteDirectory(active.runtimeRoot) else {
    return .unusable("the active runtime pointer names no absolute generation root")
  }
  guard let roots = receipt.roots,
    let receiptRuntimeHome = absoluteDirectory(roots.runtimeHome),
    let configHome = absoluteDirectory(roots.configHome),
    let productConfig = absoluteDirectory(roots.productConfig),
    let craftedHome = absoluteDirectory(roots.craftedHome),
    let launcherHome = absoluteDirectory(roots.launcherHome)
  else {
    return .unusable("the runtime install receipt records no absolute roots")
  }
  guard
    receiptRuntimeHome.standardizedFileURL.path == runtimeHome.standardizedFileURL.path
  else {
    return .unusable(
      "the runtime install receipt belongs to \(receiptRuntimeHome.path), not "
        + runtimeHome.path)
  }
  guard generationBelongsToRuntimeHome(generation: generation, runtimeHome: runtimeHome)
  else {
    return .unusable("the active generation escapes \(runtimeHome.path)/releases")
  }
  return .ready(
    InstalledRuntimeLayout(
      generation: generation,
      version: version,
      launcher: launcherHome.appendingPathComponent("vibecrafted"),
      terminal: generation.appendingPathComponent("bin/vc-terminal"),
      // The native provider, never the wrapper: pointing this back at the
      // wrapper makes the first `vc-frame ls` exec itself forever.
      frame: generation.appendingPathComponent("libexec/vc-frame"),
      start: generation.appendingPathComponent("bin/vc-start"),
      primaryShell: productConfig.appendingPathComponent(
        "vc-terminal/launch-primary-shell.zsh"),
      terminalConfig: productConfig.appendingPathComponent("vc-terminal/vc-terminal.toml"),
      frameConfig: productConfig.appendingPathComponent("vc-frame", isDirectory: true),
      runtimeHome: receiptRuntimeHome,
      configHome: configHome,
      craftedHome: craftedHome))
}
