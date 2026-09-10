# In-app update — owner, limits, and remaining provisioning

The App has a discoverable **Check for Updates…** action (tray + application
menu; accessibility also accepts **Sprawdź aktualizacje**). Progress shows the
installed version and the available version. **Install Update** applies a
verified candidate. This is not Sparkle, and it is not a Finder replacement of
a running `.app`.

## Sparkle review (why it is not pinned)

Codescribe's owner was read at
`/Volumes/vc-workspace/vetcoders/codescribe/macos/Codescribe/Services/UpdaterService.swift`:

- Sparkle 2 `SPUStandardUpdaterController(startingUpdater: true)`
- Feed `SUFeedURL` = `https://codescribe.vetcoders.io/appcast.xml`
- `SUPublicEDKey` injected at build; empty key fail-closes
- License: MIT ([Sparkle LICENSE](https://github.com/sparkle-project/Sparkle/blob/master/LICENSE))
- Maintenance: active Sparkle Project 2.x

Sparkle cannot consume this product's signed artifact
(`io.vetcoders.vibecrafted.release-output.v1` + 256-byte RSA PKCS#1 v1.5 SHA-256
detached signature + `vibecrafted-signing-v1.pub`). It has no hook into
`install-runtime-pack.sh` receipts. Adding it would require a second feed, a
second public key, and still a coordinated pack transaction. Necessity is
therefore not established. This is an observed-contract decision, not a
Founder ban on Sparkle. Sparkle is not pinned, not linked, and was not
installed (COMPILE_EMBARGO).

## One owner

1. `ProductUpdatePolicy` parses locator fields from `release-output.v1`. It does
   **not** treat `signature_valid`, notarization tickets, or a default bundle id
   as proof. Admission requires the established verifier owner
   `product_contract.release-output`, observed codesign identifier
   `io.vetcoders.vibecrafted`, and Team ID `MW223P3NPX`.
2. `ProductUpdateTrust` verifies the detached signature with `/usr/bin/openssl
   dgst -sha256 -verify` and the bundled `vibecrafted-signing-v1.pub` — the same
   check as `product_contract._verify_release_signature`. Payload hashes are
   checked against the signed document. Python is resolved only from the already
   trusted generation (`<installRoot>/bin/python3`), never `PATH` or
   `VIBECRAFTED_PYTHON`. That interpreter runs
   `python -m vibecrafted_core.product_contract release-output` against the
   staged `release-output.json` + `.sig`. Codesign uses `--verify --strict`
   before `--display`. Stapler is required. Fixture transport is `file://` only;
   it does not skip stapler or codesign.
3. `ProductUpdateCoordinator` downloads the feed, `.sig`, DMG, and Runtime Pack
   into a staging directory. The DMG is extracted **before** observation so the
   proof is not a default identity. **Install Update** then either publishes a
   matching pack or admits the helper.
4. Same-App pack repair calls `install-runtime-pack.sh` through
   `NativeInstallerProcess` / `runRuntimePackInstaller` with
   `--expected-*-revision`. Receipts are not deleted.
5. A newer App is replaced by one helper: `scripts/vc-app-update.sh`, installed
   as `Contents/Helpers/vc-app-update`. There is no compiled Swift mutation twin
   and no `dist/vc-app-update` preference. The App launches `/bin/bash` plus
   that bundled script. A compile-time `#filePath` walk is not helper authority.

## Helper transaction

The script is the only mutation implementation. There is no compiled Swift
mutation twin. Two `mv` calls are **not** atomic: every phase is journaled
before the destination changes, and resume/rollback never delete a foreign
path. A destination lock (`.vc-update.lock` + live owner pid/start) refuses
overlapping retries (exit 13).

The script:

- survives parent UI death (`trap '' HUP`; the App also `setpgid`s the helper)
- preflights source, destination, capture ownership, and signed identity
  **before** the parent may quit
- persists a helper-owned **READY** admission bound to transaction id,
  candidate identity, destination, and parent PID/`lstart`.
  `Process.isRunning` is not admission. Rejection or admission timeout leaves
  the current UI alive
- waits for that bound parent identity, then **fails before mutation**
  (exit 5) if it is still live
- refuses symlink source or destination
- writes a durable phase journal (`app-update-journal.v1`) with a unique
  owned capture/hash **before** any destination mutation
- captures the previous destination under
  `.vc-update-capture-<transaction>/prior.app` (sibling captures are left alone)
- prepares `.vc-update-prepared-<transaction>.app`, verifies codesign
  identity, journals the displace, then `mv`s the prepared bundle into place
- revalidates identifier + Team ID on source, prepared, and destination
- writes the validated terminal replacement receipt (`replaced: true`)
  **before** `/usr/bin/open -n`. Relaunch is a sidecar
  (`receipt.relaunch.json`); a missing receipt is never success
- keeps the capture so pack failure can restore `prior.app`

Restore is a separate owner path (`--mode restore` →
`restore_previous_tuple`). It never dittos the failed new app onto
`prior.app`. The failed destination is quarantined as `failed-new.app`.
Capture validity is checked before any destructive restore. A restore
receipt must not republish the failed pack (`decideProductUpdateHandoff`
returns `rolledBack`).

The parent records **READY admission**, not replacement.
`applicationWillTerminate` calls `noteUIShutdownPreservingHandoff` when a
helper is admitted and does **not** terminate that helper. User cancel
before READY still kills the helper. Missing replacement receipts are not
success. A live admitted helper with no receipt is a bounded wait, not
deletion of `pending-handoff.json`.

`applicationDidFinishLaunching` adopts the pending handoff **before**
`connectCommandDeck`. Waiting / publishing / restoring block the startup
installer so repair cannot race adoption. Interrupted or failed helpers
leave journal, admission, and recovery records.

## User-visible states

Every check ends in a result:

| Phase | Meaning |
| --- | --- |
| unavailable | No signed HTTPS feed (or explicit fixture) / missing trust root. Installed version unchanged. |
| refused | Signature, hash, codesign, notarization, or App/pack identity failed. Nothing published. |
| ready | A verified candidate is staged. **Install Update** applies it. |
| retained | Interrupted or failed mutation, and the transaction receipt plus recovered identity still show the previous working version. |
| error | Timeout, HTTP failure, or an interrupt while a publish may still have been in flight. |
| restarting | The helper was admitted. The window closes and reopens. Healthy is not claimed yet. |
| finishing | The new UI opened and is applying the matching Runtime Pack, or waiting for a live helper receipt. |
| success | Installed App and pack match the candidate. |

Healthy is claimed only when the running App, installed pack, candidate
generation, and source revision match (`productUpdateClaimsHealthy`). The
parent never claims healthy from a missing helper receipt.

Quit / `applicationShouldTerminate` still terminate the UI only. Frame, PTYs,
workers, sessions, and foreign PATH/MCP ownership are not stopped as a shortcut.
`applicationWillTerminate` cancels an in-flight pack installer and interrupts
the coordinator **only when no helper has been admitted**.

Blocking openssl / codesign / stapler / hdiutil / verifier work runs off the
UI actor through `runProductUpdateBoundProcess` (bounded lifetime, concurrent
pipe drain, terminate then KILL). Terminal phases do not depend on a spinner
alone.

## Fixture seam

Production feeds must be HTTPS. `file://` and `http://` are rejected unless
**both** are set:

- `VIBECRAFTED_UPDATE_FIXTURE=1`
- `VIBECRAFTED_UPDATE_FIXTURE_ROOT` pointing at a directory that already contains
  `release-output.json` and `release-output.json.sig`

The fixture still verifies the detached signature, payload hashes, codesign
identity, Team ID, stapler, and pack identity. It may use local transport. It
does not weaken production policy. Unsigned copies are refused. Harness flags
(`--open-bin`, `--fail-after`, `--allow-unsigned`) require
`VIBECRAFTED_UPDATE_HELPER_HARNESS=1` and are not production switches.

W2 may parameterize the already-built signed pair
`dist/*20260910-e37be2c9*` + `dist/release-output.json` (or
`VIBECRAFTED_UPDATE_FIXTURE_ROOT`) for
`test_product_update_signed_fixture_positive_path`. Workers must not execute
or replace that tuple. Missing the pair skips that one test; it is not an
excuse to skip unsigned real-process coverage.

Do not invent a production URL, EdDSA key, or signed update.

## What is actually provisioned today

Real release metadata exists (`io.vetcoders.vibecrafted.release-output.v1`,
`key_id = vibecrafted-signing-v1`). The pack trust root ships as
`Contents/Resources/runtime-pack/vibecrafted-signing-v1.pub`. The helper source
and release-packaging copy are in tree.

These production surfaces do **not** exist yet:

1. A hosted HTTPS feed (`VCUpdateFeedURL` in `Info.plist`). The key is omitted
   until that feed exists. Check for Updates then shows the bounded unavailable
   card — that is the honest product state.
2. A Founder-signed fixture tuple in this worktree (the private key is not
   present). Negative signature tests use invalid bytes on purpose.

Until (1) is provisioned, a normal install cannot download a production update.
The mechanism, helper, verifier, installer wiring, and fixture seam are in
source.

## Physical acceptance still open (W2 / Founder)

- Compile and run `tests/tui/test_product_update.py` and
  `ProductUpdatePolicyTests`. This W1 did not execute them (COMPILE_EMBARGO).
- Installed update: signed feed + notarized App/pack, then prove console reopen
  and attach to a live Frame session without terminating PTYs.
- Confirm an interrupted install leaves previous unique captures recoverable
  and that pack failure restores `prior.app` through the same helper (UI exit,
  identity wait, verified restore, previous-tuple relaunch) without
  republishing the failed pack.
- Two destination renames remain a journaled recoverable gap, not a platform
  atomic exchange.
- Security hooks skipped by this W1 checkpoint must be restored by the
  integrator.
