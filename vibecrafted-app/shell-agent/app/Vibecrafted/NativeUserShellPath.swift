import Darwin
import Foundation

/// Native adapter for the shared product contract, not a second shell resolver.
/// The shell is only executed by product_contract.py; this owns asynchronous
/// delivery and caches its PATH across the App's short-lived Python children.
@MainActor
final class NativeUserShellPath {
  typealias Runner = (Process, TimeInterval,
    @escaping @MainActor @Sendable (BoundedProcessResult) -> Void) throws -> Void

  private struct Fingerprint: Hashable {
    let generation: String
    let inputs: [String]
    let files: [String]
  }

  private struct Receipt: Decodable { let path: String }
  private struct Flight {
    let process: Process
    var waiters: [(String) -> Void]
  }

  private let run: Runner
  private var cache: (Fingerprint, String)?
  private var flights: [Fingerprint: Flight] = [:]

  init(run: @escaping Runner) { self.run = run }

  func cachedPath(generation: URL, environment: [String: String]) -> String? {
    let key = fingerprint(generation: generation, environment: environment)
    return cache?.0 == key ? cache?.1 : nil
  }

  func resolve(
    generation: URL, environment: [String: String], completion: @escaping (String) -> Void
  ) {
    let key = fingerprint(generation: generation, environment: environment)
    if cache?.0 == key, let path = cache?.1 {
      completion(path)
      return
    }
    // A single generation normally has one key. Keep distinct in-flight keys
    // separate so publication or rc drift cannot adopt an older probe answer.
    if flights[key] != nil {
      flights[key]?.waiters.append(completion)
      return
    }
    let process = Process()
    process.executableURL = generation.appendingPathComponent("bin/python3")
    process.arguments = [
      "-c",
      "import json,os,sys; sys.path.insert(0,sys.argv[1]); "
        + "from vibecrafted_core.product_contract import resolve_login_shell_path; "
        + "print(json.dumps({'path':resolve_login_shell_path(os.environ)}))",
      generation.appendingPathComponent("vibecrafted-core").path,
    ]
    process.environment = probeEnvironment(environment)
    flights[key] = Flight(process: process, waiters: [completion])
    let fallback = environment["PATH"] ?? ""
    do {
      try run(process, 7) { [weak self] result in
        guard let self else { return }
        let path: String
        if result.clean, !result.timedOut, result.terminationStatus == 0,
          let receipt = try? JSONDecoder().decode(Receipt.self, from: result.stdout),
          !receipt.path.isEmpty, !receipt.path.contains("\0"), !receipt.path.contains("\n") {
          path = receipt.path
        } else {
          path = fallback
        }
        self.finish(key: key, path: path)
      }
    } catch {
      finish(key: key, path: fallback)
    }
  }

  private func finish(key: Fingerprint, path: String) {
    let waiters = flights.removeValue(forKey: key)?.waiters ?? []
    cache = (key, path)
    waiters.forEach { $0(path) }
  }

  private func probeEnvironment(_ host: [String: String]) -> [String: String] {
    var environment = host
    for name in ["PYTHONPATH", "PYTHONHOME", "__PYVENV_LAUNCHER__"] {
      environment.removeValue(forKey: name)
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment
  }

  private func fingerprint(generation: URL, environment: [String: String]) -> Fingerprint {
    let home = environment["HOME"] ?? FileManager.default.homeDirectoryForCurrentUser.path
    let shell = environment["SHELL"] ?? "/bin/zsh"
    let config = environment["XDG_CONFIG_HOME"] ?? "\(home)/.config"
    let dotdirs = [home, environment["ZDOTDIR"], environment["VIBECRAFTED_USER_ZDOTDIR"]]
      .compactMap { $0 }
    var paths = [shell, "/etc/profile", "/etc/zshenv", "/etc/zprofile", "/etc/zshrc",
      "/etc/zlogin", "/etc/paths", "\(config)/fish/config.fish"]
    for directory in dotdirs {
      paths += [".zshenv", ".zprofile", ".zshrc", ".zlogin", ".profile", ".bash_profile",
        ".bash_login", ".bashrc"].map { "\(directory)/\($0)" }
    }
    for directory in ["/etc/paths.d", "\(config)/fish/conf.d"] {
      paths.append(directory)
      paths += (try? FileManager.default.contentsOfDirectory(atPath: directory))?.sorted()
        .map { "\(directory)/\($0)" } ?? []
    }
    // stat follows rc symlinks: changing their target must invalidate too.
    let files = paths.map { path -> String in
      var metadata = stat()
      guard stat(path, &metadata) == 0 else { return "\(path):missing" }
      return "\(path):\(metadata.st_dev):\(metadata.st_ino):\(metadata.st_size):"
        + "\(metadata.st_mtimespec.tv_sec):\(metadata.st_mtimespec.tv_nsec):"
        + "\(metadata.st_ctimespec.tv_sec):\(metadata.st_ctimespec.tv_nsec)"
    }
    let inputs = ["SHELL", "HOME", "PATH", "ZDOTDIR", "VIBECRAFTED_USER_ZDOTDIR",
      "XDG_CONFIG_HOME", "VIBECRAFTED_ROOT", "VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_HOME"]
      .map { "\($0)=\(environment[$0] ?? "")" }
    return Fingerprint(generation: generation.path, inputs: inputs, files: files)
  }
}
