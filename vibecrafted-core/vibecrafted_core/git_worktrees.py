"""Read-only worktree inventory and conservative reception evidence.

Git proves ancestry; existing runtime and dispatch receipts describe owners.
The classifications are inspection results, never authorization to remove a tree.
Cleanup, pruning and merge execution are outside this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .cache_lifecycle import CacheDeferred, observe_unused
from .control_plane import control_plane_home
from .git import _require_git_root

_TERMINAL_RUNS = {"completed", "failed", "stopped", "blocked"}
_ACTIVE_RUNS = {"active", "running", "launching", "queued", "starting", "reported"}
_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_REPO_ENV = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_QUARANTINE_PATH",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_GRAFT_FILE": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
    }


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", *args],
        cwd=root,
        capture_output=True,
        text=True,
        errors="surrogateescape",
        check=False,
        env=_git_env(),
    )


def _required(root: Path, *args: str) -> str:
    result = _run(root, *args)
    if result.returncode:
        raise RuntimeError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _target(root: Path, branch: str | None) -> tuple[str, str]:
    if branch is None:
        return "HEAD", _required(root, "rev-parse", "--verify", "HEAD^{commit}")
    ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
    if _run(root, "check-ref-format", ref).returncode:
        raise RuntimeError("target must name a valid local branch")
    return ref, _required(root, "rev-parse", "--verify", f"{ref}^{{commit}}")


def _registered(root: Path) -> list[dict[str, str]]:
    result = _run(root, "worktree", "list", "--porcelain", "-z")
    if result.returncode:
        raise RuntimeError("cannot enumerate registered worktrees")
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for field in result.stdout.split("\0"):
        if not field:
            if current:
                entries.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value
    if current:
        entries.append(current)
    return entries


def _path_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def _owners() -> tuple[
    dict[str, list[dict[str, Any]]], list[str], list[str], list[str]
]:
    """Read existing canonical records without creating state or synchronizing it."""
    home = control_plane_home()
    owners: dict[str, list[dict[str, Any]]] = {}
    problems: list[str] = []
    protected: set[str] = set()
    active_parents: set[str] = set()

    def _record_durable_paths(
        item: dict[str, Any], fields: tuple[str, ...], source: Path
    ) -> None:
        for field in fields:
            value = item.get(field)
            if value is None or value == "":
                continue
            if not isinstance(value, str) or not Path(value).is_absolute():
                problems.append(str(source))
            else:
                protected.add(value)

    for kind, pattern in (
        ("runtime", "runtime_runs/*/meta.json"),
        ("dispatch", "dispatches/*/receipts.json"),
    ):
        for path in sorted(home.glob(pattern)):
            protected.add(str(path.absolute()))

            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(doc, dict):
                    raise TypeError("not an object")
            except (OSError, ValueError, TypeError):
                problems.append(str(path))
                continue
            if kind == "runtime":
                _record_durable_paths(
                    doc,
                    ("report", "transcript", "artifact_path", "artifact_root"),
                    path,
                )
                if doc.get("status") not in _TERMINAL_RUNS or _pid_live(
                    doc.get("worker_pid") or doc.get("pid")
                ):
                    parent = doc.get("parent_root") or doc.get("root")
                    if isinstance(parent, str) and Path(parent).is_absolute():
                        active_parents.add(parent)
                location = doc.get("worktree_path")
                if location is None or location == "":
                    continue
                if not isinstance(location, str):
                    problems.append(str(path))
                    continue
                owners.setdefault(_path_key(location), []).append(
                    {
                        "kind": kind,
                        "run_id": doc.get("run_id"),
                        "agent": doc.get("agent"),
                        "state": doc.get("status"),
                        "pid": doc.get("worker_pid") or doc.get("pid"),
                        "baseline": doc.get("worktree_baseline_sha")
                        or doc.get("baseline_sha"),
                        "parent_root": doc.get("parent_root"),
                        "report": doc.get("report"),
                        "transcript": doc.get("transcript"),
                        "artifact": doc.get("artifact_path")
                        or doc.get("artifact_root"),
                        "receipt": str(path),
                        "valid": doc.get("run_id") == path.parent.name
                        and doc.get("worktree") is True
                        and Path(location).is_absolute(),
                    }
                )
            else:
                cuts = doc.get("cuts")
                if (
                    doc.get("schema") != "vibecrafted.dispatch-receipts.v1"
                    or doc.get("run_id") != path.parent.name
                    or not isinstance(cuts, dict)
                ):
                    problems.append(str(path))
                    continue
                for cut_id, cut in cuts.items():
                    if not isinstance(cut, dict):
                        problems.append(str(path))
                        continue
                    _record_durable_paths(
                        cut, ("report_path", "transcript_path", "artifact_path"), path
                    )
                    admitted, settled = (
                        cut.get("launch_admitted_epoch_ns"),
                        cut.get("settled_epoch_ns"),
                    )
                    restart_pending = isinstance(admitted, int) and (
                        not isinstance(settled, int) or admitted > settled
                    )
                    if (
                        cut.get("state") not in {"settled", "failed"}
                        or _pid_live(cut.get("pid"))
                        or restart_pending
                    ):
                        parent = doc.get("repo_root")
                        if isinstance(parent, str) and Path(parent).is_absolute():
                            active_parents.add(parent)
                    location = cut.get("worktree_path")
                    if location is None or location == "":
                        continue
                    if not isinstance(location, str):
                        problems.append(str(path))
                        continue
                    owners.setdefault(_path_key(location), []).append(
                        {
                            "kind": kind,
                            "run_id": doc.get("run_id"),
                            "cut_id": cut_id,
                            "provider_run_id": cut.get("provider_run_id"),
                            "state": cut.get("state"),
                            "pid": cut.get("pid"),
                            "baseline": cut.get("baseline_sha"),
                            "parent_root": doc.get("repo_root"),
                            "delivered": cut.get("delivered_commit_sha"),
                            "report": cut.get("report_path"),
                            "transcript": cut.get("transcript_path"),
                            "artifact": cut.get("artifact_path"),
                            "receipt": str(path),
                            "valid": doc.get("schema")
                            == "vibecrafted.dispatch-receipts.v1"
                            and doc.get("run_id") == path.parent.name
                            and cut.get("cut_id") == cut_id
                            and Path(location).is_absolute(),
                            "accepted": cut.get("acceptance") == "verified",
                            "integrator_exclusivity": cut.get("integrator_exclusivity"),
                            "launch_admitted_epoch_ns": cut.get(
                                "launch_admitted_epoch_ns"
                            ),
                            "settled_epoch_ns": cut.get("settled_epoch_ns"),
                        }
                    )
    return owners, problems, sorted(protected), sorted(active_parents)


def _pid_live(pid: Any) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # permissions and ambiguous OS failures do not prove death
    return True


def _payload(root: Path) -> dict[str, Any] | None:
    result = _run(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored=matching",
        "--ignore-submodules=none",
    )
    if result.returncode:
        return None
    counts = {"staged": 0, "unstaged": 0, "untracked": 0, "ignored": 0}
    paths: list[str] = []
    fields = iter(result.stdout.split("\0"))
    for field in fields:
        if not field:
            continue
        code = field[:2]
        paths.append(field)
        if code == "??":
            counts["untracked"] += 1
        elif code == "!!":
            counts["ignored"] += 1
        else:
            counts["staged"] += bool(code[0].strip())
            counts["unstaged"] += bool(code[1].strip())
            if "R" in code or "C" in code:
                paths.append(next(fields, ""))
    # Git status can hide modified tracked files marked assume-unchanged or
    # skip-worktree. Such files are never safe to discard based on status.
    tracked = _run(root, "ls-files", "-v", "-z")
    if tracked.returncode:
        return None
    hidden = sorted(
        field
        for field in tracked.stdout.split("\0")
        if field and (field[0].islower() or field[0] == "S")
    )
    return {**counts, "hidden_tracked": hidden, "paths": sorted(paths)}


def _holders(path: Path) -> str:
    try:
        observe_unused(path)
        return "none-observed"
    except CacheDeferred as exc:
        return str(exc)
    except (OSError, subprocess.SubprocessError):
        return "observation-uncertain"


def worktree_inventory(
    project: str | Path = ".", *, target: str | None = None
) -> dict[str, Any]:
    """Return a read-only, evidence-labelled inventory for one repository."""
    overrides = sorted(_REPO_ENV.intersection(os.environ))
    if overrides:
        raise RuntimeError(
            "unset repository-local Git environment overrides before worktree inspection: "
            + ", ".join(overrides)
        )
    root = _require_git_root(Path(project).expanduser().resolve())
    target_ref, target_sha = _target(root, target)
    entries = _registered(root)
    owners, problems, durable, active_parents = _owners()
    protected_paths: list[tuple[str, str, str]] = []
    for value in durable:
        try:
            protected_paths.append(
                (value, os.path.abspath(value), str(Path(value).resolve()))
            )
        except (OSError, RuntimeError, ValueError):
            problems.append(value)
    rows: list[dict[str, Any]] = []
    primary = _path_key(entries[0]["worktree"]) if entries else ""
    current = _path_key(root)
    invoking_cwd = _path_key(Path.cwd())
    common = _required(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    parent_keys = {_path_key(parent) for parent in active_parents}
    default_branches = {"refs/heads/main", "refs/heads/master"}
    remotes = _run(root, "remote")
    if remotes.returncode != 0:
        problems.append("cannot enumerate Git remotes")
    else:
        for remote in remotes.stdout.splitlines():
            prefix = f"refs/remotes/{remote}/"
            observed = _run(root, "symbolic-ref", "--quiet", f"{prefix}HEAD")
            remote_head = observed.stdout.strip()
            if observed.returncode == 0 and remote_head.startswith(prefix):
                default_branches.add("refs/heads/" + remote_head.removeprefix(prefix))
    parent_commons: dict[str, str | None] = {}
    for entry in entries:
        path = Path(entry["worktree"])
        key = _path_key(path)
        refs = sorted(
            owners.get(key, []), key=lambda o: (str(o["kind"]), str(o["receipt"]))
        )
        reasons: list[str] = []
        state = "unknown"
        tip = entry.get("HEAD", "")
        payload: dict[str, Any] | None = None
        identity: list[int] | None = None
        identity_valid = False
        integration = "unknown"
        divergence: dict[str, int] | None = None
        if _SHA.fullmatch(tip):
            ancestor = _run(root, "merge-base", "--is-ancestor", tip, target_sha)
            integration = (
                "exact_ancestor"
                if ancestor.returncode == 0
                else ("not_ancestor" if ancestor.returncode == 1 else "unknown")
            )
            count = _run(
                root, "rev-list", "--left-right", "--count", f"{target_sha}...{tip}"
            )
            parts = count.stdout.split()
            if (
                count.returncode == 0
                and len(parts) == 2
                and all(p.isdecimal() for p in parts)
            ):
                divergence = {"ahead": int(parts[1]), "behind": int(parts[0])}
        try:
            stat = path.stat()
            if path.is_dir():
                identity = [stat.st_dev, stat.st_ino]
                payload = _payload(path)
        except OSError:
            pass
        if identity is None:
            reasons.append("worktree missing or inaccessible")
        elif payload is None:
            reasons.append("worktree status unavailable")
        else:
            actual_root = _run(path, "rev-parse", "--show-toplevel")
            actual_common = _run(
                path, "rev-parse", "--path-format=absolute", "--git-common-dir"
            )
            actual_head = _run(path, "rev-parse", "--verify", "HEAD^{commit}")
            actual_branch = _run(path, "symbolic-ref", "-q", "HEAD")
            identity_valid = not (
                actual_root.returncode
                or actual_common.returncode
                or actual_head.returncode
                or _path_key(actual_root.stdout.removesuffix("\n")) != key
                or _path_key(actual_common.stdout.removesuffix("\n"))
                != _path_key(common)
                or actual_head.stdout.strip() != tip
                or actual_branch.returncode
                or actual_branch.stdout.strip() != entry.get("branch")
            )
            if not identity_valid:
                reasons.append("worktree Git identity or HEAD is inconsistent")
        if key in {primary, current}:
            reasons.append("primary or current checkout is protected")

        def nested(candidate: str, parent: str) -> bool:
            candidate, parent = os.path.normcase(candidate), os.path.normcase(parent)
            if sys.platform == "darwin":
                candidate, parent = candidate.casefold(), parent.casefold()
            return candidate == parent or candidate.startswith(
                parent.rstrip(os.sep) + os.sep
            )

        if nested(invoking_cwd, key):
            reasons.append("invoking process is inside this worktree")
        if any(nested(parent, key) for parent in parent_keys):
            reasons.append("active or unknown run uses this worktree as a parent")
        durable_inside = [
            value
            for value, lexical, resolved in protected_paths
            if nested(lexical, os.path.abspath(path)) or nested(resolved, key)
        ]
        if durable_inside:
            reasons.append(
                "durable report, transcript, artifact or receipt is inside worktree"
            )
        if "locked" in entry:
            reasons.append("Git worktree is locked")
        if "prunable" in entry:
            reasons.append("missing registration requires Git metadata pruning")
        branch = entry.get("branch", "")
        if not branch.startswith("refs/heads/"):
            reasons.append("detached or unknown branch cannot be fenced")
        if branch == target_ref:
            reasons.append("target branch checkout is protected")
        if branch in default_branches:
            reasons.append("default or conventional trunk checkout is protected")
        if branch.removeprefix("refs/heads/").startswith(("recovery/", "archive/")):
            reasons.append("recovery or archive checkout is protected")
        if problems:
            reasons.append("ownership record scan incomplete")
        if not refs:
            reasons.append("no canonical worker ownership receipt")
        active = any(_pid_live(ref.get("pid")) for ref in refs)
        if active:
            reasons.append("a recorded PID is alive; process identity may have changed")
        run_refs = [ref for ref in refs if ref["kind"] == "runtime"]
        dispatch_refs = [ref for ref in refs if ref["kind"] == "dispatch"]
        if any(ref.get("state") in _ACTIVE_RUNS for ref in run_refs):
            active = True
            reasons.append("runtime receipt is non-terminal")
        if any(
            ref.get("state") not in _TERMINAL_RUNS | _ACTIVE_RUNS for ref in run_refs
        ):
            reasons.append("runtime liveness is unknown")
        if any(ref.get("state") not in {"settled", "failed"} for ref in dispatch_refs):
            active = True
        if not run_refs:
            reasons.append("runtime termination is unproven")
        if any(not ref.get("valid") for ref in run_refs):
            reasons.append("runtime ownership receipt identity is invalid")
        owner_repo_valid = True
        for ref in refs:
            parent = ref.get("parent_root")
            if (
                not isinstance(parent, str)
                or not parent
                or not Path(parent).is_absolute()
            ):
                reasons.append("receipt parent repository is unproven")
                owner_repo_valid = False
                break
            if parent not in parent_commons:
                try:
                    parent_probe = _run(
                        Path(parent),
                        "rev-parse",
                        "--path-format=absolute",
                        "--git-common-dir",
                    )
                    parent_commons[parent] = (
                        parent_probe.stdout.removesuffix("\n")
                        if parent_probe.returncode == 0
                        else None
                    )
                except OSError:
                    parent_commons[parent] = None
            if not parent_commons[parent] or _path_key(
                parent_commons[parent]
            ) != _path_key(common):
                reasons.append(
                    "receipt parent repository does not match this Git repository"
                )
                owner_repo_valid = False
                break
        if any(
            not ref.get("valid")
            or ref.get("state") != "settled"
            or not ref.get("accepted")
            or ref.get("delivered") != tip
            for ref in dispatch_refs
        ):
            reasons.append(
                "dispatch delivery is incomplete or differs from worktree HEAD"
            )
        if any(ref.get("integrator_exclusivity") is not False for ref in dispatch_refs):
            reasons.append("integrator checkout or exclusivity status is unknown")
        if any(
            ref.get("provider_run_id") not in {run.get("run_id") for run in run_refs}
            for ref in dispatch_refs
        ):
            reasons.append("dispatch provider runtime receipt is missing")
        for ref in dispatch_refs:
            admitted, settled = (
                ref.get("launch_admitted_epoch_ns"),
                ref.get("settled_epoch_ns"),
            )
            if isinstance(admitted, int) and (
                not isinstance(settled, int) or admitted > settled
            ):
                active = True
                reasons.append("dispatch launch was admitted after settlement")
        for ref in refs:
            base = ref.get("baseline")
            if (
                not isinstance(base, str)
                or not _SHA.fullmatch(base)
                or _run(root, "merge-base", "--is-ancestor", base, tip).returncode != 0
            ):
                reasons.append("receipt baseline is absent or not an ancestor of HEAD")
                break
        dirty = payload is not None and (
            any(payload[k] for k in ("staged", "unstaged", "untracked"))
            or bool(payload["hidden_tracked"])
        )
        if dirty:
            reasons.append("tracked, untracked or hidden tracked work exists")
        ignored = payload is not None and bool(payload["ignored"])
        if ignored:
            reasons.append("ignored payload requires separate review")
        if integration == "not_ancestor":
            reasons.append("HEAD is not an exact ancestor of the selected target")
        elif integration == "unknown":
            reasons.append("integration could not be proven")
        holders = "not-checked"
        # An expensive recursive holder check is useful only for a potential deletion.
        if not reasons and not active:
            holders = _holders(path)
            if holders != "none-observed":
                reasons.append(f"process or open-file check: {holders}")
        if active or holders in {"live-open-file", "live-process-in-cache"}:
            state = "working"
        elif dirty:
            state = "recoverable_work"
        elif (
            not identity_valid
            or problems
            or not run_refs
            or not owner_repo_valid
            or any(
                not ref.get("valid") or ref.get("state") not in _TERMINAL_RUNS
                for ref in run_refs
            )
            or (holders not in {"none-observed", "not-checked"})
        ):
            state = "unknown"
        elif integration == "not_ancestor":
            # A cherry-pick or squash may have carried the changes even when
            # ancestry does not. Only a review can settle that question.
            state = "review_reception"
        elif ignored:
            state = "ignored_payload"
        elif integration == "exact_ancestor":
            state = "removable" if not reasons else "integrated"
        rows.append(
            {
                "path": str(path),
                "branch": branch or None,
                "head": tip or None,
                "identity": identity,
                "locked": "locked" in entry,
                "prunable": "prunable" in entry,
                "payload": payload,
                "durable_paths": durable_inside,
                "integration": integration,
                "divergence": divergence,
                "owners": refs,
                "holders": holders,
                "triage": state,
                "reasons": reasons,
            }
        )
    return {
        "schema": "vibecrafted.worktree-inventory.v1",
        "root": str(root),
        "target_ref": target_ref,
        "target_sha": target_sha,
        "ownership_scan_errors": problems,
        "worktrees": rows,
    }
