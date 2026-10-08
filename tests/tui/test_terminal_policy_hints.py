"""Prove that shipped terminal policy defines and preserves Command+Click hints.

Founder requirement INT-TRM-02: Cmd+Click on URLs and file paths in the terminal
must open in the default system application.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_shipped_terminal_policy_declares_command_click_hints() -> None:
    """Shipped terminal config must declare Command+Click hints for URLs and paths."""
    config_path = REPO_ROOT / "config" / "vc-terminal" / "vibecrafted.toml"
    assert config_path.is_file(), f"missing terminal config at {config_path}"

    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    hints = data.get("hints", {}).get("enabled", [])
    assert len(hints) >= 2, f"expected at least 2 hint blocks, got {len(hints)}"

    # 1. URL / OSC-8 hyperlink hint
    url_hints = [
        h
        for h in hints
        if h.get("hyperlinks") is True
        and h.get("mouse", {}).get("enabled") is True
        and h.get("mouse", {}).get("mods") == "Command"
    ]
    assert len(url_hints) == 1, "missing Command+Click URL/hyperlink hint"
    assert url_hints[0].get("command") == "/usr/bin/open"

    # 2. Local file path hint with line-number stripping
    path_hints = [
        h
        for h in hints
        if h.get("hyperlinks") is False
        and h.get("mouse", {}).get("enabled") is True
        and h.get("mouse", {}).get("mods") == "Command"
    ]
    assert len(path_hints) == 1, "missing Command+Click local file path hint"
    cmd_info = path_hints[0].get("command", {})
    assert isinstance(cmd_info, dict)
    assert cmd_info.get("program") == "/bin/zsh"
    args = cmd_info.get("args", [])
    assert len(args) == 2
    assert args[0] == "-lc"
    assert "/usr/bin/open" in args[1]
    assert "candidate" in args[1]


def test_reconcile_runtime_preference_preserves_hints(tmp_path: Path) -> None:
    """Reconciling terminal-policy.toml preserves hint blocks into destination."""
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(parents=True)
    generation = tmp_path / "generation"
    generation.mkdir(parents=True)

    relative = Path("config/vc-terminal/vibecrafted.toml")
    src = REPO_ROOT / relative
    (generation / relative).parent.mkdir(parents=True)
    (generation / relative).write_bytes(src.read_bytes())

    dest = tmp_path / "terminal-policy.toml"

    outcome = installer._reconcile_runtime_preference(
        dest,
        relative,
        generation,
        runtime_home=runtime_home,
        previous={},
    )
    assert outcome["error"] is None
    body = outcome["body"]
    assert body is not None

    parsed = tomllib.loads(body)
    hints = parsed.get("hints", {}).get("enabled", [])
    assert len(hints) >= 2
