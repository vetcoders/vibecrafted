# Host terminal presets (vc-terminal)

This directory holds the product shell entrypoint of the `vc-terminal` product
launcher. It pairs with the product terminal policy:

| Asset                    | Canonical path                              | Role                                                          |
| ------------------------ | ------------------------------------------- | ------------------------------------------------------------- |
| Product shell entrypoint | `config/alacritty/launch-primary-shell.zsh` | Installed at `~/.config/vibecrafted/vc-terminal/`             |
| Product terminal policy  | `config/vc-terminal/vibecrafted.toml`       | Installer-owned source loaded only through `vc-terminal.toml` |

The Runtime Pack installer publishes both into `~/.config/vibecrafted/vc-terminal/`;
users do not copy them by hand. Vibecrafted never reads or writes a private
Alacritty configuration.

## Wheel contract (do not regress)

| Buffer            | Wheel                  |
| ----------------- | ---------------------- |
| primary (`~Alt`)  | scrollback             |
| alternate (`Alt`) | Up/Down for TUIs       |
| Shift+wheel       | always host scrollback |

Never wrap the login shell in permanent `smcup`. Use `launch-primary-shell.zsh`.

## Atuin on keyboard Up

Shell binding is separate — see `config/shell/atuin-up.zsh` and
`vibecrafted-vm/zshrc.template`. Keyboard Up may open Atuin; wheel on primary
does not, because the terminal no longer turns primary-buffer scroll into arrows.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
