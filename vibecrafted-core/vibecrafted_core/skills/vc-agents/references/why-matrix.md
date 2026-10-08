# vc-why-matrix — cognitive profiles (historical core trio)

```mermaid
  graph TD
    subgraph Codex
        CodexDesc[Precision & Surgery]
        CodexBest[Best for:\n\n– Critical implementations\n– Exact refactors\n– Contract-gated execution]
        Codex --> CodexDesc
        Codex --> CodexBest
    end

    subgraph Claude
        ClaudeDesc[Forensics & Research]
        ClaudeBest[Best for:\n\n– Bug hunts across deep layers\n– Architecture audits\n– Assessing unknown paths]
        Claude --> ClaudeDesc
        Claude --> ClaudeBest
    end

    subgraph Gemini
        GeminiDesc[Radical Reframing]
        GeminiBest[Best for:\n\n– Architecture leaps\n– Fearless simplification\n– Stripping dead scaffolding\n\nText default:\n– Prose, docs, narrative\n– Human-facing copy & translation]
        Gemini --> GeminiDesc
        Gemini --> GeminiBest
    end
```

Current roster is eight agents (claude · codex · agy · junie · grok · cursor ·
kimi · copilot); pick per the Founder's current economics, not this trio alone.

## Fast execution pool — Founder selection, 2026-10-08

The Founder supplied this pool for fast, capable bounded work in the current
session. These are dispatch choices, not benchmark results or provider price
claims. Match the task first; there is no round-robin order.

| Agent    | Explicit CLI model                    | Suggested bounded cut                                              | Evidence                                                                                    |
| -------- | ------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `grok`   | `grok-4.7-build-fast`                 | Implementation across a small connected subsystem                  | Founder-supplied model; validate launch receipt                                             |
| `agy`    | `gemini-3.8-high`                     | Fixture repair, mechanical changes, docs and translation           | Founder-supplied model; validate launch receipt                                             |
| `codex`  | `gpt-6-luna`                          | Precise small patches, contract tests, independent checks          | Founder-supplied model; validate launch receipt                                             |
| `kimi`   | `kimi-code/kimi-for-coding-highspeed` | Bounded multi-file execution with explicit ownership               | Confirmed alias in local Kimi `config.toml`; subscription model `kimi-for-coding-highspeed` |
| `claude` | `claude-sonnet-5-5`                   | Causal investigation, review adjudication, fixes requiring context | Founder-supplied model; validate launch receipt                                             |

The cut suggestions are the agent's routing proposal. Model selection does not
prove latency, quality, availability or the model actually used by a provider.
The local Kimi configuration defaults to `kimi-code/k3`; selecting the coding
subscription highspeed model requires the full configured alias above. The
Founder explicitly excluded `moonshot-ai/*` API models from this pool. Other
machines must check their own configured aliases.
Never expose provider credentials while checking configuration.

For the current partner cadence, dispatch a packet at **five small fixes or two
larger fixes**, then return to diagnosis while the worker runs. Count independent
causal cuts, not failing test cases. Do not invent defects to fill the packet.
Carry the measured baseline, scope, checks and report path; the integrator owns
independent verification and admission. A completed worker report is not READY.

Use `--model` explicitly for new dispatches from this pool. A `provider_default`
receipt without an effective model is unverified model identity. Do not silently
restart a live worker or substitute its model to match a later selection.
