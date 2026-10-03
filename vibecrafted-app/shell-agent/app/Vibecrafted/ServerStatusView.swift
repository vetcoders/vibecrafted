import SwiftUI

/// The native server status surface.
///
/// What used to be a modal NSAlert with a wall of monospaced text is a system
/// utility window: grouped form, semantic health, selectable identifiers, and
/// the lifecycle actions the caretaker envelope already describes. Every value
/// arrives derived by `ServerMenuPolicy` — this view adds layout and nothing
/// else, so the window can never disagree with the tray about server truth.
struct ServerStatusView: View {
  let state: ServerMenuState
  let envelope: CaretakerEnvelope?
  let configuration: ConfigRepairEnvelope?
  let upgradeConflict: PreferenceConflictEnvelope?
  let canOpenLogs: Bool
  var onStart: (() -> Void)?
  var onStop: (() -> Void)?
  var onRestart: (() -> Void)?
  var onOpenConsole: (() -> Void)?
  var onOpenLogs: (() -> Void)?
  var onRevealPath: ((String) -> Void)?
  var onClose: (() -> Void)?

  @Environment(\.accessibilityDifferentiateWithoutColor) private var differentiateWithoutColor

  var body: some View {
    VStack(spacing: 0) {
      Form {
        healthSection
        statusSection
        processesSection
        findingsSection
        receiptSection
        configurationSection
        upgradeConflictSection
      }
      .formStyle(.grouped)
      Divider()
      footer
    }
    .frame(width: 480)
  }

  // MARK: - Health

  /// The verdict as a native grouped-form row: colored status symbol, header,
  /// detail — the same words and tone the tray shows, in System Settings
  /// clothing instead of a custom banner.
  @ViewBuilder private var healthSection: some View {
    Section {
      Label {
        VStack(alignment: .leading, spacing: 2) {
          Text(state.header)
            .font(.headline)
          if !state.detail.isEmpty {
            Text(state.detail)
              .font(.subheadline)
              .foregroundStyle(.secondary)
              .fixedSize(horizontal: false, vertical: true)
          }
        }
      } icon: {
        Image(systemName: healthSymbol)
          .font(.title2)
          .foregroundStyle(healthColor)
          .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
          .accessibilityHidden(true)
      }
      .accessibilityElement(children: .combine)
      .accessibilityAddTraits(.isHeader)
    }
  }

  private var healthSymbol: String {
    switch state.health {
    case .healthy: return "checkmark.circle.fill"
    case .transitioning: return "arrow.triangle.2.circlepath"
    case .checking: return "questionmark.circle"
    case .failed: return "xmark.octagon.fill"
    case .neutral: return "minus.circle"
    }
  }

  private var healthColor: Color {
    switch state.health {
    case .healthy: return .green
    case .transitioning: return .orange
    case .checking, .neutral: return .secondary
    case .failed: return .red
    }
  }

  // MARK: - Sections

  @ViewBuilder private var statusSection: some View {
    Section("Status") {
      LabeledContent("State", value: envelope?.server?.state?.uppercased() ?? "UNKNOWN")
      if let checkedAt = Self.parseCaretakerTimestamp(envelope?.generatedAt) {
        LabeledContent("Checked") {
          Text(checkedAt, format: .dateTime)
        }
      }
      if let endpoint = endpointLabel {
        LabeledContent("Endpoint") {
          Text(endpoint).textSelection(.enabled)
        }
      }
      if let version = envelope?.server?.liveness?.version, !version.isEmpty {
        LabeledContent("Server Version", value: version)
      }
      if let lastError = conciseCaretakerLine(envelope?.server?.lastError) {
        LabeledContent("Last Error") {
          Text(lastError)
            .foregroundStyle(.red)
            .fixedSize(horizontal: false, vertical: true)
            .textSelection(.enabled)
        }
      }
      if envelope == nil {
        Text("The caretaker has not published a reading for the installed runtime.")
          .foregroundStyle(.secondary)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
  }

  private var endpointLabel: String? {
    guard let endpoint = envelope?.server?.endpoint else { return nil }
    if let url = endpoint.url, !url.isEmpty { return url }
    guard let host = endpoint.host, let port = endpoint.port else { return nil }
    return "\(host):\(port)"
  }

  @ViewBuilder private var processesSection: some View {
    let server = envelope?.server
    let supervisor = server?.supervisorPID
    let serverPID = server?.managedPair?.serverPID
    let guardian = server?.managedPair?.guardianPID
    if supervisor != nil || serverPID != nil || guardian != nil {
      Section("Processes") {
        if let supervisor {
          pidRow("Supervisor PID", value: supervisor)
        }
        if let serverPID {
          pidRow("Server PID", value: serverPID)
        }
        if let guardian {
          pidRow("Guardian PID", value: guardian)
        }
      }
    }
  }

  private func pidRow(_ label: String, value: Int) -> some View {
    LabeledContent(label) {
      Text(value, format: .number.grouping(.never))
        .monospacedDigit()
        .textSelection(.enabled)
    }
  }

  @ViewBuilder private var findingsSection: some View {
    if let findings = envelope?.verdict?.findings, !findings.isEmpty {
      Section("Findings") {
        ForEach(Array(findings.enumerated()), id: \.offset) { _, finding in
          Label {
            VStack(alignment: .leading, spacing: 2) {
              Text(finding.detail)
                .fixedSize(horizontal: false, vertical: true)
              Text(finding.code)
                .font(.caption)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
            }
          } icon: {
            Image(systemName: findingSymbol(finding.severity))
              .foregroundStyle(findingColor(finding.severity))
              .symbolRenderingMode(differentiateWithoutColor ? .monochrome : .hierarchical)
              .accessibilityHidden(true)
          }
        }
      }
    }
  }

  private func findingSymbol(_ severity: String) -> String {
    switch severity {
    case "warn", "warning": return "exclamationmark.triangle.fill"
    case "error", "critical": return "xmark.octagon.fill"
    default: return "info.circle"
    }
  }

  private func findingColor(_ severity: String) -> Color {
    switch severity {
    case "warn", "warning": return .orange
    case "error", "critical": return .red
    default: return .secondary
    }
  }

  @ViewBuilder private var receiptSection: some View {
    if let receipt = envelope?.server?.receipt, let path = receipt.path, !path.isEmpty {
      Section("Status Receipt") {
        HStack(spacing: 8) {
          Text(path)
            .font(.system(.caption, design: .monospaced))
            .lineLimit(1)
            .truncationMode(.middle)
            .textSelection(.enabled)
            .help(path)
          if receipt.present == false {
            Text("missing")
              .font(.caption)
              .foregroundStyle(.red)
          } else if receipt.stale == true {
            Text("stale")
              .font(.caption)
              .foregroundStyle(.orange)
          }
          Spacer(minLength: 0)
          if let onRevealPath {
            Button {
              onRevealPath(path)
            } label: {
              Image(systemName: "folder")
            }
            .buttonStyle(.borderless)
            .controlSize(.small)
            .help("Show in Finder")
            .accessibilityLabel("Show status receipt in Finder")
            .accessibilityInputLabels(["Show in Finder", "Reveal", "Pokaż w Finderze"])
          }
        }
      }
    }
  }

  @ViewBuilder private var configurationSection: some View {
    if let configuration {
      Section("Configuration") {
        Text(configRepairSummary(configuration))
          .font(.callout)
          .fixedSize(horizontal: false, vertical: true)
          .textSelection(.enabled)
      }
    }
  }

  @ViewBuilder private var upgradeConflictSection: some View {
    if let upgradeConflict {
      Section("Upgrade Conflict") {
        Text(preferenceConflictDiagnostics(upgradeConflict))
          .font(.system(.caption, design: .monospaced))
          .fixedSize(horizontal: false, vertical: true)
          .textSelection(.enabled)
      }
    }
  }

  // MARK: - Footer

  private var footer: some View {
    HStack(spacing: 10) {
      if state.canStop, let onStop {
        Button("Stop…", role: .destructive, action: onStop)
          .accessibilityHint("Stops the shared VC Server after confirmation.")
          .accessibilityInputLabels(["Stop", "Stop Runtime Service", "Zatrzymaj"])
      }
      if state.canStart, let onStart {
        Button("Start", action: onStart)
          .accessibilityInputLabels(["Start", "Start Runtime Service", "Uruchom"])
      }
      if state.canRestart, let onRestart {
        Button("Restart", action: onRestart)
          .accessibilityInputLabels(["Restart", "Restart Runtime Service", "Uruchom ponownie"])
      }
      Spacer()
      if canOpenLogs, let onOpenLogs {
        Button("Open Logs", action: onOpenLogs)
          .accessibilityInputLabels(["Open Logs", "Logs", "Otwórz logi"])
      }
      if let onClose {
        Button("Close", action: onClose)
          .keyboardShortcut(.cancelAction)
          .accessibilityInputLabels(["Close", "Zamknij"])
      }
      if let onOpenConsole {
        Button("Open Console", action: onOpenConsole)
          .buttonStyle(.borderedProminent)
          .keyboardShortcut(.defaultAction)
          .accessibilityInputLabels(["Open Console", "Console", "Otwórz konsolę"])
      }
    }
    .padding(.horizontal, 16)
    .padding(.vertical, 10)
  }

  // MARK: - Timestamp

  /// The caretaker stamps its reading with an ISO-8601 string; the window shows
  /// it in the operator's locale rather than raw transport form.
  static func parseCaretakerTimestamp(_ raw: String?) -> Date? {
    guard let raw, !raw.isEmpty else { return nil }
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: raw) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: raw)
  }
}
