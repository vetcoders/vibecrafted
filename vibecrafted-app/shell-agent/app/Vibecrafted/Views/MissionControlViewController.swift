// Vibecrafted - Mission Control Snapshot
// Created by Vetcoders

import AppKit

final class MissionControlViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
  private enum Section {
    case agents
    case waves
    case skills
    case active
    case failures
    case health

    var focusID: String {
      switch self {
      case .active: return "active_dispatches"
      case .waves: return "waves"
      case .agents: return "agents"
      case .skills: return "skills"
      case .failures: return "failures"
      case .health: return "health"
      }
    }
  }

  private static let iso8601DateFormatter = ISO8601DateFormatter()
  private static let failureDateFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd HH:mm"
    return formatter
  }()

  private let scrollView = NSScrollView()
  private let contentView = NSView()
  private let stackView = NSStackView()
  private let statusLabel = NSTextField(labelWithString: "Loading Mission Control...")
  private let emptyLabel = NSTextField(labelWithString: "Loading Mission Control snapshot...")
  private let refreshButton = NSButton(title: "Refresh", target: nil, action: nil)

  private let agentTableView = NSTableView()
  private let waveTableView = NSTableView()
  private let skillTableView = NSTableView()
  private let activeTableView = NSTableView()
  private let failuresTableView = NSTableView()
  private let settlementLabel = NSTextField(labelWithString: "settlement f=— x=— n=—")
  private let healthStackView = NSStackView()
  private let dataQualityFooterLabel = NSTextField(labelWithString: "")

  private var tableSections: [ObjectIdentifier: Section] = [:]
  private var heightConstraints: [ObjectIdentifier: NSLayoutConstraint] = [:]
  private var sectionAnchors: [String: NSView] = [:]
  private var highlightedAnchor: NSView?
  private var focusResetWorkItem: DispatchWorkItem?
  private var snapshot: FfiMissionControlSnapshot?
  private var isLoading = false
  private var pendingFocusRunId: String?

  override func loadView() {
    let root = NSView()
    root.wantsLayer = true
    view = root

    let headerStack = NSStackView()
    headerStack.orientation = .horizontal
    headerStack.alignment = .centerY
    headerStack.spacing = 12
    headerStack.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(headerStack)

    let titleLabel = NSTextField(labelWithString: "Mission Control")
    titleLabel.font = NSFont.systemFont(ofSize: 18, weight: .semibold)

    statusLabel.textColor = .secondaryLabelColor
    statusLabel.lineBreakMode = .byTruncatingMiddle
    statusLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)

    refreshButton.target = self
    refreshButton.action = #selector(refreshButtonPressed(_:))
    refreshButton.bezelStyle = .rounded

    headerStack.addArrangedSubview(titleLabel)
    headerStack.addArrangedSubview(statusLabel)
    headerStack.addArrangedSubview(NSView())
    headerStack.addArrangedSubview(refreshButton)

    scrollView.hasVerticalScroller = true
    scrollView.borderType = .noBorder
    scrollView.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(scrollView)

    contentView.translatesAutoresizingMaskIntoConstraints = false
    scrollView.documentView = contentView

    stackView.orientation = .vertical
    stackView.alignment = .leading
    stackView.spacing = 14
    stackView.translatesAutoresizingMaskIntoConstraints = false
    contentView.addSubview(stackView)

    emptyLabel.font = NSFont.systemFont(ofSize: 13, weight: .regular)
    emptyLabel.textColor = .secondaryLabelColor
    emptyLabel.alignment = .center
    emptyLabel.maximumNumberOfLines = 2
    emptyLabel.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(emptyLabel)

    configureTables()
    configureHealthStrip()

    NSLayoutConstraint.activate([
      headerStack.topAnchor.constraint(equalTo: root.topAnchor, constant: 12),
      headerStack.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 16),
      headerStack.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -16),

      scrollView.topAnchor.constraint(equalTo: headerStack.bottomAnchor, constant: 12),
      scrollView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
      scrollView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
      scrollView.bottomAnchor.constraint(equalTo: root.bottomAnchor),

      contentView.leadingAnchor.constraint(equalTo: scrollView.contentView.leadingAnchor),
      contentView.trailingAnchor.constraint(equalTo: scrollView.contentView.trailingAnchor),
      contentView.topAnchor.constraint(equalTo: scrollView.contentView.topAnchor),
      contentView.widthAnchor.constraint(equalTo: scrollView.contentView.widthAnchor),

      stackView.topAnchor.constraint(equalTo: contentView.topAnchor, constant: 4),
      stackView.leadingAnchor.constraint(equalTo: contentView.leadingAnchor, constant: 16),
      stackView.trailingAnchor.constraint(equalTo: contentView.trailingAnchor, constant: -16),
      stackView.bottomAnchor.constraint(equalTo: contentView.bottomAnchor, constant: -16),

      emptyLabel.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      emptyLabel.centerYAnchor.constraint(equalTo: root.centerYAnchor),
      emptyLabel.leadingAnchor.constraint(greaterThanOrEqualTo: root.leadingAnchor, constant: 24),
      emptyLabel.trailingAnchor.constraint(lessThanOrEqualTo: root.trailingAnchor, constant: -24),
    ])
  }

  override func viewDidLoad() {
    super.viewDidLoad()
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlFocusSection),
      name: NSNotification.Name("MissionControlFocusSection"), object: nil
    )
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlFocusRun),
      name: NSNotification.Name("MissionControlFocusRun"), object: nil
    )
    refreshSnapshot()
  }

  override func viewDidAppear() {
    super.viewDidAppear()
    refreshSnapshot()
  }

  @objc private func refreshButtonPressed(_ sender: NSButton) {
    refreshSnapshot()
  }

  @objc private func handleMissionControlFocusSection(_ notification: Notification) {
    guard let section = notification.userInfo?["section"] as? String else { return }
    focusSection(section)
  }

  @objc private func handleMissionControlFocusRun(_ notification: Notification) {
    guard let runId = notification.userInfo?["run_id"] as? String, !runId.isEmpty else { return }
    pendingFocusRunId = runId
    if snapshot != nil {
      applyPendingRunFocus()
    } else {
      refreshSnapshot()
    }
  }

  func refreshSnapshot() {
    guard !isLoading else { return }
    isLoading = true
    refreshButton.isEnabled = false
    statusLabel.stringValue = "Refreshing..."
    updateEmptyState()

    Task {
      do {
        let snapshot = try await Task.detached(priority: .userInitiated) {
          try loadMissionControlSnapshot()
        }.value
        await MainActor.run {
          self.apply(snapshot)
        }
      } catch {
        await MainActor.run {
          self.isLoading = false
          self.refreshButton.isEnabled = true
          self.statusLabel.stringValue = "Snapshot failed"
          self.emptyLabel.stringValue = "Mission Control snapshot failed: \(error)"
          self.emptyLabel.isHidden = false
          self.scrollView.isHidden = true
        }
      }
    }
  }

  private func configureTables() {
    configure(
      tableView: activeTableView,
      section: .active,
      columns: [
        ("Run", "RUN_ID", 240),
        ("Agent", "AGENT", 90),
        ("Skill", "SKILL", 120),
        ("Wave", "WAVE", 180),
        ("Age", "AGE", 90),
        ("ETA", "ETA", 110),
      ],
      title: "Active dispatches"
    )
    configure(
      tableView: failuresTableView,
      section: .failures,
      columns: [
        ("Run", "RUN_ID", 240),
        ("Date", "DATE", 145),
        ("Agent", "AGENT", 90),
        ("Skill", "SKILL", 120),
        ("Reason", "REASON", 320),
        ("Age", "AGE", 90),
      ],
      title: "Failures board"
    )
    configure(
      tableView: waveTableView,
      section: .waves,
      columns: [
        ("State", "STATE", 110),
        ("Wave", "WAVE", 260),
        ("Total", "TOTAL", 70),
        ("Complete", "COMPLETE", 80),
        ("Failed", "FAILED", 70),
        ("Active", "ACTIVE", 70),
      ],
      title: "Wave atlas"
    )
    configure(
      tableView: agentTableView,
      section: .agents,
      columns: [
        ("Agent", "AGENT", 140),
        ("Runs", "RUNS", 70),
        ("Complete", "COMPLETE", 80),
        ("Failed", "FAILED", 70),
        ("Success", "SUCCESS", 80),
        ("Model", "MODEL", 70),
        ("Avg Dur", "AVG_DUR", 90),
      ],
      title: "Per-agent stats"
    )
    configure(
      tableView: skillTableView,
      section: .skills,
      columns: [
        ("Skill", "SKILL", 220),
        ("Inv", "INV", 70),
        ("Complete", "COMPLETE", 80),
        ("Failed", "FAILED", 70),
        ("Avg Dur", "AVG_DUR", 90),
      ],
      title: "Per-skill stats"
    )
  }

  private func configure(
    tableView: NSTableView,
    section: Section,
    columns: [(String, String, CGFloat)],
    title: String
  ) {
    tableSections[ObjectIdentifier(tableView)] = section
    tableView.dataSource = self
    tableView.delegate = self
    tableView.usesAlternatingRowBackgroundColors = true
    tableView.rowHeight = 24
    tableView.allowsColumnResizing = true
    tableView.allowsEmptySelection = true
    tableView.allowsMultipleSelection = false

    for (title, identifier, width) in columns {
      let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(identifier))
      column.title = title
      column.width = width
      tableView.addTableColumn(column)
    }

    let titleLabel = NSTextField(labelWithString: title)
    titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
    sectionAnchors[section.focusID] = titleLabel
    stackView.addArrangedSubview(titleLabel)

    let scroll = NSScrollView()
    scroll.hasVerticalScroller = false
    scroll.hasHorizontalScroller = true
    scroll.borderType = .bezelBorder
    scroll.documentView = tableView
    scroll.translatesAutoresizingMaskIntoConstraints = false
    stackView.addArrangedSubview(scroll)

    let height = scroll.heightAnchor.constraint(equalToConstant: 86)
    height.isActive = true
    heightConstraints[ObjectIdentifier(tableView)] = height

    NSLayoutConstraint.activate([
      scroll.leadingAnchor.constraint(equalTo: stackView.leadingAnchor),
      scroll.trailingAnchor.constraint(equalTo: stackView.trailingAnchor),
    ])
  }

  private func configureHealthStrip() {
    settlementLabel.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .medium)
    settlementLabel.textColor = .labelColor
    stackView.addArrangedSubview(settlementLabel)

    let titleLabel = NSTextField(labelWithString: "Fleet health")
    titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
    sectionAnchors[Section.health.focusID] = titleLabel
    stackView.addArrangedSubview(titleLabel)

    healthStackView.orientation = .vertical
    healthStackView.alignment = .leading
    healthStackView.spacing = 4
    healthStackView.translatesAutoresizingMaskIntoConstraints = false
    stackView.addArrangedSubview(healthStackView)
    NSLayoutConstraint.activate([
      healthStackView.leadingAnchor.constraint(equalTo: stackView.leadingAnchor),
      healthStackView.trailingAnchor.constraint(lessThanOrEqualTo: stackView.trailingAnchor),
    ])

    dataQualityFooterLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
    dataQualityFooterLabel.textColor = .secondaryLabelColor
    dataQualityFooterLabel.lineBreakMode = .byTruncatingMiddle
    dataQualityFooterLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    stackView.addArrangedSubview(dataQualityFooterLabel)
  }

  private func apply(_ snapshot: FfiMissionControlSnapshot) {
    self.snapshot = snapshot
    isLoading = false
    refreshButton.isEnabled = true

    activeTableView.reloadData()
    failuresTableView.reloadData()
    waveTableView.reloadData()
    agentTableView.reloadData()
    skillTableView.reloadData()
    updateSettlementStrip()
    updateHealthStrip()
    updateDataQualityFooter()
    updateTableHeights()
    updateEmptyState()
    updateStatus()
    NotificationCenter.default.post(
      name: NSNotification.Name("MissionControlSnapshotChanged"), object: self,
      userInfo: ["snapshot": snapshot]
    )
    applyPendingRunFocus()
  }

  private func applyPendingRunFocus() {
    guard let runId = pendingFocusRunId, let snapshot else { return }
    pendingFocusRunId = nil
    if let idx = snapshot.activeDispatches.firstIndex(where: { $0.runId == runId }) {
      focusSection("active_dispatches")
      activeTableView.selectRowIndexes(IndexSet(integer: idx), byExtendingSelection: false)
      postSelection(runId: runId, sourcePath: nil, kind: "dispatch")
      return
    }
    if let idx = snapshot.failures.firstIndex(where: { $0.runId == runId }) {
      focusSection("failures")
      failuresTableView.selectRowIndexes(IndexSet(integer: idx), byExtendingSelection: false)
      let item = snapshot.failures[idx]
      postSelection(runId: runId, sourcePath: item.sourcePath, kind: "failure")
      return
    }
    postSelection(runId: runId, sourcePath: nil, kind: "dispatch")
  }

  private func updateStatus() {
    guard let snapshot else {
      statusLabel.stringValue = "No snapshot loaded"
      return
    }

    let quality = snapshot.dataQuality
    let capped = quality.capped ? " capped" : ""
    statusLabel.stringValue =
      "Generated \(snapshot.generatedAt) - \(quality.scannedMetaFiles) meta files\(capped)"
  }

  private func updateSettlementStrip() {
    guard let snapshot else {
      settlementLabel.stringValue = "settlement f=— x=— n=—"
      return
    }

    let settlement = snapshot.settlement
    settlementLabel.stringValue =
      "settlement f=\(settlement.f) x=\(settlement.x) n=\(settlement.n)"
  }

  private func updateHealthStrip() {
    healthStackView.arrangedSubviews.forEach { view in
      healthStackView.removeArrangedSubview(view)
      view.removeFromSuperview()
    }

    guard let snapshot else { return }
    if snapshot.fleetHealth.isEmpty {
      let label = NSTextField(labelWithString: "no health signals")
      label.textColor = .secondaryLabelColor
      healthStackView.addArrangedSubview(label)
      return
    }

    for signal in snapshot.fleetHealth {
      let status = fleetHealthStatusLabel(signal.status)
      let detail = displayValue(signal.detail)
      let label = NSTextField(labelWithString: "\(signal.label): \(status) - \(detail)")
      label.textColor = fleetHealthStatusColor(signal.status)
      label.lineBreakMode = .byTruncatingTail
      healthStackView.addArrangedSubview(label)
    }
  }

  private func updateDataQualityFooter() {
    guard let snapshot else {
      dataQualityFooterLabel.stringValue = ""
      return
    }

    let quality = snapshot.dataQuality
    let capped = quality.capped ? " · capped" : ""
    let artifactRoot = quality.artifactRootPresent ? "artifact root present" : "artifact root missing"
    dataQualityFooterLabel.stringValue =
      "\(quality.scannedMetaFiles) meta files · \(quality.parseFailures) parse failures · \(quality.missingModel) missing model · \(quality.missingDuration) missing duration · \(artifactRoot)\(capped)"
  }

  private func updateEmptyState() {
    if snapshot != nil {
      emptyLabel.isHidden = true
      scrollView.isHidden = false
      return
    }

    scrollView.isHidden = true
    emptyLabel.isHidden = false
    if isLoading {
      emptyLabel.stringValue = "Loading Mission Control snapshot..."
    } else {
      emptyLabel.stringValue = "No Mission Control data yet."
    }
  }

  private func updateTableHeights() {
    for tableView in [activeTableView, failuresTableView, waveTableView, agentTableView, skillTableView] {
      let rows = max(1, tableView.numberOfRows)
      heightConstraints[ObjectIdentifier(tableView)]?.constant = CGFloat(rows * 24 + 30)
    }
  }

  func numberOfRows(in tableView: NSTableView) -> Int {
    guard let snapshot, let section = tableSections[ObjectIdentifier(tableView)] else {
      return 0
    }
    switch section {
    case .agents:
      return snapshot.agentStats.count
    case .waves:
      return snapshot.waveAtlas.count
    case .skills:
      return snapshot.skillStats.count
    case .active:
      return max(1, snapshot.activeDispatches.count)
    case .failures:
      return max(1, snapshot.failures.count)
    case .health:
      return 0
    }
  }

  func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
    guard let identifier = tableColumn?.identifier else { return nil }
    let cell = reusableCell(for: tableView, identifier: identifier)
    cell.textField?.stringValue = value(for: tableView, column: identifier.rawValue, row: row)
    return cell
  }

  private func reusableCell(for tableView: NSTableView, identifier: NSUserInterfaceItemIdentifier)
    -> NSTableCellView
  {
    if let cell = tableView.makeView(withIdentifier: identifier, owner: self) as? NSTableCellView {
      return cell
    }

    let cell = NSTableCellView()
    cell.identifier = identifier
    let textField = NSTextField(labelWithString: "")
    textField.lineBreakMode = .byTruncatingTail
    textField.translatesAutoresizingMaskIntoConstraints = false
    cell.addSubview(textField)
    cell.textField = textField
    NSLayoutConstraint.activate([
      textField.leadingAnchor.constraint(equalTo: cell.leadingAnchor, constant: 6),
      textField.centerYAnchor.constraint(equalTo: cell.centerYAnchor),
      textField.trailingAnchor.constraint(equalTo: cell.trailingAnchor, constant: -6),
    ])
    return cell
  }

  private func value(for tableView: NSTableView, column: String, row: Int) -> String {
    guard let snapshot, let section = tableSections[ObjectIdentifier(tableView)] else { return "" }

    switch section {
    case .active:
      guard !snapshot.activeDispatches.isEmpty else {
        return column == "RUN_ID" ? "no live dispatches" : "—"
      }
      let item = snapshot.activeDispatches[row]
      switch column {
      case "RUN_ID": return item.runId
      case "AGENT": return displayValue(item.agent)
      case "SKILL": return displayValue(item.skill)
      case "WAVE": return displayValue(item.wave)
      case "AGE": return displayValue(item.ageLabel)
      case "ETA": return displayValue(item.etaLabel)
      default: return ""
      }
    case .failures:
      guard !snapshot.failures.isEmpty else {
        return column == "RUN_ID" ? "no failures" : "—"
      }
      let item = snapshot.failures[row]
      switch column {
      case "RUN_ID": return item.runId
      case "DATE": return dateTime(item.occurredAt)
      case "AGENT": return displayValue(item.agent)
      case "SKILL": return displayValue(item.skill)
      case "REASON": return displayValue(item.reason)
      case "AGE": return displayValue(item.ageLabel)
      default: return ""
      }
    case .waves:
      let item = snapshot.waveAtlas[row]
      switch column {
      case "STATE": return waveStateLabel(item.latestState)
      case "WAVE": return item.waveId
      case "TOTAL": return String(item.total)
      case "COMPLETE": return String(item.completed)
      case "FAILED": return String(item.failed)
      case "ACTIVE": return String(item.active)
      default: return ""
      }
    case .agents:
      let item = snapshot.agentStats[row]
      switch column {
      case "AGENT": return item.agent
      case "RUNS": return String(item.totalRuns)
      case "COMPLETE": return String(item.completed)
      case "FAILED": return String(item.failed)
      case "SUCCESS": return percent(item.successRate)
      case "MODEL": return percent(item.modelKnownRate)
      case "AVG_DUR": return duration(item.avgDurationS)
      default: return ""
      }
    case .skills:
      let item = snapshot.skillStats[row]
      switch column {
      case "SKILL": return item.skill
      case "INV": return String(item.invocations)
      case "COMPLETE": return String(item.completed)
      case "FAILED": return String(item.failed)
      case "AVG_DUR": return duration(item.avgDurationS)
      default: return ""
      }
    case .health:
      return ""
    }
  }

  func tableViewSelectionDidChange(_ notification: Notification) {
    guard let tableView = notification.object as? NSTableView,
      let snapshot,
      let section = tableSections[ObjectIdentifier(tableView)]
    else { return }

    let row = tableView.selectedRow
    guard row >= 0 else { return }

    switch section {
    case .active:
      guard row < snapshot.activeDispatches.count else { return }
      let item = snapshot.activeDispatches[row]
      postSelection(runId: item.runId, sourcePath: nil, kind: "dispatch")
    case .failures:
      guard row < snapshot.failures.count else { return }
      let item = snapshot.failures[row]
      postSelection(runId: item.runId, sourcePath: item.sourcePath, kind: "failure")
    case .agents, .waves, .skills, .health:
      return
    }
  }

  private func postSelection(runId: String, sourcePath: String?, kind: String) {
    var userInfo: [String: Any] = [
      "run_id": runId,
      "kind": kind,
    ]
    if let sourcePath {
      userInfo["source_path"] = sourcePath
    }
    NotificationCenter.default.post(
      name: Notification.Name("MissionControlSelection"),
      object: self,
      userInfo: userInfo
    )
  }

  private func displayValue(_ value: String?) -> String {
    guard let value, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
      return "—"
    }
    return value
  }

  private func dateTime(_ value: String?) -> String {
    guard let value, let date = Self.iso8601DateFormatter.date(from: value) else {
      return "—"
    }
    Self.failureDateFormatter.timeZone = .current
    return Self.failureDateFormatter.string(from: date)
  }

  private func focusSection(_ sectionID: String) {
    guard let anchor = sectionAnchors[sectionID] else { return }
    anchor.scrollToVisible(anchor.bounds)
    flash(anchor)
  }

  private func flash(_ anchor: NSView) {
    focusResetWorkItem?.cancel()
    highlightedAnchor?.wantsLayer = false

    anchor.wantsLayer = true
    anchor.layer?.backgroundColor = NSColor.controlAccentColor.withAlphaComponent(0.16).cgColor
    anchor.layer?.cornerRadius = 4
    highlightedAnchor = anchor

    let workItem = DispatchWorkItem { [weak self, weak anchor] in
      anchor?.wantsLayer = false
      if self?.highlightedAnchor === anchor {
        self?.highlightedAnchor = nil
      }
    }
    focusResetWorkItem = workItem
    DispatchQueue.main.asyncAfter(deadline: .now() + 1.2, execute: workItem)
  }

  private func waveStateLabel(_ state: FfiWaveState) -> String {
    switch state {
    case .pending: return "pending"
    case .inProgress: return "in-progress"
    case .completed: return "completed"
    case .failed: return "failed"
    }
  }

  private func percent(_ value: Float) -> String {
    "\(Int((value * 100).rounded()))%"
  }

  private func duration(_ seconds: Double?) -> String {
    guard let seconds else { return "—" }
    if seconds >= 3_600 {
      return String(format: "%.1fh", seconds / 3_600)
    }
    if seconds >= 60 {
      return String(format: "%.1fm", seconds / 60)
    }
    return String(format: "%.0fs", seconds)
  }

  private func fleetHealthStatusLabel(_ status: FfiFleetHealthStatus) -> String {
    switch status {
    case .ok: return "ok"
    case .warn: return "warn"
    case .blocked: return "blocked"
    case .unknown: return "unknown"
    }
  }

  private func fleetHealthStatusColor(_ status: FfiFleetHealthStatus) -> NSColor {
    switch status {
    case .ok: return .systemGreen
    case .warn: return .systemOrange
    case .blocked: return .systemRed
    case .unknown: return .secondaryLabelColor
    }
  }
}
