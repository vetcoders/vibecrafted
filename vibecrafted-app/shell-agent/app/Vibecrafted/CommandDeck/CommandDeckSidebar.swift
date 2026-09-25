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
    VStack(alignment: .leading, spacing: 2) {
      footerDoor(.help, "Help & docs")
      footerDoor(.about, "About")
      HStack(spacing: 6) {
        Image(systemName: phase.statusSymbol)
        Text(phase.statusTitle)
      }
      .accessibilityElement(children: .combine)
      .accessibilityLabel(Text(phase.statusTitle))
    }
    .font(.caption)
    .foregroundStyle(.secondary)
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.horizontal, 12)
    .padding(.vertical, 8)
  }

  private func footerDoor(_ destination: CommandDeckDestination, _ title: String) -> some View {
    Button {
      selection = destination
    } label: {
      Text(title)
        .font(.caption)
        .foregroundStyle(.secondary)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
    .buttonStyle(.plain)
    .accessibilityLabel(title)
  }
}
