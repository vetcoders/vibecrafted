"""Interactive LIVE RUNS dashboard — the control-plane-direct census reader
(``python -m vibecrafted_core.live_dashboard``). The ``voc`` LIVE chip is
server-backed (`vibecrafted-app/tui-agent/src/observe.rs`, one origin
`/api/control/state`); this module is the direct reader beside it, not behind
it, and the two must never grow a second liveness definition between them.

One canonical liveness selector, shared with vc-frame's server census
(``zellij-server/src/vc_live_runs.rs``): a run is live when its
``runtime_runs/<id>/meta.json`` carries ``run_id`` + ``worker_pid`` and that
pid answers signal-0 (``EPERM`` still proves life; only ``ESRCH`` proves
death). Rows and the LIVE count both come from ONE ``scan_live_runs`` call —
this module never invents a second liveness definition.

One canonical workspace-identity resolution order, shared with the runtime
writer (``workspace_catalog.resolve_run_workspace_identity``): the exported
``VIBECRAFTED_WORKSPACE_ID`` first, then the ONE canonical catalog by
``canonical_root``, both arbitrated by that catalog. This module never invents
a second answer to "which workspace am I" — a dashboard that resolved its own
identity differently from the runtime would hide the very runs its shell
launched.

Transcript policy: HUMAN (``transcript.human.log``) is the default surface;
RAW (``transcript.log``) is an explicit toggle. A missing human transcript
renders as ``human transcript pending`` — never a silent JSON fallback.

No external processes: transcripts are followed with plain file reads, so
closing the dashboard cannot leak ``tail`` processes.
"""

from __future__ import annotations

import curses
import errno
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

REFRESH_SECONDS = 2.0
PENDING_HUMAN_NOTICE = "human transcript pending"


# --------------------------------------------------------------------------
# Canonical liveness selector (parity contract with vc_live_runs.rs)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class LiveRunCard:
    run_id: str
    agent: str
    skill: str
    root: str
    worker_pid: int
    # Cut B wire: runs launched after workspace propagation carry the explicit
    # workspace identity; legacy runs keep None and are matched by exact root.
    workspace_id: str | None = None

    @property
    def repo(self) -> str:
        return Path(self.root).name if self.root else ""


def worker_is_alive(pid: int) -> bool:
    """Signal-0 probe. EPERM still proves a live process; only ESRCH death."""
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def control_plane_root() -> Path:
    explicit = os.environ.get("VIBECRAFTED_CONTROL_PLANE")
    if explicit:
        return Path(explicit)
    home = os.environ.get("VIBECRAFTED_HOME")
    base = Path(home) if home else Path.home() / ".vibecrafted"
    return base / "control_plane"


def _card_from_meta(meta_path: Path) -> LiveRunCard | None:
    """One card from a runtime-run meta.json; incomplete metas are skipped,
    not guessed at — same contract as the Rust census."""
    import json

    try:
        value = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run_id = value.get("run_id")
    worker_pid = value.get("worker_pid")
    if not isinstance(run_id, str) or not isinstance(worker_pid, int):
        return None
    workspace_id = value.get("workspace_id")
    return LiveRunCard(
        run_id=run_id,
        agent=str(value.get("agent") or ""),
        skill=str(value.get("skill") or ""),
        root=str(value.get("root") or ""),
        worker_pid=worker_pid,
        workspace_id=workspace_id if isinstance(workspace_id, str) else None,
    )


def scan_live_runs(root: Path, is_alive=worker_is_alive) -> list[LiveRunCard]:
    """Census of currently-live runs — the ONE source for rows AND count."""
    runs_dir = root / "runtime_runs"
    try:
        entries = list(runs_dir.iterdir())
    except OSError:
        return []
    cards = [
        card
        for card in (_card_from_meta(entry / "meta.json") for entry in sorted(entries))
        if card is not None and is_alive(card.worker_pid)
    ]
    # run_id starts with a launch timestamp: lexical order is chronological.
    cards.sort(key=lambda card: card.run_id)
    return cards


WORKSPACE_CATALOG_SCHEMA = "vibecrafted.workspace-catalog.v1"
ENV_WORKSPACE_ID = "VIBECRAFTED_WORKSPACE_ID"


def _active_workspaces(plane_root: Path) -> dict[str, dict] | None:
    """Active records of the ONE canonical catalog, keyed by workspace_id.

    None means the catalog is unusable (absent, unreadable, foreign schema,
    malformed) — distinct from an empty catalog, which is a usable answer."""
    import json

    path = plane_root / "workspaces" / "catalog.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if value.get("schema") != WORKSPACE_CATALOG_SCHEMA:
        return None
    workspaces = value.get("workspaces")
    if not isinstance(workspaces, dict):
        return None
    active: dict[str, dict] = {}
    for workspace in workspaces.values():
        if not isinstance(workspace, dict) or workspace.get("buried_at"):
            continue
        workspace_id = workspace.get("workspace_id")
        if isinstance(workspace_id, str) and workspace_id:
            active[workspace_id] = workspace
    return active


def resolve_workspace_id(
    current_root: str,
    plane_root: Path,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Answer "which workspace is this dashboard?" in the SAME preference
    order the runtime uses when it stamps a run
    (``workspace_catalog.resolve_run_workspace_identity``):

    1. ``VIBECRAFTED_WORKSPACE_ID`` — the identity the runtime already
       resolved and exported into this process tree. A dashboard that ignores
       it disagrees with every run launched from its own shell.
    2. the ONE canonical catalog (``workspaces/catalog.json``) by
       ``canonical_root`` — never a second catalog, never basename guessing.

    Both steps are arbitrated by that same catalog: an env id naming no active
    workspace is stale evidence, not identity, and is refused. Unknown root /
    absent / foreign-schema catalog resolve to None; the filter then falls back
    to exact-root identity."""
    active = _active_workspaces(plane_root)
    if active is None:
        return None

    environ = os.environ if env is None else env
    claimed = str(environ.get(ENV_WORKSPACE_ID) or "").strip()
    if claimed and claimed in active:
        return claimed

    try:
        target = os.path.realpath(current_root)
    except OSError:
        target = current_root
    for workspace_id, workspace in active.items():
        root = workspace.get("canonical_root")
        if isinstance(root, str) and os.path.realpath(root) == target:
            return workspace_id
    return None


def run_started_label(run_id: str, now: float | None = None) -> str:
    """Compact age from the run_id's embedded YYMMDD-HHMMSS launch stamp."""
    parts = run_id.split("-")
    if len(parts) < 3 or len(parts[1]) != 6 or len(parts[2]) != 6:
        return "?"
    try:
        stamp = time.mktime(
            (
                2000 + int(parts[1][0:2]),
                int(parts[1][2:4]),
                int(parts[1][4:6]),
                int(parts[2][0:2]),
                int(parts[2][2:4]),
                int(parts[2][4:6]),
                0,
                0,
                -1,
            )
        )
    except (ValueError, OverflowError):
        return "?"
    age = max(0.0, (now if now is not None else time.time()) - stamp)
    if age < 3600:
        return f"{int(age // 60)}m"
    if age < 86400:
        return f"{int(age // 3600)}h"
    return f"{int(age // 86400)}d"


# --------------------------------------------------------------------------
# Pure dashboard state (curses-free, fully testable)
# --------------------------------------------------------------------------


@dataclass
class DashboardState:
    current_root: str
    cards: list[LiveRunCard] = field(default_factory=list)
    # 'current' selects by explicit workspace_id (Cut B wire contract) when
    # BOTH sides carry one. Both sides now answer that question the same way:
    # the run's identity comes from `resolve_run_workspace_identity` at launch,
    # the dashboard's from `resolve_workspace_id` here — one preference order,
    # env first, catalog-arbitrated. Legacy runs without workspace_id fall back
    # to full resolved-root identity — never the basename; two roots both named
    # `vibecrafted` stay distinct.
    current_workspace_id: str | None = None
    filter_mode: str = "current"
    selected_run_id: str | None = None
    all_streams: bool = False
    raw_transcript: bool = False

    def _same_workspace(self, card: LiveRunCard) -> bool:
        if self.current_workspace_id is not None and card.workspace_id is not None:
            return card.workspace_id == self.current_workspace_id
        try:
            card_root = str(Path(card.root).resolve())
            mine = str(Path(self.current_root).resolve())
        except OSError:
            return card.root == self.current_root
        return card_root == mine

    def visible_rows(self) -> list[LiveRunCard]:
        rows = [
            card
            for card in self.cards
            if self.filter_mode == "all" or self._same_workspace(card)
        ]
        # Stable sort: current repo first, newest started (run_id stamp)
        # descending, run_id as the final tie-breaker.
        rows.sort(
            key=lambda card: (
                not self._same_workspace(card),
                _reversed_lexical(card.run_id),
                card.run_id,
            )
        )
        return rows

    def refresh(self, cards: list[LiveRunCard]) -> None:
        """Adopt a fresh census; selection stays pinned to its run_id."""
        self.cards = cards
        rows = self.visible_rows()
        row_ids = [card.run_id for card in rows]
        if self.selected_run_id not in row_ids:
            self.selected_run_id = row_ids[0] if row_ids else None

    def live_count(self) -> int:
        """The census length — by construction the same list rows use."""
        return len(self.cards)

    def selected_card(self) -> LiveRunCard | None:
        for card in self.visible_rows():
            if card.run_id == self.selected_run_id:
                return card
        return None

    def move_selection(self, delta: int) -> None:
        rows = self.visible_rows()
        if not rows:
            self.selected_run_id = None
            return
        ids = [card.run_id for card in rows]
        selected = self.selected_run_id
        try:
            index = ids.index(selected) if selected is not None else 0
        except ValueError:
            index = 0
        self.selected_run_id = ids[max(0, min(len(ids) - 1, index + delta))]

    def toggle_filter(self) -> None:
        self.filter_mode = "all" if self.filter_mode == "current" else "current"
        self.refresh(self.cards)

    def transcript_path(self, card: LiveRunCard, plane_root: Path) -> Path:
        name = "transcript.log" if self.raw_transcript else "transcript.human.log"
        return plane_root / "runtime_runs" / card.run_id / name

    def stream_prefix(self, card: LiveRunCard) -> str:
        return f"{card.agent}/{card.repo}/{card.run_id}"

    def detail_header(self, card: LiveRunCard) -> str:
        mode = "RAW" if self.raw_transcript else "HUMAN"
        return f"{card.agent} · {card.repo} · {card.run_id} · running · {mode}"


def _reversed_lexical(value: str) -> tuple[int, ...]:
    """Sort helper: lexically descending without cmp_to_key."""
    return tuple(255 - byte for byte in value.encode("utf-8", "replace"))


# --------------------------------------------------------------------------
# Process-free transcript follower
# --------------------------------------------------------------------------


class TranscriptTail:
    """Incremental reader over one transcript file. Plain reads, no tail(1);
    a vanished/rotated file simply restarts from its head."""

    def __init__(self, path: Path, backlog_lines: int = 200) -> None:
        self.path = path
        self._offset = 0
        self._backlog = backlog_lines
        self._primed = False

    def poll(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            self._offset = 0
            self._primed = False
            return []
        if size < self._offset:  # truncated/rotated: start over
            self._offset = 0
            self._primed = False
        if not self._primed:
            lines = self._read_from(0)
            self._primed = True
            return lines[-self._backlog :]
        return self._read_from(self._offset)

    def _read_from(self, offset: int) -> list[str]:
        try:
            with self.path.open("rb") as handle:
                handle.seek(offset)
                blob = handle.read()
                self._offset = handle.tell()
        except OSError:
            return []
        return blob.decode("utf-8", "replace").splitlines()


# --------------------------------------------------------------------------
# Curses front-end
# --------------------------------------------------------------------------


def _draw(
    stdscr, state: DashboardState, plane_root: Path, tail_lines: list[str]
) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()

    def put(y: int, x: int, text: str, attr: int = 0) -> None:
        if 0 <= y < height:
            stdscr.addnstr(y, x, text, max(0, width - x - 1), attr)

    rows = state.visible_rows()
    current_tag = (
        "[current repo]" if state.filter_mode == "current" else " current repo "
    )
    all_tag = "[all]" if state.filter_mode == "all" else " all "
    put(
        0,
        0,
        f"LIVE RUNS  {state.live_count()}     {current_tag} {all_tag}",
        curses.A_BOLD,
    )

    list_height = max(1, min(len(rows), max(1, (height - 6) // 2)))
    for index, card in enumerate(rows[:list_height]):
        marker = ">" if card.run_id == state.selected_run_id else " "
        attr = curses.A_REVERSE if card.run_id == state.selected_run_id else 0
        put(
            2 + index,
            0,
            f"{marker} {card.agent:<7.7} {card.repo:<12.12} "
            f"{run_started_label(card.run_id):>4}   {card.run_id}",
            attr,
        )
    if not rows:
        put(2, 2, "no live runs — workers appear here while their pid is alive")

    help_row = 2 + max(1, list_height)
    put(help_row, 0, "Enter open · A all streams · R raw · Esc close", curses.A_DIM)
    put(help_row + 1, 0, "─" * max(0, width - 1))

    selected = state.selected_card()
    if state.all_streams:
        put(
            help_row + 2,
            0,
            f"ALL STREAMS · {len(rows)} live · "
            + ("RAW" if state.raw_transcript else "HUMAN"),
            curses.A_BOLD,
        )
    elif selected is not None:
        put(help_row + 2, 0, state.detail_header(selected), curses.A_BOLD)
        if (
            not state.raw_transcript
            and not state.transcript_path(selected, plane_root).exists()
        ):
            tail_lines = [PENDING_HUMAN_NOTICE]

    body_top = help_row + 3
    body_height = max(0, height - body_top - 1)
    for index, line in enumerate(tail_lines[-body_height:]):
        put(body_top + index, 0, line)
    stdscr.noutrefresh()
    curses.doupdate()


def run_dashboard(
    stdscr, plane_root: Path | None = None, current_root: str | None = None
) -> None:
    plane = plane_root or control_plane_root()
    root = current_root or os.getcwd()
    state = DashboardState(
        current_root=root,
        current_workspace_id=resolve_workspace_id(root, plane),
    )
    state.refresh(scan_live_runs(plane))

    curses.curs_set(0)
    stdscr.timeout(250)
    tails: dict[str, TranscriptTail] = {}
    body: list[str] = []
    last_scan = 0.0

    while True:
        now = time.monotonic()
        if now - last_scan >= REFRESH_SECONDS:
            state.refresh(scan_live_runs(plane))
            last_scan = now

        targets: list[LiveRunCard] = (
            state.visible_rows()
            if state.all_streams
            else [card for card in [state.selected_card()] if card]
        )
        active_ids = {card.run_id for card in targets}
        for stale in [run_id for run_id in tails if run_id not in active_ids]:
            del tails[stale]
        for card in targets:
            path = state.transcript_path(card, plane)
            tail = tails.get(card.run_id)
            if tail is None or tail.path != path:
                tails[card.run_id] = tail = TranscriptTail(path)
            for line in tail.poll():
                prefix = f"{state.stream_prefix(card)} · " if state.all_streams else ""
                body.append(prefix + line)
        body = body[-2000:]

        _draw(stdscr, state, plane, body)

        key = stdscr.getch()
        if key in (27, ord("q")):  # Esc / q
            return
        if key in (curses.KEY_UP, ord("k")):
            state.move_selection(-1)
            body = []
        elif key in (curses.KEY_DOWN, ord("j")):
            state.move_selection(1)
            body = []
        elif key in (curses.KEY_ENTER, 10, 13):
            state.all_streams = False
            body = []
        elif key in (ord("a"), ord("A")):
            state.all_streams = not state.all_streams
            body = []
        elif key in (ord("r"), ord("R")):
            state.raw_transcript = not state.raw_transcript
            tails.clear()
            body = []
        elif key in (ord("t"), ord("T")):
            state.toggle_filter()
            body = []


def main() -> int:
    try:
        curses.wrapper(run_dashboard)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
