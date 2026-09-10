import Foundation

func productUpdateFetchBytes(
  _ url: URL,
  timeout: TimeInterval = 15,
  completion: @escaping (Result<Data, Error>) -> Void
) -> () -> Void {
  if url.isFileURL {
    DispatchQueue.global(qos: .userInitiated).async {
      do {
        let data = try Data(contentsOf: url)
        DispatchQueue.main.async { completion(.success(data)) }
      } catch {
        DispatchQueue.main.async { completion(.failure(error)) }
      }
    }
    return {}
  }
  var request = URLRequest(url: url, timeoutInterval: timeout)
  request.cachePolicy = .reloadIgnoringLocalCacheData
  let task = URLSession.shared.dataTask(with: request) { data, response, error in
    if let error {
      DispatchQueue.main.async { completion(.failure(error)) }
      return
    }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard let data, (200...299).contains(status) else {
      DispatchQueue.main.async {
        completion(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: status,
              userInfo: [
                NSLocalizedDescriptionKey: "update feed returned HTTP \(status)"
              ])))
      }
      return
    }
    DispatchQueue.main.async { completion(.success(data)) }
  }
  task.resume()
  return { task.cancel() }
}

func productUpdateDownloadFile(
  _ url: URL,
  to destination: URL,
  completion: @escaping (Result<URL, Error>) -> Void
) -> () -> Void {
  do {
    try FileManager.default.createDirectory(
      at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
  } catch {
    completion(.failure(error))
    return {}
  }
  if url.isFileURL {
    DispatchQueue.global(qos: .userInitiated).async {
      do {
        if FileManager.default.fileExists(atPath: destination.path) {
          try FileManager.default.removeItem(at: destination)
        }
        try FileManager.default.copyItem(at: url, to: destination)
        DispatchQueue.main.async { completion(.success(destination)) }
      } catch {
        DispatchQueue.main.async { completion(.failure(error)) }
      }
    }
    return {}
  }
  let task = URLSession.shared.downloadTask(with: url) { location, response, error in
    if let error {
      DispatchQueue.main.async { completion(.failure(error)) }
      return
    }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard let location, (200...299).contains(status) else {
      DispatchQueue.main.async {
        completion(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: status,
              userInfo: [NSLocalizedDescriptionKey: "update download returned HTTP \(status)"])))
      }
      return
    }
    do {
      if FileManager.default.fileExists(atPath: destination.path) {
        try FileManager.default.removeItem(at: destination)
      }
      try FileManager.default.moveItem(at: location, to: destination)
      DispatchQueue.main.async { completion(.success(destination)) }
    } catch {
      DispatchQueue.main.async { completion(.failure(error)) }
    }
  }
  task.resume()
  return { task.cancel() }
}

func productUpdateExtractApp(
  from dmg: URL,
  to destination: URL,
  completion: @escaping (Result<URL, Error>) -> Void
) -> () -> Void {
  let cancelled = ProductUpdateCancelFlag()
  DispatchQueue.global(qos: .userInitiated).async {
    if cancelled.marked {
      DispatchQueue.main.async {
        completion(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: 7,
              userInfo: [NSLocalizedDescriptionKey: "opening the disk image was cancelled"])))
      }
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
      DispatchQueue.main.async { completion(.failure(error)) }
      return
    case .success(let result):
      if cancelled.marked || result.timedOut || result.status != 0 {
        DispatchQueue.main.async {
          completion(
            .failure(
              NSError(
                domain: "io.vetcoders.vibecrafted.update", code: 4,
                userInfo: [
                  NSLocalizedDescriptionKey: result.timedOut
                    ? "opening the disk image timed out"
                    : "the downloaded disk image could not be opened"
                ])))
        }
        return
      }
    }
    let app = mount.appendingPathComponent("Vibecrafted.app")
    guard FileManager.default.fileExists(atPath: app.path),
      (try? app.resourceValues(forKeys: [.isSymbolicLinkKey]).isSymbolicLink) != true
    else {
      DispatchQueue.main.async {
        completion(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: 5,
              userInfo: [
                NSLocalizedDescriptionKey: "the disk image does not contain Vibecrafted.app"
              ])))
      }
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
      DispatchQueue.main.async { completion(.success(destination)) }
    default:
      DispatchQueue.main.async {
        completion(
          .failure(
            NSError(
              domain: "io.vetcoders.vibecrafted.update", code: 6,
              userInfo: [
                NSLocalizedDescriptionKey: "the app could not be copied from the disk image"
              ])))
      }
    }
  }
  return { cancelled.mark() }
}
