from scripts import installer_gui
from scripts.installer.vetcoders_installer import tui as installer_textual


def _empty_diagnostics() -> dict[str, dict[str, dict[str, object]]]:
    return {category: {} for category in installer_gui.CATEGORY_ORDER}


def test_summarize_diagnostics_groups_missing_items_by_category() -> None:
    diagnostics = _empty_diagnostics()
    diagnostics["foundations"] = {
        "loctree-mcp": {"label": "loctree-mcp", "found": True},
        "aicx-mcp": {"label": "aicx-mcp", "found": False},
    }
    diagnostics["agents"] = {
        "codex": {"label": "codex", "found": True},
        "gemini": {"label": "gemini", "found": False},
    }

    found, missing, needs_install = installer_gui.summarize_diagnostics(diagnostics)

    assert "Foundations: loctree-mcp" in found
    assert "Agents: codex" in found
    assert "Foundations: aicx-mcp" in missing
    assert "Agents: gemini" in missing
    assert needs_install == {"foundations": ["aicx-mcp"], "agents": ["gemini"]}


def test_textual_chrome_uses_full_pane_width(monkeypatch) -> None:
    app = installer_textual.InstallerIntroApp(
        [("────", "content", "────")],
        version="1.2.3",
        source_dir=installer_textual.Path("."),
    )
    monkeypatch.setattr(app, "_terminal_size", lambda: (102, 32))

    rendered = app._render_chrome("────\nVetcoders\n────")

    lines = rendered.splitlines()
    assert len(lines[0]) == 100
    assert lines[0] == "─" * 100
    assert lines[1].strip() == "Vetcoders"
    assert len(lines[2]) == 100


def test_textual_install_preview_hides_raw_tail(monkeypatch) -> None:
    app = installer_textual.InstallerIntroApp(
        [("header", "content", "footer")],
        version="1.2.3",
        source_dir=installer_textual.Path("."),
    )
    monkeypatch.setattr(app, "_terminal_size", lambda: (120, 36))
    app._current = 5
    app.install_running = True
    app.install_phase_index = 2
    app.install_phase_total = 3
    app.install_phase_names = ["Introduction", "Installation", "Onboarding"]
    app.install_phase_label = "Installation"
    app.install_phase_reason = "Install foundations, skills, helpers, and runtime."
    app._add_install_log("╠════════ Live progress ════════╣")
    app._add_install_log(
        "/Users/tester/.config/vibecrafted/vc-frame/layouts/operator.kdl"
    )
    app._add_install_log("already linked")

    rendered = app._build_step_5()

    assert "Live progress" in rendered
    assert "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. install" in rendered
    assert "Introduction" in rendered
    assert "◆ Installation" in rendered
    assert "Checkpoint  2/3" in rendered
    assert "Reason      Install foundations, skills, helpers, and runtime." in rendered
    assert "Status      Checking operator.kdl" in rendered
    assert "already linked" not in rendered
    assert "╠" not in rendered
    assert "║" not in rendered


def test_textual_install_log_keeps_larger_tail() -> None:
    app = installer_textual.InstallerIntroApp(
        [("header", "content", "footer")],
        version="1.2.3",
        source_dir=installer_textual.Path("."),
    )

    for index in range(installer_textual.INSTALL_OUTPUT_TAIL + 10):
        app._add_install_log(f"line {index}")

    assert len(app.install_log) == installer_textual.INSTALL_OUTPUT_TAIL
    assert app.install_log[0] == "line 10"
    assert len(app.install_status_log) == installer_textual.INSTALL_STATUS_TAIL
    assert app.install_status_log[-1] == (
        f"line {installer_textual.INSTALL_OUTPUT_TAIL + 9}"
    )
