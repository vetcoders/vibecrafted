import Foundation

typealias ProductUpdateDataCompletion = @MainActor @Sendable (Result<Data, Error>) -> Void
typealias ProductUpdateURLCompletion = @MainActor @Sendable (Result<URL, Error>) -> Void
typealias ProductUpdateCancel = @Sendable () -> Void

private func productUpdateFinishOnMain<Value: Sendable>(
  _ result: Result<Value, Error>,
  _ completion: @escaping @MainActor @Sendable (Result<Value, Error>) -> Void
) {
  Task { @MainActor in
    completion(result)
  }
}

func productUpdateFetchBytes(
  _ url: URL,
  timeout: TimeInterval = 15,
  completion: @escaping ProductUpdateDataCompletion
) -> ProductUpdateCancel {
  if url.isFileURL {
    DispatchQueue.global(qos: .userInitiated).async {
      do {
        let data = try Data(contentsOf: url)
        productUpdateFinishOnMain(.success(data), completion)
      } catch {
        productUpdateFinishOnMain(.failure(error), completion)
      }
    }
    return {}
  }
  var request = URLRequest(url: url, timeoutInterval: timeout)
  request.cachePolicy = .reloadIgnoringLocalCacheData
  let task = URLSession.shared.dataTask(with: request) { data, response, error in
    if let error {
      productUpdateFinishOnMain(.failure(error), completion)
      return
    }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard let data, (200...299).contains(status) else {
      productUpdateFinishOnMain(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: status,
            userInfo: [
              NSLocalizedDescriptionKey: "update feed returned HTTP \(status)"
            ])),
        completion)
      return
    }
    productUpdateFinishOnMain(.success(data), completion)
  }
  task.resume()
  return { task.cancel() }
}

func productUpdateDownloadFile(
  _ url: URL,
  to destination: URL,
  completion: @escaping ProductUpdateURLCompletion
) -> ProductUpdateCancel {
  do {
    try FileManager.default.createDirectory(
      at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
  } catch {
    productUpdateFinishOnMain(.failure(error), completion)
    return {}
  }
  if url.isFileURL {
    DispatchQueue.global(qos: .userInitiated).async {
      do {
        if FileManager.default.fileExists(atPath: destination.path) {
          try FileManager.default.removeItem(at: destination)
        }
        try FileManager.default.copyItem(at: url, to: destination)
        productUpdateFinishOnMain(.success(destination), completion)
      } catch {
        productUpdateFinishOnMain(.failure(error), completion)
      }
    }
    return {}
  }
  let task = URLSession.shared.downloadTask(with: url) { location, response, error in
    if let error {
      productUpdateFinishOnMain(.failure(error), completion)
      return
    }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard let location, (200...299).contains(status) else {
      productUpdateFinishOnMain(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: status,
            userInfo: [NSLocalizedDescriptionKey: "update download returned HTTP \(status)"])),
        completion)
      return
    }
    do {
      if FileManager.default.fileExists(atPath: destination.path) {
        try FileManager.default.removeItem(at: destination)
      }
      try FileManager.default.moveItem(at: location, to: destination)
      productUpdateFinishOnMain(.success(destination), completion)
    } catch {
      productUpdateFinishOnMain(.failure(error), completion)
    }
  }
  task.resume()
  return { task.cancel() }
}

func productUpdateExtractApp(
  from dmg: URL,
  to destination: URL,
  completion: @escaping ProductUpdateURLCompletion
) -> ProductUpdateCancel {
  let cancelled = ProductUpdateCancelFlag()
  DispatchQueue.global(qos: .userInitiated).async {
    if cancelled.marked {
      productUpdateFinishOnMain(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: 7,
            userInfo: [NSLocalizedDescriptionKey: "opening the disk image was cancelled"])),
        completion)
      return
    }
    let mount = FileManager.default.temporaryDirectory.appendingPathComponent(
      "vc-update-dmg-\(UUID().uuidString)", isDirectory: true)
    try? FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true)
    let attach = runProductUpdateBoundProcess(
      executable: "/usr/bin/hdiutil",
      arguments: [
        "attach", dmg.path, "-nobrowse", "-readonly", "-mountpoint", mount.path,
      ],
      timeout: 90)
    defer {
      _ = runProductUpdateBoundProcess(
        executable: "/usr/bin/hdiutil",
        arguments: ["detach", mount.path, "-quiet"],
        timeout: 30)
    }
    switch attach {
    case .failure(let error):
      productUpdateFinishOnMain(.failure(error), completion)
      return
    case .success(let result):
      if cancelled.marked || result.timedOut || result.status != 0 {
        productUpdateFinishOnMain(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: 4,
              userInfo: [
                NSLocalizedDescriptionKey: result.timedOut
                  ? "opening the disk image timed out"
                  : "the downloaded disk image could not be opened"
              ])),
          completion)
        return
      }
    }
    let app = mount.appendingPathComponent("Vibecrafted.app")
    guard FileManager.default.fileExists(atPath: app.path),
      (try? app.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true
    else {
      productUpdateFinishOnMain(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: 5,
            userInfo: [
              NSLocalizedDescriptionKey: "the disk image does not contain Vibecrafted.app"
            ])),
        completion)
      return
    }
    if FileManager.default.fileExists(atPath: destination.path) {
      try? FileManager.default.removeItem(at: destination)
    }
    try? FileManager.default.createDirectory(
      at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
    let ditto = runProductUpdateBoundProcess(
      executable: "/usr/bin/ditto",
      arguments: [app.path, destination.path],
      timeout: 90)
    switch ditto {
    case .success(let result) where !result.timedOut && result.status == 0 && !cancelled.marked:
      productUpdateFinishOnMain(.success(destination), completion)
    default:
      productUpdateFinishOnMain(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: 6,
            userInfo: [
              NSLocalizedDescriptionKey: "the app could not be copied from the disk image"
            ])),
        completion)
    }
  }
  return { cancelled.mark() }
}
