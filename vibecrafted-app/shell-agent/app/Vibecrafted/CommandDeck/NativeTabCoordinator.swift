// Vibecrafted — native tabs
// Created by Vetcoders
//
// The one owner of tool and reference tabs. It creates a window per tab in
// the console's tab group, keeps one durable WebConsoleSession per tab,
// re-focuses an existing tab instead of opening a twin, and forgets a tab
// when its window closes. It owns no runtime: the server, supervisor and
// terminal stay with the App, and closing a tab touches none of them.

import AppKit
import Observation
import SwiftUI
import WebKit

/// Presentation model of one tool tab: projects the tab's own web load state
/// onto the shared deck phases so tool tabs and the console wear one chrome.
@MainActor
@Observable
final class ToolTabModel: CommandDeckActionHandling {
  let title: String
  private(set) var webState: WebConsoleLoadState = .idle
  private var hasLoadedOnce = false
  @ObservationIgnored var retry: @MainActor () -> Void = {}
  /// Why nothing can load yet (runtime disconnected). Shown as the problem.
  var unavailableReason: String?

  init(title: String) {
    self.title = title
  }

  func receiveWebState(_ value: WebConsoleLoadState) {
    webState = value
    if case .loaded = value { hasLoadedOnce = true }
    if case .failed = value { hasLoadedOnce = false }
    if case .interrupted = value { hasLoadedOnce = false }
  }

  var presentation: CommandDeckPresentation {
    let phase: CommandDeckPhase
    var problem: CommandDeckProblem?
    if let unavailableReason {
      phase = .recovering
      problem = CommandDeckProblem(title: "\(title) is unavailable", summary: "\(unavailableReason)", receipt: nil)
    } else {
      switch webState {
      case .idle: phase = .connecting
      case .loading: phase = hasLoadedOnce ? .online : .connecting
      case .loaded: phase = .online
      case .failed(_, let reason), .interrupted(let reason):
        phase = .recovering
        problem = CommandDeckProblem(title: "\(title) cannot load", summary: "\(reason)", receipt: nil)
      }
    }
    return CommandDeckPresentation(
      phase: phase, problem: problem, endpointCaption: title,
      availableActions: unavailableReason == nil ? [.retryConnection] : [])
  }

  func handle(_ action: CommandDeckChromeAction) {
    if action == .retryConnection { retry() }
  }
}

/// One tool or reference tab: its own window, session and model.
@MainActor
final class ToolTabWindowController: NSWindowController, NSWindowDelegate, CommandDeckNavigationHandling {
  let key: String
  let destination: ToolDestination?
  let session: WebConsoleSession
  let model: ToolTabModel
  private let openExternally: @MainActor (URL) -> Void
  var onClose: (@MainActor () -> Void)?
  /// Set by the coordinator for a tab whose own canvas cannot show the
  /// product overview: a read-only reference view runs no page script, so a
  /// dashboard loaded into it would be a script-less half-console. Home then
  /// asks the coordinator for the overview in an interactive tab instead of
  /// reloading the machine document here. Returns `false` when nothing could
  /// be shown (no runtime), and Home falls back to the session's own route.
  var presentProductOverview: (@MainActor () -> Bool)?

  init(
    key: String, title: String, destination: ToolDestination?, session: WebConsoleSession,
    openExternally: @escaping @MainActor (URL) -> Void
  ) {
    self.key = key
    self.destination = destination
    self.session = session
    self.model = ToolTabModel(title: title)
    self.openExternally = openExternally
    let window = CommandDeckWindowFactory.makeWindow(title: title, frameAutosaveName: nil)
    super.init(window: window)
    window.delegate = self
    model.retry = { [weak session] in session?.retry() }
    session.events.stateDidChange = { [weak self] state in
      self?.model.receiveWebState(state)
      if case .loaded = state, let title = self?.session.navigation.pageTitle, !title.isEmpty {
        self?.window?.title = title
      }
    }
    CommandDeckWindowFactory.mount(
      ToolTabRootView(model: model, session: session, navigationHandler: self), in: window)
  }

  @available(*, unavailable)
  required init?(coder: NSCoder) { fatalError("Use the coordinator initializer") }

  func navigate(_ action: CommandDeckNavigationAction) {
    switch action {
    case .home:
      if let presentProductOverview, presentProductOverview() { return }
      session.goHome()
    case .back: session.goBack()
    case .forward: session.goForward()
    case .openInBrowser:
      guard let url = session.navigation.currentURL, url.scheme == "http" || url.scheme == "https" else { return }
      openExternally(url)
    }
  }

  func windowWillClose(_ notification: Notification) {
    // The tab's web content goes away with it; nothing else does.
    session.webView.stopLoading()
    onClose?()
  }
}

@MainActor
private struct ToolTabRootView: View {
  let model: ToolTabModel
  let session: WebConsoleSession
  let navigationHandler: any CommandDeckNavigationHandling

  var body: some View {
    CommandDeckView(
      presentation: model.presentation, actions: model,
      navigation: session.navigation, navigationHandler: navigationHandler
    ) {
      WebConsoleHost(session: session)
    }
  }
}

@MainActor
final class NativeTabCoordinator {
  /// How a tab is asked for.
  enum Request: Equatable {
    case destination(ToolDestination)
    case url(URL, WebTabRole)
  }

  /// What happened to the request.
  enum Outcome: Equatable {
    case opened(String)
    case focused(String)
    case unavailable(reason: String)
  }

  /// Returns the console window, showing it first when it was closed. Tabs are
  /// always added to this window's group.
  private let anchorWindow: @MainActor () -> NSWindow?
  private let openExternally: @MainActor (URL) -> Void
  private let websiteDataStore: WKWebsiteDataStore
  private let homeDirectory: URL
  private let environment: [String: String]
  private let fileExists: (URL) -> Bool

  private(set) var tabs: [String: ToolTabWindowController] = [:]
  private(set) var runtimeEndpoint: URL?
  /// Runtime ownership survives an endpoint withdrawal. `WebConsoleSession`
  /// intentionally clears its transient scope while unavailable so WebKit
  /// completions from the previous server cannot affect a later connection.
  /// The coordinator therefore keeps this durable affiliation separately,
  /// allowing the tab to rejoin either the same or a replacement endpoint.
  private var runtimeTabKeys: Set<String> = []

  init(
    anchorWindow: @escaping @MainActor () -> NSWindow?,
    openExternally: @escaping @MainActor (URL) -> Void,
    websiteDataStore: WKWebsiteDataStore = .default(),
    homeDirectory: URL = FileManager.default.homeDirectoryForCurrentUser,
    environment: [String: String] = ProcessInfo.processInfo.environment,
    fileExists: @escaping (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }
  ) {
    self.anchorWindow = anchorWindow
    self.openExternally = openExternally
    self.websiteDataStore = websiteDataStore
    self.homeDirectory = homeDirectory
    self.environment = environment
    self.fileExists = fileExists
  }

  var destinationContext: ToolDestinationContext {
    ToolDestinationContext(
      runtimeEndpoint: runtimeEndpoint, homeDirectory: homeDirectory,
      environment: environment, fileExists: fileExists)
  }

  func resolve(_ destination: ToolDestination) -> ToolDestinationResolution {
    destination.resolve(in: destinationContext)
  }

  /// The runtime endpoint changed. Runtime-scoped tabs follow it (their route
  /// is kept inside the session); a lost endpoint leaves them showing an
  /// honest unavailable state, never a cached page. Local documents ignore it.
  func apply(runtimeEndpoint endpoint: URL?) {
    guard runtimeEndpoint != endpoint else { return }
    runtimeEndpoint = endpoint
    for tab in tabs.values {
      // Local documents and configured services have their own scope. Do not
      // infer runtime affiliation from `session.scope`: that scope is cleared
      // during an outage by design, while this durable ownership remains.
      guard runtimeTabKeys.contains(tab.key) else { continue }
      if let endpoint {
        tab.model.unavailableReason = nil
        if case .runtimeRoute(let path)? = tab.destination?.target {
          tab.session.apply(endpoint: endpoint, route: path, home: .route(path))
        } else {
          // A link-opened tab keeps its own route and home inside the session.
          tab.session.apply(endpoint: endpoint)
        }
      } else {
        tab.session.apply(endpoint: nil)
        tab.model.unavailableReason = "The runtime endpoint is no longer available."
      }
    }
  }

  @discardableResult
  func open(_ request: Request) -> Outcome {
    switch request {
    case .destination(let destination):
      switch resolve(destination) {
      case .unavailable(let reason):
        return .unavailable(reason: reason)
      case .available(let url, let scope):
        if let existing = tabs[destination.id] {
          focus(existing)
          return .focused(destination.id)
        }
        // Generated documents, local files and foreign services never share
        // the console's persistent store: nothing they set survives the tab,
        // and nothing the console holds is visible to them.
        let ephemeral = scope.isLocalDocument || scope.isService || destination.isolatedContent
        let session = WebConsoleSession(
          role: destination.role,
          websiteDataStore: ephemeral ? .nonPersistent() : websiteDataStore)
        makeTab(
          key: destination.id, title: destination.title, destination: destination, session: session,
          followsRuntimeEndpoint: scope.followsRuntimeEndpoint)
        switch scope {
        case .runtime:
          // A destination owns its overview: the Loctree report returns to
          // the report, not to the product root.
          if case .runtimeRoute(let path) = destination.target, let endpoint = runtimeEndpoint {
            session.apply(endpoint: endpoint, route: path, home: .route(path))
          }
        case .localDocument(let document):
          session.present(localDocument: document)
        case .service:
          session.present(service: url)
        }
        return .opened(destination.id)
      }
    case .url(let url, let role):
      let key = Self.key(for: url, role: role)
      if let existing = tabs[key] {
        focus(existing)
        return .focused(key)
      }
      guard let endpoint = runtimeEndpoint, let origin = WebRuntimeOrigin(url: endpoint), origin.covers(url),
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
      else {
        return .unavailable(reason: "Only pages on the connected runtime open in a tab.")
      }
      var path = components.percentEncodedPath.isEmpty ? "/" : components.percentEncodedPath
      if let query = components.percentEncodedQuery { path += "?" + query }
      if let fragment = components.percentEncodedFragment { path += "#" + fragment }
      let session = WebConsoleSession(role: role, websiteDataStore: websiteDataStore)
      let title = role == .reference ? "Reference · \(components.path)" : components.path
      let tab = makeTab(key: key, title: title, destination: nil, session: session, followsRuntimeEndpoint: true)
      // The URL that opened the tab is where it starts, not where Home goes.
      // A tool tab (a `target=_blank` page, raw JSON, an error) returns to
      // the runtime's product overview inside itself. A reference view runs
      // no script, so it cannot render that overview: its session keeps the
      // document as its route (so a reconnect re-presents it), and Home is
      // answered by the coordinator with the overview in an interactive tab.
      let home: WebTabHome = role == .reference ? .route(path) : .runtimeOverview
      session.apply(endpoint: endpoint, route: path, home: home)
      if role == .reference {
        tab.presentProductOverview = { [weak self] in
          guard let self else { return false }
          if case .unavailable = self.openProductOverview() { return false }
          return true
        }
      }
      return .opened(key)
    }
  }

  /// Shows the connected runtime's product overview in an interactive tool
  /// tab: the overview tab already open is focused and brought back to `/`,
  /// otherwise one opens on `/`. This is how Home leaves a view that cannot
  /// render the overview itself (a read-only reference view reached from an
  /// ordinary API link). It reuses the same route, key and origin check as
  /// any link-opened tool tab, moves no other tab and changes no script
  /// setting anywhere.
  @discardableResult
  func openProductOverview() -> Outcome {
    guard let endpoint = runtimeEndpoint, let overview = Self.productOverviewURL(for: endpoint) else {
      return .unavailable(reason: "The product overview needs a connected runtime.")
    }
    let outcome = open(.url(overview, .tool))
    // Focusing the existing tab is not Home: since it opened the user may have
    // navigated it to `/runs` or into an error. Its own Home is `/` on the
    // current endpoint and a real load, so Back still reaches the page it
    // left. A tab that just opened is already loading `/`.
    if case .focused(let key) = outcome, let tab = tabs[key] {
      tab.session.goHome()
    }
    return outcome
  }

  /// `/` on the endpoint the caretaker resolved, query and fragment dropped.
  /// The App never names a host or port of its own.
  static func productOverviewURL(for endpoint: URL) -> URL? {
    guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else { return nil }
    components.path = "/"
    components.query = nil
    components.fragment = nil
    return components.url
  }

  static func key(for url: URL, role: WebTabRole) -> String {
    "\(role.rawValue):\(url.absoluteString)"
  }

  func controller(for window: NSWindow?) -> ToolTabWindowController? {
    guard let window else { return nil }
    return tabs.values.first { $0.window === window }
  }

  @discardableResult
  private func makeTab(
    key: String, title: String, destination: ToolDestination?, session: WebConsoleSession,
    followsRuntimeEndpoint: Bool
  ) -> ToolTabWindowController {
    let tab = ToolTabWindowController(
      key: key, title: title, destination: destination, session: session, openExternally: openExternally)
    session.events.openExternally = { [openExternally] url in openExternally(url) }
    // Tool tabs never open further tabs of their own: a page inside a tool
    // tab that wants a new window is handed to the system browser instead,
    // which keeps tab count bounded by explicit user intent.
    session.events.openInTab = { [weak self, openExternally] url, _ in
      if let self, let endpoint = self.runtimeEndpoint, WebRuntimeOrigin(url: endpoint)?.covers(url) == true {
        self.open(.url(url, .tool))
      } else {
        openExternally(url)
      }
    }
    tab.onClose = { [weak self] in
      self?.tabs.removeValue(forKey: key)
      self?.runtimeTabKeys.remove(key)
    }
    tabs[key] = tab
    if followsRuntimeEndpoint { runtimeTabKeys.insert(key) }
    if let anchor = anchorWindow(), let window = tab.window {
      anchor.addTabbedWindow(window, ordered: .above)
    }
    focus(tab)
    return tab
  }

  private func focus(_ tab: ToolTabWindowController) {
    tab.showWindow(nil)
    tab.window?.makeKeyAndOrderFront(nil)
  }
}

extension WebTabScope {
  var isLocalDocument: Bool {
    if case .localDocument = self { return true }
    return false
  }

  var isService: Bool {
    if case .service = self { return true }
    return false
  }

  /// Only runtime-scoped tabs move with the caretaker endpoint.
  var followsRuntimeEndpoint: Bool {
    if case .runtime = self { return true }
    return false
  }
}
