from __future__ import annotations

import ast
import errno
import hashlib
import io
import json
import os
import pty
import select
import shlex
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
TREE_DOMAIN = b"vibecrafted.distribution-tree.v1\0"
FIXTURE_REQUIRED_FILES = {
    "VERSION",
    "LICENSE",
    "README.md",
    "Makefile",
    "install.sh",
    "install.ps1",
    "install.toml",
    "scripts/distribution_manifest.py",
    "scripts/installer_brand.py",
    "scripts/vetcoders_install.py",
    "scripts/vibecrafted",
    "scripts/verify-vibecrafted-product.sh",
    "vibecrafted-core/pyproject.toml",
    "vibecrafted-core/vibecrafted_core/VERSION",
    "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
    "vibecrafted-core/vibecrafted_core/product_contract.py",
    "vibecrafted-core/vibecrafted_core/walkaround_runner.py",
    "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json",
    "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub",
    "vibecrafted-mcp/pyproject.toml",
    "plugins/iterm2/pyproject.toml",
    "vibecrafted-app/Cargo.toml",
    "vibecrafted-app/Cargo.lock",
    "vibecrafted-server/Cargo.toml",
    "vibecrafted-server/Cargo.lock",
}
FIXTURE_REQUIRED_SURFACES = {
    "bin/vc-workflow",
    "config/README.md",
    "docs/INSTALL.md",
    "plugins/iterm2/README.md",
    "vibecrafted-core/vibecrafted_core/runtime/scripts/README.md",
    "vibecrafted-core/vibecrafted_core/runtime/shell/lib/core.sh",
    "scripts/installer/pyproject.toml",
    "vibecrafted-core/vibecrafted_core/skills/vc-init/SKILL.md",
    "templates/hooks/install.sh",
    "tools/README.md",
    "vibecrafted-core/vibecrafted_core/runtime/README.md",
    "vibecrafted-core/vibecrafted_core/skills/LIVING_TREE_RULE.md",
    "vibecrafted-vm/Containerfile",
    "workflows/MARBLES.md",
}


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _materialize_complete_distribution_fixture(source_dir: Path) -> None:
    for relative in sorted(FIXTURE_REQUIRED_FILES | FIXTURE_REQUIRED_SURFACES):
        path = source_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(f"fixture: {relative}\n", encoding="utf-8")


def _fixture_tree_record(source_dir: Path) -> dict[str, object]:
    entries: list[tuple[bytes, bytes, int, bytes]] = []
    for current, directory_names, file_names in os.walk(source_dir, followlinks=False):
        current_path = Path(current)
        directory_names.sort()
        file_names.sort()
        for name in [*directory_names, *file_names]:
            path = current_path / name
            relative = path.relative_to(source_dir)
            if relative.as_posix() == "source-provenance.json":
                continue
            raw_path = relative.as_posix().encode("utf-8")
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                entries.append((raw_path, b"l", 0o777, os.readlink(path).encode()))
            elif stat.S_ISDIR(metadata.st_mode):
                entries.append((raw_path, b"d", 0o755, b""))
            else:
                raw = path.read_bytes()
                mode = 0o755 if metadata.st_mode & 0o111 else 0o644
                entries.append(
                    (
                        raw_path,
                        b"f",
                        mode,
                        struct.pack(">Q", len(raw)) + hashlib.sha256(raw).digest(),
                    )
                )
    entries.sort(key=lambda entry: entry[0])
    digest = hashlib.sha256(TREE_DOMAIN)
    digest.update(struct.pack(">Q", len(entries)))
    for raw_path, kind, mode, payload in entries:
        digest.update(kind)
        digest.update(struct.pack(">Q", len(raw_path)))
        digest.update(raw_path)
        digest.update(struct.pack(">I", mode))
        digest.update(struct.pack(">Q", len(payload)))
        digest.update(payload)
    return {
        "schema": "vibecrafted.distribution-tree.v1",
        "algorithm": "sha256",
        "tree_sha256": digest.hexdigest(),
        "entry_count": len(entries),
    }


def _write_distribution_manifest_stub(
    source_dir: Path,
    *,
    stage_marker: Path | None = None,
    mutate_candidate: bool = False,
    usercustomize_marker: Path | None = None,
) -> None:
    _materialize_complete_distribution_fixture(source_dir)
    path = source_dir / "scripts" / "distribution_manifest.py"
    marker_statement = (
        f"Path({str(stage_marker)!r}).write_text('stage-ran\\n', encoding='utf-8')"
        if stage_marker is not None
        else "pass"
    )
    mutation_statement = (
        "(destination / 'Makefile').write_text('mutated after stage\\n', encoding='utf-8')"
        if mutate_candidate
        else "pass"
    )
    if usercustomize_marker is None:
        usercustomize_statement = "pass"
    else:
        poison = (
            "import os\n"
            "from pathlib import Path\n"
            f"Path({str(usercustomize_marker)!r}).write_text("
            "'executed\\n', encoding='utf-8')\n"
            "os._exit(0)\n"
        )
        usercustomize_statement = (
            "user_site = Path(site.getusersitepackages())\n"
            "user_site.mkdir(parents=True, exist_ok=True)\n"
            f"(user_site / 'usercustomize.py').write_text({poison!r}, encoding='utf-8')"
        )
    path.write_text(
        f"""#!/usr/bin/env python3
from pathlib import Path
import shutil
import site
import sys

if sys.argv[1] == "check":
    raise SystemExit(0)
if sys.argv[1] != "stage":
    raise SystemExit(2)
{marker_statement}
source = Path(sys.argv[sys.argv.index("--source") + 1])
destination = Path(sys.argv[sys.argv.index("--destination") + 1])
if "--require-source-provenance" in sys.argv:
    provenance = source / "source-provenance.json"
    if not provenance.is_file():
        print("missing required path: source-provenance.json", file=sys.stderr)
        raise SystemExit(2)
if destination.exists():
    shutil.rmtree(destination)
shutil.copytree(source, destination, symlinks=True)
{mutation_statement}
{usercustomize_statement}
""",
        encoding="utf-8",
    )
    provenance = {
        "schema": "vibecrafted.source-provenance.v2",
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": "0123456789abcdef0123456789abcdef01234567",
        "payload": _fixture_tree_record(source_dir),
    }
    (source_dir / "source-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    (source_dir / "source-provenance.json").chmod(0o644)


def _canonical_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    if info.isdir():
        info.mode = 0o755
    elif info.issym():
        info.mode = 0o777
    elif info.isreg():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def _write_v2_archive(source_dir: Path, archive_path: Path, root_name: str) -> None:
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(
            source_dir,
            arcname=root_name,
            recursive=True,
            filter=_canonical_tar_info,
        )


def _bootstrap_archive(
    tmp_path: Path, archive_path: Path
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bootstrap-bin"
    home = tmp_path / "bootstrap-home"
    make_capture = tmp_path / "bootstrap-make.txt"
    fake_bin.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    _write_executable(
        fake_bin / "make",
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\n" "$@" > "$MAKE_CAPTURE"\n',
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)
    return subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--yes",
            "--archive-file",
            str(archive_path),
            "install",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _append_archive_member(
    archive_path: Path,
    output_path: Path,
    member: tarfile.TarInfo,
    payload: bytes = b"",
) -> None:
    with (
        tarfile.open(archive_path, "r:gz") as source,
        tarfile.open(output_path, "w:gz") as destination,
    ):
        for existing in source.getmembers():
            extracted = source.extractfile(existing) if existing.isreg() else None
            destination.addfile(existing, extracted)
        destination.addfile(member, io.BytesIO(payload) if member.isreg() else None)


def _minimal_v2_source(
    tmp_path: Path,
    *,
    stage_marker: Path | None = None,
    mutate_candidate: bool = False,
    usercustomize_marker: Path | None = None,
) -> Path:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (source_dir / "Makefile").write_text("install:\n\t@echo ok\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("fixture\n", encoding="utf-8")
    _write_distribution_manifest_stub(
        source_dir,
        stage_marker=stage_marker,
        mutate_candidate=mutate_candidate,
        usercustomize_marker=usercustomize_marker,
    )
    return source_dir


def _run_with_tty(
    command: str, *, response: str | None = None, timeout: float = 10.0
) -> tuple[int, str]:
    pid, fd = pty.fork()
    if pid == 0:
        os.execlp("bash", "bash", "-lc", command)

    output = bytearray()
    sent_response = response is None
    deadline = time.monotonic() + timeout
    wait_status: int | None = None

    while wait_status is None:
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            _, wait_status = os.waitpid(pid, 0)
            raise AssertionError(f"Timed out waiting for command: {command}")

        finished_pid, status = os.waitpid(pid, os.WNOHANG)
        if finished_pid == pid:
            wait_status = status
            break

        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            continue

        try:
            chunk = os.read(fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                continue
            raise

        if not chunk:
            continue

        output.extend(chunk)
        if not sent_response and b"Proceed? [y/N]" in output:
            os.write(fd, f"{response}\n".encode())
            sent_response = True

    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        output.extend(chunk)

    os.close(fd)
    assert wait_status is not None
    return os.waitstatus_to_exitcode(wait_status), output.decode("utf-8", "replace")


def test_install_sh_blocks_raw_github_fallback_without_channel_archive(
    tmp_path: Path,
) -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'channel_url="https://vibecrafted.io/channel/${ref}.json"' in text
    assert "refusing the untrusted raw GitHub fallback" in text
    assert "W4 release authentication blocker" in text
    assert "archive/refs/heads/${ref}.tar.gz" not in text
    assert "api.github.com/repos" not in text

    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    capture = tmp_path / "curl-args.txt"
    fake_bin.mkdir()
    home.mkdir()
    _write_executable(
        fake_bin / "curl",
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\n" "$*" >> "$CURL_CAPTURE"\n'
        "printf '{}\\n'\n",
    )
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["CURL_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--yes"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "refusing the untrusted raw GitHub fallback" in result.stderr
    assert "W4 release authentication blocker" in result.stderr
    assert "api.github.com" not in capture.read_text(encoding="utf-8")


def test_install_sh_help_documents_runtime_flag() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        check=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert "--runtime <horse>" in result.stdout
    assert "wezterm, vc-apprt, locterm, microsandbox, or none" in result.stdout
    assert "--archive-file" in result.stdout
    assert "require the closed" in result.stdout
    assert "source-provenance.json" in result.stdout
    assert "proves every included byte against the claimed commit" in result.stdout
    assert "scripts/distribution_manifest.py archive" in result.stdout


def test_install_sh_quiets_tar_xattr_noise_and_hides_make_directory_trace() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert "tar --warning=no-unknown-keyword" in text
    assert "COPYFILE_DISABLE=1 tar" in text
    assert "make --no-print-directory -C" in text


def test_install_sh_stages_archives_through_distribution_manifest() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")

    assert 'manifest_helper="$source_dir/scripts/distribution_manifest.py"' in text
    assert "stage_args=(" in text
    assert '"$bootstrap_python" -I -S "$manifest_helper" "${stage_args[@]}"' in text
    assert '"$bootstrap_python" -I -S - "$operation" "$@"' in text
    assert '"$bootstrap_python" -I -S - "$source" "$destination"' in text
    assert '--source "$source_dir"' in text
    assert 'candidate_root="$tmpdir/candidate"' in text
    assert '--destination "$candidate_root"' in text
    assert "--require-source-provenance" in text
    assert 'bootstrap_integrity_preflight archive "$local_archive"' in text
    assert 'bootstrap_integrity_preflight tree "$source_dir"' in text
    assert 'bootstrap_integrity_preflight tree "$candidate_root"' in text
    assert 'ln -sfn "$staged_dir" "$current_link"' not in text
    assert 'rm -rf "$staged_dir"' not in text
    assert 'mv "$source_dir"' not in text


def test_bootstrap_required_shape_mirrors_distribution_writer() -> None:
    from scripts import distribution_manifest as writer

    text = INSTALL_SH.read_text(encoding="utf-8")
    embedded = text.split("<<'PY_BOOTSTRAP_INTEGRITY'\n", 1)[1].split(
        "\nPY_BOOTSTRAP_INTEGRITY", 1
    )[0]
    tree = ast.parse(embedded)
    assignments: dict[str, object] = {}
    wanted = {
        "SOURCE_PROVENANCE_FILE",
        "SOURCE_PROVENANCE_SCHEMA",
        "TREE_SCHEMA",
        "TREE_ALGORITHM",
        "TREE_DOMAIN",
        "REQUIRED_FILES",
        "REQUIRED_DIRECTORIES",
        "REQUIRED_SURFACE_FILES",
        "ALLOWED_TOP_LEVEL",
        "FORBIDDEN_COMPONENTS",
        "FORBIDDEN_SUFFIXES",
        "REQUIRED_LOCKFILES",
    }

    class ResolveConstants(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.expr:
            if node.id in assignments:
                return ast.copy_location(ast.Constant(assignments[node.id]), node)
            return node

    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in wanted:
            expression = ResolveConstants().visit(ast.fix_missing_locations(node.value))
            if (
                isinstance(expression, ast.Call)
                and isinstance(expression.func, ast.Name)
                and expression.func.id == "frozenset"
            ):
                assignments[target.id] = frozenset(ast.literal_eval(expression.args[0]))
            else:
                assignments[target.id] = ast.literal_eval(expression)

    assert assignments["SOURCE_PROVENANCE_FILE"] == writer.SOURCE_PROVENANCE_FILE
    assert assignments["SOURCE_PROVENANCE_SCHEMA"] == writer.SOURCE_PROVENANCE_SCHEMA
    assert assignments["TREE_SCHEMA"] == writer.DISTRIBUTION_TREE_SCHEMA
    assert assignments["TREE_ALGORITHM"] == writer.DISTRIBUTION_TREE_ALGORITHM
    assert assignments["TREE_DOMAIN"] == writer.DISTRIBUTION_TREE_DOMAIN
    assert set(assignments["REQUIRED_FILES"]) == set(writer.REQUIRED_FILES)
    assert set(assignments["REQUIRED_DIRECTORIES"]) == set(writer.REQUIRED_DIRECTORIES)
    assert set(assignments["REQUIRED_SURFACE_FILES"]) == set(
        writer.REQUIRED_SURFACE_FILES.values()
    )
    assert assignments["ALLOWED_TOP_LEVEL"] == writer.ALLOWED_TOP_LEVEL
    assert assignments["FORBIDDEN_COMPONENTS"] == writer.FORBIDDEN_COMPONENTS
    assert assignments["FORBIDDEN_SUFFIXES"] == writer.FORBIDDEN_SUFFIXES
    assert assignments["REQUIRED_LOCKFILES"] == writer.REQUIRED_LOCKFILES


@pytest.mark.parametrize("carrier_shape", ["missing", "v1", "open"])
def test_bootstrap_rejects_non_v2_carrier_before_candidate_helper(
    tmp_path: Path, carrier_shape: str
) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(tmp_path, stage_marker=marker)
    carrier_path = source_dir / "source-provenance.json"
    if carrier_shape == "missing":
        carrier_path.unlink()
    elif carrier_shape == "v1":
        carrier_path.write_text(
            json.dumps(
                {
                    "schema": "vibecrafted.source-provenance.v1",
                    "owner_repo": "vetcoders/vibecrafted",
                    "source_revision": "0123456789abcdef0123456789abcdef01234567",
                },
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        payload = json.loads(carrier_path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        carrier_path.write_text(
            json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    archive_path = tmp_path / f"carrier-{carrier_shape}.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode != 0
    assert "bootstrap-owned source-provenance v2 preflight" in result.stderr
    assert not marker.exists(), "candidate helper ran before carrier rejection"


def test_bootstrap_rejects_member_mutation_with_unchanged_carrier(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(tmp_path, stage_marker=marker)
    (source_dir / "Makefile").write_text("mutated archive bytes\n", encoding="utf-8")
    archive_path = tmp_path / "mutated.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode != 0
    assert "distribution tree digest mismatch" in result.stderr
    assert not marker.exists(), "candidate helper ran before member digest rejection"


def test_bootstrap_rejects_digest_consistent_incomplete_shape_before_helper(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(tmp_path, stage_marker=marker)
    (source_dir / "LICENSE").unlink()
    provenance_path = source_dir / "source-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["payload"] = _fixture_tree_record(source_dir)
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    archive_path = tmp_path / "missing-license.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode != 0
    assert "missing required file: LICENSE" in result.stderr
    assert not marker.exists(), "candidate helper ran before shape rejection"


@pytest.mark.parametrize(
    "unsafe_shape",
    ["symlink", "hardlink", "traversal", "duplicate", "fifo", "noncanonical-mode"],
)
def test_bootstrap_rejects_unsafe_tar_shape_before_candidate_helper(
    tmp_path: Path, unsafe_shape: str
) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(tmp_path, stage_marker=marker)
    archive_path = tmp_path / "valid.tar.gz"
    if unsafe_shape == "symlink":
        (source_dir / "scripts" / "escape").symlink_to("../../../outside")
        _write_distribution_manifest_stub(source_dir, stage_marker=marker)
        _write_v2_archive(source_dir, archive_path, "vibecrafted-main")
        candidate_archive = archive_path
    else:
        _write_v2_archive(source_dir, archive_path, "vibecrafted-main")
        candidate_archive = tmp_path / f"unsafe-{unsafe_shape}.tar.gz"
        if unsafe_shape == "hardlink":
            member = tarfile.TarInfo("vibecrafted-main/scripts/hardlink")
            member.type = tarfile.LNKTYPE
            member.linkname = "vibecrafted-main/Makefile"
            member.mode = 0o644
            payload = b""
        elif unsafe_shape == "traversal":
            member = tarfile.TarInfo("vibecrafted-main/scripts/../escape")
            member.type = tarfile.REGTYPE
            member.mode = 0o644
            payload = b"escape\n"
            member.size = len(payload)
        elif unsafe_shape == "duplicate":
            member = tarfile.TarInfo("vibecrafted-main/Makefile")
            member.type = tarfile.REGTYPE
            member.mode = 0o644
            payload = b"duplicate\n"
            member.size = len(payload)
        elif unsafe_shape == "fifo":
            member = tarfile.TarInfo("vibecrafted-main/scripts/fifo")
            member.type = tarfile.FIFOTYPE
            member.mode = 0o644
            payload = b""
        else:
            member = tarfile.TarInfo("vibecrafted-main/scripts/noncanonical-mode")
            member.type = tarfile.REGTYPE
            member.mode = 0o600
            payload = b"mode\n"
            member.size = len(payload)
        _append_archive_member(archive_path, candidate_archive, member, payload)

    result = _bootstrap_archive(tmp_path, candidate_archive)

    assert result.returncode != 0
    assert "bootstrap-owned source-provenance v2 preflight" in result.stderr
    assert not marker.exists(), "candidate helper ran before unsafe archive rejection"


def test_valid_v2_archive_reaches_candidate_stage(tmp_path: Path) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(tmp_path, stage_marker=marker)
    archive_path = tmp_path / "valid.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "stage-ran\n"


def test_candidate_usercustomize_cannot_short_circuit_post_stage_verifier(
    tmp_path: Path,
) -> None:
    stage_marker = tmp_path / "stage-ran.txt"
    customize_marker = tmp_path / "usercustomize-ran.txt"
    source_dir = _minimal_v2_source(
        tmp_path,
        stage_marker=stage_marker,
        usercustomize_marker=customize_marker,
    )
    archive_path = tmp_path / "usercustomize.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stage_marker.read_text(encoding="utf-8") == "stage-ran\n"
    assert not customize_marker.exists()


def test_candidate_helper_cannot_bypass_post_stage_tree_recheck(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "stage-ran.txt"
    source_dir = _minimal_v2_source(
        tmp_path,
        stage_marker=marker,
        mutate_candidate=True,
    )
    archive_path = tmp_path / "malicious-helper.tar.gz"
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    result = _bootstrap_archive(tmp_path, archive_path)

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "stage-ran\n"
    assert (
        "Staged candidate failed the bootstrap-owned integrity recheck" in result.stderr
    )
    assert not (tmp_path / "bootstrap-make.txt").exists()


def test_install_sh_attended_pipe_requires_explicit_yes_before_staging(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    home = tmp_path / "home"

    scripts_dir.mkdir(parents=True)
    home.mkdir()

    (source_dir / "Makefile").write_text("install:\n\t@echo ok\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    command = " ; ".join(
        [
            f"export HOME={shlex.quote(str(home))}",
            f"export XDG_CONFIG_HOME={shlex.quote(str(home / '.config'))}",
            f"export VIBECRAFTED_HOME={shlex.quote(str(home / '.vibecrafted'))}",
            "export PATH=/usr/bin:/bin:/usr/sbin:/sbin",
            (
                f"printf '' | bash {shlex.quote(str(INSTALL_SH))}"
                f" --archive-file {shlex.quote(str(archive_path))}"
            ),
        ]
    )

    exit_code, output = _run_with_tty(command, response="n")

    staged_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    assert exit_code == 0
    assert "⚒ 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. →" in output
    assert "unpack · verify · transact ·" in output
    assert "Proceed? [y/N]" in output
    assert "Cancelled." in output
    assert not staged_root.exists()


def test_install_sh_yes_skips_attended_prompt_for_pipe_bootstrap(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-ran.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text(
        "install-auto:\n\t@printf 'install-auto RUNTIME=$(RUNTIME)\\n' > $(MAKE_CAPTURE)\n",
        encoding="utf-8",
    )
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    (scripts_dir / "vetcoders_install.py").write_text("# compact\n", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    command = " ; ".join(
        [
            f"export HOME={shlex.quote(str(home))}",
            f"export XDG_CONFIG_HOME={shlex.quote(str(home / '.config'))}",
            f"export VIBECRAFTED_HOME={shlex.quote(str(home / '.vibecrafted'))}",
            f"export PATH={shlex.quote(f'{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin')}",
            f"export MAKE_CAPTURE={shlex.quote(str(make_capture))}",
            (
                f"printf '' | bash {shlex.quote(str(INSTALL_SH))}"
                f" --archive-file {shlex.quote(str(archive_path))} --yes"
            ),
        ]
    )

    exit_code, output = _run_with_tty(command)

    staged_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    assert exit_code == 0
    assert "Proceed? [y/N]" not in output
    assert "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. bootstrap" not in output
    assert "Running     compact installer" not in output
    assert "Non-interactive bootstrap detected" not in output
    assert "Launching installer:" not in output
    assert not staged_root.exists()
    assert make_capture.read_text(encoding="utf-8") == "install-auto RUNTIME=none\n"


def test_install_sh_runtime_flag_dispatches_staged_runtime_helper(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-ran.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text(
        "install-auto:\n\t@printf 'install-auto RUNTIME=$(RUNTIME)\\n' > $(MAKE_CAPTURE)\n",
        encoding="utf-8",
    )
    (scripts_dir / "vetcoders_install.py").write_text("# compact\n", encoding="utf-8")
    (scripts_dir / "install-runtime.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$RUNTIME_CAPTURE"\n',
        encoding="utf-8",
    )
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)

    subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--archive-file",
            str(archive_path),
            "--runtime",
            "wezterm",
            "--yes",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    staged_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    assert not staged_root.exists()
    assert make_capture.read_text(encoding="utf-8") == "install-auto RUNTIME=wezterm\n"


def test_install_sh_archive_install_runs_local_make_target(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-args.txt"
    python_capture = tmp_path / "python-called.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text("install:\n\t@echo ok\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    _write_executable(
        fake_bin / "make",
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" > "$MAKE_CAPTURE"'
        + "\n",
    )
    _write_executable(
        fake_bin / "python3",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'probe="$1"',
                'if [[ "$1" == "-I" && "$2" == "-S" ]]; then probe="$3"; fi',
                f'if [[ "$probe" == "-" || "$probe" == */distribution_manifest.py ]]; then exec {shlex.quote(sys.executable)} "$@"; fi',
                'printf "unexpected\\n" > "$PYTHON_CAPTURE"',
                "exit 97",
            ]
        )
        + "\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)
    env["PYTHON_CAPTURE"] = str(python_capture)

    subprocess.run(
        ["bash", str(INSTALL_SH), "--archive-file", str(archive_path), "install"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    staged_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    make_args = make_capture.read_text(encoding="utf-8").splitlines()
    assert make_args[:2] == ["--no-print-directory", "-C"]
    candidate_root = Path(make_args[2])
    assert candidate_root.name == "candidate"
    assert candidate_root.parent.name.startswith("vibecrafted-bootstrap.")
    assert make_args[3:] == ["install"]
    assert not candidate_root.exists()
    assert not staged_root.exists()
    assert not python_capture.exists()


def test_install_sh_same_ref_handoff_never_mutates_live_pointer(
    tmp_path: Path,
) -> None:
    """A bootstrap candidate must stay private until the leased installer
    publishes it.

    Block the candidate command at the transaction handoff, then prove that a
    same-ref live generation and `vibecrafted-current` remain byte-for-byte
    available both while the handoff is blocked and after it fails.
    """
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    live_target = tools / "vibecrafted-main"
    current = tools / "vibecrafted-current"
    ready = tmp_path / "make-ready"
    release = tmp_path / "make-release"
    candidate_capture = tmp_path / "candidate-root.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    live_target.mkdir(parents=True)
    (live_target / "identity.txt").write_text("still-live\n", encoding="utf-8")
    current.symlink_to(live_target)

    (source_dir / "Makefile").write_text("install:\n\t@echo no\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)
    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    _write_executable(
        fake_bin / "make",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == "--no-print-directory" && "$2" == "-C" ]]
printf '%s\\n' "$3" > "$CANDIDATE_CAPTURE"
: > "$MAKE_READY"
while [[ ! -e "$MAKE_RELEASE" ]]; do
  sleep 0.02
done
exit 73
""",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["CANDIDATE_CAPTURE"] = str(candidate_capture)
    env["MAKE_READY"] = str(ready)
    env["MAKE_RELEASE"] = str(release)

    process = subprocess.Popen(
        ["bash", str(INSTALL_SH), "--archive-file", str(archive_path), "install"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None:
            assert time.monotonic() < deadline, "bootstrap never reached handoff"
            time.sleep(0.02)

        assert process.poll() is None, process.communicate()
        candidate_root = Path(candidate_capture.read_text(encoding="utf-8").strip())
        assert candidate_root.name == "candidate"
        assert candidate_root.is_dir()
        assert current.is_symlink()
        assert current.resolve() == live_target.resolve()
        assert (current / "identity.txt").read_text(encoding="utf-8") == "still-live\n"

        release.touch()
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 73, stdout + stderr
        assert current.is_symlink()
        assert current.resolve() == live_target.resolve()
        assert (live_target / "identity.txt").read_text(
            encoding="utf-8"
        ) == "still-live\n"
        assert not candidate_root.exists()
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


def test_install_sh_gui_bootstrap_runs_local_guided_installer(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    python_capture = tmp_path / "python-args.txt"
    make_capture = tmp_path / "make-args.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text("install:\n\t@echo ok\n", encoding="utf-8")
    (scripts_dir / "installer_gui.py").write_text("# gui\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    _write_executable(
        fake_bin / "make",
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" > "$MAKE_CAPTURE"'
        + "\n",
    )
    _write_executable(
        fake_bin / "python3",
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'probe="$1"',
                'if [[ "$1" == "-I" && "$2" == "-S" ]]; then probe="$3"; fi',
                f'if [[ "$probe" == "-" || "$probe" == */distribution_manifest.py ]]; then exec {shlex.quote(sys.executable)} "$@"; fi',
                'printf "%s\\n" "$@" > "$PYTHON_CAPTURE"',
            ]
        )
        + "\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["PYTHON_CAPTURE"] = str(python_capture)
    env["MAKE_CAPTURE"] = str(make_capture)

    subprocess.run(
        ["bash", str(INSTALL_SH), "--archive-file", str(archive_path), "--gui"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    staged_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    python_args = python_capture.read_text(encoding="utf-8").splitlines()
    candidate_root = Path(python_args[2])
    assert Path(python_args[0]) == candidate_root / "scripts" / "installer_gui.py"
    assert python_args[1] == "--source"
    assert candidate_root.name == "candidate"
    assert candidate_root.parent.name.startswith("vibecrafted-bootstrap.")
    assert not candidate_root.exists()
    assert not staged_root.exists()
    assert not make_capture.exists()


# ---------------------------------------------------------------------------
# W3-A — installer storytelling contract: calm default, VERBOSE=1 superset
# ---------------------------------------------------------------------------


def _run_storytelling_bootstrap(
    tmp_path: Path, *, verbose: bool
) -> subprocess.CompletedProcess:
    """Run install.sh end-to-end in archive-file mode with a stubbed `make`.

    Reuses one HOME across calls so the default and VERBOSE runs emit
    line-for-line comparable output (identical paths, idempotent staging).
    """
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-bootstrap.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-args.txt"

    if not archive_path.exists():
        scripts_dir.mkdir(parents=True)
        fake_bin.mkdir()
        home.mkdir()
        (source_dir / "Makefile").write_text("install:\n\t@echo ok\n", encoding="utf-8")
        (source_dir / "VERSION").write_text("9.9.9-test\n", encoding="utf-8")
        (scripts_dir / "placeholder").write_text("", encoding="utf-8")
        _write_distribution_manifest_stub(source_dir)
        _write_v2_archive(source_dir, archive_path, "vibecrafted-main")
        _write_executable(
            fake_bin / "make",
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'printf "%s\\n" "$@" > "$MAKE_CAPTURE"\n',
        )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)
    env.pop("VERBOSE", None)
    if verbose:
        env["VERBOSE"] = "1"

    return subprocess.run(
        [
            "bash",
            str(INSTALL_SH),
            "--yes",
            "--archive-file",
            str(archive_path),
            "install",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_install_sh_default_output_fits_the_ten_line_budget(tmp_path: Path) -> None:
    """Operator contract (W3-A): the default bootstrap view is storytelling —
    ≤10 lines total, each section adding ≤2 lines. The bazaar is VERBOSE=1."""
    result = _run_storytelling_bootstrap(tmp_path, verbose=False)

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) <= 10, (
        "default install.sh output must stay within the 10-line budget; "
        f"got {len(lines)} lines:\n" + "\n".join(lines)
    )
    # The staging truth still lands in the calm view.
    assert any("vibecrafted 9.9.9-test" in line for line in lines)


def test_install_sh_verbose_output_is_a_superset_of_default(tmp_path: Path) -> None:
    """VERBOSE=1 restores the full detail without losing a single line of the
    default storytelling view."""
    default_out = _run_storytelling_bootstrap(tmp_path, verbose=False).stdout
    verbose_out = _run_storytelling_bootstrap(tmp_path, verbose=True).stdout

    default_lines = {line for line in default_out.splitlines() if line.strip()}
    verbose_lines = {line for line in verbose_out.splitlines() if line.strip()}

    missing = default_lines - verbose_lines
    assert not missing, f"VERBOSE=1 dropped default storytelling lines: {missing}"
    assert len(verbose_lines) > len(default_lines), (
        "VERBOSE=1 must restore the gated detail (strict superset)"
    )


def test_compact_onboarding_ends_with_finish_card_not_log_tail() -> None:
    """CLI_PRODUCT_SPEC §6.1: the compact install ends with the bounded finish
    card (result · key facts · one next step). The 12-line inner log viewer is
    retired — the full transaction log stays on disk and errors point at it."""
    text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(encoding="utf-8")
    assert "_tail[-12:]" not in text
    assert "Finish card (CLI_PRODUCT_SPEC §6.1)" in text
    assert "vibecrafted init claude" in text
