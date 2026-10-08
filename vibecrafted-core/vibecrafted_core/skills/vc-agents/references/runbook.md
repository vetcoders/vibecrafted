# vc-agents — launcher runbook (current deck)

The operator-facing launch path is the `vibecrafted` command deck (or the
`vc-<launcher>` helper). Agents: `claude · codex · agy · junie · grok ·
cursor · kimi · copilot`. The full flag surface, not just `--prompt`:

```bash
vibecrafted <launcher> <agent> \
  --repo "$(pwd)" \                 # target checkout (worktree parent)
  --model <id> \                    # Founder's cost button — never substitute
  --effort <low|medium|high|xhigh> \# per-provider mapping; receipted skip where unsupported
  --worktree true \                 # Fleet Worktree mode (isolated branch + worktree)
  --permissions <bypass|auto|accept-edits|read-only> \
  --prompt "..."                    # or --file /path/to/plan.md
```

**Prompt composition is a shell idiom, not a string.** Compose the prompt from
live command output so the worker receives ground truth instead of homework:

```bash
# plan-from-file with a text prefix (frontmatter at prompt start trips the
# provider-conflict guard — always prefix):
vibecrafted workflow grok --repo "$(pwd)" --model grok-4.7-build-fast \
  --worktree true \
  --prompt "Implementation plan follows. Execute it end to end.

$(cat "$PLAN")"

# inject a living CLI's --help straight into the mission:
vibecrafted workflow codex --repo "$(pwd)" --model gpt-6-sol --worktree true \
  --prompt "$(echo 'Zaprojektuj i wdroż obsługę copilot CLI zgodnie z:' \
             && echo && copilot --help \
             && echo 'Agent ma być wszędzie wpisem kolejnym po kimi.')"
```

When dispatching from inside a Claude session, scrub the inherited session
env or the child misattributes:

```bash
env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION \
    -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN \
    -u CLAUDE_PID -u CLAUDE_JOB_DIR \
  zsh -lc 'vibecrafted workflow <agent> ...'
```

Companion verbs: `vibecrafted await <agent> --run-id <id>` (arm immediately),
`observe` (transcript tail), `stop` (TERM by process group),
`usage [--run-id ...]` (per-run tokens + provider-reported cost — quote it in
every settle). If these tools are unavailable, stop pretending spawn is
correctly configured and say so explicitly.

## Output convention

- Plans: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<timestamp>_<slug>.md` or another stable per-task
  filename
- Reports: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.md`
- Transcripts: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.transcript.log`
- Metadata: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.meta.json`

Every spawn should surface a launch card immediately after dispatch.
That card should expose at least:

- `run_id`
- chosen agent / model family
- plan path
- report path
- transcript path
- metadata path
- exact await command

If the operator cannot see those paths, observability is incomplete even if the
agent is technically running.

## Observation

Canonical supervisor contract (see `docs/runtime/AGENT_OPS.md`): After
dispatch, arm `vibecrafted await <agent> --run-id <id>` immediately,
supervisor-side. Control-plane JSON, report files, transcripts, panes, and
scheduled wakeups are diagnostic only, not wake signals. Hedging await with
ad-hoc pollers/watchers is a Class 3 violation; fix `control_plane.await_run`,
do not normalize the hedge.

3-signal liveness: await verdict, terminal run meta, worker pid dead, plus
promised report presence. Two agreeing signals are enough to act, three to
declare done; any disagreement means treat as live and re-arm await. Known skew:
rc=0-on-live and meta stuck `active`/`stalled` after real completion.

Observe progress through durable artifacts in
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`, but let the
dedicated runtime helper own waiting and final summary:

```bash
vibecrafted await codex --run-id <run_id>
```

For the most recent run of a given agent:

```bash
vibecrafted await codex --last
```

For multiple spawned workers, pass their launcher or metadata paths directly to
the helper and let it wait on all of them together.

If your environment exposes the observer helper, use it for transcript-level
inspection or debugging:

```bash
vibecrafted observe codex --last
```

Use the equivalent agent observer when needed, but do not rely on `observe` as
the only status surface. `vc-agents` should remain operable from durable
artifacts even when the operator is not staring at the live panes.

## Snap-dispatch integrator loop (wzorzec Foundera, 2026-10-03)

The highest-throughput operator pattern measured in practice: the integrator
authors a dense plan, fires ONE launcher per cut, and gates the return. What
makes it fast is the PLAN QUALITY, not ceremony:

1. **Plan carries measured evidence, not homework.** Exact store paths, record
   shapes, control numbers, code anchors with line pins — everything the
   integrator already probed. The worker spends zero minutes rediscovering
   formats. (Measured effect: a six-engine telemetry cut landed in one run.)
2. **Founder canon is marked binding.** Design decisions recovered from AICX
   go into the plan under an explicit "CANON/INWARIANT — implement exactly,
   never reinterpret" heading. Dispatch prompt prefix repeats it.
3. **One snap, one cut**: `vibecrafted workflow <agent> --model <founder's
cost pick> --worktree true --prompt "Implementation plan follows. …
$(cat plan.md)"` — text prefix BEFORE the plan body (frontmatter at prompt
   start trips the provider-conflict guard), env scrubbed of CLAUDE_*
   session variables. Arm `vibecrafted await` immediately.
4. **Integrator gate on return**: re-run the worker's tests yourself in its
   worktree (clean PATH), live-probe the product surface from the worktree
   binary, and settle every red test by BASELINE-DIFF on clean HEAD before
   attributing (pre-existing red ≠ cut's red). Merge `--no-ff`, push, report
   provider-reported cost per settle.
5. **Model and effort are the Founder's cost buttons** — never substituted in
   flight; a provider 400 means STOP and ask, not swap.
