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

// Which runtime is installed, where it lives, and what may be launched out of
// it is owned by the Runtime Pack installer — not by this App. The owner
// answers one read-only question, `runtime-resolve --runtime-home <abs> --json`,
// and returns `vibecrafted.runtime-resolution.v1`. This file decodes that
// envelope and stops there: no receipt fields, no generation validation, no
// preference rules, no launch-path derivation. Every one of those used to live
// here in Swift, which made the App a second implementation of what counts as
// an installable runtime. The only thing left is the bootstrap below, which
// exists purely because the App has to find the owner before it can ask it
// anything.

let runtimeResolutionSchema = "vibecrafted.runtime-resolution.v1"

/// The owner's answer, as the App is allowed to understand it.
///
/// Generic over the launch contract on purpose. This file is Foundation-only
/// and is compiled on its own by the tray's server-menu contract test, so it
/// must not name the AppKit-side type that carries the contract — while still
/// refusing to grow a second copy of that shape here.
enum RuntimeResolution<Runtime> {
  /// A usable installation. The payload is the owner's own
  /// `vibecrafted.runtime-install-result.v1` launch contract, verbatim.
  case ready(Runtime)
  /// Nothing is installed, so publishing the bundled carrier is onboarding.
  case absent(String)
  /// Something is installed but the owner refuses it, or could not be asked.
  /// Never a licence to overwrite: that would walk the Founder's runtime
  /// backwards from an older carrier.
  case unusable(String)
}

/// `vibecrafted.runtime-resolution.v1`. Unknown keys are ignored on purpose —
/// a newer owner may report more without invalidating this read.
struct RuntimeResolutionEnvelope<Runtime: Decodable>: Decodable {
  let schema: String?
  let status: String?
  let reason: String?
  let runtime: Runtime?
}

/// The whole invocation of the read-only owner API.
///
/// `-B` is an interpreter flag and therefore precedes the script: asking what
/// is installed must not write bytecode into the installation being inspected.
func runtimeResolveArguments(installer: URL, runtimeHome: URL) -> [String] {
  [
    "-B", installer.path, "runtime-resolve",
    "--runtime-home", runtimeHome.path, "--json",
  ]
}

/// Turn one owner invocation into a verdict.
///
/// Exit 0 carries `ready` or `absent`; exit 2 carries `unusable` and is still
/// decoded, because that envelope is where the reason lives. Everything else —
/// another status, a signal, the watchdog, unparseable bytes, or a schema this
/// App does not know — is `unusable`. A resolver that cannot be trusted to
/// describe the installation must never be read as "nothing is installed",
/// because that reading is what turns a bad read into an automatic overwrite.
func decodeRuntimeResolution<Runtime: Decodable>(
  stdout: Data,
  stderr: Data,
  terminationStatus: Int32,
  clean: Bool
) -> RuntimeResolution<Runtime> {
  let diagnostic = boundedResolverDiagnostic(stdout: stdout, stderr: stderr)
  guard clean else {
    return .unusable("the runtime resolver did not exit cleanly\(diagnostic)")
  }
  guard terminationStatus == 0 || terminationStatus == 2 else {
    return .unusable("the runtime resolver exited \(terminationStatus)\(diagnostic)")
  }
  guard
    let envelope = try? JSONDecoder().decode(
      RuntimeResolutionEnvelope<Runtime>.self, from: stdout)
  else {
    return .unusable("the runtime resolver returned no readable resolution\(diagnostic)")
  }
  guard envelope.schema == runtimeResolutionSchema else {
    return .unusable("the runtime resolver answered with schema \(envelope.schema ?? "none")")
  }
  let reason = envelope.reason.flatMap { $0.isEmpty ? nil : $0 }
  switch envelope.status {
  case "ready":
    guard terminationStatus == 0 else {
      return .unusable("the runtime resolver reported ready but exited \(terminationStatus)")
    }
    guard let runtime = envelope.runtime else {
      return .unusable("the runtime resolver reported ready without a launch contract")
    }
    return .ready(runtime)
  case "absent":
    guard terminationStatus == 0 else {
      return .unusable("the runtime resolver reported absent but exited \(terminationStatus)")
    }
    return .absent(reason ?? "no Vibecrafted runtime is installed yet")
  case "unusable":
    return .unusable(reason ?? "the installed runtime cannot be used")
  default:
    return .unusable("the runtime resolver answered with status \(envelope.status ?? "none")")
  }
}

/// One short single-line excerpt of what the resolver said, for the tray and
/// the log. Bounded so a chatty failure cannot become the whole menu.
func boundedResolverDiagnostic(stdout: Data, stderr: Data, limit: Int = 240) -> String {
  let source = stderr.isEmpty ? stdout : stderr
  guard let text = String(data: source, encoding: .utf8) else { return "" }
  let collapsed =
    text
    .split(whereSeparator: { $0.isNewline })
    .map { $0.trimmingCharacters(in: .whitespaces) }
    .filter { !$0.isEmpty }
    .joined(separator: " · ")
  guard !collapsed.isEmpty else { return "" }
  return ": " + (collapsed.count > limit ? String(collapsed.prefix(limit)) + "…" : collapsed)
}

// MARK: - Owner bootstrap

// The one thing the App must work out for itself: where the owner is. This is
// deliberately the smallest read that can find it — is anything installed at
// all, and which generation carries the resolver — and it stops there.

/// What the App may do before the owner has spoken.
enum RuntimeResolverBootstrap {
  /// The owner can be asked about this runtime home.
  case ask(python: URL, installer: URL)
  /// Both identity documents are positively absent: first onboarding.
  case absent(String)
  /// An installation is present but this App cannot even ask about it.
  case unusable(String)
}

/// Filesystem probes the bootstrap needs, injected so this policy holds no
/// `FileManager` of its own and can be exercised without a real installation.
struct RuntimeIdentityProbe {
  /// Positive presence: an existing but unreadable file is *present*.
  let exists: (URL) -> Bool
  /// File bytes, or nil when they cannot be read.
  let read: (URL) -> Data?
  /// Links resolved, so containment cannot be defeated by a symlink.
  let realPath: (URL) -> URL
  /// Executable regular file.
  let isExecutable: (URL) -> Bool
}

/// The `vibecrafted.active-runtime.v1` pointer, narrowed to the one field the
/// bootstrap needs. Everything else in that document is the owner's to read.
struct ActiveRuntimePointer: Decodable {
  let runtimeRoot: String?

  enum CodingKeys: String, CodingKey {
    case runtimeRoot = "runtime_root"
  }
}

/// Location of the active-generation pointer inside a runtime home.
func activeRuntimePointerURL(runtimeHome: URL) -> URL {
  runtimeHome.appendingPathComponent("active.json")
}

/// Location of the ownership receipt inside a runtime home. Its contents are
/// the owner's; the App only ever asks whether it is there.
func runtimeInstallReceiptURL(runtimeHome: URL) -> URL {
  runtimeHome.appendingPathComponent("install-receipt.json")
}

/// Where to look for an installation: explicit override, then XDG data home,
/// then the default.
///
/// This is the same precedence the installer applies, which is exactly why the
/// resolved home is handed back to the owner as `--runtime-home`: the owner
/// refuses when it disagrees with the receipt, so a divergence (a symlinked or
/// overridden `HOME`, say) surfaces as a visible refusal instead of silently
/// addressing the wrong runtime.
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

/// True when `candidate` lives strictly beneath `directory`. Both sides are
/// expected to be link-resolved already.
func pathIsBeneath(_ candidate: URL, _ directory: URL) -> Bool {
  let root = directory.standardizedFileURL.path
  let prefix = root.hasSuffix("/") ? root : root + "/"
  return candidate.standardizedFileURL.path.hasPrefix(prefix)
}

/// Decide whether the owner can be asked, and refuse honestly when it cannot.
func runtimeResolverBootstrap(
  runtimeHome: URL,
  probe: RuntimeIdentityProbe
) -> RuntimeResolverBootstrap {
  let pointer = activeRuntimePointerURL(runtimeHome: runtimeHome)
  let receipt = runtimeInstallReceiptURL(runtimeHome: runtimeHome)
  let pointerPresent = probe.exists(pointer)
  let receiptPresent = probe.exists(receipt)

  // Absence is only absence when both identity documents are positively
  // missing. A half-published pair, or one this App cannot read, is an
  // installation — and an installation is never something to overwrite from
  // the bundled carrier without being asked.
  guard pointerPresent || receiptPresent else {
    return .absent("no Vibecrafted runtime is installed under \(runtimeHome.path)")
  }
  guard pointerPresent else {
    return .unusable("\(receipt.path) exists with no active runtime pointer beside it")
  }
  guard receiptPresent else {
    return .unusable("\(pointer.path) exists with no install receipt beside it")
  }
  guard let pointerData = probe.read(pointer), !pointerData.isEmpty else {
    return .unusable("\(pointer.path) cannot be read")
  }
  guard
    let document = try? JSONDecoder().decode(ActiveRuntimePointer.self, from: pointerData),
    let root = document.runtimeRoot, root.hasPrefix("/")
  else {
    return .unusable("\(pointer.path) names no absolute generation root")
  }

  // Resolve links on both sides before comparing, and therefore before
  // executing anything found through the result: a pointer that leaves this
  // home's releases directory is an escape, not a generation.
  let generation = probe.realPath(URL(fileURLWithPath: root, isDirectory: true))
  let releases = probe.realPath(runtimeHome)
    .appendingPathComponent("releases", isDirectory: true)
  guard pathIsBeneath(generation, releases) else {
    return .unusable("the active generation escapes \(releases.path)")
  }

  let python = generation.appendingPathComponent("bin/python3")
  guard probe.isExecutable(python) else {
    return .unusable("\(generation.lastPathComponent) carries no executable bin/python3")
  }
  let installer = generation.appendingPathComponent("scripts/vetcoders_install.py")
  guard probe.exists(installer) else {
    // A generation that predates the read-only resolver cannot be asked. The
    // answer is an explicit upgrade or repair — never a resolver copied back
    // into Swift, which is the duplication this whole cut removes.
    return .unusable(
      "\(generation.lastPathComponent) does not carry the runtime resolver "
        + "(scripts/vetcoders_install.py); repair or upgrade the runtime explicitly")
  }
  return .ask(python: python, installer: installer)
}

// MARK: - Identity freshness

/// How many times a resolve may be restarted because the installation moved
/// under it before the App stops trying and refuses instead.
let runtimeResolveDriftLimit = 2

/// Cheap identity of the two documents the installer publishes together.
///
/// A generation change is published by rewriting them, so their stamps are both
/// the refresh signal and the validity window of an answer: the App re-asks the
/// owner when this changes, reuses the last answer when it has not, and —
/// because the owner is asked in a subprocess — reads the pair again before
/// believing what that subprocess said.
struct RuntimeIdentityFingerprint: Equatable {
  let home: String
  let pointer: FileStamp?
  let receipt: FileStamp?
}

/// Size and modification time of one identity document, or nil when there is
/// nothing there to stamp.
struct FileStamp: Equatable {
  let size: Int
  let modified: Date
}

/// What may be done with an answer that has just come back from the owner.
enum RuntimeResolveDelivery: Equatable {
  /// The installation did not move: the answer describes what is installed.
  case deliver
  /// It moved. The answer describes the preceding generation, so it is neither
  /// cached nor delivered — the identity that is there now is asked about
  /// instead, on behalf of everyone still waiting.
  case reresolve
  /// It kept moving under every attempt. Refuse with this reason rather than
  /// arm the tray with whichever generation happened to answer last.
  case refuse(String)
}

/// Decide whether an owner answer may be delivered.
///
/// The owner is asked in a subprocess, so a publication can land while that
/// child runs — and a caller that joins mid-flight may already be looking at
/// the newer pointer. Comparing the identity read at invocation with the one
/// read at completion is what keeps an answer, and every action taken from it,
/// bound to the generation it actually describes.
func runtimeResolveDelivery(
  invoked: RuntimeIdentityFingerprint,
  observed: RuntimeIdentityFingerprint,
  attempt: Int,
  limit: Int = runtimeResolveDriftLimit
) -> RuntimeResolveDelivery {
  guard observed != invoked else { return .deliver }
  guard attempt < limit else {
    return .refuse(
      "the installed runtime changed while it was being resolved "
        + "(\(limit + 1) attempts); the next status poll reads it again")
  }
  return .reresolve
}
