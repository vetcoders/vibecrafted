"""Exercise the release embedding function and the detached installer archive."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack
from vibecrafted_core.runtime_pack_contract import LINUX_EXECUTABLES, write_provenance

from tests.tui.test_runtime_pack_rescue import _seal_runtime_pack_for_admission
from tests.tui.test_runtime_pack_rescue_wrapper import _sign_payload_archive

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "scripts/build-vibecrafted-release.sh"


def _assert_source_closure(directory: Path) -> None:
    installer = directory / "install-runtime-pack.sh"
    references = re.findall(
        r'^\s*(?:\.|source)\s+"\$SCRIPT_DIR/([^"\n]+)"',
        installer.read_text(),
        re.MULTILINE,
    )
    assert references, "installer source declarations must be inspected"
    for relative in references:
        assert (directory / relative).is_file(), f"missing sourced file: {relative}"
        assert (directory / relative).read_bytes() == (
            REPO_ROOT / "scripts" / relative
        ).read_bytes()


def test_dmg_embedding_contains_installer_source_closure(tmp_path: Path) -> None:
    # Run the real release staging boundary, without building/signing an app.
    function = re.search(
        r"^embed_runtime_pack\(\) \{\n.*?^\}",
        BUILDER.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert function
    resource = tmp_path / "App/Contents/Resources/runtime-pack"
    pack = tmp_path / "pack.tar.gz"
    for suffix in ("", ".sha256", ".sig"):
        Path(str(pack) + suffix).write_text("fixture" + suffix)
    env = {
        **os.environ,
        "SOURCE_ROOT": str(REPO_ROOT),
        "RUNTIME_VERSION": "test",
        "RUNTIME_PACK_RESOURCE_DIR": str(resource),
        "RUNTIME_PACK": str(pack),
        "RUNTIME_PACK_CHECKSUM": str(pack) + ".sha256",
        "RUNTIME_PACK_SIGNATURE": str(pack) + ".sig",
        "EMBEDDED_RUNTIME_PACK": str(resource / pack.name),
        "EMBEDDED_RUNTIME_PACK_CHECKSUM": str(resource / pack.name) + ".sha256",
        "EMBEDDED_RUNTIME_PACK_SIGNATURE": str(resource / pack.name) + ".sig",
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\nlog() { :; }\n"
            "verify_runtime_pack_macho_signatures() { :; }\n"
            + function.group()
            + "\nembed_runtime_pack\n",
        ],
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    _assert_source_closure(resource)


def _build_kit(tmp_path: Path, public_key: Path | None = None) -> Path:
    source = REPO_ROOT
    if public_key is not None:
        # A release-shaped source snapshot using an ephemeral test signing key.
        # No production key or installed runtime is touched.
        source = tmp_path / "source"
        for relative in (
            "Makefile",
            "scripts/install-runtime-pack.sh",
            "scripts/package-installer-kit.sh",
            "docs/INSTALLER_KIT.md",
        ):
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, target)
        shutil.copytree(REPO_ROOT / "scripts/lib", source / "scripts/lib")
        trust = source / "vibecrafted-core/vibecrafted_core/trust"
        trust.mkdir(parents=True)
        shutil.copy2(public_key, trust / "vibecrafted-signing-v1.pub")
    archive = tmp_path / "kit.tar.gz"
    result = subprocess.run(
        ["make", "-C", str(source), "installer-kit", f"INSTALLER_KIT_OUTPUT={archive}"],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    detached = tmp_path / "detached"
    detached.mkdir()
    subprocess.run(
        ["tar", "-xzf", str(archive), "-C", str(detached)],
        check=True,
        capture_output=True,
    )
    if public_key is not None:
        shutil.rmtree(source)
    return detached / "Vibecrafted_InstallerKit"


def test_make_installer_kit_contains_source_closure_and_trust(tmp_path: Path) -> None:
    kit = _build_kit(tmp_path)
    _assert_source_closure(kit)
    assert (kit / "vibecrafted-signing-v1.pub").read_bytes() == (
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
    ).read_bytes()
    assert (kit / "README.md").read_bytes() == (
        REPO_ROOT / "docs/INSTALLER_KIT.md"
    ).read_bytes()
    assert os.access(kit / "install-runtime-pack.sh", os.X_OK)


@pytest.mark.parametrize(
    "key_mode", ["adjacent", "adjacent-before-env", "env", "repo", "wrong-adjacent"]
)
def test_detached_kit_installs_signed_pack(tmp_path: Path, key_mode: str) -> None:
    payload = seed_runtime_pack(tmp_path / "payload", version="9.9.9+kit")
    _seal_runtime_pack_for_admission(payload)
    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "x64"
    if platform.system() == "Linux":
        # The Linux carrier has a closed inventory in addition to provenance.
        # These are disposable installer fixtures, not executable/build proof.
        executables = []
        for name in sorted(LINUX_EXECUTABLES):
            executable = payload / "bin" / name
            if not executable.exists():
                executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                executable.chmod(0o755)
            executables.append(
                {
                    "name": name,
                    "path": f"bin/{name}",
                    "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "version_argv": ["--version"],
                    "version_output": "installer test fixture",
                    "source_url": "https://example.invalid/installer-fixture",
                    "source_revision": "b" * 40,
                    "source_archive_sha256": "a" * 64,
                    "target": (
                        "aarch64-unknown-linux-gnu"
                        if arch == "arm64"
                        else "x86_64-unknown-linux-gnu"
                    ),
                    "license": "test fixture",
                }
            )
        (payload / "runtime-inventory.json").write_text(
            json.dumps(
                {
                    "schema": "io.vetcoders.vibecrafted.runtime-inventory.v1",
                    "platform": f"linux-{arch}",
                    "architecture": arch,
                    "executables": executables,
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    carrier = "Vibecrafted_RuntimePack_rescue-fixture.tar.gz"
    write_provenance(
        payload,
        carrier_basename=carrier,
        version="9.9.9+kit",
        platform=f"{platform.system().lower()}-{arch}",
        architecture=arch,
        source_revision="b" * 40,
        terminal_revision="2" * 40,
        frame_revision="3" * 40,
    )
    archive, public_key = _sign_payload_archive(tmp_path / "signed", payload)
    kit = _build_kit(tmp_path, public_key)
    # Only the signed carrier and unpacked kit survive. The Python installer and
    # manifest validator run from the carrier, not from the original payload.
    shutil.rmtree(payload)
    home = tmp_path / "fresh-home"
    home.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_", "XDG_", "PYTHON"))
    }
    env.update(HOME=str(home), VIBECRAFTED_HOME=str(home / ".vibecrafted"))
    # Exercise the stable-Xcode guard without selecting a host toolchain.
    developer = tmp_path / "Xcode.app/Contents/Developer"
    developer.mkdir(parents=True)
    env["DEVELOPER_DIR"] = str(developer)
    adjacent = kit / "vibecrafted-signing-v1.pub"
    if key_mode == "adjacent-before-env":
        env["VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY"] = str(tmp_path / "missing-key")
    elif key_mode == "env":
        adjacent.unlink()
        env["VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY"] = str(public_key)
    elif key_mode == "repo":
        adjacent.unlink()
        trust = kit.parent / "vibecrafted-core/vibecrafted_core/trust"
        trust.mkdir(parents=True)
        shutil.copy2(public_key, trust / adjacent.name)
    elif key_mode == "wrong-adjacent":
        adjacent.write_text("not a signing key\n")
        env["VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY"] = str(public_key)
    result = subprocess.run(
        ["bash", "./install-runtime-pack.sh", "--pack", str(archive)],
        cwd=kit,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=90,
    )
    active = home / ".local/share/vibecrafted/active.json"
    if key_mode == "wrong-adjacent":
        assert result.returncode != 0
        assert "signature verification failed" in result.stderr
        assert not active.exists()
    else:
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert active.is_file(), result.stdout
        receipt = json.loads(active.read_text())
        assert "9.9.9+kit" in json.dumps(receipt)
        assert (home / ".local/bin/vibecrafted").is_file()
