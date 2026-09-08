---
title: "Management Commands"
description: "Reference for vibecrafted management commands: init, status, doctor, receipt, settlements, update, resume, fork, version, uninstall, help."
section: cli
order: 20
---

# Management Commands

Management commands operate on the installation, the current repository, and
past runs. They launch no workflows themselves — that is the job of the
[skill launchers](/docs/workflow-launchers/).

## Reference table

| Command                              | Purpose                                                        |
| ------------------------------------ | -------------------------------------------------------------- |
| `vibecrafted init [agent]`           | Orient an agent in this repo                                   |
| `vibecrafted status`                 | Today's agent activity                                         |
| `vibecrafted doctor`                 | Installation health — pass/fail                                |
| `vibecrafted receipt [--json]`       | Delivery/runtime receipt (source ↔ installed)                  |
| `vibecrafted settlements <action>`   | Read-only f/x/n ledger query                                   |
| `vibecrafted update`                 | Update to the latest release                                   |
| `vibecrafted resume <agent>`         | Continue a stopped run or a provider session                   |
| `vibecrafted fork <agent>`           | Branch a provider session into a new one (claude, codex, grok) |
| `vibecrafted resume-session <agent>` | Continue an exact provider session as a tracked run            |
| `vibecrafted version`                | Print version                                                  |
| `vibecrafted uninstall`              | Reverse the install                                            |
| `vibecrafted help [topic\|--all]`    | Command deck · full reference                                  |

## Selecting the repository: `--repo`

Every repository-aware command takes `--repo <path>` and works from **any**
working directory, including one that is not inside Git:

```bash
cd ~/Downloads
vibecrafted workflow claude --repo ~/Projects/app --prompt "Ship it"
vibecrafted fork claude --run-id work-260908-194325-30219 --repo ~/Projects/app
vibecrafted start --repo ~/Projects/app
vibecrafted resume codex --repo ~/Projects/app --session <provider-uuid>
```

`--root <path>` is the legacy spelling with identical semantics. Passing both
with different paths is an error (`conflicting --repo … and --root …`), never a
silent pick; a missing path fails with the flag that carried it. Commands that
do not need a repository (`version`, `help`, `doctor`, `receipt`,
`settlements`, `fork-source`) never require Git to run.

Skill launchers add `--worktree [true|false]`: the worker runs in a fresh
linked checkout of `--repo` (branch `cut/<agent>-<run_id>` at the selected
HEAD, under `~/.vibecrafted/worktrees/`). The selected repository must be a
clean Git work-tree root; the launch receipt reports `worktree_path`,
`worktree_branch`, `worktree_baseline_sha` and `parent_root`.

```bash
vibecrafted workflow claude --model claude-fable-5-1 --worktree true \
  --repo ~/Projects/app --prompt "Ujednolić polecenie fork"
```

The Rust cockpit accepts the same selector: `vibecrafted tui --repo <path>`
(`voc --repo <path>`), with `--root` as the legacy spelling and the same
conflict rule.

### Execution controls: `--permissions`, `--sandbox`

Skill launchers also take `--permissions <bypass|auto|accept-edits|read-only>`
and `--sandbox [true|false]`. Both are real execution contracts, resolved
against the _installed_ provider CLI before any process starts: the launcher
maps a control only where the provider can enforce it, and refuses the launch
(exit 2, no run record) with the exact supported alternative otherwise.
`--permissions auto` is never downgraded to `bypassPermissions`; `--sandbox
true` is never downgraded to an unsandboxed run. Omitting both keeps the
historical default (`bypass`; `auto` for junie; sandbox left to the provider).

```bash
vibecrafted workflow claude --model claude-fable-5-1 --worktree true \
  --permissions auto --sandbox true --repo ~/Projects/app --prompt "Ship it"
```

`--sandbox` means the provider's _own command sandbox_: the boundary the agent
CLI draws around the shell commands it runs. It is not whole-agent or VM
isolation. For Claude that boundary is the Bash-tool sandbox (macOS Seatbelt,
Linux bubblewrap) around Bash commands and their child processes; file tools,
WebFetch and MCP servers run outside it. `sandbox_effective` reports what the
launcher configured through the provider's supported interface; the launcher
does not observe OS enforcement and never says it did.

The launch receipt (`--json`, the human receipt, `meta.json`, the launch
event) carries `execution_controls` with `permissions_requested` /
`permissions_effective`, `sandbox_requested` / `sandbox_effective`, the exact
provider flags, a `boundary` line naming what that provider's sandbox confines,
and the evidence line. Provider mapping as probed on 2026-09-08 (read-only
`--help`, binary settings schema and the Claude Code sandbox documentation):

| Provider                   | `--permissions`                                                                                                                           | `--sandbox true`                                                                                                                                                                                        | `--sandbox false`                                                                                                                        |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| claude 2.1.263             | `--permission-mode bypassPermissions\|auto\|acceptEdits\|plan`                                                                            | `--settings '{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false}}'` — the documented hard gate: a missing backend fails the run, no `dangerouslyDisableSandbox` retry | `--settings '{"sandbox":{"enabled":false}}'`                                                                                             |
| codex 0.154 (`codex exec`) | bypass → `--dangerously-bypass-approvals-and-sandbox`; auto → `--approve-for-me`; read-only → `--sandbox read-only`; accept-edits refused | bypass → `--sandbox workspace-write` (bypass flag dropped, approvals never prompted); auto/read-only already sandboxed                                                                                  | bypass only; auto/read-only refused (their sandbox enforces the policy)                                                                  |
| grok 1.0.21                | `--permission-mode …`                                                                                                                     | `--sandbox workspace` (`read-only` profile under read-only)                                                                                                                                             | `--sandbox off`                                                                                                                          |
| cursor-agent 2026.09.08    | `--force --trust` / `--trust` / `--mode ask --trust`; accept-edits refused                                                                | `--sandbox enabled` (verified against `--help`)                                                                                                                                                         | `--sandbox disabled`                                                                                                                     |
| agy 1.1.27                 | `--dangerously-skip-permissions` / default / `--mode accept-edits` / `--mode plan`                                                        | `--sandbox` (opt-in terminal restrictions)                                                                                                                                                              | refused: only an opt-in flag exists and agy keeps persistent terminal-sandbox settings, so "no flag" is not "disabled"; omit `--sandbox` |
| junie 26.8.31              | `auto` only in headless runs                                                                                                              | refused (no sandbox surface)                                                                                                                                                                            | refused                                                                                                                                  |

Claude's `--settings` document sits at the command-line level: its scalar keys
override the same keys in user, project and local settings and keep every key
it omits, while managed settings still outrank it. Array keys merge across
scopes, so commands listed in an inherited `sandbox.excludedCommands` keep
running outside the sandbox; the receipt says so.

`research` and `marbles` run under a supervised runtime that does not carry
these controls yet; passing them there is refused, not ignored. The shell
skill helpers and the interactive `init` / `operator` / `partner` sessions
refuse `--sandbox` for the same reason.

## init

```bash
vibecrafted init claude
```

The interactive first context handoff: loads repository context (history,
structural perception, verification) and opens an oriented agent session.
Run it once per repository session before dispatching work.

**Resume rides along.** Init also computes this checkout's resume payload and
carries it into the session — you never have to remember to look. If any run
here settled `n` (needs attention), init names it, says who owns it, and prints
the exact command that continues it. Runs the Guardian already owns are
reported without a command, because each holds a single automatic attempt that a
hand resume would burn. A checkout with nothing unfinished adds nothing to the
prompt; an unreadable ledger says so rather than implying "clean".

The same payload is attached to the init step of **every** pipeline launch
(`vibecrafted <skill> <agent>`), so a worker opens with unfinished work already
in view. Full inventory on demand:
`vibecrafted settlements list --bucket n --revalidatable`.

## status

```bash
vibecrafted status
```

Today's runs at a glance: which agents ran, which runs are live, which
delivered reports. The run — not a terminal tab — is the unit of truth.

## doctor

```bash
vibecrafted doctor
```

Installation health check with a pass/fail verdict: summary line first,
then failures and warnings. Passing checks are reported as a count. Exit
code is non-zero when a check fails.

## receipt

```bash
vibecrafted receipt --json
```

One delivery/runtime receipt for the fleet tools (vc-frame, vibecrafted,
scaffold-doctor, loct, aicx). Each row binds owner/repo → branch → checkout
SHA → dirty state → installed SHA → ahead/behind → index generation, and
labels the drift:

```text
SOURCE_AHEAD_OF_INSTALLED | INSTALLED_NOT_ON_PATH | UNPUSHED
| DIRTY_BUILD_PROVENANCE | INDEX_STALE | CLEAN
```

Receipt never uses the process working directory to identify a tool source.
When auto-discovery fails, point it explicitly with `VIBECRAFTED_SOURCE`,
`VC_FRAME_SOURCE`, `LOCTREE_SOURCE`, `AICX_SOURCE`, or `VIBECRAFTED_FLEET_ROOT`.

This is the fastest way to catch the push ≠ install trap: a git checkout can
be ahead of the staged tools your daily CLI actually executes. `git pull`
alone does not refresh the installed runtime — `vibecrafted update` does.

## settlements

Read-only query over the settlement ledger — the append-only source of
`f · x · n` (finalized / failed / needs-attention) verdicts:

```bash
vibecrafted settlements summary [--json]
vibecrafted settlements list [--bucket f|x|n] [--revalidatable] \
  [--group agent,skill,reason,root] [--limit N] [--json]
vibecrafted settlements inspect <run_id> [--json]
```

Counters come from the ledger, never from open terminal tabs or bare
control-plane completion rows.

## update

```bash
vibecrafted update
```

Pulls the latest release and reinstalls, refreshing the staged tools the
CLI executes. Verify afterwards with `vibecrafted version` and
`vibecrafted receipt`.

## resume and resume-session

```bash
# After stop: continue the control-plane run (new tracked job; the old PGID is dead)
vibecrafted resume claude --run-id work-260816-213657-08420
vibecrafted resume claude --run-id work-260816-213657-08420 --prompt "continue"
vibecrafted resume claude --last

# Provider-native session (Claude/Codex UUID — never a work-* id, never VIBECRAFTED_SESSION_ID)
vibecrafted resume claude --session <provider-uuid> --prompt "Continue the fix"
```

`stop` kills the launcher process group. There is no same-process restart.
`--run-id` starts a **new** tracked job that continues the stopped work: if
the run recorded a provider session, that session is resumed natively;
otherwise the original prompt is replayed as `resume-new-session`.

`--session` takes the provider UUID only. `work-…` is a control-plane run.
`01a00…` / `VIBECRAFTED_SESSION_ID` is the Vibecrafted runtime session, not
Claude or Codex.

Bare `vibecrafted resume <agent>` (optional `--repo`) opens a **new**
interactive session and attaches an AICX continuity pack. It never
native-attaches the last same-agent candidate. `--repo` (legacy `--root`)
selects the repository from any directory and narrows AICX; it is not a
session picker. The catalog in the pack is evidence, not a swipe list.

```bash
printf '%s' "continue safely" | vibecrafted resume-session codex \
  --agent-session-id <provider-session-id> --prompt-stdin
```

`resume-session` continues one exact provider-owned session as a tracked,
detached headless run. The prompt comes from `-p <text>`, `-f <path>`, or
`--prompt-stdin` (keeps the prompt out of argv). Optional flags: `--repo
<path>` (legacy `--root`), `--model <name>`, `--json` for a machine-readable
launch receipt. This command is always headless; it does not pretend to be
an interactive session.

## fork

```bash
vibecrafted fork claude --run-id work-260908-194325-30219 --repo ~/Projects/app
vibecrafted fork claude --session <provider-uuid> --model claude-fable-5-1
vibecrafted fork grok --session <provider-uuid> --placement floating
vibecrafted fork codex --session current --runtime visible
vibecrafted fork codex --session previous --placement floating
```

`fork` branches a provider session into a **new** session and leaves the
source untouched. One identity per call:

- `--session <provider-session-id>` names the source directly. `current` and
  `previous` (AICX discovery for the caller's repository) stay available.
- `--run-id <work-…>` names a control-plane run; the provider session that run
  recorded is the source, and its recorded repository is the default `--repo`.
  A run that recorded no provider session is refused — the prompt is never
  replayed and presented as a fork.

The two shapes are never confused: a `work-…` id in `--session` or a provider
UUID in `--run-id` fails with the corrected command. Identity resolution and
the provider capability verdict are owned by `vibecrafted fork-source <agent>
(--session | --run-id) [--json]`, which the deck consults.

Provider coverage (verified on the installed CLIs):

| Provider | Native fork                                                                 | `vibecrafted fork`                                                                                |
| -------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| codex    | `codex fork <id> [prompt]`                                                  | supported                                                                                         |
| claude   | `claude --resume <id> --fork-session`                                       | supported                                                                                         |
| grok     | `grok --resume <id> --fork-session` (never `--restore-code` / `--worktree`) | supported                                                                                         |
| cursor   | none (`--resume [chatId]` only)                                             | refused: `vibecrafted resume cursor --session <id>` continues the original (a resume, not a fork) |
| agy      | none (`--conversation <id>` only)                                           | refused, same hint                                                                                |
| junie    | none (`--session-id … --resume` only)                                       | refused, same hint                                                                                |

Inside vc-frame, `--runtime visible|terminal` opens a pane in the current tab
(break-right by default, `--placement floating` otherwise); in a plain TTY,
`--runtime terminal` execs the provider directly. `--runtime headless` is
refused for every provider: `codex fork` is an interactive TUI, and a headless
tracked fork for claude/grok is not wired in this release (`resume-session`
continues the original headlessly, which is a resume, not a fork).
`--permissions bypass|auto|accept-edits|read-only` maps onto each provider's
own permission contract (codex has no `accept-edits` cell); `--model` passes
through unchanged. `fork` has no `--worktree`: a fork reuses the source
session's checkout; use a launcher with `--worktree true` for an isolated cut.

The pane title is `<agent> fork @<owner>/<repo> <source-session-id>`.

## version, uninstall, help

```bash
vibecrafted version        # print version (X.Y.Z+g<shortsha>)
vibecrafted uninstall      # reverse the install
vibecrafted help           # compact command deck
vibecrafted help --all     # full reference
vibecrafted help marbles   # per-topic help
```

The version's `+g<shortsha>` suffix identifies the exact staged build —
compare it with your source checkout when diagnosing drift.
