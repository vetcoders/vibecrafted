"""Cut A: canonical Vibecrafted Workspace identity and catalog proofs."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest
from vibecrafted_core import workflow
from vibecrafted_core import workspace_catalog as wc


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    vib_home = tmp_path / ".vibecrafted"
    vib_home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(vib_home))
    monkeypatch.delenv("VIBECRAFTED_WORKER_SESSION", raising=False)
    monkeypatch.delenv(wc.ENV_WORKSPACE_ID, raising=False)
    monkeypatch.delenv(wc.ENV_VIBECRAFTED_SESSION_ID, raising=False)
    monkeypatch.delenv(wc.ENV_WORKSPACE_INSTANCE_ID, raising=False)
    return vib_home


def test_two_explicit_workspaces_same_root_remain_distinct(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "vibecrafted"
    root.mkdir()
    a = wc.create_workspace(root=root, display_label="alpha", select=False)
    b = wc.create_workspace(root=root, display_label="beta", select=False)
    assert a.workspace_id != b.workspace_id
    assert a.canonical_root == b.canonical_root
    host_a = wc.worker_host_session_name(
        workspace_id=a.workspace_id, display_label=a.display_label
    )
    host_b = wc.worker_host_session_name(
        workspace_id=b.workspace_id, display_label=b.display_label
    )
    assert host_a != host_b
    assert host_a.endswith(wc.WORKER_HOST_SUFFIX)
    assert host_b.endswith(wc.WORKER_HOST_SUFFIX)
    assert " " not in host_a
    assert " " not in host_b


def test_same_basename_different_roots_remain_distinct(
    home: Path, tmp_path: Path
) -> None:
    left = tmp_path / "checkouts" / "left" / "vibecrafted"
    right = tmp_path / "checkouts" / "right" / "vibecrafted"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    a = wc.create_workspace(root=left, display_label="vibecrafted", select=False)
    b = wc.create_workspace(root=right, display_label="vibecrafted", select=False)
    assert a.workspace_id != b.workspace_id
    assert Path(a.canonical_root).name == Path(b.canonical_root).name == "vibecrafted"
    assert wc.worker_host_session_name(
        workspace_id=a.workspace_id, display_label="vibecrafted"
    ) != wc.worker_host_session_name(
        workspace_id=b.workspace_id, display_label="vibecrafted"
    )


def test_worker_host_session_name_is_socket_safe(home: Path) -> None:
    workspace_id = "019ff97a-3328-7660-b6cd-f957b1b163f8"
    short = wc.short_workspace_token(workspace_id)
    host = wc.worker_host_session_name(
        workspace_id=workspace_id,
        display_label="screenscribe html pro report",
    )
    assert host == f"screenscribe-html-pro-re-{short}-w"
    assert " " not in host
    assert host.endswith(wc.WORKER_HOST_SUFFIX)
    legacy = wc.legacy_worker_host_session_name(
        workspace_id=workspace_id, display_label="screenscribe"
    )
    assert legacy == f"screenscribe-{short} workers"
    assert legacy != host


def test_multiple_active_workspaces_coexist(home: Path, tmp_path: Path) -> None:
    r1 = tmp_path / "a"
    r2 = tmp_path / "b"
    r1.mkdir()
    r2.mkdir()
    w1 = wc.create_workspace(root=r1, select=True)
    w2 = wc.create_workspace(root=r2, select=False)
    listed = wc.list_workspaces()
    ids = {r.workspace_id for r in listed}
    assert w1.workspace_id in ids
    assert w2.workspace_id in ids
    assert wc.read_catalog().selected_workspace_id == w1.workspace_id


def test_bury_recover_preserves_identity_and_history(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    created = wc.create_workspace(root=root, display_label="proj", notes="keep-me")
    buried = wc.bury_workspace(created.workspace_id)
    assert buried.status == wc.WORKSPACE_STATUS_BURIED
    assert buried.workspace_id == created.workspace_id
    assert buried.notes == "keep-me"
    assert buried.buried_at is not None
    assert wc.read_catalog().selected_workspace_id is None
    active = wc.list_workspaces(include_buried=False)
    assert created.workspace_id not in {r.workspace_id for r in active}
    recovered = wc.recover_workspace(created.workspace_id, select=True)
    assert recovered.status == wc.WORKSPACE_STATUS_ACTIVE
    assert recovered.workspace_id == created.workspace_id
    assert recovered.recovered_at is not None
    assert recovered.created_at == created.created_at
    assert wc.read_catalog().selected_workspace_id == created.workspace_id


def test_run_identity_fields_on_resolve(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    created = wc.create_workspace(root=root, display_label="repo", select=True)
    identity = wc.resolve_run_workspace_identity(root=root)
    assert identity.workspace_id == created.workspace_id
    assert wc.is_uuid(identity.vibecrafted_session_id)
    assert wc.is_uuid(identity.workspace_instance_id)
    assert identity.build_id.rendered
    meta = identity.to_meta_fields()
    for key in (
        "workspace_id",
        "vibecrafted_session_id",
        "workspace_instance_id",
        "build_id",
        "worker_host_session",
    ):
        assert key in meta
    assert meta["worker_host_session"].endswith(wc.WORKER_HOST_SUFFIX)
    assert " " not in meta["worker_host_session"]
    assert wc.short_workspace_token(created.workspace_id) in meta["worker_host_session"]


def test_explicit_new_root_ignores_foreign_inherited_identity(
    home: Path, tmp_path: Path
) -> None:
    foreign_root = tmp_path / "foreign"
    requested_root = tmp_path / "requested"
    foreign_root.mkdir()
    requested_root.mkdir()
    foreign_workspace = wc.create_workspace(
        root=foreign_root, display_label="foreign", select=True
    )
    foreign_identity = wc.resolve_run_workspace_identity(
        root=foreign_root,
        env={wc.ENV_WORKSPACE_ID: foreign_workspace.workspace_id},
    )

    resolved = wc.resolve_run_workspace_identity(
        root=requested_root,
        env=foreign_identity.to_env(),
    )

    assert resolved.workspace_id != foreign_identity.workspace_id
    assert resolved.vibecrafted_session_id != foreign_identity.vibecrafted_session_id
    assert wc.show_workspace(resolved.workspace_id).canonical_root == str(
        requested_root.resolve()
    )


def test_same_root_inherited_identity_reuses_session_and_instance(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    workspace = wc.create_workspace(root=root, display_label="repo", select=True)
    first = wc.resolve_run_workspace_identity(
        root=root,
        env={wc.ENV_WORKSPACE_ID: workspace.workspace_id},
    )

    second = wc.resolve_run_workspace_identity(root=root, env=first.to_env())

    assert second.workspace_id == first.workspace_id
    assert second.vibecrafted_session_id == first.vibecrafted_session_id
    assert second.workspace_instance_id == first.workspace_instance_id


def test_runtime_session_attachments_preserve_dead_frame_and_activate_replacement(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    workspace = wc.create_workspace(root=root, display_label="repo", select=True)
    identity = wc.resolve_run_workspace_identity(root=root)

    dead = wc.record_runtime_session_attachment(
        workspace_id=workspace.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="workspace-deadbeef",
        state="dead",
        socket_dir="/legacy/socket/root",
    )
    live = wc.record_runtime_session_attachment(
        workspace_id=workspace.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="works-beef-r1234",
        state="live",
        socket_dir="/legacy/socket/root",
        replaces_runtime_session_id="workspace-deadbeef",
    )

    assert dead.session_id == live.session_id == identity.vibecrafted_session_id
    restored = wc.read_workspace_session(identity.vibecrafted_session_id)
    assert [(item.runtime_session_id, item.state) for item in restored.attachments] == [
        ("workspace-deadbeef", "dead"),
        ("works-beef-r1234", "live"),
    ]
    assert restored.attachments[1].replaces_runtime_session_id == "workspace-deadbeef"
    payload = json.loads(
        wc.workspace_session_path(identity.vibecrafted_session_id).read_text(
            encoding="utf-8"
        )
    )
    assert payload["schema"] == wc.SESSION_RECORD_SCHEMA
    assert payload["workspace_id"] == workspace.workspace_id
    assert payload["workspace_instance_id"] == identity.workspace_instance_id


def test_runtime_session_attachment_rejects_foreign_instance(
    home: Path, tmp_path: Path
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()
    left_workspace = wc.create_workspace(root=left, select=True)
    right_workspace = wc.create_workspace(root=right, select=False)
    left_identity = wc.resolve_run_workspace_identity(root=left)
    right_identity = wc.resolve_run_workspace_identity(
        root=right,
        env={wc.ENV_WORKSPACE_ID: right_workspace.workspace_id},
    )

    with pytest.raises(wc.WorkspaceCatalogError, match="does not belong"):
        wc.record_runtime_session_attachment(
            workspace_id=left_workspace.workspace_id,
            vibecrafted_session_id=left_identity.vibecrafted_session_id,
            workspace_instance_id=right_identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="workspace-deadbeef",
            state="dead",
        )


def test_workspace_cli_attaches_runtime_session_to_wes(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    workspace = wc.create_workspace(root=root, select=True)
    identity = wc.resolve_run_workspace_identity(root=root)

    result = wc.workspace_cli_main(
        [
            "session-attach",
            "--workspace-id",
            workspace.workspace_id,
            "--session-id",
            identity.vibecrafted_session_id,
            "--instance-id",
            identity.workspace_instance_id,
            "--runtime",
            "vc-frame",
            "--runtime-session-id",
            "workspace-deadbeef",
            "--state",
            "dead",
            "--socket-dir",
            "/tmp/vc-frame-501",
            "--json",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["attachments"][0]["runtime_session_id"] == "workspace-deadbeef"
    assert payload["attachments"][0]["state"] == "dead"


def test_workspace_resolve_cli_reuses_selected_identity_and_stable_session(
    home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    created = wc.create_workspace(root=root, display_label="repo", select=True)

    assert wc.workspace_cli_main(["resolve", "--env"]) == 0
    first = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    assert first[wc.ENV_WORKSPACE_ID] == created.workspace_id
    assert first["VIBECRAFTED_WORKSPACE_ROOT"] == str(root)
    assert first["VIBECRAFTED_OPERATOR_SESSION"] == "repo"
    assert first["VIBECRAFTED_OPERATOR_SESSION"] == wc.operator_session_name(
        created.workspace_id
    )

    assert wc.workspace_cli_main(["resolve", "--env"]) == 0
    second = dict(
        line.split("=", 1)
        for line in capsys.readouterr().out.splitlines()
        if "=" in line
    )
    assert second[wc.ENV_WORKSPACE_ID] == first[wc.ENV_WORKSPACE_ID]
    assert second[wc.ENV_WORKSPACE_INSTANCE_ID] == first[wc.ENV_WORKSPACE_INSTANCE_ID]
    assert (
        second["VIBECRAFTED_OPERATOR_SESSION"] == first["VIBECRAFTED_OPERATOR_SESSION"]
    )


def test_operator_session_name_is_human_place_not_catalog_fallback(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "codescribe"
    root.mkdir()
    created = wc.create_workspace(root=root, display_label="codescribe", select=True)
    place = wc.operator_session_name(created.workspace_id)
    assert place == "codescribe"
    assert not wc.is_legacy_operator_session_name(place)
    assert wc.legacy_operator_session_name(created.workspace_id) == (
        f"workspace-{wc.short_workspace_token(created.workspace_id)}"
    )
    assert wc.resolve_operator_place_session(root=root) == "codescribe"


def test_operator_session_name_suffixes_only_on_label_collision(
    home: Path, tmp_path: Path
) -> None:
    left = tmp_path / "checkouts" / "left" / "vibecrafted"
    right = tmp_path / "checkouts" / "right" / "vibecrafted"
    left.mkdir(parents=True)
    right.mkdir(parents=True)
    a = wc.create_workspace(root=left, display_label="vibecrafted", select=False)
    assert wc.operator_session_name(a.workspace_id) == "vibecrafted"
    b = wc.create_workspace(root=right, display_label="vibecrafted", select=False)
    name_a = wc.operator_session_name(a.workspace_id)
    name_b = wc.operator_session_name(b.workspace_id)
    assert name_a != name_b
    assert name_a == f"vibecrafted-{wc.short_workspace_token(a.workspace_id)}"
    assert name_b == f"vibecrafted-{wc.short_workspace_token(b.workspace_id)}"
    assert "work-" not in name_a
    assert "work-" not in name_b


def test_new_uuid7_fallback_emits_uuid7(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(wc.uuid, "uuid7", raising=False)
    generated = [uuid.UUID(wc.new_uuid7()) for _ in range(8)]
    assert all(value.version == 7 for value in generated)
    assert generated == sorted(generated)


# The live catalog mixes v4 and v7 by construction: workspaces minted before
# UUIDv7 became the default keep their v4 ids, and Vibecrafted's own repository
# is one of them. Acceptance must therefore never look at the version.
LEGACY_V4_WORKSPACE_ID = "bda366e0-519f-45f1-8d10-449058491a94"


def test_uuid_acceptance_is_version_agnostic() -> None:
    assert uuid.UUID(LEGACY_V4_WORKSPACE_ID).version == 4
    assert wc.is_uuid(LEGACY_V4_WORKSPACE_ID)
    assert wc.require_uuid(LEGACY_V4_WORKSPACE_ID, field_name="workspace_id") == (
        LEGACY_V4_WORKSPACE_ID
    )

    minted = wc.new_uuid7()
    assert uuid.UUID(minted).version == 7
    assert wc.is_uuid(minted)


def test_legacy_v4_workspace_survives_the_full_catalog_round_trip(
    home: Path, tmp_path: Path
) -> None:
    """A v4 id minted before the v7 default must stay a first-class workspace.

    This is the regression guard for a v7-only rail: any reader that validates,
    filters, or sorts on the UUID version drops 22 of the 57 live workspaces --
    Vibecrafted's own checkout among them.
    """

    root = tmp_path / "legacy"
    root.mkdir()
    created = wc.create_workspace(
        root=root,
        display_label="legacy",
        workspace_id=LEGACY_V4_WORKSPACE_ID,
        select=True,
    )
    assert created.workspace_id == LEGACY_V4_WORKSPACE_ID

    assert wc.show_workspace(LEGACY_V4_WORKSPACE_ID).workspace_id == (
        LEGACY_V4_WORKSPACE_ID
    )
    assert wc.select_workspace(LEGACY_V4_WORKSPACE_ID).workspace_id == (
        LEGACY_V4_WORKSPACE_ID
    )

    v7_root = tmp_path / "modern"
    v7_root.mkdir()
    modern = wc.create_workspace(root=v7_root, display_label="modern", select=False)
    assert uuid.UUID(modern.workspace_id).version == 7

    listed = wc.list_workspaces()
    ids = [record.workspace_id for record in listed]
    assert LEGACY_V4_WORKSPACE_ID in ids
    assert modern.workspace_id in ids
    # Chronology comes from created_at, never from the id bits.
    assert listed == sorted(listed, key=lambda record: record.created_at)


def test_dirty_build_id_distinguishes_content_with_same_status(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Vibecrafted Test"],
        check=True,
    )
    tracked = root / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True
    )

    tracked.write_text("first dirty content\n", encoding="utf-8")
    first = wc.compute_build_id(root)
    tracked.write_text("second dirty content\n", encoding="utf-8")
    second = wc.compute_build_id(root)

    assert first.git_commit == second.git_commit
    assert first.dirty is second.dirty is True
    assert first.dirty_digest != second.dirty_digest

    tracked.write_text("clean\n", encoding="utf-8")
    untracked = root / "untracked.txt"
    untracked.write_text("first untracked content\n", encoding="utf-8")
    first_untracked = wc.compute_build_id(root)
    untracked.write_text("second untracked content\n", encoding="utf-8")
    second_untracked = wc.compute_build_id(root)
    assert first_untracked.dirty_digest != second_untracked.dirty_digest


def test_build_id_fails_closed_when_git_status_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses = iter(
        (
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=f"{'a' * 40}\n", stderr=""
            ),
            subprocess.CompletedProcess(args=[], returncode=2, stdout=b"", stderr=b""),
        )
    )
    monkeypatch.setattr(wc.subprocess, "run", lambda *_args, **_kwargs: next(responses))
    with pytest.raises(wc.WorkspaceCatalogError, match="git status failed"):
        wc.compute_build_id(tmp_path)


def test_instance_build_mismatch_cannot_claim_live(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    ws = wc.create_workspace(root=root, select=True)
    live = wc.materialize_instance(workspace_id=ws.workspace_id, root=root)
    other = wc.BuildId(
        git_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        dirty=False,
        dirty_digest="",
        package_version="9.9.9",
        root=str(root.resolve()),
    )
    with pytest.raises(wc.WorkspaceInstanceBuildMismatch):
        wc.claim_live_instance(
            workspace_instance_id=live.workspace_instance_id,
            expected_build_id=other,
        )
    # Materializing under a different build detaches the previous live instance.
    second = wc.materialize_instance(
        workspace_id=ws.workspace_id, root=root, build_id=other
    )
    assert second.workspace_instance_id != live.workspace_instance_id
    reloaded = wc.WorkspaceInstance.from_payload(
        json.loads(wc.instance_path(live.workspace_instance_id).read_text())
    )
    assert reloaded.status == wc.INSTANCE_STATUS_STALE


def test_atomic_write_crash_preserves_previous_catalog(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.create_workspace(root=root, display_label="first", select=True)
    path = wc.catalog_path()
    before = path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(wc.os, "replace", boom)
    with pytest.raises(OSError):
        wc.create_workspace(
            root=tmp_path / "other",
            display_label="other",
            select=False,
        )
    # Catalog file still parses to the previous valid state.
    after = path.read_text(encoding="utf-8")
    assert after == before
    catalog = wc.read_catalog()
    assert first.workspace_id in catalog.workspaces
    assert len(catalog.workspaces) == 1


def test_ephemeral_workspace_quarantine_is_explicit_receipt_backed_and_narrow(
    home: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ephemeral_root = tmp_path / "test-direct-helper0"
    ephemeral_root.mkdir()
    ephemeral = wc.create_workspace(
        root=ephemeral_root, display_label="tmp.pollution", select=True
    )
    identity = wc.resolve_run_workspace_identity(
        root=ephemeral_root,
        env={wc.ENV_WORKSPACE_ID: ephemeral.workspace_id},
    )
    instance_file = wc.instance_path(identity.workspace_instance_id)
    valid = wc.create_workspace(
        root=Path.home() / "operator-valid-workspace",
        display_label="operator-valid",
        select=False,
    )

    preview = wc.quarantine_ephemeral_workspaces()
    assert preview["applied"] is False
    assert preview["workspace_ids"] == [ephemeral.workspace_id]
    assert ephemeral.workspace_id in wc.read_catalog().workspaces

    assert wc.workspace_cli_main(["quarantine-ephemeral", "--apply", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    receipt_path = Path(result["receipt_path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert result["applied"] is True
    assert receipt["schema"] == wc.EPHEMERAL_QUARANTINE_RECEIPT_SCHEMA
    assert receipt["records"][0]["workspace"]["workspace_id"] == ephemeral.workspace_id
    assert receipt["records"][0]["reason"] == "pytest_tmp_path"

    catalog = wc.read_catalog()
    assert ephemeral.workspace_id not in catalog.workspaces
    assert valid.workspace_id in catalog.workspaces
    assert catalog.selected_workspace_id is None
    assert instance_file.is_file()


def test_migration_idempotent_and_unassigned_not_guessed(
    home: Path, tmp_path: Path
) -> None:
    from vibecrafted_core.control_plane import control_plane_home

    root_a = (tmp_path / "proj-a").resolve()
    root_a.mkdir()
    cp = control_plane_home()
    runs = cp / "runtime_runs"
    (runs / "run-clear").mkdir(parents=True)
    (runs / "run-clear" / "meta.json").write_text(
        json.dumps({"run_id": "run-clear", "root": str(root_a)}),
        encoding="utf-8",
    )
    (runs / "run-ambiguous").mkdir(parents=True)
    (runs / "run-ambiguous" / "meta.json").write_text(
        json.dumps({"run_id": "run-ambiguous", "root": ""}),
        encoding="utf-8",
    )

    report1 = wc.migrate_legacy_workspaces()
    report2 = wc.migrate_legacy_workspaces()
    assert report1["created_count"] >= 1
    # Second pass creates no additional workspaces for the same root.
    assert report2["created_count"] == 0
    unassigned_ids = {item["run_id"] for item in report1["unassigned_records"]}
    assert "run-ambiguous" in unassigned_ids
    catalog = wc.read_catalog()
    # Original evidence untouched.
    original = json.loads((runs / "run-ambiguous" / "meta.json").read_text())
    assert "workspace_id" not in original
    assert any(r.canonical_root == str(root_a) for r in catalog.workspaces.values())


def test_settlement_counts_scoped_to_workspace(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.control_plane import run_snapshot_dir

    root = tmp_path / "repo"
    root.mkdir()
    ws_a = wc.create_workspace(root=root, display_label="a", select=False)
    ws_b = wc.create_workspace(root=root, display_label="b", select=False)

    snap = run_snapshot_dir()
    snap.mkdir(parents=True, exist_ok=True)
    for run_id, wid, tui in (
        ("run-a1", ws_a.workspace_id, "f"),
        ("run-a2", ws_a.workspace_id, "n"),
        ("run-b1", ws_b.workspace_id, "x"),
        ("run-u1", None, "f"),
    ):
        (snap / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "root": str(root),
                    **({"workspace_id": wid} if wid else {}),
                }
            ),
            encoding="utf-8",
        )

    ledger = {
        "records": [
            {
                "record_type": "settlement_transition",
                "run_id": "run-a1",
                "settlement_revision": 1,
                "settlement_tui": "f",
            },
            {
                "record_type": "settlement_transition",
                "run_id": "run-a2",
                "settlement_revision": 1,
                "settlement_tui": "n",
            },
            {
                "record_type": "settlement_transition",
                "run_id": "run-b1",
                "settlement_revision": 1,
                "settlement_tui": "x",
            },
            {
                "record_type": "settlement_transition",
                "run_id": "run-u1",
                "settlement_revision": 1,
                "settlement_tui": "f",
            },
        ]
    }
    scoped = wc.settlement_counts_for_workspace(
        ws_a.workspace_id, ledger_snapshot=ledger
    )
    assert scoped["latest_by_run"] == {"f": 1, "x": 0, "n": 1, "total": 2}
    assert scoped["excluded_unassigned_run_count"] >= 1
    scoped_b = wc.settlement_counts_for_workspace(
        ws_b.workspace_id, ledger_snapshot=ledger
    )
    assert scoped_b["latest_by_run"] == {"f": 0, "x": 1, "n": 0, "total": 1}


def test_worker_routing_workspace_bound_not_basename(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "vibecrafted"
    root.mkdir()
    ws = wc.create_workspace(root=root, display_label="vibecrafted", select=True)
    host = workflow._effective_operator_session(
        root=str(root), run_id="r1", env=dict(os.environ)
    )
    assert host != "vibecrafted workers"
    assert host != f"vibecrafted{wc.WORKER_HOST_SUFFIX}"
    assert host.endswith(wc.WORKER_HOST_SUFFIX)
    assert " " not in host
    assert wc.short_workspace_token(ws.workspace_id) in host
    # Explicit override still wins.
    assert (
        workflow._effective_operator_session(
            root=str(root),
            run_id="r2",
            env={**os.environ, "VIBECRAFTED_WORKER_SESSION": "forced-host"},
        )
        == "forced-host"
    )


def test_snapshot_manifest_contract_roundtrip(home: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    ws = wc.create_workspace(root=root, select=True)
    manifest = wc.build_empty_snapshot_manifest(workspace_id=ws.workspace_id, root=root)
    path = wc.write_snapshot_manifest(manifest)
    loaded = wc.read_snapshot_manifest(manifest.snapshot_id)
    assert loaded.workspace_id == ws.workspace_id
    assert loaded.schema_version == "1"
    assert loaded.build_id.rendered
    assert path.is_file()
    assert loaded.to_payload()["schema"] == wc.SNAPSHOT_MANIFEST_SCHEMA


def test_write_meta_stamps_workspace_fields(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibecrafted_core.spawn import write_meta

    root = tmp_path / "repo"
    root.mkdir()
    ws = wc.create_workspace(root=root, select=True)
    meta_path = tmp_path / "meta.json"
    # Avoid control-plane event side effects depending on global home.
    monkeypatch.setattr(
        "vibecrafted_core.spawn.append_event",
        lambda *a, **k: None,
    )
    write_meta(
        meta_path,
        status="active",
        agent="grok",
        mode="workflow",
        root=str(root),
        input_ref="prompt",
        report=str(tmp_path / "report.md"),
        transcript=str(tmp_path / "t.log"),
        launcher="test",
        run_id="run-ws-1",
    )
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["workspace_id"] == ws.workspace_id
    assert payload["vibecrafted_session_id"]
    assert payload["workspace_instance_id"]
    assert isinstance(payload["build_id"], dict)
