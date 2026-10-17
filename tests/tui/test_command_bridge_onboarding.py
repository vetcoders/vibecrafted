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
import tomllib
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


def test_monaspace_catalog_default_proposal_and_file_presence(tmp_path: Path) -> None:
    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)

    # 1. When no policy file exists and no font file is present, fallback is Spot Mono
    assert (
        cbo.proposed_font_family(policy_path=policy, font_dirs=[font_dir])
        == cbo.FALLBACK_FONT_FAMILY
    )

    # 2. Inspect variants when no font files exist
    variants = cbo.inspect_font_variants(policy_path=policy, font_dirs=[font_dir])
    # Active fallback family Spot Mono is included as the first entry, followed by the 5 Monaspace variants
    assert len(variants) == 6
    assert variants[0].family == cbo.FALLBACK_FONT_FAMILY
    assert "current" in variants[0].label
    monaspace_part = variants[1:]
    assert [v.family for v in monaspace_part] == list(cbo.MONASPACE_FAMILIES)
    assert all(not v.font_file_present for v in monaspace_part)
    assert all(v.status_label == "font file missing" for v in monaspace_part)
    assert all(not v.selectable for v in monaspace_part)

    # 3. Drop Xenon NF font file: now Xenon is proposed by default
    xenon_file = font_dir / "MonaspaceXenonNF-Regular.otf"
    xenon_file.write_bytes(b"\x00\x01\x00\x00")

    assert (
        cbo.proposed_font_family(policy_path=policy, font_dirs=[font_dir])
        == "Monaspace Xenon NF"
    )
    updated_variants = cbo.inspect_font_variants(
        policy_path=policy, font_dirs=[font_dir]
    )
    # Now that a Monaspace variant is active, variants has exactly the 5 Monaspace families
    assert len(updated_variants) == 5
    xenon_status = next(v for v in updated_variants if v.family == "Monaspace Xenon NF")
    assert xenon_status.font_file_present is True
    assert xenon_status.selectable is True
    assert xenon_status.font_file_path == str(xenon_file)
    assert "font file present" in xenon_status.status_label


def test_preserving_any_configured_font_family_as_current(tmp_path: Path) -> None:
    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)

    # Policy specifies a custom non-Monaspace family
    policy.write_text(
        '[font.normal]\nfamily = "JetBrains Mono"\nstyle = "Regular"\n',
        encoding="utf-8",
    )

    assert cbo.read_configured_font_family(policy) == "JetBrains Mono"
    # Even if Xenon font file is present, the existing family is preserved!
    xenon_file = font_dir / "MonaspaceXenonNF.ttf"
    xenon_file.write_bytes(b"\x00\x01\x00\x00")
    assert (
        cbo.proposed_font_family(policy_path=policy, font_dirs=[font_dir])
        == "JetBrains Mono"
    )

    variants = cbo.inspect_font_variants(policy_path=policy, font_dirs=[font_dir])
    # Custom family is inserted as first entry with (current)
    assert len(variants) == 6
    current = variants[0]
    assert current.family == "JetBrains Mono"
    assert current.preserved_custom is True
    assert current.active is True
    assert current.selectable is True
    assert "current" in current.label


def test_select_font_family_refuses_missing_file_without_modifying_policy(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)

    initial_content = '[font.normal]\nfamily = "Spot Mono"\n'
    policy.write_text(initial_content, encoding="utf-8")

    # Trying to select Monaspace Argon NF when its file is not in font_dir raises FontNotAvailableError
    with pytest.raises(cbo.FontNotAvailableError) as exc_info:
        cbo.select_font_family(
            "Monaspace Argon NF",
            policy_path=policy,
            font_dirs=[font_dir],
        )

    assert "Monaspace Argon NF" in str(exc_info.value)
    # Policy file is completely untouched
    assert policy.read_text(encoding="utf-8") == initial_content


def test_select_font_family_updates_only_font_families_without_live_config_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_execution_boundary(monkeypatch)

    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    neon_file = font_dir / "MonaspaceNeonNF-Regular.otf"
    neon_file.write_bytes(b"\x00\x01\x00\x00")

    policy.write_text(
        """# Custom terminal policy comments
[window]
opacity = 0.95

[font]
size = 14.0

[font.normal]
family = "Spot Mono"
style = "Regular"

[font.bold]
family = "Spot Mono"
style = "Bold"

[font.italic]
family = "Spot Mono"
style = "Italic"

[colors]
primary.background = "#1e1e1e"
""",
        encoding="utf-8",
    )

    cbo.select_font_family(
        "Monaspace Neon NF",
        policy_path=policy,
        font_dirs=[font_dir],
    )

    result = policy.read_text(encoding="utf-8")
    assert 'family = "Monaspace Neon NF"' in result
    assert "Spot Mono" not in result
    assert "opacity = 0.95" in result
    assert "size = 14.0" in result
    assert 'primary.background = "#1e1e1e"' in result
    assert "live_config_reload" not in result
    assert cbo.read_configured_font_family(policy) == "Monaspace Neon NF"


def test_select_font_family_preserves_existing_live_config_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_execution_boundary(monkeypatch)

    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    neon_file = font_dir / "MonaspaceNeonNF-Regular.otf"
    neon_file.write_bytes(b"\x00\x01\x00\x00")

    initial = (
        "live_config_reload = false\n\n"
        '[font.normal]\nfamily = "Spot Mono"\nstyle = "Regular"\n\n'
        '[font.bold]\nfamily = "Spot Mono"\nstyle = "Bold"\n\n'
        '[font.italic]\nfamily = "Spot Mono"\nstyle = "Italic"\n'
    )
    policy.write_text(initial, encoding="utf-8")

    cbo.select_font_family(
        "Monaspace Neon NF",
        policy_path=policy,
        font_dirs=[font_dir],
    )

    result = policy.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed.get("live_config_reload") is False
    assert parsed["font"]["normal"]["family"] == "Monaspace Neon NF"
    assert parsed["font"]["bold"]["family"] == "Monaspace Neon NF"
    assert parsed["font"]["italic"]["family"] == "Monaspace Neon NF"
    assert "live_config_reload = false" in result


def test_select_font_family_preserves_commented_table_headers_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_execution_boundary(monkeypatch)

    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    neon_file = font_dir / "MonaspaceNeonNF-Regular.otf"
    neon_file.write_bytes(b"\x00\x01\x00\x00")

    initial = (
        "[font.normal] # primary font section\n"
        'family = "Spot Mono"\n'
        'style = "Regular"\n\n'
        "[font.bold] # bold style\n"
        'family = "Spot Mono"\n'
        'style = "Bold"\n\n'
        "[font.italic] # italic style\n"
        'family = "Spot Mono"\n'
        'style = "Italic"\n'
    )
    policy.write_text(initial, encoding="utf-8")

    cbo.select_font_family(
        "Monaspace Neon NF",
        policy_path=policy,
        font_dirs=[font_dir],
    )

    result = policy.read_text(encoding="utf-8")
    parsed = tomllib.loads(result)
    assert parsed["font"]["normal"]["family"] == "Monaspace Neon NF"
    assert parsed["font"]["bold"]["family"] == "Monaspace Neon NF"
    assert parsed["font"]["italic"]["family"] == "Monaspace Neon NF"
    assert "# primary font section" in result
    assert "# bold style" in result
    assert "# italic style" in result
    assert result.count("[font.normal]") == 1
    assert result.count("[font.bold]") == 1
    assert result.count("[font.italic]") == 1


def test_select_font_family_refuses_malformed_input_toml_without_modifying_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_execution_boundary(monkeypatch)

    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    neon_file = font_dir / "MonaspaceNeonNF-Regular.otf"
    neon_file.write_bytes(b"\x00\x01\x00\x00")

    malformed = '[font.normal]\nfamily = "Spot Mono\ninvalid toml syntax [[['
    policy.write_text(malformed, encoding="utf-8")

    with pytest.raises(ValueError, match="terminal-policy.toml is malformed"):
        cbo.select_font_family(
            "Monaspace Neon NF",
            policy_path=policy,
            font_dirs=[font_dir],
        )

    assert policy.read_text(encoding="utf-8") == malformed
