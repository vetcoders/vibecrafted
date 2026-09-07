// Vibecrafted — Command Deck recovery
// Created by Vetcoders

import SwiftUI

struct CommandDeckProblem: Equatable, Sendable {
  var title: LocalizedStringResource
  var summary: LocalizedStringResource
  var receipt: String?
}

struct RecoveryView: View {
  let phase: CommandDeckPhase
  let problem: CommandDeckProblem?
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?

  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor
  @Environment(\.locale) private var locale
  @FocusState private var focusedControl: CommandDeckChromeAction?
  @State private var isConfirmingStop = false

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      RecoveryIdentity(phase: phase, differentiateWithoutColor: differentiateWithoutColor)
      RecoveryCopy(phase: phase, problem: problem, locale: locale)
      RecoveryActionRow(
        availableActions: availableActions,
        actions: actions,
        focusedControl: $focusedControl,
        isConfirmingStop: $isConfirmingStop
      )
    }
    .padding(CommandDeckMetrics.overlayPadding)
    .frame(maxWidth: CommandDeckMetrics.overlayMaxWidth, alignment: .leading)
    .background(theme.palette.surfaceRaised, in: RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius))
    .overlay {
      RoundedRectangle(cornerRadius: CommandDeckMetrics.cornerRadius)
        .strokeBorder(theme.palette.stroke, lineWidth: theme.strokeWidth)
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(theme.palette.surface)
    .focusSection()
    .defaultFocus($focusedControl, .retryConnection)
    .accessibilityElement(children: .contain)
    .accessibilityLabel(phase == .blocked ? "Runtime blocked" : "Runtime recovery")
    .confirmationDialog(
      "Stop Runtime?",
      isPresented: $isConfirmingStop,
      titleVisibility: .visible
    ) {
      Button("Stop Runtime", role: .destructive, action: confirmStopRuntime)
      Button("Cancel", role: .cancel) {}
    } message: {
      Text("This stops the runtime. It does not quit the App. Open sessions may end.")
    }
  }

  private func confirmStopRuntime() {
    actions?.handle(.requestStopRuntime)
  }
}

private struct RecoveryIdentity: View {
  let phase: CommandDeckPhase
  let differentiateWithoutColor: Bool

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    Label {
      Text(phase == .blocked ? "Blocked" : "Recovering")
        .font(.title2)
        .foregroundStyle(theme.palette.ink)
    } icon: {
      Image(systemName: phase == .blocked ? "exclamationmark.octagon.fill" : "arrow.triangle.2.circlepath")
        .foregroundStyle(phase == .blocked ? theme.palette.destructive : theme.palette.amber)
        .accessibilityHidden(true)
    }
    .labelStyle(.titleAndIcon)
    .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
    .accessibilityAddTraits(.isHeader)
  }
}

private struct RecoveryCopy: View {
  let phase: CommandDeckPhase
  let problem: CommandDeckProblem?
  let locale: Locale

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      Text(problem?.title ?? fallbackTitle)
        .font(.headline)
        .foregroundStyle(theme.palette.ink)
      Text(problem?.summary ?? fallbackSummary)
        .font(.body)
        .foregroundStyle(theme.palette.muted)
        .fixedSize(horizontal: false, vertical: true)
      if let receipt = problem?.receipt, !receipt.isEmpty {
        Text(receipt)
          .font(.body.monospaced())
          .foregroundStyle(theme.palette.ink)
          .textSelection(.enabled)
          .accessibilityLabel("Receipt")
          .accessibilityValue(receipt)
      }
    }
    .environment(\.locale, locale)
  }

  private var fallbackTitle: LocalizedStringResource {
    phase == .blocked
      ? "The runtime is blocked"
      : "The product canvas cannot connect"
  }

  private var fallbackSummary: LocalizedStringResource {
    phase == .blocked
      ? "Repair through the installed runtime owner. Closing this window does not stop the runtime."
      : "Retry against the current endpoint, or repair the installed runtime. Workspaces stay in the web canvas once it is connected."
  }
}

private struct RecoveryActionRow: View {
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?
  var focusedControl: FocusState<CommandDeckChromeAction?>.Binding
  @Binding var isConfirmingStop: Bool

  var body: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: CommandDeckMetrics.chromeSpacing) {
        actionButtons()
      }
      VStack(alignment: .leading, spacing: CommandDeckMetrics.chromeSpacing) {
        actionButtons()
      }
    }
  }

  @ViewBuilder
  private func actionButtons() -> some View {
    if shows(.retryConnection) {
      Button("Retry Connection", systemImage: "arrow.clockwise") {
        actions?.handle(.retryConnection)
      }
      .buttonStyle(.commandDeckAccent)
      .keyboardShortcut("r", modifiers: .command)
      .focused(focusedControl, equals: .retryConnection)
      .accessibilityHint("Tries the current runtime endpoint again.")
      .accessibilityInputLabels(["Retry", "Retry Connection"])
    }
    if shows(.repairRuntime) {
      Button("Repair Runtime", systemImage: "wrench.and.screwdriver") {
        actions?.handle(.repairRuntime)
      }
      .buttonStyle(.commandDeckQuiet)
      .keyboardShortcut("r", modifiers: [.command, .shift])
      .focused(focusedControl, equals: .repairRuntime)
      .accessibilityHint("Repairs or reinstalls through the installed runtime owner.")
      .accessibilityInputLabels(["Repair", "Repair Runtime"])
    }
    if shows(.openTerminal) {
      Button("Open Terminal", systemImage: "terminal") {
        actions?.handle(.openTerminal)
      }
      .buttonStyle(.commandDeckQuiet)
      .keyboardShortcut("t", modifiers: [.command, .option])
      .focused(focusedControl, equals: .openTerminal)
      .accessibilityHint("Opens the generation-owned terminal. Closing it does not stop the runtime.")
    }
    if shows(.requestStopRuntime) {
      Button("Stop Runtime", systemImage: "stop.circle") {
        isConfirmingStop = true
      }
      .buttonStyle(.commandDeckDestructive)
      .focused(focusedControl, equals: .requestStopRuntime)
      .accessibilityHint("Stops the runtime. Does not quit the App.")
      .accessibilityInputLabels(["Stop Runtime"])
    }
  }

  private func shows(_ action: CommandDeckChromeAction) -> Bool {
    actions != nil && availableActions.contains(action)
  }
}
