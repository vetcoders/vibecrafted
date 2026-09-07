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

  var endpoint: URL? {
    guard case let .online(endpoint) = state else { return nil }
    return endpoint
  }

  func beginConnecting() {
    transition(to: .connecting)
  }

  /// Consumes the App's current caretaker reading; it never probes a port or
  /// manufactures an endpoint of its own.
  func refreshEndpoint(caretakerData: Data?, runtimeReady: Bool) {
    switch endpointResolver.resolve(caretakerData: caretakerData, runtimeReady: runtimeReady) {
    case let .online(endpoint):
      transition(to: .online(endpoint))
    case let .recovering(reason):
      transition(to: .recovering(reason))
    case let .blocked(reason):
      transition(to: .blocked(reason))
    }
  }

  func beginRecovery(reason: String) {
    transition(to: .recovering(reason))
  }

  func block(reason: String) {
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
