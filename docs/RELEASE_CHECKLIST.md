# Cut 4.3.3 with six carriers

This is the Agent-Operator preparation sequence. Merging into `main`, creating
and pushing the release tag, publication, and replacing a live App remain
Founder actions. [RELEASE_KICKOFF.md](RELEASE_KICKOFF.md) defines the product;
[INSTALL.md](INSTALL.md) defines the cold installation path.

## 0. One immutable release

One GitHub Release `v4.3.3` carries one framework commit and one recorded
terminal/Frame donor tuple. `VERSION` is already `4.3.3`.

The publisher's closed allowlist contains **16 assets**:

| Carrier or receipt                                                    | Required sidecars | Evidence                              |
| --------------------------------------------------------------------- | ----------------- | ------------------------------------- |
| `Vibecrafted_4.3.3-<YYYYMMDD>-<sha8>.dmg`                             | `.sha256`         | signed, notarized, stapled macOS App  |
| `Vibecrafted_RuntimePack_4.3.3-<YYYYMMDD>-<sha8>-darwin-arm64.tar.gz` | `.sha256`, `.sig` | same signed macOS runtime             |
| `Vibecrafted_4.3.3-<YYYYMMDD>-<sha8>-portable.tar.gz`                 | `.sha256`         | provenance-bound source distribution  |
| `Vibecrafted_4.3.3-<YYYYMMDD>-<sha8>-windows-x64.msi`                 | `.sha256`         | Windows per-user installer            |
| `Vibecrafted_4.3.3-<YYYYMMDD>-<sha8>-windows-x64.exe`                 | `.sha256`         | Windows setup executable              |
| `Vibecrafted_RuntimePack_4.3.3-<YYYYMMDD>-<sha8>-win32-x64.tar.gz`    | `.sha256`, `.sig` | product-key-signed Windows runtime    |
| `release-output.json`                                                 | `.sig`            | source tuple and bound macOS carriers |

Sidecars append their suffix to the complete carrier filename.
Windows MSI/EXE are Authenticode-unsigned; their documented trust path is
checksum verification, plus the embedded Runtime Pack's product signature.

`portable-output.json` is local publisher input. Native Linux packs are built
and installed by the Linux matrix; they are outside this publisher's closed
allowlist. Extra uploads require a separately reviewed publication contract.
The portable archive is a source distribution, not proof of a native runtime.

## 1. Preflight

Inspect the exact source and donor commits before any build:

```bash
test "$(tr -d '[:space:]' < VERSION)" = "4.3.3"
git status --porcelain
git rev-parse HEAD
git -C ../vc-terminal status --porcelain
git -C ../vc-frame status --porcelain
python3 scripts/version_bump.py --check
make check
make unified-product-contract-gate
make test-core
make semgrep
make test-source
```

Run pytest with `PYTHONPATH` unset and an isolated `VIBECRAFTED_HOME`.
`tests/tui` and `vibecrafted-core/tests` require separate pytest invocations.

Signing inputs live in `$HOME/.keys`: `signing-identity.txt`,
`Certificates.p12` with `cert_password.txt` when importing an identity, and
`vibecrafted-signing.key`. Inspect presence only; never print their contents.
Use a verified Keychain `NOTARY_PROFILE` or the complete notary API-key set.

Run local release commands through `zsh -ic`. `make release-prereqs` verifies
the pinned Rust toolchain, WASM targets, linker and free disk. Stable Xcode is
the default; the documented `VIBECRAFTED_ALLOW_BETA_XCODE=1` override is an
explicit build choice whose actual compiler/linker result must be recorded.

### In-flight rehearsal — no publish button

`make release-rehearsal` checks identity, inventories and recipe syntax without
tagging, compiling, notarizing or publishing. It does not replace the gates
above or artifact execution.

## 2. Build and notarize from a clean commit

```bash
make release RELEASE_FLAGS=--snapshot-donors KEYS="$HOME/.keys"
make portable
```

Set `NOTARY_PROFILE` explicitly in the calling shell.
`--snapshot-donors` binds detached source snapshots and rebuilds Frame's WASM
plugins. The builder also snapshots the framework, so later Living Tree edits
cannot change the captured payload. Confirm the recorded revisions against
the intended tuple. Record donor worktree counts before and after the build.

Monitor free disk during compilation. Preserve Founder configuration, agents
and sessions; a build is not permission to replace the live App.

`make dmg` uses `--no-notarize` and is only a pipeline debugging surface.
Artifacts handed to a person must be notarized and stapled.

## 3. Verify exact local carriers

Resolve filenames from `dist/release-output.json` and
`dist/portable-output.json`; avoid selecting an older artifact with a glob.

From the repository root:

```bash
uv run --project vibecrafted-core verify-vibecrafted-walkaround verify-release \
  --release-output dist/release-output.json \
  --signature dist/release-output.json.sig
uv run --project vibecrafted-core verify-vibecrafted-walkaround walkaround \
  --release-output dist/release-output.json \
  --signature dist/release-output.json.sig \
  --output dist/vibecrafted-4.3.3-walkaround.json
```

Verify each checksum, detached Runtime Pack signature, DMG staple and
Gatekeeper assessment. The walk-around executes the mounted product; a
version string or signature alone does not prove first run.

Physical update acceptance needs **two authentic signed releases**:

```bash
VIBECRAFTED_UPDATE_FIXTURE_ROOT="$PWD/dist" \
VIBECRAFTED_UPDATE_PRIOR_FIXTURE_ROOT="<prior signed release directory>" \
VIBECRAFTED_UPDATE_PRIOR_SOURCE_REVISION="<full prior framework SHA>" \
make test-product-update-physical
```

The prior directory must contain its original `release-output.json`, signature,
DMG and Runtime Pack. Never regenerate a prior receipt or relabel the candidate
as the prior release. These tests use isolated destinations and must preserve
the Founder’s live runtime. Missing fixtures are a failed required gate.

## 4. Founder buttons and native Windows evidence

After reviewing the exact candidate, promote it to `main` and tag that commit:

```bash
git tag -a v4.3.3 -m "Vibecrafted 4.3.3"
git push origin v4.3.3
gh run list --workflow release.yml --commit "$(git rev-parse HEAD)"
```

The tag must be annotated, match `VERSION`, and name the exact release commit.
Hosted macOS tag builds additionally require ancestry on `main`.
`Release source gate` must pass on that exact SHA; its read-only permissions
and separation from publication remain part of the contract.

The Windows matrix builds the Runtime Pack once, packages MSI/EXE, and installs,
runs doctor, and uninstalls them on separate native Windows runners.
Branch/PR artifacts use rehearsal signatures. Tag artifacts must use
`VC_SIGNING_KEY` and verify against the committed product public key.
Download the **tag's** Windows pack, MSI/EXE and sidecars into `dist/`; verify
their exact SHA/date/version stem agrees with the macOS receipt.

Before publication, inspect the latest source-gate run and require zero open
CodeQL alerts on `main`. A historical green run is not candidate evidence.

## 5. Publication and cold proof

```bash
GH_TOKEN=... \
VIBECRAFTED_UPDATE_PRIOR_FIXTURE_ROOT="<prior signed release directory>" \
VIBECRAFTED_UPDATE_PRIOR_SOURCE_REVISION="<full prior framework SHA>" \
make publish-release
```

Use the existing publisher. It validates local carriers, signatures and exact
source identity; requires the source/CodeQL/physical-update gates; creates a
draft; uploads exactly 16 assets; downloads and byte-compares them; repeats
Apple validation and mounted-product walk-around; validates the portable
archive; and performs isolated Runtime Pack install/uninstall.

Only after those checks does it publish and mark latest. The release report
lives under `~/.vibecrafted/artifacts/vetcoders/vibecrafted/<YYYY_MMDD>/reports/`.
It must record security, exposed surfaces, topology and downloaded cold proof.

Finally verify the public release asset set, HTTPS install/landing paths,
canonical metadata, sitemap/robots and first-run behavior from public downloads.
A local notarized DMG is artifact evidence; publication and installed runtime
each need their own receipt.
