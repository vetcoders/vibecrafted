"""Delivery / Runtime Receipt — one chain of truth for the fleet tools.

Binds, per tool:

    owner/repo → branch → checkout SHA → dirty (source vs generated)
    → installed SHA on PATH → ahead/behind vs origin → index generation

Drift classes (named, not inferred prose):

    SOURCE_AHEAD_OF_INSTALLED · INSTALLED_NOT_ON_PATH · UNPUSHED
    · DIRTY_BUILD_PROVENANCE · INDEX_STALE · CLEAN

Refuses to guess. Any link that cannot be established is reported as
``unknown`` with an explicit reason — never substituted from cwd.

Pattern sibling: vc-frame ``install_freshness.rs`` (reads ``.git`` directly).
This module does **not** call ``vc-frame setup --check`` (known wrong-cwd defect).

Schema: ``vibecrafted.delivery_receipt.v1``
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import product_contract

SCHEMA_VERSION = "vibecrafted.delivery_receipt.v1"

# Named drift classes — the product surface. Order is severity for primary pick.
DRIFT_SOURCE_AHEAD = "SOURCE_AHEAD_OF_INSTALLED"
DRIFT_NOT_ON_PATH = "INSTALLED_NOT_ON_PATH"
DRIFT_UNPUSHED = "UNPUSHED"
DRIFT_DIRTY_BUILD = "DIRTY_BUILD_PROVENANCE"
DRIFT_INDEX_STALE = "INDEX_STALE"
DRIFT_CLEAN = "CLEAN"

_PRIMARY_ORDER = (
    DRIFT_NOT_ON_PATH,
    DRIFT_DIRTY_BUILD,
    DRIFT_SOURCE_AHEAD,
    DRIFT_UNPUSHED,
    DRIFT_INDEX_STALE,
    DRIFT_CLEAN,
)

# Paths that are "generated assets" for dirty split (not source edits).
_GENERATED_PATH_PREFIXES = (
    "target/",
    ".loctree/",
    "node_modules/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "vibecrafted-app/src-tauri/target/",
    "vibecrafted-server/target/",
    "vibecrafted-server/control-core/target/",
)

_VERSION_SHA_RE = re.compile(
    r"""
    (?:
        \+g(?P<plus>[0-9a-fA-F]{7,40})   # cargo/uv style: 0.46.0+g5c99f72d
      | \bg(?P<gonly>[0-9a-fA-F]{7,40})\b # loct banner: +g8188cf0d already covered
      | commit[=:](?P<commit>[0-9a-fA-F]{7,40})
      | \(?(?P<paren>[0-9a-fA-F]{7,40})\)?
    )
    (?:\.(?P<dirty>dirty))?
    """,
    re.VERBOSE,
)

# True dirty markers only — do NOT match `dirty=false` in loct banners.
_DIRTY_TRUE_RE = re.compile(
    r"(?:\.dirty\b|dirty\s*=\s*true|\bdirty\s*:\s*true\b|\(dirty\))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Unknown:
    """A refused-to-guess field: always ``value="unknown"`` plus why."""

    value: str = "unknown"
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        """JSON projection of this unknown marker."""
        return {"value": "unknown", "reason": self.reason}


def _unknown(reason: str) -> dict[str, str]:
    """Shorthand for ``Unknown(reason=...).as_dict()``."""
    return Unknown(reason=reason).as_dict()


def _sha_prefix_match(a: str | None, b: str | None) -> bool:
    """Whether two SHAs are the same commit at possibly different abbreviation lengths."""
    if not a or not b or a == "unknown" or b == "unknown":
        return False
    a = a.lower().strip()
    b = b.lower().strip()
    return a.startswith(b) or b.startswith(a)


def _is_sha(value: str) -> bool:
    """Whether ``value`` looks like a git SHA: 7+ hex characters."""
    v = value.strip()
    return bool(v) and len(v) >= 7 and all(c in "0123456789abcdefABCDEF" for c in v)


# ---------------------------------------------------------------------------
# .git direct read (no git subprocess) — install_freshness pattern
# ---------------------------------------------------------------------------


def find_git_dir(start: Path) -> Path | None:
    """Walk ancestors for ``.git`` dir or ``gitdir:`` pointer file."""
    try:
        start = start.resolve()
    except OSError:
        return None
    for directory in [start, *start.parents]:
        candidate = directory / ".git"
        try:
            if candidate.is_dir():
                return candidate
            if candidate.is_file():
                pointer = candidate.read_text(encoding="utf-8", errors="replace")
                target = pointer.strip()
                if not target.startswith("gitdir:"):
                    return None
                raw = target[len("gitdir:") :].strip()
                path = Path(raw)
                if not path.is_absolute():
                    path = directory / path
                return path if path.is_dir() else None
        except OSError:
            continue
    return None


def checkout_head_sha(start: Path) -> str | None:
    """Resolve HEAD SHA by reading ``.git`` only. ``None`` = refuse to guess."""
    git_dir = find_git_dir(start)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head if _is_sha(head) else None
    ref_name = head[len("ref:") :].strip()
    loose = git_dir / ref_name
    try:
        if loose.is_file():
            sha = loose.read_text(encoding="utf-8", errors="replace").strip()
            if _is_sha(sha):
                return sha
    except OSError:
        pass
    packed = git_dir / "packed-refs"
    try:
        text = packed.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == ref_name and _is_sha(parts[0]):
            return parts[0]
    return None


def checkout_branch(start: Path) -> str | None:
    """Resolve the checked-out branch name by reading ``.git/HEAD`` only.

    Returns ``"HEAD"`` for a detached checkout, ``None`` when no ``.git`` is found
    or ``HEAD`` cannot be read.
    """
    git_dir = find_git_dir(start)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if head.startswith("ref: refs/heads/"):
        return head[len("ref: refs/heads/") :].strip() or None
    if head.startswith("ref:"):
        return head[len("ref:") :].strip() or None
    return "HEAD"  # detached


def owner_repo_from_git(start: Path) -> str | None:
    """Best-effort owner/repo from ``.git/config`` origin url. No cwd invent."""
    git_dir = find_git_dir(start)
    if git_dir is None:
        return None
    config = git_dir / "config"
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    in_origin = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_origin = stripped.lower() in {'[remote "origin"]', "[remote 'origin']"}
            continue
        if not in_origin:
            continue
        if stripped.startswith("url"):
            _, _, value = stripped.partition("=")
            return _parse_owner_repo(value.strip())
    return None


def _parse_owner_repo(url: str) -> str | None:
    """Extract ``owner/repo`` from an https or ssh-style git remote URL."""
    url = url.rstrip("/")
    url = url.removesuffix(".git")
    # git@host:owner/repo  or  https://host/owner/repo
    if ":" in url and not url.startswith("http"):
        # ssh style host:path
        path = url.split(":", 1)[-1]
    else:
        path = url
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


# ---------------------------------------------------------------------------
# Optional git subprocess (ahead/behind, dirty listing) — only when available
# ---------------------------------------------------------------------------


def _git(
    root: Path, *args: str, timeout: float = 8.0
) -> subprocess.CompletedProcess[str] | None:
    """Run ``git <args>`` in ``root``; ``None`` when git is missing or times out."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None


def dirty_split(root: Path) -> dict[str, Any]:
    """Split porcelain dirty paths into source vs generated assets."""
    result = _git(root, "status", "--porcelain", "-uall")
    if result is None:
        return {
            "dirty": _unknown("git not available to list dirty paths"),
            "source_dirty_count": _unknown("git not available"),
            "generated_dirty_count": _unknown("git not available"),
            "source_paths": [],
            "generated_paths": [],
        }
    if result.returncode != 0:
        return {
            "dirty": _unknown(
                f"git status failed: {(result.stderr or result.stdout).strip()[:200]}"
            ),
            "source_dirty_count": _unknown("git status failed"),
            "generated_dirty_count": _unknown("git status failed"),
            "source_paths": [],
            "generated_paths": [],
        }
    source_paths: list[str] = []
    generated_paths: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # porcelain: XY path  or  XY origin -> path
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if _is_generated_path(path):
            generated_paths.append(path)
        else:
            source_paths.append(path)
    return {
        "dirty": bool(source_paths or generated_paths),
        "source_dirty_count": len(source_paths),
        "generated_dirty_count": len(generated_paths),
        "source_paths": source_paths[:40],
        "generated_paths": generated_paths[:40],
    }


def _is_generated_path(path: str) -> bool:
    """Whether ``path`` falls under a known build/cache prefix (not a source edit)."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    for prefix in _GENERATED_PATH_PREFIXES:
        if normalized.startswith(prefix) or f"/{prefix}" in f"/{normalized}":
            return True
    return False


def ahead_behind(root: Path) -> dict[str, Any]:
    """Return ahead/behind vs upstream. Unknown when no upstream."""
    up = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up is None:
        return {
            "upstream": _unknown("git not available"),
            "ahead": _unknown("git not available"),
            "behind": _unknown("git not available"),
        }
    if up.returncode != 0 or not up.stdout.strip():
        return {
            "upstream": _unknown("no upstream configured"),
            "ahead": _unknown("no upstream configured"),
            "behind": _unknown("no upstream configured"),
        }
    upstream = up.stdout.strip()
    counts = _git(root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}")
    if counts is None or counts.returncode != 0:
        return {
            "upstream": upstream,
            "ahead": _unknown("could not compute ahead/behind"),
            "behind": _unknown("could not compute ahead/behind"),
        }
    parts = counts.stdout.strip().split()
    if len(parts) != 2:
        return {
            "upstream": upstream,
            "ahead": _unknown("unexpected rev-list output"),
            "behind": _unknown("unexpected rev-list output"),
        }
    # HEAD...upstream with --left-right: left=HEAD (ahead), right=upstream (behind)
    try:
        ahead_n, behind_n = int(parts[0]), int(parts[1])
    except ValueError:
        return {
            "upstream": upstream,
            "ahead": _unknown("non-integer rev-list output"),
            "behind": _unknown("non-integer rev-list output"),
        }
    return {"upstream": upstream, "ahead": ahead_n, "behind": behind_n}


def commit_exists(root: Path, sha: str) -> bool | None:
    """True if sha names a commit in this repo. None if git unavailable."""
    if not sha or not _is_sha(sha):
        return False
    result = _git(root, "cat-file", "-t", sha)
    if result is None:
        return None
    return result.returncode == 0 and result.stdout.strip() == "commit"


# ---------------------------------------------------------------------------
# Binary / version probing
# ---------------------------------------------------------------------------


def which_binary(
    name: str, which: Callable[[str], str | None] | None = None
) -> str | None:
    """PATH lookup for ``name``, defaulting to ``shutil.which`` (injectable for tests)."""
    finder = which or shutil.which
    return finder(name)


def _vibecrafted_tools_path_hints() -> list[str]:
    """Staged tools homes that embed build SHA in the directory name."""
    hints: list[str] = []
    try:
        from .runtime_paths import vibecrafted_tools_home

        tools = vibecrafted_tools_home()
    except (ImportError, OSError, RuntimeError, AttributeError, TypeError):
        data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        tools = Path(data) / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    try:
        if current.exists():
            hints.append(str(current.resolve()))
    except OSError:
        pass
    try:
        if tools.is_dir():
            for child in sorted(tools.iterdir(), reverse=True):
                if child.name.startswith("vibecrafted-") and "+g" in child.name:
                    hints.append(str(child))
                    break
    except OSError:
        pass
    return hints


def run_version(binary: str, timeout: float = 5.0) -> str:
    """Try ``--version``/``version``/``-V`` in turn; return the first non-blank line."""
    for args in ([binary, "--version"], [binary, "version"], [binary, "-V"]):
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            continue
        out = (proc.stdout or "") + (proc.stderr or "")
        line = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
        if line:
            return line
    return ""


def parse_installed_provenance(
    version_line: str, *, path_hint: str | None = None
) -> dict[str, Any]:
    """Extract git sha + dirty flag from a version banner (and optional path).

    ``path_hint`` may carry embedded provenance (e.g. tools home
    ``vibecrafted-3.6.0+g560310a9``) when the version string is bare semver.
    """
    if not version_line and not path_hint:
        return {
            "installed_sha": _unknown("empty version output"),
            "installed_dirty": _unknown("empty version output"),
            "version_line": "",
        }
    haystack = version_line or ""
    dirty = bool(_DIRTY_TRUE_RE.search(haystack))
    # Prefer +gSHA form (cargo/uv)
    m = re.search(r"\+g([0-9a-fA-F]{7,40})", haystack)
    if m:
        return {
            "installed_sha": m.group(1).lower(),
            "installed_dirty": dirty,
            "version_line": version_line,
        }
    m = re.search(r"commit[=:]([0-9a-fA-F]{7,40})", haystack)
    if m:
        return {
            "installed_sha": m.group(1).lower(),
            "installed_dirty": dirty,
            "version_line": version_line,
        }
    m = re.search(r"\bg([0-9a-fA-F]{7,40})\b", haystack)
    if m:
        return {
            "installed_sha": m.group(1).lower(),
            "installed_dirty": dirty,
            "version_line": version_line,
        }
    # Path-embedded provenance (staged tools homes)
    if path_hint:
        pm = re.search(r"\+g([0-9a-fA-F]{7,40})", path_hint)
        if pm:
            return {
                "installed_sha": pm.group(1).lower(),
                "installed_dirty": dirty,
                "version_line": version_line,
                "sha_source": "path_hint",
            }
        # Resolve symlinks once more for tools/vibecrafted-VERSION
        try:
            resolved = str(Path(path_hint).resolve())
        except OSError:
            resolved = path_hint
        pm = re.search(r"vibecrafted-[^/]*\+g([0-9a-fA-F]{7,40})", resolved)
        if pm:
            return {
                "installed_sha": pm.group(1).lower(),
                "installed_dirty": dirty,
                "version_line": version_line,
                "sha_source": "resolved_tools_path",
            }
    return {
        "installed_sha": _unknown(
            f"version line carries no git sha: {(version_line or '')[:120]}"
        ),
        "installed_dirty": dirty,
        "version_line": version_line,
    }


def _runtime_foundation_provenance(
    installed_path: str | None, *, foundation: str
) -> dict[str, Any] | None:
    """Read manifest-bound provenance for a binary inside a Runtime Pack.

    Bare foundation version banners intentionally carry no Git SHA.  The
    signed pack's ``runtime-foundations.json`` is the authority in that case;
    the enclosing Vibecrafted generation SHA is not the foundation SHA.
    """
    if not installed_path:
        return None
    try:
        executable = Path(installed_path).resolve(strict=True)
    except OSError:
        return None
    root = executable.parent.parent
    manifest_path = root / "runtime-foundations.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema") != "io.vetcoders.vibecrafted.runtime-foundations.v1":
        return None
    revisions = payload.get("source_revisions")
    files = payload.get("files")
    if not isinstance(revisions, dict) or not isinstance(files, dict):
        return None
    revision = revisions.get(foundation)
    expected_hash = files.get(executable.name)
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        return None
    if not isinstance(expected_hash, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", expected_hash
    ):
        return None
    try:
        observed_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    except OSError:
        return None
    if observed_hash != expected_hash.lower():
        return None
    return {
        "installed_sha": revision.lower(),
        "installed_dirty": False,
        "sha_source": "runtime_foundations",
        "foundation_root": str(root),
    }


# ---------------------------------------------------------------------------
# Source root identity — never cwd
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """Declarative identity contract for one fleet tool: how to find and verify it."""

    name: str
    binaries: tuple[str, ...]
    env_roots: tuple[str, ...]
    # (relative path, optional regex on file contents) identity markers
    markers: tuple[tuple[str, str | None], ...]
    # Absolute candidate roots — verified by markers, never invented from cwd
    candidate_roots: tuple[str, ...] = ()
    # Optional related binaries reported under the same tool
    related_binaries: tuple[str, ...] = ()
    index_kind: str | None = None  # "loctree_snapshot" | "aicx_index" | None


def _vibecrafted_package_repo() -> Path | None:
    """If this package lives inside a vibecrafted checkout, return that root.

    Installed tools trees (``.../tools/vibecrafted-VERSION/...``) are **not**
    checkouts — they fail the markers and return None.
    """
    here = Path(__file__).resolve()
    # .../vibecrafted/vibecrafted-core/vibecrafted_core/runtime_receipt.py
    for parent in here.parents:
        if _verify_markers(
            parent,
            (
                ("scripts/vetcoders_install.py", None),
                ("vibecrafted-core/vibecrafted_core/cli.py", None),
            ),
        ):
            # Reject staged tools homes that mirror the layout
            if "vibecrafted/tools/vibecrafted-" in str(parent).replace("\\", "/"):
                return None
            if parent.name.startswith("vibecrafted-") and "tools" in parent.parts:
                return None
            return parent
    return None


def _fleet_root_from_env() -> Path | None:
    """Resolved ``VIBECRAFTED_FLEET_ROOT``/``VC_FLEET_ROOT``, if set and a directory."""
    raw = os.environ.get("VIBECRAFTED_FLEET_ROOT") or os.environ.get("VC_FLEET_ROOT")
    if not raw:
        return None
    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        return None
    return path if path.is_dir() else None


def _default_candidate_roots(name: str) -> list[Path]:
    """Return verified-candidate search paths for a tool.

    Sources of candidates (in order):
    1. Explicit env roots for the tool (handled separately).
    2. ``VIBECRAFTED_FLEET_ROOT/<name>`` when the fleet root env is set.
    3. Sibling of a *verified* vibecrafted package checkout (package-path only,
       never ``Path.cwd()``).
    4. Home-relative workshop bases (still verified by identity markers before
       acceptance — presence on this list is not proof). Absolute build-host
       paths are deliberately absent: they duplicate source 3 where they exist
       and leak the operator's disk layout into every shipped artifact.
    """
    # scaffold-doctor ships from the vibecrafted monorepo, not its own folder.
    folder_names = {
        "scaffold-doctor": ("vibecrafted",),
        "loct": ("loctree-suite", "loctree", "loct"),
        "loctree": ("loctree-suite", "loctree"),
        "loctree-mcp": ("loctree-suite", "loctree"),
        "aicx": ("aicx", "ai-contexters"),
        "vc-frame": ("vc-frame",),
        "vibecrafted": ("vibecrafted",),
    }.get(name, (name,))

    out: list[Path] = []
    fleet = _fleet_root_from_env()
    if fleet is not None:
        for folder in folder_names:
            out.append(fleet / folder)

    package_repo = _vibecrafted_package_repo()
    if package_repo is not None:
        parent = package_repo.parent
        for folder in folder_names:
            out.append(parent / folder)
        # Loctree/ sibling of vetcoders/
        grand = parent.parent
        if name in {"loctree-suite", "loct", "loctree", "loctree-mcp", "aicx"}:
            for folder in folder_names:
                out.append(grand / "Loctree" / folder)
        if name in {"vibecrafted", "scaffold-doctor"}:
            out.append(package_repo)

    # Workshop bases — accepted only after marker verification.
    #
    # The two absolute entries that used to head this list (one build host's
    # `/Volumes/...` workshop and its Loctree sibling) are gone, and nothing was
    # lost with them: source 3 above already derives both from the package's own
    # location — `parent` reaches the vetcoders folder and `grand / "Loctree"`
    # reaches the sibling — so on the host where they resolved they were pure
    # duplicates, and on every other host they were dead entries that shipped
    # the operator's disk layout inside a signed artifact. A workshop that is
    # not a sibling of the package is named by VIBECRAFTED_FLEET_ROOT (source 2),
    # which is the supported way to say it and does not travel in the payload.
    for base in (
        Path.home() / "vc-workspace" / "vetcoders",
        Path.home() / "Libraxis",
    ):
        for folder in folder_names:
            out.append(base / folder)

    # Dedup while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for path in out:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _verify_markers(root: Path, markers: Sequence[tuple[str, str | None]]) -> bool:
    """Whether every ``(relative_path, optional content regex)`` marker matches."""
    if not root.is_dir():
        return False
    for rel, pattern in markers:
        path = root / rel
        if not path.is_file():
            return False
        if pattern is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if re.search(pattern, text, re.MULTILINE) is None:
            return False
    return True


def resolve_source_root(
    spec: ToolSpec,
) -> tuple[Path | None, str]:
    """Return (root, resolution_method) or (None, reason). Never uses cwd."""
    # 1. Explicit env
    for env_name in spec.env_roots:
        raw = os.environ.get(env_name)
        if not raw:
            continue
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            return None, f"env {env_name}={raw!r} unresolvable: {exc}"
        if _verify_markers(path, spec.markers):
            return path, f"env:{env_name}"
        return None, (f"env {env_name}={path} failed identity markers for {spec.name}")

    # 2. Binary lives inside a checkout (resolved path, not cwd)
    for binary_name in spec.binaries:
        resolved = which_binary(binary_name)
        if not resolved:
            continue
        try:
            bin_path = Path(resolved).resolve()
        except OSError:
            continue
        # Walk up from the binary for a marker-verified root
        for directory in [bin_path.parent, *bin_path.parents]:
            if _verify_markers(directory, spec.markers):
                return directory, f"binary_path:{binary_name}"

    # 3. Package-relative / fleet candidates (verified by markers)
    candidates = list(spec.candidate_roots)
    candidates.extend(str(p) for p in _default_candidate_roots(spec.name))
    # Alias names for loctree family
    if spec.name in {"loct", "loctree-mcp", "loctree"}:
        candidates.extend(str(p) for p in _default_candidate_roots("loctree-suite"))
        candidates.extend(str(p) for p in _default_candidate_roots("loctree"))

    tried: list[str] = []
    for raw in candidates:
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except OSError:
            continue
        tried.append(str(path))
        if _verify_markers(path, spec.markers):
            return path, "verified_candidate"

    if tried:
        return None, (
            f"no verified source root for {spec.name}; "
            f"set {spec.env_roots[0] if spec.env_roots else 'SOURCE'} "
            f"(tried {len(tried)} candidates)"
        )
    return None, (
        f"no source root for {spec.name}; set "
        f"{spec.env_roots[0] if spec.env_roots else 'SOURCE'} explicitly"
    )


# ---------------------------------------------------------------------------
# Index generation probes
# ---------------------------------------------------------------------------


def probe_loctree_index(source_root: Path | None) -> dict[str, Any]:
    """Loctree snapshot / bundle generation when discoverable."""
    # Prefer live `loct --version` banner (always available if on PATH)
    loct = which_binary("loct")
    banner = run_version(loct) if loct else ""
    bundle_id = None
    m = re.search(r"bundle_id=([^\s]+)", banner)
    if m:
        bundle_id = m.group(1)
    commit_m = re.search(r"commit[=:]([0-9a-fA-F]{7,40})", banner)
    binary_commit = commit_m.group(1) if commit_m else None

    snapshot_path = None
    snapshot_sha = None
    if source_root is not None:
        atlas = source_root / ".loctree" / "context-atlas" / "receipt.json"
        if atlas.is_file():
            try:
                payload = json.loads(atlas.read_text(encoding="utf-8"))
                snapshot_path = str(atlas)
                snapshot_sha = (
                    payload.get("snapshot")
                    or payload.get("head")
                    or payload.get("commit")
                )
            except (OSError, json.JSONDecodeError):
                pass
        # fallback: any snapshot under .loctree
        if snapshot_path is None:
            snap_dir = source_root / ".loctree"
            if snap_dir.is_dir():
                snapshot_path = str(snap_dir)

    status = "present" if banner or snapshot_path else "unknown"
    stale = False
    notes: list[str] = []
    if source_root is not None and binary_commit:
        head = checkout_head_sha(source_root)
        if head and not _sha_prefix_match(binary_commit, head):
            stale = True
            notes.append(
                f"loct binary commit {binary_commit[:8]} != source HEAD {head[:8]}"
            )

    return {
        "kind": "loctree_snapshot",
        "status": "stale" if stale else status,
        "bundle_id": bundle_id,
        "binary_commit": binary_commit,
        "snapshot_path": snapshot_path,
        "snapshot_ref": snapshot_sha,
        "notes": notes,
        "stale": stale,
    }


def probe_aicx_index() -> dict[str, Any]:
    """Prefer ``aicx config inspect --json`` — tool-owned truth, not cwd."""
    aicx = which_binary("aicx")
    if not aicx:
        return {
            "kind": "aicx_index",
            "status": "unknown",
            "reason": "aicx not on PATH",
            "stale": False,
        }
    try:
        proc = subprocess.run(
            [aicx, "config", "inspect", "--json"],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "kind": "aicx_index",
            "status": "unknown",
            "reason": f"aicx config inspect failed: {exc}",
            "stale": False,
        }
    if proc.returncode != 0:
        return {
            "kind": "aicx_index",
            "status": "unknown",
            "reason": f"aicx config inspect rc={proc.returncode}",
            "stale": False,
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "kind": "aicx_index",
            "status": "unknown",
            "reason": f"invalid json: {exc}",
            "stale": False,
        }
    index = payload.get("index") or {}
    build = (payload.get("runtime") or {}).get("build") or {}
    generation = index.get("generation")
    status = index.get("status") or "unknown"
    # drift signals from aicx's own install status
    installations = (payload.get("installations") or {}).get("aicx") or []
    install_status = None
    if installations:
        install_status = installations[0].get("status")
    stale = status in {"stale", "stale_index", "stale_chunks"} or install_status in {
        "stale",
        "mismatch",
        "behind",
    }
    return {
        "kind": "aicx_index",
        "status": "stale" if stale else status,
        "generation": generation,
        "manifest_path": index.get("manifest_path"),
        "hybrid_root": index.get("hybrid_root"),
        "build_git_commit": build.get("git_commit"),
        "build_version": build.get("version"),
        "install_status": install_status,
        "stale": bool(stale),
        "raw_status": status,
    }


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------


def fleet_tool_specs() -> list[ToolSpec]:
    """The fixed roster of fleet tools this receipt reports on."""
    return [
        ToolSpec(
            name="vc-frame",
            binaries=("vc-frame",),
            env_roots=("VC_FRAME_SOURCE", "VC_FRAME_ROOT"),
            markers=(
                ("Cargo.toml", r'name\s*=\s*"vc-frame"'),
                ("zellij-utils/src/install_freshness.rs", None),
            ),
            candidate_roots=(),
        ),
        ToolSpec(
            name="vibecrafted",
            binaries=("vibecrafted",),
            env_roots=("VIBECRAFTED_SOURCE", "VIBECRAFTED_ROOT"),
            markers=(
                ("scripts/vetcoders_install.py", None),
                ("vibecrafted-core/vibecrafted_core/cli.py", None),
            ),
            related_binaries=("scaffold-doctor",),
        ),
        ToolSpec(
            name="scaffold-doctor",
            binaries=("scaffold-doctor",),
            env_roots=("VIBECRAFTED_SOURCE", "VIBECRAFTED_ROOT"),
            # Lives in the vibecrafted monorepo (control-core binary).
            markers=(
                ("scripts/vetcoders_install.py", None),
                (
                    "vibecrafted-server/control-core/Cargo.toml",
                    r"scaffold",
                ),
            ),
        ),
        ToolSpec(
            name="loct",
            binaries=("loct", "loctree-mcp"),
            env_roots=("LOCTREE_SOURCE", "LOCTREE_SUITE_ROOT", "LOCT_SOURCE"),
            # Workspace root (loctree-suite / loctree), not a single package name.
            markers=(
                ("Cargo.toml", r"loctree-rs"),
                ("loctree-rs/Cargo.toml", None),
            ),
            index_kind="loctree_snapshot",
        ),
        ToolSpec(
            name="aicx",
            binaries=("aicx",),
            env_roots=("AICX_SOURCE", "AICX_ROOT"),
            markers=(("Cargo.toml", r'name\s*=\s*"aicx"'),),
            index_kind="aicx_index",
        ),
    ]


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_drift(
    *,
    on_path: bool,
    checkout_sha: str | None | dict[str, str],
    installed_sha: str | None | dict[str, str],
    installed_dirty: bool | dict[str, str] | None,
    ahead: int | dict[str, str] | None,
    index_stale: bool,
    source_known: bool,
) -> list[str]:
    """Derive the named drift classes for one tool from its already-probed signals.

    Order in the returned list is not significance — use :func:`primary_drift`
    for that.
    """
    classes: list[str] = []
    if not on_path:
        classes.append(DRIFT_NOT_ON_PATH)
        # still may have other signals from source-only inspection
    installed_sha_s = installed_sha if isinstance(installed_sha, str) else None
    checkout_sha_s = checkout_sha if isinstance(checkout_sha, str) else None

    if installed_dirty is True:
        classes.append(DRIFT_DIRTY_BUILD)

    if (
        on_path
        and source_known
        and isinstance(checkout_sha_s, str)
        and isinstance(installed_sha_s, str)
        and not _sha_prefix_match(checkout_sha_s, installed_sha_s)
    ):
        classes.append(DRIFT_SOURCE_AHEAD)

    if isinstance(ahead, int) and ahead > 0:
        classes.append(DRIFT_UNPUSHED)

    if index_stale:
        classes.append(DRIFT_INDEX_STALE)

    if not classes:
        classes.append(DRIFT_CLEAN)
    return classes


def primary_drift(classes: Sequence[str]) -> str:
    """Pick the single most-severe drift class per ``_PRIMARY_ORDER``."""
    for name in _PRIMARY_ORDER:
        if name in classes:
            return name
    return classes[0] if classes else DRIFT_CLEAN


# ---------------------------------------------------------------------------
# Build one tool row + full receipt
# ---------------------------------------------------------------------------


def _link_or_unknown(value: Any, reason_if_none: str) -> Any:
    """Pass ``value`` through, or substitute an :func:`_unknown` marker for ``None``."""
    if value is None:
        return _unknown(reason_if_none)
    if isinstance(value, dict) and value.get("value") == "unknown":
        return value
    return value


@dataclass(frozen=True)
class _InstalledRuntimeManifestProbe:
    """Tri-state result: no manifest, verified source, or fail-closed rejection."""

    state: Literal["absent", "success", "rejection"]
    source: dict[str, Any] | None = None
    reason: str | None = None


def _probe_installed_runtime_manifest(
    installed_path: str | None,
) -> _InstalledRuntimeManifestProbe:
    """Probe checkout-free provenance without conflating absence and rejection."""
    if not installed_path:
        return _InstalledRuntimeManifestProbe("absent")
    try:
        resolved = Path(installed_path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return _InstalledRuntimeManifestProbe("absent")
    for directory in (resolved.parent, *resolved.parents):
        manifest_path = directory / product_contract.RUNTIME_GENERATION_MANIFEST_NAME
        try:
            manifest_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return _InstalledRuntimeManifestProbe(
                "rejection",
                reason=(
                    f"VCPC{product_contract.E_MISSING:03d}: "
                    f"installed runtime manifest is unreadable: {exc}"
                ),
            )
        try:
            payload = product_contract.verify_installed_runtime_generation(
                directory, expected_entrypoint=resolved
            )
        except product_contract.ProductContractError as exc:
            return _InstalledRuntimeManifestProbe(
                "rejection", reason=f"VCPC{exc.code:03d}: {exc}"
            )
        return _InstalledRuntimeManifestProbe(
            "success",
            source={
                "path": str(directory),
                "owner_repo": payload["owner_repo"],
                "branch": _unknown("checkout-free installed generation has no branch"),
                "checkout_sha": payload["source_revision"],
                "source_payload": payload["source_payload"],
                "resolution": "installed_runtime_manifest",
                "dirty": False,
            },
        )
    return _InstalledRuntimeManifestProbe("absent")


def _installed_runtime_manifest(installed_path: str | None) -> dict[str, Any] | None:
    """Compatibility projection returning only a successfully verified source."""
    probe = _probe_installed_runtime_manifest(installed_path)
    return probe.source if probe.state == "success" else None


def inspect_tool(spec: ToolSpec) -> dict[str, Any]:
    """Build the full receipt row for one tool: PATH, source, remote, index, drift."""
    # PATH
    primary_bin = spec.binaries[0]
    path_hits: dict[str, str | None] = {}
    for b in spec.binaries:
        path_hits[b] = which_binary(b)
    on_path = any(path_hits.values())
    installed_path = path_hits.get(primary_bin) or next(
        (p for p in path_hits.values() if p), None
    )

    version_line = ""
    provenance: dict[str, Any] = {
        "installed_sha": _unknown("binary not on PATH"),
        "installed_dirty": _unknown("binary not on PATH"),
        "version_line": "",
    }
    if installed_path:
        version_line = run_version(installed_path)
        # Resolve the real path so staged tools homes (...+gSHA...) surface even
        # when --version is bare semver. Never uses cwd.
        path_hints: list[str] = []
        try:
            path_hints.append(str(Path(installed_path).resolve()))
        except OSError:
            path_hints.append(installed_path)
        # vibecrafted often ships as a shim whose real package lives under
        # tools/vibecrafted-<ver>+gSHA — probe that without touching cwd.
        if spec.name in {"vibecrafted", "scaffold-doctor"}:
            path_hints.extend(_vibecrafted_tools_path_hints())
        provenance = {
            "installed_sha": _unknown("empty version output"),
            "installed_dirty": _unknown("empty version output"),
            "version_line": version_line,
        }
        for hint in path_hints:
            provenance = parse_installed_provenance(version_line, path_hint=hint)
            if isinstance(provenance.get("installed_sha"), str):
                break
        foundation_proof = (
            _runtime_foundation_provenance(installed_path, foundation="aicx")
            if spec.name == "aicx"
            else None
        )
        if foundation_proof is not None:
            provenance = {
                **foundation_proof,
                "version_line": version_line,
            }
    else:
        foundation_proof = None

    # Source
    installed_manifest_probe = (
        _probe_installed_runtime_manifest(installed_path)
        if spec.name == "vibecrafted"
        else _InstalledRuntimeManifestProbe("absent")
    )
    installed_manifest = installed_manifest_probe.source
    if foundation_proof is not None:
        source_root, source_method = None, "installed_runtime_foundation"
    elif installed_manifest_probe.state == "success":
        source_root, source_method = None, "installed_runtime_manifest"
    elif installed_manifest_probe.state == "rejection":
        source_root, source_method = None, "installed_runtime_manifest_rejected"
    else:
        source_root, source_method = resolve_source_root(spec)
    source_block: dict[str, Any]
    if foundation_proof is not None:
        source_block = {
            "path": foundation_proof["foundation_root"],
            "owner_repo": "Loctree/aicx",
            "branch": _unknown("checkout-free runtime foundation has no branch"),
            "checkout_sha": foundation_proof["installed_sha"],
            "resolution": source_method,
            "dirty": False,
        }
        ab = {
            "upstream": _unknown("checkout-free runtime foundation"),
            "ahead": _unknown("checkout-free runtime foundation"),
            "behind": _unknown("checkout-free runtime foundation"),
        }
        dirty = {
            "dirty": False,
            "source_dirty_count": 0,
            "generated_dirty_count": 0,
            "source_paths": [],
            "generated_paths": [],
        }
        checkout_sha = source_block["checkout_sha"]
    elif installed_manifest is not None:
        source_block = installed_manifest
        ab = {
            "upstream": _unknown("checkout-free installed generation"),
            "ahead": _unknown("checkout-free installed generation"),
            "behind": _unknown("checkout-free installed generation"),
        }
        dirty = {
            "dirty": False,
            "source_dirty_count": 0,
            "generated_dirty_count": 0,
            "source_paths": [],
            "generated_paths": [],
        }
        checkout_sha = source_block["checkout_sha"]
    elif installed_manifest_probe.state == "rejection":
        rejection_reason = installed_manifest_probe.reason or (
            f"VCPC{product_contract.E_PROOF:03d}: "
            "installed runtime manifest rejection carried no reason"
        )
        provenance = {
            **provenance,
            "installed_dirty": True,
            "installed_dirty_reason": rejection_reason,
        }
        source_block = {
            "path": _unknown(rejection_reason),
            "owner_repo": _unknown(rejection_reason),
            "branch": _unknown(rejection_reason),
            "checkout_sha": _unknown(rejection_reason),
            "resolution": source_method,
            "dirty": _unknown(rejection_reason),
        }
        ab = {
            "upstream": _unknown(rejection_reason),
            "ahead": _unknown(rejection_reason),
            "behind": _unknown(rejection_reason),
        }
        dirty = {
            "dirty": _unknown(rejection_reason),
            "source_dirty_count": _unknown(rejection_reason),
            "generated_dirty_count": _unknown(rejection_reason),
            "source_paths": [],
            "generated_paths": [],
        }
        checkout_sha = source_block["checkout_sha"]
    elif source_root is None:
        source_block = {
            "path": _unknown(source_method),
            "owner_repo": _unknown(source_method),
            "branch": _unknown(source_method),
            "checkout_sha": _unknown(source_method),
            "resolution": source_method,
            "dirty": _unknown(source_method),
        }
        ab = {
            "upstream": _unknown(source_method),
            "ahead": _unknown(source_method),
            "behind": _unknown(source_method),
        }
        dirty = {
            "dirty": _unknown(source_method),
            "source_dirty_count": _unknown(source_method),
            "generated_dirty_count": _unknown(source_method),
            "source_paths": [],
            "generated_paths": [],
        }
        checkout_sha: str | dict[str, str] | None = source_block["checkout_sha"]
    else:
        head = checkout_head_sha(source_root)
        branch = checkout_branch(source_root)
        owner = owner_repo_from_git(source_root)
        dirty = dirty_split(source_root)
        ab = ahead_behind(source_root)
        source_block = {
            "path": str(source_root),
            "owner_repo": owner or _unknown("origin url not found in .git/config"),
            "branch": branch or _unknown("could not read branch from .git/HEAD"),
            "checkout_sha": head or _unknown("could not read HEAD from .git"),
            "resolution": source_method,
            "dirty": dirty.get("dirty"),
            "dirty_detail": {
                "source_dirty_count": dirty.get("source_dirty_count"),
                "generated_dirty_count": dirty.get("generated_dirty_count"),
                "source_paths": dirty.get("source_paths"),
                "generated_paths": dirty.get("generated_paths"),
            },
        }
        checkout_sha = head

    # Index
    index: dict[str, Any] | None = None
    index_stale = False
    if spec.index_kind == "loctree_snapshot":
        index = probe_loctree_index(source_root)
        index_stale = bool(index.get("stale"))
    elif spec.index_kind == "aicx_index":
        index = probe_aicx_index()
        index_stale = bool(index.get("stale"))

    # DIRTY_BUILD: version says dirty OR installed sha is not a real commit
    installed_dirty = provenance.get("installed_dirty")
    installed_sha = provenance.get("installed_sha")
    if (
        source_root is not None
        and isinstance(installed_sha, str)
        and installed_dirty is not True
    ):
        exists = commit_exists(source_root, installed_sha)
        if exists is False:
            # SHA not in repo → dirty-build provenance (or foreign binary)
            installed_dirty = True
            provenance = {
                **provenance,
                "installed_dirty": True,
                "installed_dirty_reason": (
                    f"installed sha {installed_sha} is not a commit in source"
                ),
            }

    classes = classify_drift(
        on_path=bool(on_path and path_hits.get(primary_bin)),
        checkout_sha=checkout_sha if isinstance(checkout_sha, str) else None,
        installed_sha=installed_sha if isinstance(installed_sha, str) else None,
        installed_dirty=installed_dirty if isinstance(installed_dirty, bool) else None,
        ahead=ab.get("ahead") if isinstance(ab.get("ahead"), int) else None,
        index_stale=index_stale,
        source_known=isinstance(checkout_sha, str),
    )
    # For related binaries (scaffold-doctor under vibecrafted), not primary
    if not path_hits.get(primary_bin) and DRIFT_NOT_ON_PATH not in classes:
        classes.insert(0, DRIFT_NOT_ON_PATH)

    related: list[dict[str, Any]] = []
    for rel in spec.related_binaries:
        rel_path = which_binary(rel)
        related.append(
            {
                "name": rel,
                "on_path": bool(rel_path),
                "path": rel_path or _unknown("not on PATH"),
                "drift": [DRIFT_NOT_ON_PATH] if not rel_path else [DRIFT_CLEAN],
            }
        )

    return {
        "name": spec.name,
        "binaries": {
            name: path or _unknown("not on PATH") for name, path in path_hits.items()
        },
        "on_path": bool(path_hits.get(primary_bin)),
        "installed": {
            "path": installed_path or _unknown(f"{primary_bin} not on PATH"),
            "version_line": provenance.get("version_line") or version_line,
            "sha": provenance.get("installed_sha"),
            "dirty_build": provenance.get("installed_dirty"),
            "dirty_build_reason": provenance.get("installed_dirty_reason"),
        },
        "source": source_block,
        "remote": {
            "upstream": ab.get("upstream"),
            "ahead": ab.get("ahead"),
            "behind": ab.get("behind"),
        },
        "index": index,
        "related": related,
        "drift": classes,
        "primary_drift": primary_drift(classes),
        "chain": {
            "owner_repo": source_block.get("owner_repo"),
            "branch": source_block.get("branch"),
            "checkout_sha": source_block.get("checkout_sha"),
            "dirty": source_block.get("dirty"),
            "installed_sha": provenance.get("installed_sha"),
            "ahead": ab.get("ahead"),
            "behind": ab.get("behind"),
            "index_generation": (
                (index or {}).get("generation")
                or (index or {}).get("bundle_id")
                or (index or {}).get("snapshot_ref")
                or (
                    _unknown("no index for this tool")
                    if index is None
                    else _unknown("index present but generation field missing")
                )
            ),
        },
    }


def build_receipt(
    specs: Sequence[ToolSpec] | None = None,
) -> dict[str, Any]:
    """Inspect every tool spec (default: the fleet roster) into one receipt payload."""
    tool_specs = list(specs) if specs is not None else fleet_tool_specs()
    tools = [inspect_tool(spec) for spec in tool_specs]
    return {
        "schema": SCHEMA_VERSION,
        "generated_by": "vibecrafted receipt",
        "cwd_policy": (
            "never uses process cwd to identify a tool's source; "
            "env → binary_path → verified_candidate only"
        ),
        "tools": tools,
        "summary": {
            "tool_count": len(tools),
            "by_primary_drift": _count_primary(tools),
            "any_drift": any(t["primary_drift"] != DRIFT_CLEAN for t in tools),
        },
    }


def _count_primary(tools: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Tally tool rows by their ``primary_drift`` class."""
    counts: dict[str, int] = {}
    for tool in tools:
        key = str(tool.get("primary_drift") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt_link(value: Any) -> str:
    """Render a receipt field for text output: unwrap ``Unknown`` markers and bools."""
    if isinstance(value, dict) and value.get("value") == "unknown":
        reason = value.get("reason") or ""
        return f"unknown ({reason})" if reason else "unknown"
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _is_unknown(value: Any) -> bool:
    """True when a receipt field carries no answer (None or an Unknown marker)."""
    if value is None:
        return True
    return isinstance(value, dict) and value.get("value") == "unknown"


def render_receipt_text(receipt: dict[str, Any]) -> str:
    """Render a :func:`build_receipt` payload as the human-readable CLI report."""
    lines: list[str] = []
    lines.append("Delivery / Runtime Receipt")
    lines.append(f"schema: {receipt.get('schema')}")
    lines.append(f"policy: {receipt.get('cwd_policy')}")
    lines.append("")
    for tool in receipt.get("tools") or []:
        name = tool.get("name")
        primary = tool.get("primary_drift")
        drift_list = ", ".join(tool.get("drift") or [])
        lines.append(f"## {name}  [{primary}]")
        lines.append(f"  drift: {drift_list}")
        chain = tool.get("chain") or {}
        src = tool.get("source") or {}
        # Source↔install drift is a maintainer question. A plain install has no
        # checkout at all, and repeating the same "no verified source root"
        # reason across eight fields per tool read as breakage to anyone who
        # simply installed the product. Say it once; the JSON payload keeps
        # every field intact for tooling.
        source_only_fields_unknown = _is_unknown(src.get("path")) and _is_unknown(
            chain.get("checkout_sha")
        )
        if source_only_fields_unknown:
            lines.append(
                "  source:        not present (installed-only — normal unless "
                "you develop this tool)"
            )
        else:
            lines.append(f"  owner/repo:     {_fmt_link(chain.get('owner_repo'))}")
            lines.append(f"  source path:   {_fmt_link(src.get('path'))}")
            lines.append(f"  resolution:    {_fmt_link(src.get('resolution'))}")
            lines.append(f"  branch:        {_fmt_link(chain.get('branch'))}")
            lines.append(f"  checkout SHA:  {_fmt_link(chain.get('checkout_sha'))}")
            dirty = chain.get("dirty")
            detail = src.get("dirty_detail") or {}
            if isinstance(dirty, bool):
                lines.append(
                    f"  dirty:         {dirty} "
                    f"(source={_fmt_link(detail.get('source_dirty_count'))}, "
                    f"generated={_fmt_link(detail.get('generated_dirty_count'))})"
                )
            else:
                lines.append(f"  dirty:         {_fmt_link(dirty)}")
        installed = tool.get("installed") or {}
        lines.append(f"  installed:     {_fmt_link(installed.get('path'))}")
        lines.append(f"  installed SHA: {_fmt_link(installed.get('sha'))}")
        lines.append(f"  dirty build:   {_fmt_link(installed.get('dirty_build'))}")
        if installed.get("version_line"):
            lines.append(f"  version:       {installed.get('version_line')}")
        remote = tool.get("remote") or {}
        # Upstream ahead/behind is a checkout question too — three more
        # "unknown (no verified source root)" repeats without one.
        if not source_only_fields_unknown:
            lines.append(
                f"  upstream:      {_fmt_link(remote.get('upstream'))} "
                f"ahead={_fmt_link(remote.get('ahead'))} "
                f"behind={_fmt_link(remote.get('behind'))}"
            )
        index = tool.get("index")
        if index:
            lines.append(
                f"  index:         kind={index.get('kind')} "
                f"status={_fmt_link(index.get('status'))} "
                f"gen={_fmt_link(index.get('generation') or index.get('bundle_id'))}"
            )
        for rel in tool.get("related") or []:
            lines.append(
                f"  related:       {rel.get('name')} "
                f"on_path={rel.get('on_path')} "
                f"drift={','.join(rel.get('drift') or [])}"
            )
        lines.append("")
    summary = receipt.get("summary") or {}
    lines.append(
        f"summary: tools={summary.get('tool_count')} "
        f"by_primary={summary.get('by_primary_drift')}"
    )
    return "\n".join(lines) + "\n"


def receipt_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for ``vibecrafted receipt``: build and print the receipt."""
    args = list(argv or [])
    as_json = False
    filtered: list[str] = []
    for arg in args:
        if arg in {"--json", "-j"}:
            as_json = True
        elif arg in {"-h", "--help"}:
            print(
                "Usage: vibecrafted receipt [--json]\n"
                "\n"
                "One delivery/runtime receipt for fleet tools:\n"
                "  vc-frame, vibecrafted, scaffold-doctor, loct, aicx\n"
                "\n"
                "Each row binds: owner/repo → branch → checkout SHA → dirty\n"
                "→ installed SHA → ahead/behind → index generation.\n"
                "Drift: SOURCE_AHEAD_OF_INSTALLED | INSTALLED_NOT_ON_PATH |\n"
                "       UNPUSHED | DIRTY_BUILD_PROVENANCE | INDEX_STALE | CLEAN\n"
                "\n"
                "Never uses process cwd to identify a tool source.\n"
                "Set VC_FRAME_SOURCE / VIBECRAFTED_SOURCE / LOCTREE_SOURCE /\n"
                "AICX_SOURCE (or VIBECRAFTED_FLEET_ROOT) when auto-discovery fails.\n"
            )
            return 0
        else:
            filtered.append(arg)
    if filtered:
        print(f"error: unexpected arguments: {filtered}", flush=True)
        return 2
    receipt = build_receipt()
    if as_json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    else:
        print(render_receipt_text(receipt), end="")
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "build_receipt",
    "checkout_head_sha",
    "classify_drift",
    "find_git_dir",
    "fleet_tool_specs",
    "inspect_tool",
    "parse_installed_provenance",
    "receipt_main",
    "render_receipt_text",
]
