"""Git truth and the public ``vc-git`` operator command."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def _git(
    path: Path, *args: str, check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run one git subcommand in `path`, returning the raw completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=check,
    )


def _git_text(path: Path, *args: str, default: str = "") -> str:
    """Return trimmed stdout for one git command, or `default` on non-zero exit."""
    result = _git(path, *args)
    if result.returncode != 0:
        return default
    return result.stdout.strip()


def _git_lines(path: Path, *args: str) -> list[str]:
    """Return non-blank stdout lines for one git command."""
    text = _git_text(path, *args)
    return [line for line in text.splitlines() if line.strip()]


def _git_root(path: Path) -> Path:
    """Resolve the repo toplevel for `path`; falls back to `path` if not a repo."""
    root = _git_text(path, "rev-parse", "--show-toplevel")
    return Path(root).resolve() if root else path.resolve()


def _require_git_root(path: Path) -> Path:
    """Resolve the repo toplevel for `path`, raising RuntimeError if not a git repo."""
    try:
        result = _git(path, "rev-parse", "--show-toplevel")
    except FileNotFoundError as exc:
        raise RuntimeError("git executable is not available on PATH") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        message = f"not a git repository: {path}"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)
    return Path(result.stdout.strip()).resolve()


def _divergence(path: Path, reference: str) -> dict[str, Any]:
    """Return explicit HEAD divergence truth without inventing zeroes on failure."""
    if not reference:
        return {
            "reference": None,
            "status": "not_configured",
            "ahead": None,
            "behind": None,
        }
    try:
        result = _git(
            path, "rev-list", "--left-right", "--count", f"HEAD...{reference}"
        )
    except OSError as exc:
        return {
            "reference": reference,
            "status": "unknown",
            "ahead": None,
            "behind": None,
            "error": str(exc),
        }
    parts = result.stdout.split()
    if result.returncode != 0 or len(parts) != 2:
        return {
            "reference": reference,
            "status": "unknown",
            "ahead": None,
            "behind": None,
        }
    try:
        ahead, behind = (int(parts[0]), int(parts[1]))
    except ValueError:
        return {
            "reference": reference,
            "status": "unknown",
            "ahead": None,
            "behind": None,
        }
    return {"reference": reference, "status": "known", "ahead": ahead, "behind": behind}


def _status_counts(path: Path) -> dict[str, int] | None:
    """Tally staged/unstaged/untracked files from `git status --porcelain`."""
    staged = unstaged = untracked = 0
    # Do not route porcelain through ``_git_text``: its outer ``strip()``
    # removes the first line's significant leading index-column space.
    result = _git(path, "status", "--porcelain")
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("??"):
            untracked += 1
            continue
        if line[:1].strip():
            staged += 1
        if line[1:2].strip():
            unstaged += 1
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def _remotes(path: Path) -> dict[str, dict[str, str]]:
    """Map remote name -> {fetch/push: url} from `git remote -v`."""
    remotes: dict[str, dict[str, str]] = {}
    for line in _git_lines(path, "remote", "-v"):
        parts = line.split()
        if len(parts) < 3:
            continue
        name, url, kind = parts[:3]
        remotes.setdefault(name, {})[kind.strip("()")] = url
    return remotes


def _recent_commits(path: Path, limit: int = 10) -> list[dict[str, str]]:
    """Return up to `limit` recent commits as short/full/date/author/title dicts."""
    commits: list[dict[str, str]] = []
    for line in _git_lines(
        path,
        "log",
        f"-n{limit}",
        "--date=short",
        "--pretty=format:%h%x00%H%x00%ad%x00%an%x00%s",
    ):
        parts = line.split("\x00")
        if len(parts) != 5:
            continue
        short, full, date, author, title = parts
        commits.append(
            {
                "short": short,
                "full": full,
                "date": date,
                "author": author,
                "title": title,
            }
        )
    return commits


def _worktrees(path: Path) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` into one dict per worktree entry."""
    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in _git_lines(path, "worktree", "list", "--porcelain"):
        if not line:
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                worktrees.append(current)
            current = {"path": value}
        elif current:
            current[key] = value
    if current:
        worktrees.append(current)
    return worktrees


def _worktree_integration(
    root: Path, worktree: dict[str, Any], target: str
) -> dict[str, Any]:
    """Classify a worktree tip against the invoking worktree's HEAD, read-only."""
    source = worktree.get("HEAD", "")
    if not source or not target:
        return {"status": "unknown", "target": target or None}
    try:
        ancestor = _git(root, "merge-base", "--is-ancestor", source, target)
    except OSError as exc:
        return {"status": "unknown", "target": target, "error": str(exc)}
    if ancestor.returncode == 0:
        return {"status": "merged", "target": target, "evidence": "exact_ancestor"}
    if ancestor.returncode != 1:
        return {"status": "unknown", "target": target}
    try:
        unique_merges = _git(root, "rev-list", "--merges", f"{target}..{source}")
    except OSError as exc:
        return {"status": "unknown", "target": target, "error": str(exc)}
    if unique_merges.returncode != 0:
        return {"status": "unknown", "target": target}
    merge_commits = unique_merges.stdout.split()
    # git cherry deliberately ignores merge commits. An empty result therefore
    # cannot prove that a range containing one was integrated by patch.
    if merge_commits:
        try:
            same_tree = _git(root, "diff", "--quiet", target, source)
        except OSError as exc:
            return {"status": "unknown", "target": target, "error": str(exc)}
        if same_tree.returncode == 0:
            return {
                "status": "integrated_by_tree_equivalence",
                "target": target,
                "evidence": "exact_tree_equivalence",
                "unique_merge_commits": merge_commits,
            }
        if same_tree.returncode == 1:
            return {
                "status": "unmerged",
                "target": target,
                "unique_merge_commits": merge_commits,
            }
        return {"status": "unknown", "target": target}
    try:
        equivalent = _git(root, "cherry", "--abbrev", target, source)
    except OSError as exc:
        return {"status": "unknown", "target": target, "error": str(exc)}
    if equivalent.returncode != 0:
        return {"status": "unknown", "target": target}
    unmatched = [
        line.split(maxsplit=1)[1]
        for line in equivalent.stdout.splitlines()
        if line.startswith("+") and len(line.split(maxsplit=1)) == 2
    ]
    if not unmatched:
        return {
            "status": "integrated_by_patch_equivalence",
            "target": target,
            "evidence": "all_unique_patches_equivalent",
            "unmatched_commits": [],
        }
    return {"status": "unmerged", "target": target, "unmatched_commits": unmatched}


def _worktree_details(root: Path, target: str) -> list[dict[str, Any]]:
    """Add observable local state and integration truth to porcelain worktrees."""
    details: list[dict[str, Any]] = []
    for item in _worktrees(root):
        worktree: dict[str, Any] = dict(item)
        location = Path(item["path"])
        worktree["locked"] = "locked" in item
        worktree["prunable"] = "prunable" in item
        worktree["comparison_target"] = target
        if not location.is_dir():
            worktree["availability"] = "missing"
            worktree["status"] = None
        else:
            worktree["availability"] = "available"
            try:
                worktree["status"] = _status_counts(location)
            except OSError as exc:
                worktree["availability"] = "unknown"
                worktree["status"] = None
                worktree["status_error"] = str(exc)
        worktree["integration"] = _worktree_integration(root, worktree, target)
        details.append(worktree)
    return details


def repo_full(path: str | Path = ".") -> dict[str, Any]:
    """Return compact repo state similar to the operator `repo-full` helper."""
    requested_path = Path(path).expanduser().resolve()
    root = _require_git_root(requested_path)
    branch = _git_text(root, "branch", "--show-current", default="HEAD")
    upstream = _git_text(
        root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
    )
    upstream_divergence = _divergence(root, upstream)
    status_counts = _status_counts(root)
    default_remote = (
        _git_text(root, "remote").splitlines()[0] if _git_text(root, "remote") else ""
    )
    default_branch = ""
    if default_remote:
        default_ref = _git_text(
            root, "symbolic-ref", f"refs/remotes/{default_remote}/HEAD"
        )
        if default_ref:
            default_branch = default_ref.rsplit("/", 1)[-1]

    return {
        "repo": root.name,
        "requested_path": str(requested_path),
        "root": str(root),
        "git_available": True,
        "branch": branch,
        "head_short": _git_text(root, "rev-parse", "--short", "HEAD"),
        "head_full": _git_text(root, "rev-parse", "HEAD"),
        "upstream": upstream,
        "ahead": upstream_divergence["ahead"],
        "behind": upstream_divergence["behind"],
        "upstream_divergence": upstream_divergence,
        "default_remote": default_remote,
        "default_branch": default_branch,
        "remotes": _remotes(root),
        "status": status_counts,
        "stashes": len(_git_lines(root, "stash", "list")),
        "worktrees": _worktree_details(root, _git_text(root, "rev-parse", "HEAD")),
        "recent_commits": _recent_commits(root),
    }


def repo_full_summary(path: str | Path = ".") -> str:
    """Render repo truth with an explicit, operator-visible worktree inventory."""
    state = repo_full(path)
    status = state["status"]
    dirt = (
        f"staged {status['staged']}, unstaged {status['unstaged']}, untracked {status['untracked']}"
        if status is not None
        else "unknown"
    )
    lines = [
        f"# {state['repo']}",
        "",
        f"- Root: {state['root']}",
        f"- Branch: {state['branch']}",
        f"- Upstream: {state['upstream'] or 'not configured'}",
        f"- Ahead: {state['ahead'] if state['ahead'] is not None else 'unknown'} (vs {state['upstream'] or 'not configured'})",
        f"- Behind: {state['behind'] if state['behind'] is not None else 'unknown'} (vs {state['upstream'] or 'not configured'})",
        f"- HEAD: {state['head_short']}",
        f"- Dirt: {dirt}",
        f"- Stashes: {state['stashes']}",
        f"- Worktrees: {len(state['worktrees'])}",
        "",
        "## Worktrees",
    ]
    for worktree in state["worktrees"]:
        branch = worktree.get("branch", "detached").removeprefix("refs/heads/")
        head = worktree.get("HEAD", "unknown")[:9]
        integration = worktree["integration"]
        state_label = integration["status"].replace("_", " ")
        if integration["status"] == "unmerged":
            if "unmatched_commits" in integration:
                state_label = f"WARN unmerged ({len(integration['unmatched_commits'])} unmatched commits)"
            else:
                state_label = (
                    "WARN unmerged "
                    f"({len(integration.get('unique_merge_commits', []))} unique merge commits; "
                    "patch equivalence unavailable)"
                )
        lines.append(
            f"- {worktree['path']} [{branch}] {head}; target {worktree['comparison_target'][:9] or 'unknown'}; {state_label}; "
            f"dirty {worktree['status']}; locked {worktree['locked']}; prunable {worktree['prunable']}"
        )
    lines.extend(("", "## Recent commits"))
    for commit in state["recent_commits"][:5]:
        lines.append(f"- {commit['short']} {commit['date']} {commit['title']}")
    return "\n".join(lines)


def _run_repo_full_shell(path: str | Path) -> int:
    """Run the original rich ``repo-full`` renderer from packaged runtime data."""
    root = _require_git_root(Path(path).expanduser().resolve())
    dispatch = Path(__file__).parent / "runtime" / "shell" / "lib" / "dispatch.sh"
    result = subprocess.run(
        ["/bin/bash", "-c", 'source "$1"; repo-full', "vc-git", str(dispatch)],
        cwd=root,
        check=False,
    )
    return result.returncode


def compare_parallel_work(
    path: str | Path, dispatch_id: str, cut_ids: list[str]
) -> dict[str, Any]:
    """Compare supervisor-accepted deliveries, never mutable work in progress.

    The existing dispatch ledger owns completion and commit attribution. This
    command only reads it and Git; it grants no merge or deployment authority.
    """
    from .control_plane import control_plane_home, lookup_runtime_run_meta

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", dispatch_id):
        raise RuntimeError("invalid dispatch run id")
    if len(cut_ids) != 2 or len(set(cut_ids)) != 2:
        raise RuntimeError("select two distinct completed cuts with --cuts")
    root = _require_git_root(Path(path).expanduser().resolve())
    receipt_path = control_plane_home() / "dispatches" / dispatch_id / "receipts.json"
    try:
        ledger = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"dispatch receipt unavailable: {receipt_path}") from exc
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema") != "vibecrafted.dispatch-receipts.v1"
        or ledger.get("run_id") != dispatch_id
        or not isinstance(ledger.get("cuts"), dict)
        or not isinstance(ledger.get("repo_root"), str)
        or not ledger["repo_root"]
    ):
        raise RuntimeError("dispatch receipt identity/schema is invalid")

    def git_text(*args: str) -> str:
        result = _git(root, *args)
        if result.returncode:
            raise RuntimeError(f"comparison Git probe failed: {args[0]}")
        return result.stdout

    common = git_text("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    declared = _git(
        Path(ledger["repo_root"]),
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
    )
    if declared.returncode or declared.stdout.strip() != common:
        raise RuntimeError("dispatch belongs to a different or unavailable repository")
    deliveries = []
    # Validate BOTH receipts before inspecting either worker's Git changes.
    for cut_id in cut_ids:
        cut = ledger["cuts"].get(cut_id)
        if (
            not isinstance(cut, dict)
            or cut.get("cut_id") != cut_id
            or cut.get("state") != "settled"
            or cut.get("acceptance") != "verified"
        ):
            raise RuntimeError(
                f"{cut_id}: wait for a settled, verified delivery before comparing"
            )
        provider_id = cut.get("provider_run_id")
        if provider_id:
            if not isinstance(provider_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*", provider_id
            ):
                raise RuntimeError(f"{cut_id}: invalid provider run identity")
            meta = lookup_runtime_run_meta(provider_id)
            if meta is not None and (
                meta.get("run_id") != provider_id
                or meta.get("status") != "completed"
                or meta.get("exit_code") != 0
            ):
                raise RuntimeError(f"{cut_id}: runtime has not completed successfully")
        delivery = {"cut_id": cut_id, "report_path": cut.get("report_path", "")}
        for key in ("baseline_sha", "delivered_commit_sha"):
            sha = cut.get(key)
            if not isinstance(sha, str) or not re.fullmatch(
                r"[0-9a-f]{40}|[0-9a-f]{64}", sha
            ):
                raise RuntimeError(f"{cut_id}: missing immutable {key}")
            if git_text("rev-parse", "--verify", f"{sha}^{{commit}}").strip() != sha:
                raise RuntimeError(f"{cut_id}: invalid commit {key}")
            delivery[key] = sha
        ancestor = _git(
            root,
            "merge-base",
            "--is-ancestor",
            delivery["baseline_sha"],
            delivery["delivered_commit_sha"],
        )
        if ancestor.returncode:
            raise RuntimeError(f"{cut_id}: delivery does not descend from its baseline")
        deliveries.append(delivery)
    for delivery in deliveries:
        base, tip = delivery["baseline_sha"], delivery["delivered_commit_sha"]
        delivery["files"] = (
            git_text(
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--name-only",
                "-z",
                base,
                tip,
            )
            .rstrip("\0")
            .split("\0")
        )
        delivery["files"] = [name for name in delivery["files"] if name]
        delivery["patch"] = git_text(
            "diff", "--no-ext-diff", "--no-textconv", base, tip, "--"
        )
    left, right = deliveries
    comparison = {
        "dispatch_id": dispatch_id,
        "repository": str(root),
        "phase": "before-deploy",
        "deliveries": deliveries,
        "overlapping_files": sorted(set(left["files"]) & set(right["files"])),
        "tip_diff": git_text(
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            left["delivered_commit_sha"],
            right["delivered_commit_sha"],
            "--",
        ),
        "deployment_authorized": False,
    }
    # A retry may replace a receipt while Git is being read. Never publish a
    # comparison against superseded lifecycle/commit attribution.
    try:
        current = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("dispatch receipt changed during comparison; retry") from exc
    if current != ledger:
        raise RuntimeError("dispatch receipt changed during comparison; retry")
    return comparison


def _comparison_summary(comparison: dict[str, Any]) -> str:
    lines = ["# Parallel work comparison before deploy", ""]
    for delivery in comparison["deliveries"]:
        lines.extend(
            (
                f"## {delivery['cut_id']}",
                f"Baseline: {delivery['baseline_sha']}",
                f"Commit: {delivery['delivered_commit_sha']}",
                f"Report: {delivery['report_path']}",
                delivery["patch"] or "No changes from baseline.",
            )
        )
    lines.extend(
        (
            "## Shared files (review overlap; this is not conflict detection)",
            *comparison["overlapping_files"],
            "## Difference between delivered tips",
            comparison["tip_diff"] or "Delivered trees are identical.",
            "Review both reports and patches before choosing integration and deploy. Deployment requires the Founder's decision.",
        )
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Expose the original rich repo renderer as a checkout-free executable."""
    parser = argparse.ArgumentParser(
        prog="vc-git",
        description="Show full Git context, including every worktree.",
    )
    parser.add_argument("project", nargs="?", default=".", help="repository path")
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    parser.add_argument(
        "--compare",
        metavar="DISPATCH_RUN_ID",
        help="compare completed parallel work before deploy",
    )
    parser.add_argument(
        "--cuts",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help="two settled, verified dispatch cuts",
    )
    args = parser.parse_args(argv)
    if bool(args.compare) != bool(args.cuts):
        parser.error("--compare and --cuts must be supplied together")
    try:
        if args.compare:
            comparison = compare_parallel_work(args.project, args.compare, args.cuts)
            print(
                json.dumps(comparison, indent=2, sort_keys=True)
                if args.json
                else _comparison_summary(comparison)
            )
            return 0
        if not args.json:
            return _run_repo_full_shell(args.project)
        print(json.dumps(repo_full(args.project), indent=2, sort_keys=True))
    except (RuntimeError, OSError) as exc:
        print(f"vc-git: {exc}", file=sys.stderr)
        return 2
    return 0
