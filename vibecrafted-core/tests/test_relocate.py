"""Tests for vibecrafted_core.relocate — session snapshot/restore for machine moves."""

from __future__ import annotations

import json
import os
import tarfile
import time
from pathlib import Path

import pytest
from vibecrafted_core import relocate


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    # cursor transcript (today)
    cur = (
        home
        / ".cursor/projects/Users-polyversai-vibecrafted/agent-transcripts"
        / ("11111111-1111-4111-8111-111111111111")
    )
    cur.mkdir(parents=True)
    (cur / "11111111-1111-4111-8111-111111111111.jsonl").write_text('{"role":"user"}\n')
    # claude transcript (today)
    cla = home / ".claude/projects/-Users-polyversai-vibecrafted"
    cla.mkdir(parents=True)
    (cla / "22222222-2222-4222-8222-222222222222.jsonl").write_text('{"type":"user"}\n')
    # codex rollout (today)
    cod = home / ".codex/sessions/2026/08/29"
    cod.mkdir(parents=True)
    (
        cod / "rollout-2026-08-29T10-00-00-33333333-3333-4333-8333-333333333333.jsonl"
    ).write_text('{"type":"session"}\n')
    # stale transcript (8 days old — must be excluded)
    old = home / ".claude/projects/-Users-polyversai-old"
    old.mkdir(parents=True)
    stale = old / "99999999-9999-4999-8999-999999999999.jsonl"
    stale.write_text('{"type":"user"}\n')
    old_ts = time.time() - 8 * relocate.DAY_S
    os.utime(stale, (old_ts, old_ts))
    # active codescribe lease pointing at the cursor session
    leases = home / ".codescribe/agent-bridge/leases"
    leases.mkdir(parents=True)
    (leases / "lease-1.json").write_text(
        json.dumps(
            {
                "lease_id": "lease-1",
                "name": "kimi",
                "provider": "cursor",
                "provider_session_id": "11111111-1111-4111-8111-111111111111",
                "active": True,
            }
        )
    )
    return home


def test_collect_sessions_finds_active_providers(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    sessions = relocate.collect_sessions(time.time(), 2 * relocate.DAY_S, home)
    by_provider = {s["provider"] for s in sessions}
    assert by_provider == {"cursor", "claude", "codex"}
    assert all("99999999" not in s["session_id"] for s in sessions)


def test_codex_session_id_extracted_from_rollout_name(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    sessions = relocate.collect_sessions(time.time(), 2 * relocate.DAY_S, home)
    codex = next(s for s in sessions if s["provider"] == "codex")
    assert codex["session_id"] == "33333333-3333-4333-8333-333333333333"


def test_lease_marks_session_reason(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    sessions = relocate.collect_sessions(time.time(), 2 * relocate.DAY_S, home)
    cursor = next(s for s in sessions if s["provider"] == "cursor")
    assert "codescribe-lease" in cursor["reasons"]


def test_resume_commands_vc_frame_first() -> None:
    frame, native = relocate.resume_commands("claude", "abc", "/tmp/x")
    assert frame == "vibecrafted resume claude --session abc"
    assert "claude --resume abc" in native
    frame, native = relocate.resume_commands("cursor", "abc", "/tmp/x")
    assert "cursor-agent --resume abc" in frame
    assert "{sid}" not in frame and "{sid}" not in native


def test_snapshot_restore_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    assert tarball.is_file()

    with tarfile.open(tarball) as tar:
        names = tar.getnames()
    assert any(n.endswith("manifest.json") for n in names)
    assert any(n.endswith("vc-relocate.py") for n in names)
    assert any(n.endswith("RESTORE.md") for n in names)

    snap_dir = out / tarball.stem.removesuffix(".tar")
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    assert manifest["schema"] == "vc-relocate.snapshot.v1"
    assert len(manifest["sessions"]) == 3
    assert len(manifest["leases"]) == 1

    target = tmp_path / "newhome"
    target.mkdir()
    rc = relocate.do_restore(tarball, target, apply_patches=False)
    assert rc == 0
    restored = list(target.rglob("*.jsonl"))
    assert len(restored) == 3
    assert (target / ".codescribe/agent-bridge/leases/lease-1.json").is_file()


def test_restore_skips_existing(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    target = tmp_path / "newhome"
    target.mkdir()
    relocate.do_restore(tarball, target, apply_patches=False)
    # second restore must not fail and must not duplicate
    rc = relocate.do_restore(tarball, target, apply_patches=False)
    assert rc == 0
    assert len(list(target.rglob("*.jsonl"))) == 3


def test_code_repos_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without the override there are no baked-in machine defaults: a shipped
    # payload must not name any operator's checkout (payload-hygiene refuses
    # host literals), so only ~/.vibecrafted — appended by the caller — travels.
    monkeypatch.delenv("VC_RELOCATE_REPOS", raising=False)
    assert relocate.code_repos() == []
    monkeypatch.setenv("VC_RELOCATE_REPOS", f"/srv/a{os.pathsep}/srv/b")
    assert relocate.code_repos() == [Path("/srv/a"), Path("/srv/b")]
