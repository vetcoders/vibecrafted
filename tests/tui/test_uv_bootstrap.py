"""Smoke tests for the uv-bootstrap path in the Makefile and install.sh.

P1-01 regression: installer Makefile targets used to split the
`uv` bootstrap `if` block and the `uv run ...` invocation across two
separate `@`-prefixed recipe lines. Make spawns a fresh shell per line,
so `export PATH="$HOME/.local/bin:$PATH"` never reached the `uv run` leg
and the recipe silently failed on hosts without preinstalled `uv`.

P3-01 regression: install.sh used `grep -oP 'VERSION\\s*:?=\\s*\\K\\S+'`
against Makefile, but the Makefile never defined `VERSION :=`. Result:
the post-install banner fell back to `basename archive_url .tar.gz`
(often `main`) and lied about the installed version. Repo truth lives
in the `VERSION` file at the root.

This module verifies:
1. Makefile's installer recipes keep bootstrap + PATH
   export + `uv run` in one continuous shell (single recipe line with
   backslash continuations).
2. Simulated no-uv PATH + `make -n vibecrafted` / `make -n install`
   prints the exported PATH before the `uv run` invocation, proving the
   shell boundary is respected.
3. install.sh reads `VERSION` directly, does not use `grep -oP`, and
   the staged banner reflects the repo's canonical version string.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
INSTALL_SH = REPO_ROOT / "install.sh"
VERSION_FILE = REPO_ROOT / "VERSION"
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


def _write_distribution_manifest_stub(source_dir: Path) -> None:
    _materialize_complete_distribution_fixture(source_dir)
    path = source_dir / "scripts" / "distribution_manifest.py"
    path.write_text(
        """#!/usr/bin/env python3
from pathlib import Path
import shutil
import sys

if sys.argv[1] == "check":
    raise SystemExit(0)
if sys.argv[1] != "stage":
    raise SystemExit(2)
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


# ---------------------------------------------------------------------------
# P1-01: Makefile shell-boundary fix
# ---------------------------------------------------------------------------


def _extract_recipe_body(text: str, target: str) -> str:
    """Return the recipe body (TAB-indented lines) for ``target:``.

    Stops at the first non-indented, non-empty line.
    """
    pattern = re.compile(
        rf"^{re.escape(target)}:[^\n]*\n((?:(?:\t[^\n]*|\s*)\n)+)",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        raise AssertionError(f"Recipe {target!r} not found in Makefile")
    return m.group(1)


def test_makefile_vibecrafted_aliases_install_front_door() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "vibecrafted: install" in text


def test_makefile_install_bootstrap_is_single_shell_stanza() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    body = _extract_recipe_body(text, "tui-installer")

    assert "fi; \\" in body, (
        "install recipe must continue the shell past the `fi` so the "
        "PATH export reaches `uv run`"
    )
    assert 'export PATH="$$HOME/.local/bin:$$PATH"' in body
    assert (
        "uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer "
        "$(MANIFEST) --quiet"
    ) in body
    assert "--yes" not in body
    recipe_lines = [ln for ln in body.splitlines() if ln.startswith("\t@")]
    assert len(recipe_lines) <= 1, (
        "install recipe regressed to multiple @-prefixed lines; each "
        "is a separate shell so PATH export will not survive."
    )


def test_makefile_install_auto_bootstrap_is_single_shell_stanza() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    body = _extract_recipe_body(text, "install-all")

    assert "fi; \\" in body, (
        "install-auto recipe must continue the shell past the `fi` so the "
        "PATH export reaches `uv run`"
    )
    assert 'export PATH="$$HOME/.local/bin:$$PATH"' in body
    assert (
        "uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer "
        "$(MANIFEST) --yes --quiet"
    ) in body
    recipe_lines = [ln for ln in body.splitlines() if ln.startswith("\t@")]
    assert len(recipe_lines) <= 1, (
        "install-auto recipe regressed to multiple @-prefixed lines; each "
        "is a separate shell so PATH export will not survive."
    )


def _make_dry_run(target: str, env: dict[str, str]) -> str:
    """Run `make -n <target>` and return combined stdout+stderr."""
    result = subprocess.run(
        ["make", "-n", target],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout + result.stderr


def _build_no_uv_env(tmp_path: Path, *, fake_uv: bool = False) -> dict[str, str]:
    """Return an environment where `uv` is either missing from PATH or
    shimmed to a no-op script under ``tmp_path/bin``.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)

    # Minimal system binaries the Makefile might call during dry-run.
    # `make -n` does NOT execute the recipe, but `make` evaluates any
    # `$(shell ...)` at parse time — we don't have any for these targets,
    # so a bare PATH is fine. We still populate sh, bash, awk, grep, sed,
    # env via symlinks to /bin/* or /usr/bin/* so that if make does try to
    # run anything, it can.
    for tool in (
        "sh",
        "bash",
        "make",
        "env",
        "awk",
        "grep",
        "sed",
        "tr",
        "cat",
        "echo",
        "printf",
        "test",
        "true",
        "false",
        "command",
        "rm",
        "chmod",
        "mkdir",
        "git",
    ):
        system = shutil.which(tool)
        if system:
            dst = fake_bin / tool
            if not dst.exists():
                os.symlink(system, dst)

    if fake_uv:
        _write_executable(
            fake_bin / "uv",
            "#!/usr/bin/env bash\nexit 0\n",
        )

    env = os.environ.copy()
    env["PATH"] = str(fake_bin)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    return env


def test_makefile_vibecrafted_dry_run_keeps_path_before_uv_run(
    tmp_path: Path,
) -> None:
    """`make -n vibecrafted` output must show the PATH export and the
    `uv run` invocation in the same shell line (backslash-continued),
    proving the recipe survives the no-uv bootstrap path.
    """
    env = _build_no_uv_env(tmp_path, fake_uv=False)
    out = _make_dry_run("tui-installer", env)

    # `make -n` emits the recipe text with continuations collapsed via
    # trailing `\`. Find the recipe block and confirm PATH export and
    # `uv run` are on the same continued line.
    assert "export PATH=" in out, out
    assert "uv run --project" in out, out

    # The PATH export and `uv run` must stay in a single shell chunk.
    idx_export = out.find("export PATH=")
    idx_uv = out.find("uv run --project")
    assert idx_export != -1 and idx_uv != -1
    assert idx_export < idx_uv
    # No blank line between `export PATH` and `uv run` — same shell.
    segment = out[idx_export:idx_uv]
    assert "\n\n" not in segment, (
        "Detected shell boundary between `export PATH` and `uv run` in make -n "
        f"output; PATH export would not survive. Segment:\n{segment!r}"
    )


def test_makefile_install_dry_run_keeps_path_before_uv_run(
    tmp_path: Path,
) -> None:
    env = _build_no_uv_env(tmp_path, fake_uv=False)
    out = _make_dry_run("tui-installer", env)

    assert "export PATH=" in out, out
    assert "uv run --project" in out, out
    assert "--quiet" in out, out
    installer_line = next(
        line for line in out.splitlines() if "vetcoders-installer install.toml" in line
    )
    assert "--yes" not in installer_line, out

    idx_export = out.find("export PATH=")
    idx_uv = out.find("uv run --project")
    assert idx_export != -1 and idx_uv != -1
    assert idx_export < idx_uv
    segment = out[idx_export:idx_uv]
    assert "\n\n" not in segment, (
        "Detected shell boundary between `export PATH` and `uv run` in install "
        f"recipe make -n output. Segment:\n{segment!r}"
    )


# ---------------------------------------------------------------------------
# P3-01: install.sh VERSION truth
# ---------------------------------------------------------------------------


def test_install_sh_does_not_use_grep_dash_p() -> None:
    """`grep -oP` is GNU-only; BSD grep on macOS does not accept it.

    Dropping -P is a correctness AND portability fix.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "grep -oP" not in text, (
        "install.sh must not rely on GNU grep -P (BSD grep on macOS "
        "rejects it silently)"
    )


def test_install_sh_reads_canonical_version_file() -> None:
    """install.sh must extract the installed version from the verified candidate
    VERSION file, not from the Makefile (which never defined VERSION).
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "candidate_root/VERSION" in text, (
        "install.sh must read VERSION directly from the verified candidate tree"
    )
    # No more archive_url fallback — the banner truth is the repo VERSION
    # file, not a tarball filename.
    assert 'basename "$archive_url" .tar.gz' not in text


def test_install_sh_reports_version_from_staged_tree(tmp_path: Path) -> None:
    """End-to-end: build a fake tarball with a VERSION file, run install.sh
    in archive-file mode, and verify the post-install banner prints that
    exact version string.
    """
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-main.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-args.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text(
        ".DEFAULT_GOAL := help\ninstall:\n\t@echo ok\n",
        encoding="utf-8",
    )
    (source_dir / "VERSION").write_text("9.9.9-test\n", encoding="utf-8")
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)

    _write_v2_archive(source_dir, archive_path, "vibecrafted-main")

    _write_executable(
        fake_bin / "make",
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\n" "$@" > "$MAKE_CAPTURE"\n',
    )
    _write_executable(
        fake_bin / "python3",
        f'#!/usr/bin/env bash\nset -euo pipefail\nexec "{sys.executable}" "$@"\n',
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--archive-file", str(archive_path), "install"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    banner = result.stdout + result.stderr
    assert "vibecrafted 9.9.9-test" in banner, (
        "install.sh banner should reflect the staged VERSION file content; "
        f"got:\n{banner}"
    )
    # Must not fall back to the archive basename.
    assert "vibecrafted vibecrafted-main" not in banner


def test_install_sh_rejects_distribution_without_required_version(
    tmp_path: Path,
) -> None:
    """A digest-consistent but structurally incomplete archive fails closed."""
    source_dir = tmp_path / "source"
    scripts_dir = source_dir / "scripts"
    archive_path = tmp_path / "vibecrafted-exotic.tar.gz"
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    make_capture = tmp_path / "make-args.txt"

    scripts_dir.mkdir(parents=True)
    fake_bin.mkdir()
    home.mkdir()

    (source_dir / "Makefile").write_text(
        ".DEFAULT_GOAL := help\ninstall:\n\t@echo ok\n",
        encoding="utf-8",
    )
    (scripts_dir / "placeholder").write_text("", encoding="utf-8")
    _write_distribution_manifest_stub(source_dir)
    (source_dir / "VERSION").unlink()
    provenance_path = source_dir / "source-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["payload"] = _fixture_tree_record(source_dir)
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    _write_v2_archive(source_dir, archive_path, "vibecrafted-exotic")

    _write_executable(
        fake_bin / "make",
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'printf "%s\\n" "$@" > "$MAKE_CAPTURE"\n',
    )
    _write_executable(
        fake_bin / "python3",
        f'#!/usr/bin/env bash\nset -euo pipefail\nexec "{sys.executable}" "$@"\n',
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["MAKE_CAPTURE"] = str(make_capture)

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--archive-file", str(archive_path), "install"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "missing required file: VERSION" in result.stderr
    assert not make_capture.exists()


def test_repo_version_file_exists_and_is_non_empty() -> None:
    """Sanity: if this test fails, the repo lost its canonical VERSION
    string and the real install.sh banner will read 'unknown'.
    """
    assert VERSION_FILE.is_file(), (
        "Repo must ship a VERSION file at the root; install.sh reads it "
        "directly for the post-install banner"
    )
    content = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert content, "VERSION file must not be empty"
    # Loose semver-ish check: digits and dots with optional suffix.
    assert re.match(r"^\d+\.\d+\.\d+", content), (
        f"VERSION content does not look like a semver string: {content!r}"
    )


# ---------------------------------------------------------------------------
# Opt-in end-to-end no-uv bootstrap (slow, network + installer side effects)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("VIBECRAFTED_RUN_NO_UV_E2E") != "1",
    reason=(
        "End-to-end `make install` without uv downloads the uv "
        "installer from astral.sh and mutates HOME. Set "
        "VIBECRAFTED_RUN_NO_UV_E2E=1 to opt in."
    ),
)
def test_make_install_no_uv_e2e(tmp_path: Path) -> None:  # pragma: no cover
    env = _build_no_uv_env(tmp_path, fake_uv=False)
    result = subprocess.run(
        ["make", "install"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert "bootstrapping uv" in (result.stdout + result.stderr)
    # We accept non-zero (the installer may exit based on its own logic),
    # but we REQUIRE that the `uv run` leg ran, i.e. the PATH export
    # survived. Look for installer-side output.
    combined = result.stdout + result.stderr
    assert "uv run" in combined or "vetcoders-installer" in combined, combined
