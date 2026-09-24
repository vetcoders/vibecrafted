from __future__ import annotations

from pathlib import Path

import tomllib


def test_pyproject_publishes_vibecrafted_distribution_with_package_data() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert data["project"]["name"] == "vibecrafted"
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "vibecrafted_core"
    ]
    artifacts = set(data["tool"]["hatch"]["build"]["artifacts"])
    assert "vibecrafted_core/runtime/**" in artifacts
    assert "vibecrafted_core/skills/**" in artifacts
    assert "vibecrafted_core/deck/vibecrafted" in artifacts
    assert "vibecrafted_core/schemas/**" in artifacts
    assert "vibecrafted_core/trust/**" in artifacts
    assert data["project"]["scripts"]["verify-vibecrafted-walkaround"] == (
        "vibecrafted_core.walkaround_runner:main"
    )


def test_package_carries_runtime_skills_and_command_deck() -> None:
    package_root = Path(__file__).resolve().parents[1] / "vibecrafted_core"

    assert (package_root / "runtime" / "scripts" / "await.sh").is_file()
    assert (
        package_root / "runtime" / "workflows" / "prune" / "default_prompt.md"
    ).is_file()
    assert (
        package_root / "runtime" / "telemetry" / "agy-monitor" / "agy_monitor.py"
    ).is_file()
    assert (
        package_root / "runtime" / "telemetry" / "kimi-monitor" / "kimi_monitor.py"
    ).is_file()
    assert (
        package_root
        / "runtime"
        / "telemetry"
        / "com.vetcoders.telemetry.agy.plist.template"
    ).is_file()
    assert (package_root / "skills" / "vc-justdo" / "SKILL.md").is_file()
    assert (package_root / "deck" / "vibecrafted").is_file()
    assert (package_root / "product_contract.py").is_file()
    assert (package_root / "walkaround_runner.py").is_file()
    assert (package_root / "schemas" / "unified_product.schema.v1.json").is_file()
    assert (package_root / "trust" / "release-policy.v1.json").is_file()
    assert (package_root / "trust" / "vibecrafted-signing-v1.pub").is_file()
