from __future__ import annotations

import io
import itertools
import json
import os
import pty
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from vibecrafted_core.spawn import (
    PERMISSION_POLICIES,
    POLICY_MODES,
    POLICY_PROVIDERS,
    RUNTIME_POLICIES,
    interactive_policy_command,
    interactive_workspace_command,
    launch_interactive_workspace,
    main,
    prepare_interactive_workspace_launch,
    resolve_provider_policy,
)


def _fake_interactive_provider(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "capture = pathlib.Path(os.environ['SMOKE_CAPTURE'])\n"
        "capture.write_text(json.dumps({\n"
        "  'pid': os.getpid(), 'stdin_tty': os.isatty(0),\n"
        "  'stdout_tty': os.isatty(1), 'stderr_tty': os.isatty(2),\n"
        "  'run_id': os.environ['VIBECRAFTED_RUN_ID'],\n"
        "}) + '\\n', encoding='utf-8')\n"
        "if os.environ.get('SMOKE_BLOCK') == '1':\n"
        "  while True: time.sleep(0.05)\n"
        "raise SystemExit(int(os.environ.get('SMOKE_EXIT', '0')))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _wait_for(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _interactive_argv(repo: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "vibecrafted_core.spawn",
        "interactive-launch",
        "codex",
        "--runtime",
        "local-native",
        "--permissions",
        "read-only",
        "--root",
        str(repo),
        "--prompt",
        "/vc-init",
    ]


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repo(path: Path) -> str:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "runtime-test")
    (path / ".gitignore").write_text("target/\n", encoding="utf-8")
    (path / "README.md").write_text("parent\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    return _git(path, "rev-parse", "HEAD")


def test_interactive_worktree_launch_uses_canonical_owner_and_parent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    baseline = _repo(repo)

    launch = prepare_interactive_workspace_launch(
        provider="codex",
        runtime="local-worktrees",
        permissions="read-only",
        selected_root=repo,
        prompt="/vc-init",
        run_id="init-260825-123102-00001",
        executable=sys.executable,
        worker_pid=4242,
    )

    effective = Path(launch.effective_root)
    assert effective != repo.resolve()
    assert _git(effective, "rev-parse", "HEAD") == baseline
    assert (repo / "README.md").read_text(encoding="utf-8") == "parent\n"
    assert launch.parent_root == str(repo.resolve())
    assert launch.workspace_id
    assert launch.vibecrafted_session_id
    assert launch.meta_path.is_file()
    assert launch.receipt["root"] == str(effective)
    assert launch.receipt["parent_root"] == str(repo.resolve())
    assert launch.receipt["workspace_id"] == launch.workspace_id
    assert launch.receipt["worker_pid"] == 4242


def test_local_native_keeps_selected_checkout_and_creates_no_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    baseline = _repo(repo)

    launch = prepare_interactive_workspace_launch(
        provider="codex",
        runtime="local-native",
        permissions="read-only",
        selected_root=repo,
        prompt="/vc-init",
        run_id="init-260825-123102-00002",
        executable=sys.executable,
    )

    assert launch.effective_root == str(repo.resolve())
    assert launch.receipt["effective_worktree_path"] == ""
    assert _git(repo, "rev-parse", "HEAD") == baseline
    assert not (tmp_path / "home" / "worktrees").exists()


def test_two_interactive_worktree_launches_cannot_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    _repo(repo)

    launches = [
        prepare_interactive_workspace_launch(
            provider="codex",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id=f"init-260825-123102-0000{index}",
            executable=sys.executable,
        )
        for index in (3, 4)
    ]

    assert launches[0].effective_root != launches[1].effective_root
    assert launches[0].meta_path != launches[1].meta_path
    assert _git(Path(launches[0].effective_root), "branch", "--show-current") != _git(
        Path(launches[1].effective_root), "branch", "--show-current"
    )


def test_interactive_worktree_execs_provider_inside_canonical_checkout(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    baseline = _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "codex"
    provider.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['SMOKE_CAPTURE']).write_text(json.dumps({\n"
        "  'argv': sys.argv, 'cwd': os.getcwd(),\n"
        "  'run_id': os.environ['VIBECRAFTED_RUN_ID'],\n"
        "  'workspace_id': os.environ['VIBECRAFTED_WORKSPACE_ID'],\n"
        "  'session_id': os.environ['VIBECRAFTED_SESSION_ID'],\n"
        "  'instance_id': os.environ['VIBECRAFTED_WORKSPACE_INSTANCE_ID'],\n"
        "  'build_id': os.environ['VIBECRAFTED_BUILD_ID'],\n"
        "  'parent_root': os.environ['VIBECRAFTED_PARENT_ROOT'],\n"
        "  'effective_root': os.environ['VIBECRAFTED_EFFECTIVE_ROOT'],\n"
        "}) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["VIBECRAFTED_HOME"] = str(home)
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["SMOKE_CAPTURE"] = str(capture)
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.spawn",
            "interactive-launch",
            "codex",
            "--runtime",
            "local-worktrees",
            "--permissions",
            "read-only",
            "--root",
            str(repo),
            "--prompt",
            "/vc-init",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    observed = json.loads(capture.read_text(encoding="utf-8"))
    effective = Path(observed["effective_root"])
    assert Path(observed["cwd"]) == effective
    assert effective != repo.resolve()
    assert observed["parent_root"] == str(repo.resolve())
    assert observed["workspace_id"]
    assert observed["session_id"]
    assert observed["instance_id"]
    assert observed["build_id"]
    assert _git(effective, "rev-parse", "HEAD") == baseline
    assert _git(repo, "status", "--porcelain") == ""
    meta = json.loads(
        (
            home / "control_plane/runtime_runs" / observed["run_id"] / "meta.json"
        ).read_text(encoding="utf-8")
    )
    assert meta["parent_root"] == str(repo.resolve())
    assert meta["effective_worktree_path"] == str(effective)
    assert meta["runtime_policy"] == "local-worktrees"
    assert meta["permission_policy"] == "read-only"
    assert meta["status"] == "completed"
    assert meta["liveness"] == "terminal"
    assert meta["exit_code"] == 0
    assert meta["terminal_reason"] == "provider_exit_zero"


def test_interactive_owner_keeps_distinct_live_provider_on_inherited_tty(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_interactive_provider(fake_bin / "codex")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        VIBECRAFTED_HOME=str(home),
        VIBECRAFTED_RUNTIME_BIN=str(fake_bin),
        SMOKE_CAPTURE=str(capture),
        SMOKE_BLOCK="1",
        PATH=str(fake_bin) + os.pathsep + env["PATH"],
    )
    master_fd, slave_fd = pty.openpty()
    owner = subprocess.Popen(
        _interactive_argv(repo),
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
    )
    os.close(slave_fd)
    try:
        _wait_for(capture)
        observed = json.loads(capture.read_text(encoding="utf-8"))
        meta_path = (
            home / "control_plane/runtime_runs" / observed["run_id"] / "meta.json"
        )
        _wait_for(meta_path)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert observed["stdin_tty"] is True
        assert observed["stdout_tty"] is True
        assert observed["stderr_tty"] is True
        assert meta["owner_pid"] == owner.pid
        assert meta["worker_pid"] == observed["pid"]
        assert meta["owner_pid"] != meta["worker_pid"]
        assert meta["status"] == "active"
        assert meta["liveness"] == "active"
        os.kill(meta["owner_pid"], 0)
        os.kill(meta["worker_pid"], 0)
        owner.send_signal(signal.SIGTERM)
        assert owner.wait(timeout=5) == 128 + signal.SIGTERM
        terminal = json.loads(meta_path.read_text(encoding="utf-8"))
        assert terminal["status"] == "cancelled"
        assert terminal["terminal_reason"] == "owner_signal:SIGTERM"
        with pytest.raises(ProcessLookupError):
            os.kill(meta["worker_pid"], 0)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()
        os.close(master_fd)


def test_interactive_nonzero_exit_terminalizes_and_returns_provider_status(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_interactive_provider(fake_bin / "codex")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        VIBECRAFTED_HOME=str(home),
        VIBECRAFTED_RUNTIME_BIN=str(fake_bin),
        SMOKE_CAPTURE=str(capture),
        SMOKE_EXIT="7",
        PATH=str(fake_bin) + os.pathsep + env["PATH"],
    )

    completed = subprocess.run(
        _interactive_argv(repo),
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=False,
    )

    observed = json.loads(capture.read_text(encoding="utf-8"))
    meta = json.loads(
        (
            home / "control_plane/runtime_runs" / observed["run_id"] / "meta.json"
        ).read_text(encoding="utf-8")
    )
    assert completed.returncode == 7
    assert meta["status"] == "failed"
    assert meta["liveness"] == "terminal"
    assert meta["exit_code"] == 7
    assert meta["terminal_reason"] == "provider_exit_nonzero"


@pytest.mark.parametrize(
    ("signum", "expected_status"),
    [(signal.SIGINT, 128 + signal.SIGINT), (signal.SIGTERM, 128 + signal.SIGTERM)],
)
def test_interactive_owner_signal_terminalizes_without_surviving_child(
    signum: int, expected_status: int, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_interactive_provider(fake_bin / "codex")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        VIBECRAFTED_HOME=str(home),
        VIBECRAFTED_RUNTIME_BIN=str(fake_bin),
        SMOKE_CAPTURE=str(capture),
        SMOKE_BLOCK="1",
        PATH=str(fake_bin) + os.pathsep + env["PATH"],
    )
    owner = subprocess.Popen(
        _interactive_argv(repo),
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        start_new_session=True,
    )
    try:
        _wait_for(capture)
        observed = json.loads(capture.read_text(encoding="utf-8"))
        meta_path = (
            home / "control_plane/runtime_runs" / observed["run_id"] / "meta.json"
        )
        _wait_for(meta_path)
        owner.send_signal(signum)
        assert owner.wait(timeout=5) == expected_status
        terminal = json.loads(meta_path.read_text(encoding="utf-8"))
        assert terminal["status"] == "cancelled"
        assert terminal["liveness"] == "terminal"
        assert terminal["exit_code"] == expected_status
        assert (
            terminal["terminal_reason"] == f"owner_signal:{signal.Signals(signum).name}"
        )
        with pytest.raises(ProcessLookupError):
            os.kill(observed["pid"], 0)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()


def test_child_spawn_failure_publishes_no_false_active_and_removes_clean_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core import spawn

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "codex"
    _fake_interactive_provider(provider)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    real_prepare = spawn.prepare_interactive_workspace_launch
    real_popen = spawn.subprocess.Popen

    def prepare_then_break_spawn(*args: object, **kwargs: object):
        prepared = real_prepare(*args, **kwargs)

        def break_once(*_args: object, **_kwargs: object):
            monkeypatch.setattr(spawn.subprocess, "Popen", real_popen)
            raise OSError("spawn denied")

        monkeypatch.setattr(
            spawn.subprocess,
            "Popen",
            break_once,
        )
        return prepared

    monkeypatch.setattr(
        spawn, "prepare_interactive_workspace_launch", prepare_then_break_spawn
    )

    with pytest.raises(OSError, match="spawn denied"):
        launch_interactive_workspace(
            "codex", "/vc-init", "local-worktrees", "read-only", repo
        )

    meta_path = next((home / "control_plane/runtime_runs").glob("*/meta.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["liveness"] == "terminal"
    assert meta["terminal_reason"] == "child_spawn_failed"
    assert meta["prepared_worktree_cleanup"] == "removed"
    assert not Path(meta["effective_worktree_path"]).exists()
    events = (home / "control_plane/events.jsonl").read_text(encoding="utf-8")
    assert "lifecycle:active" not in events


@pytest.mark.parametrize("kind", ["non-git", "dirty"])
def test_invalid_worktree_parent_fails_before_runtime_truth(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    if kind == "non-git":
        repo.mkdir()
    else:
        _repo(repo)
        (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises((ValueError, RuntimeError), match="git repository|clean"):
        prepare_interactive_workspace_launch(
            provider="codex",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id="init-260825-123102-00005",
            executable=sys.executable,
        )

    assert not (home / "control_plane" / "runtime_runs").exists()


def test_worktree_creation_failure_has_no_accepted_or_spawned_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.dispatch.worktrees import WorktreeManager

    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    repo = tmp_path / "repo"
    _repo(repo)
    monkeypatch.setattr(
        WorktreeManager,
        "prepare_agent_launch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("create failed")),
    )

    with pytest.raises(RuntimeError, match="create failed"):
        prepare_interactive_workspace_launch(
            provider="codex",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id="init-260825-123102-00006",
            executable=sys.executable,
        )

    assert not (home / "control_plane" / "runtime_runs").exists()


def test_every_runtime_permission_provider_mode_cell_is_explicit() -> None:
    cells = [
        resolve_provider_policy(provider, runtime, permissions, mode)
        for provider, runtime, permissions, mode in itertools.product(
            POLICY_PROVIDERS, RUNTIME_POLICIES, PERMISSION_POLICIES, POLICY_MODES
        )
    ]

    assert len(cells) == 5 * 4 * 4 * 2
    assert all(cell.behavior or cell.reason for cell in cells)
    assert all(cell.supported != bool(cell.reason) for cell in cells)


@pytest.mark.parametrize("provider", POLICY_PROVIDERS)
def test_worktrees_are_interactive_only_while_vm_and_cloud_stay_unavailable(
    provider: str,
) -> None:
    assert resolve_provider_policy(
        provider, "local-worktrees", "bypass", "interactive"
    ).supported
    assert not resolve_provider_policy(
        provider, "local-worktrees", "bypass", "headless"
    ).supported
    assert (
        "VM entrypoint"
        in resolve_provider_policy(provider, "local-vm", "bypass", "interactive").reason
    )
    assert (
        "coming soon"
        in resolve_provider_policy(
            provider, "cloud-soon", "bypass", "interactive"
        ).reason
    )


def test_accept_edits_is_native_or_unsupported_never_approximated() -> None:
    for provider in ("claude", "agy", "grok"):
        decision = resolve_provider_policy(
            provider, "local-native", "accept-edits", "headless"
        )
        assert decision.supported
        assert "edits pass" in decision.behavior
        assert "fail closed" in decision.behavior

    for provider in ("codex", "junie"):
        decision = resolve_provider_policy(
            provider, "local-native", "accept-edits", "interactive"
        )
        assert not decision.supported
        assert "no native accept-edits" in decision.reason


def test_junie_interactive_only_policies_fail_closed_headless() -> None:
    assert resolve_provider_policy(
        "junie", "local-native", "bypass", "interactive"
    ).supported
    assert not resolve_provider_policy(
        "junie", "local-native", "bypass", "headless"
    ).supported
    assert not resolve_provider_policy(
        "junie", "local-native", "read-only", "headless"
    ).supported


def test_interactive_command_uses_contract_flags() -> None:
    command = interactive_policy_command(
        "claude", "/vc-init", "local-native", "accept-edits"
    )
    assert command == [
        "claude",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "/vc-init",
    ]

    with pytest.raises(ValueError, match="no native accept-edits"):
        interactive_policy_command("codex", "/vc-init", "local-native", "accept-edits")


def test_interactive_workspace_command_wraps_the_exact_init_route(
    tmp_path: Path,
) -> None:
    command = interactive_workspace_command(
        "codex", "/vc-init", "local-worktrees", "read-only", tmp_path
    )

    assert command[:4] == [
        sys.executable,
        "-m",
        "vibecrafted_core.spawn",
        "interactive-launch",
    ]
    assert command[-2:] == ["--prompt", "/vc-init"]
    assert "local-worktrees" in command
    assert "read-only" in command


def test_policy_cli_reads_the_same_contract(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("/vc-init"))

    assert (
        main(
            [
                "policy-command",
                "grok",
                "--runtime",
                "local-native",
                "--permissions",
                "read-only",
            ]
        )
        == 0
    )
    assert shlex.split(capsys.readouterr().out) == [
        "grok",
        "--cwd",
        ".",
        "--permission-mode",
        "plan",
        "--no-alt-screen",
        "/vc-init",
    ]
