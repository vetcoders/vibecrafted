import Foundation

// This file is deliberately Foundation-only.
//
// The repository already proves its Swift policy layers cheaply: the tray's
// contract test compiles `ServerMenuPolicy.swift` together with a tiny harness
// through `swiftc` and asserts on real output. Importing WebKit or AppKit here
// would take that proof away, so every WebKit type is reduced to primitives at
// the call site and only the decision lives in this file. Keep it that way.
//
// The decisions below are the App's containment boundary. The embedded server
// UI owns dense navigation, but it does not get to decide what leaves the App,
// what runs, or where a file lands. That is native responsibility and it is
// expressed here as pure functions with no side effects.

/// A scheme/host/port triple the App treats as its own runtime.
///
/// Comparison is exact and port-normalised, so `http://127.0.0.1:3024` and
/// `http://127.0.0.1:3024/sessions` share an origin while
/// `http://127.0.0.1:3025` does not. No address is ever hardcoded; the origin
/// is always derived from the endpoint the resolver handed the App.
struct WebRuntimeOrigin: Equatable, Sendable {
  let scheme: String
  let host: String
  let port: Int

  /// Builds an origin from a resolved endpoint. Returns `nil` for anything
  /// that is not a well-formed http/https URL with a host.
  init?(url: URL) {
    guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
      return nil
    }
    guard let scheme = components.scheme?.lowercased() else { return nil }
    guard scheme == "http" || scheme == "https" else { return nil }
    guard let host = components.host?.lowercased(), !host.isEmpty else { return nil }
    guard components.user == nil, components.password == nil else { return nil }
    self.scheme = scheme
    self.host = host
    self.port = components.port ?? (scheme == "https" ? 443 : 80)
  }

  /// True when `url` belongs to this origin.
  func covers(_ url: URL) -> Bool {
    guard let other = WebRuntimeOrigin(url: url) else { return false }
    return other == self
  }
}

/// What the native host does with one navigation request.
enum WebNavigationDecision: Equatable, Sendable {
  /// Stays inside the App's single web session.
  case allowInApp
  /// Leaves through the system browser/mail client. Never through a shell.
  case openExternally(URL)
  /// Becomes an explicit download with a native destination flow.
  case startDownload
  /// Refused, with a reason the App can surface honestly.
  case block(reason: String)
}

enum WebResponseDecision: Equatable, Sendable {
  case allowInApp
  case startDownload
  /// A machine document (JSON, plain text, XML…) answered a main-frame
  /// navigation inside the console. The console keeps its current document
  /// and the App shows the response in a read-only reference tab instead.
  case divertToReferenceTab
  case block(reason: String)
}

/// What one native tab is for. The role decides how much a web document may
/// do inside it, never the other way round: a page cannot promote itself.
enum WebTabRole: String, Equatable, Sendable {
  /// The product console. Interactive, keeps the runtime session, and never
  /// lets a machine document replace its main document.
  case console
  /// An interactive tool surface (server page opened via `target=_blank`, a
  /// generated report). Same containment as the console, no runtime routing.
  case tool
  /// A read-only reference view for machine documents. Scripts are off.
  case reference

  var allowsContentJavaScript: Bool { self != .reference }
}

/// Where a `window.open` / `target=_blank` request goes. There is never a
/// WebKit-created second view; every new view is a native tab the App owns.
enum WebNewWindowDecision: Equatable, Sendable {
  case openInTab(URL, WebTabRole)
  case openExternally(URL)
  case startDownload(URL)
  case block(reason: String)
}

/// Disposition for an authentication challenge, expressed without WebKit.
enum WebAuthChallengeDecision: Equatable, Sendable {
  /// Hand the challenge back to the system: standard TLS validation, or the
  /// platform's own credential prompt.
  case performDefaultHandling
  /// Refuse the challenge without offering credentials.
  case cancel(reason: String)
}

/// Where a download is allowed to land.
enum WebDownloadDestinationDecision: Equatable, Sendable {
  case save(URL)
  case cancel(reason: String)
}

enum WebNavigationPolicy {
  /// Schemes that may be handed to the system when a top-level navigation
  /// leaves the runtime origin. Everything absent from this list is refused.
  ///
  /// This allowlist is the reason a link in the embedded UI cannot reach a
  /// shell: `file:`, `javascript:`, `data:`, and every custom application
  /// scheme fall through to `.block`, so they never reach `NSWorkspace`.
  static let externallyOpenableSchemes: Set<String> = ["http", "https"]

  /// Decides one navigation.
  ///
  /// - Parameters:
  ///   - url: the requested URL, `nil` when WebKit could not supply one.
  ///   - runtime: the origin currently serving the App, if one is resolved.
  ///   - isMainFrame: `false` for sub-frame navigations.
  ///   - shouldPerformDownload: WebKit's own signal that this is a download.
  static func decide(
    url: URL?,
    runtime: WebRuntimeOrigin?,
    isMainFrame: Bool,
    shouldPerformDownload: Bool
  ) -> WebNavigationDecision {
    guard let url else {
      return .block(reason: "a navigation arrived without a URL")
    }
    guard let scheme = URLComponents(url: url, resolvingAgainstBaseURL: false)?.scheme?.lowercased()
    else {
      return .block(reason: "a navigation arrived without a scheme")
    }

    // WebKit's own bookkeeping pages stay inside the session.
    if url.absoluteString == "about:blank", !shouldPerformDownload { return .allowInApp }

    // Blob URLs carry their originating origin in the resource specifier, and
    // the server UI uses them for client-generated files. Accept only blobs
    // minted by the runtime origin itself.
    if scheme == "blob" {
      guard let runtime else {
        return .block(reason: "a blob URL arrived before an endpoint was resolved")
      }
      let inner = String(url.absoluteString.dropFirst("blob:".count))
      guard let innerURL = URL(string: inner), runtime.covers(innerURL) else {
        return .block(reason: "a blob URL did not originate from the runtime")
      }
      // Top-level blobs are exports with an explicit native destination.
      return isMainFrame || shouldPerformDownload ? .startDownload : .allowInApp
    }

    if let runtime, runtime.covers(url) {
      return shouldPerformDownload ? .startDownload : .allowInApp
    }
    if shouldPerformDownload { return .block(reason: "downloads must originate from the runtime") }

    // Foreign sub-frames can never use cleartext transport. The sole allowed
    // HTTP origin is the caretaker-selected runtime above; a foreign iframe
    // must use HTTPS, so removing the broad WebContent ATS exemption does not
    // leave an application-level cleartext escape hatch.
    if !isMainFrame {
      guard scheme == "https" else {
        return .block(reason: "a foreign sub-frame requested the unsupported scheme '\(scheme)'")
      }
      return .allowInApp
    }

    guard externallyOpenableSchemes.contains(scheme) else {
      return .block(reason: "the scheme '\(scheme)' is not allowed to leave the App")
    }
    return .openExternally(url)
  }

  /// Responses must agree with the action boundary, including runtime blobs
  /// and permitted HTTP(S) subframes. Foreign main-frame redirects never load.
  static func decideResponse(
    url: URL?, runtime: WebRuntimeOrigin?, isMainFrame: Bool,
    canShowMIMEType: Bool, statusCode: Int?
  ) -> WebResponseDecision {
    decideResponse(url: url, runtime: runtime, isMainFrame: isMainFrame,
      canShowMIMEType: canShowMIMEType, statusCode: statusCode, mimeType: nil, role: .tool)
  }

  /// MIME types WebKit renders as a page the user works in. Everything else
  /// WebKit can show (JSON, plain text, XML, CSV…) is a machine document: a
  /// legitimate thing to read, never a replacement for the console's document.
  static func isInteractiveDocumentMIME(_ mimeType: String) -> Bool {
    let essence = mimeType.split(separator: ";").first.map {
      $0.trimmingCharacters(in: .whitespaces).lowercased()
    } ?? ""
    switch essence {
    case "text/html", "application/xhtml+xml", "image/svg+xml", "application/pdf":
      return true
    default:
      return essence.hasPrefix("image/") || essence.hasPrefix("video/") || essence.hasPrefix("audio/")
    }
  }

  /// Role-aware response decision. The route already passed `decide`; this is
  /// where the *response* MIME is allowed to change the outcome, so a UI route
  /// and an API endpoint on the same origin are told apart by what the server
  /// actually answered, not by guessing from the path.
  static func decideResponse(
    url: URL?, runtime: WebRuntimeOrigin?, isMainFrame: Bool,
    canShowMIMEType: Bool, statusCode: Int?, mimeType: String?, role: WebTabRole
  ) -> WebResponseDecision {
    guard let url else { return .block(reason: "response has no URL") }
    if isMainFrame, let statusCode, statusCode >= 400 {
      return .block(reason: "Server returned HTTP \(statusCode).")
    }
    let action = decide(url: url, runtime: runtime, isMainFrame: isMainFrame,
      shouldPerformDownload: !canShowMIMEType)
    switch action {
    case .allowInApp:
      if role == .console, isMainFrame, let mimeType, !isInteractiveDocumentMIME(mimeType) {
        return .divertToReferenceTab
      }
      return .allowInApp
    case .startDownload: return .startDownload
    case .openExternally: return .block(reason: "response left the runtime origin")
    case .block(let reason): return .block(reason: reason)
    }
  }

  /// Decides a `window.open` / `target=_blank` request from `role`.
  ///
  /// Same-origin pages become a native tool tab so the requesting document is
  /// never replaced. Foreign http(s) goes to the system browser. A tab may not
  /// open a tab of a more privileged role, and reference views open nothing.
  static func decideNewWindow(
    url: URL?, runtime: WebRuntimeOrigin?, role: WebTabRole, shouldPerformDownload: Bool
  ) -> WebNewWindowDecision {
    guard role != .reference else {
      return .block(reason: "a reference view may not open new windows")
    }
    switch decide(url: url, runtime: runtime, isMainFrame: true, shouldPerformDownload: shouldPerformDownload) {
    case .allowInApp:
      guard let url else { return .block(reason: "a navigation arrived without a URL") }
      guard url.absoluteString != "about:blank" else {
        return .block(reason: "an empty window has nothing to show")
      }
      return .openInTab(url, .tool)
    case .startDownload:
      guard let url else { return .block(reason: "a download arrived without a URL") }
      return .startDownload(url)
    case .openExternally(let external): return .openExternally(external)
    case .block(let reason): return .block(reason: reason)
    }
  }

  /// Navigation inside a tab that shows one local HTML document (a generated
  /// report). The tab may show that file and nothing else on disk: fragments
  /// and queries of the same file stay, http(s) links leave through the
  /// system browser, every other scheme and every sub-frame is refused.
  static func decideLocalDocumentNavigation(
    url: URL?, document: URL, isMainFrame: Bool, shouldPerformDownload: Bool
  ) -> WebNavigationDecision {
    guard let url else { return .block(reason: "a navigation arrived without a URL") }
    guard let scheme = URLComponents(url: url, resolvingAgainstBaseURL: false)?.scheme?.lowercased()
    else { return .block(reason: "a navigation arrived without a scheme") }
    if url.absoluteString == "about:blank", !shouldPerformDownload { return .allowInApp }
    guard isMainFrame else { return .block(reason: "a local document may not embed frames") }
    if shouldPerformDownload { return .block(reason: "a local document may not start downloads") }
    if scheme == "file" {
      let same = url.standardizedFileURL.path == document.standardizedFileURL.path
      return same ? .allowInApp : .block(reason: "a local document may only show itself")
    }
    guard externallyOpenableSchemes.contains(scheme) else {
      return .block(reason: "the scheme '\(scheme)' is not allowed to leave the App")
    }
    return .openExternally(url)
  }

  /// Decides an authentication challenge.
  ///
  /// Server trust is always handed back to the system: this App never builds a
  /// credential from a trust object, so there is no code path that accepts an
  /// invalid certificate. Credential prompts are confined to the runtime
  /// origin, so a foreign host cannot harvest them.
  static func decideAuthenticationChallenge(
    method: String,
    host: String,
    port: Int,
    scheme: String?,
    runtime: WebRuntimeOrigin?
  ) -> WebAuthChallengeDecision {
    switch method {
    case NSURLAuthenticationMethodServerTrust:
      return .performDefaultHandling
    case NSURLAuthenticationMethodClientCertificate:
      return .cancel(reason: "client certificate authentication is not part of the runtime contract")
    case NSURLAuthenticationMethodHTTPBasic,
      NSURLAuthenticationMethodHTTPDigest,
      NSURLAuthenticationMethodNTLM,
      NSURLAuthenticationMethodNegotiate:
      guard let runtime else {
        return .cancel(reason: "credentials were requested before an endpoint was resolved")
      }
      guard runtime.host == host.lowercased(), runtime.port == port,
        scheme?.lowercased() == runtime.scheme else {
        return .cancel(reason: "credentials were requested by a host outside the runtime origin")
      }
      return .performDefaultHandling
    default:
      return .performDefaultHandling
    }
  }

  /// Reduces a server-suggested filename to something safe to write.
  ///
  /// Returns `nil` when nothing usable survives, so the caller falls back to a
  /// neutral name rather than inventing one from untrusted input.
  static func sanitizedDownloadFilename(_ suggested: String) -> String? {
    var scalars = String.UnicodeScalarView()
    for scalar in suggested.unicodeScalars {
      if CharacterSet.controlCharacters.contains(scalar) {
        continue
      }
      // Path separators and the HFS separator would let a suggested name walk
      // out of the chosen directory.
      if scalar == "/" || scalar == "\\" || scalar == ":" {
        scalars.append("_")
        continue
      }
      scalars.append(scalar)
    }

    var name = String(scalars).trimmingCharacters(in: .whitespacesAndNewlines)
    // A leading dot would silently hide the file; `.` and `..` are directories.
    while name.hasPrefix(".") {
      name.removeFirst()
    }
    name = name.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !name.isEmpty else { return nil }

    // Keep the name inside the common 255-byte filesystem limit without
    // splitting a grapheme.
    while name.utf8.count > 255, !name.isEmpty {
      name.removeLast()
    }
    return name.isEmpty ? nil : name
  }

  /// Picks a concrete destination inside `directory`.
  ///
  /// `fileExists` is injected so the choice can be proven without touching a
  /// filesystem. An existing file is never overwritten: a numbered sibling is
  /// used instead, because a download must not be able to destroy the user's
  /// data on its own.
  static func downloadDestination(
    directory: URL,
    suggestedFilename: String,
    fallbackName: String = "download",
    fileExists: (URL) -> Bool
  ) -> WebDownloadDestinationDecision {
    let safeName = sanitizedDownloadFilename(suggestedFilename) ?? fallbackName
    let candidate = directory.appendingPathComponent(safeName)
    if !fileExists(candidate) {
      return .save(candidate)
    }

    let base = candidate.deletingPathExtension().lastPathComponent
    let ext = candidate.pathExtension
    for index in 1...999 {
      let numbered = ext.isEmpty ? "\(base)-\(index)" : "\(base)-\(index).\(ext)"
      let attempt = directory.appendingPathComponent(numbered)
      if !fileExists(attempt) {
        return .save(attempt)
      }
    }
    return .cancel(reason: "no free filename was available for '\(safeName)'")
  }
}
