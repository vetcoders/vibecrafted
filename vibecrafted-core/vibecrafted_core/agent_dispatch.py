"""Agent dispatch model-parity enforcement (Plan 06).

Python parallel to ``scripts/lib/spawn.sh``. Captures the doctrine 2026-04-10
axiom: every native delegation must pass the parent's model tier. Mixed-tier
dispatch (Opus parent -> Sonnet child) breaks the Anthropic prompt cache and
poisons the parent's reasoning chain with shallower subagent output.

This module is intended for vibecrafted-mcp and any Python-side dispatch
primitive that wants automated enforcement of the parity rule. The bash
equivalent lives in ``scripts/lib/spawn.sh``; the two implementations are
kept structurally identical so cross-tier behaviour stays predictable.

Public surface:
    - :func:`detect_parent_model` — best-effort env-var probe.
    - :func:`check_parity` — pure (parent, child) -> (ok, reason) check.
    - :func:`require_parity` — hard gate honoring
      ``VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1`` for documented exceptions.

The override env var emits a warning when used so the downgrade stays
auditable.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass

__all__ = [
    "DispatchGranularity",
    "ParityError",
    "check_parity",
    "detect_parent_model",
    "dispatch_granularity",
    "extract_session_id",
    "normalize_model",
    "require_parity",
    "sandbox_supported",
    "tier_family",
    "tier_rank",
]


@dataclass(frozen=True)
class DispatchGranularity:
    """Recommended cut shape/size for delegating work to a given model tier."""

    shape: str
    max_files_per_cut: int
    max_parallel_cuts: int
    rationale: str


class ParityError(RuntimeError):
    """Raised when a parity gate rejects a dispatch without an override."""


# Ordered list of env vars consulted by detect_parent_model().
_PARENT_ENV_VARS = (
    "VIBECRAFTED_PARENT_MODEL",
    "CLAUDE_MODEL",
    "CODEX_MODEL",
    "GEMINI_MODEL",
    "GROK_MODEL",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_SESSION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    agent: (
        re.compile(
            r"(?:^|\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]\s+)session: "
            r"([A-Za-z0-9][A-Za-z0-9-]*)",
            re.MULTILINE,
        ),
        re.compile(
            r"(?:session[_ -]?id|session)\s*[:=]\s*([A-Za-z0-9][A-Za-z0-9-]*)",
            re.IGNORECASE,
        ),
    )
    for agent in ("claude", "codex", "agy", "junie", "grok", "cursor")
}

_SANDBOX_SUPPORTED_AGENTS = {
    "claude",
    "codex",
    "agy",
    "junie",
    "grok",
    "cursor",
    "command",
}


def detect_parent_model() -> str | None:
    """Return the first non-empty parent-model identifier from the env, if any.

    The probe order matches ``spawn_detect_parent_model`` in spawn.sh:
    ``VIBECRAFTED_PARENT_MODEL`` first (operator override), then the
    family-native vars in vc-why-matrix order (Claude, Codex, Gemini).
    """
    for var in _PARENT_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    return None


def extract_session_id(agent: str, transcript: str) -> str | None:
    """Extract an agent session id from transcript text.

    The primary pattern intentionally mirrors
    ``runtime/scripts/lib/meta.sh``:
    ``(?:^|[HH:MM:SS] )session: <id>``.
    """
    clean = _ANSI_RE.sub("", transcript or "")
    patterns = _SESSION_PATTERNS.get(agent, _SESSION_PATTERNS["codex"])
    for pattern in patterns:
        match = pattern.search(clean)
        if match:
            return match.group(1)
    return None


def sandbox_supported(agent: str) -> bool:
    """Return whether the dispatch surface can run as a CLI inside microsandbox."""
    return agent in _SANDBOX_SUPPORTED_AGENTS


def normalize_model(raw: str) -> tuple[str, bool]:
    """Reduce a model identifier to its tier token.

    Returns ``(tier_token, recognized)``. When ``recognized`` is False the
    caller knows the input did not match any known family; the returned
    token is still the lowercased raw input for logging.
    """
    if not raw:
        return "", False
    lower = raw.lower()

    # Anthropic family
    if "opus" in lower:
        return "opus", True
    if "sonnet" in lower:
        return "sonnet", True
    if "haiku" in lower:
        return "haiku", True

    # Codex / OpenAI family
    if "spark" in lower:
        return "spark", True
    if "gpt-5.3" in lower or "gpt-5-3" in lower:
        return "gpt-5.3", True
    if "gpt-5.6" in lower or "gpt-5-6" in lower:
        return "gpt-5.6", True
    if "gpt-5.5" in lower or "gpt-5-5" in lower:
        return "gpt-5.5", True
    if "gpt-5" in lower:
        return "gpt-5", True
    if "gpt-4" in lower:
        return "gpt-4", True

    # Gemini family
    if "gemini-3" in lower and "pro" in lower:
        return "gemini-3-pro", True
    if "gemini-3" in lower and "flash" in lower:
        return "gemini-3-flash", True
    if "auto-gemini-3" in lower or "gemini-3-auto" in lower:
        return "gemini-3-auto", True
    if "3.1-pro" in lower or "3-pro" in lower:
        return "gemini-3-pro", True
    if "3-flash" in lower:
        return "gemini-3-flash", True
    if "gemini" in lower:
        return "gemini", True
    if "grok-build" in lower or "grok-code-fast" in lower:
        return "grok-build", True

    return lower, False


_TIER_RANK: dict[str, int] = {
    # Anthropic
    "opus": 300,
    "sonnet": 200,
    "haiku": 100,
    # Codex
    "gpt-5.3": 530,
    "gpt-5.6": 560,
    "gpt-5.5": 550,
    "gpt-5": 500,
    "spark": 450,
    "gpt-4": 400,
    # Gemini
    "gemini-3-pro": 730,
    "gemini-3-auto": 720,
    "gemini-3-flash": 710,
    "gemini": 700,
    "grok-build": 600,
}

_TIER_FAMILY: dict[str, str] = {
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "gpt-5.3": "codex",
    "gpt-5": "codex",
    "spark": "codex",
    "gpt-4": "codex",
    "gemini-3-pro": "gemini",
    "gemini-3-auto": "gemini",
    "gemini-3-flash": "gemini",
    "gemini": "gemini",
    "grok-build": "xai",
}


def dispatch_granularity(model: str) -> DispatchGranularity:
    """Choose cut size from measured model capability/cost, conservatively.

    Frontier models get fewer, coherent cuts to avoid repeatedly paying for the
    same large context. Economy and unknown models get smaller sequential cuts;
    this never weakens the existing model-parity floor.
    """
    tier, recognized = normalize_model(model)
    if not recognized:
        return DispatchGranularity(
            "surgical", 1, 1, "unknown model: smallest sequential proof surface"
        )
    if tier in {"opus", "gpt-5.6", "gpt-5.5", "gpt-5.3", "gemini-3-pro", "grok-build"}:
        return DispatchGranularity(
            "coherent",
            8,
            3,
            "frontier model: amortize repeated context across coherent cuts",
        )
    if tier in {"sonnet", "gpt-5", "gemini-3-auto", "gemini"}:
        return DispatchGranularity(
            "bounded",
            4,
            2,
            "standard model: bounded cuts with explicit integration seams",
        )
    return DispatchGranularity(
        "surgical", 2, 1, "economy model: small sequential cuts with tight verification"
    )


def tier_rank(token: str) -> int:
    """Numeric rank within a family — higher is stronger. Unknown -> 0."""
    return _TIER_RANK.get(token, 0)


def tier_family(token: str) -> str:
    """Family bucket for a tier token. Unknown -> 'unknown'."""
    return _TIER_FAMILY.get(token, "unknown")


def check_parity(parent: str, child: str) -> tuple[bool, str]:
    """Return ``(ok, reason)`` for a parent/child model pairing.

    - Both same family + child rank >= parent rank -> ``(True, "...ok...")``.
    - Cross-family pairings -> ``(True, "cross-family allowed")`` (operator
      made an explicit vc-why-matrix selection).
    - Same family + child rank < parent rank -> ``(False, diagnostic)``.
    - Unrecognized inputs -> ``(False, "cannot classify ...")``.
    """
    if not parent or not child:
        return False, f"missing argument (parent='{parent}' child='{child}')"

    parent_tier, parent_ok = normalize_model(parent)
    child_tier, child_ok = normalize_model(child)

    if not parent_ok:
        return False, f"cannot classify parent model '{parent}'"
    if not child_ok:
        return False, f"cannot classify child model '{child}'"

    p_family = tier_family(parent_tier)
    c_family = tier_family(child_tier)

    if p_family != c_family:
        return (
            True,
            f"cross-family delegation allowed (parent_family={p_family} child_family={c_family})",
        )

    p_rank = tier_rank(parent_tier)
    c_rank = tier_rank(child_tier)

    if c_rank >= p_rank:
        return (
            True,
            f"parity ok (parent_tier={parent_tier} child_tier={child_tier} within {p_family})",
        )

    return (
        False,
        (
            f"downgrade rejected — parent='{parent}' (tier={parent_tier}) "
            f"child='{child}' (tier={child_tier}); "
            "see doctrine 2026-04-10 (AGENT MODEL PARITY)"
        ),
    )


def require_parity(
    parent: str,
    child: str,
    *,
    allow_downgrade_env: str = "VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE",
    stream=None,
) -> None:
    """Hard gate: raise :class:`ParityError` on downgrade without override.

    When the env var named by ``allow_downgrade_env`` is set to ``"1"``, the
    function returns normally and prints a warning to ``stream`` (defaults
    to ``sys.stderr``) so the override stays auditable.
    """
    ok, reason = check_parity(parent, child)
    if ok:
        return

    if os.environ.get(allow_downgrade_env) == "1":
        out = stream if stream is not None else sys.stderr
        print(
            f"require_parity: WARNING — downgrade explicitly allowed by "
            f"{allow_downgrade_env}=1 (parent='{parent}' child='{child}')",
            file=out,
        )
        return

    detail = (
        "require_parity: BLOCKED.\n\n"
        "Native delegation from a higher tier to a lower tier within the same\n"
        "model family violates the AGENT MODEL PARITY axiom (doctrine 2026-04-10):\n"
        "  - Anthropic prompt cache is keyed per model. Mixed-tier dispatch breaks\n"
        "    cache sharing - the subagent re-reads context uncached.\n"
        "  - Lower-tier output feeds back into parent's reasoning chain, producing\n"
        "    shallower research, weaker code, and less reliable verdicts.\n\n"
        f"Parent model: {parent}\n"
        f"Child model:  {child}\n"
        f"Reason:       {reason}\n\n"
        "If this downgrade is intentional (e.g. Codex Spark for speed, documented\n"
        "in vc-delegate as an allowed exception), re-run with:\n"
        f"  {allow_downgrade_env}=1 ...\n"
        "Override emits a warning so it stays auditable.\n"
    )
    raise ParityError(detail)
