# In-app update — owner, limits, and remaining provisioning

The App now has a discoverable **Check for Updates…** action (tray + application
menu; accessibility also accepts **Sprawdź aktualizacje**). That action is not
Sparkle and it is not a Finder replacement of a running `.app`.

## One owner

`ProductUpdatePolicy` admits a candidate. `ProductUpdateCoordinator` hosts one
bounded check. Publication, if any, goes through the existing Runtime Pack
installer (`install-runtime-pack.sh` via `NativeInstallerProcess` /
`runRuntimePackInstaller`) with the same `--expected-*-revision` identity the
onboarding path already uses. Receipts are not deleted. Conflict checks are not
bypassed.

Codescribe's `UpdaterService` is Sparkle 2 (`SPUStandardUpdaterController`,
`SUFeedURL`, `SUPublicEDKey`). Sparkle is MIT and maintained, but it replaces a
standalone `.app` outside this product's receipt transaction. Adding it would
create a second publisher and can leave a mixed App/pack generation. It is
therefore not pinned and not linked.

## User-visible states

Every check ends in a result:

| Phase | Meaning |
| --- | --- |
| unavailable | Feed, `vibecrafted-signing-v1.pub`, replacement helper, or staged payloads are missing. Installed generation unchanged. |
| refused | Signature or App/pack identity mismatch. Nothing published. |
| readyToReplace | A newer signed App+pack was admitted. This running App does **not** publish that pack. Offers **Quit App** (UI only) so a future helper can replace the UI. |
| retained | Interrupted or failed same-App pack repair. Previous working generation remains; the receipt is still authority. |
| error | Feed timeout or HTTP failure (15s). Installed generation unchanged. |
| success | Installed App and pack already match the candidate, or same-App pack repair published a matching generation. Offers **Quit App** (UI only). |

Healthy is claimed only when the running App, installed pack, candidate
generation, and source revision match (`productUpdateClaimsHealthy`). A mixed
generation is never called healthy. A newer candidate never runs
`install-runtime-pack.sh` from the old App (`readyToReplace`). Same-App pack
repair is the only mutation this process will ask of the installer, and it
still uses `--expected-*-revision` plus the existing receipt transaction.

Quit App / `applicationShouldTerminate` still terminate the UI only. Frame,
PTYs, workers, sessions, and foreign PATH/MCP ownership are not stopped as a
shortcut to replace the UI. `applicationWillTerminate` interrupts an in-flight
check so a mid-quit update is retained, not published.

## What is actually provisioned today

Real release metadata exists (`io.vetcoders.vibecrafted.release-output.v1`,
`key_id = vibecrafted-signing-v1`, notarization tickets, `source_revisions`).
The pack trust root ships as `Contents/Resources/runtime-pack/vibecrafted-signing-v1.pub`.

These production surfaces do **not** exist yet:

1. A hosted HTTPS feed (`VCUpdateFeedURL` in `Info.plist`) that serves a signed
   `release-output.v1` document. The key is omitted until that feed exists.
2. A Sparkle appcast or EdDSA public key. Do not invent one.
3. `Contents/Helpers/vc-app-update` — a signed, notarized helper that replaces
   `/Applications/Vibecrafted.app` (or the running bundle) **after** a UI-only
   quit, then relaunches the UI so the console can attach to the live Frame
   session. W1 does not invent that helper's argv or launch it. The call site
   today is UI-only quit (`requestQuit` → `.terminateNow`). When the helper
   ships, it must wait for this UI pid to exit, replace the bundle, and
   relaunch without touching Frame, PTYs, workers, receipts, PATH, or MCP.
4. In-app download of DMG/pack bytes. A feed that only names relative
   `runtime_pack.path` / `dmg.path` is treated as unavailable until those
   artifacts are staged as local absolute files (`assets.runtime_pack`,
   `assets.app`).
5. A notarized update that this cut can claim to have installed. W1 did not
   build, sign, notarize, or run the installer.

Until 1–4 are provisioned, Check for Updates is supposed to show the bounded
unavailable card with the exact gap. That is the honest product state, not a
failure of the owner.

## Physical acceptance still open (W2 / Founder)

- Compile and run `tests/tui/test_product_update.py` and the Command Deck
  integration tray contract.
- Installed update: signed feed + staged notarized App/pack + helper, then
  prove console reopen and attach to a live session.
- Confirm an interrupted install leaves the previous receipt recoverable.
- Security hooks skipped by this W1 checkpoint must be restored by the
  integrator.
