"""Snapshot open agent sessions and dirty worktrees for machine relocation.

``vibecrafted relocate snapshot`` captures every recently active provider
session (cursor / claude / codex transcripts), active Codescribe leases, and
dirty-or-unpushed worktree state into a single self-contained tarball under
``<vibecrafted-home>/snapshots/``.

``vibecrafted relocate restore <tarball>`` on the target machine drops the
transcripts back into the provider stores, restores leases, runs
``aicx catalog rebuild`` so the sessions register in the frame, and prints a
vc-frame-first resume plan (``vibecrafted resume <agent> --session <id>``
with the native provider command as fallback).

The tarball embeds a copy of the standalone restore entry point so a bare
target machine only needs ``tar`` and ``python3``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

DAY_S = 86400

PROVIDER_STORES = {
    "cursor": ".cursor/projects",
    "claude": ".claude/projects",
    "codex": ".codex/sessions",
}

# vc-frame lane: `vibecrafted resume <agent> --session <uuid>` (tracked run).
# cursor lands in that lane with the fleet adapter cut; until then native.
VC_RESUME_AGENTS = {"claude", "codex", "grok", "junie", "agy"}

NATIVE_RESUME = {
    "cursor": "cursor-agent --resume {sid}",
    "claude": "claude --resume {sid}",
    "codex": "codex resume {sid}",
    "grok": "grok --resume {sid}  # UNVERIFIED — confirm resume flag",
}


def code_repos() -> list[Path]:
    """Repos whose worktrees get snapshotted. ``VC_RELOCATE_REPOS`` (os.pathsep-
    separated) overrides the Founder-machine defaults so the mechanism travels
    to Linux/cloud boxes; ``~/.vibecrafted`` is always appended by the caller."""
    override = os.environ.get("VC_RELOCATE_REPOS", "").strip()
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p.strip()]
    return [
        Path("/Volumes/vc-workspace/vetcoders/vibecrafted-suite/vibecrafted"),
        Path("/Volumes/vc-workspace/Loctree/aicx"),
    ]


def _sh(args: list[str], cwd: Path | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False
    ).stdout


def _read_lease(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def active_leases(home: Path) -> list[dict]:
    leases = []
    lease_root = home / ".codescribe/agent-bridge/leases"
    for f in sorted(lease_root.glob("*.json")):
        d = _read_lease(f)
        if d is None:
            continue
        if d.get("active"):
            leases.append(d)
    return leases


def _cursor_project_cwd(slug: str) -> str:
    # slug: Users-polyversai-vibecrafted -> /Users/polyversai/vibecrafted (best effort)
    parts = slug.split("-")
    for i in range(1, len(parts)):
        candidate = Path("/" + "/".join(parts[:i]) + "/" + "-".join(parts[i:]))
        if candidate.is_dir():
            return str(candidate)
    return "/" + "/".join(parts)


def resume_commands(provider: str, sid: str, cwd: str) -> tuple[str, str]:
    if provider in NATIVE_RESUME:
        native = NATIVE_RESUME[provider].format(sid=sid)
    else:
        native = f"# no native resume known for {provider}"
    native_full = (
        f"cd {cwd} && {native}" if provider in {"cursor", "claude"} else native
    )
    if provider in VC_RESUME_AGENTS:
        return f"vibecrafted resume {provider} --session {sid}", native_full
    return (
        f"# vc-frame lane pending cursor fleet adapter — native: {native_full}",
        native_full,
    )


def collect_sessions(now: float, max_age_s: float, home: Path) -> list[dict]:
    sessions: list[dict] = []
    lease_sids = {l.get("provider_session_id") for l in active_leases(home)}

    for provider, rel_store in PROVIDER_STORES.items():
        root = home / rel_store
        if not root.is_dir():
            continue
        for f in root.rglob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            age = now - st.st_mtime
            if age > max_age_s:
                continue
            sid = f.stem
            if provider == "codex":
                m = re.search(
                    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                    f.name,
                )
                sid = m.group(1) if m else f.stem
            if provider == "cursor":
                parts = f.relative_to(root).parts
                slug = parts[0] if len(parts) > 2 else ""
                cwd = _cursor_project_cwd(slug) if slug else str(home)
            elif provider == "claude":
                slug = f.parent.name
                cwd = "/" + slug.lstrip("-").replace("-", "/")
                cwd = cwd if Path(cwd).is_dir() else str(home)
            else:
                cwd = str(home)
            reasons = ["active-today" if age < DAY_S else "active-window"]
            if sid in lease_sids:
                reasons.append("codescribe-lease")
            vc_resume, native_resume = resume_commands(provider, sid, cwd)
            sessions.append(
                {
                    "provider": provider,
                    "session_id": sid,
                    "transcript": str(f),
                    "rel_transcript": f"{provider}/{f.relative_to(root)}",
                    "cwd": cwd,
                    "mtime": datetime.fromtimestamp(
                        st.st_mtime, timezone.utc
                    ).isoformat(),
                    "size": st.st_size,
                    "resume": vc_resume,
                    "resume_native": native_resume,
                    "reasons": reasons,
                }
            )
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def collect_worktrees(repos: Sequence[Path], home: Path) -> list[dict]:
    seen: set[str] = set()
    entries: list[dict] = []
    for repo in [*repos, home / ".vibecrafted"]:
        if not (repo / ".git").exists() and not (repo / "HEAD").exists():
            continue
        for line in _sh(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"]
        ).splitlines():
            if not line.startswith("worktree "):
                continue
            wt = line.split(" ", 1)[1]
            if wt in seen:
                continue
            seen.add(wt)
            branch = _sh(["git", "-C", wt, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
            head = _sh(["git", "-C", wt, "rev-parse", "HEAD"]).strip()
            status = _sh(["git", "-C", wt, "status", "--porcelain"]).strip()
            upstream = _sh(
                [
                    "git",
                    "-C",
                    wt,
                    "rev-parse",
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{u}",
                ]
            ).strip()
            unpushed = ""
            if upstream:
                unpushed = _sh(
                    ["git", "-C", wt, "log", "--format=%H %s", "@{u}..HEAD"]
                ).strip()
            entries.append(
                {
                    "path": wt,
                    "repo": str(repo),
                    "branch": branch,
                    "head": head,
                    "dirty": bool(status),
                    "status": status,
                    "upstream": upstream or None,
                    "unpushed": unpushed.splitlines() if unpushed else [],
                }
            )
    return entries


def do_snapshot(
    out_root: Path | None, home: Path, repos: Sequence[Path] | None = None
) -> Path:
    from .runtime_paths import vibecrafted_home

    if repos is None:
        repos = code_repos()

    now = datetime.now(timezone.utc).astimezone()
    stamp = now.strftime("%Y-%m-%dT%H%M%S")
    root = out_root or vibecrafted_home() / "snapshots"
    snap_dir = root / f"relocate-{stamp}"
    snap_dir.mkdir(parents=True, exist_ok=False)

    sessions = collect_sessions(now.timestamp(), max_age_s=2 * DAY_S, home=home)
    leases = active_leases(home)
    worktrees = collect_worktrees(repos, home)

    for s in sessions:
        src = Path(s["transcript"])
        dst = snap_dir / "transcripts" / s["rel_transcript"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    lease_dir = snap_dir / "codescribe/leases"
    lease_dir.mkdir(parents=True, exist_ok=True)
    for l in leases:
        (lease_dir / f"{l.get('lease_id', 'unknown')}.json").write_text(
            json.dumps(l, indent=2)
        )

    wt_dir = snap_dir / "worktrees"
    wt_dir.mkdir(exist_ok=True)
    for w in worktrees:
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", w["path"]).strip("_")[-80:]
        if w["dirty"]:
            patch = _sh(["git", "-C", w["path"], "diff", "HEAD"])
            if patch:
                (wt_dir / f"{slug}.patch").write_text(patch)
        if w["unpushed"] and w["upstream"]:
            fp = _sh(
                [
                    "git",
                    "-C",
                    w["path"],
                    "format-patch",
                    "--stdout",
                    f"{w['upstream']}..HEAD",
                ]
            )
            if fp:
                (wt_dir / f"{slug}.unpushed.patch").write_text(fp)

    manifest = {
        "schema": "vc-relocate.snapshot.v1",
        "created_at": now.astimezone().isoformat(),
        "host": os.uname().nodename,
        "home": str(home),
        "sessions": sessions,
        "leases": leases,
        "worktrees": worktrees,
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    shutil.copy2(Path(__file__).resolve(), snap_dir / "vc-relocate.py")

    lines = [
        "# RESTORE — vc-relocate",
        "",
        f"Snapshot: {stamp} from {os.uname().nodename}",
        "",
        "## Quick restore on the new machine",
        "",
        "```bash",
        f"tar xzf relocate-{stamp}.tar.gz",
        f"vibecrafted relocate restore relocate-{stamp}",
        "```",
        "",
        "## Sessions captured",
        "",
    ]
    for s in sessions:
        lines.append(
            f"- [{s['provider']}] `{s['session_id']}` ({', '.join(s['reasons'])})"
        )
        lines.append(f"  `{s['resume']}`")
    lines += ["", "## Dirty worktrees", ""]
    for w in worktrees:
        if w["dirty"] or w["unpushed"]:
            lines.append(
                f"- `{w['path']}` [{w['branch']}] dirty={w['dirty']} unpushed={len(w['unpushed'])}"
            )
    (snap_dir / "RESTORE.md").write_text("\n".join(lines) + "\n")

    tarball = root / f"relocate-{stamp}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(snap_dir, arcname=snap_dir.name)
    return tarball


def do_restore(src: Path, target_home: Path, apply_patches: bool) -> int:
    if src.is_file():
        tmp = Path(tempfile.mkdtemp(prefix="vc-relocate-"))
        shutil.unpack_archive(src, tmp)  # tarball is self-produced by `snapshot`
        dirs = [d for d in tmp.iterdir() if d.is_dir()]
        if not dirs:
            print(f"error: no snapshot directory in {src}", file=sys.stderr)
            return 1
        src = dirs[0]
    manifest = json.loads((src / "manifest.json").read_text())
    real_home = Path.home()
    restored = 0
    for s in manifest["sessions"]:
        rel = s["rel_transcript"]
        provider = s["provider"]
        store = target_home / PROVIDER_STORES[provider]
        dst = store / Path(rel).relative_to(provider)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"skip (exists): {dst}")
            continue
        shutil.copy2(src / "transcripts" / rel, dst)
        restored += 1
    lease_src = src / "codescribe/leases"
    if lease_src.is_dir():
        lease_dst = target_home / ".codescribe/agent-bridge/leases"
        lease_dst.mkdir(parents=True, exist_ok=True)
        for f in lease_src.glob("*.json"):
            if not (lease_dst / f.name).exists():
                shutil.copy2(f, lease_dst / f.name)

    print(f"\nrestored {restored} session transcripts into {target_home}\n")

    aicx = shutil.which("aicx")
    if aicx and restored and target_home == real_home:
        r = subprocess.run(
            [aicx, "catalog", "rebuild"], capture_output=True, text=True, check=False
        )
        cataloged = r.returncode == 0
        print(
            "aicx catalog rebuild: "
            + ("OK — sessions registered in the frame" if cataloged else "FAILED")
        )
        if not cataloged:
            print(r.stderr[-500:])
    elif target_home != real_home:
        print("custom --target-home: skipping aicx catalog rebuild (dry-run mode)")
    elif not aicx:
        print(
            "aicx not on PATH — run `aicx catalog rebuild` to register sessions in the frame"
        )

    print("\n== Resume plan (vc-frame first, native fallback) ==")
    for s in manifest["sessions"]:
        print(f"[{s['provider']:6}] {s['session_id']}  ({s['mtime'][:16]})")
        print(f"         frame : {s['resume']}")
        print(f"         native: {s.get('resume_native', '-')}")
    dirty = [w for w in manifest["worktrees"] if w["dirty"] or w["unpushed"]]
    if dirty:
        print("\n== Worktrees with state ==")
        for w in dirty:
            slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", w["path"]).strip("_")[-80:]
            print(f"- {w['path']} [{w['branch']}]")
            for suffix in (".unpushed.patch", ".patch"):
                p = src / "worktrees" / f"{slug}{suffix}"
                if not p.exists():
                    continue
                if apply_patches and Path(w["path"]).is_dir():
                    r = subprocess.run(
                        ["git", "-C", w["path"], "apply", "--3way", str(p)], check=False
                    )
                    print(f"    applied {p.name}: exit {r.returncode}")
                else:
                    print(f"    patch: {p} (run with --apply-patches to apply)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vibecrafted relocate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot", help="capture open sessions + worktree state")
    sp.add_argument("--out", type=Path, default=None)
    rp = sub.add_parser("restore", help="restore a snapshot on the target machine")
    rp.add_argument("src", type=Path)
    rp.add_argument("--target-home", type=Path, default=Path.home())
    rp.add_argument("--apply-patches", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    home = Path.home()
    if args.cmd == "snapshot":
        tarball = do_snapshot(args.out, home)
        print(f"snapshot: {tarball}")
        return 0
    return do_restore(args.src, args.target_home, args.apply_patches)
