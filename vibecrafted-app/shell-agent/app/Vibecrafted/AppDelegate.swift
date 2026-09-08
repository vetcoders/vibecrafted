import AppKit
import CoreText
import Darwin
import os.log

// The durable lifecycle trail (installLog, lifecycleLog, signal handlers)
// lives in LifecycleLog.swift — supervision evidence extracted from this
// delegate; the Runtime Pack installer owns initialization of the log file.

/// The owner's `vibecrafted.runtime-install-result.v1` launch contract.
///
/// One shape, two producers, both of them the installer: `runtime-install`
/// prints it after publishing a generation, and the read-only `runtime-resolve`
/// prints it back for the generation already installed. The App decodes this
/// and launches what it says. It derives none of these paths for itself — that
/// duplication is precisely what made the App a second opinion on what counts
/// as an installable runtime.
private struct CanonicalRuntimeInstall: Decodable {
  let root: URL
  let launcher: URL
  let terminal: URL
  let terminalHost: URL
  let frame: URL
  let start: URL
  let primaryShell: URL
  let terminalConfig: URL
  let frameConfig: URL
  let runtimeHome: URL
  let configHome: URL
  let craftedHome: URL

  enum CodingKeys: String, CodingKey {
    case root
    case launcher
    case terminal
    case terminalHost = "terminal_host"
    case frame
    case start
    case primaryShell = "primary_shell"
    case terminalConfig = "terminal_config"
    case frameConfig = "frame_config"
    case runtimeHome = "runtime_home"
    case configHome = "config_home"
    case craftedHome = "crafted_home"
  }

  init(from decoder: Decoder) throws {
    let container = try decoder.container(keyedBy: CodingKeys.self)

    func fileURL(_ key: CodingKeys) throws -> URL {
      let path = try container.decode(String.self, forKey: key)
      guard path.hasPrefix("/") else {
        throw DecodingError.dataCorruptedError(
          forKey: key, in: container,
          debugDescription: "runtime installer returned a non-absolute filesystem path")
      }
      return URL(fileURLWithPath: path)
    }

    root = try fileURL(.root)
    launcher = try fileURL(.launcher)
    terminal = try fileURL(.terminal)
    terminalHost = try fileURL(.terminalHost)
    frame = try fileURL(.frame)
    start = try fileURL(.start)
    primaryShell = try fileURL(.primaryShell)
    terminalConfig = try fileURL(.terminalConfig)
    frameConfig = try fileURL(.frameConfig)
    runtimeHome = try fileURL(.runtimeHome)
    configHome = try fileURL(.configHome)
    craftedHome = try fileURL(.craftedHome)
  }
}

final class EventObserver: @unchecked Sendable, EventCallback {
  func onEvent(eventJson: String) {
    DispatchQueue.main.async {
      NotificationCenter.default.post(
        name: NSNotification.Name("IpcEvent"), object: nil, userInfo: ["eventJson": eventJson])
    }
  }

  func onError(err: String) {
    print("IPC Stream Error: \(err)")
  }
}

/// Accumulates a bounded amount of one subprocess stream.
///
/// Foundation delivers pipe readability on its own queue, so this is the same
/// shape as `EventObserver` above: an `@unchecked Sendable` box whose mutation
/// is serialised by a lock. Draining continuously is what stops a child from
/// blocking forever on a full pipe buffer; the ceiling is what stops a chatty
/// failure from becoming unbounded memory behind the tray.
private final class BoundedOutputSink: @unchecked Sendable {
  private let lock = NSLock()
  private let limit: Int
  private var storage = Data()

  init(limit: Int) {
    self.limit = limit
  }

  func absorb(_ chunk: Data) {
    guard !chunk.isEmpty else { return }
    lock.lock()
    defer { lock.unlock() }
    let room = limit - storage.count
    guard room > 0 else { return }
    storage.append(chunk.count <= room ? chunk : chunk.prefix(room))
  }

  var collected: Data {
    lock.lock()
    defer { lock.unlock() }
    return storage
  }
}

/// The outcome of one bounded subprocess.
private struct BoundedProcessResult {
  let stdout: Data
  let stderr: Data
  let terminationStatus: Int32
  /// The child exited on its own rather than being signalled or timed out.
  let clean: Bool
}

/// Take whatever a pipe still holds. Safe only once the writer is gone, which
/// is why this runs from the termination handler and not before it.
private func drainRemainder(_ handle: FileHandle, into sink: BoundedOutputSink) {
  handle.readabilityHandler = nil
  var tail = handle.availableData
  while !tail.isEmpty {
    sink.absorb(tail)
    tail = handle.availableData
  }
}

extension RuntimeIdentityProbe {
  /// The real filesystem. `exists` is deliberately positive presence, so an
  /// existing file this App cannot read stays an installation rather than
  /// becoming "nothing is installed".
  static var live: RuntimeIdentityProbe {
    RuntimeIdentityProbe(
      exists: { FileManager.default.fileExists(atPath: $0.path) },
      read: { try? Data(contentsOf: $0) },
      realPath: { $0.resolvingSymlinksInPath() },
      isExecutable: { FileManager.default.isExecutableFile(atPath: $0.path) })
  }
}

/// The App only ever resolves one thing: the owner's launch contract.
private typealias RuntimeContract = RuntimeResolution<CanonicalRuntimeInstall>

/// How long the quit path waits for the lifecycle launcher to describe active
/// work before treating its silence as an answer.
private let activityTruthTimeout: TimeInterval = 15

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate, NSMenuItemValidation, CommandDeckActionHandling {
  var mainWindow: MainWindowController?
  private let model = AppModel()
  private lazy var webSession = WebConsoleSession()
  /// Tool and reference tabs. Owns windows and their web sessions only; the
  /// runtime, supervisor and terminal stay here.
  private lazy var tabs = NativeTabCoordinator(
    anchorWindow: { [unowned self] in
      self.showMainWindowIfNeeded()
      return self.mainWindow?.window
    },
    openExternally: { [unowned self] url in self.openExternalURL(url) })
  private var tray: StatusItemController?
  private var repairInFlight = false
  private var confirmedStopRoot: URL?
  private var terminalWorkingDirectory = FileManager.default.homeDirectoryForCurrentUser
  private var signedCarrierRevisions: (source: String, terminal: String, frame: String)?
  private var statusRefreshTimer: Timer?
  private var terminalApplication: NSRunningApplication?
  private var terminalLaunchInFlight = false
  private var serverStatusProcess: Process?
  private var serverActionProcess: Process?
  private var serverActionInFlight: ServerLifecycleAction?
  /// The one tray action allowed to be preflighting its runtime identity. This
  /// is not the same as an action being in flight: between the click and the
  /// spawn the App is asking the owner what is installed, and a second click in
  /// that window would put two verbs on the same service.
  private var runtimeActionPreflight: String?
  private var serverUtilityProcess: Process?
  /// The last caretaker envelope bytes the status poll brought back. The menu,
  /// the diagnostics alert and the action in-flight state all render from this
  /// one reading — never from a second, privately-fused source.
  private var lastCaretakerData: Data?
  private var canonicalInstall: CanonicalRuntimeInstall?
  private var canonicalRuntimeEnvironment: [String: String]?
  private var workspaceLaunchFailureReported = false
  private var eyeReconcileProcess: Process?
  private var runtimeResolveProcess: Process?
  /// Callers that asked while a resolve was already in flight. They join the
  /// one invocation instead of racing a second resolver against it.
  private var runtimeResolveWaiters: [(RuntimeContract) -> Void] = []
  /// Bumped whenever runtime truth changes — adopted, lost or refused. Results
  /// and actions carrying an older epoch are dropped, so a late answer for a
  /// previous generation can never repopulate live controls.
  private var runtimeResolveEpoch: UInt64 = 0
  private var cachedResolution: (fingerprint: RuntimeIdentityFingerprint, value: RuntimeContract)?
  /// Why there is no usable runtime, rendered where the generation would be.
  private var runtimeResolutionFailure: String?
  /// A non-fatal note about the shared service, rendered under the runtime line.
  private var runtimeAdvisory: String?
  /// The owner's last word on configuration, for the diagnostics surface.
  private var lastConfigRepair: ConfigRepairEnvelope?
  private var configRepairProcess: Process?
  private var terminalLaunch: TerminalLauncher.Launch?
  private var terminalRegistration: TerminalRegistrationObservation?
  private var terminalRegistrationTimer: Timer?
  let eventObserver = EventObserver()

  func showMainWindowIfNeeded() {
    if mainWindow == nil {
      mainWindow = MainWindowController(model: model, session: webSession, actions: self,
        openExternally: { [unowned self] url in self.openExternalURL(url) })
    }
    mainWindow?.showWindow(nil)
    mainWindow?.window?.makeKeyAndOrderFront(nil)
    NSApp.activate(ignoringOtherApps: true)
  }

  func applicationDidFinishLaunching(_ notification: Notification) {
    if ProcessInfo.processInfo.arguments.contains("--uninstall") {
      do {
        try uninstallCanonicalRuntime()
        exit(EXIT_SUCCESS)
      } catch {
        fputs("Vibecrafted uninstall failed: \(error)\n", stderr)
        exit(EXIT_FAILURE)
      }
    }
    if ProcessInfo.processInfo.arguments.contains("--bootstrap-only") {
      do {
        let install = try installCanonicalRuntime()
        print(install.root.path)
        exit(EXIT_SUCCESS)
      } catch {
        fputs("Vibecrafted bootstrap failed: \(error)\n", stderr)
        exit(EXIT_FAILURE)
      }
    }

    installLifecycleSignalHandlers()
    let launchArgs = ProcessInfo.processInfo.arguments.dropFirst().joined(separator: " ")
    let launchedByLS = ProcessInfo.processInfo.environment["__CFBundleIdentifier"] != nil
    lifecycleLog(
      "launch ppid=\(getppid()) launchedByLS=\(launchedByLS) args=[\(launchArgs)]")

    NSApp.setActivationPolicy(.regular)
    configureCommandDeck()
    buildMainMenu()
    buildStatusItem()
    startNativeNotifications()

    let socketPath = "/tmp/vibecrafted-mux.sock"
    do {
      try initRuntime(socketPath: socketPath)
      Task {
        do {
          try await subscribeEvents(callback: eventObserver)
        } catch {
          print("Failed to subscribe: \(error)")
        }
      }
    } catch {
      print("Failed to init runtime: \(error)")
    }

    showMainWindowIfNeeded()
    connectCommandDeck()
  }

  func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
    showMainWindowIfNeeded()
    return true
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    false
  }

  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    // Quit closes UI only; the service, terminals and agent lanes keep running.
    .terminateNow
  }

  func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
    true
  }

  func application(_ application: NSApplication, open urls: [URL]) {
    NotificationManager.shared.handleOpenURLs(urls)
  }

  func applicationWillTerminate(_ notification: Notification) {
    // The workspace terminal is now started through the generation wrapper, so
    // it is a child of this process rather than an independent application.
    // That changes nothing about its lifetime: quitting the App is a view
    // closing. Nothing here stops the terminal, the shared service, frame
    // sessions, PTYs or workers, and nothing here should.
    let terminalState: String
    if let application = terminalApplication, !application.isTerminated {
      terminalState = "vc-terminal pid=\(application.processIdentifier) still running"
    } else if let launch = terminalLaunch, launch.isRunning {
      terminalState = "vc-terminal pid=\(launch.receipt.processIdentifier) still running"
    } else if terminalApplication != nil || terminalLaunch != nil {
      terminalState = "vc-terminal already exited"
    } else {
      terminalState = "no vc-terminal"
    }
    lifecycleLog("applicationWillTerminate; \(terminalState)")
    statusRefreshTimer?.invalidate()
    terminalRegistrationTimer?.invalidate()
    NotificationManager.shared.clearHeartbeat(craftedHome: craftedHomeURL())
  }

  private func startNativeNotifications() {
    NotificationManager.shared.presentWindow = { [weak self] in
      self?.showMainWindowIfNeeded()
    }
    NotificationManager.shared.presentRun = { [weak self] runID, report, preferReport in
      guard !runID.isEmpty,
        runID.unicodeScalars.allSatisfy({ CharacterSet.alphanumerics.contains($0) || "-_".unicodeScalars.contains($0) }) else { return }
      self?.openRoute("/run/\(runID)")
      if preferReport, let report {
        do { try self?.nativeBridge.perform(.revealPath(try .init(path: report))) }
        catch { self?.showNativeMessage("Cannot reveal report", error.localizedDescription) }
      }
    }
    NotificationManager.shared.start(craftedHome: craftedHomeURL())
  }

  private func craftedHomeURL() -> URL {
    let host = ProcessInfo.processInfo.environment
    let home = host["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
    return URL(
      fileURLWithPath: host["VIBECRAFTED_HOME"] ?? "\(home)/.vibecrafted", isDirectory: true)
  }

  private func configureCommandDeck() {
    model.endpointDidChange = { [weak self] endpoint in
      self?.webSession.apply(endpoint: endpoint)
      self?.tabs.apply(runtimeEndpoint: endpoint)
    }
    model.setStateChangeHandler { [weak self] _ in self?.updateDeckPresentation() }
    webSession.events.stateDidChange = { [weak self] state in self?.model.receiveWebState(state) }
    webSession.events.openExternally = { [weak self] url in self?.openExternalURL(url) }
    // A `target=_blank` page or a machine document (JSON endpoint) never
    // replaces the console document; it gets its own native tab.
    webSession.events.openInTab = { [weak self] url, role in
      guard let self else { return }
      if case .unavailable(let reason) = self.tabs.open(.url(url, role)) {
        self.showNativeMessage("Cannot open in a tab", reason)
      }
    }
    webSession.events.navigationBlocked = { [weak self] _, reason in
      self?.showNativeMessage("Navigation blocked", reason)
    }
    webSession.events.downloadFinished = { url in NSWorkspace.shared.activateFileViewerSelecting([url]) }
    webSession.events.downloadFailed = { [weak self] reason in self?.showNativeMessage("Download failed", reason) }
  }

  private func connectCommandDeck() {
    _ = try? loadSignedCarrierRevisions()
    model.beginConnecting()
    resolveInstalledRuntime { [weak self] resolution in
      guard let self else { return }
      // First onboarding and explicit repair remain in the existing installer.
      let adopted: RuntimeContract
      if case .absent = resolution {
        do {
          adopted = .ready(try self.installCanonicalRuntime())
          self.cachedResolution = nil
        } catch {
          self.applyResolution(resolution)
          let reason = "Runtime onboarding failed: \(error.localizedDescription)"
          self.runtimeResolutionFailure = reason
          self.model.block(reason: reason)
          return
        }
      } else { adopted = resolution }
      guard let install = self.applyResolution(adopted),
        let environment = self.canonicalRuntimeEnvironment else {
        self.renderServerStatus()
        return
      }
      self.reconcileControlPlaneEye(install: install, environment: environment)
      self.inspectConfigurationAtLaunch()
      self.refreshServerStatus()
    }
  }

  /// Read-only configuration check at launch.
  ///
  /// A plan writes nothing — not a merge, not a backup, not a receipt — so a
  /// valid configuration is untouched on the first launch and on every launch
  /// after it. The unconditional startup regeneration this replaces could not
  /// make that promise: it rewrote first and asked nothing. Drift now becomes a
  /// line in the tray and an offer behind Repair Runtime.
  private func inspectConfigurationAtLaunch() {
    let epoch = runtimeResolveEpoch
    runConfigRepair(plan: true) { [weak self] outcome in
      guard let self, epoch == self.runtimeResolveEpoch else { return }
      switch outcome {
      case .healthy(let envelope):
        self.lastConfigRepair = envelope
      case .repairable(let envelope), .conflict(let envelope), .repaired(let envelope):
        self.lastConfigRepair = envelope
        if let advisory = configRepairAdvisory(envelope) {
          self.surfaceRuntimeAdvisory(advisory)
        }
      case .absent, .unusable:
        // The runtime itself is what is wrong, and the resolver has already
        // said so in the place this would otherwise overwrite.
        break
      }
      self.renderServerStatus()
    }
  }

  /// Ask the configuration owner: the installed generation's own installer.
  ///
  /// Never this App's carrier copy. The generation that is running is the one
  /// entitled to say what its configuration should be, which is also what lets
  /// a repair work on an installation newer than this bundle.
  private func runConfigRepair(
    plan: Bool, completion: @escaping (ConfigRepairOutcome) -> Void
  ) {
    let runtimeHome = currentRuntimeHome()
    switch runtimeResolverBootstrap(runtimeHome: runtimeHome, probe: .live) {
    case .absent(let reason): completion(.absent(reason))
    case .unusable(let reason): completion(.unusable(reason))
    case .ask(let python, let installer):
      let process = Process()
      process.executableURL = python
      process.arguments = runtimeRepairArguments(
        installer: installer, runtimeHome: runtimeHome, plan: plan)
      process.environment = runtimeResolverEnvironment()
      do {
        try runBounded(
          process, timeout: plan ? 20 : 180,
          label: plan ? "runtime-repair --plan" : "runtime-repair"
        ) { [weak self] result in
          self?.configRepairProcess = nil
          completion(
            decodeConfigRepair(
              stdout: result.stdout, stderr: result.stderr,
              terminationStatus: result.terminationStatus, clean: result.clean))
        }
        configRepairProcess = process
      } catch {
        configRepairProcess = nil
        completion(
          .unusable(
            "the configuration owner could not be started: \(error.localizedDescription)"))
      }
    }
  }

  private var nativeBridge: NativeCommandBridge {
    NativeCommandBridge(allowedPathRoots: [FileManager.default.homeDirectoryForCurrentUser,
      craftedHomeURL()] + [canonicalInstall?.runtimeHome].compactMap { $0 }, actions: .init(
      openTerminal: { [unowned self] directory in
        guard self.canonicalInstall != nil else { throw NativeCommandError.pathNotAllowed }
        self.terminalWorkingDirectory = directory
        if self.terminalIsLive() { self.focusTerminal() }
        else { self.launchWorkspaceTerminal() }
      },
      openExternalURL: { url in NSWorkspace.shared.open(url) },
      revealPath: { url in NSWorkspace.shared.activateFileViewerSelecting([url]) },
      retryConnection: { [unowned self] in
        guard self.runtimeActionPreflight == nil, self.serverActionInFlight == nil else { return }
        self.webSession.retry()
        self.connectCommandDeck()
      },
      confirmRuntimeStop: { [unowned self] in self.confirmRuntimeStop() },
      stopRuntime: { [unowned self] in self.performServerAction(.stop) }))
  }

  /// Every hand-off to the system browser goes through the typed bridge, so a
  /// URL is validated the same way whichever tab asked.
  private func openExternalURL(_ url: URL) {
    do { try nativeBridge.perform(.openExternalURL(try .init(value: url.absoluteString))) }
    catch { reportWorkspaceLaunchFailure(error.localizedDescription) }
  }

  // MARK: - Tabs and history (selected window only)

  /// The navigation owner of the key window: the console or one tool tab.
  private func selectedNavigationHandler() -> (any CommandDeckNavigationHandling)? {
    let key = NSApp.keyWindow
    if let mainWindow, mainWindow.window === key { return mainWindow }
    if let tab = tabs.controller(for: key) { return tab }
    return mainWindow
  }

  private func selectedSession() -> WebConsoleSession? {
    let key = NSApp.keyWindow
    if let mainWindow, mainWindow.window === key { return webSession }
    return tabs.controller(for: key)?.session ?? (mainWindow == nil ? nil : webSession)
  }

  @objc private func goHome() { selectedNavigationHandler()?.navigate(.home) }
  @objc private func goBack() { selectedNavigationHandler()?.navigate(.back) }
  @objc private func goForward() { selectedNavigationHandler()?.navigate(.forward) }
  @objc private func openSelectedPageInBrowser() { selectedNavigationHandler()?.navigate(.openInBrowser) }

  @objc private func openDestinationInTab(_ sender: NSMenuItem) {
    guard let id = sender.representedObject as? String, let destination = ToolDestination.named(id) else { return }
    if case .unavailable(let reason) = tabs.open(.destination(destination)) {
      showNativeMessage("\(destination.title) is unavailable", reason)
    }
  }

  @objc private func openDestinationExternally(_ sender: NSMenuItem) {
    guard let id = sender.representedObject as? String, let destination = ToolDestination.named(id) else { return }
    switch tabs.resolve(destination) {
    case .available(let url, .runtime), .available(let url, .service): openExternalURL(url)
    case .available(let url, .localDocument): revealNativePath(url)
    case .unavailable(let reason): showNativeMessage("\(destination.title) is unavailable", reason)
    }
  }

  func validateMenuItem(_ item: NSMenuItem) -> Bool {
    switch item.action {
    case #selector(goBack): return selectedSession()?.navigation.canGoBack ?? false
    case #selector(goForward): return selectedSession()?.navigation.canGoForward ?? false
    case #selector(goHome): return selectedSession() != nil
    case #selector(openSelectedPageInBrowser):
      guard let url = selectedSession()?.navigation.currentURL else { return false }
      return url.scheme == "http" || url.scheme == "https"
    case #selector(openDestinationInTab(_:)), #selector(openDestinationExternally(_:)):
      guard let id = item.representedObject as? String, let destination = ToolDestination.named(id) else { return false }
      switch tabs.resolve(destination) {
      case .available: item.toolTip = nil; return true
      case .unavailable(let reason): item.toolTip = reason; return false
      }
    default: return true
    }
  }

  func handle(_ action: CommandDeckChromeAction) {
    do {
      switch action {
      case .openTerminal: openTerminalFromStatusItem()
      case .retryConnection: try nativeBridge.perform(.retryConnection)
      case .requestStopRuntime: try nativeBridge.perform(.requestRuntimeStop)
      case .repairRuntime: repairRuntime()
      case .showDiagnostics: showServerDiagnostics()
      }
    } catch { showNativeMessage("Native action failed", error.localizedDescription) }
  }

  private func handleTray(_ action: StatusItemAction) {
    switch action {
    case .showCommandDeck: showMainWindowIfNeeded()
    case .openTerminal: handle(.openTerminal)
    case .retryConnection: handle(.retryConnection)
    case .repairRuntime: handle(.repairRuntime)
    case .stopRuntime: handle(.requestStopRuntime)
    case .showDiagnostics: handle(.showDiagnostics)
    case .showServer: openServerFromStatusItem()
    case .showWorkspaces: openWorkspacesFromStatusItem()
    case .startServer: startServerFromStatusItem()
    case .restartServer: restartServerFromStatusItem()
    case .showLogs: openServerLogsFromStatusItem()
    case .revealRuntime: revealRuntimeHomeFromStatusItem()
    case .revealControlPlane: openControlPlaneFromStatusItem()
    case .copyRuntimeIdentity: copyRuntimeIdentityFromStatusItem()
    case .help: showStatusItemHelp()
    case .quitApp: requestQuit()
    }
  }

  private func confirmRuntimeStop() -> Bool {
    confirmedStopRoot = nil
    guard runtimeActionPreflight == nil, serverActionInFlight == nil, !repairInFlight,
      let install = canonicalInstall,
      deriveServerMenuState(caretakerData: lastCaretakerData,
        actionInFlight: nil, runtimeReady: true).canStop else { return false }
    let activity: String
    switch activeRunSummary() {
    case .available(let summary): activity = "\(summary.lanes) active or stalled lanes; \(summary.worktrees) use worktrees."
    case .unavailable: activity = "Active lane counts are unavailable."
    }
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = "Stop Runtime service?"
    alert.informativeText = "This stops the shared VC Server through its service owner. "
      + "The App loses its web canvas and other clients lose server access. "
      + "It does not stop terminal sessions or agents. \(activity) "
      + "Generation: \(install.root.lastPathComponent)."
    alert.addButton(withTitle: "Cancel")
    alert.addButton(withTitle: "Stop Runtime Service")
    guard alert.runModal() == .alertSecondButtonReturn else { return false }
    confirmedStopRoot = install.root
    return true
  }

  private func showNativeMessage(_ title: String, _ message: String) {
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = message
    alert.addButton(withTitle: "OK")
    alert.runModal()
  }

  private func launchWorkspaceTerminal() {
    if terminalIsLive() || terminalLaunchInFlight {
      return
    }

    // Carrier identity is this bundle's own property, so the tray can compare
    // it against the live generation whether or not this open installs
    // anything. A malformed manifest must not block opening an installed
    // runtime; it degrades the drift line, which the policy reports honestly.
    _ = try? loadSignedCarrierRevisions()

    // Opening is not installing. The runtime of record is whatever generation
    // the Founder has installed, and the owner is the only thing entitled to
    // say which one that is. The bundled carrier is bootstrap and repair
    // material — republishing it for a window re-ran a full install and could
    // walk a newer runtime backwards.
    terminalLaunchInFlight = true
    resolveInstalledRuntime { [weak self] resolution in
      guard let self else { return }
      switch resolution {
      case .ready:
        guard let install = self.applyResolution(resolution) else {
          self.terminalLaunchInFlight = false
          return
        }
        self.openWorkspaceTerminal(install: install)
      case .absent(let reason):
        // Nothing is installed yet, so the bundled carrier is the only runtime
        // that can exist. Publishing it here is first onboarding.
        self.applyResolution(resolution)
        lifecycleLog("no installed runtime (\(reason)); bootstrapping the bundled carrier")
        let install: CanonicalRuntimeInstall
        do {
          install = try self.installCanonicalRuntime()
        } catch {
          self.terminalLaunchInFlight = false
          self.reportWorkspaceLaunchFailure(
            "Cannot publish the canonical Vibecrafted runtime: \(error.localizedDescription)")
          return
        }
        // The bootstrap just changed what is installed, so the cached answer is
        // stale by construction.
        self.cachedResolution = nil
        self.applyResolution(.ready(install))
        self.openWorkspaceTerminal(install: install)
      case .unusable(let reason):
        // An installation exists but the owner refuses it. Overwriting it from
        // the bundled carrier would be an automatic downgrade of the Founder's
        // runtime, so repair stays a deliberate action in the tray.
        self.terminalLaunchInFlight = false
        self.applyResolution(resolution)
        self.renderServerStatus()
        self.reportWorkspaceLaunchFailure(
          "The installed Vibecrafted runtime cannot be used: \(reason). "
            + "Use Repair Runtime… to repair it.")
      }
    }
  }

  /// Open the workspace terminal on the generation the owner selected.
  ///
  /// The public wrapper is the entry point, not a terminal binary chosen here.
  /// The wrapper is where the selected generation's product config and native
  /// host are applied — it refuses a caller-supplied `--config-file` outright —
  /// and the host itself is the owner's choice, carried in the environment
  /// rather than substituted from this bundle. Pairing a newly resolved
  /// generation with the old App's helper binary was the ownership failure this
  /// replaces.
  private func openWorkspaceTerminal(install: CanonicalRuntimeInstall) {
    defer { terminalLaunchInFlight = false }
    guard let environment = canonicalRuntimeEnvironment else {
      reportWorkspaceLaunchFailure("The resolved runtime carries no launch environment")
      return
    }
    do {
      try registerBundledFonts()
      let specification = try TerminalLauncher.Specification(
        generationRoot: install.root, terminal: install.terminal,
        terminalHost: install.terminalHost, primaryShell: install.primaryShell,
        start: install.start, workingDirectory: terminalWorkingDirectory,
        environment: environment)
      terminalLaunch = try TerminalLauncher.launch(specification)
      observeTerminalRegistration()
    } catch {
      reportWorkspaceLaunchFailure("Cannot open the generation-owned terminal: \(error.localizedDescription)")
    }
  }

  private func terminalIsLive() -> Bool {
    terminalLaunch?.isRunning == true || terminalApplication?.isTerminated == false
  }

  private func focusTerminal() {
    // An explicit Open Terminal retries focus for this exact live launch.
    observeTerminalRegistration()
  }

  private func observeTerminalRegistration() {
    terminalRegistrationTimer?.invalidate()
    guard let launch = terminalLaunch, launch.isRunning else { return }
    terminalRegistration = TerminalRegistrationObservation(
      receipt: launch.receipt, now: ProcessInfo.processInfo.systemUptime)
    checkTerminalRegistration()
    guard terminalRegistration != nil else { return }
    terminalRegistrationTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
      Task { @MainActor [weak self] in self?.checkTerminalRegistration() }
    }
  }

  private func checkTerminalRegistration() {
    guard let observation = terminalRegistration else { return }
    let launch = terminalLaunch
    let application = launch.flatMap { NSRunningApplication(processIdentifier: $0.receipt.processIdentifier) }
    let registered = application?.isTerminated == false
      && application?.executableURL?.resolvingSymlinksInPath()
        == launch?.receipt.specification.terminalHost.resolvingSymlinksInPath()
    let outcome = observation.observe(now: ProcessInfo.processInfo.systemUptime,
      currentLaunchID: launch?.receipt.launchID, isRunning: launch?.isRunning == true,
      isRegistered: registered)
    guard outcome != .waiting else { return }
    terminalRegistrationTimer?.invalidate()
    terminalRegistrationTimer = nil
    terminalRegistration = nil
    if outcome == .ready, let application {
      terminalApplication = application
      application.activate(options: [])
    } else if outcome == .timedOut {
      showNativeMessage("Terminal is still starting",
        "The process started, but its macOS application registration was not observed within 10 seconds. "
          + "It remains running. Use Open Terminal to try focusing the same process again.")
    }
  }

  /// The environment every generation-owned subprocess inherits: the tray's
  /// caretaker poll, the service actions and the workspace terminal all run
  /// with exactly this, so they can never address different roots.
  private func composeRuntimeEnvironment(install: CanonicalRuntimeInstall) -> [String: String] {
    let host = ProcessInfo.processInfo.environment
    let inherited = [
      "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "TMPDIR",
      "SHELL",
    ]
    var environment = Dictionary(
      uniqueKeysWithValues: inherited.compactMap { key in host[key].map { (key, $0) } })
    // The workspace terminal spawns agent CLIs (codex, gh, claude, loct) whose
    // `#!/usr/bin/env` shebangs resolve against exactly this PATH. Amputating the
    // caller's PATH down to the system set hides Homebrew, ~/.local/bin and
    // ~/.cargo/bin, so those tools die with exit 127. Keep the host PATH first;
    // the signed generation is a fallback, not a shadow of user-owned tools.
    environment["PATH"] = composedPath(
      generation: install.root, inherited: host["PATH"])
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["XDG_CONFIG_HOME"] = install.configHome.path
    environment["VIBECRAFTED_HOME"] = install.craftedHome.path
    environment["VIBECRAFTED_RUNTIME_HOME"] = install.runtimeHome.path
    environment["VIBECRAFTED_RUNTIME_ROOT"] = install.root.path
    environment["VIBECRAFTED_ROOT"] = install.root.path
    environment["VIBECRAFTED_DECLARED_LAUNCHER"] = install.launcher.path
    environment["VIBECRAFTED_PYTHON"] = install.root.appendingPathComponent("bin/python3").path
    environment["VIBECRAFTED_VC_FRAME_BIN"] = install.frame.path
    environment["VC_FRAME_CONFIG_DIR"] = install.frameConfig.path
    // The public wrapper resolves its native host from this, defaulting to the
    // generation's own libexec. Carrying the owner's answer through is how the
    // App consumes a resolved host instead of imposing the one it ships.
    environment["VIBECRAFTED_TERMINAL_HOST"] = install.terminalHost.path
    // Keep Unix socket paths below macOS' 104-byte sockaddr_un limit. Preserve
    // the former TMPDIR namespace for one-way import into WES during startup.
    let socketRoot = "/tmp/vc-frame-\(getuid())"
    environment["VC_FRAME_SOCKET_DIR"] = socketRoot
    environment["ZELLIJ_SOCKET_DIR"] = socketRoot
    if let temp = host["TMPDIR"]?.trimmingCharacters(in: CharacterSet(charactersIn: "/")),
      !temp.isEmpty
    {
      environment["VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR"] =
        "/\(temp)/vc-frame-\(getuid())"
    }
    return environment
  }

  /// The owner is asked in a closed interpreter environment.
  ///
  /// No inherited `PYTHONPATH`/`PYTHONHOME` — the runtime exports a global
  /// `PYTHONPATH` that poisons unrelated Python tools, and the resolver must
  /// read the installation, not whatever the host session had loaded. No user
  /// site packages, and no bytecode written into the installation being
  /// inspected. Built from an allow-list so a new host variable cannot leak in.
  private func runtimeResolverEnvironment() -> [String: String] {
    let host = ProcessInfo.processInfo.environment
    let inherited = ["HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR"]
    var environment = Dictionary(
      uniqueKeysWithValues: inherited.compactMap { key in host[key].map { (key, $0) } })
    // No PATH at all. Both the interpreter and the installer script are given
    // as absolute paths, so a read-only resolve that needed to look an
    // executable up in PATH would be doing something it is not allowed to do.
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment
  }

  /// Where this process looks for an installation.
  private func currentRuntimeHome() -> URL {
    let host = ProcessInfo.processInfo.environment
    return resolvedRuntimeHome(
      environment: host,
      homeDirectory: host["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path)
  }

  /// Ask the owner what is installed.
  ///
  /// Coalesced and cached: callers that arrive while a resolve is in flight
  /// join it rather than racing a second resolver, and an unchanged identity
  /// pair reuses the last answer, so a five-second tray poll does not spawn an
  /// interpreter it does not need. The call itself is read-only by contract —
  /// it installs, publishes, reconciles and repairs nothing.
  ///
  /// Every answer is bound to the identity that was read when it was asked for,
  /// and that identity is read again before the answer is believed. A generation
  /// published while the resolver runs therefore cannot be handed to a caller
  /// that is already looking at the new one.
  private func resolveInstalledRuntime(
    forceRefresh: Bool = false,
    completion: @escaping (RuntimeContract) -> Void
  ) {
    let runtimeHome = currentRuntimeHome()
    let fingerprint = identityFingerprint(runtimeHome: runtimeHome)
    if !forceRefresh, let cached = cachedResolution, cached.fingerprint == fingerprint {
      completion(cached.value)
      return
    }
    // Join first, then decide whether to ask. One serialization point means a
    // caller that arrives mid-flight is answered about the installation as it
    // stands when the answer lands, not about the one it happened to catch.
    runtimeResolveWaiters.append(completion)
    guard runtimeResolveProcess == nil else { return }
    beginRuntimeResolve(runtimeHome: runtimeHome, fingerprint: fingerprint, attempt: 0)
  }

  /// Ask the owner once, about one identity, for everyone currently waiting.
  private func beginRuntimeResolve(
    runtimeHome: URL,
    fingerprint: RuntimeIdentityFingerprint,
    attempt: Int
  ) {
    switch runtimeResolverBootstrap(runtimeHome: runtimeHome, probe: .live) {
    case .absent(let reason):
      let resolution: RuntimeContract = .absent(reason)
      cachedResolution = (fingerprint, resolution)
      deliverResolution(resolution)
    case .unusable(let reason):
      let resolution: RuntimeContract = .unusable(reason)
      cachedResolution = (fingerprint, resolution)
      deliverResolution(resolution)
    case .ask(let python, let installer):
      let process = Process()
      process.executableURL = python
      process.arguments = runtimeResolveArguments(installer: installer, runtimeHome: runtimeHome)
      process.environment = runtimeResolverEnvironment()
      do {
        try runBounded(process, timeout: 20, label: "runtime-resolve") { [weak self] result in
          guard let self else { return }
          self.runtimeResolveProcess = nil
          // The child ran while the installer was free to publish, so the pair
          // is read again: this answer is only about the identity it was asked
          // about.
          let delivery = runtimeResolveDelivery(
            invoked: fingerprint,
            observed: self.identityFingerprint(runtimeHome: runtimeHome),
            attempt: attempt)
          switch delivery {
          case .deliver:
            let resolution: RuntimeContract = decodeRuntimeResolution(
              stdout: result.stdout,
              stderr: result.stderr,
              terminationStatus: result.terminationStatus,
              clean: result.clean)
            self.cachedResolution = (fingerprint, resolution)
            self.deliverResolution(resolution)
          case .reresolve:
            lifecycleLog(
              "runtime identity changed while resolving; discarding that answer and re-asking")
            // Cached under neither identity: it describes what was asked about,
            // which is no longer what is installed.
            self.cachedResolution = nil
            self.restartRuntimeResolve(attempt: attempt + 1)
          case .refuse(let reason):
            lifecycleLog("runtime identity kept changing while resolving; refusing")
            self.cachedResolution = nil
            self.deliverResolution(.unusable(reason))
          }
        }
        runtimeResolveProcess = process
      } catch {
        runtimeResolveProcess = nil
        let resolution: RuntimeContract = .unusable(
          "the runtime resolver could not be started: \(error.localizedDescription)")
        cachedResolution = (fingerprint, resolution)
        deliverResolution(resolution)
      }
    }
  }

  /// Ask again, about the identity that is installed now, keeping the callers
  /// that are still waiting for an answer.
  private func restartRuntimeResolve(attempt: Int) {
    let runtimeHome = currentRuntimeHome()
    beginRuntimeResolve(
      runtimeHome: runtimeHome,
      fingerprint: identityFingerprint(runtimeHome: runtimeHome),
      attempt: attempt)
  }

  /// Hand one answer to everybody who asked, exactly once.
  private func deliverResolution(_ resolution: RuntimeContract) {
    let waiters = runtimeResolveWaiters
    runtimeResolveWaiters = []
    for waiter in waiters {
      waiter(resolution)
    }
  }

  private func identityFingerprint(runtimeHome: URL) -> RuntimeIdentityFingerprint {
    RuntimeIdentityFingerprint(
      home: runtimeHome.path,
      pointer: fileStamp(activeRuntimePointerURL(runtimeHome: runtimeHome)),
      receipt: fileStamp(runtimeInstallReceiptURL(runtimeHome: runtimeHome)))
  }

  private func fileStamp(_ url: URL) -> FileStamp? {
    guard
      let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
      let size = attributes[.size] as? Int,
      let modified = attributes[.modificationDate] as? Date
    else {
      return nil
    }
    return FileStamp(size: size, modified: modified)
  }

  /// The one place runtime truth changes.
  ///
  /// Adopting a generation, losing one and being refused one all bump the
  /// epoch, so a caretaker reading or a service action belonging to the
  /// previous generation is dropped instead of repopulating live controls.
  @discardableResult
  private func applyResolution(_ resolution: RuntimeContract) -> CanonicalRuntimeInstall? {
    switch resolution {
    case .ready(let install):
      let changed = install.root.standardizedFileURL.path
        != canonicalInstall?.root.standardizedFileURL.path
      if changed {
        runtimeResolveEpoch &+= 1
        lastCaretakerData = nil
        serverActionInFlight = nil
        runtimeAdvisory = nil
        // A newly adopted generation is allowed to report its own failures.
        workspaceLaunchFailureReported = false
      }
      canonicalInstall = install
      canonicalRuntimeEnvironment = composeRuntimeEnvironment(install: install)
      runtimeResolutionFailure = nil
      if changed {
        model.refreshEndpoint(caretakerData: nil, runtimeReady: true)
        lifecycleLog("adopted active generation at \(install.root.path)")
      }
      return install
    case .absent(let reason):
      discardRuntimeTruth(failure: nil, log: "no installed runtime (\(reason))")
      return nil
    case .unusable(let reason):
      discardRuntimeTruth(failure: reason, log: "installed runtime is unusable: \(reason)")
      return nil
    }
  }

  /// Stop controlling a runtime this App can no longer resolve.
  ///
  /// A stale generation must not stay behind the tray as fallback truth: if the
  /// published pointer breaks, the honest reading is that there is nothing to
  /// control, so the actions go quiet and the reason is rendered in their place.
  private func discardRuntimeTruth(failure: String?, log: String) {
    let hadRuntime = canonicalInstall != nil
    runtimeResolveEpoch &+= 1
    canonicalInstall = nil
    canonicalRuntimeEnvironment = nil
    lastCaretakerData = nil
    serverActionInFlight = nil
    runtimeAdvisory = nil
    runtimeResolutionFailure = failure
    model.refreshEndpoint(caretakerData: nil, runtimeReady: false)
    if hadRuntime || failure != nil {
      lifecycleLog(log)
    }
  }

  /// Attach bounded, continuously drained plumbing to a configured subprocess,
  /// start it, and deliver its outcome on the main thread exactly once.
  ///
  /// Every generation-owned verb the tray calls goes through here. Reading a
  /// pipe only after the child has exited deadlocks as soon as the child writes
  /// more than one pipe buffer, and an unbounded child leaves a tray control —
  /// or, from `applicationShouldTerminate`, the quit itself — waiting forever.
  /// Callers configure the process and own its lifetime reference; the pipes
  /// belong to this helper.
  private func runBounded(
    _ process: Process,
    timeout: TimeInterval,
    label: String,
    stdoutLimit: Int = 1 << 20,
    stderrLimit: Int = 1 << 16,
    completion: @escaping @MainActor @Sendable (BoundedProcessResult) -> Void
  ) throws {
    let output = Pipe()
    let errors = Pipe()
    let stdout = BoundedOutputSink(limit: stdoutLimit)
    let stderr = BoundedOutputSink(limit: stderrLimit)
    process.standardOutput = output
    process.standardError = errors
    output.fileHandleForReading.readabilityHandler = { handle in
      let chunk = handle.availableData
      if chunk.isEmpty {
        handle.readabilityHandler = nil
      } else {
        stdout.absorb(chunk)
      }
    }
    errors.fileHandleForReading.readabilityHandler = { handle in
      let chunk = handle.availableData
      if chunk.isEmpty {
        handle.readabilityHandler = nil
      } else {
        stderr.absorb(chunk)
      }
    }
    process.terminationHandler = { finished in
      drainRemainder(output.fileHandleForReading, into: stdout)
      drainRemainder(errors.fileHandleForReading, into: stderr)
      let result = BoundedProcessResult(
        stdout: stdout.collected,
        stderr: stderr.collected,
        terminationStatus: finished.terminationStatus,
        clean: finished.terminationReason == .exit)
      // Foundation runs this handler on its own queue, so the completion has to
      // hop before it touches anything the delegate owns. Once it is on the
      // main queue the isolation is a fact, not an assumption, and asserting it
      // keeps the callback main-actor typed instead of laundering tray state
      // through unchecked mutable sharing.
      DispatchQueue.main.async {
        MainActor.assumeIsolated {
          completion(result)
        }
      }
    }
    do {
      try process.run()
    } catch {
      output.fileHandleForReading.readabilityHandler = nil
      errors.fileHandleForReading.readabilityHandler = nil
      process.terminationHandler = nil
      throw error
    }
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak process] in
      guard let process, process.isRunning else { return }
      lifecycleLog("\(label) exceeded \(Int(timeout))s; terminating pid=\(process.processIdentifier)")
      process.terminate()
      DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak process] in
        guard let process, process.isRunning else { return }
        kill(process.processIdentifier, SIGKILL)
      }
    }
  }

  /// Ensure the canonical shared service exists.
  ///
  /// Keeping the shared caretaker available is the App's job and the canonical
  /// verb is idempotent, so asking for it on open is legitimate. Asking blindly
  /// is not: the previous version discarded stdout, stderr and the exit status,
  /// so a refused or leased reconcile was indistinguishable from a successful
  /// one. Recovery ownership stays with the supervisor; this asks once,
  /// observes the answer and reports it.
  private func reconcileControlPlaneEye(
    install: CanonicalRuntimeInstall, environment: [String: String]
  ) {
    // One reconcile at a time: overlapping calls would race the supervisor's
    // install lease against itself.
    guard eyeReconcileProcess?.isRunning != true else {
      lifecycleLog("service reconcile already in flight; not starting a second")
      return
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      surfaceRuntimeAdvisory("The installed service owner is missing: \(deck.path)")
      return
    }
    let epoch = runtimeResolveEpoch
    let process = Process()
    process.executableURL = deck
    process.arguments = ["server", "service", "reconcile"]
    process.environment = environment
    do {
      try runBounded(process, timeout: 120, label: "server service reconcile") {
        [weak self] result in
        guard let self else { return }
        self.eyeReconcileProcess = nil
        guard epoch == self.runtimeResolveEpoch else { return }
        guard result.clean, result.terminationStatus == 0 else {
          let detail = boundedResolverDiagnostic(stdout: result.stdout, stderr: result.stderr)
          self.surfaceRuntimeAdvisory(
            "The shared VC Server service could not be reconciled" + detail)
          self.renderServerStatus()
          return
        }
        self.runtimeAdvisory = nil
        lifecycleLog("shared service reconciled on \(install.root.lastPathComponent)")
        self.renderServerStatus()
      }
      eyeReconcileProcess = process
    } catch {
      surfaceRuntimeAdvisory(
        "The shared VC Server service could not be reconciled: \(error.localizedDescription)")
    }
  }

  /// Report something about the shared service without interrupting the
  /// Founder: the log for post-mortem, the tray for the current truth.
  private func surfaceRuntimeAdvisory(_ message: String) {
    runtimeAdvisory = message
    installLog.error("\(message, privacy: .public)")
    lifecycleLog(message)
    applyRuntimePackMenuState()
  }

  private func registerBundledFonts() throws {
    let font = Bundle.main.bundleURL.appendingPathComponent(
      "Contents/Resources/fonts/SpotMono.ttc")
    guard FileManager.default.fileExists(atPath: font.path) else {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.fonts", code: 1,
        userInfo: [NSLocalizedDescriptionKey: "bundled SpotMono.ttc is missing"])
    }

    var registrationError: Unmanaged<CFError>?
    if !CTFontManagerRegisterFontsForURL(font as CFURL, .session, &registrationError) {
      let message =
        registrationError?.takeRetainedValue().localizedDescription
        ?? "CoreText rejected SpotMono.ttc"
      // A system-installed Spot Mono can already occupy the session scope.
      // Accept that case only when CoreText resolves the required family.
      let descriptor = CTFontDescriptorCreateWithAttributes(
        [kCTFontFamilyNameAttribute as String: "Spot Mono"] as CFDictionary)
      guard let match = CTFontDescriptorCreateMatchingFontDescriptor(descriptor, nil),
        CTFontDescriptorCopyAttribute(match, kCTFontFamilyNameAttribute) as? String == "Spot Mono"
      else {
        throw NSError(
          domain: "io.vetcoders.vibecrafted.fonts", code: 2,
          userInfo: [NSLocalizedDescriptionKey: message])
      }
    }
  }

  /// Read the revision tuple this signed bundle ships and record it for the
  /// tray. Carrier identity is a property of the App, not of any installation,
  /// so drift supervision must not depend on having just run the installer.
  @discardableResult
  private func loadSignedCarrierRevisions() throws -> (
    source: String, terminal: String, frame: String
  ) {
    let resources = Bundle.main.bundleURL.appendingPathComponent(
      "Contents/Resources", isDirectory: true)
    let manifestData = try Data(
      contentsOf: resources.appendingPathComponent("product-manifest.json"))
    guard
      let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
      let sourceRevision = manifest["git_sha"] as? String,
      let modules = manifest["modules"] as? [[String: Any]],
      let terminalRevision = modules.first(where: { $0["module"] as? String == "vc-terminal" })?[
        "git_sha"] as? String,
      let frameRevision = modules.first(where: { $0["module"] as? String == "vc-frame" })?[
        "git_sha"] as? String
    else {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install", code: 1,
        userInfo: [
          NSLocalizedDescriptionKey: "signed product manifest has no Runtime Pack source tuple"
        ])
    }
    signedCarrierRevisions = (sourceRevision, terminalRevision, frameRevision)
    return (sourceRevision, terminalRevision, frameRevision)
  }

  private func installCanonicalRuntime() throws -> CanonicalRuntimeInstall {
    let appRoot = Bundle.main.bundleURL
    let resources = appRoot.appendingPathComponent("Contents/Resources", isDirectory: true)
    let carrierDirectory = resources.appendingPathComponent("runtime-pack", isDirectory: true)
    let carriers = try FileManager.default.contentsOfDirectory(
      at: carrierDirectory, includingPropertiesForKeys: nil
    ).filter {
      $0.lastPathComponent.hasPrefix("Vibecrafted_RuntimePack_") && $0.pathExtension == "gz"
    }
    guard carriers.count == 1 else {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install", code: 1,
        userInfo: [NSLocalizedDescriptionKey: "signed App must contain one Runtime Pack carrier"])
    }
    let (sourceRevision, terminalRevision, frameRevision) = try loadSignedCarrierRevisions()
    let terminalHost = appRoot.appendingPathComponent(
      "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty")
    let frameHelper = appRoot.appendingPathComponent("Contents/Helpers/vc-frame")
    let output = try runRuntimePackInstaller(arguments: [
      "--pack", carriers[0].path,
      "--app-root", appRoot.path,
      "--terminal-host", terminalHost.path,
      "--frame-helper", frameHelper.path,
      "--expected-source-revision", sourceRevision,
      "--expected-terminal-revision", terminalRevision,
      "--expected-frame-revision", frameRevision,
    ])
    do {
      return try JSONDecoder().decode(CanonicalRuntimeInstall.self, from: output)
    } catch {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install", code: 2,
        userInfo: [
          NSLocalizedDescriptionKey:
            "installer returned an invalid runtime result: \(error.localizedDescription)"
        ])
    }
  }

  private func uninstallCanonicalRuntime() throws {
    _ = try runRuntimePackInstaller(arguments: ["--uninstall"])
  }

  private func runRuntimePackInstaller(arguments: [String]) throws -> Data {
    let carrierDirectory = Bundle.main.bundleURL.appendingPathComponent(
      "Contents/Resources/runtime-pack", isDirectory: true)
    let installer = carrierDirectory.appendingPathComponent("install-runtime-pack.sh")
    let publicKey = carrierDirectory.appendingPathComponent("vibecrafted-signing-v1.pub")
    guard FileManager.default.isExecutableFile(atPath: installer.path),
      FileManager.default.fileExists(atPath: publicKey.path)
    else {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install", code: 1,
        userInfo: [
          NSLocalizedDescriptionKey:
            "signed Runtime Pack bootstrap or trust root is missing"
        ])
    }

    let process = Process()
    let output = Pipe()
    let errors = Pipe()
    process.executableURL = URL(fileURLWithPath: "/bin/bash")
    process.arguments = [installer.path] + arguments
    var environment = ProcessInfo.processInfo.environment
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY"] = publicKey.path
    process.environment = environment
    process.standardOutput = output
    process.standardError = errors
    try process.run()
    process.waitUntilExit()

    let result = output.fileHandleForReading.readDataToEndOfFile()
    let failure = errors.fileHandleForReading.readDataToEndOfFile()
    guard process.terminationStatus == 0 else {
      // The installer is a Python program and prints a traceback when it
      // refuses. Reducing it to the owner's actual message is what keeps a
      // stack of interpreter frames out of a normal repair dialog.
      let diagnostic = boundedResolverDiagnostic(
        stdout: result, stderr: failure, limit: 480)
      let detail =
        "the Runtime Pack installer exited \(process.terminationStatus)\(diagnostic)"
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install",
        code: Int(process.terminationStatus),
        userInfo: [NSLocalizedDescriptionKey: detail])
    }
    return result
  }

  /// Inherited PATH first, then the signed generation fallback; use the minimal
  /// system set only when the caller carried no PATH at all.
  private func composedPath(generation: URL, inherited: String?) -> String {
    let generationBin = generation.appendingPathComponent("bin").path
    let head = (inherited ?? "").isEmpty ? "/usr/bin:/bin:/usr/sbin:/sbin" : inherited!
    let entries = head.split(separator: ":").map(String.init).filter { $0 != generationBin }
    return (entries + [generationBin]).joined(separator: ":")
  }

  /// Surface a launch failure where the operator can actually see it: the unified
  /// log for post-mortem, plus one modal so a broken install is never silent.
  private func reportWorkspaceLaunchFailure(_ message: String) {
    installLog.error("\(message, privacy: .public)")
    fputs("Vibecrafted workspace launch failed: \(message)\n", stderr)
    guard !workspaceLaunchFailureReported else { return }
    workspaceLaunchFailureReported = true
    let alert = NSAlert()
    alert.alertStyle = .critical
    alert.messageText = "Vibecrafted cannot open its workspace terminal"
    alert.informativeText = message
    alert.addButton(withTitle: "OK")
    alert.runModal()
  }

  // MARK: - Main Menu

  private func buildStatusItem() {
    tray = StatusItemController { [weak self] action in self?.handleTray(action) }
    tray?.install()
    statusRefreshTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
      Task { @MainActor [weak self] in self?.refreshServerStatus() }
    }
  }

  private func refreshServerStatus() {
    if let launch = terminalLaunch, !launch.isRunning {
      terminalRegistrationTimer?.invalidate()
      terminalRegistration = nil
      terminalLaunch = nil
      terminalApplication = nil
      if let status = launch.exitStatus, status != 0 {
        showNativeMessage("Terminal exited", "The generation-owned terminal exited with status \(status). Open Terminal to try again.")
      }
    }
    // Render what is already known, then re-ask the owner. The active pointer
    // can move under a running App — a runtime-first upgrade republishes it
    // while the tray is open — so liveness and the service actions must follow
    // the generation the Founder is actually running rather than a root cached
    // at launch forever.
    renderServerStatus()
    resolveInstalledRuntime { [weak self] resolution in
      guard let self else { return }
      guard let install = self.applyResolution(resolution),
        let environment = self.canonicalRuntimeEnvironment
      else {
        self.renderServerStatus()
        return
      }
      self.pollCaretaker(install: install, environment: environment)
    }
  }

  /// Read the caretaker verdict of the resolved generation.
  ///
  /// One verb, one truth: the caretaker builds the envelope, publishes it for
  /// every other reader (the HTTP route serves the same bytes), and prints the
  /// already-derived verdict. The tray renders; it never re-derives.
  private func pollCaretaker(install: CanonicalRuntimeInstall, environment: [String: String]) {
    guard serverStatusProcess?.isRunning != true else { return }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      lastCaretakerData = nil
      renderServerStatus()
      return
    }
    let epoch = runtimeResolveEpoch
    let process = Process()
    process.executableURL = deck
    process.arguments = serverCaretakerArguments()
    process.environment = environment
    do {
      try runBounded(process, timeout: 30, label: "server caretaker") { [weak self] result in
        guard let self else { return }
        self.serverStatusProcess = nil
        // A verdict about a generation this App no longer controls is not the
        // current truth, however recently it arrived.
        guard epoch == self.runtimeResolveEpoch else { return }
        self.lastCaretakerData = result.clean && result.terminationStatus == 0
          && !result.stdout.isEmpty ? result.stdout : nil
        self.renderServerStatus()
      }
      serverStatusProcess = process
    } catch {
      serverStatusProcess = nil
      lastCaretakerData = nil
      renderServerStatus()
    }
  }

  /// Paint the tray from cached truth alone. This spawns nothing, so it is safe
  /// to call from any completion handler.
  private func renderServerStatus() {
    if canonicalInstall == nil, let reason = runtimeResolutionFailure {
      model.block(reason: reason)
    } else {
      model.refreshEndpoint(caretakerData: lastCaretakerData, runtimeReady: canonicalInstall != nil)
    }
    updateDeckPresentation()
  }

  private func applyRuntimePackMenuState() { updateDeckPresentation() }

  private func updateDeckPresentation() {
    let state = deriveServerMenuState(caretakerData: lastCaretakerData,
      actionInFlight: serverActionInFlight, runtimeReady: canonicalInstall != nil)
    var actions: Set<CommandDeckChromeAction> = [.showDiagnostics]
    if !repairInFlight { actions.insert(.repairRuntime) }
    if runtimeActionPreflight == nil && serverActionInFlight == nil && !repairInFlight {
      actions.insert(.retryConnection)
    }
    if canonicalInstall != nil && !terminalLaunchInFlight { actions.insert(.openTerminal) }
    if state.canStop && runtimeActionPreflight == nil { actions.insert(.requestStopRuntime) }
    model.availableActions = actions
    let presentation = model.presentation
    let runtimePack = deriveRuntimePackMenuState(
      generation: canonicalInstall?.root.lastPathComponent,
      signedSourceRevision: signedCarrierRevisions?.source,
      runtimeReady: canonicalInstall != nil)
    let detail: String
    switch model.state {
    case .blocked(let reason), .recovering(let reason): detail = runtimeResolutionFailure ?? reason
    default: detail = runtimeAdvisory ?? state.detail
    }
    var utilities: Set<StatusItemAction> = []
    if canonicalInstall != nil {
      utilities.formUnion([.revealRuntime, .revealControlPlane, .copyRuntimeIdentity])
      if serverUtilityProcess?.isRunning != true && runtimeActionPreflight == nil { utilities.insert(.showLogs) }
    }
    if state.canStart { utilities.insert(.startServer) }
    if state.canRestart { utilities.insert(.restartServer) }
    let health: TrayServerHealth
    switch presentation.phase {
    case .bootstrapping, .connecting: health = .checking
    case .online: health = state.health
    case .recovering, .blocked: health = .failed
    }
    tray?.update(StatusItemPresentation(
      health: health,
      statusLine: "Command Deck: \(presentation.phase.rawValue)", detailLine: detail,
      availability: StatusItemAvailability(canShowCommandDeck: true,
        canOpenTerminal: actions.contains(.openTerminal),
        canRetryConnection: actions.contains(.retryConnection),
        canRepairRuntime: actions.contains(.repairRuntime),
        canStopRuntime: actions.contains(.requestStopRuntime),
        canShowDiagnostics: true, canQuitApp: true, runtimeActions: utilities),
      toolTip: "Vibecrafted — \(presentation.phase.rawValue). \(detail). \(runtimePack.header). \(runtimePack.detail)"))
  }

  private func revealNativePath(_ url: URL) {
    do { try nativeBridge.perform(.revealPath(try .init(path: url.path))) }
    catch { showNativeMessage("Cannot reveal path", error.localizedDescription) }
  }

  @objc private func revealRuntimeHomeFromStatusItem() {
    guard let install = canonicalInstall else { return }
    revealNativePath(install.runtimeHome)
  }

  @objc private func openControlPlaneFromStatusItem() {
    guard let install = canonicalInstall else { return }
    let controlPlane = install.craftedHome.appendingPathComponent(
      "control_plane", isDirectory: true)
    revealNativePath(
      FileManager.default.fileExists(atPath: controlPlane.path)
        ? controlPlane : install.craftedHome)
  }

  @objc private func copyRuntimeIdentityFromStatusItem() {
    guard let install = canonicalInstall else { return }
    let blob = runtimeIdentityBlob(
      generation: install.root.lastPathComponent,
      sourceRevision: signedCarrierRevisions?.source,
      terminalRevision: signedCarrierRevisions?.terminal,
      frameRevision: signedCarrierRevisions?.frame,
      runtimeHome: install.runtimeHome.path,
      configHome: install.configHome.path)
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(blob, forType: .string)
  }

  /// Explicit repair, proportionate to what is actually wrong.
  ///
  /// The owner is asked first, read-only. Configuration drift is then repaired
  /// *as configuration*, through the same merge, lease and transaction a
  /// reinstall uses — because republishing this App's carrier to fix a merge is
  /// both heavier than the problem and refused outright when the installation
  /// is newer than the carrier. That refusal is how a merge problem became a
  /// dead end. Reinstalling stays available, named, and the Founder's choice.
  @objc private func repairRuntime() {
    guard !repairInFlight, runtimeActionPreflight == nil, serverActionInFlight == nil
    else { return }
    repairInFlight = true
    updateDeckPresentation()
    runConfigRepair(plan: true) { [weak self] outcome in
      guard let self else { return }
      self.repairInFlight = false
      self.updateDeckPresentation()
      switch outcome {
      case .repairable(let envelope), .conflict(let envelope), .repaired(let envelope):
        self.lastConfigRepair = envelope
        self.offerConfigurationRepair(envelope)
      case .healthy(let envelope):
        self.lastConfigRepair = envelope
        self.offerRuntimePackReinstall(
          configuration: "Configuration already matches the installed generation.")
      case .absent(let reason):
        self.offerRuntimePackReinstall(configuration: reason)
      case .unusable(let reason):
        self.offerRuntimePackReinstall(
          configuration: "Configuration could not be inspected: \(reason)")
      }
    }
  }

  /// Put the small remedy first and the large one beside it, both named.
  private func offerConfigurationRepair(_ envelope: ConfigRepairEnvelope) {
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = "Repair Vibecrafted configuration?"
    alert.informativeText =
      configRepairSummary(envelope)
      + "\n\nRepairing keeps your settings and preserves a copy of anything it changes. "
      + "It does not replace the installed runtime."
    alert.addButton(withTitle: "Cancel")
    alert.addButton(withTitle: "Repair Configuration")
    alert.addButton(withTitle: "Reinstall Runtime…")
    switch alert.runModal() {
    case .alertSecondButtonReturn: applyConfigurationRepair()
    case .alertThirdButtonReturn:
      offerRuntimePackReinstall(configuration: configRepairSummary(envelope))
    default: return
    }
  }

  /// Run the owner for real, on the generation that is installed now.
  private func applyConfigurationRepair() {
    guard !repairInFlight, runtimeActionPreflight == nil, serverActionInFlight == nil
    else { return }
    repairInFlight = true
    updateDeckPresentation()
    runConfigRepair(plan: false) { [weak self] outcome in
      guard let self else { return }
      self.repairInFlight = false
      switch outcome {
      case .repaired(let envelope), .healthy(let envelope), .conflict(let envelope),
        .repairable(let envelope):
        self.lastConfigRepair = envelope
        // Configuration moved under the installation, so any cached answer
        // about it is stale by construction.
        self.cachedResolution = nil
        self.presentConfigRepairResult(envelope)
        self.refreshServerStatus()
      case .absent(let reason), .unusable(let reason):
        self.showNativeMessage("Vibecrafted could not repair its configuration", reason)
      }
      self.updateDeckPresentation()
    }
  }

  /// The visible typed outcome: counts, files, reasons, preserved copies.
  private func presentConfigRepairResult(_ envelope: ConfigRepairEnvelope) {
    lifecycleLog(
      "configuration repair: \(envelope.status ?? "unknown") "
        + "repaired=\(envelope.repaired ?? 0) conflicts=\(envelope.conflicts ?? 0)")
    runtimeAdvisory = configRepairAdvisory(envelope)
    let alert = NSAlert()
    alert.alertStyle = envelope.status == "conflict" ? .warning : .informational
    alert.messageText = "Vibecrafted configuration"
    alert.informativeText = configRepairSummary(envelope)
    alert.addButton(withTitle: "OK")
    alert.runModal()
  }

  /// Publish this App's signed carrier over the current installation.
  ///
  /// Normal opening never reaches here — it resolves whatever is installed —
  /// so replacing a runtime stays something the Founder asks for, with the
  /// generation that would be written named before the fact.
  private func offerRuntimePackReinstall(configuration: String) {
    guard !repairInFlight, runtimeActionPreflight == nil, serverActionInFlight == nil
    else { return }
    let confirmation = NSAlert()
    confirmation.alertStyle = .warning
    confirmation.messageText = "Reinstall the Vibecrafted runtime from this App?"
    let installed = canonicalInstall.map { "Installed generation: \($0.root.lastPathComponent).\n" }
      ?? "No usable runtime is currently installed.\n"
    confirmation.informativeText =
      installed
      + configuration + "\n"
      + "This publishes the Runtime Pack carried by this App"
      + (signedCarrierRevisions.map { " (source \(String($0.source.prefix(8))))" } ?? "")
      + ". The installer refuses to replace a newer runtime with an older carrier."
    confirmation.addButton(withTitle: "Cancel")
    confirmation.addButton(withTitle: "Reinstall")
    guard confirmation.runModal() == .alertSecondButtonReturn else { return }
    repairInFlight = true
    updateDeckPresentation()
    defer { repairInFlight = false; updateDeckPresentation() }
    let install: CanonicalRuntimeInstall
    do {
      install = try installCanonicalRuntime()
    } catch {
      lifecycleLog("runtime repair failed: \(error.localizedDescription)")
      let failure = NSAlert()
      failure.alertStyle = .critical
      failure.messageText = "Vibecrafted could not reinstall its runtime"
      failure.informativeText = error.localizedDescription
      failure.addButton(withTitle: "OK")
      failure.runModal()
      return
    }
    // Repair just republished the installation, so any cached resolution — and
    // any reading or action still in flight for the previous generation — is
    // stale by construction.
    cachedResolution = nil
    guard let repaired = applyResolution(.ready(install)),
      let environment = canonicalRuntimeEnvironment
    else {
      return
    }
    // A repaired install is allowed to report its launch failures again.
    workspaceLaunchFailureReported = false
    lifecycleLog("runtime repaired to generation \(repaired.root.lastPathComponent)")
    reconcileControlPlaneEye(install: repaired, environment: environment)
    applyRuntimePackMenuState()
    refreshServerStatus()
    webSession.retry()
  }

  @objc private func openConsoleFromStatusItem() { openRoute("/") }
  @objc private func openServerFromStatusItem() { openRoute("/") }
  @objc private func openWorkspacesFromStatusItem() { openRoute("/workspaces") }

  private func openRoute(_ path: String) {
    showMainWindowIfNeeded()
    webSession.navigate(path: path)
    refreshServerStatus()
  }

  @objc private func openTerminalFromStatusItem() {
    do {
      try nativeBridge.perform(.openTerminal(.init(
        workingDirectory: try .init(path: terminalWorkingDirectory.path))))
    } catch { reportWorkspaceLaunchFailure(error.localizedDescription) }
  }

  @objc private func startServerFromStatusItem() {
    performServerAction(.start)
  }

  @objc private func stopServerFromStatusItem() { handle(.requestStopRuntime) }

  @objc private func restartServerFromStatusItem() {
    performServerAction(.restart)
  }

  /// Start, stop or restart the shared service — on the generation that is
  /// installed now, never on one the tray merely remembers.
  ///
  /// The active pointer can move between the menu being drawn and an item being
  /// clicked, and the refresh that would notice it is asynchronous. So the owner
  /// is asked again here and the verb runs against whatever that answer adopts.
  /// An unchanged identity is answered from cache and spawns nothing, which
  /// leaves the ordinary click exactly as immediate as it was.
  private func performServerAction(_ action: ServerLifecycleAction) {
    guard serverActionProcess?.isRunning != true, runtimeActionPreflight == nil else { return }
    runtimeActionPreflight = "server \(action.rawValue)"
    // The transition is honest from the click rather than from the spawn: the
    // Founder asked for it and the App is already working on it.
    serverActionInFlight = action
    renderServerStatus()
    resolveInstalledRuntime { [weak self] resolution in
      guard let self else { return }
      self.runtimeActionPreflight = nil
      guard let install = self.applyResolution(resolution),
        let environment = self.canonicalRuntimeEnvironment
      else {
        // Adoption failed, so there is nothing to act on. Say why, instead of
        // running a service verb through a generation this App no longer
        // controls.
        self.serverActionInFlight = nil
        self.renderServerStatus()
        self.reportWorkspaceLaunchFailure(
          self.runtimeResolutionFailure.map {
            "Cannot \(action.rawValue) VC Server: \($0)"
          } ?? "Cannot \(action.rawValue) VC Server before runtime onboarding completes")
        return
      }
      if action == .stop {
        guard self.confirmedStopRoot == install.root,
          deriveServerMenuState(caretakerData: self.lastCaretakerData,
            actionInFlight: nil, runtimeReady: true).canStop else {
          self.confirmedStopRoot = nil
          self.serverActionInFlight = nil
          self.renderServerStatus()
          self.reportWorkspaceLaunchFailure("Runtime identity or stop availability changed; review Stop Runtime again.")
          return
        }
        self.confirmedStopRoot = nil
      }
      self.runServerAction(action, install: install, environment: environment)
    }
  }

  /// Run the canonical service verb against an identity that was just adopted.
  ///
  /// The epoch is read after that adoption, so this action belongs to the
  /// generation it addresses: if the pointer moves again while the verb runs,
  /// its completion is dropped rather than allowed to repaint the tray.
  private func runServerAction(
    _ action: ServerLifecycleAction,
    install: CanonicalRuntimeInstall,
    environment: [String: String]
  ) {
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      serverActionInFlight = nil
      renderServerStatus()
      reportWorkspaceLaunchFailure("Canonical server launcher is missing: \(deck.path)")
      return
    }

    let epoch = runtimeResolveEpoch
    let process = Process()
    process.executableURL = deck
    process.arguments = serverActionArguments(for: action)
    process.environment = environment
    do {
      try runBounded(process, timeout: 120, label: "server \(action.rawValue)") {
        [weak self] result in
        guard let self else { return }
        self.serverActionProcess = nil
        // An action that finished against a generation this App no longer
        // controls must not clear the current transition or repaint the tray.
        guard epoch == self.runtimeResolveEpoch else { return }
        self.serverActionInFlight = nil
        if !result.clean || result.terminationStatus != 0 {
          let detail = String(
            data: result.stderr.isEmpty ? result.stdout : result.stderr, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "Canonical service owner exited \(result.terminationStatus)"
          let alert = NSAlert()
          alert.alertStyle = .critical
          alert.messageText = "Vibecrafted could not \(action.rawValue) VC Server"
          alert.informativeText = detail
          alert.addButton(withTitle: "OK")
          alert.runModal()
        }
        self.refreshServerStatus()
      }
      serverActionProcess = process
      // Adoption may have cleared the transition on the way here; the verb is
      // running, so it is true again.
      serverActionInFlight = action
      renderServerStatus()
    } catch {
      serverActionProcess = nil
      serverActionInFlight = nil
      renderServerStatus()
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted could not \(action.rawValue) VC Server"
      alert.informativeText = error.localizedDescription
      alert.runModal()
    }
  }

  @objc private func openServerLogsFromStatusItem() {
    guard serverUtilityProcess?.isRunning != true, runtimeActionPreflight == nil else { return }
    // Same rule as the service verbs: the log location belongs to the
    // generation that is installed now, so the owner is asked before the deck
    // of a remembered one is executed.
    runtimeActionPreflight = "server logs"
    resolveInstalledRuntime { [weak self] resolution in
      guard let self else { return }
      self.runtimeActionPreflight = nil
      guard let install = self.applyResolution(resolution),
        let environment = self.canonicalRuntimeEnvironment
      else {
        self.renderServerStatus()
        self.reportWorkspaceLaunchFailure(
          self.runtimeResolutionFailure.map { "Cannot open VC Server logs: \($0)" }
            ?? "Cannot open VC Server logs before runtime onboarding completes")
        return
      }
      self.runServerLogs(install: install, environment: environment)
    }
  }

  /// Ask the canonical service owner where its logs are, on the generation that
  /// was just adopted. The epoch is read after that adoption, so a location
  /// belonging to a runtime this App no longer controls is never opened.
  private func runServerLogs(install: CanonicalRuntimeInstall, environment: [String: String]) {
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      renderServerStatus()
      reportWorkspaceLaunchFailure("Canonical server launcher is missing: \(deck.path)")
      return
    }

    let epoch = runtimeResolveEpoch
    let process = Process()
    process.executableURL = deck
    process.arguments = ["server", "service", "logs", "--json"]
    process.environment = environment
    do {
      try runBounded(process, timeout: 30, label: "server service logs") { [weak self] result in
        guard let self else { return }
        self.serverUtilityProcess = nil
        guard epoch == self.runtimeResolveEpoch else { return }
        if result.clean, result.terminationStatus == 0,
          let logs = decodeServerLogs(data: result.stdout)
        {
          self.revealNativePath(logs.directory)
        } else {
          let detail = String(
            data: result.stderr.isEmpty ? result.stdout : result.stderr, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "Canonical service owner did not return its log location"
          let alert = NSAlert()
          alert.alertStyle = .critical
          alert.messageText = "Vibecrafted could not open VC Server logs"
          alert.informativeText = detail
          alert.addButton(withTitle: "OK")
          alert.runModal()
        }
        self.refreshServerStatus()
      }
      serverUtilityProcess = process
    } catch {
      serverUtilityProcess = nil
      renderServerStatus()
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted could not open VC Server logs"
      alert.informativeText = error.localizedDescription
      alert.runModal()
    }
  }

  @objc private func showServerDiagnostics() {
    let envelope = decodeCaretakerEnvelope(data: lastCaretakerData)
    let alert = NSAlert()
    alert.alertStyle = envelope?.verdict?.health == "healthy" ? .informational : .warning
    alert.messageText = "Vibecrafted Server"
    var lines = caretakerDiagnosticsLines(data: lastCaretakerData)
    if let configuration = lastConfigRepair {
      lines.append("")
      lines.append("Configuration")
      lines.append(configRepairSummary(configuration))
    }
    alert.informativeText = lines.joined(separator: "\n")
    alert.addButton(withTitle: "OK")
    alert.addButton(withTitle: "Open Console")
    if alert.runModal() == .alertSecondButtonReturn {
      showMainWindowIfNeeded()
    }
  }

  @objc private func showStatusItemHelp() {
    let alert = NSAlert()
    alert.alertStyle = .informational
    alert.messageText = "Vibecrafted Help"
    alert.informativeText =
      "The tray reports the combined native connection and web canvas state. Open VC Server and Open Workspaces use the configured live server only when its caretaker says it is available. Console and Workspaces stay in the Command Deck web session. Reveal Control Plane Files opens the on-disk runtime state."
    alert.addButton(withTitle: "OK")
    alert.runModal()
  }

  private func activeRunSummary() -> RuntimeActivityTruth {
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      return .unavailable("canonical runtime onboarding is incomplete")
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      return .unavailable("canonical lifecycle launcher is missing")
    }
    let output = Pipe()
    let process = Process()
    process.executableURL = deck
    process.arguments = ["status", "--activity", "--json"]
    process.environment = environment
    process.standardOutput = output
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
    } catch {
      return .unavailable(error.localizedDescription)
    }
    // Stop confirmation names the current activity. A bounded failure is
    // reported as unknown activity; Quit App never enters this read path.
    let deadline = Date().addingTimeInterval(activityTruthTimeout)
    while process.isRunning, Date() < deadline {
      usleep(50_000)
    }
    guard !process.isRunning else {
      process.terminate()
      usleep(200_000)
      if process.isRunning {
        kill(process.processIdentifier, SIGKILL)
      }
      lifecycleLog("lifecycle activity read exceeded \(Int(activityTruthTimeout))s; terminated")
      return .unavailable(
        "the canonical lifecycle launcher did not answer within \(Int(activityTruthTimeout))s")
    }
    let data = output.fileHandleForReading.readDataToEndOfFile()
    return decodeRuntimeActivityTruth(data: data, terminationStatus: process.terminationStatus)
  }

  @objc private func requestQuit() {
    NSApp.terminate(nil)
  }

  private func buildMainMenu() {
    let mainMenu = NSMenu()

    // Application menu
    let appMenu = NSMenu()
    appMenu.addItem(
      withTitle: "About Vibecrafted",
      action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    appMenu.addItem(.separator())
    appMenu.addItem(
      withTitle: "Hide Vibecrafted", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
    let hideOthers = appMenu.addItem(
      withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)),
      keyEquivalent: "h")
    hideOthers.keyEquivalentModifierMask = [.command, .option]
    appMenu.addItem(
      withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)),
      keyEquivalent: "")
    appMenu.addItem(.separator())
    let appQuit = appMenu.addItem(
      withTitle: "Quit Vibecrafted", action: #selector(requestQuit), keyEquivalent: "q")
    appQuit.target = self

    let appMenuItem = NSMenuItem()
    appMenuItem.submenu = appMenu
    mainMenu.addItem(appMenuItem)

    // File menu
    let fileMenu = NSMenu(title: "File")
    fileMenu.addItem(
      withTitle: "Close Window", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")

    let fileMenuItem = NSMenuItem()
    fileMenuItem.submenu = fileMenu
    mainMenu.addItem(fileMenuItem)

    let viewMenu = NSMenu(title: "View")
    for (title, selector, key) in [
      ("Console", #selector(openConsoleFromStatusItem), "1"),
      ("Server", #selector(openServerFromStatusItem), "2"),
      ("Workspaces", #selector(openWorkspacesFromStatusItem), "3")
    ] {
      let item = viewMenu.addItem(withTitle: title, action: selector, keyEquivalent: key)
      item.target = self
    }
    viewMenu.addItem(.separator())
    // History verbs act on the selected tab only; validation reads its state.
    let home = viewMenu.addItem(withTitle: "Home", action: #selector(goHome), keyEquivalent: "h")
    home.keyEquivalentModifierMask = [.command, .shift]
    home.target = self
    let back = viewMenu.addItem(withTitle: "Back", action: #selector(goBack), keyEquivalent: "[")
    back.target = self
    let forward = viewMenu.addItem(withTitle: "Forward", action: #selector(goForward), keyEquivalent: "]")
    forward.target = self
    let inBrowser = viewMenu.addItem(
      withTitle: "Open Page in Browser", action: #selector(openSelectedPageInBrowser), keyEquivalent: "")
    inBrowser.target = self
    viewMenu.addItem(.separator())
    let openInTab = NSMenu(title: "Open in Tab")
    let openExternal = NSMenu(title: "Open in Browser")
    for destination in ToolDestination.catalog {
      let tabItem = openInTab.addItem(
        withTitle: destination.title, action: #selector(openDestinationInTab(_:)), keyEquivalent: "")
      tabItem.target = self
      tabItem.representedObject = destination.id
      let externalItem = openExternal.addItem(
        withTitle: destination.title, action: #selector(openDestinationExternally(_:)), keyEquivalent: "")
      externalItem.target = self
      externalItem.representedObject = destination.id
    }
    let openInTabItem = viewMenu.addItem(withTitle: "Open in Tab", action: nil, keyEquivalent: "")
    openInTabItem.submenu = openInTab
    let openExternalItem = viewMenu.addItem(withTitle: "Open in Browser", action: nil, keyEquivalent: "")
    openExternalItem.submenu = openExternal
    let viewMenuItem = NSMenuItem()
    viewMenuItem.submenu = viewMenu
    mainMenu.addItem(viewMenuItem)

    let editMenu = NSMenu(title: "Edit")
    for (title, selector, key) in [
      ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
      ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")
    ] { editMenu.addItem(withTitle: title, action: NSSelectorFromString(selector), keyEquivalent: key) }
    let editItem = NSMenuItem()
    editItem.submenu = editMenu
    mainMenu.addItem(editItem)

    // Window menu
    let windowMenu = NSMenu(title: "Window")
    windowMenu.addItem(
      withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
    windowMenu.addItem(
      withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
    windowMenu.addItem(.separator())
    // Native tab verbs: AppKit implements them for any window in a tab group.
    let previousTab = windowMenu.addItem(
      withTitle: "Show Previous Tab", action: #selector(NSWindow.selectPreviousTab(_:)), keyEquivalent: "{")
    previousTab.keyEquivalentModifierMask = [.command, .shift]
    let nextTab = windowMenu.addItem(
      withTitle: "Show Next Tab", action: #selector(NSWindow.selectNextTab(_:)), keyEquivalent: "}")
    nextTab.keyEquivalentModifierMask = [.command, .shift]
    windowMenu.addItem(
      withTitle: "Merge All Windows", action: #selector(NSWindow.mergeAllWindows(_:)), keyEquivalent: "")

    let windowMenuItem = NSMenuItem()
    windowMenuItem.submenu = windowMenu
    mainMenu.addItem(windowMenuItem)
    NSApp.windowsMenu = windowMenu

    // Help menu
    let helpMenu = NSMenu(title: "Help")
    let helpMenuItem = NSMenuItem()
    helpMenuItem.submenu = helpMenu
    mainMenu.addItem(helpMenuItem)
    NSApp.helpMenu = helpMenu

    NSApp.mainMenu = mainMenu
  }
}
