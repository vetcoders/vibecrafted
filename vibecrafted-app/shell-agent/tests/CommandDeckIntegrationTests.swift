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

    // Slack console: owner-backed through the operator's config.toml [tools]
    // table. Unset reads as a boundary that names the owner and the file.
    let slack = ToolDestination.named("slack-agent-console")!
    func configured(_ toml: String?, xdg: String? = nil) -> ToolDestinationContext {
      var env: [String: String] = [:]
      if let xdg { env["XDG_CONFIG_HOME"] = xdg }
      return ToolDestinationContext(runtimeEndpoint: endpoint, homeDirectory: home, environment: env,
        fileExists: { _ in true }, readConfiguration: { _ in toml })
    }
    guard case .unavailable(let slackReason) = slack.resolve(in: configured(nil)),
      slackReason.contains("vc-slack-agent"), slackReason.contains("/fixture/home/.config/vibecrafted/config.toml"),
      slackReason.contains("[tools.slack-console]")
    else { throw Failure(message: "Unconfigured Slack console did not name its owner and the config file") }
    guard case .unavailable(let onlyServer) = slack.resolve(in: configured("[server]\nport = 3025\n")),
      onlyServer.contains("not configured")
    else { throw Failure(message: "A config.toml with only [server] was not read as unconfigured") }
    guard case .available(let consoleURL, .service(let consoleOrigin)) = slack.resolve(in: configured(
      "# operator config\n[server]\nport = 3025\n\n[tools.slack-console]\nurl = \"http://100.82.232.70:4300/console\" # served by make portal-preview\n")),
      consoleURL.absoluteString == "http://100.82.232.70:4300/console",
      consoleOrigin == WebRuntimeOrigin(url: URL(string: "http://100.82.232.70:4300/")!)!
    else { throw Failure(message: "Configured Slack console did not resolve to a service scope on its own origin") }
    try require(ToolDestinationConfiguration.configurationFile(homeDirectory: home, environment: ["XDG_CONFIG_HOME": "/fixture/xdg"]).path
      == "/fixture/xdg/vibecrafted/config.toml", "XDG_CONFIG_HOME was ignored for config.toml")
    guard case .unavailable(let emptyReason) = slack.resolve(in: configured("[tools.slack-console]\nurl = \"\"\n")),
      emptyReason.contains("not configured")
    else { throw Failure(message: "An empty url was not read as unconfigured") }
    for invalid in [
      "[tools.slack-console]\nurl = \"ftp://x/console\"\n",
      "[tools.slack-console]\nurl = \"http://u:p@x/console\"\n",
      "[tools.slack-console]\nurl = \"http://x/console?token=1\"\n",
      "[tools.slack-console]\nurl = \"http://x/console#frag\"\n",
      "[tools.slack-console]\nurl = 4300\n",
      "[tools.slack-console]\nport = 4300\n",
      "[tools.portal]\nurl = \"http://x/\"\n",
      "[tools]\nslack-console = 1\n",
    ] {
      guard case .unavailable(let reason) = slack.resolve(in: configured(invalid)), reason.contains("invalid [tools] table")
      else { throw Failure(message: "Invalid [tools] contract was accepted: \(invalid)") }
    }
    try require(!ToolDestination.catalog.contains { destination in
      if case .runtimeRoute(let path) = destination.target { return path.contains("://") || path.contains(":") }
      if case .unavailable = destination.target { return true }
      return false
    }, "A destination hardcodes a host or port, or is registered as permanently unavailable")
    try require(report.isolatedContent && !dashboard.isolatedContent,
      "Generated report must be isolated; the AICX dashboard is a plain local document")
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
  static func tabsContract(_ endpoint: URL, reconnectEndpoint: URL) async throws {
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

    // 4. Home/back in the tool tab move only the tool tab. Home in a tab
    //    opened from a link is the runtime overview, never the URL that
    //    opened it; Back still reaches that page.
    tool.session.navigate(path: "/runs")
    try await waitFor { if case .loaded(let url) = tool.session.loadState { return url.path == "/runs" }; return false }
    try await waitFor { tool.session.navigation.canGoBack }
    tool.navigate(.back)
    try await waitFor { tool.session.webView.url?.path == "/workspaces" }
    try await waitFor { tool.session.navigation.canGoForward }
    tool.navigate(.home)
    try await waitFor { if case .loaded(let url) = tool.session.loadState { return url.path == "/" }; return false }
    try require(consoleView.url?.path == "/scaffold" && console.loadState == .loaded(consoleView.url!),
      "Tool tab Home/back mutated the console tab")
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts", "Tool tab Home mutated the reference tab")
    tool.navigate(.back)
    try await waitFor { tool.session.webView.url?.path == "/workspaces" }
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
    try require(coordinator.tabs.count == 2, "Unconfigured service created a tab")

    // 6b. The Loctree report is a generated document: its own tab, an
    //     ephemeral data store, the console's store untouched.
    let reportDestination = ToolDestination.named("loctree-report")!
    try require(coordinator.open(.destination(reportDestination)) == .opened("loctree-report"), "Report tab did not open")
    let report = coordinator.tabs["loctree-report"]!
    try require(!report.session.webView.configuration.websiteDataStore.isPersistent,
      "Generated report shares the console's persistent website data store")
    try require(report.session.webView !== consoleView && report.session.role == .tool, "Report tab identity")
    try require(report.session.scope == .runtime(WebRuntimeOrigin(url: endpoint)!), "Report tab scope")
    try require(coordinator.open(.destination(reportDestination)) == .focused("loctree-report"), "Report tab duplicated")

    // 6c. A configured service console: its own origin, its own store, no
    //     runtime coupling, foreign links leave through the system browser.
    let serviceHome = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    let configDir = serviceHome.appendingPathComponent(".config/vibecrafted", isDirectory: true)
    try FileManager.default.createDirectory(at: configDir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: serviceHome) }
    try "[tools.slack-console]\nurl = \"http://127.0.0.1:\(endpoint.port!)/console\"\n"
      .write(to: configDir.appendingPathComponent("config.toml"), atomically: true, encoding: .utf8)
    var serviceExternals: [URL] = []
    let serviceCoordinator = NativeTabCoordinator(
      anchorWindow: { controller.window }, openExternally: { serviceExternals.append($0) },
      websiteDataStore: .nonPersistent(), homeDirectory: serviceHome, environment: [:],
      fileExists: { FileManager.default.fileExists(atPath: $0.path) })
    // No runtime endpoint applied on purpose: a service does not need one.
    try require(serviceCoordinator.open(.destination(slack)) == .opened("slack-agent-console"),
      "Configured service did not open without a runtime")
    let service = serviceCoordinator.tabs["slack-agent-console"]!
    try require(service.session.scope == .service(WebRuntimeOrigin(url: endpoint)!), "Service tab scope")
    try require(!service.session.webView.configuration.websiteDataStore.isPersistent, "Service tab shares the console store")
    try await waitFor { if case .loaded(let url) = service.session.loadState { return url.path == "/console" }; return false }
    try require(service.model.presentation.exposesCanvas, "Service tab did not expose its canvas")
    service.session.navigate(path: "/console/wire")
    try await waitFor { if case .loaded(let url) = service.session.loadState { return url.path == "/console/wire" }; return false }
    service.navigate(.home)
    try await waitFor { if case .loaded(let url) = service.session.loadState { return url.path == "/console" }; return false }
    try await evaluate("location.href = 'https://example.com/foreign'", in: service.session.webView)
    try await waitFor { !serviceExternals.isEmpty }
    try require(serviceExternals.last?.host == "example.com" && service.session.webView.url?.path == "/console",
      "Foreign navigation from the service tab did not leave via the system browser")
    serviceCoordinator.apply(runtimeEndpoint: endpoint)
    serviceCoordinator.apply(runtimeEndpoint: nil)
    try require(service.model.presentation.exposesCanvas && service.session.webView.url?.path == "/console",
      "Service tab was tied to the runtime endpoint")
    try require(serviceCoordinator.open(.destination(slack)) == .focused("slack-agent-console"), "Service tab duplicated")
    service.close()

    // 7. Losing the runtime: runtime tabs say so, the local document does not care.
    coordinator.apply(runtimeEndpoint: nil)
    try require(reference.model.presentation.phase == .recovering && !reference.model.presentation.exposesCanvas,
      "Runtime tab kept a cached page after the endpoint vanished")
    try require(report.model.presentation.phase == .recovering, "Report tab kept a cached page after the endpoint vanished")
    try require(local.model.presentation.exposesCanvas, "Local document tab was tied to the runtime")
    coordinator.apply(runtimeEndpoint: endpoint)
    try await waitFor { reference.model.presentation.exposesCanvas }
    try await waitFor { report.model.presentation.exposesCanvas }
    try require(reference.session.webView.url?.port == endpoint.port && report.session.webView.url?.port == endpoint.port,
      "Runtime tabs did not reconnect to the same endpoint")
    coordinator.apply(runtimeEndpoint: reconnectEndpoint)
    try await waitFor { reference.model.presentation.exposesCanvas && report.model.presentation.exposesCanvas }
    try require(reference.session.webView.url?.port == reconnectEndpoint.port
      && report.session.webView.url?.port == reconnectEndpoint.port,
      "Runtime tabs did not reconnect to the replacement endpoint")
    print("Witness: destinations resolve honestly, report and service tabs are isolated, local document confined, runtime loss and same/new endpoint reconnect shown")

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

  /// Home ownership on a real WKWebView. The Scaffold Inspector's artifact
  /// endpoint link (`target=_blank`, answered with JSON) opens a tool tab;
  /// Home returns that tab to the runtime overview on the same origin, from
  /// the JSON and from an error page, with its own history intact and every
  /// sibling untouched. A destination keeps its own overview. An ordinary
  /// endpoint link lands in a script-less reference view; Home from there
  /// reaches the overview through the coordinator in an interactive tab.
  /// No host or port is ever named by the App.
  static func homeContract(_ endpoint: URL, reconnectEndpoint: URL) async throws {
    let model = AppModel()
    let console = WebConsoleSession(websiteDataStore: .nonPersistent(), downloadDestinationProvider: { _, _, _ in nil })
    var opened: [(URL, WebTabRole)] = []
    var externals: [URL] = []
    console.events.openInTab = { opened.append(($0, $1)) }
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

    // The Inspector's artifact endpoint link: target=_blank on a JSON route.
    try await evaluate("document.getElementById('api-blank').click()", in: consoleView)
    try await waitFor { opened.count == 1 }
    let endpointURL = opened[0].0
    try require(opened[0].1 == .tool && endpointURL.path == "/api/scaffold/artifacts"
      && endpointURL.query == "org=o&repo=r&day=d&plan_id=p",
      "_blank endpoint link did not request a tool tab with its query")

    let coordinator = NativeTabCoordinator(
      anchorWindow: { controller.window }, openExternally: { externals.append($0) },
      websiteDataStore: .nonPersistent(),
      homeDirectory: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true),
      environment: [:], fileExists: { _ in false })
    coordinator.apply(runtimeEndpoint: endpoint)
    guard case .opened(let rawKey) = coordinator.open(.url(endpointURL, .tool)) else {
      throw Failure(message: "Raw endpoint tool tab did not open")
    }
    let raw = coordinator.tabs[rawKey]!
    try await waitFor { if case .loaded(let url) = raw.session.loadState { return url.path == "/api/scaffold/artifacts" }; return false }
    try require(raw.session.webView.url?.query == "org=o&repo=r&day=d&plan_id=p", "Tool tab lost the endpoint query")
    try require(raw.model.presentation.phase == .online && raw.model.presentation.exposesCanvas,
      "Loaded JSON left the tool toolbar in \(raw.model.presentation.phase)")
    try require(!raw.session.navigation.canGoBack, "A fresh tool tab claimed history")

    // 1. Home from raw JSON: the runtime overview on the same origin, as a
    //    new history entry; the console and its edit state stay put.
    raw.navigate(.home)
    try await waitFor { if case .loaded(let url) = raw.session.loadState { return url.path == "/" }; return false }
    let overview = raw.session.webView.url!
    try require(overview.host == endpoint.host && overview.port == endpoint.port && overview.query == nil,
      "Home left the runtime origin or kept the endpoint query: \(overview)")
    try require(try await evaluateString("document.getElementById('fixture').textContent", in: raw.session.webView) == "Ready",
      "Home did not render the product overview")
    try require(raw.model.presentation.phase == .online, "Home left the toolbar in \(raw.model.presentation.phase)")
    try await waitFor { raw.session.navigation.canGoBack }
    try require(consoleView.url?.path == "/scaffold" && console.loadState == .loaded(consoleView.url!),
      "Tool tab Home moved the console")
    try require(try await evaluateString("document.getElementById('draft').value", in: consoleView) == "edited-draft",
      "Tool tab Home discarded Scaffold edit state")
    raw.navigate(.back)
    try await waitFor { raw.session.webView.url?.path == "/api/scaffold/artifacts" }
    try await waitFor { raw.session.navigation.canGoForward }
    raw.navigate(.forward)
    try await waitFor { raw.session.webView.url?.path == "/" }
    print("Witness: _blank raw endpoint tab: Home returned to the runtime overview; Back/Forward kept the JSON in the tab's own history")

    // 2. Home from an error page recovers the tab.
    raw.session.navigate(path: "/failure")
    try await waitFor { if case .failed = raw.session.loadState { return true }; return false }
    try require(raw.model.presentation.phase == .recovering, "HTTP failure did not surface in the tool tab")
    raw.navigate(.home)
    try await waitFor { if case .loaded(let url) = raw.session.loadState { return url.path == "/" }; return false }
    try require(raw.model.presentation.phase == .online, "Home from an error page did not recover the tab")

    // 3. Same-origin classification: only the connected runtime gets a tab,
    //    and Home follows the endpoint the caretaker resolved, never a host
    //    or port of its own.
    var foreign = URLComponents(url: endpointURL, resolvingAgainstBaseURL: false)!
    foreign.port = (endpoint.port ?? 0) + 1
    guard case .unavailable = coordinator.open(.url(foreign.url!, .tool)) else {
      throw Failure(message: "A foreign origin was given a runtime tab")
    }
    coordinator.apply(runtimeEndpoint: reconnectEndpoint)
    try await waitFor { raw.session.webView.url?.port == reconnectEndpoint.port }
    raw.session.navigate(path: "/workspaces")
    try await waitFor { if case .loaded(let url) = raw.session.loadState { return url.path == "/workspaces" }; return false }
    raw.navigate(.home)
    try await waitFor {
      if case .loaded(let url) = raw.session.loadState { return url.path == "/" && url.port == reconnectEndpoint.port }
      return false
    }
    coordinator.apply(runtimeEndpoint: endpoint)
    try await waitFor { raw.session.webView.url?.port == endpoint.port }
    print("Witness: Home followed the replacement endpoint; a foreign origin got no tab")

    // 4. A destination keeps its own overview: the Loctree report returns to
    //    the report, not to the product root.
    let reportDestination = ToolDestination.named("loctree-report")!
    try require(coordinator.open(.destination(reportDestination)) == .opened("loctree-report"), "Report tab did not open")
    let report = coordinator.tabs["loctree-report"]!
    try await waitFor { if case .loaded(let url) = report.session.loadState { return url.path == "/structure/report" }; return false }
    report.session.navigate(path: "/structure/report/graph")
    try await waitFor { if case .loaded(let url) = report.session.loadState { return url.path == "/structure/report/graph" }; return false }
    report.navigate(.home)
    try await waitFor { if case .loaded(let url) = report.session.loadState { return url.path == "/structure/report" }; return false }
    try require(report.session.webView.url?.port == endpoint.port, "Report Home left its origin")
    try require(raw.session.webView.url?.path == "/", "Report Home moved the raw endpoint tab")

    print("Witness: destination Home stayed on the report")

    // 5. The ordinary endpoint link: JSON diverted from a plain `<a>` into a
    //    read-only reference view. That view runs no script, so it cannot
    //    render the product overview itself; Home from it must still bring
    //    the user to the overview, through the coordinator, in an
    //    interactive tool tab on the same origin. The reference view keeps
    //    its document, role, no-script setting and history; the console
    //    keeps its edit state; no sibling tab moves.
    try await evaluate("document.getElementById('api').click()", in: consoleView)
    try await waitFor { opened.count == 2 }
    try require(opened[1].1 == .reference, "Plain endpoint link was not diverted to a reference view")
    guard case .opened(let referenceKey) = coordinator.open(.url(opened[1].0, .reference)) else {
      throw Failure(message: "Reference tab did not open")
    }
    let reference = coordinator.tabs[referenceKey]!
    try await waitFor { if case .loaded(let url) = reference.session.loadState { return url.path == "/api/scaffold/artifacts" }; return false }
    try require(!reference.session.webView.configuration.defaultWebpagePreferences.allowsContentJavaScript,
      "Reference view runs page script")
    // The overview is derived here from the fixture endpoint, not read from
    // the App, so this witness also holds against product code that lacks it.
    var overviewComponents = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)!
    overviewComponents.path = "/"
    overviewComponents.query = nil
    overviewComponents.fragment = nil
    let overviewKey = NativeTabCoordinator.key(for: overviewComponents.url!, role: .tool)
    let tabsBeforeHome = coordinator.tabs.count
    try require(coordinator.tabs[overviewKey] == nil, "An overview tab existed before Home")
    reference.navigate(.home)
    try await waitFor { coordinator.tabs[overviewKey] != nil }
    let overviewTab = coordinator.tabs[overviewKey]!
    try require(coordinator.tabs.count == tabsBeforeHome + 1, "Reference Home opened more than one tab")
    try require(overviewTab.session.role == .tool && overviewTab.session.webView !== reference.session.webView
      && overviewTab.session.webView !== consoleView, "Overview tab identity")
    try await waitFor { if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/" }; return false }
    let overviewURL = overviewTab.session.webView.url!
    try require(overviewURL.host == endpoint.host && overviewURL.port == endpoint.port && overviewURL.query == nil,
      "Reference Home left the runtime origin: \(overviewURL)")
    try require(try await evaluateString("document.getElementById('fixture').textContent", in: overviewTab.session.webView) == "Ready",
      "Reference Home did not render the product overview")
    try require(overviewTab.model.presentation.phase == .online && overviewTab.model.presentation.exposesCanvas,
      "Overview tab toolbar in \(overviewTab.model.presentation.phase)")
    try require(overviewTab.window?.tabGroup != nil && overviewTab.window?.tabGroup === controller.window?.tabGroup,
      "Overview tab is not in the console's native tab group")
    try require(overviewTab.window?.tabGroup?.selectedWindow === overviewTab.window,
      "Reference Home did not bring the overview tab forward")
    try await tick()
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts"
      && reference.session.loadState == .loaded(reference.session.webView.url!),
      "Reference Home reloaded or moved the reference view")
    try require(!reference.session.navigation.canGoBack, "Reference Home added a history entry to the reference view")
    try require(reference.session.role == .reference
      && !reference.session.webView.configuration.defaultWebpagePreferences.allowsContentJavaScript,
      "Reference Home changed the view's role or enabled script")
    try require(consoleView.url?.path == "/scaffold" && console.loadState == .loaded(consoleView.url!),
      "Reference Home moved the console")
    try require(try await evaluateString("document.getElementById('draft').value", in: consoleView) == "edited-draft",
      "Reference Home discarded Scaffold edit state")
    try require(raw.session.webView.url?.path == "/" && report.session.webView.url?.path == "/structure/report",
      "Reference Home moved a sibling tab")
    print("Witness: ordinary API link -> reference view -> Home reached the product overview in an interactive tab; the reference view, console and siblings stayed put")

    // 5b. Home again from the reference view focuses that overview tab; no twin.
    reference.window?.tabGroup?.selectedWindow = reference.window
    try require(overviewTab.window?.tabGroup?.selectedWindow === reference.window, "Fixture could not reselect the reference tab")
    reference.navigate(.home)
    try await tick()
    try require(coordinator.tabs.count == tabsBeforeHome + 1 && coordinator.tabs[overviewKey] === overviewTab,
      "Repeated reference Home opened a twin overview tab")
    try require(overviewTab.window?.tabGroup?.selectedWindow === overviewTab.window,
      "Repeated reference Home did not focus the existing overview tab")
    // The overview tab is an ordinary tool tab: its own Home is `/`.
    overviewTab.session.navigate(path: "/workspaces")
    try await waitFor { if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/workspaces" }; return false }
    overviewTab.navigate(.home)
    try await waitFor { if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/" }; return false }
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts", "Overview Home moved the reference view")

    // 5b'. The overview tab was navigated away, then failed, since it opened.
    //      Home from the reference view must bring that same tab back to `/`
    //      of the current runtime, not merely focus whatever it shows now,
    //      with the tab's Back history kept and no twin opened.
    overviewTab.session.navigate(path: "/runs")
    try await waitFor { if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/runs" }; return false }
    reference.window?.tabGroup?.selectedWindow = reference.window
    reference.navigate(.home)
    try await waitFor {
      if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/" && url.port == endpoint.port }
      return false
    }
    try require(coordinator.tabs.count == tabsBeforeHome + 1 && coordinator.tabs[overviewKey] === overviewTab,
      "Reference Home over a navigated-away overview tab opened a twin")
    try require(overviewTab.window?.tabGroup?.selectedWindow === overviewTab.window,
      "Reference Home did not select the reused overview tab")
    try require(try await evaluateString("document.getElementById('fixture').textContent", in: overviewTab.session.webView) == "Ready",
      "Reused overview tab did not render the product overview")
    try require(overviewTab.session.navigation.canGoBack, "Reference Home over a navigated-away overview tab discarded its history")
    overviewTab.navigate(.back)
    try await waitFor { overviewTab.session.webView.url?.path == "/runs" }
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts" && !reference.session.navigation.canGoBack,
      "Reusing the overview tab touched the reference view")
    overviewTab.session.navigate(path: "/failure")
    try await waitFor { if case .failed = overviewTab.session.loadState { return true }; return false }
    try require(overviewTab.model.presentation.phase == .recovering, "HTTP failure did not surface in the overview tab")
    reference.window?.tabGroup?.selectedWindow = reference.window
    reference.navigate(.home)
    try await waitFor { if case .loaded(let url) = overviewTab.session.loadState { return url.path == "/" }; return false }
    try require(overviewTab.model.presentation.phase == .online && coordinator.tabs.count == tabsBeforeHome + 1,
      "Reference Home did not recover the failed overview tab in place")
    print("Witness: reference Home brought a navigated-away and then a failed overview tab back to `/` with history kept and no twin")

    // 5c. Without a runtime there is no overview to show: Home from the
    //     reference view opens nothing, and the reconnect re-presents the
    //     document the view exists for.
    overviewTab.close()
    try await waitFor { coordinator.tabs[overviewKey] == nil }
    coordinator.apply(runtimeEndpoint: nil)
    try require(reference.model.presentation.phase == .recovering, "Runtime loss was not shown on the reference view")
    reference.navigate(.home)
    try await tick()
    try require(coordinator.tabs.count == tabsBeforeHome && coordinator.tabs[overviewKey] == nil,
      "Reference Home opened a tab without a connected runtime")
    coordinator.apply(runtimeEndpoint: endpoint)
    try await waitFor { reference.model.presentation.exposesCanvas }
    try require(reference.session.webView.url?.path == "/api/scaffold/artifacts"
      && reference.session.webView.url?.port == endpoint.port,
      "Reconnect moved the reference view off its document")
    print("Witness: repeated reference Home focused the one overview tab; no runtime, no tab; reconnect kept the document")

    for tab in coordinator.tabs.values { tab.close() }
    controller.close()
  }

  static func main() async throws {
    _ = NSApplication.shared
    let endpoint = URL(string: CommandLine.arguments[1])!
    let reconnectEndpoint = URL(string: CommandLine.arguments[2])!
    try require(endpoint.scheme == "http" && endpoint.host == "127.0.0.1" && endpoint.port != nil,
      "Only a loopback fixture endpoint is permitted")
    try require(reconnectEndpoint.scheme == "http" && reconnectEndpoint.host == "127.0.0.1" && reconnectEndpoint.port != nil,
      "Only a loopback reconnect endpoint is permitted")
    try stateContract(endpoint)
    try policyContract(endpoint)
    try authenticationAndDownloadContract(endpoint)
    try tabPolicyContract(endpoint)
    try destinationContract(endpoint)
    try await webContract(endpoint)
    try await tabsContract(endpoint, reconnectEndpoint: reconnectEndpoint)
    try await homeContract(endpoint, reconnectEndpoint: reconnectEndpoint)
    print("CommandDeckIntegrationTests passed")
  }
}
