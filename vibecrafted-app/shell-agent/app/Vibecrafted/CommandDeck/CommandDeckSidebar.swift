// Vibecrafted — native source list
// Created by Vetcoders

import SwiftUI

struct CommandDeckSidebar: View {
  @Binding var selection: CommandDeckDestination?
  let phase: CommandDeckPhase

  var body: some View {
    List(selection: $selection) {
      ForEach(CommandDeckDestinationSection.allCases) { section in
        Section(section.title) {
          ForEach(CommandDeckDestination.inSection(section)) { destination in
            Label(destination.title, systemImage: destination.symbol)
              .tag(destination)
              .accessibilityLabel(destination.title)
          }
        }
      }
    }
    .listStyle(.sidebar)
    .accessibilityLabel("Vibecrafted")
    .safeAreaInset(edge: .bottom) {
      footer
    }
  }

  private var footer: some View {
    HStack(spacing: 6) {
      Image(systemName: phase.statusSymbol)
      Text(phase.statusTitle)
    }
      .font(.caption)
      .foregroundStyle(.secondary)
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding(.horizontal, 12)
      .padding(.vertical, 8)
      .accessibilityElement(children: .combine)
      .accessibilityLabel(Text(phase.statusTitle))
  }
}
