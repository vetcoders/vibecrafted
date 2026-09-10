import Foundation

func productUpdateFetchBytes(
  _ url: URL,
  timeout: TimeInterval = 15,
  completion: @escaping (Result<Data, Error>) -> Void
) -> () -> Void {
  if url.isFileURL {
    do {
      let data = try Data(contentsOf: url)
      completion(.success(data))
    } catch {
      completion(.failure(error))
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
    do {
      if FileManager.default.fileExists(atPath: destination.path) {
        try FileManager.default.removeItem(at: destination)
      }
      try FileManager.default.copyItem(at: url, to: destination)
      completion(.success(destination))
    } catch {
      completion(.failure(error))
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
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
  let mount = FileManager.default.temporaryDirectory.appendingPathComponent(
    "vc-update-dmg-\(UUID().uuidString)", isDirectory: true)
  process.arguments = [
    "attach", dmg.path, "-nobrowse", "-readonly", "-mountpoint", mount.path,
  ]
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  do { try process.run() } catch {
    completion(.failure(error))
    return {}
  }
  process.waitUntilExit()
  guard process.terminationStatus == 0 else {
    completion(
      .failure(
        NSError(
          domain: "io.vetcoders.vibecrafted.update", code: 4,
          userInfo: [NSLocalizedDescriptionKey: "the downloaded disk image could not be opened"])))
    return {}
  }
  defer {
    let detach = Process()
    detach.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
    detach.arguments = ["detach", mount.path, "-quiet"]
    detach.standardOutput = Pipe()
    detach.standardError = Pipe()
    try? detach.run()
    detach.waitUntilExit()
  }
  let app = mount.appendingPathComponent("Vibecrafted.app")
  guard FileManager.default.fileExists(atPath: app.path) else {
    completion(
      .failure(
        NSError(
          domain: "io.vetcoders.vibecrafted.update", code: 5,
          userInfo: [NSLocalizedDescriptionKey: "the disk image does not contain Vibecrafted.app"])))
    return {}
  }
  do {
    if FileManager.default.fileExists(atPath: destination.path) {
      try FileManager.default.removeItem(at: destination)
    }
    try FileManager.default.createDirectory(
      at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
    let ditto = Process()
    ditto.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
    ditto.arguments = [app.path, destination.path]
    ditto.standardOutput = Pipe()
    ditto.standardError = Pipe()
    try ditto.run()
    ditto.waitUntilExit()
    if ditto.terminationStatus == 0 {
      completion(.success(destination))
    } else {
      completion(
        .failure(
          NSError(
            domain: "io.vetcoders.vibecrafted.update", code: 6,
            userInfo: [NSLocalizedDescriptionKey: "the app could not be copied from the disk image"])))
    }
  } catch {
    completion(.failure(error))
  }
  return {
    if process.isRunning { process.terminate() }
  }
}
