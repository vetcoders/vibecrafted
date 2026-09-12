import Foundation

/// Supervision state for the Runtime Pack section of the tray menu.
///
/// After the dmg-first eradication the Runtime Pack is the product carrier and
/// the App consumes it: the tray's supervision duty is to show which generation
/// is live and whether it still matches the carrier this signed App shipped.
/// Drift is expected when the runtime moves ahead of the App (runtime-first
/// upgrades install straight from a tarball), so it surfaces as amber, not red.
struct RuntimePackMenuState {
  let header: String
  let detail: String
  let health: TrayServerHealth
  let actionsEnabled: Bool
}

/// Revision token embedded in a generation label `X.Y.Z+g<shortsha>`.
/// Returns nil for unstamped or malformed labels (`+UNSTAMPED`, bare versions).
func generationRevisionToken(_ generation: String) -> String? {
  guard let plusRange = generation.range(of: "+g", options: .backwards) else { return nil }
  let token = generation[plusRange.upperBound...].trimmingCharacters(in: .whitespacesAndNewlines)
  guard !token.isEmpty, token.allSatisfy({ $0.isHexDigit }) else { return nil }
  return token.lowercased()
}

/// True when the installed generation was cut from the same source revision the
/// signed App carrier ships. The generation carries an 8-char prefix of the
/// manifest's full `git_sha`.
func runtimePackMatchesCarrier(generation: String, signedSourceRevision: String) -> Bool {
  guard let token = generationRevisionToken(generation) else { return false }
  return signedSourceRevision.lowercased().hasPrefix(token)
}

func deriveRuntimePackMenuState(
  generation: String?,
  signedSourceRevision: String?,
  runtimeReady: Bool
) -> RuntimePackMenuState {
  guard runtimeReady else {
    return RuntimePackMenuState(
      header: "Runtime Pack: WAITING FOR RUNTIME",
      detail: "Runtime onboarding has not completed",
      health: .checking,
      actionsEnabled: false)
  }
  guard let generation, !generation.isEmpty else {
    return RuntimePackMenuState(
      header: "Runtime Pack: UNKNOWN",
      detail: "The installed runtime did not report a generation",
      health: .neutral,
      actionsEnabled: true)
  }
  guard let signed = signedSourceRevision, !signed.isEmpty else {
    return RuntimePackMenuState(
      header: "Runtime Pack: \(generation)",
      detail: "Signed carrier revision is unavailable",
      health: .neutral,
      actionsEnabled: true)
  }
  guard generationRevisionToken(generation) != nil else {
    return RuntimePackMenuState(
      header: "Runtime Pack: \(generation)",
      detail: "Installed generation carries no source stamp",
      health: .neutral,
      actionsEnabled: true)
  }
  if runtimePackMatchesCarrier(generation: generation, signedSourceRevision: signed) {
    return RuntimePackMenuState(
      header: "Runtime Pack: \(generation)",
      detail: "Matches this App's signed carrier",
      health: .healthy,
      actionsEnabled: true)
  }
  return RuntimePackMenuState(
    header: "Runtime Pack: \(generation)",
    detail: "Carrier expects \(String(signed.prefix(8))) — the installed runtime moved; update the App to re-sync",
    health: .transitioning,
    actionsEnabled: true)
}

/// Compact diagnostics blob for the "Copy Runtime Identity" tray action — the
/// identity tuple a support report needs to attribute a runtime to its carrier.
func runtimeIdentityBlob(
  generation: String,
  sourceRevision: String?,
  terminalRevision: String?,
  frameRevision: String?,
  runtimeHome: String,
  configHome: String
) -> String {
  var lines = ["vibecrafted-runtime: \(generation)"]
  lines.append("carrier-source: \(sourceRevision ?? "unknown")")
  lines.append("carrier-vc-terminal: \(terminalRevision ?? "unknown")")
  lines.append("carrier-vc-frame: \(frameRevision ?? "unknown")")
  lines.append("runtime-home: \(runtimeHome)")
  lines.append("config-home: \(configHome)")
  return lines.joined(separator: "\n")
}
