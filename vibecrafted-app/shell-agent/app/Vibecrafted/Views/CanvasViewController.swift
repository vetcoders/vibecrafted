// Vibecrafted — Canvas View (Mission Control / Routing Matrix / Log Tail)
// Created by Vetcoders

import AppKit

class CanvasViewController: NSViewController, NSTableViewDataSource, NSTableViewDelegate {
  private let segmentedControl = NSSegmentedControl(
    labels: ["Mission Control", "Routing Matrix", "Log Tail"], trackingMode: .selectOne, target: nil,
    action: nil)

  // Mission Control
  private let missionControlVC = MissionControlViewController()

  // Routing Matrix
  private let matrixScrollView = NSScrollView()
  private let matrixTableView = NSTableView()
  private var routes: [FfiRoute] = []

  // Log Tail
  private let logScrollView = NSScrollView()
  private let logTextView = NSTextView()

  private var currentServerName: String?
  private var currentRunID: String?

  override func loadView() {
    let container = NSView()
    container.wantsLayer = true
    view = container

    segmentedControl.selectedSegment = 0
    segmentedControl.target = self
    segmentedControl.action = #selector(segmentChanged(_:))
    segmentedControl.translatesAutoresizingMaskIntoConstraints = false
    container.addSubview(segmentedControl)

    addChild(missionControlVC)
    let missionControlView = missionControlVC.view
    missionControlView.translatesAutoresizingMaskIntoConstraints = false
    container.addSubview(missionControlView)

    // Matrix setup
    matrixScrollView.hasVerticalScroller = true
    matrixScrollView.borderType = .bezelBorder

    let clientCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Client"))
    clientCol.title = "Client"
    clientCol.width = 100
    matrixTableView.addTableColumn(clientCol)

    let serverCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("Server"))
    serverCol.title = "Server"
    serverCol.width = 150
    matrixTableView.addTableColumn(serverCol)

    let stateCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("State"))
    stateCol.title = "State"
    stateCol.width = 100
    matrixTableView.addTableColumn(stateCol)

    matrixTableView.dataSource = self
    matrixTableView.delegate = self
    matrixScrollView.documentView = matrixTableView
    matrixScrollView.translatesAutoresizingMaskIntoConstraints = false
    matrixScrollView.isHidden = true
    container.addSubview(matrixScrollView)

    // Log setup
    logScrollView.hasVerticalScroller = true
    logScrollView.borderType = .bezelBorder
    logTextView.isEditable = false
    logTextView.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
    logScrollView.documentView = logTextView
    logScrollView.translatesAutoresizingMaskIntoConstraints = false
    logScrollView.isHidden = true
    container.addSubview(logScrollView)

    NSLayoutConstraint.activate([
      segmentedControl.topAnchor.constraint(equalTo: container.topAnchor, constant: 12),
      segmentedControl.centerXAnchor.constraint(equalTo: container.centerXAnchor),

      missionControlView.topAnchor.constraint(equalTo: segmentedControl.bottomAnchor, constant: 12),
      missionControlView.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 12),
      missionControlView.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
      missionControlView.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -12),

      matrixScrollView.topAnchor.constraint(equalTo: segmentedControl.bottomAnchor, constant: 12),
      matrixScrollView.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 12),
      matrixScrollView.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
      matrixScrollView.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -12),

      logScrollView.topAnchor.constraint(equalTo: segmentedControl.bottomAnchor, constant: 12),
      logScrollView.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 12),
      logScrollView.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -12),
      logScrollView.bottomAnchor.constraint(equalTo: container.bottomAnchor, constant: -12),
    ])

    NotificationCenter.default.addObserver(
      self, selector: #selector(handleIpcEvent),
      name: NSNotification.Name("IpcEvent"), object: nil
    )
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleSelectedServerChanged),
      name: NSNotification.Name("SelectedServerChanged"), object: nil
    )
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlFocusSection),
      name: NSNotification.Name("MissionControlFocusSection"), object: nil
    )
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlFocusRun),
      name: NSNotification.Name("MissionControlFocusRun"), object: nil
    )
    NotificationCenter.default.addObserver(
      self, selector: #selector(handleMissionControlSelection),
      name: NSNotification.Name("MissionControlSelection"), object: nil
    )

    refreshRoutes()
  }

  @objc private func segmentChanged(_ sender: NSSegmentedControl) {
    let selectedSegment = sender.selectedSegment
    missionControlVC.view.isHidden = selectedSegment != 0
    matrixScrollView.isHidden = selectedSegment != 1
    logScrollView.isHidden = selectedSegment != 2

    if selectedSegment == 0 {
      missionControlVC.refreshSnapshot()
    } else if selectedSegment == 2 {
      refreshLogs()
    }
  }

  @objc private func handleIpcEvent(_ notification: Notification) {
    refreshRoutes()
  }

  @objc private func handleSelectedServerChanged(_ notification: Notification) {
    currentServerName = notification.userInfo?["serverName"] as? String
    currentRunID = nil
    if segmentedControl.selectedSegment == 2 {
      refreshLogs()
    }
  }

  @objc private func handleMissionControlSelection(_ notification: Notification) {
    currentRunID = notification.userInfo?["run_id"] as? String
    currentServerName = nil
    if segmentedControl.selectedSegment == 2 {
      refreshLogs()
    }
  }

  @objc private func handleMissionControlFocusSection(_ notification: Notification) {
    showMissionControlTab()
  }

  @objc private func handleMissionControlFocusRun(_ notification: Notification) {
    showMissionControlTab()
  }

  private func showMissionControlTab() {
    if segmentedControl.selectedSegment != 0 {
      segmentedControl.selectedSegment = 0
      segmentChanged(segmentedControl)
    }
  }

  private func refreshRoutes() {
    Task {
      do {
        self.routes = try await getRoutes()
        self.matrixTableView.reloadData()
      } catch {
        print("Failed to get routes: \(error)")
      }
    }
  }

  private func refreshLogs() {
    if let runID = currentRunID {
      Task {
        do {
          let detail = try loadRunDetail(runId: runID)
          self.logTextView.string =
            detail.transcriptTail.isEmpty
            ? "Run \(runID) has no transcript output yet."
            : detail.transcriptTail
          self.logTextView.scrollToEndOfDocument(nil)
        } catch {
          self.logTextView.string = "Failed to load run \(runID): \(error)"
        }
      }
      return
    }

    guard let server = currentServerName else {
      logTextView.string = "Select a run in Mission Control or a server in Routing Matrix."
      return
    }
    Task {
      do {
        let lines = try await getRecentLogs(service: server, lines: 100)
        self.logTextView.string = lines.joined(separator: "\n")
        self.logTextView.scrollToEndOfDocument(nil)
      } catch {
        self.logTextView.string = "Failed to load logs: \(error)"
      }
    }
  }

  // MARK: - NSTableViewDataSource

  func numberOfRows(in tableView: NSTableView) -> Int {
    routes.count
  }

  // MARK: - NSTableViewDelegate

  func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView?
  {
    let route = routes[row]
    let identifier = tableColumn?.identifier ?? NSUserInterfaceItemIdentifier("")
    var cell = tableView.makeView(withIdentifier: identifier, owner: self) as? NSTableCellView

    if cell == nil {
      cell = NSTableCellView()
      cell?.identifier = identifier
      let textField = NSTextField(labelWithString: "")
      textField.translatesAutoresizingMaskIntoConstraints = false
      cell?.addSubview(textField)
      cell?.textField = textField
      NSLayoutConstraint.activate([
        textField.leadingAnchor.constraint(equalTo: cell!.leadingAnchor, constant: 4),
        textField.centerYAnchor.constraint(equalTo: cell!.centerYAnchor),
        textField.trailingAnchor.constraint(equalTo: cell!.trailingAnchor, constant: -4),
      ])
    }

    if identifier.rawValue == "Client" {
      cell?.textField?.stringValue = String(describing: route.client)
    } else if identifier.rawValue == "Server" {
      cell?.textField?.stringValue = route.service
    } else if identifier.rawValue == "State" {
      cell?.textField?.stringValue = route.state
    }

    return cell
  }

  func tableViewSelectionDidChange(_ notification: Notification) {
    guard notification.object as? NSTableView === matrixTableView else { return }
    let row = matrixTableView.selectedRow
    guard row >= 0, row < routes.count else { return }
    NotificationCenter.default.post(
      name: NSNotification.Name("SelectedServerChanged"),
      object: self,
      userInfo: ["serverName": routes[row].service]
    )
  }
}
