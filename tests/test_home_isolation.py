"""C10: root pytest conftest must pin VIBECRAFTED_HOME off the operator store."""

from __future__ import annotations

import os

import pytest
from _home_isolation import (
    ISOLATED_HOME_DIRNAME,
    OperatorHomeIsolationError,
    fail_closed_isolated_home,
    operator_vibecrafted_home,
)
from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.runtime_paths import vibecrafted_home


def test_isolation_root_fixture_sets_tmp_vibecrafted_home() -> None:
    assigned = fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))
    operator = operator_vibecrafted_home()
    resolved = vibecrafted_home().resolve()
    assert assigned == resolved
    assert resolved != operator
    assert not resolved.is_relative_to(operator)
    assert ISOLATED_HOME_DIRNAME in resolved.parts


def test_isolation_root_pythonpath_is_unset() -> None:
    assert "PYTHONPATH" not in os.environ


def test_isolation_root_fail_closed_when_home_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBECRAFTED_HOME", raising=False)
    with pytest.raises(OperatorHomeIsolationError, match="unset"):
        fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))


def test_isolation_root_write_stays_off_operator_home() -> None:
    operator = operator_vibecrafted_home()
    isolated = vibecrafted_home().resolve()
    marker = control_plane_home() / "c10-root-isolation-probe"
    marker.mkdir(parents=True, exist_ok=True)
    probe = marker / "probe.txt"
    probe.write_text("c10-root", encoding="utf-8")
    assert probe.resolve().is_relative_to(isolated)
    assert not (
        operator / "control_plane" / "c10-root-isolation-probe" / "probe.txt"
    ).exists()
