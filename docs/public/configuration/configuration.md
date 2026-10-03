---
title: "Configuration"
description: "The config surfaces of Vibecrafted: the install manifest, the state root, the installed runtime root, and XDG config."
section: configuration
order: 10
---

# Configuration

Vibecrafted keeps a strict separation between four surfaces: the install manifest that declares what gets installed, the state root that holds your run history, the installed runtime root that holds what actually executes, and a small XDG config layer for shell integration. Knowing which surface owns a file tells you where to fix a problem.

## install.toml — the install manifest

`install.toml` at the root of the source repository is the manifest consumed by the built-in installer (`make install`, `make setup-dev`, `make wizard`). It declares:

- **Phases** — introduction, diagnostics, installation, onboarding — each with an explicit stated reason before any durable write.
- **Diagnostics categories** — frameworks, foundations (`loctree-mcp`, `aicx-mcp`, `prview`, `screenscribe`), toolchains (`python3`, `node`, `git`, `rsync`), agents (`claude`, `codex`, `agy`, `junie`, `grok`, `cursor`), and additional tools.
- **Runtime horses** — optional runtime surfaces selectable at install time: `wezterm`, `vc-apprt`, `locterm`, `microsandbox` (default: `none`).
- **The installer log** location and the fact that the installer tool persists so `vibecrafted update` can re-run the manifest.

Preview what the manifest would do without touching anything:

```bash
uv run --project scripts/installer vetcoders-installer install.toml --dry-run
```

## `~/.vibecrafted/` — the state root

Your operational history. Overridable with `VIBECRAFTED_HOME` (see [Environment](/docs/environment/)).

| Path                                                         | Contents                                        |
| ------------------------------------------------------------ | ----------------------------------------------- |
| `~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/`         | Plans, reports, and temp files per repo per day |
| `~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/` | Final workflow reports — the durable run truth  |
| `~/.vibecrafted/logs/installer/`                             | Installer logs (`install-<timestamp>.log`)      |
| `~/.vibecrafted/backups/installer/<timestamp>/`              | Restore kits that survive uninstall             |
| `~/.vibecrafted/` (rest)                                     | Local control-plane state and run history       |

This root is deliberately explicit so reports, transcripts, and run state can be inspected, moved, backed up, or deleted. `vibecrafted uninstall` retains it intentionally.

## `~/.local/share/vibecrafted` — the installed root

What actually runs. `${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted/tools/` holds the immutable `vibecrafted-generation-*` directories and the atomic `vibecrafted-current` pointer. The public launcher in `~/.local/bin` enters only this root. See [Update and rollback](/docs/update/) for the generation mechanics.

## `~/.config/vibecrafted/` — the one config home

All product configuration lives here (`${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted`). Vibecrafted reads and writes no other configuration directory.

| Path                                              | Purpose                                                                                                                   |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `~/.config/vibecrafted/config.toml`               | Per-user runtime picks (for example research agent defaults), `[server]`, `[tools]`, `[memex]`, `[aicx_sync]` — mode 0600 |
| `~/.config/vibecrafted/starship.toml`, `atuin/`   | Prompt and history preferences, preserved across reinstall                                                                |
| `~/.config/vibecrafted/vc-frame/`, `vc-terminal/` | Frame cockpit and product terminal configuration                                                                          |
| `~/.config/vibecrafted/shell/`                    | Product shell helpers; `vc-skills.sh` is the opt-in helper shim for sourcing `vc-*` shortcuts by hand                     |

The same file owns the server endpoint. The installer seeds this table once
from a verified existing service, then preserves it across upgrades:

```toml
[server]
bind_host = "127.0.0.1"
port = 3024
public_url = "http://127.0.0.1:3024"
```

Use a tailnet/LAN address when remote consumers must reach the observer. The
LaunchAgent, status receipts, guardian URL, and Slack plist are generated views
of this table; do not edit them as configuration.

### `[agents.<agent>]` — fleet model and effort defaults

Set dispatch and launch defaults independently of the provider CLI's interactive
configuration. Each agent table accepts optional `model` and `effort` strings:

```toml
[agents.codex]
model = "gpt-6.1-sol"
effort = "low"

[agents.claude]
model = "sonnet"
```

Model precedence is CLI `--model` or dispatch `cut.model`, then brief frontmatter,
then `[agents.<agent>].model`, then `provider_default`. Effort precedence is CLI
`--effort` or dispatch `cut.effort`, then `[agents.<agent>].effort`, then the
provider default. Dispatch TOML accepts `effort` beside `model` in each `[[cuts]]`.
A resumed model stays pinned to its previous selection unless explicitly changed.

Launch receipts record `model_requested`, `model_effective`, `model_source`,
`effort_requested`, `effort_effective`, and `effort_source`. Sources are `cli`,
`plan`, `config.toml`, or `provider_default` (`resume_previous` for a preserved
resume pin). An unpinned Codex receipt may also show `provider_config_model` and
`provider_config_effort`, read from `$CODEX_HOME/config.toml` or
`~/.codex/config.toml`. These are observations of CLI settings, not proof of the
effective model; other config keys never enter the receipt.

Effort uses the existing provider adapter: Codex receives
`-c model_reasoning_effort=<value>`; Claude, Agy and Junie receive `--effort`;
Grok and Copilot receive `--reasoning-effort`. Provider catalogs define valid
level names. Cursor and Kimi have no supported effort flag and receive a
receipted skip with an empty `effort_effective`.

A present but invalid agent table (including misspelled keys, non-string or
empty pins) stops admission with an error, even when an explicit pin overrides
it. Fix or remove the table before retrying. Copilot also permits its existing
`provider` subtable below.

### `[agents.copilot.provider]` — BYOK model pin for the `copilot` agent

The same `config.toml` can pin the `copilot` agent to a Bring-Your-Own-Key
(BYOK) provider — for example a local Ollama endpoint — instead of Copilot's
own default model. Absence of this section is the default: `copilot` runs
unchanged. When present, `base_url` is required; every other key mirrors a
`copilot help providers` environment variable one-to-one and is translated to
`COPILOT_PROVIDER_*` / `COPILOT_MODEL` env vars at spawn time, without
mutating your shell or exporting secrets into any report, transcript, or
`meta.json`. A var you already exported yourself always wins over the config
pin.

```toml
[agents.copilot.provider]
base_url = "http://localhost:11434/v1"
model = "kimi-k3:cloud"
# Optional, mirroring `copilot help providers`:
# type = "openai"
# api_key = "..."            # prefer api_key_command for a keychain lookup
# api_key_command = "..."
# bearer_token = "..."
# wire_api = "responses"
# transport = "http"
# headers = "X-Foo: bar"
# model_id = "..."
# wire_model = "..."
# max_prompt_tokens = "128000"
# max_output_tokens = "8192"
```

This mirrors `ollama launch copilot`'s own interactive BYOK wiring so a
dispatched `vibecrafted implement copilot ...` headless worker can run
against the same local model the Founder uses interactively.

The default installer does not source helpers into your host shell. It may add only a guarded `~/.local/bin` `PATH` entry after explicit consent; the full helper profile belongs to the explicit `vc-start` environment.

## Installed-over-checkout doctrine

The repository is a workshop; the installed generation is the runtime. Installed artifacts never point at a repository checkout:

- Publication of a new generation **fails** when any installed symlink resolves outside its generation, or when active config, KDL, helper, or command-deck content references the source checkout.
- The generation manifest records a one-way fingerprint of the source root — never the checkout path itself.
- `vibecrafted doctor` repeats this audit continuously against the installed artifact and fails when the public launcher resolves outside `~/.local/share/vibecrafted`.

Practical consequence: editing files in a git checkout changes nothing about the running CLI until you re-run `make install`. Verify what is live at any time:

```bash
vibecrafted doctor
vibecrafted receipt
```

## Next

- [Environment](/docs/environment/) — variables that change these locations.
- [Terminal frontier](/docs/terminal-frontier/) — the optional prompt/history/dashboard layer.
