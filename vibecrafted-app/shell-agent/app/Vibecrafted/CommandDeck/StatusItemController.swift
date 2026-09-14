// Vibecrafted — status item
// Created by Vetcoders
//
// Sole tray owner for the shell App. Lifecycle precision stays in
// AppKit (NSStatusItem). Actions are typed callbacks — this type never owns
// runtime processes and never treats Quit App as Stop Runtime.

import AppKit

/// Typed tray verbs. AppDelegate routes these to native bridges; this leaf only emits.
enum StatusItemAction: String, Sendable, CaseIterable {
  case showCommandDeck
  case openTerminal
  case checkForUpdates
  case retryConnection
  case repairRuntime
  case stopRuntime
  case showDiagnostics
  case showServer
  case showWorkspaces
  case startServer
  case restartServer
  case showLogs
  case revealRuntime
  case revealControlPlane
  case copyRuntimeIdentity
  case help
  case quitApp
}

/// Injected enablement for verbs that depend on session/runtime truth owned
/// elsewhere. Defaults deny process actions before runtime truth arrives.
struct StatusItemAvailability: Equatable, Sendable {
  var canShowCommandDeck: Bool
  var canOpenTerminal: Bool
  var canCheckForUpdates: Bool = true
  var canRetryConnection: Bool
  var canRepairRuntime: Bool
  var canStopRuntime: Bool
  var canShowDiagnostics: Bool
  var canQuitApp: Bool
  var runtimeActions: Set<StatusItemAction> = []

  static let `default` = StatusItemAvailability(
    canShowCommandDeck: true,
    canOpenTerminal: false,
    canCheckForUpdates: true,
    canRetryConnection: true,
    canRepairRuntime: true,
    canStopRuntime: false,
    canShowDiagnostics: true,
    canQuitApp: true
  )
}

/// Presentation snapshot for the tray chrome (not process truth).
struct StatusItemPresentation: Equatable, Sendable {
  var health: TrayServerHealth
  var statusLine: String
  var detailLine: String
  var availability: StatusItemAvailability
  var toolTip: String

  static let bootstrapping = StatusItemPresentation(
    health: .checking,
    statusLine: "Status: Starting…",
    detailLine: "Waiting for the runtime owner",
    availability: .default,
    toolTip: "Vibecrafted — preparing"
  )
}

/// AppKit menu-bar owner for Vibecrafted. Construct once, call `install()`,
/// push presentation updates; remove with `uninstall()`. Does not create a
/// second status item in mux-agent or tray-agent.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
  typealias Handler = (StatusItemAction) -> Void

  private let handler: Handler
  private var statusItem: NSStatusItem?
  private var presentation: StatusItemPresentation

  private weak var statusLineItem: NSMenuItem?
  private var actionItems: [StatusItemAction: NSMenuItem] = [:]

  /// Keep user-facing labels and their typed actions in one inspectable shape.
  /// AppDelegate remains the dispatch owner; this menu only emits these actions.
  static let primaryCommands: [StatusItemMenuCommand] = [
    .init(action: .showCommandDeck, title: "Open Vibecrafted", keyEquivalent: "o"),
    .init(action: .openTerminal, title: "Open Terminal", keyEquivalent: "t"),
    .init(action: .checkForUpdates, title: "Check for Updates…"),
    .init(action: .showWorkspaces, title: "Workspaces"),
    .init(action: .help, title: "Help & Diagnostics…")
  ]

  static let advancedCommands: [StatusItemMenuCommand] = [
    .init(action: .retryConnection, title: "Reconnect"),
    .init(action: .repairRuntime, title: "Repair Runtime…"),
    .init(action: .showServer, title: "Open Runtime Server"),
    .init(action: .startServer, title: "Start Runtime Service"),
    .init(action: .restartServer, title: "Restart Runtime Service"),
    .init(action: .stopRuntime, title: "Stop Runtime Service…"),
    .init(action: .showLogs, title: "Open Runtime Logs"),
    .init(action: .revealRuntime, title: "Open Runtime Folder"),
    .init(action: .revealControlPlane, title: "Open Control Plane Folder"),
    .init(action: .copyRuntimeIdentity, title: "Copy Runtime Identity")
  ]

  init(
    presentation: StatusItemPresentation = .bootstrapping,
    handler: @escaping Handler
  ) {
    self.presentation = presentation
    self.handler = handler
    super.init()
  }

  var isInstalled: Bool { statusItem != nil }

  /// Install the one shell status item. Idempotent.
  func install() {
    guard statusItem == nil else {
      applyPresentation()
      return
    }

    let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    item.button?.imagePosition = .imageOnly
    item.button?.imageScaling = .scaleProportionallyDown

    let menu = NSMenu(title: "Vibecrafted")
    menu.delegate = self
    menu.autoenablesItems = false

    let status = menu.addItem(withTitle: presentation.statusLine, action: nil, keyEquivalent: "")
    status.isEnabled = false
    statusLineItem = status

    menu.addItem(.separator())
    Self.primaryCommands.forEach { add($0, to: menu) }
    menu.addItem(.separator())

    let advanced = NSMenu(title: "Advanced")
    Self.advancedCommands.enumerated().forEach { index, command in
      if index == 2 || index == 6 || index == 7 { advanced.addItem(.separator()) }
      add(command, to: advanced)
    }
    let advancedItem = menu.addItem(withTitle: "Advanced", action: nil, keyEquivalent: "")
    advancedItem.submenu = advanced
    menu.addItem(.separator())

    // Quit App leaves server / PTYs / agents / sessions alive by contract.
    // AppDelegate routes this callback to App termination only — never Stop Runtime.
    let quit = menu.addItem(
      withTitle: "Quit Vibecrafted",
      action: #selector(emitQuitApp(_:)),
      keyEquivalent: "q"
    )
    quit.target = self
    actionItems[.quitApp] = quit

    item.menu = menu
    statusItem = item
    applyPresentation()
  }

  /// Remove the status item from the system status bar.
  func uninstall() {
    guard let item = statusItem else { return }
    NSStatusBar.system.removeStatusItem(item)
    statusItem = nil
    statusLineItem = nil
    actionItems.removeAll()
  }

  func update(_ presentation: StatusItemPresentation) {
    self.presentation = presentation
    guard statusItem != nil else { return }
    applyPresentation()
  }

  func menuWillOpen(_ menu: NSMenu) {
    applyPresentation()
  }

  // MARK: - Presentation

  private func applyPresentation() {
    let glyph = TrayGlyph.statusImage(health: presentation.health)
    statusItem?.button?.image = glyph
    statusItem?.button?.toolTip = presentation.toolTip
    statusItem?.button?.setAccessibilityLabel(glyph.accessibilityDescription)

    statusLineItem?.title = presentation.statusLine

    let availability = presentation.availability
    for (action, item) in actionItems {
      item.isEnabled = Self.isEnabled(action, availability: availability)
    }
  }

  private func add(_ command: StatusItemMenuCommand, to menu: NSMenu) {
    let item = menu.addItem(
      withTitle: command.title, action: #selector(emitMenuAction(_:)), keyEquivalent: command.keyEquivalent)
    if !command.keyEquivalent.isEmpty { item.keyEquivalentModifierMask = [.command, .option] }
    item.target = self
    item.representedObject = command.action.rawValue
    if command.action == .checkForUpdates {
      item.toolTip = "Sprawdź aktualizacje"
    }
    actionItems[command.action] = item
  }

  static func isEnabled(_ action: StatusItemAction, availability: StatusItemAvailability) -> Bool {
    switch action {
    case .showCommandDeck: availability.canShowCommandDeck
    case .openTerminal: availability.canOpenTerminal
    case .checkForUpdates: availability.canCheckForUpdates
    case .retryConnection: availability.canRetryConnection
    case .repairRuntime: availability.canRepairRuntime
    case .stopRuntime: availability.canStopRuntime
    case .showDiagnostics: availability.canShowDiagnostics
    case .quitApp: availability.canQuitApp
    case .showServer, .showWorkspaces, .help: true
    case .startServer, .restartServer, .showLogs, .revealRuntime, .revealControlPlane, .copyRuntimeIdentity:
      availability.runtimeActions.contains(action)
    }
  }

  // MARK: - Actions

  private func emit(_ action: StatusItemAction) {
    handler(action)
  }

  @objc private func emitMenuAction(_ item: NSMenuItem) {
    guard let raw = item.representedObject as? String,
      let action = StatusItemAction(rawValue: raw) else { return }
    emit(action)
  }

  @objc private func emitQuitApp(_ sender: Any?) {
    emit(.quitApp)
  }
}

struct StatusItemMenuCommand: Equatable {
  let action: StatusItemAction
  let title: String
  let keyEquivalent: String

  init(action: StatusItemAction, title: String, keyEquivalent: String = "") {
    self.action = action
    self.title = title
    self.keyEquivalent = keyEquivalent
  }
}
