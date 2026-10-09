from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from vibecrafted_core.runtime_pack_contract import LINUX_EXECUTABLES

REPO_ROOT = Path(__file__).resolve().parents[2]


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.parametrize(
    ("donor", "revision", "archive_sha256"),
    [
        (
            "terminal",
            "cfa2c367ed36ba9179a05fff32ab1221060cb04e",
            "ea2f893250a42b0b2722e835e990d6c8169e4e65d817b99fc951c1f2de128c94",
        ),
        (
            "frame",
            "5db1b3ebcfe34aa1c83ac383a28bb0199fe30bfa",
            "5cfcdf19c5954fb70c63c5d31983453745d283bf8b21aa0bd88bf16b92a3b2ff",
        ),
    ],
)
def test_platform_builders_share_exact_donor_inputs(
    donor: str, revision: str, archive_sha256: str
) -> None:
    """Pin parity includes the archive digest, not only the commit label."""
    for filename, names in (
        (
            "build-linux-arm64-runtime-pack.sh",
            (f"{donor}_revision", f"{donor}_archive_sha256"),
        ),
        (
            "build-windows-x64-runtime-pack.ps1",
            (f"{donor}Revision", f"{donor}ArchiveSha256"),
        ),
    ):
        source = (REPO_ROOT / "scripts" / filename).read_text(encoding="utf-8")
        actual = []
        for name in names:
            prefix = r"\$" if filename.endswith(".ps1") else ""
            match = re.search(
                rf'^{prefix}{name}\s*=\s*"([0-9a-f]+)"$', source, re.MULTILINE
            )
            assert match is not None, (filename, name)
            actual.append(match.group(1))
        assert actual == [revision, archive_sha256], filename


@pytest.mark.parametrize("builder", ["linux", "windows"])
@pytest.mark.parametrize("changed_pin", [False, True])
def test_runtime_inventory_uses_verified_donor_archive_digests(
    tmp_path: Path, builder: str, changed_pin: bool
) -> None:
    """Execute the shipped writer and its bindings, without a native build."""
    source = (
        REPO_ROOT
        / "scripts"
        / (
            "build-linux-arm64-runtime-pack.sh"
            if builder == "linux"
            else "build-windows-x64-runtime-pack.ps1"
        )
    ).read_text(encoding="utf-8")
    variables = {}
    for donor in ("terminal", "frame"):
        for field, suffix in (
            ("revision", "Revision"),
            ("archive_sha256", "ArchiveSha256"),
        ):
            name = f"{donor}_{field}" if builder == "linux" else f"{donor}{suffix}"
            prefix = "" if builder == "linux" else r"\$"
            match = re.search(rf'^{prefix}{name}="([0-9a-f]+)"$', source, re.MULTILINE)
            if builder == "windows":
                match = re.search(rf'^\${name} = "([0-9a-f]+)"$', source, re.MULTILINE)
            assert match is not None, name
            value = match.group(1)
            if changed_pin:
                digest = hashlib.sha256(f"next {donor} {field}".encode()).hexdigest()
                value = digest[:40] if field == "revision" else digest
            variables[name] = value

    payload = tmp_path / "payload"
    (payload / "bin").mkdir(parents=True)
    (payload / "libexec").mkdir()
    provenance = b'{"fixture": "inventory unit input, not a release manifest"}\n'
    (payload / "source-provenance.json").write_bytes(provenance)
    for name in set(LINUX_EXECUTABLES) | {
        "python",
        "vc-server",
        "vc-terminal",
        "vc-frame",
    }:
        for suffix in ("", ".exe"):
            _executable(
                payload / "bin" / f"{name}{suffix}",
                f"#!{sys.executable}\nprint('synthetic version probe')\n",
            )
    for name in ("vc-terminal", "vc-frame"):
        (payload / "libexec" / f"{name}.exe").write_bytes(b"synthetic native input")

    env = os.environ.copy()
    for name in ("TERMINAL_ARCHIVE_SHA256", "FRAME_ARCHIVE_SHA256"):
        env.pop(name, None)
    if builder == "linux":
        writer = source.split('PAYLOAD="$payload" SOURCE_REVISION=', 1)[1]
        writer = (
            'PAYLOAD="$payload" SOURCE_REVISION='
            + writer.split("\nPY\n", 1)[0]
            + "\nPY\n"
        )
        setup = "\n".join(
            f"{name}={shlex.quote(value)}" for name, value in variables.items()
        )
        setup += f"\npayload={shlex.quote(str(payload))}\nsource_revision={'a' * 40}\n"
        setup += (
            "platform=linux-x64\narchitecture=x64\ntarget=x86_64-unknown-linux-gnu\n"
        )
        command = ["bash", "-c", f"set -euo pipefail\n{setup}{writer}"]
    else:
        section = source.split(
            '$inventoryScript = Join-Path $work "write_inventory.py"', 1
        )[1]
        writer, bindings = section.split("@'\n", 1)[1].split("\n'@ | Set-Content", 1)
        values = {**variables, "payload": str(payload), "SourceRevision": "a" * 40}
        for name, variable in re.findall(
            r"^\$env:([A-Z_0-9]+) = \$(\w+)$", bindings, re.MULTILINE
        ):
            if variable in values:
                env[name] = values[variable]
        command = [sys.executable, "-c", writer]
    result = subprocess.run(
        command, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    inventory = json.loads((payload / "runtime-inventory.json").read_text())
    records = {row["name"]: row for row in inventory["executables"]}
    for donor in ("terminal", "frame"):
        sha_name = (
            f"{donor}_archive_sha256" if builder == "linux" else f"{donor}ArchiveSha256"
        )
        revision_name = (
            f"{donor}_revision" if builder == "linux" else f"{donor}Revision"
        )
        assert records[f"vc-{donor}"]["source_archive_sha256"] == variables[sha_name]
        assert records[f"vc-{donor}"]["source_revision"] == variables[revision_name]
    for name, row in records.items():
        if name not in {"vc-terminal", "vc-frame"}:
            assert (
                row["source_archive_sha256"] == hashlib.sha256(provenance).hexdigest()
            )


def test_linux_arm64_is_no_longer_blocked_by_channel_foundations() -> None:
    # Until 2026-09-25 the stager refused Linux/aarch64 because npm publishes
    # no linux-arm64 Loctree/AICX package. The pack no longer carries them, so
    # its inventory names first-party executables only, on every architecture.
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )

    assert "stage-runtime-foundations" not in assembler
    assert "runtime_pack_contract write-foundations" in assembler
    for name in LINUX_EXECUTABLES:
        assert f'"{name}": ["--version"]' in assembler, name
    for name in (
        "loct",
        "loctree",
        "loctree-mcp",
        "loctree-lsp",
        "aicx",
        "aicx-mcp",
        "prview",
        "screenscribe",
    ):
        assert f'"{name}": ["--version"]' not in assembler, name
        assert name not in LINUX_EXECUTABLES


def test_local_vm_image_consumes_only_the_exact_runtime_pack_carrier() -> None:
    containerfile = (REPO_ROOT / "vibecrafted-vm/Containerfile").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "COPY src/loctree-suite",
        "COPY src/aicx",
        "releases/latest",
        "installing stub",
        "best-effort",
        '|| echo "[warn]',
        'VOLUME ["/workspace"',
    )
    assert not [token for token in forbidden if token in containerfile]
    assert "ARG RUNTIME_PACK_ARCHIVE" in containerfile
    assert "runtime-pack-provenance.json" in containerfile
    assert "passwd tini" in containerfile
    assert "/usr/sbin/groupadd" in containerfile
    assert "/usr/sbin/useradd" in containerfile
    assert "chmod -R a-w" not in containerfile
    assert "chown -R root:root /opt/vibecrafted-runtime" in containerfile
    assert "USER vibecrafted" in containerfile
    assert "vc-frame vc-terminal voc" in containerfile
    entry = (REPO_ROOT / "vibecrafted-vm/runtime-entry.sh").read_text(encoding="utf-8")
    assert "vc-frame vc-terminal voc" in entry


def test_vm_docker_context_admits_only_exact_runtime_pack_archives(
    tmp_path: Path,
) -> None:
    # Reuse Docker's actual context tar helper; no daemon or image build is needed.
    # The Linux Docker CI lane installs docker==7.1.0 explicitly for this probe.
    docker_build = pytest.importorskip("docker.utils.build")
    context = tmp_path / "context"
    required = {
        "build/Vibecrafted_RuntimePack_linux-arm64.tar.gz",
        "build/linux-arm64-runtime-pack/Vibecrafted_RuntimePack_linux-arm64.tar.gz",
    }
    excluded = {
        "build/private.env",
        "build/runtime-cache/unrelated.tar.gz",
        "build/linux-arm64-runtime-pack/cache/private.json",
        "build/linux-arm64-runtime-pack/unrelated.tar.gz",
        "build/linux-arm64-runtime-pack/Vibecrafted_RuntimePack_linux-arm64.tar.gz.sha256",
        "other/build/Vibecrafted_RuntimePack_linux-arm64.tar.gz",
        "operator-tui/target/cache.bin",
        ".vibecrafted/private.cfg",
    }
    controls = {
        "vibecrafted-vm/Containerfile",
        "vibecrafted-vm/runtime-entry.sh",
        "vibecrafted-vm/runtime-provider-lock.json",
    }
    for name in required | excluded | controls:
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic context fixture")
    patterns = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    (context / ".dockerignore").write_text(patterns, encoding="utf-8")

    with (
        docker_build.tar(
            str(context),
            exclude=patterns.splitlines(),
            dockerfile=("vibecrafted-vm/Containerfile", None),
        ) as archive,
        tarfile.open(fileobj=archive) as payload,
    ):
        members = set(payload.getnames())

    assert required <= members, f"missing required carrier inputs: {required - members}"
    assert controls <= members
    assert not (excluded & members), (
        f"excluded build inputs leaked: {excluded & members}"
    )


def test_linux_builder_uses_pinned_public_inputs_for_arm64_and_x64() -> None:
    builder = (REPO_ROOT / "vibecrafted-vm/RuntimePack.Containerfile").read_text(
        encoding="utf-8"
    )
    wrapper = (REPO_ROOT / "scripts/build-linux-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assert "build-linux-arm64-runtime-pack.sh" in wrapper
    assert "astral.sh/uv" not in builder
    assert "rustup target add wasm32-unknown-unknown wasm32-wasip1" in builder
    assert builder.index("ARG VIBECRAFTED_SOURCE_REVISION") > builder.index(
        "WORKDIR /src/vibecrafted"
    )
    assert "69616218470b2ad053617efb9e7027b1518ea38918d933c2791e113d99cec507" in builder
    assert "cfa2c367ed36ba9179a05fff32ab1221060cb04e" in assembler
    assert "5db1b3ebcfe34aa1c83ac383a28bb0199fe30bfa" in assembler
    assert "git clone" not in assembler
    assert "VIBECRAFTED_SOURCE_OWNER_REPO" in assembler
    assert 'export VIBECRAFTED_SOURCE_REVISION="$source_revision"' in assembler
    assert 'export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.97.0}"' in assembler
    assert 'export CC="${CC:-gcc}"' in assembler
    assert 'export CXX="${CXX:-g++}"' in assembler
    assert 'voc_target="$work/voc-target"' in assembler
    assert 'CARGO_TARGET_DIR="$voc_target" cargo build --locked' in assembler
    assert (
        "--release -p voc --bin voc --bin vc-start --bin vc-admin --bin vc-procs"
        in assembler
    )
    assert (
        'install -m 0755 "$voc_target/release/vc-start" '
        '"$payload/bin/vc-start"' in assembler
    )
    assert 'install -m 0755 "$voc_target/release/voc" "$payload/bin/vc-o"' in assembler
    assert (
        'install -m 0755 "$voc_target/release/vc-admin" '
        '"$payload/bin/vc-admin"' in assembler
    )
    assert (
        'install -m 0755 "$voc_target/release/vc-procs" '
        '"$payload/bin/vc-procs"' in assembler
    )
    assert {"vc-o", "vc-admin", "vc-procs"}.issubset(LINUX_EXECUTABLES)
    assert '"vc-o": ["--version"]' in assembler
    assert '"vc-admin": ["--version"]' in assembler
    assert '"vc-procs": ["--version"]' in assembler
    assert '"$repo_root/vibecrafted-app/target' not in assembler
    assert 'rm -rf "$work/vc-terminal" "$work/vc-terminal.tar.gz"' in assembler
    assert (
        'RUSTFLAGS="--remap-path-prefix=$work/vc-frame=/usr/src/vc-frame"' in assembler
    )
    assert "cargo xtask build --release --no-plugins" not in assembler
    assert "cargo xtask build --release" in assembler
    assert 'rm -rf "$work/vc-frame" "$work/vc-frame.tar.gz"' in assembler
    assert 'rm -rf "$voc_target"' in assembler
    assert 'rm -rf "$server_build"' in assembler
    assert (
        'install -m 0755 "$repo_root/scripts/vibecrafted" '
        '"$payload/scripts/vibecrafted"' in assembler
    )
    assert 'printf \'%s+g%.8s\\n\' "$version" "$source_revision"' in assembler
    assert 'architecture="arm64"' in assembler
    assert 'architecture="x64"' in assembler
    assert 'target="aarch64-unknown-linux-gnu"' in assembler
    assert 'target="x86_64-unknown-linux-gnu"' in assembler
    assert 'platform="linux-$architecture"' in assembler
    assert '--platform "$platform" --architecture "$architecture"' in assembler
    assert '"$payload/bin/vibecrafted-server-web"' in assembler
    assert '"$payload/bin/vc-server-supervisor"' in assembler
    assert '"$payload/bin/scaffold-doctor"' in assembler
    assert '"$payload/bin/control-observe"' in assembler
    assert "--bin scaffold-doctor --bin control-observe" in assembler or (
        "--bin scaffold-doctor" in assembler and "--bin control-observe" in assembler
    )
    assert '"$payload/vibecrafted-mcp/"' in assembler
    assert "install_portable_python" in assembler
    assert "portable_python_load_pin" in assembler
    assert "scripts/lib/portable-python.sh" in assembler
    assert "uv python install 3.12.3" not in assembler
    assert "PORTABLE_PYTHON_BIN" in assembler
    assert "python3.12" not in assembler


def test_linux_carrier_provisions_wasi_targets_for_the_assembler_toolchain(
    tmp_path: Path,
) -> None:
    """A divergent ambient rustup default must not hide a missing donor target."""
    workflow = (REPO_ROOT / ".github/workflows/install-linux.yml").read_text(
        encoding="utf-8"
    )
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )

    assert 'export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.97.0}"' in assembler
    assert 'RUSTUP_TOOLCHAIN: "1.97.0"' in workflow
    assert 'rustup toolchain install "$RUSTUP_TOOLCHAIN" --profile minimal' in workflow
    assert 'rustup target add --toolchain "$RUSTUP_TOOLCHAIN"' in workflow
    assert "wasm32-unknown-unknown wasm32-wasip1" in workflow
    assert 'rustup target list --installed --toolchain "$RUSTUP_TOOLCHAIN"' in workflow
    assert 'grep -Fx "$target"' in workflow
    assert "rustup target add wasm32-unknown-unknown wasm32-wasip1" not in workflow

    # Exercise the actual workflow preflight body with an ambient default that
    # never participates. The fake rustup exposes only the selected toolchain;
    # omitting WASI must fail here, before any carrier cargo invocation.
    preflight_start = workflow.index("          rustup toolchain install")
    preflight_end = workflow.index("          curl -L", preflight_start)
    preflight = "\n".join(
        line.removeprefix("          ")
        for line in workflow[preflight_start:preflight_end].splitlines()
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "rustup-calls"
    _executable(
        fake_bin / "rustup",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$RUSTUP_CALLS"\n'
        'case "$1 $2" in\n'
        '  "toolchain install"|"target add") exit 0 ;;\n'
        '  "target list")\n'
        "    echo wasm32-unknown-unknown\n"
        '    [ "${MISSING_WASI:-0}" = 1 ] || echo wasm32-wasip1\n'
        "    exit 0 ;;\n"
        "esac\n"
        "exit 99\n",
    )
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RUSTUP_TOOLCHAIN": "1.97.0",
        "RUSTUP_CALLS": str(calls),
    }
    success = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{preflight}"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert success.returncode == 0, success.stderr
    assert "target add --toolchain 1.97.0" in calls.read_text(encoding="utf-8")

    missing = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{preflight}"],
        env={**env, "MISSING_WASI": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "FATAL: wasm32-wasip1 missing from 1.97.0" in missing.stderr


def test_linux_x86_64_host_expects_carrier_architecture_x64() -> None:
    """uname -m is x86_64; the Runtime Pack carrier slug is x64 / linux-x64."""
    installer = (REPO_ROOT / "scripts/install-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    assert 'expected_architecture="x64"' in installer
    assert 'expected_architecture="x86_64"' not in installer


def test_make_runtime_pack_on_linux_takes_the_install_lane() -> None:
    """`make runtime-pack` on Linux used to run the macOS release builder.

    That builder hard-codes darwin-arm64, so a Linux host died with
    "supports only darwin-arm64" and bare `make install` then refused the
    pending selection record. Linux now routes to its own assembler lane.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("\nruntime-pack:\n", 1)[1].split("\n\n", 1)[0]

    assert (
        "LINUX_RUNTIME_PACK_SCRIPT := scripts/build-linux-runtime-pack.sh" in makefile
    )
    assert '[ "$$(uname -s)" = Linux ]' in target
    assert 'bash "$(LINUX_RUNTIME_PACK_SCRIPT)" --for-install' in target
    assert target.index("--for-install") < target.index("--runtime-pack-only")
    assert "runtime_pack_selection_read" in target

    prereqs = makefile.split("\nrelease-prereqs:\n", 1)[1].split("\n\n", 1)[0]
    linux_exit = prereqs.index('if [ "$$(uname -s)" != Darwin ]; then')
    assert linux_exit < prereqs.index("command -v rustup")
    assert linux_exit < prereqs.index("Release prerequisites ready")


def test_linux_install_lane_refuses_before_it_claims_the_record() -> None:
    wrapper = (REPO_ROOT / "scripts/build-linux-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    begin = wrapper.index("runtime_pack_selection_begin")
    for refusal in (
        'git -C "$repo_root" diff --quiet HEAD --',
        "release signing key missing",
        "missing build tools",
    ):
        assert wrapper.index(refusal) < begin, refusal
    assert 'source_revision="$(git -C "$repo_root" rev-parse HEAD)"' in wrapper
    assert 'VIBECRAFTED_RUNTIME_PACK_SELECTION_ATTEMPT="$attempt"' in wrapper
    # The CI lane (an output path, no flag) still reaches the assembler as-is.
    assert '[[ "${1:-}" == "--for-install" ]] || exec "$assembler" "$@"' in wrapper


def test_linux_assembler_publishes_only_a_pack_the_installer_will_trust() -> None:
    assembler = (REPO_ROOT / "scripts/build-linux-arm64-runtime-pack.sh").read_text(
        encoding="utf-8"
    )
    lane = assembler.split(
        'if [[ -n "${VIBECRAFTED_RUNTIME_PACK_SELECTION_ATTEMPT:-}" ]]; then', 1
    )[1]
    sign = lane.index("openssl dgst -sha256 -sign")
    verify = lane.index("openssl dgst -sha256 -verify")
    publish = lane.index("runtime_pack_selection_publish")
    assert sign < verify < publish
    assert "trust/vibecrafted-signing-v1.pub" in lane
    assert (
        'rustup target add --toolchain "$RUSTUP_TOOLCHAIN" \\\n'
        "    wasm32-unknown-unknown wasm32-wasip1" in assembler
    )


def test_linux_install_lane_refuses_a_non_linux_host() -> None:
    if os.uname().sysname == "Linux":
        pytest.skip("the refusal under test is the non-Linux one")
    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/build-linux-runtime-pack.sh"),
            "--for-install",
        ],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "--for-install runs natively on Linux" in result.stderr
