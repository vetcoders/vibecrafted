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
    ProviderUsageCapability,
    _ClaudeTranscriptUsage,
    interactive_policy_command,
    interactive_workspace_command,
    launch_interactive_workspace,
    main,
    prepare_interactive_workspace_launch,
    resolve_provider_policy,
    resolve_provider_usage_capability,
    resolve_quota_policy,
)


def _fake_interactive_provider(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if '--help' in sys.argv:\n"
        "  print('  --session-id <uuid>')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv:\n"
        "  print('2.1.232 (Claude Code)')\n"
        "  raise SystemExit(0)\n"
        "session_id = sys.argv[sys.argv.index('--session-id') + 1]\n"
        "capture = pathlib.Path(os.environ['SMOKE_CAPTURE'])\n"
        "capture.write_text(json.dumps({\n"
        "  'pid': os.getpid(), 'stdin_tty': os.isatty(0),\n"
        "  'stdout_tty': os.isatty(1), 'stderr_tty': os.isatty(2),\n"
        "  'run_id': os.environ['VIBECRAFTED_RUN_ID'],\n"
        "  'session_id': session_id,\n"
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
        "claude",
        "--runtime",
        "local-native",
        "--permissions",
        "read-only",
        "--root",
        str(repo),
        "--prompt",
        "/vc-init",
    ]


_TEST_USAGE_CAPABILITY = ProviderUsageCapability(
    provider="claude",
    supported=True,
    source="claude-transcript-jsonl-v1",
    provider_version="2.1.232",
)
_TEST_PROVIDER_SESSION_ID = "11111111-1111-4111-8111-111111111111"


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
        provider="claude",
        runtime="local-worktrees",
        permissions="read-only",
        selected_root=repo,
        prompt="/vc-init",
        run_id="init-260825-123102-00001",
        executable=sys.executable,
        worker_pid=4242,
        quota_policy=resolve_quota_policy("safe", runtime="local-worktrees"),
        usage_capability=_TEST_USAGE_CAPABILITY,
        provider_session_id=_TEST_PROVIDER_SESSION_ID,
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
        provider="claude",
        runtime="local-native",
        permissions="read-only",
        selected_root=repo,
        prompt="/vc-init",
        run_id="init-260825-123102-00002",
        executable=sys.executable,
        quota_policy=resolve_quota_policy("safe", runtime="local-native"),
        usage_capability=_TEST_USAGE_CAPABILITY,
        provider_session_id=_TEST_PROVIDER_SESSION_ID,
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
            provider="claude",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id=f"init-260825-123102-0000{index}",
            executable=sys.executable,
            quota_policy=resolve_quota_policy("safe", runtime="local-worktrees"),
            usage_capability=_TEST_USAGE_CAPABILITY,
            provider_session_id=f"11111111-1111-4111-8111-11111111111{index}",
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
    provider = fake_bin / "claude"
    provider.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "if '--help' in sys.argv:\n"
        "  print('  --session-id <uuid>')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv:\n"
        "  print('2.1.232 (Claude Code)')\n"
        "  raise SystemExit(0)\n"
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
            "claude",
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
    _fake_interactive_provider(fake_bin / "claude")
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
        assert meta["quota_policy"] == {
            "kind": "bounded",
            "token_budget": 250_000,
            "selection": "safe",
            "warning": "",
        }
        assert meta["usage_capability"]["source"] == "claude-transcript-jsonl-v1"
        assert meta["provider_session_id"] == observed["session_id"]
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


def test_interactive_small_token_quota_stops_live_provider_with_distinct_truth(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    claude_home = tmp_path / "claude-home"
    repo = tmp_path / "repo"
    _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "claude"
    provider.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "if '--help' in sys.argv:\n"
        "  print('  --session-id <uuid>')\n"
        "  raise SystemExit(0)\n"
        "if '--version' in sys.argv:\n"
        "  print('2.1.232 (Claude Code)')\n"
        "  raise SystemExit(0)\n"
        "session_id = sys.argv[sys.argv.index('--session-id') + 1]\n"
        "capture = pathlib.Path(os.environ['SMOKE_CAPTURE'])\n"
        "capture.write_text(json.dumps({'pid': os.getpid(), 'session_id': session_id}) + '\\n', encoding='utf-8')\n"
        "transcript = pathlib.Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects' / 'fixture' / f'{session_id}.jsonl'\n"
        "transcript.parent.mkdir(parents=True, exist_ok=True)\n"
        "transcript.write_text(json.dumps({\n"
        "  'type': 'assistant', 'uuid': 'event-1', 'sessionId': session_id,\n"
        "  'cwd': os.getcwd(), 'version': '2.1.232',\n"
        "  'message': {'id': 'msg-1', 'type': 'message', 'usage': {\n"
        "    'input_tokens': 1, 'cache_creation_input_tokens': 0,\n"
        "    'cache_read_input_tokens': 0, 'output_tokens': 1,\n"
        "  }},\n"
        "}) + '\\n', encoding='utf-8')\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        VIBECRAFTED_HOME=str(home),
        VIBECRAFTED_RUNTIME_BIN=str(fake_bin),
        CLAUDE_CONFIG_DIR=str(claude_home),
        SMOKE_CAPTURE=str(capture),
        PATH=str(fake_bin) + os.pathsep + env["PATH"],
    )
    owner = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.spawn",
            "interactive-launch",
            "claude",
            "--runtime",
            "local-native",
            "--permissions",
            "read-only",
            "--root",
            str(repo),
            "--prompt",
            "/vc-init",
            "--token-budget",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    try:
        assert owner.wait(timeout=5) == 75
        observed = json.loads(capture.read_text(encoding="utf-8"))
        meta_path = next((home / "control_plane/runtime_runs").glob("*/meta.json"))
        terminal = json.loads(meta_path.read_text(encoding="utf-8"))
        assert terminal["status"] == "quota_exhausted"
        assert terminal["terminal_reason"] == "quota_exhausted"
        assert terminal["provider_session_id"] == observed["session_id"]
        assert terminal["measured_usage"]["total_tokens"] == 2
        assert terminal["quota_policy"]["token_budget"] == 1
        with pytest.raises(ProcessLookupError):
            os.kill(observed["pid"], 0)
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait()


def test_interactive_nonzero_exit_terminalizes_and_returns_provider_status(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_interactive_provider(fake_bin / "claude")
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
    assert meta["status"] != "quota_exhausted"


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
    _fake_interactive_provider(fake_bin / "claude")
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
    provider = fake_bin / "claude"
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
            "claude", "/vc-init", "local-worktrees", "read-only", repo
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
            provider="claude",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id="init-260825-123102-00005",
            executable=sys.executable,
            quota_policy=resolve_quota_policy("safe", runtime="local-worktrees"),
            usage_capability=_TEST_USAGE_CAPABILITY,
            provider_session_id=_TEST_PROVIDER_SESSION_ID,
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
            provider="claude",
            runtime="local-worktrees",
            permissions="read-only",
            selected_root=repo,
            prompt="/vc-init",
            run_id="init-260825-123102-00006",
            executable=sys.executable,
            quota_policy=resolve_quota_policy("safe", runtime="local-worktrees"),
            usage_capability=_TEST_USAGE_CAPABILITY,
            provider_session_id=_TEST_PROVIDER_SESSION_ID,
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "vibecrafted_core.spawn.resolve_provider_usage_capability",
        lambda _provider: _TEST_USAGE_CAPABILITY,
    )
    command = interactive_workspace_command(
        "claude", "/vc-init", "local-worktrees", "read-only", tmp_path
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


@pytest.mark.parametrize(
    ("selection", "runtime", "expected_kind", "expected_budget"),
    [
        (None, "local-native", "bounded", 250_000),
        ("safe", "local-worktrees", "bounded", 250_000),
        ("42", "local-native", "bounded", 42),
        ("unlimited", "local-native", "unlimited", None),
    ],
)
def test_quota_policy_is_typed_and_validated(
    selection: str | None, runtime: str, expected_kind: str, expected_budget: int | None
) -> None:
    policy = resolve_quota_policy(selection, runtime=runtime)
    assert policy.kind == expected_kind
    assert policy.token_budget == expected_budget


@pytest.mark.parametrize("selection", ["0", "-1", "10000001", "wat"])
def test_invalid_bounded_quota_fails_closed(selection: str) -> None:
    with pytest.raises(ValueError, match="token budget"):
        resolve_quota_policy(selection, runtime="local-native")


def test_unlimited_quota_is_restricted_to_observed_local_native() -> None:
    with pytest.raises(ValueError, match="User-observed local-native"):
        resolve_quota_policy("unlimited", runtime="local-worktrees")


def test_unsupported_provider_quota_fails_before_runtime_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    _repo(repo)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    provider = fake_bin / "codex"
    provider.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    provider.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])

    with pytest.raises(ValueError, match="no verified live"):
        launch_interactive_workspace(
            "codex", "/vc-init", "local-native", "read-only", repo, "safe"
        )

    assert not (home / "control_plane" / "runtime_runs").exists()


def test_measured_usage_capability_matrix_has_one_honest_provider(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    claude = fake_bin / "claude"
    _fake_interactive_provider(claude)

    supported = resolve_provider_usage_capability("claude", executable=str(claude))
    assert supported.supported is True
    assert supported.source == "claude-transcript-jsonl-v1"
    assert supported.provider_version == "2.1.232"
    for provider in ("codex", "agy", "grok", "junie"):
        executable = fake_bin / provider
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        capability = resolve_provider_usage_capability(
            provider, executable=str(executable)
        )
        assert capability.supported is False
        assert "no verified live" in capability.reason


def _usage_event(
    *, session_id: str, cwd: Path, message_id: str, input_tokens: int = 3
) -> dict[str, object]:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "cwd": str(cwd),
        "version": "2.1.232",
        "message": {
            "id": message_id,
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 2,
            },
        },
    }


def test_exact_session_usage_is_monotonic_and_deduplicates_message_ids(
    tmp_path: Path,
) -> None:
    session_id = "22222222-2222-4222-8222-222222222222"
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "claude"
    reader = _ClaudeTranscriptUsage(
        provider_session_id=session_id,
        effective_root=str(repo),
        provider_version="2.1.232",
        env={"CLAUDE_CONFIG_DIR": str(config)},
    )
    transcript = config / "projects" / "fixture" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    event = _usage_event(session_id=session_id, cwd=repo, message_id="msg-1")
    transcript.write_text(
        json.dumps(event) + "\n" + json.dumps(event) + "\n", encoding="utf-8"
    )

    assert reader.poll() == {
        "input_tokens": 3,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 2,
        "total_tokens": 5,
        "messages": 1,
    }
    transcript.write_text(
        transcript.read_text(encoding="utf-8")
        + json.dumps(_usage_event(session_id=session_id, cwd=repo, message_id="msg-2"))
        + "\n",
        encoding="utf-8",
    )
    assert reader.poll()["total_tokens"] == 10


def test_usage_reader_ignores_unrelated_newest_session_and_rejects_foreign_event(
    tmp_path: Path,
) -> None:
    session_id = "33333333-3333-4333-8333-333333333333"
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "claude"
    unrelated = config / "projects" / "fixture" / "newest.jsonl"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("{}\n", encoding="utf-8")
    reader = _ClaudeTranscriptUsage(
        provider_session_id=session_id,
        effective_root=str(repo),
        provider_version="2.1.232",
        env={"CLAUDE_CONFIG_DIR": str(config)},
    )
    assert reader.poll()["total_tokens"] == 0
    transcript = unrelated.with_name(f"{session_id}.jsonl")
    transcript.write_text(
        json.dumps(
            _usage_event(
                session_id="foreign-session", cwd=repo, message_id="msg-foreign"
            )
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="foreign session"):
        reader.poll()


def test_usage_reader_rejects_preexisting_exact_session_source(tmp_path: Path) -> None:
    session_id = "44444444-4444-4444-8444-444444444444"
    repo = tmp_path / "repo"
    repo.mkdir()
    config = tmp_path / "claude"
    transcript = config / "projects" / "fixture" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        _ClaudeTranscriptUsage(
            provider_session_id=session_id,
            effective_root=str(repo),
            provider_version="2.1.232",
            env={"CLAUDE_CONFIG_DIR": str(config)},
        )


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
