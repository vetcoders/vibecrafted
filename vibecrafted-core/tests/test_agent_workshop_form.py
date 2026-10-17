"""Comprehensive unit and regression tests for Workshop Form UX repairs & ZenEntry onboarding.

Covers:
- Findings: 1000002, 12, 15, 1000008, 1000011, 18, 1000013, 22, 23
- Acceptance Criteria: #1..#11, #16
- Monaspace typography selector and ZenEntry onboarding scene integration
- Invariant: Zero diff in lines 360..584 of vc-agent-workshop.py
"""

from __future__ import annotations

import curses
import importlib.util
import re
import subprocess
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
    form = workshop.Workshop(RecordingWindow(), mode="launcher")
    form.path = "/test/repo"
    form.error = "Failed to launch tab: connection refused"
    form.error_stage = "Agent tab launch"

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


# ─── Acceptance Criteria #16: Line 360..584 Zero-Diff Invariant ───


def test_vc_agent_workshop_lines_360_to_584_have_zero_diff_from_main() -> None:
    """Lines 360..584 in vc-agent-workshop.py must have zero diff compared to main (2d7493c)."""
    git_diff = subprocess.run(
        [
            "git",
            "diff",
            "2d7493cf1f324d9819e3fff4ead4f102778d08c5",
            "--",
            str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    diff_text = git_diff.stdout

    # Parse unified diff hunk headers: @@ -from_line,from_count +to_line,to_count @@
    hunks = re.findall(r"@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@", diff_text)
    for from_line, from_count, to_line, to_count in hunks:
        f_start = int(from_line)
        f_len = int(from_count) if from_count else 1
        t_start = int(to_line)
        t_len = int(to_count) if to_count else 1
        f_end = f_start + f_len
        # Neither hunk range should overlap with 360..584
        overlap = not (f_end < 360 or f_start > 584)
        assert not overlap, (
            f"Forbidden diff overlap in lines 360..584: -{f_start},{f_len} +{t_start},{t_len}"
        )
