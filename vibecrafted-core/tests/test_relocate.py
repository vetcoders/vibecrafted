"""Tests for vibecrafted_core.relocate — session snapshot/restore for machine moves."""

from __future__ import annotations

import json
import os
import subprocess
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
        / ".cursor/projects/Users-operator-vibecrafted/agent-transcripts"
        / ("11111111-1111-4111-8111-111111111111")
    )
    cur.mkdir(parents=True)
    (cur / "11111111-1111-4111-8111-111111111111.jsonl").write_text('{"role":"user"}\n')
    # claude transcript (today)
    cla = home / ".claude/projects/-Users-operator-vibecrafted"
    cla.mkdir(parents=True)
    (cla / "22222222-2222-4222-8222-222222222222.jsonl").write_text('{"type":"user"}\n')
    # codex rollout (today)
    cod = home / ".codex/sessions/2026/08/29"
    cod.mkdir(parents=True)
    (
        cod / "rollout-2026-08-29T10-00-00-33333333-3333-4333-8333-333333333333.jsonl"
    ).write_text('{"type":"session"}\n')
    # stale transcript (8 days old — must be excluded)
    old = home / ".claude/projects/-Users-operator-old"
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
    assert manifest["schema"] == relocate.SNAPSHOT_SCHEMA
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


# ---------------------------------------------------------------------------
# Relocation must preserve data, refuse escapes and admit failure.
# Reproduced against c522ad2c before the repair: untracked work never reached
# the archive, a `..` in rel_transcript wrote outside the provider store, and a
# failed `git apply` still returned 0.
# ---------------------------------------------------------------------------

# 0xaa..0xdd carries no NUL byte, so git classifies it as *text* and emits the
# raw bytes into the diff. That is the case that makes a text-mode capture raise.
NO_NUL_BYTES = b"\xaa\xbb\xcc\xdd"
WITH_NUL_BYTES = b"\x00\x01\x02\x03"
NESTED_BYTES = b"\x10\x20\x30\x40\x50"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    _git(path, "config", "user.name", "Relocate Test")
    _git(path, "config", "user.email", "relocate@example.invalid")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def _dirty_repo(path: Path) -> Path:
    """A repo with every class of pending work and no upstream configured."""
    repo = _init_repo(path)
    (repo / "tracked_text.txt").write_text("line 1\n")
    (repo / "tracked_bin.bin").write_bytes(WITH_NUL_BYTES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")

    # staged: a text edit plus a brand-new file git will treat as text
    (repo / "tracked_text.txt").write_text("line 1\nline 2 staged\n")
    (repo / "staged_new.bin").write_bytes(NO_NUL_BYTES)
    _git(repo, "add", "tracked_text.txt", "staged_new.bin")

    # unstaged on top of the staged state, including a real binary edit
    (repo / "tracked_text.txt").write_text("line 1\nline 2 staged\nline 3 unstaged\n")
    (repo / "tracked_bin.bin").write_bytes(WITH_NUL_BYTES + b"\xff\xfe")

    # untracked, at the root and nested
    (repo / "untracked_root.txt").write_text("untracked text\n")
    (repo / "sub" / "dir").mkdir(parents=True)
    (repo / "sub" / "dir" / "untracked_nested.bin").write_bytes(NESTED_BYTES)
    return repo


def _snapshot_dir(out: Path, tarball: Path) -> Path:
    return out / tarball.stem.removesuffix(".tar")


def _rewrite_manifest(snap_dir: Path, mutate) -> dict:
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    mutate(manifest)
    (snap_dir / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_roundtrip_staged_unstaged_untracked_binary_no_upstream(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    repo = _dirty_repo(tmp_path / "worktrees" / "repo1")
    out = tmp_path / "snaps"

    tarball = relocate.do_snapshot(out, home, repos=[repo])
    snap_dir = _snapshot_dir(out, tarball)
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    entry = next(w for w in manifest["worktrees"] if w["path"].endswith("/repo1"))

    # no upstream: nothing to push, and the snapshot says so instead of guessing
    assert entry["upstream"] is None
    assert entry["unpushed"] == []
    assert entry["dirty"] is True

    captured = {u["path"]: u for u in entry["untracked"]}
    assert set(captured) == {"untracked_root.txt", "sub/dir/untracked_nested.bin"}
    assert all(u["captured"] for u in captured.values())

    # Wipe every trace of the pending work: whatever comes back came from the
    # snapshot, not from the disk.
    _git(repo, "reset", "--hard", "-q", "HEAD")
    _git(repo, "clean", "-qfdx")
    assert not (repo / "untracked_root.txt").exists()
    assert (repo / "tracked_text.txt").read_text() == "line 1\n"

    target = tmp_path / "newhome"
    target.mkdir()
    assert relocate.do_restore(tarball, target, apply_patches=True) == 0

    # worktree bytes, text and binary alike
    assert (repo / "tracked_text.txt").read_text() == (
        "line 1\nline 2 staged\nline 3 unstaged\n"
    )
    assert (repo / "tracked_bin.bin").read_bytes() == WITH_NUL_BYTES + b"\xff\xfe"
    assert (repo / "staged_new.bin").read_bytes() == NO_NUL_BYTES
    # untracked work, at the root and nested
    assert (repo / "untracked_root.txt").read_text() == "untracked text\n"
    assert (repo / "sub/dir/untracked_nested.bin").read_bytes() == NESTED_BYTES

    # and the index/worktree split survived the move
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    unstaged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert sorted(staged) == ["staged_new.bin", "tracked_text.txt"]
    assert sorted(unstaged) == ["tracked_bin.bin", "tracked_text.txt"]


def test_snapshot_survives_bytes_git_calls_text(tmp_path: Path) -> None:
    """Regression: `git diff` emits raw file bytes for anything without a NUL."""
    home = _make_home(tmp_path)
    repo = _init_repo(tmp_path / "worktrees" / "bin")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    (repo / "high.dat").write_bytes(NO_NUL_BYTES * 64)
    _git(repo, "add", "high.dat")

    tarball = relocate.do_snapshot(tmp_path / "snaps", home, repos=[repo])
    snap_dir = _snapshot_dir(tmp_path / "snaps", tarball)
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    entry = next(w for w in manifest["worktrees"] if w["path"].endswith("/bin"))
    patch = (snap_dir / "worktrees" / f"{entry['slug']}.staged.patch").read_bytes()
    assert NO_NUL_BYTES * 4 in patch


def test_snapshot_leaves_the_source_worktree_untouched(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    repo = _dirty_repo(tmp_path / "worktrees" / "repo1")
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    digests_before = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git/" not in p.relative_to(repo).as_posix()
    }

    relocate.do_snapshot(tmp_path / "snaps", home, repos=[repo])

    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    digests_after = {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git/" not in p.relative_to(repo).as_posix()
    }
    assert before == after
    assert digests_before == digests_after


def test_untracked_symlinks_are_recorded_not_silently_dropped(tmp_path: Path) -> None:
    """Neither symlink shape is copied — but neither disappears from the record.

    A snapshot that quietly drops work is worse than one that names the gap, so
    both the escaping link and the internal one carry an explicit reason.
    """
    home = _make_home(tmp_path)
    repo = _init_repo(tmp_path / "worktrees" / "links")
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    outside = tmp_path / "outside.txt"
    outside.write_text("foreign\n")
    (repo / "link_out").symlink_to(outside)
    (repo / "link_in").symlink_to(repo / "seed.txt")

    relocate.do_snapshot(tmp_path / "snaps", home, repos=[repo])
    snap = next(d for d in (tmp_path / "snaps").iterdir() if d.is_dir())
    manifest = json.loads((snap / "manifest.json").read_text())
    entry = next(w for w in manifest["worktrees"] if w["path"].endswith("/links"))
    recorded = {u["path"]: u for u in entry["untracked"]}
    assert set(recorded) == {"link_out", "link_in"}
    assert recorded["link_out"]["captured"] is False
    assert recorded["link_out"]["reason"] == "path escapes the worktree"
    assert recorded["link_in"]["captured"] is False
    assert recorded["link_in"]["reason"] == "not a regular file"


def test_worktree_slug_separates_paths_sharing_a_long_tail() -> None:
    shared = "/" + "x" * 120 + "/vetcoders/vibecrafted/2026_0913/worker"
    a = "/Volumes/one" + shared
    b = "/Volumes/two" + shared
    assert relocate.legacy_worktree_slug(a) == relocate.legacy_worktree_slug(b)
    assert relocate.worktree_slug(a) != relocate.worktree_slug(b)


def test_restore_refuses_manifest_path_traversal(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    snap_dir = _snapshot_dir(out, tarball)
    (snap_dir / "ESCAPED.txt").write_text("payload\n")

    def mutate(manifest: dict) -> None:
        claude = next(s for s in manifest["sessions"] if s["provider"] == "claude")
        claude["rel_transcript"] = "claude/../../ESCAPED.txt"

    _rewrite_manifest(snap_dir, mutate)

    target = tmp_path / "newhome"
    target.mkdir()
    assert relocate.do_restore(snap_dir, target, apply_patches=False) == 1
    assert not (target / "ESCAPED.txt").exists()
    assert list(target.rglob("ESCAPED.txt")) == []
    # the honest sessions still landed
    assert len(list(target.rglob("*.jsonl"))) == 2


def test_restore_refuses_tarball_traversal_without_writing_outside(
    tmp_path: Path,
) -> None:
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    snap_dir = _snapshot_dir(out, tarball)

    hostile = tmp_path / "hostile.tar.gz"
    with tarfile.open(hostile, "w:gz") as tar:
        tar.add(snap_dir, arcname=snap_dir.name)
        escape = tmp_path / "escape-source.txt"
        escape.write_text("payload\n")
        tar.add(escape, arcname=f"{snap_dir.name}/../../PWNED.txt")

    target = tmp_path / "newhome"
    target.mkdir()
    before = sorted(p.name for p in tmp_path.iterdir())
    assert relocate.do_restore(hostile, target, apply_patches=False) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert not (tmp_path / "PWNED.txt").exists()
    assert list(target.rglob("*.jsonl")) == []


def test_restore_refuses_a_symlink_inside_the_provider_store(tmp_path: Path) -> None:
    """A `..`-free manifest path still escapes through a symlinked subdirectory.

    The store root itself being a symlink is the operator’s own configuration and stays
    honoured; what must be refused is a path that *traverses* a link on its way
    out of the store.
    """
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    snap_dir = _snapshot_dir(out, tarball)

    target = tmp_path / "newhome"
    outside = tmp_path / "outside-store"
    outside.mkdir()
    store = target / ".claude/projects"
    store.mkdir(parents=True)
    (store / "-Users-operator-vibecrafted").symlink_to(
        outside, target_is_directory=True
    )

    assert relocate.do_restore(snap_dir, target, apply_patches=False) == 1
    assert list(outside.rglob("*.jsonl")) == []
    # the two sessions that do not traverse the link still landed
    assert len(list(target.rglob("*.jsonl"))) == 2


def test_restore_returns_nonzero_on_mixed_git_apply_outcomes(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    good = _dirty_repo(tmp_path / "worktrees" / "good")
    bad = _dirty_repo(tmp_path / "worktrees" / "bad")
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[good, bad])
    snap_dir = _snapshot_dir(out, tarball)
    manifest = json.loads((snap_dir / "manifest.json").read_text())
    bad_entry = next(w for w in manifest["worktrees"] if w["path"].endswith("/bad"))

    for repo in (good, bad):
        _git(repo, "reset", "--hard", "-q", "HEAD")
        _git(repo, "clean", "-qfdx")
    # make the bad worktree's preimage unreachable so `git apply` must fail
    (bad / "tracked_text.txt").write_text("an entirely different baseline\n")
    _git(bad, "commit", "-qam", "divergent")

    target = tmp_path / "newhome"
    target.mkdir()
    rc = relocate.do_restore(snap_dir, target, apply_patches=True)
    assert rc == 1

    # the worktree that could be restored *was* restored — no silent rollback
    assert (good / "tracked_text.txt").read_text() == (
        "line 1\nline 2 staged\nline 3 unstaged\n"
    )
    assert (good / "untracked_root.txt").read_text() == "untracked text\n"
    # and the failure is attributed to the worktree it belongs to
    assert (
        bad_entry["slug"]
        != next(w for w in manifest["worktrees"] if w["path"].endswith("/good"))["slug"]
    )


def test_restore_never_overwrites_foreign_content_at_the_destination(
    tmp_path: Path,
) -> None:
    home = _make_home(tmp_path)
    repo = _dirty_repo(tmp_path / "worktrees" / "repo1")
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[repo])
    snap_dir = _snapshot_dir(out, tarball)

    _git(repo, "reset", "--hard", "-q", "HEAD")
    _git(repo, "clean", "-qfdx")
    # foreign work already on the destination under the same name
    (repo / "untracked_root.txt").write_text("DESTINATION WORK, NOT THE SNAPSHOT\n")

    target = tmp_path / "newhome"
    target.mkdir()
    rc = relocate.do_restore(snap_dir, target, apply_patches=True)
    assert rc == 1
    assert (repo / "untracked_root.txt").read_text() == (
        "DESTINATION WORK, NOT THE SNAPSHOT\n"
    )
    # the non-colliding untracked file still came back
    assert (repo / "sub/dir/untracked_nested.bin").read_bytes() == NESTED_BYTES


def test_restore_accepts_identical_untracked_content_at_the_destination(
    tmp_path: Path,
) -> None:
    home = _make_home(tmp_path)
    repo = _dirty_repo(tmp_path / "worktrees" / "repo1")
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[repo])
    snap_dir = _snapshot_dir(out, tarball)

    _git(repo, "reset", "--hard", "-q", "HEAD")
    _git(repo, "clean", "-qfdx")
    (repo / "untracked_root.txt").write_text("untracked text\n")

    target = tmp_path / "newhome"
    target.mkdir()
    assert relocate.do_restore(snap_dir, target, apply_patches=True) == 0


def test_restore_refuses_an_unknown_schema(tmp_path: Path) -> None:
    home = _make_home(tmp_path)
    out = tmp_path / "snaps"
    tarball = relocate.do_snapshot(out, home, repos=[])
    snap_dir = _snapshot_dir(out, tarball)
    _rewrite_manifest(snap_dir, lambda m: m.__setitem__("schema", "something.else.v9"))

    target = tmp_path / "newhome"
    target.mkdir()
    assert relocate.do_restore(snap_dir, target, apply_patches=False) == 1
    assert list(target.rglob("*.jsonl")) == []
