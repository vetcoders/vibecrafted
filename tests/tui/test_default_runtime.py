from __future__ import annotations

from vibecrafted_core import cli


def _force_non_tty(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False, raising=False)


def _force_real_tty(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True, raising=False)


def _clear_session_env(monkeypatch) -> None:
    for key in (
        "VIBECRAFTED_OPERATOR_SESSION",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ_SESSION_NAME",
    ):
        monkeypatch.delenv(key, raising=False)


def test_default_runtime_passes_through_explicit_runtime(monkeypatch) -> None:
    _clear_session_env(monkeypatch)
    # Explicit runtime wins even over a real operator TTY — internal
    # `--runtime headless` dispatches (wrappers, supervisor) must stay headless.
    _force_real_tty(monkeypatch)
    assert cli._default_runtime("headless", "/some/repo") == "headless"


def test_default_runtime_headless_for_real_operator_tty(monkeypatch) -> None:
    _clear_session_env(monkeypatch)
    _force_real_tty(monkeypatch)
    # A terminal invocation still launches an ordinary worker headless. A
    # visible compatibility host is an explicit --runtime choice.
    assert cli._default_runtime("", "/some/repo") == "headless"


def test_default_runtime_headless_for_nontty_without_session_env(monkeypatch) -> None:
    _clear_session_env(monkeypatch)
    _force_non_tty(monkeypatch)
    # Dispatched workers (no real TTY, no explicit runtime) default headless —
    # visibility comes from the LIVE bucket viewer, not a worker-owned tab.
    assert cli._default_runtime("", "/some/repo") == "headless"


def test_default_runtime_headless_when_inside_vc_frame_env_nontty(monkeypatch) -> None:
    _clear_session_env(monkeypatch)
    _force_non_tty(monkeypatch)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "pensieve")
    # DELIBERATE REVERSAL of 141a19d/3d794af: inheriting an in-frame session
    # env no longer forces terminal for a non-TTY dispatch — the worker stays
    # headless and the LIVE bucket viewer (cut c1) carries visibility instead.
    assert cli._default_runtime("", "/some/repo") == "headless"


def test_default_runtime_headless_when_inside_zellij_env_nontty(monkeypatch) -> None:
    _clear_session_env(monkeypatch)
    _force_non_tty(monkeypatch)
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "pensieve")
    # Same reversal for the raw zellij session-name env, without the
    # vc-frame-specific wrapper var.
    assert cli._default_runtime("", "/some/repo") == "headless"
