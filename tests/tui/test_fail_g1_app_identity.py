"""G1 falsifiers: Debug App must not hijack the product LaunchServices name.

Also pins Start Here to a generation interpreter so host Xcode/Homebrew
python3 cannot claim 'healthy' while failing to import vibecrafted_core.
"""

from __future__ import annotations

import builtins
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_YML = REPO_ROOT / "vibecrafted-app/shell-agent/app/project.yml"
MAKEFILE = REPO_ROOT / "vibecrafted-app/shell-agent/Makefile"
APP_DELEGATE = (
    REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
)
INFO_PLIST = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/Info.plist"
START_HERE = (
    REPO_ROOT / "vibecrafted-core/vibecrafted_core/config/vc-frame/vc-start-here.py"
)


def test_fail_g1_debug_bundle_id_is_not_the_product_id() -> None:
    text = PROJECT_YML.read_text(encoding="utf-8")
    debug = text.split("Debug:", 1)[1].split("Release:", 1)[0]
    release = text.split("Release:", 1)[1]
    assert "PRODUCT_BUNDLE_IDENTIFIER: io.vetcoders.vibecrafted.dev" in debug
    assert "PRODUCT_BUNDLE_IDENTIFIER: io.vetcoders.vibecrafted" in release
    assert "io.vetcoders.vibecrafted.dev" not in release
    makefile = MAKEFILE.read_text(encoding="utf-8")
    assert "build/VibecraftedDev.app" in makefile
    assert 'cp -R "$$BUILT_APP" build/Vibecrafted.app' not in makefile


def test_fail_g1_app_does_not_restore_windows_across_login() -> None:
    """Secure coding stays on; restoration ownership is the actual off switch."""
    delegate = APP_DELEGATE.read_text(encoding="utf-8")
    secure = delegate.split("func applicationSupportsSecureRestorableState")[1].split(
        "func "
    )[0]
    restore = delegate.split("func applicationShouldRestoreApplicationState")[1].split(
        "func "
    )[0]
    save = delegate.split("func applicationShouldSaveApplicationState")[1].split(
        "func "
    )[0]
    assert "\n    true\n" in secure
    assert "\n    false\n" not in secure
    assert "\n    false\n" in restore
    assert "\n    true\n" not in restore
    assert "\n    false\n" in save
    assert "\n    true\n" not in save
    factory = (
        REPO_ROOT
        / "vibecrafted-app/shell-agent/app/Vibecrafted/Views/MainWindowController.swift"
    ).read_text(encoding="utf-8")
    make_window = factory.split("static func makeWindow")[1].split("static func mount")[
        0
    ]
    assert "window.isRestorable = false" in make_window
    assert "window.restorationClass = nil" in make_window
    assert "disableRelaunchOnLogin" not in delegate
    plist = INFO_PLIST.read_text(encoding="utf-8")
    assert "<key>NSQuitAlwaysKeepsWindows</key>" in plist
    assert "<false/>" in plist.split("<key>NSQuitAlwaysKeepsWindows</key>", 1)[1][:80]
    assert "<key>NSSupportsAutomaticTermination</key>" in plist
    assert "<key>NSSupportsSuddenTermination</key>" in plist


def test_fail_g1_start_here_reexecs_generation_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host python3 without vibecrafted_core must exec the generation interpreter."""
    generation = tmp_path / "generation" / "bin"
    generation.mkdir(parents=True)
    wrapper = generation / "python3"
    wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    wrapper.chmod(0o755)
    monkeypatch.setenv("VIBECRAFTED_PYTHON", str(wrapper))
    monkeypatch.delenv("VIBECRAFTED_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("VIBECRAFTED_ROOT", raising=False)

    spec = importlib.util.spec_from_file_location("vc_start_here_g1", START_HERE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    recorded: list[tuple[str, list[str]]] = []

    def fake_execv(path: str, argv: list[str]) -> None:
        recorded.append((path, list(argv)))
        raise SystemExit(0)

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == "vibecrafted_core":
            raise ImportError("forced host python")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(module.os, "execv", fake_execv)
    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(SystemExit) as exited:
        module.ensure_generation_python()
    assert exited.value.code == 0
    assert recorded, "host python must re-exec a generation interpreter"
    assert recorded[0][0] == str(wrapper)
    assert recorded[0][1][0] == str(wrapper)
