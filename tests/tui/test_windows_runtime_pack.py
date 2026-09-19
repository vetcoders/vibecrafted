"""Native Windows Runtime Pack contract: paths, install, negatives, authority."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from argparse import Namespace
from pathlib import Path

import pytest
from vibecrafted_core.runtime_pack_contract import (
    WINDOWS_X64_MANDATORY_EXECUTABLES,
    RuntimePackContractError,
    _windows_x64_inventory,
)
from vibecrafted_core.runtime_paths import (
    GenerationResolutionError,
    agent_tool_search_path,
    canonical_vibecrafted_home,
    canonical_vibecrafted_launcher_bin,
    canonical_vibecrafted_runtime_home,
    launcher_name,
    resolve_active_generation,
)

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SHA = "1" * 40
VERSION = "4.3.1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows Runtime Pack contract"
)


@pytest.fixture(autouse=True)
def _isolate_windows_operator_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Never write the operator profile; exercise native LOCALAPPDATA layout."""
    profile = tmp_path_factory.mktemp("win-profile")
    local = profile / "AppData" / "Local"
    roaming = profile / "AppData" / "Roaming"
    local.mkdir(parents=True)
    roaming.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("VIBECRAFTED_SKIP_USER_PATH", "1")
    for name in (
        "VIBECRAFTED_HOME",
        "VIBECRAFTED_RUNTIME_HOME",
        "VIBECRAFTED_LAUNCHER_BIN",
        "VIBECRAFTED_TOOLS_HOME",
        "VIBECRAFTED_RUNTIME_BIN",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_canonical_windows_paths_use_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = tmp_path / "Users" / "operator"
    local = profile / "AppData" / "Local"
    roaming = profile / "AppData" / "Roaming"
    for directory in (profile, local, roaming):
        directory.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    for name in (
        "VIBECRAFTED_HOME",
        "VIBECRAFTED_RUNTIME_HOME",
        "VIBECRAFTED_LAUNCHER_BIN",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
    ):
        monkeypatch.delenv(name, raising=False)

    assert canonical_vibecrafted_runtime_home() == local / "Vibecrafted"
    assert canonical_vibecrafted_launcher_bin() == local / "Vibecrafted" / "bin"
    assert canonical_vibecrafted_home() == local / "Vibecrafted" / "home"
    assert str(canonical_vibecrafted_runtime_home()).startswith(str(tmp_path))
    assert launcher_name("vibecrafted") == "vibecrafted.cmd"


def test_agent_tool_search_path_uses_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = tmp_path / "Users" / "fresh"
    local = profile / "AppData" / "Local"
    runtime_bin = local / "Vibecrafted" / "bin"
    rogue = tmp_path / "rogue-bin"
    runtime_bin.mkdir(parents=True)
    rogue.mkdir()
    monkeypatch.delenv("VIBECRAFTED_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("VIBECRAFTED_RUNTIME_BIN", raising=False)
    monkeypatch.delenv("VIBECRAFTED_LAUNCHER_BIN", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    entries = agent_tool_search_path(
        {
            "USERPROFILE": str(profile),
            "HOME": str(profile),
            "LOCALAPPDATA": str(local),
            "PATH": str(rogue),
        }
    ).split(os.pathsep)
    assert str(runtime_bin) in entries
    # PR88 keeps inherited PATH except owned generation bins.
    assert str(rogue) in entries
    system_root = Path(os.environ.get("SystemRoot") or r"C:\Windows")
    assert str(system_root / "System32") in entries


def test_windows_inventory_requires_mandatory_and_declares_unsupported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "payload"
    (root / "bin").mkdir(parents=True)
    records = []
    for name in sorted(WINDOWS_X64_MANDATORY_EXECUTABLES):
        relative = "bin/python.exe" if name == "python" else f"bin/{name}.exe"
        path = root / relative
        path.write_bytes(name.encode("utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": digest,
                "version_argv": ["--version"],
                "version_output": f"{name} 0",
                "source_url": "https://github.com/vetcoders/vibecrafted",
                "source_revision": SOURCE_SHA,
                "source_archive_sha256": "a" * 64,
                "target": "x86_64-pc-windows-msvc",
                "license": "MIT",
            }
        )
    unsupported = [
        {
            "name": "prview",
            "classification": "release-blocker",
            "reason": "no Windows artifact",
        },
        {
            "name": "screenscribe",
            "classification": "release-blocker",
            "reason": "no Windows artifact",
        },
        {
            "name": "voc",
            "classification": "limited-platform-scope",
            "reason": "no Windows artifact",
        },
        {
            "name": "vc-start",
            "classification": "limited-platform-scope",
            "reason": "no Windows artifact",
        },
        {
            "name": "vc-frame",
            "classification": "limited-platform-scope",
            "reason": "no Windows artifact",
        },
        {
            "name": "vc-terminal",
            "classification": "limited-platform-scope",
            "reason": "no Windows artifact",
        },
        {
            "name": "vc-server-supervisor",
            "classification": "limited-platform-scope",
            "reason": "no Windows artifact",
        },
    ]
    inventory = {
        "schema": "io.vetcoders.vibecrafted.runtime-inventory.v1",
        "platform": "win32-x64",
        "architecture": "x64",
        "executables": records,
        "unsupported": unsupported,
    }
    (root / "runtime-inventory.json").write_text(
        json.dumps(inventory, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    loaded = _windows_x64_inventory(root)
    assert {item["name"] for item in loaded["executables"]} == set(
        WINDOWS_X64_MANDATORY_EXECUTABLES
    )
    assert {item["name"] for item in loaded["unsupported"]} == {
        "prview",
        "screenscribe",
        "voc",
        "vc-start",
        "vc-frame",
        "vc-terminal",
        "vc-server-supervisor",
    }


def test_windows_inventory_refuses_missing_mandatory(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    (root / "bin").mkdir(parents=True)
    inventory = {
        "schema": "io.vetcoders.vibecrafted.runtime-inventory.v1",
        "platform": "win32-x64",
        "architecture": "x64",
        "executables": [],
        "unsupported": [],
    }
    (root / "runtime-inventory.json").write_text(
        json.dumps(inventory, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(RuntimePackContractError, match="mandatory"):
        _windows_x64_inventory(root)


def _write_file(path: Path, body: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _windows_payload(root: Path, *, include_server: bool = True) -> Path:
    payload = root / "runtime-pack"
    names = [
        "loct",
        "loctree",
        "loctree-mcp",
        "loctree-lsp",
        "aicx",
        "aicx-mcp",
    ]
    if include_server:
        names.append("vc-server")
    for name in names:
        _write_file(payload / "bin" / f"{name}.exe", "MZ")
    _write_file(payload / "bin" / "python.exe", "MZ")
    _write_file(
        payload / "bin" / "vibecrafted.cmd",
        "@echo off\r\necho vibecrafted fixture\r\n",
    )
    _write_file(payload / "vibecrafted-core/vibecrafted_core/deck/vibecrafted")
    _write_file(payload / "VERSION", f"{VERSION}+g12345678\n")
    skills = payload / "vibecrafted-core/vibecrafted_core/skills/vc-audit"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# vc-audit\n", encoding="utf-8")
    frame = payload / "vibecrafted-core/vibecrafted_core/config/vc-frame"
    frame.mkdir(parents=True)
    (frame / "config.kdl").write_text("// frame\n", encoding="utf-8")
    _write_file(payload / "scripts/vetcoders_install.py", "# installer\n")
    _write_file(payload / "scripts/install-runtime-pack.ps1", "# ps1\n")
    _write_file(payload / "scripts/distribution_manifest.py", "# dist\n")
    _write_file(payload / "scripts/installer_brand.py", "# brand\n")
    _write_file(payload / "scripts/vibecrafted", "#!/bin/sh\n")
    return payload


def _mute_generation_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    def materialize(root: Path) -> None:
        destination = (
            root / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame"
        )
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "config.kdl").write_text("// generated\n", encoding="utf-8")
        (destination / "layouts").mkdir()
        (destination / "themes").mkdir()

    def provenance(_root: Path) -> dict:
        return {
            "schema": "vibecrafted.source-provenance.v2",
            "owner_repo": "vetcoders/vibecrafted",
            "source_revision": SOURCE_SHA,
            "payload": {
                "schema": "vibecrafted.distribution-tree.v1",
                "algorithm": "sha256",
                "tree_sha256": "2" * 64,
                "entry_count": 1,
            },
        }

    def write_manifest(root: Path, **_kwargs: object) -> None:
        hashes = {}
        for relative in installer._RUNTIME_GENERATION_REQUIRED_HASHES:
            path = root / relative
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else "a" * 64
            )
            hashes[relative.as_posix()] = digest
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        payload = {
            "schema": installer._RUNTIME_GENERATION_MANIFEST_SCHEMA,
            "version": version,
            "source_fingerprint": "2" * 64,
            "owner_repo": "vetcoders/vibecrafted",
            "source_revision": SOURCE_SHA,
            "source_payload": provenance(root)["payload"],
            "entrypoint": installer._RUNTIME_GENERATION_ENTRYPOINT.as_posix(),
            "hashes": hashes,
        }
        (root / "runtime-manifest.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(installer, "_materialize_vc_frame_generation", materialize)
    monkeypatch.setattr(installer, "load_source_provenance", provenance)
    monkeypatch.setattr(installer, "_write_runtime_generation_manifest", write_manifest)
    monkeypatch.setattr(
        installer, "_runtime_generation_payload_errors", lambda _root: []
    )
    monkeypatch.setattr(
        installer, "_teardown_owned_runtime_for_uninstall", lambda *_a, **_k: ()
    )


def test_windows_install_uninstall_round_trip_and_second_fresh_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _mute_generation_binding(monkeypatch)
    payload = _windows_payload(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=None,
        terminal_host=None,
        frame_helper=None,
    )
    assert installer.cmd_runtime_install(args) == 0
    installed = json.loads(capsys.readouterr().out)
    runtime_home = installer.vibecrafted_runtime_home()
    generation = Path(installed["root"])
    assert generation.is_dir()
    assert (runtime_home / "active.json").is_file()
    current = runtime_home / "tools" / "vibecrafted-current"
    assert current.exists()
    junction = getattr(current, "is_junction", None)
    assert current.is_symlink() or (callable(junction) and junction())
    assert resolve_active_generation(runtime_home) == generation.resolve()
    launcher = installer.vibecrafted_launcher_bin() / "vibecrafted.cmd"
    assert launcher.is_file()
    assert "VIBECRAFTED_RUNTIME_ROOT" in launcher.read_text(encoding="utf-8")
    walkaround = (
        installer.vibecrafted_launcher_bin() / "verify-vibecrafted-walkaround.cmd"
    )
    assert walkaround.is_file()
    walkaround_bytes = walkaround.read_bytes()
    assert (
        installer.SECURE_WALKAROUND_LAUNCHER_MARKER.encode("ascii") in walkaround_bytes
    )
    expected_walkaround = installer._secure_walkaround_launcher_contents(
        current,
        generation / "bin" / "python.exe",
        launcher_path=walkaround,
    )
    assert walkaround_bytes == expected_walkaround
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()
    operator_note = installer.vibecrafted_home() / "operator-owned.txt"
    operator_note.parent.mkdir(parents=True, exist_ok=True)
    operator_note.write_text("keep\n", encoding="utf-8")
    receipt = json.loads(
        (runtime_home / installer.RUNTIME_INSTALL_RECEIPT).read_text(encoding="utf-8")
    )
    if str(installer.vibecrafted_home()) not in receipt.get("owned_dirs", []):
        operator_keep = True
    else:
        operator_keep = False
    assert (
        installer.cmd_runtime_uninstall(Namespace(dry_run=False, emit_result=True)) == 0
    )
    removed = json.loads(capsys.readouterr().out)
    assert removed["status"] == "removed"
    assert not (runtime_home / "active.json").exists()
    with pytest.raises(GenerationResolutionError):
        resolve_active_generation(runtime_home)
    if operator_keep:
        assert operator_note.is_file()
    assert installer.cmd_runtime_install(args) == 0
    capsys.readouterr()
    assert (installer.vibecrafted_runtime_home() / "active.json").is_file()


def test_missing_mandatory_server_does_not_publish_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mute_generation_binding(monkeypatch)
    payload = _windows_payload(tmp_path, include_server=False)
    args = Namespace(
        payload_root=str(payload),
        app_root=None,
        terminal_host=None,
        frame_helper=None,
    )
    with pytest.raises(RuntimeError, match="incomplete"):
        installer.cmd_runtime_install(args)
    runtime_home = installer.vibecrafted_runtime_home()
    assert not (runtime_home / "active.json").exists()
    with pytest.raises(GenerationResolutionError):
        resolve_active_generation(runtime_home)


def test_split_brain_is_fail_closed(tmp_path: Path) -> None:
    runtime_home = installer.vibecrafted_runtime_home()
    releases = runtime_home / "releases"
    one = releases / "a"
    two = releases / "b"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    (runtime_home / "active.json").write_text(
        json.dumps(
            {
                "schema": "vibecrafted.active-runtime.v1",
                "version": "a",
                "runtime_root": str(one),
                "app_root": "",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    tools = runtime_home / "tools"
    tools.mkdir(parents=True)
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(tools / "vibecrafted-current"), str(two)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("cannot create directory junction in this environment")
    with pytest.raises(GenerationResolutionError, match="split-brain"):
        resolve_active_generation(runtime_home)


def test_ps1_installer_refuses_bad_checksum_before_extract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "Vibecrafted_RuntimePack_fixture-win32-x64.tar.gz"
    payload = tmp_path / "VibecraftedRuntime"
    payload.mkdir()
    (payload / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(payload, arcname="VibecraftedRuntime")
    (tmp_path / f"{archive.name}.sha256").write_text(
        f"{'0' * 64}  {archive.name}\n", encoding="utf-8"
    )
    (tmp_path / f"{archive.name}.sig").write_bytes(b"\0" * 64)
    result = subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(REPO_ROOT / "scripts" / "install-runtime-pack.ps1"),
            "-Pack",
            str(archive),
            "-ExpectedVersion",
            VERSION,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "VIBECRAFTED_SKIP_USER_PATH": "1",
            "VIBECRAFTED_RUNTIME_HOME": str(tmp_path / "runtime"),
        },
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "checksum" in combined.lower()
    assert not (tmp_path / "runtime" / "active.json").exists()


def test_ps1_installer_refuses_incompatible_architecture(tmp_path: Path) -> None:
    archive = tmp_path / "Vibecrafted_RuntimePack_fixture-win32-arm64.tar.gz"
    archive.write_bytes(b"not-a-tar")
    (tmp_path / f"{archive.name}.sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n",
        encoding="utf-8",
    )
    (tmp_path / f"{archive.name}.sig").write_bytes(b"\0" * 64)
    result = subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(REPO_ROOT / "scripts" / "install-runtime-pack.ps1"),
            "-Pack",
            str(archive),
            "-ExpectedArchitecture",
            "arm64",
            "-ExpectedVersion",
            VERSION,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "architecture" in (result.stdout + result.stderr).lower()


def test_windows_packager_uses_python_tarfile_not_git_tar() -> None:
    packager = (REPO_ROOT / "scripts" / "package-runtime-pack.ps1").read_text(
        encoding="utf-8"
    )
    installer = (REPO_ROOT / "scripts" / "install-runtime-pack.ps1").read_text(
        encoding="utf-8"
    )
    assert "tarfile" in packager
    assert "Get-Command tar" not in packager
    assert 'Copy-Item -LiteralPath (Join-Path $payload "*")' not in packager
    assert "implausibly small" in packager
    assert "Get-WindowsTar" in installer
    assert r"System32\tar.exe" in installer
    assert "& tar -tzf" not in installer
    assert "& tar -xzf" not in installer


def test_current_without_active_json_is_fail_closed() -> None:
    runtime_home = installer.vibecrafted_runtime_home()
    generation = runtime_home / "releases" / "orphan"
    generation.mkdir(parents=True)
    tools = runtime_home / "tools"
    tools.mkdir(parents=True)
    completed = subprocess.run(
        [
            "cmd",
            "/c",
            "mklink",
            "/J",
            str(tools / "vibecrafted-current"),
            str(generation),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("cannot create directory junction in this environment")
    with pytest.raises(GenerationResolutionError, match="without active.json"):
        resolve_active_generation(runtime_home)


def test_stale_active_json_refuses_install_without_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mute_generation_binding(monkeypatch)
    runtime_home = installer.vibecrafted_runtime_home()
    runtime_home.mkdir(parents=True, exist_ok=True)
    (runtime_home / "active.json").write_text("{not-json\n", encoding="utf-8")
    payload = _windows_payload(tmp_path)
    args = Namespace(
        payload_root=str(payload),
        app_root=None,
        terminal_host=None,
        frame_helper=None,
    )
    with pytest.raises(RuntimeError, match="stale or split-brain"):
        installer.cmd_runtime_install(args)
    raw = (runtime_home / "active.json").read_text(encoding="utf-8")
    assert raw.startswith("{not-json")
    with pytest.raises(GenerationResolutionError):
        resolve_active_generation(runtime_home)


def test_ps1_installer_refuses_bad_signature_before_extract(tmp_path: Path) -> None:
    archive = tmp_path / "Vibecrafted_RuntimePack_fixture-win32-x64.tar.gz"
    payload = tmp_path / "VibecraftedRuntime"
    payload.mkdir()
    (payload / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(payload, arcname="VibecraftedRuntime")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8"
    )
    (tmp_path / f"{archive.name}.sig").write_bytes(b"\0" * 64)
    result = subprocess.run(
        [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(REPO_ROOT / "scripts" / "install-runtime-pack.ps1"),
            "-Pack",
            str(archive),
            "-ExpectedVersion",
            VERSION,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "VIBECRAFTED_SKIP_USER_PATH": "1",
            "VIBECRAFTED_RUNTIME_HOME": str(tmp_path / "runtime"),
        },
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "signature" in combined or "checksum" in combined
    assert not (tmp_path / "runtime" / "active.json").exists()


def test_windows_inventory_accepts_screenscribe_cmd(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    (root / "bin").mkdir(parents=True)
    records = []
    for name in sorted(WINDOWS_X64_MANDATORY_EXECUTABLES):
        relative = "bin/python.exe" if name == "python" else f"bin/{name}.exe"
        path = root / relative
        path.write_bytes(name.encode("utf-8"))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "name": name,
                "path": relative,
                "sha256": digest,
                "version_argv": ["--version"],
                "version_output": f"{name} 0",
                "source_url": "https://github.com/vetcoders/vibecrafted",
                "source_revision": SOURCE_SHA,
                "source_archive_sha256": "a" * 64,
                "target": "x86_64-pc-windows-msvc",
                "license": "MIT",
            }
        )
    screenscribe = root / "bin" / "screenscribe.cmd"
    screenscribe.write_text("@echo off\r\n", encoding="utf-8")
    records.append(
        {
            "name": "screenscribe",
            "path": "bin/screenscribe.cmd",
            "sha256": hashlib.sha256(screenscribe.read_bytes()).hexdigest(),
            "version_argv": ["--version"],
            "version_output": "screenscribe 0.1.19",
            "source_url": "https://github.com/vetcoders/vibecrafted",
            "source_revision": SOURCE_SHA,
            "source_archive_sha256": "a" * 64,
            "target": "x86_64-pc-windows-msvc",
            "license": "MIT",
        }
    )
    unsupported = [
        {
            "name": name,
            "classification": classification,
            "reason": "no Windows artifact",
        }
        for name, classification in (
            ("prview", "release-blocker"),
            ("voc", "limited-platform-scope"),
            ("vc-start", "limited-platform-scope"),
            ("vc-frame", "limited-platform-scope"),
            ("vc-terminal", "limited-platform-scope"),
            ("vc-server-supervisor", "limited-platform-scope"),
        )
    ]
    inventory = {
        "schema": "io.vetcoders.vibecrafted.runtime-inventory.v1",
        "platform": "win32-x64",
        "architecture": "x64",
        "executables": records,
        "unsupported": unsupported,
    }
    (root / "runtime-inventory.json").write_text(
        json.dumps(inventory, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    loaded = _windows_x64_inventory(root)
    names = {item["name"] for item in loaded["executables"]}
    assert "screenscribe" in names
    assert "prview" in {item["name"] for item in loaded["unsupported"]}


def test_windows_release_key_probe_does_not_require_usr_bin() -> None:
    probe = installer._openssl_for_release_key_probe()
    assert probe is None or probe.is_file()
    if probe is not None:
        assert probe.as_posix() != "/usr/bin/openssl"


def test_path_fingerprint_does_not_raise_on_posix_unc_lookalike() -> None:
    installer._path_fingerprint(Path("/console/open/"))


def test_atomic_bytes_file_writes_on_windows(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "snapshot.bin"
    installer._atomic_bytes_file(target, b"runtime-verifier\n", mode=0o600)
    assert target.read_bytes() == b"runtime-verifier\n"


def test_capture_runtime_bound_file_ignores_windows_ctime_skew(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deck-vibecrafted"
    path.write_bytes(b"#!/bin/bash\n" + b"x" * 4096)
    captured = installer._capture_runtime_bound_file(path)
    assert captured == path.read_bytes()


def test_source_provenance_loads_canonical_bytes(tmp_path: Path) -> None:
    from scripts import distribution_manifest as manifest

    record = {
        "schema": manifest.SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": SOURCE_SHA,
        "payload": {
            "schema": manifest.DISTRIBUTION_TREE_SCHEMA,
            "algorithm": manifest.DISTRIBUTION_TREE_ALGORITHM,
            "tree_sha256": "a" * 64,
            "entry_count": 1,
        },
    }
    path = tmp_path / manifest.SOURCE_PROVENANCE_FILE
    path.write_bytes(
        (json.dumps(record, sort_keys=True, indent=2) + "\n").encode("ascii")
    )
    loaded = manifest.load_source_provenance(tmp_path)
    assert loaded == record
    if sys.platform != "win32":
        path.chmod(0o666)
        with pytest.raises(manifest.ManifestError, match="canonical mode 0644"):
            manifest.load_source_provenance(tmp_path)


def test_windows_entrypoint_renderer_writes_cmd_not_posix_shim(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project.scripts]\ndemo-cli = "demo_module:main"\n',
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/render-python-entrypoint-launchers.py"),
            "--pyproject",
            str(pyproject),
            "--bin-dir",
            str(bin_dir),
            "--windows",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    cmd = bin_dir / "demo-cli.cmd"
    text = cmd.read_text(encoding="ascii")
    assert "python.exe" in text
    assert "VIBECRAFTED_DECLARED_LAUNCHER" in text
    assert "PYTHONIOENCODING" in text
    assert not (bin_dir / "demo-cli").exists()


def test_find_launcher_wrapper_resolves_cmd_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "vc-agents.cmd").write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(installer, "_launcher_bin_dirs", lambda: [bindir])
    found = installer._find_launcher_wrapper("vc-agents")
    assert found == bindir / "vc-agents.cmd"


def test_symlink_target_compares_junctions_without_extended_prefix(
    tmp_path: Path,
) -> None:
    generation = tmp_path / "releases" / "a"
    generation.mkdir(parents=True)
    link = tmp_path / "tools" / "vibecrafted-current"
    link.parent.mkdir(parents=True)
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(generation.resolve())],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("cannot create directory junction in this environment")
    assert os.readlink(link).startswith("\\\\?\\")
    assert installer._symlink_target(link) == generation.resolve()
    assert installer._is_owned_pointer(link)


def test_process_image_reports_current_interpreter() -> None:
    from vibecrafted_core.windows_server import _process_image

    image = _process_image(os.getpid())
    assert image
    assert Path(image).name.lower().startswith("python")
