# Agent interactive contract — init / resume / operator / partner

Spec for how `vibecrafted init|resume|operator|partner <agent>` must behave.
Applies to the **operator seat** (interactive launcher → explicit or detected
operator target). Fleet workers (`*_spawn.sh`, marbles baton) stay
non-interactive by design.

## Single rule (typed owner)

```text
bare resume → interactive → explicit or detected operator target
resume prompt/file → tracked headless worker
init / operator / partner → always interactive (prompt/file = seed context)
provider adapter → changes argv only, never policy
```

- **Policy owner:** `_vetcoders_resolve_interactive_operator_target` +
  `_vetcoders_resume_with_contract` (shell runtime). Not comments in adapters.
- **Adapters** (`_vetcoders_resume_command` / `_vetcoders_fresh_session_command`)
  only emit provider argv for a given `mode`.
- **No provider special-case** for surface preparation (including Codex).

### Interactive target resolution (order)

Ownership must be **proven**. A session is this project's target only when the
caller owns it, named it, or it belongs to this project.

1. Explicit `VIBECRAFTED_OPERATOR_SESSION` (jawny target)
2. Verified in-frame env of THIS caller (`VC_FRAME_PANE_ID` +
   `VC_FRAME_SESSION_NAME`)
3. Detected: this project's workspace-bound host, when live
4. Detected: this project's repository `basename`, when live
5. No match → target stays empty; the caller **prepares this project's own
   session**. Unrelated live sessions are listed on stderr as context only.

Explicitly **not** targets (removed 2026-09-06):

- a session vc-frame lists as `(attached)` / `(current)` — that marker means
  _some_ client is attached, routinely another operator window on another
  repository; it is not proof that this caller owns it;
- "exactly one live session" — a global count is not ownership.

Both let a single unrelated live session capture another project's resume and
dispatch its provider tab there (P0, Founder 2026-09-06: a resume launched in
`mlx-batch-runner` was blocked by `Live runs` / `Needs attention` /
`host-a`).

### Project identity (one owner)

Agents restores missing pane exports from the current physical seat's existing
WES binding before loading project choices. The observed live inventory, socket
namespace and recorded Frame incarnation determine that context. Conflicting
exports refuse recovery; a verified bound root wins over an inherited cwd.
Without a binding, a Git cwd supplies a project path hint only. Starting that
current project cannot allocate a collision-suffixed replacement: the launcher
keeps the existing seat and offers the Sessions rail / `vc-frame attach` route.
An explicit different project still uses the canonical shell preparation owner.

Frame's native rail **Open project** currently creates directly from a folder
selection, outside that shell owner. Its cwd/name transport cannot establish
WES ownership retrospectively. That creation path must enter the canonical
missing → materialize → create → live-attachment lifecycle before switching to
the returned physical seat. Agents recovery does not certify that origin path.

The selected generation exposes that preparation through its existing Workshop
entry point, without curses or a terminal:

```text
<runtime>/bin/python3 <runtime>/vibecrafted-core/vibecrafted_core/config/vc-frame/vc-agent-workshop.py prepare --root <declared-project> [--session <requested-name>] [--layout <absolute-physical-layout>]
```

The caller supplies an absolute physical `VIBECRAFTED_RUNTIME_ROOT` and its
existing absolute `VC_FRAME_SOCKET_DIR` (or `ZELLIJ_SOCKET_DIR`). Absolute socket
aliases such as macOS `/tmp` are valid and retain the same spelling throughout
preparation. Layout
is optional creation input; reusing a live destination preserves its layout,
panes and workloads. The existing resolver, selected-runtime admission,
workspace materialization, locked background creation and live WES admission
remain the only owners. This internal entry does not reconcile the host server
or LaunchAgent. There is no direct-create fallback.

In installed product mode, the missing-seat create owner retains an explicit
selected layout and its `.swap.kdl` companion as exact content-addressed files
inside the existing writable product `vc-frame/layouts` directory. Publication
runs under the create lock after rechecking that the seat is missing. It never
refreshes product configuration or writes the selected Runtime Pack; absent or
symlinked product configuration, conflicting retained bytes and symlinked input
or output refuse creation. The public `bin/vc-frame` wrapper keeps its normal
installed-layout guard and invokes the generation's distinct native `libexec`
engine. Reuse does not publish or apply a newly selected layout.

A registered project without a verifiable original Frame binding refuses
automatic preparation when its requested native name is occupied by an unbound
live session, including when the caller is in the lobby or has no current
session marker. It does not adopt that session or silently allocate a hashed
replacement. A new project or a verified foreign owner still uses the existing
name-collision route; unrelated unbound names do not block a free destination.
Root/name similarity cannot prove an arbitrarily renamed unbound session's
identity.

Success writes one JSON object to stdout with `native_session_name`,
`session_incarnation`, `socket_dir`, `VIBECRAFTED_OPERATOR_SESSION`,
`VIBECRAFTED_WORKSPACE_ROOT`, `VIBECRAFTED_WORKSPACE_ID`,
`VIBECRAFTED_SESSION_ID`, `VIBECRAFTED_WORKSPACE_INSTANCE_ID` and
`VIBECRAFTED_BUILD_ID`. These describe the admitted seat, which can differ from
the requested name. Operational refusal exits 4 with stderr and no success
JSON; invalid command arguments exit 2. The consumer switches only after this
packet succeeds and uses its exact native name/incarnation/socket namespace.

Native PTY fixtures observe IPC readiness and the intended layout before acting;
background-create acknowledgement alone is not readiness. Cleanup waits for the
fixture-owned native server processes to exit before removing their private
socket/cache trees. Logical session removal alone is not process termination.

`_vetcoders_effective_project_root` resolves the project **once**, at the public
entry: explicit `--root` (normalized to an absolute physical path _before_ any
cwd change) → ambient project root → the caller's repository. Terminal cwd,
workspace/session naming, AICX and provider cwd all read that one value.

`VIBECRAFTED_ROOT` is **not** a project source when it equals
`VIBECRAFTED_RUNTIME_ROOT`: every front door (`vc_start.rs`,
`scripts/vc-terminal-product-entry.sh`, `scripts/vc-frame-product-entry.sh`,
`shell/lib/core.sh`, `shell/lib/dashboard.sh`) pins both to the runtime
generation, and naming a session after the release is never right.

### No TTY (public entries)

`vc-frame` keeps its strict TTY guard — it is internal and still refuses a pipe.
A **public** entry (`vc-start`, `vc-resume`) instead opens the product terminal
host on this project and re-runs itself there with a real PTY, via the owner
`Vibecrafted.app` already uses:

```text
vc-terminal --working-directory <project> -e <launch-primary-shell.zsh> <front door> [argv...]
```

- The launcher has ONE physical owner:
  `$HOME/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh`, with no
  symlinked ancestor. No `XDG_CONFIG_HOME` override, no release-default
  fallback — the generation's `config/alacritty` copy is installer input.
- `VIBECRAFTED_TERMINAL_ENTRY=1` rides with the child and stops a launch loop.
- Escalation happens **before** any AICX/provider side effect, so the pack is
  assembled exactly once — in the child.
- Launch is admitted or it failed: a wrapper that rejects a missing config
  (exit 2) or an invalid native host (exit 127) is reported as a failure, never
  as "opened". A missing front door returns immediately.

### Order inside the terminal

A foreground `vc-frame` client does not return until the Founder detaches, so
sequencing matters:

1. prepare (create the project's session **detached**, never foreground),
2. create the provider tab,
3. hand the terminal over (`attach`) — **last**.

Preparing by attaching first meant the provider tab was created only after the
window had been closed.

Interactive without a resolved target and without any way to prepare one
**refuses to downgrade** to headless.

## Modes

| Mode                | Trigger                                                                                                                                          | UI                                                    | Agent invocation                     |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------ |
| **interactive**     | `init` / `operator` / `partner` (with or without extra `--prompt`/`--file` seed); bare `resume`; or `resume --session` without operator job text | Explicit or detected operator target (tab / frame)    | TUI stays open; human can continue   |
| **non-interactive** | explicit `--prompt` / `--file` on **resume** (job continue); fleet spawn                                                                         | Headless worker; tab/UI is transcript projection only | One-shot / print / exec / `--single` |

An internal AICX continuity pack is **transport**, not operator job text, for
every provider — bare resume stays interactive.

## Lifecycle (clean install)

1. `vc-start` / `vibecrafted start` → operator layout (`vibecrafted` / `operator.kdl`).
   Both public entries accept `--root <project-path>` (or `--root=<project-path>`).
   The launcher validates and normalizes that path before workspace admission or
   terminal escalation, then consumes it; `vc-frame` never receives `--root`.
2. Tab **Launchpad** = Guide / onboarding (map + picker when productized).
3. **Start 1st Operator session** → pick agent + root → `vibecrafted init <agent>`
   → new tab on the **human seat** with `/vc-init` seed, **interactive**.
4. Workers launch headless in their own process sessions; they do not live in
   the human seat or depend on a vc-frame session.
5. Guardian and the immutable settlement ledger own `f · x · n`. vc-frame may
   project those counts and transcripts, but tabs and bucket sessions are not
   settlement truth or process ownership.

## Per-command

### `vibecrafted init <agent>`

- Always **interactive-only** (`terminal` / `visible`).
- Seed prompt: `/vc-init` (+ optional operator text).
- Grok: positional PROMPT, **no** `--single`, **no** `streaming-json`.
- Policy flags are `--policy-runtime local-native|local-worktrees|local-vm|cloud-soon`
  and `--permissions bypass|auto|accept-edits|read-only`. The canonical matrix
  lives in `vibecrafted_core.spawn`; unsupported provider cells fail closed.
- Environments (`runtime_policy_capabilities`): `local-native` is this checkout;
  `local-worktrees` cuts a branch-backed worktree under
  `${VIBECRAFTED_HOME}/worktrees` at admission and records project, worktree
  root, branch and baseline; `local-vm` is the **local container** (not a VM):
  the persistent per-project Compose project built from the shipped
  `runtime/dev-container` recipe (`vibecrafted_core.dev_container`), with the
  project mounted at `/workspace` and agent history in named volumes. Image
  identity is the recipe digest; a missing/stale image is built inside the
  Agent tab with visible progress, retry or cancel. The recipe installs Claude,
  Codex, Gemini and Kimi and persists their state in named volumes. Launcher
  support for this cell is restricted by `CONTAINER_PROVIDERS` to Claude, Codex
  and Kimi; other providers are refused even when their CLI is in the image.
- Usage metering is reported, not an environment gate: without a live meter the
  session runs with `--token-budget unmetered` (shown as "no data"). An
  explicit token limit still requires metering and is refused otherwise.
- `full-lineage` needs an explicit parent lineage; it is never guessed from the
  launcher pane and never downgraded to `fresh`.

### `vibecrafted resume <agent>`

| Args                              | Mode                                                                                    |
| --------------------------------- | --------------------------------------------------------------------------------------- |
| bare                              | AICX 48h pack → **new interactive** session. Never native attach. `--session` only.     |
| `--session <id>`                  | **interactive** resume of that session                                                  |
| `--run-id <id>`                   | **interactive** resume of that run's proven session, in its recorded checkout/container |
| `--runtime plain`                 | this terminal is the Agent TTY (the Agents launcher's one tab); no second tab           |
| `--session` + `--prompt`/`--file` | **non-interactive** continue (job)                                                      |
| bare + `--prompt`/`--file`        | **non-interactive** fresh tracked job; never adopts an AICX-selected historical session |

### `vibecrafted operator <agent>`

Same interactive contract as init; seed `/vc-operator`.

### `vibecrafted partner <agent>`

Same interactive contract as init; seed `/vc-partner`. `--prompt` / `--file`
append extra seed context; they never select `launch_workflow` / a headless
worker. `vc-partner` without a TTY refuses:

```text
`vc-partner` is available from interactive agent session. Use vc-init first, and then trigger the skill from the active session
```

Visible live partner is `vc-start` / `[New]` with the partner ritual, then
`/vc-partner` in that session.

## Grok CLI flags (ground truth)

From `grok --help`:

- `[PROMPT]` — interactive session seed (TUI stays open).
- `-p, --single <PROMPT>` — **single-turn, print + exit** (headless only).
- `-r, --resume [SESSION_ID]` — resume session.
- Never use `--restore-code` on resume (clobbers working tree).

## Anti-patterns

- Using `--single` on init / operator / bare interactive resume.
- Treating AICX continuity file as “operator prompt” for mode selection
  (the rule applies to every provider).
- Dumping worker tabs into the operator interactive session (G7).
- Treating closure of a viewer tab or vc-frame session as authority to stop a
  headless worker.
- Provider-specific policy forks (e.g. “only Codex prepares the operator
  surface”) — policy is one owner; adapters change argv only.
- Treating a global `(attached)` marker or a lone live session as ownership of
  the current project — list them as context and prepare this project's own
  target instead.
- Reading `VIBECRAFTED_ROOT` as the project behind a product front door (it is
  the runtime generation there).
- Reporting a backgrounded terminal launch as success without any admission
  evidence.
- Attaching the foreground frame client before the provider tab exists.

## Ownership

Mode + target policy is shared. Each agent lane owns only its **flag matrix**
(argv). A broken interactive _policy_ is fixed once for every agent; a broken
argv path is fixed on that agent only.
