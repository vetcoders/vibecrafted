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


def test_wes_same_build_resolve_preserves_bound_session_without_inheritance(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="/sock",
    )
    before = wc.read_workspace_session(first.vibecrafted_session_id).to_payload()
    second = wc.resolve_run_workspace_identity(root=root, env={})
    assert second.to_env() == first.to_env()
    assert (
        wc.read_workspace_session(first.vibecrafted_session_id).to_payload() == before
    )


@pytest.mark.parametrize("change", ["revision", "package", "root"])
def test_wes_build_transition_allocates_coherent_pair_and_preserves_old_session(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    old_build = wc.BuildId("a" * 40, False, "", "1", str(root))
    monkeypatch.setattr(wc, "compute_build_id", lambda *_a, **_k: old_build)
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="dead",
        socket_dir="/sock",
    )
    before = wc.read_workspace_session(first.vibecrafted_session_id).to_payload()
    current = wc.BuildId(
        "b" * 40 if change == "revision" else old_build.git_commit,
        False,
        "",
        "2" if change == "package" else "1",
        str(tmp_path / "moved") if change == "root" else str(root),
    )
    monkeypatch.setattr(wc, "compute_build_id", lambda *_a, **_k: current)
    second = wc.resolve_run_workspace_identity(root=root, env=first.to_env())
    assert second.workspace_instance_id != first.workspace_instance_id
    assert second.vibecrafted_session_id != first.vibecrafted_session_id
    bound = wc.record_runtime_session_attachment(
        workspace_id=second.workspace_id,
        vibecrafted_session_id=second.vibecrafted_session_id,
        workspace_instance_id=second.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="/sock",
    )
    assert bound.workspace_instance_id == second.workspace_instance_id
    assert (
        wc.read_workspace_session(first.vibecrafted_session_id).to_payload() == before
    )
    old = wc.WorkspaceInstance.from_payload(
        json.loads(wc.instance_path(first.workspace_instance_id).read_text())
    )
    assert old.build_id == old_build


@pytest.mark.parametrize("mismatch", ["instance", "session"])
def test_wes_supplied_same_root_identity_mismatch_refuses_without_writes(
    home: Path, tmp_path: Path, mismatch: str
) -> None:
    root = tmp_path / "repo"
    other_root = tmp_path / "other"
    root.mkdir()
    other_root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    other = wc.resolve_run_workspace_identity(root=other_root, env={})
    env = first.to_env()
    key = (
        wc.ENV_WORKSPACE_INSTANCE_ID
        if mismatch == "instance"
        else wc.ENV_VIBECRAFTED_SESSION_ID
    )
    env[key] = other.to_env()[key]
    before = {str(p): p.read_bytes() for p in wc.instances_dir().glob("*.json")}
    with pytest.raises(wc.WorkspaceCatalogError, match="ownership|belong|identity"):
        wc.resolve_run_workspace_identity(root=root, env=env)
    assert {str(p): p.read_bytes() for p in wc.instances_dir().glob("*.json")} == before


def test_wes_stale_same_build_session_cannot_be_rebound_to_new_instance(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="dead",
        socket_dir="/sock",
    )
    path = wc.instance_path(first.workspace_instance_id)
    payload = json.loads(path.read_text())
    payload["status"] = wc.INSTANCE_STATUS_STALE
    path.write_text(json.dumps(payload))
    before = wc.read_workspace_session(first.vibecrafted_session_id).to_payload()
    second = wc.resolve_run_workspace_identity(root=root, env=first.to_env())
    assert second.workspace_instance_id != first.workspace_instance_id
    assert second.vibecrafted_session_id != first.vibecrafted_session_id
    wc.record_runtime_session_attachment(
        workspace_id=second.workspace_id,
        vibecrafted_session_id=second.vibecrafted_session_id,
        workspace_instance_id=second.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="/sock",
    )
    assert (
        wc.read_workspace_session(first.vibecrafted_session_id).to_payload() == before
    )


@pytest.mark.parametrize("state", ["dead", "missing"])
def test_wes_same_build_restoration_allows_next_fresh_workflow(
    home: Path, tmp_path: Path, state: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="",
    )
    restored = wc.resolve_run_workspace_identity(
        root=root,
        env=first.to_env(),
        runtime_session_id="repo",
        socket_dir="",
        runtime_state=state,
    )
    assert restored.vibecrafted_session_id != first.vibecrafted_session_id
    assert restored.workspace_instance_id != first.workspace_instance_id
    old = wc.read_workspace_session(first.vibecrafted_session_id)
    assert old.workspace_instance_id == first.workspace_instance_id
    assert old.attachments[0].state == state
    wc.record_runtime_session_attachment(
        workspace_id=restored.workspace_id,
        vibecrafted_session_id=restored.vibecrafted_session_id,
        workspace_instance_id=restored.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="",
    )
    assert (
        wc.resolve_run_workspace_identity(root=root, env={}).to_env()
        == restored.to_env()
    )
    returned = wc.resolve_run_workspace_identity(
        root=root,
        env=first.to_env(),
        runtime_session_id="repo",
        socket_dir="",
        runtime_state="live",
    )
    assert returned.to_env() == restored.to_env()
    instances = wc.list_instances(workspace_id=first.workspace_id)
    retired = next(
        item
        for item in instances
        if item.workspace_instance_id == first.workspace_instance_id
    )
    assert retired.status == wc.INSTANCE_STATUS_STALE
    assert retired.build_id == first.build_id


def test_wes_orphan_session_refuses_before_materialization(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="dead",
        socket_dir="",
    )
    wc.instance_path(first.workspace_instance_id).unlink()
    before = wc.workspace_session_path(first.vibecrafted_session_id).read_bytes()
    with pytest.raises(wc.WorkspaceCatalogError, match="no owning instance"):
        wc.materialize_instance(
            workspace_id=first.workspace_id,
            root=root,
            vibecrafted_session_id=first.vibecrafted_session_id,
            build_id=first.build_id,
        )
    assert not wc.list_instances()
    assert (
        wc.workspace_session_path(first.vibecrafted_session_id).read_bytes() == before
    )


@pytest.mark.parametrize(
    "status", [wc.INSTANCE_STATUS_STALE, wc.INSTANCE_STATUS_DETACHED]
)
@pytest.mark.parametrize("attachment_state", ["missing", "dead"])
def test_inactive_malformed_logical_history_does_not_block_fresh_materialization(
    home: Path, tmp_path: Path, status: str, attachment_state: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="historical",
        state=attachment_state,
        socket_dir="/private/synthetic",
    )
    instance_path = wc.instance_path(first.workspace_instance_id)
    payload = json.loads(instance_path.read_text())
    payload["status"] = status
    instance_path.write_text(json.dumps(payload))
    logical_path = wc.workspace_session_path(first.vibecrafted_session_id)
    payload = json.loads(logical_path.read_text())
    payload["schema"] = "historical-unreadable"
    logical_path.write_text(json.dumps(payload))
    before = {path: path.read_bytes() for path in (instance_path, logical_path)}
    fresh_session_id = wc.new_uuid7()

    fresh = wc.materialize_instance(
        workspace_id=first.workspace_id,
        root=root,
        vibecrafted_session_id=fresh_session_id,
        build_id=first.build_id,
    )

    assert fresh.workspace_instance_id != first.workspace_instance_id
    assert fresh.vibecrafted_session_id == fresh_session_id
    assert fresh.workspace_id == first.workspace_id
    assert fresh.status == wc.INSTANCE_STATUS_LIVE
    assert len(wc.list_instances(workspace_id=first.workspace_id)) == 2
    assert {path: path.read_bytes() for path in before} == before
    # Selecting the damaged historical identity is still an explicit refusal.
    after = {path: path.read_bytes() for path in home.rglob("*.json")}
    with pytest.raises(wc.WorkspaceCatalogError, match="session record schema"):
        wc.materialize_instance(
            workspace_id=first.workspace_id,
            root=root,
            vibecrafted_session_id=first.vibecrafted_session_id,
            build_id=first.build_id,
        )
    assert {path: path.read_bytes() for path in home.rglob("*.json")} == after


@pytest.mark.parametrize("attachment_state", ["live", "missing", "dead"])
def test_live_instance_malformed_logical_record_refuses_without_mutation(
    home: Path, tmp_path: Path, attachment_state: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="vc-host",
        state=attachment_state,
        socket_dir="/private/synthetic",
    )
    path = wc.workspace_session_path(first.vibecrafted_session_id)
    payload = json.loads(path.read_text())
    payload["schema"] = "unreadable-current-owner"
    path.write_text(json.dumps(payload))
    before = {path: path.read_bytes() for path in home.rglob("*.json")}

    with pytest.raises(wc.WorkspaceCatalogError, match="session record schema"):
        wc.materialize_instance(
            workspace_id=first.workspace_id,
            root=root,
            vibecrafted_session_id=wc.new_uuid7(),
            build_id=first.build_id,
        )

    assert {path: path.read_bytes() for path in home.rglob("*.json")} == before


def test_wes_live_return_ignores_stale_history_and_dead_foreign_names(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    foreign = tmp_path / "foreign"
    root.mkdir()
    foreign.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    for identity, state in [
        (first, "live"),
        (wc.resolve_run_workspace_identity(root=foreign, env={}), "dead"),
    ]:
        wc.record_runtime_session_attachment(
            workspace_id=identity.workspace_id,
            vibecrafted_session_id=identity.vibecrafted_session_id,
            workspace_instance_id=identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="repo",
            state=state,
            socket_dir="/sock",
        )
    build = wc.BuildId("b" * 40, False, "", "2", str(root))
    monkeypatch.setattr(wc, "compute_build_id", lambda *_a, **_k: build)
    current = wc.resolve_run_workspace_identity(root=root, env=first.to_env())
    wc.record_runtime_session_attachment(
        workspace_id=current.workspace_id,
        vibecrafted_session_id=current.vibecrafted_session_id,
        workspace_instance_id=current.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="repo",
        state="live",
        socket_dir="/sock",
    )
    before = {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    }
    monkeypatch.setattr(
        wc,
        "compute_build_id",
        lambda *_a, **_k: pytest.fail("live return must not compute current build"),
    )
    returned = wc.resolve_run_workspace_identity(
        root=root,
        env=first.to_env(),
        runtime_session_id="repo",
        socket_dir="/sock",
        runtime_state="live",
    )
    assert returned.to_env() == current.to_env()
    assert {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    } == before


@pytest.mark.parametrize("state", ["live", "dead", "missing"])
def test_wes_ambiguous_actual_live_seats_refuse_before_mutation(
    home: Path, tmp_path: Path, state: str
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    second = wc.materialize_instance(
        workspace_id=first.workspace_id,
        root=root,
        vibecrafted_session_id=wc.new_uuid7(),
        build_id=first.build_id,
    )
    for iid, sid in [
        (first.workspace_instance_id, first.vibecrafted_session_id),
        (second.workspace_instance_id, second.vibecrafted_session_id),
    ]:
        wc.record_runtime_session_attachment(
            workspace_id=first.workspace_id,
            vibecrafted_session_id=sid,
            workspace_instance_id=iid,
            runtime="vc-frame",
            runtime_session_id="repo",
            state="live",
            socket_dir="/sock",
        )
    before = {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    }
    with pytest.raises(wc.WorkspaceCatalogError, match="ambiguous"):
        wc.resolve_run_workspace_identity(
            root=root,
            env={},
            runtime_session_id="repo",
            socket_dir="/sock",
            runtime_state=state,
        )
    assert {
        str(p): p.read_bytes()
        for folder in [wc.instances_dir(), wc.sessions_dir()]
        for p in folder.glob("*.json")
    } == before


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
    assert wc.runtime_session_owner_root(
        "works-beef-r1234", "/legacy/socket/root"
    ) == str(root.resolve())
    assert wc.runtime_session_owner_root("works-beef-r1234", "/other/socket") is None
    assert wc.runtime_session_owner_root("unbound", "/legacy/socket/root") is None


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
    assert (
        wc.workspace_cli_main(
            [
                "session-owner",
                "--runtime-session-id",
                "workspace-deadbeef",
                "--socket-dir",
                "/tmp/vc-frame-501",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == str(root.resolve())


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
                args=[], returncode=0, stdout=f"{tmp_path}\n", stderr=""
            ),
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

    def boom(*_args, **_kwargs):
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


def test_default_lobby_resolution_has_no_git_build_or_selected_identity(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    wc.create_workspace(root=project)
    lobby = home / "projects"
    lobby.mkdir()
    monkeypatch.chdir(lobby)
    monkeypatch.setattr(
        wc, "compute_build_id", lambda *a, **k: pytest.fail("no build in lobby")
    )
    monkeypatch.setattr(
        wc.subprocess, "run", lambda *a, **k: pytest.fail("no Git in lobby")
    )
    assert wc.workspace_cli_main(["resolve", "--env"]) == 0
    assert capsys.readouterr().out == ""
    assert wc.workspace_cli_main(["resolve", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_workspace_catalog_refuses_home_and_identity_free_lobby(home: Path) -> None:
    lobby = home / "projects"
    lobby.mkdir()
    for root in (home, lobby):
        with pytest.raises(wc.WorkspaceCatalogError):
            wc.create_workspace(root=root)
        with pytest.raises(wc.WorkspaceCatalogError):
            wc.resolve_run_workspace_identity(root=root)


def test_supplied_env_cannot_disguise_runtime_home_as_a_project(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lobby = home / "projects"
    lobby.mkdir()
    monkeypatch.setattr(
        wc, "read_catalog", lambda: pytest.fail("deny before touching catalog")
    )
    for root in (home, lobby):
        with pytest.raises(wc.WorkspaceCatalogError):
            wc.resolve_run_workspace_identity(
                root=root, env={"VIBECRAFTED_HOME": str(tmp_path / "other-home")}
            )


def test_run_identity_defers_full_dirty_digest(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "untracked").write_text("content")
    monkeypatch.setattr(
        wc, "_dirty_worktree_digest", lambda *a, **k: pytest.fail("digest must be lazy")
    )
    identity = wc.resolve_run_workspace_identity(root=root)
    assert identity.build_id.dirty_digest == "dirty-unknown"
    assert "dirty-unknown" in identity.build_id.rendered


def test_exact_digest_timeout_is_dirty_unknown_without_aborting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "untracked").write_text("content")

    def timeout(*a: object, **k: object) -> str:
        raise subprocess.TimeoutExpired("git diff --binary HEAD", 20)

    monkeypatch.setattr(wc, "_dirty_worktree_digest", timeout)
    build = wc.compute_build_id(root)
    assert build.dirty_digest == "dirty-unknown"
    assert "dirty-unknown" in build.rendered
    assert not build.matches(build)


def test_git_identity_probe_timeout_never_claims_a_clean_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*a: object, **k: object) -> str:
        raise subprocess.TimeoutExpired("git rev-parse", 1)

    monkeypatch.setattr(wc.subprocess, "run", timeout)
    build = wc.compute_build_id(tmp_path, exact=False)
    assert build.dirty
    assert build.dirty_digest == "dirty-unknown"


def test_explicit_project_from_lobby_resolves_only_selected_project(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    foreign = tmp_path / "foreign"
    selected = tmp_path / "selected"
    foreign.mkdir()
    selected.mkdir()
    foreign_workspace = wc.create_workspace(root=foreign)
    lobby = home / "projects"
    lobby.mkdir()
    monkeypatch.chdir(lobby)
    assert wc.workspace_cli_main(["resolve", "--repo", str(selected), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["VIBECRAFTED_WORKSPACE_ROOT"] == str(selected)
    assert payload[wc.ENV_WORKSPACE_ID] != foreign_workspace.workspace_id
    assert wc.workspace_cli_main(["resolve", "--repo", str(lobby), "--json"]) == 2


def test_uncertain_digest_preserves_same_session_without_build_equality(
    home: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    workspace = wc.create_workspace(root=root)
    session_id = wc.new_uuid7()
    exact = wc.BuildId("a" * 40, False, "", "1", str(root))
    uncertain = wc.BuildId("a" * 40, True, "dirty-unknown", "1", str(root))
    first = wc.materialize_instance(
        workspace_id=workspace.workspace_id,
        root=root,
        vibecrafted_session_id=session_id,
        build_id=exact,
    )
    observed = wc.materialize_instance(
        workspace_id=workspace.workspace_id,
        root=root,
        vibecrafted_session_id=session_id,
        build_id=uncertain,
    )
    assert observed.workspace_instance_id == first.workspace_instance_id
    assert not observed.build_id.matches(exact)
    assert not observed.build_id.matches(uncertain)

    wc.materialize_instance(
        workspace_id=workspace.workspace_id,
        root=root,
        vibecrafted_session_id=wc.new_uuid7(),
        build_id=uncertain,
    )
    assert all(i.status == wc.INSTANCE_STATUS_LIVE for i in wc.list_instances())

    changed_revision = wc.BuildId("b" * 40, True, "dirty-unknown", "1", str(root))
    changed = wc.materialize_instance(
        workspace_id=workspace.workspace_id,
        root=root,
        vibecrafted_session_id=session_id,
        build_id=changed_revision,
    )
    assert changed.workspace_instance_id != first.workspace_instance_id
    assert all(
        i.status == wc.INSTANCE_STATUS_STALE
        for i in wc.list_instances()
        if i.workspace_instance_id != changed.workspace_instance_id
    )


@pytest.mark.parametrize("historical", ["missing", "dead", "stale"])
def test_live_session_owner_ignores_unavailable_history_without_mutation(
    home: Path, tmp_path: Path, historical: str
) -> None:
    from dataclasses import replace

    foreign = tmp_path / "foreign"
    current = tmp_path / "current"
    foreign.mkdir()
    current.mkdir()
    old = wc.resolve_run_workspace_identity(root=foreign, env={})
    old_session = wc.record_runtime_session_attachment(
        workspace_id=old.workspace_id,
        vibecrafted_session_id=old.vibecrafted_session_id,
        workspace_instance_id=old.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="reused",
        socket_dir="/socket",
        state="live" if historical == "stale" else historical,
    )
    if historical == "stale":
        instance = wc.list_instances(workspace_id=old.workspace_id)[0]
        with wc._catalog_lock(exclusive=True):
            wc._write_instance_unlocked(
                replace(instance, status=wc.INSTANCE_STATUS_STALE)
            )
    before = wc.workspace_session_path(old_session.session_id).read_bytes()
    new = wc.resolve_run_workspace_identity(root=current, env={})
    wc.record_runtime_session_attachment(
        workspace_id=new.workspace_id,
        vibecrafted_session_id=new.vibecrafted_session_id,
        workspace_instance_id=new.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="reused",
        socket_dir="/socket",
        state="live",
    )
    assert wc.runtime_session_owner_root(
        "reused", "/socket", runtime_state="live"
    ) == str(current)
    assert wc.workspace_session_path(old_session.session_id).read_bytes() == before
    with pytest.raises(wc.WorkspaceCatalogError, match="conflicting owners"):
        wc.runtime_session_owner_root("reused", "/socket", runtime_state="dead")


def test_live_session_owner_refuses_ambiguous_same_root_and_unreadable_current(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    first = wc.resolve_run_workspace_identity(root=root, env={})
    second = wc.materialize_instance(
        workspace_id=first.workspace_id,
        root=root,
        vibecrafted_session_id=wc.new_uuid7(),
        build_id=first.build_id,
    )
    for iid, sid in [
        (first.workspace_instance_id, first.vibecrafted_session_id),
        (second.workspace_instance_id, second.vibecrafted_session_id),
    ]:
        wc.record_runtime_session_attachment(
            workspace_id=first.workspace_id,
            vibecrafted_session_id=sid,
            workspace_instance_id=iid,
            runtime="vc-frame",
            runtime_session_id="same",
            socket_dir="/socket",
            state="live",
        )
    with pytest.raises(wc.WorkspaceCatalogError, match="ambiguous live seats"):
        wc.runtime_session_owner_root("same", "/socket", runtime_state="live")
    wc.instance_path(second.workspace_instance_id).write_text("invalid json")
    with pytest.raises(wc.WorkspaceCatalogError):
        wc.runtime_session_owner_root("same", "/socket", runtime_state="live")


def test_live_seat_refuses_selected_sibling_workspace_at_same_root(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    owner = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=owner.workspace_id,
        vibecrafted_session_id=owner.vibecrafted_session_id,
        workspace_instance_id=owner.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="owner-seat",
        socket_dir="/socket",
        state="live",
    )
    sibling = wc.create_workspace(root=root, display_label="sibling", select=True)
    assert sibling.workspace_id != owner.workspace_id
    before = wc.read_catalog().to_payload()
    with pytest.raises(wc.WorkspaceCatalogError, match="belongs to another workspace"):
        wc.resolve_run_workspace_identity(
            root=root,
            env={},
            runtime_session_id="owner-seat",
            socket_dir="/socket",
            runtime_state="live",
        )
    assert wc.read_catalog().to_payload() == before
    admitted = wc.resolve_run_workspace_identity(
        root=root,
        env=owner.to_env(),
        runtime_session_id="owner-seat",
        socket_dir="/socket",
        runtime_state="live",
    )
    assert admitted.workspace_id == owner.workspace_id
    assert admitted.vibecrafted_session_id == owner.vibecrafted_session_id


def test_live_session_owner_preserves_foreign_root_and_no_identity_refusal(
    home: Path, tmp_path: Path
) -> None:
    root = tmp_path / "foreign"
    root.mkdir()
    ident = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=ident.workspace_id,
        vibecrafted_session_id=ident.vibecrafted_session_id,
        workspace_instance_id=ident.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="foreign",
        socket_dir="/socket",
        state="live",
    )
    assert wc.runtime_session_owner_root(
        "foreign", "/socket", runtime_state="live"
    ) == str(root)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(wc.WorkspaceCatalogError, match="does not match requested root"):
        wc.resolve_run_workspace_identity(
            root=other,
            env={},
            runtime_session_id="foreign",
            socket_dir="/socket",
            runtime_state="live",
        )
    with pytest.raises(wc.WorkspaceCatalogError, match="no consistent live WES"):
        wc.resolve_run_workspace_identity(
            root=other,
            env={},
            runtime_session_id="unbound",
            socket_dir="/socket",
            runtime_state="live",
        )


@pytest.mark.parametrize("corruption", ["invalid-json", "unreadable"])
def test_live_session_owner_refuses_unreadable_session_record(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    identity = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="current",
        socket_dir="/socket",
        state="live",
    )
    path = wc.workspace_session_path(identity.vibecrafted_session_id)
    if corruption == "invalid-json":
        path.write_text("{broken")
    else:
        read = wc._read_json
        monkeypatch.setattr(wc, "_read_json", lambda p: None if p == path else read(p))
    before = path.read_bytes()
    with pytest.raises(wc.WorkspaceCatalogError, match="ownership is unreadable"):
        wc.runtime_session_owner_root("current", "/socket", runtime_state="live")
    assert path.read_bytes() == before


def _destination_seat(root: Path, name: str, socket_dir: str):
    identity = wc.resolve_run_workspace_identity(root=root, env={})
    wc.record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id=name,
        state="live",
        socket_dir=socket_dir,
    )
    return identity


def test_frame_incarnation_projects_rename_without_changing_receipt(
    home, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    incarnation = wc.new_uuid7()
    physical = {"seat-a": incarnation}
    monkeypatch.setattr(
        wc, "_frame_session_incarnation", lambda name, _socket: physical.get(name)
    )
    identity = _destination_seat(root, "seat-a", "/socket")
    path = wc.workspace_session_path(identity.vibecrafted_session_id)
    before = path.read_bytes()
    assert (
        wc.read_workspace_session(identity.vibecrafted_session_id)
        .attachments[0]
        .session_incarnation
        == incarnation
    )
    physical["seat-b"] = physical.pop("seat-a")
    # A different server may reuse the old display name without owning this seat.
    physical["seat-a"] = wc.new_uuid7()
    assert (
        wc.runtime_session_owner_root("seat-a", "/socket", runtime_state="live") is None
    )
    assert wc.runtime_session_owner_root(
        "seat-b", "/socket", runtime_state="live"
    ) == str(root)
    assert (
        wc.runtime_session_owner_root("seat-b", "/different", runtime_state="live")
        is None
    )
    resolved = wc.resolve_run_workspace_identity(
        root=root,
        env={},
        runtime_session_id="seat-b",
        socket_dir="/socket",
        runtime_state="live",
    )
    assert resolved.to_env() == identity.to_env()
    wc.record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="seat-b",
        state="live",
        socket_dir="/socket",
    )
    assert path.read_bytes() == before
    replacement = wc.record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="seat-b",
        state="live",
        socket_dir="/socket",
        replaces_runtime_session_id="preserved-dead-seat",
    )
    assert (
        replacement.attachments[0].replaces_runtime_session_id == "preserved-dead-seat"
    )
    before = path.read_bytes()
    physical["seat-b"] = wc.new_uuid7()
    assert (
        wc.runtime_session_owner_root("seat-b", "/socket", runtime_state="live") is None
    )
    with pytest.raises(wc.WorkspaceCatalogError, match="Frame rail"):
        wc.resolve_project_destination(
            root=root,
            requested="seat-a",
            live_names=["seat-a", "seat-b"],
            occupied_names=["seat-a", "seat-b"],
            socket_dir="/socket",
            env=identity.to_env(),
        )
    with pytest.raises(wc.WorkspaceCatalogError, match="incarnation changed"):
        wc.record_runtime_session_attachment(
            workspace_id=identity.workspace_id,
            vibecrafted_session_id=identity.vibecrafted_session_id,
            workspace_instance_id=identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="seat-a",
            state="live",
            socket_dir="/socket",
        )
    assert path.read_bytes() == before


@pytest.mark.parametrize("proof", [None, "different", "unavailable"])
def test_missing_frame_name_with_unproven_live_seat_refuses_duplication(
    home, tmp_path, monkeypatch, proof
):
    root = tmp_path / "project"
    root.mkdir()
    incarnation = wc.new_uuid7()
    physical = {"old": incarnation if proof is not None else None}
    monkeypatch.setattr(
        wc, "_frame_session_incarnation", lambda name, _socket: physical.get(name)
    )
    identity = _destination_seat(root, "old", "/socket")
    before = wc.workspace_session_path(identity.vibecrafted_session_id).read_bytes()
    physical.clear()
    if proof != "unavailable":
        physical["new"] = wc.new_uuid7()
    with pytest.raises(wc.WorkspaceCatalogError, match="Frame rail"):
        wc.resolve_project_destination(
            root=root,
            requested="old",
            live_names=["new"],
            occupied_names=["new"],
            socket_dir="/socket",
            env=identity.to_env(),
        )
    assert (
        wc.workspace_session_path(identity.vibecrafted_session_id).read_bytes()
        == before
    )


@pytest.mark.parametrize("state", ["missing", "dead"])
def test_bound_incarnation_still_allows_confirmed_unavailable_recovery(
    home, tmp_path, monkeypatch, state
):
    root = tmp_path / "project"
    root.mkdir()
    incarnation = wc.new_uuid7()
    monkeypatch.setattr(wc, "_frame_session_incarnation", lambda *_: incarnation)
    original = _destination_seat(root, "seat", "/socket")
    monkeypatch.setattr(wc, "_frame_session_incarnation", lambda *_: None)
    recovered = wc.resolve_run_workspace_identity(
        root=root,
        env={},
        runtime_session_id="seat",
        socket_dir="/socket",
        runtime_state=state,
    )
    assert recovered.vibecrafted_session_id != original.vibecrafted_session_id
    old = wc.read_workspace_session(original.vibecrafted_session_id)
    assert old.attachments[0].state == state
    assert old.attachments[0].session_incarnation == incarnation
    assert (
        next(
            i
            for i in wc.list_instances()
            if i.workspace_instance_id == original.workspace_instance_id
        ).status
        == wc.INSTANCE_STATUS_STALE
    )


def test_legacy_name_refresh_cannot_mint_incarnation_authority(
    home, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(wc, "_frame_session_incarnation", lambda *_: None)
    identity = _destination_seat(root, "reused", "/socket")
    monkeypatch.setattr(wc, "_frame_session_incarnation", lambda *_: wc.new_uuid7())
    receipt = wc.record_runtime_session_attachment(
        workspace_id=identity.workspace_id,
        vibecrafted_session_id=identity.vibecrafted_session_id,
        workspace_instance_id=identity.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="reused",
        state="live",
        socket_dir="/socket",
    )
    assert receipt.attachments[0].session_incarnation is None


def test_duplicate_frame_incarnation_owners_and_host_are_protected(
    home, tmp_path, monkeypatch
):
    from dataclasses import replace

    root = tmp_path / "project"
    root.mkdir()
    incarnation = wc.new_uuid7()
    monkeypatch.setattr(wc, "_frame_session_incarnation", lambda *_: incarnation)
    identity = _destination_seat(root, "old", "/socket")
    assert (
        wc.runtime_session_owner_root("vc-host", "/socket", runtime_state="live")
        is None
    )
    second = wc.materialize_instance(
        workspace_id=identity.workspace_id,
        root=root,
        vibecrafted_session_id=wc.new_uuid7(),
        build_id=identity.build_id,
    )
    wc.record_runtime_session_attachment(
        workspace_id=second.workspace_id,
        vibecrafted_session_id=second.vibecrafted_session_id,
        workspace_instance_id=second.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="second",
        state="live",
        socket_dir="/socket",
    )
    with pytest.raises(wc.WorkspaceCatalogError, match="ambiguous live seats"):
        wc.runtime_session_owner_root("renamed", "/socket", runtime_state="live")
    with wc._catalog_lock(exclusive=True):
        wc._write_instance_unlocked(replace(second, status=wc.INSTANCE_STATUS_STALE))
    assert wc.runtime_session_owner_root(
        "renamed", "/socket", runtime_state="live"
    ) == str(root)
    path = wc.workspace_session_path(identity.vibecrafted_session_id)
    payload = json.loads(path.read_text())
    payload["attachments"][0]["session_incarnation"] = "malformed"
    path.write_text(json.dumps(payload))
    with pytest.raises(wc.WorkspaceCatalogError, match="session_incarnation"):
        wc.runtime_session_owner_root("renamed", "/socket", runtime_state="live")


@pytest.mark.parametrize("payload", ["{broken", "[]", "{}", "[{}]", "[null]"])
def test_frame_incarnation_probe_rejects_unavailable_metadata_and_pins_engine(
    home, tmp_path, monkeypatch, payload
):
    generation = tmp_path / "selected"
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_ROOT", str(generation))
    monkeypatch.setenv("VIBECRAFTED_VC_FRAME_BIN", str(tmp_path / "foreign"))
    calls = []

    def observe(command, **kwargs):
        calls.append(command)
        assert kwargs["env"]["VC_FRAME_SOCKET_DIR"] == "/socket"
        assert kwargs["env"]["ZELLIJ_SOCKET_DIR"] == "/socket"
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    monkeypatch.setattr(wc.subprocess, "run", observe)
    assert wc._frame_session_incarnation("renamed", "/socket") is None
    assert calls == [
        [
            str(generation / "bin/vc-frame"),
            "--session",
            "renamed",
            "action",
            "list-tabs",
            "--json",
            "--all",
        ]
    ]


def test_canonical_tuple_prefers_renamed_seat_among_multiple_project_sessions(
    home, tmp_path, monkeypatch
):
    root = tmp_path / "project"
    root.mkdir()
    incarnation = wc.new_uuid7()
    physical = {"old": incarnation, "other": wc.new_uuid7()}
    monkeypatch.setattr(
        wc, "_frame_session_incarnation", lambda name, _socket: physical.get(name)
    )
    original = _destination_seat(root, "old", "/socket")
    other = wc.materialize_instance(
        workspace_id=original.workspace_id,
        root=root,
        vibecrafted_session_id=wc.new_uuid7(),
        build_id=original.build_id,
    )
    wc.record_runtime_session_attachment(
        workspace_id=other.workspace_id,
        vibecrafted_session_id=other.vibecrafted_session_id,
        workspace_instance_id=other.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="other",
        state="live",
        socket_dir="/socket",
    )
    physical["renamed"] = physical.pop("old")
    environment = {**original.to_env(), "VC_FRAME_SESSION_NAME": "old"}
    arguments = {
        "root": root,
        "requested": "old",
        "live_names": ["renamed", "other"],
        "occupied_names": ["renamed", "other"],
        "socket_dir": "/socket",
        "env": environment,
    }
    route = wc.resolve_project_destination(**arguments)
    assert route["VIBECRAFTED_OPERATOR_SESSION"] == "renamed"
    with pytest.raises(wc.WorkspaceCatalogError, match="multiple live Frame"):
        wc.resolve_project_destination(**{**arguments, "env": {}})
    # Even a complete logical tuple cannot choose between two observations of
    # the same physical incarnation. It must never pick the first sorted name.
    physical["alias"] = incarnation
    with pytest.raises(wc.WorkspaceCatalogError, match="multiple live Frame"):
        wc.resolve_project_destination(
            **{**arguments, "live_names": ["renamed", "other", "alias"]}
        )


def test_destination_preserves_foreign_basename_and_reuses_verified_custom_seat(
    home, tmp_path
):
    target, foreign = tmp_path / "project", tmp_path / "elsewhere"
    target.mkdir()
    foreign.mkdir()
    _destination_seat(foreign, "project", "/private/frame")
    custom = _destination_seat(target, "my-work", "/private/frame")
    before = {p: p.read_bytes() for p in wc.sessions_dir().glob("*.json")}
    route = wc.resolve_project_destination(
        root=target,
        requested="project",
        live_names=["project", "my-work"],
        occupied_names=[],
        socket_dir="/private/frame",
        env={},
    )
    assert route["VIBECRAFTED_OPERATOR_SESSION"] == "my-work"
    assert route["VIBECRAFTED_REQUESTED_FRAME_SESSION"] == "project"
    assert route["VIBECRAFTED_FRAME_DESTINATION_REASON"] == "owned-live-seat"
    assert (
        wc.read_workspace_session(custom.vibecrafted_session_id).attachments[0].state
        == "live"
    )
    assert {p: p.read_bytes() for p in before} == before


def test_destination_stale_recent_is_reconciled_by_existing_missing_lifecycle(
    home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    old = _destination_seat(root, "project-old", "/private/frame")
    route = wc.resolve_project_destination(
        root=root,
        requested="project",
        live_names=["vc-host"],
        occupied_names=[],
        socket_dir="/private/frame",
        env={},
    )
    assert route["VIBECRAFTED_OPERATOR_SESSION"] == "project-old"
    assert route["VIBECRAFTED_FRAME_DESTINATION_REASON"] == "stale-seat-restore"
    assert (
        wc.read_workspace_session(old.vibecrafted_session_id).attachments[0].state
        == "live"
    )
    restored = wc.resolve_run_workspace_identity(
        root=root,
        env={},
        runtime_session_id="project-old",
        socket_dir="/private/frame",
        runtime_state="missing",
    )
    assert restored.vibecrafted_session_id != old.vibecrafted_session_id
    assert (
        wc.read_workspace_session(old.vibecrafted_session_id).attachments[0].state
        == "missing"
    )
    assert (
        wc._runtime_workspace_identity(
            root=root.resolve(),
            runtime_session_id="project-old",
            socket_dir="/private/frame",
        )
        is None
    )


def test_destination_unknown_live_name_gets_free_route_without_adoption(home, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    route = wc.resolve_project_destination(
        root=root,
        requested="project",
        live_names=["project"],
        occupied_names=[],
        socket_dir="/private/frame",
        env={},
    )
    alternate = route["VIBECRAFTED_OPERATOR_SESSION"]
    assert alternate != "project" and alternate.startswith("project-")
    assert route["VIBECRAFTED_FRAME_DESTINATION_REASON"] == "occupied-name-preserved"
    assert not wc.list_instances() and not wc.read_catalog().workspaces
    retry = wc.resolve_project_destination(
        root=root,
        requested="project",
        live_names=["project", alternate],
        occupied_names=[],
        socket_dir="/private/frame",
        env={},
    )
    assert retry["VIBECRAFTED_OPERATOR_SESSION"] != alternate


def test_destination_ambiguous_or_unreadable_identity_cannot_be_readiness(
    home, tmp_path
):
    root = tmp_path / "project"
    root.mkdir()
    first = _destination_seat(root, "custom-a", "/private/frame")
    wc.record_runtime_session_attachment(
        workspace_id=first.workspace_id,
        vibecrafted_session_id=first.vibecrafted_session_id,
        workspace_instance_id=first.workspace_instance_id,
        runtime="vc-frame",
        runtime_session_id="custom-b",
        state="live",
        socket_dir="/private/frame",
    )
    before = wc.workspace_session_path(first.vibecrafted_session_id).read_bytes()
    with pytest.raises(wc.WorkspaceCatalogError, match="multiple live"):
        wc.resolve_project_destination(
            root=root,
            requested="project",
            live_names=["custom-a", "custom-b"],
            occupied_names=[],
            socket_dir="/private/frame",
            env={},
        )
    assert (
        wc.workspace_session_path(first.vibecrafted_session_id).read_bytes() == before
    )
    wc.workspace_session_path(first.vibecrafted_session_id).write_text("{")
    with pytest.raises(wc.WorkspaceCatalogError, match="unreadable"):
        wc.resolve_project_destination(
            root=root,
            requested="project",
            live_names=["custom-a"],
            occupied_names=[],
            socket_dir="/private/frame",
            env={},
        )


def test_concurrent_first_project_resolution_has_one_catalog_owner(
    home, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    root = tmp_path / "first-project"
    root.mkdir()
    entering_create = Barrier(2)
    create = wc.create_workspace

    def concurrent_create(**kwargs):
        entering_create.wait(timeout=10)
        return create(**kwargs)

    monkeypatch.setattr(wc, "create_workspace", concurrent_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: wc.resolve_run_workspace_identity(root=root, env={}), range(2)
            )
        )
    assert results[0].workspace_id == results[1].workspace_id
    assert results[0].workspace_instance_id == results[1].workspace_instance_id
    assert len(wc.read_catalog().workspaces) == 1
