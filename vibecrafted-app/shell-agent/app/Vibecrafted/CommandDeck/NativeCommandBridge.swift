import Foundation

/// The complete native action allowlist. No payload may supply argv, environment, an
/// executable, or a pre-confirmed stop. Quit App is deliberately not a command.
enum NativeCommand: Equatable, Sendable {
  case openTerminal(TerminalPayload)
  case openExternalURL(ExternalURLPayload)
  case revealPath(PathPayload)
  case retryConnection
  case requestRuntimeStop

  struct PathPayload: Equatable, Sendable {
    let url: URL

    init(path: String) throws {
      guard path.hasPrefix("/"), !path.hasPrefix("//"), path.utf8.count <= 4096,
        !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
        !path.split(separator: "/").contains("..")
      else { throw NativeCommandError.invalidPath }
      url = URL(fileURLWithPath: path)
    }
  }

  struct TerminalPayload: Equatable, Sendable {
    let workingDirectory: PathPayload
  }

  struct ExternalURLPayload: Equatable, Sendable {
    let url: URL

    init(value: String) throws {
      guard value.utf8.count <= 16384,
        !value.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.contains($0)
          || CharacterSet.controlCharacters.contains($0) }),
        let components = URLComponents(string: value),
        let scheme = components.scheme?.lowercased(), ["https", "http"].contains(scheme),
        let host = components.host, !host.isEmpty,
        components.user == nil, components.password == nil,
        let url = URL(string: value, encodingInvalidCharacters: false)
      else { throw NativeCommandError.invalidURL }
      self.url = url
    }
  }

}

enum NativeCommandError: Error {
  case invalidURL
  case invalidPath
  case pathNotAllowed
}

/// Native-only typed dispatch. No WebKit request channel calls this bridge.
/// Views and web content never acquire runtime process ownership.
@MainActor
struct NativeCommandBridge {
  struct Actions {
    /// Resolve/adopt through AppDelegate, then construct TerminalLauncher.Specification.
    let openTerminal: (URL) throws -> Void
    let openExternalURL: (URL) throws -> Void
    let revealPath: (URL) throws -> Void
    let retryConnection: () throws -> Void
    /// Must present native confirmation with current active consequences.
    let confirmRuntimeStop: () throws -> Bool
    /// Route to the existing runtime owner only after confirmation.
    let stopRuntime: () throws -> Void
  }

  /// Native-selected workspace/artifact roots, never provided by the request.
  let allowedPathRoots: [URL]
  let actions: Actions

  func perform(_ command: NativeCommand) throws {
    switch command {
    case .openTerminal(let payload):
      let directory = try validatedPath(payload.workingDirectory, directoryOnly: true)
      try actions.openTerminal(directory)
    case .openExternalURL(let payload):
      try actions.openExternalURL(payload.url)
    case .revealPath(let payload):
      try actions.revealPath(validatedPath(payload, directoryOnly: false))
    case .retryConnection:
      try actions.retryConnection()
    case .requestRuntimeStop:
      if try actions.confirmRuntimeStop() { try actions.stopRuntime() }
    }
  }

  private func validatedPath(_ payload: NativeCommand.PathPayload, directoryOnly: Bool) throws -> URL {
    // Revalidate at dispatch, not only construction: files and symlinks can change
    // while a request waits. Return the resolved path to the native handler.
    let candidate = payload.url.resolvingSymlinksInPath().standardizedFileURL
    var isDirectory: ObjCBool = false
    guard FileManager.default.fileExists(atPath: candidate.path, isDirectory: &isDirectory),
      !directoryOnly || isDirectory.boolValue
    else { throw NativeCommandError.invalidPath }
    let allowed = allowedPathRoots.contains { root in
      guard root.isFileURL, root.baseURL == nil,
        root.host == nil || root.host == "" || root.host == "localhost"
      else { return false }
      let parts = root.resolvingSymlinksInPath().standardizedFileURL.pathComponents
      return candidate.pathComponents.starts(with: parts)
    }
    guard allowed else { throw NativeCommandError.pathNotAllowed }
    return candidate
  }
}
