"""Classify git-push text as destructive (operator button) or not.

Non-destructive remote push of the current feature branch is a free move
and, after an authored commit, a duty. Force, delete, mirror, tag broadcast,
and dest-is-trunk stay hard-stops. See ``skills/vc-operator/AUTONOMY.md``.
"""

from __future__ import annotations

import re
import shlex

_PUSH_INVOCATION = re.compile(r"\bgit\s+push\b[^\n;&|]*", re.IGNORECASE)
_FORCE_FLAG = re.compile(r"^(?:--force(?:-with-lease)?|-f)$", re.IGNORECASE)
_DELETE_FLAG = re.compile(r"^(?:--delete|-d)$", re.IGNORECASE)
_BROADCAST_FLAG = re.compile(r"^(?:--mirror|--all|--tags)$", re.IGNORECASE)
_OPTION_WITH_VALUE = {
    "--repo",
    "--receive-pack",
    "--exec",
    "--signed",
    "-o",
    "--push-option",
}
_PROTECTED_EXACT = frozenset({"main", "master", "develop", "trunk"})
_VERSION_TAG = re.compile(r"^v\d+(?:\.\d+){0,3}$", re.IGNORECASE)
_REMOTE_LIKE = re.compile(r"^(?:origin|upstream)$|://|@", re.IGNORECASE)


def destructive_remote_push(text: str) -> str | None:
    """Return the matching ``git push …`` snippet if it is a hard-stop, else None."""
    if not text:
        return None
    for match in _PUSH_INVOCATION.finditer(text):
        snippet = match.group(0).strip()
        if _invocation_is_destructive(snippet):
            return snippet
    return None


def _invocation_is_destructive(snippet: str) -> bool:
    tokens = _tokens(snippet)
    if len(tokens) < 2 or tokens[0].lower() != "git" or tokens[1].lower() != "push":
        return False
    skip_next = False
    for token in tokens[2:]:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            continue
        if _FORCE_FLAG.match(token) or token.startswith("+"):
            return True
        if _DELETE_FLAG.match(token) or token.startswith(":"):
            return True
        if _BROADCAST_FLAG.match(token):
            return True
        if token.startswith("-"):
            name = token.split("=", 1)[0]
            if name in _OPTION_WITH_VALUE and "=" not in token:
                skip_next = True
            continue
        if _ref_is_protected(token):
            return True
    return False


def _tokens(snippet: str) -> list[str]:
    try:
        return shlex.split(snippet)
    except ValueError:
        return snippet.split()


def _ref_is_protected(token: str) -> bool:
    cleaned = token.lstrip("+").strip(".,;:!?)'\"}]")
    dest = cleaned.rsplit(":", 1)[-1] if ":" in cleaned else cleaned
    dest = dest.removeprefix("refs/heads/").removeprefix("refs/tags/")
    dest = dest.removeprefix("origin/").removeprefix("upstream/")
    if not dest or _REMOTE_LIKE.search(dest):
        return False
    lowered = dest.lower()
    if lowered in _PROTECTED_EXACT:
        return True
    if lowered.startswith("release/"):
        return True
    return _VERSION_TAG.match(dest) is not None
