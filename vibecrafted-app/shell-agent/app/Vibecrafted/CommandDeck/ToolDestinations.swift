import Foundation

// Foundation-only, like WebNavigationPolicy: the tray/deck harness compiles
// this file through `swiftc` without AppKit or WebKit, so every decision here
// is a pure function over values the App already holds.

/// What one native tab is allowed to show. A tab's scope is fixed at creation
/// and a page can never widen it.
enum WebTabScope: Equatable, Sendable {
  /// Routes on the caretaker-resolved runtime origin.
  case runtime(WebRuntimeOrigin)
  /// Exactly one local HTML file, with read access to that file alone.
  case localDocument(URL)
  /// A served tool console on its own http(s) origin, named by the operator in
  /// `~/.config/vibecrafted/config.toml`. Navigation is confined to that origin
  /// exactly as the console is confined to the runtime; the runtime endpoint
  /// never redirects it and it never inherits the console's data store.
  case service(WebRuntimeOrigin)
}

/// Where Home returns a tab on an http(s) origin.
///
/// Decided by the owner that opens the tab, from what the tab is *for*, never
/// from the URL that happened to open it. A page reached through
/// `target=_blank` or a diverted API endpoint is where a tab starts; it is not
/// a place the user asked to come back to, so Home from raw JSON or an error
/// page returns to the product overview instead of reloading the same bytes.
enum WebTabHome: Equatable, Sendable {
  /// The connected runtime's product overview (`/`). The console lives here,
  /// and so does every tool tab opened from a link inside the product.
  case runtimeOverview
  /// A surface's own overview, owned by its `ToolDestination` (the Loctree
  /// report returns to the report). A read-only reference view also keeps its
  /// one document here as the route a reconnect re-presents; its Home button,
  /// though, is answered by `NativeTabCoordinator.openProductOverview()`,
  /// because a script-less view cannot render the product overview itself.
  case route(String)

  /// The route Home selects. Only ever a path on the tab's own origin.
  var path: String {
    switch self {
    case .runtimeOverview: return "/"
    case .route(let path): return path
    }
  }
}

/// A registered tool surface the App can open in a native tab.
///
/// Only surfaces with a real, resolvable contract are registered. Nothing here
/// invents a port, spawns a service, or mocks a dashboard: a destination either
/// resolves against truth the App already owns (the runtime endpoint, a file
/// the owner tool wrote) or it reports exactly why it is unavailable.
struct ToolDestination: Identifiable, Equatable, Sendable {
  enum Target: Equatable, Sendable {
    /// A path on the current runtime origin. The origin is never hardcoded.
    case runtimeRoute(String)
    /// A file the owning tool generates. `homeRelativePath` is resolved under
    /// the directory named by `homeEnvironmentKey` when set, else `~`.
    case localDocument(homeRelativePath: String, homeEnvironmentKey: String?)
    /// A served console owned outside this repository. Its URL comes from the
    /// `[tools.<key>]` table of the operator's `config.toml` (the same file
    /// that owns `[server]`), never from a guessed host or port. `owner`
    /// names who serves it, so an unset key reads as a real boundary.
    case configuredService(key: String, owner: String)
    /// No launch contract exists. The reason is shown, never hidden.
    case unavailable(reason: String)
  }

  let id: String
  let title: String
  let symbol: String
  let role: WebTabRole
  let target: Target
  /// Generated or untrusted content: the tab gets a non-persistent website
  /// data store, so nothing it sets outlives the tab and nothing the console
  /// holds (cookies, storage) is visible to it.
  let isolatedContent: Bool

  init(id: String, title: String, symbol: String, role: WebTabRole, target: Target, isolatedContent: Bool = false) {
    self.id = id
    self.title = title
    self.symbol = symbol
    self.role = role
    self.target = target
    self.isolatedContent = isolatedContent
  }

  /// Every destination the App knows about, in menu order.
  static let catalog: [ToolDestination] = [
    // The server answers this route with a Content-Security-Policy `sandbox`,
    // so the report runs with an opaque origin and no control-plane authority;
    // the App adds an ephemeral data store on top.
    ToolDestination(
      id: "loctree-report", title: "Loctree Report", symbol: "doc.text.magnifyingglass",
      role: .tool, target: .runtimeRoute("/structure/report"), isolatedContent: true),
    ToolDestination(
      id: "aicx-dashboard", title: "AICX Dashboard", symbol: "clock.arrow.circlepath",
      role: .tool, target: .localDocument(homeRelativePath: ".aicx/aicx-dashboard.html",
        homeEnvironmentKey: "AICX_HOME")),
    ToolDestination(
      id: "slack-agent-console", title: "Slack Agent Console", symbol: "bubble.left.and.bubble.right",
      role: .tool, target: .configuredService(key: "slack-console",
        owner: "vc-slack-agent serves it as the `/console` route of its portal "
          + "(`make portal-preview` on :4300 for LAN/tailnet, or its deploy); Vibecrafted does not host it")),
  ]

  static func named(_ id: String) -> ToolDestination? {
    catalog.first { $0.id == id }
  }
}

/// The `[tools]` table of `~/.config/vibecrafted/config.toml`, read with the
/// same contract `vibecrafted_core.server_config.load_tool_destinations`
/// applies: one `url` per known key, http(s), a host, no credentials, no
/// query, no fragment. Anything else is reported, never guessed around.
/// Why the `[tools]` table cannot be used. Carried as text so the menu can
/// show the exact contract violation.
struct ToolConfigurationError: Error, Equatable, Sendable {
  let message: String
  init(_ message: String) { self.message = message }
}

enum ToolDestinationConfiguration {
  /// Keys the App knows how to open. Mirrors `TOOL_DESTINATION_KEYS`.
  static let knownKeys: Set<String> = ["slack-console"]

  static func configurationFile(homeDirectory: URL, environment: [String: String]) -> URL {
    let base: URL
    if let xdg = environment["XDG_CONFIG_HOME"], xdg.hasPrefix("/") {
      base = URL(fileURLWithPath: xdg, isDirectory: true)
    } else {
      base = homeDirectory.appendingPathComponent(".config", isDirectory: true)
    }
    return base.appendingPathComponent("vibecrafted/config.toml")
  }

  /// Parses the TOML subset the table uses: `[tools.<key>]` headers and
  /// `url = "..."` basic strings. Other tables are skipped untouched.
  static func parse(_ text: String) -> Result<[String: URL], ToolConfigurationError> {
    var destinations: [String: URL] = [:]
    var currentKey: String?
    var insideTools = false
    for rawLine in text.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline) {
      let line = stripComment(String(rawLine)).trimmingCharacters(in: .whitespaces)
      if line.isEmpty { continue }
      if line.hasPrefix("[") {
        currentKey = nil
        insideTools = false
        guard line.hasSuffix("]") else { return .failure(ToolConfigurationError("malformed table header `\(line)`")) }
        var header = String(line.dropFirst().dropLast()).trimmingCharacters(in: .whitespaces)
        if header.hasPrefix("[") && header.hasSuffix("]") { return .failure(ToolConfigurationError("[tools] does not accept array tables")) }
        if header == "tools" { insideTools = true; continue }
        guard header.hasPrefix("tools.") else { continue }
        header.removeFirst("tools.".count)
        let key = unquote(header.trimmingCharacters(in: .whitespaces))
        guard knownKeys.contains(key) else { return .failure(ToolConfigurationError("unsupported [tools] key: \(key)")) }
        currentKey = key
        continue
      }
      guard let equals = line.firstIndex(of: "=") else { return .failure(ToolConfigurationError("malformed line `\(line)`")) }
      let name = unquote(line[..<equals].trimmingCharacters(in: .whitespaces))
      let value = line[line.index(after: equals)...].trimmingCharacters(in: .whitespaces)
      if insideTools, currentKey == nil {
        // `[tools]` with a bare key means the table is not a table of tables.
        return .failure(ToolConfigurationError("[tools.\(name)] must be a TOML table"))
      }
      guard let key = currentKey else { continue }
      guard name == "url" else { return .failure(ToolConfigurationError("unsupported [tools.\(key)] key: \(name)")) }
      guard value.hasPrefix("\""), value.hasSuffix("\""), value.count >= 2 else {
        return .failure(ToolConfigurationError("tools.\(key).url must be a string"))
      }
      let raw = String(value.dropFirst().dropLast())
      if raw.isEmpty { continue }
      guard let url = validatedServiceURL(raw) else {
        return .failure(ToolConfigurationError("tools.\(key).url must be an HTTP(S) URL without credentials, query, or fragment"))
      }
      destinations[key] = url
    }
    return .success(destinations)
  }

  static func validatedServiceURL(_ raw: String) -> URL? {
    guard !raw.contains(where: { $0.isWhitespace || CharacterSet.controlCharacters.contains($0.unicodeScalars.first!) }),
      let components = URLComponents(string: raw),
      let scheme = components.scheme?.lowercased(), scheme == "http" || scheme == "https",
      let host = components.host, !host.isEmpty,
      components.user == nil, components.password == nil,
      components.query == nil, components.fragment == nil,
      let url = components.url, WebRuntimeOrigin(url: url) != nil
    else { return nil }
    return url
  }

  private static func stripComment(_ line: String) -> String {
    var inString = false
    var result = ""
    for character in line {
      if character == "\"" { inString.toggle() }
      if character == "#", !inString { break }
      result.append(character)
    }
    return result
  }

  private static func unquote(_ value: String) -> String {
    guard value.count >= 2, value.hasPrefix("\""), value.hasSuffix("\"") else { return value }
    return String(value.dropFirst().dropLast())
  }
}

enum ToolDestinationResolution: Equatable, Sendable {
  case available(URL, WebTabScope)
  case unavailable(reason: String)

  var url: URL? {
    if case .available(let url, _) = self { return url }
    return nil
  }

  var scope: WebTabScope? {
    if case .available(_, let scope) = self { return scope }
    return nil
  }
}

/// Inputs a resolution may consult. Injected so the choice is provable without
/// a runtime, a home directory or a filesystem.
struct ToolDestinationContext {
  var runtimeEndpoint: URL?
  var homeDirectory: URL
  var environment: [String: String]
  var fileExists: (URL) -> Bool
  /// Reads the operator's `config.toml`; `nil` when there is none. Injected so
  /// the contract is provable with a string instead of a home directory.
  var readConfiguration: (URL) -> String?

  init(
    runtimeEndpoint: URL?, homeDirectory: URL, environment: [String: String],
    fileExists: @escaping (URL) -> Bool,
    readConfiguration: @escaping (URL) -> String? = { url in
      guard FileManager.default.fileExists(atPath: url.path) else { return nil }
      return try? String(contentsOf: url, encoding: .utf8)
    }
  ) {
    self.runtimeEndpoint = runtimeEndpoint
    self.homeDirectory = homeDirectory
    self.environment = environment
    self.fileExists = fileExists
    self.readConfiguration = readConfiguration
  }

  var configurationFile: URL {
    ToolDestinationConfiguration.configurationFile(homeDirectory: homeDirectory, environment: environment)
  }

  /// The configured tool destinations, or the reason the table is unusable.
  var toolDestinations: Result<[String: URL], ToolConfigurationError> {
    guard let text = readConfiguration(configurationFile) else { return .success([:]) }
    return ToolDestinationConfiguration.parse(text)
  }
}

extension ToolDestination {
  func resolve(in context: ToolDestinationContext) -> ToolDestinationResolution {
    switch target {
    case .runtimeRoute(let path):
      guard let endpoint = context.runtimeEndpoint, let origin = WebRuntimeOrigin(url: endpoint) else {
        return .unavailable(reason: "\(title) needs a connected runtime. Connect first, then open it.")
      }
      guard path.hasPrefix("/"), !path.hasPrefix("//"),
        var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)
      else { return .unavailable(reason: "\(title) has a malformed route.") }
      components.path = path
      components.query = nil
      components.fragment = nil
      guard let url = components.url else { return .unavailable(reason: "\(title) has a malformed route.") }
      return .available(url, .runtime(origin))
    case .localDocument(let relative, let key):
      let home: URL
      if let key, let override = context.environment[key], override.hasPrefix("/") {
        // An explicit tool home replaces `~/.<tool>` entirely, so drop that
        // leading component and keep the file name the owner tool uses.
        let file = relative.split(separator: "/").dropFirst().joined(separator: "/")
        home = URL(fileURLWithPath: override, isDirectory: true).appendingPathComponent(file)
      } else {
        home = context.homeDirectory.appendingPathComponent(relative)
      }
      let document = home.standardizedFileURL
      guard context.fileExists(document) else {
        return .unavailable(reason:
          "No generated dashboard at \(document.path). Run `aicx dashboard` to generate it, then open again.")
      }
      return .available(document, .localDocument(document))
    case .configuredService(let key, let owner):
      let file = context.configurationFile.path
      switch context.toolDestinations {
      case .failure(let problem):
        return .unavailable(reason: "\(title) cannot be resolved: \(file) has an invalid [tools] table (\(problem.message)).")
      case .success(let destinations):
        guard let url = destinations[key] else {
          return .unavailable(reason:
            "\(title) is not configured. \(owner). Add `[tools.\(key)]` with `url = \"http://host:port/console\"` to \(file) once it is served.")
        }
        guard let origin = WebRuntimeOrigin(url: url) else {
          return .unavailable(reason: "\(title) has a malformed URL in \(file).")
        }
        return .available(url, .service(origin))
      }
    case .unavailable(let reason):
      return .unavailable(reason: reason)
    }
  }
}
