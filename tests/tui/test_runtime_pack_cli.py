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
SELECTION_LIBRARY = REPO_ROOT / "scripts/lib/runtime-pack-selection.sh"
SELECTION_SCHEMA = "vibecrafted.runtime-pack-selection.v1"
# The eighteen canonical archives the Founder's dist actually held on
# 2026-09-09, when `make runtime-pack && make install` refused as ambiguous.
HISTORICAL_PACKS = (
    "Vibecrafted_RuntimePack_4.3.0-20260906-6a0cf10a-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260906-ae650a83-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260906-cb026674-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260906-f861d136-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260907-043864af-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260907-09c947e0-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.0-20260907-ae7c5ed8-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-32659c8a-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-381c6b8b-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-48dc4050-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-653649f4-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-68065b95-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-83c26f12-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-aa12980d-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-ec951bbd-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260908-f53e79a0-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260909-0e5f10ee-darwin-arm64.tar.gz",
    "Vibecrafted_RuntimePack_4.3.1-20260909-8177a33d-darwin-arm64.tar.gz",
)
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


def _fake_runtime_payload(root: Path, capture: Path, marker: str | None = None) -> None:
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
        'if [[ "${INSTALLER_CHILD_FIXTURE:-}" == "1" ]]; then\n'
        '  printf "%s" "$$" > "$INSTALLER_CHILD_PID"\n'
        "  trap '' TERM\n"
        '  sleep "${INSTALLER_CHILD_SLEEP:-2}"\n'
        '  printf mutation > "$INSTALLER_CHILD_MUTATION"\n'
        "  exit 0\n"
        "fi\n"
        # Identify WHICH archive's payload is running, so a selection test can
        # prove the installed bytes are the recorded ones rather than merely
        # some archive that happened to verify.
        'if [[ -n "${PACK_MARKER_OUT:-}" ]]; then\n'
        '  marker="$(dirname "$0")/../PACK-MARKER"\n'
        '  [[ -f "$marker" ]] && cat "$marker" > "$PACK_MARKER_OUT"\n'
        "fi\n"
        'printf "%s\\n" "$@" > "$CAPTURE"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    if marker is not None:
        (root / "PACK-MARKER").write_text(marker, encoding="utf-8")
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
    terminal_wrapper = root / "bin/vc-terminal"
    terminal_wrapper.write_text(
        (REPO_ROOT / "scripts/vc-terminal-product-entry.sh").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    terminal_wrapper.chmod(0o755)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        REPO_ROOT / "scripts/vc-terminal-product-entry.sh",
        root / "scripts/vc-terminal-product-entry.sh",
    )
    terminal_host = root / "libexec/vc-terminal"
    terminal_host.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)
    terminal_host.chmod(0o755)
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
    keys: tuple[Path, Path] | None = None,
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
    # Two archives sealed by the SAME key make a selection test honest: picking
    # the wrong one then fails on selection, not on an unrelated signature.
    if keys is not None:
        private_key, public_key = keys
    else:
        private_key = tmp_path / "signing.key"
        public_key = tmp_path / "signing.pub"
    if not private_key.exists():
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
    (scripts / "lib").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "Makefile", repo / "Makefile")
    shutil.copy2(INSTALLER, scripts / INSTALLER.name)
    # The installer reads the build -> install handoff through its owner. A repo
    # without that library keeps the pre-handoff single-archive behaviour, which
    # is exactly what the App-embedded copy relies on.
    shutil.copy2(SELECTION_LIBRARY, scripts / "lib" / SELECTION_LIBRARY.name)
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


def _historical_dist(dist: Path) -> None:
    """Reproduce the Founder's dist: many legitimate, non-installable archives."""
    for name in HISTORICAL_PACKS:
        (dist / name).write_bytes(b"historical archive")


def _selection_record(repo: Path, fields: dict[str, str]) -> Path:
    record = repo / "build/runtime-pack-selection.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(f'  "{key}": "{value}"' for key, value in fields.items())
    record.write_text("{\n" + body + "\n}\n", encoding="utf-8")
    return record


def _ready_fields(pack: Path, **overrides: str) -> dict[str, str]:
    fields = {
        "schema": SELECTION_SCHEMA,
        "status": "ready",
        "attempt": "fixture-attempt",
        "pack": str(pack),
        "carrier_basename": pack.name,
        "sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
        "size": str(pack.stat().st_size),
        "version": VERSION,
        "platform": "darwin-arm64",
        "architecture": "arm64",
        "source_revision": SOURCE_SHA,
        "terminal_revision": TERMINAL_SHA,
        "frame_revision": FRAME_SHA,
        "completed_at": "2026-09-09T11:01:06Z",
    }
    fields.update(overrides)
    return fields


def _git_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    run("init", "-q")
    run("config", "user.email", "agents@vetcoders.io")
    run("config", "user.name", "fixture")
    run("commit", "-q", "--allow-empty", "-m", "fixture")
    return run("rev-parse", "HEAD").stdout.strip()


def test_recorded_build_wins_over_eighteen_historical_archives(
    tmp_path: Path,
) -> None:
    """The reported defect: a completed build is not what `make install` picks.

    With the Founder's eighteen historical archives on disk the installer saw
    many canonical candidates and refused. The producer knew the exact path all
    along, so it now records it and this resolves to those bytes -- never by
    mtime, never by glob order, and without removing a single historical pack.
    """

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    _historical_dist(dist)
    built = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(built, tmp_path / "argv", marker="just-built")
    archive, public_key = _sealed_archive(
        dist,
        built,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    _selection_record(repo, _ready_fields(archive))
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker_out.read_text(encoding="utf-8") == "just-built"
    assert len(list(dist.glob("Vibecrafted_RuntimePack_*"))) >= len(HISTORICAL_PACKS)


def test_explicit_pack_outranks_the_build_selection_record(tmp_path: Path) -> None:
    """Installing another signed generation on purpose stays supported."""

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    keys = (tmp_path / "signing.key", tmp_path / "signing.pub")
    recorded_payload = tmp_path / "recorded/VibecraftedRuntime"
    _fake_runtime_payload(recorded_payload, tmp_path / "argv", marker="recorded")
    recorded, public_key = _sealed_archive(
        dist,
        recorded_payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        keys=keys,
    )
    requested_payload = tmp_path / "requested/VibecraftedRuntime"
    _fake_runtime_payload(requested_payload, tmp_path / "argv", marker="requested")
    requested, _ = _sealed_archive(
        dist,
        requested_payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260908-aa12980d-darwin-arm64.tar.gz",
        keys=keys,
    )
    _selection_record(repo, _ready_fields(recorded))
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "RUNTIME_PACK": str(requested),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker_out.read_text(encoding="utf-8") == "requested"


def test_interrupted_build_cannot_publish_the_previous_success(
    tmp_path: Path,
) -> None:
    """A failed retry must not let the last good archive pose as today's build.

    dist holds exactly ONE installable archive here, so the pre-handoff path
    would happily install it. The pending record is the whole difference: the
    attempt was claimed before the build could fail, so nothing is ready.
    """

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    previous = tmp_path / "previous/VibecraftedRuntime"
    _fake_runtime_payload(previous, tmp_path / "argv", marker="previous-success")
    _, public_key = _sealed_archive(
        dist,
        previous,
        name="Vibecrafted_RuntimePack_4.3.0-20260908-aa12980d-darwin-arm64.tar.gz",
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    _selection_record(
        repo,
        {
            "schema": SELECTION_SCHEMA,
            "status": "pending",
            "attempt": "interrupted-attempt",
            "source_revision": SOURCE_SHA,
            "started_at": "2026-09-09T11:01:06Z",
        },
    )
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "did not complete" in result.stderr
    assert not marker_out.exists()


def test_recorded_pack_swapped_under_its_digest_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    payload = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(payload, tmp_path / "argv", marker="just-built")
    archive, public_key = _sealed_archive(
        dist,
        payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    fields = _ready_fields(archive)
    _selection_record(repo, fields)
    # Same path, same signed-looking name, different bytes.
    archive.write_bytes(b"swapped bytes")

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "recorded digest" in result.stderr


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"platform": "linux-arm64"}, "targets linux-arm64"),
        ({"architecture": "x64"}, "targets x64"),
        ({"status": "half-written"}, "no usable status"),
        ({"schema": "vibecrafted.some-other-record.v1"}, "unknown schema"),
        ({"sha256": "not-a-digest"}, "no usable digest"),
        ({"source_revision": "abcdef"}, "no full source revision"),
        ({"carrier_basename": "Vibecrafted_RuntimePack_other.tar.gz"}, "disagrees"),
    ],
)
def test_unusable_selection_record_never_falls_back_to_another_archive(
    tmp_path: Path, overrides: dict[str, str], expected: str
) -> None:
    """Every refusal here has ONE installable archive sitting in dist.

    That archive is what the legacy path would install. A record that cannot be
    honoured must fail visibly instead of quietly resolving to it.
    """

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    payload = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(payload, tmp_path / "argv", marker="just-built")
    archive, public_key = _sealed_archive(
        dist,
        payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    _selection_record(repo, _ready_fields(archive, **overrides))
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert expected in result.stderr
    assert not marker_out.exists()


def test_record_from_a_foreign_source_generation_is_refused(tmp_path: Path) -> None:
    """ "The pack you just built HERE" is a claim about this checkout."""

    repo = tmp_path / "repo"
    _git_repo(repo)
    dist = repo / "dist"
    dist.mkdir(parents=True)
    payload = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(payload, tmp_path / "argv", marker="foreign")
    archive, public_key = _sealed_archive(
        dist,
        payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    _selection_record(repo, _ready_fields(archive))
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "this source is at" in result.stderr
    assert not marker_out.exists()


def test_record_bound_to_this_checkout_installs_from_a_custom_release_dir(
    tmp_path: Path,
) -> None:
    """The counterpart: right source, and a release directory outside dist.

    VIBECRAFTED_RELEASE_DIR is why a reconstructed `dist/<name>` was never safe.
    The recorded path is absolute, so a directory with a space in it is ordinary.
    """

    repo = tmp_path / "repo"
    head = _git_repo(repo)
    release_dir = tmp_path / "release output"
    release_dir.mkdir()
    payload = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(payload, tmp_path / "argv", marker="custom-dir-build")
    archive, public_key = _sealed_archive(
        release_dir,
        payload,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        source_revision=head,
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    _selection_record(repo, _ready_fields(archive, source_revision=head))
    marker_out = tmp_path / "installed-marker"

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker_out.read_text(encoding="utf-8") == "custom-dir-build"
    assert not (repo / "dist").exists()


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
    terminal = payload / "libexec/vc-terminal"
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
        "bin/vibecrafted-mcp",
        "vibecrafted-mcp/vibecrafted_mcp/__init__.py",
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
        assert "VibecraftedRuntime/libexec/vc-terminal" in names
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
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        helper = runtime / relative
        helper.parent.mkdir(parents=True, exist_ok=True)
        helper.write_text("#!/bin/sh\n", encoding="utf-8")
        helper.chmod(0o755)
    (runtime / "libexec/vc-frame").write_bytes(b"\x7fELF" + b"\x00" * 32)
    (runtime / "libexec/vc-terminal").write_bytes(b"\x7fELF" + b"\x00" * 32)
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


# --- the build -> install handoff, exercised rather than read -------------
#
# The assertions below drive the REAL producer entry point and the REAL record
# owner. Every one of them reports the subject's exit status explicitly instead
# of letting a shell's last command decide it: a refusal has to be the refusal
# under test, never an unrelated failure that happens to be non-zero too.

RELEASE_BUILDER = REPO_ROOT / "scripts/build-vibecrafted-release.sh"


def _preflight_builder_repo(tmp_path: Path) -> tuple[Path, str, Path]:
    """A repo where the real release builder runs as far as its preflight.

    Only the builder and the record's owner are copied. That is not a shortcut:
    every failure exercised here happens before the remaining libraries are even
    sourced, which is precisely the property under test -- the attempt must be
    claimed before them.
    """

    repo = tmp_path / "repo"
    (repo / "scripts/lib").mkdir(parents=True)
    (repo / "dist").mkdir()
    shutil.copy2(RELEASE_BUILDER, repo / "scripts" / RELEASE_BUILDER.name)
    shutil.copy2(SELECTION_LIBRARY, repo / "scripts/lib" / SELECTION_LIBRARY.name)
    (repo / "VERSION").write_text(f"{VERSION}\n", encoding="utf-8")
    # The builder prefers a rustup cargo before it parses a single argument.
    # HOME is a fresh directory here, so a real rustup would find no toolchain
    # under it and spend the test installing one over the network -- silently,
    # because that probe is `|| true`. Stub it: which cargo wins is irrelevant
    # to a preflight that dies long before anything compiles.
    fake_bin = repo / "fake-bin"
    fake_bin.mkdir()
    rustup = fake_bin / "rustup"
    rustup.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    rustup.chmod(0o755)
    head = _git_repo(repo)
    previous = repo / "dist/Vibecrafted_RuntimePack_previous-darwin-arm64.tar.gz"
    previous.write_bytes(b"the pack that succeeded yesterday")
    _selection_record(
        repo, _ready_fields(previous, attempt="previous-success", source_revision=head)
    )
    return repo, head, previous


def _run_builder(
    repo: Path, *arguments: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / RELEASE_BUILDER.name), *arguments],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PATH": f"{repo / 'fake-bin'}:{os.environ['PATH']}",
            "VIBECRAFTED_TERMINAL_REPO": str(repo),
            "VIBECRAFTED_FRAME_REPO": str(repo),
            **(env or {}),
        },
    )


def _record(repo: Path) -> dict[str, str]:
    # json.loads is the point: the record has to be real JSON, not merely a
    # shape the shell reader happens to accept.
    return json.loads((repo / "build/runtime-pack-selection.json").read_text("utf-8"))


@pytest.mark.parametrize(
    ("environment", "expected"),
    (
        pytest.param(
            {"VIBECRAFTED_TERMINAL_REPO": "/nonexistent/vc-terminal"},
            "missing donor directory",
            id="missing-donor",
        ),
        pytest.param(
            {"VIBECRAFTED_RELEASE_DATE": "not-a-date"},
            "VIBECRAFTED_RELEASE_DATE must be YYYYMMDD",
            id="invalid-release-date",
        ),
        pytest.param(
            {"DEVELOPER_DIR": "/nonexistent/Xcode.app/Contents/Developer"},
            "no usable Xcode developer dir",
            id="unusable-xcode",
        ),
    ),
)
def test_a_failed_preflight_invalidates_the_previous_ready_selection(
    tmp_path: Path, environment: dict[str, str], expected: str
) -> None:
    """A build that dies before it starts still has to void yesterday's answer.

    These three die in the builder's executable top level, above everything that
    looks like "the build": donor roots, the release date, the Xcode toolchain.
    Claiming the attempt just before `build_product` left all of them outside
    the protection, so a failed retry -- at the very same source SHA, where no
    name derived from HEAD can tell the runs apart -- left the previous success
    selected and `make install` installed it as if it were today's build.
    """

    repo, head, previous = _preflight_builder_repo(tmp_path)
    assert _record(repo)["status"] == "ready"

    result = _run_builder(repo, "--runtime-pack-only", env=environment)

    # Name the failure. A non-zero exit alone would also be satisfied by the
    # sandbox lacking something unrelated.
    assert result.returncode != 0
    assert expected in result.stderr, result.stderr
    record = _record(repo)
    assert record["status"] == "pending"
    assert record["source_revision"] == head
    assert "pack" not in record
    # The archive itself is untouched; only the claim that it is current is gone.
    assert previous.is_file()


@pytest.mark.parametrize(
    ("arguments", "environment", "expected"),
    (
        pytest.param(
            ("--not-a-flag",),
            {},
            "usage:",
            id="usage-error",
        ),
        pytest.param(
            ("--notarize-only",),
            {"VIBECRAFTED_TERMINAL_REPO": "/nonexistent/vc-terminal"},
            "missing donor directory",
            id="notarize-only",
        ),
    ),
)
def test_a_run_that_builds_no_carrier_leaves_the_selection_untouched(
    tmp_path: Path,
    arguments: tuple[str, ...],
    environment: dict[str, str],
    expected: str,
) -> None:
    """Invalidation belongs to builds, not to every invocation.

    Misuse is rejected before the claim, and --notarize-only re-runs
    notarization for an App that already exists: it produces no new carrier, so
    voiding the record would refuse an install of a pack that is still perfectly
    current.
    """

    repo, _head, _previous = _preflight_builder_repo(tmp_path)
    before = (repo / "build/runtime-pack-selection.json").read_bytes()

    result = _run_builder(repo, *arguments, env=environment)

    assert result.returncode != 0
    assert expected in result.stderr, result.stderr
    assert (repo / "build/runtime-pack-selection.json").read_bytes() == before


def _selection_shell(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Drive the record's owner directly, reporting its status as data.

    `set -e` is deliberately absent and every exit code is printed rather than
    returned: a test that asserted on the process status would be asserting on
    whatever ran last.
    """

    return subprocess.run(
        [
            "bash",
            "-c",
            f'. "{SELECTION_LIBRARY}"\n{script}',
            "runtime-pack-selection",
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_stale_builder_cannot_publish_over_a_newer_attempt(tmp_path: Path) -> None:
    """The interleaving that hashing-then-renaming could not survive.

    Reading the current attempt, then digesting a several-hundred-megabyte
    archive, then renaming over the record is check-then-act, and the window is
    exactly as long as the digest. Schedule: A claims, A digests, B claims while
    A is still digesting, A finishes and tries to publish. The rename is atomic
    but it is not compare-and-swap, so A's ready record used to bury B -- and
    when B then failed, the failed build's install resolved to A's bytes.

    Nothing here sleeps or races: measuring and committing are separate steps,
    so the schedule is written down rather than hoped for.
    """

    repo = tmp_path / "repo"
    (repo / "dist").mkdir(parents=True)
    pack = repo / "dist/Vibecrafted_RuntimePack_A-darwin-arm64.tar.gz"
    pack.write_bytes(b"A's carrier")

    result = _selection_shell(
        'repo="$1"; pack="$2"; sha="$3"\n'
        'a="$(runtime_pack_selection_attempt_id)"\n'
        'b="$a-newer"\n'
        'runtime_pack_selection_begin "$repo" "$a" "$sha"; echo "begin_a=$?"\n'
        'runtime_pack_selection_measure "$pack"; echo "measure_a=$?"\n'
        'runtime_pack_selection_begin "$repo" "$b" "$sha"; echo "begin_b=$?"\n'
        'runtime_pack_selection_commit "$repo" "$a" "4.3.1" darwin-arm64 arm64 '
        '"$sha" "$sha" "$sha"; echo "commit_a=$?"\n'
        'runtime_pack_selection_read "$repo" darwin-arm64 arm64; echo "read=$?"\n'
        'echo "error=$RUNTIME_PACK_SELECTION_ERROR"\n',
        str(repo),
        str(pack),
        SOURCE_SHA,
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["begin_a"] == "0"
    assert reported["measure_a"] == "0"
    assert reported["begin_b"] == "0"
    # A's build genuinely succeeded; it simply is no longer the answer. That is
    # a step aside, not a build failure, so it does not fail the builder.
    assert reported["commit_a"] == "0"
    assert "owns the record" in result.stderr

    record = _record(repo)
    assert record["status"] == "pending"
    assert record["attempt"].endswith("-newer")
    # The consumer's verdict is the one that matters: B never completed, so
    # nothing is installable -- least of all the stale winner's bytes.
    assert reported["read"] == "2"
    assert "did not complete" in reported["error"]


# --- the record lock, driven rather than described --------------------------
#
# The lock is what makes the ownership check above indivisible, so it is
# exercised with real contenders rather than asserted about. Every scenario
# runs from a script file: a contender has to be a separate live process, and
# nesting one inside a `bash -c` string only proves how quoting survived.

CONTENDER = """\
. "$1"
RUNTIME_PACK_SELECTION_LOCK_TIMEOUT="${5:-30}"
runtime_pack_selection_lock "$2" || exit 9
touch "$3/entered"
while [[ ! -e "$3/release" ]]; do sleep 0.02; done
runtime_pack_selection_unlock
"""

# Inside the lock, look: exactly one name may be in the witness directory. A
# contender that sees a second one writes the violation down, so "never two
# entrants" is observed by the entrants themselves, not inferred from timing.
WITNESS = """\
. "$1"
RUNTIME_PACK_SELECTION_LOCK_TIMEOUT=30
runtime_pack_selection_lock "$2" || { echo "refused:$4" >> "$3/violations"; exit 9; }
: > "$3/inside/$4"
seen="$(ls "$3/inside" | wc -l | tr -d " ")"
[[ "$seen" == "1" ]] || echo "overlap:$4:$seen" >> "$3/violations"
sleep 0.05
rm -f "$3/inside/$4"
runtime_pack_selection_unlock
echo "done:$4" >> "$3/completed"
"""


def _lock_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A record path, a scratch directory for the scenario, and a contender."""

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    contender = tmp_path / "contender.sh"
    contender.write_text(CONTENDER, encoding="utf-8")
    return tmp_path / "repo/build/runtime-pack-selection.json", fixture, contender


def _selection_scenario(
    tmp_path: Path, body: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a scenario against the real library, reporting statuses as data.

    `set -e` is absent here for the same reason it is absent from
    `_selection_shell`: every exit code under test is printed, so no assertion
    can land on whatever command happened to run last.
    """

    script = tmp_path / "scenario.sh"
    script.write_text(f'. "{SELECTION_LIBRARY}"\n{body}', encoding="utf-8")
    return subprocess.run(
        ["bash", str(script), str(SELECTION_LIBRARY), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_departing_owner_cannot_release_its_successors_lock(tmp_path: Path) -> None:
    """The release that used to reach a lock its caller no longer held.

    A takes the lock and gives it up. B takes it and stays inside. A then
    releases a second time -- what a trap, a retry or a late cleanup does. The
    old release removed a pid file and rmdir'd a directory without asking whose
    they were, so it deleted B's live claim and the next builder walked in.
    Releasing is now closing this process's own descriptor, which cannot name
    anyone else's lock.
    """

    record, fixture, contender = _lock_fixture(tmp_path)

    result = _selection_scenario(
        tmp_path,
        'library="$1"; file="$2"; fixture="$3"; contender="$4"\n'
        'runtime_pack_selection_lock "$file"; echo "a_lock=$?"\n'
        'runtime_pack_selection_unlock; echo "a_release=$?"\n'
        'bash "$contender" "$library" "$file" "$fixture" &\n'
        "b=$!\n"
        'for ((i=0;i<250;i++)); do [[ -e "$fixture/entered" ]] && break; sleep 0.02; done\n'
        '[[ -e "$fixture/entered" ]] && echo "b_inside=1" || echo "b_inside=0"\n'
        'runtime_pack_selection_unlock; echo "a_release_again=$?"\n'
        'bash -c \'. "$1"; RUNTIME_PACK_SELECTION_LOCK_TIMEOUT=2; '
        'runtime_pack_selection_lock "$2"\' _ "$library" "$file" 2>/dev/null\n'
        'echo "c_lock=$?"\n'
        'touch "$fixture/release"; wait "$b"; echo "b_exit=$?"\n',
        str(record),
        str(fixture),
        str(contender),
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["a_lock"] == "0"
    assert reported["a_release"] == "0"
    assert reported["b_inside"] == "1", result.stderr
    # C is refused because B still holds it -- the whole point of the scenario.
    assert reported["c_lock"] == "1", result.stdout
    assert reported["b_exit"] == "0"


def test_a_killed_owner_leaves_no_lock_to_break(tmp_path: Path) -> None:
    """Crash recovery is the kernel's, so there is nothing here to reclaim.

    The holder is killed outright while inside the lock. No successor inspects
    a pid, breaks a directory or waits out a timeout: the descriptor died with
    the process, and with it the lock.
    """

    record, fixture, contender = _lock_fixture(tmp_path)

    result = _selection_scenario(
        tmp_path,
        'library="$1"; file="$2"; fixture="$3"; contender="$4"\n'
        'bash "$contender" "$library" "$file" "$fixture" &\n'
        "holder=$!\n"
        'for ((i=0;i<250;i++)); do [[ -e "$fixture/entered" ]] && break; sleep 0.02; done\n'
        '[[ -e "$fixture/entered" ]] && echo "holder_inside=1" || echo "holder_inside=0"\n'
        'kill -9 "$holder"; wait "$holder" 2>/dev/null\n'
        "RUNTIME_PACK_SELECTION_LOCK_TIMEOUT=10\n"
        'runtime_pack_selection_lock "$file"; echo "successor_lock=$?"\n'
        "runtime_pack_selection_unlock\n"
        '[[ -e "$file.lock" ]] && echo "lock_file_kept=1" || echo "lock_file_kept=0"\n',
        str(record),
        str(fixture),
        str(contender),
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["holder_inside"] == "1", result.stderr
    assert reported["successor_lock"] == "0", result.stderr
    # The file stays. Unlinking it would let two builders lock two inodes under
    # one name, each of them correctly.
    assert reported["lock_file_kept"] == "1"


def test_a_bounded_wait_refuses_instead_of_hanging(tmp_path: Path) -> None:
    """A builder that cannot have the lock is told so, and told when."""

    record, fixture, contender = _lock_fixture(tmp_path)

    result = _selection_scenario(
        tmp_path,
        'library="$1"; file="$2"; fixture="$3"; contender="$4"\n'
        'bash "$contender" "$library" "$file" "$fixture" &\n'
        "b=$!\n"
        'for ((i=0;i<250;i++)); do [[ -e "$fixture/entered" ]] && break; sleep 0.02; done\n'
        '[[ -e "$fixture/entered" ]] && echo "b_inside=1" || echo "b_inside=0"\n'
        "RUNTIME_PACK_SELECTION_LOCK_TIMEOUT=1\n"
        "started=$(date -u +%s)\n"
        'runtime_pack_selection_lock "$file"; echo "a_lock=$?"\n'
        'echo "waited=$(( $(date -u +%s) - started ))"\n'
        'touch "$fixture/release"; wait "$b"; echo "b_exit=$?"\n',
        str(record),
        str(fixture),
        str(contender),
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["b_inside"] == "1", result.stderr
    assert reported["a_lock"] == "1"
    assert "timed out waiting" in result.stderr
    # Honest failure means bounded, not merely eventual.
    assert int(reported["waited"]) <= 10, result.stdout
    assert reported["b_exit"] == "0"


def test_no_two_builders_are_ever_inside_the_record_lock(tmp_path: Path) -> None:
    """Eight contenders, one of them arriving over a killed owner's remains.

    The takeover the old lock permitted needed a dead holder to break, so the
    scenario supplies one before the field starts. Overlap is not inferred from
    timing: every contender counts the names in the witness directory while it
    is inside, and one that sees a second name writes it down.
    """

    record, fixture, contender = _lock_fixture(tmp_path)
    (fixture / "inside").mkdir()
    witness = tmp_path / "witness.sh"
    witness.write_text(WITNESS, encoding="utf-8")

    result = _selection_scenario(
        tmp_path,
        'library="$1"; file="$2"; fixture="$3"; contender="$4"; witness="$5"\n'
        'bash "$contender" "$library" "$file" "$fixture" &\n'
        "holder=$!\n"
        'for ((i=0;i<250;i++)); do [[ -e "$fixture/entered" ]] && break; sleep 0.02; done\n'
        '[[ -e "$fixture/entered" ]] && echo "holder_inside=1" || echo "holder_inside=0"\n'
        'kill -9 "$holder"; wait "$holder" 2>/dev/null\n'
        "for id in 1 2 3 4 5 6 7 8; do\n"
        '  bash "$witness" "$library" "$file" "$fixture" "$id" &\n'
        "done\n"
        "wait\n"
        'echo "violations=$(cat "$fixture/violations" 2>/dev/null | wc -l | tr -d " ")"\n'
        'echo "completed=$(cat "$fixture/completed" 2>/dev/null | wc -l | tr -d " ")"\n',
        str(record),
        str(fixture),
        str(contender),
        str(witness),
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["holder_inside"] == "1", result.stderr
    violations = fixture / "violations"
    assert reported["violations"] == "0", (
        violations.read_text("utf-8") if violations.exists() else result.stdout
    )
    # All eight got a turn: exclusion, not a queue that quietly dropped anyone.
    assert reported["completed"] == "8", result.stdout


def test_the_lock_refuses_an_older_mkdir_lock_rather_than_removing_it(
    tmp_path: Path,
) -> None:
    """The one thing this file must never do, it still never does.

    A directory at the lock's path is the previous mkdir-based lock, left by a
    builder that died inside it. Deleting it is exactly the move whose race
    this change removes, so the library says what it found and stops -- and the
    directory is still there afterwards.
    """

    record, _fixture, _contender = _lock_fixture(tmp_path)
    stale = Path(f"{record}.lock")
    stale.mkdir(parents=True)
    (stale / "pid").write_text("99999999\n", encoding="utf-8")

    result = _selection_scenario(
        tmp_path,
        'file="$2"\nruntime_pack_selection_lock "$file"; echo "lock=$?"\n',
        str(record),
    )

    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert reported["lock"] == "1"
    assert "directory left by an older lock" in result.stderr
    assert stale.is_dir()
    assert (stale / "pid").exists()


@pytest.mark.parametrize(
    "mangle",
    (
        pytest.param(
            lambda text: text.rstrip("\n")[:-1] + "\n",
            id="truncated-closing-brace",
        ),
        pytest.param(
            lambda text: text.replace(
                '  "status": "ready",', '  "status": "ready",\n  "status": "pending",'
            ),
            id="duplicate-status",
        ),
        pytest.param(
            lambda text: text.replace(
                '  "sha256"', '  "pack": "/tmp/other.tar.gz",\n  "sha256"'
            ),
            id="duplicate-pack",
        ),
        pytest.param(
            lambda text: (
                "\n".join(line for line in text.splitlines() if '"size"' not in line)
                + "\n"
            ),
            id="missing-size",
        ),
        pytest.param(
            lambda text: (
                "\n".join(
                    line
                    for line in text.splitlines()
                    if '"terminal_revision"' not in line
                )
                + "\n"
            ),
            id="missing-donor-revision",
        ),
        pytest.param(
            lambda text: (
                "\n".join(line for line in text.splitlines() if '"version"' not in line)
                + "\n"
            ),
            id="missing-version",
        ),
        pytest.param(
            lambda text: text.replace('"status": "ready"', '"status": "pending"'),
            id="mixed-generation",
        ),
        pytest.param(
            lambda text: text + '  "pack": "/tmp/appended.tar.gz"\n',
            id="trailing-field-after-close",
        ),
    ),
)
def test_only_one_whole_record_is_ever_honoured(tmp_path: Path, mangle) -> None:
    """Matching lines are not a record; the whole document is.

    The reader used to pull each field out with an anchored `sed`, so a record
    with its closing brace removed read exactly like a complete one, a second
    `status` or `pack` silently won by being first, and version, size and the
    donor revisions could simply be absent. None of those describe a build that
    finished, and a selection record that cannot describe a finished build must
    refuse rather than hand the installer a candidate.
    """

    repo = tmp_path / "repo"
    (repo / "dist").mkdir(parents=True)
    pack = repo / "dist/Vibecrafted_RuntimePack_4.3.0-darwin-arm64.tar.gz"
    pack.write_bytes(b"a carrier whose record is broken")
    record = _selection_record(repo, _ready_fields(pack))
    intact = record.read_text(encoding="utf-8")

    # The intact record is honoured -- otherwise this proves nothing.
    healthy = _selection_shell(
        'runtime_pack_selection_read "$1" darwin-arm64 arm64; echo "read=$?"\n',
        str(repo),
    )
    assert "read=0" in healthy.stdout, healthy.stdout

    record.write_text(mangle(intact), encoding="utf-8")
    result = _selection_shell(
        'runtime_pack_selection_read "$1" darwin-arm64 arm64; echo "read=$?"\n'
        'echo "pack=$RUNTIME_PACK_SELECTION_PACK"\n',
        str(repo),
    )

    # Exit 2 is the contract: a record exists and cannot be honoured, so the
    # caller must fail visibly instead of resolving some other archive.
    assert "read=2" in result.stdout, result.stdout
    assert "pack=\n" in result.stdout + "\n", result.stdout


def test_a_relative_release_dir_is_recorded_as_the_directory_it_means(
    tmp_path: Path,
) -> None:
    """VIBECRAFTED_RELEASE_DIR may be relative; the record may not be.

    The reader accepts absolute carriers only -- a relative path means a
    different file from every directory -- so the producer resolves what it is
    about to record. Spaces are ordinary in a release directory and survive.
    """

    repo = tmp_path / "repo"
    (repo / "out dir").mkdir(parents=True)
    pack = repo / "out dir/Vibecrafted_RuntimePack_4.3.0-darwin-arm64.tar.gz"
    pack.write_bytes(b"a carrier outside dist")

    result = _selection_shell(
        'cd "$1"\n'
        'attempt="$(runtime_pack_selection_attempt_id)"\n'
        'runtime_pack_selection_begin "$1" "$attempt" "$2"; echo "begin=$?"\n'
        'runtime_pack_selection_publish "$1" "$attempt" "./out dir/$3" "4.3.0" '
        'darwin-arm64 arm64 "$2" "$2" "$2"; echo "publish=$?"\n'
        'runtime_pack_selection_read "$1" darwin-arm64 arm64; echo "read=$?"\n'
        'echo "pack=$RUNTIME_PACK_SELECTION_PACK"\n',
        str(repo),
        SOURCE_SHA,
        pack.name,
    )

    assert "begin=0" in result.stdout, result.stdout
    assert "publish=0" in result.stdout, result.stdout
    assert "read=0" in result.stdout, result.stdout
    assert f"pack={pack}" in result.stdout, result.stdout
    assert _record(repo)["pack"] == str(pack)
    assert not (repo / "dist").exists()


def test_the_producers_own_record_carries_its_build_into_the_installer(
    tmp_path: Path,
) -> None:
    """The handoff end to end, with nothing about the record fabricated.

    Every other selection test writes the record by hand, which proves the
    consumer reads a shape but not that the producer writes it. Here the real
    owner publishes -- after the archive is sealed and signed, as the builder
    does -- and `make install` resolves it past eighteen legitimate historical
    archives, then puts the recorded bytes through the checksum, the detached
    signature and the internal provenance gates unchanged.
    """

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    _historical_dist(dist)
    head = _git_repo(repo)
    built = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(built, tmp_path / "argv", marker="just-built")
    archive, public_key = _sealed_archive(
        dist,
        built,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        source_revision=head,
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )

    published = _selection_shell(
        'repo="$1"; pack="$2"; head="$3"; terminal="$4"; frame="$5"; version="$6"\n'
        'attempt="$(runtime_pack_selection_attempt_id)"\n'
        'runtime_pack_selection_begin "$repo" "$attempt" "$head"; echo "begin=$?"\n'
        'runtime_pack_selection_publish "$repo" "$attempt" "$pack" "$version" '
        'darwin-arm64 arm64 "$head" "$terminal" "$frame"; echo "publish=$?"\n',
        str(repo),
        str(archive),
        head,
        TERMINAL_SHA,
        FRAME_SHA,
        VERSION,
    )
    assert "begin=0" in published.stdout, published.stdout + published.stderr
    assert "publish=0" in published.stdout, published.stdout + published.stderr

    marker_out = tmp_path / "installed-marker"
    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "PACK_MARKER_OUT": str(marker_out),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode == 0, result.stderr
    assert marker_out.read_text(encoding="utf-8") == "just-built"
    # Selection resolved bytes, and it did so without touching a single one of
    # the archives it did not choose.
    carriers = sorted(
        path.name for path in dist.glob("Vibecrafted_RuntimePack_*.tar.gz")
    )
    assert carriers == sorted((*HISTORICAL_PACKS, archive.name))


def test_a_carrier_swapped_after_publication_fails_the_producers_own_record(
    tmp_path: Path,
) -> None:
    """The record names bytes. Replacing them is caught before the installer.

    This is the same publication as above, so the refusal cannot be blamed on a
    hand-written record: only the archive changed underneath it.
    """

    repo = tmp_path / "repo"
    dist = repo / "dist"
    dist.mkdir(parents=True)
    head = _git_repo(repo)
    built = tmp_path / "built/VibecraftedRuntime"
    _fake_runtime_payload(built, tmp_path / "argv", marker="just-built")
    archive, public_key = _sealed_archive(
        dist,
        built,
        name="Vibecrafted_RuntimePack_4.3.0-20260909-d7d83dc5-darwin-arm64.tar.gz",
        source_revision=head,
        keys=(tmp_path / "signing.key", tmp_path / "signing.pub"),
    )
    published = _selection_shell(
        'attempt="$(runtime_pack_selection_attempt_id)"\n'
        'runtime_pack_selection_begin "$1" "$attempt" "$3"; echo "begin=$?"\n'
        'runtime_pack_selection_publish "$1" "$attempt" "$2" "$6" darwin-arm64 '
        'arm64 "$3" "$4" "$5"; echo "publish=$?"\n',
        str(repo),
        str(archive),
        head,
        TERMINAL_SHA,
        FRAME_SHA,
        VERSION,
    )
    assert "publish=0" in published.stdout, published.stdout + published.stderr

    archive.write_bytes(archive.read_bytes() + b"appended")

    result = _isolated_repo_install(
        tmp_path,
        env={
            "CAPTURE": str(tmp_path / "argv"),
            "VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY": str(public_key),
        },
    )

    assert result.returncode != 0
    assert "recorded digest" in result.stderr, result.stderr
