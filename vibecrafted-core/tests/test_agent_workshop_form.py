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
    form.path = ""  # Exercise literal insertion independently of project discovery.
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


def _runtime_caps(
    available: bool, reason: str = "provider is not installed"
) -> dict[str, dict[str, object]]:
    return {
        name: {"available": available, "reason": "" if available else reason}
        for name in ("local-native", "local-worktrees", "local-vm", "cloud-soon")
    }


def test_omp_agent_interactive_unavailability_and_cycling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """omp is selectable when installed; unavailable providers are skipped and refused."""
    workshop = _load()
    assert "omp" in workshop.AGENTS
    omp_idx = workshop.AGENTS.index("omp")
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def installed_caps(agent: str) -> dict[str, dict[str, object]]:
        return _runtime_caps(True)

    monkeypatch.setattr(workshop, "runtime_policy_capabilities", installed_caps)
    assert workshop._provider_available("omp") is True

    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.save_choices = lambda: None
    form._load_choices = lambda: None
    form.row = 0
    form.agent = (omp_idx - 1) % len(workshop.AGENTS)
    form._cycle_agent(1)
    assert form.agent == omp_idx

    form.draw_launcher()
    target = next(
        t for t in form.mouse_targets if t[4] == "provider" and t[3] == omp_idx
    )
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, target[1], target[0], 0, workshop.curses.BUTTON1_CLICKED),
    )
    form.handle_mouse()
    assert form.agent == omp_idx

    init_argv = workshop.launch_argv(
        "omp",
        "init",
        "local-native",
        "auto",
        workspace=tmp_path,
    )
    assert init_argv[:5] == ["vibecrafted", "init", "omp", "--runtime", "plain"]
    resume_argv = workshop.launch_argv(
        "omp",
        "resume",
        "local-native",
        "auto",
        workspace=tmp_path,
        session="omp-native-sid-1",
    )
    assert resume_argv[:5] == ["vibecrafted", "resume", "omp", "--runtime", "plain"]
    assert "--session" in resume_argv
    assert resume_argv[resume_argv.index("--session") + 1] == "omp-native-sid-1"

    unavailable = "copilot"

    def missing_caps(agent: str) -> dict[str, dict[str, object]]:
        return _runtime_caps(agent != unavailable)

    monkeypatch.setattr(workshop, "runtime_policy_capabilities", missing_caps)
    assert workshop._provider_available(unavailable) is False
    missing_idx = workshop.AGENTS.index(unavailable)
    form.error = ""
    form.agent = (missing_idx - 1) % len(workshop.AGENTS)
    form._cycle_agent(1)
    assert form.agent != missing_idx

    form.draw_launcher()
    missing_target = next(
        t for t in form.mouse_targets if t[4] == "provider" and t[3] == missing_idx
    )
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (
            0,
            missing_target[1],
            missing_target[0],
            0,
            workshop.curses.BUTTON1_CLICKED,
        ),
    )
    form.handle_mouse()
    assert form.agent != missing_idx
    assert form.error

    form.agent = missing_idx
    form.path = str(tmp_path)
    form.error = ""
    form._launch_one()
    assert form.error


def test_stale_launch_failure_invalidation_on_config_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # Provider discovery is separate; make a selectable replacement available.
    monkeypatch.setattr(workshop, "_provider_available", lambda _agent: True)
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


def _mock_monaspace_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cbo.sys, "platform", "darwin")
    monkeypatch.setattr(
        cbo,
        "_native_font_faces",
        lambda: [
            cbo._FontFace(family, style, (1 << 10) | traits)
            for family in cbo.MONASPACE_FAMILIES
            for style, traits in (("Regular", 0), ("Bold", 2), ("Italic", 1))
        ],
    )


def test_onboarding_font_apply_malformed_toml_error_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed terminal-policy.toml raises ValueError, caught gracefully across key/mouse/run paths, preserving file and recovering cleanly."""
    workshop = _load()
    _mock_monaspace_metadata(monkeypatch)
    vc_home = tmp_path / "vc-home"
    vc_home.mkdir()

    policy_path = tmp_path / "terminal-policy.toml"
    malformed_bytes = b'[font.normal\nfamily = "Monaspace Neon"\n'
    policy_path.write_bytes(malformed_bytes)

    # 1. Launcher key path: handle_launcher_key(10)
    window = RecordingWindow()
    form = workshop.Workshop(
        window,
        mode="launcher",
        onboarding=True,
        onboarding_policy_path=policy_path,
        onboarding_home=vc_home,
    )
    assert form.onboarding_active is True
    # Navigate to Step 2
    form.handle_launcher_key(ord("s"))
    assert form.onboarding_step == 2

    # Select Neon variant
    neon_idx = next(
        i for i, v in enumerate(form.onboarding_variants) if "Neon" in v.family
    )
    form.onboarding_font_idx = neon_idx

    # Press Enter via public handle_launcher_key(10)
    form.handle_launcher_key(10)
    assert bool(form.error) is True
    assert "malformed" in form.error.lower()
    assert form.onboarding_active is True
    assert form.onboarding_step == 2
    assert policy_path.read_bytes() == malformed_bytes

    # 2. Home key path: handle_home_key(10)
    window_home = RecordingWindow()
    form_home = workshop.Workshop(
        window_home,
        mode="home",
        onboarding=True,
        onboarding_policy_path=policy_path,
        onboarding_home=vc_home,
    )
    form_home.handle_home_key(ord("s"))
    assert form_home.onboarding_step == 2
    form_home.onboarding_font_idx = neon_idx
    form_home.handle_home_key(10)
    assert bool(form_home.error) is True
    assert "malformed" in form_home.error.lower()
    assert form_home.onboarding_active is True
    assert form_home.onboarding_step == 2
    assert policy_path.read_bytes() == malformed_bytes

    # 3. Mouse path: handle_mouse() on onboarding_font and onboarding_apply_font
    form.draw()
    # Click on onboarding_font for neon_idx
    font_target = next(
        t for t in form.mouse_targets if t[4] == "onboarding_font" and t[3] == neon_idx
    )
    f_row, f_start, _, _, _ = font_target
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, f_start, f_row, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    form.handle_mouse()
    assert bool(form.error) is True
    assert "malformed" in form.error.lower()
    assert form.onboarding_active is True
    assert policy_path.read_bytes() == malformed_bytes

    # Click on onboarding_apply_font
    apply_target = next(
        t for t in form.mouse_targets if t[4] == "onboarding_apply_font"
    )
    a_row, a_start, _, _, _ = apply_target
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, a_start, a_row, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    form.handle_mouse()
    assert bool(form.error) is True
    assert "malformed" in form.error.lower()
    assert form.onboarding_active is True
    assert policy_path.read_bytes() == malformed_bytes

    # 4. Run loop dispatch: workshop.run() with Enter key
    window_run = GetchWindow([10])
    form_run = workshop.Workshop(
        window_run,
        mode="launcher",
        onboarding=True,
        onboarding_policy_path=policy_path,
        onboarding_home=vc_home,
    )
    form_run.onboarding_step = 2
    form_run.onboarding_font_idx = neon_idx
    form_run.configure = lambda: None
    try:
        form_run.run()
    except InputFinished:
        pass
    assert bool(form_run.error) is True
    assert "malformed" in form_run.error.lower()
    assert form_run.onboarding_active is True
    assert policy_path.read_bytes() == malformed_bytes

    # 5. Recovery without exiting the app: repair policy_path with valid TOML
    policy_path.write_text(
        '[font.normal]\nfamily = "Monaspace Xenon NF"\n', encoding="utf-8"
    )
    form.handle_launcher_key(10)
    assert form.error == ""
    assert form.onboarding_active is True
    content = policy_path.read_text(encoding="utf-8")
    assert "[font.normal]" in content
    assert "[font.bold]" in content
    assert "[font.italic]" in content
    assert '"Monaspace Neon NF"' in content


def test_onboarding_font_apply_oserror_and_recovery_or_dismiss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-writable policy seam raises OSError, caught gracefully across key/mouse/run paths, preserving file and recovering cleanly."""
    workshop = _load()
    _mock_monaspace_metadata(monkeypatch)
    vc_home = tmp_path / "vc-home"
    vc_home.mkdir()

    sub_dir = tmp_path / "protected"
    sub_dir.mkdir()
    policy_path = sub_dir / "terminal-policy.toml"
    valid_initial_toml = '[font.normal]\nfamily = "Monaspace Argon NF"\n'
    policy_path.write_text(valid_initial_toml, encoding="utf-8")
    initial_bytes = policy_path.read_bytes()

    # Make parent dir non-writable so atomic tempfile creation fails with OSError (PermissionError)
    sub_dir.chmod(0o555)
    try:
        # 1. Trigger via public handle_launcher_key(10)
        window = RecordingWindow()
        form = workshop.Workshop(
            window,
            mode="launcher",
            onboarding=True,
            onboarding_policy_path=policy_path,
            onboarding_home=vc_home,
        )
        assert form.onboarding_active is True
        form.handle_launcher_key(ord("s"))
        assert form.onboarding_step == 2

        neon_idx = next(
            i for i, v in enumerate(form.onboarding_variants) if "Neon" in v.family
        )
        form.onboarding_font_idx = neon_idx

        form.handle_launcher_key(10)
        assert bool(form.error) is True
        assert form.onboarding_active is True
        assert form.onboarding_step == 2
        assert policy_path.read_bytes() == initial_bytes

        # 2. Trigger via public handle_home_key(10)
        window_home = RecordingWindow()
        form_home = workshop.Workshop(
            window_home,
            mode="home",
            onboarding=True,
            onboarding_policy_path=policy_path,
            onboarding_home=vc_home,
        )
        form_home.handle_home_key(ord("s"))
        form_home.onboarding_font_idx = neon_idx
        form_home.handle_home_key(10)
        assert bool(form_home.error) is True
        assert form_home.onboarding_active is True
        assert policy_path.read_bytes() == initial_bytes

        # 3. Trigger via public handle_mouse() on onboarding_apply_font
        form.draw()
        apply_target = next(
            t for t in form.mouse_targets if t[4] == "onboarding_apply_font"
        )
        a_row, a_start, _, _, _ = apply_target
        monkeypatch.setattr(
            workshop.curses,
            "getmouse",
            lambda: (0, a_start, a_row, 0, workshop.curses.BUTTON1_CLICKED),
            raising=False,
        )
        form.handle_mouse()
        assert bool(form.error) is True
        assert form.onboarding_active is True
        assert policy_path.read_bytes() == initial_bytes

        # 4. Trigger via workshop.run() loop
        window_run = GetchWindow([10])
        form_run = workshop.Workshop(
            window_run,
            mode="launcher",
            onboarding=True,
            onboarding_policy_path=policy_path,
            onboarding_home=vc_home,
        )
        form_run.onboarding_step = 2
        form_run.onboarding_font_idx = neon_idx
        form_run.configure = lambda: None
        try:
            form_run.run()
        except InputFinished:
            pass
        assert bool(form_run.error) is True
        assert form_run.onboarding_active is True
        assert policy_path.read_bytes() == initial_bytes

        # 5. Recovery: Restore write permissions
        sub_dir.chmod(0o755)

        # Apply via mouse click on onboarding_apply_font
        form.draw()
        apply_target = next(
            t for t in form.mouse_targets if t[4] == "onboarding_apply_font"
        )
        a_row, a_start, _, _, _ = apply_target
        monkeypatch.setattr(
            workshop.curses,
            "getmouse",
            lambda: (0, a_start, a_row, 0, workshop.curses.BUTTON1_CLICKED),
            raising=False,
        )
        form.handle_mouse()
        assert form.error == ""
        assert form.onboarding_active is True
        content = policy_path.read_text(encoding="utf-8")
        assert "[font.normal]" in content
        assert "[font.bold]" in content
        assert "[font.italic]" in content
        assert '"Monaspace Neon NF"' in content
    finally:
        sub_dir.chmod(0o755)


def test_onboarding_close_clears_error_without_leak_to_launcher_or_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Esc or clicking onboarding_close clears prior error so it does not leak to home/launcher."""
    workshop = _load()
    _mock_monaspace_metadata(monkeypatch)
    vc_home = tmp_path / "vc-home"
    vc_home.mkdir()

    policy_path = tmp_path / "terminal-policy.toml"
    policy_path.write_bytes(b'[font.normal\nfamily = "Monaspace Neon"\n')

    # Launcher mode
    window = RecordingWindow()
    form = workshop.Workshop(
        window,
        mode="launcher",
        onboarding=True,
        onboarding_policy_path=policy_path,
        onboarding_home=vc_home,
    )
    form.handle_launcher_key(ord("s"))  # Step 2
    form.handle_launcher_key(10)  # Fails with malformed TOML
    assert bool(form.error) is True
    assert form.onboarding_active is True

    # Click onboarding_close via handle_mouse
    form.draw()
    close_target = next(t for t in form.mouse_targets if t[4] == "onboarding_close")
    c_row, c_start, _, _, _ = close_target
    monkeypatch.setattr(
        workshop.curses,
        "getmouse",
        lambda: (0, c_start, c_row, 0, workshop.curses.BUTTON1_CLICKED),
        raising=False,
    )
    form.handle_mouse()
    assert form.onboarding_active is False
    assert form.error == ""

    # Drawing launcher has NO leaked error
    window.erase()
    form.draw()
    drawn_texts = [text for _, _, text, _ in window.drawn]
    assert not any("malformed" in t.lower() for t in drawn_texts)

    # Home mode: test Esc closing
    window_home = RecordingWindow()
    form_home = workshop.Workshop(
        window_home,
        mode="home",
        onboarding=True,
        onboarding_policy_path=policy_path,
        onboarding_home=vc_home,
    )
    form_home.handle_home_key(ord("s"))  # Step 2
    form_home.handle_home_key(10)  # Error set
    assert bool(form_home.error) is True

    # Esc once in Step 2 returns to Step 1 and clears error
    form_home.handle_home_key(27)
    assert form_home.onboarding_step == 1
    assert form_home.error == ""
    assert form_home.onboarding_active is True

    # Esc again closes onboarding
    form_home.handle_home_key(27)
    assert form_home.onboarding_active is False
    assert form_home.error == ""


def test_onboarding_confirm_provider_oserror_and_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workshop._onboarding_confirm_provider catches OSError and ValueError and displays error without crashing."""
    workshop = _load()
    vc_home = tmp_path / "vc-home"
    vc_home.mkdir()

    window = RecordingWindow()
    form = workshop.Workshop(
        window,
        mode="launcher",
        onboarding=True,
        onboarding_home=vc_home,
    )
    assert form.onboarding_step == 1

    # Mock probe_provider raising ValueError
    monkeypatch.setattr(
        cbo,
        "probe_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("provider config malformed")
        ),
    )
    form.handle_launcher_key(10)
    assert form.error == "provider config malformed"
    assert form.notice == ""
    assert form.onboarding_step == 1
    assert form.onboarding_active is True

    # Mock probe_provider raising OSError
    monkeypatch.setattr(
        cbo,
        "probe_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("auth path inaccessible")
        ),
    )
    form.handle_launcher_key(10)
    assert form.error == "auth path inaccessible"
    assert form.notice == ""
    assert form.onboarding_step == 1
    assert form.onboarding_active is True


def test_font_direct_entry_skips_provider_and_preserves_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    policy = tmp_path / "policy.toml"
    original = '[font.normal]\nfamily = "Proportional Custom"\n'
    policy.write_text(original)
    monkeypatch.setattr(
        cbo, "configured_providers", lambda: pytest.fail("provider probe")
    )
    monkeypatch.setattr(cbo, "begin", lambda **kwargs: pytest.fail("onboarding marker"))
    monkeypatch.setattr(cbo, "inspect_font_variants", lambda **kwargs: [])
    args = workshop.parse_args(["launcher", "--font"])
    form = workshop.Workshop(
        RecordingWindow(), mode=args.mode, font=args.font, onboarding_policy_path=policy
    )
    assert form.onboarding_active and form.onboarding_step == 2
    assert form.configured_font_family == "Proportional Custom"
    assert not cbo.in_progress_path(tmp_path).exists()
    form.handle_onboarding_key(10)
    assert form.onboarding_active and policy.read_text() == original
    with pytest.raises(SystemExit) as closed:
        form.handle_onboarding_key(27)
    assert closed.value.code == 0 and policy.read_text() == original
    assert workshop.parse_args(["launcher", "--onboarding"]).onboarding
    for argv in (
        ["--font"],
        ["home", "--font"],
        ["launcher", "--font", "--onboarding"],
    ):
        with pytest.raises(SystemExit) as invalid:
            workshop.parse_args(argv)
        assert invalid.value.code == 2


@pytest.mark.parametrize("height", [14, 24, 40])
def test_font_viewport_keyboard_mouse_and_resize(
    height: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    variants = [
        cbo.FontVariantStatus(
            f"Family {idx:03}", f"Family {idx:03}", "Fixed width", True
        )
        for idx in range(120)
    ]
    monkeypatch.setattr(cbo, "inspect_font_variants", lambda **kwargs: variants)
    window = RecordingWindow(height=height)
    policy = tmp_path / "policy.toml"
    form = workshop.Workshop(
        window, mode="launcher", font=True, onboarding_policy_path=policy
    )
    form.draw()
    targets = [t for t in form.mouse_targets if t[4] == "onboarding_font"]
    capacity = len(targets)
    assert 0 < capacity < len(variants)
    assert all(0 <= row < height - 5 for row, *_ in targets)
    form.handle_onboarding_key(curses.KEY_NPAGE)
    form.draw()
    assert form.onboarding_font_idx == capacity
    targets = [t for t in form.mouse_targets if t[4] == "onboarding_font"]
    assert any(t[3] == capacity for t in targets)
    row, start, _, index, _ = targets[-1]
    monkeypatch.setattr(
        workshop.curses, "getmouse", lambda: (0, start, row, 0, curses.BUTTON1_CLICKED)
    )
    form.handle_mouse()
    assert form.onboarding_font_idx == index and not policy.exists()
    monkeypatch.setattr(
        workshop.curses, "getmouse", lambda: (0, start, row, 0, curses.BUTTON4_PRESSED)
    )
    form.handle_mouse()
    assert form.onboarding_font_idx == index - 1
    form.handle_onboarding_key(curses.KEY_END)
    window.height = 16
    form.draw()
    targets = [t for t in form.mouse_targets if t[4] == "onboarding_font"]
    assert targets[-1][3] == len(variants) - 1
    assert all(t[0] < 16 - 5 for t in targets)
    form.handle_onboarding_key(curses.KEY_HOME)
    form.draw()
    assert form.onboarding_font_scroll == 0
    assert not policy.exists()


@pytest.mark.parametrize("font_only", [False, True])
def test_font_apply_stays_open_for_repeated_selection_and_close(
    font_only: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc-home"))
    policy = tmp_path / "terminal-policy.toml"
    policy.write_text('[font.normal]\nfamily = "Family 000"\n')
    families = [f"Family {index:03}" for index in range(30)]

    def catalogue(**kwargs: Any) -> list[Any]:
        active = cbo.read_configured_font_family(policy)
        return [
            cbo.FontVariantStatus(
                family, family, "Fixed width", True, active=family == active
            )
            for family in families
        ]

    monkeypatch.setattr(cbo, "inspect_font_variants", catalogue)
    window = RecordingWindow(height=16)
    form = workshop.Workshop(
        window,
        mode="launcher",
        font=font_only,
        onboarding_policy_path=policy,
        onboarding_home=tmp_path / "onboarding",
    )
    if not font_only:
        form.open_onboarding(step=2)
    form.onboarding_font_idx = 15
    form.draw()
    initial_scroll = form.onboarding_font_scroll
    assert initial_scroll > 0
    form.handle_onboarding_key(10)
    assert form.onboarding_active and form.onboarding_step == 2
    assert (
        form.onboarding_font_idx == 15 and form.onboarding_font_scroll == initial_scroll
    )
    assert (
        form.configured_font_family
        == cbo.read_configured_font_family(policy)
        == families[15]
    )
    assert "Saved terminal font" in form.notice

    form.handle_onboarding_key(curses.KEY_DOWN)
    form.draw()
    second_scroll = form.onboarding_font_scroll
    form.handle_onboarding_key(10)
    assert form.onboarding_active and form.onboarding_step == 2
    assert (
        form.onboarding_font_idx == 16 and form.onboarding_font_scroll == second_scroll
    )
    assert (
        form.configured_font_family
        == cbo.read_configured_font_family(policy)
        == families[16]
    )

    before = policy.read_bytes()

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(cbo, "select_font_family", fail)
    form.handle_onboarding_key(10)
    assert form.onboarding_active and form.onboarding_font_idx == 16
    assert form.onboarding_font_scroll == second_scroll
    assert form.configured_font_family == families[16] and policy.read_bytes() == before
    assert "synthetic write failure" in form.error and not form.notice

    form.draw()
    close_target = next(
        target for target in form.mouse_targets if target[4] == "onboarding_close"
    )
    row, start, _, _, _ = close_target
    monkeypatch.setattr(
        workshop.curses, "getmouse", lambda: (0, start, row, 0, curses.BUTTON1_CLICKED)
    )
    if font_only:
        with pytest.raises(SystemExit) as closed:
            form.handle_mouse()
        assert closed.value.code == 0
    else:
        form.handle_mouse()
        assert not form.onboarding_active
    assert policy.read_bytes() == before


def test_font_apply_retains_selected_family_after_catalogue_reorders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc-home"))
    policy = tmp_path / "policy.toml"
    policy.write_text('[font.normal]\nfamily = "Alpha"\n')

    def catalogue(**kwargs: Any) -> list[Any]:
        active = cbo.read_configured_font_family(policy)
        names = (
            ["Alpha", "Beta", "Gamma"]
            if active == "Alpha"
            else ["Gamma", "Beta", "Alpha"]
        )
        return [
            cbo.FontVariantStatus(
                name, name, "Fixed width", True, active=name == active
            )
            for name in names
        ]

    monkeypatch.setattr(cbo, "inspect_font_variants", catalogue)
    form = workshop.Workshop(
        RecordingWindow(), mode="launcher", font=True, onboarding_policy_path=policy
    )
    form.onboarding_font_idx = 2
    form.handle_onboarding_key(10)
    assert form.onboarding_active
    assert form.onboarding_variants[form.onboarding_font_idx].family == "Gamma"
    assert cbo.read_configured_font_family(policy) == "Gamma"


def test_font_catalogue_error_keeps_current_and_can_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()

    def fail(**kwargs: Any) -> list[Any]:
        raise cbo.OnboardingError("native catalogue unavailable")

    monkeypatch.setattr(cbo, "inspect_font_variants", fail)
    policy = tmp_path / "policy.toml"
    policy.write_text('[font.normal]\nfamily = "Old"\n')
    form = workshop.Workshop(
        RecordingWindow(), mode="launcher", font=True, onboarding_policy_path=policy
    )
    assert form.configured_font_family == "Old"
    assert form.font_catalogue_note == "native catalogue unavailable"
    form.handle_onboarding_key(10)
    assert form.error and form.onboarding_active
    with pytest.raises(SystemExit):
        form.handle_onboarding_key(27)
    assert cbo.read_configured_font_family(policy) == "Old"


def test_real_macos_font_catalogue_and_offscreen_direct_chooser(tmp_path: Path) -> None:
    import fcntl
    import json
    import os
    import pty
    import select
    import struct
    import subprocess
    import sys
    import termios
    import time
    import tomllib

    if sys.platform != "darwin":
        pytest.skip("real CoreText acceptance requires macOS")
    variants = [
        v
        for v in cbo.inspect_font_variants(policy_path=tmp_path / "absent")
        if v.selectable
    ]
    if len(variants) < 2:
        pytest.skip(
            "paging acceptance requires at least two installed fixed-width families"
        )
    terminal_height = min(24, len(variants) + 10)
    page_size = terminal_height - 11
    assert any(v.family not in cbo.MONASPACE_FAMILIES for v in variants)
    assert any("mono" not in v.family.casefold() for v in variants)
    faces = cbo._native_font_faces()
    fixed = {face.family for face in faces if face.traits & (1 << 10)}
    assert all(v.family in fixed for v in variants)
    proportional = {face.family for face in faces} - fixed
    assert proportional and not proportional.intersection(v.family for v in variants)

    config = tmp_path / "config"
    policy = config / "vibecrafted" / "terminal-policy.toml"
    policy.parent.mkdir(parents=True)
    original = f"[font]\nsize = 16.5\n[font.normal]\nfamily = {json.dumps(variants[0].family)}\n"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_FRAME_", "ZELLIJ"))
        and key not in ("PYTHONPATH", "PYTHONHOME")
    }
    env.update(
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(config),
        XDG_DATA_HOME=str(tmp_path / "data"),
        VIBECRAFTED_HOME=str(tmp_path / "vc-home"),
        TERM="xterm-256color",
    )

    def run(steps: list[tuple[bytes, str | None]], name: str) -> None:
        before = policy.read_bytes()
        master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[0] &= ~(termios.IXON | termios.IXOFF)
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        fcntl.ioctl(
            slave, termios.TIOCSWINSZ, struct.pack("HHHH", terminal_height, 100, 0, 0)
        )
        child = subprocess.Popen(
            [sys.executable, str(SCRIPT), "launcher", "--font"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            start_new_session=True,
        )
        os.close(slave)
        output = bytearray()
        sent = 0
        deadline = time.monotonic() + 20
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                if not sent and b"Enter apply" in output:
                    assert policy.read_bytes() == before
                    os.write(master, steps[0][0])
                    sent = 1
                elif sent and sent < len(steps):
                    expected = steps[sent - 1][1]
                    if expected and cbo.read_configured_font_family(policy) == expected:
                        assert child.poll() is None, (
                            "picker exited immediately after apply"
                        )
                        os.write(master, steps[sent][0])
                        sent += 1
                if child.poll() is not None:
                    break
            assert sent == len(steps), output.decode(errors="replace")
            assert child.wait(timeout=3) == 0, output.decode(errors="replace")
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)
            os.close(master)
            (tmp_path / f"{name}.ansi").write_bytes(output)

    policy.write_text(original)
    run([(b"\x1b", None)], "cancel")
    assert policy.read_text() == original
    run(
        [
            (b"\x1b[6~\r", variants[page_size].family),
            (b"\x1b[5~\r", variants[0].family),
            (b"\x1b", None),
        ],
        "page-apply-twice-and-close",
    )
    result = tomllib.loads(policy.read_text())
    chosen = variants[0]
    assert result["font"]["size"] == 16.5
    assert [result["font"][key] for key in ("normal", "bold", "italic")] == [
        {"family": chosen.family, "style": style} for style in chosen.face_styles
    ]
    invalid = b'[font.normal]\nfamily = "Broken\xff"\n'
    policy.write_bytes(invalid)
    run([(b"\x1b", None)], "invalid-utf8-cancel")
    assert policy.read_bytes() == invalid
    assert not list((tmp_path / "vc-home").rglob("command_bridge_onboarding*"))
    receipt = {
        "eligible_families": len(variants),
        "families": [v.family for v in variants],
        "excluded_proportional_families": len(proportional),
        "selected_index": 0,
        "first_selected_index": page_size,
        "terminal_height": terminal_height,
        "selected_family": chosen.family,
        "selected_styles": chosen.face_styles,
        "cancel_preserved_bytes": True,
        "invalid_utf8_cancel_preserved_bytes": True,
        "policy": str(policy),
    }
    (tmp_path / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


@pytest.mark.parametrize("font_only", [False, True])
def test_invalid_utf8_policy_is_recoverable_and_cancellable(
    font_only: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workshop = _load()
    policy = tmp_path / "terminal-policy.toml"
    original = b'[font.normal]\nfamily = "Broken\xff"\n'
    policy.write_bytes(original)
    monkeypatch.setattr(
        cbo, "configured_providers", lambda: pytest.fail("provider probe")
    )
    form = workshop.Workshop(
        RecordingWindow(), mode="launcher", onboarding_policy_path=policy
    )
    form.configured_font_family = "stale previous selection"
    form.font_only = font_only
    form.open_onboarding(step=2)
    assert form.onboarding_active and form.onboarding_step == 2
    assert form.configured_font_family == ""
    assert not form.onboarding_variants and form.font_catalogue_note
    form.draw()
    assert any(form.font_catalogue_note in text for _, _, text, _ in form.window.drawn)
    form.handle_onboarding_key(10)
    assert form.error and policy.read_bytes() == original
    if font_only:
        with pytest.raises(SystemExit) as closed:
            form.handle_onboarding_key(27)
        assert closed.value.code == 0
    else:
        form.handle_onboarding_key(27)
        form.handle_onboarding_key(27)
        assert not form.onboarding_active
    assert policy.read_bytes() == original
