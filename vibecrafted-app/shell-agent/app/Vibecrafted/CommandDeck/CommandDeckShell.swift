// Vibecrafted — native window split
// Created by Vetcoders

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
  @ViewBuilder var workspace: () -> Workspace

  @State private var selection: CommandDeckDestination? = .overview
  @State private var columnVisibility = NavigationSplitViewVisibility.all
  @State private var inspectorPresented = true
  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  var body: some View {
    NavigationSplitView(columnVisibility: $columnVisibility) {
      CommandDeckSidebar(selection: $selection, phase: phase)
        .navigationSplitViewColumnWidth(min: 180, ideal: 220, max: 280)
    } detail: {
      workspace()
        .navigationTitle(selection?.title ?? "Vibecrafted")
        .toolbar {
          CommandDeckColumnToggles(
            sidebarHidden: columnVisibility == .detailOnly,
            inspectorPresented: inspectorPresented,
            toggleSidebar: toggleSidebar,
            toggleInspector: toggleInspector
          )
        }
    }
    .inspector(isPresented: $inspectorPresented) {
      CommandDeckInspector(phase: phase)
        .inspectorColumnWidth(min: 240, ideal: 320, max: 480)
    }
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: columnVisibility)
    .animation(reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration), value: inspectorPresented)
    .onChange(of: selection) { _, destination in
      guard let destination else { return }
      openPath?(destination.path)
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleSidebar)) { _ in
      toggleSidebar()
    }
    .onReceive(NotificationCenter.default.publisher(for: .commandDeckToggleInspector)) { _ in
      toggleInspector()
    }
  }

  private func toggleSidebar() {
    columnVisibility = columnVisibility == .detailOnly ? .all : .detailOnly
  }

  private func toggleInspector() {
    inspectorPresented.toggle()
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
