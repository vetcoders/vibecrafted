// Vibecrafted — Command Deck shell
// Created by Vetcoders

import SwiftUI

/// Presentation projection of the session. W2 maps sibling session types here.
/// This type does not own endpoint resolution, WebKit, or process lifetime.
enum CommandDeckPhase: String, Equatable, Sendable {
  case bootstrapping
  case connecting
  case online
  case recovering
  case blocked

  var exposesCanvas: Bool { self == .online }

  var statusTitle: LocalizedStringResource {
    switch self {
    case .bootstrapping: "Preparing"
    case .connecting: "Connecting"
    case .online: "Connected"
    case .recovering: "Recovering"
    case .blocked: "Blocked"
    }
  }

  var statusSymbol: String {
    switch self {
    case .bootstrapping: "hourglass"
    case .connecting: "antenna.radiowaves.left.and.right"
    case .online: "link"
    case .recovering: "arrow.triangle.2.circlepath"
    case .blocked: "exclamationmark.octagon.fill"
    }
  }
}

enum CommandDeckChromeAction: String, Hashable, Sendable {
  case retryConnection
  case repairRuntime
  case openTerminal
  case requestStopRuntime
  case showDiagnostics
}

@MainActor
protocol CommandDeckActionHandling: AnyObject {
  func handle(_ action: CommandDeckChromeAction)
}

struct CommandDeckPresentation: Equatable, Sendable {
  var phase: CommandDeckPhase
  var problem: CommandDeckProblem?
  var endpointCaption: String?
  var availableActions: Set<CommandDeckChromeAction>

  var exposesCanvas: Bool { phase.exposesCanvas }

  init(
    phase: CommandDeckPhase,
    problem: CommandDeckProblem? = nil,
    endpointCaption: String? = nil,
    availableActions: Set<CommandDeckChromeAction> = []
  ) {
    self.phase = phase
    self.problem = problem
    self.endpointCaption = endpointCaption
    self.availableActions = availableActions
  }
}

/// Compact native chrome around an injected product canvas.
/// W2 supplies the canvas and action handler; this view does not stub sibling types.
struct CommandDeckView<Canvas: View>: View {
  let presentation: CommandDeckPresentation
  let canvas: Canvas
  var actions: (any CommandDeckActionHandling)?

  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  init(
    presentation: CommandDeckPresentation,
    actions: (any CommandDeckActionHandling)? = nil,
    @ViewBuilder canvas: () -> Canvas
  ) {
    self.presentation = presentation
    self.actions = actions
    self.canvas = canvas()
  }

  var body: some View {
    CommandDeckScaffold(
      presentation: presentation,
      actions: actions,
      canvas: canvas
    )
    .commandDeckThemed()
    .animation(
      reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration),
      value: presentation.phase
    )
    .accessibilityElement(children: .contain)
    .accessibilityLabel("Command Deck")
  }
}

private struct CommandDeckScaffold<Canvas: View>: View {
  let presentation: CommandDeckPresentation
  var actions: (any CommandDeckActionHandling)?
  let canvas: Canvas

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    VStack(spacing: 0) {
      CommandDeckChromeBar(
        phase: presentation.phase,
        endpointCaption: presentation.endpointCaption,
        availableActions: chromeActions,
        actions: actions
      )
      CommandDeckCanvasStage(
        phase: presentation.phase,
        problem: presentation.problem,
        availableActions: presentation.availableActions,
        actions: actions,
        canvas: canvas
      )
    }
    .background(theme.palette.surface)
  }

  private var chromeActions: Set<CommandDeckChromeAction> {
    switch presentation.phase {
    case .recovering, .blocked:
      []
    case .bootstrapping, .connecting, .online:
      presentation.availableActions.subtracting([.requestStopRuntime])
    }
  }
}

private struct CommandDeckCanvasStage<Canvas: View>: View {
  let phase: CommandDeckPhase
  let problem: CommandDeckProblem?
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?
  let canvas: Canvas

  var body: some View {
    ZStack {
      canvas
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .opacity(phase.exposesCanvas ? 1 : 0)
        .allowsHitTesting(phase.exposesCanvas)
        .accessibilityHidden(!phase.exposesCanvas)
      if !phase.exposesCanvas {
        overlay
          .transition(.opacity)
      }
    }
    .frame(maxWidth: .infinity, maxHeight: .infinity)
  }

  @ViewBuilder
  private var overlay: some View {
    switch phase {
    case .bootstrapping, .connecting:
      CommandDeckProgressCover(phase: phase)
    case .recovering, .blocked:
      RecoveryView(
        phase: phase,
        problem: problem,
        availableActions: availableActions,
        actions: actions
      )
    case .online:
      EmptyView()
    }
  }
}

private struct CommandDeckChromeBar: View {
  let phase: CommandDeckPhase
  let endpointCaption: String?
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?

  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor
  @ScaledMetric(relativeTo: .body) private var symbolPointSize = 13.0

  var body: some View {
    HStack(spacing: CommandDeckMetrics.chromeSpacing) {
      Text("Vibecrafted")
        .font(.headline)
        .foregroundStyle(theme.palette.ink)
        .accessibilityAddTraits(.isHeader)
      CommandDeckStatusBadge(
        phase: phase,
        differentiateWithoutColor: differentiateWithoutColor,
        symbolPointSize: symbolPointSize
      )
      if let endpointCaption, !endpointCaption.isEmpty {
        Text(endpointCaption)
          .font(.body.monospaced())
          .foregroundStyle(theme.palette.muted)
          .lineLimit(1)
          .truncationMode(.middle)
          .textSelection(.enabled)
          .accessibilityLabel("Endpoint")
          .accessibilityValue(endpointCaption)
      }
      Spacer(minLength: 8)
      CommandDeckChromeActionsView(
        availableActions: availableActions,
        actions: actions
      )
    }
    .padding(.horizontal, CommandDeckMetrics.chromePadding)
    .padding(.vertical, 8)
    .background(theme.palette.surfaceRaised)
    .overlay(alignment: .bottom) {
      Rectangle()
        .fill(theme.palette.stroke)
        .frame(height: theme.strokeWidth)
        .accessibilityHidden(true)
    }
    .accessibilityElement(children: .contain)
    .accessibilityLabel("Command Deck chrome")
  }
}

private struct CommandDeckStatusBadge: View {
  let phase: CommandDeckPhase
  let differentiateWithoutColor: Bool
  let symbolPointSize: CGFloat

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    Label {
      Text(phase.statusTitle)
        .font(.body)
        .foregroundStyle(theme.palette.ink)
    } icon: {
      Image(systemName: phase.statusSymbol)
        .font(.system(size: symbolPointSize))
        .foregroundStyle(statusTint)
        .accessibilityHidden(true)
    }
    .labelStyle(.titleAndIcon)
    .padding(.horizontal, 8)
    .padding(.vertical, 4)
    .background(theme.palette.surface, in: Capsule())
    .overlay {
      Capsule()
        .strokeBorder(theme.palette.stroke, lineWidth: theme.strokeWidth)
    }
    .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
    .accessibilityElement(children: .combine)
    .accessibilityLabel(Text(phase.statusTitle))
  }

  private var statusTint: Color {
    switch phase {
    case .bootstrapping, .connecting, .recovering: theme.palette.amber
    case .online: theme.palette.ink
    case .blocked: theme.palette.destructive
    }
  }
}

private struct CommandDeckChromeActionsView: View {
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?

  var body: some View {
    ViewThatFits(in: .horizontal) {
      HStack(spacing: 8) {
        chromeButtons(.titleAndIcon)
      }
      HStack(spacing: 8) {
        chromeButtons(.iconOnly)
      }
    }
  }

  @ViewBuilder
  private func chromeButtons<Style: LabelStyle>(_ style: Style) -> some View {
    if shows(.retryConnection) {
      Button("Retry Connection", systemImage: "arrow.clockwise") {
        actions?.handle(.retryConnection)
      }
      .buttonStyle(.commandDeckQuiet)
      .labelStyle(style)
      .keyboardShortcut("r", modifiers: .command)
      .accessibilityHint("Tries the current runtime endpoint again.")
    }
    if shows(.repairRuntime) {
      Button("Repair Runtime", systemImage: "wrench.and.screwdriver") {
        actions?.handle(.repairRuntime)
      }
      .buttonStyle(.commandDeckQuiet)
      .labelStyle(style)
      .keyboardShortcut("r", modifiers: [.command, .shift])
      .accessibilityHint("Repairs or reinstalls through the installed runtime owner.")
    }
    if shows(.openTerminal) {
      Button("Open Terminal", systemImage: "terminal") {
        actions?.handle(.openTerminal)
      }
      .buttonStyle(.commandDeckQuiet)
      .labelStyle(style)
      .keyboardShortcut("t", modifiers: [.command, .option])
      .accessibilityHint("Opens the generation-owned terminal. Closing it does not stop the runtime.")
    }
    if shows(.showDiagnostics) {
      Button("Diagnostics", systemImage: "stethoscope") {
        actions?.handle(.showDiagnostics)
      }
      .buttonStyle(.commandDeckQuiet)
      .labelStyle(style)
      .accessibilityHint("Shows runtime diagnostics.")
    }
  }

  private func shows(_ action: CommandDeckChromeAction) -> Bool {
    actions != nil && availableActions.contains(action)
  }
}

private struct CommandDeckProgressCover: View {
  let phase: CommandDeckPhase

  @Environment(\.commandDeckTheme) private var theme

  var body: some View {
    VStack(spacing: 12) {
      ProgressView()
        .controlSize(.regular)
        .tint(theme.palette.amber)
        .accessibilityLabel(phase == .bootstrapping ? "Preparing the command deck" : "Connecting to the runtime")
      Text(phase.statusTitle)
        .font(.headline)
        .foregroundStyle(theme.palette.ink)
      Text(phase == .bootstrapping
        ? "Resolving the installed runtime. Closing this window does not stop it."
        : "Waiting for the product canvas. Workspaces open here once connected.")
        .font(.body)
        .foregroundStyle(theme.palette.muted)
        .multilineTextAlignment(.center)
        .fixedSize(horizontal: false, vertical: true)
    }
    .padding(CommandDeckMetrics.overlayPadding)
    .frame(maxWidth: CommandDeckMetrics.overlayMaxWidth)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(theme.palette.surface)
    .accessibilityElement(children: .combine)
  }
}

#Preview("Connecting") {
  CommandDeckView(
    presentation: CommandDeckPresentation(phase: .connecting)
  ) {
    Color.clear
  }
  .frame(width: 760, height: 480)
}

#Preview("Recovering") {
  CommandDeckView(
    presentation: CommandDeckPresentation(
      phase: .recovering,
      problem: CommandDeckProblem(
        title: "Server unavailable",
        summary: "The caretaker did not expose a reachable endpoint.",
        receipt: "generation unknown"
      ),
      availableActions: [.retryConnection, .repairRuntime, .openTerminal]
    )
  ) {
    Color.clear
  }
  .frame(width: 760, height: 480)
}

#Preview("Online") {
  CommandDeckView(
    presentation: CommandDeckPresentation(
      phase: .online,
      endpointCaption: "runtime endpoint",
      availableActions: [.openTerminal]
    )
  ) {
    Rectangle().fill(.secondary.opacity(0.12))
  }
  .frame(width: 760, height: 480)
}
