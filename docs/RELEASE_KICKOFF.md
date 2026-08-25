# Vibecrafted release kickoff

## Public product

- Owner: `vetcoders/vibecrafted`
- Artifacts: three carriers, one commit
  - macOS desktop: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`
  - macOS CLI: `Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz`
  - every other system: `Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz`
- App: `Vibecrafted.app`
- Download: `https://github.com/vetcoders/vibecrafted/releases/latest` → DMG (macOS desktop), `RuntimePack_...tar.gz` (macOS CLI), or `-portable.tar.gz` (Linux / WSL2 / source fallback)
- Embedded donors: `vc-terminal`, `vc-frame`
- Entry: bundled `vc-start` with durable `workspace_id`

The donor repositories never publish an app, DMG, MSI, installer or update
channel. Apple signing and notarization run in one of two places; publication
stays an explicit operator step either way:

- **Hosted runner** (`.github/workflows/release-dmg.yml`, macos-15): builds,
  signs and notarizes `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` on every
  `v*` tag or via `workflow_dispatch` (ref, donor refs, notarize on/off) and
  uploads it as the `vibecrafted-dmg-<run_id>` artifact together with
  `release-output.json[.sig]`. The runner's home is declared ephemeral for the
  payload-hygiene gate; the operator's account and checkout are still refused.
  Nothing is published from CI.
- **Operator machine**:

```bash
make release
make portable
GH_TOKEN=... make publish-release
```

Either way `gh run download -n vibecrafted-dmg-<run_id>` / `dist/` is what
`publish-vibecrafted-release.sh` takes to the GitHub release.

`make portable` needs neither signing identity nor notary account — it is a
provenance-bound source distribution, so it builds anywhere `git` and `python3`
do, and it re-validates the archive it just wrote before the bytes may leave the
machine. It writes `dist/portable-output.json`, which is how the publisher
resolves `PORTABLE_NAME` and the digests it must see again.

`publish-release` refuses a dirty tree, a non-annotated or unpushed tag, a
failed source gate, any open CodeQL alert on `main`, an invalid signature,
a portable manifest naming a revision other than HEAD, unexpected release
assets, failed Apple validation, a failed mounted-DMG walk-around, or a
downloaded tarball whose source-provenance does not close against HEAD. It
publishes only after downloading and byte-comparing the draft assets.

The ordered command sequence to cut 4.1.0 with that DMG attached — including
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

## Required proof

The generated release report must contain:

1. Security gate.
2. Exposed surface inventory.
3. Deployment topology and rollback decision.
4. Post-release smoke from the published URL — both channels.

The canonical report lives under
`~/.vibecrafted/artifacts/vetcoders/vibecrafted/<YYYY_MMDD>/reports/` and is
also used as the GitHub Release notes.
