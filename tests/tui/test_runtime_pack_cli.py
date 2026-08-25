from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

from vibecrafted_core.runtime_pack_contract import write_provenance

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts/install-runtime-pack.sh"
PACKAGER = REPO_ROOT / "scripts/package-runtime-pack.sh"
SOURCE_SHA = "1" * 40
TERMINAL_SHA = "2" * 40
FRAME_SHA = "3" * 40
VERSION = "4.3.0"


def _fake_runtime_payload(root: Path, capture: Path) -> None:
    (root / "bin").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    contract_dir = root / "vibecrafted-core/vibecrafted_core"
    contract_dir.mkdir(parents=True)
    (contract_dir / "__init__.py").write_text("", encoding="utf-8")
    (contract_dir / "runtime_pack_contract.py").write_bytes(
        (
            REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"
        ).read_bytes()
    )
    (root / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    python = root / "bin/python3"
    python.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "-m" ]]; then\n'
        f'  exec "{sys.executable}" "$@"\n'
        "fi\n"
        'printf "%s\\n" "$@" > "$CAPTURE"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (root / "scripts/vetcoders_install.py").write_text("# fixture\n", encoding="utf-8")
    capture.parent.mkdir(parents=True, exist_ok=True)


def _source_provenance(root: Path, revision: str = SOURCE_SHA) -> None:
    payload = {
        "schema": "vibecrafted.source-provenance.v2",
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": revision,
        "payload": {},
    }
    (root / "source-provenance.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def _sealed_archive(
    tmp_path: Path,
    payload: Path,
    *,
    name: str = "Vibecrafted_RuntimePack_fixture.tar.gz",
    source_revision: str = SOURCE_SHA,
) -> tuple[Path, Path]:
    archive = tmp_path / name
    _source_provenance(payload, source_revision)
    write_provenance(
        payload,
        carrier_basename=name,
        version=VERSION,
        platform="darwin",
        architecture="arm64",
        source_revision=source_revision,
        terminal_revision=TERMINAL_SHA,
        frame_revision=FRAME_SHA,
    )
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload, arcname="VibecraftedRuntime")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="utf-8"
    )
    private_key = tmp_path / "signing.key"
    public_key = tmp_path / "signing.pub"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "RSA", "-out", str(private_key)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_key),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(archive) + ".sig",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    return archive, public_key


def _run(
    *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--expected-version",
            VERSION,
            "--expected-platform",
            "darwin",
            "--expected-architecture",
            "arm64",
            *arguments,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_runtime_pack_rejects_directory_carrier(tmp_path: Path) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)

    result = _run("--pack", str(payload), env={"CAPTURE": str(capture)})

    assert result.returncode != 0
    assert "canonical .tar.gz carrier" in result.stderr
    assert not capture.exists()


def test_runtime_pack_rejects_app_as_carrier(tmp_path: Path) -> None:
    app = tmp_path / "Vibecrafted.app"
    payload = app / "Contents/Resources/runtime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    terminal = app / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    frame = app / "Contents/Helpers/vc-frame"
    for helper in (terminal, frame):
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\n", encoding="utf-8")
        helper.chmod(0o755)

    result = _run("--pack", str(app), env={"CAPTURE": str(capture)})

    assert result.returncode != 0
    assert "canonical .tar.gz carrier" in result.stderr
    assert not capture.exists()


def test_runtime_uninstall_uses_installed_generation_tool(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime_home = home / ".local/share/vibecrafted"
    generation = runtime_home / "releases/4.2.4+gfixture"
    capture = tmp_path / "argv"
    _fake_runtime_payload(generation, capture)
    (runtime_home / "tools").mkdir(parents=True)
    (runtime_home / "tools/vibecrafted-current").symlink_to(generation)
    (runtime_home / "install-receipt.json").write_text("{}\n", encoding="utf-8")

    result = _run(
        "--uninstall",
        "--dry-run",
        env={"HOME": str(home), "CAPTURE": str(capture)},
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(generation / "scripts/vetcoders_install.py"),
        "runtime-uninstall",
        "--dry-run",
    ]


def test_runtime_uninstall_is_idempotent_when_receipt_is_absent(tmp_path: Path) -> None:
    result = _run("--uninstall", env={"HOME": str(tmp_path / "home")})

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "absent"


def test_runtime_uninstall_recovers_from_pack_when_projection_is_missing(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime_home = home / ".local/share/vibecrafted"
    runtime_home.mkdir(parents=True)
    (runtime_home / "install-receipt.json").write_text("{}\n", encoding="utf-8")
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)

    archive, public_key = _sealed_archive(tmp_path, payload)
    result = _run(
        "--uninstall",
        "--pack",
        str(archive),
        env={
            "HOME": str(home),
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        next(
            line
            for line in capture.read_text(encoding="utf-8").splitlines()
            if line.endswith("scripts/vetcoders_install.py")
        ),
        "runtime-uninstall",
    ]


def test_runtime_pack_rejects_an_unresolvable_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing/RuntimePack.tar.gz"

    result = _run("--pack", str(missing))

    assert result.returncode != 0
    assert "cannot resolve Runtime Pack path" in result.stderr


def test_runtime_packager_emits_one_closed_root_and_checksum(tmp_path: Path) -> None:
    app = tmp_path / "Vibecrafted.app"
    runtime = app / "Contents/Resources/runtime"
    required = (
        "VERSION",
        "bin/python3",
        "bin/vibecrafted",
        "scripts/vc-frame-product-entry.sh",
        "scripts/vetcoders_install.py",
    )
    for relative in required:
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "bin/python3":
            path.write_text(
                f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n',
                encoding="utf-8",
            )
        else:
            path.write_text(
                f"{VERSION}\n" if relative == "VERSION" else "fixture\n",
                encoding="utf-8",
            )
        if relative.startswith("bin/"):
            path.chmod(0o755)
    contract_dir = runtime / "vibecrafted-core/vibecrafted_core"
    contract_dir.mkdir(parents=True)
    (contract_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py",
        contract_dir / "runtime_pack_contract.py",
    )
    _source_provenance(runtime)
    terminal = app / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    frame = app / "Contents/Helpers/vc-frame"
    for helper in (terminal, frame):
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\n", encoding="utf-8")
        helper.chmod(0o755)
    output = tmp_path / "Vibecrafted_RuntimePack_fixture.tar.gz"

    result = subprocess.run(
        [
            "bash",
            str(PACKAGER),
            "--app",
            str(app),
            "--output",
            str(output),
            "--source-revision",
            SOURCE_SHA,
            "--terminal-revision",
            TERMINAL_SHA,
            "--frame-revision",
            FRAME_SHA,
            "--version",
            VERSION,
            "--platform",
            "darwin",
            "--architecture",
            "arm64",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
        assert all(
            name == "VibecraftedRuntime" or name.startswith("VibecraftedRuntime/")
            for name in names
        )
        assert "VibecraftedRuntime/bin/vc-terminal" in names
        assert "VibecraftedRuntime/bin/vc-frame" in names
        assert "VibecraftedRuntime/libexec/vc-frame" in names
        assert "VibecraftedRuntime/runtime-pack-provenance.json" in names
        assert not any(
            member.issym() or member.islnk() for member in archive.getmembers()
        )
    expected = hashlib.sha256(output.read_bytes()).hexdigest()
    assert (
        output.with_suffix(output.suffix + ".sha256")
        .read_text(encoding="utf-8")
        .split()[0]
        == expected
    )

    # The same canonical writer also accepts a release-built Linux payload;
    # consumers never compile it. Missing helpers still fail through the
    # common required-file contract below.
    for relative in ("bin/vc-terminal", "bin/vc-frame", "libexec/vc-frame"):
        helper = runtime / relative
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\n", encoding="utf-8")
        helper.chmod(0o755)
    linux_output = tmp_path / "Vibecrafted_RuntimePack_fixture-linux-arm64.tar.gz"
    linux = subprocess.run(
        [
            "bash",
            str(PACKAGER),
            "--payload-root",
            str(runtime),
            "--output",
            str(linux_output),
            "--source-revision",
            SOURCE_SHA,
            "--terminal-revision",
            TERMINAL_SHA,
            "--frame-revision",
            FRAME_SHA,
            "--version",
            VERSION,
            "--platform",
            "linux",
            "--architecture",
            "arm64",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert linux.returncode == 0, linux.stderr
    with tarfile.open(linux_output, "r:gz") as archive:
        provenance = json.load(
            archive.extractfile("VibecraftedRuntime/runtime-pack-provenance.json")
        )
    assert provenance["platform"] == "linux"
    assert provenance["architecture"] == "arm64"


def test_runtime_pack_archive_requires_release_signature(tmp_path: Path) -> None:
    archive = tmp_path / "Vibecrafted_RuntimePack_fixture.tar.gz"
    root = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(root, capture)
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(root, arcname="VibecraftedRuntime")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{checksum}  {archive.name}\n", encoding="utf-8"
    )

    result = _run("--pack", str(archive), env={"CAPTURE": str(capture)})

    assert result.returncode != 0
    assert "Runtime Pack signature is missing" in result.stderr
    assert not capture.exists()


def test_signed_archive_bootstraps_without_ambient_python_and_cleans_temp(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    ambient_python = fake_bin / "python3"
    ambient_python.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    ambient_python.chmod(0o755)
    extraction_home = tmp_path / "extract"
    extraction_home.mkdir()

    result = _run(
        "--pack",
        str(archive),
        env={
            "CAPTURE": str(capture),
            "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(extraction_home),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert arguments[1:3] == ["runtime-install", "--payload-root"]
    assert arguments[3].startswith(str(extraction_home))
    assert arguments[0] == f"{arguments[3]}/scripts/vetcoders_install.py"
    assert not any(extraction_home.iterdir())


def test_signed_carrier_rejects_expected_source_mismatch_before_installer(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)

    result = _run(
        "--pack",
        str(archive),
        "--expected-source-revision",
        "4" * 40,
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "internal provenance verification failed" in result.stderr
    assert not capture.exists()


def test_signed_carrier_rejects_expected_donor_mismatch_before_installer(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)

    result = _run(
        "--pack",
        str(archive),
        "--expected-terminal-revision",
        "5" * 40,
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "internal provenance verification failed" in result.stderr
    assert not capture.exists()


def test_signed_carrier_rejects_selected_platform_mismatch_before_installer(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)

    result = _run(
        "--pack",
        str(archive),
        "--expected-platform",
        "linux",
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "internal provenance verification failed" in result.stderr
    assert not capture.exists()
