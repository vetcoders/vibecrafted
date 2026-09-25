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
    .accessibilityLabel(phase == .blocked ? "Server blocked" : "Server starting")
  }
}

private struct RecoveryIdentity: View {
  let phase: CommandDeckPhase
  let differentiateWithoutColor: Bool

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    Label {
      Text(phase == .blocked ? "Blocked" : "Starting…")
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
      ? "The server is blocked"
      : "The server is not up yet"
  }

  private var fallbackSummary: LocalizedStringResource {
    phase == .blocked
      ? "Set the server up again from the installed copy. Closing this window does not stop it."
      : "The server is still starting. Try again in a moment."
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
      Button("Try Again", systemImage: "arrow.clockwise") {
        actions?.handle(.retryConnection)
      }
      .buttonStyle(.commandDeckAccent)
      .focused(focusedControl, equals: .retryConnection)
      .accessibilityHint("Connects to the server again.")
      .accessibilityInputLabels(["Try Again", "Retry"])
    }
    if shows(.repairRuntime) {
      Button("Reinitialize", systemImage: "wrench.and.screwdriver") {
        actions?.handle(.repairRuntime)
      }
      .buttonStyle(.commandDeckAccent)
      .focused(focusedControl, equals: .repairRuntime)
      .accessibilityHint("Sets the server up again from the installed copy.")
      .accessibilityInputLabels(["Reinitialize"])
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
      Button("Stop Server", systemImage: "stop.circle") {
        actions?.handle(.requestStopRuntime)
      }
      .buttonStyle(.commandDeckDestructive)
      .focused(focusedControl, equals: .requestStopRuntime)
      .accessibilityHint("Stops the server. Does not quit the app or stop agents.")
      .accessibilityInputLabels(["Stop Server"])
    }
  }

  private func shows(_ action: CommandDeckChromeAction) -> Bool {
    actions != nil && availableActions.contains(action)
  }
}
