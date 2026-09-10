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
    }
  }
}

/// Same check as `product_contract._verify_release_signature`: `/usr/bin/openssl dgst
/// -sha256 -verify` with the pinned bundled public key over the exact payload bytes.
func verifyDetachedReleaseSignature(
  payload: Data,
  signature: Data,
  publicKeyPath: String,
  opensslPath: String = "/usr/bin/openssl"
) -> Result<Void, ProductUpdateTrustError> {
  guard FileManager.default.isReadableFile(atPath: publicKeyPath) else {
    return .failure(.missingPublicKey)
  }
  guard signature.count == productUpdateSignatureBytes else {
    return .failure(.invalidSignatureSize)
  }
  guard FileManager.default.isExecutableFile(atPath: opensslPath) else {
    return .failure(.opensslUnavailable)
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
    let process = Process()
    process.executableURL = URL(fileURLWithPath: opensslPath)
    process.arguments = [
      "dgst", "-sha256", "-verify", publicKeyPath, "-signature", signatureFile.path,
    ]
    let stdin = Pipe()
    process.standardInput = stdin
    process.standardOutput = Pipe()
    process.standardError = Pipe()
    try process.run()
    stdin.fileHandleForWriting.write(payload)
    stdin.fileHandleForWriting.closeFile()
    process.waitUntilExit()
    return process.terminationStatus == 0 ? .success(()) : .failure(.signatureInvalid)
  } catch {
    return .failure(.opensslUnavailable)
  }
}

func verifyProductUpdatePayloadDigest(
  fileURL: URL,
  expectedSHA256: String,
  expectedSize: Int,
  opensslPath: String = "/usr/bin/openssl"
) -> Result<Void, ProductUpdateTrustError> {
  guard let values = try? FileManager.default.attributesOfItem(atPath: fileURL.path),
    let size = values[.size] as? NSNumber, size.intValue == expectedSize
  else {
    return .failure(.hashMismatch)
  }
  guard let hex = sha256HexOfFile(at: fileURL, opensslPath: opensslPath) else {
    return .failure(.opensslUnavailable)
  }
  return hex == expectedSHA256.lowercased() ? .success(()) : .failure(.hashMismatch)
}

func sha256HexOfFile(at url: URL, opensslPath: String = "/usr/bin/openssl") -> String? {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: opensslPath)
  process.arguments = ["dgst", "-sha256", "-r", url.path]
  let stdout = Pipe()
  process.standardOutput = stdout
  process.standardError = Pipe()
  do { try process.run() } catch { return nil }
  process.waitUntilExit()
  guard process.terminationStatus == 0 else { return nil }
  let text = String(data: stdout.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
  let hex = text.split(separator: " ").first.map(String.init) ?? ""
  return hex.count == 64 ? hex.lowercased() : nil
}

func observeProductUpdateCodesignIdentifier(
  appURL: URL,
  codesignPath: String = "/usr/bin/codesign"
) -> String? {
  guard FileManager.default.isExecutableFile(atPath: codesignPath) else { return nil }
  let process = Process()
  process.executableURL = URL(fileURLWithPath: codesignPath)
  process.arguments = ["--display", "--verbose=4", appURL.path]
  let err = Pipe()
  process.standardError = err
  process.standardOutput = Pipe()
  do { try process.run() } catch { return nil }
  process.waitUntilExit()
  let text = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
  for line in text.split(separator: "\n") {
    if line.hasPrefix("Identifier=") {
      return String(line.dropFirst("Identifier=".count))
    }
  }
  return nil
}

func observeProductUpdateStapledNotarization(
  appURL: URL,
  staplerLauncher: String = "/usr/bin/xcrun"
) -> Bool {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: staplerLauncher)
  process.arguments = ["stapler", "validate", appURL.path]
  process.standardOutput = Pipe()
  process.standardError = Pipe()
  do { try process.run() } catch { return false }
  process.waitUntilExit()
  return process.terminationStatus == 0
}

/// Established owner: `python -m vibecrafted_core.product_contract release-output`.
func invokeReleaseOutputVerifier(
  releaseOutput: URL,
  signature: URL,
  python: URL,
  extraEnvironment: [String: String] = [:]
) -> Result<Void, ProductUpdateTrustError> {
  guard releaseOutput.lastPathComponent == "release-output.json",
    signature.lastPathComponent == "release-output.json.sig",
    releaseOutput.deletingLastPathComponent().standardizedFileURL
      == signature.deletingLastPathComponent().standardizedFileURL
  else {
    return .failure(.verifierFailed("release verification requires the canonical signed tuple"))
  }
  let process = Process()
  process.executableURL = python
  process.arguments = [
    "-m", "vibecrafted_core.product_contract", "release-output",
    releaseOutput.path, signature.path,
  ]
  var environment = ProcessInfo.processInfo.environment
  extraEnvironment.forEach { environment[$0] = $1 }
  process.environment = environment
  let stderr = Pipe()
  process.standardOutput = Pipe()
  process.standardError = stderr
  do { try process.run() } catch {
    return .failure(.verifierFailed("could not start the signed release checker"))
  }
  process.waitUntilExit()
  if process.terminationStatus == 0 {
    return .success(())
  }
  let detail = String(data: stderr.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
  return .failure(
    .verifierFailed(detail.isEmpty ? "signed release checker refused the candidate" : detail))
}

func makeVerifiedProductUpdateProof(
  codesignIdentifier: String,
  notarizedAndStapled: Bool,
  packIdentityMatches: Bool,
  observedSourceRevision: String? = nil,
  observedTerminalRevision: String? = nil,
  observedFrameRevision: String? = nil,
  verifierOwner: String = "product_contract.release-output"
) -> ProductUpdateProof {
  ProductUpdateProof(
    signatureVerifiedOverExactBytes: true,
    verifierOwner: verifierOwner,
    payloadHashesMatch: true,
    codesignIdentifier: codesignIdentifier,
    notarizedAndStapled: notarizedAndStapled,
    packIdentityMatches: packIdentityMatches,
    observedSourceRevision: observedSourceRevision,
    observedTerminalRevision: observedTerminalRevision,
    observedFrameRevision: observedFrameRevision)
}
