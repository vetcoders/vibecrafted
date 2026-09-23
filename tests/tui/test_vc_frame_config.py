from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VC_FRAME_CONFIG = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "config.kdl"
)
LAYOUTS_DIR = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "layouts"
)
THEMES_DIR = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "config"
    / "vc-frame"
    / "themes"
)


def test_vc_frame_config_uses_plain_ctrl_without_option_layer() -> None:
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")

    assert 'unbind "Alt f" "Alt n" "Alt i" "Alt o"' in payload
    assert 'bind "Ctrl n" { NewPane; }' in payload
    assert "Ctrl Shift" not in payload


def test_composer_bind_does_not_enable_line_numbers() -> None:
    """Composer is prose. A mouse selection copies cells; a gutter rides into paste.

    The Super+e fallback must not resurrect `set number` after 60d9986f dropped
    the gutter from vc-composer.sh.
    """
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    composer = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "config"
        / "vc-frame"
        / "vc-composer.sh"
    ).read_text(encoding="utf-8")

    assert "-c 'set number'" not in payload
    assert "-c 'set nonumber'" in payload
    assert "set nonumber" in composer
    assert "set norelativenumber" in composer
    assert "Draft in vim with: number," not in composer


def test_vc_frame_config_enables_kitty_protocol_for_super_switcher() -> None:
    # Key-contract v3 (8a0f14e65): the global Super/Cmd switcher rides kitty
    # CSI-u sequences. Disabling this strands "Super Left/Right/Up/Down" and
    # "Super e" as raw escape passthrough in every pane — see doctrine
    # 2026-08-05 for the live-session repro.
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")

    assert "support_kitty_keyboard_protocol true" in payload


def test_quick_cmd_shortcut_reuses_active_compact_bar_including_locked_mode() -> None:
    """Cmd+Shift+. is a shared message, not a second quick-command launcher."""
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    shared = payload[payload.index("    shared {") : payload.index("    shared_except")]
    quick_cmd = shared[
        shared.index('bind "Super Shift ."') : shared.index(
            "        // Command Composer"
        )
    ]

    assert 'bind "Super Shift ."' in quick_cmd
    assert 'MessagePlugin "compact-bar"' in quick_cmd
    assert 'name "vc_quick_cmd"' in quick_cmd
    assert "Run " not in quick_cmd


def test_cmd_n_opens_existing_session_manager_not_a_direct_tab() -> None:
    """Cmd+N is Create new workspace through the existing Session Manager."""
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    shared = payload[payload.index("    shared {") : payload.index("    shared_except")]
    workspace = shared[
        shared.index('bind "Super n"') : shared.index('bind "Super Shift ."')
    ]

    assert 'bind "Super n"' in workspace
    assert 'LaunchOrFocusPlugin "session-manager"' in workspace
    assert "floating true" in workspace
    assert "move_to_focused_tab true" in workspace
    assert "NewTab" not in workspace


def test_vc_frame_config_ctrl_q_closes_focus_not_session() -> None:
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    active_lines = [
        line.strip()
        for line in payload.splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    ]

    # Plain Ctrl+q must never map to Quit. Full quit stays inside session mode.
    assert 'unbind "Ctrl q"' in payload
    assert 'bind "Ctrl q" { CloseFocus; SwitchToMode "Normal"; }' in payload
    assert 'bind "q" { Quit; }' in payload
    assert 'bind "Ctrl q" { Quit; }' not in active_lines


def test_vc_frame_config_dual_theme_monochrome_dark_ivory_light() -> None:
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")

    # Brand block stays defined (graphite + amber) for explicit / mesh use.
    assert "vibecrafted {" in payload
    assert "amber gold" in payload.lower() or "214 175 54" in payload
    # Fleet chrome: dark monochrome + light ivory; never default pastel green.
    assert 'theme "monochrome"' in payload
    assert 'theme_dark "monochrome"' in payload
    assert 'theme_light "vibecrafted-ivory"' in payload
    assert 'theme "pastel"' not in payload
    # Flat key tiles — no powerline  triangles on status-bar / tab-bar.
    assert "simplified_ui true" in payload


def test_vibecrafted_ivory_theme_file_exists_and_is_warm_paper() -> None:
    ivory = THEMES_DIR / "vibecrafted-ivory.kdl"
    assert ivory.is_file()
    payload = ivory.read_text(encoding="utf-8")
    assert "vibecrafted-ivory" in payload
    # Ivory paper background (not dark, not neon green ribbons).
    assert "250 246 238" in payload
    assert "166 227 161" not in payload  # catppuccin green ribbon from pastel


def test_vc_frame_config_session_resilience() -> None:
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")

    assert 'on_force_close "detach"' in payload
    assert "session_serialization true" in payload
    assert "serialize_pane_viewport true" in payload


def test_native_default_names_the_packaged_operator_layout() -> None:
    """First product session is the frame host; operator.kdl stays Start here.

    Repointed from default_layout "operator": Start here stays on operator.kdl
    as guest/workspace content. The native default is layouts/host.kdl.
    """
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    host = LAYOUTS_DIR / "host.kdl"
    operator = LAYOUTS_DIR / "operator.kdl"

    assert 'default_layout "host"' in payload
    assert host.is_file()
    assert not host.is_symlink()
    assert operator.is_file()
    assert not operator.is_symlink()
    assert 'tab name="Start here"' in operator.read_text(encoding="utf-8")


def test_vc_frame_config_has_plugin_aliases() -> None:
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")

    # vc-frame still accepts builtin plugin aliases through the upstream
    # zellij: URL scheme; vc-frame: is rejected by the 0.45.x parser.
    assert 'compact-bar location="zellij:compact-bar"' in payload
    assert 'session-manager location="zellij:session-manager"' in payload
    assert 'frame-host location="zellij:session-manager"' in payload


def test_all_layouts_keep_sessions_rail_always_visible() -> None:
    """Every layout tab template must pin session-manager rail ALWAYS."""
    for layout_file in sorted(LAYOUTS_DIR.glob("*.kdl")):
        payload = layout_file.read_text(encoding="utf-8")
        assert "session-manager" in payload, (
            f"{layout_file.name} missing session-manager"
        )
        assert "rail true" in payload or 'rail "true"' in payload, (
            f"{layout_file.name} missing rail true on session-manager"
        )
        assert "default_tab_template" in payload or "new_tab_template" in payload, (
            f"{layout_file.name} missing tab template"
        )


def test_all_layouts_have_status_chrome() -> None:
    for layout_file in sorted(LAYOUTS_DIR.glob("*.kdl")):
        payload = layout_file.read_text(encoding="utf-8")
        assert 'plugin location="status-bar"' in payload, (
            f"{layout_file.name} missing status-bar"
        )
        assert (
            'plugin location="compact-bar"' in payload
            or 'plugin location="tab-bar"' in payload
        ), f"{layout_file.name} missing top bar plugin"


def test_layout_tab_branding_matches_frame_contract() -> None:
    """Non-operator layouts use the brand prefix on primary tabs."""
    for layout_file in sorted(LAYOUTS_DIR.glob("*.kdl")):
        payload = layout_file.read_text(encoding="utf-8")
        if layout_file.name == "operator.kdl":
            # Launch alias for default_layout "vibecrafted": product workspace tabs.
            assert 'tab name="Start here"' in payload
            assert 'tab name="Agents"' in payload
            assert 'tab name="Shell"' in payload
            assert 'tab name="Voc"' in payload
            continue
        assert "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍." in payload, f"{layout_file.name} missing branded tab name"


def test_marbles_layout_is_operator_centric() -> None:
    """Marbles layout must give operator the majority of screen space and
    keep monitoring in a compact section."""
    payload = (LAYOUTS_DIR / "marbles.kdl").read_text(encoding="utf-8")
    assert 'name="operator"' in payload
    assert 'size="75%"' in payload
    assert "focus=true" in payload


def test_operator_layout_matches_vibecrafted_standard() -> None:
    """vc-start operator.kdl is the Start here / guest workspace layout:
    Start here + Agents + Shell + Voc, SESSIONS rail on every tab, no strider."""
    payload = (LAYOUTS_DIR / "operator.kdl").read_text(encoding="utf-8")
    assert 'tab name="Start here"' in payload
    assert 'tab name="Agents"' in payload
    assert 'tab name="Shell"' in payload
    assert 'tab name="Voc"' in payload
    assert "vc-start-here.py" in payload
    assert "vc-agent-workshop.py" in payload
    assert "pane-python" in payload
    assert "VIBECRAFTED_PYTHON" in payload
    assert "$HOME/.local/bin/voc" in payload
    assert payload.index(
        "VIBECRAFTED_RUNTIME_ROOT:+$VIBECRAFTED_RUNTIME_ROOT/bin/vc-o"
    ) < payload.index("command -v voc")
    assert "vibecrafted tui" in payload
    assert "session-manager" in payload
    assert "rail true" in payload
    assert "default_tab_template" in payload
    assert "compact-bar" in payload
    assert "status-bar" in payload
    assert "session_layer" in payload
    assert 'tab name="Start here" focus=true' in payload
    assert "vibecrafted start" in payload
    # Rejected parallel path (ignore comments).
    active = "\n".join(
        line
        for line in payload.splitlines()
        if line.strip() and not line.lstrip().startswith("//")
    )
    assert "strider" not in active
    assert 'tab name="Operator"' not in active
    assert "VibeCrafted" not in active


def test_operator_voc_uses_active_generation_before_standalone_voc(
    tmp_path: Path,
) -> None:
    payload = (LAYOUTS_DIR / "operator.kdl").read_text(encoding="utf-8")
    line = next(line for line in payload.splitlines() if 'args "-lc" "for c in' in line)
    match = re.search(r'args "-lc" (".*")', line)
    assert match is not None
    command = json.loads(match.group(1))
    runtime_bin = tmp_path / "runtime/bin"
    runtime_bin.mkdir(parents=True)
    vc_o = runtime_bin / "vc-o"
    vc_o.write_text("#!/bin/sh\nprintf 'active-generation\\n'\n")
    vc_o.chmod(0o755)
    old_bin = tmp_path / "old-bin"
    old_bin.mkdir()
    old_voc = old_bin / "voc"
    old_voc.write_text("#!/bin/sh\nprintf 'old-voc\\n'\n")
    old_voc.chmod(0o755)
    env = os.environ.copy()
    env["VIBECRAFTED_RUNTIME_ROOT"] = str(tmp_path / "runtime")
    env["PATH"] = f"{old_bin}:/usr/bin:/bin"
    result = subprocess.run(
        ["bash", "-lc", command], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "active-generation\n"


def test_dashboard_and_marbles_probe_packaged_mission_control() -> None:
    """Runtime Pack helpers live under vibecrafted_core/runtime, not ~/.vibecrafted/runtime."""
    for name in ("dashboard.kdl", "marbles.kdl"):
        payload = (LAYOUTS_DIR / name).read_text(encoding="utf-8")
        assert "vibecrafted-core/vibecrafted_core/runtime" in payload, name
        assert "vc-operator/mission-control/" in payload, name


def test_operator_layout_start_here_and_shell_tabs() -> None:
    payload = (LAYOUTS_DIR / "operator.kdl").read_text(encoding="utf-8")
    assert 'command="bash" name="Start Here"' in payload
    assert 'plugin location="about"' not in payload
    assert "pane-python" in payload
    # `config install` is retired (e1d7a791); the Runtime Pack installer owns
    # product configuration and the repair hint routes through make install.
    assert "config install" not in payload
    assert "make install" in payload
    assert 'name="Shell"' in payload
    # Shell wakes with banner then zsh (not bare suspended /bin/zsh).
    assert "exec zsh" in payload or "zsh -l" in payload
    assert "start_suspended true" not in payload


def test_workflow_layout_has_swap_layouts() -> None:
    """Workflow layout should support solo/dual swap modes."""
    payload = (LAYOUTS_DIR / "workflow.kdl").read_text(encoding="utf-8")
    assert "swap_tiled_layout" in payload
    assert '"solo"' in payload
    assert '"dual"' in payload


def test_research_layout_synthesis_focused() -> None:
    """Research layout should give synthesis pane the focus and majority."""
    payload = (LAYOUTS_DIR / "research.kdl").read_text(encoding="utf-8")
    assert 'name="synthesis"' in payload
    assert 'size="55%"' in payload


def _layout_declares_frame_host(payload: str) -> bool:
    return "frame_host true" in payload or 'frame_host "true"' in payload


def _kdl_block(payload: str, declaration: str) -> str:
    start = payload.index(declaration)
    opening_brace = payload.index("{", start)
    depth = 0
    for offset, character in enumerate(payload[opening_brace:], start=opening_brace):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return payload[opening_brace + 1 : offset]
    raise AssertionError(f"unterminated KDL block: {declaration}")


def test_product_layout_declares_frame_host() -> None:
    """A1: a shipped product layout carries rail frame_host true."""
    host = LAYOUTS_DIR / "host.kdl"
    assert host.is_file()
    assert not host.is_symlink()
    payload = host.read_text(encoding="utf-8")
    assert _layout_declares_frame_host(payload)
    assert "rail true" in payload or 'rail "true"' in payload
    assert "session-manager" in payload
    assert 'plugin location="frame-host"' in payload
    config = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    assert "frame-host location=" in config
    assert "frame_host true" in config


def test_host_layout_has_exactly_one_projection_owner_outside_session_layer() -> None:
    """The session layer is cloned per tab and cannot own the host projection."""
    payload = (LAYOUTS_DIR / "host.kdl").read_text(encoding="utf-8")
    session_layer = _kdl_block(payload, "session_layer")
    workspace_tab = _kdl_block(payload, 'tab name="Workspace"')

    assert "frame_host true" not in session_layer
    assert payload.count("frame_host true") == 1
    assert "frame_host true" in workspace_tab
    assert "workspace_surface true" in workspace_tab


def test_layout_contract_gate_is_fail_closed_on_hash_drift(tmp_path: Path) -> None:
    layouts = tmp_path / "layouts"
    shutil.copytree(LAYOUTS_DIR, layouts)
    config = tmp_path / "config.kdl"
    shutil.copy2(VC_FRAME_CONFIG, config)
    lock = tmp_path / "layouts.sha256.json"
    shutil.copy2(LAYOUTS_DIR.parent / "layouts.sha256.json", lock)
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts/check-layout-contract.py"),
        "--layouts-dir",
        str(layouts),
        "--config",
        str(config),
        "--lock",
        str(lock),
    ]

    clean = subprocess.run(command, capture_output=True, text=True, check=False)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    with (layouts / "dashboard.kdl").open("a", encoding="utf-8") as handle:
        handle.write("\n// unreviewed layout drift\n")
    drifted = subprocess.run(command, capture_output=True, text=True, check=False)
    assert drifted.returncode != 0
    assert "layout hashes drifted" in drifted.stderr


def test_first_session_is_the_frame_host() -> None:
    """A2: the first product session uses the host layout (config default)."""
    payload = VC_FRAME_CONFIG.read_text(encoding="utf-8")
    assert 'default_layout "host"' in payload
    host = LAYOUTS_DIR / "host.kdl"
    assert host.is_file()
    host_text = host.read_text(encoding="utf-8")
    assert _layout_declares_frame_host(host_text)
    # vc-start still creates operator.kdl as kind=host when outside a frame;
    # that rail must also be a frame host so the first Start here session
    # switches through activate_session_request.
    operator = (LAYOUTS_DIR / "operator.kdl").read_text(encoding="utf-8")
    assert _layout_declares_frame_host(operator)
    assert "rail true" in operator or 'rail "true"' in operator
