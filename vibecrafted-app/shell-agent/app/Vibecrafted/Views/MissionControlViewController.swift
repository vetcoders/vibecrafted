// Vibecrafted - Mission Control Snapshot
// Created by Vetcoders

import AppKit

final class MissionControlViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
  private enum Section: CaseIterable {
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

  private enum SortValue {
    case date(Date?)
    case number(Double)
    case text(String)
  }

  private let sectionContainer = NSView()
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
  private var sectionViews: [Section: NSView] = [:]
  private var selectedSection: Section = .active
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
    refreshButton.keyEquivalent = "r"
    refreshButton.keyEquivalentModifierMask = [.command]
    refreshButton.toolTip = "Refresh Mission Control (Command-R)"
    refreshButton.setAccessibilityLabel("Refresh Mission Control")

    headerStack.addArrangedSubview(titleLabel)
    headerStack.addArrangedSubview(statusLabel)
    headerStack.addArrangedSubview(NSView())
    headerStack.addArrangedSubview(refreshButton)

    sectionContainer.translatesAutoresizingMaskIntoConstraints = false
    root.addSubview(sectionContainer)

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

      sectionContainer.topAnchor.constraint(equalTo: headerStack.bottomAnchor, constant: 12),
      sectionContainer.leadingAnchor.constraint(equalTo: root.leadingAnchor, constant: 16),
      sectionContainer.trailingAnchor.constraint(equalTo: root.trailingAnchor, constant: -16),
      sectionContainer.bottomAnchor.constraint(equalTo: root.bottomAnchor, constant: -16),

      emptyLabel.centerXAnchor.constraint(equalTo: root.centerXAnchor),
      emptyLabel.centerYAnchor.constraint(equalTo: root.centerYAnchor),
      emptyLabel.leadingAnchor.constraint(greaterThanOrEqualTo: root.leadingAnchor, constant: 24),
      emptyLabel.trailingAnchor.constraint(lessThanOrEqualTo: root.trailingAnchor, constant: -24),
    ])
    selectSection(.active)
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
          self.sectionContainer.isHidden = true
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
        ("Started", "STARTED", 145),
        ("Age", "AGE", 90),
        ("ETA", "ETA", 110),
      ],
      title: "Active dispatches",
      actions: [
        actionButton("Inspect Selected", action: #selector(inspectSelectedRun)),
        actionButton("Check Clients", action: #selector(checkClients)),
      ]
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
      title: "Failures board",
      actions: [
        actionButton("Inspect Selected", action: #selector(inspectSelectedRun)),
        actionButton("Open Artifact", action: #selector(openSelectedFailureArtifact)),
      ]
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
      title: "Wave atlas",
      actions: []
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
      title: "Per-agent stats",
      actions: [actionButton("Check Clients", action: #selector(checkClients))]
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
      title: "Per-skill stats",
      actions: []
    )
  }

  private func configure(
    tableView: NSTableView,
    section: Section,
    columns: [(String, String, CGFloat)],
    title: String,
    actions: [NSButton]
  ) {
    tableSections[ObjectIdentifier(tableView)] = section
    tableView.dataSource = self
    tableView.delegate = self
    tableView.usesAlternatingRowBackgroundColors = true
    tableView.rowHeight = 24
    tableView.allowsColumnResizing = true
    tableView.allowsColumnReordering = true
    tableView.allowsEmptySelection = true
    tableView.allowsMultipleSelection = false
    tableView.setAccessibilityLabel(title)

    for (title, identifier, width) in columns {
      let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(identifier))
      column.title = title
      column.width = width
      column.sortDescriptorPrototype = NSSortDescriptor(
        key: identifier, ascending: true,
        selector: #selector(NSString.localizedStandardCompare(_:)))
      tableView.addTableColumn(column)
    }

    let panel = NSView()
    panel.translatesAutoresizingMaskIntoConstraints = false
    sectionContainer.addSubview(panel)
    sectionViews[section] = panel

    let titleLabel = NSTextField(labelWithString: title)
    titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
    titleLabel.translatesAutoresizingMaskIntoConstraints = false
    titleLabel.setAccessibilityRole(.staticText)
    titleLabel.setAccessibilitySubrole(NSAccessibility.Subrole(rawValue: "AXHeading"))
    panel.addSubview(titleLabel)

    let actionStack = NSStackView(views: actions)
    actionStack.orientation = .horizontal
    actionStack.spacing = 8
    actionStack.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(actionStack)

    let scroll = NSScrollView()
    scroll.hasVerticalScroller = true
    scroll.hasHorizontalScroller = true
    scroll.borderType = .bezelBorder
    scroll.documentView = tableView
    scroll.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(scroll)

    NSLayoutConstraint.activate([
      panel.topAnchor.constraint(equalTo: sectionContainer.topAnchor),
      panel.leadingAnchor.constraint(equalTo: sectionContainer.leadingAnchor),
      panel.trailingAnchor.constraint(equalTo: sectionContainer.trailingAnchor),
      panel.bottomAnchor.constraint(equalTo: sectionContainer.bottomAnchor),
      titleLabel.topAnchor.constraint(equalTo: panel.topAnchor),
      titleLabel.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      actionStack.centerYAnchor.constraint(equalTo: titleLabel.centerYAnchor),
      actionStack.trailingAnchor.constraint(equalTo: panel.trailingAnchor),
      titleLabel.trailingAnchor.constraint(lessThanOrEqualTo: actionStack.leadingAnchor, constant: -12),
      scroll.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 8),
      scroll.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      scroll.trailingAnchor.constraint(equalTo: panel.trailingAnchor),
      scroll.bottomAnchor.constraint(equalTo: panel.bottomAnchor),
    ])
  }

  private func configureHealthStrip() {
    let panel = NSView()
    panel.translatesAutoresizingMaskIntoConstraints = false
    sectionContainer.addSubview(panel)
    sectionViews[.health] = panel

    let titleLabel = NSTextField(labelWithString: "Fleet health")
    titleLabel.font = NSFont.systemFont(ofSize: 13, weight: .semibold)
    titleLabel.translatesAutoresizingMaskIntoConstraints = false
    titleLabel.setAccessibilityRole(.staticText)
    titleLabel.setAccessibilitySubrole(NSAccessibility.Subrole(rawValue: "AXHeading"))
    panel.addSubview(titleLabel)

    let actions = NSStackView(views: [
      actionButton("Launch Server", action: #selector(launchServer)),
      actionButton("Relaunch Server", action: #selector(relaunchServer)),
      actionButton("Check Clients", action: #selector(checkClients)),
      actionButton("Open Server", action: #selector(openServer)),
    ])
    actions.orientation = .horizontal
    actions.spacing = 8
    actions.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(actions)

    settlementLabel.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .medium)
    settlementLabel.textColor = .labelColor
    settlementLabel.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(settlementLabel)

    healthStackView.orientation = .vertical
    healthStackView.alignment = .leading
    healthStackView.spacing = 4
    healthStackView.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(healthStackView)

    dataQualityFooterLabel.font = NSFont.systemFont(ofSize: 12, weight: .regular)
    dataQualityFooterLabel.textColor = .secondaryLabelColor
    dataQualityFooterLabel.lineBreakMode = .byTruncatingMiddle
    dataQualityFooterLabel.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
    dataQualityFooterLabel.translatesAutoresizingMaskIntoConstraints = false
    panel.addSubview(dataQualityFooterLabel)

    NSLayoutConstraint.activate([
      panel.topAnchor.constraint(equalTo: sectionContainer.topAnchor),
      panel.leadingAnchor.constraint(equalTo: sectionContainer.leadingAnchor),
      panel.trailingAnchor.constraint(equalTo: sectionContainer.trailingAnchor),
      panel.bottomAnchor.constraint(equalTo: sectionContainer.bottomAnchor),
      titleLabel.topAnchor.constraint(equalTo: panel.topAnchor),
      titleLabel.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      actions.centerYAnchor.constraint(equalTo: titleLabel.centerYAnchor),
      actions.trailingAnchor.constraint(equalTo: panel.trailingAnchor),
      titleLabel.trailingAnchor.constraint(lessThanOrEqualTo: actions.leadingAnchor, constant: -12),
      settlementLabel.topAnchor.constraint(equalTo: titleLabel.bottomAnchor, constant: 16),
      settlementLabel.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      healthStackView.topAnchor.constraint(equalTo: settlementLabel.bottomAnchor, constant: 12),
      healthStackView.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      healthStackView.trailingAnchor.constraint(lessThanOrEqualTo: panel.trailingAnchor),
      dataQualityFooterLabel.leadingAnchor.constraint(equalTo: panel.leadingAnchor),
      dataQualityFooterLabel.trailingAnchor.constraint(equalTo: panel.trailingAnchor),
      dataQualityFooterLabel.bottomAnchor.constraint(equalTo: panel.bottomAnchor),
    ])
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
      let displayedRow = sortedIndices(for: activeTableView).firstIndex(of: idx) ?? idx
      activeTableView.selectRowIndexes(IndexSet(integer: displayedRow), byExtendingSelection: false)
      postSelection(runId: runId, sourcePath: nil, kind: "dispatch")
      return
    }
    if let idx = snapshot.failures.firstIndex(where: { $0.runId == runId }) {
      focusSection("failures")
      let displayedRow = sortedIndices(for: failuresTableView).firstIndex(of: idx) ?? idx
      failuresTableView.selectRowIndexes(IndexSet(integer: displayedRow), byExtendingSelection: false)
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
      sectionContainer.isHidden = false
      return
    }

    sectionContainer.isHidden = true
    emptyLabel.isHidden = false
    if isLoading {
      emptyLabel.stringValue = "Loading Mission Control snapshot..."
    } else {
      emptyLabel.stringValue = "No Mission Control data yet."
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
    let sourceRow = sourceRow(for: tableView, displayedRow: row)
    cell.textField?.stringValue = value(for: tableView, column: identifier.rawValue, row: sourceRow)
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
      case "STARTED": return dateTime(item.startedAt)
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

    let displayedRow = tableView.selectedRow
    guard displayedRow >= 0 else { return }
    let row = sourceRow(for: tableView, displayedRow: displayedRow)

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

  func tableView(_ tableView: NSTableView, sortDescriptorsDidChange oldDescriptors: [NSSortDescriptor]) {
    tableView.reloadData()
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
    guard let section = Section.allCases.first(where: { $0.focusID == sectionID }) else { return }
    selectSection(section)
  }

  private func selectSection(_ section: Section) {
    selectedSection = section
    for (candidate, sectionView) in sectionViews {
      sectionView.isHidden = candidate != section
    }
    sectionViews[section]?.setAccessibilityFocused(true)
  }

  private func actionButton(_ title: String, action: Selector) -> NSButton {
    let button = NSButton(title: title, target: self, action: action)
    button.bezelStyle = .rounded
    button.setAccessibilityLabel(title)
    return button
  }

  private func sourceRow(for tableView: NSTableView, displayedRow: Int) -> Int {
    let indices = sortedIndices(for: tableView)
    guard indices.indices.contains(displayedRow) else { return displayedRow }
    return indices[displayedRow]
  }

  private func sortedIndices(for tableView: NSTableView) -> [Int] {
    guard let snapshot, let section = tableSections[ObjectIdentifier(tableView)] else { return [] }
    let count: Int
    switch section {
    case .active: count = snapshot.activeDispatches.count
    case .failures: count = snapshot.failures.count
    case .waves: count = snapshot.waveAtlas.count
    case .agents: count = snapshot.agentStats.count
    case .skills: count = snapshot.skillStats.count
    case .health: count = 0
    }
    let indices = Array(0..<count)
    guard let descriptor = tableView.sortDescriptors.first, let column = descriptor.key else {
      return indices
    }
    return indices.sorted { lhs, rhs in
      let result = compare(
        sortValue(section: section, column: column, row: lhs),
        sortValue(section: section, column: column, row: rhs))
      if result == .orderedSame { return lhs < rhs }
      return descriptor.ascending ? result == .orderedAscending : result == .orderedDescending
    }
  }

  private func sortValue(section: Section, column: String, row: Int) -> SortValue {
    guard let snapshot else { return .text("") }
    switch section {
    case .active:
      let item = snapshot.activeDispatches[row]
      switch column {
      case "RUN_ID": return .text(item.runId)
      case "AGENT": return .text(item.agent)
      case "SKILL": return .text(item.skill)
      case "WAVE": return .text(item.wave ?? "")
      case "STARTED": return .date(item.startedAt.flatMap(Self.iso8601DateFormatter.date(from:)))
      case "AGE": return .date(item.startedAt.flatMap(Self.iso8601DateFormatter.date(from:)))
      case "ETA": return .text(item.etaLabel)
      default: return .text("")
      }
    case .failures:
      let item = snapshot.failures[row]
      switch column {
      case "RUN_ID": return .text(item.runId)
      case "DATE", "AGE": return .date(item.occurredAt.flatMap(Self.iso8601DateFormatter.date(from:)))
      case "AGENT": return .text(item.agent)
      case "SKILL": return .text(item.skill)
      case "REASON": return .text(item.reason)
      default: return .text("")
      }
    case .waves:
      let item = snapshot.waveAtlas[row]
      switch column {
      case "STATE": return .text(waveStateLabel(item.latestState))
      case "WAVE": return .text(item.waveId)
      case "TOTAL": return .number(Double(item.total))
      case "COMPLETE": return .number(Double(item.completed))
      case "FAILED": return .number(Double(item.failed))
      case "ACTIVE": return .number(Double(item.active))
      default: return .text("")
      }
    case .agents:
      let item = snapshot.agentStats[row]
      switch column {
      case "AGENT": return .text(item.agent)
      case "RUNS": return .number(Double(item.totalRuns))
      case "COMPLETE": return .number(Double(item.completed))
      case "FAILED": return .number(Double(item.failed))
      case "SUCCESS": return .number(Double(item.successRate))
      case "MODEL": return .number(Double(item.modelKnownRate))
      case "AVG_DUR": return .number(item.avgDurationS ?? -.infinity)
      default: return .text("")
      }
    case .skills:
      let item = snapshot.skillStats[row]
      switch column {
      case "SKILL": return .text(item.skill)
      case "INV": return .number(Double(item.invocations))
      case "COMPLETE": return .number(Double(item.completed))
      case "FAILED": return .number(Double(item.failed))
      case "AVG_DUR": return .number(item.avgDurationS ?? -.infinity)
      default: return .text("")
      }
    case .health:
      return .text("")
    }
  }

  private func compare(_ lhs: SortValue, _ rhs: SortValue) -> ComparisonResult {
    switch (lhs, rhs) {
    case (.number(let lhs), .number(let rhs)):
      if lhs == rhs { return .orderedSame }
      return lhs < rhs ? .orderedAscending : .orderedDescending
    case (.date(let lhs), .date(let rhs)):
      if lhs == rhs { return .orderedSame }
      guard let lhs else { return .orderedAscending }
      guard let rhs else { return .orderedDescending }
      return lhs < rhs ? .orderedAscending : .orderedDescending
    case (.text(let lhs), .text(let rhs)):
      return lhs.localizedStandardCompare(rhs)
    default:
      return .orderedSame
    }
  }

  @objc private func inspectSelectedRun() {
    let tableView = selectedSection == .failures ? failuresTableView : activeTableView
    guard tableView.selectedRow >= 0 else {
      NSSound.beep()
      return
    }
    tableViewSelectionDidChange(Notification(name: NSTableView.selectionDidChangeNotification, object: tableView))
  }

  @objc private func openSelectedFailureArtifact() {
    guard let snapshot, failuresTableView.selectedRow >= 0 else {
      NSSound.beep()
      return
    }
    let row = sourceRow(for: failuresTableView, displayedRow: failuresTableView.selectedRow)
    guard snapshot.failures.indices.contains(row), let path = snapshot.failures[row].sourcePath else {
      NSSound.beep()
      return
    }
    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
  }

  @objc private func launchServer() {
    routeServerLifecycleAction("startServerFromStatusItem")
  }

  @objc private func relaunchServer() {
    routeServerLifecycleAction("restartServerFromStatusItem")
  }

  private func routeServerLifecycleAction(_ selectorName: String) {
    let handled = NSApp.sendAction(Selector(selectorName), to: NSApp.delegate, from: self)
    if !handled {
      presentAlert(title: "Server action unavailable", detail: "The canonical app service owner is not ready.")
    }
  }

  @objc private func checkClients() {
    Task {
      do {
        let result = try await verifyClient(kind: .codex)
        await MainActor.run {
          self.presentAlert(
            title: result.ok ? "Client check passed" : "Client check failed",
            detail: result.detail)
        }
      } catch {
        await MainActor.run {
          self.presentAlert(title: "Client check failed", detail: error.localizedDescription)
        }
      }
    }
  }

  @objc private func openServer() {
    let environment = ProcessInfo.processInfo.environment
    let rawURL =
      environment["VIBECRAFTED_SERVER_URL"] ?? environment["VC_SERVER_URL"]
      ?? "http://127.0.0.1:3025"
    guard let url = URL(string: rawURL),
      ["http", "https"].contains(url.scheme?.lowercased() ?? ""),
      url.host != nil
    else {
      presentAlert(title: "Server URL unavailable", detail: "The configured server URL is not a safe HTTP(S) URL.")
      return
    }
    NSWorkspace.shared.open(url)
  }

  private func presentAlert(title: String, detail: String) {
    let alert = NSAlert()
    alert.messageText = title
    alert.informativeText = detail
    alert.runModal()
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
