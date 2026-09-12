from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from vibecrafted_core.runtime_pack_contract import (
    REQUIRED_FOUNDATION_EXECUTABLES,
    RuntimePackContractError,
    verify_provenance,
    write_provenance,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts/install-runtime-pack.sh"
PACKAGER = REPO_ROOT / "scripts/package-runtime-pack.sh"
SOURCE_SHA = "1" * 40
TERMINAL_SHA = "2" * 40
FRAME_SHA = "3" * 40
VERSION = "4.3.0"


def _foundation_manifest(root: Path) -> None:
    files: dict[str, str] = {}
    for name in sorted(REQUIRED_FOUNDATION_EXECUTABLES):
        executable = root / "bin" / name
        if not executable.exists():
            executable.write_text(f"#!/bin/sh\n# {name} fixture\n", encoding="utf-8")
            executable.chmod(0o755)
        files[name] = hashlib.sha256(executable.read_bytes()).hexdigest()
    payload = {
        "schema": "io.vetcoders.vibecrafted.runtime-foundations.v1",
        "versions": {"aicx": "fixture", "loctree": "fixture", "prview": "fixture"},
        "source_revisions": {},
        "source_archives": {},
        "licenses": {},
        "files": files,
    }
    (root / "runtime-foundations.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
        "export PYTHONDONTWRITEBYTECODE=1\n"
        'if [[ "${1:-}" == "-m" ]]; then\n'
        f'  exec "{sys.executable}" "$@"\n'
        "fi\n"
        'printf "%s\\n" "$@" > "$CAPTURE"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    vc_start = root / "bin/vc-start"
    vc_start.write_text("#!/bin/sh\n", encoding="utf-8")
    vc_start.chmod(0o755)
    (root / "scripts/vetcoders_install.py").write_text("# fixture\n", encoding="utf-8")
    launcher = root / "scripts/vibecrafted"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    _seed_vc_frame_product_payload(root)
    _foundation_manifest(root)
    capture.parent.mkdir(parents=True, exist_ok=True)


def _seed_vc_frame_product_payload(root: Path) -> None:
    wrapper = root / "bin/vc-frame"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    native = root / "libexec/vc-frame"
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
    native.chmod(0o755)
    config = root / "vibecrafted-core/vibecrafted_core/config/vc-frame"
    (config / "layouts").mkdir(parents=True, exist_ok=True)
    (config / "themes").mkdir()
    (config / "config.kdl").write_text("fixture\n", encoding="utf-8")
    (config / "layouts/operator.kdl").write_text("fixture\n", encoding="utf-8")
    (config / "themes/default.kdl").write_text("fixture\n", encoding="utf-8")


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
        platform="darwin-arm64",
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
    *arguments: str,
    env: dict[str, str] | None = None,
    umask: int = -1,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(INSTALLER),
            "--expected-version",
            VERSION,
            "--expected-platform",
            "darwin-arm64",
            "--expected-architecture",
            "arm64",
            *arguments,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
        umask=umask,
    )


def _isolated_repo_install(
    root: Path,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    repo = root / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "Makefile", repo / "Makefile")
    shutil.copy2(INSTALLER, scripts / INSTALLER.name)
    fake_bin = root / "fake-bin"
    fake_bin.mkdir()
    uname = fake_bin / "uname"
    uname.write_text(
        "#!/bin/sh\n"
        'case "${1:-}" in\n'
        "  -s) printf 'Darwin\\n' ;;\n"
        "  -m) printf 'arm64\\n' ;;\n"
        "  *) printf 'Darwin\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)
    return subprocess.run(
        ["make", "--no-print-directory", "install"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            **(env or {}),
        },
    )


def test_make_install_discovers_exact_canonical_runtime_pack_name(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    payload = tmp_path / "payload/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    producer_name = (
        "Vibecrafted_RuntimePack_4.3.0-20260826-eb5741b2-darwin-arm64.tar.gz"
    )
    archive, public_key = _sealed_archive(dist, payload, name=producer_name)

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert archive.name == producer_name
    assert capture.read_text(encoding="utf-8").splitlines()[1:3] == [
        "runtime-install",
        "--payload-root",
    ]


@pytest.mark.parametrize(
    "wrong_name",
    [
        "Vibecrafted_RuntimePack_4.3.0-20260826-eb5741b2-linux-arm64.tar.gz",
        "Vibecrafted_RuntimePack_4.3.0-20260826-eb5741b2-darwin-x64.tar.gz",
    ],
)
def test_make_install_rejects_wrong_platform_or_architecture_pack(
    tmp_path: Path, wrong_name: str
) -> None:
    dist = tmp_path / "repo/dist"
    dist.mkdir(parents=True)
    (dist / wrong_name).write_bytes(b"wrong target")

    result = _isolated_repo_install(tmp_path)

    assert result.returncode != 0
    assert "no darwin-arm64/arm64 Runtime Pack found" in result.stderr


def test_make_install_rejects_ambiguous_canonical_runtime_packs(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "repo/dist"
    dist.mkdir(parents=True)
    for revision in ("11111111", "22222222"):
        (
            dist
            / f"Vibecrafted_RuntimePack_4.3.0-20260826-{revision}-darwin-arm64.tar.gz"
        ).write_bytes(b"ambiguous")

    result = _isolated_repo_install(tmp_path)

    assert result.returncode != 0
    assert "multiple Runtime Packs in dist" in result.stderr


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


def test_final_app_carrier_installer_uses_its_bound_version_truth(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)

    carrier = tmp_path / "Vibecrafted.app/Contents/Resources/runtime-pack"
    carrier.mkdir(parents=True)
    staged_installer = carrier / "install-runtime-pack.sh"
    shutil.copy2(INSTALLER, staged_installer)
    staged_installer.chmod(0o755)
    shutil.copy2(public_key, carrier / "vibecrafted-signing-v1.pub")
    (carrier / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    staged_archive = carrier / archive.name
    for source, destination in (
        (archive, staged_archive),
        (Path(str(archive) + ".sha256"), Path(str(staged_archive) + ".sha256")),
        (Path(str(archive) + ".sig"), Path(str(staged_archive) + ".sig")),
    ):
        shutil.copy2(source, destination)

    result = subprocess.run(
        [
            "bash",
            str(staged_installer),
            "--pack",
            str(staged_archive),
            "--verify-only",
            "--expected-platform",
            "darwin-arm64",
            "--expected-architecture",
            "arm64",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(
                carrier / "vibecrafted-signing-v1.pub"
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "No such file or directory" not in result.stderr
    assert json.loads(result.stdout)["version"] == VERSION
    assert (carrier / "VERSION").read_text(encoding="utf-8") == f"{VERSION}\n"


def test_app_helpers_can_verify_but_cannot_replace_signed_pack_bytes(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    terminal = payload / "bin/vc-terminal"
    terminal.write_text("#!/bin/sh\n# signed terminal\n", encoding="utf-8")
    terminal.chmod(0o755)
    archive, public_key = _sealed_archive(tmp_path, payload)
    app = tmp_path / "Vibecrafted.app"
    app_terminal = app / "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    app_frame = app / "Contents/Helpers/vc-frame"
    app_terminal.parent.mkdir(parents=True)
    app_frame.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(terminal, app_terminal)
    shutil.copy2(payload / "libexec/vc-frame", app_frame)

    result = _run(
        "--pack",
        str(archive),
        "--app-root",
        str(app),
        "--terminal-host",
        str(app_terminal),
        "--frame-helper",
        str(app_frame),
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert "--app-root" in argv
    assert "--terminal-host" not in argv
    assert "--frame-helper" not in argv

    app_frame.write_bytes(b"foreign app frame")
    rejected = _run(
        "--pack",
        str(archive),
        "--app-root",
        str(app),
        "--terminal-host",
        str(app_terminal),
        "--frame-helper",
        str(app_frame),
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert rejected.returncode != 0
    assert "App vc-frame helper disagrees" in rejected.stderr


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
    runtime = tmp_path / "runtime-payload"
    required = (
        "VERSION",
        "bin/python3",
        "bin/scaffold-doctor",
        "bin/vc-start",
        "bin/vibecrafted",
        "scripts/vibecrafted",
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
        if relative.startswith("bin/") or relative == "scripts/vibecrafted":
            path.chmod(0o755)
    (runtime / ".DS_Store").write_bytes(b"mutable Finder metadata")
    (runtime / "python/.DS_Store").parent.mkdir(parents=True)
    (runtime / "python/.DS_Store").write_bytes(b"nested Finder metadata")
    contract_dir = runtime / "vibecrafted-core/vibecrafted_core"
    contract_dir.mkdir(parents=True)
    (contract_dir / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py",
        contract_dir / "runtime_pack_contract.py",
    )
    _foundation_manifest(runtime)
    _source_provenance(runtime)
    _seed_vc_frame_product_payload(runtime)
    terminal = runtime / "bin/vc-terminal"
    terminal.write_text("#!/bin/sh\n", encoding="utf-8")
    terminal.chmod(0o755)
    output = tmp_path / "Vibecrafted_RuntimePack_fixture.tar.gz"

    result = subprocess.run(
        [
            "bash",
            str(PACKAGER),
            "--payload-root",
            str(runtime),
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
            "darwin-arm64",
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
        assert "VibecraftedRuntime/bin/vc-start" in names
        assert "VibecraftedRuntime/libexec/vc-frame" in names
        assert "VibecraftedRuntime/runtime-pack-provenance.json" in names
        assert "VibecraftedRuntime/scripts/vibecrafted" in names
        assert not any(name.endswith("/.DS_Store") for name in names)
        assert not any(
            member.issym() or member.islnk() for member in archive.getmembers()
        )
        foundations = json.load(
            archive.extractfile("VibecraftedRuntime/runtime-foundations.json")
        )
        assert REQUIRED_FOUNDATION_EXECUTABLES.issubset(foundations["files"])
        for name in REQUIRED_FOUNDATION_EXECUTABLES:
            shipped = archive.extractfile(f"VibecraftedRuntime/bin/{name}").read()
            assert hashlib.sha256(shipped).hexdigest() == foundations["files"][name]
    expected = hashlib.sha256(output.read_bytes()).hexdigest()
    assert (
        output.with_suffix(output.suffix + ".sha256")
        .read_text(encoding="utf-8")
        .split()[0]
        == expected
    )

    # Linux arm64 additionally requires the closed executable inventory that
    # the native builder records from real produced bytes. A synthetic payload
    # cannot be mislabeled as a complete Linux carrier merely because it has
    # executable-shaped files.
    for relative in ("bin/vc-terminal", "bin/vc-frame", "libexec/vc-frame"):
        helper = runtime / relative
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\n", encoding="utf-8")
        helper.chmod(0o755)
    (runtime / "libexec/vc-frame").write_bytes(b"\x7fELF" + b"\x00" * 32)
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
            "linux-arm64",
            "--architecture",
            "arm64",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert linux.returncode != 0
    assert "Linux arm64 Runtime Pack inventory is invalid" in linux.stderr
    assert not linux_output.exists()


def test_runtime_pack_contract_rejects_missing_install_launcher(tmp_path: Path) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)
    (payload / "scripts/vibecrafted").unlink()

    with pytest.raises(
        RuntimePackContractError,
        match="Runtime Pack installer payload is missing scripts/vibecrafted",
    ):
        write_provenance(
            payload,
            carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
            version=VERSION,
            platform="darwin-arm64",
            architecture="arm64",
            source_revision=SOURCE_SHA,
            terminal_revision=TERMINAL_SHA,
            frame_revision=FRAME_SHA,
        )


def test_runtime_pack_contract_rejects_dead_vc_frame_wrapper_only(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)
    (payload / "libexec/vc-frame").unlink()

    with pytest.raises(RuntimePackContractError, match="native vc-frame is missing"):
        write_provenance(
            payload,
            carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
            version=VERSION,
            platform="darwin-arm64",
            architecture="arm64",
            source_revision=SOURCE_SHA,
            terminal_revision=TERMINAL_SHA,
            frame_revision=FRAME_SHA,
        )


def test_runtime_pack_contract_ignores_mutable_host_metadata(tmp_path: Path) -> None:
    """Host services stamp .DS_Store into live trees faster than any sweep.

    The carrier tar excludes the name, so it can never ship: the closed
    inventory skips it on both write and verify instead of failing a valid
    payload, and never records it in provenance.
    """
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)
    (payload / ".DS_Store").write_bytes(b"mutable Finder metadata")
    (payload / "bin/.DS_Store").write_bytes(b"mutable Finder metadata")

    provenance = write_provenance(
        payload,
        carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
        version=VERSION,
        platform="darwin-arm64",
        architecture="arm64",
        source_revision=SOURCE_SHA,
        terminal_revision=TERMINAL_SHA,
        frame_revision=FRAME_SHA,
    )
    recorded = {entry["path"] for entry in provenance["payload"]["files"]}
    assert not any(name.endswith(".DS_Store") for name in recorded)

    verified = verify_provenance(
        payload, carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz"
    )
    assert verified["payload"]["files"] == provenance["payload"]["files"]


def test_runtime_pack_contract_rejects_post_manifest_foundation_mutation(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)
    (payload / "bin/loctree").write_bytes(b"post-manifest signing mutation")

    with pytest.raises(
        RuntimePackContractError,
        match="foundation digest does not match final bytes: bin/loctree",
    ):
        write_provenance(
            payload,
            carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
            version=VERSION,
            platform="darwin-arm64",
            architecture="arm64",
            source_revision=SOURCE_SHA,
            terminal_revision=TERMINAL_SHA,
            frame_revision=FRAME_SHA,
        )


def test_runtime_pack_contract_rejects_split_platform_identity(tmp_path: Path) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)

    with pytest.raises(
        RuntimePackContractError,
        match="canonical <os>-<architecture> target slug",
    ):
        write_provenance(
            payload,
            carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
            version=VERSION,
            platform="darwin",
            architecture="arm64",
            source_revision=SOURCE_SHA,
            terminal_revision=TERMINAL_SHA,
            frame_revision=FRAME_SHA,
        )


def test_runtime_pack_contract_rejects_missing_vc_start(tmp_path: Path) -> None:
    payload = tmp_path / "VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    _source_provenance(payload)
    (payload / "bin/vc-start").unlink()

    with pytest.raises(
        RuntimePackContractError,
        match="Runtime Pack installer payload is missing bin/vc-start",
    ):
        write_provenance(
            payload,
            carrier_basename="Vibecrafted_RuntimePack_fixture.tar.gz",
            version=VERSION,
            platform="darwin-arm64",
            architecture="arm64",
            source_revision=SOURCE_SHA,
            terminal_revision=TERMINAL_SHA,
            frame_revision=FRAME_SHA,
        )


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
    ambient_tar = fake_bin / "tar"
    ambient_tar.write_text(
        """#!/usr/bin/env bash
set -eu
/usr/bin/tar "$@"
if [[ " $* " == *" -xpzf "* ]]; then
  previous=""
  for argument in "$@"; do
    if [[ "$previous" == "-C" ]]; then
      touch "$argument/VibecraftedRuntime/.DS_Store"
      break
    fi
    previous="$argument"
  done
fi
""",
        encoding="utf-8",
    )
    ambient_tar.chmod(0o755)
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
    assert Path(arguments[3]).parent.name.startswith(".vibecrafted-runtime-pack.")
    assert arguments[0] == f"{arguments[3]}/scripts/vetcoders_install.py"
    assert not any(extraction_home.iterdir())


def test_signed_archive_preserves_provenance_modes_across_ambient_umask(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    lock = payload / "python-site/.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    lock.chmod(0o777)
    archive, public_key = _sealed_archive(tmp_path, payload)

    result = _run(
        "--pack",
        str(archive),
        "--verify-only",
        env={"VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key)},
        umask=0o077,
    )

    assert result.returncode == 0, result.stderr
    assert '"path":"python-site/.lock"' in result.stdout
    assert '"mode":"0777"' in result.stdout


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
        "linux-arm64",
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "internal provenance verification failed" in result.stderr
    assert not capture.exists()


def test_signed_carrier_rejects_selected_architecture_mismatch_before_installer(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "source/VibecraftedRuntime"
    capture = tmp_path / "argv"
    _fake_runtime_payload(payload, capture)
    archive, public_key = _sealed_archive(tmp_path, payload)

    result = _run(
        "--pack",
        str(archive),
        "--expected-architecture",
        "x64",
        env={
            "CAPTURE": str(capture),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "internal provenance verification failed" in result.stderr
    assert not capture.exists()
