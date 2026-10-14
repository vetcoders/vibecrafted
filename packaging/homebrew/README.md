# Homebrew tap staging

These files are the tap contents for a future `vetcoders/homebrew-tap`.
They are not published. `brew install` against them will fail until the
operator creates the tap and fills the SHA-256 / DMG coordinates.

## What each file is

| File                       | Homebrew kind | Installs                                    | When it can go live                            |
| -------------------------- | ------------- | ------------------------------------------- | ---------------------------------------------- |
| `Formula/vibecrafted.rb`   | formula       | command deck from the tagged source archive | after the GitHub Release for its tag is public |
| `Casks/vibecrafted-app.rb` | cask          | `Vibecrafted.app` from the signed DMG       | after `make publish-release` attaches that DMG |

The names are deliberately different. Homebrew will not host a formula and
a cask that share the token `vibecrafted` in one tap.

The formula does **not** pretend to be a Cellar-complete runtime. It
stages the repo into `libexec`, sets `VIBECRAFTED_ROOT`, and links
`scripts/vibecrafted`. Runtime generations still live under
`~/.local/share/vibecrafted` (XDG), matching `docs/INSTALL.md`.

The cask is macOS 14+ arm64 only. There is no Intel bottle and no Windows
cask.

## Operator: create the tap

```bash
# 1. Create an empty public repo named exactly homebrew-tap
gh repo create vetcoders/homebrew-tap --public --description "Homebrew tap for Vibecrafted"

# 2. Seed it from this staging tree
git clone git@github.com:vetcoders/homebrew-tap.git
mkdir -p homebrew-tap/Formula homebrew-tap/Casks
cp packaging/homebrew/Formula/vibecrafted.rb homebrew-tap/Formula/
cp packaging/homebrew/Casks/vibecrafted-app.rb homebrew-tap/Casks/
# optional: copy this README into the tap root

# 3. After the release for v<VERSION> is public (formula) / attaches the DMG (cask):
shasum -a 256 /tmp/v<VERSION>.tar.gz
# paste the digest into Formula/vibecrafted.rb
# paste version, YYYYMMDD, sha8, and DMG sha256 into Casks/vibecrafted-app.rb

# 4. Commit in the tap repo and push. Do not commit those values here
#    until the artifacts exist — a 404 URL is worse than a placeholder.
```

Users then:

```bash
brew tap vetcoders/tap
brew install vibecrafted              # CLI
brew install --cask vibecrafted-app   # desktop app, after the DMG exists
```

`brew tap vetcoders/tap` resolves to `vetcoders/homebrew-tap`.

## Operator: refresh after a release

1. Cut the release named in `VERSION` with a DMG using [docs/RELEASE_CHECKLIST.md](../../docs/RELEASE_CHECKLIST.md).
2. Download the source archive and the DMG.
3. `shasum -a 256` both.
4. Update `version` / `sha256` / cask CSV fields in the **tap** repo.
5. `brew audit --strict --online vetcoders/tap/vibecrafted`
6. `brew audit --cask --online vetcoders/tap/vibecrafted-app` (only after the DMG URL 200s)

Do not send these files to `homebrew-core` or `homebrew-cask`. The license
is BUSL-1.1, the CLI is not prefix-pure, and the DMG is still unpublished.

## Honesty constraints

- Do not add a Windows bottle or a `depends_on :windows`.
- Do not point the cask at `Vibecrafted.dmg` (legacy unversioned name).
  The published name is `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg`.
- Do not mark the formula `keg_only` unless a real name clash appears.
- Do not run `brew create` against `https://vibecrafted.io/install.sh`.
  Piped bootstrap is not a Homebrew install.
- The formula stages the tagged source tree into `libexec`; it is not a
  Runtime Pack install. Before publishing the tap, run `vibecrafted doctor`
  from the formula on a clean machine. If the command deck needs a staged
  runtime generation, bind the formula to the signed Runtime Pack instead.
