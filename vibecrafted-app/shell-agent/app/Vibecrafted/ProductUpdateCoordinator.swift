import Foundation

/// Host for one in-app update check.
///
/// The coordinator never publishes a generation itself. Admission lives in
/// `ProductUpdatePolicy`. Publication, if any, is injected and must call the
/// existing Runtime Pack installer. A UI-only quit is requested only after a
/// healthy identity match. Nothing here stops Frame, PTYs, workers or config.
@MainActor
final class ProductUpdateCoordinator {
  struct Dependencies {
    var channel: () -> ProductUpdateChannel
    var installed: () -> ProductUpdateIdentity
    var fileExists: (String) -> Bool
    var fetchFeed: ((URL, @escaping (Result<Data, Error>) -> Void) -> Void)?
    var installCandidate:
      ((ProductUpdateCandidate, @escaping (Result<ProductUpdateIdentity, Error>) -> Void) -> Void)?
    var requestUIOnlyQuit: () -> Void
    var checkTimeout: TimeInterval
  }

  private let dependencies: Dependencies
  private(set) var progress: ProductUpdateProgress
  private var generation: UInt64 = 0
  private var busy = false
  var onProgress: ((ProductUpdateProgress) -> Void)?

  init(dependencies: Dependencies) {
    self.dependencies = dependencies
    self.progress = deriveProductUpdateProgress(
      phase: .idle, installed: dependencies.installed(), candidate: nil)
  }

  var isBusy: Bool { busy }

  func checkForUpdates() {
    if busy { return }
    busy = true
    generation += 1
    let token = generation
    let installed = dependencies.installed()
    publish(deriveProductUpdateProgress(phase: .checking, installed: installed, candidate: nil))
    let channel = dependencies.channel()
    if let gap = channel.provisioningGap {
      finish(
        deriveProductUpdateProgress(
          phase: .unavailable, installed: installed, candidate: nil, detail: gap),
        token: token)
      return
    }
    guard let feed = channel.feedURL, let fetch = dependencies.fetchFeed else {
      finish(
        deriveProductUpdateProgress(
          phase: .unavailable, installed: installed, candidate: nil,
          detail: "The update feed is not reachable from this App. The installed generation is unchanged."),
        token: token)
      return
    }
    var settled = false
    fetch(feed) { [weak self] result in
      Task { @MainActor in
        guard let self, self.generation == token else { return }
        settled = true
        self.consumeFeed(result, channel: channel, installed: installed, token: token)
      }
    }
    let timeout = dependencies.checkTimeout
    DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { [weak self] in
      guard let self, self.generation == token, self.busy, !settled else { return }
      self.generation += 1
      self.finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: nil,
          detail:
            "The update feed did not answer within \(Int(timeout))s. The installed generation is unchanged."),
        token: self.generation)
    }
  }

  /// Interruption while work is in flight keeps the previous generation.
  func interrupt() {
    guard busy else { return }
    generation += 1
    finish(
      deriveProductUpdateProgress(
        phase: .retained, installed: dependencies.installed(), candidate: nil,
        detail:
          "The update was interrupted. The previous working generation remains. The installer receipt was not replaced."),
      token: generation)
  }

  func quitUIOnly() {
    guard progress.requestsUIOnlyQuit else { return }
    dependencies.requestUIOnlyQuit()
  }

  private func consumeFeed(
    _ result: Result<Data, Error>,
    channel: ProductUpdateChannel,
    installed: ProductUpdateIdentity,
    token: UInt64
  ) {
    switch result {
    case .failure(let error):
      finish(
        deriveProductUpdateProgress(
          phase: .error, installed: installed, candidate: nil,
          detail: "The update feed failed: \(error.localizedDescription). The installed generation is unchanged."),
        token: token)
    case .success(let data):
      publish(deriveProductUpdateProgress(phase: .downloading, installed: installed, candidate: nil))
      let candidate: ProductUpdateCandidate
      do {
        candidate = try decodeProductUpdateFeed(data)
      } catch {
        finish(
          deriveProductUpdateProgress(
            phase: .refused, installed: installed, candidate: nil,
            detail: "The update feed was refused: \(error.localizedDescription). The installed generation is unchanged."),
          token: token)
        return
      }
      publish(
        deriveProductUpdateProgress(phase: .verifying, installed: installed, candidate: candidate))
      switch admitProductUpdateCandidate(
        channel: channel, candidate: candidate, fileExists: dependencies.fileExists)
      {
      case .unavailable(let reason):
        finish(
          deriveProductUpdateProgress(
            phase: .unavailable, installed: installed, candidate: candidate, detail: reason),
          token: token)
      case .refuse(let reason):
        finish(
          deriveProductUpdateProgress(
            phase: .refused, installed: installed, candidate: candidate, detail: reason),
          token: token)
      case .admit(let admitted):
        if productUpdateClaimsHealthy(installed: installed, candidate: admitted) {
          finish(
            deriveProductUpdateProgress(phase: .success, installed: installed, candidate: admitted),
            token: token)
          return
        }
        if !productUpdateRunningAppMatchesCandidate(installed: installed, candidate: admitted) {
          finish(
            deriveProductUpdateProgress(
              phase: .readyToReplace, installed: installed, candidate: admitted),
            token: token)
          return
        }
        guard let install = dependencies.installCandidate else {
          finish(
            deriveProductUpdateProgress(
              phase: .unavailable, installed: installed, candidate: admitted,
              detail:
                "The Runtime Pack installer is not wired for updates. The installed generation is unchanged."),
            token: token)
          return
        }
        publish(
          deriveProductUpdateProgress(
            phase: .installing, installed: installed, candidate: admitted))
        install(admitted) { [weak self] outcome in
          Task { @MainActor in
            guard let self, self.generation == token else { return }
            switch outcome {
            case .failure(let error):
              self.finish(
                deriveProductUpdateProgress(
                  phase: .retained, installed: self.dependencies.installed(), candidate: admitted,
                  detail:
                    "The installer did not publish the candidate (\(error.localizedDescription)). The previous working generation remains."),
                token: token)
            case .success(let published):
              if productUpdateClaimsHealthy(installed: published, candidate: admitted) {
                self.finish(
                  deriveProductUpdateProgress(
                    phase: .success, installed: published, candidate: admitted),
                  token: token)
              } else {
                self.finish(
                  deriveProductUpdateProgress(
                    phase: .refused, installed: published, candidate: admitted,
                    detail:
                      "The installer did not reconcile App and Runtime Pack identity. Healthy is not claimed. The receipt remains the authority."),
                  token: token)
              }
            }
          }
        }
      }
    }
  }

  private func publish(_ value: ProductUpdateProgress) {
    progress = value
    onProgress?(value)
  }

  private func finish(_ value: ProductUpdateProgress, token: UInt64) {
    guard generation == token else { return }
    busy = false
    publish(value)
  }
}
