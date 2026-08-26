"""Contract for native settlement notifications (app + guardian handoff)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INFO_PLIST = (
    REPO_ROOT / "vibecrafted-app" / "shell-agent" / "app" / "Vibecrafted" / "Info.plist"
)
NOTIFICATION_MANAGER = (
    REPO_ROOT
    / "vibecrafted-app"
    / "shell-agent"
    / "app"
    / "Vibecrafted"
    / "NotificationManager.swift"
)


def test_info_plist_declares_vibecrafted_url_scheme() -> None:
    # Source Info.plist carries a Semgrep comment that stdlib plistlib rejects.
    text = INFO_PLIST.read_text(encoding="utf-8")
    assert "CFBundleURLTypes" in text
    assert "<string>vibecrafted</string>" in text
    assert "CFBundleURLSchemes" in text


def test_notification_manager_owns_user_notification_center() -> None:
    source = NOTIFICATION_MANAGER.read_text(encoding="utf-8")
    assert "UNUserNotificationCenter" in source
    assert "run.settled" in source
    assert "OPEN_RUN" in source
    assert "OPEN_REPORT" in source
    assert "native_app.pid" in source


def test_notification_manager_accepts_start_here_console_deep_link() -> None:
    source = NOTIFICATION_MANAGER.read_text(encoding="utf-8")
    assert 'url.host == "console"' in source
    assert 'url.path == "/open"' in source
    assert "presentWindow?()" in source
