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
`3more-studio`).

### Project identity (one owner)

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
2. Tab **Start here** = Guide / onboarding (map + picker when productized).
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

### `vibecrafted resume <agent>`

| Args                              | Mode                                                                                    |
| --------------------------------- | --------------------------------------------------------------------------------------- |
| bare                              | AICX 48h pack → **new interactive** session. Never native attach. `--session` only.     |
| `--session <id>`                  | **interactive** resume of that session                                                  |
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
