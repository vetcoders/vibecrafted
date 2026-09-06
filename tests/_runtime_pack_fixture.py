"""Detached Runtime Pack fixtures using the real materializer and manifest validators.

Native host bytes are type probes only; tests never execute them. Python verifier
entrypoints are copied from the repository and run through the test interpreter.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_test_source_provenance(
    root: Path,
    *,
    owner_repo: str = "vetcoders/vibecrafted",
    source_revision: str = "b" * 40,
) -> dict[str, object]:
    """Mint a test-only carrier for a detached fixture's current input tree."""
    provenance: dict[str, object] = {
        "schema": installer._SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": installer._distribution_manifest._distribution_tree_record(root),
    }
    carrier = root / "source-provenance.json"
    carrier.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    carrier.chmod(0o644)
    return provenance


_RUNTIME_GENERATION_FIXTURE_SOURCES = {
    Path("VERSION"): Path("VERSION"),
    Path("scripts/distribution_manifest.py"): Path("scripts/distribution_manifest.py"),
    Path("scripts/installer_brand.py"): Path("scripts/installer_brand.py"),
    Path("scripts/vetcoders_install.py"): Path("scripts/vetcoders_install.py"),
    Path("scripts/vibecrafted"): Path("scripts/vibecrafted"),
    Path(
        "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    ): Path("vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"),
    installer._RUNTIME_GENERATION_ENTRYPOINT: Path(
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
    ),
    Path("vibecrafted-core/vibecrafted_core/product_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/product_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/walkaround_runner.py"): Path(
        "vibecrafted-core/vibecrafted_core/walkaround_runner.py"
    ),
    Path(
        "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"
    ): Path("vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"),
    Path("vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"): Path(
        "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"
    ),
    Path("vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"): Path(
        "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
    ),
}


def seed_runtime_pack(
    payload: Path,
    *,
    version: str = "9.9.9+g12345678",
    frame_config: str | None = None,
    terminal_policy: str | None = None,
) -> Path:
    """Supply real manifest inputs; publication performs its own sealing."""
    for destination, source in _RUNTIME_GENERATION_FIXTURE_SOURCES.items():
        if (
            "generated" in destination.parts
            or destination == installer._RUNTIME_GENERATION_ENTRYPOINT
        ):
            continue
        target = payload / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source, target)
    for relative in (
        "vibecrafted-core/vibecrafted_core/vc_frame_staging.py",
        "vibecrafted-core/vibecrafted_core/runtime_paths.py",
        "scripts/vc-terminal-product-entry.sh",
        "scripts/vc-frame-product-entry.sh",
    ):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    for relative in (
        "vibecrafted-core/vibecrafted_core/config/vc-frame",
        "config/vc-terminal",
    ):
        shutil.copytree(REPO_ROOT / relative, payload / relative, dirs_exist_ok=True)
    for relative in (
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
        "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
        "config/alacritty/launch-primary-shell.zsh",
    ):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    for name in (
        "vibecrafted",
        "loct",
        "loctree-mcp",
        "aicx",
        "aicx-mcp",
        "prview",
        "screenscribe",
        "vc-server",
        "vc-server-supervisor",
        "vc-start",
        "vc-workflow",
    ):
        target = payload / "bin" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    python = payload / "bin/python3"
    python.write_text(f'#!/bin/sh\nexec {installer.shlex_quote(sys.executable)} "$@"\n')
    python.chmod(0o755)
    skills = payload / "vibecrafted-core/vibecrafted_core/skills"
    for name in ("vc-audit", "vc-implement"):
        target = skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n")
    (skills / "VERIFICATION_RULE.md").write_text("# Verification rule\n")
    (payload / "VERSION").write_text(version + "\n")
    for relative, content in (
        ("vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl", frame_config),
        ("config/vc-terminal/vibecrafted.toml", terminal_policy),
    ):
        if content is not None:
            (payload / relative).write_text(content, encoding="utf-8")
    # Bind the distribution input before adding native donor payload.
    _write_test_source_provenance(payload)
    for relative in ("bin/vc-terminal", "libexec/vc-frame"):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # A real native file, never launched; avoids accepting a shell as Frame.
        shutil.copyfile("/usr/bin/true", target)
        target.chmod(0o755)
    return payload
