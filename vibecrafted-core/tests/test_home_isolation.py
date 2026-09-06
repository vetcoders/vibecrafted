"""C10: pytest must not write the operator ``~/.vibecrafted`` control plane."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from _home_isolation import (
    ISOLATED_HOME_DIRNAME,
    OperatorHomeIsolationError,
    fail_closed_isolated_home,
    operator_vibecrafted_home,
)
from vibecrafted_core import workspace_catalog as wc
from vibecrafted_core.control_plane import control_plane_home
from vibecrafted_core.runtime_paths import vibecrafted_home


def test_isolation_fixture_sets_tmp_vibecrafted_home() -> None:
    assigned = fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))
    operator = operator_vibecrafted_home()
    resolved = vibecrafted_home().resolve()
    assert assigned == resolved
    assert resolved != operator
    assert not resolved.is_relative_to(operator)
    assert ISOLATED_HOME_DIRNAME in resolved.parts


def test_isolation_pythonpath_is_unset() -> None:
    assert "PYTHONPATH" not in os.environ


def test_isolation_fail_closed_when_home_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBECRAFTED_HOME", raising=False)
    with pytest.raises(OperatorHomeIsolationError, match="unset"):
        fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))


def test_isolation_fail_closed_when_home_is_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(operator_vibecrafted_home()))
    with pytest.raises(OperatorHomeIsolationError, match="operator"):
        fail_closed_isolated_home(os.environ.get("VIBECRAFTED_HOME"))


def test_isolation_control_plane_write_stays_off_operator_home() -> None:
    operator = operator_vibecrafted_home()
    isolated = vibecrafted_home().resolve()
    marker = control_plane_home() / "c10-isolation-probe"
    marker.mkdir(parents=True, exist_ok=True)
    probe = marker / "probe.txt"
    probe.write_text("c10", encoding="utf-8")
    assert probe.is_file()
    assert probe.resolve().is_relative_to(isolated)
    assert not (
        operator / "control_plane" / "c10-isolation-probe" / "probe.txt"
    ).exists()


def test_isolation_create_workspace_does_not_write_operator_catalog(
    tmp_path: Path,
) -> None:
    operator = operator_vibecrafted_home()
    root = tmp_path / "c10-probe-ws"
    root.mkdir()
    record = wc.create_workspace(
        root=root, display_label="c10-isolation-probe", select=False
    )
    catalog = wc.catalog_path().resolve()
    isolated = vibecrafted_home().resolve()
    assert catalog.is_file()
    assert catalog.is_relative_to(isolated)
    assert not catalog.is_relative_to(operator)
    leaked = operator / "control_plane" / "workspaces" / "catalog.json"
    if leaked.exists():
        body = leaked.read_text(encoding="utf-8")
        assert record.workspace_id not in body
    assert record.workspace_id in catalog.read_text(encoding="utf-8")


def test_direct_helper_refuses_pytest_root_against_nonisolated_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="vibecrafted-production-home-") as raw:
        production_home = Path(raw) / ".vibecrafted"
        test_root = Path(raw) / "pytest-of-direct-helper" / "test_meta0"
        test_root.mkdir(parents=True)
        monkeypatch.setenv("VIBECRAFTED_HOME", str(production_home))

        with pytest.raises(
            wc.WorkspaceCatalogError,
            match="refusing to persist an ephemeral test workspace root",
        ):
            wc.resolve_run_workspace_identity(root=test_root)

        assert not (
            production_home / "control_plane" / "workspaces" / "catalog.json"
        ).exists()
