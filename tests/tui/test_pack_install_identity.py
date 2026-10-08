"""Pack install state must carry the pack's own source identity.

A git checkout still records ``get_repo_commit`` / ``get_repo_url`` and leaves
``repo_origin`` empty. Only the Runtime Pack projection writer fills commit,
URL, and ``repo_origin=pack-provenance`` from the pack files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer

_SHA = "a1b2c3d4" * 5
_OTHER = "0123456789abcdef" * 2 + "01234567"


def _write_pack(
    generation: Path,
    *,
    sha: str | None = _SHA,
    schema: str = installer._PACK_PROVENANCE_SCHEMA,
    source: dict[str, object] | None = None,
    revisions: dict[str, object] | None = None,
) -> None:
    generation.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"schema": schema}
    if revisions is None and sha is not None:
        revisions = {
            "vibecrafted": sha,
            "vc-terminal": _OTHER,
            "vc-frame": _OTHER,
        }
    if revisions is not None:
        payload["source_revisions"] = revisions
    (generation / installer.RUNTIME_PACK_PROVENANCE_NAME).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    if source is not None:
        (generation / installer._PACK_SOURCE_PROVENANCE_NAME).write_text(
            json.dumps(source),
            encoding="utf-8",
        )


def _matching_source(sha: str = _SHA) -> dict[str, object]:
    return {
        "schema": installer._SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": sha,
        "payload": {"schema": "vibecrafted.distribution-tree.v1"},
    }


def test_pack_identity_uses_short_sha_and_confirmed_url(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    _write_pack(generation, source=_matching_source())

    assert installer.pack_install_identity(generation) == (
        _SHA[:8],
        "https://github.com/vetcoders/vibecrafted",
        "pack-provenance",
    )


def test_pack_identity_keeps_sha_when_source_provenance_disagrees(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "generation"
    _write_pack(
        generation,
        source={
            "schema": installer._SOURCE_PROVENANCE_SCHEMA,
            "owner_repo": "someone/else",
            "source_revision": _SHA,
        },
    )

    assert installer.pack_install_identity(generation) == (
        _SHA[:8],
        "",
        "pack-provenance",
    )


def test_pack_identity_rejects_untrusted_revision(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    _write_pack(generation, revisions={"vibecrafted": "not-a-sha"})

    assert installer.pack_install_identity(generation) == ("unknown", "", "")


def test_pack_identity_missing_pack_is_unknown(tmp_path: Path) -> None:
    assert installer.pack_install_identity(tmp_path / "absent") == (
        "unknown",
        "",
        "",
    )


def test_old_install_state_without_origin_loads_blank(tmp_path: Path) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / installer.STATE_FILE).write_text(
        json.dumps({"repo_commit": "abcdef12", "repo_url": "git@example:repo.git"}),
        encoding="utf-8",
    )

    loaded = installer.InstallState.load(store)

    assert loaded.repo_commit == "abcdef12"
    assert loaded.repo_url == "git@example:repo.git"
    assert loaded.repo_origin == ""


def test_pack_projection_writes_provenance_into_install_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    real_home = Path(os.environ.get("HOME", "")).resolve()
    real_state = real_home / ".vibecrafted" / installer.STATE_FILE
    real_stamp = real_state.stat().st_mtime_ns if real_state.exists() else None
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("PYTHONPATH", raising=False)

    sha = "deadbeef" * 5
    generation = tmp_path / "generation"
    skill = generation / "vibecrafted-core/vibecrafted_core/skills/vc-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    _write_pack(generation, sha=sha, source=_matching_source(sha))

    def stage_file(path: Path, content: str, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        path.chmod(mode)

    def stage_symlink(path: Path, target: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        path.symlink_to(target)

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    installer._install_runtime_agent_projections(
        generation,
        version="4.3.1",
        runtime_home=runtime_home,
        receipt={},
        stage_file=stage_file,
        stage_symlink=stage_symlink,
    )

    state_path = home / ".vibecrafted" / installer.STATE_FILE
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["repo_commit"] == sha[:8]
    assert state["repo_url"] == "https://github.com/vetcoders/vibecrafted"
    assert state["repo_origin"] == "pack-provenance"
    loaded = installer.InstallState.load(state_path.parent)
    assert loaded.repo_commit == sha[:8]
    assert loaded.repo_origin == "pack-provenance"
    assert state_path.is_relative_to(home)
    if real_stamp is None:
        assert not real_state.exists()
    else:
        assert real_state.stat().st_mtime_ns == real_stamp
