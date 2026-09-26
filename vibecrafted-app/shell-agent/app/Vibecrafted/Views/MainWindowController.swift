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
  /// The corner mark projects it across this window. Absent, the mark stays
  /// quiet. Never a second window, and never a guessed port.
  var frameWebURL: (() -> URL?)?
  /// Product route to restore when the projection bar goes back.
  private var dashboardPath = "/"

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
      CommandDeckRootView(
        model: model, session: session, actions: actions, navigationHandler: self,
        openPath: { [weak self] path in self?.openWorkspacePath(path) },
        frameOrigin: { [weak self] in self?.configuredFrameOrigin() },
        presentFrame: { [weak self] in self?.presentConfiguredFrame() ?? false },
        restoreFrame: { [weak self] in self?.restoreFromFrame() }),
      in: window)
  }

  /// Sidebar selection returns to the product console in this window.
  /// The frame origin is not a destination; the corner mark presents it.
  func openWorkspacePath(_ path: String) {
    dashboardPath = path
    if session.restoreProduct(route: path) { return }
    session.navigate(path: path)
  }

  /// Corner mark. `present(service:)` is the only way the configured origin
  /// occupies this window. Returns false when there is nothing to project.
  @discardableResult
  func presentConfiguredFrame() -> Bool {
    guard case .runtime = session.scope, let url = frameWebURL?(), WebRuntimeOrigin(url: url) != nil
    else { return false }
    if let current = session.navigation.currentURL, session.runtimeOrigin?.covers(current) == true {
      dashboardPath = Self.dashboardRoute(from: current)
    }
    session.present(service: url)
    return true
  }

  /// Projection bar. Puts the product console back on the route that was open.
  func restoreFromFrame() {
    let path = dashboardPath
    if session.restoreProduct(route: path) { return }
    session.navigate(path: path)
  }

  private func configuredFrameOrigin() -> URL? {
    guard case .runtime = session.scope else { return nil }
    return frameWebURL?()
  }

  private static func dashboardRoute(from url: URL) -> String {
    let path = url.path.isEmpty ? "/" : url.path
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
      let query = components.percentEncodedQuery, !query.isEmpty
    else { return path }
    return "\(path)?\(query)"
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
  var frameOrigin: () -> URL?
  var presentFrame: () -> Bool
  var restoreFrame: () -> Void

  var body: some View {
    CommandDeckView(
      presentation: model.presentation, actions: actions,
      navigation: session.navigation, navigationHandler: navigationHandler,
      openPath: openPath, frameOrigin: frameOrigin, presentFrame: presentFrame,
      restoreFrame: restoreFrame
    ) {
      WebConsoleHost(session: session)
    }
  }
}
