# Launcher operational policy

Every agent a `vc-*` launcher starts receives one concise Vibecrafted
operational policy in addition to its provider's own defaults and its private
task. This page describes where the policy lives, how each provider receives
it, and what is proven by the launcher versus what still needs native
acceptance.

## One source

- Text: `vibecrafted-core/vibecrafted_core/runtime/policy/OPERATIONAL_POLICY.md`
  (packaged with the `runtime/` tree; versioned, sha256-attributed).
- Owner: `vibecrafted_core.operational_policy` — the only module that decides
  the transport, renders the block and writes the receipt. Builders, shell
  spawners and tests read the resource; none keeps a copy.
- Receipt: every launch records `operational_policy` in its run metadata
  (`schema`, `version`, `digest`, `provider`, `lane`, `continuation`,
  `delivery`, `native_option`, `reason`, `acceptance`).
- Inspect: `python -m vibecrafted_core.operational_policy matrix|digest|path`.

## Transports

| Delivery       | Meaning                                                                                                                                                                   |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `native`       | Full policy through the provider's additive system option; the private task carries only a digest reference.                                                              |
| `current_turn` | Full policy inside the private task (stdin, private file or stream-json turn via `prompt_transport`), placed before the operator task; it supersedes any earlier copy.    |
| `unsupported`  | The launch has no initial turn that could carry it. Run metadata says so and the launcher prints `operational policy NOT delivered …` with the policy path. Never silent. |

Native is used only for a **fresh** provider conversation in lanes whose
Python builder owns the final argv (interactive, headless, supervised child).
Claude records the first system prompt and replays it verbatim on every later
request and resume until compaction (`--system-prompt-snapshot`, on by
default), so a flag on a resume or fork proves nothing. Grok and Junie refresh
semantics are unverified. Every continuation therefore carries the policy in
the current turn.

Options that replace provider defaults are refused, never emitted: Claude
`--system-prompt`/`--system-prompt-file`, Grok `--system-prompt-override`,
Kimi `--agent-file`/`--agent`. `VIBECRAFTED_OPERATIONAL_POLICY_NATIVE=0`
forces the current-turn lane (compatibility guard for a CLI that rejects the
additive option).

## Provider matrix

| Provider | Additive option (evidence)                                                | Fresh interactive / headless / child                                                 | Resume, fork        | Idle bare fork | Legacy `*_spawn.sh` |
| -------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------- | -------------- | ------------------- |
| claude   | `--append-system-prompt` (2.1.296 help)                                   | native                                                                               | current turn        | unsupported    | current turn        |
| grok     | `--rules` (1.0.50 help)                                                   | native                                                                               | current turn        | unsupported    | current turn        |
| junie    | `--system-prompt=` additional (build 3760.1 saved help)                   | native                                                                               | current turn        | unsupported    | current turn        |
| codex    | none advertised (0.162.1); `-c` keys are not evidence                     | current turn                                                                         | current turn        | unsupported    | current turn        |
| agy      | none advertised (1.3.3)                                                   | current turn (stream-json turn)                                                      | current turn        | unsupported    | current turn        |
| cursor   | none advertised (2026.09.28)                                              | current turn                                                                         | current turn        | unsupported    | current turn        |
| copilot  | none advertised (1.0.95); custom instructions are global files            | current turn                                                                         | current turn        | unsupported    | current turn        |
| kimi     | none: `--agent-file` renders the whole system prompt, refuses `--session` | interactive: unsupported (TUI has no initial prompt); headless: current turn on argv | current turn (argv) | unsupported    | current turn (argv) |

Kimi print mode has no stdin lane; its whole private prompt, policy included,
rides `-p` argv. That ps/ARG_MAX exposure predates this policy and is not
called private.

## Launch owners covered

- Interactive `init`/`operator`/`partner`/`resume`/`fork`:
  `spawn.compose_interactive_task_prompt` places the block before the operator
  extra; `spawn.interactive_policy_command` adds native argv; the supervised
  operator pair receives its own fresh delivery.
- Headless workflows, manual resume, Guardian native resume and manual native
  fork: `workflow.launch_workflow` (block before `Operator prompt:` in
  `_runtime_prompt`; native argv only on a fresh provider argv).
- Supervised research/marbles/polarize children and research synthesis:
  `workflow_runtime._run_child`.
- Legacy shell spawners (all eight `runtime/scripts/*_spawn.sh`, the marbles
  baton and shell research): `lib/prompt.sh` renders the block through
  `python -m vibecrafted_core.operational_policy turn`; current turn only.
- Microsandbox `Supervisor.spawn` default command: block on the inline prompt.

## Acceptance status

The launcher proves composition: exact argv, private prompt bytes, ordering,
digest and receipts (`vibecrafted-core/tests/test_operational_policy.py`).
It does not prove that a provider model read or followed the policy. Native
fresh/resume/fork acceptance per supported provider — the installed CLI
accepting the option, the model citing the current digest, the original
continuation identity preserved — remains a separate, parent-gated step after
installation.
