import AppKit
import WebKit

/// Outcome callbacks for downloads started by the embedded console.
struct WebDownloadCoordinatorEvents {
  var didFinish: @MainActor (URL) -> Void = { _ in }
  var didFail: @MainActor (String) -> Void = { _ in }
}

/// Asks the user where a download should land.
///
/// Injectable so the destination flow can be exercised without a panel, and so
/// a caller can substitute a policy-only flow. The default implementation is a
/// real `NSSavePanel`: a download from the embedded server never picks its own
/// location silently.
typealias WebDownloadDestinationProvider = @MainActor (
  _ suggestedFilename: String,
  _ defaultDirectory: URL,
  _ window: NSWindow?
) async -> URL?

/// Owns every `WKDownload` the embedded console produces.
///
/// Downloads are the one place where a web page can write to the user's disk,
/// so the whole path is explicit here: the filename is sanitised by
/// `WebNavigationPolicy`, the destination is chosen by the user, an existing
/// file is never overwritten, and credentials are only offered back to the
/// runtime origin.
@MainActor
final class WebDownloadCoordinator: NSObject {
  var events = WebDownloadCoordinatorEvents()
  var destinationProvider: WebDownloadDestinationProvider

  /// Downloads in flight. `WKDownload.delegate` is weak and the coordinator
  /// must outlive the transfer, so each one is retained until it settles.
  private var active: [ObjectIdentifier: WKDownload] = [:]
  private var hostWindows: [ObjectIdentifier: NSWindow] = [:]
  private var origins: [ObjectIdentifier: WebRuntimeOrigin] = [:]
  private var destinations: [ObjectIdentifier: URL] = [:]

  static let fallbackFilename = "download"

  init(
    destinationProvider: @escaping WebDownloadDestinationProvider = WebDownloadCoordinator
      .savePanelDestinationProvider
  ) {
    self.destinationProvider = destinationProvider
    super.init()
  }

  /// Adopts a download WebKit just handed over.
  func take(_ download: WKDownload, relativeTo window: NSWindow?, runtime: WebRuntimeOrigin?) {
    let key = ObjectIdentifier(download)
    download.delegate = self
    active[key] = download
    hostWindows[key] = window
    origins[key] = runtime
  }

  private func settle(_ key: ObjectIdentifier) {
    active[key] = nil
    hostWindows[key] = nil
    origins[key] = nil
    destinations[key] = nil
  }

  static var defaultDownloadsDirectory: URL {
    FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
      ?? FileManager.default.homeDirectoryForCurrentUser
  }

  /// The default explicit destination flow.
  static let savePanelDestinationProvider: WebDownloadDestinationProvider = {
    suggestedFilename, defaultDirectory, window in
    let panel = NSSavePanel()
    panel.nameFieldStringValue = suggestedFilename
    panel.directoryURL = defaultDirectory
    panel.canCreateDirectories = true
    panel.isExtensionHidden = false
    panel.message = "Choose where to save this file from the Vibecrafted server."
    let response = await WebDownloadCoordinator.presentPanel(panel, in: window)
    guard response == .OK else { return nil }
    return panel.url
  }

  /// Presents a panel as a sheet when there is a window, and modally when the
  /// App has none yet. Shared with the console host's file-upload panel.
  static func presentPanel(
    _ panel: NSSavePanel,
    in window: NSWindow?
  ) async -> NSApplication.ModalResponse {
    await withCheckedContinuation { continuation in
      if let window {
        panel.beginSheetModal(for: window) { response in
          continuation.resume(returning: response)
        }
      } else {
        panel.begin { response in
          continuation.resume(returning: response)
        }
      }
    }
  }
}

extension WebDownloadCoordinator: WKDownloadDelegate {
  func download(
    _ download: WKDownload,
    decideDestinationUsing response: URLResponse,
    suggestedFilename: String
  ) async -> URL? {
    let key = ObjectIdentifier(download)
    let suggestion =
      WebNavigationPolicy.sanitizedDownloadFilename(suggestedFilename) ?? Self.fallbackFilename

    guard
      let chosen = await destinationProvider(
        suggestion, Self.defaultDownloadsDirectory, hostWindows[key])
    else {
      events.didFail("the download of '\(suggestion)' was cancelled")
      settle(key)
      return nil
    }

    // WebKit refuses a destination that already exists, and a download must
    // not be able to destroy the user's data on its own. Re-running the policy
    // over the chosen URL yields a numbered sibling instead of an overwrite.
    let decision = WebNavigationPolicy.downloadDestination(
      directory: chosen.deletingLastPathComponent(),
      suggestedFilename: chosen.lastPathComponent,
      fallbackName: Self.fallbackFilename,
      fileExists: { FileManager.default.fileExists(atPath: $0.path) }
    )
    switch decision {
    case .save(let destination):
      destinations[key] = destination
      return destination
    case .cancel(let reason):
      events.didFail(reason)
      settle(key)
      return nil
    }
  }

  func download(
    _ download: WKDownload,
    respondTo challenge: URLAuthenticationChallenge
  ) async -> (URLSession.AuthChallengeDisposition, URLCredential?) {
    let key = ObjectIdentifier(download)
    let space = challenge.protectionSpace
    let decision = WebNavigationPolicy.decideAuthenticationChallenge(
      method: space.authenticationMethod,
      host: space.host,
      port: space.port,
      scheme: space.protocol,
      runtime: origins[key]
    )
    switch decision {
    case .performDefaultHandling:
      return (.performDefaultHandling, nil)
    case .cancel(let reason):
      events.didFail(reason)
      return (.cancelAuthenticationChallenge, nil)
    }
  }

  func download(
    _ download: WKDownload,
    willPerformHTTPRedirection response: HTTPURLResponse,
    newRequest request: URLRequest
  ) async -> WKDownload.RedirectPolicy {
    let key = ObjectIdentifier(download)
    let decision = WebNavigationPolicy.decide(url: request.url, runtime: origins[key],
      isMainFrame: true, shouldPerformDownload: true)
    guard case .startDownload = decision else {
      events.didFail("the download redirected outside the runtime origin")
      settle(key)
      return .cancel
    }
    return .allow
  }

  func downloadDidFinish(_ download: WKDownload) {
    let key = ObjectIdentifier(download)
    if let destination = destinations[key] {
      events.didFinish(destination)
    }
    settle(key)
  }

  func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
    let key = ObjectIdentifier(download)
    guard active[key] != nil else { return }
    events.didFail((error as NSError).localizedDescription)
    settle(key)
  }
}
