// Vibecrafted — Sidebar
// Created by Vetcoders

import AppKit

class SidebarViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
  private let scrollView = NSScrollView()
  private let tableView = NSTableView()

  private enum MissionControlSection: String, CaseIterable {
    case activeDispatches = "active_dispatches"
    case waves
    case agents
    case skills
    case failures
    case health

    var title: String {
      switch self {
      case .activeDispatches: return "Active Dispatches"
      case .waves: return "Waves"
      case .agents: return "Agents"
      case .skills: return "Skills"
      case .failures: return "Failures"
      case .health: return "Health"
      }
    }

    var symbolName: String {
      switch self {
      case .activeDispatches: return "bolt.fill"
      case .waves: return "waveform.path.ecg"
      case .agents: return "person.2.fill"
      case .skills: return "hammer.fill"
      case .failures: return "exclamationmark.triangle.fill"
      case .health: return "heart.fill"
      }
    }

    func count(in snapshot: FfiMissionControlSnapshot?) -> Int {
      guard let snapshot else { return 0 }
      switch self {
      case .activeDispatches: return snapshot.activeDispatches.count
      case .waves: return snapshot.waveAtlas.count
      case .agents: return snapshot.agentStats.count
      case .skills: return snapshot.skillStats.count
      case .failures: return snapshot.failures.count
      case .health: return snapshot.fleetHealth.count
      }
    }
  }

  private var snapshot: FfiMissionControlSnapshot?

  override func loadView() {
    let container = NSView()
    container.wantsLayer = true
    view = container

    scrollView.hasVerticalScroller = true
    scrollView.borderType = .noBorder

    let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("MissionControlColumn"))
    column.title = "Mission Control"
    tableView.addTableColumn(column)
    tableView.headerView = nil
    tableView.dataSource = self
    tableView.delegate = self
    tableView.rowHeight = 28
    tableView.style = .sourceList
    tableView.allowsEmptySelection = false
    tableView.allowsMultipleSelection = false
    tableView.setAccessibilityLabel("Mission Control sections")

    scrollView.documentView = tableView
    scrollView.translatesAutoresizingMaskIntoConstraints = false
    container.addSubview(scrollView)

    NSLayoutConstraint.activate([
      scrollView.topAnchor.constraint(equalTo: container.topAnchor),
      scrollView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
      scrollView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
      scrollView.bottomAnchor.constraint(equalTo: container.bottomAnchor),
    ])

    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlSnapshotChanged),
      name: NSNotification.Name("MissionControlSnapshotChanged"), object: nil
    )

    refreshCounts()
    tableView.selectRowIndexes(IndexSet(integer: 0), byExtendingSelection: false)
  }

  @objc private func handleMissionControlSnapshotChanged(_ notification: Notification) {
    snapshot = notification.userInfo?["snapshot"] as? FfiMissionControlSnapshot
    tableView.reloadData()
  }

  private func refreshCounts() {
    Task {
      do {
        let snapshot = try await Task.detached(priority: .userInitiated) {
          try loadMissionControlSnapshot()
        }.value
        await MainActor.run {
          self.snapshot = snapshot
          self.tableView.reloadData()
        }
      } catch {
        print("Failed to get Mission Control snapshot for sidebar: \(error)")
      }
    }
  }

  // MARK: - NSTableViewDataSource

  func numberOfRows(in tableView: NSTableView) -> Int {
    MissionControlSection.allCases.count
  }

  // MARK: - NSTableViewDelegate

  func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView?
  {
    let section = MissionControlSection.allCases[row]
    let identifier = NSUserInterfaceItemIdentifier("MissionControlSectionCell")
    var cell = tableView.makeView(withIdentifier: identifier, owner: self) as? MissionControlSidebarCell

    if cell == nil {
      cell = MissionControlSidebarCell()
      cell?.identifier = identifier

      let imageView = NSImageView()
      imageView.translatesAutoresizingMaskIntoConstraints = false
      imageView.symbolConfiguration = NSImage.SymbolConfiguration(pointSize: 12, weight: .regular)

      let textField = NSTextField(labelWithString: "")
      textField.translatesAutoresizingMaskIntoConstraints = false
      textField.lineBreakMode = .byTruncatingTail

      let countField = NSTextField(labelWithString: "")
      countField.translatesAutoresizingMaskIntoConstraints = false
      countField.alignment = .right
      countField.font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .medium)
      countField.textColor = .secondaryLabelColor
      countField.setContentHuggingPriority(.required, for: .horizontal)

      cell?.addSubview(imageView)
      cell?.addSubview(textField)
      cell?.addSubview(countField)
      cell?.imageView = imageView
      cell?.textField = textField
      cell?.countField = countField

      NSLayoutConstraint.activate([
        imageView.leadingAnchor.constraint(equalTo: cell!.leadingAnchor, constant: 4),
        imageView.centerYAnchor.constraint(equalTo: cell!.centerYAnchor),
        imageView.widthAnchor.constraint(equalToConstant: 16),
        imageView.heightAnchor.constraint(equalToConstant: 16),

        textField.leadingAnchor.constraint(equalTo: imageView.trailingAnchor, constant: 6),
        textField.centerYAnchor.constraint(equalTo: cell!.centerYAnchor),
        textField.trailingAnchor.constraint(lessThanOrEqualTo: countField.leadingAnchor, constant: -8),

        countField.centerYAnchor.constraint(equalTo: cell!.centerYAnchor),
        countField.trailingAnchor.constraint(equalTo: cell!.trailingAnchor, constant: -6),
        countField.widthAnchor.constraint(greaterThanOrEqualToConstant: 20),
      ])
    }

    cell?.textField?.stringValue = section.title
    cell?.countField?.stringValue = String(section.count(in: snapshot))
    cell?.imageView?.image = NSImage(
      systemSymbolName: section.symbolName, accessibilityDescription: section.title)
    cell?.imageView?.contentTintColor = .secondaryLabelColor
    cell?.setAccessibilityLabel("\(section.title), \(section.count(in: snapshot)) items")

    return cell
  }

  func tableViewSelectionDidChange(_ notification: Notification) {
    let row = tableView.selectedRow
    if row >= 0 && row < MissionControlSection.allCases.count {
      let section = MissionControlSection.allCases[row]
      NotificationCenter.default.post(
        name: NSNotification.Name("MissionControlFocusSection"), object: nil,
        userInfo: ["section": section.rawValue]
      )
    }
  }
}

private final class MissionControlSidebarCell: NSTableCellView {
  var countField: NSTextField?
}
