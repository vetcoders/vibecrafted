# Host terminal presets (Alacritty / vc-terminal)

This directory contains two deliberately different inputs. Do not collapse them
into one user-managed Alacritty configuration:

| Asset                    | Canonical path                              |
| ------------------------ | ------------------------------------------- |
| Private frontier sidecar | `config/alacritty/vc-frame.toml`            | Optional import for a user's own Alacritty                    |
| Product shell entrypoint | `config/alacritty/launch-primary-shell.zsh` | Installed at `$XDG_CONFIG_HOME/vibecrafted/vc-terminal/`      |
| Product terminal policy  | `config/vc-terminal/vibecrafted.toml`       | Installer-owned source loaded only through `vc-terminal.toml` |

`vc-frame.toml` is staged into `$XDG_CONFIG_HOME/vetcoders/frontier/alacritty/`
by `install-frontier-config.sh` when present. It is a sidecar and never
overwrites `~/.config/alacritty/alacritty.toml`. The Runtime Pack installer
publishes the product shell entrypoint separately; users do not copy it by hand.

## What the operator should wire

```toml
# ~/.config/alacritty/alacritty.toml
[general]
import = [
  # after: cp $VC_FRAME_CHECKOUT/tools/alacritty/vc-frame.toml ~/.config/alacritty/
  "~/.config/alacritty/vc-frame.toml",
]
```

Or copy the optional preset from the frontier sidecar after
`vc-frontier-install`. This private import does not configure Vibecrafted.app or
the `vc-terminal` product launcher.

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
does not, because Alacritty no longer turns primary-buffer scroll into arrows.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
