"""One repository selector for every repository-aware ``vibecrafted`` command.

Public commands used to grow their own ``--root`` parsing, each with a
different fallback and a different failure shape.  This module is the single
Python owner of that decision so ``--repo`` means the same thing everywhere:

* ``--repo <path>`` selects the repository from ANY working directory,
  including a directory that is not inside Git.
* ``--root <path>`` stays accepted as the explicit legacy spelling.
* Passing both with different values is a conflict and fails loudly; the
  selector never silently prefers one.
* A missing or non-directory path fails with the flag that carried it.
* Commands that need Git ask for it explicitly (``require_git``); commands
  that do not must never demand a ``.git`` just to run.

The shell twin lives in ``runtime/helpers/vetcoders-runtime-core.sh``
(``_vetcoders_select_repo``) and mirrors these rules for deck-owned verbs.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import tomllib

REPO_FLAG = "--repo"
ROOT_FLAG = "--root"
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


class RepoSelectionError(ValueError):
    """The caller's repository selection cannot be honoured as written."""


@dataclass(frozen=True)
class RepoSelection:
    """The resolved repository choice and where it came from."""

    path: str
    source: str  # "repo" | "root" | "fallback"
    flag: str  # the flag that carried the path ("" for fallback)
    git_toplevel: str  # "" when the path is not inside a Git work tree

    @property
    def is_git(self) -> bool:
        return bool(self.git_toplevel)

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "path": self.path,
            "source": self.source,
            "flag": self.flag,
            "git_toplevel": self.git_toplevel,
            "is_git": self.is_git,
        }


def add_repo_arguments(
    parser: argparse.ArgumentParser,
    *,
    root_default: str = "",
    help_text: str = "repository to operate on (usable from any working directory)",
) -> None:
    """Register ``--repo`` and the legacy ``--root`` on one parser.

    Both land on separate destinations so :func:`select_repository` can detect
    a conflicting pair instead of letting argparse keep the last one.
    """
    parser.add_argument(REPO_FLAG, dest="repo", default="", help=help_text)
    parser.add_argument(
        ROOT_FLAG,
        dest="root",
        default=root_default,
        help=f"legacy spelling of {REPO_FLAG}; identical semantics",
    )


def _normalize(raw: str) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def git_toplevel(path: Path, *, env: Mapping[str, str] | None = None) -> str:
    """Return the Git work-tree root that contains ``path``, or ``""``.

    A missing ``git`` binary is the same as "not a repository": repository
    independence must never turn into a crash.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
            env=dict(env) if env is not None else None,
        )
    except (OSError, ValueError):
        return ""
    # Callers stub subprocess.run in tests with bare namespaces; a missing
    # field is "no answer", never a crash inside the selector.
    if getattr(proc, "returncode", 1) != 0:
        return ""
    top = str(getattr(proc, "stdout", "") or "").strip()
    if not top:
        return ""
    return str(Path(top).expanduser().resolve(strict=False))


def select_repository(
    repo: str | os.PathLike[str] | None,
    root: str | os.PathLike[str] | None = None,
    *,
    fallback: str
    | os.PathLike[str]
    | Callable[[], str | os.PathLike[str]]
    | None = None,
    require_git: bool = False,
    label: str = "",
    env: Mapping[str, str] | None = None,
) -> RepoSelection:
    """Resolve the repository one command should operate on.

    ``repo`` and ``root`` are the raw values of ``--repo`` / ``--root``.  When
    neither is given, ``fallback`` (a path or a zero-argument callable) decides;
    an empty fallback means "no repository selected" and raises.
    """
    prefix = f"{label}: " if label else ""
    raw_repo = str(repo or "").strip()
    raw_root = str(root or "").strip()

    if raw_repo and raw_root:
        repo_path = _normalize(raw_repo)
        root_path = _normalize(raw_root)
        if repo_path != root_path:
            raise RepoSelectionError(
                f"{prefix}conflicting {REPO_FLAG} {raw_repo} and {ROOT_FLAG} {raw_root}; "
                f"pass one repository ({REPO_FLAG} is the standard spelling)"
            )
    if raw_repo:
        chosen, source, flag = raw_repo, "repo", REPO_FLAG
    elif raw_root:
        chosen, source, flag = raw_root, "root", ROOT_FLAG
    else:
        resolved_fallback = fallback() if callable(fallback) else fallback
        chosen = str(resolved_fallback or "").strip()
        source, flag = "fallback", ""
        if not chosen:
            raise RepoSelectionError(
                f"{prefix}no repository selected; pass {REPO_FLAG} <path>"
            )

    path = _normalize(chosen)
    if not path.exists():
        raise RepoSelectionError(
            f"{prefix}{flag or 'repository'} is not an existing directory: {chosen}"
        )
    if not path.is_dir():
        raise RepoSelectionError(
            f"{prefix}{flag or 'repository'} is not a directory: {chosen}"
        )
    toplevel = git_toplevel(path, env=env)
    if require_git and not toplevel:
        raise RepoSelectionError(
            f"{prefix}{flag or 'repository'} is not inside a Git repository: {path}"
        )
    return RepoSelection(
        path=str(path),
        source=source,
        flag=flag,
        git_toplevel=toplevel,
    )


def resolve_repository_identity(identity: str) -> tuple[str, str]:
    """Prepare an explicitly configured repository on this execution host.

    The existing product config owns the catalog; hosts are never inferred
    from an org/name spelling. Cache mutation alone is serialized.
    """
    from .runtime_paths import vibecrafted_home

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", identity
    ):
        raise RepoSelectionError("invalid repository identity; expected org/name")
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "vibecrafted"
        / "config.toml"
    )
    try:
        data = tomllib.loads(config.read_text())
        entry = data.get("repositories", {}).get(identity)
    except (OSError, ValueError):
        entry = None
    remote = entry.get("remote") if isinstance(entry, dict) else None
    if not isinstance(remote, str) or not remote:
        raise RepoSelectionError(
            'unknown repository host; configure repositories."org/name".remote in vibecrafted/config.toml'
        )
    # Credential-bearing URLs must not enter argv, receipts, or cache config.
    from urllib.parse import urlsplit

    parsed = urlsplit(remote)
    if (
        parsed.password
        or (parsed.scheme in {"http", "https"} and parsed.username)
        or parsed.query
        or parsed.fragment
        or any(ord(c) < 32 for c in remote)
        or remote.startswith("-")
    ):
        raise RepoSelectionError(
            "repository remote must not contain credentials, query, fragment or control characters"
        )
    if parsed.scheme and parsed.scheme not in {"https", "http", "ssh", "file"}:
        raise RepoSelectionError("unsupported Git remote transport")
    cache_key = hashlib.sha256(remote.encode()).hexdigest()[:20]
    cache = vibecrafted_home() / "repositories" / cache_key / identity
    lock_root = vibecrafted_home() / "locks" / "repositories"
    lock_root.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            raise RepoSelectionError(
                "repository preparation timed out or Git is unavailable; no worker started"
            ) from None
        if proc.returncode:
            raise RepoSelectionError(
                "repository clone/fetch/ref failed; verify configured remote, authentication and requested ref"
            )
        return proc.stdout.strip()

    with (lock_root / f"{cache_key}.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--", remote, str(cache))
            marker = cache / ".git" / "vibecrafted-repository.json"
            marker.write_text(json.dumps({"identity": identity, "remote": remote}))
        else:
            marker = cache / ".git" / "vibecrafted-repository.json"
            try:
                owner = json.loads(marker.read_text())
            except (OSError, ValueError):
                raise RepoSelectionError(
                    "existing repository cache lacks its ownership receipt; preserved for inspection"
                ) from None
            if (
                owner != {"identity": identity, "remote": remote}
                or git("-C", str(cache), "remote", "get-url", "origin") != remote
            ):
                raise RepoSelectionError(
                    "repository cache provenance mismatch; preserved for inspection"
                )
        git(
            "-C",
            str(cache),
            "fetch",
            "--prune",
            "--no-tags",
            "origin",
            "+refs/heads/*:refs/remotes/origin/*",
            "+refs/tags/*:refs/vibecrafted/tags/*",
        )
        # Query the source every time: cached origin/HEAD may be obsolete.
        head = git("-C", str(cache), "ls-remote", "--symref", "origin", "HEAD")
        match = re.search(r"^ref: (refs/heads/[^\s]+)\s+HEAD$", head, re.MULTILINE)
        if not match:
            raise RepoSelectionError(
                "remote HEAD does not name an available default branch"
            )
        default_ref = match.group(1).replace("refs/heads/", "refs/remotes/origin/", 1)
        return str(cache.resolve()), default_ref


def resolve_repository_base(
    root: str | Path, requested: str = "HEAD"
) -> tuple[str, str]:
    """Resolve a local branch/tag/SHA once; never move the selected checkout."""
    ref = requested or "HEAD"
    if ref.startswith("-") or any(ord(c) < 32 for c in ref):
        raise RepoSelectionError("invalid --base reference")

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    resolved_ref = ref
    if not ref.startswith("refs/") and ref != "HEAD":
        branch = git("show-ref", "--verify", "--hash", f"refs/heads/{ref}")
        tag = git("show-ref", "--verify", "--hash", f"refs/tags/{ref}")
        if branch and tag:
            raise RepoSelectionError(
                "ambiguous --base: branch and tag exist; use refs/heads/ or refs/tags/"
            )
        if branch:
            resolved_ref = f"refs/heads/{ref}"
        elif tag:
            resolved_ref = f"refs/tags/{ref}"
        elif git("show-ref", "--verify", "--hash", f"refs/remotes/{ref}"):
            resolved_ref = f"refs/remotes/{ref}"
    sha = git("rev-parse", "--verify", "--end-of-options", f"{resolved_ref}^{{commit}}")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise RepoSelectionError(
            "--base does not resolve to one available commit on this host"
        )
    return resolved_ref, sha


def selected_root(
    args: argparse.Namespace,
    *,
    fallback: str
    | os.PathLike[str]
    | Callable[[], str | os.PathLike[str]]
    | None = None,
    require_git: bool = False,
    label: str = "",
) -> str:
    """Convenience for argparse callers: the selected path as a string."""
    return select_repository(
        getattr(args, "repo", ""),
        getattr(args, "root", ""),
        fallback=fallback,
        require_git=require_git,
        label=label,
    ).path


def parse_worktree_flag(value: str | bool | None, *, label: str = "") -> bool:
    """Interpret ``--worktree`` (``true``/``false``, ``yes``/``no``, ``1``/``0``).

    An absent flag arrives as ``None`` or ``""`` (argparse default) and means
    no worktree; a bare ``--worktree`` must be registered with ``const="true"``
    so it reaches here as the word ``true``.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "":
        return False
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    prefix = f"{label}: " if label else ""
    raise RepoSelectionError(f"{prefix}--worktree expects true or false, got {value!r}")


__all__ = [
    "REPO_FLAG",
    "ROOT_FLAG",
    "RepoSelection",
    "RepoSelectionError",
    "add_repo_arguments",
    "git_toplevel",
    "parse_worktree_flag",
    "select_repository",
    "selected_root",
]
