# Vibecrafted release kickoff

## Public product

- Owner: `vetcoders/vibecrafted`
- Artifacts: six carriers, one commit
  - macOS desktop: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`
  - macOS CLI: `Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`
  - source fallback: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz`
  - Windows installers: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-windows-x64.msi` and `.exe`
  - Windows CLI: `Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-win32-x64.tar.gz`
- App: `Vibecrafted.app`
- Download: `https://github.com/vetcoders/vibecrafted/releases/latest` → DMG (macOS desktop), MSI/EXE (Windows), the platform Runtime Pack (CLI), or `-portable.tar.gz` (Linux / WSL2 / source fallback)
- Embedded donors: `vc-terminal`, `vc-frame`
- Entry: bundled `vc-start` with durable `workspace_id`

The public donor repositories supply source, not separate app, DMG, MSI,
installer or update channels. Tag builds use the same immutable donor commits
as the Linux and Windows builders: vc-frame `b19487648fba3451f61f153e8cd7b1edac40dfa5`
and vc-terminal `cfa2c367ed36ba9179a05fff32ab1221060cb04e`. The public source
archive digests are pinned in those builders. Manual DMG rehearsals may select
different donor refs; their signed receipts record the actual revisions.

Foundations use their own public channels: npm `@loctree/loctree` and
`@loctree/aicx`, PyPI `screenscribe`, and GitHub Releases
`vetcoders/prview-rs`. The Runtime Pack neither carries them nor installs a
bundled substitute. Existing working PATH installations are preserved.

Apple signing and notarization run in one of two places; publication stays an
explicit Founder-authorized step either way:

- **Hosted runner** (`.github/workflows/release-dmg.yml`, macos-15): builds,
  signs and notarizes `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` on every
  `v*` tag or via `workflow_dispatch` (ref, donor refs, notarize on/off) and
  uploads it as the `vibecrafted-release-<run_id>` artifact together with
  `release-output.json[.sig]`. The runner's home is declared ephemeral for the
  payload-hygiene gate; the operator's account and checkout are still refused.
  Nothing is published from CI.
- **Operator machine**:

```bash
make release
make portable
GH_TOKEN=... make publish-release
```

Either way `gh run download -n vibecrafted-release-<run_id>` / `dist/` is what
`publish-vibecrafted-release.sh` takes to the GitHub release.

Tag DMG builds require the tagged commit to be on `main`. Merging a PR into
`release/v4.3.1-candidate` does not satisfy that check. Promote the reviewed
candidate to `main` before retagging; the annotated tag must match `VERSION`.
The hosted notary step accepts either the complete App Store Connect API key
set or the complete Apple ID credential set, which it stores in an ephemeral
runner Keychain profile before invoking the builder.

## Exact-SHA rehearsal before tagging

After merging to `main`, rehearse the resulting commit. A green PR run does
not certify a later squash commit. Both triggers call the same local
`source-gate.yml` reusable workflow; only the tag caller enables immutable
annotated-tag verification. The runner, toolchain, tests and budgets have one
definition. Workflow permissions remain `contents: read`.

The Founder performs this ceremony from a clean, up-to-date `main` checkout:

```bash
git pull --ff-only origin main
RELEASE_SHA="$(git rev-parse HEAD)"
gh workflow run gate-rehearsal.yml --ref main
# Wait for a completed green run whose headSha is exactly RELEASE_SHA.
# Re-run admission immediately before tagging; failure stops the ceremony.
bash scripts/check-release-rehearsal.sh "$RELEASE_SHA" && \
  git tag -a "v$(tr -d '[:space:]' < VERSION)" "$RELEASE_SHA" -m "Release $(cat VERSION)"
# Only after admission and review, push that annotated tag explicitly.
```

If `main` moves before dispatch resolves its ref, that run certifies the new
SHA. Update the checkout and rehearse again; never substitute a green result
from another SHA. The read-only guard checks completed successful manual
rehearsals for the exact full SHA and refuses missing evidence or GitHub query
errors with that SHA and `gh workflow run gate-rehearsal.yml --repo
vetcoders/vibecrafted --ref main`. The publisher invokes the same guard before
its existing tag and artifact verification. Direct `git tag` commands bypass
this scripted ceremony; always use the guarded sequence above.

## Hosted runner admission

The shared `source-gate.yml` provisions Rust `1.97.0`, its WASM
targets, and a default toolchain before any source tests. They export the
provisioned `RUSTUP_HOME` and `CARGO_HOME` through `GITHUB_ENV`: test fixtures
isolate `HOME`, so setting a default alone does not make Rust reachable.
The release builder independently retains its exact Rust `1.96.0` contract.

| Source test                                                 | Hosted policy                                                                                      |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `test_compiled_vc_start_enters_lobby_through_bundled_shell` | Provision Rust; execute the real compiled entry.                                                   |
| `test_voc_admits_real_core` (both cases)                    | Provision Rust; execute both exact Rust tests with the source deck.                                |
| `test_five_upgrades_dispose_payload_keep_unique_receipts`   | Execute with strictly verified ad-hoc fixtures (`TeamIdentifier=not set`); no Developer ID bypass. |

These four cases remain active. The v4.3.3 codesign failure included a broken
pipe while parsing display output; the identity reader now consumes that
output without early pipeline termination and still matches the identifier
and team exactly. Authentic signed carrier replacement remains a separate
mandatory physical gate in the publisher.

Hosted Apple admission selects only the exact
`hosted-macos15-xcode26.3` Xcode bundle and measured clang/classic-linker pair;
it does not use the runner's independently rotating Command Line Tools pair.
Local profiles retain their own exact pins and linker policy. On hosted image
drift, inspect `release-toolchain-probe.yml`'s image/version/architecture and
resolved compiler/linker measurements, propose the new hosted tuple in a
bounded commit, and require a green probe compiling and executing both native
C and Rust fixtures through the production linker wrapper before admitting
that commit. Record the run URL, image and tuple in the review. Do not widen
version matching or move a local pin based on hosted measurements.

The Windows matrix also runs on `v*` tags. Tag carriers require `VC_SIGNING_KEY`
and must verify against the committed product public key; PR carriers keep
their isolated rehearsal signatures. Download the Windows pack and MSI/EXE
artifacts for the same exact commit into `dist/` before `make publish-release`.
The publisher remains explicit: retagging builds and verifies carriers but
does not itself publish a GitHub Release.

`make portable` needs neither signing identity nor notary account — it is a
provenance-bound source distribution, so it builds anywhere `git` and `python3`
do, and it re-validates the archive it just wrote before the bytes may leave the
machine. It writes `dist/portable-output.json`, which is how the publisher
resolves `PORTABLE_NAME` and the digests it must see again.

`publish-release` refuses a dirty tree, a missing successful exact-SHA manual
rehearsal, a non-annotated or unpushed tag, a
failed source gate, any open CodeQL alert on `main`, an invalid signature,
a portable manifest naming a revision other than HEAD, unexpected release
assets, failed Apple validation, a failed mounted-DMG walk-around, or a
downloaded tarball whose source-provenance does not close against HEAD. It
publishes only after downloading and byte-comparing the draft assets.

The ordered command sequence to cut 4.4.0 with that DMG attached — including
which `$HOME/.keys` files must be present and what each verification step
proves — is [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

## Public CTA

macOS: download the canonical `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` and
its `.dmg.sha256` from the latest release, verify the checksum, and open the DMG.

macOS without the App: download
`Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz` plus
its `.sha256` and `.sig`, then run `make install RUNTIME_PACK=<downloaded-path>`.
Reset with `make uninstall`; both buttons use the same receipted installer.

Linux, WSL2, or the explicit source fallback: download
`Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz` and its `.sha256` from
the same release, verify the checksum, unpack, and run the packed `install.sh`.

Windows: download the canonical `-windows-x64.msi` or `-windows-x64.exe` and
its `.sha256` from that same release. The installers are Authenticode-unsigned;
the Windows Runtime Pack additionally has a product-key `.sig`.

## Required proof

The generated release report must contain:

1. Security gate.
2. Exposed surface inventory.
3. Deployment topology and rollback decision.
4. Post-release smoke from the published URLs for every supported channel.

The canonical report lives under
`~/.vibecrafted/artifacts/vetcoders/vibecrafted/<YYYY_MMDD>/reports/` and is
also used as the GitHub Release notes.
