// Vibecrafted — Main Window Controller
// Created by Vetcoders

import AppKit
import SwiftUI

/// One window shape for every native tab: the console and each tool or
/// reference tab are `NSWindow`s in one tab group, so AppKit's own tab bar,
/// Window menu and ⌘⇧] / ⌘⇧[ tab switching apply. The SwiftUI toolbar is
/// bridged into the unified titlebar; there is no second chrome row.
@MainActor
enum CommandDeckWindowFactory {
  /// Windows sharing this identifier tab together. One product, one group.
  static let tabbingIdentifier = "io.vetcoders.vibecrafted.command-deck"

  static func makeWindow(title: String, frameAutosaveName: String?) -> NSWindow {
    let window = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
      styleMask: [.titled, .closable, .miniaturizable, .resizable],
      backing: .buffered, defer: false)
    window.title = title
    window.isReleasedWhenClosed = false
    window.minSize = NSSize(width: 800, height: 600)
    window.tabbingIdentifier = tabbingIdentifier
    window.tabbingMode = .preferred
    window.toolbarStyle = .unified
    window.titleVisibility = .visible
    // Frame autosave is geometry only. AppKit window restoration is owned
    // here: titled windows default restorable; these two assignments are the
    // verified off switch (NSWindowRestoration.h). Loginwindow must not
    // restitch the Command Deck.
    window.isRestorable = false
    window.restorationClass = nil
    if let frameAutosaveName {
      window.setFrameAutosaveName(frameAutosaveName)
    }
    window.center()
    return window
  }

  static func mount<Root: View>(_ root: Root, in window: NSWindow) {
    let hosting = NSHostingView(rootView: root)
    // The SwiftUI `.toolbar` becomes the window's toolbar. Title stays native
    // so the tab bar shows the window title the controller sets.
    hosting.sceneBridgingOptions = [.toolbars]
    window.contentView = hosting
  }
}

/// The console tab. The App retains this controller after close; reopen
/// mounts the same session, so no runtime state is ever lost to a window.
@MainActor
final class MainWindowController: NSWindowController, CommandDeckNavigationHandling {
  let session: WebConsoleSession
  private let openExternally: @MainActor (URL) -> Void
  /// Configured `vc-frame web` origin, when `[tools.vc-frame]` names one.
  /// Sidebar Frame loads it in this window's web view. Absent, Frame stays
  /// the product `/frame` page. Never a second window.
  var frameWebURL: (() -> URL?)?

  init(
    model: AppModel, session: WebConsoleSession, actions: any CommandDeckActionHandling,
    openExternally: @escaping @MainActor (URL) -> Void = { NSWorkspace.shared.open($0) }
  ) {
    self.session = session
    self.openExternally = openExternally
    let window = CommandDeckWindowFactory.makeWindow(
      title: "Vibecrafted", frameAutosaveName: "VibecraftedCommandDeck")
    super.init(window: window)
    CommandDeckWindowFactory.mount(
      CommandDeckRootView(model: model, session: session, actions: actions, navigationHandler: self,
        openPath: { [weak self] path in self?.openWorkspacePath(path) }),
      in: window)
  }

  /// Sidebar selection. Frame, when configured, is the Zellij web client in
  /// this web view. Every other destination returns to the product console.
  func openWorkspacePath(_ path: String) {
    if path == CommandDeckDestination.frame.path, let url = frameWebURL?() {
      session.present(service: url)
      return
    }
    if session.restoreProduct(route: path) { return }
    session.navigate(path: path)
  }

  @available(*, unavailable)
  required init?(coder: NSCoder) { fatalError("Use the App-owned session initializer") }

  /// History moves apply to this window's session only.
  func navigate(_ action: CommandDeckNavigationAction) {
    switch action {
    case .home: session.goHome()
    case .back: session.goBack()
    case .forward: session.goForward()
    case .openInBrowser:
      guard let url = session.navigation.currentURL, session.runtimeOrigin?.covers(url) == true else { return }
      openExternally(url)
    }
  }
}

@MainActor
private struct CommandDeckRootView: View {
  let model: AppModel
  let session: WebConsoleSession
  let actions: any CommandDeckActionHandling
  let navigationHandler: any CommandDeckNavigationHandling
  var openPath: (String) -> Void

  var body: some View {
    CommandDeckView(
      presentation: model.presentation, actions: actions,
      navigation: session.navigation, navigationHandler: navigationHandler,
      openPath: openPath
    ) {
      WebConsoleHost(session: session)
    }
  }
}
