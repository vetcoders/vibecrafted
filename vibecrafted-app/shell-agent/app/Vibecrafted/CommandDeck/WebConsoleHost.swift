import AppKit
import Observation
import SwiftUI
import WebKit

/// What one web session is currently doing.
///
/// This is the *web session's* state, not the App's. The App model owns
/// bootstrapping/connecting/blocked; this enum only reports what happened to
/// the embedded canvas so the native shell can tell the truth about it.
enum WebConsoleLoadState: Equatable, Sendable {
  case idle
  case loading(URL)
  case loaded(URL)
  case failed(url: URL?, reason: String)
  /// The web content process died, or the server went away mid-session.
  case interrupted(reason: String)
}

/// Typed outbound callbacks. The host never performs a native side effect on
/// its own: it reports, and the App decides. That keeps the native action
/// surface in one owner instead of growing a second one inside WebKit.
struct WebConsoleEvents {
  var stateDidChange: @MainActor (WebConsoleLoadState) -> Void = { _ in }
  /// A top-level navigation left the runtime origin through an allowed
  /// scheme. The receiver is expected to hand it to the system browser.
  var openExternally: @MainActor (URL) -> Void = { _ in }
  /// The page asked for a document that must not replace this tab's own:
  /// a `target=_blank` page, or a machine document (JSON) answering a
  /// main-frame navigation in the console. The receiver opens a native tab.
  var openInTab: @MainActor (URL, WebTabRole) -> Void = { _, _ in }
  /// A navigation was refused, with the policy's reason.
  var navigationBlocked: @MainActor (URL?, String) -> Void = { _, _ in }
  var downloadFinished: @MainActor (URL) -> Void = { _ in }
  var downloadFailed: @MainActor (String) -> Void = { _ in }

  init() {}
}

/// Observable projection of one tab's history and page identity for the
/// native toolbar. Written only by the owning session from WebKit KVO.
@MainActor
@Observable
final class WebTabNavigation {
  private(set) var canGoBack = false
  private(set) var canGoForward = false
  private(set) var isLoading = false
  private(set) var pageTitle: String?
  private(set) var currentURL: URL?

  fileprivate func update(from webView: WKWebView) {
    canGoBack = webView.canGoBack
    canGoForward = webView.canGoForward
    isLoading = webView.isLoading
    let title = webView.title?.trimmingCharacters(in: .whitespacesAndNewlines)
    pageTitle = (title?.isEmpty == false) ? title : nil
    currentURL = webView.url
  }
}

/// The durable owner of exactly one `WKWebView` for exactly one native tab.
///
/// Everything that must survive a window close, a route change, or a server
/// restart lives here rather than in the SwiftUI view: the web view itself,
/// the website data store, the navigation delegates, and the download
/// coordinator. `WebConsoleHost` only mounts this object, so SwiftUI is free
/// to rebuild its view tree without ever costing the user their session.
///
/// The console tab's session is created once and held for the App's lifetime.
/// Tool and reference tabs each own their own session; none of them is shared.
@MainActor
final class WebConsoleSession: NSObject {
  let role: WebTabRole
  let webView: WKWebView
  let navigation = WebTabNavigation()
  var events: WebConsoleEvents

  private(set) var loadState: WebConsoleLoadState = .idle
  /// The endpoint currently admitted by the App's caretaker resolver.
  private(set) var appliedEndpoint: URL?
  private(set) var scope: WebTabScope?

  var runtimeOrigin: WebRuntimeOrigin? {
    if case .runtime(let origin) = scope { return origin }
    return nil
  }

  private var activeNavigation: WKNavigation?
  private var lastCommittedURL: URL?
  private var route = URLComponents(string: "/")!
  /// Where Home goes for a runtime tab. The console always returns to the
  /// product overview; a tool tab returns to the route it was opened on.
  private var homeRoute = URLComponents(string: "/")!

  /// Native entrypoints select a route, never another window or origin.
  func navigate(path: String) {
    guard let components = Self.routeComponents(path) else { return }
    route = components
    retry()
  }

  private static func routeComponents(_ path: String) -> URLComponents? {
    guard path.hasPrefix("/"), !path.hasPrefix("//"),
      let components = URLComponents(string: path), components.scheme == nil,
      components.host == nil else { return nil }
    return components
  }

  private var routeURL: URL? {
    guard let endpoint = appliedEndpoint,
      var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)
    else { return nil }
    components.percentEncodedPath = route.percentEncodedPath
    components.percentEncodedQuery = route.percentEncodedQuery
    components.percentEncodedFragment = route.percentEncodedFragment
    return components.url
  }

  private func rememberRoute(_ url: URL) {
    guard case .runtime(let origin) = scope, origin.covers(url),
      let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
    route = components
  }

  private func scopeCovers(_ url: URL) -> Bool {
    switch scope {
    case .runtime(let origin): return origin.covers(url)
    case .localDocument(let document):
      return url.isFileURL && url.standardizedFileURL.path == document.standardizedFileURL.path
    case nil: return false
    }
  }

  private let downloads: WebDownloadCoordinator
  /// Guards the single automatic reload after a content-process death, so a
  /// repeatedly crashing page can never become a reload loop.
  private var didAutoRecoverFromTermination = false

  init(
    role: WebTabRole = .console,
    events: WebConsoleEvents = WebConsoleEvents(),
    websiteDataStore: WKWebsiteDataStore = .default(),
    downloadDestinationProvider: @escaping WebDownloadDestinationProvider = WebDownloadCoordinator.savePanelDestinationProvider
  ) {
    self.role = role
    self.events = events
    self.downloads = WebDownloadCoordinator(destinationProvider: downloadDestinationProvider)
    self.webView = WKWebView(frame: .zero, configuration: Self.makeConfiguration(role: role, websiteDataStore: websiteDataStore))
    super.init()

    webView.navigationDelegate = self
    webView.uiDelegate = self
    webView.allowsBackForwardNavigationGestures = true
    // Avoids a white flash before first paint and follows the system
    // appearance; the visual identity itself belongs to the deck's theme.
    webView.underPageBackgroundColor = .windowBackgroundColor
    webView.setAccessibilityLabel(role == .console ? "Vibecrafted server console" : "Vibecrafted \(role.rawValue) view")
    #if DEBUG
      // macOS 13.3+, and the repository targets macOS 14, so no gate is needed.
      webView.isInspectable = true
    #endif

    downloads.events = WebDownloadCoordinatorEvents(
      didFinish: { [weak self] url in self?.events.downloadFinished(url) },
      didFail: { [weak self] reason in self?.events.downloadFailed(reason) }
    )
    syncNavigation()
  }

  /// Mirrors WebKit's history state into the observable projection. Called
  /// from every navigation delegate callback and native history move, so the
  /// toolbar follows the page without a KVO closure crossing isolation.
  private func syncNavigation() {
    navigation.update(from: webView)
  }

  private static func makeConfiguration(role: WebTabRole, websiteDataStore: WKWebsiteDataStore) -> WKWebViewConfiguration {
    let configuration = WKWebViewConfiguration()
    // One shared, persistent store. Cookies and HTTP authentication survive a
    // window close and a server restart, which is what makes this a session
    // rather than a page load.
    configuration.websiteDataStore = websiteDataStore
    // Reference tabs display machine documents; they run no page script.
    configuration.defaultWebpagePreferences.allowsContentJavaScript = role.allowsContentJavaScript
    // INVARIANT: no `WKScriptMessageHandler` is registered here or anywhere
    // else in this file. The App exposes no JavaScript-to-native channel, so
    // no page can name an action for the App to perform. Native actions are
    // reached through native UI and typed native code, never through a string
    // sent from a web page. This holds for every tab role.
    return configuration
  }

  // MARK: - Endpoint

  /// Applies a resolved endpoint.
  ///
  /// Repeated caretaker polls do not reload. An unavailable-to-available
  /// transition reapplies even the same URL; a web failure uses explicit retry.
  func apply(endpoint: URL?) {
    guard let endpoint else {
      // Preserve cookies and route, but fence completions from the old owner.
      activeNavigation = nil
      lastCommittedURL = nil
      appliedEndpoint = nil
      scope = nil
      webView.stopLoading()
      updateState(.idle)
      return
    }
    guard appliedEndpoint != endpoint else { return }
    guard let origin = WebRuntimeOrigin(url: endpoint) else {
      events.navigationBlocked(endpoint, "the resolved endpoint is not an http(s) URL")
      return
    }
    lastCommittedURL = nil
    appliedEndpoint = endpoint
    scope = .runtime(origin)
    didAutoRecoverFromTermination = false
    if let url = routeURL { load(url) }
  }

  /// Applies an endpoint and selects the route this tab is for. Used by tool
  /// tabs: Home returns here, not to the product overview.
  func apply(endpoint: URL, homePath: String) {
    if let components = Self.routeComponents(homePath) {
      homeRoute = components
      route = components
    }
    apply(endpoint: endpoint)
  }

  /// Shows exactly one local HTML document. WebKit read access is granted to
  /// that file alone, so the page cannot enumerate or load anything else from
  /// disk; links out go through the system browser via the policy.
  func present(localDocument: URL) {
    guard localDocument.isFileURL else {
      events.navigationBlocked(localDocument, "a local document must be a file URL")
      return
    }
    let document = localDocument.standardizedFileURL
    appliedEndpoint = nil
    lastCommittedURL = nil
    scope = .localDocument(document)
    didAutoRecoverFromTermination = false
    updateState(.loading(document))
    activeNavigation = webView.loadFileURL(document, allowingReadAccessTo: document)
  }

  /// Re-requests the current endpoint after a failure or a server restart.
  /// Safe to call from a retry button; does nothing before a first endpoint.
  func retry() {
    switch scope {
    case .runtime:
      guard appliedEndpoint != nil else { return }
      didAutoRecoverFromTermination = false
      if let url = routeURL { load(url) }
    case .localDocument(let document):
      present(localDocument: document)
    case nil:
      return
    }
  }

  // MARK: - History

  /// Home is always valid: with a runtime it returns to this tab's home route
  /// even while the page shows an error or a machine document; without one it
  /// records the wish so the next applied endpoint lands on Home.
  func goHome() {
    switch scope {
    case .runtime:
      route = homeRoute
      retry()
    case .localDocument(let document):
      present(localDocument: document)
    case nil:
      route = homeRoute
    }
  }

  func goBack() {
    guard webView.canGoBack else { return }
    activeNavigation = webView.goBack()
    syncNavigation()
  }

  func goForward() {
    guard webView.canGoForward else { return }
    activeNavigation = webView.goForward()
    syncNavigation()
  }

  private func load(_ url: URL) {
    rememberRoute(url)
    updateState(.loading(url))
    var request = URLRequest(url: url)
    // A restarted server must not be answered out of the cache.
    request.cachePolicy = .reloadIgnoringLocalCacheData
    activeNavigation = webView.load(request)
  }

  private func updateState(_ state: WebConsoleLoadState) {
    syncNavigation()
    guard loadState != state else { return }
    loadState = state
    events.stateDidChange(state)
  }

  // MARK: - Mounting

  /// Moves the one web view into `container`, keeping the live session.
  /// Idempotent: re-attaching to the same container is a no-op.
  func attach(to container: NSView) {
    guard webView.superview !== container else { return }
    webView.removeFromSuperview()
    webView.translatesAutoresizingMaskIntoConstraints = false
    container.addSubview(webView)
    NSLayoutConstraint.activate([
      webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
      webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
      webView.topAnchor.constraint(equalTo: container.topAnchor),
      webView.bottomAnchor.constraint(equalTo: container.bottomAnchor),
    ])
  }
}

// MARK: - Navigation

extension WebConsoleSession: WKNavigationDelegate {
  private func decideAction(url: URL?, isMainFrame: Bool, shouldPerformDownload: Bool) -> WebNavigationDecision {
    if case .localDocument(let document) = scope {
      return WebNavigationPolicy.decideLocalDocumentNavigation(
        url: url, document: document, isMainFrame: isMainFrame, shouldPerformDownload: shouldPerformDownload)
    }
    return WebNavigationPolicy.decide(
      url: url, runtime: runtimeOrigin, isMainFrame: isMainFrame, shouldPerformDownload: shouldPerformDownload)
  }

  func webView(
    _ webView: WKWebView,
    decidePolicyFor navigationAction: WKNavigationAction,
    preferences: WKWebpagePreferences
  ) async -> (WKNavigationActionPolicy, WKWebpagePreferences) {
    let url = navigationAction.request.url
    // A nil target frame means a new window/tab, which is a top-level move.
    let isMainFrame = navigationAction.targetFrame?.isMainFrame ?? true
    let decision = decideAction(
      url: url, isMainFrame: isMainFrame, shouldPerformDownload: navigationAction.shouldPerformDownload)
    switch decision {
    case .allowInApp:
      if isMainFrame, let url { rememberRoute(url) }
      return (.allow, preferences)
    case .startDownload:
      return (.download, preferences)
    case .openExternally(let external):
      events.openExternally(external)
      return (.cancel, preferences)
    case .block(let reason):
      events.navigationBlocked(url, reason)
      return (.cancel, preferences)
    }
  }

  func webView(
    _ webView: WKWebView,
    decidePolicyFor navigationResponse: WKNavigationResponse
  ) async -> WKNavigationResponsePolicy {
    let response = navigationResponse.response
    if case .localDocument = scope {
      // The action policy already confined this tab to its one file.
      return navigationResponse.canShowMIMEType ? .allow : .cancel
    }
    let decision = WebNavigationPolicy.decideResponse(
      url: response.url, runtime: runtimeOrigin,
      isMainFrame: navigationResponse.isForMainFrame,
      canShowMIMEType: navigationResponse.canShowMIMEType,
      statusCode: (response as? HTTPURLResponse)?.statusCode,
      mimeType: response.mimeType, role: role)
    switch decision {
    case .allowInApp: return .allow
    case .startDownload: return .download
    case .divertToReferenceTab:
      // Cancelling before commit keeps this tab's current document, DOM and
      // history exactly as they were; the JSON is shown in its own tab.
      if let url = response.url { events.openInTab(url, .reference) }
      restoreCommittedCanvas()
      return .cancel
    case .block(let reason):
      if navigationResponse.isForMainFrame {
        updateState(.failed(url: response.url, reason: reason))
      }
      return .cancel
    }
  }

  func webView(
    _ webView: WKWebView,
    respondTo challenge: URLAuthenticationChallenge
  ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
    let space = challenge.protectionSpace
    let decision = WebNavigationPolicy.decideAuthenticationChallenge(
      method: space.authenticationMethod,
      host: space.host,
      port: space.port,
      scheme: space.protocol,
      runtime: runtimeOrigin
    )
    switch decision {
    case .performDefaultHandling:
      // No credential is ever constructed here. In particular there is no
      // `URLCredential(trust:)` path, so an invalid certificate cannot be
      // accepted by this App.
      return (.performDefaultHandling, nil)
    case .cancel(let reason):
      // The reason already names the offending host; a bare host string is
      // not a URL and must not be passed off as one.
      events.navigationBlocked(nil, reason)
      return (.cancelAuthenticationChallenge, nil)
    }
  }

  func webView(
    _ webView: WKWebView,
    navigationAction: WKNavigationAction,
    didBecome download: WKDownload
  ) {
    downloads.take(download, relativeTo: webView.window, runtime: runtimeOrigin)
    restoreCommittedCanvas()
  }

  func webView(
    _ webView: WKWebView,
    navigationResponse: WKNavigationResponse,
    didBecome download: WKDownload
  ) {
    downloads.take(download, relativeTo: webView.window, runtime: runtimeOrigin)
    restoreCommittedCanvas()
  }

  /// A cancelled main-frame navigation (download, diverted machine document)
  /// leaves the previously committed page on screen; say so.
  private func restoreCommittedCanvas() {
    guard let url = lastCommittedURL, scopeCovers(url) else { return }
    rememberRoute(url)
    updateState(.loaded(url))
  }

  func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
    guard scope != nil else { return }
    activeNavigation = navigation
    switch scope {
    case .runtime: if let url = routeURL { updateState(.loading(url)) }
    case .localDocument(let document): updateState(.loading(document))
    case nil: break
    }
  }

  func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
    syncNavigation()
  }

  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    syncNavigation()
    guard let navigation, navigation === activeNavigation,
      let url = webView.url, scopeCovers(url) else { return }
    didAutoRecoverFromTermination = false
    lastCommittedURL = url
    rememberRoute(url)
    updateState(.loaded(url))
  }

  func webView(
    _ webView: WKWebView,
    didFailProvisionalNavigation navigation: WKNavigation!,
    withError error: Error
  ) {
    guard let navigation, navigation === activeNavigation else { return }
    report(error)
  }

  func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
    guard let navigation, navigation === activeNavigation else { return }
    report(error)
  }

  private func report(_ error: Error) {
    syncNavigation()
    let nsError = error as NSError
    // A cancelled navigation is our own policy decision, not a fault: WebKit
    // reports an action/response `.cancel` as "frame load interrupted"
    // (WebKitErrorDomain 102) and a stopped load as NSURLErrorCancelled. Real
    // refusals already set their own state where the decision was made.
    let interruptedByPolicy = nsError.domain == "WebKitErrorDomain" && nsError.code == 102
    guard !(nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled), !interruptedByPolicy else {
      return
    }
    updateState(.failed(url: appliedEndpoint, reason: nsError.localizedDescription))
  }

  func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
    updateState(.interrupted(reason: "the web content process ended"))
    guard !didAutoRecoverFromTermination, scope != nil else { return }
    // Exactly one automatic recovery; anything further is the user's call.
    didAutoRecoverFromTermination = true
    switch scope {
    case .runtime: if let url = routeURL { load(url) }
    case .localDocument(let document): present(localDocument: document)
    case nil: break
    }
  }
}

// MARK: - UI

extension WebConsoleSession: WKUIDelegate {
  func webView(
    _ webView: WKWebView,
    createWebViewWith configuration: WKWebViewConfiguration,
    for navigationAction: WKNavigationAction,
    windowFeatures: WKWindowFeatures
  ) -> WKWebView? {
    // WebKit never gets a second view of its own: `target="_blank"` becomes a
    // native tab the App owns (with no `window.opener` link back to this
    // page), a download, or a system-browser hand-off. Returning nil keeps
    // the "one WKWebView per native tab" guarantee structural.
    let url = navigationAction.request.url
    if case .localDocument(let document) = scope {
      switch WebNavigationPolicy.decideLocalDocumentNavigation(
        url: url, document: document, isMainFrame: true,
        shouldPerformDownload: navigationAction.shouldPerformDownload)
      {
      case .openExternally(let external): events.openExternally(external)
      case .block(let reason): events.navigationBlocked(url, reason)
      case .allowInApp, .startDownload: break
      }
      return nil
    }
    let decision = WebNavigationPolicy.decideNewWindow(
      url: url, runtime: runtimeOrigin, role: role,
      shouldPerformDownload: navigationAction.shouldPerformDownload)
    switch decision {
    case .openInTab(let url, let role):
      events.openInTab(url, role)
    case .startDownload(let url):
      // Load it here; the response policy turns it into a native download.
      load(url)
    case .openExternally(let external):
      events.openExternally(external)
    case .block(let reason):
      events.navigationBlocked(url, reason)
    }
    return nil
  }

  func webView(
    _ webView: WKWebView,
    runOpenPanelWith parameters: WKOpenPanelParameters,
    initiatedByFrame frame: WKFrameInfo
  ) async -> [URL]? {
    let panel = NSOpenPanel()
    panel.canChooseFiles = true
    panel.canChooseDirectories = parameters.allowsDirectories
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection
    panel.prompt = "Choose"
    let response = await WebDownloadCoordinator.presentPanel(panel, in: webView.window)
    guard response == .OK else { return nil }
    return panel.urls
  }
}

/// Plain container whose only job is to give the persistent web view a place
/// to live, so the web view is never owned by a SwiftUI-managed view.
final class WebConsoleContainerView: NSView {
  override var isFlipped: Bool { true }
}

/// SwiftUI mount point for one tab's persistent session.
///
/// The representable is intentionally almost empty. It holds no web view, no
/// delegate and no navigation state; SwiftUI may recreate this struct as often
/// as it likes without disturbing the session.
struct WebConsoleHost: NSViewRepresentable {
  /// The tab-owned session. Pass the same instance for the tab's lifetime.
  let session: WebConsoleSession

  func makeNSView(context: Context) -> WebConsoleContainerView {
    let container = WebConsoleContainerView()
    container.setAccessibilityElement(false)
    session.attach(to: container)
    return container
  }

  /// Rendering only mounts. AppModel callbacks own endpoint changes outside
  /// SwiftUI's update pass, so drawing cannot publish state or reload a page.
  func updateNSView(_ nsView: WebConsoleContainerView, context: Context) {
    session.attach(to: nsView)
  }
}
