# vc-frame Multi-Agent Layouts

> Plan 12 (META_22) — Wave 4 agent-native runtime cut.

Vibecrafted ships a vc-frame configuration tuned for the way Vetcoders actually
work: parallel agents, shared Living Tree, mesh of workstations, no babysitting.
The shipped surface gives every layout host-aware identity colors so an
operator instantly knows which machine they are looking at.

This document covers what is shipped, how it auto-discovers itself, and how to
extend it.

## What ships

```
config/vc-frame/
├── config.kdl                       # base config + monochrome chrome (flat UI)
├── auto-theme.sh                    # host detection -> theme name
├── themes/
│   └── vetcoders-mesh.kdl           # 4 mesh accent themes (red/purple/cyan/green)
└── layouts/
    ├── operator.kdl                 # launch alias of built-in vibecrafted — `vibecrafted start`
    ├── dashboard.kdl                # mission control 2x2 grid
    ├── marbles.kdl                  # convergence workspace
    ├── research.kdl                 # triple-agent research swarm
    └── workflow.kdl                 # ERi implementation workspace
```

Once installed (`vibecrafted install` or `make install`), the framework symlinks
this directory under `~/.config/vetcoders/frontier/vc-frame/` and the layouts
become reachable through the `vibecrafted dashboard <layout>` family of CLIs.

## Mesh-aware host theming

Vibecrafted ships four accent variants of the base theme so an operator can
instantly tell workstations apart through screen-share or browser-mirrored
vc-frame. Which host gets which accent is your configuration, not ours:

| theme         | accent | use it for                       |
| ------------- | ------ | -------------------------------- |
| `mesh-red`    | red    | any workstation you pick         |
| `mesh-purple` | purple | any workstation you pick         |
| `mesh-cyan`   | cyan   | any workstation you pick         |
| `mesh-green`  | green  | any workstation you pick         |
| `vibecrafted` | amber  | Neutral default (fleet baseline) |

The themes live in `config/vc-frame/themes/vetcoders-mesh.kdl`. vc-frame auto-loads
nested theme blocks from the same config dir, so no extra wiring is needed at
the framework level.

### Per-host mapping

`auto-theme.sh` never carries a built-in host list. It reads the mapping from
the first source it finds:

1. `VIBECRAFTED_MESH_MAP` — inline, comma-separated `host=theme` pairs:

   ```bash
   export VIBECRAFTED_MESH_MAP="host-a=mesh-red,host-b=mesh-green"
   ```

2. `mesh.conf` — one `host theme` pair per line. Default path is
   `$VIBECRAFTED_HOME/mesh.conf` (`~/.vibecrafted/mesh.conf`); override with
   `VIBECRAFTED_MESH_CONF`. `#` comments and blank lines are allowed, and a
   host may be listed several times to declare aliases:

   ```
   # host         theme
   host-a         mesh-red      # server
   host-b         mesh-purple   # desktop
   host-c         mesh-cyan     # laptop
   host-d         mesh-green    # laptop
   host-d-alias   mesh-green    # same machine, different LocalHostName
   ```

A host matches an entry when the normalized name is equal to it or starts
with `<entry>-` (so `host-a-2` picks up the `host-a` line). Hosts with no
entry — or no configuration at all — resolve to `vibecrafted`.

### Resolving the theme at runtime

`config/vc-frame/auto-theme.sh` emits the theme name for the current workstation.
Detection order:

1. `VIBECRAFTED_HOST_NAME` (operator override — useful for tests/staging)
2. `scutil --get LocalHostName` (macOS default local name)
3. `scutil --get ComputerName` (macOS user-friendly name)
4. `hostname -s` / `hostname` (Linux fallback)

The result is normalized (lowercase + strip `.local`/`.lan`) before matching;
mapping keys go through the same normalization. If `scutil --get LocalHostName`
returns something other than the name you use day to day, add that value as an
alias line in `mesh.conf`.

The `VIBECRAFTED_THEME` env var bypasses host detection outright, so an
operator can pin a fleet baseline theme even when running on a mesh host.

### Fleet chrome default (status-bar / tab-bar)

Shipped `config.kdl` uses two eyes + flat tiles:

```kdl
theme "monochrome"
theme_dark "monochrome"
theme_light "vibecrafted-ivory"
simplified_ui true
```

| Mode                           | Theme               | Feel                       |
| ------------------------------ | ------------------- | -------------------------- |
| dark (fallback + `theme_dark`) | `monochrome`        | greyscale graphite         |
| light (`theme_light`)          | `vibecrafted-ivory` | kość słoniowa / warm paper |

- **simplified_ui** — flat `Ctrl+<key> LABEL` tiles (no powerline ``)
- Ivory lives in `themes/vibecrafted-ivory.kdl` (auto-loaded with mesh themes)
- Brand block `vibecrafted` (graphite + amber) and mesh host accents are **opt-in**

### Operator layout = vibecrafted standard

`layouts/operator.kdl` (vc-start) and built-in `default_layout "vibecrafted"`
share the same product tabs: **Start here**, **Agents**, **Shell**, and **voc**
(no spaces in layout _filenames_; never `Vibecrafted Operator.kdl`):

- `default_tab_template` — compact-bar brand + **SESSIONS rail always** + status-bar
- tab **Start here** — product map (`about` / `guide_mode "mission-control"`)
- tab **Agents** — Agent Workspaces dashboard; `[New agent]` creates an
  interactive Agent TTY on this tab, while PANE + arrows walks its faces
- tab **Shell** — workspace shell
- tab **voc** — observation door for this workspace, never the launcher itself

The interactive launcher deliberately exposes only contracts that stay in the
current panel today: `init --runtime plain` and bare `resume`. The accepted
design keeps `operator` / `partner` unresolved and `New dispatch` as a later,
server-owned headless door; the UI must not fake those choices prematurely.

No strider split on the entrypoint.

### SESSIONS rail and finished-run triage (`f` · `x` · `n`)

The left **Sessions** column (session-manager plugin, `rail true` in shipped
layouts) lists User Sessions and optional viewer/compatibility hosts. Ordinary
workers launch headless and do not live in this rail (see
`docs/runtime/AGENT_OPS.md`).

For an explicitly terminal-backed compatibility run, the runtime may call
**`vc-frame triage-run`**:

1. Capture scrollback + run identity.
2. Recreate a viewer/rerun tab in one of:
   - `Finalized runs` (**f**)
   - `Failed runs` (**x**)
   - `Needs attention` (**n**)
3. Only then close the origin tab in the work session.

Board counters **`f · x · n` must come from the immutable settlement ledger**,
not from bucket-tab counts or bare control-plane completion. Bucket sessions
remain optional transcript/rerun projections; closing them changes no
settlement fact and cannot stop a headless worker.

Full contract (classification, origin stamp, push≠install, research vs
implement, backfill):
**[`docs/runtime/TRIAGE_AND_SESSIONS.md`](runtime/TRIAGE_AND_SESSIONS.md)**.

### Research layout (multi-pane ≠ multi-session)

Static `layouts/research.kdl`:

- Includes the same **session-manager** rail as operator/dashboard.
- **One** research tab: synthesis left (~55%), agent stack right (claude /
  codex / agy). Swap layouts: `grid`, `synthesis`.
- Agents are **panes**, not separate SESSIONS board columns.

Workflow-generated research KDL (`workflow._write_research_layout`) may omit
the session-manager rail even though the static file has it — treat that as a
layout-generator gap, not as research “learning” sessions over time. A finished
research **run** still triages as one origin tab into a bucket when origin +
install wire are present (see triage doc).

### Activating the host theme

The shipped chrome default is monochrome (flat). To activate a host accent
instead, wire one of the following in your shell init or in a host-local
`config/vc-frame/local.kdl` overlay:

```bash
# Shell init — print the matching theme name for diagnostics.
~/.config/vetcoders/frontier/vc-frame/auto-theme.sh
```

or pin via env:

```bash
export VIBECRAFTED_THEME="$(~/.config/vetcoders/frontier/vc-frame/auto-theme.sh)"
```

When the operator-facing launcher in a future plan rewrites the theme line on
session start, all five layouts will pick up the host accent automatically.

## Verification

```bash
make test-vc-frame
```

Runs `tests/vc-frame-layouts-smoke.sh`, which asserts:

- every shipped layout parses via `vc-frame --layout <name> setup --check`
- every mesh theme loads alongside `config.kdl` without parse errors
- `auto-theme.sh` passes `bash -n` and shellcheck (when installed)
- `auto-theme.sh` resolves hosts through a temporary `mesh.conf` (aliases,
  `VIBECRAFTED_MESH_MAP` precedence, `VIBECRAFTED_THEME` pin) and falls back
  to neutral for unknown hosts (case-insensitive, `.local` suffix tolerant)

Tolerant of missing `vc-frame` / `shellcheck` — those checks are deferred to CI
when the host doesn't have them.

## Living Tree etiquette

- Layout edits are **append-only**. Existing pane configurations are preserved
  byte-for-byte.
- `auto-theme.sh` probes multiple roots
  (`$VIBECRAFTED_HOME/tools/vibecrafted-current/config/vc-frame`,
  `$VIBECRAFTED_ROOT/config/vc-frame`, `./config/vc-frame`) so it works whether
  invoked from the installed framework, a Living Tree worktree, or a CI runner.

## Related

- Doctrine 2026-05-05 — Vetcoders mesh topology + per-host color assignments
- Doctrine 2026-04-12 — first vc-frame landing
- `docs/plans/META_22_SCAFFOLD_TO_RELEASE.md` Plan 12 — full contract
- `skills/vc-agents/SKILL.md` — operator-facing dispatch surface
- [`docs/runtime/TRIAGE_AND_SESSIONS.md`](runtime/TRIAGE_AND_SESSIONS.md) — f/x/n, `triage-run`, origin stamp
- [`docs/runtime/AGENT_OPS.md`](runtime/AGENT_OPS.md) — worker host sessions (G7)

Vibecrafted with AI Agents (c)2024-2026 LibraxisAI
