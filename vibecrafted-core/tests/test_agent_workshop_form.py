"""Comprehensive unit and regression tests for Workshop Form UX repairs & ZenEntry onboarding.

Covers:
- Findings: 1000002, 12, 15, 1000008, 1000011, 18, 1000013, 22, 23
- Acceptance Criteria: #1..#11, #16
- Monaspace typography selector and ZenEntry onboarding scene integration
- Per-stage launch progress, error stage attribution, and Enter/mouse launch parity
"""

from __future__ import annotations

import curses
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from vibecrafted_core import command_bridge_onboarding as cbo

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-agent-workshop.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vc_agent_workshop_form_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingWindow:
    def __init__(self, height: int = 30, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.drawn: list[tuple[int, int, str, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def addstr(self, row: int, col: int, text: str, attr: int = 0) -> None:
        self.drawn.append((row, col, text, attr))

    def erase(self) -> None:
        self.drawn.clear()

    def refresh(self) -> None:
        pass


def _dispatch_key(form: Any, key: int) -> None:
    if getattr(form, "onboarding_active", False):
        form.handle_onboarding_key(key)
    elif form.mode == "launcher":
        form.handle_launcher_key(key)
    else:
        form.handle_home_key(key)


# ─── Acceptance Criteria #1 & #3 (Finding 23, 1000011): Truthful Project Path Initialization ───


def test_truthful_project_path_initialization_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When VIBECRAFTED_WORKSPACE_ROOT is unset, path defaults to empty string and shows placeholder."""
    monkeypatch.delenv("VIBECRAFTED_WORKSPACE_ROOT", raising=False)
    workshop = _load()
    window = RecordingWindow()
    form = workshop.Workshop(window, mode="launcher")

    assert form.path == ""
    assert form.workspace == ""
    assert form.path_explicit is False

    # Check that drawing renders the placeholder
    form.draw_launcher()
    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert any(workshop.PATH_PLACEHOLDER in t for t in drawn_texts)


def test_truthful_project_path_initialization_when_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When VIBECRAFTED_WORKSPACE_ROOT points to a valid existing dir, path is prefilled."""
    valid_dir = tmp_path / "my_project"
    valid_dir.mkdir()
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(valid_dir))

    workshop = _load()
    window = RecordingWindow()
    form = workshop.Workshop(window, mode="launcher")

    assert form.path == str(valid_dir.resolve())
    assert form.workspace == str(valid_dir.resolve())
    assert form.path_explicit is True


def test_truthful_project_path_initialization_ignores_nonexistent_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When VIBECRAFTED_WORKSPACE_ROOT points to a non-existent dir, path defaults to empty."""
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(tmp_path / "does_not_exist"))
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")

    assert form.path == ""
    assert form.path_explicit is False


# ─── Acceptance Criteria #1 & #2 (Finding 1000002): Path Editor Navigation, Editing & Isolation ───


def test_path_editor_cursor_navigation_and_editing() -> None:
    """Left, Right, Home, End, Ctrl+A, Ctrl+E, Backspace, Delete operate mid-path correctly."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 1  # Project path row
    form.path = "hello/world"
    form.path_cursor = 5  # Right after 'hello'

    # Insert mid-path
    _dispatch_key(form, ord("-"))
    assert form.path == "hello-/world"
    assert form.path_cursor == 6

    # Left arrow
    _dispatch_key(form, curses.KEY_LEFT)
    assert form.path_cursor == 5

    # Home / Ctrl+A
    _dispatch_key(form, 1)  # Ctrl+A
    assert form.path_cursor == 0

    # Right arrow
    _dispatch_key(form, curses.KEY_RIGHT)
    assert form.path_cursor == 1

    # End / Ctrl+E
    _dispatch_key(form, 5)  # Ctrl+E
    assert form.path_cursor == len("hello-/world")

    # Backspace (127)
    _dispatch_key(form, 127)
    assert form.path == "hello-/worl"
    assert form.path_cursor == len("hello-/worl")

    # Move cursor to index 5 and Delete (KEY_DC)
    form.path_cursor = 5
    _dispatch_key(form, curses.KEY_DC)
    # At index 5 was '-', so deleting it leaves 'hello/worl'
    assert form.path == "hello/worl"
    assert form.path_cursor == 5


def test_path_editor_literal_character_insertion_and_shortcut_isolation() -> None:
    """Keys a, A, c, C, e, E, r, R, o, O, f, F, +, -, 1, 2, 3 insert literally without triggering actions."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 1  # Focused on path
    initial_agent = form.agent
    initial_advanced = form.advanced
    initial_count = form.agent_count

    test_chars = "aAceErRoOfF+-123"
    for ch in test_chars:
        _dispatch_key(form, ord(ch))

    assert form.path == test_chars
    assert form.path_cursor == len(test_chars)
    # None of the shortcuts were triggered!
    assert form.agent == initial_agent
    assert form.advanced == initial_advanced
    assert form.agent_count == initial_count
    assert form.onboarding_active is False


def test_paste_path_text_burst_insertion() -> None:
    """paste_path_text inserts multi-character text at cursor without corruption."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 1
    form.path = "/users/test"
    form.path_cursor = 6  # after '/users'

    form.paste_path_text("/alice")
    assert form.path == "/users/alice/test"
    assert form.path_cursor == 12


# ─── Acceptance Criteria #3 & #4 (Findings 12, 1000011): Directory-Only Autocomplete ───


def test_compute_path_suggestions_filters_files_and_expands_dirs(
    tmp_path: Path,
) -> None:
    """_compute_path_suggestions returns directories only, ignoring regular files."""
    workshop = _load()
    # Create subdirs and files
    (tmp_path / "alpha_dir").mkdir()
    (tmp_path / "beta_dir").mkdir()
    (tmp_path / "alpha_file.txt").write_text("hello")

    base_str = str(tmp_path) + "/"
    suggestions = workshop._compute_path_suggestions(base_str)
    # Must only contain alpha_dir and beta_dir, not alpha_file.txt
    assert any("alpha_dir/" in s for s in suggestions)
    assert any("beta_dir/" in s for s in suggestions)
    assert not any("alpha_file.txt" in s for s in suggestions)


def test_autocomplete_dropdown_interaction_and_tab_cycle(tmp_path: Path) -> None:
    """Tab opens suggestions, Up/Down cycles them, Enter accepts without launching."""
    workshop = _load()
    (tmp_path / "proj_alpha").mkdir()
    (tmp_path / "proj_beta").mkdir()

    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 1
    form.path = str(tmp_path) + "/proj_"
    form.path_cursor = len(form.path)

    # Press Tab: opens suggestions
    _dispatch_key(form, 9)  # Tab
    assert form.path_suggestions_open is True
    assert len(form.path_suggestions) == 2

    # Up/Down navigation in suggestions
    initial_idx = form.path_suggestion_idx
    _dispatch_key(form, curses.KEY_DOWN)
    assert form.path_suggestion_idx == (initial_idx + 1) % len(form.path_suggestions)

    # Enter on suggestion: accepts directory into path, keeps suggestions open or drillable, does NOT launch
    chosen = form.path_suggestions[form.path_suggestion_idx]
    _dispatch_key(form, 10)  # Enter
    assert form.path == chosen
    # Launcher was NOT submitted:
    assert form.error == ""


# ─── Acceptance Criteria #5 & #6 (Findings 12, 15): Layered Esc & Dedicated Ctrl+Q ───


def test_layered_esc_dismissal_order() -> None:
    """Esc dismisses suggestions -> onboarding -> error details -> error -> advanced -> status notice."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")

    # Layer 1: Suggestions open
    form.path_suggestions_open = True
    _dispatch_key(form, 27)  # Esc
    assert form.path_suggestions_open is False

    # Layer 2: Onboarding active
    form.onboarding_active = True
    form.onboarding_step = 2
    _dispatch_key(form, 27)
    assert form.onboarding_step == 1  # steps back to step 1
    _dispatch_key(form, 27)
    assert form.onboarding_active is False  # closes onboarding

    # Layer 3: Error details open
    form.error = "Some launch failure"
    form.error_details = True
    _dispatch_key(form, 27)
    assert form.error_details is False
    assert form.error == "Some launch failure"

    # Layer 4: Error active
    _dispatch_key(form, 27)
    assert form.error == ""

    # Layer 5: Advanced options open
    form.advanced = True
    _dispatch_key(form, 27)
    assert form.advanced is False

    # Layer 6: In standalone launcher mode, Esc sets notice instead of quitting
    _dispatch_key(form, 27)
    assert "Ctrl+Q" in form.notice


def test_dedicated_ctrl_q_with_dirty_confirmation(tmp_path: Path) -> None:
    """Ctrl+Q requires double-press if form is dirty; Esc cancels quit confirmation."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    # Make form dirty by editing path
    form.path = str(tmp_path)
    assert form._is_dirty() is True

    # First Ctrl+Q (code 17)
    _dispatch_key(form, 17)
    assert form.confirm_quit is True
    assert "Unsaved form changes" in form.notice

    # Esc cancels quit confirmation
    _dispatch_key(form, 27)
    assert form.confirm_quit is False

    # First Ctrl+Q again
    _dispatch_key(form, 17)
    assert form.confirm_quit is True

    # Second Ctrl+Q confirms quit (raises SystemExit(0))
    with pytest.raises(SystemExit):
        _dispatch_key(form, 17)


# ─── Acceptance Criteria #7 (Finding 1000008): Structured Error Panel & Recovery ───


def test_structured_error_panel_and_recovery_actions() -> None:
    """Error panel preserves form inputs, sets stage, and provides r (retry), Esc, e (details)."""
    workshop = _load()
    window = RecordingWindow(height=30, width=60)
    form = workshop.Workshop(window, mode="launcher")
    form.path = "/test/repo"
    form.error = "Failed to launch tab: connection refused"
    form.error_stage = "Agent tab launch"

    form.draw_launcher()
    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert any(form.error_stage in t for t in drawn_texts)
    assert any("Retry" in t for t in drawn_texts)
    assert any("Details" in t for t in drawn_texts)

    # Toggle details with 'e'
    _dispatch_key(form, ord("e"))
    assert form.error_details is True

    # Toggle off with 'e'
    _dispatch_key(form, ord("e"))
    assert form.error_details is False

    # Path and state remain intact
    assert form.path == "/test/repo"


# ─── Acceptance Criteria #8 & #16 (Finding 1000011, 18): Agent Stepper Controls ───


def test_agent_stepper_controls_and_clamping() -> None:
    """Agent count ranges 1..3, clamped with + and - keys when not editing path."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 0  # Not editing path

    assert form.agent_count == 1
    # Decrement at min: stays 1
    _dispatch_key(form, ord("-"))
    assert form.agent_count == 1

    # Increment
    _dispatch_key(form, ord("+"))
    assert form.agent_count == 2

    _dispatch_key(form, ord("+"))
    assert form.agent_count == 3

    # Increment at max: stays 3
    _dispatch_key(form, ord("+"))
    assert form.agent_count == 3

    _dispatch_key(form, ord("-"))
    assert form.agent_count == 2


def test_agent_stepper_renders_persistent_chips() -> None:
    """Agent count stepper renders persistent [- agent] and [+ agent] on top + 2."""
    workshop = _load()
    window = RecordingWindow()
    form = workshop.Workshop(window, mode="launcher")
    form.draw_launcher()

    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert any("[- agent]" in t for t in drawn_texts)
    assert any("[+ agent]" in t for t in drawn_texts)
    assert any("Agents: 1/3" in t for t in drawn_texts)


# ─── Acceptance Criteria #9 & #10 (Finding 1000013, 22): OMP Gating & Stale Result Invalidation ───


def test_omp_agent_interactive_unavailability_and_cycling() -> None:
    """omp agent is unavailable for interactive launch and skipped by arrow navigation."""
    workshop = _load()
    # Check that omp is in AGENTS
    assert "omp" in workshop.AGENTS
    omp_idx = workshop.AGENTS.index("omp")

    # In interactive capability check, omp is unavailable
    assert workshop._provider_available("omp") is False
    assert "headless" in workshop._provider_unavailable_reason("omp")

    # Left/Right arrow navigation skips unavailable agents
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.row = 0  # Agent row
    # Start at agent before omp (or after omp)
    form.agent = (omp_idx - 1) % len(workshop.AGENTS)
    # Moving right should skip omp if omp is unavailable
    form._cycle_agent(1)
    assert form.agent != omp_idx


def test_stale_launch_failure_invalidation_on_config_change(tmp_path: Path) -> None:
    """Changing agent or path immediately clears stale failed launch result."""
    workshop = _load()
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.path = str(tmp_path / "p1")
    form.agent = workshop.AGENTS.index("codex")

    # Simulate a failed launch result recorded with old request_key
    old_key = form._slot_request_key(form.agent_slots[0], form._path)
    form.launch_results = [
        {
            "status": "failed",
            "reason": "Previous launch failed",
            "provider": "codex",
            "workspace": form._path,
            "request_key": old_key,
        }
    ]
    form.error = "Previous launch failed"
    form.error_stage = "Workspace preparation"

    # User changes configuration: changes agent
    form.row = 0
    form._cycle_agent(1)
    # Stale error and failed result should be invalidated
    assert form.error == ""
    assert form.error_stage == ""
    assert form.launch_results == []


# ─── Acceptance Criteria #11: ZenEntry Onboarding & Typography Scene ───


def test_zenentry_onboarding_scene_step_navigation_and_cancel(tmp_path: Path) -> None:
    """Onboarding supports 2-step navigation, font selection, and cancel without false completion."""
    workshop = _load()
    policy = tmp_path / "terminal-policy.toml"
    font_dir = tmp_path / "Fonts"
    font_dir.mkdir()
    xenon_file = font_dir / "MonaspaceXenonNF.ttf"
    xenon_file.write_bytes(b"\x00\x01\x00\x00")

    vc_home = tmp_path / "vc-home"
    vc_home.mkdir()

    window = RecordingWindow()
    form = workshop.Workshop(
        window,
        mode="launcher",
        onboarding=True,
        onboarding_policy_path=policy,
        onboarding_font_dirs=[font_dir],
        onboarding_home=vc_home,
    )

    assert form.onboarding_active is True
    assert form.onboarding_step == 1

    # In Step 1, pressing Tab / Right advances to Step 2 (Typography)
    _dispatch_key(form, 9)  # Tab
    assert form.onboarding_step == 2

    # In Step 2, glyph sample preview is drawn
    form.draw_launcher()
    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert any("0O1lI" in t for t in drawn_texts)

    # Esc steps back to Step 1
    _dispatch_key(form, 27)
    assert form.onboarding_step == 1

    # Esc again closes onboarding
    _dispatch_key(form, 27)
    assert form.onboarding_active is False

    # Durable completion was NOT falsely recorded
    assert cbo.status(home=vc_home).state == "pending"


# ─── Acceptance Criteria #16: Stage Progress, Partial Failure & Launch Parity ───


def test_launch_stage_progress_and_enter_mouse_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies per-stage progress, stage error attribution, and Enter/mouse launch parity."""
    workshop = _load()
    window = RecordingWindow(height=30, width=100)
    repo = tmp_path / "test-repo"
    repo.mkdir()

    form = workshop.Workshop(window, mode="launcher")
    form.path = str(repo)

    # 1. Verify Enter and mouse launch target parity on _attempt_launch
    # When path suggestions are open, attempting launch closes suggestions
    form.path_suggestions_open = True
    attempt_calls: list[str] = []
    monkeypatch.setattr(form, "launch", lambda: attempt_calls.append("launched"))

    # Keyboard Enter
    _dispatch_key(form, 10)
    assert form.path_suggestions_open is False
    assert attempt_calls == ["launched"]

    # Mouse click on [ Launch ]
    form.path_suggestions_open = True
    form.draw_launcher()
    launch_target = next(t for t in form.mouse_targets if t[4] == "launch")
    l_row, l_start, _end, _, _ = launch_target
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, l_start, l_row, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    form.handle_mouse()
    assert form.path_suggestions_open is False
    assert attempt_calls == ["launched", "launched"]

    # 2. Verify per-stage progress and partial launch failure state
    monkeypatch.delattr(form, "launch")

    form.select_slot(0)
    form.agent = workshop.AGENTS.index("codex")
    form.agent_slots[0] = form._snapshot()
    form.add_agent()
    form.select_slot(1)
    form.agent = workshop.AGENTS.index("claude")
    form.agent_slots[1] = form._snapshot()
    assert len(form.agent_slots) == 2

    calls = 0

    def mock_launch_one() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            form._opened = True
            form.launch_stage = "Opening project workspace…"
            form.progress_stages.append("Opening project workspace…")
        else:
            form._opened = False
            form.error_stage = "Opening claude tab"
            form.error = "connection refused on terminal socket"
            form.launch_stage = "Opening claude tab…"
            form.progress_stages.append("Opening claude tab…")

    monkeypatch.setattr(form, "_launch_one", mock_launch_one)
    form.launch()

    # Per-stage progress tracked on Workshop
    assert "Validating configuration…" in form.progress_stages
    assert "Opening project workspace…" in form.progress_stages
    assert "Opening claude tab…" in form.progress_stages
    assert form.error_stage == "Opening claude tab"
    assert "connection refused" in form.error

    # Partial launch results state
    assert len(form.launch_results) == 2
    assert form.launch_results[0]["status"] == "opened"
    assert form.launch_results[1]["status"] == "failed"
    assert form.launch_results[1]["request_key"] is not None
    assert form.mode == "launcher"

    # Draw launcher and verify stage attribution and partial results are rendered
    window.erase()
    form.draw_launcher()
    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert any("[Opening claude tab]" in t for t in drawn_texts)
    assert any("1 codex: opened" in t for t in drawn_texts)
    assert any("2 claude: failed" in t for t in drawn_texts)


# ─── Counterexample Unit Tests ───


class InputFinished(Exception):
    pass


class GetchWindow(RecordingWindow):
    def __init__(
        self,
        keys: bytes | tuple[int, ...] | list[int] = (),
        height: int = 30,
        width: int = 100,
    ) -> None:
        super().__init__(height=height, width=width)
        self.keys = iter(keys)

    def getch(self) -> int:
        try:
            return next(self.keys)
        except StopIteration:
            raise InputFinished


def test_workshop_form_utf8_getch_input_via_run_loop() -> None:
    """UTF-8 multi-byte characters (e.g. 'zażółć') are decoded and preserved across form.run() getch loop."""
    workshop = _load()
    typed = "zażółć"
    window = GetchWindow(typed.encode("utf-8"))
    form = workshop.Workshop(window, mode="launcher")
    form.path = ""
    form.row = 1
    form.configure = lambda: None
    form.draw = lambda: None
    try:
        form.run()
    except InputFinished:
        pass
    assert form.path == typed


def test_workshop_short_error_recovery_buttons_and_mouse_targets_match_drawn_cells() -> (
    None
):
    """Short errors draw [ Retry (r) ], [ Back (Esc) ], [ Details (e) ] and mouse_targets match drawn cells."""
    workshop = _load()
    window = RecordingWindow(30, 100)
    form = workshop.Workshop(window, mode="launcher")
    form.error = "Oops"
    form.error_stage = "Validation"
    form.draw_launcher()

    all_drawn = "\n".join(text for _, _, text, _ in window.drawn)
    assert "[ Retry (r) ]" in all_drawn
    assert "[ Back (Esc) ]" in all_drawn
    assert "[ Details (e) ]" in all_drawn
    assert "[Validation] Oops" in all_drawn

    action_targets = [
        t
        for t in form.mouse_targets
        if t[4] in ("retry", "dismiss_error", "error_details")
    ]
    assert len(action_targets) == 3
    for row, start, _end, _, _kind in action_targets:
        assert any(r == row and c == start for r, c, _t, _ in window.drawn)


def test_workshop_narrow_window_clips_mouse_targets_without_phantoms() -> None:
    """In a narrow window, recovery buttons are clipped by _choice_layout with zero phantom mouse targets."""
    workshop = _load()
    narrow_window = RecordingWindow(30, 25)
    form = workshop.Workshop(narrow_window, mode="launcher")
    form.error = "Oops"
    form.error_stage = "Validation"
    form.draw_launcher()

    action_targets = [
        t
        for t in form.mouse_targets
        if t[4] in ("retry", "dismiss_error", "error_details")
    ]
    assert len(action_targets) >= 1
    # All registered targets must fit within the window width (0 <= start < end <= 25)
    for row, start, end, _, _kind in action_targets:
        assert 0 <= start < end <= 25
        assert any(r == row and c == start for r, c, _t, _ in narrow_window.drawn)


class GridWindow:
    """A bounds-checking 2D cell grid fake window.

    Out-of-bounds writes are ignored and later addstr calls overwrite
    earlier cells at the same (row, col).
    """

    def __init__(self, height: int = 30, width: int = 100) -> None:
        self.height = height
        self.width = width
        self.grid: list[list[str]] = [
            [" " for _ in range(width)] for _ in range(height)
        ]
        self.drawn: list[tuple[int, int, str, int]] = []

    def getmaxyx(self) -> tuple[int, int]:
        return (self.height, self.width)

    def erase(self) -> None:
        self.grid = [[" " for _ in range(self.width)] for _ in range(self.height)]
        self.drawn.clear()

    def refresh(self) -> None:
        pass

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.drawn.append((y, x, text, attr))
        if 0 <= y < self.height:
            for i, ch in enumerate(text):
                col = x + i
                if 0 <= col < self.width:
                    self.grid[y][col] = ch

    def get_row(self, y: int) -> str:
        if 0 <= y < self.height:
            return "".join(self.grid[y])
        return ""


class WchWindow(RecordingWindow):
    def __init__(
        self,
        items: list[str | int],
        height: int = 30,
        width: int = 100,
    ) -> None:
        super().__init__(height=height, width=width)
        self.items = iter(items)

    def get_wch(self) -> str | int:
        try:
            return next(self.items)
        except StopIteration:
            raise InputFinished


def test_workshop_error_recovery_grid_window_no_overwrite_and_exact_cell_targets() -> (
    None
):
    """GridWindow proves Summary and [ Retry (r) ] [ Back (Esc) ] [ Details (e) ] do not overwrite each other."""
    workshop = _load()
    window = GridWindow(30, 100)
    form = workshop.Workshop(window, mode="launcher")
    form.error = "Oops"
    form.error_stage = "Validation"
    form.draw_launcher()

    err_row = 30 - 3  # 27
    act_row = 30 - 1  # 29

    err_line = window.get_row(err_row)
    act_line = window.get_row(act_row)

    # Summary row has error and stage, but zero action button text
    assert "[Validation] Oops" in err_line
    assert "[ Retry (r) ]" not in err_line
    assert "[ Back (Esc) ]" not in err_line
    assert "[ Details (e) ]" not in err_line

    # Action row has all three buttons, and zero error/stage text
    assert "[ Retry (r) ]" in act_line
    assert "[ Back (Esc) ]" in act_line
    assert "[ Details (e) ]" in act_line
    assert "Oops" not in act_line
    assert "Validation" not in act_line

    # Mouse targets match the exact final visible cells in the 2D grid
    action_targets = [
        t
        for t in form.mouse_targets
        if t[4] in ("retry", "dismiss_error", "error_details")
    ]
    assert len(action_targets) == 3
    for row, start, end, _, kind in action_targets:
        assert row == act_row
        cell_slice = "".join(window.grid[row][start:end])
        expected_label = {
            "retry": "[ Retry (r) ]",
            "dismiss_error": "[ Back (Esc) ]",
            "error_details": "[ Details (e) ]",
        }[kind]
        assert cell_slice == expected_label


def test_workshop_interrupted_utf8_lead_byte_resets_buffer_and_preserves_next_key() -> (
    None
):
    """Interrupted UTF-8 lead byte clears _utf8_buf and does not swallow the next ASCII character or Enter key."""
    workshop = _load()
    window = RecordingWindow(30, 100)
    form = workshop.Workshop(window, mode="launcher")
    form.path = ""
    form.row = 1  # editing_path

    # Step 1: Send incomplete lead byte (195). Should be buffered.
    form.handle_launcher_key(195)
    assert len(form._utf8_buf) == 1
    assert form.path == ""

    # Step 2: Send ASCII 'a' (97). Non-continuation byte must clear buffer and insert 'a'.
    form.handle_launcher_key(ord("a"))
    assert len(form._utf8_buf) == 0
    assert form.path == "a"

    # Step 3: Send another interrupted lead byte (195), followed by Enter (10).
    form.handle_launcher_key(195)
    assert len(form._utf8_buf) == 1

    launched = False

    def mock_launch() -> None:
        nonlocal launched
        launched = True

    form._attempt_launch = mock_launch
    form.handle_launcher_key(10)
    assert len(form._utf8_buf) == 0
    assert launched is True


def test_workshop_get_wch_dispatch_in_run_loop() -> None:
    """form.run() handles get_wch returning unicode strings directly without colliding with KEY_RIGHT."""
    workshop = _load()
    typed = "zażółć"
    window = WchWindow(list(typed))
    form = workshop.Workshop(window, mode="launcher")
    form.path = ""
    form.row = 1
    form.configure = lambda: None
    form.draw = lambda: None
    try:
        form.run()
    except InputFinished:
        pass
    assert form.path == typed

    # Ensure 'ą' (ord 261 == curses.KEY_RIGHT) on row 0 (Agent) does not step agent index
    window_non_typing = WchWindow(["ą"])
    form_non_typing = workshop.Workshop(window_non_typing, mode="launcher")
    form_non_typing.row = 0
    initial_agent = form_non_typing.agent
    form_non_typing.configure = lambda: None
    form_non_typing.draw = lambda: None
    try:
        form_non_typing.run()
    except InputFinished:
        pass
    assert form_non_typing.agent == initial_agent
