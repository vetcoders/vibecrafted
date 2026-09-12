---
title: "Terminal frontier"
description: "The optional operator terminal layer: starship prompt, atuin history, and vc-frame layouts installed from the runtime generation."
section: configuration
order: 30
---

# Terminal frontier

## Vibecrafted Terminal

Opening **Terminal** in the app, or running `vc-terminal`, opens an independent
interactive shell. It does not create or attach a Frame workspace. Start a
workspace explicitly with `vc-start --repo <path>`, find existing workspaces with
`vc-frame list-sessions`, or return to one with `vc-frame attach <name>`.

The installed terminal uses its own startup files under
`~/.config/vibecrafted/vc-terminal/`. It loads neither your private `.zshrc` nor
your private login profile. Your other terminal applications keep their existing
setup.

The profile initializes installed Starship, Atuin, zoxide, zsh-autosuggestions
and zsh-syntax-highlighting. Atuin uses **Ctrl+R**; ordinary Up-arrow behavior is
preserved. Zoxide provides `z <directory-name>` after you have visited a directory.
Tab completion uses zsh and the installed product commands' help.

Prompt and history preferences live in `~/.config/vibecrafted/starship.toml` and
`~/.config/vibecrafted/atuin/config.toml`. Reinstallation preserves edits to these
preferences. History, Atuin data, zoxide data and prompt cache are separate from
your ordinary shell, under `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/`.

Missing optional tools leave the shell usable and produce a setup notice.
`startup.log` in that shell directory contains a bounded snapshot of component
notices, without command history or tool initialization output. Install missing
tools through their upstream package or installer. A failed explicit workspace
launch keeps its error visible and leaves a shell available for the next command.

## Legacy frontier helpers

The opt-in helper layer below is separate from the installed Terminal profile.

Frontier config is the lightweight, optional terminal layer that ships with the runtime: a `starship` prompt with repo and runtime context, `atuin` searchable history tuned for project recall, and dormant `vc-frame` dashboard layouts. None of it is required — `vibecrafted` works without any of it — and none of it bulldozes your existing terminal setup.

## What it gives you

| Component | What it adds                                                                                                                              |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| starship  | Prompt showing directory, git branch and dirty state, Python/Node/Rust context, and the active agent and runtime while a spawn is running |
| atuin     | Fuzzy history with workspace-first filtering, home-scope fallback, preview-enabled recall, noise filtering for trivial commands           |
| shell     | `atuin-up.zsh` — keyboard Up opens Atuin; mouse wheel stays host scrollback via the Alacritty preset                                      |
| alacritty | Optional host sidecars (wheel `~Alt`/`Alt` split + primary-shell launcher); never overwrites `~/.config/alacritty`                        |
| vc-frame  | Repo-owned `config.kdl` and dashboard layouts that stay dormant until you launch them                                                     |

## Opt in

```bash
brew install starship atuin     # or your distro's packages
make install                    # from a local checkout
vc-frontier-paths               # inspect the resolved config paths
```

Install or refresh the frontier sidecars at any time:

```bash
vc-frontier-install
```

The installer places all assets under `$HOME/.config/vetcoders/frontier/` — **not** into your global `$HOME/.config/starship.toml` or `$HOME/.config/vc-frame`. If vc-frame is on your machine, the same command also stages the repo-owned `config.kdl` and dashboard layouts. Nothing activates until you run a dashboard command or point your shell at those files:

```bash
vibecrafted dashboard
```

During a full install, frontier staging runs from the installed runtime generation (the `vibecrafted-current` tree), and a failure there is non-fatal — the install proceeds and prints a warning.

## Opt out

Frontier is opt-in by construction:

- Skip `vc-frontier-install` and no frontier files are staged.
- Staged files are inert until referenced — your shell keeps its own prompt and history config.
- Removing the layer means deleting `$HOME/.config/vetcoders/frontier/` and any lines you added to your shell config yourself.

If you already run your shell inside a vc-frame session, spawned agents still reuse panes automatically whether or not you install the repo-owned dashboards.

## Config resolution

The helper layer resolves each artifact **independently**, first match wins:

1. `$XDG_CONFIG_HOME/vetcoders/frontier/`
2. `$VIBECRAFTED_HOME/tools/vibecrafted-current/config/`
3. `$VIBECRAFTED_ROOT/config/`
4. `<current vibecrafted repo>/config/`

Per-asset resolution means a companion config can override only the vc-frame bits while the runtime still provides the prompt and history defaults — or provide a single layout without shadowing anything else.

Check what is actually resolved on your machine:

```bash
vc-frontier-paths
```

## The installed-root rule

Active frontier configuration must resolve inside the installed root, never inside a repository checkout. This is enforced twice:

- **At publication** — a new runtime generation fails to publish if generated vc-frame configuration references the source checkout. The generation manifest carries SHA-256 digests for the generated vc-frame configuration.
- **Continuously** — `vibecrafted doctor` audits the installed artifact and fails on checkout-linked config or drifted manifest-bound files.

If `doctor` reports checkout-linked frontier config, re-run the install so the config is regenerated into the current generation — see [Common issues](/docs/common-issues/).

## Next

- [Configuration](/docs/configuration/) — the full directory-surface map.
- [Doctor](/docs/doctor/) — how the frontier audit is reported.
