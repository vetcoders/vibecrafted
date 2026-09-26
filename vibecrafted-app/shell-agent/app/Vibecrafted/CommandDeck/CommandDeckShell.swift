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
  var openPath: ((String) -> Void)?
  var frameOrigin: (() -> URL?)?
  var presentFrame: (() -> Bool)?
  var restoreFrame: (() -> Void)?
  @ViewBuilder var workspace: () -> Workspace

  @State private var selection: CommandDeckDestination? = .overview
  @State private var columnVisibility = NavigationSplitViewVisibility.all
  @State private var inspectorPresented = false
  @State private var projectingFrame = false
  @State private var sidebarBeforeProjection: NavigationSplitViewVisibility?
  @State private var inspectorBeforeProjection = false
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
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
      .navigationTitle(projectingFrame ? "Frame" : (selection?.title ?? "Vibecrafted"))
      .toolbarRole(.editor)
      .toolbar {
        CommandDeckColumnToggles(
          sidebarHidden: columnVisibility == .detailOnly,
          inspectorPresented: inspectorPresented,
          toggleSidebar: toggleSidebar,
          toggleInspector: toggleInspector
        )
      }
    }
    .inspector(isPresented: runInspectorPresented) {
      CommandDeckInspector(phase: phase)
        .inspectorColumnWidth(min: 240, ideal: 320, max: 480)
    }
    .background(BrowserToolbarStripper())
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: columnVisibility)
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: inspectorPresented)
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: projectingFrame)
    .onChange(of: selection) { _, destination in
      if destination?.showsRunDocument != true {
        inspectorPresented = false
      }
      guard !projectingFrame, let destination else { return }
      openPath?(destination.path)
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleSidebar)) { _ in
      toggleSidebar()
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleInspector)) { _ in
      toggleInspector()
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
    guard !projectingFrame, selection?.showsRunDocument == true else { return }
    inspectorPresented.toggle()
  }

  /// Projects and Costs are not run lists. The empty document stays closed.
  private var runInspectorPresented: Binding<Bool> {
    Binding(
      get: { inspectorPresented && selection?.showsRunDocument == true },
      set: { inspectorPresented = $0 }
    )
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

private struct CommandDeckColumnToggles: ToolbarContent {
  let sidebarHidden: Bool
  let inspectorPresented: Bool
  let toggleSidebar: () -> Void
  let toggleInspector: () -> Void

  var body: some ToolbarContent {
    ToolbarItem(placement: .navigation) {
      Button(sidebarHidden ? "Show Sidebar" : "Hide Sidebar", systemImage: "sidebar.leading", action: toggleSidebar)
        .accessibilityLabel(sidebarHidden ? "Show Sidebar" : "Hide Sidebar")
        .help(sidebarHidden ? "Show Sidebar" : "Hide Sidebar")
    }
    ToolbarItem(placement: .primaryAction) {
      Button(inspectorPresented ? "Hide Inspector" : "Show Inspector", systemImage: "sidebar.trailing", action: toggleInspector)
        .accessibilityLabel(inspectorPresented ? "Hide Inspector" : "Show Inspector")
        .help(inspectorPresented ? "Hide Inspector" : "Show Inspector")
    }
  }
}
