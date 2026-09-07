// Vibecrafted — Main Window Controller
// Created by Vetcoders

import AppKit
import SwiftUI

/// The App retains this controller after close; reopen mounts the same session.
@MainActor
final class MainWindowController: NSWindowController {
  init(model: AppModel, session: WebConsoleSession, actions: any CommandDeckActionHandling) {
    let window = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
      styleMask: [.titled, .closable, .miniaturizable, .resizable],
      backing: .buffered, defer: false)
    window.title = "Vibecrafted"
    window.isReleasedWhenClosed = false
    window.minSize = NSSize(width: 800, height: 600)
    window.setFrameAutosaveName("VibecraftedCommandDeck")
    window.center()
    window.contentView = NSHostingView(rootView: CommandDeckRootView(
      model: model, session: session, actions: actions))
    super.init(window: window)
  }

  @available(*, unavailable)
  required init?(coder: NSCoder) { fatalError("Use the App-owned session initializer") }
}

@MainActor
private struct CommandDeckRootView: View {
  let model: AppModel
  let session: WebConsoleSession
  let actions: any CommandDeckActionHandling

  var body: some View {
    CommandDeckView(presentation: model.presentation, actions: actions) {
      WebConsoleHost(session: session)
    }
  }
}
