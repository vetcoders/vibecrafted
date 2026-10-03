"""Architecture guard for the control-plane locking doctrine.

INCIDENT (2026-07-12): a single global ``.sync.lock`` taken with an unbounded
``flock(LOCK_EX)`` serialized EVERY control-plane mutation of ALL runs. Because
the per-run hot path (lookup/await/heartbeat) and the append path (every
spawn/emit/stop event) all took that one lock, a fleet under load thundering-
herded: new dispatchers hung forever on an empty pane, and — the heartbeat
write being behind the same lock — a live-but-lock-starved worker could not
record liveness and was falsely declared stalled/pid_gone after 120s.

The cure: the per-run path is free of the global lock and uses only its own
run-id mutation key; the append path uses only the dedicated event lock around
one bounded ``O_APPEND`` write, rollback, and fsync; only the rare full-board
rebuild in ``sync_state`` still takes the (now bounded) global lock.

This guard exists because the WRONG fix is the one "the rest of the world" and
training-data habit both suggest: wrap the append/hot path back in a global
mutex "for safety". That reintroduces the migraine. If you are here because this
test failed, do not delete it — the design is deliberate. Append-only JSONL uses
its event lock; conflicting snapshots use an independent per-run CAS. Neither
needs the shared lock. See the fix commits.
"""

from __future__ import annotations

import ast
from pathlib import Path

import vibecrafted_core.control_plane as control_plane_module
import vibecrafted_core.events as events_module

# The ONLY functions permitted to acquire the shared global lock. Everything
# else — especially the per-run and append/emit hot paths — must be lockless.
# drain_settled_snapshots is a board-level mutator like the full rebuild:
# it batches (≤50 snapshots per acquisition, lock released between batches)
# and never sits on a per-run or append/emit path.
_SYNC_LOCK_ALLOWED_CALLERS = {"sync_state", "drain_settled_snapshots"}

# Hot-path functions that must NEVER acquire the shared sync lock. Adding _sync_lock
# to any of these is the exact regression this guard blocks.
_HOT_PATH_LOCKLESS = {
    "_append_event",
    "append_event",
    "record_stop_transition",
    "_record_transition",
    "lookup_run",
    "await_run",
}


def _module_path(module) -> Path:
    return Path(module.__file__)


def _enclosing_function(tree: ast.AST, target: ast.AST) -> str | None:
    """Return the name of the nearest FunctionDef enclosing ``target``."""
    best: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for descendant in ast.walk(node):
                if descendant is target:
                    best = node.name
    return best


def _sync_lock_call_sites(tree: ast.AST) -> list[tuple[str | None, int]]:
    sites: list[tuple[str | None, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name == "_sync_lock":
            sites.append((_enclosing_function(tree, node), node.lineno))
    return sites


def test_sync_lock_is_only_acquired_by_the_board_rebuild() -> None:
    """No caller outside the allowlist may take the shared control-plane lock.

    Guards against reintroducing the global-serialization migraine by wrapping
    the per-run or append path back in the mutex.
    """
    tree = ast.parse(_module_path(control_plane_module).read_text(encoding="utf-8"))
    offenders = [
        f"{caller or '<module>'}:{lineno}"
        for caller, lineno in _sync_lock_call_sites(tree)
        if caller not in _SYNC_LOCK_ALLOWED_CALLERS
    ]
    assert not offenders, (
        "control-plane lock doctrine violated: _sync_lock acquired outside "
        f"{sorted(_SYNC_LOCK_ALLOWED_CALLERS)} at {offenders}. The per-run and "
        "append/emit paths must stay free of the global sync lock (see this "
        "file's docstring / the "
        "2026-07-12 flock incident). Do not re-serialize them 'for safety'."
    )


def test_hot_path_functions_contain_no_sync_lock() -> None:
    """The append/emit/per-run hot paths must not mention the shared lock."""
    tree = ast.parse(_module_path(control_plane_module).read_text(encoding="utf-8"))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in _HOT_PATH_LOCKLESS:
            continue
        for _caller, lineno in _sync_lock_call_sites(node):
            violations.append(f"{node.name}:{lineno}")
    assert not violations, (
        "hot-path function acquired _sync_lock (regression): "
        f"{violations}. These must stay free of the global sync lock."
    )


def test_events_append_is_lockless() -> None:
    """events.append_event must not import or take the shared lock."""
    source = _module_path(events_module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not _sync_lock_call_sites(tree), (
        "events.append_event took _sync_lock — the append path must be a "
        "lockless atomic O_APPEND write. Reverting this reintroduces the herd."
    )


def test_append_event_uses_atomic_o_append() -> None:
    """The append primitive must retain its bounded O_APPEND write."""
    tree = ast.parse(_module_path(control_plane_module).read_text(encoding="utf-8"))
    append_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_append_event"
    )
    names = {
        node.attr for node in ast.walk(append_fn) if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(append_fn) if isinstance(node, ast.Name)}
    assert "O_APPEND" in names, (
        "_append_event no longer uses an O_APPEND write. The bounded append "
        "inside the dedicated event lock is the stream write contract."
    )
