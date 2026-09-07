import Foundation
import Observation

/// Observable state for the one Command Deck session.
///
/// The App owns the lifecycle of its UI, but not the runtime. Endpoint truth
/// arrives from the runtime's caretaker through `RuntimeEndpointResolver`.
@MainActor
@Observable
final class AppModel {
  enum State: Equatable {
    case bootstrapping
    case connecting
    case online(URL)
    case recovering(String)
    case blocked(String)
  }

  private(set) var state: State
  private(set) var endpoint: URL?
  private var lastLoadedURL: URL?
  private(set) var webState: WebConsoleLoadState = .idle
  var availableActions: Set<CommandDeckChromeAction> = [.showDiagnostics]
  @ObservationIgnored var endpointDidChange: ((URL?) -> Void)?

  /// Session callbacks are coordination plumbing, not renderable state.
  @ObservationIgnored private var stateChangeHandler: ((State) -> Void)?

  private let endpointResolver: RuntimeEndpointResolver

  init(
    state: State = .bootstrapping,
    endpointResolver: RuntimeEndpointResolver = RuntimeEndpointResolver()
  ) {
    self.state = state
    self.endpointResolver = endpointResolver
  }

  func beginConnecting() {
    transition(to: .connecting)
  }

  /// Consumes the App's current caretaker reading; it never probes a port or
  /// manufactures an endpoint of its own.
  func refreshEndpoint(caretakerData: Data?, runtimeReady: Bool) {
    switch endpointResolver.resolve(caretakerData: caretakerData, runtimeReady: runtimeReady) {
    case let .online(endpoint):
      let changed = self.endpoint != endpoint
      self.endpoint = endpoint
      if changed {
        lastLoadedURL = nil
        webState = .idle
        transition(to: .connecting)
        endpointDidChange?(endpoint)
      }
      projectWebState()
    case let .recovering(reason):
      invalidateEndpoint()
      transition(to: .recovering(reason))
    case let .blocked(reason):
      invalidateEndpoint()
      transition(to: .blocked(reason))
    }
  }

  /// Caretaker availability cannot clear a WebKit failure. Only a new
  /// navigation completion may make the combined presentation online.
  func receiveWebState(_ value: WebConsoleLoadState) {
    guard endpoint != nil else { return }
    webState = value
    projectWebState()
  }

  private func invalidateEndpoint() {
    guard endpoint != nil else { return }
    endpoint = nil
    lastLoadedURL = nil
    webState = .idle
    endpointDidChange?(nil)
  }

  private func projectWebState() {
    guard let endpoint else { return }
    switch webState {
    case .idle:
      lastLoadedURL = nil
      transition(to: .connecting)
    case .loading:
      // Ordinary same-owner navigation keeps the already displayed canvas.
      // A failure or owner loss clears this proof, so retry stays covered.
      if let lastLoadedURL { transition(to: .online(lastLoadedURL)) }
      else { transition(to: .connecting) }
    case .loaded(let url):
      guard WebRuntimeOrigin(url: url) == WebRuntimeOrigin(url: endpoint) else { return }
      lastLoadedURL = url
      transition(to: .online(url))
    case .failed(_, let reason), .interrupted(let reason):
      lastLoadedURL = nil
      transition(to: .recovering(reason))
    }
  }

  var isNavigating: Bool {
    if case .loading = webState { return lastLoadedURL != nil }
    return false
  }

  var presentation: CommandDeckPresentation {
    let phase: CommandDeckPhase
    var reason: String?
    switch state {
    case .bootstrapping: phase = .bootstrapping
    case .connecting: phase = .connecting
    case .online: phase = .online
    case .recovering(let detail): phase = .recovering; reason = detail
    case .blocked(let detail): phase = .blocked; reason = detail
    }
    return CommandDeckPresentation(
      phase: phase,
      problem: reason.map { CommandDeckProblem(
        title: "Connection needs attention", summary: "\($0)", receipt: nil) },
      endpointCaption: endpoint.map { ($0.host ?? "Runtime") + (isNavigating ? " · Loading" : "") },
      availableActions: availableActions)
  }

  func beginRecovery(reason: String) {
    transition(to: .recovering(reason))
  }

  func block(reason: String) {
    invalidateEndpoint()
    transition(to: .blocked(reason))
  }

  func setStateChangeHandler(_ handler: ((State) -> Void)?) {
    stateChangeHandler = handler
  }

  private func transition(to newState: State) {
    state = newState
    stateChangeHandler?(newState)
  }
}
