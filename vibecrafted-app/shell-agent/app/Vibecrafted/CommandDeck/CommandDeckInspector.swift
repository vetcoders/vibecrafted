// Vibecrafted — native document pane
// Created by Vetcoders

import SwiftUI

/// The right column. A selected run still lives in the workspace page, so
/// this pane stays an empty document until a native selection exists.
struct CommandDeckInspector: View {
  let phase: CommandDeckPhase

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text("Document")
        .font(.caption)
        .foregroundStyle(.secondary)
        .textCase(.uppercase)
      Text("Nothing selected")
        .font(.title3)
      Text("Select a run in the workspace. Its transcript stays in the page until a native row selection exists.")
        .font(.body)
        .foregroundStyle(.secondary)
        .fixedSize(horizontal: false, vertical: true)
      Spacer(minLength: 0)
      HStack(spacing: 6) {
        Image(systemName: phase.statusSymbol)
        Text(phase.statusTitle)
      }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
    .padding(16)
    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    .accessibilityElement(children: .combine)
    .accessibilityLabel("Document")
    .accessibilityValue("Nothing selected")
  }
}
