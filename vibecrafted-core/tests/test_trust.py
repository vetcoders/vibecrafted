from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import textwrap
import threading
from pathlib import Path

import pytest
from vibecrafted_core import cli, control_plane, guard, trust, workflow
from vibecrafted_core.run_mutation import run_mutation_locks
from vibecrafted_core.settlement import TrustReceiptV1, board_fxn_counts
from vibecrafted_core.workflows import registry


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Trust Fixture")
    _git(repo, "config", "user.email", "agents@vetcoders.io")
    return repo


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    # GIT_EDITOR workaround for multi-line via -m multiple times
    parts = message.strip().split("\n\n", 1)
    subject = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""
    cmd = ["commit", "-m", subject]
    if body:
        cmd.extend(["-m", body])
    _git(repo, *cmd)
    return _git(repo, "rev-parse", "HEAD")


def _fair_message(agent: str = "codex") -> str:
    return textwrap.dedent(
        f"""\
        [{agent}/workflow] feat(trust): prove honest envelope

        Implements the focused path with pytest coverage on the real helper.

        Authored-By: {agent} <agents@vetcoders.io>
        session_id: 019e93be-379d-7303-9ad4-ffae468db99f
        time: 2026-07-25T12:00:00+02:00
        runtime: terminal
        """
    )


def _unfair_message() -> str:
    return textwrap.dedent(
        """\
        [claude/workflow] feat(trust): all done production ready

        Authored-By: codex <agents@vetcoders.io>
        Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
        session_id: 019e93be-379d-7303-9ad4-ffae468db99f
        time: 2026-07-25T12:00:00+02:00
        runtime: terminal
        """
    )


def test_registry_exposes_trust_as_read_only_and_guard_as_enforcer() -> None:
    definition = registry.workflow_definition("trust")

    assert definition is not None
    assert definition.cadence == "read"
    assert definition.can_modify_code is False
    assert definition.tooling[-1] == "vc-trust"

    guard_def = registry.workflow_definition("guard")
    assert guard_def is not None
    assert guard_def.cadence == "read"
    assert guard_def.can_modify_code is False
    assert "vc-guard" in guard_def.tooling

    assert {
        verdict: trust.tui_key_for(terminal)
        for verdict, terminal in trust.VERDICT_TO_SETTLEMENT.items()
    } == {
        "pass": "f",
        "pass-with-gaps": "n",
        "block": "x",
    }


def test_core_command_deck_exposes_trust_and_guard_launchers(capsys) -> None:
    assert cli.main(["trust", "--help"]) == 0
    trust_out = capsys.readouterr().out
    # Agent alternation mirrors the vc-trust SKILL.md invocation canon
    # (cursor joined the fleet with full parity in 8373c26f).
    assert "vibecrafted trust <claude|codex|agy|junie|grok|cursor>" in trust_out
    assert "version 1.0.0 · READ" in trust_out

    assert cli.main(["guard", "--help"]) == 0
    guard_out = capsys.readouterr().out
    assert "vibecrafted guard" in guard_out
    assert "READ" in guard_out


def test_note_appends_claim_evidence_and_projects_settlement(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _fair_message(), {"proof.txt": "proof\n"})
    crafted_home = tmp_path / "crafted"
    run_dir = crafted_home / "control_plane" / "runtime_runs" / "run-trust"
    run_dir.mkdir(parents=True)
    meta = run_dir / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "run-trust",
                "state": "completed",
                "exit_code": 0,
                "agent": "codex",
                "skill": "workflow",
            }
        ),
        encoding="utf-8",
    )
    snapshot = crafted_home / "control_plane" / "runs" / "run-trust.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "run_id": "run-trust",
                "state": "completed",
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    entry = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-trust",
        claims=[
            {
                "claim": "the focused test proves the path",
                "grade": "strong",
                "evidence": "negative fixture failed, then passed",
            }
        ],
    )

    assert entry["verdict"] == "pass-with-gaps"
    assert entry["settlement_tui"] == "n"
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    assert records == [entry]
    settled = json.loads(meta.read_text())
    assert settled["settlement_verdict"] == "needs_attention"
    assert settled["settlement_source"] == "trust"
    assert settled["settlement_tui"] == "n"
    assert board_fxn_counts([settled]) == {"f": 0, "x": 0, "n": 1}
    projected = json.loads(snapshot.read_text())
    assert projected["settlement_source"] == "trust"
    assert projected["settlement_revision"] == 1
    assert len(projected["settlement_claim_digest"]) == 64
    assert entry["schema"] == trust.TRUST_JOURNAL_SCHEMA_V2
    assert entry["claim_digest"] == projected["settlement_claim_digest"]
    assert entry["trust_receipt"] == settled["trust_receipt"]
    assert entry["trust_receipt"] == projected["trust_receipt"]
    assert board_fxn_counts([projected]) == {"f": 0, "x": 0, "n": 1}
    settlement_events = [
        json.loads(line)
        for line in (crafted_home / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(settlement_events) == 1
    event_payload = settlement_events[0]["payload"]
    assert event_payload["schema"] == "vibecrafted.settlement-event.v2"
    assert event_payload["run_id"] == "run-trust"
    assert event_payload["previous"] is None
    assert event_payload["current"] == {"verdict": "needs_attention", "tui": "n"}
    assert event_payload["reason"] == f"trust_pass_with_gaps:{sha}"
    assert event_payload["source"] == "trust"
    assert event_payload["settled_at"] == entry["recorded_at"]
    assert event_payload["claim_digest"] == projected["settlement_claim_digest"]
    assert event_payload["waived"] is False
    assert event_payload["revision"] == 1
    assert event_payload["trust_receipt"] == entry["trust_receipt"]
    assert event_payload["event_key"] == (
        f"run-trust:1:{entry['trust_receipt']['receipt_id']}"
    )

    # The explicit trust snapshot write follows sync_state; it must not publish
    # a second event for the same revision.
    trust.control_plane.sync_state(only_run_id="run-trust")
    replayed = [
        json.loads(line)
        for line in (crafted_home / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(replayed) == 1


def test_note_verdict_uses_shared_parent_mutation_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _fair_message(), {"proof.txt": "proof\n"})
    crafted_home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    attempted = threading.Event()
    persisted = threading.Event()
    original_locks = trust.run_mutation_locks

    def observed_locks(*args, **kwargs):
        attempted.set()
        return original_locks(*args, **kwargs)

    monkeypatch.setattr(trust, "run_mutation_locks", observed_locks)
    monkeypatch.setattr(
        trust,
        "_persist_trust_settlement",
        lambda **_kwargs: (
            persisted.set()
            or {
                "settlement_tui": "n",
            }
        ),
    )
    result: list[dict[str, object]] = []

    def record() -> None:
        result.append(
            trust.note_verdict(
                repo=repo,
                journal=tmp_path / "journal.jsonl",
                sha=sha,
                verdict="pass-with-gaps",
                claims=[
                    {
                        "claim": "serialized",
                        "grade": "strong",
                        "evidence": "shared mutation lock",
                    }
                ],
                run_id="run-serialized",
            )
        )

    with run_mutation_locks(
        trust.control_plane.control_plane_home(),
        run_id="run-serialized",
    ):
        worker = threading.Thread(target=record)
        worker.start()
        assert attempted.wait(timeout=5)
        assert persisted.is_set() is False
        assert worker.is_alive()

    worker.join(timeout=5)
    assert worker.is_alive() is False
    assert persisted.is_set() is True
    assert result[0]["settlement_tui"] == "n"


@pytest.mark.parametrize(
    "field",
    [
        "schema",
        "receipt_id",
        "repo_root",
        "run_id",
        "commit_sha",
        "trust_verdict",
        "settlement_verdict",
        "settlement_tui",
        "settlement_revision",
        "claim_digest",
    ],
)
def test_trust_receipt_rejects_mutation_of_every_bound_field(
    tmp_path: Path,
    field: str,
) -> None:
    receipt = TrustReceiptV1.issue(
        repo_root=str(tmp_path.resolve()),
        run_id="run-bound",
        commit_sha="a" * 40,
        trust_verdict="pass-with-gaps",
        settlement_verdict="needs_attention",
        settlement_tui="n",
        settlement_revision=4,
        claim_digest="b" * 64,
    )
    payload = receipt.to_payload()
    replacements: dict[str, object] = {
        "schema": "vibecrafted.trust-receipt.v0",
        "receipt_id": "c" * 64,
        "repo_root": str((tmp_path / "other").resolve()),
        "run_id": "other-run",
        "commit_sha": "d" * 40,
        "trust_verdict": "block",
        "settlement_verdict": "failed",
        "settlement_tui": "x",
        "settlement_revision": 5,
        "claim_digest": "e" * 64,
    }
    payload[field] = replacements[field]

    with pytest.raises((TypeError, ValueError)):
        TrustReceiptV1.from_payload(payload)


def _trust_run_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str,
) -> tuple[Path, str, Path, Path, Path]:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _fair_message(), {"proof.txt": "proof\n"})
    crafted_home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    run_dir = crafted_home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    meta = run_dir / "meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "state": "failed",
                "exit_code": 9,
                "agent": "codex",
                "skill": "implement",
                "root": str(repo),
                "worker_alive": False,
                "recovery_required": True,
            }
        ),
        encoding="utf-8",
    )
    snapshot = crafted_home / "control_plane" / "runs" / f"{run_id}.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "state": "failed",
                "exit_code": 9,
                "root": str(repo),
                "worker_alive": False,
                "recovery_required": True,
            }
        ),
        encoding="utf-8",
    )
    return repo, sha, tmp_path / "journal.jsonl", meta, snapshot


def _wait_for_child(pid: int, *, expected_exit: int) -> None:
    waited, status = os.waitpid(pid, 0)
    assert waited == pid
    assert os.waitstatus_to_exitcode(status) == expected_exit


def _fresh_process_recovery_report(report_path: Path) -> dict[str, object]:
    pid = os.fork()
    if pid == 0:  # pragma: no branch - the child never returns to pytest
        try:
            report = trust.recover_pending_trust_settlements()
            payload = {
                "scanned": report.scanned,
                "recovered": [
                    {
                        "run_id": item.run_id,
                        "receipt_id": item.receipt_id,
                        "settlement_revision": item.settlement_revision,
                    }
                    for item in report.recovered
                ],
                "errors": [
                    {
                        "outbox_path": item.outbox_path,
                        "run_id": item.run_id,
                        "error_type": item.error_type,
                        "message": item.message,
                        "retryable": item.retryable,
                    }
                    for item in report.errors
                ],
                "skipped": report.skipped,
                "truncated": report.truncated,
                "ok": report.ok,
            }
            report_path.write_text(json.dumps(payload), encoding="utf-8")
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            report_path.write_text(
                json.dumps(
                    {
                        "child_error": type(exc).__name__,
                        "message": str(exc),
                    }
                ),
                encoding="utf-8",
            )
            os._exit(98)
        os._exit(0)
    _wait_for_child(pid, expected_exit=0)
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_trust_transaction_orders_outbox_journal_projection_then_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta, snapshot = _trust_run_fixture(
        tmp_path, monkeypatch, run_id="run-order"
    )
    order: list[str] = []
    original_recover_journal = trust._recover_prepared_journal_entry
    original_write = trust.control_plane._write_json_durable
    original_publish = trust._publish_trust_event

    def recover_journal(path, payload, *, receipt_id):
        order.append("journal")
        return original_recover_journal(
            path,
            payload,
            receipt_id=receipt_id,
        )

    def write(path, payload):
        if "trust_settlement_outbox" in path.parts:
            order.append("outbox")
        elif path == meta:
            order.append("meta")
        elif path == snapshot:
            order.append("snapshot")
        return original_write(path, payload)

    def publish(event):
        order.append("event")
        return original_publish(event)

    monkeypatch.setattr(
        trust,
        "_recover_prepared_journal_entry",
        recover_journal,
    )
    monkeypatch.setattr(trust.control_plane, "_write_json_durable", write)
    monkeypatch.setattr(trust, "_publish_trust_event", publish)

    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-order",
        claims=[{"claim": "order", "grade": "strong", "evidence": "fsync"}],
    )

    assert order.index("outbox") < order.index("journal")
    assert order.index("journal") < order.index("meta")
    assert order.index("meta") < order.index("snapshot")
    assert order.index("snapshot") < order.index("event")


@pytest.mark.parametrize(
    "transition",
    ["outbox", "journal", "meta", "snapshot", "event"],
)
def test_trust_recovery_after_every_durable_transition_is_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    run_id = f"run-stop-{transition}"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": f"recover after {transition}",
            "grade": "strong",
            "evidence": "deterministic crash seam",
        }
    ]
    crashed = False

    def stop_after(durable_transition: str) -> None:
        nonlocal crashed
        if durable_transition == transition and not crashed:
            crashed = True
            raise OSError(f"stop after {transition}")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after)
    with pytest.raises(OSError, match=f"stop after {transition}"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )

    outbox_path = trust._trust_outbox_path(run_id)
    assert outbox_path.is_file()
    prepared = json.loads(outbox_path.read_text(encoding="utf-8"))
    receipt_id = prepared["trust_receipt"]["receipt_id"]
    revision = prepared["trust_receipt"]["settlement_revision"]
    assert revision == 1

    journal_records = trust._read_journal(journal)
    assert len(journal_records) == (0 if transition == "outbox" else 1)
    meta_before = json.loads(meta_path.read_text(encoding="utf-8"))
    snapshot_before = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if transition in {"meta", "snapshot", "event"}:
        assert meta_before["trust_receipt"]["receipt_id"] == receipt_id
    else:
        assert "trust_receipt" not in meta_before
    if transition in {"snapshot", "event"}:
        assert snapshot_before["trust_receipt"]["receipt_id"] == receipt_id
    else:
        assert "trust_receipt" not in snapshot_before

    stream = tmp_path / "crafted" / "control_plane" / "events.jsonl"
    before_events = (
        [
            json.loads(line)
            for line in stream.read_text(encoding="utf-8").splitlines()
            if json.loads(line).get("kind") == "settlement.changed"
        ]
        if stream.is_file()
        else []
    )
    assert len(before_events) == (1 if transition == "event" else 0)
    if transition == "event":
        assert prepared["published_event_key"] == (f"{run_id}:{revision}:{receipt_id}")

    monkeypatch.setattr(trust, "_after_trust_transition", lambda _transition: None)
    recovered = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id=run_id,
        claims=claims,
    )

    assert recovered["trust_receipt"]["receipt_id"] == receipt_id
    assert recovered["trust_receipt"]["settlement_revision"] == revision
    assert not outbox_path.exists()
    assert trust._read_journal(journal) == [recovered]
    for path in (meta_path, snapshot_path):
        projection = json.loads(path.read_text(encoding="utf-8"))
        assert projection["trust_receipt"]["receipt_id"] == receipt_id
        assert projection["settlement_revision"] == revision
    after_events = [
        json.loads(line)
        for line in stream.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(after_events) == 1
    assert after_events[0]["payload"]["event_key"] == (
        f"{run_id}:{revision}:{receipt_id}"
    )


def test_trust_recovery_clears_stale_snapshot_await_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-stale-await-projection"
    repo, sha, journal, _meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )

    def stop_after_outbox(transition: str) -> None:
        if transition == "outbox":
            raise OSError("stop after prepared outbox")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after_outbox)
    with pytest.raises(OSError, match="stop after prepared outbox"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=[
                {
                    "claim": "stale await projection is superseded",
                    "grade": "strong",
                    "evidence": "canonical recovery writer",
                }
            ],
        )

    outbox_path = trust._trust_outbox_path(run_id)
    outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot.update(outbox["projection_fields"])
    snapshot.update(
        {
            "await_rc": 0,
            "await_outcome": "completed",
            "await_settled_at": "2026-08-26T10:00:00Z",
        }
    )
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _name: None)

    report = trust.recover_pending_trust_settlements()

    assert report.ok is True
    assert [item.run_id for item in report.recovered] == [run_id]
    assert not outbox_path.exists()
    recovered = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert recovered["await_rc"] is None
    assert recovered["await_outcome"] == ""
    assert recovered["await_settled_at"] == ""
    assert len(trust._read_journal(journal)) == 1
    stream = tmp_path / "crafted" / "control_plane" / "events.jsonl"
    events = [
        json.loads(line)
        for line in stream.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1


@pytest.mark.parametrize("transition", ["outbox", "journal", "meta", "snapshot"])
def test_fresh_process_sweep_recovers_after_hard_exit_at_each_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition: str,
) -> None:
    run_id = f"run-hard-exit-{transition}"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": f"hard exit after {transition}",
            "grade": "strong",
            "evidence": "fresh process recovery sweep",
        }
    ]
    exit_code = 80
    pid = os.fork()
    if pid == 0:  # pragma: no branch - the child never returns to pytest

        def stop_after(durable_transition: str) -> None:
            if durable_transition == transition:
                os._exit(exit_code)

        trust._after_trust_transition = stop_after
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )
        os._exit(97)
    _wait_for_child(pid, expected_exit=exit_code)

    outbox_path = trust._trust_outbox_path(run_id)
    prepared = json.loads(outbox_path.read_text(encoding="utf-8"))
    expected_receipt = prepared["trust_receipt"]
    report = _fresh_process_recovery_report(
        tmp_path / f"recovery-report-{transition}.json"
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["recovered"] == [
        {
            "run_id": run_id,
            "receipt_id": expected_receipt["receipt_id"],
            "settlement_revision": expected_receipt["settlement_revision"],
        }
    ]
    assert not outbox_path.exists()
    records = trust._read_journal(journal)
    assert len(records) == 1
    assert records[0]["trust_receipt"] == expected_receipt
    for projection_path in (meta_path, snapshot_path):
        projection = json.loads(projection_path.read_text(encoding="utf-8"))
        assert projection["trust_receipt"] == expected_receipt
        assert projection["settlement_revision"] == 1
    events = [
        json.loads(line)
        for line in (tmp_path / "crafted" / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["trust_receipt"] == expected_receipt


def test_trust_crash_after_projection_recovers_same_receipt_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, _meta, snapshot = _trust_run_fixture(
        tmp_path, monkeypatch, run_id="run-crash"
    )
    claims = [{"claim": "recover", "grade": "strong", "evidence": "outbox"}]
    original_publish = trust._publish_trust_event
    monkeypatch.setattr(
        trust,
        "_publish_trust_event",
        lambda _event: (_ for _ in ()).throw(OSError("crash after snapshot")),
    )

    with pytest.raises(OSError, match="crash after snapshot"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id="run-crash",
            claims=claims,
        )

    projected = json.loads(snapshot.read_text(encoding="utf-8"))
    receipt_id = projected["trust_receipt"]["receipt_id"]
    outbox = trust._trust_outbox_path("run-crash")
    assert outbox.is_file()
    monkeypatch.setattr(trust, "_publish_trust_event", original_publish)

    recovered = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-crash",
        claims=claims,
    )

    assert recovered["trust_receipt"]["receipt_id"] == receipt_id
    assert not outbox.exists()
    events = [
        json.loads(line)
        for line in (tmp_path / "crafted" / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
        and json.loads(line).get("payload", {}).get("schema")
        == "vibecrafted.settlement-event.v2"
    ]
    assert [event["payload"]["trust_receipt"]["receipt_id"] for event in events] == [
        receipt_id
    ]


@pytest.mark.parametrize(
    "run_id",
    ['run-quote-"', "run-backslash-\\", "run-newline-\n-mid"],
    ids=["quoted", "backslash", "newline"],
)
def test_event_replay_deduplicates_json_escaped_event_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run_id: str,
) -> None:
    repo, sha, journal, _meta, _snapshot = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "escaped event key remains exactly once",
            "grade": "strong",
            "evidence": "publish-before-ack replay",
        }
    ]
    original_publish = trust._publish_trust_event
    published_once = False

    def publish_then_crash(event):
        nonlocal published_once
        result = original_publish(event)
        if not published_once:
            published_once = True
            raise OSError("crash after publish before acknowledgement")
        return result

    monkeypatch.setattr(trust, "_publish_trust_event", publish_then_crash)
    with pytest.raises(OSError, match="before acknowledgement"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )
    outbox_path = trust._trust_outbox_path(run_id)
    assert outbox_path.is_file()
    monkeypatch.setattr(trust, "_publish_trust_event", original_publish)

    recovered = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id=run_id,
        claims=claims,
    )

    assert not outbox_path.exists()
    events = [
        json.loads(line)
        for line in (tmp_path / "crafted" / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["run_id"] == run_id
    assert events[0]["payload"]["event_key"] == (
        f"{run_id}:1:{recovered['trust_receipt']['receipt_id']}"
    )


def test_publish_must_be_exactly_observable_before_outbox_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-post-publish-verification"
    repo, sha, journal, _meta, _snapshot = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "publish is verified before ack",
            "grade": "strong",
            "evidence": "empty publisher leaves prepared outbox",
        }
    ]
    original_publish = trust._publish_trust_event
    monkeypatch.setattr(trust, "_publish_trust_event", lambda _event: {})

    with pytest.raises(OSError, match="event durability verification failed"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )

    outbox_path = trust._trust_outbox_path(run_id)
    outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    assert not outbox.get("published_event_key")
    event_path = tmp_path / "crafted" / "control_plane" / "events.jsonl"
    assert not event_path.exists()

    monkeypatch.setattr(trust, "_publish_trust_event", original_publish)
    report = trust.recover_pending_trust_settlements()

    assert report.ok
    assert [item.run_id for item in report.recovered] == [run_id]
    assert not outbox_path.exists()
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1


def test_hard_exit_after_unterminated_event_repairs_and_republishes_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-unterminated-event"
    repo, sha, journal, _meta, _snapshot = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "unterminated event is not an acknowledgement",
            "grade": "strong",
            "evidence": "hard exit after durable JSON before newline",
        }
    ]
    exit_code = 82
    pid = os.fork()
    if pid == 0:  # pragma: no branch - the child never returns to pytest

        def write_unterminated_event_then_exit(event):
            control_plane._ensure_event_segment()
            previous = event.previous.verdict if event.previous else "unsettled"
            record = {
                "ts": control_plane._now().isoformat(),
                "run_id": event.run_id,
                "kind": "settlement.changed",
                "message": (
                    f"settlement revision {event.revision}: "
                    f"{previous} -> {event.current.verdict}"
                ),
                "payload": event.to_payload(),
            }
            encoded = json.dumps(record, ensure_ascii=False).encode("utf-8")
            with control_plane._event_lock(exclusive=True):
                flags = os.O_RDWR | os.O_APPEND
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(control_plane.event_stream_path(), flags)
                control_plane._repair_incomplete_event_tail_locked(descriptor)
                offset = 0
                while offset < len(encoded):
                    written = os.write(descriptor, encoded[offset:])
                    if written <= 0:
                        os._exit(96)
                    offset += written
                os.fsync(descriptor)
                os._exit(exit_code)

        trust._publish_trust_event = write_unterminated_event_then_exit
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )
        os._exit(97)
    _wait_for_child(pid, expected_exit=exit_code)

    outbox_path = trust._trust_outbox_path(run_id)
    prepared = json.loads(outbox_path.read_text(encoding="utf-8"))
    expected_event_key = prepared["event"]["event_key"]
    event_path = control_plane.event_stream_path()
    crashed_stream = event_path.read_bytes()
    assert not crashed_stream.endswith(b"\n")
    assert json.loads(crashed_stream.splitlines()[-1])["payload"]["event_key"] == (
        expected_event_key
    )

    report = _fresh_process_recovery_report(
        tmp_path / "unterminated-event-recovery-report.json"
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert not outbox_path.exists()
    control_plane._append_event(
        {
            "ts": control_plane._now().isoformat(),
            "run_id": "unrelated-run",
            "kind": "worker.heartbeat",
            "message": "force tail repair boundary",
            "payload": {},
        }
    )
    repaired_stream = event_path.read_bytes()
    assert repaired_stream.endswith(b"\n")
    events = [
        json.loads(line)
        for line in repaired_stream.splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["event_key"] == expected_event_key


def test_acknowledged_outbox_republishes_event_missing_from_retention_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-acknowledged-event-pruned"
    repo, sha, journal, _meta, _snapshot = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "acknowledgement never outranks retained event truth",
            "grade": "strong",
            "evidence": "missing acknowledged event is republished",
        }
    ]

    def stop_after_ack(transition: str) -> None:
        if transition == "event":
            raise OSError("stop after event acknowledgement")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after_ack)
    with pytest.raises(OSError, match="after event acknowledgement"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )

    outbox_path = trust._trust_outbox_path(run_id)
    acknowledged = json.loads(outbox_path.read_text(encoding="utf-8"))
    event_key = acknowledged["published_event_key"]
    assert event_key
    event_path = tmp_path / "crafted" / "control_plane" / "events.jsonl"
    retained = [
        raw
        for raw in event_path.read_text(encoding="utf-8").splitlines()
        if json.loads(raw).get("kind") != "settlement.changed"
    ]
    event_path.write_text("\n".join(retained) + "\n", encoding="utf-8")
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _name: None)

    report = trust.recover_pending_trust_settlements()

    assert report.ok
    assert [item.run_id for item in report.recovered] == [run_id]
    assert not outbox_path.exists()
    events = [
        json.loads(line)
        for line in event_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["event_key"] == event_key


def test_trust_missing_snapshot_recovers_full_meta_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id="run-missing-snapshot",
    )
    snapshot_path.unlink()

    entry = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-missing-snapshot",
        claims=[
            {
                "claim": "snapshot materialization",
                "grade": "strong",
                "evidence": "fresh runtime meta",
            }
        ],
    )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["state"] == "failed"
    assert snapshot["agent"] == "codex"
    assert snapshot["skill"] == "implement"
    assert snapshot["exit_code"] == 9
    assert snapshot["trust_receipt"] == entry["trust_receipt"]
    assert snapshot["trust_receipt"] == meta["trust_receipt"]


def test_trust_recovery_refuses_nonexact_journal_before_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id="run-journal-conflict",
    )
    monkeypatch.setattr(
        trust,
        "_after_trust_transition",
        lambda transition: (
            (_ for _ in ()).throw(OSError("stop after outbox"))
            if transition == "outbox"
            else None
        ),
    )
    claims = [
        {
            "claim": "exact journal authority",
            "grade": "strong",
            "evidence": "conflicting same-receipt record",
        }
    ]
    with pytest.raises(OSError, match="stop after outbox"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id="run-journal-conflict",
            claims=claims,
        )
    outbox = json.loads(
        trust._trust_outbox_path("run-journal-conflict").read_text(encoding="utf-8")
    )
    conflicting = dict(outbox["journal_entry"])
    conflicting["verdict"] = "block"
    trust._append_jsonl(journal, conflicting)
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _transition: None)

    with pytest.raises(
        ValueError,
        match="journal receipt missing or mismatched",
    ):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id="run-journal-conflict",
            claims=claims,
        )

    assert "trust_receipt" not in json.loads(meta_path.read_text(encoding="utf-8"))
    assert "trust_receipt" not in json.loads(snapshot_path.read_text(encoding="utf-8"))
    event_stream = tmp_path / "crafted" / "control_plane" / "events.jsonl"
    assert not event_stream.exists()


@pytest.mark.parametrize(
    ("path_field", "error"),
    [
        ("journal", "journal path mismatch"),
        ("meta_path", "meta path mismatch"),
        ("snapshot_path", "snapshot path mismatch"),
    ],
)
def test_trust_recovery_refuses_outbox_path_redirection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path_field: str,
    error: str,
) -> None:
    run_id = f"run-path-{path_field}"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    crashed = False

    def stop_after_outbox(transition: str) -> None:
        nonlocal crashed
        if transition == "outbox" and not crashed:
            crashed = True
            raise OSError("stop after outbox")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after_outbox)
    claims = [{"claim": "path binding", "grade": "strong", "evidence": path_field}]
    with pytest.raises(OSError, match="stop after outbox"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )

    outbox_path = trust._trust_outbox_path(run_id)
    outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    victim = tmp_path / f"victim-{path_field}.json"
    victim.write_text(json.dumps({"application": "unrelated"}), encoding="utf-8")
    outbox[path_field] = str(victim.resolve())
    outbox_path.write_text(json.dumps(outbox), encoding="utf-8")
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _transition: None)

    with pytest.raises(ValueError, match=error):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )

    assert json.loads(victim.read_text(encoding="utf-8")) == {
        "application": "unrelated"
    }
    assert "trust_receipt" not in json.loads(meta_path.read_text(encoding="utf-8"))
    assert "trust_receipt" not in json.loads(snapshot_path.read_text(encoding="utf-8"))


def test_trust_recovery_upgrades_pending_4a9_v1_projection_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id="run-v1-upgrade",
    )
    crashed = False

    def stop_after_outbox(transition: str) -> None:
        nonlocal crashed
        if transition == "outbox" and not crashed:
            crashed = True
            raise OSError("stop after outbox")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after_outbox)
    claims = [{"claim": "v1 upgrade", "grade": "strong", "evidence": "4a9 plan"}]
    with pytest.raises(OSError, match="stop after outbox"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id="run-v1-upgrade",
            claims=claims,
        )

    outbox_path = trust._trust_outbox_path("run-v1-upgrade")
    outbox = json.loads(outbox_path.read_text(encoding="utf-8"))
    for key in ("run_id", "root", "repo_root", "commit_sha"):
        outbox["projection_fields"].pop(key)
    outbox_path.write_text(json.dumps(outbox), encoding="utf-8")
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _transition: None)

    recovered = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-v1-upgrade",
        claims=claims,
    )

    assert recovered["trust_receipt"]["settlement_revision"] == 1
    for path in (meta_path, snapshot_path):
        projection = json.loads(path.read_text(encoding="utf-8"))
        assert projection["run_id"] == "run-v1-upgrade"
        assert projection["root"] == str(repo.resolve())
        assert projection["repo_root"] == str(repo.resolve())
        assert projection["commit_sha"] == sha


def test_journal_reader_reports_concurrent_partial_append_as_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = tmp_path / "journal.jsonl"
    trust._append_jsonl(journal, {"record": "before"})
    first_chunk_written = threading.Event()
    release_writer = threading.Event()
    writer_errors: list[BaseException] = []
    real_write = trust._journal_write
    calls = 0

    def paused_short_write(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            split = max(1, len(data) // 2)
            written = real_write(descriptor, data[:split])
            first_chunk_written.set()
            assert release_writer.wait(timeout=5)
            return written
        return real_write(descriptor, data)

    monkeypatch.setattr(trust, "_journal_write", paused_short_write)

    def append_unrelated() -> None:
        try:
            trust._append_jsonl(journal, {"record": "unrelated"})
        except (OSError, AssertionError) as exc:  # pragma: no cover
            writer_errors.append(exc)

    writer = threading.Thread(target=append_unrelated)
    writer.start()
    assert first_chunk_written.wait(timeout=5)
    with pytest.raises(trust.TrustJournalRetryable, match="busy"):
        trust._read_journal(journal)
    release_writer.set()
    writer.join(timeout=5)

    assert writer.is_alive() is False
    assert writer_errors == []
    assert trust._read_journal(journal) == [
        {"record": "before"},
        {"record": "unrelated"},
    ]


def test_journal_reader_reports_unterminated_tail_as_retryable(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'{"record":"partial"')

    with pytest.raises(trust.TrustJournalRetryable, match="partial tail"):
        trust._read_journal(journal)


def test_journal_write_error_after_short_write_rolls_back_exact_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = tmp_path / "journal.jsonl"
    trust._append_jsonl(journal, {"record": "stable"})
    before = journal.read_bytes()
    real_write = trust._journal_write
    calls = 0

    def short_then_error(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, data[:7])
        raise OSError("injected journal write failure")

    monkeypatch.setattr(trust, "_journal_write", short_then_error)
    with pytest.raises(OSError, match="injected journal write failure"):
        trust._append_jsonl(journal, {"record": "must-not-tear"})

    assert journal.read_bytes() == before
    assert trust._read_journal(journal) == [{"record": "stable"}]


def test_journal_write_all_completes_repeated_short_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = tmp_path / "journal.jsonl"
    real_write = trust._journal_write

    def always_short(descriptor: int, data: bytes) -> int:
        return real_write(descriptor, data[: max(1, len(data) // 3)])

    monkeypatch.setattr(trust, "_journal_write", always_short)
    trust._append_jsonl(journal, {"record": "complete"})

    assert trust._read_journal(journal) == [{"record": "complete"}]


def test_hard_exit_during_prepared_append_repairs_only_exact_torn_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-torn-prepared-append"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "repair exact torn append",
            "grade": "strong",
            "evidence": "hard os._exit inside journal write",
        }
    ]
    exit_code = 81
    pid = os.fork()
    if pid == 0:  # pragma: no branch - the child never returns to pytest
        real_write = trust._journal_write

        def write_prefix_then_exit(descriptor: int, data: bytes) -> int:
            prefix_size = max(1, len(data) // 2)
            real_write(descriptor, data[:prefix_size])
            os.fsync(descriptor)
            os._exit(exit_code)

        trust._journal_write = write_prefix_then_exit
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )
        os._exit(97)
    _wait_for_child(pid, expected_exit=exit_code)

    outbox_path = trust._trust_outbox_path(run_id)
    prepared = json.loads(outbox_path.read_text(encoding="utf-8"))
    expected_entry = prepared["journal_entry"]
    expected_receipt = prepared["trust_receipt"]
    torn = journal.read_bytes()
    expected_encoded = (
        json.dumps(expected_entry, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert torn
    assert not torn.endswith(b"\n")
    assert expected_encoded.startswith(torn)

    report = _fresh_process_recovery_report(tmp_path / "torn-recovery-report.json")

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["recovered"] == [
        {
            "run_id": run_id,
            "receipt_id": expected_receipt["receipt_id"],
            "settlement_revision": 1,
        }
    ]
    assert trust._read_journal(journal) == [expected_entry]
    assert not outbox_path.exists()
    for path in (meta_path, snapshot_path):
        projection = json.loads(path.read_text(encoding="utf-8"))
        assert projection["trust_receipt"] == expected_receipt
    events = [
        json.loads(line)
        for line in (tmp_path / "crafted" / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["trust_receipt"] == expected_receipt


def test_hard_exit_after_complete_prepared_json_finishes_newline_exactly_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-complete-prepared-without-newline"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    claims = [
        {
            "claim": "finish exact prepared record",
            "grade": "strong",
            "evidence": "hard os._exit after every JSON byte before newline",
        }
    ]
    exit_code = 83
    pid = os.fork()
    if pid == 0:  # pragma: no branch - the child never returns to pytest
        real_write = trust._journal_write

        def write_record_without_newline_then_exit(
            descriptor: int,
            data: bytes,
        ) -> int:
            record = data[:-1]
            offset = 0
            while offset < len(record):
                written = real_write(descriptor, record[offset:])
                if written <= 0:
                    os._exit(96)
                offset += written
            os.fsync(descriptor)
            os._exit(exit_code)

        trust._journal_write = write_record_without_newline_then_exit
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=claims,
        )
        os._exit(97)
    _wait_for_child(pid, expected_exit=exit_code)

    outbox_path = trust._trust_outbox_path(run_id)
    prepared = json.loads(outbox_path.read_text(encoding="utf-8"))
    expected_entry = prepared["journal_entry"]
    expected_receipt = prepared["trust_receipt"]
    expected_encoded = (
        json.dumps(expected_entry, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert journal.read_bytes() == expected_encoded[:-1]

    report = _fresh_process_recovery_report(
        tmp_path / "complete-prepared-recovery-report.json"
    )

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["recovered"] == [
        {
            "run_id": run_id,
            "receipt_id": expected_receipt["receipt_id"],
            "settlement_revision": 1,
        }
    ]
    assert journal.read_bytes() == expected_encoded
    assert trust._read_journal(journal) == [expected_entry]
    assert not outbox_path.exists()
    for path in (meta_path, snapshot_path):
        projection = json.loads(path.read_text(encoding="utf-8"))
        assert projection["trust_receipt"] == expected_receipt
    events = [
        json.loads(line)
        for line in (tmp_path / "crafted" / "control_plane" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if json.loads(line).get("kind") == "settlement.changed"
    ]
    assert len(events) == 1
    assert events[0]["payload"]["trust_receipt"] == expected_receipt


@pytest.mark.parametrize(
    "foreign_tail",
    [
        b"not-json\n",
        b'{"record":"complete-foreign"}',
        b'{"record":',
    ],
    ids=["complete-corrupt", "complete-unterminated", "foreign-partial"],
)
def test_recovery_refuses_corrupt_or_foreign_journal_tail_without_deleting_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_tail: bytes,
) -> None:
    run_id = "run-refuse-foreign-tail"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )

    def stop_after_outbox(transition: str) -> None:
        if transition == "outbox":
            raise OSError("stop after prepared outbox")

    monkeypatch.setattr(trust, "_after_trust_transition", stop_after_outbox)
    with pytest.raises(OSError, match="stop after prepared outbox"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=[
                {
                    "claim": "foreign tail is never ours to erase",
                    "grade": "strong",
                    "evidence": "fail-closed recovery report",
                }
            ],
        )
    outbox_path = trust._trust_outbox_path(run_id)
    journal.write_bytes(foreign_tail)
    journal.chmod(0o600)
    monkeypatch.setattr(trust, "_after_trust_transition", lambda _name: None)

    report = trust.recover_pending_trust_settlements()

    assert report.scanned == 1
    assert report.recovered == ()
    assert len(report.errors) == 1
    assert report.errors[0].run_id == run_id
    assert report.errors[0].retryable is False
    assert journal.read_bytes() == foreign_tail
    assert outbox_path.is_file()
    assert "trust_receipt" not in json.loads(meta_path.read_text(encoding="utf-8"))
    assert "trust_receipt" not in json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert not (tmp_path / "crafted" / "control_plane" / "events.jsonl").exists()


def test_recovery_sweep_is_bounded_and_reports_non_authority_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crafted_home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    outbox_dir = trust._trust_outbox_dir()
    outbox_dir.mkdir(parents=True)
    (outbox_dir / "foreign-one.json").write_text("{}", encoding="utf-8")
    (outbox_dir / "foreign-two.json").write_text("{}", encoding="utf-8")

    report = trust.recover_pending_trust_settlements(limit=1)

    assert report.scanned == 1
    assert report.truncated is True
    assert report.recovered == ()
    assert len(report.errors) == 1
    assert "filename invalid" in report.errors[0].message
    assert report.ok is False


def test_recovery_sweep_refuses_symlinked_outbox_without_touching_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crafted_home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    outbox_dir = trust._trust_outbox_dir()
    outbox_dir.mkdir(parents=True)
    victim = tmp_path / "victim.json"
    victim.write_text('{"application":"unrelated"}\n', encoding="utf-8")
    victim.chmod(0o600)
    symlink = outbox_dir / f"{'a' * 64}.json"
    symlink.symlink_to(victim)

    report = trust.recover_pending_trust_settlements()

    assert report.scanned == 1
    assert report.recovered == ()
    assert len(report.errors) == 1
    assert report.errors[0].error_type == "OSError"
    assert victim.read_text(encoding="utf-8") == '{"application":"unrelated"}\n'
    assert symlink.is_symlink()


def test_note_refuses_symlinked_outbox_directory_before_prepared_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-symlinked-outbox-dir"
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id=run_id,
    )
    outbox_dir = trust._trust_outbox_dir()
    victim = tmp_path / "foreign-outbox-dir"
    victim.mkdir()
    outbox_dir.symlink_to(victim, target_is_directory=True)

    with pytest.raises(PermissionError, match="not canonical"):
        trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict="pass-with-gaps",
            run_id=run_id,
            claims=[
                {
                    "claim": "prepared authority stays in its exact directory",
                    "grade": "strong",
                    "evidence": "symlink target remains untouched",
                }
            ],
        )

    assert list(victim.iterdir()) == []
    assert not journal.exists()
    assert "trust_receipt" not in json.loads(meta_path.read_text(encoding="utf-8"))
    assert "trust_receipt" not in json.loads(snapshot_path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("limit", [0, trust.TRUST_RECOVERY_MAX_LIMIT + 1])
def test_recovery_sweep_rejects_unbounded_limits(limit: int) -> None:
    with pytest.raises(ValueError, match="recovery limit"):
        trust.recover_pending_trust_settlements(limit=limit)


def test_guardian_resume_authority_is_exact_and_legacy_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path, monkeypatch, run_id="run-authority"
    )
    entry = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-authority",
        claims=[{"claim": "resume", "grade": "strong", "evidence": "exact"}],
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    receipt_id = entry["trust_receipt"]["receipt_id"]

    allowed = guard.authorize_guardian_resume(
        run_id="run-authority",
        repo=repo,
        journal=journal,
        meta=meta,
        projection=snapshot,
        expected_receipt_id=receipt_id,
    )
    assert allowed.allowed is True
    assert allowed.receipt_id == receipt_id

    for field in entry["trust_receipt"]:
        mismatched = dict(snapshot)
        mismatched["trust_receipt"] = dict(snapshot["trust_receipt"])
        mismatched["trust_receipt"][field] = "mismatch"
        denied = guard.authorize_guardian_resume(
            run_id="run-authority",
            repo=repo,
            journal=journal,
            meta=meta,
            projection=mismatched,
            expected_receipt_id=receipt_id,
        )
        assert denied.allowed is False, field
        assert denied.retryable is False, field

    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text(
        json.dumps(
            {
                "schema": "vibecrafted.trust-journal.v1",
                "repo_root": str(repo.resolve()),
                "sha": sha,
                "verdict": "pass-with-gaps",
                "settlement_tui": "n",
                "run_id": "run-authority",
                "claims": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    denied_legacy = guard.authorize_guardian_resume(
        run_id="run-authority",
        repo=repo,
        journal=legacy,
        meta=meta,
        projection=snapshot,
    )
    assert denied_legacy.allowed is False
    assert denied_legacy.reason == "legacy_trust_record_not_resume_authority"
    assert denied_legacy.terminal is True


@pytest.mark.parametrize("field", ["run_id", "root", "repo_root", "commit_sha"])
@pytest.mark.parametrize("target", ["meta", "projection"])
def test_guardian_resume_denies_live_top_level_authority_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    target: str,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id="run-live-mismatch",
    )
    entry = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-live-mismatch",
        claims=[{"claim": "resume", "grade": "strong", "evidence": "live fields"}],
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    projection = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mutated = meta if target == "meta" else projection
    mutated[field] = (
        str((tmp_path / "different-repo").resolve())
        if field in {"root", "repo_root"}
        else ("different-run" if field == "run_id" else "f" * 40)
    )

    decision = guard.authorize_guardian_resume(
        run_id="run-live-mismatch",
        repo=repo,
        journal=journal,
        meta=meta,
        projection=projection,
        expected_receipt_id=entry["trust_receipt"]["receipt_id"],
    )

    assert decision.allowed is False
    assert decision.retryable is False
    assert decision.reason == f"{target}_{field}_mismatch"


def test_guardian_resume_treats_busy_journal_as_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, sha, journal, meta_path, snapshot_path = _trust_run_fixture(
        tmp_path,
        monkeypatch,
        run_id="run-busy-journal",
    )
    entry = trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass-with-gaps",
        run_id="run-busy-journal",
        claims=[{"claim": "resume", "grade": "strong", "evidence": "busy journal"}],
    )
    descriptor = os.open(journal, os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        decision = guard.authorize_guardian_resume(
            run_id="run-busy-journal",
            repo=repo,
            journal=journal,
            meta=json.loads(meta_path.read_text(encoding="utf-8")),
            projection=json.loads(snapshot_path.read_text(encoding="utf-8")),
            expected_receipt_id=entry["trust_receipt"]["receipt_id"],
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert decision.allowed is False
    assert decision.reason == "trust_journal_busy"
    assert decision.retryable is True
    assert decision.terminal is False


def test_enumerate_skips_commits_already_in_append_only_journal(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _fair_message(), {"proof.txt": "proof\n"})
    journal = tmp_path / "journal.jsonl"
    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass",
        claims=[{"claim": "fixture", "grade": "strong", "evidence": "direct"}],
    )

    assert trust.enumerate_commits(repo=repo, journal=journal) == []
    assert (
        trust.enumerate_commits(repo=repo, journal=journal, include_noted=True)[0][
            "sha"
        ]
        == sha
    )


def test_triage_uses_latest_verdict_per_repo_and_commit() -> None:
    records = [
        {"repo_root": "/repo", "sha": "a", "settlement_tui": "x"},
        {"repo_root": "/repo", "sha": "a", "settlement_tui": "f"},
        {"repo_root": "/repo", "sha": "b", "settlement_tui": "n"},
        {"repo_root": "/other", "sha": "a", "settlement_tui": "x"},
    ]

    result = trust.triage_records(records)

    assert result["counts"] == {"f": 1, "x": 1, "n": 1}
    assert result["commits"] == 3


def test_claim_grade_and_evidence_are_one_to_one() -> None:
    args = argparse.Namespace(
        claim=["one", "two"],
        grade=["strong"],
        evidence=["proof"],
    )

    try:
        trust._claims_from_args(args)
    except ValueError as exc:
        assert "each --claim" in str(exc)
    else:
        raise AssertionError("mismatched claim evidence must fail closed")


def test_inspect_flags_agent_fairness_breach_and_never_auto_passes(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    # Root commit: must list real files (diff-tree --root) and must not invent
    # an empty-envelope gap when the commit actually added paths.
    fair_sha = _commit(repo, _fair_message("codex"), {"proof.txt": "proof\n"})
    unfair_sha = _commit(repo, _unfair_message(), {"bad.txt": "bad\n"})

    fair = trust.extract_fairness_and_completeness_claims(repo=repo, sha=fair_sha)
    unfair = trust.extract_fairness_and_completeness_claims(repo=repo, sha=unfair_sha)

    assert fair["subject_agent"] == "codex"
    assert fair["authored_by_agent"] == "codex"
    assert fair["recommended_verdict"] in {"pass-with-gaps", "pass"}
    # Format alone must not become pass without runtime evidence.
    assert fair["recommended_verdict"] == "pass-with-gaps"
    assert fair["failures"] == []
    assert fair["files"] == ["proof.txt"]
    assert not any("empty envelope" in g for g in fair["gaps"])
    assert not any(
        "zero paths" in str(c.get("evidence", ""))
        for c in fair["claims"]
        if isinstance(c, dict)
    )

    assert unfair["recommended_verdict"] == "block"
    assert any("fairness" in f for f in unfair["failures"])
    assert any("vendor" in f for f in unfair["failures"])
    assert any(
        "fairness" in c["claim"] for c in unfair["claims"] if isinstance(c, dict)
    )
    # Non-root unfair commit also lists its real file.
    assert unfair["files"] == ["bad.txt"]


def test_inspect_root_commit_lists_files_via_diff_tree_root(tmp_path: Path) -> None:
    """Regression: root commits must not look like empty envelopes."""
    repo = _init_repo(tmp_path)
    sha = _commit(
        repo,
        _fair_message("grok"),
        {"proof.txt": "root-proof\n", "nested/a.py": "print(1)\n"},
    )
    files = trust._commit_files(repo, sha)
    assert "proof.txt" in files
    assert "nested/a.py" in files

    inspect = trust.extract_fairness_and_completeness_claims(repo=repo, sha=sha)
    assert set(inspect["files"]) == {"proof.txt", "nested/a.py"}
    assert not any("empty envelope" in g for g in inspect["gaps"])
    assert inspect["recommended_verdict"] == "pass-with-gaps"
    assert inspect["failures"] == []


def test_inspect_flags_foreign_unclaimed_envelope_files(tmp_path: Path) -> None:
    """Message claims a scoped path set while the envelope smuggles foreign files."""
    repo = _init_repo(tmp_path)
    # Baseline root so the unfair commit is a non-root multi-file envelope.
    _commit(repo, _fair_message("codex"), {"README.md": "base\n"})

    foreign_message = textwrap.dedent(
        """\
        [codex/workflow] feat(trust): touch only core/owned.py

        Updates `core/owned.py` with the focused helper path and pytest coverage.

        Authored-By: codex <agents@vetcoders.io>
        session_id: 019e93be-379d-7303-9ad4-ffae468db99f
        time: 2026-07-25T12:00:00+02:00
        runtime: terminal
        """
    )
    sha = _commit(
        repo,
        foreign_message,
        {
            "core/owned.py": "owned = True\n",
            "secrets/other_agent.env": "FOREIGN=1\n",
            "unrelated/smuggled.txt": "nope\n",
        },
    )

    inspect = trust.extract_fairness_and_completeness_claims(repo=repo, sha=sha)

    assert "core/owned.py" in inspect["files"]
    assert "secrets/other_agent.env" in inspect["files"]
    assert "unrelated/smuggled.txt" in inspect["files"]
    assert inspect["recommended_verdict"] in {"pass-with-gaps", "block"}
    assert any("foreign unclaimed" in g for g in inspect["gaps"])
    assert any(
        "foreign unclaimed" in c["claim"]
        for c in inspect["claims"]
        if isinstance(c, dict)
    )
    # Named evidence must list the smuggled paths.
    foreign_claim = next(
        c
        for c in inspect["claims"]
        if isinstance(c, dict) and "foreign unclaimed" in c["claim"]
    )
    assert "secrets/other_agent.env" in foreign_claim["evidence"]
    assert "unrelated/smuggled.txt" in foreign_claim["evidence"]


def test_inspect_flags_claimed_paths_absent_from_envelope(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    message = textwrap.dedent(
        """\
        [grok/workflow] fix(runtime): repair never_exists.py

        Fixes `never_exists.py` and documents the gate in docs/missing.md.

        Authored-By: grok <agents@vetcoders.io>
        session_id: 019e93be-379d-7303-9ad4-ffae468db99f
        time: 2026-07-25T12:00:00+02:00
        runtime: terminal
        """
    )
    sha = _commit(repo, message, {"actually_touched.txt": "x\n"})
    inspect = trust.extract_fairness_and_completeness_claims(repo=repo, sha=sha)

    assert inspect["files"] == ["actually_touched.txt"]
    assert inspect["recommended_verdict"] == "block"
    assert any("absent from the commit envelope" in f for f in inspect["failures"])
    assert any(
        "message-claimed paths are present" in c["claim"]
        for c in inspect["claims"]
        if isinstance(c, dict)
    )


def test_inspect_cli_drives_real_entry_point(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _unfair_message(), {"x.txt": "x\n"})
    rc = trust.main(["--repo", str(repo), "inspect", sha])
    assert rc == 0


def test_git_log_range_never_feeds_failed_rev_to_since(tmp_path: Path) -> None:
    """Unresolvable since (root^) must fail open — never become --since=<rev>."""
    repo = _init_repo(tmp_path)
    root = _commit(repo, _fair_message("codex"), {"root.txt": "r\n"})
    child = _commit(repo, _fair_message("codex"), {"child.txt": "c\n"})

    # Valid parent → exclusive range args.
    assert trust._git_log_range(repo, root) == [f"{root}..HEAD"]

    # Root has no parent: root^ fails rev-parse → no lower bound (not --since).
    bad = trust._git_log_range(repo, root + "^")
    assert bad == []
    assert not any(a.startswith("--since=") for a in bad)

    # ISO run-meta dates still use the date filter (await-primary boundary).
    assert trust._git_log_range(repo, "2026-07-25T00:00:00+00:00") == [
        "--since=2026-07-25T00:00:00+00:00"
    ]

    # Empty since → no bound; enumerate must still see both commits.
    empty = trust.enumerate_commits(
        repo=repo, journal=tmp_path / "empty.jsonl", since=""
    )
    shas = {c["sha"] for c in empty}
    assert root in shas and child in shas

    # Unresolvable rev fails open: still lists existing commits.
    open_range = trust.enumerate_commits(
        repo=repo, journal=tmp_path / "empty2.jsonl", since=root + "^"
    )
    open_shas = {c["sha"] for c in open_range}
    assert root in open_shas and child in open_shas


def test_await_primary_lists_candidates_and_does_not_auto_note(
    tmp_path: Path, monkeypatch
) -> None:
    repo = _init_repo(tmp_path)
    # Two commits so since can be a real parent baseline (deterministic).
    parent = _commit(repo, _fair_message("codex"), {"parent.txt": "p\n"})
    sha = _commit(repo, _fair_message("codex"), {"a.txt": "a\n"})
    journal = tmp_path / "journal.jsonl"
    crafted_home = tmp_path / "crafted"
    run_dir = crafted_home / "control_plane" / "runtime_runs" / "run-await"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(
        json.dumps(
            {
                "run_id": "run-await",
                "state": "completed",
                "exit_code": 0,
                "started_at": "2026-07-25T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    def _fake_await(run_id: str, **_kwargs):
        return {
            "completed": True,
            "reason": "completed",
            "await_rc": 0,
            "await_outcome": "completed",
        }

    monkeypatch.setattr(trust.control_plane, "await_run", _fake_await)

    # Parent..HEAD range: child is a candidate; parent is the lower bound.
    result = trust.await_primary(
        run_id="run-await",
        repo=repo,
        journal=journal,
        since=parent,
        interval=0.1,
        timeout=2.0,
    )

    assert result["schema"] == "vibecrafted.trust-await-primary.v1"
    assert result["await"]["completed"] is True
    candidate_shas = [c["sha"] for c in result["candidate_commits"]]
    assert sha in candidate_shas
    assert parent not in candidate_shas
    # No auto-note: journal must still be empty
    assert not journal.is_file() or journal.read_text().strip() == ""

    # Root-only fail-open path: unresolvable since=HEAD^ must still list HEAD.
    solo_base = tmp_path / "solo"
    solo_base.mkdir()
    solo = _init_repo(solo_base)
    solo_sha = _commit(solo, _fair_message("grok"), {"solo.txt": "s\n"})
    solo_journal = tmp_path / "solo-journal.jsonl"
    solo_run = crafted_home / "control_plane" / "runtime_runs" / "run-await-solo"
    solo_run.mkdir(parents=True)
    (solo_run / "meta.json").write_text(
        json.dumps(
            {
                "run_id": "run-await-solo",
                "state": "completed",
                "exit_code": 0,
                "started_at": "2026-07-25T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    solo_result = trust.await_primary(
        run_id="run-await-solo",
        repo=solo,
        journal=solo_journal,
        since=solo_sha + "^",
        interval=0.1,
        timeout=2.0,
    )
    assert any(c["sha"] == solo_sha for c in solo_result["candidate_commits"])
    assert not solo_journal.is_file() or solo_journal.read_text().strip() == ""


def test_guard_inventory_and_block_enforcement(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _unfair_message(), {"z.txt": "z\n"})
    journal = tmp_path / "journal.jsonl"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "crafted"))

    inv = guard.inventory()
    assert inv["schema"] == "vibecrafted.guard-inventory.v1"
    assert any(g["id"] == "trust-block-dispatch" for g in inv["gates"])
    assert inv["doctrine"]["settlement_authority"].startswith("vc-trust")

    open_decision = guard.enforce_continuation(repo=repo, journal=journal)
    assert open_decision.allowed is True

    inspect = trust.extract_fairness_and_completeness_claims(repo=repo, sha=sha)
    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="block",
        claims=inspect["claims"][:2]
        or [
            {
                "claim": "agent fairness",
                "grade": "strong",
                "evidence": "subject != Authored-By",
            }
        ],
    )

    blocked = guard.enforce_continuation(repo=repo, journal=journal, sha=sha)
    assert blocked.allowed is False
    assert blocked.blocking_verdict == "block"
    assert "Remedium" in blocked.remedium
    assert blocked.blocking_sha.startswith(sha[:8]) or blocked.blocking_sha == sha

    # pass after block: append newer verdict for same sha (latest wins)
    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="pass",
        claims=[
            {
                "claim": "runtime proof",
                "grade": "strong",
                "evidence": "negative fixture then green",
            }
        ],
    )
    allowed = guard.enforce_continuation(repo=repo, journal=journal, sha=sha)
    assert allowed.allowed is True

    # CLI exit codes
    assert guard.main(["inventory"]) == 0
    # After pass, check allows
    assert (
        guard.main(
            ["--repo", str(repo), "--journal", str(journal), "check", "--sha", sha]
        )
        == 0
    )


def test_launch_workflow_refuses_on_trust_block(tmp_path: Path, monkeypatch) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _unfair_message(), {"w.txt": "w\n"})
    journal = tmp_path / "journal.jsonl"
    crafted = tmp_path / "crafted"
    crafted.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "1")
    monkeypatch.setattr(trust, "default_journal_path", lambda: journal)

    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="block",
        claims=[
            {
                "claim": "agent fairness",
                "grade": "strong",
                "evidence": "subject != Authored-By",
            }
        ],
    )

    # Patch guard to use our journal when launch_workflow imports it
    real_enforce = guard.enforce_continuation

    def _enforce(**kwargs):
        kwargs = {**kwargs, "journal": journal, "repo": repo}
        return real_enforce(**kwargs)

    monkeypatch.setattr(guard, "enforce_continuation", _enforce)

    from vibecrafted_core.workflow import WorkflowLaunchSpec

    spec = WorkflowLaunchSpec(
        skill="implement",
        agent="codex",
        mode="prompt",
        file="",
        runtime="headless",
        root=str(repo),
        prompt="should be refused",
    )
    try:
        workflow.launch_workflow(spec, source_dir=repo)
    except ValueError as exc:
        assert "vc-guard" in str(exc) or "Remedium" in str(exc) or "block" in str(exc)
    else:
        raise AssertionError("launch_workflow must refuse on trust block")


def test_settlement_mapping_closed_pass_f_gaps_n_block_x(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    sha = _commit(repo, _fair_message(), {"m.txt": "m\n"})
    journal = tmp_path / "j.jsonl"
    for verdict, letter in (
        ("pass", "f"),
        ("pass-with-gaps", "n"),
        ("block", "x"),
    ):
        entry = trust.note_verdict(
            repo=repo,
            journal=journal,
            sha=sha,
            verdict=verdict,
            claims=[
                {
                    "claim": f"map {verdict}",
                    "grade": "strong",
                    "evidence": "unit",
                }
            ],
        )
        assert entry["settlement_tui"] == letter
    triage = trust.triage_records(
        [json.loads(line) for line in journal.read_text().splitlines()]
    )
    # latest wins for same sha — last was block → x only for that sha
    assert triage["counts"]["x"] == 1
    assert triage["commits"] == 1
