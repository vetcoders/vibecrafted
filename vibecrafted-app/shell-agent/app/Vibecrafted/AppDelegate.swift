import AppKit
import CoreText
import Darwin
import os.log

private let installLog = Logger(subsystem: "io.vetcoders.vibecrafted", category: "install")

/// Durable lifecycle trail. The Runtime Pack installer owns initialization of
/// `<crafted home>/logs/app-lifecycle.log`; the UI host only appends events to
/// that canonical file and never becomes a second installer implementation.
private func lifecycleLog(_ event: String) {
  let stamp = ISO8601DateFormatter().string(from: Date())
  let line = "\(stamp) pid=\(getpid()) \(event)\n"
  installLog.notice("\(event, privacy: .public)")
  let host = ProcessInfo.processInfo.environment
  let home = host["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
  let crafted = host["VIBECRAFTED_HOME"] ?? "\(home)/.vibecrafted"
  let logDir = URL(fileURLWithPath: crafted, isDirectory: true).appendingPathComponent("logs")
  let logURL = logDir.appendingPathComponent("app-lifecycle.log")
  do {
    let handle = try FileHandle(forWritingTo: logURL)
    defer { try? handle.close() }
    try handle.seekToEnd()
    try handle.write(contentsOf: Data(line.utf8))
  } catch {
    installLog.error("lifecycle log write failed: \(error.localizedDescription, privacy: .public)")
  }
}

@MainActor private var lifecycleSignalSources: [DispatchSourceSignal] = []

/// SIGTERM/SIGHUP/SIGINT bypass `applicationShouldTerminate`; without this the
/// App dies silently and its vc-terminal child goes with it. Log the signal,
/// then quit through AppKit so `applicationWillTerminate` still runs.
@MainActor private func installLifecycleSignalHandlers() {
  for (signalNumber, name) in [(SIGTERM, "SIGTERM"), (SIGHUP, "SIGHUP"), (SIGINT, "SIGINT")] {
    signal(signalNumber, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
    source.setEventHandler {
      lifecycleLog("signal \(name) received; terminating via AppKit")
      NSApp.terminate(nil)
    }
    source.resume()
    lifecycleSignalSources.append(source)
  }
}

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

extension TrayServerHealth {
  var color: NSColor {
    switch self {
    case .checking: return .systemGray
    case .healthy: return .systemGreen
    case .transitioning: return .systemOrange
    case .failed: return .systemRed
    case .neutral: return .systemGray
    }
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

@MainActor
class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
  var mainWindow: MainWindowController?
  private var statusItem: NSStatusItem?
  private var serverStatusMenuItem: NSMenuItem?
  private var serverDetailMenuItem: NSMenuItem?
  private var startServerMenuItem: NSMenuItem?
  private var stopServerMenuItem: NSMenuItem?
  private var restartServerMenuItem: NSMenuItem?
  private var openServerLogsMenuItem: NSMenuItem?
  private var trayBaseIcon: NSImage?
  private var statusRefreshTimer: Timer?
  private var terminalProcess: Process?
  private var serverStatusProcess: Process?
  private var serverActionProcess: Process?
  private var serverActionInFlight: ServerLifecycleAction?
  private var serverUtilityProcess: Process?
  private var canonicalInstall: CanonicalRuntimeInstall?
  private var canonicalRuntimeEnvironment: [String: String]?
  private var workspaceLaunchFailureReported = false
  private var eyeReconcileProcess: Process?
  let eventObserver = EventObserver()

  func showMainWindowIfNeeded() {
    if mainWindow == nil {
      mainWindow = MainWindowController()
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

    launchWorkspaceTerminal()
  }

  func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
    false
  }

  func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
    let reply = decideTermination()
    lifecycleLog("applicationShouldTerminate -> \(reply == .terminateNow ? "terminateNow" : "terminateCancel")")
    return reply
  }

  private func decideTermination() -> NSApplication.TerminateReply {
    switch activeRunSummary() {
    case .available(let summary) where summary.lanes == 0:
      lifecycleLog("quit requested with 0 active/stalled lanes")
      return .terminateNow
    case .available(let summary):
      lifecycleLog(
        "quit requested with \(summary.lanes) active/stalled lane(s), \(summary.worktrees) worktree-backed; asking")
      let alert = NSAlert()
      alert.alertStyle = .warning
      alert.messageText = "Active or stalled Vibecrafted lanes still need a control surface"
      alert.informativeText =
        "\(summary.lanes) active/stalled lane(s), including \(summary.worktrees) worktree-backed lane(s). Quitting the app does not make that work disappear, but removes its live control surface."
      alert.addButton(withTitle: "Cancel")
      alert.addButton(withTitle: "Quit Anyway")
      return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    case .unavailable(let reason):
      installLog.error("Cannot inspect lifecycle truth before quit: \(reason, privacy: .public)")
      lifecycleLog("quit requested but lifecycle truth unavailable: \(reason); asking")
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted lifecycle truth is unavailable"
      alert.informativeText =
        "The canonical control plane could not confirm whether any lanes are active or stalled. Cancel to keep the live control surface, or quit explicitly anyway."
      alert.addButton(withTitle: "Cancel")
      alert.addButton(withTitle: "Quit Anyway")
      return alert.runModal() == .alertSecondButtonReturn ? .terminateNow : .terminateCancel
    }
  }

  func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
    true
  }

  func application(_ application: NSApplication, open urls: [URL]) {
    NotificationManager.shared.handleOpenURLs(urls)
  }

  func applicationWillTerminate(_ notification: Notification) {
    let terminalState =
      terminalProcess.map { $0.isRunning ? "vc-terminal pid=\($0.processIdentifier) still running" : "vc-terminal already exited" }
      ?? "no vc-terminal"
    lifecycleLog("applicationWillTerminate; \(terminalState)")
    statusRefreshTimer?.invalidate()
    NotificationManager.shared.clearHeartbeat(craftedHome: craftedHomeURL())
  }

  private func startNativeNotifications() {
    NotificationManager.shared.presentWindow = { [weak self] in
      self?.showMainWindowIfNeeded()
    }
    NotificationManager.shared.start(craftedHome: craftedHomeURL())
  }

  private func craftedHomeURL() -> URL {
    let host = ProcessInfo.processInfo.environment
    let home = host["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
    return URL(
      fileURLWithPath: host["VIBECRAFTED_HOME"] ?? "\(home)/.vibecrafted", isDirectory: true)
  }

  private func launchWorkspaceTerminal() {
    if terminalProcess?.isRunning == true {
      return
    }

    let install: CanonicalRuntimeInstall
    do {
      install = try installCanonicalRuntime()
    } catch {
      reportWorkspaceLaunchFailure(
        "Cannot publish the canonical Vibecrafted runtime: \(error.localizedDescription)")
      return
    }
    canonicalInstall = install

    for required in [
      install.terminal, install.terminalHost, install.frame, install.start,
      install.primaryShell,
    ]
    where !FileManager.default.isExecutableFile(
      atPath: required.path)
    {
      reportWorkspaceLaunchFailure(
        "Bundled product entry is missing or not executable: \(required.path)")
      return
    }
    guard FileManager.default.fileExists(atPath: install.terminalConfig.path) else {
      reportWorkspaceLaunchFailure(
        "Canonical terminal config is missing: \(install.terminalConfig.path)")
      return
    }
    do {
      try registerBundledFonts()
    } catch {
      reportWorkspaceLaunchFailure(
        "Cannot register the bundled terminal font: \(error.localizedDescription)")
      return
    }

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
    // ~/.cargo/bin, so those tools die with exit 127. Keep the host PATH and only
    // give the signed generation priority over it.
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
    canonicalRuntimeEnvironment = environment
    refreshServerStatus()

    let process = Process()
    process.executableURL = install.terminalHost
    process.arguments = [
      "--config-file", install.terminalConfig.path,
      "-e", install.primaryShell.path, install.start.path, "operator",
    ]
    process.environment = environment
    process.terminationHandler = { finished in
      let how = finished.terminationReason == .uncaughtSignal ? "signal" : "exit"
      lifecycleLog(
        "vc-terminal pid=\(finished.processIdentifier) ended by \(how) status=\(finished.terminationStatus)")
    }
    do {
      try process.run()
      terminalProcess = process
      lifecycleLog("vc-terminal launched pid=\(process.processIdentifier) host=\(install.terminalHost.lastPathComponent) generation=\(install.root.lastPathComponent)")
    } catch {
      lifecycleLog("vc-terminal launch failed: \(error.localizedDescription)")
      reportWorkspaceLaunchFailure(
        "Failed to launch bundled vc-terminal: \(error.localizedDescription)")
    }
    reconcileControlPlaneEye(install: install, environment: environment)
  }

  private func reconcileControlPlaneEye(
    install: CanonicalRuntimeInstall, environment: [String: String]
  ) {
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else { return }
    let process = Process()
    process.executableURL = deck
    process.arguments = ["server", "service", "reconcile"]
    process.environment = environment
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
      eyeReconcileProcess = process
    } catch {
      print("Cannot reconcile the control-plane eye: \(error)")
    }
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
      let detail =
        String(data: failure.isEmpty ? result : failure, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines)
        ?? "installer exited \(process.terminationStatus)"
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install",
        code: Int(process.terminationStatus),
        userInfo: [NSLocalizedDescriptionKey: detail])
    }
    return result
  }

  /// Signed generation bin first, then the inherited PATH; use the minimal
  /// system set only when the caller carried no PATH at all.
  private func composedPath(generation: URL, inherited: String?) -> String {
    let tail = (inherited ?? "").isEmpty ? "/usr/bin:/bin:/usr/sbin:/sbin" : inherited!
    return "\(generation.appendingPathComponent("bin").path):\(tail)"
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
    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    // Inherit the app (dock) icon for the menu-bar status item so the tray
    // matches the dock panda instead of a generic SF Symbol.
    let trayIcon = NSApp.applicationIconImage.copy() as? NSImage
    trayIcon?.size = NSSize(width: 18, height: 18)
    trayIcon?.accessibilityDescription = "Vibecrafted"
    trayBaseIcon =
      trayIcon ?? NSImage(systemSymbolName: "hammer.fill", accessibilityDescription: "Vibecrafted")
    item.button?.image = statusIcon(health: .checking)
    item.button?.imagePosition = .imageOnly
    item.button?.toolTip = "Vibecrafted — checking server"

    let menu = NSMenu()
    menu.delegate = self
    let serverStatus = menu.addItem(
      withTitle: "VC Server: CHECKING…", action: nil, keyEquivalent: "")
    serverStatus.isEnabled = false
    serverStatusMenuItem = serverStatus
    let serverDetail = menu.addItem(
      withTitle: "Reading supervisor state…", action: nil, keyEquivalent: "")
    serverDetail.isEnabled = false
    serverDetailMenuItem = serverDetail
    menu.addItem(.separator())
    let console = menu.addItem(
      withTitle: "Open VC Console", action: #selector(openConsoleFromStatusItem), keyEquivalent: "")
    console.target = self
    let terminal = menu.addItem(
      withTitle: "Open VC Terminal", action: #selector(openTerminalFromStatusItem),
      keyEquivalent: "")
    terminal.target = self
    let serverOwner = menu.addItem(withTitle: "VC Server", action: nil, keyEquivalent: "")
    let serverMenu = NSMenu(title: "VC Server")
    let start = serverMenu.addItem(
      withTitle: "Start", action: #selector(startServerFromStatusItem), keyEquivalent: "")
    start.target = self
    startServerMenuItem = start
    let stop = serverMenu.addItem(
      withTitle: "Stop", action: #selector(stopServerFromStatusItem), keyEquivalent: "")
    stop.target = self
    stopServerMenuItem = stop
    let restart = serverMenu.addItem(
      withTitle: "Restart", action: #selector(restartServerFromStatusItem), keyEquivalent: "")
    restart.target = self
    restartServerMenuItem = restart
    serverMenu.addItem(.separator())
    let logs = serverMenu.addItem(
      withTitle: "Open Logs", action: #selector(openServerLogsFromStatusItem), keyEquivalent: "")
    logs.target = self
    openServerLogsMenuItem = logs
    serverOwner.submenu = serverMenu
    let diagnostics = menu.addItem(
      withTitle: "Server Diagnostics…", action: #selector(showServerDiagnostics),
      keyEquivalent: "")
    diagnostics.target = self
    menu.addItem(.separator())
    menu.addItem(
      withTitle: "About Vibecrafted",
      action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    let help = menu.addItem(
      withTitle: "Vibecrafted Help", action: #selector(showStatusItemHelp), keyEquivalent: "")
    help.target = self
    menu.addItem(.separator())
    let quit = menu.addItem(
      withTitle: "Quit Vibecrafted", action: #selector(requestQuit), keyEquivalent: "q")
    quit.target = self
    item.menu = menu
    statusItem = item
    statusRefreshTimer = Timer.scheduledTimer(
      timeInterval: 5, target: self, selector: #selector(refreshServerStatusFromTimer),
      userInfo: nil, repeats: true)
    refreshServerStatus()
  }

  func menuWillOpen(_ menu: NSMenu) {
    refreshServerStatus()
  }

  @objc private func refreshServerStatusFromTimer() {
    refreshServerStatus()
  }

  private func statusIcon(health: TrayServerHealth) -> NSImage? {
    guard let base = trayBaseIcon else { return nil }
    let size = NSSize(width: 18, height: 18)
    let image = NSImage(size: size, flipped: false) { rect in
      base.draw(in: rect)
      let dotRect = NSRect(x: 11.5, y: 0.5, width: 6, height: 6)
      NSColor.windowBackgroundColor.setFill()
      NSBezierPath(ovalIn: dotRect.insetBy(dx: -1, dy: -1)).fill()
      health.color.setFill()
      NSBezierPath(ovalIn: dotRect).fill()
      return true
    }
    image.isTemplate = false
    image.accessibilityDescription = "Vibecrafted server status"
    return image
  }

  private func supervisorStatusURL() -> URL? {
    canonicalInstall?.craftedHome.appendingPathComponent("server/supervisor.status.json")
  }

  private func readSupervisorSnapshot() -> ServerSupervisorSnapshot? {
    supervisorData().flatMap { try? JSONDecoder().decode(ServerSupervisorSnapshot.self, from: $0) }
  }

  private func supervisorData() -> Data? {
    guard let url = supervisorStatusURL() else { return nil }
    return try? Data(contentsOf: url)
  }

  private func conciseServerReason(_ reason: String?) -> String? {
    guard let firstLine = reason?.split(whereSeparator: \.isNewline).first else { return nil }
    let plain = String(firstLine)
      .replacingOccurrences(of: "\u{001B}[31m", with: "")
      .replacingOccurrences(of: "\u{001B}[0m", with: "")
      .trimmingCharacters(in: .whitespacesAndNewlines)
    guard !plain.isEmpty else { return nil }
    return plain.count > 96 ? "\(plain.prefix(93))…" : plain
  }

  private func refreshServerStatus() {
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      applyServerMenuState(
        deriveServerMenuState(
          supervisorData: nil, serviceData: nil, actionInFlight: nil, runtimeReady: false))
      return
    }
    guard serverStatusProcess?.isRunning != true else { return }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      applyServerMenuState(
        deriveServerMenuState(
          supervisorData: supervisorData(), serviceData: nil,
          actionInFlight: serverActionInFlight, runtimeReady: true))
      return
    }

    let output = Pipe()
    let errors = Pipe()
    let process = Process()
    process.executableURL = deck
    process.arguments = ["server", "service", "status", "--json"]
    process.environment = environment
    process.standardOutput = output
    process.standardError = errors
    process.terminationHandler = { [weak self] _ in
      let serviceData = output.fileHandleForReading.readDataToEndOfFile()
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.serverStatusProcess = nil
        self.applyServerMenuState(
          deriveServerMenuState(
            supervisorData: self.supervisorData(),
            serviceData: serviceData.isEmpty ? nil : serviceData,
            actionInFlight: self.serverActionInFlight,
            runtimeReady: true))
      }
    }
    do {
      try process.run()
      serverStatusProcess = process
    } catch {
      applyServerMenuState(
        deriveServerMenuState(
          supervisorData: supervisorData(), serviceData: nil,
          actionInFlight: serverActionInFlight, runtimeReady: true))
    }
  }

  private func applyServerMenuState(_ state: ServerMenuState) {
    serverStatusMenuItem?.title = state.header
    serverDetailMenuItem?.title = state.detail
    serverDetailMenuItem?.isHidden = state.detail.isEmpty
    startServerMenuItem?.isEnabled = state.canStart
    stopServerMenuItem?.isEnabled = state.canStop
    restartServerMenuItem?.isEnabled = state.canRestart
    openServerLogsMenuItem?.isEnabled =
      canonicalInstall != nil && serverUtilityProcess?.isRunning != true
    statusItem?.button?.image = statusIcon(health: state.health)
    statusItem?.button?.toolTip = "Vibecrafted — \(state.header)"
  }

  @objc private func openConsoleFromStatusItem() {
    Task {
      _ = try? await getServerStatus()
      await MainActor.run {
        self.showMainWindowIfNeeded()
      }
    }
  }

  @objc private func openTerminalFromStatusItem() {
    if let process = terminalProcess, process.isRunning {
      NSRunningApplication(processIdentifier: process.processIdentifier)?.activate(options: [])
      return
    }
    launchWorkspaceTerminal()
  }

  @objc private func startServerFromStatusItem() {
    performServerAction(.start)
  }

  @objc private func stopServerFromStatusItem() {
    performServerAction(.stop)
  }

  @objc private func restartServerFromStatusItem() {
    performServerAction(.restart)
  }

  private func performServerAction(_ action: ServerLifecycleAction) {
    guard serverActionProcess?.isRunning != true else { return }
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      reportWorkspaceLaunchFailure(
        "Cannot \(action.rawValue) VC Server before runtime onboarding completes")
      return
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      reportWorkspaceLaunchFailure("Canonical server launcher is missing: \(deck.path)")
      return
    }

    let process = Process()
    let output = Pipe()
    let errors = Pipe()
    process.executableURL = deck
    process.arguments = serverActionArguments(for: action)
    process.environment = environment
    process.standardOutput = output
    process.standardError = errors
    process.terminationHandler = { [weak self] finished in
      let stdout = output.fileHandleForReading.readDataToEndOfFile()
      let stderr = errors.fileHandleForReading.readDataToEndOfFile()
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.serverActionProcess = nil
        self.serverActionInFlight = nil
        if finished.terminationStatus != 0 {
          let detail = String(data: stderr.isEmpty ? stdout : stderr, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            ?? "Canonical service owner exited \(finished.terminationStatus)"
          let alert = NSAlert()
          alert.alertStyle = .critical
          alert.messageText = "Vibecrafted could not \(action.rawValue) VC Server"
          alert.informativeText = detail
          alert.addButton(withTitle: "OK")
          alert.runModal()
        }
        self.refreshServerStatus()
      }
    }
    do {
      try process.run()
      serverActionProcess = process
      serverActionInFlight = action
      applyServerMenuState(
        deriveServerMenuState(
          supervisorData: supervisorData(), serviceData: nil,
          actionInFlight: action, runtimeReady: true))
    } catch {
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted could not \(action.rawValue) VC Server"
      alert.informativeText = error.localizedDescription
      alert.runModal()
    }
  }

  @objc private func openServerLogsFromStatusItem() {
    guard serverUtilityProcess?.isRunning != true else { return }
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      reportWorkspaceLaunchFailure("Cannot open VC Server logs before runtime onboarding completes")
      return
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      reportWorkspaceLaunchFailure("Canonical server launcher is missing: \(deck.path)")
      return
    }

    let output = Pipe()
    let errors = Pipe()
    let process = Process()
    process.executableURL = deck
    process.arguments = ["server", "service", "logs", "--json"]
    process.environment = environment
    process.standardOutput = output
    process.standardError = errors
    process.terminationHandler = { [weak self] finished in
      let stdout = output.fileHandleForReading.readDataToEndOfFile()
      let stderr = errors.fileHandleForReading.readDataToEndOfFile()
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.serverUtilityProcess = nil
        if finished.terminationStatus == 0, let logs = decodeServerLogs(data: stdout) {
          NSWorkspace.shared.open(logs.directory)
        } else {
          let detail = String(data: stderr.isEmpty ? stdout : stderr, encoding: .utf8)?
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
    }
    do {
      try process.run()
      serverUtilityProcess = process
      openServerLogsMenuItem?.isEnabled = false
    } catch {
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted could not open VC Server logs"
      alert.informativeText = error.localizedDescription
      alert.runModal()
    }
  }

  @objc private func showServerDiagnostics() {
    let snapshot = readSupervisorSnapshot()
    let alert = NSAlert()
    alert.alertStyle = snapshot?.state.lowercased() == "healthy" ? .informational : .warning
    alert.messageText = "Vibecrafted Server"
    if let snapshot {
      var lines = [
        "State: \(snapshot.state.uppercased())",
        "Supervisor PID: \(snapshot.supervisorPID.map(String.init) ?? "—")",
        "Server PID: \(snapshot.managedPair?.serverPID.map(String.init) ?? "—")",
        "Guardian PID: \(snapshot.managedPair?.guardianPID.map(String.init) ?? "—")",
      ]
      if let reason = conciseServerReason(snapshot.lastError) {
        lines.append("Last error: \(reason)")
      }
      if let path = supervisorStatusURL()?.path {
        lines.append("Status receipt: \(path)")
      }
      alert.informativeText = lines.joined(separator: "\n")
    } else {
      alert.informativeText = "No supervisor status receipt exists for the installed runtime."
    }
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
      "The tray dot reports VC Server: green is healthy, amber is transitioning, red needs attention, and gray is stopped. Open VC Console for live runs; VC Server actions always route through the installed service owner."
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
      let data = output.fileHandleForReading.readDataToEndOfFile()
      process.waitUntilExit()
      return decodeRuntimeActivityTruth(data: data, terminationStatus: process.terminationStatus)
    } catch {
      return .unavailable(error.localizedDescription)
    }
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

    // View menu
    let viewMenu = NSMenu(title: "View")
    let sidebarItem = viewMenu.addItem(
      withTitle: "Toggle Sidebar", action: #selector(NSSplitViewController.toggleSidebar(_:)),
      keyEquivalent: "s")
    sidebarItem.keyEquivalentModifierMask = [.command, .control]
    let inspectorItem = viewMenu.addItem(
      withTitle: "Toggle Inspector", action: #selector(NSSplitViewController.toggleInspector(_:)),
      keyEquivalent: "i")
    inspectorItem.keyEquivalentModifierMask = [.command, .control]

    let viewMenuItem = NSMenuItem()
    viewMenuItem.submenu = viewMenu
    mainMenu.addItem(viewMenuItem)

    // Window menu
    let windowMenu = NSMenu(title: "Window")
    windowMenu.addItem(
      withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
    windowMenu.addItem(
      withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")

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
