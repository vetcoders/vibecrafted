import Foundation

enum ProductUpdateTrustError: Error, Equatable {
  case missingPublicKey
  case invalidSignatureSize
  case signatureInvalid
  case opensslUnavailable
  case hashMismatch
  case verifierFailed(String)
  case codesignFailed
  case notarizationFailed
  case pythonMissing
  case timedOut

  var localizedDescription: String {
    switch self {
    case .missingPublicKey: return "The bundled signing key is missing."
    case .invalidSignatureSize: return "The detached signature is the wrong size."
    case .signatureInvalid: return "The detached signature is not valid for these bytes."
    case .opensslUnavailable: return "The system signature checker is unavailable."
    case .hashMismatch: return "A downloaded file does not match its signed hash."
    case .verifierFailed(let reason): return reason
    case .codesignFailed: return "The app signature is not the expected Vibecrafted identity."
    case .notarizationFailed: return "The app is not notarized and stapled."
    case .pythonMissing:
      return "The signed release checker is not installed with this App."
    case .timedOut: return "A verification tool did not finish in time."
    }
  }
}

struct ProductUpdateCodesignIdentity: Equatable, Sendable {
  var identifier: String
  var teamID: String
}

/// Same check as `product_contract._verify_release_signature`: `/usr/bin/openssl dgst
/// -sha256 -verify` with the pinned bundled public key over the exact payload bytes.
func verifyDetachedReleaseSignature(
  payload: Data,
  signature: Data,
  publicKeyPath: String,
  opensslPath: String = "/usr/bin/openssl",
  timeout: TimeInterval = 20
) -> Result<Void, ProductUpdateTrustError> {
  guard FileManager.default.isReadableFile(atPath: publicKeyPath) else {
    return .failure(.missingPublicKey)
  }
  guard signature.count == productUpdateSignatureBytes else {
    return .failure(.invalidSignatureSize)
  }
  let directory = FileManager.default.temporaryDirectory
    .appendingPathComponent("vibecrafted-update-sig-\(UUID().uuidString)", isDirectory: true)
  do {
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: directory) }
    let signatureFile = directory.appendingPathComponent("release-output.json.sig")
    try signature.write(to: signatureFile, options: .atomic)
    try FileManager.default.setAttributes(
      [.posixPermissions: 0o400], ofItemAtPath: signatureFile.path)
    switch runProductUpdateBoundProcess(
      executable: opensslPath,
      arguments: [
        "dgst", "-sha256", "-verify", publicKeyPath, "-signature", signatureFile.path,
      ],
      stdin: payload,
      timeout: timeout)
    {
    case .failure(.executableMissing), .failure(.startFailed):
      return .failure(.opensslUnavailable)
    case .failure:
      return .failure(.opensslUnavailable)
    case .success(let result):
      if result.timedOut { return .failure(.timedOut) }
      return result.status == 0 ? .success(()) : .failure(.signatureInvalid)
    }
  } catch {
    return .failure(.opensslUnavailable)
  }
}

func verifyProductUpdatePayloadDigest(
  fileURL: URL,
  expectedSHA256: String,
  expectedSize: Int,
  opensslPath: String = "/usr/bin/openssl",
  timeout: TimeInterval = 20
) -> Result<Void, ProductUpdateTrustError> {
  guard let values = try? FileManager.default.attributesOfItem(atPath: fileURL.path),
    let size = values[.size] as? NSNumber, size.intValue == expectedSize
  else {
    return .failure(.hashMismatch)
  }
  guard let hex = sha256HexOfFile(at: fileURL, opensslPath: opensslPath, timeout: timeout) else {
    return .failure(.opensslUnavailable)
  }
  return hex == expectedSHA256.lowercased() ? .success(()) : .failure(.hashMismatch)
}

func sha256HexOfFile(
  at url: URL, opensslPath: String = "/usr/bin/openssl", timeout: TimeInterval = 20
) -> String? {
  switch runProductUpdateBoundProcess(
    executable: opensslPath,
    arguments: ["dgst", "-sha256", "-r", url.path],
    timeout: timeout)
  {
  case .failure:
    return nil
  case .success(let result):
    guard !result.timedOut, result.status == 0 else { return nil }
    let text = String(data: result.stdout, encoding: .utf8) ?? ""
    let hex = text.split(separator: " ").first.map(String.init) ?? ""
    return hex.count == 64 ? hex.lowercased() : nil
  }
}

func observeProductUpdateCodesignIdentity(
  appURL: URL,
  codesignPath: String = "/usr/bin/codesign",
  timeout: TimeInterval = 20
) -> ProductUpdateCodesignIdentity? {
  switch runProductUpdateBoundProcess(
    executable: codesignPath,
    arguments: ["--verify", "--strict", "--verbose=4", appURL.path],
    timeout: timeout)
  {
  case .failure:
    return nil
  case .success(let verified):
    guard !verified.timedOut, verified.status == 0 else { return nil }
  }
  switch runProductUpdateBoundProcess(
    executable: codesignPath,
    arguments: ["--display", "--verbose=4", appURL.path],
    timeout: timeout)
  {
  case .failure:
    return nil
  case .success(let result):
    guard !result.timedOut, result.status == 0 else { return nil }
    let text = String(data: result.stderr + result.stdout, encoding: .utf8) ?? ""
    var identifier: String?
    var team: String?
    for line in text.split(separator: "\n") {
      if line.hasPrefix("Identifier=") {
        identifier = String(line.dropFirst("Identifier=".count))
      }
      if line.hasPrefix("TeamIdentifier=") {
        team = String(line.dropFirst("TeamIdentifier=".count))
      }
    }
    guard let identifier, let team else { return nil }
    return ProductUpdateCodesignIdentity(identifier: identifier, teamID: team)
  }
}

func observeProductUpdateStapledNotarization(
  appURL: URL,
  staplerLauncher: String = "/usr/bin/xcrun",
  timeout: TimeInterval = 30
) -> Bool {
  switch runProductUpdateBoundProcess(
    executable: staplerLauncher,
    arguments: ["stapler", "validate", appURL.path],
    timeout: timeout)
  {
  case .failure:
    return false
  case .success(let result):
    return !result.timedOut && result.status == 0
  }
}

/// Established owner: `python -m vibecrafted_core.product_contract release-output`.
func invokeReleaseOutputVerifier(
  releaseOutput: URL,
  signature: URL,
  python: URL,
  extraEnvironment: [String: String] = [:],
  timeout: TimeInterval = 180
) -> Result<Void, ProductUpdateTrustError> {
  guard releaseOutput.lastPathComponent == "release-output.json",
    signature.lastPathComponent == "release-output.json.sig",
    releaseOutput.deletingLastPathComponent().standardizedFileURL
      == signature.deletingLastPathComponent().standardizedFileURL
  else {
    return .failure(.verifierFailed("release verification requires the canonical signed tuple"))
  }
  var environment = extraEnvironment
  environment["PYTHONNOUSERSITE"] = "1"
  environment["PYTHONDONTWRITEBYTECODE"] = "1"
  switch runProductUpdateBoundProcess(
    executable: python.path,
    arguments: [
      "-m", "vibecrafted_core.product_contract", "release-output",
      releaseOutput.path, signature.path,
    ],
    timeout: timeout,
    extraEnvironment: environment)
  {
  case .failure(.executableMissing):
    return .failure(.pythonMissing)
  case .failure:
    return .failure(.verifierFailed("could not start the signed release checker"))
  case .success(let result):
    if result.timedOut { return .failure(.timedOut) }
    if result.status == 0 { return .success(()) }
    let detail = String(data: result.stderr, encoding: .utf8) ?? ""
    return .failure(
      .verifierFailed(detail.isEmpty ? "signed release checker refused the candidate" : detail))
  }
}

func observeProductUpdateRevisions(from payload: Data) -> (
  source: String?, terminal: String?, frame: String?
) {
  guard let root = try? JSONSerialization.jsonObject(with: payload) as? [String: Any],
    let revisions = root["source_revisions"] as? [String: Any]
  else { return (nil, nil, nil) }
  return (
    revisions["vibecrafted"] as? String,
    revisions["vc-terminal"] as? String,
    revisions["vc-frame"] as? String
  )
}

/// Python from the already-trusted installed generation — never PATH or an
/// environment-selected interpreter.
func resolveProductContractPython(installRoot: URL?) -> URL? {
  guard let root = installRoot else { return nil }
  let python = root.appendingPathComponent("bin/python3")
  guard FileManager.default.isExecutableFile(atPath: python.path) else { return nil }
  if let values = try? python.resourceValues(forKeys: [.isSymbolicLinkKey]),
    values.isSymbolicLink == true
  {
    guard let destination = try? FileManager.default.destinationOfSymbolicLink(atPath: python.path)
    else { return nil }
    let resolved =
      (destination as NSString).isAbsolutePath
      ? URL(fileURLWithPath: destination)
      : python.deletingLastPathComponent().appendingPathComponent(destination)
    let standardized = resolved.standardizedFileURL
    guard standardized.path.hasPrefix(root.standardizedFileURL.path + "/")
      || standardized.path == root.standardizedFileURL.appendingPathComponent("bin/python3").path
    else { return nil }
  }
  return python
}
