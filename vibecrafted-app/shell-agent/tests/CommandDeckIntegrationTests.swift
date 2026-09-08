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


  /// Tab-role and MIME contracts: a UI route and an API endpoint on the same
  /// origin are told apart by the response, never by guessing from the path.
  static func tabPolicyContract(_ endpoint: URL) throws {
    let origin = WebRuntimeOrigin(url: endpoint)!
    let api = URL(string: "/api/scaffold/artifacts?plan_id=x", relativeTo: endpoint)!.absoluteURL
    let page = URL(string: "/scaffold", relativeTo: endpoint)!.absoluteURL
    func response(_ url: URL, mime: String?, role: WebTabRole, mainFrame: Bool = true) -> WebResponseDecision {
      WebNavigationPolicy.decideResponse(url: url, runtime: origin, isMainFrame: mainFrame,
        canShowMIMEType: true, statusCode: 200, mimeType: mime, role: role)
    }
    try require(response(api, mime: "application/json", role: .console) == .divertToReferenceTab,
      "Console let a JSON endpoint replace its document")
    try require(response(api, mime: "text/plain; charset=utf-8", role: .console) == .divertToReferenceTab,
      "Console let a plain-text machine document replace its document")
    try require(response(page, mime: "text/html; charset=utf-8", role: .console) == .allowInApp,
      "Console refused its own HTML route")
    try require(response(api, mime: "application/json", role: .console, mainFrame: false) == .allowInApp,
      "Console blocked a same-origin JSON sub-frame; API requests must not be globally blocked")
    try require(response(api, mime: "application/json", role: .reference) == .allowInApp,
      "Reference tab refused the machine document it exists for")
    try require(response(api, mime: "application/json", role: .tool) == .allowInApp,
      "Tool tab refused a machine document")
    try require(response(api, mime: nil, role: .console) == .allowInApp,
      "An unknown MIME was treated as a machine document")
    guard case .block = WebNavigationPolicy.decideResponse(
      url: URL(string: "https://foreign.example/landing"), runtime: origin, isMainFrame: true,
      canShowMIMEType: true, statusCode: 200, mimeType: "text/html", role: .tool)
    else { throw Failure(message: "Tool tab followed a foreign main-frame redirect") }
    try require(WebNavigationPolicy.isInteractiveDocumentMIME("application/xhtml+xml")
      && !WebNavigationPolicy.isInteractiveDocumentMIME("application/x-ndjson"), "MIME classification")

    // target=_blank / window.open: a native tab, never a second WebKit view.
    try require(WebNavigationPolicy.decideNewWindow(url: page, runtime: origin, role: .console,
      shouldPerformDownload: false) == .openInTab(page, .tool), "Same-origin _blank did not become a tool tab")
    try require(WebNavigationPolicy.decideNewWindow(url: page, runtime: origin, role: .tool,
      shouldPerformDownload: false) == .openInTab(page, .tool), "Tool tab _blank policy")
    let foreign = URL(string: "https://example.com/")!
    try require(WebNavigationPolicy.decideNewWindow(url: foreign, runtime: origin, role: .console,
      shouldPerformDownload: false) == .openExternally(foreign), "Foreign _blank did not leave via the system browser")
    guard case .block = WebNavigationPolicy.decideNewWindow(url: page, runtime: origin, role: .reference,
      shouldPerformDownload: false) else { throw Failure(message: "Reference tab opened a window") }
    guard case .block = WebNavigationPolicy.decideNewWindow(url: URL(string: "javascript:alert(1)"),
      runtime: origin, role: .console, shouldPerformDownload: false)
    else { throw Failure(message: "javascript: window.open escaped") }

    // A local report tab shows exactly one file.
    let document = URL(fileURLWithPath: "/fixture/.aicx/aicx-dashboard.html")
    try require(WebNavigationPolicy.decideLocalDocumentNavigation(
      url: URL(string: "file:///fixture/.aicx/aicx-dashboard.html#sessions"), document: document,
      isMainFrame: true, shouldPerformDownload: false) == .allowInApp, "Fragment of the local document refused")
    guard case .block = WebNavigationPolicy.decideLocalDocumentNavigation(
      url: URL(fileURLWithPath: "/fixture/.aicx/config.toml"), document: document,
      isMainFrame: true, shouldPerformDownload: false)
    else { throw Failure(message: "Local document tab reached a sibling file") }
    guard case .block = WebNavigationPolicy.decideLocalDocumentNavigation(
      url: URL(string: "https://example.com/frame"), document: document,
      isMainFrame: false, shouldPerformDownload: false)
    else { throw Failure(message: "Local document tab embedded a frame") }
    try require(WebNavigationPolicy.decideLocalDocumentNavigation(
      url: foreign, document: document, isMainFrame: true, shouldPerformDownload: false) == .openExternally(foreign),
      "Local document link did not leave via the system browser")
  }

  /// Destinations resolve against runtime truth or say why they cannot.
  static func destinationContract(_ endpoint: URL) throws {
    let home = URL(fileURLWithPath: "/fixture/home", isDirectory: true)
    func context(endpoint: URL?, env: [String: String] = [:], exists: @escaping (URL) -> Bool = { _ in false })
      -> ToolDestinationContext {
      ToolDestinationContext(runtimeEndpoint: endpoint, homeDirectory: home, environment: env, fileExists: exists)
    }
    let report = ToolDestination.named("loctree-report")!
    guard case .available(let url, .runtime(let origin)) = report.resolve(in: context(endpoint: endpoint)) else {
      throw Failure(message: "Loctree report did not resolve against the connected runtime")
    }
    try require(url.host == endpoint.host && url.port == endpoint.port && url.path == "/structure/report"
      && origin == WebRuntimeOrigin(url: endpoint)!, "Loctree report route was not derived from the endpoint")
    guard case .unavailable = report.resolve(in: context(endpoint: nil)) else {
      throw Failure(message: "Loctree report claimed availability without a runtime")
    }

    let dashboard = ToolDestination.named("aicx-dashboard")!
    guard case .unavailable(let reason) = dashboard.resolve(in: context(endpoint: endpoint)),
      reason.contains("/fixture/home/.aicx/aicx-dashboard.html"), reason.contains("aicx dashboard")
    else { throw Failure(message: "Missing AICX dashboard was not reported with its path and the owner command") }
    let generated = URL(fileURLWithPath: "/fixture/home/.aicx/aicx-dashboard.html")
    guard case .available(let file, .localDocument(let scoped)) = dashboard.resolve(
      in: context(endpoint: nil, exists: { $0.path == generated.path })), file == scoped, file.path == generated.path
    else { throw Failure(message: "Generated AICX dashboard did not resolve as a local document") }
    let overridden = URL(fileURLWithPath: "/fixture/aicx-home/aicx-dashboard.html")
    guard case .available(let overrideFile, _) = dashboard.resolve(in: context(endpoint: nil,
      env: ["AICX_HOME": "/fixture/aicx-home"], exists: { $0.path == overridden.path })),
      overrideFile.path == overridden.path
    else { throw Failure(message: "AICX_HOME override was ignored") }

    guard case .unavailable(let slackReason) = ToolDestination.named("slack-agent-console")!.resolve(
      in: context(endpoint: endpoint, exists: { _ in true })), slackReason.contains("make portal")
    else { throw Failure(message: "Slack console pretended to have a launch contract") }
    try require(!ToolDestination.catalog.contains { destination in
      if case .runtimeRoute(let path) = destination.target { return path.contains("://") || path.contains(":") }
      return false
    }, "A destination hardcodes a host or port")
  }

  static func hasFixtureCookie(_ store: WKHTTPCookieStore) async -> Bool {
    await withCheckedContinuation { continuation in
      store.getAllCookies { cookies in
        continuation.resume(returning: cookies.contains { $0.name == "w3_session" })
      }
    }
  }

  static func evaluateString(_ script: String, in view: WKWebView) async throws -> String {
    try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<String, Error>) in
      view.evaluateJavaScript(script) { value, error in
        if let error { continuation.resume(throwing: error) }
        else { continuation.resume(returning: value as? String ?? "") }
      }
    }
  }

  static func tick() async throws {
    try await Task.sleep(for: .milliseconds(50))
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


  /// Real WKWebView proof: an endpoint link and a `_blank` link leave the
  /// console document, its DOM edit state and its history untouched; tabs are
  /// deduplicated; Home/back in a tool tab never move the console; closing a
  /// tab keeps the console and its web view.
  static func tabsContract(_ endpoint: URL) async throws {
    let model = AppModel()
    let console = WebConsoleSession(websiteDataStore: .nonPersistent(), downloadDestinationProvider: { _, _, _ in nil })
    var opened: [(URL, WebTabRole)] = []
    var blocked: [String] = []
    var externals: [URL] = []
    console.events.openInTab = { opened.append(($0, $1)) }
    console.events.navigationBlocked = { _, reason in blocked.append(reason) }
    console.events.openExternally = { externals.append($0) }
    let actions = Actions()
    let controller = MainWindowController(model: model, session: console, actions: actions,
      openExternally: { externals.append($0) })
    controller.showWindow(nil)
    let consoleView = console.webView
    console.apply(endpoint: endpoint)
    try await waitFor { if case .loaded = console.loadState { return true }; return false }
    console.navigate(path: "/scaffold")
    try await waitFor { if case .loaded(let url) = console.loadState { return url.path == "/scaffold" }; return false }
    try await evaluate("document.getElementById('draft').value = 'edited-draft'", in: consoleView)
    try await waitFor { console.navigation.canGoBack }

    // 1. Endpoint link: JSON must not replace the Scaffold document.
    try await evaluate("document.getElementById('api').click()", in: consoleView)
    try await waitFor { opened.count == 1 }
    try require(opened[0].1 == .reference && opened[0].0.path == "/api/scaffold/artifacts",
      "JSON endpoint was not diverted to a reference tab")
    try await tick()
    try require(consoleView.url?.path == "/scaffold", "Endpoint link replaced the Scaffold document")
    try require(console.loadState == .loaded(consoleView.url!), "Diverted endpoint left the console stranded")
    try require(try await evaluateString("document.getElementById('draft').value", in: consoleView) == "edited-draft",
      "Endpoint link discarded Scaffold edit state")
    try require(model.presentation.exposesCanvas || model.state == .bootstrapping,
      "Diverted endpoint hid the canvas")

    // 2. target=_blank: a tool tab request, same document kept.
    try await evaluate("document.getElementById('blank').click()", in: consoleView)
    try await waitFor { opened.count == 2 }
    try require(opened[1].1 == .tool && opened[1].0.path == "/workspaces", "_blank did not request a tool tab")
    try await evaluate("window.open('/runs')", in: consoleView)
    try await waitFor { opened.count == 3 }
    try require(opened[2].0.path == "/runs", "window.open did not request a tool tab")
    try require(consoleView.url?.path == "/scaffold" && console.webView === consoleView,
      "_blank replaced or recreated the console view")
    try require(try await evaluateString("document.getElementById('draft').value", in: consoleView) == "edited-draft",
      "_blank discarded Scaffold edit state")
    print("Witness: JSON endpoint and _blank kept the Scaffold DOM, edit state and history in the console")

    // 3. Coordinator: one window per destination, focus instead of twins.
    let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    try FileManager.default.createDirectory(at: home.appendingPathComponent(".aicx"), withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: home) }
    let coordinator = NativeTabCoordinator(
      anchorWindow: { controller.window }, openExternally: { externals.append($0) },
      websiteDataStore: .nonPersistent(), homeDirectory: home, environment: [:],
      fileExists: { FileManager.default.fileExists(atPath: $0.path) })
    coordinator.apply(runtimeEndpoint: endpoint)
    let apiURL = opened[0].0
    try require(coordinator.open(.url(apiURL, .reference)) == .opened(NativeTabCoordinator.key(for: apiURL, role: .reference)),
      "Reference tab did not open")
    try require(coordinator.tabs.count == 1, "Reference tab count")
    let reference = coordinator.tabs.values.first!
    try require(reference.session.role == .reference && reference.session.webView !== consoleView,
      "Reference tab reused the console web view")
    try require(!reference.session.webView.configuration.defaultWebpagePreferences.allowsContentJavaScript,
      "Reference tab runs page script")
    try await waitFor { if case .loaded(let url) = reference.session.loadState { return url.path == "/api/scaffold/artifacts" }; return false }
    try require(coordinator.open(.url(apiURL, .reference)) == .focused(reference.key) && coordinator.tabs.count == 1,
      "Repeated destination opened a twin tab")
    try require(reference.window?.tabGroup != nil && reference.window?.tabGroup === controller.window?.tabGroup,
      "Reference tab is not in the console's native tab group")

    let workspaces = opened[1].0
    guard case .opened(let toolKey) = coordinator.open(.url(workspaces, .tool)) else {
      throw Failure(message: "Tool tab did not open")
    }
    let tool = coordinator.tabs[toolKey]!
    try require(coordinator.tabs.count == 2 && tool.session.role == .tool, "Tool tab identity")
    try await waitFor { if case .loaded(let url) = tool.session.loadState { return url.path == "/workspaces" }; return false }
    try require(tool.model.presentation.exposesCanvas, "Tool tab did not expose its canvas")

    // 4. Home/back in the tool tab move only the tool tab.
    tool.session.navigate(path: "/runs")
    try await waitFor { if case .loaded(let url) = tool.session.loadState { return url.path == "/runs" }; return false }
    try await waitFor { tool.session.navigation.canGoBack }
    tool.navigate(.back)
    try await waitFor { tool.session.webView.url?.path == "/workspaces" }
    try await waitFor { tool.session.navigation.canGoForward }
    tool.navigate(.home)
    try await waitFor { if case .loaded(let url) = tool.session.loadState { return url.path == "/workspaces" }; return false }
    try require(consoleView.url?.path == "/scaffold" && console.loadState == .loaded(consoleView.url!),
      "Tool tab Home/back mutated the console tab")
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts", "Tool tab Home mutated the reference tab")
    controller.navigate(.home)
    try await waitFor { if case .loaded(let url) = console.loadState { return url.path == "/" }; return false }
    try require(tool.session.webView.url?.path == "/workspaces", "Console Home mutated the tool tab")
    try require(console.navigation.canGoBack, "Console Home discarded history")
    controller.navigate(.back)
    try await waitFor { consoleView.url?.path == "/scaffold" }
    print("Witness: Home/back/forward targeted the selected tab only, in both directions")

    // 5. Closing a tool tab keeps the console, its window and its web view.
    let consoleWindow = controller.window
    tool.close()
    try await waitFor { coordinator.tabs.count == 1 }
    try require(controller.window === consoleWindow && console.webView === consoleView
      && consoleView.url?.path == "/scaffold", "Closing a tool tab disturbed the console")
    try require(coordinator.tabs[reference.key] != nil, "Closing one tab closed a sibling")

    // 6. Destinations: unavailable is said, available is one local file.
    let dashboard = ToolDestination.named("aicx-dashboard")!
    guard case .unavailable(let reason) = coordinator.open(.destination(dashboard)), reason.contains("aicx dashboard") else {
      throw Failure(message: "Missing dashboard opened a fake tab")
    }
    try require(coordinator.tabs.count == 1, "Unavailable destination created a tab")
    let file = home.appendingPathComponent(".aicx/aicx-dashboard.html")
    try "<!doctype html><title>AICX fixture</title><p id='ok'>dashboard</p>".write(to: file, atomically: true, encoding: .utf8)
    try require(coordinator.open(.destination(dashboard)) == .opened("aicx-dashboard"), "Generated dashboard did not open")
    let local = coordinator.tabs["aicx-dashboard"]!
    var localBlocked: [String] = []
    local.session.events.navigationBlocked = { _, reason in localBlocked.append(reason) }
    try await waitFor { if case .loaded = local.session.loadState { return true }; return false }
    try require(local.session.scope == .localDocument(file.standardizedFileURL), "Local document scope")
    // WebKit itself refuses file→file moves from a page loaded with read
    // access to one file, so the policy may never be consulted; the proof is
    // the outcome: the tab still shows its one document and nothing else.
    try await evaluate("location.href = 'file:///etc/hosts'", in: local.session.webView)
    try await tick()
    try await tick()
    try require(local.session.webView.url?.standardizedFileURL.path == file.standardizedFileURL.path,
      "Local document tab navigated to another file (blocked=\(localBlocked))")
    try require(try await evaluateString("document.getElementById('ok').textContent", in: local.session.webView) == "dashboard",
      "Local document tab lost its document")
    try require(local.session.loadState == .loaded(file.standardizedFileURL), "Local document tab state drifted")
    try require(coordinator.open(.destination(dashboard)) == .focused("aicx-dashboard"), "Dashboard tab duplicated")
    let slack = ToolDestination.named("slack-agent-console")!
    guard case .unavailable = coordinator.open(.destination(slack)) else {
      throw Failure(message: "Slack console opened without a contract")
    }

    // 7. Losing the runtime: runtime tabs say so, the local document does not care.
    coordinator.apply(runtimeEndpoint: nil)
    try require(reference.model.presentation.phase == .recovering && !reference.model.presentation.exposesCanvas,
      "Runtime tab kept a cached page after the endpoint vanished")
    try require(local.model.presentation.exposesCanvas, "Local document tab was tied to the runtime")
    coordinator.apply(runtimeEndpoint: endpoint)
    try await waitFor { reference.model.presentation.exposesCanvas }
    print("Witness: destinations resolve honestly, local report tab is confined to one file, runtime loss is shown")

    // 8. One chrome: the bridged toolbar exists at compact and regular widths; no content chrome row.
    try await waitFor { controller.window?.toolbar != nil }
    let toolbar = controller.window!.toolbar!
    let identifiersRegular = toolbar.items.map(\.itemIdentifier)
    controller.window?.setContentSize(NSSize(width: 800, height: 600))
    try await tick()
    try require(controller.window?.toolbar === toolbar && toolbar.items.map(\.itemIdentifier) == identifiersRegular,
      "Compact width changed the toolbar item set instead of overflowing")
    try require(!identifiersRegular.isEmpty, "Toolbar bridged no items")
    for tab in coordinator.tabs.values { tab.close() }
    controller.close()
    try require(blocked.isEmpty, "Console blocked navigations unexpectedly: \(blocked)")
    print("Witness: unified toolbar bridged into the window; item set stable across widths")
  }

  static func main() async throws {
    _ = NSApplication.shared
    let endpoint = URL(string: CommandLine.arguments[1])!
    try require(endpoint.scheme == "http" && endpoint.host == "127.0.0.1" && endpoint.port != nil,
      "Only a loopback fixture endpoint is permitted")
    try stateContract(endpoint)
    try policyContract(endpoint)
    try authenticationAndDownloadContract(endpoint)
    try tabPolicyContract(endpoint)
    try destinationContract(endpoint)
    try await webContract(endpoint)
    try await tabsContract(endpoint)
    print("CommandDeckIntegrationTests passed")
  }
}
