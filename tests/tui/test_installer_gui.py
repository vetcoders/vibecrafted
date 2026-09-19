from __future__ import annotations

import inspect
from pathlib import Path

from scripts import installer_gui


def test_build_install_command_respects_shell_toggle(tmp_path: Path) -> None:
    installer = tmp_path / "scripts" / "vetcoders_install.py"
    installer.parent.mkdir(parents=True)
    installer.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    with_shell = installer_gui.build_install_command(str(tmp_path), with_shell=True)
    without_shell = installer_gui.build_install_command(str(tmp_path), with_shell=False)

    assert with_shell[-1] == "--with-shell"
    assert "--with-shell" not in without_shell
    assert "--mirror" in with_shell
    assert "--mirror" in without_shell
    assert with_shell[:5] == without_shell[:5]


def test_build_install_steps_include_foundations_before_installer(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("VIBECRAFTED_RUNTIME", raising=False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "vetcoders_install.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    (scripts_dir / "install-foundations.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )

    steps = installer_gui.build_install_steps(str(tmp_path), with_shell=True)

    assert [step.label for step in steps] == [
        "Bootstrap foundations",
        "Install Vibecrafted",
    ]
    assert steps[0].command == ["bash", str(scripts_dir / "install-foundations.sh")]
    assert steps[1].command[-1] == "--with-shell"
    assert "--mirror" in steps[1].command


def test_build_install_steps_include_selected_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "vetcoders_install.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    (scripts_dir / "install-runtime.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    monkeypatch.setenv("VIBECRAFTED_RUNTIME", "wezterm")

    steps = installer_gui.build_install_steps(str(tmp_path), with_shell=True)

    assert [step.label for step in steps] == [
        "Install Vibecrafted",
        "Install runtime: wezterm",
    ]
    assert steps[1].command == [
        "bash",
        str(scripts_dir / "install-runtime.sh"),
        "--runtime",
        "wezterm",
        "--yes",
    ]


def test_preflight_payload_summarizes_diagnostics(monkeypatch, tmp_path: Path) -> None:
    diagnostics = {
        "frameworks": {
            "workflows": {
                "label": "workflows",
                "found": True,
                "detail": "ready",
            }
        },
        "foundations": {
            "loctree-mcp": {
                "label": "loctree-mcp",
                "found": False,
                "detail": "missing",
            }
        },
        "toolchains": {},
        "agents": {},
        "additional_tools": {},
    }
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "vetcoders_install.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    (scripts_dir / "install-foundations.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )

    monkeypatch.setattr(installer_gui, "read_framework_version", lambda _: "1.2.1")
    monkeypatch.setattr(installer_gui, "run_diagnostics", lambda _src=None: diagnostics)
    monkeypatch.setattr(
        installer_gui,
        "sync_state",
        lambda: {
            "active_runs": [],
            "recent_runs": [],
            "warnings": [],
            "events": [],
        },
    )
    monkeypatch.setattr(
        installer_gui,
        "start_here_path",
        lambda: tmp_path / "guide" / "START_HERE.md",
    )
    monkeypatch.setattr(
        installer_gui,
        "helper_layer_path",
        lambda: tmp_path / ".config" / "vibecrafted" / "shell" / "vc-skills.sh",
    )
    skill_store = tmp_path / ".vibecrafted" / "skills"
    (skill_store / "vc-init").mkdir(parents=True)
    monkeypatch.setattr(installer_gui, "framework_store_dir", lambda: skill_store)

    controller = installer_gui.InstallController(str(tmp_path))
    payload = controller.preflight_payload()

    assert payload["version"] == "1.2.1"
    assert payload["found_count"] == 1
    assert payload["missing_count"] == 1
    assert payload["found_items"] == ["Frameworks: workflows"]
    assert payload["missing_items"] == ["Foundations: loctree-mcp"]
    assert payload["needs_install"] == {"foundations": ["loctree-mcp"]}
    assert [step["label"] for step in payload["install_plan"]] == [
        "Bootstrap foundations",
        "Install Vibecrafted",
    ]
    assert payload["launcher_defaults"]["workflows"] == [
        "workflow",
        "research",
        "review",
        "marbles",
    ]
    assert payload["control_plane"]["skills_ready"] == 1
    assert payload["status"]["completed"] is False
    assert payload["bundled_bin_dir"] == str(tmp_path / "tools" / "bin")
    assert payload["bundled_bin_present"] is False


def test_gui_keeps_tui_diagnostic_api_contract() -> None:
    """The browser installer relies on source-aware TUI diagnostics."""

    signature = inspect.signature(installer_gui.run_diagnostics)

    assert "source_dir" in signature.parameters
    assert signature.parameters["source_dir"].default is None
    assert callable(installer_gui.bundled_bin_root)


def test_install_runtime_env_prepends_repo_owned_bins(
    monkeypatch, tmp_path: Path
) -> None:
    crafted_home = tmp_path / ".vibecrafted"
    runtime_home = tmp_path / ".local" / "share" / "vibecrafted"
    cargo_bin = tmp_path / ".cargo" / "bin"
    local_bin = tmp_path / ".local" / "bin"
    node_bin = runtime_home / "tools" / "node" / "bin"
    runtime_bin = runtime_home / "bin"
    for path in (cargo_bin, local_bin, node_bin, runtime_bin):
        path.mkdir(parents=True)

    monkeypatch.setattr(installer_gui, "vibecrafted_home", lambda: crafted_home)
    monkeypatch.setattr(installer_gui, "vibecrafted_runtime_bin", lambda: runtime_bin)
    monkeypatch.setattr(
        installer_gui, "vibecrafted_tools_home", lambda: runtime_home / "tools"
    )
    monkeypatch.setattr(installer_gui.Path, "home", lambda: tmp_path)

    env = installer_gui.install_runtime_env({"PATH": "/usr/bin"})
    pieces = env["PATH"].split(":")

    assert pieces[:4] == [
        str(local_bin),
        str(cargo_bin),
        str(node_bin),
        str(runtime_bin),
    ]


def test_build_html_renders_wizard_shell() -> None:
    html = installer_gui.build_html(
        {
            "version": "1.2.1",
            "source_dir_display": "~/src/vibecrafted",
            "guide_path_display": "~/.vibecrafted/START_HERE.md",
            "helper_path_display": "~/.config/vibecrafted/shell/vc-skills.sh",
            "found_count": 3,
            "missing_count": 2,
            "found_items": ["Frameworks: workflows"],
            "missing_items": ["Foundations: loctree-mcp"],
            "needs_install": {"foundations": ["loctree-mcp"]},
            "install_plan": [
                {
                    "label": "Bootstrap foundations",
                    "command": "bash scripts/install-foundations.sh",
                },
                {
                    "label": "Install Vibecrafted",
                    "command": "python3 scripts/vetcoders_install.py install --compact",
                },
            ],
            "control_plane": {
                "active_runs": [],
                "recent_runs": [],
                "warnings": [],
                "events": [],
                "helper_path": "~/.config/vibecrafted/shell/vc-skills.sh",
                "skills_ready": 3,
            },
            "launcher_defaults": {
                "workflows": ["workflow", "research", "review", "marbles"],
                "agents": ["claude", "codex", "gemini", "agy", "junie", "grok"],
                "runtimes": ["headless", "terminal", "visible"],
            },
            "categories": [],
            "status": {"completed": False, "running": False, "output": []},
        }
    )

    assert 'id="wizard-progress"' in html
    assert 'data-step="5"' in html
    assert "Launch guided install" in html
    assert "Finish state" in html
    assert "make wizard" in html
    assert "make install" in html
    assert "Local checkout GUI" in html
    assert "Terminal-native fallback" not in html
    assert "height: calc(100dvh - 36px);" in html
    assert "overflow: hidden;" in html
    assert "-webkit-overflow-scrolling: touch;" in html
    assert "grid-template-rows: auto auto minmax(0, 1fr) auto;" in html
    assert "document.addEventListener('keydown'" in html
    assert "activeSlide?.querySelector('.slide-body')?.scrollTo" in html
    assert 'id="launcher-form"' in html
    assert 'id="active-run-list"' in html
    assert "Vibecrafted Control Plane" in html


def test_launch_workflow_returns_control_plane_payload(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        installer_gui,
        "normalize_launch_spec",
        lambda payload, source_dir: {"payload": payload, "source_dir": source_dir},
    )
    monkeypatch.setattr(
        installer_gui,
        "launch_workflow",
        lambda spec, source_dir, env=None: {
            "accepted": True,
            "message": "launched",
            "spec": spec,
            "source_dir": source_dir,
            "env_path": env.get("PATH", "") if env else "",
        },
    )
    monkeypatch.setattr(
        installer_gui,
        "sync_state",
        lambda: {
            "active_runs": [{"run_id": "run-1"}],
            "recent_runs": [],
            "warnings": [],
            "events": [],
        },
    )
    monkeypatch.setattr(
        installer_gui, "framework_store_dir", lambda: tmp_path / "skills"
    )
    controller = installer_gui.InstallController(str(tmp_path))

    ok, payload = controller.launch_workflow({"skill": "workflow", "prompt": "Ship it"})

    assert ok is True
    assert payload["accepted"] is True
    assert payload["spec"]["payload"]["skill"] == "workflow"
    assert payload["source_dir"] == str(tmp_path)


def _stub_controller_deps(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(installer_gui, "read_framework_version", lambda _: "0.0.0")
    monkeypatch.setattr(installer_gui, "run_diagnostics", lambda _src=None: {})
    monkeypatch.setattr(
        installer_gui,
        "summarize_diagnostics",
        lambda diagnostics: ([], [], {}),
    )


def _make_bundle_shape(root: Path) -> Path:
    install_page = root / "en" / "install" / "index.html"
    install_page.parent.mkdir(parents=True)
    install_page.write_text("<html><body>bundle</body></html>", encoding="utf-8")
    return root


def test_resolve_site_dist_finds_bundle_in_source_tree(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_controller_deps(monkeypatch, tmp_path)
    bundle = _make_bundle_shape(tmp_path / "site" / "dist")

    controller = installer_gui.InstallController(str(tmp_path))

    assert controller.site_dist_dir == bundle.resolve()


def test_resolve_site_dist_respects_bundle_dir_flag(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_controller_deps(monkeypatch, tmp_path)
    bundle = _make_bundle_shape(tmp_path / "prebuilt")

    controller = installer_gui.InstallController(str(tmp_path), bundle_dir=str(bundle))

    assert controller.site_dist_dir == bundle.resolve()


def test_resolve_site_dist_respects_env_var(monkeypatch, tmp_path: Path) -> None:
    _stub_controller_deps(monkeypatch, tmp_path)
    bundle = _make_bundle_shape(tmp_path / "env-bundle")
    monkeypatch.setenv("VIBECRAFTED_SITE_BUNDLE", str(bundle))

    controller = installer_gui.InstallController(str(tmp_path))

    assert controller.site_dist_dir == bundle.resolve()


def test_resolve_site_dist_finds_built_sibling_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    source_root = tmp_path / "vibecrafted"
    source_root.mkdir()
    _stub_controller_deps(monkeypatch, source_root)
    bundle = _make_bundle_shape(tmp_path / "vibecrafted-io" / "site" / "dist")

    controller = installer_gui.InstallController(str(source_root))

    assert controller.site_dist_dir == bundle.resolve()


def test_resolve_site_dist_returns_none_when_absent(
    monkeypatch, tmp_path: Path
) -> None:
    _stub_controller_deps(monkeypatch, tmp_path)
    monkeypatch.delenv("VIBECRAFTED_SITE_BUNDLE", raising=False)

    controller = installer_gui.InstallController(str(tmp_path))

    assert controller.site_dist_dir is None
