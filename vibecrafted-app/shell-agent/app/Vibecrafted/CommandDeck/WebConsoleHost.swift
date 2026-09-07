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
  /// The endpoint currently applied. Used to keep `updateNSView` idempotent.
  private(set) var appliedEndpoint: URL?
  private(set) var runtimeOrigin: WebRuntimeOrigin?

  private let downloads: WebDownloadCoordinator
  /// Guards the single automatic reload after a content-process death, so a
  /// repeatedly crashing page can never become a reload loop.
  private var didAutoRecoverFromTermination = false

  init(events: WebConsoleEvents = WebConsoleEvents()) {
    self.events = events
    self.downloads = WebDownloadCoordinator()
    self.webView = WKWebView(frame: .zero, configuration: Self.makeConfiguration())
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

  private static func makeConfiguration() -> WKWebViewConfiguration {
    let configuration = WKWebViewConfiguration()
    // One shared, persistent store. Cookies and HTTP authentication survive a
    // window close and a server restart, which is what makes this a session
    // rather than a page load.
    configuration.websiteDataStore = .default()
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
  /// Idempotent by contract: applying the same endpoint twice does nothing at
  /// all, so `updateNSView` can be called as often as SwiftUI likes. A genuine
  /// re-connection is an explicit act — see `retry()`.
  func apply(endpoint: URL?) {
    guard let endpoint else {
      // No endpoint yet: native recovery owns that state; do not blank the
      // canvas or tear down the session.
      return
    }
    guard appliedEndpoint != endpoint else { return }
    guard let origin = WebRuntimeOrigin(url: endpoint) else {
      events.navigationBlocked(endpoint, "the resolved endpoint is not an http(s) URL")
      return
    }
    appliedEndpoint = endpoint
    runtimeOrigin = origin
    didAutoRecoverFromTermination = false
    load(endpoint)
  }

  /// Re-requests the current endpoint after a failure or a server restart.
  /// Safe to call from a retry button; does nothing before a first endpoint.
  func retry() {
    guard let endpoint = appliedEndpoint else { return }
    didAutoRecoverFromTermination = false
    load(endpoint)
  }

  private func load(_ url: URL) {
    updateState(.loading(url))
    var request = URLRequest(url: url)
    // A restarted server must not be answered out of the cache.
    request.cachePolicy = .reloadIgnoringLocalCacheData
    webView.load(request)
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
    // Anything WebKit cannot render becomes an explicit download instead of a
    // blank page.
    navigationResponse.canShowMIMEType ? .allow : .download
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
  }

  func webView(
    _ webView: WKWebView,
    navigationResponse: WKNavigationResponse,
    didBecome download: WKDownload
  ) {
    downloads.take(download, relativeTo: webView.window, runtime: runtimeOrigin)
  }

  func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
    didAutoRecoverFromTermination = false
    // Never invent a URL to report success with; if WebKit has none and no
    // endpoint was applied, there is nothing honest to say.
    guard let url = webView.url ?? appliedEndpoint else { return }
    updateState(.loaded(url))
  }

  func webView(
    _ webView: WKWebView,
    didFailProvisionalNavigation navigation: WKNavigation!,
    withError error: Error
  ) {
    report(error)
  }

  func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
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
    guard !didAutoRecoverFromTermination, let endpoint = appliedEndpoint else { return }
    // Exactly one automatic recovery; anything further is the user's call.
    didAutoRecoverFromTermination = true
    load(endpoint)
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
  /// The endpoint currently resolved by the App, or `nil` while unknown.
  let endpoint: URL?

  func makeNSView(context: Context) -> WebConsoleContainerView {
    let container = WebConsoleContainerView()
    container.setAccessibilityElement(false)
    session.attach(to: container)
    session.apply(endpoint: endpoint)
    return container
  }

  /// Idempotent: re-attaching to the same container and re-applying the same
  /// endpoint are both no-ops, so a redraw never reloads the page or drops the
  /// web session.
  func updateNSView(_ nsView: WebConsoleContainerView, context: Context) {
    session.attach(to: nsView)
    session.apply(endpoint: endpoint)
  }
}
