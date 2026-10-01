"""Regression test for the 2026-10-01 dead-hook-path incident (f3-dead-hook-paths).

`~/.copilot/hooks/vibecrafted-fleet.json` (written 2026-09-28 by vc-hello
onboarding) invoked `python3 "$HOME/.agents/skills/vc-hello/tools/hook_bridge.py"
...` for sessionStart/preCompact/preToolUse. That file did not exist in any
generation, so every hook exited non-zero and the Copilot CLI harness treated
the failure as a hard deny for every bash call, overnight, unnoticed — because
`fleet_scan.py` only ever compared hook *families* (derived basenames), never
resolved a hook command to a real, checkable filesystem path.

This test exercises the fix: `fleet_scan.py` now extracts every
interpreter/script path out of a hook command line (including a script
wrapped by `hook_bridge.py` after its `--` separator) and flags any path that
does not exist as `consensus["hooks"]["status"] == "broken"`, with the CLI's
`check` subcommand exiting non-zero.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_SCAN = (
    REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/vc-hello/tools/fleet_scan.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_vc_hello_fleet_scan_module", FLEET_SCAN
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_copilot_hooks(home: Path, bridge_path: Path, wrapped_path: Path) -> None:
    """Write a fake `~/.copilot/hooks/vibecrafted-fleet.json`.

    Uses absolute paths under `home` (no `$HOME`/`~` tokens) so the test never
    depends on the real operator's home directory contents.
    """
    hooks_dir = home / ".copilot" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    command = (
        f'python3 "{bridge_path}" --to copilot --timeout 20 -- bash "{wrapped_path}"'
    )
    config = {
        "version": 1,
        "hooks": {
            "sessionStart": [
                {
                    "type": "command",
                    "bash": command,
                    "matcher": "^(?:startup|resume)$",
                    "timeoutSec": 25,
                }
            ]
        },
    }
    (hooks_dir / "vibecrafted-fleet.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


def test_extract_script_paths_catches_bridge_wrapped_script() -> None:
    module = _load_module()
    command = (
        'python3 "/home/u/.agents/skills/vc-hello/tools/hook_bridge.py" '
        '--to copilot --timeout 20 -- bash "/home/u/.claude/hooks/x.sh"'
    )
    assert module._extract_script_paths(command) == [
        "/home/u/.agents/skills/vc-hello/tools/hook_bridge.py",
        "/home/u/.claude/hooks/x.sh",
    ]


def test_extract_script_paths_stops_at_shell_redirect() -> None:
    module = _load_module()
    command = (
        "bash /home/u/.claude/hooks/aicx-sessionstart.sh > /home/u/out.md 2>&1 || true"
    )
    assert module._extract_script_paths(command) == [
        "/home/u/.claude/hooks/aicx-sessionstart.sh"
    ]


def test_missing_bridge_script_is_flagged_broken(tmp_path: Path) -> None:
    module = _load_module()
    module.HOME = tmp_path

    missing_bridge = (
        tmp_path / ".agents" / "skills" / "vc-hello" / "tools" / "hook_bridge.py"
    )
    wrapped = tmp_path / ".claude" / "hooks" / "aicx-sessionstart.sh"
    wrapped.parent.mkdir(parents=True, exist_ok=True)
    wrapped.write_text("#!/bin/sh\n", encoding="utf-8")
    _write_copilot_hooks(tmp_path, missing_bridge, wrapped)

    fleet = {"copilot": module.scan_copilot()}
    broken = module._hook_broken_entries(fleet)

    assert broken == [
        {"cli": "copilot", "event": "session_start", "path": str(missing_bridge)}
    ]
    consensus = module.compute_consensus(fleet)
    assert consensus["hooks"]["status"] == "broken"
    assert consensus["hooks"]["broken"] == broken


def test_existing_bridge_script_is_ok(tmp_path: Path) -> None:
    module = _load_module()
    module.HOME = tmp_path

    bridge = tmp_path / ".agents" / "skills" / "vc-hello" / "tools" / "hook_bridge.py"
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    wrapped = tmp_path / ".claude" / "hooks" / "aicx-sessionstart.sh"
    wrapped.parent.mkdir(parents=True, exist_ok=True)
    wrapped.write_text("#!/bin/sh\n", encoding="utf-8")

    _write_copilot_hooks(tmp_path, bridge, wrapped)

    fleet = {"copilot": module.scan_copilot()}
    broken = module._hook_broken_entries(fleet)

    assert broken == []
    consensus = module.compute_consensus(fleet)
    assert consensus["hooks"]["status"] == "ok"
    assert consensus["hooks"]["broken"] == []


def test_cli_check_subcommand_exits_nonzero_on_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    module.HOME = tmp_path
    # Isolate to copilot only so other fleet members' real-host configs can
    # never leak into this assertion via module-level HOME resolution.
    module.SCANNERS = {"copilot": module.scan_copilot}

    missing_bridge = (
        tmp_path / ".agents" / "skills" / "vc-hello" / "tools" / "hook_bridge.py"
    )
    wrapped = tmp_path / ".claude" / "hooks" / "aicx-sessionstart.sh"
    wrapped.parent.mkdir(parents=True, exist_ok=True)
    wrapped.write_text("#!/bin/sh\n", encoding="utf-8")
    _write_copilot_hooks(tmp_path, missing_bridge, wrapped)

    output = tmp_path / "broken.json"
    monkeypatch.setattr(
        sys, "argv", ["fleet_scan.py", "check", "--output", str(output)]
    )
    exit_code = module.main()

    assert exit_code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "broken"
    assert report["broken"] == [
        {"cli": "copilot", "event": "session_start", "path": str(missing_bridge)}
    ]


def test_cli_check_subcommand_exits_zero_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    module.HOME = tmp_path
    module.SCANNERS = {"copilot": module.scan_copilot}

    bridge = tmp_path / ".agents" / "skills" / "vc-hello" / "tools" / "hook_bridge.py"
    bridge.parent.mkdir(parents=True, exist_ok=True)
    bridge.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    wrapped = tmp_path / ".claude" / "hooks" / "aicx-sessionstart.sh"
    wrapped.parent.mkdir(parents=True, exist_ok=True)
    wrapped.write_text("#!/bin/sh\n", encoding="utf-8")
    _write_copilot_hooks(tmp_path, bridge, wrapped)

    output = tmp_path / "broken.json"
    monkeypatch.setattr(
        sys, "argv", ["fleet_scan.py", "check", "--output", str(output)]
    )
    exit_code = module.main()

    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "ok"
    assert report["broken"] == []
