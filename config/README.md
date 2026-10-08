# Frontier Config

Repo-owned presets for the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. operator surface. These are shipped
defaults: the installer publishes the active copies into `~/.config/vibecrafted/`,
the one product configuration home. Nothing reads or writes any other
configuration directory.

This layer is intentionally separate from personal shell identity:

- banner art stays user-owned
- shell helpers stay in `runtime/shell/`
- these files cover reproducible prompt/history presets plus the product terminal policy
- Starship/Atuin resolve per asset: `~/.config/vibecrafted/` first, then these shipped defaults

Current presets:

- `starship.toml` — compact operator prompt with repo/runtime context
- `atuin/config.toml` — history defaults tuned for project/workspace recall
- `shell/atuin-up.zsh` — keyboard Up → Atuin (wheel stays host scrollback)
- `alacritty/launch-primary-shell.zsh` — product primary-shell entrypoint for `vc-terminal`
- `vc-terminal/` — product terminal policy and interactive profile
- `memex.toml.example`, `aicx-sync.toml.example` — the `[memex]` and `[aicx_sync]` tables of `~/.config/vibecrafted/config.toml`

vc-frame config and layouts ship in `vibecrafted-core/vibecrafted_core/config/vc-frame/`
and are published to `~/.config/vibecrafted/vc-frame/`.
