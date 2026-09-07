import AppKit
import SwiftUI
import WebKit

/// What the one web session is currently doing.
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
  /// A navigation was refused, with the policy's reason.
  var navigationBlocked: @MainActor (URL?, String) -> Void = { _, _ in }
  var downloadFinished: @MainActor (URL) -> Void = { _ in }
  var downloadFailed: @MainActor (String) -> Void = { _ in }

  init() {}
}

/// The durable owner of exactly one `WKWebView`.
///
/// Everything that must survive a window close, a route change, or a server
/// restart lives here rather than in the SwiftUI view: the web view itself,
/// the shared website data store, the navigation delegates, and the download
/// coordinator. `WebConsoleHost` only mounts this object, so SwiftUI is free
/// to rebuild its view tree without ever costing the user their session.
///
/// Create exactly one and hold it for the App's lifetime.
@MainActor
final class WebConsoleSession: NSObject {
  let webView: WKWebView
  var events: WebConsoleEvents

  private(set) var loadState: WebConsoleLoadState = .idle
  /// The endpoint currently admitted by the App's caretaker resolver.
  private(set) var appliedEndpoint: URL?
  private(set) var runtimeOrigin: WebRuntimeOrigin?

  private var activeNavigation: WKNavigation?
  private var lastCommittedURL: URL?
  private var route = URLComponents(string: "/")!

  /// Native entrypoints select a route, never another window or origin.
  func navigate(path: String) {
    guard path.hasPrefix("/"), !path.hasPrefix("//"),
      let components = URLComponents(string: path), components.scheme == nil,
      components.host == nil else { return }
    route = components
    retry()
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
    guard WebRuntimeOrigin(url: url) == runtimeOrigin,
      let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
    route = components
  }

  private let downloads: WebDownloadCoordinator
  /// Guards the single automatic reload after a content-process death, so a
  /// repeatedly crashing page can never become a reload loop.
  private var didAutoRecoverFromTermination = false

  init(
    events: WebConsoleEvents = WebConsoleEvents(),
    websiteDataStore: WKWebsiteDataStore = .default(),
    downloadDestinationProvider: @escaping WebDownloadDestinationProvider = WebDownloadCoordinator.savePanelDestinationProvider
  ) {
    self.events = events
    self.downloads = WebDownloadCoordinator(destinationProvider: downloadDestinationProvider)
    self.webView = WKWebView(frame: .zero, configuration: Self.makeConfiguration(websiteDataStore: websiteDataStore))
    super.init()

    webView.navigationDelegate = self
    webView.uiDelegate = self
    webView.allowsBackForwardNavigationGestures = true
    // Avoids a white flash before first paint and follows the system
    // appearance; the visual identity itself belongs to the deck's theme.
    webView.underPageBackgroundColor = .windowBackgroundColor
    webView.setAccessibilityLabel("Vibecrafted server console")
    #if DEBUG
      // macOS 13.3+, and the repository targets macOS 14, so no gate is needed.
      webView.isInspectable = true
    #endif

    downloads.events = WebDownloadCoordinatorEvents(
      didFinish: { [weak self] url in self?.events.downloadFinished(url) },
      didFail: { [weak self] reason in self?.events.downloadFailed(reason) }
    )
  }

  private static func makeConfiguration(websiteDataStore: WKWebsiteDataStore) -> WKWebViewConfiguration {
    let configuration = WKWebViewConfiguration()
    // One shared, persistent store. Cookies and HTTP authentication survive a
    // window close and a server restart, which is what makes this a session
    // rather than a page load.
    configuration.websiteDataStore = websiteDataStore
    configuration.defaultWebpagePreferences.allowsContentJavaScript = true
    // INVARIANT: no `WKScriptMessageHandler` is registered here or anywhere
    // else in this file. The App exposes no JavaScript-to-native channel, so
    // no page can name an action for the App to perform. Native actions are
    // reached through native UI and typed native code, never through a string
    // sent from a web page.
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
      runtimeOrigin = nil
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
    runtimeOrigin = origin
    didAutoRecoverFromTermination = false
    if let url = routeURL { load(url) }
  }

  /// Re-requests the current endpoint after a failure or a server restart.
  /// Safe to call from a retry button; does nothing before a first endpoint.
  func retry() {
    guard appliedEndpoint != nil else { return }
    didAutoRecoverFromTermination = false
    if let url = routeURL { load(url) }
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
  func webView(
    _ webView: WKWebView,
    decidePolicyFor navigationAction: WKNavigationAction,
    preferences: WKWebpagePreferences
  ) async -> (WKNavigationActionPolicy, WKWebpagePreferences) {
    let url = navigationAction.request.url
    // A nil target frame means a new window/tab, which is a top-level move.
    let isMainFrame = navigationAction.targetFrame?.isMainFrame ?? true
    let decision = WebNavigationPolicy.decide(
      url: url,
      runtime: runtimeOrigin,
      isMainFrame: isMainFrame,
      shouldPerformDownload: navigationAction.shouldPerformDownload
    )
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
    let decision = WebNavigationPolicy.decideResponse(
      url: response.url, runtime: runtimeOrigin,
      isMainFrame: navigationResponse.isForMainFrame,
      canShowMIMEType: navigationResponse.canShowMIMEType,
      statusCode: (response as? HTTPURLResponse)?.statusCode)
    switch decision {
    case .allowInApp: return .allow
    case .startDownload: return .download
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
    restoreCommittedCanvasAfterDownload()
  }

  func webView(
    _ webView: WKWebView,
    navigationResponse: WKNavigationResponse,
    didBecome download: WKDownload
  ) {
    downloads.take(download, relativeTo: webView.window, runtime: runtimeOrigin)
    restoreCommittedCanvasAfterDownload()
  }

  private func restoreCommittedCanvasAfterDownload() {
    guard let url = lastCommittedURL, WebRuntimeOrigin(url: url) == runtimeOrigin else { return }
    rememberRoute(url)
    updateState(.loaded(url))
  }

  func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
    guard runtimeOrigin != nil else { return }
    activeNavigation = navigation
    if let url = routeURL { updateState(.loading(url)) }
  }

  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    guard let navigation, navigation === activeNavigation,
      let url = webView.url, WebRuntimeOrigin(url: url) == runtimeOrigin else { return }
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
    let nsError = error as NSError
    // A cancelled navigation is usually our own policy decision, not a fault.
    guard !(nsError.domain == NSURLErrorDomain && nsError.code == NSURLErrorCancelled) else {
      return
    }
    updateState(.failed(url: appliedEndpoint, reason: nsError.localizedDescription))
  }

  func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
    updateState(.interrupted(reason: "the web content process ended"))
    guard !didAutoRecoverFromTermination, appliedEndpoint != nil else { return }
    // Exactly one automatic recovery; anything further is the user's call.
    didAutoRecoverFromTermination = true
    if let url = routeURL { load(url) }
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
    // Never a second web view: `target="_blank"` either stays in the one
    // session or leaves through the system, and returning nil keeps the
    // "exactly one WKWebView" guarantee structural rather than aspirational.
    let url = navigationAction.request.url
    let decision = WebNavigationPolicy.decide(
      url: url,
      runtime: runtimeOrigin,
      isMainFrame: true,
      shouldPerformDownload: navigationAction.shouldPerformDownload
    )
    switch decision {
    case .allowInApp, .startDownload:
      // Load it here; a download response is caught by the response policy.
      if let url { load(url) }
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

/// SwiftUI mount point for the one persistent session.
///
/// The representable is intentionally almost empty. It holds no web view, no
/// delegate and no navigation state; SwiftUI may recreate this struct as often
/// as it likes without disturbing the session.
struct WebConsoleHost: NSViewRepresentable {
  /// The App-owned session. Pass the same instance for the App's lifetime.
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
