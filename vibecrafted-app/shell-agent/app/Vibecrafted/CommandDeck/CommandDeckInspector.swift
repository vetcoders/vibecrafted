// Vibecrafted — native document pane
// Created by Vetcoders

import SwiftUI

/// The right column. A selected run still lives in the workspace page, so
/// this pane stays an empty document until a native selection exists.
struct CommandDeckInspector: View {
  let phase: CommandDeckPhase
  var openPath: ((String) -> Void)?

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text("Document")
        .font(.caption)
        .foregroundStyle(.secondary)
        .textCase(.uppercase)
      Text("Nothing selected")
        .font(.title3)
      Text("Open Runs to read reports and transcripts.")
        .font(.body)
        .foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
      if let openPath {
        Button("View runs", systemImage: "play.rectangle") { openPath("/runs") }
        Button("Open project", systemImage: "folder") { openPath("/projects") }
        Button("Diagnostics", systemImage: "stethoscope") { openPath("/diagnostics") }
      }
      Spacer(minLength: 0)
      HStack(spacing: 6) {
        Image(systemName: phase.statusSymbol)
        Text(phase.statusTitle)
      }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(16)
    // Match the native column's minimum during SwiftUI's zero-width sizing
    // probe too; otherwise the wrapped paragraph claims a screen-high minimum.
    .frame(minWidth: 240, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .accessibilityElement(children: .combine)
    .accessibilityLabel("Document")
    .accessibilityValue("Nothing selected")
    .accessibilityIdentifier("command-deck-inspector")
  }
}
