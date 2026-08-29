"""Contract for the one caretaker truth.

Each test here guards a property whose loss silently restores the multi-source
fusion this envelope replaced: a verdict that disagrees with its own header, a
stale receipt read as a running server, a corrupt plane rendered as healthy, or
a second resume classifier drifting away from the one in ``init_resume``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from vibecrafted_core import caretaker


def _plane(tmp_path: Path) -> Path:
    """A minimal but structurally real control plane."""
    plane = tmp_path / "control_plane"
    for child in ("runs", "runtime_runs", "lifecycle_runs"):
        (plane / child).mkdir(parents=True)
    (plane / "events.jsonl").write_text("", encoding="utf-8")
    return plane


def _receipt(tmp_path: Path, **overrides: object) -> Path:
    """Write a supervisor receipt under a fake crafted home; return that home."""
    home = tmp_path / "crafted"
    (home / "server").mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "vibecrafted.server-supervisor.v1",
        "state": "healthy",
        "supervisor_pid": 4242,
        "service_managed": True,
        "endpoint": {
            "host": "127.0.0.1",
            "port": 3024,
            "url": "http://127.0.0.1:3024",
            "public_url": "http://127.0.0.1:3024",
        },
        "managed_pair": {"guardian_pid": 11, "server_pid": 12},
        "last_error": None,
        "consecutive_failures": 0,
    }
    payload.update(overrides)
    (home / "server" / "supervisor.status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return home


def _reachable(
    monkeypatch: pytest.MonkeyPatch, *, reachable: bool, reason: str = ""
) -> None:
    """Pin the liveness probe; no test may depend on a real listening port."""
    monkeypatch.setattr(
        caretaker,
        "probe_health",
        lambda origin, **_: {
            "origin": origin,
            "reachable": reachable,
            "reason": reason,
            "version": "4.3.0" if reachable else "",
        },
    )


def test_verdict_header_and_health_never_disagree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A demoted verdict must demote its header too.

    The first draft of this module reported ``health: degraded`` under a header
    that still read ``HEALTHY`` — the exact two-truths defect the envelope
    exists to remove, reproduced inside the fix. A menu renders the header; a
    script branches on the health. They must never say different things.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)
    _reachable(monkeypatch, reachable=True)
    # One corrupt snapshot is enough to make upkeep non-silent.
    (plane / "runs" / "broken.json").write_text("{not json", encoding="utf-8")

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)
    verdict = snapshot["verdict"]

    assert verdict["health"] != caretaker.HEALTHY
    assert "HEALTHY ·" in verdict["header"], verdict["header"]
    assert "upkeep item" in verdict["header"]
    # The server leg itself is fine; only the plane is not. Both stay legible.
    assert verdict["server_health"] == caretaker.HEALTHY


def test_serving_endpoint_with_stale_receipt_is_not_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt nobody refreshes is not evidence of a supervised server.

    The tray previously read ``supervisor.status.json`` with no age check, so a
    supervisor that died left a file still saying ``healthy`` next to a
    live-looking endpoint. Freshness is part of the truth or it is not truth.
    """
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)
    _reachable(monkeypatch, reachable=True)

    receipt = home / "server" / "supervisor.status.json"
    stale = os.stat(receipt).st_mtime - (caretaker.RECEIPT_STALE_SECONDS + 60)
    os.utime(receipt, (stale, stale))

    snapshot = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)

    assert snapshot["server"]["receipt"]["stale"] is True
    assert snapshot["verdict"]["health"] == caretaker.DEGRADED
    assert "RECEIPT STALE" in snapshot["verdict"]["header"]
    codes = {finding["code"] for finding in snapshot["verdict"]["findings"]}
    assert "supervisor_receipt_stale" in codes


def test_unreachable_endpoint_is_never_rendered_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No receipt state may override a silent port."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path, state="healthy")
    _reachable(
        monkeypatch, reachable=False, reason="ConnectionRefusedError: [Errno 61]"
    )

    verdict = caretaker.build_caretaker_snapshot(home=home, control_plane=plane)[
        "verdict"
    ]

    assert verdict["health"] == caretaker.UNAVAILABLE
    assert "UNREACHABLE" in verdict["header"]
    assert {f["code"] for f in verdict["findings"]} >= {"server_unreachable"}


def test_unprobed_liveness_is_unknown_not_healthy(tmp_path: Path) -> None:
    """A snapshot built without probing must say ``unknown``, never guess up."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    assert snapshot["server"]["liveness"]["probed"] is False
    assert snapshot["verdict"]["health"] == caretaker.UNKNOWN


def test_missing_receipt_degrades_without_raising(tmp_path: Path) -> None:
    """A supervisor that never ran is a reported condition, not an exception."""
    plane = _plane(tmp_path)
    home = tmp_path / "empty-home"
    home.mkdir()

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    server = snapshot["server"]
    assert server["available"] is False
    assert "not published" in server["receipt"]["reason"]
    assert snapshot["verdict"]["health"] != caretaker.HEALTHY


def test_missing_control_plane_is_an_error_finding(tmp_path: Path) -> None:
    """An absent plane must be loud; silence would read as a clean plane."""
    absent = tmp_path / "no-such-plane"

    section = caretaker.build_maintenance_section(control_plane=absent)

    assert section["available"] is False
    assert [finding["code"] for finding in section["findings"]] == [
        "control_plane_missing"
    ]
    assert section["findings"][0]["severity"] == caretaker.ERROR


def test_maintenance_names_orphans_and_corruption(tmp_path: Path) -> None:
    """Upkeep findings are computed from the plane, not asserted from config."""
    plane = _plane(tmp_path)
    (plane / "runtime_runs" / "with-meta").mkdir()
    (plane / "runtime_runs" / "with-meta" / "meta.json").write_text(
        "{}", encoding="utf-8"
    )
    (plane / "runtime_runs" / "orphan-a").mkdir()
    (plane / "runtime_runs" / "orphan-b").mkdir()
    (plane / "runs" / "good.json").write_text('{"run_id": "good"}', encoding="utf-8")
    (plane / "runs" / "broken.json").write_text("{not json", encoding="utf-8")

    section = caretaker.build_maintenance_section(control_plane=plane)

    assert section["scanned"] == 3
    assert section["orphan_runtime_runs"] == 2
    assert section["corrupt_run_snapshots"] == 1
    by_code = {finding["code"]: finding for finding in section["findings"]}
    assert by_code["orphan_runtime_runs"]["count"] == 2
    assert by_code["corrupt_run_snapshots"]["severity"] == caretaker.ERROR
    # Three retained directories is not retention pressure.
    assert "runtime_run_retention" not in by_code


def test_event_stream_pressure_is_reported(tmp_path: Path) -> None:
    """Rotation debt shows up before it makes unrelated commands slow."""
    plane = _plane(tmp_path)
    (plane / "events.jsonl").write_bytes(
        b"x" * (caretaker.EVENT_STREAM_PRESSURE_BYTES + 1)
    )

    section = caretaker.build_maintenance_section(control_plane=plane)

    codes = {finding["code"] for finding in section["findings"]}
    assert "event_stream_pressure" in codes


def test_resume_classification_is_delegated_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caretaker counts must equal ``init_resume``'s own classification.

    A second classifier here would drift from the one init prints into agent
    prompts, and the two would eventually disagree about the same run. The
    contract is delegation, so this asserts the caretaker's buckets against
    ``classify_resume_row`` applied to the identical rows.
    """
    from vibecrafted_core import init_resume, settlements_query

    rows = [
        {
            "run_id": "guardian-1",
            "agent": "codex",
            "skill": "implement",
            "root": "/repo",
            "native_resume_candidate": True,
            "trust_receipt_present": True,
        },
        {
            "run_id": "operator-1",
            "agent": "claude",
            "skill": "workflow",
            "root": "/repo",
            "revalidatable": True,
            "checkout_exists": True,
        },
        {
            "run_id": "evidence-1",
            "agent": "grok",
            "skill": "review",
            "root": "/repo",
        },
    ]
    monkeypatch.setattr(
        settlements_query, "list_settlements", lambda **_: {"runs": rows}
    )

    section = caretaker.build_resumeability_section()

    expected: dict[str, int] = dict.fromkeys(init_resume.RESUME_CLASSES, 0)
    for row in rows:
        expected[init_resume.classify_resume_row(row)] += 1

    assert section["available"] is True
    assert section["counts"] == expected
    assert section["matched"] == len(rows)
    # The rendered command is the public grammar, produced by the same owner.
    assert (
        section["classes"]["operator_resume"][0]["command"]
        == "vibecrafted resume claude --run-id operator-1"
    )


def test_resume_section_survives_an_unreadable_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken ledger must read as unknown, never as an empty backlog."""

    def _explode(**_: object) -> dict[str, object]:
        raise RuntimeError("ledger is a directory")

    monkeypatch.setattr("vibecrafted_core.settlements_query.list_settlements", _explode)

    section = caretaker.build_resumeability_section()

    assert section["available"] is False
    assert "ledger is a directory" in section["reason"]
    assert section["matched"] == 0


def test_publish_and_read_round_trip_with_freshness(tmp_path: Path) -> None:
    """The published bytes and the read view carry one schema and one age."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )
    written = caretaker.publish_caretaker_snapshot(snapshot, control_plane=plane)
    assert written == plane / caretaker.CARETAKER_SNAPSHOT_NAME

    view = caretaker.read_caretaker_snapshot(control_plane=plane)

    assert view["published"] is True
    assert view["stale"] is False
    assert view["snapshot"]["schema"] == caretaker.CARETAKER_SCHEMA
    assert view["snapshot"]["verdict"] == snapshot["verdict"]
    assert view["age_seconds"] is not None


def test_read_reports_absence_and_corruption_distinctly(tmp_path: Path) -> None:
    """ "Never published" and "published garbage" need different responses."""
    plane = _plane(tmp_path)

    absent = caretaker.read_caretaker_snapshot(control_plane=plane)
    assert absent["published"] is False
    assert absent["stale"] is True
    assert "not published" in absent["reason"]

    (plane / caretaker.CARETAKER_SNAPSHOT_NAME).write_text(
        "{not json", encoding="utf-8"
    )
    corrupt = caretaker.read_caretaker_snapshot(control_plane=plane)
    assert corrupt["published"] is False
    assert "corrupt JSON" in corrupt["reason"]


def test_envelope_sections_are_all_present_and_schema_stamped(
    tmp_path: Path,
) -> None:
    """Every reader can rely on the same four sections plus a verdict."""
    plane = _plane(tmp_path)
    home = _receipt(tmp_path)

    snapshot = caretaker.build_caretaker_snapshot(
        home=home, control_plane=plane, probe=False
    )

    assert snapshot["schema"] == caretaker.CARETAKER_SCHEMA
    assert set(snapshot) >= {
        "schema",
        "generated_at",
        "control_plane",
        "server",
        "observability",
        "resumeability",
        "maintenance",
        "verdict",
    }
    for name in ("server", "observability", "resumeability", "maintenance"):
        assert "available" in snapshot[name], name
        assert "reason" in snapshot[name], name
    assert json.loads(json.dumps(snapshot)) == snapshot, "envelope must be JSON-clean"
