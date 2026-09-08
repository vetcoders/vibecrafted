// Vibecrafted — Command Deck shell
// Created by Vetcoders

import SwiftUI

/// Presentation projection of the session. AppModel projects combined connection truth here.
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

/// Runtime verbs. Owned by the App (console) or the tool tab model; the
/// toolbar and the recovery card only emit them.
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

/// History verbs. Always owned by the window that shows the tab, so a command
/// can only ever move the selected tab and never a sibling.
enum CommandDeckNavigationAction: String, Hashable, Sendable {
  case home
  case back
  case forward
  case openInBrowser
}

@MainActor
protocol CommandDeckNavigationHandling: AnyObject {
  func navigate(_ action: CommandDeckNavigationAction)
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

  /// The recovery card carries one primary verb for its phase plus the
  /// destructive stop, which the toolbar never shows. Everything else lives
  /// in the toolbar only, so there is exactly one action set per window.
  var recoveryCardActions: Set<CommandDeckChromeAction> {
    let primary: CommandDeckChromeAction = phase == .blocked ? .repairRuntime : .retryConnection
    return availableActions.intersection([primary, .requestStopRuntime])
  }
}

/// Native chrome around an injected product canvas: the window toolbar holds
/// navigation, status and runtime actions; the content area holds only the
/// canvas and its phase overlay. The App supplies the persistent canvas and
/// the native action handlers.
struct CommandDeckView<Canvas: View>: View {
  let presentation: CommandDeckPresentation
  let canvas: Canvas
  var actions: (any CommandDeckActionHandling)?
  var navigation: WebTabNavigation?
  var navigationHandler: (any CommandDeckNavigationHandling)?

  @Environment(\.accessibilityReduceMotion) private var reduceMotion

  init(
    presentation: CommandDeckPresentation,
    actions: (any CommandDeckActionHandling)? = nil,
    navigation: WebTabNavigation? = nil,
    navigationHandler: (any CommandDeckNavigationHandling)? = nil,
    @ViewBuilder canvas: () -> Canvas
  ) {
    self.presentation = presentation
    self.actions = actions
    self.navigation = navigation
    self.navigationHandler = navigationHandler
    self.canvas = canvas()
  }

  var body: some View {
    CommandDeckCanvasStage(
      phase: presentation.phase,
      problem: presentation.problem,
      availableActions: presentation.recoveryCardActions,
      actions: actions,
      canvas: canvas
    )
    .commandDeckThemed()
    .animation(
      reduceMotion ? nil : .easeInOut(duration: CommandDeckMetrics.motionDuration),
      value: presentation.phase
    )
    .toolbar {
      CommandDeckToolbar(
        presentation: presentation,
        actions: actions,
        navigation: navigation,
        navigationHandler: navigationHandler
      )
    }
    .accessibilityElement(children: .contain)
    .accessibilityLabel("Command Deck")
  }
}

private struct CommandDeckCanvasStage<Canvas: View>: View {
  let phase: CommandDeckPhase
  let problem: CommandDeckProblem?
  let availableActions: Set<CommandDeckChromeAction>
  var actions: (any CommandDeckActionHandling)?
  let canvas: Canvas

  @Environment(\.commandDeckTheme) private var theme

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
    .background(theme.palette.surface)
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

/// The one chrome. Bridged into the window's unified toolbar, so the selected
/// tab shows exactly one status and one action set, at every width: AppKit
/// moves items that do not fit into the toolbar's overflow menu.
struct CommandDeckToolbar: ToolbarContent {
  let presentation: CommandDeckPresentation
  var actions: (any CommandDeckActionHandling)?
  var navigation: WebTabNavigation?
  var navigationHandler: (any CommandDeckNavigationHandling)?

  var body: some ToolbarContent {
    ToolbarItemGroup(placement: .navigation) {
      if let navigationHandler {
        Button("Back", systemImage: "chevron.left") { navigationHandler.navigate(.back) }
          .keyboardShortcut("[", modifiers: .command)
          .disabled(!(navigation?.canGoBack ?? false))
          .help("Show the previous page in this tab.")
        Button("Forward", systemImage: "chevron.right") { navigationHandler.navigate(.forward) }
          .keyboardShortcut("]", modifiers: .command)
          .disabled(!(navigation?.canGoForward ?? false))
          .help("Show the next page in this tab.")
        Button("Home", systemImage: "house") { navigationHandler.navigate(.home) }
          .keyboardShortcut("h", modifiers: [.command, .shift])
          .help("Return this tab to its product overview. Works while a page shows an error or raw data.")
          .accessibilityHint("Returns to the overview of the connected runtime without restarting it.")
      }
    }
    ToolbarItem(placement: .principal) {
      CommandDeckStatusBadge(
        phase: presentation.phase,
        endpointCaption: presentation.endpointCaption,
        isLoading: navigation?.isLoading ?? false
      )
    }
    ToolbarItemGroup(placement: .primaryAction) {
      if shows(.retryConnection) {
        Button("Retry Connection", systemImage: "arrow.clockwise") { actions?.handle(.retryConnection) }
          .keyboardShortcut("r", modifiers: .command)
          .help("Retry Connection (⌘R). Tries the current runtime endpoint again.")
      }
      if shows(.repairRuntime) {
        Button("Repair Runtime", systemImage: "wrench.and.screwdriver") { actions?.handle(.repairRuntime) }
          .keyboardShortcut("r", modifiers: [.command, .shift])
          .help("Repair Runtime (⇧⌘R). Repairs or reinstalls through the installed runtime owner.")
      }
      if shows(.openTerminal) {
        Button("Open Terminal", systemImage: "terminal") { actions?.handle(.openTerminal) }
          .keyboardShortcut("t", modifiers: [.command, .option])
          .help("Open Terminal (⌥⌘T). Opens the generation-owned terminal; closing it does not stop the runtime.")
      }
      if shows(.showDiagnostics) {
        Button("Diagnostics", systemImage: "stethoscope") { actions?.handle(.showDiagnostics) }
          .help("Diagnostics. Shows runtime diagnostics for the shared server.")
      }
      if let navigationHandler, navigation?.currentURL.map({ $0.scheme == "http" || $0.scheme == "https" }) == true {
        Button("Open in Browser", systemImage: "safari") { navigationHandler.navigate(.openInBrowser) }
          .help("Open in Browser. Opens this tab's current page in the system browser.")
      }
    }
  }

  private func shows(_ action: CommandDeckChromeAction) -> Bool {
    actions != nil && presentation.availableActions.contains(action)
  }
}

/// Runtime identity in the toolbar: phase, host, and whether the tab is
/// mid-navigation. Text-first so the state is readable without colour.
private struct CommandDeckStatusBadge: View {
  let phase: CommandDeckPhase
  let endpointCaption: String?
  let isLoading: Bool

  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor
  @ScaledMetric(relativeTo: .body) private var symbolPointSize = 12.0

  var body: some View {
    HStack(spacing: 6) {
      Image(systemName: phase.statusSymbol)
        .font(.system(size: symbolPointSize, weight: .medium))
        .foregroundStyle(statusTint)
        .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
        .accessibilityHidden(true)
      Text(phase.statusTitle)
        .font(.callout.weight(.medium))
        .foregroundStyle(.primary)
      if let endpointCaption, !endpointCaption.isEmpty {
        Text(endpointCaption)
          .font(.callout.monospaced())
          .foregroundStyle(.secondary)
          .lineLimit(1)
          .truncationMode(.middle)
          .frame(maxWidth: 220)
      }
      if isLoading {
        ProgressView()
          .controlSize(.mini)
          .accessibilityLabel("Loading")
      }
    }
    .padding(.horizontal, 10)
    .padding(.vertical, 4)
    .background(.quaternary.opacity(0.35), in: Capsule())
    .accessibilityElement(children: .combine)
    .accessibilityLabel(Text(phase.statusTitle))
    .accessibilityValue(endpointCaption ?? "")
  }

  private var statusTint: Color {
    switch phase {
    case .bootstrapping, .connecting, .recovering: theme.palette.amber
    case .online: .primary
    case .blocked: theme.palette.destructive
    }
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
