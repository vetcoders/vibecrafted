import Foundation

/// Resolves a Command Deck endpoint from the App's existing caretaker truth.
///
/// It deliberately delegates URL validation and caretaker availability policy
/// to `resolveServerNavigation(caretakerData:)`, the same owner used by the
/// existing tray. Dependency injection keeps this leaf testable without a
/// process, network request, or running runtime.
struct RuntimeEndpointResolver {
  enum Resolution: Equatable {
    case online(URL)
    case recovering(String)
    case blocked(String)
  }

  typealias CaretakerNavigationResolver = (Data?) -> ServerNavigationState

  private let resolveNavigation: CaretakerNavigationResolver

  init(
    resolveNavigation: @escaping CaretakerNavigationResolver = resolveServerNavigation
  ) {
    self.resolveNavigation = resolveNavigation
  }

  func resolve(caretakerData: Data?, runtimeReady: Bool) -> Resolution {
    guard runtimeReady else {
      return .blocked("The installed runtime is not available to the Command Deck.")
    }

    let navigation = resolveNavigation(caretakerData)
    guard let endpoint = navigation.server else {
      return .recovering(
        navigation.unavailableReason
          ?? "The runtime caretaker has not published a usable server endpoint.")
    }

    return .online(endpoint)
  }
}
