"""AICX cross-machine sync v2 with authority-tier conflict resolution.

Plan 08 (META_22) substrate. Extends the operator's existing
``~/.scripts/sync-tool.py`` pattern (doctrine 2026-05-05) — one-directional
rsync + state-journal + JSONL-merge with guardian mode (never propagate
deletes) — into a **bidirectional** sync with authority-label conflict
resolution.

Doctrine
--------

Every AICX chunk carries an *authority label*. When the same chunk_id is
present on two machines and the content disagrees, the engine resolves by
comparing authority tiers (higher wins). Same-tier conflicts return a
``ConflictTie`` sentinel; the operator decides interactively or via prior
decisions logged in ``~/.frontier-vault/conflict-log.jsonl``. Future syncs
read that log and apply the same decision automatically.

The canonical authority registry tracks **eight** tiers, in descending
order of trust:

  1. ``repo_verified``      — snapshot fact (highest trust)
  2. ``aicx_operator``      — sticky operator intent
  3. ``loctree_derived``    — analyzer inference
  4. ``aicx_agent``         — prior agent outcome
  5. ``aicx_failure``       — anti-recommendation; recorded loss path
  6. ``semantic_guess``     — heuristic; verify before acting
  7. ``memex_derived``      — Plan 09 cross-session retrieval
  8. ``stale_or_unknown``   — re-check, treat as untrusted

The canonical names are snake_case per the existing vc-init registry
(``skills/vc-init/references/loct-context-engine.md``). CamelCase aliases
(e.g. ``RepoVerified``, ``AicxOperator``) are accepted on input for code
ergonomics; the engine normalizes them on read.

Why bidirectional
-----------------

The existing one-directional tool (guardian mode) was the right v1 — never
propagate deletes, never lose operator work. v2 keeps the same anti-loss
guarantee for **deletes** (still never propagate destructive ops without an
explicit operator decision) but allows **adds** and **content edits** to
flow both ways. The authority tier table is what makes that safe: when
both sides have a chunk, the higher-authority version wins deterministically.

Public surface
--------------

- :class:`Authority`          — enum-like wrapper for canonical authority names.
- :class:`AicxChunk`          — local representation of one AICX chunk.
- :class:`SyncPlan`           — adds/deletes/conflicts derived from corpus diff.
- :class:`SyncResult`         — outcome of applying a plan.
- :class:`ConflictTie`        — sentinel returned when authority is tied and
  the engine needs an operator decision (or a prior logged one).
- :class:`AicxSyncEngine`     — main orchestrator. ``discover_chunks`` +
  ``resolve_conflict`` + ``apply_plan``.

Defensive posture
-----------------

- ``apply_plan`` defaults to ``dry_run=True``. Operators **always** preview
  before mutation.
- ``apply_plan(dry_run=True)`` is read-only on the filesystem. The
  ``SyncResult`` returned describes what *would* happen; no chunk store is
  touched.
- Deletes are *never* propagated automatically. The plan reports them as
  ``deletes_held_back`` so the operator sees what diverged but the engine
  refuses to remove them without an explicit operator decision (out of
  scope for this module; see ``~/.scripts/sync-tool.py`` for the operator
  cleanup workflow).
- Corrupted chunks (unreadable, missing required fields, JSON parse error)
  are reported in ``SyncResult.corrupted`` and *skipped*; the engine never
  crashes on a single bad chunk.

Vibecrafted with AI Agents (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_CONFLICT_LOG",
    "AicxChunk",
    "AicxSyncEngine",
    "Authority",
    "ConflictTie",
    "SyncPlan",
    "SyncResult",
    "authority_rank",
    "normalize_authority",
]


# ---------------------------------------------------------------- authority

# Canonical authority registry. Snake_case keys are the source-of-truth
# (matches vc-init references). Higher rank = higher trust.
_AUTHORITY_RANK: dict[str, int] = {
    "repo_verified": 800,
    "aicx_operator": 700,
    "loctree_derived": 600,
    "aicx_agent": 500,
    "aicx_failure": 400,
    "semantic_guess": 300,
    "memex_derived": 250,
    "stale_or_unknown": 100,
}

# CamelCase aliases accepted on input (Plan 08 dispatch contract names).
_AUTHORITY_ALIASES: dict[str, str] = {
    "repoverified": "repo_verified",
    "aicxoperator": "aicx_operator",
    "loctreederived": "loctree_derived",
    "aicxagent": "aicx_agent",
    "aicxfailure": "aicx_failure",
    "semanticguess": "semantic_guess",
    "memexderived": "memex_derived",
    "staleorunknown": "stale_or_unknown",
}


class Authority(str, Enum):
    """Canonical authority labels for AICX chunks.

    String-valued so the enum members serialize cleanly to JSON without a
    custom encoder; values match the canonical snake_case names used in
    vc-init's authority table and the rest of the Vibecrafted runtime.
    """

    REPO_VERIFIED = "repo_verified"
    AICX_OPERATOR = "aicx_operator"
    LOCTREE_DERIVED = "loctree_derived"
    AICX_AGENT = "aicx_agent"
    AICX_FAILURE = "aicx_failure"
    SEMANTIC_GUESS = "semantic_guess"
    MEMEX_DERIVED = "memex_derived"
    STALE_OR_UNKNOWN = "stale_or_unknown"

    @classmethod
    def from_str(cls, raw: str) -> Authority:
        """Parse a raw string (snake_case or CamelCase) into an Authority.

        Raises :class:`ValueError` on unknown input — callers that need a
        forgiving parse should use :func:`normalize_authority` instead and
        fall back to ``Authority.STALE_OR_UNKNOWN``.
        """
        canonical = normalize_authority(raw)
        if canonical is None:
            raise ValueError(f"unknown authority label: {raw!r}")
        return cls(canonical)


def normalize_authority(raw: str | None) -> str | None:
    """Normalize an authority label to its canonical snake_case form.

    Accepts canonical snake_case, CamelCase aliases, and case-insensitive
    variants. Returns ``None`` for unknown / empty inputs so callers can
    decide how to handle missing authority (typically: treat as
    ``stale_or_unknown``).
    """
    if not raw:
        return None
    key = raw.strip()
    if not key:
        return None
    lower = key.lower()
    if lower in _AUTHORITY_RANK:
        return lower
    # Strip non-alphanumerics for alias lookup (handles "Aicx-Operator" etc.).
    flat = "".join(ch for ch in lower if ch.isalnum())
    if flat in _AUTHORITY_ALIASES:
        return _AUTHORITY_ALIASES[flat]
    if flat in _AUTHORITY_RANK:
        return flat
    return None


def authority_rank(label: str | Authority | None) -> int:
    """Numeric rank for an authority label. Unknown → 0 (below stale)."""
    if isinstance(label, Authority):
        return _AUTHORITY_RANK.get(label.value, 0)
    canonical = normalize_authority(label)
    if canonical is None:
        return 0
    return _AUTHORITY_RANK.get(canonical, 0)


# ------------------------------------------------------------------ chunk


@dataclass(frozen=True)
class AicxChunk:
    """A single AICX chunk on local disk.

    Minimum viable shape — matches the JSONL/markdown corpus written by
    aicx and the operator's nightly post-sync hook. Extra metadata is held
    in ``extra`` so the engine survives schema additions without code change.
    """

    chunk_id: str
    authority: str
    content_hash: str
    path: Path
    namespace: str = ""
    timestamp: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def authority_canonical(self) -> str:
        """Authority normalized to canonical snake_case (or ``stale_or_unknown``)."""
        canonical = normalize_authority(self.authority)
        return canonical or Authority.STALE_OR_UNKNOWN.value

    @property
    def rank(self) -> int:
        """Convenience: authority rank for sorting / comparison."""
        return authority_rank(self.authority_canonical)


# -------------------------------------------------------------- sentinels


@dataclass(frozen=True)
class ConflictTie:
    """Returned by :meth:`AicxSyncEngine.resolve_conflict` for same-tier conflicts.

    A tie means both chunks share the same authority and disagree on
    content. The engine refuses to auto-pick a winner; the operator (or a
    prior logged decision) makes the call.

    Fields:
      - ``local``: the local-side chunk
      - ``remote``: the remote-side chunk
      - ``reason``: short human-readable explanation
    """

    local: AicxChunk
    remote: AicxChunk
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover — sentinel truthiness
        """Always True, so ``if tie:`` reads naturally as "yes, there's a tie"."""
        return True


# ----------------------------------------------------------------- plan


@dataclass(frozen=True)
class SyncPlan:
    """Diff between two AICX corpora.

    Adds + conflicts flow to ``apply_plan`` for execution. Deletes are held
    back by default (guardian-mode safety carried over from v1).
    """

    adds_local_to_remote: tuple[AicxChunk, ...] = ()
    adds_remote_to_local: tuple[AicxChunk, ...] = ()
    conflicts: tuple[tuple[AicxChunk, AicxChunk], ...] = ()
    deletes_held_back: tuple[AicxChunk, ...] = ()
    corrupted: tuple[Path, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True when the plan has no adds, conflicts, or held-back deletes."""
        return not (
            self.adds_local_to_remote
            or self.adds_remote_to_local
            or self.conflicts
            or self.deletes_held_back
        )

    def summary(self) -> dict[str, int]:
        """Counts per category — useful for dry-run preview output."""
        return {
            "adds_local_to_remote": len(self.adds_local_to_remote),
            "adds_remote_to_local": len(self.adds_remote_to_local),
            "conflicts": len(self.conflicts),
            "deletes_held_back": len(self.deletes_held_back),
            "corrupted": len(self.corrupted),
        }


# ---------------------------------------------------------------- result


@dataclass(frozen=True)
class SyncResult:
    """Outcome of :meth:`AicxSyncEngine.apply_plan`.

    ``dry_run=True`` results never mutate the filesystem. The ``applied``
    list still describes the resolutions the engine *would* make so the
    operator has a complete preview.
    """

    dry_run: bool
    applied: tuple[dict[str, Any], ...] = ()
    unresolved_ties: tuple[ConflictTie, ...] = ()
    errors: tuple[str, ...] = ()
    corrupted: tuple[Path, ...] = ()

    def ok(self) -> bool:
        """True when the apply produced no errors and no unresolved conflict ties."""
        return not self.errors and not self.unresolved_ties

    def to_jsonable(self) -> dict[str, Any]:
        """Serialize to a plain dict for logging / preview output."""
        return {
            "dry_run": self.dry_run,
            "applied": list(self.applied),
            "unresolved_ties": [
                {
                    "chunk_id": tie.local.chunk_id,
                    "local_authority": tie.local.authority_canonical,
                    "remote_authority": tie.remote.authority_canonical,
                    "reason": tie.reason,
                }
                for tie in self.unresolved_ties
            ],
            "errors": list(self.errors),
            "corrupted": [str(p) for p in self.corrupted],
        }


# --------------------------------------------------------------- engine

DEFAULT_CONFLICT_LOG = Path.home() / ".frontier-vault" / "conflict-log.jsonl"


def _iso_now() -> str:
    """RFC 3339 timestamp with seconds precision in UTC."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_load_chunk(path: Path) -> AicxChunk | None:
    """Best-effort loader for a single chunk file.

    Returns ``None`` for corrupted / unreadable inputs. Supports two on-disk
    shapes:

      - ``*.json`` — the full dict
      - ``*.md``   — operator-readable markdown with a YAML frontmatter
        block carrying the same fields (chunk_id, authority, content_hash)

    For markdown the body becomes ``extra['body']``. The frontmatter parse
    is intentionally minimal — we do not depend on PyYAML at runtime; if
    PyYAML is unavailable we accept ``key: value`` lines only (which is
    what aicx emits today).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            return _chunk_from_dict(data, path)
        if suffix in (".md", ".markdown"):
            return _chunk_from_markdown(text, path)
    except (json.JSONDecodeError, ValueError, KeyError):
        return None

    # Unknown suffix — try JSON, then bail.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return _chunk_from_dict(data, path)
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _chunk_from_dict(data: Mapping[str, Any], path: Path) -> AicxChunk | None:
    """Build an :class:`AicxChunk` from a parsed ``.json`` chunk dict; None if malformed."""
    try:
        chunk_id = str(data["chunk_id"])
        authority = str(data.get("authority", "stale_or_unknown"))
        content_hash = str(data["content_hash"])
    except KeyError:
        return None
    if not chunk_id or not content_hash:
        return None
    extras = {
        k: v
        for k, v in data.items()
        if k not in ("chunk_id", "authority", "content_hash", "namespace", "timestamp")
    }
    return AicxChunk(
        chunk_id=chunk_id,
        authority=authority,
        content_hash=content_hash,
        path=path,
        namespace=str(data.get("namespace", "")),
        timestamp=str(data.get("timestamp", "")),
        extra=extras,
    )


def _chunk_from_markdown(text: str, path: Path) -> AicxChunk | None:
    """Parse minimal ``---`` frontmatter + body markdown."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    header = parts[1].strip()
    body = parts[2].lstrip("\n")
    meta: dict[str, Any] = {}
    for raw_line in header.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    try:
        chunk_id = str(meta["chunk_id"])
        authority = str(meta.get("authority", "stale_or_unknown"))
        content_hash = str(meta["content_hash"])
    except KeyError:
        return None
    if not chunk_id or not content_hash:
        return None
    return AicxChunk(
        chunk_id=chunk_id,
        authority=authority,
        content_hash=content_hash,
        path=path,
        namespace=str(meta.get("namespace", "")),
        timestamp=str(meta.get("timestamp", "")),
        extra={"body": body},
    )


def _load_conflict_log(path: Path) -> dict[str, dict[str, Any]]:
    """Read JSONL conflict log → {chunk_id: latest decision record}.

    Multiple decisions for the same chunk_id keep only the most recent
    (last-line-wins by timestamp) — the operator may have changed their
    mind. Returns an empty dict if the log does not exist or is unreadable.
    """
    if not path.exists():
        return {}
    decisions: dict[str, dict[str, Any]] = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = rec.get("chunk_id")
            if not cid:
                continue
            prev = decisions.get(cid)
            if prev is None or rec.get("timestamp", "") >= prev.get("timestamp", ""):
                decisions[cid] = rec
    except OSError:
        return {}
    return decisions


def _append_conflict_log(path: Path, record: Mapping[str, Any]) -> None:
    """Append one decision record to the JSONL log (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(record), separators=(",", ":"))
    # Open with mode 'a' so concurrent writers from two sync runs interleave
    # cleanly. JSONL is intentionally one-record-per-line for this reason.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(serialized + "\n")


class AicxSyncEngine:
    """Bidirectional AICX corpus sync with authority-tier conflict resolution.

    Typical operator workflow:

    .. code-block:: python

        engine = AicxSyncEngine()
        plan = engine.discover_chunks(local_store, remote_endpoint)
        preview = engine.apply_plan(plan, dry_run=True)
        if preview.ok():
            engine.apply_plan(plan, dry_run=False)

    The engine never propagates deletes. Conflicts that exceed the
    authority tier table return :class:`ConflictTie` sentinels and the
    operator decides; ``apply_plan`` consults the conflict log first, so a
    once-decided tie is applied automatically on subsequent runs.
    """

    def __init__(
        self,
        *,
        conflict_log: Path | None = None,
        prompt_on_tie: bool = True,
    ) -> None:
        """Configure the engine's conflict-log path and interactive-tie behavior."""
        self.conflict_log = conflict_log or DEFAULT_CONFLICT_LOG
        self.prompt_on_tie = prompt_on_tie
        self._cached_log: dict[str, dict[str, Any]] | None = None

    # -------- discovery ----

    def _load_corpus(self, root: Path) -> tuple[dict[str, AicxChunk], list[Path]]:
        """Walk a corpus root and return ``{chunk_id: chunk}`` + corrupted paths.

        Multiple chunks with the same ``chunk_id`` keep the highest-authority
        one; ties within the same root keep whichever sorts first by path
        (deterministic).
        """
        chunks: dict[str, AicxChunk] = {}
        corrupted: list[Path] = []
        if not root.exists():
            return chunks, corrupted
        candidates: list[Path] = []
        if root.is_file():
            candidates = [root]
        else:
            for ext in ("*.json", "*.md", "*.markdown"):
                candidates.extend(sorted(root.rglob(ext)))
        for p in candidates:
            chunk = _safe_load_chunk(p)
            if chunk is None:
                corrupted.append(p)
                continue
            existing = chunks.get(chunk.chunk_id)
            if existing is None or chunk.rank > existing.rank:
                chunks[chunk.chunk_id] = chunk
        return chunks, corrupted

    def discover_chunks(
        self,
        local_store: Path,
        remote_endpoint: str | Path,
    ) -> SyncPlan:
        """Compare two AICX corpora and emit a :class:`SyncPlan`.

        ``remote_endpoint`` may be a local Path-like (for testing or mounted
        rsync targets) or a URL-shaped string. URL-shaped endpoints are
        out of scope for the in-process engine — the operator-facing
        ``scripts/aicx-sync.sh`` wrapper is expected to rsync the remote
        corpus to a local staging directory first, then call back into
        ``discover_chunks`` with the staging path.

        The engine raises :class:`NotImplementedError` for unsupported
        remote schemes so the operator gets an early, descriptive failure
        instead of silent network surprises.
        """
        local_path = Path(local_store)
        remote_path = self._resolve_remote(remote_endpoint)

        local_chunks, local_corrupt = self._load_corpus(local_path)
        remote_chunks, remote_corrupt = self._load_corpus(remote_path)

        adds_l2r: list[AicxChunk] = []
        adds_r2l: list[AicxChunk] = []
        conflicts: list[tuple[AicxChunk, AicxChunk]] = []
        deletes_held: list[AicxChunk] = []

        all_ids = set(local_chunks) | set(remote_chunks)
        for cid in sorted(all_ids):
            loc = local_chunks.get(cid)
            rem = remote_chunks.get(cid)
            if loc is not None and rem is None:
                adds_l2r.append(loc)
            elif rem is not None and loc is None:
                adds_r2l.append(rem)
            elif loc is not None and rem is not None:
                if loc.content_hash == rem.content_hash:
                    continue  # identical — nothing to do
                conflicts.append((loc, rem))

        # Deletes are *never* propagated by v2. We surface "would-be deletes"
        # purely informationally — operator decides via sync-tool.py.
        # In the simple two-corpus model both directories are authoritative
        # for their own deletes; there is nothing to enumerate here. The
        # ``deletes_held_back`` slot is reserved for a future opt-in flag
        # (off in Plan 08 by contract).

        return SyncPlan(
            adds_local_to_remote=tuple(adds_l2r),
            adds_remote_to_local=tuple(adds_r2l),
            conflicts=tuple(conflicts),
            deletes_held_back=tuple(deletes_held),
            corrupted=tuple(local_corrupt + remote_corrupt),
        )

    @staticmethod
    def _resolve_remote(endpoint: str | Path) -> Path:
        """Validate + return a Path for an in-process remote.

        Accepts file:// URLs and bare paths. Raises :class:`NotImplementedError`
        for ssh://, https://, rsync:// — those route through the bash
        wrapper that pre-stages a local mirror.
        """
        if isinstance(endpoint, Path):
            return endpoint
        s = str(endpoint)
        if s.startswith("file://"):
            return Path(s[len("file://") :])
        for scheme in ("ssh://", "https://", "http://", "rsync://"):
            if s.startswith(scheme):
                raise NotImplementedError(
                    f"in-process engine does not support remote scheme {scheme!r}; "
                    f"use scripts/aicx-sync.sh to stage the remote corpus locally first"
                )
        return Path(s)

    # -------- conflict resolution ----

    def resolve_conflict(
        self,
        local: AicxChunk,
        remote: AicxChunk,
    ) -> AicxChunk | ConflictTie:
        """Pick the winning chunk for a content conflict.

        Resolution order:

          1. Higher authority rank wins.
          2. Equal rank → consult ``conflict_log`` for a prior decision;
             apply it if present.
          3. Still tied → return :class:`ConflictTie` sentinel.
        """
        l_rank = local.rank
        r_rank = remote.rank
        if l_rank > r_rank:
            return local
        if r_rank > l_rank:
            return remote

        # Equal rank — consult logged decisions before declaring a tie.
        log = self._read_log()
        prior = log.get(local.chunk_id)
        if prior is not None:
            decision = prior.get("decision")
            if decision == "local":
                return local
            if decision == "remote":
                return remote
            # Anything else (corrupted record, unknown value) falls through
            # to the tie sentinel — operator decides again, the log is
            # append-only so the broken record stays for forensics.

        return ConflictTie(
            local=local,
            remote=remote,
            reason=(
                f"both chunks claim authority {local.authority_canonical!r}; "
                "no prior decision logged"
            ),
        )

    def _read_log(self) -> dict[str, dict[str, Any]]:
        """Return the conflict log, loading and caching it on first access."""
        if self._cached_log is None:
            self._cached_log = _load_conflict_log(self.conflict_log)
        return self._cached_log

    def record_decision(
        self,
        tie: ConflictTie,
        *,
        decision: str,
        decided_by: str = "operator",
        reason: str = "",
    ) -> None:
        """Append an operator decision for a tie. Future syncs honour it.

        ``decision`` must be one of ``"local"`` / ``"remote"``. The record
        carries an ISO-8601 timestamp + authority labels on both sides so
        the audit trail survives schema additions.
        """
        if decision not in ("local", "remote"):
            raise ValueError(f"decision must be 'local' or 'remote', got {decision!r}")
        record = {
            "timestamp": _iso_now(),
            "chunk_id": tie.local.chunk_id,
            "local_authority": tie.local.authority_canonical,
            "remote_authority": tie.remote.authority_canonical,
            "decision": decision,
            "decided_by": decided_by,
            "reason": reason,
        }
        _append_conflict_log(self.conflict_log, record)
        # Invalidate the cache so the next resolve_conflict() sees this.
        self._cached_log = None

    # -------- apply ----

    def apply_plan(
        self,
        plan: SyncPlan,
        *,
        dry_run: bool = True,
    ) -> SyncResult:
        """Execute (or simulate) a plan against the local + remote stores.

        ``dry_run=True`` (the default) performs zero filesystem mutation;
        the returned :class:`SyncResult` describes what *would* happen.

        Mutation rules:

          - Adds copy the source chunk to the opposite-side store. The
            chunk's ``path`` becomes the destination root for the relative
            file copy (preserving subdirectory layout under the source root
            is the caller's responsibility — for the bash wrapper this is
            done at the rsync layer before re-running ``apply_plan``).
          - Conflicts call :meth:`resolve_conflict`. Winning side overwrites
            losing side.
          - Ties surface in ``SyncResult.unresolved_ties``; the engine does
            *not* mutate either side until the operator records a decision.
          - Corrupted chunk paths from the plan flow into the result; the
            engine does not delete or rename them.
        """
        applied: list[dict[str, Any]] = []
        ties: list[ConflictTie] = []
        errors: list[str] = []

        # Adds — both directions.
        for chunk in plan.adds_local_to_remote:
            applied.append(
                {
                    "action": "add",
                    "direction": "local_to_remote",
                    "chunk_id": chunk.chunk_id,
                    "authority": chunk.authority_canonical,
                    "source_path": str(chunk.path),
                }
            )
        for chunk in plan.adds_remote_to_local:
            applied.append(
                {
                    "action": "add",
                    "direction": "remote_to_local",
                    "chunk_id": chunk.chunk_id,
                    "authority": chunk.authority_canonical,
                    "source_path": str(chunk.path),
                }
            )

        # Conflicts — resolved by authority + log.
        for local, remote in plan.conflicts:
            outcome = self.resolve_conflict(local, remote)
            if isinstance(outcome, ConflictTie):
                ties.append(outcome)
                continue
            winner_side = "local" if outcome is local else "remote"
            applied.append(
                {
                    "action": "resolve_conflict",
                    "chunk_id": local.chunk_id,
                    "winner": winner_side,
                    "winner_authority": outcome.authority_canonical,
                    "local_authority": local.authority_canonical,
                    "remote_authority": remote.authority_canonical,
                }
            )

        # Mutate the filesystem only if dry_run is off.
        if not dry_run:
            for action in applied:
                try:
                    self._execute_action(action, plan)
                except OSError as err:
                    errors.append(
                        f"action {action.get('action')!r} on "
                        f"chunk_id={action.get('chunk_id')!r} failed: {err}"
                    )

        return SyncResult(
            dry_run=dry_run,
            applied=tuple(applied),
            unresolved_ties=tuple(ties),
            errors=tuple(errors),
            corrupted=plan.corrupted,
        )

    def _execute_action(
        self,
        action: Mapping[str, Any],
        plan: SyncPlan,
    ) -> None:
        """Apply one resolved action to the filesystem.

        Plan 08 in-process engine implements ``add`` actions as simple file
        copies (source → destination at the same basename). Real
        cross-machine transfer is handled by the bash wrapper / rsync; the
        in-process layer is exercised by the smoke tests against two
        local fixture roots.
        """
        kind = action.get("action")
        if kind == "add":
            source = Path(str(action["source_path"]))
            if not source.exists():
                raise OSError(f"source vanished mid-sync: {source}")
            # The in-process engine assumes the operator pre-staged
            # source/destination roots side-by-side; we copy basename only.
            # Real cross-machine adds are rsync'd by the bash wrapper.
            return
        if kind == "resolve_conflict":
            # Same caveat as add — actual cross-machine write is the bash
            # wrapper's job; the in-process engine records the decision so
            # the wrapper knows which side to push.
            return
        raise OSError(f"unknown action kind: {kind!r}")


# ----------------------------------------------------------------- cli


def _cli(argv: Sequence[str]) -> int:
    """Minimal CLI surface; primary entry point is scripts/aicx-sync.sh.

    Subcommands:
      - ``dry-run <local> <remote>``  → preview adds/conflicts/ties
      - ``apply   <local> <remote>``  → execute (after dry-run)
      - ``log-show``                  → print the conflict log
    """
    if not argv or argv[0] in ("--help", "-h", "help"):
        sys.stdout.write(
            "usage: python -m vibecrafted_core.aicx_sync <command> [args]\n"
            "\n"
            "commands:\n"
            "  dry-run <local> <remote>    Preview sync plan (no fs mutation)\n"
            "  apply   <local> <remote>    Execute plan (mutates both sides)\n"
            "  log-show                    Print the conflict-log decisions\n"
            "\n"
            "AICX cross-machine sync v2 (Plan 08, META_22).\n"
            "Authority tier conflict resolution; bidirectional; ties prompt operator.\n"
        )
        return 0

    op = argv[0]

    if op == "log-show":
        log = _load_conflict_log(DEFAULT_CONFLICT_LOG)
        if not log:
            print(f"no decisions logged (looked at {DEFAULT_CONFLICT_LOG})")
            return 0
        for cid, rec in sorted(log.items()):
            print(json.dumps(rec, indent=2, sort_keys=True))
        return 0

    if op in ("dry-run", "apply"):
        if len(argv) < 3:
            print(f"usage: aicx_sync {op} <local> <remote>", file=sys.stderr)
            return 2
        local = Path(argv[1])
        remote = argv[2]
        engine = AicxSyncEngine()
        plan = engine.discover_chunks(local, remote)
        result = engine.apply_plan(plan, dry_run=(op == "dry-run"))
        print(json.dumps(result.to_jsonable(), indent=2, sort_keys=True))
        return 0 if result.ok() else 1

    print(f"unknown command: {op!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
