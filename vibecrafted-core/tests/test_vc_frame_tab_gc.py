from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core.run_triage import (
    TransferProofError,
    load_durable_transfer_proof,
    load_vc_frame_transfer_proof,
)
from vibecrafted_core.runtime_transcript import write_runtime_transcript_manifest
from vibecrafted_core.vc_frame_tab_gc import (
    BUCKET_SESSIONS,
    PROTECTED_TAB_NAMES,
    LiveTab,
    close_tab,
    collect_cleanup,
    durable_run_ids,
    durable_transfer_proofs,
    list_tabs,
    plan_tab_cleanup,
    terminal_origins,
)

ORIGIN_INSTANCE = "1" * 32
VIEWER_INSTANCE = "2" * 32
VIEWER_TOKEN = "a" * 32


def test_product_workspace_tabs_are_never_gc_candidates() -> None:
    assert PROTECTED_TAB_NAMES == {"Start here", "Agents", "Shell", "voc"}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def durable_run(
    control_plane: Path,
    run_id: str = "impl-good",
    *,
    verdict: str = "needs_attention",
    updated_at: int = 1_700_000_000,
    origin_instance: str = ORIGIN_INSTANCE,
    viewer_instance: str = VIEWER_INSTANCE,
) -> tuple[dict[str, Any], Path]:
    """Materialize vc-frame's exact v4 receipt and a linked runtime settlement."""
    bucket_by_verdict = {
        "finalized": ("Finalized", "Finalized runs", "f", 0),
        "failed": ("Failed", "Failed runs", "x", 7),
        "needs_attention": ("NeedsAttention", "Needs attention", "n", 7),
    }
    bucket, bucket_session, tui, exit_code = bucket_by_verdict[verdict]
    origin_session = "vibecrafted"
    origin_tab = run_id
    origin_identity = {
        "session": origin_session,
        "name": origin_tab,
        "id": 7,
        "session_incarnation": "origin-incarnation",
        "tab_instance_id": origin_instance,
    }
    viewer_identity = {
        "session": bucket_session,
        "name": f"{run_id} [vc:{VIEWER_TOKEN}]",
        "id": 4,
        "session_incarnation": "viewer-incarnation",
        "tab_instance_id": viewer_instance,
    }
    scrollback = f"durable output for {run_id}\n".encode()
    digest = hashlib.sha256(scrollback).hexdigest()
    capture = {
        "capture_source": "terminal_scrollback",
        "source_identity": (
            f"session={origin_session};tab_id=7;"
            f"tab_instance_id={origin_instance};pane_id=terminal_3"
        ),
        "bytes": len(scrollback),
        "sha256": digest,
        "origin_tab_identity": origin_identity,
    }
    receipt = {
        "version": 4,
        "run": run_id,
        "bucket": bucket,
        "exit_code": exit_code,
        "origin_session": origin_session,
        "origin_tab": origin_tab,
        "command": ["codex", "exec", run_id],
        "cwd": "/repo",
        "pane_id": "terminal_3",
        "runtime_transcript": None,
        "settlement_revision": 3,
        "capture": capture,
        "capture_committed": True,
        "metadata_committed": True,
        "viewer_confirmed": True,
        "viewer_tab_identity": viewer_identity,
        "viewer_creation_pending": False,
        "viewer_token": VIEWER_TOKEN,
        "superseded_viewers": [],
        "origin_tab_state": "closed",
        "fault": None,
        "updated_at": updated_at,
    }
    finished = control_plane / "finished_runs" / run_id
    finished.mkdir(parents=True, exist_ok=True)
    (finished / "scrollback.txt").write_bytes(scrollback)
    _write_json(finished / "transfer.json", receipt)
    _write_json(
        finished / "capture.manifest.json",
        {
            "version": 1,
            "run_id": run_id,
            "session": origin_session,
            "origin_tab": origin_tab,
            "pane_id": "terminal_3",
            "runtime_transcript": None,
            "staging_file": ".terminal-scrollback.staging",
            "evidence": capture,
        },
    )
    _write_json(
        finished / "meta.json",
        {
            "run": run_id,
            "exit_code": exit_code,
            "bucket": bucket,
            "origin_session": origin_session,
            "origin_tab": origin_tab,
            "command": ["codex", "exec", run_id],
            "cwd": "/repo",
            "captured_at": updated_at,
            "capture_source": "terminal_scrollback",
            "capture_source_identity": capture["source_identity"],
            "capture_bytes": len(scrollback),
            "capture_sha256": digest,
        },
    )

    runtime_payload: dict[str, Any] = {
        "run_id": run_id,
        "exit_code": exit_code,
        "origin_session": origin_session,
        "origin_tab": origin_tab,
        "origin_pane_id": "terminal_3",
        "command": ["codex", "exec", run_id],
        "root": "/repo",
        "triage": verdict,
        "triage_verdict": verdict,
        "triage_pending": False,
        "triage_bucket": bucket_session,
        "settlement_revision": 3,
        "settlement_verdict": verdict,
        "settlement_tui": tui,
        "triage_settlement_revision": 3,
        "triage_settlement_verdict": verdict,
        "triage_settlement_tui": tui,
        "await_outcome": "completed",
    }
    proof = load_vc_frame_transfer_proof(control_plane, runtime_payload)
    runtime_payload["triage_transfer_receipt"] = str(proof.receipt_path)
    runtime_payload["triage_transfer"] = proof.projection()
    runtime_meta = control_plane / "runtime_runs" / run_id / "meta.json"
    _write_json(runtime_meta, runtime_payload)
    return runtime_payload, runtime_meta


def live_tab(
    *,
    session: str,
    name: str,
    tab_id: int,
    position: int = 2,
    session_incarnation: str,
    tab_instance_id: str,
    active: bool = False,
    focused_elsewhere: bool = False,
) -> LiveTab:
    return LiveTab(
        session=session,
        tab_id=tab_id,
        name=name,
        position=position,
        active=active,
        focused_elsewhere=focused_elsewhere,
        session_incarnation=session_incarnation,
        tab_instance_id=tab_instance_id,
    )


def live_json(tab: LiveTab) -> dict[str, Any]:
    return {
        "tab_id": tab.tab_id,
        "name": tab.name,
        "position": tab.position,
        "active": tab.active,
        "other_focused_clients": [9] if tab.focused_elsewhere else [],
        "session_incarnation": tab.session_incarnation,
        "tab_instance_id": tab.tab_instance_id,
    }


def viewer_tab(run_id: str = "impl-good", **overrides: Any) -> LiveTab:
    payload: dict[str, Any] = {
        "session": "Needs attention",
        "name": f"{run_id} [vc:{VIEWER_TOKEN}]",
        "tab_id": 4,
        "session_incarnation": "viewer-incarnation",
        "tab_instance_id": VIEWER_INSTANCE,
    }
    payload.update(overrides)
    return live_tab(**payload)


def test_only_full_v4_proof_is_durable(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)

    proof = load_durable_transfer_proof(cp, runtime_meta)

    assert proof.run_id == "impl-good"
    assert proof.settlement_revision == 3
    assert durable_run_ids(cp) == {"impl-good"}
    assert terminal_origins(cp) == {("vibecrafted", "impl-good")}


def test_runtime_transcript_capture_is_valid_durable_source(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    runtime_payload, runtime_meta = durable_run(cp)
    finished = cp / "finished_runs" / "impl-good"
    transcript = (tmp_path / "run.transcript.log").resolve()
    transcript.write_bytes((finished / "scrollback.txt").read_bytes())
    assert write_runtime_transcript_manifest(transcript, run_id="impl-good")
    runtime_payload["transcript"] = str(transcript)
    receipt = json.loads((finished / "transfer.json").read_text(encoding="utf-8"))
    capture = dict(receipt["capture"])
    capture.update(
        {
            "capture_source": "runtime_transcript",
            "source_identity": str(transcript),
            "origin_tab_identity": None,
        }
    )
    receipt["runtime_transcript"] = str(transcript)
    receipt["capture"] = capture
    _write_json(finished / "transfer.json", receipt)
    manifest = json.loads(
        (finished / "capture.manifest.json").read_text(encoding="utf-8")
    )
    manifest["runtime_transcript"] = str(transcript)
    manifest["evidence"] = capture
    _write_json(finished / "capture.manifest.json", manifest)
    finished_meta = json.loads((finished / "meta.json").read_text(encoding="utf-8"))
    finished_meta["capture_source"] = "runtime_transcript"
    finished_meta["capture_source_identity"] = str(transcript)
    _write_json(finished / "meta.json", finished_meta)
    proof = load_vc_frame_transfer_proof(cp, runtime_payload)
    runtime_payload["triage_transfer_receipt"] = str(proof.receipt_path)
    runtime_payload["triage_transfer"] = proof.projection()
    _write_json(runtime_meta, runtime_payload)

    durable = load_durable_transfer_proof(cp, runtime_meta)

    assert durable.capture_source == "runtime_transcript"
    assert durable.origin_identity is None


def test_runtime_transcript_source_must_match_requested_path(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    runtime_payload, _runtime_meta = durable_run(cp)
    finished = cp / "finished_runs" / "impl-good"
    requested = (tmp_path / "requested.transcript.log").resolve()
    requested.write_bytes((finished / "scrollback.txt").read_bytes())
    assert write_runtime_transcript_manifest(requested, run_id="impl-good")
    runtime_payload["transcript"] = str(requested)
    other = (tmp_path / "other.transcript.log").resolve()
    receipt = json.loads((finished / "transfer.json").read_text(encoding="utf-8"))
    capture = dict(receipt["capture"])
    capture.update(
        {
            "capture_source": "runtime_transcript",
            "source_identity": str(other),
            "origin_tab_identity": None,
        }
    )
    receipt["runtime_transcript"] = str(requested)
    receipt["capture"] = capture
    _write_json(finished / "transfer.json", receipt)
    manifest = json.loads(
        (finished / "capture.manifest.json").read_text(encoding="utf-8")
    )
    manifest["runtime_transcript"] = receipt["runtime_transcript"]
    manifest["evidence"] = capture
    _write_json(finished / "capture.manifest.json", manifest)
    finished_meta = json.loads((finished / "meta.json").read_text(encoding="utf-8"))
    finished_meta["capture_source"] = "runtime_transcript"
    finished_meta["capture_source_identity"] = capture["source_identity"]
    _write_json(finished / "meta.json", finished_meta)

    with pytest.raises(
        TransferProofError,
        match="source does not match the requested path",
    ):
        load_vc_frame_transfer_proof(cp, runtime_payload)


def test_transfer_proof_rejects_bucket_verdict_disagreement(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    runtime_payload, runtime_meta = durable_run(cp)
    runtime_payload.update(
        {
            "triage": "finalized",
            "triage_verdict": "finalized",
            "settlement_verdict": "finalized",
            "settlement_tui": "f",
        }
    )
    _write_json(runtime_meta, runtime_payload)

    with pytest.raises(TransferProofError, match="bucket disagrees"):
        load_durable_transfer_proof(cp, runtime_meta)


def test_empty_objects_are_never_capture_proof(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, _runtime_meta = durable_run(cp)
    _write_json(cp / "finished_runs" / "impl-good" / "transfer.json", {})

    assert durable_run_ids(cp) == set()


@pytest.mark.parametrize(
    ("target", "replacement"),
    [
        ("transfer.json", b"{not-json"),
        ("capture.manifest.json", b"{}"),
        ("meta.json", b"{}"),
    ],
)
def test_corrupt_or_empty_transfer_files_fail_closed(
    tmp_path: Path,
    target: str,
    replacement: bytes,
) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    (cp / "finished_runs" / "impl-good" / target).write_bytes(replacement)

    with pytest.raises(TransferProofError):
        load_durable_transfer_proof(cp, runtime_meta)
    assert durable_transfer_proofs(cp) == {}


def test_missing_transfer_file_fails_closed(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    (cp / "finished_runs" / "impl-good" / "transfer.json").unlink()

    with pytest.raises(TransferProofError):
        load_durable_transfer_proof(cp, runtime_meta)


def test_malformed_receipt_types_fail_closed_without_escaping(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    receipt_path = cp / "finished_runs" / "impl-good" / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["bucket"] = []
    _write_json(receipt_path, receipt)

    with pytest.raises(TransferProofError):
        load_durable_transfer_proof(cp, runtime_meta)
    assert durable_transfer_proofs(cp) == {}


@pytest.mark.parametrize("field", ["run", "origin_session", "origin_tab"])
def test_mismatched_receipt_identity_fails_closed(
    tmp_path: Path,
    field: str,
) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    receipt_path = cp / "finished_runs" / "impl-good" / "transfer.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = "somebody-elses-value"
    _write_json(receipt_path, receipt)

    with pytest.raises(TransferProofError):
        load_durable_transfer_proof(cp, runtime_meta)


def test_tampered_scrollback_hash_fails_closed(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    (cp / "finished_runs" / "impl-good" / "scrollback.txt").write_text(
        "tampered but still non-empty\n",
        encoding="utf-8",
    )

    with pytest.raises(TransferProofError, match="size|hash"):
        load_durable_transfer_proof(cp, runtime_meta)


def test_symlinked_capture_path_escape_fails_closed(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    scrollback = cp / "finished_runs" / "impl-good" / "scrollback.txt"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(scrollback.read_bytes())
    scrollback.unlink()
    scrollback.symlink_to(outside)

    with pytest.raises(TransferProofError, match="symlink"):
        load_durable_transfer_proof(cp, runtime_meta)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("triage_pending", True),
        ("settlement_revision", 0),
        ("settlement_verdict", ""),
        ("await_outcome", "unknown"),
    ],
)
def test_pending_or_unsettled_runtime_is_not_durable(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    payload = json.loads(runtime_meta.read_text(encoding="utf-8"))
    payload[field] = value
    _write_json(runtime_meta, payload)

    with pytest.raises(TransferProofError):
        load_durable_transfer_proof(cp, runtime_meta)
    assert durable_run_ids(cp) == set()


def test_stale_runtime_projection_is_not_authority(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    _payload, runtime_meta = durable_run(cp)
    payload = json.loads(runtime_meta.read_text(encoding="utf-8"))
    payload["triage_transfer"]["capture"]["sha256"] = "0" * 64
    _write_json(runtime_meta, payload)

    with pytest.raises(TransferProofError, match="projection"):
        load_durable_transfer_proof(cp, runtime_meta)


def test_same_name_successor_is_never_selected(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    proofs = durable_transfer_proofs(cp)
    successor = viewer_tab(
        tab_id=99,
        session_incarnation="successor-incarnation",
        tab_instance_id="9" * 32,
    )

    assert (
        plan_tab_cleanup(
            {"Needs attention": [successor]},
            proofs=proofs,
            bucket_tab_limit=0,
        )
        == []
    )


def test_exact_viewer_is_selected_only_with_explicit_limit(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    proofs = durable_transfer_proofs(cp)
    viewer = viewer_tab()

    assert (
        plan_tab_cleanup(
            {"Needs attention": [viewer]},
            proofs=proofs,
            bucket_tab_limit=None,
        )
        == []
    )
    selected = plan_tab_cleanup(
        {"Needs attention": [viewer]},
        proofs=proofs,
        bucket_tab_limit=0,
    )
    assert [(item.run_id, item.reason) for item in selected] == [
        ("impl-good", "durable-bucket-view")
    ]


def test_active_or_other_client_tab_is_never_selected(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    proofs = durable_transfer_proofs(cp)

    for viewer in (
        viewer_tab(active=True),
        viewer_tab(focused_elsewhere=True),
    ):
        assert (
            plan_tab_cleanup(
                {"Needs attention": [viewer]},
                proofs=proofs,
                bucket_tab_limit=0,
            )
            == []
        )


def test_bucket_limit_keeps_newest_proof_backed_viewer(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(
        cp,
        "impl-old",
        updated_at=10,
        origin_instance="3" * 32,
        viewer_instance="4" * 32,
    )
    durable_run(
        cp,
        "impl-new",
        updated_at=20,
        origin_instance="5" * 32,
        viewer_instance="6" * 32,
    )
    proofs = durable_transfer_proofs(cp)
    old = viewer_tab(
        "impl-old",
        tab_instance_id="4" * 32,
        position=2,
    )
    new = viewer_tab(
        "impl-new",
        tab_instance_id="6" * 32,
        position=3,
    )

    selected = plan_tab_cleanup(
        {"Needs attention": [old, new]},
        proofs=proofs,
        bucket_tab_limit=1,
    )

    assert [(item.run_id, item.reason) for item in selected] == [
        ("impl-old", "durable-bucket-view")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "{}",
        '[{"tab_id": 1}]',
        json.dumps(
            [
                {
                    "tab_id": 1,
                    "name": "one",
                    "position": 1,
                    "active": False,
                    "other_focused_clients": [],
                    "session_incarnation": "inc",
                    "tab_instance_id": "1" * 32,
                },
                {
                    "tab_id": 1,
                    "name": "two",
                    "position": 2,
                    "active": False,
                    "other_focused_clients": [],
                    "session_incarnation": "inc",
                    "tab_instance_id": "2" * 32,
                },
            ]
        ),
    ],
)
def test_list_query_ambiguity_fails_closed(payload: str) -> None:
    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=payload, stderr="")

    assert list_tabs("vc-frame", "session", env={}, runner=runner) is None


def test_collect_queries_only_bucket_and_ambient_origin_sessions(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    seen: list[str] = []

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        seen.append(env["VC_FRAME_SESSION_NAME"])
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    assert (
        collect_cleanup(
            "vc-frame",
            cp,
            bucket_tab_limit=0,
            env={"VC_FRAME_SESSION_NAME": "vibecrafted"},
            runner=runner,
        )
        == []
    )
    assert set(seen) == {*BUCKET_SESSIONS, "vibecrafted"}


def test_close_revalidates_proof_and_uses_typed_selector(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    proofs = durable_transfer_proofs(cp)
    viewer = viewer_tab()
    candidate = plan_tab_cleanup(
        {"Needs attention": [viewer]},
        proofs=proofs,
        bucket_tab_limit=0,
    )[0]
    calls: list[list[str]] = []
    present = True

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        nonlocal present
        calls.append(argv)
        if argv[1:] == ["action", "list-tabs", "--json"]:
            payload = [live_json(viewer)] if present else []
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps(payload),
                stderr="",
            )
        pending_meta = cp / "runtime_runs" / "impl-good" / "meta.json"
        pending_payload = json.loads(pending_meta.read_text(encoding="utf-8"))
        assert pending_payload["triage_gc"]["status"] == "pending"
        assert pending_payload["triage_gc"]["reason"] == "explicit_apply"
        assert "triage_gc_error" not in pending_payload
        present = False
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = close_tab("vc-frame", cp, candidate, env={}, runner=runner)

    assert result.succeeded is True
    assert result.status == "closed"
    assert result.persisted is True
    close = next(call for call in calls if "close-tab" in call)
    assert close == [
        "vc-frame",
        "action",
        "close-tab",
        "--tab-id",
        "4",
        "--expected-name",
        f"impl-good [vc:{VIEWER_TOKEN}]",
        "--expected-session-incarnation",
        "viewer-incarnation",
        "--expected-tab-instance-id",
        VIEWER_INSTANCE,
        "--gc-if-quiescent",
    ]
    runtime_meta = cp / "runtime_runs" / "impl-good" / "meta.json"
    payload = json.loads(runtime_meta.read_text(encoding="utf-8"))
    assert payload["triage"] == "needs_attention"
    assert payload["triage_pending"] is False
    assert payload["triage_transfer"]["version"] == 4
    assert payload["triage_gc"]["status"] == "closed"
    assert payload["triage_gc"]["target"] == {
        "session": "Needs attention",
        "name": f"impl-good [vc:{VIEWER_TOKEN}]",
        "id": 4,
        "session_incarnation": "viewer-incarnation",
        "tab_instance_id": VIEWER_INSTANCE,
    }
    assert "triage_gc_error" not in payload


def test_apply_refuses_when_proof_changes_after_planning(tmp_path: Path) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    viewer = viewer_tab()
    candidate = plan_tab_cleanup(
        {"Needs attention": [viewer]},
        proofs=durable_transfer_proofs(cp),
        bucket_tab_limit=0,
    )[0]
    receipt = cp / "finished_runs" / "impl-good" / "transfer.json"
    receipt.write_text(receipt.read_text(encoding="utf-8") + " ", encoding="utf-8")
    called_close = False

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        nonlocal called_close
        called_close = called_close or "close-tab" in argv
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    result = close_tab("vc-frame", cp, candidate, env={}, runner=runner)

    assert result.succeeded is False
    assert result.reason == "proof_unavailable"
    assert result.persisted is True
    assert called_close is False
    runtime_meta = cp / "runtime_runs" / "impl-good" / "meta.json"
    payload = json.loads(runtime_meta.read_text(encoding="utf-8"))
    assert payload["triage"] == "needs_attention"
    assert payload["triage_gc"]["status"] == "error"
    assert payload["triage_gc_error"]["code"] == "proof_unavailable"


def test_server_gc_refusal_is_typed_durable_and_keeps_terminal_truth(
    tmp_path: Path,
) -> None:
    cp = tmp_path / "control_plane"
    durable_run(cp)
    viewer = viewer_tab()
    candidate = plan_tab_cleanup(
        {"Needs attention": [viewer]},
        proofs=durable_transfer_proofs(cp),
        bucket_tab_limit=0,
    )[0]
    calls: list[list[str]] = []

    def runner(
        argv: list[str],
        *,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1:] == ["action", "list-tabs", "--json"]:
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=json.dumps([live_json(viewer)]),
                stderr="",
            )
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="GC-safe close refused: rerun terminal is running (not held)",
        )

    result = close_tab("vc-frame", cp, candidate, env={}, runner=runner)

    assert result.succeeded is False
    assert result.status == "error"
    assert result.reason == "vc_frame_refused"
    assert result.returncode == 1
    assert result.persisted is True
    close = next(call for call in calls if "close-tab" in call)
    assert "--gc-if-quiescent" in close
    runtime_meta = cp / "runtime_runs" / "impl-good" / "meta.json"
    payload = json.loads(runtime_meta.read_text(encoding="utf-8"))
    assert payload["triage"] == "needs_attention"
    assert payload["triage_pending"] is False
    assert payload["triage_transfer"]["version"] == 4
    assert payload["triage_gc"]["status"] == "error"
    assert payload["triage_gc"]["reason"] == "vc_frame_refused"
    assert payload["triage_gc_error"] == {
        "schema": "vibecrafted.vc-frame-tab-gc.v1",
        "code": "vc_frame_refused",
        "detail": "GC-safe close refused: rerun terminal is running (not held)",
        "returncode": 1,
        "recorded_at": payload["triage_gc"]["recorded_at"],
    }


def test_gc_cli_rejects_bucket_limit_without_a_value() -> None:
    script = (
        Path(__file__).parents[1]
        / "vibecrafted_core/runtime/vc-operator/mission-control/vc-frame-gc.sh"
    )

    result = subprocess.run(
        ["bash", str(script), "--bucket-tab-limit"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert result.stderr.strip() == "--bucket-tab-limit requires a value"
