"""Executable contract tests for vc-canary catalog settlement."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = (
    REPO_ROOT
    / "vibecrafted-core/vibecrafted_core/skills/vc-canary/scripts/canary_cli.py"
)


def _cli_module():
    spec = importlib.util.spec_from_file_location("test_canary_cli", CLI)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _unit(file_path: str, kind: str) -> dict[str, object]:
    return {
        "file": file_path,
        "name": "entry",
        "line": 1,
        "kind": kind,
        "role": "owns the demonstrated runtime behavior",
        "docstring_added": False,
        "authority": "repo_verified",
    }


def _run_merge(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    catalogs = tmp_path / "catalogs"
    output = tmp_path / "catalog.json"
    return subprocess.run(
        [
            sys.executable,
            str(CLI),
            "merge-catalog",
            "--input-dir",
            str(catalogs),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_catalog(tmp_path: Path, name: str, unit: object) -> None:
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    (catalogs / name).write_text(json.dumps({"catalog": [unit]}), encoding="utf-8")


def test_merge_catalog_rejects_missing_authority_with_catalog_and_unit(
    tmp_path: Path,
) -> None:
    unit = _unit("src/lib.rs", "fn")
    unit.pop("authority")
    _write_catalog(tmp_path, "rust-scope.json", unit)

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert "rust-scope.json unit[0] file 'src/lib.rs' plugin rust.py" in result.stderr
    assert "missing required field 'authority'" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_merge_catalog_accepts_complete_rust_unit(tmp_path: Path) -> None:
    _write_catalog(tmp_path, "rust-scope.json", _unit("src/lib.rs", "fn"))

    result = _run_merge(tmp_path)

    assert result.returncode == 0, result.stderr
    merged = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert merged["counts"]["units_cataloged"] == 1


def test_merge_catalog_uses_shell_plugin_not_an_unvalidated_fallback(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path, "shell-scope.json", _unit("scripts/demo.sh", "def"))

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert (
        "shell-scope.json unit[0] file 'scripts/demo.sh' plugin shell.py"
        in result.stderr
    )
    assert "invalid kind 'def'" in result.stderr


def test_every_shipped_language_plugin_uses_the_rust_required_field_contract() -> None:
    module = _cli_module()
    plugins = {plugin.name: plugin for plugin in module.load_language_plugins()}
    expected = plugins["rust.py"].required_fields

    assert set(plugins) == {
        "javascript.py",
        "python.py",
        "rust.py",
        "shell.py",
        "toml.py",
        "typescript.py",
    }
    assert all(plugin.required_fields == expected for plugin in plugins.values())


def test_merge_catalog_accepts_legacy_units_key_with_warning(tmp_path: Path) -> None:
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    (catalogs / "legacy-scope.json").write_text(
        json.dumps({"units": [_unit("src/lib.rs", "fn")]}), encoding="utf-8"
    )

    result = _run_merge(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "legacy top-level key 'units'" in result.stderr
    merged = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert merged["counts"]["units_cataloged"] == 1


def test_merge_catalog_validates_pluginless_language_with_shared_contract(
    tmp_path: Path,
) -> None:
    _write_catalog(
        tmp_path, "swift-scope.json", _unit("Sources/App/Main.swift", "func")
    )

    result = _run_merge(tmp_path)

    assert result.returncode == 0, result.stderr
    merged = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert merged["counts"]["units_cataloged"] == 1


def test_merge_catalog_rejects_pluginless_unit_missing_authority(
    tmp_path: Path,
) -> None:
    unit = _unit("Sources/App/Main.swift", "func")
    unit.pop("authority")
    _write_catalog(tmp_path, "swift-scope.json", unit)

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert "no language plugin; shared contract" in result.stderr
    assert "missing required field 'authority'" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_merge_catalog_fails_on_empty_input_dir(tmp_path: Path) -> None:
    (tmp_path / "catalogs").mkdir()

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert "empty merge is not a settled canary" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_merge_catalog_fails_on_zero_units_total(tmp_path: Path) -> None:
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    (catalogs / "empty-scope.json").write_text(
        json.dumps({"catalog": []}), encoding="utf-8"
    )

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert "zero units" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_merge_catalog_rejects_file_with_surrounding_whitespace(
    tmp_path: Path,
) -> None:
    _write_catalog(tmp_path, "shell-scope.json", _unit("scripts/demo.sh ", "def"))

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert "surrounding whitespace" in result.stderr
    assert not (tmp_path / "catalog.json").exists()


def test_failed_rerun_removes_previous_settled_output(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_text(
        json.dumps({"counts": {"units_cataloged": 99}}), encoding="utf-8"
    )
    unit = _unit("src/lib.rs", "fn")
    unit.pop("authority")
    _write_catalog(tmp_path, "rust-scope.json", unit)

    result = _run_merge(tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / "catalog.json").exists(), (
        "failed merge must not leave a settled-looking artifact behind"
    )


def test_merge_catalog_rejects_paths_escaping_the_repository(tmp_path: Path) -> None:
    for name, bad in (("dotdot.json", "../outside.py"), ("abs.json", "/tmp/other.py")):
        catalogs = tmp_path / name.replace(".json", "") / "catalogs"
        catalogs.mkdir(parents=True)
        (catalogs / name).write_text(
            json.dumps({"catalog": [_unit(bad, "fn")]}), encoding="utf-8"
        )
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "merge-catalog",
                "--input-dir",
                str(catalogs),
                "--output",
                str(catalogs.parent / "catalog.json"),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, bad
        assert "non-escaping repository" in result.stderr, bad
        assert not (catalogs.parent / "catalog.json").exists(), bad


def test_no_strict_still_rejects_escaping_paths(tmp_path: Path) -> None:
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    (catalogs / "bad.json").write_text(
        json.dumps({"catalog": [_unit("../outside.py", "fn")]}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "merge-catalog",
            "--input-dir",
            str(catalogs),
            "--output",
            str(tmp_path / "catalog.json"),
            "--no-strict",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "non-escaping repository" in result.stderr


def test_output_inside_input_dir_is_rejected_before_cleanup(tmp_path: Path) -> None:
    _write_catalog(tmp_path, "rust-scope.json", _unit("src/lib.rs", "fn"))
    catalogs = tmp_path / "catalogs"
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "merge-catalog",
            "--input-dir",
            str(catalogs),
            "--output",
            str(catalogs / "rust-scope.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "destroy its own input" in result.stderr
    assert (catalogs / "rust-scope.json").exists(), "input must survive"
