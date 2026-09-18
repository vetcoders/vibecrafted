import Foundation

/// How the App starts the product multiplexer web client (`vc-frame web`).
///
/// Tabs never spawn this service. AppDelegate may, and only when the operator
/// has named a loopback `http` origin in `[tools.vc-frame]`. The bind host and
/// port come from that URL — no default port is invented.
enum FrameWebLaunch {
  struct Bind: Equatable, Sendable {
    var host: String
    var port: Int

    /// Native `vc-frame` argv. Product entry passthroughs the `web` subcommand.
    var startArguments: [String] {
      ["web", "--ip", host, "--port", String(port)]
    }
  }

  /// `nil` unless `url` is loopback HTTP with a host. HTTPS and remote hosts
  /// stay openable as a tab; the App never binds a listener for them.
  static func bind(url: URL) -> Bind? {
    guard let origin = WebRuntimeOrigin(url: url) else { return nil }
    guard origin.scheme == "http" else { return nil }
    guard isLoopback(origin.host) else { return nil }
    guard (1...65535).contains(origin.port) else { return nil }
    return Bind(host: origin.host, port: origin.port)
  }

  static func isLoopback(_ host: String) -> Bool {
    host == "127.0.0.1" || host == "localhost" || host == "::1"
  }
}
