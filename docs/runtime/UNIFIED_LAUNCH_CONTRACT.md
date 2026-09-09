# Unified launch contract: source continuation

This document distinguishes implemented source behavior from integration and
installed acceptance. The original full launch contract remains open. The flag
syntax and catalog table below are implementation choices for review, not newly
attributed Founder decisions.

## Declaration and ownership

Execution skills use `cli.py` -> `workflow.normalize_launch_spec` ->
`launch_workflow`. Shell aliases enter the same core parser. `repo_selection.py`
owns path/ref/catalog resolution. `repository_claims.canonical_repo_identity`
owns repository identity, including validated managed-clone receipts;
`dispatch.worktrees.WorktreeManager` owns linked checkout admission. Provider
argument builders and `model_overrides.py` own exact model flags. `spawn.py` and
`control_plane.py` retain process and durable run-state ownership.

```sh
vibecrafted workflow codex --repo /path/to/repo --base HEAD \
  --execution-runtime local-worktrees --runtime headless --file plan.md
vc-workflow codex --repo team/project --worktree --file plan.md
```

`--repo` is the public spelling; `--root` is a conflict-checked alias. An existing
path wins over identity syntax. Explicit missing paths are errors. Public core
launches without either flag resolve the caller's Git top-level, never the build
root or ambient workspace. Subdirectories and symlinks resolve to that worktree.

`--base` defaults to local `HEAD` for paths (including unpushed commits), and the
source remote's current default branch for catalog identities. Branches and tags
with the same spelling require `refs/heads/...` or `refs/tags/...`. Local refs
stay local; remote refs require qualification. Resolution produces a commit SHA
before materialization. Linked worktrees use that SHA even if the parent moves.
Dirty parent files and index are preserved. A Living Tree request for another
commit refuses; it never checks out, stashes, resets or commits the parent.

The implementation separates `--execution-runtime living-tree|local-worktrees`
from `--runtime headless|visible|terminal` (legacy presentation names). `--worktree`
is the local-worktrees alias. VM/cloud and unknown runtime spellings refuse;
there is no local fallback. Visible/terminal lifecycle parity still requires
native acceptance and the reserved transport integration.

## Catalog identities

The existing `$XDG_CONFIG_HOME/vibecrafted/config.toml` (default
`~/.config/vibecrafted/config.toml`) can contain explicit source URLs:

```toml
[repositories."team/project"]
remote = "ssh://git@git.example.org/team/project.git"
```

There is no implicit GitHub host. Unknown entries refuse. Credential-bearing
HTTP URLs, passwords, query strings, fragments and unsupported transports
refuse before Git. Use the Git provider's existing authentication mechanism.
Local paths/file remotes support hermetic testing.

A managed clone lives under
`$VIBECRAFTED_HOME/repositories/<remote-digest>/<org>/<repo>`. A receipt in its
common Git directory binds identity and origin. Reuse checks both. Clone/fetch
mutations are locked by remote; preparation subprocesses have bounded timeouts.
Branches and tags refresh into separate remote namespaces with pruning; remote
HEAD is queried for every selection. A requested SHA must be reachable from a
refreshed source branch or tag; cache-only commit objects refuse. A partial clone or foreign cache without a
valid receipt refuses and is preserved for inspection. Worker checkouts retain
the standard `worktrees/<org>/<repo>/YYYY_MMDD/<cut>/` geometry.

Remote-host execution and transporting a local-only commit are unsupported.
The catalog is resolved on the execution host; this implementation only has a
local host adapter. A managed clone's local HEAD is not silently moved when the
remote default changes: use local-worktrees to launch from the refreshed SHA.

## Model and prompt

Launch selection is CLI model > plan-frontmatter model > provider default.
Dispatch TOML `model` is an explicit override at the same precedence as CLI.
Resume selection is CLI model > newly supplied plan model > previous effective
model (then previous requested model if effective observation is unavailable).
A plain continuation note and an old runtime prompt snapshot do not reselect a
model. Parent metadata and source plan files are never rewritten by overrides.

The existing frontmatter parser has a strict launch mode using the existing
PyYAML dependency. A model must be one nonempty string. Null, collections,
booleans, numbers, duplicate model/agent keys and provider conflicts refuse.
BOM/CRLF are accepted for metadata extraction without modifying source bytes.
Model identifiers pass unchanged to provider argument lists; no universal
`effort` exists. Catalog/account availability remains a provider rejection,
not proof from the local parser. A provider with no known model flag refuses
an explicit pin rather than silently ignoring it.

`--prompt TEXT` remains supported as one argument (including heredoc-equivalent
shell substitutions), with `--file` and `--prompt-stdin` alternatives. The deck
moves inline text to a private pipe before invoking Python. Core materializes
`plan-source.md` and `prompt.md` through the existing writer with exclusive
creation and mode 0600. Existing files and symlinks are not overwritten. The
source digest detects edits between selection and launch. Report-safe spec
serialization omits prompt text; receipts include source digest/reference and
model source. Snapshots stay with the run for historical attribution; no new
automatic cleanup deletes them or the input file.

The initial shell argv exposure and OS ARG_MAX cannot be undone. Use file/stdin
for inputs beyond ARG_MAX. This cut proves producer transport; it does not
certify every downstream provider or HTTP log viewer. In particular the current
Agy adapter expands stdin into `--print` argv: direct core Agy launches now
refuse before launch mutation until that reserved adapter gains private transport.
Supervised research lanes and native interactive prompt composition still need
end-to-end privacy admission.

## Public entry matrix

| Entry                                                                                                                                   | Parser and route                                                                  | Repo/base/execution                                               | Model/prompt                                                              | Evidence boundary                                                                                                                    |
| --------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| workflow, implement, review, research, marbles                                                                                          | public deck -> core CLI -> workflow                                               | shared core flags                                                 | strict selection, private core input                                      | focused source tests; supervisor/provider integration separate                                                                       |
| audit, canary, decorate, delegate, dou, followup, guard, hydrate, intents, justdo, ownership, polarize, prune, release, scaffold, trust | same core execution route                                                         | shared core flags                                                 | same selection                                                            | alias/parser route; skill semantics unchanged                                                                                        |
| vc-* execution aliases                                                                                                                  | deck `run_wrapper` or Python wrappers -> core                                     | same as corresponding skill                                       | same as corresponding skill                                               | native alias installation not changed                                                                                                |
| resume --run-id                                                                                                                         | shell shared parser -> core operator_continue_run                                 | original root/baseline; conflicting overrides refuse              | new CLI/plan/previous precedence                                          | canonical metadata provider wins over settlement author                                                                              |
| resume --session with explicit input                                                                                                    | shared parser -> resume-session                                                   | explicit native session; no unrecorded baseline override          | whole plan or stdin, exact model                                          | native session exclusion/PTY acceptance remains open                                                                                 |
| bare resume, fork, init, partner, operator                                                                                              | shell -> spawn interactive-command -> normalize_launch_spec -> interactive-launch | canonical repo/base/execution; resume preserves recorded checkout | immutable private admission/source, exact model; fresh child run identity | real PTY fixture cases cover normal and failed provider exits for init/partner/operator/resume; native Frame/fork acceptance pending |
| dispatch TOML                                                                                                                           | dispatch schema/supervisor -> workflow                                            | existing dispatch substrate and baseline policy                   | TOML override / brief model; source and model frozen together             | dependency baseline policy needs final audit; no fleet launched by this worker                                                       |
| vc-start / vibecrafted start                                                                                                            | shared dashboard parser and create-only owner                                     | local path; default Git top-level name                            | no work/model flags added                                                 | fake-engine and real-PTY fixtures; guest integration pending                                                                         |
| VOC/App/MCP                                                                                                                             | their owned declarations -> core                                                  | capability additions required below                               | capability additions required below                                       | sibling integrations and installed acceptance pending                                                                                |
| observe/await/status and other read-only commands                                                                                       | existing observation owners                                                       | no meaningless work flags                                         | no prompt launch contract                                                 | unchanged                                                                                                                            |

## Public `vc-*` / `vibecrafted <verb>` alias matrix

One semantic owner: the packaged deck (`vibecrafted-core/vibecrafted_core/deck/vibecrafted`, mirrored at `scripts/vibecrafted`). Python `cli.SHELL_WRAPPER_VERBS` only prepends the deck verb when the installed name is a symlink or hatch-less shim to `vibecrafted`. Runtime install writes that verb into the shim (`_RUNTIME_WRAPPER_VERBS`) because `#!` rebuilds argv. Interactive zsh uses `_vetcoders_vc_passthrough`. A PATH symlink alone is not enough: `run_wrapper` falls back to `_has_skill`, and fork/operator are not skills.

| Pair                                                                                                                  | Owner / route                                                   | Publication                                                                                                                                                                   | Evidence boundary                                                                                                                                                                                            |
| --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `vc-workflow` ↔ `vibecrafted workflow` (and the other `LAUNCHERS` / `SKILL_WRAPPER_NAMES` skills, including `canary`) | deck `run_skill` / core `LAUNCHERS`                             | skill wrappers: `bin/vc-*` + wheel `[project.scripts]` when listed in `PYTHON_ENTRYPOINT_LAUNCHERS`; `canary` is a deck-verb shim (`SHELL_WRAPPER_VERBS`), not a hatch script | source tests compare both spellings at the same launch boundary; skill semantics unchanged                                                                                                                   |
| `vc-init` ↔ `vibecrafted init`                                                                                        | deck `cmd_init`                                                 | `LAUNCHER_WRAPPERS` + `SHELL_WRAPPER_VERBS` + `_RUNTIME_WRAPPER_VERBS` + `run_wrapper` + `dispatch.sh`                                                                        | existing resume/init wrapper tests                                                                                                                                                                           |
| `vc-resume` ↔ `vibecrafted resume`                                                                                    | deck `cmd_resume`                                               | same deck-verb family                                                                                                                                                         | existing wrapper tests; native session/PTY acceptance remains open                                                                                                                                           |
| `vc-fork` ↔ `vibecrafted fork`                                                                                        | deck `cmd_fork` only — no `fork_main`, no hatch script          | same deck-verb family (`vc-fork` added to the four coordinated maps + `run_wrapper` + `dispatch.sh`)                                                                          | authored source tests invoke both spellings; compare help, refusal rc/text, and normalized child/admission argv. No live provider. Installed PATH on a frozen generation is the next admission, not this cut |
| `vc-operator` ↔ `vibecrafted operator`                                                                                | deck `cmd_operator`                                             | same deck-verb family (shell `dispatch.sh` already passed through; installer/PATH now matches)                                                                                | help/refusal parity tests; live operator TTY is native acceptance                                                                                                                                            |
| `vc-start` ↔ `vibecrafted start`                                                                                      | shared start owner (not a full-arg passthrough — re-entry bomb) | `vc-start` binary / deck `cmd_start`                                                                                                                                          | existing start tests                                                                                                                                                                                         |
| `vc-dashboard` / `vc-dispatch` / `vc-help` / `vc-doctor` / `vc-status` / `vc-update` / `vc-receipt` / `telemetry`     | deck verbs via `SHELL_WRAPPER_VERBS`                            | deck-verb shims                                                                                                                                                               | existing catalog/set-equality tests                                                                                                                                                                          |
| `vc-justdo` ↔ `vibecrafted justdo`                                                                                    | deck `run_skill justdo` (not implement)                         | deck-verb shim                                                                                                                                                                | ADR-0001; existing justdo tests                                                                                                                                                                              |

Explicitly **not** equivalent — do not invent a silent twin:

| Name                                                                                  | Why it is not a 1:1 public pair                                                                        | What to use                                                                                        |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| `vc-partner` (wheel/`wrappers.partner_main`) vs `vibecrafted partner`                 | in-session skill wrapper vs TTY/frame launcher                                                         | `vibecrafted partner` / shell passthrough for the TTY face; `/vc-partner` inside a session         |
| `vc-observe` / `vc-await` / `vc-stop`                                                 | no public `vc-*` alias; read-only verbs stay on the deck/core owner and must not grow work/model flags | `vibecrafted observe\|await\|stop <agent>`                                                         |
| `vc-research-await` / `vc-research-synthesize` / `vc-sandbox` / `vc-paste` / `vc-git` | standalone or sidecar binaries (alias matrix `STANDALONE`)                                             | the named binary; no `vibecrafted <verb>` requirement                                              |
| `vc-frame` / `vc-terminal` / `voc` / `vc-server` / `vc-slack`                         | product binaries, not workflow aliases                                                                 | those binaries                                                                                     |
| Wheel `[project.scripts]`                                                             | publishes `PYTHON_ENTRYPOINT_LAUNCHERS` only                                                           | deck-verb aliases (`vc-fork`, `vc-init`, …) are installer/runtime shims, same as today's `vc-init` |

Help text may mention both spellings. Tests must compare actual normalized request/child semantics, help, and failure behavior — not merely that the strings `vc-fork` or `cmd_fork` exist. This cut does not certify every provider adapter or a live installed generation.

## Provider and presentation capability matrix

| Provider      | Model argument                                         | Private prompt mechanism in current source | Status                                                   |
| ------------- | ------------------------------------------------------ | ------------------------------------------ | -------------------------------------------------------- |
| Codex         | `-m` after `exec`, including absolute executable paths | stdin                                      | source tested; two live native Codex forks verified      |
| Claude        | `--model`                                              | print-mode stdin                           | source tested; native session/account acceptance pending |
| Grok          | `--model` (`-m` recognized)                            | `--prompt-file /dev/stdin`                 | adapter inspected; runtime acceptance pending            |
| Cursor        | `--model`                                              | existing stream/stdin contract             | adapter admission retained; runtime acceptance pending   |
| Junie         | advertised `--model` is forwarded                      | existing text input                        | native resume concurrency remains unverified             |
| Agy           | `--model` builder exists                               | current inner argv transport is unsafe     | direct workflow rejected pending spawn-owner correction  |
| Gemini legacy | deprecated                                             | no supported launch                        | rejected; no substitution of requested model             |

Codex native fork acceptance uses an isolated live interactive source; other native provider concurrency remains unverified.

## Workspace creation and Frame dependency

The recovered START implementation uses one parser for the deck and shell.
It chooses the explicit workspace name or repository basename, validates it,
reads live/exited engine inventory, and refuses duplicate engine creation.
Worktree materialization currently precedes this check; that ordering remains open. No silent attach, replacement, suffix, kill or deletion.
Inventory failures refuse rather than treating uncertainty as an empty list.
Outside Frame, creation is exclusive; a no-TTY caller opens VC Terminal only
after successful creation, carrying the exact root and created-session marker.

The recovered implementation's inside-Frame `switch-session` was not a stable
host with separate guest workspace identities. This generation therefore
refuses new inside-Frame workspace creation before mutation. The Frame baton
must supply an exclusive guest-create operation accepting host identity, guest
identity, repository/cwd and layout; a guest inventory/collision query; and an
activation operation that keeps the existing host/server and canvas alive.
The precise API names must come from that implementation, not invented flags.

## Reserved integration interfaces

- **Admitted Live Runs (`071c24b8`):** exact scoped owner patches are reconciled. Preserve launch identity fields through all phases and
  settlement; event author (`guardian`) must not replace execution provider
  (`claude`). `control_plane._merge_event_stream` preserves admitted provider identity across
  Guardian events; the regression covers launch-Claude / complete-Guardian without
  rewriting historical events. Preserve `model_source`,
  source digest/reference, repo kind/request, baseline/ref, runtime class and
  presentation in durable projections. Native continuation metadata must retain
  parent linkage. Prove readiness/publication failure semantics at process launch.
- **Spawn:** replace Agy's `--print "$(cat)"` inner argv transport, or expose it
  as unsupported. Verify supervised lanes, provider transcript echo and HTTP log
  viewing cannot disclose raw prompt snapshots. Verify inherited runtime Python
  state is scrubbed at every provider boundary; no host interpreter repair.
- **VOC:** add base, execution-runtime, repo identity capability, model selector
  and model-source/source-reference fields without reordering the provider
  catalog or changing rendering. Display unsupported VM/cloud and Agy private
  transport explicitly. Existing `workflow-capabilities` region is reserved.
- **Frame:** admit the actual stable-host guest API above before enabling
  inside-host start. Closing a viewer must not own the worker lifecycle.

Interactive admission now reserves a fresh run identity before a provider starts,
materializes the original source and admission at mode 0600, passes only private
references through command composition, and uses the existing control-plane owner
for prepared, active and terminal projections. The output capture keeps provider
TTY descriptors while recording a private, prompt-filtered transcript. Admission
replay refuses a second executor. Interactive native-session leases exclude another
interactive launch for the same native identity.

Init/partner/operator stay interactive even with explicit input. Bare resume stays
interactive; explicit prompt/file/stdin resume takes the tracked noninteractive
path. Shell command substitution may already have removed trailing newlines before
admission; the launcher preserves the bytes it actually receives, including CRLF.
Dispatch stores the admitted original source independently of its assembled runtime
instructions. Research model pins belong to the selected provider role, not swarm.

Remaining acceptance includes native Frame viewer detachment and cold-start host
handoff, supervised transcript echo filtering, other-provider native fork acceptance, complete native-
session mutual exclusion across interactive and noninteractive paths, and all-surface
idempotency/crash/cleanup. Explicit provider-session resume without a recorded run
still cannot accept an unrecorded baseline override. These are not passes.

Review and integrate the local commits before a clean signed build. The Operator
owns installation, notarization, live UI/provider acceptance and release.

## Shared session selection and native task forks

Resume and fork use `--session <session_id|current|last>`. `--run-id` is a
separate control-plane identity and cannot be combined with `--session`.
`resume --last` and `--session previous` are retired and refuse explicitly.

`current` requires one explicit provider identity from parent process context;
it never means newest mtime. A recorded source must match the selected checkout.
`last` selects the newest timezone-aware start timestamp among **recorded native
sessions in the selected checkout and provider**. It includes the current session
if that session is newest. Equal timestamps for different sessions refuse as
ambiguous. Native sessions absent from control-plane records are outside this
selector's inventory; pass their exact native ID. This is a deliberate scoped
inventory rule, not provider-global history selection.

Bare fork remains interactive. `--prompt`, `--file` or `--prompt-stdin` selects
a tracked noninteractive fork; an explicit incompatible presentation refuses.
Codex uses `exec ... fork <source> -`, with parent exec flags before the fork
subcommand. Claude and Grok compose native resume with `--fork-session`.
The normal permission/model/repository/worktree owners still apply. Original
input is retained in a private byte-exact source snapshot; the provider receives
the runtime instruction envelope through stdin. Child metadata records
`native_fork`, `fork_source_session_id` and `parent_run_id`. The parent session ID
is never seeded as the child's identity. A successful process without a distinct
native child ID is a failed fork.

Bare interactive fork admission proves a provider process was started, not that
its pane displays a new native session. The receipt leaves `agent_session_id`
and `provider_session_id` empty with `native_identity_status: pending`.
`provider_session_requested`, when present, is only the UUID supplied to a
provider's `--session-id` option; Codex fork has no such option. Without an
attributable provider acknowledgement, an exit-zero interactive fork settles as
`native_fork_identity_unconfirmed`. Codex now implements the correlated
acknowledgement described below. No newest-file or inherited
parent-ID heuristic promotes it to success. Remote app-server/pane child identity
and continued source usability remain required native acceptance.

`session_selection` retains the original selector, resolved native source,
selection root and resolution provenance through admission and control-plane
projection. A handed-off `last` selection is pinned: admission revalidates its
exact target rather than selecting again after newer runs appear. Source selection
and child identity are separate evidence.

The installed help advertises no verified native fork mechanism for Agy, Junie
or Cursor. Their refusal describes this adapter limit; it is not exhaustive proof
that their product can never fork. Interactive exact-session resume uses Agy's
`--conversation`, Junie's `--resume --session-id`, Cursor's `--resume`, and Grok's
`--resume`. These argv contracts have help evidence; their real live-source
concurrency is not certified by Codex acceptance.

Full launcher parity remains incomplete: all-entrypoint request idempotency,
prepared-admission failure recovery, workspace duplicate-before-materialization,
slow-display backpressure, supervised partial-echo/log-view privacy, and installed
Frame/VOC lifecycle acceptance retain their separate proof obligations. Output
capture follows terminal geometry during quiet periods; this is not full keyboard
or every-terminal SIGWINCH acceptance.

## Exact deck generation selection

Invoking a physical checkout `scripts/vibecrafted` (or its packaged deck copy)
selects that checkout's core and shell helpers, independently of cwd and inherited
`VIBECRAFTED_ROOT` / `VIBECRAFTED_RUNTIME_ROOT`. Those variables are context, not
development selectors. Select another checkout by invoking its deck explicitly.
`VIBECRAFTED_PYTHON` remains the explicit development interpreter override;
an override pointing into a receipted installed generation is rejected for source
selection. Otherwise the checkout's core `.venv` or host Python is used. Select
a receipted installed generation by invoking its physical deck: its adjacent
core and `bin/python3` are mandatory, with no foreign-generation/host fallback.
`--repo` / `--root` select the work repository and never the code generation.
VOC consumes the real selected deck catalog; absent provider/environment cells
remain unavailable, without a locally invented capability list.

## Correlated interactive Codex fork

The interactive spawn owner uses the selected Codex executable's app-server
`thread/fork`, correlating the response ID to the run. It requires a distinct
native child ID, exact `forkedFromId`, and admitted cwd, then independently calls
`thread/read` for that exact child and checks all three fields again. Only then
are the child IDs and `native_identity_evidence` published through the existing
control plane. The selected CLI opens that acknowledged child with `resume`;
the source is never the resume target. Permission/model selection is retained.
The public bare fork opens the child idle. Its synthetic shell skill marker is
not a user task and is not submitted to the provider; explicit task-bearing
forks continue through the private-input headless path.

Local native RPC uses that exact executable's `app-server --stdio` and its existing
Codex home. Explicit `CODEX_REMOTE=unix:///absolute/socket` uses that same remote
endpoint for the native fork and interactive CLI. Other remote transports refuse
before provider spawn; no fallback changes the selected endpoint or executable.
The acknowledgement deadline is bounded. Missing/mismatched replies leave the
receipt failed and identity unconfirmed, with no automatic retry of a possibly
completed native mutation. Process/window admission remains separate from native
identity and from terminal exit. Other providers retain their existing pending
identity semantics until a correlated acknowledgement adapter is implemented.
The Unix WebSocket adapter uses the maintained BSD-licensed `websockets` client
for framing, bounded reads and connection cleanup; it does not implement a
second session resolver or persist identities outside the control plane.
