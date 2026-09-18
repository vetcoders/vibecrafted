"""W1-03 — one-time host preparation gate for the command bridge.

Mission R08: an unprepared host must configure exactly one provider before the
command bridge admits work; completion means readiness only — no agent spawn
and no generative (paid) model request at the execution boundary. The
completion record is durable: a second client or a UI restart reads the same
record and never resets it. A later auth failure does not lock the operator
out of Home or a live conversation — only auth-requiring actions are blocked
while the probe finds no configured provider.

Secrets policy: probes are structural (file presence, env-var presence). No
secret value is ever read into the record, returned, or logged — only signal
labels such as ``file:.claude/.credentials.json`` or ``env:ANTHROPIC_API_KEY``.

The durable write reuses ``control_plane._write_json_durable`` (atomic tmp +
fsync + rename) instead of growing a second durability path.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vibecrafted_core.control_plane import _write_json_durable, control_plane_home

RECORD_VERSION = 1
RECORD_OWNER = "vibecrafted-core/command-bridge-onboarding"
RECORD_FILENAME = "command_bridge_onboarding.json"
IN_PROGRESS_FILENAME = "command_bridge_onboarding.in-progress.json"

SURFACE_HOME = "home"
SURFACE_CONVERSATION = "conversation"
SURFACE_AUTH_ACTION = "auth_action"
SURFACES = (SURFACE_HOME, SURFACE_CONVERSATION, SURFACE_AUTH_ACTION)

REASON_READY = "ready"
REASON_ONBOARDING_REQUIRED = "onboarding_required"
REASON_AUTH_EXPIRED = "auth_expired"
REASON_RECORD_INCOMPATIBLE = "record_incompatible"

# Known provider setup signals. File paths are relative to the user's home and
# mirror the fleet config map (vc-hello fleet_scan); env names mirror the
# secret keys the spawn path already treats as provider authority. Presence
# only — values are never inspected.
PROVIDER_AUTH_FILES: dict[str, tuple[str, ...]] = {
    "claude": (".claude/.credentials.json",),
    "codex": (".codex/auth.json",),
    "grok": (".grok/config.toml",),
    "kimi": (".kimi-code/config.toml",),
}
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY",),
    "codex": ("OPENAI_API_KEY", "OPENAI_API_KEY_CODEX"),
    "grok": ("XAI_API_KEY", "GROK_API_KEY"),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
}
PROVIDERS = tuple(PROVIDER_AUTH_FILES)


class OnboardingError(RuntimeError):
    """Base class for command-bridge onboarding failures."""


class UnknownProvider(OnboardingError):
    """Raised when a provider name is outside the known fleet."""


class ProviderNotConfigured(OnboardingError):
    """Raised when completion is attempted without a configured provider."""


class OnboardingRecordIncompatible(OnboardingError):
    """Existing completion record belongs to another owner/version.

    Recovery contract (W1-03): an incompatible owner of the completion state
    is never overwritten and no second record is created; the caller must
    surface a proposal to the integrator instead.
    """


@dataclass(frozen=True)
class ProviderProbe:
    """Structural auth signal for one provider; never carries secret values."""

    provider: str
    configured: bool
    evidence: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OnboardingStatus:
    """Read model of the durable completion record."""

    state: str  # "pending" | "in_progress" | "ready" | "incompatible"
    provider: str | None = None
    record: dict[str, Any] | None = None


@dataclass(frozen=True)
class Admission:
    """Gate decision for one command-bridge surface."""

    surface: str
    allowed: bool
    reason: str
    state: str


def _user_home(user_home: Path | None) -> Path:
    return user_home if user_home is not None else Path.home()


def record_path(home: Path | None = None) -> Path:
    """Durable completion record location under the control-plane home."""
    root = home if home is not None else control_plane_home()
    return root / RECORD_FILENAME


def in_progress_path(home: Path | None = None) -> Path:
    root = home if home is not None else control_plane_home()
    return root / IN_PROGRESS_FILENAME


def probe_provider(
    provider: str,
    *,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProviderProbe:
    """Structurally probe whether one provider is configured on this host."""
    if provider not in PROVIDERS:
        raise UnknownProvider(
            f"unknown provider {provider!r} (known: {', '.join(PROVIDERS)})"
        )
    environ = os.environ if env is None else env
    home = _user_home(user_home)
    evidence: list[str] = []
    for relative in PROVIDER_AUTH_FILES[provider]:
        if (home / relative).is_file():
            evidence.append(f"file:{relative}")
    for name in PROVIDER_ENV_KEYS[provider]:
        if environ.get(name):
            evidence.append(f"env:{name}")
    return ProviderProbe(
        provider=provider, configured=bool(evidence), evidence=tuple(evidence)
    )


def configured_providers(
    *,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """All fleet providers with at least one structural auth signal."""
    return [
        provider
        for provider in PROVIDERS
        if probe_provider(provider, user_home=user_home, env=env).configured
    ]


def load_record(home: Path | None = None) -> dict[str, Any] | None:
    """Load the completion record, refusing foreign or future versions.

    Returns ``None`` when no record exists. Raises
    ``OnboardingRecordIncompatible`` when a record exists but was written by
    another owner or an unknown record version — the record is left untouched.
    """
    path = record_path(home)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OnboardingRecordIncompatible(
            f"unreadable onboarding record at {path}: {exc}"
        ) from exc
    if (
        not isinstance(record, dict)
        or record.get("record_version") != RECORD_VERSION
        or record.get("owner") != RECORD_OWNER
    ):
        raise OnboardingRecordIncompatible(
            f"onboarding record at {path} has an incompatible owner or version; "
            "refusing to replace it — surface a proposal to the integrator"
        )
    return record


def status(home: Path | None = None) -> OnboardingStatus:
    """Current onboarding state for this host."""
    try:
        record = load_record(home)
    except OnboardingRecordIncompatible:
        return OnboardingStatus(state="incompatible")
    if record is not None:
        return OnboardingStatus(
            state="ready", provider=record.get("provider"), record=record
        )
    if in_progress_path(home).is_file():
        return OnboardingStatus(state="in_progress")
    return OnboardingStatus(state="pending")


def begin(provider: str | None = None, *, home: Path | None = None) -> Path:
    """Mark preparation as started. The marker never counts as completion."""
    if provider is not None and provider not in PROVIDERS:
        raise UnknownProvider(
            f"unknown provider {provider!r} (known: {', '.join(PROVIDERS)})"
        )
    marker = in_progress_path(home)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "owner": RECORD_OWNER,
        "record_version": RECORD_VERSION,
        "provider": provider,
        "started_at": datetime.now(UTC).isoformat(),
    }
    marker.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return marker


def cancel(*, home: Path | None = None) -> None:
    """Abort preparation: drop the in-progress marker, never write completion."""
    try:
        in_progress_path(home).unlink()
    except FileNotFoundError:
        pass


def complete(
    provider: str | None = None,
    *,
    home: Path | None = None,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Record readiness after exactly one configured provider is proven.

    Pure local state transition: no subprocess, no network, no model request.
    Idempotent for an existing compatible record; refuses (without writing)
    when no provider is configured or a foreign record owns the state.
    """
    existing = load_record(home)
    if existing is not None:
        return existing

    if provider is None:
        configured = configured_providers(user_home=user_home, env=env)
        if not configured:
            raise ProviderNotConfigured(
                "host preparation requires one configured provider; none of "
                f"{', '.join(PROVIDERS)} shows an auth signal"
            )
        provider = configured[0]

    probe = probe_provider(provider, user_home=user_home, env=env)
    if not probe.configured:
        raise ProviderNotConfigured(
            f"provider {provider!r} shows no auth signal; refusing to record "
            "a false completion"
        )

    record = {
        "record_version": RECORD_VERSION,
        "owner": RECORD_OWNER,
        "provider": probe.provider,
        "completed_at": datetime.now(UTC).isoformat(),
        "evidence": list(probe.evidence),
    }
    _write_json_durable(record_path(home), record)
    cancel(home=home)
    return record


def admit(
    surface: str,
    *,
    home: Path | None = None,
    user_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Admission:
    """Gate one command-bridge surface against the onboarding state.

    - pending / in_progress: every surface is refused with
      ``onboarding_required`` — first preparation is mandatory.
    - incompatible: fail closed with ``record_incompatible``.
    - ready: Home and live conversations stay open even when provider auth
      later expires; only auth-requiring actions re-probe the host and are
      refused with ``auth_expired`` while no provider is configured.
    """
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r} (known: {', '.join(SURFACES)})")
    current = status(home)
    if current.state == "incompatible":
        return Admission(surface, False, REASON_RECORD_INCOMPATIBLE, current.state)
    if current.state != "ready":
        return Admission(surface, False, REASON_ONBOARDING_REQUIRED, current.state)
    if surface in (SURFACE_HOME, SURFACE_CONVERSATION):
        return Admission(surface, True, REASON_READY, current.state)
    if configured_providers(user_home=user_home, env=env):
        return Admission(surface, True, REASON_READY, current.state)
    return Admission(surface, False, REASON_AUTH_EXPIRED, current.state)
