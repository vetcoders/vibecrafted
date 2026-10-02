// Vibecrafted — native window split
// Created by Vetcoders

import AppKit
import SwiftUI

extension Notification.Name {
  static let commandDeckToggleSidebar = Notification.Name("io.vetcoders.vibecrafted.toggle-sidebar")
  static let commandDeckToggleInspector = Notification.Name("io.vetcoders.vibecrafted.toggle-inspector")
}

/// Sidebar | workspace | inspector. Each side column hides on its own.
/// Visibility lasts for this window session. The app has no preferences store
/// to reuse, so this is not written to disk.
struct CommandDeckShell<Workspace: View>: View {
  let phase: CommandDeckPhase
  var navigation: WebTabNavigation?
  var openPath: ((String) -> Void)?
  var frameOrigin: (() -> URL?)?
  var presentFrame: (() -> Bool)?
  var restoreFrame: (() -> Void)?
  @ViewBuilder var workspace: () -> Workspace

  @State private var selection: CommandDeckDestination? = .overview
  @State private var columnVisibility = NavigationSplitViewVisibility.all
  @State private var inspectorPresented = true
  @State private var projectingFrame = false
  @State private var sidebarBeforeProjection: NavigationSplitViewVisibility?
  @State private var inspectorBeforeProjection = false
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    inspectedShell
    .background(BrowserToolbarStripper())
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: columnVisibility)
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: inspectorPresented)
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: projectingFrame)
    .onChange(of: selection) { _, destination in
      guard !projectingFrame, let destination, currentPath != destination.path else { return }
      openPath?(destination.path)
    }
    .onChange(of: currentPath, initial: true) { _, path in
      if let destination = CommandDeckDestination.allCases.first(where: { $0.path == path }) {
        selection = destination
      }
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleSidebar)) { _ in
      toggleSidebar()
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleInspector)) { _ in
      toggleInspector()
    }
  }

  private var navigationShell: some View {
    NavigationSplitView(columnVisibility: $columnVisibility) {
      CommandDeckSidebar(selection: $selection, phase: phase)
        .navigationSplitViewColumnWidth(min: 180, ideal: 220, max: 280)
    } detail: {
      VStack(spacing: 0) {
        if projectingFrame {
          FrameProjectionBar(onBack: restoreDashboardFromFrame)
        }
        workspace()
          .safeAreaInset(edge: .bottom, alignment: .trailing, spacing: 0) {
            if !projectingFrame {
              FrameProjectionMarkButton(
                available: frameOrigin?() != nil,
                present: activateFrame
              )
              .padding(.trailing, 16)
              .padding(.bottom, 12)
              .padding(.top, 4)
            }
          }
      }
      // The document owns its title; the native toolbar owns window actions.
      .navigationTitle("Vibecrafted")
      .toolbarRole(.editor)
      .toolbar {
        ToolbarItem(placement: .primaryAction) {
          Button(inspectorPresented && inspectorAvailable ? "Hide Inspector" : "Show Inspector", systemImage: "sidebar.right", action: toggleInspector)
            .labelStyle(.titleAndIcon)
            .disabled(!inspectorAvailable)
            .help("Show or hide the run document (⌥⌘I). Available on Overview and Runs.")
            .accessibilityIdentifier("command-deck-inspector-toggle")
        }
      }

    }
  }

  /// Remove the native modifier on routes without a run document. Keeping
  /// it mounted with a derived false binding lets AppKit retain an obsolete
  /// column while navigation rebuilds the split. The persistent web session
  /// owns the canvas across these native remounts.
  @ViewBuilder
  private var inspectedShell: some View {
    if inspectorAvailable {
      navigationShell.inspector(isPresented: Binding(
        get: { inspectorPresented },
        set: { if inspectorAvailable { inspectorPresented = $0 } }
      )) {
        CommandDeckInspector(phase: phase, openPath: openPath)
          .inspectorColumnWidth(min: 240, ideal: 260, max: 480)
      }
    } else {
      navigationShell
    }
  }

  private func activateFrame() {
    guard frameOrigin?() != nil else { return }
    guard presentFrame?() == true else { return }
    sidebarBeforeProjection = columnVisibility
    inspectorBeforeProjection = inspectorPresented
    projectingFrame = true
    columnVisibility = .detailOnly
    inspectorPresented = false
  }

  private func restoreDashboardFromFrame() {
    restoreFrame?()
    projectingFrame = false
    if let sidebarBeforeProjection {
      columnVisibility = sidebarBeforeProjection
    }
    self.sidebarBeforeProjection = nil
    inspectorPresented = inspectorBeforeProjection
  }

  private func toggleSidebar() {
    guard !projectingFrame else { return }
    columnVisibility = columnVisibility == .detailOnly ? .all : .detailOnly
  }

  private func toggleInspector() {
    guard inspectorAvailable else { return }
    inspectorPresented.toggle()
  }

  private var currentPath: String? { navigation?.currentURL?.path }

  /// Web cards, history and sidebar navigation share the destination policy.
  /// Hiding on other routes preserves the window's open/closed preference.
  private var inspectorAvailable: Bool {
    guard !projectingFrame else { return false }
    guard let currentPath else { return selection?.showsRunDocument == true }
    return currentPath.hasPrefix("/run/")
      || CommandDeckDestination.allCases.first(where: { $0.path == currentPath })?.showsRunDocument == true
  }

}

/// Thin bar above the projected web view. The way back stays in this window.
private struct FrameProjectionBar: View {
  let onBack: () -> Void

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    HStack(spacing: 8) {
      Button("Back", systemImage: "chevron.backward", action: onBack)
        .buttonStyle(.borderless)
        .accessibilityLabel("Back to dashboard")
        .help("Back. Returns to the dashboard in this window.")
      Spacer(minLength: 0)
      Text("Frame")
        .font(.caption)
        .foregroundStyle(theme.palette.muted)
    }
    .padding(.horizontal, 10)
    .padding(.vertical, 4)
    .background(theme.palette.surfaceRaised)
    .overlay(alignment: .bottom) {
      Rectangle().fill(theme.palette.stroke).frame(height: theme.strokeWidth)
    }
  }
}

struct FrameProjectionMarkButton: View {
  let available: Bool
  let present: () -> Void

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    Button(action: present) {
      Text(FrameProjectionMark.label)
        .font(.caption.monospaced().weight(.semibold))
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
    }
    .buttonStyle(.plain)
    .disabled(!available)
    .foregroundStyle(theme.palette.ink)
    .background(theme.palette.surfaceRaised, in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    .overlay {
      RoundedRectangle(cornerRadius: 6, style: .continuous)
        .strokeBorder(theme.palette.stroke, lineWidth: theme.strokeWidth)
    }
    .opacity(available ? 1 : 0.72)
    .help(available ? "Open Frame. Shows the configured frame in this window." : "Frame is not available.")
    .accessibilityLabel(FrameProjectionMark.label)
    .accessibilityValue(available ? "available" : "unavailable")
    .fixedSize()
  }
}

/// macOS puts Back / Forward / Reload into a window that hosts a web view.
/// Those are not this product's chrome. The sidebar toggle stays.
private struct BrowserToolbarStripper: NSViewRepresentable {
  func makeNSView(context: Context) -> NSView {
    let view = NSView(frame: .zero)
    DispatchQueue.main.async { Self.strip(view.window) }
    return view
  }

  func updateNSView(_ nsView: NSView, context: Context) {
    DispatchQueue.main.async { Self.strip(nsView.window) }
  }

  private static func strip(_ window: NSWindow?) {
    guard let toolbar = window?.toolbar else { return }
    let banned = ["back", "forward", "reload"]
    let indexes = toolbar.items.indices.filter { index in
      let item = toolbar.items[index]
      let blob = (item.itemIdentifier.rawValue + " " + item.label + " " + item.paletteLabel).lowercased()
      if blob.contains("sidebar") { return false }
      return item.isNavigational || banned.contains { blob.contains($0) }
    }
    for index in indexes.reversed() {
      toolbar.removeItem(at: index)
    }
  }
}
