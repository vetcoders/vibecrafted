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
    /// No launch contract exists. The reason is shown, never hidden.
    case unavailable(reason: String)
  }

  let id: String
  let title: String
  let symbol: String
  let role: WebTabRole
  let target: Target

  /// Every destination the App knows about, in menu order.
  static let catalog: [ToolDestination] = [
    ToolDestination(
      id: "loctree-report", title: "Loctree Report", symbol: "doc.text.magnifyingglass",
      role: .tool, target: .runtimeRoute("/structure/report")),
    ToolDestination(
      id: "aicx-dashboard", title: "AICX Dashboard", symbol: "clock.arrow.circlepath",
      role: .tool, target: .localDocument(homeRelativePath: ".aicx/aicx-dashboard.html",
        homeEnvironmentKey: "AICX_HOME")),
    ToolDestination(
      id: "slack-agent-console", title: "Slack Agent Console", symbol: "bubble.left.and.bubble.right",
      role: .tool, target: .unavailable(reason:
        "The vc-slack operator console only exists as a development server (`make portal`). "
        + "No installed surface or launch contract is registered, so the App does not guess a port.")),
  ]

  static func named(_ id: String) -> ToolDestination? {
    catalog.first { $0.id == id }
  }
}

enum ToolDestinationResolution: Equatable, Sendable {
  case available(URL, WebTabScope)
  case unavailable(reason: String)

  var url: URL? {
    if case .available(let url, _) = self { return url }
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

  init(
    runtimeEndpoint: URL?, homeDirectory: URL, environment: [String: String],
    fileExists: @escaping (URL) -> Bool
  ) {
    self.runtimeEndpoint = runtimeEndpoint
    self.homeDirectory = homeDirectory
    self.environment = environment
    self.fileExists = fileExists
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
    case .unavailable(let reason):
      return .unavailable(reason: reason)
    }
  }
}
