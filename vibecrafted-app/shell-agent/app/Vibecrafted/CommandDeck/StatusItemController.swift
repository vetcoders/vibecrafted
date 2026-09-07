// Vibecrafted — Command Deck status item
// Created by Vetcoders
//
// Future sole tray owner for the shell App. Lifecycle precision stays in
// AppKit (NSStatusItem). Actions are typed callbacks — this type never owns
// runtime processes and never treats Quit App as Stop Runtime.

import AppKit

/// Typed tray verbs. W2 wires these to native bridges; this leaf only emits.
enum StatusItemAction: String, Sendable, CaseIterable {
  case showCommandDeck
  case openTerminal
  case retryConnection
  case repairRuntime
  case stopRuntime
  case showDiagnostics
  case quitApp
}

/// Injected enablement for verbs that depend on session/runtime truth owned
/// elsewhere. Defaults keep the menu usable before W2 wiring.
struct StatusItemAvailability: Equatable, Sendable {
  var canShowCommandDeck: Bool
  var canOpenTerminal: Bool
  var canRetryConnection: Bool
  var canRepairRuntime: Bool
  var canStopRuntime: Bool
  var canShowDiagnostics: Bool
  var canQuitApp: Bool

  static let `default` = StatusItemAvailability(
    canShowCommandDeck: true,
    canOpenTerminal: true,
    canRetryConnection: true,
    canRepairRuntime: true,
    canStopRuntime: true,
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
    statusLine: "Command Deck: Preparing…",
    detailLine: "Waiting for session wiring",
    availability: .default,
    toolTip: "Vibecrafted — preparing"
  )
}

/// AppKit menu-bar owner for Command Deck. Construct once, call `install()`,
/// push presentation updates; remove with `uninstall()`. Does not create a
/// second status item in mux-agent or tray-agent.
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
  typealias Handler = (StatusItemAction) -> Void

  private let handler: Handler
  private var statusItem: NSStatusItem?
  private var presentation: StatusItemPresentation

  private weak var statusLineItem: NSMenuItem?
  private weak var detailLineItem: NSMenuItem?
  private weak var showCommandDeckItem: NSMenuItem?
  private weak var openTerminalItem: NSMenuItem?
  private weak var retryItem: NSMenuItem?
  private weak var repairItem: NSMenuItem?
  private weak var stopRuntimeItem: NSMenuItem?
  private weak var diagnosticsItem: NSMenuItem?
  private weak var quitAppItem: NSMenuItem?

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

    let detail = menu.addItem(withTitle: presentation.detailLine, action: nil, keyEquivalent: "")
    detail.isEnabled = false
    detailLineItem = detail

    menu.addItem(.separator())

    let showDeck = menu.addItem(
      withTitle: "Show Command Deck",
      action: #selector(emitShowCommandDeck(_:)),
      keyEquivalent: "o"
    )
    showDeck.keyEquivalentModifierMask = [.command, .option]
    showDeck.target = self
    showCommandDeckItem = showDeck

    let terminal = menu.addItem(
      withTitle: "Open Terminal",
      action: #selector(emitOpenTerminal(_:)),
      keyEquivalent: "t"
    )
    terminal.keyEquivalentModifierMask = [.command, .option]
    terminal.target = self
    openTerminalItem = terminal

    menu.addItem(.separator())

    let retry = menu.addItem(
      withTitle: "Retry Connection",
      action: #selector(emitRetryConnection(_:)),
      keyEquivalent: ""
    )
    retry.target = self
    retryItem = retry

    let repair = menu.addItem(
      withTitle: "Repair Runtime…",
      action: #selector(emitRepairRuntime(_:)),
      keyEquivalent: ""
    )
    repair.target = self
    repairItem = repair

    // Explicit Stop Runtime — never aliased to Quit App.
    let stop = menu.addItem(
      withTitle: "Stop Runtime…",
      action: #selector(emitStopRuntime(_:)),
      keyEquivalent: ""
    )
    stop.target = self
    stopRuntimeItem = stop

    let diagnostics = menu.addItem(
      withTitle: "Diagnostics…",
      action: #selector(emitShowDiagnostics(_:)),
      keyEquivalent: ""
    )
    diagnostics.target = self
    diagnosticsItem = diagnostics

    menu.addItem(.separator())

    // Quit App leaves server / PTYs / agents / sessions alive by contract.
    // W2 must wire this callback to App termination only — never Stop Runtime.
    let quit = menu.addItem(
      withTitle: "Quit App",
      action: #selector(emitQuitApp(_:)),
      keyEquivalent: "q"
    )
    quit.target = self
    quitAppItem = quit

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
    detailLineItem = nil
    showCommandDeckItem = nil
    openTerminalItem = nil
    retryItem = nil
    repairItem = nil
    stopRuntimeItem = nil
    diagnosticsItem = nil
    quitAppItem = nil
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

    statusLineItem?.title = cappedMenuTitle(presentation.statusLine)
    detailLineItem?.title = cappedMenuTitle(presentation.detailLine)
    detailLineItem?.isHidden = presentation.detailLine.isEmpty

    let availability = presentation.availability
    showCommandDeckItem?.isEnabled = availability.canShowCommandDeck
    openTerminalItem?.isEnabled = availability.canOpenTerminal
    retryItem?.isEnabled = availability.canRetryConnection
    repairItem?.isEnabled = availability.canRepairRuntime
    stopRuntimeItem?.isEnabled = availability.canStopRuntime
    diagnosticsItem?.isEnabled = availability.canShowDiagnostics
    quitAppItem?.isEnabled = availability.canQuitApp
  }

  /// Menu-bar labels stay scannable (swiftui-patterns menu-bar guidance).
  private func cappedMenuTitle(_ title: String) -> String {
    if title.count <= 30 { return title }
    return String(title.prefix(27)) + "..."
  }

  // MARK: - Actions

  private func emit(_ action: StatusItemAction) {
    handler(action)
  }

  @objc private func emitShowCommandDeck(_ sender: Any?) {
    emit(.showCommandDeck)
  }

  @objc private func emitOpenTerminal(_ sender: Any?) {
    emit(.openTerminal)
  }

  @objc private func emitRetryConnection(_ sender: Any?) {
    emit(.retryConnection)
  }

  @objc private func emitRepairRuntime(_ sender: Any?) {
    emit(.repairRuntime)
  }

  @objc private func emitStopRuntime(_ sender: Any?) {
    emit(.stopRuntime)
  }

  @objc private func emitShowDiagnostics(_ sender: Any?) {
    emit(.showDiagnostics)
  }

  @objc private func emitQuitApp(_ sender: Any?) {
    emit(.quitApp)
  }
}
