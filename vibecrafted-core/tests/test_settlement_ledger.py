from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import control_plane, events, settlement_ledger
from vibecrafted_core.settlement import (
    SettlementEventStateV1,
    SettlementEventV2,
    TrustReceiptV1,
    emit_settlement_event,
)


def _event(
    *,
    run_id: str,
    revision: int,
    verdict: str,
    tui: str,
    previous_verdict: str | None = None,
    previous_tui: str | None = None,
    reason_suffix: str = "",
) -> SettlementEventV2:
    trust_verdict = {
        "finalized": "pass",
        "failed": "block",
        "needs_attention": "pass-with-gaps",
    }[verdict]
    claim_digest = hashlib.sha256(f"{run_id}:{revision}:{verdict}".encode()).hexdigest()
    receipt = TrustReceiptV1.issue(
        repo_root=str(Path(__file__).resolve().parent),
        run_id=run_id,
        commit_sha="a" * 40,
        trust_verdict=trust_verdict,
        settlement_verdict=verdict,
        settlement_tui=tui,
        settlement_revision=revision,
        claim_digest=claim_digest,
    )
    previous = (
        SettlementEventStateV1(
            verdict=previous_verdict,
            tui=str(previous_tui),
        )
        if previous_verdict is not None
        else None
    )
    return SettlementEventV2(
        run_id=run_id,
        previous=previous,
        current=SettlementEventStateV1(verdict=verdict, tui=tui),
        reason=f"trust_{trust_verdict.replace('-', '_')}:{revision}{reason_suffix}",
        source="trust",
        settled_at=f"2026-07-26T12:00:{revision:02d}+00:00",
        claim_digest=claim_digest,
        waived=False,
        revision=revision,
        trust_receipt=receipt,
    )


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "crafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    if sys.platform == "win32":
        # Snapshot fixtures precede ledger initialization. Create their root
        # with the production privacy policy rather than inherited temp ACLs.
        settlement_ledger._secure_control_plane_home()
    return home


def _write_snapshot(
    path: Path,
    *,
    run_id: str,
    tui: str,
    revision: int | None,
) -> None:
    settlement: dict[str, object] = {"tui": tui}
    if revision is not None:
        settlement["revision"] = revision
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_id": run_id, "settlement": settlement}) + "\n",
        encoding="utf-8",
    )


def test_initializer_freezes_deduplicated_snapshot_lower_bound() -> None:
    root = settlement_ledger.settlement_ledger_path().parent
    _write_snapshot(
        root / "runs" / "run-alpha.json",
        run_id="run-alpha",
        tui="n",
        revision=1,
    )
    _write_snapshot(
        root / "runs" / "archive" / "run-alpha.json",
        run_id="run-alpha",
        tui="n",
        revision=1,
    )
    _write_snapshot(
        root / "runs" / "run-beta.json",
        run_id="run-beta",
        tui="x",
        revision=None,
    )

    metadata = settlement_ledger.initialize_settlement_ledger()
    snapshot = settlement_ledger.read_settlement_ledger()

    assert metadata["history_origin"] == "observed_snapshot_lower_bound"
    assert metadata["backfill"]["observation_count"] == 2
    assert metadata["backfill"]["facts_invented"] is False
    assert (
        snapshot["integrity"]["historical_coverage"]
        == "observed_preledger_lower_bound_plus_v2"
    )
    assert snapshot["counts"]["historical_transitions"] == {
        "f": 0,
        "x": 1,
        "n": 1,
        "total": 2,
    }
    assert snapshot["counts"]["latest_by_run"] == {
        "f": 0,
        "x": 1,
        "n": 1,
        "total": 2,
    }

    settlement_ledger._append_settlement_fact(
        _event(
            run_id="run-alpha",
            revision=2,
            verdict="finalized",
            tui="f",
            previous_verdict="needs_attention",
            previous_tui="n",
        )
    )
    grown = settlement_ledger.read_settlement_ledger()
    assert grown["counts"]["historical_transitions"] == {
        "f": 1,
        "x": 1,
        "n": 1,
        "total": 3,
    }
    assert grown["counts"]["latest_by_run"] == {
        "f": 1,
        "x": 1,
        "n": 0,
        "total": 2,
    }
    assert grown["history_gaps"] == [
        {
            "kind": "preledger_history_unknown",
            "backfill_status": "observed_snapshot_lower_bound",
            "facts_invented": False,
        }
    ]


def test_first_v2_event_upgrades_matching_baseline_without_double_count() -> None:
    root = settlement_ledger.settlement_ledger_path().parent
    _write_snapshot(
        root / "runs" / "run-upgrade.json",
        run_id="run-upgrade",
        tui="n",
        revision=1,
    )
    event = _event(
        run_id="run-upgrade",
        revision=1,
        verdict="needs_attention",
        tui="n",
    )

    result = settlement_ledger._append_settlement_fact(event)
    snapshot = settlement_ledger.read_settlement_ledger()

    assert result.appended is True
    assert snapshot["counts"]["historical_transitions"] == {
        "f": 0,
        "x": 0,
        "n": 1,
        "total": 1,
    }
    assert snapshot["counts"]["latest_by_run"]["total"] == 1
    assert snapshot["historical_transitions"][0]["event_key"] == event.event_key


def test_exact_duplicate_is_idempotent_and_identity_collision_fails() -> None:
    original = _event(
        run_id="run-idempotent",
        revision=1,
        verdict="needs_attention",
        tui="n",
    )
    first = settlement_ledger._append_settlement_fact(original)
    duplicate = settlement_ledger._append_settlement_fact(original)

    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.ordinal == first.ordinal
    assert duplicate.record_hash == first.record_hash

    same_key_different_fact = _event(
        run_id="run-idempotent",
        revision=1,
        verdict="needs_attention",
        tui="n",
        reason_suffix=":changed",
    )
    assert same_key_different_fact.event_key == original.event_key
    with pytest.raises(
        settlement_ledger.SettlementLedgerCollision,
        match="event key collision",
    ):
        settlement_ledger._append_settlement_fact(same_key_different_fact)

    same_revision_different_receipt = _event(
        run_id="run-idempotent",
        revision=1,
        verdict="failed",
        tui="x",
    )
    with pytest.raises(
        settlement_ledger.SettlementLedgerCollision,
        match="revision collision",
    ):
        settlement_ledger._append_settlement_fact(same_revision_different_receipt)


def test_history_counts_transitions_without_overwriting_latest_by_run() -> None:
    transitions = [
        _event(
            run_id="run-alpha",
            revision=1,
            verdict="needs_attention",
            tui="n",
        ),
        _event(
            run_id="run-alpha",
            revision=2,
            verdict="finalized",
            tui="f",
            previous_verdict="needs_attention",
            previous_tui="n",
        ),
        _event(
            run_id="run-beta",
            revision=1,
            verdict="needs_attention",
            tui="n",
        ),
        _event(
            run_id="run-beta",
            revision=2,
            verdict="failed",
            tui="x",
            previous_verdict="needs_attention",
            previous_tui="n",
        ),
    ]
    for event in transitions:
        settlement_ledger._append_settlement_fact(event)

    snapshot = settlement_ledger.read_settlement_ledger()

    assert snapshot["counts"]["historical_transitions"] == {
        "f": 1,
        "x": 1,
        "n": 2,
        "total": 4,
    }
    assert snapshot["counts"]["latest_by_run"] == {
        "f": 1,
        "x": 1,
        "n": 0,
        "total": 2,
    }
    assert [item["event_key"] for item in snapshot["historical_transitions"]] == [
        event.event_key for event in transitions
    ]
    assert snapshot["latest_by_run"]["run-alpha"]["settlement_tui"] == "f"
    assert snapshot["latest_by_run"]["run-beta"]["settlement_tui"] == "x"
    assert snapshot["integrity"]["history_complete"] is False
    assert snapshot["metadata"]["backfill"] == {
        "status": "not_performed",
        "history_before_ledger": "unknown",
        "facts_invented": False,
    }
    assert snapshot["history_gaps"] == [
        {
            "kind": "preledger_history_unknown",
            "backfill_status": "not_performed",
            "facts_invented": False,
        }
    ]


def test_revision_gap_is_explicit_and_older_revision_is_rejected() -> None:
    settlement_ledger._append_settlement_fact(
        _event(
            run_id="run-gap",
            revision=3,
            verdict="needs_attention",
            tui="n",
        )
    )

    snapshot = settlement_ledger.read_settlement_ledger()
    assert snapshot["history_gaps"][1] == {
        "kind": "missing_settlement_revisions",
        "run_id": "run-gap",
        "from_revision": 1,
        "to_revision": 2,
        "count": 2,
    }

    with pytest.raises(
        settlement_ledger.SettlementLedgerOrderError,
        match="non-monotonic",
    ):
        settlement_ledger._append_settlement_fact(
            _event(
                run_id="run-gap",
                revision=2,
                verdict="failed",
                tui="x",
            )
        )


def test_normal_event_and_snapshot_retention_never_touches_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settlement_ledger._append_settlement_fact(
        _event(
            run_id="run-retention-proof",
            revision=1,
            verdict="needs_attention",
            tui="n",
        )
    )
    ledger_path = settlement_ledger.settlement_ledger_path()
    before = ledger_path.read_bytes()

    archive_dir = control_plane._events_archive_dir()
    archive_dir.mkdir(parents=True)
    old_event_archive = archive_dir / "events-old.jsonl"
    old_event_archive.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        control_plane, "_configured_events_archive_max_files", lambda: 0
    )
    monkeypatch.setattr(
        control_plane, "_configured_events_archive_max_bytes", lambda: 0
    )
    with control_plane._event_lock(exclusive=True):
        removed = control_plane._prune_event_archives_locked()
    control_plane._archive_expired_snapshots()

    assert removed == [old_event_archive]
    assert ledger_path.read_bytes() == before
    assert (
        settlement_ledger.read_settlement_ledger()["counts"]["historical_transitions"][
            "total"
        ]
        == 1
    )


def test_partial_tail_is_rolled_back_but_complete_corruption_fails_closed() -> None:
    first = _event(
        run_id="run-tail-a",
        revision=1,
        verdict="needs_attention",
        tui="n",
    )
    second = _event(
        run_id="run-tail-b",
        revision=1,
        verdict="failed",
        tui="x",
    )
    settlement_ledger._append_settlement_fact(first)
    path = settlement_ledger.settlement_ledger_path()
    with path.open("ab") as handle:
        handle.write(b'{"partial":')
        handle.flush()
        os.fsync(handle.fileno())

    settlement_ledger._append_settlement_fact(second)
    snapshot = settlement_ledger.read_settlement_ledger()
    assert snapshot["counts"]["historical_transitions"]["total"] == 2
    assert path.read_bytes().endswith(b"\n")

    lines = path.read_bytes().splitlines(keepends=True)
    corrupt = json.loads(lines[-1])
    corrupt["settlement_tui"] = "f"
    lines[-1] = (
        json.dumps(corrupt, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    path.write_bytes(b"".join(lines))
    before = path.read_bytes()

    with pytest.raises(settlement_ledger.SettlementLedgerCorrupt):
        settlement_ledger.read_settlement_ledger()
    with pytest.raises(settlement_ledger.SettlementLedgerCorrupt):
        settlement_ledger._append_settlement_fact(
            _event(
                run_id="run-tail-c",
                revision=1,
                verdict="finalized",
                tui="f",
            )
        )
    assert path.read_bytes() == before


def test_short_append_rolls_back_to_last_complete_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settlement_ledger._append_settlement_fact(
        _event(
            run_id="run-short-a",
            revision=1,
            verdict="needs_attention",
            tui="n",
        )
    )
    before = settlement_ledger.settlement_ledger_path().read_bytes()
    real_write = settlement_ledger.os.write

    def short_write(fd: int, data: bytes) -> int:
        partial = data[: max(len(data) // 2, 1)]
        return real_write(fd, partial)

    monkeypatch.setattr(settlement_ledger.os, "write", short_write)
    with pytest.raises(OSError, match="short settlement ledger append"):
        settlement_ledger._append_settlement_fact(
            _event(
                run_id="run-short-b",
                revision=1,
                verdict="failed",
                tui="x",
            )
        )
    monkeypatch.setattr(settlement_ledger.os, "write", real_write)

    assert settlement_ledger.settlement_ledger_path().read_bytes() == before
    assert (
        settlement_ledger.read_settlement_ledger()["counts"]["historical_transitions"][
            "total"
        ]
        == 1
    )


def test_concurrent_process_writers_preserve_one_valid_chain() -> None:
    child_code = """
import json
import sys
from vibecrafted_core.settlement import SettlementEventV2
from vibecrafted_core.settlement_ledger import _append_settlement_fact

_append_settlement_fact(SettlementEventV2.from_payload(json.loads(sys.argv[1])))
"""
    processes: list[subprocess.Popen[str]] = []
    for index in range(6):
        event = _event(
            run_id=f"run-concurrent-{index}",
            revision=1,
            verdict="needs_attention",
            tui="n",
        )
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    json.dumps(event.to_payload()),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, (stdout, stderr)

    snapshot = settlement_ledger.read_settlement_ledger()
    assert snapshot["integrity"]["valid"] is True
    assert snapshot["counts"]["historical_transitions"] == {
        "f": 0,
        "x": 0,
        "n": 6,
        "total": 6,
    }
    assert list(snapshot["latest_by_run"]) == [
        f"run-concurrent-{index}" for index in range(6)
    ]


def test_v2_emission_is_fail_closed_before_transient_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event(
        run_id="run-fail-closed",
        revision=1,
        verdict="failed",
        tui="x",
    )
    transient_calls: list[str] = []
    monkeypatch.setattr(
        settlement_ledger,
        "_append_settlement_fact",
        lambda _event: (_ for _ in ()).throw(OSError("ledger unavailable")),
    )
    monkeypatch.setattr(
        events,
        "append_event",
        lambda *_args, **_kwargs: transient_calls.append("published"),
    )

    with pytest.raises(OSError, match="ledger unavailable"):
        emit_settlement_event(event)
    assert transient_calls == []


def test_tail_repair_without_posix_pread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Native Windows lacks pread; recovery must still preserve the hash chain."""
    monkeypatch.delattr(os, "pread", raising=False)
    original = _event(run_id="run-portable-tail", revision=1, verdict="failed", tui="x")
    settlement_ledger._append_settlement_fact(original)
    path = settlement_ledger.settlement_ledger_path()
    with path.open("ab") as stream:
        stream.write(b'{"interrupted":')
    duplicate = settlement_ledger._append_settlement_fact(original)
    assert duplicate.appended is False
    snapshot = settlement_ledger.read_settlement_ledger()
    assert snapshot["integrity"]["valid"] is True
    assert snapshot["counts"]["historical_transitions"]["total"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL")
def test_windows_initializer_creates_private_custom_root(tmp_path: Path) -> None:
    path = tmp_path / "new-private-root" / "ledger.jsonl"
    settlement_ledger.initialize_settlement_ledger(path)
    snapshot = settlement_ledger.read_settlement_ledger(path)
    assert snapshot["integrity"]["valid"] is True
    assert snapshot["counts"]["historical_transitions"]["total"] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACL")
@pytest.mark.parametrize("target", ["home", "ledger", "lock"])
def test_windows_ledger_rejects_other_user_write_access(target: str) -> None:
    settlement_ledger.initialize_settlement_ledger()
    path = settlement_ledger.settlement_ledger_path()
    selected = {
        "home": path.parent,
        "ledger": path,
        "lock": path.with_name(".settlement_ledger.lock"),
    }[target]
    subprocess.run(
        ["icacls", str(selected), "/grant", "*S-1-1-0:(W)"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(PermissionError, match="writable by another principal"):
        settlement_ledger.read_settlement_ledger()
