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

class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
  var mainWindow: MainWindowController?
  private var statusItem: NSStatusItem?
  private var serverStatusMenuItem: NSMenuItem?
  private var serverDetailMenuItem: NSMenuItem?
  private var startServerMenuItem: NSMenuItem?
  private var stopServerMenuItem: NSMenuItem?
  private var restartServerMenuItem: NSMenuItem?
  private var openServerMenuItem: NSMenuItem?
  private var openWorkspacesMenuItem: NSMenuItem?
  private var openServerLogsMenuItem: NSMenuItem?
  private var runtimePackStatusMenuItem: NSMenuItem?
  private var runtimePackDetailMenuItem: NSMenuItem?
  private var revealRuntimeHomeMenuItem: NSMenuItem?
  private var openControlPlaneMenuItem: NSMenuItem?
  private var copyRuntimeIdentityMenuItem: NSMenuItem?
  private var repairRuntimeMenuItem: NSMenuItem?
  private var signedCarrierRevisions: (source: String, terminal: String, frame: String)?
  private var trayBaseIcon: NSImage?
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
  private var terminalProcess: Process?
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
    // The workspace terminal is now started through the generation wrapper, so
    // it is a child of this process rather than an independent application.
    // That changes nothing about its lifetime: quitting the App is a view
    // closing. Nothing here stops the terminal, the shared service, frame
    // sessions, PTYs or workers, and nothing here should.
    let terminalState: String
    if let application = terminalApplication, !application.isTerminated {
      terminalState = "vc-terminal pid=\(application.processIdentifier) still running"
    } else if let process = terminalProcess, process.isRunning {
      terminalState = "vc-terminal pid=\(process.processIdentifier) still running"
    } else if terminalApplication != nil || terminalProcess != nil {
      terminalState = "vc-terminal already exited"
    } else {
      terminalState = "no vc-terminal"
    }
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
    if terminalIsLive() || terminalLaunchInFlight {
      return
    }

    // Carrier identity is this bundle's own property, so the tray can compare
    // it against the live generation whether or not this open installs
    // anything. A malformed manifest must not block opening an installed
    // runtime; it degrades the drift line, which the policy reports honestly.
    try? loadSignedCarrierRevisions()

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
            + "Use Runtime Pack ▸ Reinstall From Bundled Pack… to repair it.")
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
    guard let environment = canonicalRuntimeEnvironment else {
      terminalLaunchInFlight = false
      reportWorkspaceLaunchFailure("The resolved runtime carries no launch environment")
      return
    }
    do {
      try registerBundledFonts()
    } catch {
      terminalLaunchInFlight = false
      reportWorkspaceLaunchFailure(
        "Cannot register the bundled terminal font: \(error.localizedDescription)")
      return
    }
    applyRuntimePackMenuState()
    refreshServerStatus()

    let process = Process()
    process.executableURL = install.terminal
    process.arguments = ["-e", install.primaryShell.path, install.start.path, "operator"]
    process.environment = environment
    // No pipes: the workspace terminal is a long-lived view process, not a verb
    // whose output the tray reads. Giving it a pipe nobody drains is how a
    // terminal ends up blocked on its own logging.
    process.terminationHandler = { [weak self] finished in
      DispatchQueue.main.async { [weak self] in
        guard let self else { return }
        self.terminalProcess = nil
        self.terminalApplication = nil
        lifecycleLog(
          "vc-terminal exited status=\(finished.terminationStatus) generation=\(install.root.lastPathComponent)"
        )
      }
    }
    do {
      try process.run()
    } catch {
      terminalLaunchInFlight = false
      reportWorkspaceLaunchFailure(
        "Failed to launch the installed vc-terminal wrapper at \(install.terminal.path): "
          + error.localizedDescription)
      return
    }
    terminalProcess = process
    terminalLaunchInFlight = false
    // The wrapper execs its native host in place, so the view is trackable as
    // an application only once macOS has registered it. Bring it forward when
    // it is there; never make the launch depend on that.
    if let application = NSRunningApplication(processIdentifier: process.processIdentifier) {
      terminalApplication = application
      application.activate(options: [])
    }
    lifecycleLog(
      "vc-terminal launched through the generation wrapper pid=\(process.processIdentifier) generation=\(install.root.lastPathComponent)"
    )
    reconcileControlPlaneEye(install: install, environment: environment)
  }

  /// True when a workspace terminal this App started is still up.
  private func terminalIsLive() -> Bool {
    if let application = terminalApplication, !application.isTerminated {
      return true
    }
    return terminalProcess?.isRunning == true
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
    completion: @escaping (BoundedProcessResult) -> Void
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
      DispatchQueue.main.async {
        completion(result)
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
    let openServer = menu.addItem(
      withTitle: "Open VC Server", action: #selector(openServerFromStatusItem),
      keyEquivalent: "o")
    openServer.keyEquivalentModifierMask = [.command, .option]
    openServer.target = self
    openServerMenuItem = openServer
    let console = menu.addItem(
      withTitle: "Open Native Console", action: #selector(openConsoleFromStatusItem),
      keyEquivalent: "c")
    console.keyEquivalentModifierMask = [.command, .option]
    console.target = self
    let terminal = menu.addItem(
      withTitle: "Open VC Terminal", action: #selector(openTerminalFromStatusItem),
      keyEquivalent: "t")
    terminal.keyEquivalentModifierMask = [.command, .option]
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
    let workspaces = serverMenu.addItem(
      withTitle: "Open Workspaces", action: #selector(openWorkspacesFromStatusItem),
      keyEquivalent: "w")
    workspaces.keyEquivalentModifierMask = [.command, .option]
    workspaces.target = self
    openWorkspacesMenuItem = workspaces
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
    // Runtime Pack supervision: the pack is the product carrier and this App
    // consumes it, so the tray shows the live generation and its drift against
    // the signed carrier instead of hiding the runtime behind the server dot.
    let runtimePackStatus = menu.addItem(
      withTitle: "Runtime Pack: WAITING FOR RUNTIME", action: nil, keyEquivalent: "")
    runtimePackStatus.isEnabled = false
    runtimePackStatusMenuItem = runtimePackStatus
    let runtimePackDetail = menu.addItem(
      withTitle: "Runtime onboarding has not completed", action: nil, keyEquivalent: "")
    runtimePackDetail.isEnabled = false
    runtimePackDetailMenuItem = runtimePackDetail
    let runtimePackOwner = menu.addItem(withTitle: "Runtime Pack", action: nil, keyEquivalent: "")
    let runtimePackMenu = NSMenu(title: "Runtime Pack")
    let revealHome = runtimePackMenu.addItem(
      withTitle: "Reveal Runtime Home", action: #selector(revealRuntimeHomeFromStatusItem),
      keyEquivalent: "")
    revealHome.target = self
    revealRuntimeHomeMenuItem = revealHome
    let openControlPlane = runtimePackMenu.addItem(
      withTitle: "Reveal Control Plane Files", action: #selector(openControlPlaneFromStatusItem),
      keyEquivalent: "")
    openControlPlane.target = self
    openControlPlaneMenuItem = openControlPlane
    let copyIdentity = runtimePackMenu.addItem(
      withTitle: "Copy Runtime Identity", action: #selector(copyRuntimeIdentityFromStatusItem),
      keyEquivalent: "")
    copyIdentity.target = self
    copyRuntimeIdentityMenuItem = copyIdentity
    runtimePackMenu.addItem(.separator())
    // The one deliberate way back to the bundled carrier. Routine opening
    // resolves the installed generation instead, so this action — not a window
    // — is what may replace a runtime, and it always asks first.
    let repair = runtimePackMenu.addItem(
      withTitle: "Reinstall From Bundled Pack…", action: #selector(repairRuntimeFromBundledPack),
      keyEquivalent: "")
    repair.target = self
    repairRuntimeMenuItem = repair
    runtimePackOwner.submenu = runtimePackMenu
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

  private func refreshServerStatus() {
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
        if !result.stdout.isEmpty {
          self.lastCaretakerData = result.stdout
        }
        self.renderServerStatus()
      }
      serverStatusProcess = process
    } catch {
      serverStatusProcess = nil
      renderServerStatus()
    }
  }

  /// Paint the tray from cached truth alone. This spawns nothing, so it is safe
  /// to call from any completion handler.
  private func renderServerStatus() {
    applyServerMenuState(
      deriveServerMenuState(
        caretakerData: lastCaretakerData,
        actionInFlight: serverActionInFlight,
        runtimeReady: canonicalInstall != nil))
  }

  private func applyServerMenuState(_ state: ServerMenuState) {
    serverStatusMenuItem?.title = state.header
    // When the runtime itself cannot be resolved, that reason outranks the
    // generic waiting-for-runtime line: it is the only thing that tells the
    // Founder what actually happened and what to do next.
    let detail = runtimeResolutionFailure ?? state.detail
    serverDetailMenuItem?.title = detail
    serverDetailMenuItem?.isHidden = detail.isEmpty
    startServerMenuItem?.isEnabled = state.canStart
    stopServerMenuItem?.isEnabled = state.canStop
    restartServerMenuItem?.isEnabled = state.canRestart
    let navigation = resolveServerNavigation(caretakerData: lastCaretakerData)
    // No resolved runtime means no navigable server, whatever the last envelope
    // happened to say.
    let navigable = navigation.isAvailable && canonicalInstall != nil
    openServerMenuItem?.isEnabled = navigable
    openServerMenuItem?.toolTip = runtimeResolutionFailure ?? navigation.unavailableReason
    openWorkspacesMenuItem?.isEnabled = navigable
    openWorkspacesMenuItem?.toolTip = runtimeResolutionFailure ?? navigation.unavailableReason
    openServerLogsMenuItem?.isEnabled =
      canonicalInstall != nil && serverUtilityProcess?.isRunning != true
    statusItem?.button?.image = statusIcon(health: state.health)
    statusItem?.button?.toolTip = "Vibecrafted — \(state.header)"
    applyRuntimePackMenuState()
  }

  private func applyRuntimePackMenuState() {
    let state = deriveRuntimePackMenuState(
      generation: canonicalInstall?.root.lastPathComponent,
      signedSourceRevision: signedCarrierRevisions?.source,
      runtimeReady: canonicalInstall != nil)
    runtimePackStatusMenuItem?.title = state.header
    // Same precedence as the server line: a refusal from the owner first, then
    // a problem with the shared service, then whatever the policy derived.
    let detail = runtimeResolutionFailure ?? runtimeAdvisory ?? state.detail
    runtimePackDetailMenuItem?.title = detail
    runtimePackDetailMenuItem?.isHidden = detail.isEmpty
    revealRuntimeHomeMenuItem?.isEnabled = state.actionsEnabled
    openControlPlaneMenuItem?.isEnabled = state.actionsEnabled
    copyRuntimeIdentityMenuItem?.isEnabled = state.actionsEnabled
    // Repair is deliberately not gated on `actionsEnabled`: it is the way out
    // of an absent or refused installation, which is exactly when the
    // inspection actions above have nothing to inspect.
  }

  @objc private func revealRuntimeHomeFromStatusItem() {
    guard let install = canonicalInstall else { return }
    NSWorkspace.shared.open(install.runtimeHome)
  }

  @objc private func openControlPlaneFromStatusItem() {
    guard let install = canonicalInstall else { return }
    let controlPlane = install.craftedHome.appendingPathComponent(
      "control_plane", isDirectory: true)
    NSWorkspace.shared.open(
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

  /// Explicit repair: publish this App's signed carrier over the current
  /// installation. Normal opening never reaches here — it resolves whatever is
  /// installed — so replacing a runtime stays something the Founder asks for,
  /// with the generation that would be written named before the fact.
  @objc private func repairRuntimeFromBundledPack() {
    let confirmation = NSAlert()
    confirmation.alertStyle = .warning
    confirmation.messageText = "Reinstall the Vibecrafted runtime from this App?"
    let installed = canonicalInstall.map { "Installed generation: \($0.root.lastPathComponent).\n" }
      ?? "No usable runtime is currently installed.\n"
    confirmation.informativeText =
      installed
      + "This publishes the Runtime Pack carried by this App"
      + (signedCarrierRevisions.map { " (source \(String($0.source.prefix(8))))" } ?? "")
      + ". The installer refuses to replace a newer runtime with an older carrier."
    confirmation.addButton(withTitle: "Cancel")
    confirmation.addButton(withTitle: "Reinstall")
    guard confirmation.runModal() == .alertSecondButtonReturn else { return }

    repairRuntimeMenuItem?.isEnabled = false
    defer { repairRuntimeMenuItem?.isEnabled = true }
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
    // Repair is usually asked for because the terminal would not open. Attach
    // to the live one if it survived, otherwise open it on the repaired
    // runtime; the launch path re-resolves through the owner and so also proves
    // the repair.
    launchWorkspaceTerminal()
  }

  @objc private func openConsoleFromStatusItem() {
    Task {
      _ = try? await getServerStatus()
      await MainActor.run {
        self.showMainWindowIfNeeded()
      }
    }
  }

  @objc private func openServerFromStatusItem() {
    openServerPage(\.server, label: "VC Server")
  }

  @objc private func openWorkspacesFromStatusItem() {
    openServerPage(\.workspaces, label: "Workspaces")
  }

  private func openServerPage(
    _ keyPath: KeyPath<ServerNavigationState, URL?>, label: String
  ) {
    let navigation = resolveServerNavigation(caretakerData: lastCaretakerData)
    guard let url = navigation[keyPath: keyPath] else {
      let alert = NSAlert()
      alert.alertStyle = .warning
      alert.messageText = "\(label) is unavailable"
      alert.informativeText =
        navigation.unavailableReason ?? "The configured VC Server is unavailable."
      alert.addButton(withTitle: "OK")
      alert.runModal()
      return
    }
    NSWorkspace.shared.open(url)
  }

  @objc private func openTerminalFromStatusItem() {
    if let application = terminalApplication, !application.isTerminated {
      application.activate(options: [])
      return
    }
    // The wrapper execs its host in place, so a live terminal is not always
    // registered as an application. Try to adopt it before deciding it is gone
    // and opening a second one.
    if let process = terminalProcess, process.isRunning {
      if let application = NSRunningApplication(processIdentifier: process.processIdentifier) {
        terminalApplication = application
        application.activate(options: [])
      }
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
    openServerLogsMenuItem?.isEnabled = false
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
          NSWorkspace.shared.open(logs.directory)
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
    alert.informativeText = caretakerDiagnosticsLines(data: lastCaretakerData)
      .joined(separator: "\n")
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
      "The tray dot reports VC Server: green is healthy, amber is transitioning, red needs attention, and gray is stopped. Open VC Server and Open Workspaces use the configured live server only when its caretaker says it is available. Open Native Console shows the local AppKit console. Reveal Control Plane Files opens the on-disk runtime state."
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
    // This one answer is needed synchronously, because the quit decision hangs
    // on it. Waiting for it forever means a wedged launcher can make the App
    // unquittable, so the wait is bounded and a silent launcher becomes an
    // explicit "unavailable" the Founder is asked about.
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
