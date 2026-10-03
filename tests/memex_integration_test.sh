#!/usr/bin/env bash
# memex_integration_test.sh — Plan 09 (META_22) verification gate.
#
# Sandboxed integration smoke for the memex cross-session retrieval
# client wired into `/vc-init` Sense 1 (intentions) as fallthrough.
#
# Scenarios:
#   1. SKILL.md Sense 1 documents memex fallthrough (markdown contract).
#   2. memex_client imports cleanly and exposes the public surface.
#   3. Sparse-AICX + populated-memex fixture: search() returns memex hits
#      via injected mcp_call stub, all tagged authority=memex_derived.
#   4. Negative case: endpoint unreachable → empty list, warning logged,
#      no exception escapes.
#   5. Config file precedence: TOML wins over env vars.
#   6. Pure defaults: client is disabled, search short-circuits cleanly.
#
# Designed to run inside `make test-memex` alongside the pytest unit
# suite. The bash tier exists to assert the OPERATOR-VISIBLE contract:
# Sense 1 documentation, end-to-end CLI invocation, and graceful
# degradation in a sandbox shell environment.
#
# Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$HERE/.." && pwd)

PASS=0
FAIL=0

red()   { printf '\033[31m%s\033[0m' "$*"; }
green() { printf '\033[32m%s\033[0m' "$*"; }
amber() { printf '\033[33m%s\033[0m' "$*"; }

ok() {
    printf '  %s %s\n' "$(green ok)" "$1"
    PASS=$((PASS + 1))
}

fail() {
    printf '  %s %s\n' "$(red FAIL)" "$1"
    if [[ -n "${2:-}" ]]; then
        printf '       %s\n' "$2"
    fi
    FAIL=$((FAIL + 1))
}

section() {
    printf '\n%s\n' "$(amber "── $1 ──")"
}

# Sandboxed config dir — never touches the operator's real config.
SANDBOX=$(mktemp -d -t vibecrafted-memex.XXXXXX)
trap 'rm -rf "$SANDBOX"' EXIT

run_py() {
    cd "$REPO_ROOT"
    uv run --project vibecrafted-core --quiet python "$@"
}

# ----------------------------------------------------------------- section 1
section "Sense 1 SKILL.md documents memex fallthrough"

SKILL_MD="$REPO_ROOT/skills/vc-init/SKILL.md"
if [[ ! -f "$SKILL_MD" ]]; then
    fail "skills/vc-init/SKILL.md missing"
else
    if grep -qE 'Memex fallthrough|memex_derived' "$SKILL_MD"; then
        ok "Sense 1 mentions memex fallthrough"
    else
        fail "Sense 1 does not document memex fallthrough" \
             "expected 'Memex fallthrough' or 'memex_derived' marker"
    fi
    if grep -q "Plan 09" "$SKILL_MD"; then
        ok "Plan 09 citation present"
    else
        fail "Plan 09 citation absent from SKILL.md"
    fi
fi

# ----------------------------------------------------------------- section 2
section "memex_client public surface importable"

if run_py - <<'PY' >/dev/null 2>&1
from vibecrafted_core import memex_client as mc
required = {
    "search", "load_config", "MemexChunk", "MemexConfig",
    "MEMEX_AUTHORITY_LABEL", "SPARSE_AICX_THRESHOLD",
}
missing = required - set(dir(mc))
if missing:
    raise SystemExit(f"missing public symbols: {sorted(missing)}")
assert mc.MEMEX_AUTHORITY_LABEL == "memex_derived"
assert mc.SPARSE_AICX_THRESHOLD == 5
PY
then
    ok "public surface complete (MEMEX_AUTHORITY_LABEL + SPARSE_AICX_THRESHOLD)"
else
    fail "public surface assertion failed"
fi

# ----------------------------------------------------------------- section 3
section "Sparse-AICX + populated-memex fallthrough"

if run_py - <<'PY'
from vibecrafted_core import memex_client as mc

def fake_mcp(query, namespace, limit):
    # Simulate populated memex: 3 chunks for the operator's query.
    return {
        "chunks": [
            {"text": f"chunk-{i} for {query}", "score": 0.9 - i*0.1,
             "source": f"aicx/session-{i}.md", "namespace": namespace}
            for i in range(3)
        ]
    }

cfg = mc.MemexConfig(
    endpoint="http://memex.local:11211",
    token="test-token",
    default_namespace="local",
    timeout_seconds=2.0,
    enabled=True,
    source="test",
)
out = mc.search("Sense 1 sparse fallthrough", config=cfg, mcp_call=fake_mcp)
assert len(out) == 3, f"expected 3 chunks, got {len(out)}"
assert all(c.authority == "memex_derived" for c in out), \
    "every chunk must be tagged memex_derived"
assert out[0].source == "aicx/session-0.md"
print("OK")
PY
then
    ok "memex returns 3 chunks, all tagged memex_derived"
else
    fail "populated-memex fallthrough did not return tagged chunks"
fi

# ----------------------------------------------------------------- section 4
section "Graceful degradation when endpoint unreachable"

# Use 127.0.0.1:1 which is reserved and refuses TCP — synchronous fail.
if run_py - <<'PY'
import logging, sys
from vibecrafted_core import memex_client as mc

# Capture WARNING logs to ensure degradation emits the audit trail.
buf = []
handler = logging.Handler()
handler.emit = lambda rec: buf.append(rec.getMessage())
logging.getLogger("vibecrafted_core.memex_client").addHandler(handler)
logging.getLogger("vibecrafted_core.memex_client").setLevel(logging.WARNING)

cfg = mc.MemexConfig(
    endpoint="http://127.0.0.1:1",  # connection refused, fast fail
    token="t",
    default_namespace="local",
    timeout_seconds=0.5,
    enabled=True,
    source="test",
)
out = mc.search("anything", config=cfg)
assert out == [], f"expected empty list, got {out!r}"
assert any("unreachable" in m or "memex" in m for m in buf), \
    f"expected warning log, got: {buf!r}"
print("OK")
PY
then
    ok "unreachable endpoint degrades to empty list + warning"
else
    fail "graceful degradation broke (exception escaped or no warning)"
fi

# ----------------------------------------------------------------- section 5
section "Config file precedence ([memex] table over env)"

CFG_FILE="$SANDBOX/config.toml"
cat > "$CFG_FILE" <<'TOML'
[server]
port = 3024

[memex]
endpoint = "http://from-toml.local:11211"
token = "tok-toml"
default_namespace = "toml-ns"
timeout_seconds = 4.0
TOML

if MEMEX_ENDPOINT="http://from-env.local" MEMEX_TOKEN="tok-env" \
   MEMEX_TIMEOUT_SECONDS="1.0" \
   CFG_PATH="$CFG_FILE" run_py - <<'PY'
import os
from pathlib import Path
from vibecrafted_core import memex_client as mc

cfg = mc.load_config(
    config_path=Path(os.environ["CFG_PATH"]),
    environ=dict(os.environ),
)
assert cfg.endpoint == "http://from-toml.local:11211", cfg.endpoint
assert cfg.token == "tok-toml", cfg.token
assert cfg.default_namespace == "toml-ns", cfg.default_namespace
assert cfg.timeout_seconds == 4.0, cfg.timeout_seconds
assert cfg.enabled is True
assert "config:" in cfg.source
print("OK")
PY
then
    ok "TOML config wins over env vars when both present"
else
    fail "config precedence violated"
fi

# ----------------------------------------------------------------- section 6
section "Pure defaults: client disabled, search returns []"

if CFG_PATH="$SANDBOX/does-not-exist.toml" run_py - <<'PY'
import os
from pathlib import Path
from vibecrafted_core import memex_client as mc

# Empty environ — no env-side overrides.
cfg = mc.load_config(config_path=Path(os.environ["CFG_PATH"]), environ={})
assert cfg.enabled is False
assert cfg.token == ""
out = mc.search("anything", config=cfg)
assert out == []
print("OK")
PY
then
    ok "pure defaults short-circuit search() to []"
else
    fail "pure defaults degradation broke"
fi

# ----------------------------------------------------------------- section 7
section "Empty query short-circuit"

if run_py - <<'PY'
from vibecrafted_core import memex_client as mc
assert mc.search("") == []
assert mc.search("   ") == []
print("OK")
PY
then
    ok "empty / whitespace query returns []"
else
    fail "empty query did not short-circuit"
fi

# ----------------------------------------------------------------- summary
printf '\n%s\n' "$(amber "── summary ──")"
printf '  pass:  %d\n' "$PASS"
printf '  fail:  %d\n' "$FAIL"

if (( FAIL > 0 )); then
    printf '\n%s\n' "$(red 'memex integration test FAILED')"
    exit 1
fi

printf '\n%s\n' "$(green 'memex integration test green')"
