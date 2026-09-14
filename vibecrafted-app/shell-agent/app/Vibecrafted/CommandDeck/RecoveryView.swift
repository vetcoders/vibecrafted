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

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      RecoveryIdentity(phase: phase, differentiateWithoutColor: differentiateWithoutColor)
      RecoveryCopy(phase: phase, problem: problem, locale: locale)
      RecoveryActionRow(
        availableActions: availableActions,
        actions: actions,
        focusedControl: $focusedControl
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
    .defaultFocus($focusedControl, phase == .blocked ? .repairRuntime : .retryConnection)
    .accessibilityElement(children: .contain)
    .accessibilityLabel(phase == .blocked ? "Runtime blocked" : "Runtime recovery")
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
      : "Retry against the current endpoint. Repair Runtime, Open Terminal and Diagnostics stay in the toolbar above. Workspaces return to this canvas once it is connected."
  }
}

private struct RecoveryActionRow: View {
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?
  var focusedControl: FocusState<CommandDeckChromeAction?>.Binding

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
      .focused(focusedControl, equals: .retryConnection)
      .accessibilityHint("Tries the current runtime endpoint again.")
      .accessibilityInputLabels(["Retry", "Retry Connection"])
    }
    if shows(.repairRuntime) {
      Button("Repair Runtime", systemImage: "wrench.and.screwdriver") {
        actions?.handle(.repairRuntime)
      }
      .buttonStyle(.commandDeckAccent)
      .focused(focusedControl, equals: .repairRuntime)
      .accessibilityHint("Repairs or reinstalls through the installed runtime owner.")
      .accessibilityInputLabels(["Repair", "Repair Runtime"])
    }
    if shows(.openTerminal) {
      Button("Open Terminal", systemImage: "terminal") {
        actions?.handle(.openTerminal)
      }
      .buttonStyle(.commandDeckQuiet)
      .focused(focusedControl, equals: .openTerminal)
      .accessibilityHint("Opens the generation-owned terminal. Closing it does not stop the runtime.")
    }
    if shows(.showDiagnostics) {
      Button("Diagnostics", systemImage: "info.circle") { actions?.handle(.showDiagnostics) }
        .buttonStyle(.commandDeckQuiet)
        .focused(focusedControl, equals: .showDiagnostics)
    }
    if shows(.requestStopRuntime) {
      Button("Stop Runtime", systemImage: "stop.circle") {
        actions?.handle(.requestStopRuntime)
      }
      .buttonStyle(.commandDeckDestructive)
      .focused(focusedControl, equals: .requestStopRuntime)
      .accessibilityHint("Confirms stopping the shared Runtime service. Does not quit the App or stop agents.")
      .accessibilityInputLabels(["Stop Runtime"])
    }
  }

  private func shows(_ action: CommandDeckChromeAction) -> Bool {
    actions != nil && availableActions.contains(action)
  }
}
