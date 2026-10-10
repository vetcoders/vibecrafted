"""Three interactive environments through one canonical launch route.

checkout (local-native), branch-backed worktree (local-worktrees) and the local
container (policy key local-vm) are admitted by ``interactive_workspace_command``
and executed by ``launch_interactive_workspace``. Fakes cover the matrix; the
real engine and provider runs are recorded in the worker report.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from vibecrafted_core import dev_container, spawn
from vibecrafted_core.spawn import (
    ProviderUsageCapability,
    interactive_workspace_command,
    launch_interactive_workspace,
    resumable_interactive_runs,
)

UNMETERED = ProviderUsageCapability("codex", False, reason="no live usage side channel")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "runtime-test")
    (path / "README.md").write_text("parent\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    _git(path, "remote", "add", "origin", "https://github.com/vetcoders/fixture.git")
    return _git(path, "rev-parse", "HEAD")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vc_home = tmp_path / "vc-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(vc_home))
    for name in ("VIBECRAFTED_RUN_ID", "CODEX_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        spawn, "resolve_provider_usage_capability", lambda *_a, **_k: UNMETERED
    )
    return vc_home


def _admission(command: list[str]) -> dict:
    return json.loads(Path(command[-1]).read_bytes())


def _flag(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


@pytest.mark.parametrize("skill", ["init", "partner", "operator"])
def test_worktree_route_is_not_blocked_by_missing_metering(
    tmp_path: Path, home: Path, skill: str
) -> None:
    repo = tmp_path / "project"
    baseline = _repo(repo)

    command = interactive_workspace_command(
        "codex",
        "/vc-init",
        "local-worktrees",
        "bypass",
        repo,
        token_budget="unmetered",
        skill=skill,
    )

    admission = _admission(command)
    worktree = Path(admission["worktree_path"])
    assert worktree.is_relative_to(home / "worktrees")
    assert _git(worktree, "rev-parse", "HEAD") == baseline
    assert admission["worktree_branch"] == _git(worktree, "branch", "--show-current")
    assert admission["worktree_baseline_sha"] == baseline
    assert admission["parent_root"] == str(repo.resolve())
    assert admission["root"] == str(worktree)
    assert admission["runtime_policy"] == "local-worktrees"
    assert admission["execution_environment"] == "worktree"
    assert admission["skill"] == skill
    # The worktree is already the root; the owner runs it like a checkout.
    assert _flag(command, "--runtime") == "local-native"
    assert _flag(command, "--root") == str(worktree)
    assert _flag(command, "--token-budget") == "unmetered"


def test_explicit_token_limit_still_needs_metering(tmp_path: Path, home: Path) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    with pytest.raises(ValueError, match="token limit 5000 needs live usage metering"):
        interactive_workspace_command(
            "codex", "/vc-init", "local-worktrees", "bypass", repo, token_budget="5000"
        )
    with pytest.raises(ValueError, match="needs live usage metering"):
        interactive_workspace_command(
            "codex", "/vc-init", "local-vm", "bypass", repo, token_budget="safe"
        )


@pytest.mark.parametrize("skill", ["init", "partner", "operator"])
def test_container_route_is_admitted_over_the_live_checkout(
    tmp_path: Path, home: Path, skill: str
) -> None:
    repo = tmp_path / "project"
    _repo(repo)

    command = interactive_workspace_command(
        "codex",
        "/vc-init",
        "local-vm",
        "auto",
        repo,
        token_budget="unmetered",
        skill=skill,
        model="gpt-6.1-sol",
        effort="high",
    )

    admission = _admission(command)
    assert admission["runtime_policy"] == "local-vm"
    assert admission["execution_environment"] == "local-container"
    # The container mounts the living tree; no second checkout is cut.
    assert admission["runtime_class"] == "living-tree"
    assert admission["root"] == str(repo.resolve())
    assert "worktree_path" not in admission
    assert admission["model_requested"] == "gpt-6.1-sol"
    assert admission["effort_requested"] == "high"
    assert _flag(command, "--runtime") == "local-vm"
    assert _flag(command, "--permissions") == "auto"
    assert not (home / "worktrees").exists() or not any((home / "worktrees").rglob("*"))


def test_container_route_refuses_unsupported_cells_per_provider(
    tmp_path: Path, home: Path
) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    with pytest.raises(ValueError, match="agy is not installed in the local container"):
        interactive_workspace_command(
            "agy", "/vc-init", "local-vm", "bypass", repo, token_budget="unmetered"
        )
    with pytest.raises(ValueError, match="choose one environment"):
        interactive_workspace_command(
            "codex",
            "/vc-init",
            "local-vm",
            "bypass",
            repo,
            token_budget="unmetered",
            worktree="true",
        )


def _record_run(home: Path, run_id: str, **fields: object) -> Path:
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "run_id": run_id,
        "agent": "codex",
        "mode": "interactive",
        "status": "completed",
        "liveness": "terminal",
        **fields,
    }
    path = run_dir / "meta.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_resume_by_run_returns_to_the_recorded_container(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    baseline = _repo(repo)
    session = "019a0000-0000-7000-8000-00000000c0de"
    parent = {
        "run_id": "init-20261010-010101-aaaa",
        "agent": "codex",
        "root": str(repo.resolve()),
        "parent_root": str(repo.resolve()),
        "baseline_sha": baseline,
        "runtime_class": "living-tree",
        "runtime_policy": "local-vm",
        "container": {"compose_project": "vc-project"},
        "agent_session_id": session,
        "status": "completed",
    }
    monkeypatch.setattr(
        "vibecrafted_core.workflow.lookup_run", lambda _run: dict(parent)
    )
    monkeypatch.setattr(
        "vibecrafted_core.workflow._native_resume_meta", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        "vibecrafted_core.workflow._worker_process_alive", lambda _run: False
    )

    command = interactive_workspace_command(
        "codex",
        "",
        "local-vm",
        "bypass",
        "",
        token_budget="unmetered",
        skill="resume",
        resume_run_id=parent["run_id"],
    )

    admission = _admission(command)
    assert _flag(command, "--runtime") == "local-vm"
    assert admission["agent_session_id"] == session
    assert admission["runtime_policy"] == "local-vm"
    assert admission["execution_environment"] == "local-container"
    assert admission["root"] == str(repo.resolve())
    with pytest.raises(ValueError, match="resume preserves its environment"):
        interactive_workspace_command(
            "codex",
            "",
            "local-worktrees",
            "bypass",
            "",
            token_budget="unmetered",
            skill="resume",
            resume_run_id=parent["run_id"],
        )


def test_resume_catalog_lists_only_proven_sessions_in_that_environment(
    tmp_path: Path, home: Path
) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    worktree = home / "worktrees" / "wt-one"
    worktree.mkdir(parents=True)
    _record_run(
        home,
        "init-native-proven",
        root=str(repo),
        parent_root=str(repo),
        runtime_class="living-tree",
        agent_session_id="019a-native",
    )
    _record_run(
        home,
        "init-worktree-proven",
        root=str(worktree),
        parent_root=str(repo),
        runtime_class="local-worktrees",
        worktree_path=str(worktree),
        worktree_branch="cut/codex-init-worktree-proven",
        agent_session_id="019a-worktree",
    )
    _record_run(
        home,
        "init-worktree-unproven",
        root=str(worktree),
        parent_root=str(repo),
        runtime_class="local-worktrees",
        provider_session_requested="019a-requested-only",
    )
    _record_run(
        home,
        "init-container",
        root=str(repo),
        parent_root=str(repo),
        runtime_policy="local-vm",
        container={"compose_project": "vc-project"},
        agent_session_id="019a-container",
    )
    _record_run(
        home,
        "init-live",
        root=str(repo),
        parent_root=str(repo),
        status="active",
        agent_session_id="019a-live",
    )

    native = resumable_interactive_runs("codex", repo, "local-native")
    worktrees = resumable_interactive_runs("codex", repo, "local-worktrees")
    containers = resumable_interactive_runs("codex", repo, "local-vm")

    assert [item["session_id"] for item in native] == ["019a-native"]
    assert [item["run_id"] for item in worktrees] == ["init-worktree-proven"]
    assert worktrees[0]["root"] == str(worktree.resolve())
    assert worktrees[0]["branch"] == "cut/codex-init-worktree-proven"
    assert [item["session_id"] for item in containers] == ["019a-container"]
    assert resumable_interactive_runs("claude", repo, "local-native") == []


def test_host_native_identity_needs_provider_owned_evidence(tmp_path: Path) -> None:
    home_dir = tmp_path / "home"
    root = tmp_path / "project"
    root.mkdir()
    requested = "11111111-2222-4333-8444-555555555555"
    env = {"HOME": str(home_dir)}
    since = time.time() - 5

    assert (
        spawn._prove_host_native_session(
            "claude",
            requested=requested,
            effective_root=str(root),
            env=env,
            since=since,
        )
        == ""
    )
    project_dir = home_dir / ".claude" / "projects" / "-project"
    project_dir.mkdir(parents=True)
    (project_dir / f"{requested}.jsonl").write_text("{}\n", encoding="utf-8")
    assert (
        spawn._prove_host_native_session(
            "claude",
            requested=requested,
            effective_root=str(root),
            env=env,
            since=since,
        )
        == requested
    )

    day = time.gmtime()
    sessions = home_dir / ".codex" / "sessions" / time.strftime("%Y/%m/%d", day)
    sessions.mkdir(parents=True)

    def rollout(name: str, identity: str, cwd: Path) -> None:
        (sessions / f"rollout-{name}.jsonl").write_text(
            json.dumps(
                {"type": "session_meta", "payload": {"id": identity, "cwd": str(cwd)}}
            )
            + "\n",
            encoding="utf-8",
        )

    rollout("a", "019a-codex-one", root)
    rollout("b", "019a-elsewhere", tmp_path)
    assert (
        spawn._prove_host_native_session(
            "codex", requested="", effective_root=str(root), env=env, since=since
        )
        == "019a-codex-one"
    )
    rollout("c", "019a-codex-two", root)
    # Two candidates for one launch are a guess; the run stays unbound.
    assert (
        spawn._prove_host_native_session(
            "codex", requested="", effective_root=str(root), env=env, since=since
        )
        == ""
    )


FAKE_EXEC = r"""#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["FAKE_EXEC_LOG"], "a") as log:
    log.write(json.dumps({"argv": argv, "secret_seen": os.environ.get("OPENAI_API_KEY", "")}) + "\n")
if argv[:2] == ["exec", "-i"]:
    with open(os.environ["FAKE_EXEC_STAGE"], "a") as stage:
        stage.write(json.dumps({"path": argv[-2] + "/" + argv[-1], "body": sys.stdin.read()}) + "\n")
    sys.exit(0)
if "python3" in argv:
    print(os.environ.get("FAKE_CODEX_SESSION", ""))
    sys.exit(0)
if argv[:2] == ["exec", "-it"]:
    print("codex in container: ready")
    sys.exit(int(os.environ.get("FAKE_PROVIDER_EXIT", "0")))
sys.exit(0)
"""


def _fake_target(tmp_path: Path, root: Path) -> dev_container.ContainerTarget:
    cli = tmp_path / "bin" / "docker"
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(
        FAKE_EXEC.replace("#!/usr/bin/env python3", f"#!{sys.executable}", 1),
        encoding="utf-8",
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return dev_container.ContainerTarget(
        cli=str(cli),
        project="vc-project",
        container_id="cid-project",
        container_name="vc-project-dev-1",
        image="vibecrafted-dev:recipe-test",
        recipe_digest="test",
        host_root=str(root.resolve()),
        engine_context="colima-ci",
    )


def test_container_launch_runs_the_provider_inside_and_binds_its_session(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    target = _fake_target(tmp_path, repo)
    log = tmp_path / "exec.jsonl"
    stage = tmp_path / "stage.jsonl"
    monkeypatch.setenv("FAKE_EXEC_LOG", str(log))
    monkeypatch.setenv("FAKE_EXEC_STAGE", str(stage))
    monkeypatch.setenv("FAKE_CODEX_SESSION", "019a0000-0000-7000-8000-0000000c0dex")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-reach-argv")
    monkeypatch.setenv(
        "PATH", f"{target.cli.rsplit('/', 1)[0]}{os.pathsep}{os.environ['PATH']}"
    )
    progress: list[str] = []

    def fake_ensure(root: str, *, env: dict, log) -> dev_container.ContainerTarget:
        assert Path(root) == repo.resolve()
        log("[1/3] Image vibecrafted-dev:recipe-test (Docker context colima-ci)")
        progress.append("prepared")
        return target

    monkeypatch.setattr(dev_container, "ensure_container", fake_ensure)

    status = launch_interactive_workspace(
        "codex",
        "/vc-init",
        "local-vm",
        "bypass",
        repo,
        token_budget="unmetered",
        admission={"run_id": "init-20261010-020202-cccc", "skill": "init"},
    )

    assert status == 0
    assert progress == ["prepared"]
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    provider = next(
        call["argv"] for call in calls if call["argv"][:2] == ["exec", "-it"]
    )
    assert provider[:4] == ["exec", "-it", "-w", "/workspace"]
    assert "OPENAI_API_KEY" in provider
    assert not any("sk-must-not-reach-argv" in arg for arg in provider)
    assert "VIBECRAFTED_RUN_ID=init-20261010-020202-cccc" in provider
    inner = provider[provider.index("cid-project") + 1 :]
    assert inner[:2] == ["sh", "-c"]
    assert inner[4] == "/root/.vibecrafted/agent-runs/init-20261010-020202-cccc"
    assert inner[5:7] == ["codex", "--dangerously-bypass-approvals-and-sandbox"]
    assert provider[-1] == (
        "Read and follow the private task file: "
        "/root/.vibecrafted/agent-runs/init-20261010-020202-cccc/prompt.md"
    )
    staged = [json.loads(line) for line in stage.read_text().splitlines()]
    assert staged[-1]["path"].endswith("init-20261010-020202-cccc/prompt.md")
    assert str(repo.resolve()) not in staged[-1]["body"]
    meta = json.loads(
        (
            home
            / "control_plane"
            / "runtime_runs"
            / "init-20261010-020202-cccc"
            / "meta.json"
        ).read_text()
    )
    assert meta["status"] == "completed"
    assert meta["runtime_policy"] == "local-vm"
    assert meta["execution_environment"] == "local-container"
    assert meta["container"]["container_name"] == "vc-project-dev-1"
    assert meta["usage_measurement"]["source"] == "unmetered"
    # The provider never outlives its tab inside the container.
    teardown = [c["argv"] for c in calls if c["argv"][:2] == ["exec", "cid-project"]]
    assert any("provider.pid" in argv[4] for argv in teardown)
    assert "container_provider_teardown" in meta
    # Provider-owned evidence (the container's rollout) binds the conversation.
    assert meta["agent_session_id"] == "019a0000-0000-7000-8000-0000000c0dex"
    assert meta["native_identity_status"] == "proven"


def test_container_preparation_failure_is_visible_and_never_runs_on_host(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    repo = tmp_path / "project"
    _repo(repo)

    def refuse(_root: str, *, env: dict, log) -> dev_container.ContainerTarget:
        raise dev_container.ContainerError(
            "Docker daemon is not running (context colima-ci); start it with "
            "`colima start --profile ci`, then Launch again",
            stage="engine",
        )

    monkeypatch.setattr(dev_container, "ensure_container", refuse)
    monkeypatch.setattr(spawn, "_ask_container_retry", lambda _stage: False)
    # The transcript capture exists only around a provider child.
    monkeypatch.setattr(
        spawn,
        "InteractiveTranscriptCapture",
        lambda *_a, **_k: pytest.fail("a provider started without its container"),
    )

    status = launch_interactive_workspace(
        "claude",
        "/vc-init",
        "local-vm",
        "bypass",
        repo,
        token_budget="unmetered",
        admission={"run_id": "init-20261010-030303-dddd", "skill": "init"},
    )

    assert status == 1
    err = capsys.readouterr().err
    assert "Local container is not ready (engine)" in err
    assert "colima start --profile ci" in err
    meta = json.loads(
        (
            home
            / "control_plane"
            / "runtime_runs"
            / "init-20261010-030303-dddd"
            / "meta.json"
        ).read_text()
    )
    assert meta["status"] == "failed"
    assert meta["terminal_reason"] == "container_engine_failed"


def test_container_preparation_retry_then_cancel(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    attempts: list[int] = []

    def flaky(_root: str, *, env: dict, log) -> dev_container.ContainerTarget:
        attempts.append(1)
        if len(attempts) == 1:
            raise dev_container.ContainerError("build failed (exit 7)", stage="build")
        raise dev_container.ContainerCancelled()

    answers = iter([True])
    monkeypatch.setattr(dev_container, "ensure_container", flaky)
    monkeypatch.setattr(spawn, "_ask_container_retry", lambda _stage: next(answers))

    status = launch_interactive_workspace(
        "codex",
        "/vc-init",
        "local-vm",
        "bypass",
        repo,
        token_budget="unmetered",
        admission={"run_id": "init-20261010-040404-eeee", "skill": "init"},
    )

    assert len(attempts) == 2
    assert status == 130
    meta = json.loads(
        (
            home
            / "control_plane"
            / "runtime_runs"
            / "init-20261010-040404-eeee"
            / "meta.json"
        ).read_text()
    )
    assert meta["status"] == "cancelled"
    assert meta["terminal_reason"] == "container_prepare_cancelled"


def test_cli_entry_carries_the_container_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict = {}

    def capture(provider, prompt, runtime, *_args, **kwargs):
        seen.update(provider=provider, runtime=runtime)
        return 0

    monkeypatch.setattr(spawn, "launch_interactive_workspace", capture)
    assert (
        spawn.main(
            ["interactive-launch", "codex", "--runtime", "local-vm", "--root", "/tmp"]
        )
        == 0
    )
    assert seen == {"provider": "codex", "runtime": "local-vm"}


def test_policy_matrix_names_the_container_honestly() -> None:
    rows = [
        spawn.resolve_provider_policy(provider, "local-vm", "bypass", "interactive")
        for provider in spawn.POLICY_PROVIDERS
    ]
    assert {row.provider for row in rows if row.supported} == {
        "claude",
        "codex",
        "kimi",
    }
    assert all("VM entrypoint" not in row.reason for row in rows)


def test_shell_resume_runs_plain_in_this_tab() -> None:
    marbles = (
        Path(spawn.__file__).resolve().parent
        / "runtime"
        / "shell"
        / "lib"
        / "marbles.sh"
    ).read_text(encoding="utf-8")
    assert '""|headless|terminal|visible|plain)' in marbles
    assert (
        '_vetcoders_init_in_current_terminal "$tool" "$resume_cmd" plain resume'
        in marbles
    )


if sys.platform == "win32":  # pragma: no cover - POSIX PTY contract
    pytest.skip("POSIX only", allow_module_level=True)


def test_terminal_state_is_published_before_container_teardown(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "project"
    _repo(repo)
    target = _fake_target(tmp_path, repo)
    monkeypatch.setenv("FAKE_EXEC_LOG", str(tmp_path / "exec.jsonl"))
    monkeypatch.setenv("FAKE_EXEC_STAGE", str(tmp_path / "stage.jsonl"))
    monkeypatch.setattr(dev_container, "ensure_container", lambda *_a, **_k: target)
    order: list[str] = []
    terminalize = spawn._terminalize_interactive_launch

    def record_terminal(*args, **kwargs):
        order.append("terminal")
        return terminalize(*args, **kwargs)

    def record_teardown(*_args, **_kwargs):
        order.append("teardown")
        return "terminated"

    monkeypatch.setattr(spawn, "_terminalize_interactive_launch", record_terminal)
    monkeypatch.setattr(dev_container, "terminate_provider", record_teardown)

    status = launch_interactive_workspace(
        "codex",
        "/vc-init",
        "local-vm",
        "bypass",
        repo,
        token_budget="unmetered",
        admission={"run_id": "init-20261010-050505-ffff", "skill": "init"},
    )

    assert status == 0
    # A closed tab can hard-kill the owner after its hangup: the terminal
    # receipt must already exist when the slower container cleanup runs.
    assert order == ["terminal", "teardown"]
    meta = json.loads(
        (
            home
            / "control_plane"
            / "runtime_runs"
            / "init-20261010-050505-ffff"
            / "meta.json"
        ).read_text()
    )
    assert meta["status"] == "completed"
    assert meta["container_provider_teardown"] == "terminated"
