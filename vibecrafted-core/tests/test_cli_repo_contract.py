"""Public command contract, proven through the real CLI subprocess.

Every test here runs ``python -m vibecrafted_core.cli`` from a temporary
working directory that is NOT inside Git, with an isolated
``VIBECRAFTED_HOME`` and no inherited ``PYTHONPATH`` / ``PYTHONHOME``.
Provider workloads are stubbed with a fake ``claude`` on PATH; no real
session is ever touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).resolve().parents[1]


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


def _fake_claude(bin_dir: Path, argv_file: Path, cwd_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'printf "%s\\n" "$@" > {json.dumps(str(argv_file))}',
                f'printf "%s\\n" "$PWD" > {json.dumps(str(cwd_file))}',
                "cat >/dev/null",
                'printf \'{"type":"assistant","message":"ok"}\\n\'',
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _env(tmp_path: Path, fake_bin: Path | None = None) -> dict[str, str]:
    """Isolated environment: no runtime leak, no host PYTHONPATH, tmp home."""
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
    if fake_bin is not None:
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


def test_workflow_from_non_git_cwd_routes_repo_and_worktree(tmp_path: Path) -> None:
    """The Founder's command: --repo from outside Git, --worktree true, exact model."""
    repo = tmp_path / "repo with space"
    baseline = _git_repo(repo)
    outside = tmp_path / "no git here"
    outside.mkdir()
    fake_bin = tmp_path / "bin"
    argv_file = tmp_path / "claude-argv.txt"
    cwd_file = tmp_path / "claude-cwd.txt"
    _fake_claude(fake_bin, argv_file, cwd_file)
    env = _env(tmp_path, fake_bin)

    result = _cli(
        [
            "workflow",
            "claude",
            "--model",
            "claude-fable-5-1",
            "--worktree",
            "true",
            "--repo",
            str(repo),
            "--prompt",
            "Ujednolić i ustandaryzować polecenie fork",
            "--json",
        ],
        cwd=outside,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert receipt["worktree"] is True
    assert receipt["parent_root"] == str(repo.resolve())
    worktree = Path(receipt["root"])
    assert worktree == Path(receipt["worktree_path"])
    assert worktree.is_dir()
    assert worktree != repo.resolve()
    assert Path(env["VIBECRAFTED_HOME"]) in worktree.parents
    assert receipt["worktree_branch"] == f"cut/claude-{receipt['run_id']}"
    assert receipt["worktree_baseline_sha"] == baseline
    # The prompt argument boundary and the exact model survive the whole path.
    assert receipt["worker_command"][1:3] == ["--model", "claude-fable-5-1"]
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f"worktree {worktree}" in listing
    assert f"branch refs/heads/cut/claude-{receipt['run_id']}" in listing
    prompt = Path(receipt["prompt_file"]).read_text(encoding="utf-8")
    assert "Ujednolić i ustandaryzować polecenie fork" in prompt

    # Detached dispatcher: wait for the stub worker to run inside the checkout.
    deadline = 30.0
    while deadline > 0 and not cwd_file.exists():
        import time

        time.sleep(0.2)
        deadline -= 0.2
    assert cwd_file.exists(), "stub provider never ran"
    assert (
        Path(cwd_file.read_text(encoding="utf-8").strip()).resolve()
        == worktree.resolve()
    )
    worker_argv = argv_file.read_text(encoding="utf-8").splitlines()
    assert worker_argv[:2] == ["--model", "claude-fable-5-1"]
    meta = json.loads((Path(receipt["meta"])).read_text(encoding="utf-8"))
    assert meta["worktree"] is True
    assert meta["parent_root"] == str(repo.resolve())
    assert meta["worktree_branch"] == receipt["worktree_branch"]
    assert meta["model_requested"] == "claude-fable-5-1"


def test_worktree_false_and_legacy_root_run_in_the_selected_checkout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _git_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    fake_bin = tmp_path / "bin"
    _fake_claude(fake_bin, tmp_path / "argv.txt", tmp_path / "cwd.txt")
    env = _env(tmp_path, fake_bin)

    result = _cli(
        [
            "workflow",
            "claude",
            "--worktree",
            "false",
            "--root",
            str(repo),
            "--prompt",
            "legacy spelling",
            "--json",
        ],
        cwd=outside,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert receipt["root"] == str(repo.resolve())
    assert "worktree" not in receipt


def test_invalid_repo_is_refused_before_any_launch(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    env = _env(tmp_path)

    result = _cli(
        ["workflow", "claude", "--repo", str(tmp_path / "missing"), "--prompt", "x"],
        cwd=outside,
        env=env,
    )

    assert result.returncode == 2
    assert "--repo is not an existing directory" in result.stderr
    assert result.stdout == ""
    assert not (
        Path(env["VIBECRAFTED_HOME"]) / "control_plane" / "runtime_runs"
    ).exists()


def test_conflicting_repo_and_root_are_refused_not_silently_picked(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _git_repo(left)
    _git_repo(right)
    env = _env(tmp_path)

    result = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(left),
            "--root",
            str(right),
            "--prompt",
            "x",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 2
    assert "conflicting --repo" in result.stderr
    assert "--root" in result.stderr


def test_worktree_requires_a_git_repository_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_repo(repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    (repo / "sub").mkdir()
    env = _env(tmp_path)

    result = _cli(
        ["workflow", "claude", "--repo", str(plain), "--worktree", "--prompt", "x"],
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 2
    assert "not inside a Git repository" in result.stderr

    fake_bin = tmp_path / "bin"
    _fake_claude(fake_bin, tmp_path / "argv.txt", tmp_path / "cwd.txt")
    env = _env(tmp_path, fake_bin)
    result = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo / "sub"),
            "--worktree",
            "--prompt",
            "x",
            "--json",
        ],
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False
    assert receipt["reason"] == "worktree_rejected"
    assert receipt["status"] == "failed"
    assert "subdirectory" in receipt["error"]
    assert receipt["worktree"] is True
    assert receipt["parent_root"] == str((repo / "sub").resolve())


def test_worktree_refuses_a_dirty_selected_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_repo(repo)
    (repo / "dirty.txt").write_text("unstaged\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    _fake_claude(fake_bin, tmp_path / "argv.txt", tmp_path / "cwd.txt")
    env = _env(tmp_path, fake_bin)

    result = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo),
            "--worktree",
            "true",
            "--prompt",
            "x",
            "--json",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 1
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is False
    assert receipt["reason"] == "worktree_rejected"
    assert "clean selected workspace" in receipt["error"]
    listing = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert listing.count("worktree ") == 1


def test_bad_worktree_word_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_repo(repo)
    env = _env(tmp_path)

    result = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo),
            "--worktree",
            "maybe",
            "--prompt",
            "x",
        ],
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 2
    assert "--worktree expects true or false" in result.stderr


def test_repository_independent_commands_do_not_need_git(tmp_path: Path) -> None:
    """version/help/fork-source run from a non-git cwd with a failing git on PATH."""
    outside = tmp_path / "outside"
    outside.mkdir()
    broken_bin = tmp_path / "broken-bin"
    broken_bin.mkdir()
    git_stub = broken_bin / "git"
    git_stub.write_text(
        "#!/usr/bin/env bash\necho 'git: unavailable' >&2\nexit 127\n", encoding="utf-8"
    )
    git_stub.chmod(0o755)
    env = _env(tmp_path, broken_bin)

    version = _cli(["version"], cwd=outside, env=env)
    assert version.returncode == 0, version.stderr
    assert version.stdout.startswith("vibecrafted ")

    help_out = _cli(["help"], cwd=outside, env=env)
    assert help_out.returncode == 0, help_out.stderr
    assert "--repo <path>" in help_out.stdout
    assert "fork <agent>" in help_out.stdout

    workflow_help = _cli(["help", "workflow"], cwd=outside, env=env)
    assert workflow_help.returncode == 0
    assert "--repo <path>" in workflow_help.stdout
    assert "--worktree [true|false]" in workflow_help.stdout
    assert (
        "--model claude-fable-5-1 --worktree true --permissions auto --sandbox true "
        "--repo" in workflow_help.stdout
    )

    fork_source = _cli(
        [
            "fork-source",
            "cursor",
            "--session",
            "3f2b8c1e-0d2a-4f6b-9c1d-5e7a8b9c0d1e",
            "--json",
        ],
        cwd=outside,
        env=env,
    )
    assert fork_source.returncode == 2
    payload = json.loads(fork_source.stdout)
    assert payload["reason"] == "native_fork_unsupported"


def test_fork_source_identity_classification(tmp_path: Path) -> None:
    env = _env(tmp_path)
    home = Path(env["VIBECRAFTED_HOME"])
    run_id = "work-260908-120000-11111"
    provider = "3f2b8c1e-0d2a-4f6b-9c1d-5e7a8b9c0d1e"
    runtime = "0d1e5e7a-8b9c-4f6b-9c1d-3f2b8c1e0d2a"
    repo = tmp_path / "recorded"
    repo.mkdir()
    meta = {
        "run_id": run_id,
        "agent": "claude",
        "skill_code": "work",
        "status": "completed",
        "agent_session_id": provider,
        "runtime_session_id": runtime,
        "root": str(repo),
    }
    reports = home / "artifacts" / "local" / "recorded" / "2026_0908" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"{run_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    runtime_dir = home / "control_plane" / "runtime_runs" / run_id
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def source(*args: str) -> tuple[int, dict]:
        proc = _cli(["fork-source", *args, "--json"], cwd=tmp_path, env=env)
        return proc.returncode, json.loads(proc.stdout)

    rc, payload = source("claude", "--run-id", run_id)
    assert rc == 0
    assert payload["accepted"] is True
    assert payload["agent_session_id"] == provider
    assert payload["source_run_id"] == run_id
    assert payload["source_root"] == str(repo)
    assert payload["identity_source"] == "run_meta"

    rc, payload = source("claude", "--session", provider)
    assert rc == 0 and payload["identity_source"] == "recorded_session"
    assert payload["source_run_id"] == run_id

    rc, payload = source("claude", "--session", run_id)
    assert rc == 2 and payload["reason"] == "run_id_not_session"
    assert f"--run-id {run_id}" in payload["hint"]

    rc, payload = source("claude", "--run-id", provider)
    assert rc == 2 and payload["reason"] == "provider_session_not_run_id"

    rc, payload = source("claude", "--session", runtime)
    assert rc == 2 and payload["reason"] == "vibecrafted_session_not_provider_session"
    assert provider in payload["hint"]

    rc, payload = source("codex", "--run-id", run_id)
    assert rc == 2 and payload["reason"] == "agent_mismatch"

    rc, payload = source("claude", "--run-id", run_id, "--session", provider)
    assert rc == 2 and payload["reason"] == "conflicting_identity"

    rc, payload = source("claude")
    assert rc == 2 and payload["reason"] == "missing_identity"

    rc, payload = source("claude", "--run-id", "work-260908-999999-00000")
    assert rc == 2 and payload["reason"] == "run_not_found"

    # A run that never recorded a provider session is not silently replayed.
    bare = "work-260908-130000-22222"
    bare_meta = {
        "run_id": bare,
        "agent": "grok",
        "skill_code": "work",
        "status": "completed",
    }
    (reports / f"{bare}.meta.json").write_text(json.dumps(bare_meta), encoding="utf-8")
    rc, payload = source("grok", "--run-id", bare)
    assert rc == 2 and payload["reason"] == "no_provider_session"
    assert "resume grok --run-id" in payload["hint"]


def test_resume_session_and_agent_resume_accept_repo_and_refuse_conflicts(
    tmp_path: Path,
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    env = _env(tmp_path)

    result = _cli(
        [
            "resume-session",
            "codex",
            "--agent-session-id",
            "abc",
            "--prompt",
            "x",
            "--repo",
            str(left),
            "--root",
            str(right),
        ],
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 2
    assert "conflicting --repo" in result.stderr

    result = _cli(
        [
            "resume",
            "claude",
            "--run-id",
            "work-260908-120000-11111",
            "--repo",
            str(tmp_path / "nope"),
        ],
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 2
    assert "--repo is not an existing directory" in result.stderr


@pytest.mark.parametrize("command", ["cron tick", "paste", "workspace create"])
def test_other_public_commands_share_the_selector_words(
    tmp_path: Path, command: str
) -> None:
    env = _env(tmp_path)
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    argv = [*command.split(), "--repo", str(left), "--root", str(right)]
    if command == "paste":
        argv.append("--dry-run")

    result = _cli(argv, cwd=tmp_path, env=env)

    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "conflicting --repo" in result.stderr
