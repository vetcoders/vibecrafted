# Memex — cross-session retrieval (Plan 09)

> Optional opt-in semantic substrate that augments `/vc-init` Sense 1
> (intentions) when local AICX is sparse. Vibecrafted works without it.

## What memex is

[`rust-memex`](https://github.com/vetcoders/rust-memex) is a small,
namespace-aware semantic memory substrate. It indexes prior agent
sessions, chronicle fragments, and operator notes into chunks that can
be retrieved by free-text query across machines. The memex host
serves its clients over your private network.

Vibecrafted does **not** ship memex. Operators install the foundation
separately (`vibecrafted doctor` reports its presence). Plan 09 wires
the client into the perception layer; the substrate itself is
upstream.

## How `/vc-init` Sense 1 uses memex

Sense 1 (intentions) primarily pulls from local AICX — the
intention-retrieval engine that captures what previous agents in
this checkout decided and why. When that local lookup returns fewer
than **5 chunks** for the current scope
(`memex_client.SPARSE_AICX_THRESHOLD`), the agent SHOULD fall
through to memex:

```python
from vibecrafted_core import memex_client

# After local AICX returned < 5 chunks for the current scope.
chunks = memex_client.search(
    "Sense 1 sparse AICX fallthrough",
    namespace="vibecrafted",
    limit=10,
)
for chunk in chunks:
    # chunk.authority == "memex_derived"
    ...
```

The fallthrough rule is documented inline in
[`skills/vc-init/SKILL.md`](../skills/vc-init/SKILL.md) Sense 1.

## Configuration

Two paths; the TOML file wins when both are present.

### Option A — operator config file (recommended)

Copy the template:

```bash
mkdir -p ~/.config/vetcoders
cp config/memex.toml.example ~/.config/vetcoders/memex.toml
# Edit ~/.config/vetcoders/memex.toml — fill in endpoint and token.
```

Fields:

| Field               | Default                    | Notes                                                         |
| ------------------- | -------------------------- | ------------------------------------------------------------- |
| `endpoint`          | `http://memex.local:11211` | Mesh-hosted memex; trailing slash is stripped.                |
| `token`             | `""`                       | Bearer token; required to enable the client.                  |
| `default_namespace` | `local`                    | Used when `search()` is called without an explicit namespace. |
| `timeout_seconds`   | `5.0`                      | HTTP timeout. Lower for tight CI shells.                      |

### Option B — environment variables (ephemeral / CI)

```bash
export MEMEX_ENDPOINT=http://memex.local:11211
export MEMEX_TOKEN=<bearer-token>
export MEMEX_NAMESPACE=vibecrafted
export MEMEX_TIMEOUT_SECONDS=3
```

Env-only mode is intended for CI runners and one-off shells. For
day-to-day operator use, prefer the TOML file — it survives shell
restarts without polluting your dotfiles.

### Disabled mode (default)

Without either path, the client stays in **disabled mode**.
`search()` short-circuits to an empty list and emits a single
`INFO`-level log line on first call. No exceptions; the fallthrough
is silently a no-op. This is the correct behavior for operators who
have not opted in to memex — `/vc-init` proceeds exactly as it would
in v1.7.

## Authority tier — `memex_derived`

Plan 09 introduces a new authority label for chunks sourced from
memex. The full ranking (high → low):

1. `repo_verified` — snapshot fact, top trust
2. `loctree_derived` — analyzer inference
3. `aicx_operator` — sticky operator intent
4. `aicx_agent` — prior agent outcome
5. **`memex_derived`** — cross-session memex retrieval (NEW, Plan 09)
6. `aicx_failure` — prior failed path; don't repeat
7. `semantic_guess` — heuristic; verify
8. `stale_or_unknown` — re-check

**Why `memex_derived` sits below `aicx_*`:** AICX chunks carry
operator-intent authority captured in the same checkout where the
decision was made. Memex chunks pull from cross-machine sessions
whose authority context is weaker — the chunk may be authoritative
in its origin namespace but is candidate context here. Treat memex
hits as input to verify against Sense 2 (perception via loctree) or
Sense 3 (ground truth via git history + agent configs), not as
final-tier directives.

Operators reviewing Plan 09 may decide to elevate or pin the tier
for specific namespaces in a future revision; the current default is
deliberately conservative.

## Verification

```bash
# Bash + Python tiers together.
make test-memex
```

The bash tier (`tests/memex_integration_test.sh`) sandboxes the
client and asserts:

- public surface complete
- populated memex returns chunks tagged `memex_derived`
- unreachable endpoint → empty list + warning (no exception)
- TOML config wins over env vars
- pure defaults disable the client cleanly
- empty/whitespace queries short-circuit

The Python tier (`vibecrafted-core/tests/test_memex_client.py`)
covers HTTP success/failure parsing, MCP-bridge transport, config
precedence layers, and malformed-response paths.

## Cross-links

- Plan 09 contract: [META_22 §09](plans/META_22_SCAFFOLD_TO_RELEASE.md)
- Plan 08 (AICX sync v2 — complementary): same META_22 doc
- Operator config template: [`config/memex.toml.example`](../config/memex.toml.example)
- Sense 1 fallthrough rule: [`skills/vc-init/SKILL.md`](../skills/vc-init/SKILL.md)
- Marble report: [`.vibecrafted/reports/marbles/2026_0512/plan-09-memex-retrieval.md`](../.vibecrafted/reports/marbles/2026_0512/plan-09-memex-retrieval.md)

## Operator-honest framing

This is opt-in tooling. The agent perception layer works without
memex — vibecrafted's v1.7 surface is unchanged for operators who
don't configure it. When configured, memex offers cross-session
context that would otherwise require manual escalation ("look at
the host-d session from last Thursday"). The trade-off:
weaker authority tier, opt-in cognitive load (token rotation,
namespace hygiene), and dependence on a mesh service. Worth it for
operators running multiple machines in the Vetcoders mesh; safe to
ignore for everyone else.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
