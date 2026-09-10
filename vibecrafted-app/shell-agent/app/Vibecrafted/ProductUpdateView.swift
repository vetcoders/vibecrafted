import SwiftUI

/// Progress card for Check for Updates. Reuses the Command Deck reconnect
/// chrome so update work is the same visual language as recovery, not a
/// second product skin. Terminal phases never rely on a spinner alone.
struct ProductUpdateView: View {
  let progress: ProductUpdateProgress
  var onRetry: (() -> Void)?
  var onInstall: (() -> Void)?
  var onClose: (() -> Void)?

  @Environment(\.commandDeckTheme) private var theme
  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor
  @Environment(\.locale) private var locale

  var body: some View {
    VStack(alignment: .leading, spacing: 16) {
      Label {
        Text(progress.title)
          .font(.title2)
          .foregroundStyle(theme.palette.ink)
      } icon: {
        Image(systemName: symbolName)
          .foregroundStyle(symbolColor)
          .accessibilityHidden(true)
      }
      .labelStyle(.titleAndIcon)
      .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
      .accessibilityAddTraits(.isHeader)

      VStack(alignment: .leading, spacing: 8) {
        Text(progress.summary)
          .font(.body)
          .foregroundStyle(theme.palette.muted)
          .fixedSize(horizontal: false, vertical: true)
        Text("Installed: \(progress.installedGeneration)")
          .font(.body.monospaced())
          .foregroundStyle(theme.palette.ink)
          .textSelection(.enabled)
          .accessibilityLabel("Installed version")
          .accessibilityValue(progress.installedGeneration)
        Text("Available: \(progress.candidateGeneration ?? "none")")
          .font(.body.monospaced())
          .foregroundStyle(theme.palette.ink)
          .textSelection(.enabled)
          .accessibilityLabel("Available version")
          .accessibilityValue(progress.candidateGeneration ?? "none")
      }

      if showsBusyIndicator {
        ProgressView()
          .controlSize(.small)
          .accessibilityLabel("Update in progress")
          .accessibilityValue(progress.title)
      }

      HStack(spacing: CommandDeckMetrics.chromeSpacing) {
        if progress.canInstall, let onInstall {
          Button("Install Update", systemImage: "arrow.down.app", action: onInstall)
            .buttonStyle(.commandDeckAccent)
            .accessibilityHint("Installs the signed update. Frame, terminals, agents and sessions stay running.")
            .accessibilityInputLabels(["Install Update", "Install", "Zainstaluj aktualizację"])
        }
        if progress.canRetry, let onRetry {
          if progress.canInstall {
            Button("Check Again", systemImage: "arrow.clockwise", action: onRetry)
              .buttonStyle(.commandDeckQuiet)
              .accessibilityHint("Runs Check for Updates again.")
              .accessibilityInputLabels(["Check Again", "Check for Updates", "Sprawdź aktualizacje"])
          } else {
            Button("Check Again", systemImage: "arrow.clockwise", action: onRetry)
              .buttonStyle(.commandDeckAccent)
              .accessibilityHint("Runs Check for Updates again.")
              .accessibilityInputLabels(["Check Again", "Check for Updates", "Sprawdź aktualizacje"])
          }
        }
        if let onClose {
          Button("Close", action: onClose)
            .buttonStyle(.commandDeckQuiet)
        }
      }
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
    .commandDeckThemed()
    .environment(\.locale, locale)
    .accessibilityElement(children: .contain)
    .accessibilityLabel("Check for Updates")
  }

  private var showsBusyIndicator: Bool {
    switch progress.phase {
    case .checking, .downloading, .verifying, .installing, .restarting: true
    case .idle, .ready, .success, .unavailable, .refused, .retained, .error: false
    }
  }

  private var symbolName: String {
    switch progress.phase {
    case .idle, .checking, .downloading: "arrow.triangle.2.circlepath"
    case .verifying, .installing, .restarting: "checkmark.seal"
    case .ready: "arrow.down.app"
    case .success: "checkmark.circle.fill"
    case .unavailable, .error: "exclamationmark.triangle.fill"
    case .refused, .retained: "exclamationmark.octagon.fill"
    }
  }

  private var symbolColor: Color {
    switch progress.phase {
    case .success, .ready: theme.palette.ink
    case .refused, .retained: theme.palette.destructive
    case .unavailable, .error, .checking, .downloading, .verifying, .installing, .restarting, .idle:
      theme.palette.amber
    }
  }
}
