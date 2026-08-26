import AppKit
import os.log
import UserNotifications

/// Native macOS notifications for settled runs.
///
/// `osascript display notification` is attributed to Script Editor. Posting
/// through `UNUserNotificationCenter` from this bundle keeps the sender as
/// Vibecrafted.app and lets a click open Mission Control on the run.
///
/// Mutable state (`started`, `presentWindow`) is only written on the main
/// thread during app launch; notification-center and IPC callbacks arrive on
/// other threads but never mutate, which is why this is @unchecked Sendable.
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate, @unchecked Sendable {
  static let shared = NotificationManager()

  static let categoryRunSettled = "run.settled"
  static let actionOpenRun = "OPEN_RUN"
  static let actionOpenReport = "OPEN_REPORT"
  static let urlScheme = "vibecrafted"
  static let pidFileName = "native_app.pid"

  static let terminalStates: Set<String> = [
    "settled", "completed", "failed", "error", "stopped", "cancelled",
  ]

  var presentWindow: (() -> Void)?

  private let log = Logger(subsystem: "io.vetcoders.vibecrafted", category: "notify")
  private var started = false

  private override init() {
    super.init()
  }

  func start(craftedHome: URL) {
    writeHeartbeat(craftedHome: craftedHome)
    guard !started else { return }
    started = true

    let center = UNUserNotificationCenter.current()
    center.delegate = self
    center.setNotificationCategories([Self.settledCategory()])
    center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
      if let error {
        self.log.error("notification authorization failed: \(error.localizedDescription, privacy: .public)")
        return
      }
      if !granted {
        self.log.error("notification authorization denied")
      }
    }

    NotificationCenter.default.addObserver(
      self, selector: #selector(handleIpcEvent),
      name: NSNotification.Name("IpcEvent"), object: nil)
  }

  func clearHeartbeat(craftedHome: URL) {
    let url = Self.heartbeatURL(craftedHome: craftedHome)
    try? FileManager.default.removeItem(at: url)
  }

  func handleOpenURLs(_ urls: [URL]) {
    for url in urls {
      if Self.isConsoleURL(url) {
        presentWindow?()
        continue
      }
      guard let target = Self.parseVibecraftedURL(url) else { continue }
      present(runId: target.runId, report: nil, preferReport: target.kind == "report")
    }
  }

  static func isConsoleURL(_ url: URL) -> Bool {
    guard url.scheme == urlScheme else { return false }
    return url.host == "console" && url.path == "/open"
  }

  static func parseVibecraftedURL(_ url: URL) -> (kind: String, runId: String)? {
    guard url.scheme == urlScheme else { return nil }
    var parts: [String] = []
    if let host = url.host, !host.isEmpty {
      parts.append(host)
    }
    parts.append(
      contentsOf: url.path.split(separator: "/").map(String.init).filter { !$0.isEmpty })
    guard parts.count >= 2 else { return nil }
    let kind = parts[0]
    let runId = parts[1]
    guard (kind == "run" || kind == "report"), !runId.isEmpty else { return nil }
    return (kind, runId)
  }

  static func heartbeatURL(craftedHome: URL) -> URL {
    craftedHome.appendingPathComponent("control_plane", isDirectory: true)
      .appendingPathComponent(pidFileName)
  }

  static func settledEvent(from json: String) -> (runId: String, title: String, body: String, report: String?)?
  {
    guard let data = json.data(using: .utf8),
      let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else { return nil }

    let payload: [String: Any]
    if let nested = root["payload"] as? [String: Any],
      (root["type"] as? String) == "SpawnUpdate"
    {
      payload = nested
    } else if let kind = root["kind"] as? String,
      kind == "spawn-update" || kind == "run-settled"
    {
      payload = (root["payload"] as? [String: Any]) ?? [:]
    } else {
      return nil
    }

    let state = ((payload["state"] as? String) ?? (payload["status"] as? String) ?? "")
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .lowercased()
    guard terminalStates.contains(state) else { return nil }

    let runId =
      (payload["run_id"] as? String)
      ?? (root["run_id"] as? String)
      ?? ""
    guard !runId.isEmpty else { return nil }

    let agent = payload["agent"] as? String ?? ""
    let skill = payload["skill"] as? String ?? ""
    let mode = payload["mode"] as? String ?? ""
    let report = payload["report"] as? String
    let title: String
    if state == "settled" {
      let tui = mode.isEmpty ? "f" : mode
      title = "Vibecrafted \(tui): settled"
    } else {
      title = "Vibecrafted \(state)"
    }
    let bodyBits = [runId, skill, agent].filter { !$0.isEmpty }
    let body = bodyBits.isEmpty ? runId : bodyBits.joined(separator: " · ")
    return (runId, title, body, report)
  }

  private func writeHeartbeat(craftedHome: URL) {
    let url = Self.heartbeatURL(craftedHome: craftedHome)
    do {
      try FileManager.default.createDirectory(
        at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
      let bundle = Bundle.main.bundleIdentifier ?? "io.vetcoders.vibecrafted"
      let body = "\(ProcessInfo.processInfo.processIdentifier)\n\(bundle)\n"
      try Data(body.utf8).write(to: url, options: .atomic)
    } catch {
      log.error("native app heartbeat not written: \(error.localizedDescription, privacy: .public)")
    }
  }

  private static func settledCategory() -> UNNotificationCategory {
    let openRun = UNNotificationAction(
      identifier: actionOpenRun, title: "Open run", options: [.foreground])
    let openReport = UNNotificationAction(
      identifier: actionOpenReport, title: "Report", options: [.foreground])
    return UNNotificationCategory(
      identifier: categoryRunSettled, actions: [openRun, openReport],
      intentIdentifiers: [], options: [])
  }

  @objc private func handleIpcEvent(_ notification: Notification) {
    guard let json = notification.userInfo?["eventJson"] as? String else { return }
    guard let event = Self.settledEvent(from: json) else { return }
    postSettled(runId: event.runId, title: event.title, body: event.body, report: event.report)
  }

  private func postSettled(runId: String, title: String, body: String, report: String?) {
    let content = UNMutableNotificationContent()
    content.title = title
    content.body = body
    content.sound = .default
    content.categoryIdentifier = Self.categoryRunSettled
    var userInfo: [String: String] = ["run_id": runId]
    if let report, !report.isEmpty {
      userInfo["report"] = report
    }
    content.userInfo = userInfo

    let request = UNNotificationRequest(
      identifier: "\(Self.categoryRunSettled).\(runId)", content: content, trigger: nil)
    UNUserNotificationCenter.current().add(request) { error in
      if let error {
        self.log.error("notification post failed: \(error.localizedDescription, privacy: .public)")
      }
    }
  }

  private func present(runId: String, report: String?, preferReport: Bool) {
    presentWindow?()
    NotificationCenter.default.post(
      name: NSNotification.Name("MissionControlFocusSection"), object: nil,
      userInfo: ["section": "active_dispatches"])
    var userInfo: [String: Any] = ["run_id": runId]
    if let report, !report.isEmpty {
      userInfo["source_path"] = report
    }
    NotificationCenter.default.post(
      name: NSNotification.Name("MissionControlFocusRun"), object: nil, userInfo: userInfo)
    if preferReport {
      NotificationCenter.default.post(
        name: NSNotification.Name("MissionControlSelection"), object: nil,
        userInfo: userInfo)
    }
  }

  func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    willPresent notification: UNNotification,
    withCompletionHandler completionHandler:
      @escaping (UNNotificationPresentationOptions) -> Void
  ) {
    completionHandler([.banner, .list, .sound])
  }

  func userNotificationCenter(
    _ center: UNUserNotificationCenter,
    didReceive response: UNNotificationResponse,
    withCompletionHandler completionHandler: @escaping () -> Void
  ) {
    let info = response.notification.request.content.userInfo
    let runId = info["run_id"] as? String ?? ""
    let report = info["report"] as? String
    if !runId.isEmpty {
      let preferReport = response.actionIdentifier == Self.actionOpenReport
      DispatchQueue.main.async {
        self.present(runId: runId, report: report, preferReport: preferReport)
      }
    }
    completionHandler()
  }
}
