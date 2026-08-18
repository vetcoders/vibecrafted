"""Isolate pytest from the operator ``~/.vibecrafted`` control plane (C10).

A live dispatched runtime exports ``VIBECRAFTED_*``. The suite must strip those
first, then pin ``VIBECRAFTED_HOME`` to a tmp tree. Stripping without the pin
falls through to ``Path.home() / ".vibecrafted"`` and tests write a real
workspace (HAK-31).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import pytest

ISOLATED_HOME_DIRNAME = "isolated-vibecrafted-home"


class OperatorHomeIsolationError(RuntimeError):
    """Test process would use the operator ``~/.vibecrafted`` control plane."""


def operator_vibecrafted_home() -> Path:
    """Account ``~/.vibecrafted``, ignoring a later ``HOME`` monkeypatch."""
    home = Path.home()
    try:
        import pwd

        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    return (home / ".vibecrafted").expanduser().resolve()


def fail_closed_isolated_home(assigned: str | None) -> Path:
    """Raise if ``VIBECRAFTED_HOME`` is missing or is the operator store."""
    operator = operator_vibecrafted_home()
    if not (assigned or "").strip():
        raise OperatorHomeIsolationError(
            "VIBECRAFTED_HOME is unset after the isolation fixture; "
            f"tests would write the operator control plane at {operator}"
        )
    resolved = Path(assigned).expanduser().resolve()
    if resolved == operator or resolved.is_relative_to(operator):
        raise OperatorHomeIsolationError(
            f"VIBECRAFTED_HOME {resolved} is the operator control plane "
            f"{operator}; tests must use a temporary home"
        )
    return resolved


def isolate_vibecrafted_test_env(
    monkeypatch: pytest.MonkeyPatch,
    isolated_home: Path,
    *,
    strip_prefixes: tuple[str, ...] = ("VIBECRAFTED_",),
    restore: Mapping[str, str] | None = None,
    unset_pythonpath: bool = True,
) -> Path:
    """Strip ambient runtime env, then pin ``VIBECRAFTED_HOME`` to ``isolated_home``.

    Order is load-bearing: strip first (live run identity), then assign the
    tmp home so the strip cannot fall through to the operator store.

    ``isolated_home`` must NOT be a child of the test's ``tmp_path``. Distribution
    and other tests treat ``tmp_path`` as a clean staging tree; nesting the
    control-plane home there leaked ``isolated-vibecrafted-home`` into archive
    inventories (C10 × GTM).
    """
    for key in [name for name in os.environ if name.startswith(strip_prefixes)]:
        monkeypatch.delenv(key, raising=False)
    if unset_pythonpath:
        monkeypatch.delenv("PYTHONPATH", raising=False)

    isolated_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(isolated_home))
    for key, value in (restore or {}).items():
        monkeypatch.setenv(key, value)
    fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))
    return isolated_home
