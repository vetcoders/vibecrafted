"""Pytest mirror of tests/spawn_parity_test.sh (Plan 06).

Exercises ``vibecrafted_core.agent_dispatch`` against the AGENT MODEL PARITY
axiom (doctrine 2026-04-10). The bash and Python parity layers are kept
behaviourally identical; this suite verifies that for the Python side.

Run:
    python -m pytest tests/agent_dispatch_test.py -q
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

# vibecrafted-core is a sibling project; pytest needs its src on sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORE_SRC = _REPO_ROOT / "vibecrafted-core"
if str(_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(_CORE_SRC))

from vibecrafted_core import agent_dispatch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure parity env vars are reset between tests."""
    for var in (
        "VIBECRAFTED_PARENT_MODEL",
        "CLAUDE_MODEL",
        "CODEX_MODEL",
        "GEMINI_MODEL",
        "VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


# ---------------------------------------------------------------------------
# normalize_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected_tier",
    [
        ("claude-opus-4-7", "opus"),
        ("Opus 4.7", "opus"),
        ("claude-sonnet-4-7[1m]", "sonnet"),
        ("haiku", "haiku"),
        ("gpt-5.3-codex", "gpt-5.3"),
        ("gpt-5.3-codex-spark", "spark"),
        ("gpt-5-mini", "gpt-5"),
        ("gpt-4o", "gpt-4"),
        ("gemini-3.1-pro-preview", "gemini-3-pro"),
        ("gemini-3-flash", "gemini-3-flash"),
        ("auto-gemini-3", "gemini-3-auto"),
    ],
)
def test_normalize_model_recognized(raw, expected_tier):
    tier, recognized = agent_dispatch.normalize_model(raw)
    assert recognized is True
    assert tier == expected_tier


def test_normalize_model_unknown():
    tier, recognized = agent_dispatch.normalize_model("custom-llm-7b")
    assert recognized is False
    assert tier == "custom-llm-7b"


def test_normalize_model_empty():
    tier, recognized = agent_dispatch.normalize_model("")
    assert recognized is False
    assert tier == ""


# ---------------------------------------------------------------------------
# tier_rank / tier_family
# ---------------------------------------------------------------------------


def test_tier_rank_ordering_within_anthropic():
    assert agent_dispatch.tier_rank("opus") > agent_dispatch.tier_rank("sonnet")
    assert agent_dispatch.tier_rank("sonnet") > agent_dispatch.tier_rank("haiku")


def test_tier_rank_ordering_within_codex():
    assert agent_dispatch.tier_rank("gpt-5.3") > agent_dispatch.tier_rank("gpt-5")
    # Spark is documented as speed-tier below mainline gpt-5.3 in vc-delegate.
    assert agent_dispatch.tier_rank("gpt-5.3") > agent_dispatch.tier_rank("spark")
    assert agent_dispatch.tier_rank("gpt-5") > agent_dispatch.tier_rank("spark")
    assert agent_dispatch.tier_rank("spark") > agent_dispatch.tier_rank("gpt-4")


def test_tier_rank_unknown_token():
    assert agent_dispatch.tier_rank("nope") == 0


def test_tier_family_buckets():
    assert agent_dispatch.tier_family("opus") == "anthropic"
    assert agent_dispatch.tier_family("gpt-5.3") == "codex"
    assert agent_dispatch.tier_family("spark") == "codex"
    assert agent_dispatch.tier_family("gemini-3-pro") == "gemini"
    assert agent_dispatch.tier_family("nope") == "unknown"


@pytest.mark.parametrize(
    "model,shape,max_files,max_parallel",
    [
        ("gpt-5.6-sol", "coherent", 8, 3),
        ("grok-build", "coherent", 8, 3),
        ("claude-sonnet-4-7", "bounded", 4, 2),
        ("claude-haiku-4", "surgical", 2, 1),
        ("unknown-model", "surgical", 1, 1),
    ],
)
def test_dispatch_granularity_is_model_aware_and_conservative(
    model, shape, max_files, max_parallel
):
    policy = agent_dispatch.dispatch_granularity(model)
    assert policy.shape == shape
    assert policy.max_files_per_cut == max_files
    assert policy.max_parallel_cuts == max_parallel


# ---------------------------------------------------------------------------
# check_parity
# ---------------------------------------------------------------------------


def test_check_parity_same_tier_ok():
    ok, reason = agent_dispatch.check_parity("claude-opus-4-7", "opus")
    assert ok is True
    assert "parity ok" in reason


def test_check_parity_upgrade_ok():
    # Sonnet parent -> Opus child is an upgrade, allowed.
    ok, reason = agent_dispatch.check_parity("claude-sonnet-4-7", "claude-opus-4-7")
    assert ok is True
    assert "parity ok" in reason


def test_check_parity_downgrade_rejected():
    ok, reason = agent_dispatch.check_parity("claude-opus-4-7", "claude-sonnet-4-7")
    assert ok is False
    assert "downgrade rejected" in reason
    assert "doctrine 2026-04-10" in reason


def test_check_parity_haiku_downgrade_rejected():
    ok, reason = agent_dispatch.check_parity("claude-opus-4-7", "claude-haiku-4")
    assert ok is False
    assert "tier=haiku" in reason


def test_check_parity_cross_family_allowed():
    """Cross-family is an explicit vc-why-matrix selection, not a downgrade."""
    ok, reason = agent_dispatch.check_parity("claude-opus-4-7", "gpt-5.3-codex")
    assert ok is True
    assert "cross-family" in reason


def test_check_parity_spark_codex_downgrade_rejected():
    """Documented Spark exception still requires explicit override."""
    ok, reason = agent_dispatch.check_parity("gpt-5.3-codex", "gpt-5.3-codex-spark")
    assert ok is False
    assert "downgrade rejected" in reason


def test_check_parity_unrecognized_input():
    ok, reason = agent_dispatch.check_parity("custom-llm", "another-llm")
    assert ok is False
    assert "cannot classify" in reason


def test_check_parity_missing_args():
    ok, reason = agent_dispatch.check_parity("", "")
    assert ok is False
    assert "missing argument" in reason


# ---------------------------------------------------------------------------
# require_parity
# ---------------------------------------------------------------------------


def test_require_parity_pass_returns_none():
    # Returns None on pass.
    assert agent_dispatch.require_parity("claude-opus-4-7", "claude-opus-4-7") is None


def test_require_parity_raises_on_downgrade():
    with pytest.raises(agent_dispatch.ParityError) as excinfo:
        agent_dispatch.require_parity("claude-opus-4-7", "claude-sonnet-4-7")
    detail = str(excinfo.value)
    assert "BLOCKED" in detail
    assert "doctrine 2026-04-10" in detail
    assert "VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE" in detail


def test_require_parity_override_allows_with_warning(monkeypatch):
    monkeypatch.setenv("VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE", "1")
    stream = io.StringIO()
    # Should NOT raise.
    agent_dispatch.require_parity(
        "claude-opus-4-7",
        "claude-sonnet-4-7",
        stream=stream,
    )
    output = stream.getvalue()
    assert "WARNING" in output
    assert "downgrade explicitly allowed" in output


def test_require_parity_override_value_must_be_one(monkeypatch):
    # Truthy but not "1" -> still rejected.
    monkeypatch.setenv("VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE", "true")
    with pytest.raises(agent_dispatch.ParityError):
        agent_dispatch.require_parity("claude-opus-4-7", "claude-sonnet-4-7")


def test_require_parity_codex_spark_override(monkeypatch):
    """The documented Codex Spark exception: must invoke override env var."""
    # Without override: rejected.
    with pytest.raises(agent_dispatch.ParityError):
        agent_dispatch.require_parity("gpt-5.3-codex", "gpt-5.3-codex-spark")

    # With override: allowed with warning.
    monkeypatch.setenv("VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE", "1")
    stream = io.StringIO()
    agent_dispatch.require_parity("gpt-5.3-codex", "gpt-5.3-codex-spark", stream=stream)
    assert "WARNING" in stream.getvalue()


# ---------------------------------------------------------------------------
# detect_parent_model
# ---------------------------------------------------------------------------


def test_detect_parent_model_empty_when_unset():
    assert agent_dispatch.detect_parent_model() is None


def test_detect_parent_model_uses_claude_model_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-4-7")
    assert agent_dispatch.detect_parent_model() == "claude-opus-4-7"


def test_detect_parent_model_vibecrafted_var_wins(monkeypatch):
    """VIBECRAFTED_PARENT_MODEL is the operator-explicit override; it wins."""
    monkeypatch.setenv("VIBECRAFTED_PARENT_MODEL", "opus")
    monkeypatch.setenv("CLAUDE_MODEL", "sonnet")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.3")
    assert agent_dispatch.detect_parent_model() == "opus"


def test_detect_parent_model_codex_fallback(monkeypatch):
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.3-codex")
    assert agent_dispatch.detect_parent_model() == "gpt-5.3-codex"


def test_detect_parent_model_gemini_fallback(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
    assert agent_dispatch.detect_parent_model() == "gemini-3.1-pro-preview"


# ---------------------------------------------------------------------------
# Cross-implementation alignment smoke
# ---------------------------------------------------------------------------


def test_python_matches_bash_for_canonical_cases():
    """Smoke test that the Python ranks/families align with spawn.sh.

    If this fails, the two parity layers have drifted — fix one to match
    the other before merging.
    """
    # Anthropic ordering
    assert agent_dispatch.tier_rank("opus") > agent_dispatch.tier_rank("sonnet")
    # Cross-family allowed
    ok, _ = agent_dispatch.check_parity("claude-opus-4-7", "gpt-5.3-codex")
    assert ok is True
    # Same-family downgrade rejected
    ok, _ = agent_dispatch.check_parity("gpt-5.3-codex", "gpt-4o")
    assert ok is False
