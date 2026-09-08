import AppKit
import Foundation
import WebKit

/// W3-only executable harness. All network traffic targets the fixture passed
/// by test_command_deck_integration.py; no installed runtime is consulted.
@main
@MainActor
struct CommandDeckIntegrationTests {
  struct Failure: Error { let message: String }
  final class Actions: CommandDeckActionHandling {
    var received: [CommandDeckChromeAction] = []
    func handle(_ action: CommandDeckChromeAction) { received.append(action) }
  }
  @MainActor
  final class DownloadChoice {
    var cancel = false
  }

  static func require(_ value: Bool, _ message: String) throws {
    if !value { throw Failure(message: message) }
  }

  static func waitFor(line: UInt = #line, _ condition: () -> Bool) async throws {
    let deadline = Date().addingTimeInterval(10)
    while !condition() {
      if Date() > deadline { throw Failure(message: "Fixture navigation timed out at line \(line)") }
      try await Task.sleep(for: .milliseconds(25))
    }
  }

  static func stateContract(_ endpoint: URL) throws {
    var published: URL? = endpoint
    let resolver = RuntimeEndpointResolver { _ in
      ServerNavigationState(server: published,
        workspaces: published?.appendingPathComponent("workspaces"),
        unavailableReason: published == nil ? "fixture offline" : nil)
    }
    let model = AppModel(endpointResolver: resolver)
    var endpoints: [URL?] = []
    model.endpointDidChange = { endpoints.append($0) }
    model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
    try require(model.state == .connecting, "Caretaker availability was mistaken for web success")
    model.receiveWebState(.failed(url: endpoint, reason: "fixture failed"))
    model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
    try require(model.state == .recovering("fixture failed"), "Polling erased the web failure")
    try require(endpoints.count == 1, "Same endpoint reloaded on each poll")
    model.receiveWebState(.loaded(endpoint.appendingPathComponent("workspaces")))
    try require(model.presentation.exposesCanvas, "Loaded page did not expose the canvas")
    model.receiveWebState(.loading(endpoint.appendingPathComponent("runs")))
    try require(model.presentation.exposesCanvas && model.isNavigating,
      "Ordinary internal navigation hid the retained canvas")
    model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
    try require(model.presentation.exposesCanvas, "Caretaker poll hid in-session navigation")
    model.receiveWebState(.failed(url: endpoint, reason: "HTTP failure"))
    try require(!model.presentation.exposesCanvas, "HTTP failure retained online canvas")
    model.receiveWebState(.loading(endpoint))
    try require(!model.presentation.exposesCanvas, "Retry displayed failed content as online")
    model.receiveWebState(.loaded(endpoint))
    published = nil
    model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
    model.receiveWebState(.loaded(endpoint))
    try require(!model.presentation.exposesCanvas, "Stale finish bypassed unavailable owner")
    published = endpoint
    model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
    try require(endpoints.count == 3 && endpoints[1] == nil && endpoints[2] == endpoint,
      "Same-endpoint recovery did not reapply the endpoint")
    try require(model.state == .connecting, "Reappearing endpoint reused stale loaded state")
    model.refreshEndpoint(caretakerData: nil, runtimeReady: false)
    try require(model.endpoint == nil && !model.presentation.exposesCanvas, "Missing runtime stayed online")
  }

  static func policyContract(_ endpoint: URL) throws {
    let origin = WebRuntimeOrigin(url: endpoint)!
    for path in ["/", "/workspaces", "/run/example"] {
      let url = URL(string: path, relativeTo: endpoint)!.absoluteURL
      try require(WebNavigationPolicy.decide(url: url, runtime: origin,
        isMainFrame: true, shouldPerformDownload: false) == .allowInApp, "Internal route escaped")
    }
    let external = URL(string: "https://example.com/")!
    try require(WebNavigationPolicy.decide(url: external, runtime: origin,
      isMainFrame: true, shouldPerformDownload: false) == .openExternally(external), "External URL policy")
    for value in ["file:///tmp/private", "javascript:alert(1)", "data:text/html,x", "mailto:a@example.com"] {
      let decision = WebNavigationPolicy.decide(url: URL(string: value), runtime: origin,
        isMainFrame: true, shouldPerformDownload: true)
      guard case .block = decision else { throw Failure(message: "Download flag bypassed scheme policy") }
    }

    // The caretaker may select either loopback or a Tailscale IP literal for
    // its local HTTP server. Both remain the exact in-app runtime origin.
    for value in ["http://127.0.0.1:4107/console", "http://100.64.0.7:4107/console"] {
      let runtimeURL = URL(string: value)!
      let runtime = WebRuntimeOrigin(url: runtimeURL)!
      try require(WebNavigationPolicy.decide(url: runtimeURL, runtime: runtime,
        isMainFrame: true, shouldPerformDownload: false) == .allowInApp,
        "Caretaker-selected local runtime was rejected: \(value)")
    }

    let foreignHTTPS = URL(string: "https://foreign.example/frame")!
    try require(WebNavigationPolicy.decide(url: foreignHTTPS, runtime: origin,
      isMainFrame: false, shouldPerformDownload: false) == .allowInApp,
      "Secure foreign sub-frame was rejected")
    let foreignHTTP = URL(string: "http://foreign.example/frame")!
    guard case .block = WebNavigationPolicy.decide(url: foreignHTTP, runtime: origin,
      isMainFrame: false, shouldPerformDownload: false)
    else { throw Failure(message: "Foreign cleartext sub-frame bypassed runtime boundary") }
  }

  static func authenticationAndDownloadContract(_ endpoint: URL) throws {
    let origin = WebRuntimeOrigin(url: endpoint)!
    try require(WebNavigationPolicy.decideAuthenticationChallenge(
      method: NSURLAuthenticationMethodServerTrust, host: origin.host, port: origin.port,
      scheme: origin.scheme, runtime: origin) == .performDefaultHandling,
      "TLS bypassed system trust")
    let foreign = WebNavigationPolicy.decideAuthenticationChallenge(
      method: NSURLAuthenticationMethodHTTPBasic, host: "foreign.example", port: origin.port,
      scheme: origin.scheme, runtime: origin)
    guard case .cancel = foreign else { throw Failure(message: "Credentials escaped runtime origin") }
    let downgrade = WebNavigationPolicy.decideAuthenticationChallenge(
      method: NSURLAuthenticationMethodHTTPBasic, host: origin.host, port: origin.port,
      scheme: "https", runtime: origin)
    guard case .cancel = downgrade else { throw Failure(message: "Credential scheme mismatch allowed") }
    let blob = URL(string: "blob:\(endpoint.absoluteString)fixture-id")!
    try require(WebNavigationPolicy.decideResponse(url: blob, runtime: origin,
      isMainFrame: true, canShowMIMEType: false, statusCode: nil) == .startDownload,
      "Same-origin blob rejected at response stage")
    let foreignBlob = WebNavigationPolicy.decideResponse(
      url: URL(string: "blob:https://foreign.example/id"), runtime: origin,
      isMainFrame: true, canShowMIMEType: false, statusCode: nil)
    guard case .block = foreignBlob else { throw Failure(message: "Foreign blob admitted") }
    try require(WebNavigationPolicy.decideResponse(url: endpoint, runtime: origin,
      isMainFrame: true, canShowMIMEType: false, statusCode: 200) == .startDownload,
      "Normal download rejected")
    try require(WebNavigationPolicy.decideResponse(url: URL(string: "https://foreign.example/frame"),
      runtime: origin, isMainFrame: false, canShowMIMEType: true, statusCode: 200) == .allowInApp,
      "Action/response subframe policies disagree")
    let directory = URL(fileURLWithPath: "/fixture/downloads")
    let destination = WebNavigationPolicy.downloadDestination(directory: directory,
      suggestedFilename: "report.txt", fileExists: { $0.lastPathComponent == "report.txt" })
    try require(destination == .save(directory.appendingPathComponent("report-1.txt")),
      "Download overwrote an existing file")
    let name = WebNavigationPolicy.sanitizedDownloadFilename("../private/secret")
    try require(name != nil && !name!.contains("/") && !name!.hasPrefix("."), "Unsafe filename")
  }

  static func hasFixtureCookie(_ store: WKHTTPCookieStore) async -> Bool {
    await withCheckedContinuation { continuation in
      store.getAllCookies { cookies in
        continuation.resume(returning: cookies.contains { $0.name == "w3_session" })
      }
    }
  }

  static func evaluate(_ script: String, in view: WKWebView) async throws {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
      view.evaluateJavaScript(script) { _, error in
        if let error { continuation.resume(throwing: error) }
        else { continuation.resume() }
      }
    }
  }

  static func webContract(_ endpoint: URL) async throws {
    let model = AppModel()
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let downloadChoice = DownloadChoice()
    let session = WebConsoleSession(websiteDataStore: .nonPersistent(), downloadDestinationProvider: { name, _, _ in
      downloadChoice.cancel ? nil : directory.appendingPathComponent(name)
    })
    var downloaded: [URL] = []
    var downloadErrors: [String] = []
    session.events.downloadFinished = { downloaded.append($0) }
    session.events.downloadFailed = { downloadErrors.append($0) }
    var interruptions = 0
    session.events.stateDidChange = { state in
      if case .interrupted = state { interruptions += 1 }
    }
    let original = session.webView
    session.apply(endpoint: endpoint)
    try await waitFor { if case .loaded = session.loadState { return true }; return false }
    session.navigate(path: "/workspaces?filter=active#fixture")
    try await waitFor { if case .loaded(let url) = session.loadState { return url.path == "/workspaces" }; return false }
    let cookieBefore = await hasFixtureCookie(session.webView.configuration.websiteDataStore.httpCookieStore)
    try require(cookieBefore, "Fixture cookie missing")
    let firstContainer = NSView()
    let secondContainer = NSView()
    session.attach(to: firstContainer)
    session.attach(to: secondContainer)
    try require(session.webView === original && session.webView.superview === secondContainer,
      "Mounting recreated the web session")
    session.apply(endpoint: nil)
    session.apply(endpoint: endpoint)
    try await waitFor { if case .loaded(let url) = session.loadState { return url.query == "filter=active" }; return false }
    try require(session.webView.url?.fragment == "fixture", "Recovery lost the route fragment")
    let cookieAfter = await hasFixtureCookie(session.webView.configuration.websiteDataStore.httpCookieStore)
    try require(cookieAfter, "Recovery discarded cookies")
    session.navigate(path: "/failure")
    try await waitFor { if case .failed = session.loadState { return true }; return false }
    session.retry()
    try await waitFor { if case .failed = session.loadState { return true }; return false }
    session.navigate(path: "/workspaces")
    try await waitFor { if case .loaded = session.loadState { return true }; return false }
    // Obtain ownership directly from this nonpersistent fixture view. A
    // requested WebKit termination suppresses the crash callback, so inject
    // an actual process loss into that exact process instead.
    // Source: WebKit/Source/WebKit/UIProcess/API/Cocoa/WKWebViewPrivate.h
    let processIdentifier = NSSelectorFromString("_webProcessIdentifier")
    try require(session.webView.responds(to: processIdentifier),
      "WebKit process-loss fixture SPI unavailable; witness cannot be accepted")
    let fixturePID = (session.webView.value(forKey: "_webProcessIdentifier") as? NSNumber)?.int32Value ?? 0
    try require(fixturePID > 1 && fixturePID != getpid(), "Invalid fixture process identity")
    try require(kill(fixturePID, SIGKILL) == 0, "Fixture process-loss injection failed")
    try await waitFor { interruptions == 1 }
    try await waitFor { if case .loaded(let url) = session.loadState { return url.path == "/workspaces" }; return false }
    try require(session.webView === original, "Process recovery replaced the retained web view")
    let recoveredPID = (session.webView.value(forKey: "_webProcessIdentifier") as? NSNumber)?.int32Value ?? 0
    try require(recoveredPID > 1 && recoveredPID != fixturePID, "Content process was not replaced")
    let cookieAfterProcessLoss = await hasFixtureCookie(session.webView.configuration.websiteDataStore.httpCookieStore)
    try require(cookieAfterProcessLoss, "Process recovery discarded the original cookie")
    print("Witness: real WebKit process loss, delegate interruption, retained-view route recovery and cookie retention")
    try await evaluate("document.getElementById('download').click()", in: session.webView)
    try await waitFor { downloaded.count == 1 }
    try require(try String(contentsOf: downloaded[0], encoding: .utf8) == "server-download", "Server download bytes")
    try await evaluate("document.getElementById('blob').click()", in: session.webView)
    try await waitFor { downloaded.count == 2 }
    try require(try String(contentsOf: downloaded[1], encoding: .utf8) == "blob-download", "Blob download bytes")
    downloadChoice.cancel = true
    try await evaluate("document.getElementById('blob').click()", in: session.webView)
    try await waitFor { !downloadErrors.isEmpty }
    try require(downloaded.count == 2, "Cancelled download wrote a file")
    print("Witness: server download bytes, blob download bytes and blob cancellation")
    let actions = Actions()
    let controller = MainWindowController(model: model, session: session, actions: actions)
    let window = controller.window
    controller.close()
    controller.showWindow(nil)
    try require(controller.window === window && session.webView === original, "Reopen recreated native or web window")
    print("Witness: remount, reconnect, query/fragment, HTTP failure/retry and close/reopen identity")
    controller.close()
  }

  static func main() async throws {
    _ = NSApplication.shared
    let endpoint = URL(string: CommandLine.arguments[1])!
    try require(endpoint.scheme == "http" && endpoint.host == "127.0.0.1" && endpoint.port != nil,
      "Only a loopback fixture endpoint is permitted")
    try stateContract(endpoint)
    try policyContract(endpoint)
    try authenticationAndDownloadContract(endpoint)
    try await webContract(endpoint)
    print("CommandDeckIntegrationTests passed")
  }
}
