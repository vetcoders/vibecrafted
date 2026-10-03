# Frontier Config — Starship, Atuin, vc-frame

## What This Is

Frontier config is the lightweight operator layer of prompt, history, and
session presets:

- `starship` for prompt context
- `atuin` for searchable history
- `vc_frame` config and dashboards for a product-owned session surface

All product configuration lives in one place: `~/.config/vibecrafted/`. The
installer publishes the active presets there (`starship.toml`,
`atuin/config.toml`, `vc-frame/`) and preserves your edits on reinstall.
Vibecrafted reads and writes no other configuration directory, so your own
terminal setup stays yours.

None of this is required. `vibecrafted` works without any of it.

---

## Quick Setup

```bash
brew install starship atuin
make install
vc-frontier-paths
```

That gives you:

- a prompt with repo/runtime context
- searchable shell history tuned for project recall
- vc-frame dashboards that stay dormant until you launch them

If you already run your shell inside a `vc_frame` session, spawned agents still
reuse panes automatically. Launch the dashboards explicitly with
`vibecrafted dashboard`.

---

## Starship

The helper layer auto-detects Starship and points it at
`~/.config/vibecrafted/starship.toml`, falling back to the shipped
`config/starship.toml` of the installed generation. An explicit
`STARSHIP_CONFIG` in your environment wins.

What it shows:

- current directory
- git branch and dirty state
- Python / Node / Rust context
- active agent and runtime when a spawn is running

Check the resolved path:

```bash
vc-frontier-paths
```

---

## Atuin

Preferences live in `~/.config/vibecrafted/atuin/config.toml`, seeded from the
shipped `config/atuin/config.toml`:

- fuzzy history
- workspace-first filtering
- home-scope fallback when the current repo/workspace is empty
- preview-enabled recall
- noise filtering for trivial commands

An explicit `ATUIN_CONFIG` in your environment wins.

Keyboard Up is wired by `config/shell/atuin-up.zsh` (also in
`vibecrafted-vm/zshrc.template`). Mouse wheel is **not** an Atuin concern —
the product terminal policy splits primary-buffer scrollback from
alternate-buffer arrows (`config/vc-terminal/vibecrafted.toml`, canonical
source `vc-frame/tools/alacritty/`).

---

## Config Resolution

The helper layer resolves the Starship and Atuin presets per asset, first match
wins:

1. `~/.config/vibecrafted/` — the installer-owned product config
2. `${VIBECRAFTED_TOOLS_HOME:-~/.local/share/vibecrafted/tools}/vibecrafted-current/config/` — shipped defaults of the installed generation
3. `$VIBECRAFTED_ROOT/config/`
4. `<current vibecrafted repo>/config/`

vc-frame config is not searched: it is pinned to `~/.config/vibecrafted/vc-frame/`
(developer mode on a source checkout pins the checkout's own
`vibecrafted-core/vibecrafted_core/config/vc-frame/` instead).

Inspect the active paths with:

```bash
vc-frontier-paths
```

---

## Shipped Surface

The core repo ships:

- `vc_frame` layouts and config
- `starship` and `atuin` presets
- the product terminal policy (`config/vc-terminal/`)

The installer publishes them into `~/.config/vibecrafted/`. If you prefer
`vc_frame`, keep using it: the spawn runtime still detects an active session and
opens panes there when possible. The framework owns an optional dashboard
surface, not your whole terminal identity.
