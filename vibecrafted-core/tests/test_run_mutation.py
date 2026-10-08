from __future__ import annotations

import json
import multiprocessing
import stat
from multiprocessing.connection import Connection
from pathlib import Path

import pytest
from vibecrafted_core.run_mutation import (
    RunMetaMutationError,
    mutate_run_meta,
    read_run_meta,
    run_mutation_locks,
)


def _write_meta(path: Path, *, run_id: str = "run-1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"run_id": run_id, "marker": "birth"}) + "\n",
        encoding="utf-8",
    )


def _acquire_run_lock_in_child(
    control_plane_root: str,
    run_id: str,
    sender: Connection,
) -> None:
    sender.send("attempting")
    with run_mutation_locks(Path(control_plane_root), run_id=run_id):
        sender.send("acquired")
    sender.close()


def test_mutation_merges_latest_payload_and_replaces_with_mode_0600(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "runtime_runs" / "run-1" / "meta.json"
    _write_meta(meta)

    assert mutate_run_meta(
        tmp_path,
        meta_path=meta,
        run_id="run-1",
        mutator=lambda payload: {**payload, "settlement_revision": 4},
    )

    assert read_run_meta(meta, expected_run_id="run-1") == {
        "run_id": "run-1",
        "marker": "birth",
        "settlement_revision": 4,
    }
    assert stat.S_IMODE(meta.stat().st_mode) == 0o600
    assert not list(meta.parent.glob(".meta.json.*.tmp"))


def test_mutation_can_create_post_birth_meta_under_the_same_lock(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "runtime_runs" / "run-1" / "meta.json"
    meta.parent.mkdir(parents=True)

    assert mutate_run_meta(
        tmp_path,
        meta_path=meta,
        run_id="run-1",
        create=True,
        mutator=lambda payload: {**payload, "run_id": "run-1", "state": "spawned"},
    )

    assert read_run_meta(meta, expected_run_id="run-1")["state"] == "spawned"


def test_mutation_can_target_artifact_root_without_moving_shared_lock(
    tmp_path: Path,
) -> None:
    control_plane = tmp_path / "control-plane"
    artifact_root = tmp_path / "artifacts" / "run-1"
    control_plane.mkdir()
    artifact_root.mkdir(parents=True)
    meta = artifact_root / "meta.json"

    assert mutate_run_meta(
        control_plane,
        meta_path=meta,
        mutation_root=artifact_root,
        run_id="run-1",
        create=True,
        mutator=lambda payload: {**payload, "run_id": "run-1", "state": "spawned"},
    )

    assert read_run_meta(meta, expected_run_id="run-1")["state"] == "spawned"
    assert list((control_plane / "run_mutation_locks" / "run").glob("*.lock"))
    assert not (artifact_root / "run_mutation_locks").exists()


def test_mutation_rejects_symlinks_and_wrong_run_identity(tmp_path: Path) -> None:
    meta = tmp_path / "runtime_runs" / "run-1" / "meta.json"
    _write_meta(meta)
    alias = tmp_path / "alias.json"
    alias.symlink_to(meta)

    with pytest.raises(RunMetaMutationError, match="canonical|regular"):
        mutate_run_meta(
            tmp_path,
            meta_path=alias,
            run_id="run-1",
            mutator=lambda payload: payload,
        )
    with pytest.raises(RunMetaMutationError, match="identity mismatch"):
        mutate_run_meta(
            tmp_path,
            meta_path=meta,
            run_id="some-other-run",
            mutator=lambda payload: payload,
        )

    assert read_run_meta(meta, expected_run_id="run-1")["marker"] == "birth"


def test_mutation_rejects_meta_outside_control_plane_root(tmp_path: Path) -> None:
    control_plane = tmp_path / "control-plane"
    control_plane.mkdir()
    outside = tmp_path / "outside" / "meta.json"
    _write_meta(outside)

    with pytest.raises(RunMetaMutationError, match="outside the mutation root"):
        mutate_run_meta(
            control_plane,
            meta_path=outside,
            run_id="run-1",
            mutator=lambda payload: {**payload, "should_not_land": True},
        )

    assert "should_not_land" not in read_run_meta(outside, expected_run_id="run-1")

    allowed_artifacts = tmp_path / "allowed-artifacts"
    allowed_artifacts.mkdir()
    with pytest.raises(RunMetaMutationError, match="outside the mutation root"):
        mutate_run_meta(
            control_plane,
            meta_path=outside,
            mutation_root=allowed_artifacts,
            run_id="run-1",
            mutator=lambda payload: {**payload, "should_not_land": True},
        )

    assert "should_not_land" not in read_run_meta(outside, expected_run_id="run-1")


def test_same_thread_can_reenter_the_same_run_lock(tmp_path: Path) -> None:
    with (
        run_mutation_locks(tmp_path, run_id="run-1"),
        run_mutation_locks(tmp_path, run_id="run-1"),
    ):
        marker = "nested-lock-acquired"

    assert marker == "nested-lock-acquired"


def test_nested_lock_keeps_a_second_process_serialized(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_acquire_run_lock_in_child,
        args=(str(tmp_path), "run-1", sender),
    )

    try:
        with run_mutation_locks(tmp_path, run_id="run-1"):
            with run_mutation_locks(tmp_path, run_id="run-1"):
                process.start()
                sender.close()
                assert receiver.poll(5)
                assert receiver.recv() == "attempting"
            assert not receiver.poll(0.2)

        assert receiver.poll(5)
        assert receiver.recv() == "acquired"
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        receiver.close()


def test_mutation_accepts_symlinked_ancestor_root(tmp_path: Path) -> None:
    """A `$HOME` behind a symlink (`/tmp` → `/private/tmp`) is a valid root."""
    real_root = tmp_path / "real" / "control_plane"
    meta = real_root / "runtime_runs" / "run-1" / "meta.json"
    _write_meta(meta)
    alias_home = tmp_path / "alias-home"
    alias_home.symlink_to(tmp_path / "real", target_is_directory=True)
    alias_root = alias_home / "control_plane"
    alias_meta = alias_root / "runtime_runs" / "run-1" / "meta.json"

    changed = mutate_run_meta(
        alias_root,
        meta_path=alias_meta,
        run_id="run-1",
        mutator=lambda payload: {**payload, "marker": "via-alias"},
    )

    assert changed is True
    assert read_run_meta(meta, expected_run_id="run-1")["marker"] == "via-alias"
    assert read_run_meta(alias_meta, expected_run_id="run-1")["marker"] == "via-alias"


def test_mutation_locks_are_reentrant_across_symlinked_root_spellings(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real" / "control_plane"
    real_root.mkdir(parents=True)
    alias_home = tmp_path / "alias-home"
    alias_home.symlink_to(tmp_path / "real", target_is_directory=True)
    alias_root = alias_home / "control_plane"

    # Outer lock through the alias, inner lock through the resolved root:
    # both must hit one key, or the inner flock blocks forever.
    with (
        run_mutation_locks(alias_root, run_id="run-1"),
        run_mutation_locks(real_root, run_id="run-1"),
    ):
        pass
