from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence
from pathlib import Path

import pytest
from vibecrafted_core.capabilities import ProbeResult as CliProbe
from vibecrafted_core.continuity import capabilities as continuity
from vibecrafted_core.runtime_paths import (
    agent_tool_search_path,
    is_operator_home_root,
    resolve_operator_launch_root,
)

ALL_AGENTS = ("claude", "codex", "gemini", "agy", "junie", "grok", "cursor")
VERDICTS = {
    continuity.SUPPORTED,
    continuity.UNSUPPORTED,
    continuity.UNVERIFIED,
}
FORK_VERDICTS = VERDICTS | {continuity.TERMINAL_ONLY}


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    continuity.clear_probe_cache()
    yield
    continuity.clear_probe_cache()


# ---------------------------------------------------------------- schema (§7)


def test_table_declares_all_agents_with_every_spec7_field() -> None:
    assert set(continuity.CAPABILITIES) == set(ALL_AGENTS)
    for agent in ALL_AGENTS:
        cap = continuity.capability_for(agent)
        assert cap.agent == agent
        for item in dataclasses.fields(continuity.ProviderCapability):
            assert hasattr(cap, item.name)
        # Free-text spec fields must never be silently empty.
        assert cap.session_id_shape
        assert cap.session_id_sources
        assert cap.prompt_transport in {"stdin", "file", "flag_value", "none"}
        assert cap.session_identity_event
        assert cap.cwd_safety
        assert cap.interactive_resume in VERDICTS
        assert cap.noninteractive_resume in VERDICTS
        assert cap.native_fork in FORK_VERDICTS
        assert cap.execution in {continuity.EXECUTABLE, continuity.EVIDENCE_ONLY}


def test_gemini_is_evidence_only_and_never_probeable() -> None:
    cap = continuity.capability_for("gemini")
    assert cap.execution == continuity.EVIDENCE_ONLY
    assert cap.probe_recipe is None
    assert cap.interactive_resume == continuity.UNSUPPORTED
    assert cap.noninteractive_resume == continuity.UNSUPPORTED
    assert cap.native_fork == continuity.UNSUPPORTED
    assert cap.prompt_transport == "none"


def test_grok_forbids_checkout_mutating_recovery_flags() -> None:
    cap = continuity.capability_for("grok")
    assert "--restore-code" in cap.forbidden_flags
    assert "--worktree" in cap.forbidden_flags


def test_unverified_never_upgraded_optimistically() -> None:
    # AICX/host evidence shows a resume *surface* for agy and junie, but the
    # headless contract is unproven (F06) — the table must say so.
    assert (
        continuity.capability_for("agy").noninteractive_resume == continuity.UNVERIFIED
    )
    assert (
        continuity.capability_for("junie").noninteractive_resume
        == continuity.UNVERIFIED
    )
    assert (
        continuity.capability_for("cursor").noninteractive_resume
        == continuity.UNVERIFIED
    )


def test_capability_registry_is_serializable_and_versioned() -> None:
    payload = continuity.capability_registry()
    assert payload["schema"] == "vibecrafted.continuity.capabilities.v1"
    assert set(payload["agents"]) == set(ALL_AGENTS)
    grok = payload["agents"]["grok"]
    assert grok["forbidden_flags"] == ["--restore-code", "--worktree"]
    assert grok["probe_recipe"]["cli"] == "grok"


def test_unknown_agent_rejected() -> None:
    with pytest.raises(ValueError, match="unknown agent"):
        continuity.capability_for("copilot")


def test_package_root_exports_resolve_lazily() -> None:
    import vibecrafted_core

    assert vibecrafted_core.ProviderCapability is continuity.ProviderCapability
    assert vibecrafted_core.probe_provider is continuity.probe
    assert vibecrafted_core.capability_registry is continuity.capability_registry


# ------------------------------------------------------- probe with fake CLIs


def _write_fake_cli(directory: Path, name: str, version: str, help_text: str) -> Path:
    # Builtins only (printf): the probe tests pin $PATH to the fake dir, so
    # external tools like cat/sed would not resolve inside the script.
    help_lines = "".join(
        f"  --help) printf '%s\\n' '{line}'" if index == 0 else f" '{line}'"
        for index, line in enumerate(help_text.splitlines())
    )
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf '%s\\n' '{version}' ;;\n"
        f"{help_lines} ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_agent_tool_search_path_matches_detached_allowlist(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime_bin = tmp_path / "runtime-bin"
    rogue_bin = tmp_path / "rogue-bin"
    for directory in (home / ".local/bin", home / ".cargo/bin", runtime_bin, rogue_bin):
        directory.mkdir(parents=True)

    entries = agent_tool_search_path(
        {
            "HOME": str(home),
            "PATH": str(rogue_bin),
            "VIBECRAFTED_RUNTIME_BIN": str(runtime_bin),
        }
    ).split(os.pathsep)

    assert entries[:3] == [
        str(runtime_bin),
        str(home / ".local/bin"),
        str(home / ".cargo/bin"),
    ]
    assert str(rogue_bin) not in entries
    assert len(entries) == len(set(entries))


def test_resolve_operator_launch_root_uses_workspace_from_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "screenscribe"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    resolved = resolve_operator_launch_root(
        cwd=home,
        env={"HOME": str(home), "VIBECRAFTED_WORKSPACE_ROOT": str(workspace)},
    )
    assert resolved == workspace.resolve()


def test_resolve_operator_launch_root_keeps_an_explicit_git_tree(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "other"
    workspace = tmp_path / "selected"
    repo.mkdir()
    (repo / ".git").mkdir()
    workspace.mkdir()
    resolved = resolve_operator_launch_root(
        cwd=repo,
        env={"VIBECRAFTED_WORKSPACE_ROOT": str(workspace)},
    )
    assert resolved == repo.resolve()


def test_is_operator_home_root_matches_only_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    other = tmp_path / "repo"
    home.mkdir()
    other.mkdir()
    env = {"HOME": str(home)}
    assert is_operator_home_root(home, env=env)
    assert not is_operator_home_root(other, env=env)


def test_probe_confirms_fake_agy_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cli(
        tmp_path,
        "agy",
        "1.1.3",
        "--continue Continue\n--conversation Resume by ID\n--print Run once",
    )
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))

    result = continuity.probe("agy")

    assert result.state == continuity.PROBE_CONFIRMED
    assert result.version == "1.1.3"
    assert result.executable == str(tmp_path / "agy")
    assert all(result.markers.values())


def test_probe_confirms_fake_junie_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cli(
        tmp_path,
        "junie",
        "Junie version: 26.7.13 (2285.4)",
        "--resume Resume the last session\n--session-id=<text> Session id",
    )
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))

    result = continuity.probe("junie")

    assert result.state == continuity.PROBE_CONFIRMED
    assert result.version == "Junie version: 26.7.13 (2285.4)"
    assert result.markers == {"--resume": True, "--session-id": True}


def test_probe_reports_unsupported_when_markers_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older junie without the resume surface: runs fine, contract absent.
    _write_fake_cli(tmp_path, "junie", "Junie version: 25.1.0", "--task only")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))

    result = continuity.probe("junie")

    assert result.state == continuity.PROBE_UNSUPPORTED
    assert result.markers == {"--resume": False, "--session-id": False}
    assert "--resume" in result.detail


def test_probe_failed_is_not_unsupported_when_cli_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))
    monkeypatch.setattr(continuity, "agent_tool_search_path", lambda: str(tmp_path))

    result = continuity.probe("agy")

    assert result.state == continuity.PROBE_FAILED
    assert result.state != continuity.PROBE_UNSUPPORTED
    assert "NOT proof" in result.detail


def test_probe_failed_when_cli_breaks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = tmp_path / "grok"
    broken.write_text("#!/bin/sh\necho boom >&2\nexit 1\n", encoding="utf-8")
    broken.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))

    result = continuity.probe("grok")

    assert result.state == continuity.PROBE_FAILED
    assert result.executable == str(broken)
    assert "boom" in result.detail


def test_probe_gemini_is_evidence_only_and_executes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))

    def _never(cmd: Sequence[str]) -> CliProbe:  # pragma: no cover - guard
        pytest.fail(f"gemini probe must never execute, got {cmd}")

    result = continuity.probe("gemini", runner=_never)

    assert result.state == continuity.PROBE_EVIDENCE_ONLY
    assert result.executable is None


def test_probe_caches_until_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cli(
        tmp_path,
        "agy",
        "1.1.3",
        "--continue x\n--conversation y\n--print z",
    )
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(tmp_path))
    calls: list[Sequence[str]] = []
    real_runner = continuity._default_runner(5.0)

    def counting(cmd: Sequence[str]) -> CliProbe:
        calls.append(cmd)
        return real_runner(cmd)

    first = continuity.probe("agy", runner=counting)
    assert len(calls) == 2  # --version + --help
    second = continuity.probe("agy", runner=counting)
    assert second is first
    assert len(calls) == 2  # cached: no new executions

    third = continuity.probe("agy", runner=counting, refresh=True)
    assert len(calls) == 4
    assert third.state == continuity.PROBE_CONFIRMED


# ------------------------------------------- opt-in real host CLI integration


@pytest.mark.skipif(
    not os.environ.get("VIBECRAFTED_PROBE_INTEGRATION"),
    reason="set VIBECRAFTED_PROBE_INTEGRATION=1 to probe the real host CLIs",
)
@pytest.mark.parametrize("agent", ["agy", "junie", "grok", "claude", "codex"])
def test_probe_real_host_cli(agent: str) -> None:
    result = continuity.probe(agent, refresh=True)

    # On a host without the CLI this is probe_failed by design; when the CLI
    # is installed the declared contract must be decidable.
    assert result.state in {
        continuity.PROBE_CONFIRMED,
        continuity.PROBE_UNSUPPORTED,
        continuity.PROBE_FAILED,
    }
    if result.state != continuity.PROBE_FAILED:
        assert result.version
        assert result.markers
