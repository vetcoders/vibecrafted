# Vibecrafted Runtime

The execution layer of the Vibecrafted framework.

This is the machinery that makes agent work observable: command routing,
terminal/session management, telemetry, spawn mechanics, durable artifacts, and
the contracts that keep runs auditable.

For the canonical product lifecycle — the read/write cadence of the
`vc-ship` pipeline, the component architecture, and the async supervision
model — see [`LIFECYCLE.md`](./LIFECYCLE.md).

For the agent-ops canon — runtime failure classes of the multi-agent
machinery (gate-nap, report-on-death) with confirmed remediations and
supervisor watcher patterns — see [`AGENT_OPS.md`](./AGENT_OPS.md).

---

## Runtime Today

| Surface                                 | What it owns                                                     |
| --------------------------------------- | ---------------------------------------------------------------- |
| `scripts/vibecrafted`                   | The public command deck and route selection                      |
| `runtime/scripts/`                      | Spawn, await, watcher, meta, marbles, and install script runtime |
| `runtime/scripts/lib/`                  | Shared launcher/session/path/prompt/meta helpers                 |
| `runtime/shell/lib/`                    | Installed shell facade modules                                   |
| `runtime/vc-marbles/`                   | Extracted workflow runtime pattern                               |
| `runtime/vc-research/`                  | Research launcher shell runtime                                  |
| `runtime/vc-operator/`                  | Mission-control helper scripts                                   |
| `vibecrafted dispatch`                  | Deterministic dispatch supervisor and async lifecycle lane       |
| `vibecrafted gui` / `tui` / `dashboard` | Operator surfaces over local state                               |

## Foundations

- [loctree](https://loct.io) — Codebase mapping and architectural perception.
- [aicx](https://github.com/vetcoders/aicx) — Context boundaries and intentions retrieval.
- [prview](https://github.com/vetcoders/prview) — Continuous review pipelines.
- [Screenscribe](https://github.com/vetcoders/Screenscribe) — Voice-to-text context ingestion.

These are the senses and memory layer. They make agent work inspectable instead
of theatrical.

## Skills

Skills are the instruction contracts in `skills/`. Public docs should teach
skill-first command grammar:

```bash
vibecrafted workflow claude --prompt "Plan and implement the fix"
vibecrafted implement codex --prompt "Ship the bounded change"
```

Agent names always come last in the public grammar. Agent-first commands are
rejected with an action-first migration hint.

## External Fleet

`vc-agents` is the external specialized-agent layer. Native in-process
subagents are a different thing; they are bounded sidecars under `vc-delegate`,
not substitutes for telemetry-backed fleet dispatch.

---

## Documents in this directory

| Document                                           | What it covers                                                                                                                                     |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| [CONTRACT.md](./CONTRACT.md)                       | Session ownership, vc_frame layout, plan templates, living tree rule, spawn commands, output conventions, observation, quality gates, safety rules |
| [OPEN_THREADS.md](./OPEN_THREADS.md)               | What the superseded June-August 2026 direction docs left open, plus the invariants that still bind                                                 |
| [TRIAGE_AND_SESSIONS.md](./TRIAGE_AND_SESSIONS.md) | Finished-run triage: `vc-frame triage-run`, bucket sessions `f·x·n`, origin stamp, push≠install, research vs implement                             |
| [AGENT_OPS.md](./AGENT_OPS.md)                     | Multi-agent failure classes, await contracts, worker host sessions (G7), remediations                                                              |
| [EXECUTION_SURFACES.md](./EXECUTION_SURFACES.md)   | Canonical command surfaces, agent PATH expectations, shell helper boundaries, and sandbox execution notes                                          |
| [TOPOLOGY.md](./TOPOLOGY.md)                       | Current runtime component topology                                                                                                                 |

---

## Why use `vc-agents`?

- Your context is precious and built through many sessions, so you should
  orchestrate the work precisely and minimize context bloat.
- vc-agents are copies of yourself or extensions of you as the main agent:
  same smart, same capable, just lighter and more agile because they do not
  carry your full context window.
- You can spawn multiple agents when the work is truly parallel, and the
  `vc-why-matrix` keeps that choice explicit.
- You and your human partner can discuss further features or issues keeping
  the pace and focus.
- Spawn exists so field teams can implement, research, review, and converge
  outside the main thread while storing durable artifacts under
  `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/`.

---

## `vc-why-matrix`

Just like in the human teams, AI agents have their strengths and weaknesses.

```mermaid
  graph TD
    subgraph Codex
        CodexDesc[Why choose it:\n\n– Precision, implementation purity\n– Highly reliable code surgery]
        CodexBest[Best for:\n\n– Critical implementations\n– Exact refactors and test‑gated fixes\n– Bounded engineering execution]
        CodexAvoid[Avoid when:\n\n– Repo is chaotic\n– Brief is vague\n– You need exploration/cleanup]
        Codex --> CodexDesc
        Codex --> CodexBest
        Codex --> CodexAvoid
    end

    subgraph Claude
        ClaudeDesc[Why choose it:\n\n– Investigative depth\n– Stubborn logic tracing\n– Exhaustive research instincts]
        ClaudeBest[Best for:\n\n– Bug hunts and codebase forensics\n– Audits and architecture research\n– Assessing state‑of‑the‑art frameworks]
        ClaudeAvoid[Avoid when:\n\n– Work is straightforward surgery\n– No need for deep investigative pass]
        Claude --> ClaudeDesc
        Claude --> ClaudeBest
        Claude --> ClaudeAvoid
    end

    subgraph Gemini
        GeminiDesc[Why choose it:\n\n– Bold reframing\n– Creative system redesign\n– Fearless simplification]
        GeminiBest[Best for:\n\n– Architecture leaps\n– Radical cleanup ideas\n– Product reframing and high‑variance exploration]
        GeminiAvoid[Avoid when:\n\n– Task needs predictable, surgical implementation\n– Low‑variance execution suffices]
        Gemini --> GeminiDesc
        Gemini --> GeminiBest
        Gemini --> GeminiAvoid
    end
```

---

_For the full runtime contract and spawn mechanics, see [CONTRACT.md](./CONTRACT.md)._
_For the delegation doctrine used by agents, see [`skills/vc-agents/SKILL.md`](../../skills/vc-agents/SKILL.md)._
