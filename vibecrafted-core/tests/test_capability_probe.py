from __future__ import annotations

import dataclasses
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from vibecrafted_core.capabilities import ProbeResult as CliProbe
from vibecrafted_core.continuity import capabilities as continuity
from vibecrafted_core.runtime_paths import (
    agent_tool_search_path,
    is_operator_home_root,
    resolve_operator_launch_root,
    selected_runtime_environment,
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


REPO = Path(__file__).resolve().parents[2]
COMMON_SH = REPO / "vibecrafted-core/vibecrafted_core/runtime/scripts/lib/util.sh"
SHELL_FACADE = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"


def test_agent_tool_search_path_keeps_founder_entries_and_strips_owned_generation(
    tmp_path: Path,
) -> None:
    """Inherited order is authoritative; only owned generation bins are cut.

    The previous contract was a closed allowlist that discarded the inherited
    PATH and put the runtime bin first, so a detached provider process resolved
    a bundled — possibly stale — ``aicx``/``loct`` instead of the Founder's own.
    """

    home = tmp_path / "home"
    public_bin = home / ".local/bin"
    custom_bin = tmp_path / "custom-bin"
    stale_generation = home / ".local/share/vibecrafted/releases/4.3.0+gSTALE/bin"
    # Merely looks like a generation bin; owned by the operator, not by us.
    lookalike = home / "dev/vibecrafted/releases/1.0/bin"
    for directory in (public_bin, custom_bin, stale_generation, lookalike):
        directory.mkdir(parents=True)

    entries = agent_tool_search_path(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "PATH": os.pathsep.join(
                (
                    str(stale_generation),
                    str(custom_bin),
                    str(lookalike),
                    str(public_bin),
                    "/usr/bin",
                )
            ),
        }
    ).split(os.pathsep)

    assert entries[:4] == [
        str(custom_bin),
        str(lookalike),
        str(public_bin),
        "/usr/bin",
    ]
    assert str(stale_generation) not in entries
    assert len(entries) == len(set(entries))
    # Minimal launchd environments still reach the host CLIs, as a suffix.
    assert "/bin" in entries


def test_agent_tool_search_path_anchors_on_custom_runtime_home(
    tmp_path: Path,
) -> None:
    """A custom runtime home has no ``vibecrafted`` component to match on."""

    home = tmp_path / "home"
    runtime_home = tmp_path / "opt" / "vcrt"
    stale_generation = runtime_home / "releases/4.3.0+gSTALE/bin"
    public_bin = home / ".local/bin"
    for directory in (stale_generation, public_bin):
        directory.mkdir(parents=True)

    entries = agent_tool_search_path(
        {
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "PATH": os.pathsep.join(
                (str(stale_generation), str(public_bin), "/usr/bin")
            ),
        }
    ).split(os.pathsep)

    assert str(stale_generation) not in entries
    assert entries[:2] == [str(public_bin), "/usr/bin"]


def _shell_search_path(
    shell: str, source: Path, invocation: str, env: dict[str, str]
) -> str:
    script = f'source "{source}" >/dev/null 2>&1\n{invocation}\n'
    proc = subprocess.run(
        [shell, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO,
    )
    return proc.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize(
    "case",
    ("canonical_runtime_home", "custom_runtime_home", "selected_generation_root"),
)
def test_owned_generation_sanitation_agrees_across_bash_zsh_and_python(
    tmp_path: Path, case: str
) -> None:
    """One contract, three owners: core.sh, util.sh and runtime_paths.py.

    These three implement the same grammar in different runtimes (the shell
    facade is sourced under ``zsh -lic``), so a divergence would let a stale
    generation reach one execution lane while the others are clean.
    """

    home = tmp_path / "home"
    public_bin = home / ".local/bin"
    custom_bin = tmp_path / "custom-bin"
    lookalike = home / "dev/vibecrafted/releases/1.0/bin"
    canonical_home = home / ".local/share/vibecrafted"
    custom_home = tmp_path / "opt" / "vcrt"

    if case == "custom_runtime_home":
        runtime_home = custom_home
    else:
        runtime_home = canonical_home
    stale_generation = runtime_home / "releases/4.3.0+gSTALE/bin"
    selected_root = runtime_home / "releases/4.3.0+gSELECTED"

    for directory in (
        public_bin,
        custom_bin,
        lookalike,
        stale_generation,
        selected_root / "bin",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    env = {
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "PATH": os.pathsep.join(
            (
                str(stale_generation),
                str(selected_root / "bin"),
                str(custom_bin),
                str(lookalike),
                str(public_bin),
                "/usr/bin",
                "/bin",
            )
        ),
    }
    if case == "custom_runtime_home":
        env["VIBECRAFTED_RUNTIME_HOME"] = str(custom_home)
    if case == "selected_generation_root":
        env["VIBECRAFTED_RUNTIME_ROOT"] = str(selected_root)

    # ``selected_runtime_environment`` validates a selected root as a stamped
    # immutable generation, which a fixture directory is not; the sanitation
    # grammar is what is under test, so exercise it directly for that case.
    if case == "selected_generation_root":
        from vibecrafted_core.runtime_paths import _is_owned_generation_bin

        assert _is_owned_generation_bin(str(selected_root / "bin"), env) is True
        python_result = os.pathsep.join(
            entry
            for entry in env["PATH"].split(os.pathsep)
            if not _is_owned_generation_bin(entry, env)
        )
    else:
        python_result = agent_tool_search_path(env)

    bash_util = _shell_search_path(
        "bash",
        COMMON_SH,
        'spawn_prepend_agent_tool_paths; printf "%s\\n" "$PATH"',
        dict(env),
    )
    bash_core = _shell_search_path(
        "bash",
        SHELL_FACADE,
        f'_vetcoders_path_with_bundled_bin_priority "{env["PATH"]}"',
        dict(env),
    )
    zsh_core = _shell_search_path(
        "zsh",
        SHELL_FACADE,
        f'_vetcoders_path_with_bundled_bin_priority "{env["PATH"]}"',
        dict(env),
    )

    assert bash_util == bash_core
    assert bash_core == zsh_core
    assert str(stale_generation) not in zsh_core.split(os.pathsep)

    if case == "selected_generation_root":
        # Direct-grammar comparison: the shells append their discovery suffix.
        assert zsh_core.split(os.pathsep)[: len(python_result.split(os.pathsep))] == (
            python_result.split(os.pathsep)
        )
        assert str(selected_root / "bin") not in zsh_core.split(os.pathsep)
    else:
        assert python_result == zsh_core


def test_selected_generation_rebinds_stale_runtime_bin_and_python(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "releases" / "new"
    stale = tmp_path / "releases" / "old"
    selected_bin = selected / "bin"
    stale_bin = stale / "bin"
    for directory in (selected_bin, stale_bin):
        directory.mkdir(parents=True)
    (selected / "VERSION").write_text("4.3.0+g16425e69\n", encoding="utf-8")
    python = selected_bin / "python3"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)

    environment = selected_runtime_environment(
        {
            "VIBECRAFTED_RUNTIME_ROOT": str(selected),
            "VIBECRAFTED_RUNTIME_BIN": str(stale_bin),
            "VIBECRAFTED_PYTHON": str(stale_bin / "python3"),
            "VIBECRAFTED_ROOT": str(stale),
        }
    )

    assert environment["VIBECRAFTED_RUNTIME_ROOT"] == str(selected)
    assert environment["VIBECRAFTED_RUNTIME_BIN"] == str(selected_bin)
    assert environment["VIBECRAFTED_PYTHON"] == str(python)
    assert environment["VIBECRAFTED_ROOT"] == str(selected)


def test_selected_generation_fails_closed_when_identity_is_incomplete(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "releases" / "incomplete"
    selected.mkdir(parents=True)

    with pytest.raises(ValueError, match="immutable stamped generation"):
        selected_runtime_environment(
            {
                "VIBECRAFTED_RUNTIME_ROOT": str(selected),
                "VIBECRAFTED_RUNTIME_BIN": str(tmp_path / "stale-bin"),
            }
        )


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


def _pin_fake_cli_dir(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    """Make ``directory`` win provider discovery.

    Provider CLIs are host tools resolved from the Founder's own PATH; the
    generation bin is a private carrier that no longer participates in
    ambient lookup.  A fake CLI is therefore pinned by leading the inherited
    PATH rather than by pointing ``VIBECRAFTED_RUNTIME_BIN`` at it.
    """

    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(directory), os.environ.get("PATH", "")))
    )


def test_probe_confirms_fake_agy_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cli(
        tmp_path,
        "agy",
        "1.1.3",
        "--continue Continue\n--conversation Resume by ID\n--print Run once",
    )
    _pin_fake_cli_dir(monkeypatch, tmp_path)

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
    _pin_fake_cli_dir(monkeypatch, tmp_path)

    result = continuity.probe("junie")

    assert result.state == continuity.PROBE_CONFIRMED
    assert result.version == "Junie version: 26.7.13 (2285.4)"
    assert result.markers == {"--resume": True, "--session-id": True}


def test_probe_reports_unsupported_when_markers_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An older junie without the resume surface: runs fine, contract absent.
    _write_fake_cli(tmp_path, "junie", "Junie version: 25.1.0", "--task only")
    _pin_fake_cli_dir(monkeypatch, tmp_path)

    result = continuity.probe("junie")

    assert result.state == continuity.PROBE_UNSUPPORTED
    assert result.markers == {"--resume": False, "--session-id": False}
    assert "--resume" in result.detail


def test_probe_failed_is_not_unsupported_when_cli_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pin_fake_cli_dir(monkeypatch, tmp_path)
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
    _pin_fake_cli_dir(monkeypatch, tmp_path)

    result = continuity.probe("grok")

    assert result.state == continuity.PROBE_FAILED
    assert result.executable == str(broken)
    assert "boom" in result.detail


def test_probe_gemini_is_evidence_only_and_executes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pin_fake_cli_dir(monkeypatch, tmp_path)

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
    _pin_fake_cli_dir(monkeypatch, tmp_path)
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
