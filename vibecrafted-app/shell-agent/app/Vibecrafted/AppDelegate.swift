import AppKit
import CoreText
import Darwin
import os.log

private let installLog = Logger(subsystem: "io.vetcoders.vibecrafted", category: "install")

private struct CanonicalRuntimeInstall: Decodable {
  let root: URL
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

private struct ServerSupervisorSnapshot: Decodable {
  struct ManagedPair: Decodable {
    let guardianPID: Int?
    let serverPID: Int?

    enum CodingKeys: String, CodingKey {
      case guardianPID = "guardian_pid"
      case serverPID = "server_pid"
    }
  }

  let state: String
  let lastError: String?
  let supervisorPID: Int?
  let managedPair: ManagedPair?

  enum CodingKeys: String, CodingKey {
    case state
    case lastError = "last_error"
    case supervisorPID = "supervisor_pid"
    case managedPair = "managed_pair"
  }
}

private struct RuntimeStatusSnapshot: Decodable {
  struct Run: Decodable {
    let state: String
    let health: String?
    let root: String
  }

  let runs: [Run]
}

private enum TrayServerHealth {
  case checking
  case healthy
  case degraded
  case failed
  case stopped

  var color: NSColor {
    switch self {
    case .checking: return .systemGray
    case .healthy: return .systemGreen
    case .degraded: return .systemOrange
    case .failed: return .systemRed
    case .stopped: return .systemGray
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
  private var restartServerMenuItem: NSMenuItem?
  private var trayBaseIcon: NSImage?
  private var statusRefreshTimer: Timer?
  private var terminalProcess: Process?
  private var serverActionProcess: Process?
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

  func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool {
    true
  }

  func application(_ application: NSApplication, open urls: [URL]) {
    NotificationManager.shared.handleOpenURLs(urls)
  }

  func applicationWillTerminate(_ notification: Notification) {
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
    do {
      try process.run()
      terminalProcess = process
    } catch {
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
    let runtime = appRoot.appendingPathComponent(
      "Contents/Resources/runtime", isDirectory: true)
    let terminalHost = appRoot.appendingPathComponent(
      "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty")
    let frameHelper = appRoot.appendingPathComponent("Contents/Helpers/vc-frame")
    let output = try runRuntimeInstaller(arguments: [
      "runtime-install",
      "--payload-root", runtime.path,
      "--app-root", appRoot.path,
      "--terminal-host", terminalHost.path,
      "--frame-helper", frameHelper.path,
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
    _ = try runRuntimeInstaller(arguments: ["runtime-uninstall"])
  }

  private func runRuntimeInstaller(arguments: [String]) throws -> Data {
    let runtime = Bundle.main.bundleURL.appendingPathComponent(
      "Contents/Resources/runtime", isDirectory: true)
    let python = runtime.appendingPathComponent("bin/python3")
    let installer = runtime.appendingPathComponent("scripts/vetcoders_install.py")
    for required in [python, installer]
    where !FileManager.default.isExecutableFile(atPath: required.path) {
      throw NSError(
        domain: "io.vetcoders.vibecrafted.install", code: 1,
        userInfo: [
          NSLocalizedDescriptionKey:
            "signed Runtime Pack installer entry is missing: \(required.path)"
        ])
    }

    let process = Process()
    let output = Pipe()
    let errors = Pipe()
    process.executableURL = python
    process.arguments = [installer.path] + arguments
    var environment = ProcessInfo.processInfo.environment
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
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
    let serverStatus = menu.addItem(withTitle: "Server: CHECKING…", action: nil, keyEquivalent: "")
    serverStatus.isEnabled = false
    serverStatusMenuItem = serverStatus
    let serverDetail = menu.addItem(
      withTitle: "Reading supervisor state…", action: nil, keyEquivalent: "")
    serverDetail.isEnabled = false
    serverDetailMenuItem = serverDetail
    menu.addItem(.separator())
    let console = menu.addItem(
      withTitle: "VC Console", action: #selector(openConsoleFromStatusItem), keyEquivalent: "")
    console.target = self
    let terminal = menu.addItem(
      withTitle: "VC Terminal", action: #selector(openTerminalFromStatusItem),
      keyEquivalent: "")
    terminal.target = self
    let restart = menu.addItem(
      withTitle: "VC Server", action: #selector(restartServerFromStatusItem),
      keyEquivalent: "")
    restart.target = self
    restartServerMenuItem = restart
    let diagnostics = menu.addItem(
      withTitle: "Server Diagnostics…", action: #selector(showServerDiagnostics),
      keyEquivalent: "")
    diagnostics.target = self
    menu.addItem(.separator())
    menu.addItem(
      withTitle: "About",
      action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
    let help = menu.addItem(
      withTitle: "Help", action: #selector(showStatusItemHelp), keyEquivalent: "")
    help.target = self
    menu.addItem(.separator())
    let quit = menu.addItem(withTitle: "Quit", action: #selector(requestQuit), keyEquivalent: "q")
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
    guard let url = supervisorStatusURL(), let data = try? Data(contentsOf: url) else {
      return nil
    }
    return try? JSONDecoder().decode(ServerSupervisorSnapshot.self, from: data)
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
    guard canonicalInstall != nil else {
      applyServerStatus(
        title: "Server: WAITING FOR RUNTIME", detail: "Runtime onboarding has not completed",
        health: .checking)
      return
    }
    guard let snapshot = readSupervisorSnapshot() else {
      applyServerStatus(
        title: "Server: NOT INSTALLED", detail: "No supervisor status receipt",
        health: .failed)
      return
    }

    let state = snapshot.state.lowercased()
    let pairHealthy =
      snapshot.managedPair?.serverPID != nil && snapshot.managedPair?.guardianPID != nil
    let health: TrayServerHealth
    if state == "healthy" && pairHealthy {
      health = .healthy
    } else if state == "starting" || state == "stopping" {
      health = .degraded
    } else if state == "backoff" || state == "stop-failed" {
      health = .failed
    } else {
      health = .stopped
    }
    let reason = conciseServerReason(snapshot.lastError)
    let detail = reason ?? "Supervisor PID \(snapshot.supervisorPID.map(String.init) ?? "—")"
    applyServerStatus(
      title: "Server: \(snapshot.state.uppercased())", detail: detail, health: health)
  }

  private func applyServerStatus(title: String, detail: String, health: TrayServerHealth) {
    serverStatusMenuItem?.title = title
    serverDetailMenuItem?.title = detail
    serverDetailMenuItem?.isHidden = detail.isEmpty
    restartServerMenuItem?.isEnabled = serverActionProcess?.isRunning != true
    statusItem?.button?.image = statusIcon(health: health)
    statusItem?.button?.toolTip = "Vibecrafted — \(title)"
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

  @objc private func restartServerFromStatusItem() {
    guard serverActionProcess?.isRunning != true else { return }
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      reportWorkspaceLaunchFailure("Cannot restart the server before runtime onboarding completes")
      return
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else {
      reportWorkspaceLaunchFailure("Canonical server launcher is missing: \(deck.path)")
      return
    }

    let process = Process()
    process.executableURL = deck
    // Reconcile is the service-owner operation: it starts a stopped pair and
    // replaces a stale supervisor generation without creating a second owner.
    process.arguments = ["server", "service", "reconcile"]
    process.environment = environment
    process.standardOutput = FileHandle.nullDevice
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
      serverActionProcess = process
      applyServerStatus(
        title: "Server: RESTARTING…", detail: "Reconciling the installed supervisor",
        health: .degraded)
    } catch {
      let alert = NSAlert()
      alert.alertStyle = .critical
      alert.messageText = "Vibecrafted could not restart the server"
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
      "The tray dot reports the local server: green is healthy, orange is transitioning, red needs attention. Open Console for live runs, or Server Diagnostics for the exact supervisor receipt."
    alert.addButton(withTitle: "OK")
    alert.runModal()
  }

  private func activeRunSummary() -> (lanes: Int, worktrees: Int)? {
    guard let install = canonicalInstall, let environment = canonicalRuntimeEnvironment else {
      return nil
    }
    let deck = install.root.appendingPathComponent("bin/vibecrafted")
    guard FileManager.default.isExecutableFile(atPath: deck.path) else { return nil }
    let output = Pipe()
    let process = Process()
    process.executableURL = deck
    process.arguments = ["status", "--json"]
    process.environment = environment
    process.standardOutput = output
    process.standardError = FileHandle.nullDevice
    do {
      try process.run()
      let data = output.fileHandleForReading.readDataToEndOfFile()
      process.waitUntilExit()
      guard process.terminationStatus == 0 else { return nil }
      let status = try JSONDecoder().decode(RuntimeStatusSnapshot.self, from: data)
      let active = status.runs.filter {
        ["active", "running", "launching"].contains($0.state) || $0.health == "active"
      }
      let worktrees = active.filter { $0.root.contains("/.vibecrafted/worktrees/") }
      return (active.count, worktrees.count)
    } catch {
      installLog.error("Cannot inspect active lanes before quit: \(error.localizedDescription, privacy: .public)")
      return nil
    }
  }

  @objc private func requestQuit() {
    guard let summary = activeRunSummary(), summary.lanes > 0 else {
      NSApp.terminate(nil)
      return
    }
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = "Active Vibecrafted lanes are still running"
    alert.informativeText =
      "\(summary.lanes) active lane(s), including \(summary.worktrees) worktree-backed lane(s). Quitting the app does not make that work disappear, but removes its live control surface."
    alert.addButton(withTitle: "Keep Running")
    alert.addButton(withTitle: "Quit Anyway")
    if alert.runModal() == .alertSecondButtonReturn {
      NSApp.terminate(nil)
    }
  }

  private func buildMainMenu() {
    let mainMenu = NSMenu()

    // Application menu
    let appMenu = NSMenu()
    appMenu.addItem(
      withTitle: "About",
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
