import Foundation

/// In-app product update owner.
///
/// Codescribe updates a standalone `.app` through Sparkle 2. Vibecrafted's
/// product is the signed App plus the Runtime Pack published by the existing
/// transactional installer. Sparkle would be a second publisher and can leave
/// a mixed generation, so it is not the owner here.
///
/// This file is Foundation-only. It admits a candidate, names progress a
/// person can act on, and never claims healthy unless App and pack identities
/// match. Missing feed, trust root, replacement helper or staged payloads is a
/// bounded unavailable state — never an infinite spinner.
enum ProductUpdatePhase: String, Equatable, Sendable {
  case idle
  case checking
  case downloading
  case verifying
  case installing
  case success
  case readyToReplace
  case unavailable
  case refused
  case retained
  case error
}

struct ProductUpdateChannel: Equatable, Sendable {
  var feedURL: URL?
  var publicKeyPresent: Bool
  var publicKeyName: String
  var expectedKeyID: String
  var appReplacementHelperPresent: Bool

  var provisioningGap: String? {
    var missing: [String] = []
    if feedURL == nil {
      missing.append("VCUpdateFeedURL (HTTPS release-output.v1 feed)")
    }
    if !publicKeyPresent {
      missing.append("\(publicKeyName) trust root")
    }
    if !appReplacementHelperPresent {
      missing.append(
        "signed helper Contents/Helpers/vc-app-update that replaces the App after a UI-only quit")
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

struct ProductUpdateCandidate: Equatable, Sendable {
  var generation: String
  var sourceRevision: String
  var terminalRevision: String
  var frameRevision: String
  var bundleIdentifier: String
  var keyID: String
  var signatureValid: Bool
  var notarized: Bool
  var packPath: String
  var appPath: String
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
  var requestsUIOnlyQuit: Bool
  var claimsHealthy: Bool
}

func resolveProductUpdateChannel(
  feedURLString: String?,
  publicKeyPresent: Bool,
  publicKeyName: String = "vibecrafted-signing-v1.pub",
  expectedKeyID: String = "vibecrafted-signing-v1",
  appReplacementHelperPresent: Bool
) -> ProductUpdateChannel {
  let trimmed = feedURLString?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
  let feed: URL?
  if trimmed.isEmpty {
    feed = nil
  } else if let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
    scheme == "https", url.host != nil
  {
    feed = url
  } else {
    feed = nil
  }
  return ProductUpdateChannel(
    feedURL: feed,
    publicKeyPresent: publicKeyPresent,
    publicKeyName: publicKeyName,
    expectedKeyID: expectedKeyID,
    appReplacementHelperPresent: appReplacementHelperPresent)
}

func productUpdateGenerationLabel(version: String, sourceRevision: String) -> String {
  let token = String(sourceRevision.trimmingCharacters(in: .whitespacesAndNewlines).prefix(8))
    .lowercased()
  return "\(version)+g\(token)"
}

func productUpdatePayloadsAreStaged(
  _ candidate: ProductUpdateCandidate,
  fileExists: (String) -> Bool
) -> Bool {
  candidate.packPath.hasPrefix("/") && fileExists(candidate.packPath)
    && candidate.appPath.hasPrefix("/") && fileExists(candidate.appPath)
}

/// True when this running App already carries the candidate source identity.
/// A newer App must be replaced after a UI-only quit; publishing its pack
/// here would leave a mixed generation on disk.
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
  expectedBundleIdentifier: String = "io.vetcoders.vibecrafted",
  fileExists: (String) -> Bool = { _ in false }
) -> ProductUpdateAdmission {
  if let gap = channel.provisioningGap {
    return .unavailable(
      "Updates are not provisioned yet: \(gap). The installed generation stays as it is.")
  }
  guard let candidate else {
    return .unavailable("The update feed did not name a candidate. Nothing was installed.")
  }
  guard candidate.signatureValid else {
    return .refuse(
      "The candidate signature is not valid for \(channel.expectedKeyID). The installed generation is unchanged.")
  }
  guard candidate.keyID == channel.expectedKeyID else {
    return .refuse(
      "The candidate key \(candidate.keyID) is not \(channel.expectedKeyID). The installed generation is unchanged.")
  }
  guard candidate.notarized else {
    return .refuse(
      "The candidate is not a notarized, stapled App. The installed generation is unchanged.")
  }
  guard candidate.bundleIdentifier == expectedBundleIdentifier else {
    return .refuse(
      "The candidate bundle \(candidate.bundleIdentifier) is not \(expectedBundleIdentifier). The installed generation is unchanged.")
  }
  guard runtimePackMatchesCarrier(
    generation: candidate.generation, signedSourceRevision: candidate.sourceRevision)
  else {
    return .refuse(
      "The candidate App and Runtime Pack identities do not match. The installed generation is unchanged.")
  }
  guard !candidate.terminalRevision.isEmpty, !candidate.frameRevision.isEmpty else {
    return .refuse(
      "The candidate is missing vc-terminal or vc-frame revisions. The installed generation is unchanged.")
  }
  guard productUpdatePayloadsAreStaged(candidate, fileExists: fileExists) else {
    return .unavailable(
      "A signed candidate was named, but the App and Runtime Pack are not staged as local files. In-app download is not provisioned, so nothing was installed.")
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
      summary: "Look for a signed App and matching Runtime Pack. This does not stop Frame, terminals, agents or sessions.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .checking:
    return ProductUpdateProgress(
      phase: phase,
      title: "Looking for a signed update",
      summary: detail ??
        "Asking the update feed. Installed: \(installedLabel). This check ends with a result or an error; it does not spin forever.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .downloading:
    return ProductUpdateProgress(
      phase: phase,
      title: "Reading the signed candidate",
      summary: detail ??
        "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). The feed document is being read.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .verifying:
    return ProductUpdateProgress(
      phase: phase,
      title: "Verifying signature and identity",
      summary: detail ??
        "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). The installer remains the authority for publication.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .installing:
    return ProductUpdateProgress(
      phase: phase,
      title: "Installing through the Runtime Pack owner",
      summary: detail ??
        "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). A failure keeps the previous working generation.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .success:
    return ProductUpdateProgress(
      phase: phase,
      title: "Update installed",
      summary: detail ??
        "Installed and candidate are \(candidateLabel ?? installedLabel). Quit the App if you want to close the UI. Frame, terminals, agents and sessions stay running.",
      installedGeneration: candidateLabel ?? installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: true,
      claimsHealthy: true)
  case .readyToReplace:
    return ProductUpdateProgress(
      phase: phase,
      title: "Ready to replace the App",
      summary: detail ??
        "Installed: \(installedLabel). Candidate: \(candidateLabel ?? "unknown"). This running App will not publish the candidate Runtime Pack. Quit the App so a signed helper can replace the UI; the new App then uses the existing installer. Frame, terminals, agents and sessions stay running.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      requestsUIOnlyQuit: true,
      claimsHealthy: false)
  case .unavailable:
    return ProductUpdateProgress(
      phase: phase,
      title: "Updates are not available yet",
      summary: detail ?? "The update channel is not provisioned. The installed generation is unchanged.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .refused:
    return ProductUpdateProgress(
      phase: phase,
      title: "This update was refused",
      summary: detail ?? "Signature or identity did not match. The installed generation is unchanged.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: false,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .retained:
    return ProductUpdateProgress(
      phase: phase,
      title: "The update did not finish",
      summary: detail ??
        "The previous working generation \(installedLabel) is still installed. The installer receipt was not replaced.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  case .error:
    return ProductUpdateProgress(
      phase: phase,
      title: "The update check failed",
      summary: detail ?? "The feed or installer returned an error. The installed generation is unchanged.",
      installedGeneration: installedLabel,
      candidateGeneration: candidateLabel,
      canRetry: true,
      requestsUIOnlyQuit: false,
      claimsHealthy: false)
  }
}

func decodeProductUpdateFeed(_ data: Data) throws -> ProductUpdateCandidate {
  let object = try JSONSerialization.jsonObject(with: data)
  guard let root = object as? [String: Any] else {
    throw ProductUpdateFeedError.malformed("update feed is not a JSON object")
  }
  guard root["schema"] as? String == "io.vetcoders.vibecrafted.release-output.v1" else {
    throw ProductUpdateFeedError.malformed("update feed is not release-output.v1")
  }
  guard let policy = root["signature_policy"] as? [String: Any],
    let keyID = policy["key_id"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update feed has no signature_policy.key_id")
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
    let packPath = pack["path"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update feed has no runtime_pack.path")
  }
  guard let dmg = root["dmg"] as? [String: Any],
    let dmgPath = dmg["path"] as? String
  else {
    throw ProductUpdateFeedError.malformed("update feed has no dmg.path")
  }
  guard let notarization = root["notarization"] as? [String: Any],
    let appTicket = notarization["app"] as? [String: Any],
    let dmgTicket = notarization["dmg"] as? [String: Any],
    appTicket["ticket"] as? Bool == true,
    dmgTicket["ticket"] as? Bool == true
  else {
    throw ProductUpdateFeedError.malformed("update feed does not attest notarization tickets")
  }
  let assets = root["assets"] as? [String: Any]
  let resolvedPack = (assets?["runtime_pack"] as? String) ?? packPath
  let resolvedApp = (assets?["app"] as? String) ?? dmgPath
  let signatureValid = (root["signature_valid"] as? Bool) ?? false
  let bundle =
    (root["bundle_identifier"] as? String)
    ?? "io.vetcoders.vibecrafted"
  return ProductUpdateCandidate(
    generation: productUpdateGenerationLabel(version: version, sourceRevision: source),
    sourceRevision: source,
    terminalRevision: terminal,
    frameRevision: frame,
    bundleIdentifier: bundle,
    keyID: keyID,
    signatureValid: signatureValid,
    notarized: true,
    packPath: resolvedPack,
    appPath: resolvedApp)
}

enum ProductUpdateFeedError: Error, Equatable {
  case malformed(String)

  var localizedDescription: String {
    switch self {
    case .malformed(let reason): return reason
    }
  }
}
