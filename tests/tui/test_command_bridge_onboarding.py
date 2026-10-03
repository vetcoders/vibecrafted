"""W1-03 admission verifier: one-time host preparation for the command bridge.

Proves the R08 contract against an isolated runtime home with fixture auth
(no real secrets):

1. An unprepared host is refused until exactly one provider is configured.
2. Completion marks readiness only — zero spawns and zero generative model
   requests at the execution boundary.
3. A second client and a UI restart read the same durable record; nothing
   resets or rewrites it.
4. After auth expiry, Home and a live conversation still open; only the
   auth-requiring action is blocked.
5. Cancelling or interrupting preparation never writes a false completion
   and never leaks a secret into the record or log surface.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import command_bridge_onboarding as cbo

SECRET_SENTINEL = "sk-fixture-w103-not-a-real-secret"
ALL_ENV_KEYS = tuple(key for keys in cbo.PROVIDER_ENV_KEYS.values() for key in keys)


@pytest.fixture(autouse=True)
def _isolated_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """Isolated runtime home + user home; no ambient provider auth leaks in."""
    vc_home = tmp_path / "vc-home"
    user_home = tmp_path / "user-home"
    vc_home.mkdir()
    user_home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(vc_home))
    monkeypatch.setenv("HOME", str(user_home))
    for name in ALL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    return {"vc_home": vc_home, "user_home": user_home}


@pytest.fixture
def homes(_isolated_host: dict[str, Path]) -> dict[str, Path]:
    return _isolated_host


def _fixture_auth(user_home: Path, provider: str = "claude") -> Path:
    """Drop a structural auth fixture — dummy content, never a real secret."""
    relative = cbo.PROVIDER_AUTH_FILES[provider][0]
    target = user_home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"fixture": true, "note": "w1-03 admission"}\n')
    return target


def _forbid_execution_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any spawn or network call during onboarding fails the verifier."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "onboarding crossed the execution boundary (spawn/model request)"
        )

    monkeypatch.setattr(subprocess, "Popen", _refuse)
    monkeypatch.setattr(subprocess, "run", _refuse)
    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def test_unprepared_host_requires_single_provider_setup(
    homes: dict[str, Path],
) -> None:
    assert cbo.status().state == "pending"
    for surface in cbo.SURFACES:
        decision = cbo.admit(surface)
        assert not decision.allowed, surface
        assert decision.reason == cbo.REASON_ONBOARDING_REQUIRED, surface

    cbo.begin(provider="claude")
    assert cbo.status().state == "in_progress"
    decision = cbo.admit(cbo.SURFACE_HOME)
    assert not decision.allowed
    assert decision.reason == cbo.REASON_ONBOARDING_REQUIRED
    assert not cbo.record_path().exists()

    _fixture_auth(homes["user_home"])
    record = cbo.complete("claude")
    assert record["provider"] == "claude"
    assert cbo.status().state == "ready"
    assert cbo.admit(cbo.SURFACE_HOME).allowed


def test_completion_is_readiness_only_no_spawn_no_model_request(
    homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_execution_boundary(monkeypatch)
    monkeypatch.setenv("KIMI_API_KEY", SECRET_SENTINEL)

    record = cbo.complete("kimi")

    assert record["provider"] == "kimi"
    assert record["record_version"] == cbo.RECORD_VERSION
    assert record["owner"] == cbo.RECORD_OWNER
    assert record["evidence"] == ["env:KIMI_API_KEY"]
    raw = cbo.record_path().read_text(encoding="utf-8")
    assert SECRET_SENTINEL not in raw
    assert cbo.status().state == "ready"
    assert all(cbo.admit(surface).allowed for surface in cbo.SURFACES)


def test_second_client_and_ui_restart_never_reset_completion(
    homes: dict[str, Path],
) -> None:
    _fixture_auth(homes["user_home"])
    record = cbo.complete("claude")
    before = cbo.record_path().read_bytes()

    # Second client: a fresh reader against the same durable home.
    reread = cbo.load_record()
    assert reread == record

    # UI restart: status/admit re-resolve from disk and must not rewrite.
    assert cbo.status().state == "ready"
    assert cbo.admit(cbo.SURFACE_HOME).allowed
    assert cbo.admit(cbo.SURFACE_CONVERSATION).allowed
    after = cbo.record_path().read_bytes()
    assert after == before

    # Idempotent completion returns the existing record instead of resetting.
    again = cbo.complete("claude")
    assert again == record
    assert cbo.record_path().read_bytes() == before


def test_expired_auth_keeps_home_and_live_conversation_open(
    homes: dict[str, Path],
) -> None:
    fixture = _fixture_auth(homes["user_home"])
    cbo.complete("claude")
    assert cbo.admit(cbo.SURFACE_AUTH_ACTION).allowed

    # Auth expires: every structural signal for every provider disappears.
    fixture.unlink()
    assert cbo.configured_providers() == []

    home_decision = cbo.admit(cbo.SURFACE_HOME)
    conversation_decision = cbo.admit(cbo.SURFACE_CONVERSATION)
    action_decision = cbo.admit(cbo.SURFACE_AUTH_ACTION)

    assert home_decision.allowed
    assert conversation_decision.allowed
    assert not action_decision.allowed
    assert action_decision.reason == cbo.REASON_AUTH_EXPIRED
    # The durable record is untouched by the later auth failure.
    assert cbo.status().state == "ready"


def test_cancel_and_refusal_never_write_false_completion_or_secrets(
    homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET_SENTINEL)

    # Interrupted preparation: begin then cancel leaves no trace of completion.
    cbo.begin(provider="claude")
    cbo.cancel()
    assert cbo.status().state == "pending"
    assert not cbo.record_path().exists()
    assert not cbo.in_progress_path().exists()

    # Completion without a configured provider refuses and writes nothing.
    for name in ALL_ENV_KEYS:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(cbo.ProviderNotConfigured):
        cbo.complete()
    assert cbo.status().state == "pending"
    assert not cbo.record_path().exists()

    # No secret value anywhere under the runtime home after the whole flow.
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET_SENTINEL)
    cbo.begin(provider="claude")
    cbo.cancel()
    for artifact in homes["vc_home"].rglob("*"):
        if artifact.is_file():
            assert SECRET_SENTINEL not in artifact.read_text(
                encoding="utf-8", errors="replace"
            ), artifact


def test_incompatible_record_is_never_replaced(homes: dict[str, Path]) -> None:
    foreign = cbo.record_path()
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(
        '{"record_version": 999, "owner": "other-system", "provider": "claude"}\n'
    )
    before = foreign.read_bytes()

    assert cbo.status().state == "incompatible"
    decision = cbo.admit(cbo.SURFACE_HOME)
    assert not decision.allowed
    assert decision.reason == cbo.REASON_RECORD_INCOMPATIBLE

    _fixture_auth(homes["user_home"])
    with pytest.raises(cbo.OnboardingRecordIncompatible):
        cbo.complete("claude")
    # No second record, no silent overwrite of the foreign owner.
    assert foreign.read_bytes() == before
    assert list(foreign.parent.iterdir()) == [foreign]


def test_unknown_provider_and_surface_are_refused(homes: dict[str, Path]) -> None:
    with pytest.raises(cbo.UnknownProvider):
        cbo.probe_provider("gpt-mystery")
    with pytest.raises(cbo.UnknownProvider):
        cbo.begin(provider="gpt-mystery")
    with pytest.raises(ValueError, match="unknown surface"):
        cbo.admit("billing")
    assert not cbo.record_path().exists()
