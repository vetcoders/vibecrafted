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
therefore not established. Sparkle is not pinned, not linked, and was not
installed (COMPILE_EMBARGO).

## One owner

1. `ProductUpdatePolicy` parses locator fields from `release-output.v1`. It does
   **not** treat `signature_valid`, notarization tickets, or a default bundle id
   as proof.
2. `ProductUpdateTrust` verifies the detached signature with `/usr/bin/openssl
   dgst -sha256 -verify` and the bundled `vibecrafted-signing-v1.pub` — the same
   check as `product_contract._verify_release_signature`. Payload hashes are
   checked against the signed document. When Python from the installed runtime
   is available, `python -m vibecrafted_core.product_contract release-output`
   is the established owner for codesign, stapler/spctl, and pack identity.
3. `ProductUpdateCoordinator` downloads the feed, `.sig`, DMG, and Runtime Pack
   into a staging directory, then exposes **Install Update**.
4. Same-App pack repair calls `install-runtime-pack.sh` through
   `NativeInstallerProcess` / `runRuntimePackInstaller` with
   `--expected-*-revision`. Receipts are not deleted.
5. A newer App is replaced by `Contents/Helpers/vc-app-update`
   (`scripts/vc-app-update.sh`, also a `vc-app-update` tool target). The helper
   waits for the UI pid, copies the bundle with `ditto`, and relaunches. It does
   not stop Frame, PTYs, workers, or rewrite PATH / MCP. The new App publishes
   the matching pack; this process does not publish a newer pack under the old
   App.

## User-visible states

Every check ends in a result:

| Phase | Meaning |
| --- | --- |
| unavailable | No signed HTTPS feed (or explicit fixture) / missing trust root. Installed version unchanged. |
| refused | Signature, hash, codesign, notarization, or App/pack identity failed. Nothing published. |
| ready | A verified candidate is staged. **Install Update** applies it. |
| retained | Interrupted or failed mutation, and the transaction receipt plus recovered identity still show the previous working version. |
| error | Timeout, HTTP failure, or an interrupt while a publish may still have been in flight. |
| restarting | The helper is replacing the UI. The window closes and reopens. Healthy is not claimed yet. |
| success | Installed App and pack match the candidate. |

Healthy is claimed only when the running App, installed pack, candidate
generation, and source revision match (`productUpdateClaimsHealthy`).

Quit / `applicationShouldTerminate` still terminate the UI only. Frame, PTYs,
workers, sessions, and foreign PATH/MCP ownership are not stopped as a shortcut.
`applicationWillTerminate` cancels an in-flight installer process and interrupts
the coordinator.

## Fixture seam

Production feeds must be HTTPS. `file://` and `http://` are rejected unless
**both** are set:

- `VIBECRAFTED_UPDATE_FIXTURE=1`
- `VIBECRAFTED_UPDATE_FIXTURE_ROOT` pointing at a directory that already contains
  `release-output.json` and `release-output.json.sig`

The fixture still verifies the detached signature with the bundled public key.
It does not weaken production policy. W2 / Founder provision a real signed
tuple when they want the skipped real-process test
(`test_product_update_real_process_fixture_preserves_sessions`).

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
  `ProductUpdatePolicyTests`.
- Installed update: signed feed + notarized App/pack, then prove console reopen
  and attach to a live Frame session without terminating PTYs.
- Confirm an interrupted install leaves the previous receipt recoverable.
- Security hooks skipped by this W1 checkpoint must be restored by the
  integrator.
