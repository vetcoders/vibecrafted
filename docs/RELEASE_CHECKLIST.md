# Cut 4.1.0 with both channels attached

Operator sequence. This file does not change the security boundary:
`.github/workflows/release.yml` stays `contents: read` and does not run
`gh release create`. Apple signing, notarization, and publication stay on
this macOS machine.

Positioning and product identity stay in
[RELEASE_KICKOFF.md](RELEASE_KICKOFF.md). This page is the ordered
commands.

Live install truth for strangers is [INSTALL.md](INSTALL.md): bootstrap
today; DMG and portable tarball when a release actually carries them. Both
build paths are contract-gated and both have been exercised locally — treat
the first published 4.1.0 artifacts as proven only once the walk-arounds
below pass on downloaded bytes.

## 0. What this cut must produce

One GitHub Release `v4.1.0` whose assets are exactly:

| Asset                                                 | Proves                                           |
| ----------------------------------------------------- | ------------------------------------------------ |
| `Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>.dmg`             | signed, notarized desktop product                |
| that name plus `.dmg.sha256`                          | checksum a stranger can `shasum -a 256 -c`       |
| `Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>-portable.tar.gz` | installable product for Linux / WSL2 / macOS CLI |
| that name plus `.sha256`                              | checksum a stranger can `sha256sum -c`           |
| `release-output.json`                                 | bound source revisions + DMG path                |
| `release-output.json.sig`                             | detached signature over that receipt             |

Exactly six assets. `publish-release` refuses anything else — including the
old source-tarball set. `portable-output.json` stays local: it is how the
publisher resolves the tarball name and digests, not something a stranger
needs.

`VERSION` is already `4.1.0`. There are local `v3.7.1` and `v4.0.0` tags,
but no `v4.1.0`. Latest **published** GitHub Release is still `v3.5.0`.

## 1. Signing material that must already exist

`make release` reads `$KEYS` (default `$HOME/.keys`):

| Path                                                 | Required for                       | What a missing file does                                          |
| ---------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------- |
| `$KEYS/signing-identity.txt`                         | codesign                           | `make release` dies before the app is signed                      |
| `$KEYS/Certificates.p12` + `$KEYS/cert_password.txt` | import into a temporary keychain   | skipped only if the Developer ID is already in the login keychain |
| `$KEYS/vibecrafted-signing.key`                      | detached `release-output.json.sig` | receipt signature cannot be produced                              |
| `$KEYS/.notary.env` **or** `NOTARY_PROFILE`          | `notarytool submit`                | notarization dies; `make dmg` still works for local testing       |

`.notary.env` must export `NOTARY_APPLE_ID`, `NOTARY_TEAM_ID`,
`NOTARY_PASSWORD`. Prefer a keychain profile (`NOTARY_PROFILE`) so the
password never sits in a file.

Also required on this Mac:

- Xcode CLI tools (`codesign`, `notarytool`, `stapler`, `xcrun`, `xcodebuild`, `xcodegen`)
- `cargo`, `uv`, `git`, `gh`, `hdiutil`, `otool`, `spctl`
- Sibling donor checkouts, clean:
  - `../vc-terminal` (override: `VIBECRAFTED_TERMINAL_REPO`)
  - `../vc-frame` (override: `VIBECRAFTED_FRAME_REPO`)
- This repo clean (`git status --porcelain` empty)

`make app` / `make dmg` can run without notarization for a local look.
`make release` and `make publish-release` cannot.

## 2. Confirm the tree you are about to tag

```bash
# this repo
test "$(tr -d '[:space:]' < VERSION)" = "4.1.0"
git status --porcelain          # must be empty
git rev-parse --abbrev-ref HEAD

# donors
git -C ../vc-terminal status --porcelain
git -C ../vc-frame status --porcelain

# signing material is present (names only — do not cat secrets)
ls -1 "$HOME/.keys/signing-identity.txt" \
      "$HOME/.keys/vibecrafted-signing.key"
test -f "$HOME/.keys/.notary.env" || test -n "${NOTARY_PROFILE:-}"

# local gates that the tag workflow will also run
make check
make unified-product-contract-gate
make test-core
make semgrep
```

What this proves: the version file, the donors, and the source gates agree
before you spend an hour in notarization.

### When a donor is dirty and you are not going to clean it

The Living Tree keeps donors dirty on purpose, and the builder refuses a dirty
donor because a receipt must not bind a SHA that could move mid-build. Do **not**
hand-roll `git worktree add --detach` into a temp dir: that is where the ghost
registration of 2026-08-11 came from. Use the flag instead:

```bash
make dmg RELEASE_FLAGS=--snapshot-donors
# or directly:
bash scripts/build-vibecrafted-release.sh --no-notarize --snapshot-donors
```

It creates a detached worktree at each donor's HEAD under
`build/unified-release/donor-snapshots/`, builds from those, and reaps them from
the same trap that ends the signing keychain — on success, on failure and on
Ctrl-C. The donor's own working tree, index and stashes are never touched, and
the receipt still binds the donor HEAD, because that is what the snapshot is.

Two things to know before you use it: the snapshot starts from a **cold cargo
target directory**, so the build is a full rebuild; and the check afterwards is
that each donor is back to the worktree count it had _before_ the build, not
that it has exactly one — donors legitimately carry other agents' worktrees.

```bash
git -C ../vc-frame worktree list      # same entries as before the build
git -C ../vc-terminal worktree list
```

**Use it for anything you intend to ship.** `--snapshot-donors` is the only mode
that rebuilds vc-frame's bundled WASM plugins. Those blobs are git-tracked build
output: `make release-binary` builds `--no-plugins` and embeds them with
`include_bytes!`, so without a rebuild the release ships whatever paths the
machine that last ran `make plugins-assets` happened to have. Measured on the
4.1.0 DMG that was 411 occurrences of the operator's home directory inside
`Contents/Helpers/vc-frame` alone. The rebuild adds about a minute and the
snapshot is the only tree we are entitled to regenerate into — doing it to the
living donor would rewrite tracked files another agent may be mid-edit on.

### The payload gate

Before a signature is spent, the builder greps the assembled bundle for every
path that exists only on this machine — your home directory, the checkout, both
donors, the snapshots — and refuses to continue if it finds one. There is no
allowlist, on purpose.

If it fires, the message names each offending file and how many times. Read it
as a real finding: `--remap-path-prefix` only covers rustc, and the payload has
at least four other producers (cc-rs, Swift/xcodebuild, uv's CPython, pip
console scripts). `scripts/payload_hygiene.py` explains each one.

You can ask the same question of an artifact you already have, without a
rebuild:

```bash
make payload-hygiene ARTIFACT=dist/Vibecrafted.app
make payload-hygiene ARTIFACT=dist/Vibecrafted_4.1.0-20260817-237d2814.dmg
make payload-hygiene ARTIFACT=dist/Vibecrafted_4.1.0-20260818-c52f1326-portable.tar.gz
```

A `.dmg` is attached read-only and detached again; a tarball is extracted into a
temp directory that is removed on every exit path. The artifact is never written
to.

## 3. Build, sign, notarize

```bash
make release
```

This is `scripts/build-vibecrafted-release.sh` with `KEYS=$HOME/.keys`.
It sets `MACOSX_DEPLOYMENT_TARGET=14.0` and hands every compiler in the build a
prefix map so no debug metadata names this machine: `RUSTFLAGS` for rustc,
`CFLAGS`/`CXXFLAGS` for the C sources cc-rs compiles, and `-debug-prefix-map`
for Swift through xcodebuild. Order matters — rustc applies the **last** match,
so the list runs broadest first. The payload gate above is what checks the
result rather than assuming it.

Expected outputs under `dist/`:

```text
Vibecrafted.app
Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>.dmg
Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>.dmg.sha256
release-output.json
release-output.json.sig
```

Then build the portable channel from the same clean tree:

```bash
make portable
```

No signing identity, no notary account, no Xcode: `git` and `python3` are
enough, which is why this channel can also be built on Linux. It adds:

```text
Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>-portable.tar.gz
Vibecrafted_4.1.0-<YYYYMMDD>-<sha8>-portable.tar.gz.sha256
portable-output.json
```

The builder refuses a dirty tree, then unpacks what it just wrote, revalidates
the payload against this exact HEAD, and makes the packed `install.sh` answer
for itself. If any of that fails, no bytes are published.

## 4. Local verification (before any tag)

```bash
cd dist
shasum -a 256 -c Vibecrafted_4.1.0-*.dmg.sha256
xcrun stapler validate Vibecrafted_4.1.0-*.dmg
spctl --assess --type open --context context:primary-signature --verbose=2 \
  Vibecrafted_4.1.0-*.dmg

uv run --project vibecrafted-core verify-vibecrafted-walkaround verify-release \
  --release-output dist/release-output.json \
  --signature dist/release-output.json.sig

uv run --project vibecrafted-core verify-vibecrafted-walkaround walkaround \
  --release-output dist/release-output.json \
  --signature dist/release-output.json.sig \
  --output dist/vibecrafted-4.1.0-walkaround.json
```

| Command                      | What a pass proves                                       |
| ---------------------------- | -------------------------------------------------------- |
| `shasum -a 256 -c`           | the checksum file matches the bytes on disk              |
| `stapler validate`           | Apple stapled a notarization ticket to the DMG           |
| `spctl --assess --type open` | Gatekeeper will open that DMG                            |
| `verify-release`             | the signed receipt names this tree and this DMG          |
| `walkaround`                 | the mounted DMG is the product, not a sibling look-alike |

Portable channel, same idea without an Apple ticket to lean on:

```bash
tar -xzf dist/Vibecrafted_4.1.0-*-portable.tar.gz -C "$(mktemp -d)"
python3 scripts/distribution_manifest.py check \
  --root <unpacked>/vibecrafted-4.1.0 \
  --expected-owner-repo vetcoders/vibecrafted \
  --expected-source-revision "$(git rev-parse HEAD)"
```

| Command                       | What a pass proves                                       |
| ----------------------------- | -------------------------------------------------------- |
| `sha256sum -c`                | the checksum file matches the bytes on disk              |
| `distribution_manifest check` | the payload is the allowlisted projection of this commit |

## 5. Operator buttons — tag and source gate

`publish-release` will not invent a tag and will not push one.

```bash
# annotated tag at this exact HEAD (not a lightweight tag)
git tag -a v4.1.0 -m "Vibecrafted 4.1.0"

# OPERATOR BUTTON — this worker does not push
git push origin v4.1.0
```

Wait for `.github/workflows/release.yml` (`Release source gate`) to go
green on that tag. `publish-release` looks up that run and dies if it is
missing.

```bash
gh run list --workflow release.yml --commit "$(git rev-parse HEAD)"
```

What this proves: VERSION matches the tag, the tag is annotated, Semgrep
and the product-contract / core / installer gates passed on the immutable
tag SHA. It does **not** build or upload a DMG. That is intentional.
Do not add `contents: write` or `gh release create` to `release.yml`.

Also required: zero open CodeQL alerts on `main`.

```bash
gh api "/repos/vetcoders/vibecrafted/code-scanning/alerts?state=open&ref=refs/heads/main&per_page=1"
```

## 6. Publish

```bash
GH_TOKEN=... make publish-release
```

`scripts/publish-vibecrafted-release.sh` then:

1. refuses a dirty tree, a missing/unpushed/non-annotated tag, or a tag
   that is not HEAD
2. verifies the local signed receipt
3. verifies `portable-output.json` names this exact HEAD, and checks the
   local tarball checksum
4. creates a **draft** release if needed
5. uploads the six assets
6. downloads them back into a temp dir and `cmp`s every byte
7. re-runs `verify-release`, `walkaround`, `stapler`, and `spctl` on the
   downloaded DMG
8. unpacks the downloaded tarball into a fresh root, revalidates its
   source-provenance against HEAD, and runs the packed `install.sh`
9. writes the four-section release report — now covering both channels —
   under `~/.vibecrafted/artifacts/vetcoders/vibecrafted/<YYYY_MMDD>/reports/`
10. undrafts the release and marks it latest

What a pass proves: the bytes a stranger downloads are the bytes you
notarized, and the mounted app matches the signed receipt.

## 7. Public confirmation

```bash
gh release view v4.1.0 --json tagName,isDraft,isLatest,assets \
  --jq '{tag:.tagName,draft:.isDraft,latest:.isLatest,assets:[.assets[].name]}'
```

Expected: `draft=false`, `latest=true`, four names, one of them matching
`Vibecrafted_4.1.0-*.dmg`.

Then update the staged Homebrew cask coordinates in
`packaging/homebrew/Casks/vibecrafted-app.rb` (in the tap repo, after the
tap exists). See [packaging/homebrew/README.md](../packaging/homebrew/README.md).

## 8. Stop conditions

- Dirty vibecrafted / vc-terminal / vc-frame tree
- Missing `$HOME/.keys` material
- `release.yml` red or still running
- Open CodeQL alert on `main`
- `spctl` or `stapler` fail on either the local or the downloaded DMG
- Temptation to "just `gh release upload`" a source tarball onto `v4.1.0`
  — `publish-release` will reject unexpected assets

If you only need a local unsigned look, stop after `make dmg` and do not
tag.
