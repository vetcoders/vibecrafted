import Foundation

/// In-app product update owner.
///
/// Codescribe updates a standalone `.app` through Sparkle 2 (`SPUStandardUpdaterController`,
/// `SUFeedURL` appcast, `SUPublicEDKey` Ed25519). Sparkle is MIT and maintained, but it
/// cannot verify this product's `release-output.v1` detached RSA signature or publish a
/// Runtime Pack receipt. It is therefore not pinned and not linked. See
/// `docs/installer/IN_APP_UPDATE.md`.
///
/// Admission never reads `signature_valid`, notarization tickets, or a default bundle
/// identity from untrusted JSON. Those are claims. Proof comes from the established
/// release-output verifier, `/usr/bin/openssl dgst` over exact bytes with the bundled
/// `vibecrafted-signing-v1.pub`, payload hashes, `codesign` identity, and stapler/spctl.
enum ProductUpdatePhase: String, Equatable, Sendable {
  case idle
  case checking
  case downloading
  case verifying
  case ready
  case installing
  case restarting
  case success
  case unavailable
  case refused
  case retained
  case error
}

struct ProductUpdateChannel: Equatable, Sendable {
  var feedURL: URL?
  var signatureURL: URL?
  var publicKeyURL: URL?
  var publicKeyName: String
  var expectedKeyID: String
  var fixtureAllowed: Bool
  var helperURL: URL?

  var provisioningGap: String? {
    var missing: [String] = []
    if feedURL == nil {
      missing.append(
        fixtureAllowed
          ? "fixture root has no signed release-output.json"
          : "a signed update feed is not configured yet")
    }
    if publicKeyURL == nil {
      missing.append("\(publicKeyName) trust root")
    }
    return missing.isEmpty ? nil : missing.joined(separator: "; ")
  }
}

struct ProductUpdateIdentity: Equatable, Sendable {
  var appGeneration: String
  var packGeneration: String?
  var sourceRevision: String?
  var terminalRevision: String?
  var frameRevision: String?
}

/// Locator fields parsed from a feed document. None of these fields is a verification
/// result. `signature_valid`, ticket booleans, and a default bundle id are ignored.
struct ProductUpdateCandidate: Equatable, Sendable {
  var generation: String
  var sourceRevision: String
  var terminalRevision: String
  var frameRevision: String
  var keyID: String
  var algorithm: String
  var spkiSHA256: String
  var packRelativePath: String
  var appRelativePath: String
  var packSHA256: String
  var appSHA256: String
  var packSize: Int
  var appSize: Int
}

/// Verification results produced by an established owner. Never decoded from feed JSON.
struct ProductUpdateProof: Equatable, Sendable {
  var signatureVerifiedOverExactBytes: Bool
  var verifierOwner: String
  var payloadHashesMatch: Bool
  var codesignIdentifier: String?
  var codesignTeamID: String?
  var notarizedAndStapled: Bool
  var packIdentityMatches: Bool
  var observedSourceRevision: String?
  var observedTerminalRevision: String?
  var observedFrameRevision: String?

  static func unsigned() -> ProductUpdateProof {
    ProductUpdateProof(
      signatureVerifiedOverExactBytes: false,
      verifierOwner: "none",
      payloadHashesMatch: false,
      codesignIdentifier: nil,
      codesignTeamID: nil,
      notarizedAndStapled: false,
      packIdentityMatches: false,
      observedSourceRevision: nil,
      observedTerminalRevision: nil,
      observedFrameRevision: nil)
  }
}

enum ProductUpdateAdmission: Equatable, Sendable {
  case unavailable(String)
  case refuse(String)
  case admit(ProductUpdateCandidate)
}

struct ProductUpdateProgress: Equatable, Sendable {
  var phase: ProductUpdatePhase
  var title: String
  var summary: String
  var installedGeneration: String
  var candidateGeneration: String?
  var canRetry: Bool
  var canInstall: Bool
  var claimsHealthy: Bool
  var willCloseUIForReplacement: Bool
}

let productUpdateFixtureFlag = "VIBECRAFTED_UPDATE_FIXTURE"
let productUpdateFixtureRootKey = "VIBECRAFTED_UPDATE_FIXTURE_ROOT"
let productUpdateExpectedBundleIdentifier = "io.vetcoders.vibecrafted"
let productUpdateExpectedTeamID = "MW223P3NPX"
let productUpdateExpectedVerifierOwner = "product_contract.release-output"
let productUpdateExpectedKeyID = "vibecrafted-signing-v1"
let productUpdateExpectedAlgorithm = "rsa-pkcs1v15-sha256"
let productUpdateExpectedSPKI =
  "521ed59d3c446c540afe1557c2dbc39c9c190775f99896b2b65206c32814b25b"
let productUpdateSignatureBytes = 256

func productUpdateFixtureAllowed(environment: [String: String]) -> Bool {
  let flag = environment[productUpdateFixtureFlag]?.trimmingCharacters(in: .whitespacesAndNewlines)
  return flag == "1" || flag?.lowercased() == "true"
}

func productUpdateFixtureRoot(environment: [String: String]) -> URL? {
  guard productUpdateFixtureAllowed(environment: environment) else { return nil }
  let raw = environment[productUpdateFixtureRootKey]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
  guard !raw.isEmpty else { return nil }
  let url = URL(fileURLWithPath: raw, isDirectory: true)
  let feed = url.appendingPathComponent("release-output.json")
  let signature = url.appendingPathComponent("release-output.json.sig")
  guard FileManager.default.isReadableFile(atPath: feed.path),
    FileManager.default.isReadableFile(atPath: signature.path)
  else { return nil }
  return url
}

func resolveProductUpdateFeedURL(
  feedURLString: String?,
  environment: [String: String]
) -> URL? {
  if let root = productUpdateFixtureRoot(environment: environment) {
    return root.appendingPathComponent("release-output.json")
  }
  let trimmed = feedURLString?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
  guard !trimmed.isEmpty, let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
    scheme == "https", url.host != nil
  else { return nil }
  return url
}

func resolveProductUpdateSignatureURL(for feedURL: URL) -> URL {
  if feedURL.isFileURL {
    return feedURL.deletingLastPathComponent().appendingPathComponent("release-output.json.sig")
  }
  if feedURL.path.hasSuffix(".json") {
    return feedURL.appendingPathExtension("sig")
  }
  return feedURL.appendingPathComponent("release-output.json.sig")
}

func resolveProductUpdateChannel(
  feedURLString: String?,
  publicKeyURL: URL?,
  helperURL: URL?,
  environment: [String: String] = [:],
  publicKeyName: String = "vibecrafted-signing-v1.pub",
  expectedKeyID: String = productUpdateExpectedKeyID
) -> ProductUpdateChannel {
  let feed = resolveProductUpdateFeedURL(feedURLString: feedURLString, environment: environment)
  let key: URL?
  if let publicKeyURL, FileManager.default.isReadableFile(atPath: publicKeyURL.path) {
    key = publicKeyURL
  } else {
    key = nil
  }
  return ProductUpdateChannel(
    feedURL: feed,
    signatureURL: feed.map(resolveProductUpdateSignatureURL(for:)),
    publicKeyURL: key,
    publicKeyName: publicKeyName,
    expectedKeyID: expectedKeyID,
    fixtureAllowed: productUpdateFixtureAllowed(environment: environment),
    helperURL: helperURL)
}

func productUpdateGenerationLabel(version: String, sourceRevision: String) -> String {
  let token = String(sourceRevision.trimmingCharacters(in: .whitespacesAndNewlines).prefix(8))
    .lowercased()
  return "\(version)+g\(token)"
}

func productUpdateRunningAppMatchesCandidate(
  installed: ProductUpdateIdentity,
  candidate: ProductUpdateCandidate
) -> Bool {
  guard runtimePackMatchesCarrier(
    generation: installed.appGeneration, signedSourceRevision: candidate.sourceRevision)
  else { return false }
  if let source = installed.sourceRevision, !source.isEmpty {
    let sameSource = source.lowercased() == candidate.sourceRevision.lowercased()
    let appMatchesInstalledSource = runtimePackMatchesCarrier(
      generation: installed.appGeneration, signedSourceRevision: source)
    guard sameSource && appMatchesInstalledSource else { return false }
  }
  return runtimePackMatchesCarrier(
    generation: candidate.generation, signedSourceRevision: candidate.sourceRevision)
}

func productUpdateClaimsHealthy(
  installed: ProductUpdateIdentity,
  candidate: ProductUpdateCandidate
) -> Bool {
  guard productUpdateRunningAppMatchesCandidate(installed: installed, candidate: candidate)
  else { return false }
  guard let pack = installed.packGeneration, !pack.isEmpty else { return false }
  guard runtimePackMatchesCarrier(generation: pack, signedSourceRevision: candidate.sourceRevision)
  else { return false }
  guard runtimePackMatchesCarrier(
    generation: candidate.generation, signedSourceRevision: candidate.sourceRevision)
  else { return false }
  guard pack == candidate.generation else { return false }
  if let source = installed.sourceRevision, !source.isEmpty {
    guard runtimePackMatchesCarrier(generation: pack, signedSourceRevision: source) else {
      return false
    }
  }
  return true
}

func admitProductUpdateCandidate(
  channel: ProductUpdateChannel,
  candidate: ProductUpdateCandidate?,
  proof: ProductUpdateProof?,
  expectedBundleIdentifier: String = productUpdateExpectedBundleIdentifier
) -> ProductUpdateAdmission {
  if let gap = channel.provisioningGap {
    return .unavailable(
      "Updates are not available yet. \(gap). Your current version stays installed.")
  }
  guard let candidate else {
    return .unavailable("No update was named. Your current version stays installed.")
  }
  guard let proof else {
    return .refuse(
      "The update was not verified with the signed release checker. Your current version stays installed.")
  }
  guard proof.signatureVerifiedOverExactBytes else {
    return .refuse(
      "The update signature did not match the bundled signing key. Your current version stays installed.")
  }
  guard proof.verifierOwner == productUpdateExpectedVerifierOwner else {
    return .refuse(
      "The update was not verified with the signed release checker. Your current version stays installed.")
  }
  guard candidate.keyID == channel.expectedKeyID,
    candidate.algorithm == productUpdateExpectedAlgorithm,
    candidate.spkiSHA256 == productUpdateExpectedSPKI
  else {
    return .refuse(
      "The update was signed with an unexpected key. Your current version stays installed.")
  }
  guard proof.payloadHashesMatch else {
    return .refuse(
      "The downloaded files did not match the signed sizes and hashes. Your current version stays installed.")
  }
  guard proof.codesignIdentifier == expectedBundleIdentifier,
    proof.codesignTeamID == productUpdateExpectedTeamID
  else {
    return .refuse(
      "The update is not the Vibecrafted app. Your current version stays installed.")
  }
  guard proof.notarizedAndStapled else {
    return .refuse(
      "The update is not a notarized, stapled app. Your current version stays installed.")
  }
  guard proof.packIdentityMatches else {
    return .refuse(
      "The app and Runtime Pack in this update do not match. Your current version stays installed.")
  }
  if let observed = proof.observedSourceRevision, !observed.isEmpty,
    observed.lowercased() != candidate.sourceRevision.lowercased()
  {
    return .refuse(
      "The Runtime Pack was cut from a different revision than the app. Your current version stays installed.")
  }
  if let terminal = proof.observedTerminalRevision, !terminal.isEmpty,
    terminal.lowercased() != candidate.terminalRevision.lowercased()
  {
    return .refuse(
      "The Terminal revision in this update does not match the Runtime Pack. Your current version stays installed.")
  }
  if let frame = proof.observedFrameRevision, !frame.isEmpty,
    frame.lowercased() != candidate.frameRevision.lowercased()
  {
    return .refuse(
      "The Frame revision in this update does not match the Runtime Pack. Your current version stays installed.")
  }
  guard runtimePackMatchesCarrier(
    generation: candidate.generation, signedSourceRevision: candidate.sourceRevision)
  else {
    return .refuse(
      "The update version does not match its source revision. Your current version stays installed.")
  }
  guard !candidate.terminalRevision.isEmpty, !candidate.frameRevision.isEmpty else {
    return .refuse(
      "The update is missing Terminal or Frame revisions. Your current version stays installed.")
  }
  return .admit(candidate)
}

func deriveProductUpdateProgress(
  phase: ProductUpdatePhase,
  installed: ProductUpdateIdentity,
  candidate: ProductUpdateCandidate?,
  detail: String? = nil
) -> ProductUpdateProgress {
  let installedLabel = installed.packGeneration ?? installed.appGeneration
  let candidateLabel = candidate?.generation
  switch phase {
  case .idle:
    return ProductUpdateProgress(
      phase: phase,
      title: "Check for Updates",
      summary: detail
        ?? "Look for a newer signed app and matching Runtime Pack. This does not stop Frame, terminals, agents or sessions.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .checking:
    return ProductUpdateProgress(
      phase: phase,
      title: "Looking for an update",
      summary: detail ?? "Checking for a newer version. Installed: \(installedLabel).",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .downloading:
    return ProductUpdateProgress(
      phase: phase,
      title: "Downloading the update",
      summary: detail
        ?? "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). Files are being copied to a staging folder.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .verifying:
    return ProductUpdateProgress(
      phase: phase,
      title: "Checking the update is genuine",
      summary: detail
        ?? "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). Checking the signature, files, and matching Runtime Pack.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .ready:
    return ProductUpdateProgress(
      phase: phase,
      title: "An update is ready",
      summary: detail
        ?? "Installed: \(installedLabel). Available: \(candidateLabel ?? "unknown"). Choose Install Update to apply it. Frame, terminals, agents and sessions stay running.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      canInstall: true,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .installing:
    return ProductUpdateProgress(
      phase: phase,
      title: "Installing the update",
      summary: detail
        ?? "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). If this is interrupted, the previous working version is kept.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .restarting:
    return ProductUpdateProgress(
      phase: phase,
      title: "Restarting to finish the update",
      summary: detail
        ?? "The app window will close and reopen on \(candidateLabel ?? installedLabel). Frame, terminals, agents and sessions stay running. The new app publishes the matching Runtime Pack.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: true)
  case .success:
    return ProductUpdateProgress(
      phase: phase,
      title: "Update installed",
      summary: detail
        ?? "You are on \(candidateLabel ?? installedLabel). You can open the console again and reconnect to the same session.",
      installedGeneration: candidateLabel ?? installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: true,
      willCloseUIForReplacement: false)
  case .unavailable:
    return ProductUpdateProgress(
      phase: phase,
      title: "Updates are not available yet",
      summary: detail ?? "Automatic updates are not configured on this copy. Your current version stays installed.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .refused:
    return ProductUpdateProgress(
      phase: phase,
      title: "This update was refused",
      summary: detail ?? "The update did not pass the signature or identity check. Your current version stays installed.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .retained:
    return ProductUpdateProgress(
      phase: phase,
      title: "The update did not finish",
      summary: detail
        ?? "The previous working version \(installedLabel) is still installed.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  case .error:
    return ProductUpdateProgress(
      phase: phase,
      title: "The update check failed",
      summary: detail ?? "The check did not finish. Your current version stays installed.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      canInstall: false,
      claimsHealthy: false,
      willCloseUIForReplacement: false)
  }
}

func decodeProductUpdateFeed(_ data: Data) throws -> ProductUpdateCandidate {
  let object = try JSONSerialization.jsonObject(with: data)
  guard let root = object as? [String: Any] else {
    throw ProductUpdateFeedError.malformed("update feed is not a JSON object")
  }
  // Untrusted self-attestations. Reading them as proof is a trust-boundary bug.
  _ = root["signature_valid"]
  _ = (root["notarization"] as? [String: Any])
  _ = root["bundle_identifier"]
  guard root["schema"] as? String == "io.vetcoders.vibecrafted.release-output.v1" else {
    throw ProductUpdateFeedError.malformed("update feed is not release-output.v1")
  }
  guard let policy = root["signature_policy"] as? [String: Any],
    let keyID = policy["key_id"] as? String,
    let algorithm = policy["algorithm"] as? String,
    let spki = policy["spki_sha256"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update feed has no signature_policy")
  }
  guard let product = root["product"] as? [String: Any],
    let version = product["version"] as? String, !version.isEmpty
  else {
    throw ProductUpdateFeedError.malformed("update feed has no product.version")
  }
  guard let revisions = root["source_revisions"] as? [String: Any],
    let source = revisions["vibecrafted"] as? String,
    let terminal = revisions["vc-terminal"] as? String,
    let frame = revisions["vc-frame"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update feed is missing source_revisions")
  }
  guard let pack = root["runtime_pack"] as? [String: Any],
    let packPath = pack["path"] as? String,
    let packHash = pack["sha256"] as? String,
    let packSize = pack["size"] as? Int, packSize > 0
  else {
    throw ProductUpdateFeedError.malformed("update feed has no runtime_pack path, hash and size")
  }
  guard let dmg = root["dmg"] as? [String: Any],
    let dmgPath = dmg["path"] as? String,
    let dmgHash = dmg["sha256"] as? String,
    let dmgSize = dmg["size"] as? Int, dmgSize > 0
  else {
    throw ProductUpdateFeedError.malformed("update feed has no dmg path, hash and size")
  }
  return ProductUpdateCandidate(
    generation: productUpdateGenerationLabel(version: version, sourceRevision: source),
    sourceRevision: source,
    terminalRevision: terminal,
    frameRevision: frame,
    keyID: keyID,
    algorithm: algorithm,
    spkiSHA256: spki,
    packRelativePath: packPath,
    appRelativePath: dmgPath,
    packSHA256: packHash,
    appSHA256: dmgHash,
    packSize: packSize,
    appSize: dmgSize)
}

enum ProductUpdateFeedError: Error, Equatable {
  case malformed(String)

  var localizedDescription: String {
    switch self {
    case .malformed(let reason): return reason
    }
  }
}
