# Agent interactive contract — init / resume / operator / partner

Spec for how `vibecrafted init|resume|operator|partner <agent>` must behave.
Applies to the **operator seat** (interactive launcher → explicit or detected
operator target). Fleet workers (`*_spawn.sh`, marbles baton) stay
non-interactive by design.

## Single rule (typed owner)

```text
bare resume → interactive → explicit or detected operator target
prompt/file → tracked headless worker
provider adapter → changes argv only, never policy
```

- **Policy owner:** `_vetcoders_resolve_interactive_operator_target` +
  `_vetcoders_resume_with_contract` (shell runtime). Not comments in adapters.
- **Adapters** (`_vetcoders_resume_command` / `_vetcoders_fresh_session_command`)
  only emit provider argv for a given `mode`.
- **No provider special-case** for surface preparation (including Codex).

### Interactive target resolution (order)

1. Explicit `VIBECRAFTED_OPERATOR_SESSION` (jawny target)
2. In-frame env (`VC_FRAME_PANE_ID` + `VC_FRAME_SESSION_NAME`)
3. Detected: vc-frame `(attached)` / `(current)`
4. Detected: live repo-bound host (`basename` of project root)
5. Detected: exactly one live non-`EXITED` session
6. Multiple live candidates with no unique pick → **fail closed** with candidate
   list (never silent headless, never pick arbitrarily)

Interactive without a resolved target **refuses to downgrade** to headless.

## Modes

| Mode                | Trigger                                                                                          | UI                                                    | Agent invocation                     |
| ------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------- | ------------------------------------ |
| **interactive**     | bare `init` / `resume` / `operator` / `partner`; or `resume --session` without operator job text | Explicit or detected operator target (tab / frame)    | TUI stays open; human can continue   |
| **non-interactive** | explicit `--prompt` / `--file` on resume or partner (job continue); fleet spawn                  | Headless worker; tab/UI is transcript projection only | One-shot / print / exec / `--single` |

An internal AICX continuity pack is **transport**, not operator job text, for
every provider — bare resume stays interactive.

## Lifecycle (clean install)

1. `vc-start` / `vibecrafted start` → operator layout (`vibecrafted` / `operator.kdl`).
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

- Bare (no `--prompt` / `--file` / `--prompt-stdin`): **interactive** TTY face, seed `/vc-partner`. Same spawn family as init (vc-frame tab, or current terminal when `--runtime plain` / no cockpit).
- Explicit `--prompt` / `--file` / `--prompt-stdin`: **non-interactive** tracked worker (`launch_workflow` / supervised skill). Not a TTY face.

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
- Equating “interactive endpoint” solely with auto-detected `(attached)` when
  live sessions exist but none was selected — list candidates and require an
  explicit target instead.

## Ownership

Mode + target policy is shared. Each agent lane owns only its **flag matrix**
(argv). A broken interactive _policy_ is fixed once for every agent; a broken
argv path is fixed on that agent only.
