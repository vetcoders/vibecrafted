"""Public ``--permissions`` / ``--sandbox`` contract through the real CLI subprocess.

Every test runs ``python -m vibecrafted_core.cli`` from a temporary directory
that is NOT inside Git, with an isolated ``VIBECRAFTED_HOME``, no inherited
``PYTHONPATH`` / ``PYTHONHOME``, and provider binaries stubbed on PATH. The
stubs only record their argv; no real agent workload, session, global config,
MCP server or Python install is ever touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import workflow
from vibecrafted_core.continuity.capabilities import ProbeResult
from vibecrafted_core.execution_controls import resolve_execution_controls

CORE_ROOT = Path(__file__).resolve().parents[1]
# Claude Code's documented hard gate (see execution_controls.SANDBOX_EVIDENCE).
CLAUDE_SANDBOX_ON = (
    '{"sandbox":{"enabled":true,"failIfUnavailable":true,'
    '"allowUnsandboxedCommands":false}}'
)
CLAUDE_SANDBOX_ON_SETTINGS = {
    "sandbox": {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
    }
}
FOUNDER_PROMPT = "Ujednolić i ustandaryzować polecenie fork — bez zmian bajtów"


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agents@vetcoders.io"], cwd=path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "contract-test"], cwd=path, check=True
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/vetcoders/fixture.git"],
        cwd=path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _fake_provider(bin_dir: Path, name: str, argv_file: Path) -> None:
    """A provider stub that records argv + stdin bytes and exits 0."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" > {json.dumps(str(argv_file))}',
                f"cat > {json.dumps(str(argv_file) + '.stdin')}",
                'printf \'{"type":"assistant","message":"ok"}\\n\'',
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_FRAME", "ZELLIJ"))
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env["HOME"] = str(home)
    # The core conftest pinned an isolated VIBECRAFTED_HOME outside tmp_path.
    env["VIBECRAFTED_HOME"] = os.environ["VIBECRAFTED_HOME"]
    env["VIBECRAFTED_GUARD"] = "0"
    env["PYTHONPATH"] = str(CORE_ROOT)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def _cli(
    args: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "vibecrafted_core.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _await_argv(argv_file: Path, *, timeout: float = 20.0) -> list[str]:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if argv_file.exists() and argv_file.with_suffix(".txt.stdin").exists():
            return argv_file.read_text(encoding="utf-8").splitlines()
        time.sleep(0.1)
    raise AssertionError(f"provider stub never ran: {argv_file}")


def _runtime_runs(env: dict[str, str]) -> Path:
    return Path(env["VIBECRAFTED_HOME"]) / "control_plane" / "runtime_runs"


class _World:
    def __init__(self, tmp_path: Path, provider: str = "claude") -> None:
        self.repo = tmp_path / "repo with space"
        self.baseline = _git_repo(self.repo)
        self.outside = tmp_path / "no git here"
        self.outside.mkdir()
        self.bin = tmp_path / "bin"
        self.argv_file = tmp_path / f"{provider}-argv.txt"
        _fake_provider(self.bin, provider, self.argv_file)
        self.env = _env(tmp_path, self.bin)

    def launch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return _cli([*args, "--json"], cwd=self.outside, env=self.env)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_founder_command_enforces_auto_permissions_and_sandbox(world: _World) -> None:
    """The exact public command, end to end, with the provider stubbed."""
    result = world.launch(
        "workflow",
        "claude",
        "--model",
        "claude-fable-5-1",
        "--worktree",
        "true",
        "--permissions",
        "auto",
        "--sandbox",
        "true",
        "--repo",
        str(world.repo),
        "--prompt",
        FOUNDER_PROMPT,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    # --repo from outside Git and --worktree true still work together.
    assert receipt["worktree"] is True
    assert receipt["parent_root"] == str(world.repo.resolve())
    assert receipt["worktree_baseline_sha"] == world.baseline
    assert Path(receipt["root"]) == Path(receipt["worktree_path"])
    # Requested and effective values are both in the machine receipt.
    controls = receipt["execution_controls"]
    assert controls["schema"] == "vibecrafted.execution_controls.v1"
    assert controls["permissions_requested"] == "auto"
    assert controls["permissions_effective"] == "auto"
    assert controls["sandbox_requested"] == "true"
    assert controls["sandbox_effective"] == "enabled"
    assert controls["provider_flags"] == [
        "--permission-mode",
        "auto",
        "--settings",
        CLAUDE_SANDBOX_ON,
    ]
    assert "2.1.263" in controls["evidence"]
    # The receipt names the real boundary (Claude's Bash-tool sandbox), the
    # inherited hole it cannot close (excludedCommands) and that enforcement is
    # configured, not observed.
    assert "Bash-tool sandbox" in controls["boundary"]
    assert "MCP servers run outside" in controls["boundary"]
    assert "excludedCommands" in controls["behavior"]
    assert "not observed" in controls["behavior"]
    # The worker argv the dispatcher was handed.
    worker = receipt["worker_command"]
    assert worker[1:3] == ["--model", "claude-fable-5-1"]
    assert worker[-4:] == ["--permission-mode", "auto", "--settings", CLAUDE_SANDBOX_ON]
    # The exact settings document Claude receives: the no-fallback hard gate.
    assert json.loads(worker[-1]) == CLAUDE_SANDBOX_ON_SETTINGS
    assert "bypassPermissions" not in worker

    # The provider stub actually received that argv, in the worktree, with the
    # prompt bytes on stdin untouched.
    argv = _await_argv(world.argv_file)
    assert argv[:2] == ["--model", "claude-fable-5-1"]
    assert argv[-4:] == ["--permission-mode", "auto", "--settings", CLAUDE_SANDBOX_ON]
    stdin = world.argv_file.with_suffix(".txt.stdin").read_text(encoding="utf-8")
    assert FOUNDER_PROMPT in stdin
    # Run metadata carries the same receipt.
    meta = json.loads(
        (_runtime_runs(world.env) / receipt["run_id"] / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["execution_controls"]["permissions_effective"] == "auto"
    assert meta["execution_controls"]["sandbox_effective"] == "enabled"
    assert meta["model_requested"] == "claude-fable-5-1"


def test_omitting_the_flags_resolves_the_auto_default(world: _World) -> None:
    """Fleet doctrine (Founder 13716da4a2b8af7049ea1565): no flags = auto."""
    result = world.launch(
        "workflow", "claude", "--repo", str(world.repo), "--prompt", "default run"
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert "worktree" not in receipt
    worker = receipt["worker_command"]
    assert worker[-2:] == ["--permission-mode", "auto"]
    assert "--settings" not in worker
    controls = receipt["execution_controls"]
    assert controls["permissions_requested"] == ""
    assert controls["permissions_effective"] == "auto"
    assert controls["permissions_source"] == "default"
    assert controls["sandbox_requested"] == ""
    assert controls["sandbox_effective"] == "provider-default"
    argv = _await_argv(world.argv_file)
    assert argv[-2:] == ["--permission-mode", "auto"]
    assert "--settings" not in argv


def test_sandbox_false_is_an_explicit_setting(world: _World) -> None:
    result = world.launch(
        "workflow",
        "claude",
        "--sandbox",
        "false",
        "--repo",
        str(world.repo),
        "--prompt",
        "no sandbox",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["execution_controls"]["sandbox_effective"] == "disabled"
    assert receipt["worker_command"][-2:] == [
        "--settings",
        '{"sandbox":{"enabled":false}}',
    ]
    argv = _await_argv(world.argv_file)
    assert argv[-1] == '{"sandbox":{"enabled":false}}'
    # Off touches one scalar key; none of the hard-gate keys leak into it.
    assert json.loads(argv[-1]) == {"sandbox": {"enabled": False}}


def test_bare_sandbox_flag_means_true(world: _World) -> None:
    result = world.launch(
        "workflow", "claude", "--sandbox", "--repo", str(world.repo), "-p", "bare"
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["execution_controls"]["sandbox_requested"] == "true"
    assert receipt["worker_command"][-1] == CLAUDE_SANDBOX_ON


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        (
            ["--permissions", "yolo"],
            "--permissions expects bypass|auto|accept-edits|read-only",
        ),
        (["--permissions", "bypassPermissions"], "--permissions expects"),
        (["--sandbox", "maybe"], "--sandbox expects true or false"),
    ],
)
def test_invalid_values_are_refused_before_any_control_plane_write(
    world: _World, args: list[str], fragment: str
) -> None:
    runs_before = (
        sorted(_runtime_runs(world.env).glob("*"))
        if _runtime_runs(world.env).exists()
        else []
    )
    result = world.launch(
        "workflow", "claude", *args, "--repo", str(world.repo), "-p", "x"
    )
    assert result.returncode == 2
    assert fragment in result.stderr
    assert result.stdout.strip() == ""
    runs_after = (
        sorted(_runtime_runs(world.env).glob("*"))
        if _runtime_runs(world.env).exists()
        else []
    )
    assert runs_after == runs_before
    assert not world.argv_file.exists()


@pytest.mark.parametrize(
    ("provider", "args", "fragment"),
    [
        ("junie", ["--sandbox", "true"], "junie 26.8.31 exposes no sandbox control"),
        ("junie", ["--permissions", "read-only"], "supported: auto"),
        (
            "codex",
            ["--permissions", "auto", "--sandbox", "false"],
            "--permissions bypass --sandbox false",
        ),
        (
            "codex",
            ["--permissions", "read-only", "--sandbox", "false"],
            "Omit --sandbox or pass --sandbox true",
        ),
        (
            "codex",
            ["--permissions", "accept-edits"],
            "supported: bypass, auto, read-only",
        ),
        (
            "cursor",
            ["--permissions", "accept-edits"],
            "supported: bypass, auto, read-only",
        ),
        (
            "agy",
            ["--sandbox", "false"],
            "Omit --sandbox to keep agy's own setting",
        ),
        (
            "agy",
            ["--permissions", "auto", "--sandbox", "off"],
            "persistent terminal-sandbox settings",
        ),
    ],
)
def test_unenforceable_combinations_are_refused_with_the_alternative(
    tmp_path: Path, provider: str, args: list[str], fragment: str
) -> None:
    world = _World(tmp_path, provider=provider)
    result = world.launch(
        "workflow", provider, *args, "--repo", str(world.repo), "-p", "x"
    )
    assert result.returncode == 2, result.stdout
    assert f"error: {provider}:" in result.stderr
    assert fragment in result.stderr
    assert result.stdout.strip() == ""
    assert not world.argv_file.exists()
    assert not (_runtime_runs(world.env)).exists() or not any(
        _runtime_runs(world.env).iterdir()
    )


def test_agy_sandbox_false_is_refused_before_the_provider_launches(
    tmp_path: Path,
) -> None:
    """Explicit false fails closed during control resolution, before launch."""
    world = _World(tmp_path, provider="agy")
    runs = _runtime_runs(world.env)

    refused = world.launch(
        "workflow", "agy", "--sandbox", "false", "--repo", str(world.repo), "-p", "x"
    )
    assert refused.returncode == 2, refused.stdout
    assert "error: agy: agy 1.1.27 exposes only the opt-in --sandbox" in refused.stderr
    assert "Omit --sandbox to keep agy's own setting" in refused.stderr
    assert "disabled" not in refused.stdout
    assert refused.stdout.strip() == ""
    assert not world.argv_file.exists()
    assert not runs.exists() or not any(runs.iterdir())


def test_omp_workflow_launch_records_the_resolved_argv(tmp_path: Path) -> None:
    """omp worker end to end through the typed lane, provider stubbed.

    The default launch resolves the fleet auto doctrine onto omp's native
    write tier; argv carries flags only, the prompt rides stdin.
    """
    world = _World(tmp_path, provider="omp")
    result = world.launch("workflow", "omp", "--repo", str(world.repo), "-p", "x")
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert receipt["worker_command"][1:] == [
        "-p",
        "--mode",
        "json",
        "--approval-mode",
        "write",
    ]
    controls = receipt["execution_controls"]
    assert controls["permissions_effective"] == "auto"
    assert controls["permissions_source"] == "default"
    argv = _await_argv(world.argv_file)
    assert argv == ["-p", "--mode", "json", "--approval-mode", "write"]


def test_codex_auto_uses_the_exec_surface(tmp_path: Path) -> None:
    world = _World(tmp_path, provider="codex")
    result = world.launch(
        "workflow",
        "codex",
        "--permissions",
        "auto",
        "--repo",
        str(world.repo),
        "-p",
        "review it",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["worker_command"] == [
        str(world.bin / "codex"),
        "exec",
        "--json",
        "--approve-for-me",
        "-",
    ]
    assert receipt["execution_controls"]["sandbox_effective"] == "enabled"
    argv = _await_argv(world.argv_file)
    assert argv == ["exec", "--json", "--approve-for-me", "-"]


def test_codex_bypass_with_sandbox_true_keeps_the_sandbox(tmp_path: Path) -> None:
    world = _World(tmp_path, provider="codex")
    # bypass is an explicit opt-in (doctrine 13716da4a2b8af7049ea1565).
    result = world.launch(
        "workflow",
        "codex",
        "--permissions",
        "bypass",
        "--sandbox",
        "true",
        "--repo",
        str(world.repo),
        "-p",
        "x",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["worker_command"][1:] == [
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        "-",
    ]
    assert "--dangerously-bypass-approvals-and-sandbox" not in receipt["worker_command"]


def test_grok_sandbox_true_maps_to_the_workspace_profile(tmp_path: Path) -> None:
    world = _World(tmp_path, provider="grok")
    result = world.launch(
        "workflow",
        "grok",
        "--permissions",
        "auto",
        "--sandbox",
        "true",
        "--repo",
        str(world.repo),
        "-p",
        "x",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    worker = receipt["worker_command"]
    assert worker[3:7] == ["--permission-mode", "auto", "--sandbox", "workspace"]
    assert "bypassPermissions" not in worker
    argv = _await_argv(world.argv_file)
    assert argv[2:6] == ["--permission-mode", "auto", "--sandbox", "workspace"]


def test_supervised_runtimes_refuse_controls_instead_of_ignoring_them(
    world: _World,
) -> None:
    result = world.launch(
        "marbles",
        "claude",
        "--permissions",
        "auto",
        "--repo",
        str(world.repo),
        "-p",
        "x",
    )
    assert result.returncode == 2
    assert "not carried into the marbles supervised runtime" in result.stderr


def test_help_surface_documents_the_controls(world: _World) -> None:
    result = _cli(["help", "workflow"], cwd=world.outside, env=world.env)
    assert result.returncode == 0, result.stderr
    assert "--permissions <policy>" in result.stdout
    # Auto-first: the help never advertises bypass as the default.
    assert "default: per-agent/global config, else auto" in result.stdout
    assert "default: bypass" not in result.stdout
    assert "--sandbox [true|false]" in result.stdout
    assert "--permissions auto --sandbox true" in result.stdout
    argparse_help = _cli(["workflow", "--help"], cwd=world.outside, env=world.env)
    assert argparse_help.returncode == 0
    assert "--permissions" in argparse_help.stdout
    assert "else auto" in " ".join(argparse_help.stdout.split())
    assert "default: bypass" not in argparse_help.stdout
    assert "--sandbox" in argparse_help.stdout


def test_human_receipt_prints_requested_and_effective(world: _World) -> None:
    result = _cli(
        [
            "workflow",
            "claude",
            "--permissions",
            "read-only",
            "--repo",
            str(world.repo),
            "-p",
            "x",
        ],
        cwd=world.outside,
        env=world.env,
    )
    assert result.returncode == 0, result.stderr
    assert "permissions: read-only  (requested: read-only)" in result.stdout
    assert "sandbox:    provider-default  (requested: (default))" in result.stdout


def test_native_resume_keeps_admitted_policy_in_provider_argv_and_receipt(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing durable config cannot escalate a resumed read-only session."""
    native_id = "admitted-native-session"
    source = _runtime_runs(world.env) / "work-admitted" / "meta.json"
    source.parent.mkdir(parents=True)
    admitted = resolve_execution_controls("claude", permissions="read-only")
    source.write_text(
        json.dumps(
            {
                "run_id": "work-admitted",
                "agent": "claude",
                "agent_session_id": native_id,
                "root": str(world.repo),
                "execution_controls": admitted.receipt(),
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('permissions = "bypass"\n', encoding="utf-8")
    for key in ("VIBECRAFTED_HOME", "PATH", "HOME"):
        monkeypatch.setenv(key, world.env[key])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent.parent))
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: ProbeResult(
            agent=agent,
            state="confirmed",
            executable=str(world.bin / "claude"),
            version="fixture",
        ),
    )
    result = workflow.manual_resume_session(
        "claude",
        native_id,
        CORE_ROOT,
        prompt="continue without mutations",
        root=world.repo,
    )
    assert result["accepted"] is True, result
    argv = _await_argv(world.argv_file)
    assert argv[argv.index("--resume") + 1] == native_id
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in argv
    receipt = json.loads(
        (_runtime_runs(world.env) / result["run_id"] / "meta.json").read_text()
    )["execution_controls"]
    assert receipt == admitted.receipt()


def _seed_admitted_session(
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_id: str,
    run_id: str,
    admitted_permissions: str,
    config_permissions: str = "",
) -> dict[str, object]:
    """One prior run owning ``native_id`` plus an in-process probe of the stub."""
    admitted = resolve_execution_controls("claude", permissions=admitted_permissions)
    source = _runtime_runs(world.env) / run_id / "meta.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "agent": "claude",
                "agent_session_id": native_id,
                "root": str(world.repo),
                "execution_controls": admitted.receipt(),
            }
        ),
        encoding="utf-8",
    )
    config_home = tmp_path / "xdg"
    if config_permissions:
        config = config_home / "vibecrafted" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(f'permissions = "{config_permissions}"\n', encoding="utf-8")
    for key in ("VIBECRAFTED_HOME", "PATH", "HOME"):
        monkeypatch.setenv(key, world.env[key])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: ProbeResult(
            agent=agent,
            state="confirmed",
            executable=str(world.bin / agent),
            version="fixture",
        ),
    )
    return admitted.receipt()


def _child_meta(world: _World, run_id: str) -> dict[str, object]:
    return json.loads((_runtime_runs(world.env) / run_id / "meta.json").read_text())


@pytest.mark.parametrize(
    ("verb", "identity"),
    [("implement", "--session"), ("resume-session", "--agent-session-id")],
)
def test_review_session_continued_as_bypass_implement_honours_current_choice(
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verb: str,
    identity: str,
) -> None:
    """`implement claude --session N --permissions bypass` after a read-only review."""
    from vibecrafted_core import cli

    native_id = "review-native-session"
    _seed_admitted_session(
        world,
        tmp_path,
        monkeypatch,
        native_id=native_id,
        run_id="revi-admitted",
        admitted_permissions="read-only",
    )
    rc = cli.main(
        [
            verb,
            "claude",
            identity,
            native_id,
            "--permissions",
            "bypass",
            "--prompt",
            "now implement the reviewed cut",
            "--root",
            str(world.repo),
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert rc == 0, result
    argv = _await_argv(world.argv_file)
    expected = resolve_execution_controls("claude", permissions="bypass")
    assert argv[argv.index("--resume") + 1] == native_id
    assert "plan" not in argv
    for flag in expected.provider_flags:
        assert flag in argv
    meta = _child_meta(world, result["run_id"])
    controls = meta["execution_controls"]
    assert controls["permissions_requested"] == "bypass"
    assert controls["permissions_effective"] == "bypass"
    assert controls["permissions_source"] == "dispatch"
    assert controls["provider_flags"] == list(expected.provider_flags)
    # Identity stays pinned to the continued provider session and checkout.
    assert meta["agent"] == "claude"
    assert meta["agent_session_id"] == native_id
    assert meta["resume_mode"] == "manual_explicit"
    # The detached launcher keeps rewriting meta; the synchronous receipt owns root.
    assert Path(str(result["root"])).resolve() == world.repo.resolve()


def test_prior_bypass_session_continued_read_only_drops_to_plan(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_id = "bypass-native-session"
    _seed_admitted_session(
        world,
        tmp_path,
        monkeypatch,
        native_id=native_id,
        run_id="impl-admitted",
        admitted_permissions="bypass",
    )
    result = workflow.manual_resume_session(
        "claude",
        native_id,
        CORE_ROOT,
        prompt="review only",
        root=world.repo,
        permissions="read-only",
    )
    assert result["accepted"] is True, result
    argv = _await_argv(world.argv_file)
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert "--dangerously-skip-permissions" not in argv
    assert "bypassPermissions" not in argv
    controls = _child_meta(world, result["run_id"])["execution_controls"]
    assert controls["permissions_effective"] == "read-only"
    assert controls["permissions_source"] == "dispatch"


def test_omitted_override_inherits_admitted_bypass_not_live_config(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_id = "inherit-native-session"
    admitted = _seed_admitted_session(
        world,
        tmp_path,
        monkeypatch,
        native_id=native_id,
        run_id="impl-inherit",
        admitted_permissions="bypass",
        config_permissions="read-only",
    )
    result = workflow.manual_resume_session(
        "claude", native_id, CORE_ROOT, prompt="keep going", root=world.repo
    )
    assert result["accepted"] is True, result
    _await_argv(world.argv_file)
    assert _child_meta(world, result["run_id"])["execution_controls"] == admitted


def test_explicit_sandbox_override_is_honoured_and_keeps_admitted_permissions(
    world: _World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_id = "sandbox-native-session"
    admitted = _seed_admitted_session(
        world,
        tmp_path,
        monkeypatch,
        native_id=native_id,
        run_id="revi-sandbox",
        admitted_permissions="read-only",
    )
    result = workflow.manual_resume_session(
        "claude",
        native_id,
        CORE_ROOT,
        prompt="sandboxed review",
        root=world.repo,
        sandbox="true",
    )
    assert result["accepted"] is True, result
    argv = _await_argv(world.argv_file)
    assert argv[argv.index("--permission-mode") + 1] == "plan"
    assert json.loads(argv[argv.index("--settings") + 1]) == CLAUDE_SANDBOX_ON_SETTINGS
    controls = _child_meta(world, result["run_id"])["execution_controls"]
    assert controls["sandbox_requested"] == "true"
    assert controls["permissions_effective"] == "read-only"
    assert controls["permissions_requested"] == admitted["permissions_requested"]
    assert controls["permissions_source"] == admitted["permissions_source"]


@pytest.mark.parametrize(
    ("permissions", "sandbox"), [("everything", None), ("", "maybe")]
)
def test_malformed_session_override_refuses_before_any_run_is_reserved(
    world: _World,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    permissions: str,
    sandbox: str | None,
) -> None:
    native_id = "malformed-native-session"
    _seed_admitted_session(
        world,
        tmp_path,
        monkeypatch,
        native_id=native_id,
        run_id="revi-malformed",
        admitted_permissions="read-only",
    )
    before = sorted(_runtime_runs(world.env).iterdir())
    result = workflow.manual_resume_session(
        "claude",
        native_id,
        CORE_ROOT,
        prompt="x",
        root=world.repo,
        permissions=permissions,
        sandbox=sandbox,
    )
    assert result["accepted"] is False
    assert result["reason"] == "launch_spec_invalid"
    assert sorted(_runtime_runs(world.env).iterdir()) == before
    assert not world.argv_file.exists()


def test_unsupported_session_override_fails_closed_with_policy_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex cannot enforce accept-edits: refused, never silently downgraded."""
    codex = _World(tmp_path, provider="codex")
    native_id = "codex-native-thread"
    admitted = resolve_execution_controls("codex", permissions="read-only")
    for key in ("VIBECRAFTED_HOME", "PATH", "HOME"):
        monkeypatch.setenv(key, codex.env[key])
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: ProbeResult(
            agent=agent,
            state="confirmed",
            executable=str(codex.bin / agent),
            version="fixture",
        ),
    )
    with pytest.raises(workflow._NativeResumeCommandRejected) as refused:
        workflow._verified_native_resume_command(
            "codex",
            native_id,
            parent_meta={"execution_controls": admitted.receipt()},
            permissions="accept-edits",
        )
    assert refused.value.reason == "native_resume_policy_unavailable"
    assert "accept-edits" in refused.value.detail


def test_tracked_continuation_without_override_reuses_frozen_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Automatic/tracked resume passes no override: the frozen receipt rides."""
    world = _World(tmp_path)
    admitted = resolve_execution_controls("claude", permissions="read-only")
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('permissions = "bypass"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent.parent))
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: ProbeResult(
            agent=agent,
            state="confirmed",
            executable=str(world.bin / agent),
            version="fixture",
        ),
    )
    command, _state, _version, controls = workflow._verified_native_resume_command(
        "claude",
        "tracked-native-session",
        parent_meta={"execution_controls": admitted.receipt()},
    )
    assert controls == admitted
    assert command[command.index("--permission-mode") + 1] == "plan"
    assert command[command.index("--resume") + 1] == "tracked-native-session"


@pytest.mark.parametrize(
    ("body", "override", "effective", "source"),
    [
        ('[runtime.picking]\npermissions = "bypass"\n', "", "bypass", "config:global"),
        (
            'permissions = "read-only"\n[runtime.picking]\npermissions = "bypass"\n',
            "",
            "bypass",
            "config:global",
        ),
        (
            'permissions = "bypass"\n[runtime.picking]\npermissions = "auto"\n',
            "",
            "auto",
            "config:global",
        ),
        (
            'permissions = "bypass"\n[runtime.picking]\npermissions = ""\n',
            "",
            "auto",
            "default",
        ),
        ('permissions = "bypass"\n', "", "bypass", "config:global"),
        (
            (
                '[runtime.picking]\npermissions = "bypass"\n'
                '[agents.claude]\npermissions = "read-only"\n'
            ),
            "",
            "read-only",
            "config:agents.claude",
        ),
        (
            (
                '[runtime.picking]\npermissions = "bypass"\n'
                '[agents.claude]\npermissions = "read-only"\n'
            ),
            "auto",
            "auto",
            "dispatch",
        ),
    ],
)
def test_ui_permission_precedence_reaches_public_launch_and_metadata(
    world: _World, tmp_path: Path, body: str, override: str, effective: str, source: str
) -> None:
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(body, encoding="utf-8")
    world.env["XDG_CONFIG_HOME"] = str(config.parent.parent)
    args = ["--permissions", override] if override else []
    result = world.launch(
        "workflow", "claude", *args, "--repo", str(world.repo), "-p", "configured run"
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    controls = receipt["execution_controls"]
    assert controls["permissions_effective"] == effective
    assert controls["permissions_source"] == source
    assert controls["permissions_requested"] == (
        "" if source == "default" else effective
    )
    argv = _await_argv(world.argv_file)
    assert argv == receipt["worker_command"][1:]
    expected_flag = {"bypass": "bypassPermissions", "auto": "auto", "read-only": "plan"}
    assert argv[argv.index("--permission-mode") + 1] == expected_flag[effective]
    meta = json.loads(
        (_runtime_runs(world.env) / receipt["run_id"] / "meta.json").read_text()
    )
    assert meta["execution_controls"] == controls


@pytest.mark.parametrize(
    "body",
    [
        'permissions = "bypass"\n[runtime.picking]\npermissions = "yolo"\n',
        "[runtime.picking]\npermissions = false\n",
        '[runtime.picking]\npermissions = ["bypass"]\n',
        'runtime = "bypass"\n',
        '[runtime]\npicking = "bypass"\n',
    ],
)
def test_malformed_ui_permission_refuses_before_launch(
    world: _World, tmp_path: Path, body: str
) -> None:
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(body, encoding="utf-8")
    world.env["XDG_CONFIG_HOME"] = str(config.parent.parent)
    result = world.launch("workflow", "claude", "--repo", str(world.repo), "-p", "x")
    assert result.returncode == 2, result.stdout
    assert "runtime" in result.stderr
    assert not world.argv_file.exists()
    runs = _runtime_runs(world.env)
    assert not runs.exists() or not any(runs.iterdir())


@pytest.mark.parametrize("selection", ["dispatch", "global", "agent"])
def test_junie_bypass_launch_records_honest_auto_adaptation(
    tmp_path: Path, selection: str
) -> None:
    world = _World(tmp_path, provider="junie")
    config = tmp_path / "xdg" / "vibecrafted" / "config.toml"
    config.parent.mkdir(parents=True)
    table = "agents.junie" if selection == "agent" else "runtime.picking"
    config.write_text(f'[{table}]\npermissions = "bypass"\n', encoding="utf-8")
    world.env["XDG_CONFIG_HOME"] = str(config.parent.parent)
    args = ["--permissions", "bypass"] if selection == "dispatch" else []
    result = world.launch(
        "workflow", "junie", *args, "--repo", str(world.repo), "-p", "x"
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    controls = receipt["execution_controls"]
    assert controls["permissions_requested"] == "bypass"
    assert controls["permissions_effective"] == "auto"
    assert (
        controls["permissions_source"]
        == {
            "dispatch": "dispatch",
            "global": "config:global",
            "agent": "config:agents.junie",
        }[selection]
    )
    assert controls["permissions_reason"] == "junie-headless-no-bypass"
    assert controls["provider_flags"] == []
    argv = _await_argv(world.argv_file)
    assert "--brave" not in argv
    assert "--plan" not in argv
    assert argv == receipt["worker_command"][1:]
    meta = json.loads(
        (_runtime_runs(world.env) / receipt["run_id"] / "meta.json").read_text()
    )
    assert meta["execution_controls"] == controls
