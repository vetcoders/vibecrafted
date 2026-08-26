from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from vibecrafted_core.vc_frame_staging import materialize_vc_frame_config

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "vc-start-here.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vc_start_here", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_here_is_shipped_and_keeps_executable_mode(tmp_path: Path) -> None:
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111

    destination = tmp_path / "vc-frame"
    materialize_vc_frame_config(
        SCRIPT.parent,
        destination,
        pane_shell="bash",
        clipboard_command=None,
    )

    installed = destination / SCRIPT.name
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111


def test_start_here_routes_to_existing_product_owners() -> None:
    start_here = _load()

    assert start_here.action_argv("agents") == [
        "vc-frame",
        "action",
        "go-to-tab-name",
        "Agents",
    ]
    assert start_here.action_argv("shell") == [
        "vc-frame",
        "action",
        "go-to-tab-name",
        "Shell",
    ]
    assert start_here.action_argv("console") == [
        "/usr/bin/open",
        "vibecrafted://console/open",
    ]
    assert start_here.action_argv("help")[:5] == [
        "vc-frame",
        "action",
        "new-pane",
        "--floating",
        "--name",
    ]


def test_start_here_readiness_is_truthful_and_actionable() -> None:
    start_here = _load()
    healthy = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": True,
    }

    assert start_here.readiness_from_service_payload(healthy) == (
        "ready",
        "VC Server is healthy — this workspace is ready",
    )
    assert start_here.readiness_from_service_payload(
        {"installed": True, "loaded": False}
    ) == (
        "stopped",
        "VC Server is stopped — use the Vibecrafted menu bar to start it",
    )
    assert start_here.readiness_from_service_payload(None, deck_available=False) == (
        "missing",
        "Vibecrafted launcher is missing — reinstall the Runtime Pack",
    )


def test_start_here_mouse_targets_the_same_actions_as_keyboard() -> None:
    start_here = _load()
    targets = [(10, 4, 28, "agents"), (13, 4, 28, "shell")]

    assert start_here.action_for_mouse_row(10, targets, 9) == "agents"
    assert start_here.action_for_mouse_row(13, targets, 28) == "shell"
    assert start_here.action_for_mouse_row(12, targets, 9) is None
