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


def test_omitting_the_flags_keeps_the_historical_default(world: _World) -> None:
    result = world.launch(
        "workflow", "claude", "--repo", str(world.repo), "--prompt", "default run"
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert "worktree" not in receipt
    worker = receipt["worker_command"]
    assert worker[-2:] == ["--permission-mode", "bypassPermissions"]
    assert "--settings" not in worker
    controls = receipt["execution_controls"]
    assert controls["permissions_requested"] == ""
    assert controls["permissions_effective"] == "bypass"
    assert controls["sandbox_requested"] == ""
    assert controls["sandbox_effective"] == "provider-default"
    argv = _await_argv(world.argv_file)
    assert argv[-2:] == ["--permission-mode", "bypassPermissions"]
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
        ("junie", ["--permissions", "bypass"], "supported: auto"),
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
    result = world.launch(
        "workflow", "codex", "--sandbox", "true", "--repo", str(world.repo), "-p", "x"
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
    assert "--sandbox [true|false]" in result.stdout
    assert "--permissions auto --sandbox true" in result.stdout
    argparse_help = _cli(["workflow", "--help"], cwd=world.outside, env=world.env)
    assert argparse_help.returncode == 0
    assert "--permissions" in argparse_help.stdout
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
