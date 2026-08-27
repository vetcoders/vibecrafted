from __future__ import annotations

import json
import multiprocessing
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from vibecrafted_core import cli as root_cli
from vibecrafted_core.repository_claims import (
    ClaimConflictError,
    ClaimContractError,
    RepositoryClaimRegistry,
)


def _init_repo(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "claims@vetcoders.io"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "claims-test"], cwd=path, check=True)
    (path / "src").mkdir()
    (path / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def _race_acquire(
    registry_root: str,
    repo: str,
    ready: multiprocessing.synchronize.Event,
    start: multiprocessing.synchronize.Event,
    output: multiprocessing.queues.Queue,
    name: str,
) -> None:
    registry = RepositoryClaimRegistry(
        root=Path(registry_root), grace_seconds=60, emit_events=False
    )
    ready.set()
    start.wait(10)
    try:
        result = registry.acquire(
            repo=repo,
            owned_paths=("src",) if name == "parent" else ("src/a.py",),
            run_id=f"run-{name}",
            session_id=f"session-{name}",
            agent=name,
        )
    except ClaimConflictError as exc:
        output.put((name, False, exc.result))
    else:
        output.put((name, True, result))


def _acquire_then_exit(registry_root: str, repo: str) -> None:
    RepositoryClaimRegistry(root=Path(registry_root), emit_events=False).acquire(
        repo=repo,
        owned_paths=("src/a.py",),
        run_id="dead-run",
        session_id="dead-session",
        agent="dead-agent",
    )


def test_parent_child_race_has_exactly_one_winner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    registry_root = tmp_path / "claims"
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    output = context.Queue()
    ready = [context.Event(), context.Event()]
    processes = [
        context.Process(
            target=_race_acquire,
            args=(str(registry_root), str(repo), ready[index], start, output, name),
        )
        for index, name in enumerate(("parent", "child"))
    ]
    for process in processes:
        process.start()
    for marker in ready:
        assert marker.wait(10)
    start.set()
    results = [output.get(timeout=10), output.get(timeout=10)]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    assert sum(1 for _name, won, _result in results if won) == 1
    refused = next(result for _name, won, result in results if not won)
    assert refused["conflicts"][0]["overlapping_paths"]


def test_non_overlapping_claims_coexist_and_live_owner_survives_zero_grace(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    registry = RepositoryClaimRegistry(
        root=tmp_path / "claims", grace_seconds=0, emit_events=False
    )
    first = registry.acquire(
        repo=repo,
        owned_paths=("src/a.py",),
        run_id="run-a",
        session_id="session-a",
        agent="codex",
    )
    second = registry.acquire(
        repo=repo,
        owned_paths=("tests",),
        run_id="run-b",
        session_id="session-b",
        agent="claude",
    )

    assert first["claim"]["claim_id"] != second["claim"]["claim_id"]
    with pytest.raises(ClaimConflictError):
        registry.acquire(
            repo=repo,
            owned_paths=("src",),
            run_id="run-c",
            session_id="session-c",
            agent="grok",
        )
    assert len(registry.list(repo=repo)["claims"]) == 2


def test_linked_worktrees_share_logical_repository_identity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    linked = tmp_path / "linked"
    _init_repo(repo)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "linked-test", str(linked)],
        cwd=repo,
        check=True,
    )
    registry = RepositoryClaimRegistry(root=tmp_path / "claims", emit_events=False)
    registry.acquire(
        repo=repo,
        owned_paths=("src",),
        run_id="main-run",
        session_id="main-session",
        agent="codex",
    )

    with pytest.raises(ClaimConflictError) as refused:
        registry.acquire(
            repo=linked,
            owned_paths=("src/a.py",),
            run_id="linked-run",
            session_id="linked-session",
            agent="claude",
        )

    assert refused.value.result["conflicts"][0]["run_id"] == "main-run"


def test_dead_owner_requires_grace_then_is_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    registry_root = home / "control_plane" / "repository_claims"
    context = multiprocessing.get_context("spawn")
    owner = context.Process(
        target=_acquire_then_exit, args=(str(registry_root), str(repo))
    )
    owner.start()
    owner.join(10)
    assert owner.exitcode == 0

    observed_at = datetime.now(UTC)
    before_grace = RepositoryClaimRegistry(
        root=registry_root,
        grace_seconds=10,
        now=lambda: observed_at,
        emit_events=True,
    )
    with pytest.raises(ClaimConflictError) as refused:
        before_grace.acquire(
            repo=repo,
            owned_paths=("src",),
            run_id="replacement",
            session_id="replacement-session",
            agent="codex",
        )
    assert refused.value.result["conflicts"][0]["owner_liveness"] == "dead"
    assert refused.value.result["conflicts"][0]["reclaimable"] is False

    after_grace = RepositoryClaimRegistry(
        root=registry_root,
        grace_seconds=10,
        now=lambda: observed_at + timedelta(seconds=11),
        emit_events=True,
    )
    acquired = after_grace.acquire(
        repo=repo,
        owned_paths=("src",),
        run_id="replacement",
        session_id="replacement-session",
        agent="codex",
    )
    assert acquired["ok"] is True
    assert acquired["reclaimed"][0]["claim"]["run_id"] == "dead-run"
    events = (home / "control_plane" / "events.jsonl").read_text(encoding="utf-8")
    assert "repository_mutation.conflict" in events
    assert "repository_mutation.reclaimed" in events


def test_release_is_immediate_idempotent_and_events_are_control_plane_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    registry = RepositoryClaimRegistry()
    acquired = registry.acquire(
        repo=repo,
        owned_paths=("src/a.py",),
        run_id="event-run",
        session_id="event-session",
        agent="codex",
    )
    claim_id = acquired["claim"]["claim_id"]

    released = registry.release(
        claim_id, run_id="event-run", session_id="event-session"
    )
    repeated = registry.release(
        claim_id, run_id="event-run", session_id="event-session"
    )

    assert released["released"] is True
    assert repeated["released"] is False
    events = (home / "control_plane" / "events.jsonl").read_text(encoding="utf-8")
    assert "repository_mutation.acquired" in events
    assert "repository_mutation.released" in events


def test_force_release_requires_reason_and_records_operator_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    registry = RepositoryClaimRegistry()
    claim_id = registry.acquire(
        repo=repo,
        owned_paths=("src",),
        run_id="owned-run",
        session_id="owned-session",
        agent="codex",
    )["claim"]["claim_id"]

    with pytest.raises(ClaimContractError, match="non-empty reason"):
        registry.release(
            claim_id,
            run_id="operator",
            session_id="operator",
            force=True,
        )
    result = registry.release(
        claim_id,
        run_id="operator",
        session_id="operator",
        force=True,
        reason="operator confirmed abandoned session",
    )

    assert result["action"] == "force-release"
    events = (home / "control_plane" / "events.jsonl").read_text(encoding="utf-8")
    assert '"forced": true' in events
    assert "operator confirmed abandoned session" in events


def test_public_cli_json_round_trip(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))

    assert (
        root_cli.main(
            [
                "claims",
                "--json",
                "acquire",
                "--repo",
                str(repo),
                "--run-id",
                "cli-run",
                "--session-id",
                "cli-session",
                "--agent",
                "codex",
                "src/a.py",
            ]
        )
        == 0
    )
    acquired = json.loads(capsys.readouterr().out)
    claim_id = acquired["claim"]["claim_id"]

    assert root_cli.main(["claims", "--json", "status", claim_id]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["claim"]["owner_liveness"] == "alive"

    assert (
        root_cli.main(
            [
                "claims",
                "--json",
                "heartbeat",
                claim_id,
                "--run-id",
                "cli-run",
                "--session-id",
                "cli-session",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["action"] == "heartbeat"

    assert root_cli.main(["claims", "--json", "list", "--repo", str(repo)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [claim["claim_id"] for claim in listing["claims"]] == [claim_id]

    assert (
        root_cli.main(
            [
                "claims",
                "--json",
                "release",
                claim_id,
                "--run-id",
                "cli-run",
                "--session-id",
                "cli-session",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["released"] is True
