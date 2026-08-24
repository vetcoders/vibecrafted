from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from scripts import distribution_manifest as distribution
from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_granular_installer_resolves_the_distribution_root(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "python-argv.txt"
    python = fake_bin / "python3"
    python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE"\n',
        encoding="utf-8",
    )
    python.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            str(
                REPO_ROOT
                / "vibecrafted-core/vibecrafted_core/runtime/scripts/install.sh"
            ),
            "--source",
            str(REPO_ROOT),
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "CAPTURE": str(capture),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(REPO_ROOT / "scripts/vetcoders_install.py"),
        "install",
        "--source",
        str(REPO_ROOT),
        "--dry-run",
    ]


def _minimal_distribution_source(root: Path) -> None:
    for relative in distribution.REQUIRED_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture for {relative}\n", encoding="utf-8")
    for relative in distribution.REQUIRED_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)
    for relative in distribution.REQUIRED_SURFACE_FILES.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"runtime sentinel for {relative}\n", encoding="utf-8")


def test_makefile_python_runner_rejects_xcode_python_39() -> None:
    """The Make front door must not trust macOS's ambient `python3`."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    runner = REPO_ROOT / "scripts" / "project-python"

    assert "PYTHON   ?= $(CURDIR)/scripts/project-python" in makefile
    assert runner.stat().st_mode & 0o111

    result = subprocess.run(
        [str(runner), "-c", "import sys, tomllib; print(sys.version_info[:2])"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("(3, 1")


def test_installer_smokes_the_packaged_walkaround_entrypoint() -> None:
    assert "verify-vibecrafted-walkaround" in installer.PYTHON_ENTRYPOINT_LAUNCHERS
    assert (
        "verify-vibecrafted-walkaround" in installer._installer_managed_launcher_names()
    )
    pyproject = (REPO_ROOT / "vibecrafted-core/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert (
        'verify-vibecrafted-walkaround = "vibecrafted_core.walkaround_runner:main"'
        in pyproject
    )
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    install_tools = makefile.split("install-tools-held:", 1)[1].split(
        "\n# install-all", 1
    )[0]
    assert (
        "for entrypoint in vibecrafted vc-workflow vc-guardian vc-server-supervisor "
        "verify-vibecrafted-walkaround"
    ) in install_tools
    assert 'if ! "$$resolved" --help' in install_tools


def test_installer_owns_public_vc_git_entrypoint() -> None:
    assert "vc-git" in installer.PYTHON_ENTRYPOINT_LAUNCHERS
    assert "vc-git" in installer._installer_managed_launcher_names()
    pyproject = (REPO_ROOT / "vibecrafted-core/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert 'vc-git = "vibecrafted_core.git:main"' in pyproject


def test_unified_product_contract_gate_executes_installed_runner() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    gate = makefile.split("unified-product-contract-gate:", 1)[1].split("\n\n", 1)[0]

    assert "verify-vibecrafted-product.sh --self-test" in gate
    assert "uv build --wheel --project vibecrafted-core" in gate
    assert 'runner="$$tmp/venv/bin/verify-vibecrafted-walkaround"' in gate
    assert '"$$runner" --help' in gate
    assert '"$$runner" trust-probe' in gate
    assert '"$$runner" verify-release --release-output' in gate
    assert '"$$runner" walkaround --release-output' in gate
    assert "PYTHONNOUSERSITE=1" in gate
    for required_test in (
        "tests/tui/test_install_bootstrap.py",
        "tests/tui/test_installer_doctor.py",
        "tests/tui/test_installer_uninstall.py",
        "tests/tui/test_staged_tools_sync.py",
        "tests/tui/test_uv_bootstrap.py",
        "vibecrafted-core/tests/test_runtime_receipt.py",
    ):
        assert required_test in gate


def test_release_workflow_is_read_only_and_validates_the_exact_tag_source() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "run: make unified-product-contract-gate" in workflow
    assert "run: make test-core" in workflow
    assert "run: make semgrep" in workflow
    assert "runs-on: macos-15" in workflow
    assert "run: brew install shellcheck" in workflow
    assert "ubuntu-latest" not in workflow
    assert "apt-get" not in workflow
    assert 'test "$GITHUB_REF_TYPE" = "tag"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in workflow
    assert (
        'release_tag_ref="refs/vibecrafted-release-tags/$GITHUB_REF_NAME"' in workflow
    )
    assert '"refs/tags/$GITHUB_REF_NAME:$release_tag_ref"' in workflow
    assert 'test "$(git cat-file -t "$release_tag_ref")" = "tag"' in workflow
    assert 'test "$(git rev-list -n 1 "$release_tag_ref")" = "$GITHUB_SHA"' in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "contents: write" not in workflow
    assert "gh release create" not in workflow
    assert "gh release upload" not in workflow
    assert "gh release edit" not in workflow


def test_core_gate_isolated_from_the_previously_installed_runtime_stamp() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    gate = makefile.split("\ntest-core:", 1)[1].split("\ndispatch-test:", 1)[0]

    assert "mktemp -d" in gate
    assert 'VIBECRAFTED_TOOLS_HOME="$$test_tools_home"' in gate
    assert "vibecrafted-test-core-tools.XXXXXX" in gate


def test_bootstrap_help_requires_canonical_provenance_archives() -> None:
    installer = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    usage = installer.split("usage() {", 1)[1].split("EOF_USAGE", 2)[1]

    assert "source-provenance.json" in usage
    assert "scripts/distribution_manifest.py archive" in usage


def test_makefile_keeps_install_as_terminal_first_front_door() -> None:
    """Contract: `make install` is the terminal-native human front door — a
    compact, log-quiet step runner that greets, walks the canonical install
    steps through $(INSTALL_STEP), and ends by pointing at `vc-start`.
    `make install-auto` is the unattended automation path that reuses the same
    front door, and `make setup-dev` opens the uv meta-installer in advanced
    mode.

    Every recipe that bootstraps uv (setup-dev, install-all, tui-installer)
    must keep the uv bootstrap and the `uv run` invocation inside one shell
    stanza, otherwise the `export PATH=...` from the bootstrap leg dies before
    `uv run` sees it (each `@`-prefixed recipe line spawns a fresh shell).
    See P1-01.
    """
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    # CLI_PRODUCT_SPEC §6.5: `make help` is the six-target deck; everything
    # else lives in `make help-dev`.
    assert "make install      \\033[2mGuided install" in text
    assert "make doctor       \\033[2mHealth check" in text
    assert "dev targets: make help-dev" in text
    assert "help-dev:" in text
    assert "make skills" not in text.split("help:", 1)[1].split("\nvibecrafted:", 1)[0]
    assert "vibecrafted: install" in text

    # The front door is the compact step runner: it greets, walks the canonical
    # install steps through $(INSTALL_STEP), and ends by pointing at vc-start.
    install_block = text.split("\ninstall:\n", 1)[1].split(
        "\ninstall-python-tools:", 1
    )[0]
    assert 'printf "Installing Vibecrafted\\n"' in install_block
    for label in (
        "foundations",
        "skills and launchers",
        "runtime tools",
        "app binaries",
    ):
        assert f'$(INSTALL_STEP) "{label}"' in install_block, (
            f"front door must walk the `{label}` install step via $(INSTALL_STEP)"
        )
    assert "vc-start" in install_block

    # install-auto is the unattended path: it reuses the same front door rather
    # than forking a second installer recipe.
    assert "install-auto: install" in text

    # setup-dev opens the uv meta-installer in advanced mode. Advanced is an
    # interactive surface, so it never carries the auto-approve `--yes`.
    setup_dev_block = text.split("setup-dev: init-hooks", 1)[1].split("\ndry-run:", 1)[
        0
    ]
    assert "vetcoders-installer $(MANIFEST)" in setup_dev_block
    assert "--advanced --quiet" in setup_dev_block
    assert "--yes" not in setup_dev_block

    # install-all is the auto-approved meta-installer: same runner, but --yes.
    install_all_block = text.split("install-all: init-hooks", 1)[1].split(
        "\n# Output discipline", 1
    )[0]
    assert (
        "uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST) --yes --quiet"
        in install_all_block
    )

    # P1-01: every uv-bootstrapping recipe exports PATH first and chains the
    # bootstrap `fi` into the same shell as `uv run` via `fi; \`, so the
    # freshly-installed uv is visible to `uv run`.
    tui_installer_block = text.split("tui-installer: init-hooks", 1)[1].split(
        "\n# BUNDLE_DIR", 1
    )[0]
    for name, block in (
        ("setup-dev", setup_dev_block),
        ("install-all", install_all_block),
        ("tui-installer", tui_installer_block),
    ):
        assert 'export PATH="$$HOME/.local/bin:$$PATH"' in block, (
            f"{name} must export PATH before `uv run`"
        )
        assert "fi; \\" in block, (
            f"{name} must chain the uv bootstrap `fi` into the same shell as "
            "`uv run` via `fi; \\`"
        )
        assert (
            "uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST)"
            in block
        ), f"{name} must invoke the uv meta-installer"
        assert 'UV_PROJECT_ENVIRONMENT="$(UV_PROJECT_ENVIRONMENT)"' in block, (
            f"{name} must isolate uv from a foreign checkout .venv"
        )

    assert (
        "UV_PROJECT_ENVIRONMENT ?= "
        "$(INSTALLER_CACHE_HOME)/vibecrafted/venvs/installer-$(INSTALLER_HOST_TAG)"
        in text
    )
    assert "INSTALLER_HOST_TAG := $(shell uname -s" in text


def test_installer_environment_is_host_scoped_outside_checkout(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    cache = tmp_path / "xdg-cache"
    probe = tmp_path / "probe.mk"
    probe.write_text(
        f"include {REPO_ROOT / 'Makefile'}\n"
        "print-installer-env:\n"
        "\t@printf '%s\\n' '$(UV_PROJECT_ENVIRONMENT)'\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-s",
            "-f",
            str(probe),
            f"HOME={home}",
            f"XDG_CACHE_HOME={cache}",
            "print-installer-env",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    environment = Path(result.stdout.strip())
    assert environment.parent == cache / "vibecrafted" / "venvs"
    assert environment.name.startswith("installer-")
    assert REPO_ROOT not in environment.parents


def test_bundle_check_uses_portable_mktemp_template() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert 'mktemp "$$tmp_root/vibecrafted-bundle.XXXXXX"' in text
    assert 'mktemp "$$tmp_root/vibecrafted-bundle.XXXXXX.plugin"' not in text


def test_bundle_targets_use_distribution_manifest_for_runtime_archive() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    bundle_block = text.split("\nbundle:\n", 1)[1].split("\nbundle-check:\n", 1)[0]
    check_block = text.split("\nbundle-check:\n", 1)[1].split(
        "\nversion version-show:", 1
    )[0]

    for block in (bundle_block, check_block):
        assert "scripts/distribution_manifest.py archive" in block
        assert "scripts/distribution_manifest.py check" in check_block
    assert (
        "BUNDLE_ARCHIVE ?= $(SOURCE)/dist/vibecrafted-$(BUNDLE_VERSION).tar.gz" in text
    )
    assert '--root-name "vibecrafted-$(BUNDLE_VERSION)"' in bundle_block
    assert 'source_root="$$(cd "$(SOURCE)" && pwd -P)"' in bundle_block
    assert 'source_parent="$$(dirname "$$source_root")"' in bundle_block
    assert 'mktemp "$$source_parent/.vibecrafted-bundle-archive.XXXXXX"' in bundle_block
    assert '--output "$$tmp_archive"' in bundle_block
    assert '--publish-output "$(BUNDLE_ARCHIVE)"' in bundle_block
    assert '--source "$$source_root"' in bundle_block
    assert "os.replace(sys.argv[1], sys.argv[2])" not in bundle_block
    assert "mv -f" not in bundle_block
    assert '--output "$(BUNDLE_ARCHIVE)"' not in bundle_block
    assert "build_marketplace_bundle.py" in bundle_block
    assert (
        'build_marketplace_bundle.py --output "$(SOURCE)/dist/'
        'vibecrafted-framework.plugin"'
    ) in bundle_block
    assert '--output "$(SOURCE)/vibecrafted-framework.plugin"' not in bundle_block
    assert "build_marketplace_bundle.py" in check_block
    assert 'tar -xzpf "$$tmp_archive"' in check_block
    assert "cmp -s" not in check_block
    assert 'test -s "$$tmp_bundle"' in check_block
    assert check_block.lstrip().startswith("@set -e;")


def test_bundle_target_cannot_self_poison_a_clean_git_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    archive = source / "dist" / "vibecrafted-9.8.7.tar.gz"
    plugin = source / "dist" / "vibecrafted-framework.plugin"
    python_dispatch = tmp_path / "python-dispatch.py"
    _minimal_distribution_source(source)
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    for key, value in (
        ("user.name", "Bundle Contract Test"),
        ("user.email", "bundle-contract@example.invalid"),
    ):
        subprocess.run(
            ["git", "-C", str(source), "config", key, value],
            check=True,
        )
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "remote",
            "add",
            "origin",
            "https://github.com/vetcoders/vibecrafted.git",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "--quiet", "-m", "fixture"],
        check=True,
    )
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    stale_root_plugin = source / "vibecrafted-framework.plugin"
    stale_root_plugin.write_bytes(b"stale ignored marketplace plugin\n")
    python_dispatch.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import sys\n"
        "script = Path(sys.argv[1]).name\n"
        "if script == 'build_marketplace_bundle.py':\n"
        "    output = Path(sys.argv[sys.argv.index('--output') + 1])\n"
        "    output.parent.mkdir(parents=True, exist_ok=True)\n"
        "    output.write_bytes(b'isolated marketplace plugin\\n')\n"
        "    raise SystemExit(0)\n"
        "os.execv(sys.executable, [sys.executable, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.pop("VIBECRAFTED_SOURCE_OWNER_REPO", None)
    environment.pop("VIBECRAFTED_SOURCE_REVISION", None)

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "bundle",
            f"SOURCE={source}",
            "BUNDLE_VERSION=9.8.7",
            f"BUNDLE_ARCHIVE={archive}",
            f"PYTHON={sys.executable} {python_dispatch}",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert plugin.read_bytes() == b"isolated marketplace plugin\n"
    assert stale_root_plugin.read_bytes() == b"stale ignored marketplace plugin\n"
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as bundle:
        names = bundle.getnames()
        assert not any(name.endswith("vibecrafted-framework.plugin") for name in names)
        carrier = bundle.extractfile(
            f"vibecrafted-9.8.7/{distribution.SOURCE_PROVENANCE_FILE}"
        )
        assert carrier is not None
        assert revision.encode("ascii") in carrier.read()

    tracked_readme = source / "README.md"
    tracked_readme_before = tracked_readme.read_bytes()
    rejected = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "bundle",
            f"SOURCE={source}",
            "BUNDLE_VERSION=9.8.7",
            f"BUNDLE_ARCHIVE={tracked_readme}",
            f"PYTHON={sys.executable} {python_dispatch}",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "inside source must be below its physical dist directory" in rejected.stderr
    assert tracked_readme.read_bytes() == tracked_readme_before
    assert (
        subprocess.run(
            ["git", "-C", str(source), "diff", "--quiet"], check=False
        ).returncode
        == 0
    )


def test_control_plane_staging_delegates_to_distribution_manifest(
    monkeypatch, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    seen: dict[str, object] = {}

    source_provenance = {
        "schema": "vibecrafted.source-provenance.v2",
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": "1" * 40,
        "payload": {
            "schema": "vibecrafted.distribution-tree.v1",
            "algorithm": "sha256",
            "tree_sha256": "2" * 64,
            "entry_count": 1,
        },
    }

    def fake_stage(
        src: Path,
        dst: Path,
        *,
        mirror: bool,
        require_source_provenance: bool,
    ) -> dict[str, object]:
        seen.update(
            source=src,
            destination=dst,
            mirror=mirror,
            require_source_provenance=require_source_provenance,
        )
        dst.mkdir(parents=True)
        (dst / "payload.txt").write_text("validated\n", encoding="utf-8")
        return source_provenance

    monkeypatch.setattr(installer, "stage_distribution_payload", fake_stage)
    monkeypatch.setattr(
        installer,
        "_materialize_vc_frame_generation",
        lambda runtime_root: seen.update(materialized=runtime_root),
    )
    monkeypatch.setattr(
        installer,
        "_write_runtime_generation_manifest",
        lambda runtime_root, **kwargs: seen.update(
            manifested=runtime_root,
            manifest_source_provenance=kwargs["source_provenance"],
        ),
    )
    monkeypatch.setattr(
        installer,
        "_runtime_generation_payload_errors",
        lambda runtime_root: seen.update(validated=runtime_root) or [],
    )

    installer.sync_control_plane_tree(source, destination, mirror=True)

    assert seen["source"] == source
    assert seen["destination"] != destination
    assert Path(seen["destination"]).parent == destination.parent
    assert seen["mirror"] is True
    assert seen["require_source_provenance"] is True
    assert seen["manifest_source_provenance"] == source_provenance
    assert seen["materialized"] == seen["destination"]
    assert seen["manifested"] == seen["destination"]
    assert seen["validated"] == seen["destination"]
    assert (destination / "payload.txt").read_text(encoding="utf-8") == "validated\n"
    source_text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )
    assert "_CONTROL_PLANE_EXCLUDES" not in source_text


def test_install_manifest_post_install_uses_mirror_sync() -> None:
    text = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "make --no-print-directory install-bundle-tools" in text
    assert (
        '$(PYTHON) $(INSTALLER) install --source "$(SOURCE)" '
        "--compact --non-interactive --mirror"
    ) in makefile
    assert "make --no-print-directory install-python-tools" not in text
    assert "make --no-print-directory install-server\n" not in text
    assert "$(MAKE) --no-print-directory build-server-release" in makefile
    assert "$(MAKE) --no-print-directory install-server-payload" in makefile
    assert "make --no-print-directory install-server-service" not in text
    assert (
        'bash "$stable_root/vibecrafted-core/vibecrafted_core/runtime/scripts/'
        'install-frontier-config.sh" --source "$stable_root"'
    ) in text
    assert text.index("make --no-print-directory install-bundle-tools") < text.index(
        'install-frontier-config.sh" --source "$stable_root"'
    )
    assert "bash runtime/scripts/install-frontier-config.sh" not in text


def test_make_install_executes_the_shell_owned_stable_root_contract(
    tmp_path: Path,
) -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    install_block = makefile.split("\ninstall:\n", 1)[1].split("\n# `make install`", 1)[
        0
    ]
    install_tools_block = makefile.split("\ninstall-tools-held:\n", 1)[1].split(
        "\n# install-all owns", 1
    )[0]

    assert 'PYTHONPATH="$$stable_root/vibecrafted-core"' in install_block
    assert '"$$tool_python" -c' in install_block
    assert install_block.index('export PATH="$$HOME/.local/bin:$$PATH"') < (
        install_block.index("uv tool dir")
    )
    assert 'PYTHONPATH="$(SOURCE)/vibecrafted-core"' not in install_block
    assert install_block.index("install-bundle-tools") < install_block.index(
        'install-frontier-config.sh" --source "$$stable_root"'
    )
    assert (
        "from vibecrafted_core.vc_frame_delivery import wire_vc_frame_config"
    ) in install_block
    assert "wire_vc_frame_config(force_frontier=True)" in install_block
    assert "ensure_zshrc" not in install_block
    assert "stage_vc_frame_config" not in install_block
    assert "vc-frame config delivery skipped" not in install_block
    assert install_block.index("skills and launchers") < install_block.index(
        "vc-frame config"
    )

    # Execute the exact shared Make expressions in both production shell
    # contexts. PYTHON is a guard: a regression to the deleted shim or any
    # sys.path bootstrap trips it before the path can be accepted.
    probe = tmp_path / "stable-root-probe.mk"
    probe.write_text(
        f"include {REPO_ROOT / 'Makefile'}\n"
        "probe-install:\n"
        "\t@bash -e -c '$(RESOLVE_STABLE_RUNTIME_ROOT); "
        '$(REQUIRE_STAGED_RUNTIME_ROOT); printf "%s\\n" "$$stable_root"\'\n'
        "probe-install-tools-held:\n"
        "\t@set -eu; $(RESOLVE_STABLE_RUNTIME_ROOT); "
        '$(REQUIRE_STAGED_RUNTIME_ROOT); printf "%s\\n" "$$stable_root"\n',
        encoding="utf-8",
    )
    guard_marker = tmp_path / "python-was-called"
    python_guard = tmp_path / "python-guard"
    python_guard.write_text(
        f"#!/bin/sh\nprintf called > {guard_marker}\nexit 91\n",
        encoding="utf-8",
    )
    python_guard.chmod(0o755)

    home = tmp_path / "home"
    stable_root = home / ".local/share/vibecrafted/tools/vibecrafted-current"
    (stable_root / "vibecrafted-core").mkdir(parents=True)
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "VIBECRAFTED_INSTALL_NONINTERACTIVE": "1",
    }
    for name in (
        "VIBECRAFTED_HOME",
        "VIBECRAFTED_RUNTIME_HOME",
        "VIBECRAFTED_TOOLS_HOME",
        "XDG_DATA_HOME",
    ):
        environment.pop(name, None)

    for target in ("probe-install", "probe-install-tools-held"):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-f",
                str(probe),
                target,
                f"PYTHON={python_guard}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(stable_root)
    assert not guard_marker.exists(), "stable-root resolution must not invoke Python"

    shutil.rmtree(stable_root)
    for target in ("probe-install", "probe-install-tools-held"):
        result = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-f",
                str(probe),
                target,
                f"PYTHON={python_guard}",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        assert result.returncode != 0
        assert "✗ current tools root drift: staged runtime missing at" in result.stderr
        assert "→ fix: vibecrafted doctor --fix-legacy-bootstrap --fix-launchers" in (
            result.stderr
        )

    for block in (install_block, install_tools_block):
        assert "$(RESOLVE_STABLE_RUNTIME_ROOT)" in block
        assert "from runtime_paths import vibecrafted_tools_home" not in block


def test_installer_copies_skill_rules_to_fresh_skills_root(tmp_path: Path) -> None:
    source_skills = tmp_path / "source" / "skills"
    install_skills = tmp_path / "install" / "skills"
    runtime_skills = tmp_path / "home" / ".claude" / "skills"
    installed_skill = install_skills / "vc-implement"
    runtime_skill = runtime_skills / "vc-implement"

    (source_skills / "vc-implement").mkdir(parents=True)
    (source_skills / "vc-implement" / "SKILL.md").write_text(
        "See [Verification Rule](../VERIFICATION_RULE.md).\n"
        "See [Living Tree Rule](../LIVING_TREE_RULE.md).\n",
        encoding="utf-8",
    )
    (source_skills / "VERIFICATION_RULE.md").write_text(
        "# Verification\n", encoding="utf-8"
    )
    (source_skills / "LIVING_TREE_RULE.md").write_text(
        "# Living Tree\n", encoding="utf-8"
    )
    (source_skills / "pl").mkdir()
    (source_skills / "pl" / "LIVING_TREE_RULE.md").write_text(
        "# Zywe Drzewo\n", encoding="utf-8"
    )
    installed_skill.mkdir(parents=True)
    runtime_skill.mkdir(parents=True)

    copied = installer.sync_skill_root_rules(source_skills, install_skills)
    copied_again = installer.sync_skill_root_rules(source_skills, install_skills)
    runtime_copied = installer.sync_skill_root_rules(source_skills, runtime_skills)

    assert copied == copied_again
    assert runtime_copied == copied
    assert Path("VERIFICATION_RULE.md") in copied
    assert Path("LIVING_TREE_RULE.md") in copied
    assert Path("pl/LIVING_TREE_RULE.md") in copied
    assert (installed_skill / ".." / "VERIFICATION_RULE.md").is_file()
    assert (installed_skill / ".." / "LIVING_TREE_RULE.md").is_file()
    assert (runtime_skills / "VERIFICATION_RULE.md").is_file()
    assert (runtime_skills / "LIVING_TREE_RULE.md").is_file()
    assert (runtime_skill / ".." / "VERIFICATION_RULE.md").is_file()
    assert (install_skills / "pl" / "LIVING_TREE_RULE.md").is_file()
    assert (runtime_skills / "pl" / "LIVING_TREE_RULE.md").is_file()
    assert not (install_skills / "pl" / "VERIFICATION_RULE.md").exists()


def test_install_all_paths_do_not_install_shell_helpers_by_default() -> None:
    """New runtime contract: install-all installs tools and views, but does not
    wire legacy shell helpers or mutate shell rc files by default."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")

    install_all_block = makefile.split("install-all:", 1)[1].split("\nskills:", 1)[0]
    assert "--with-shell" not in install_all_block
    assert "--write-shell-rc" not in install_all_block

    installation_phase = manifest.split('key = "installation"', 1)[1].split(
        "\n\n[[phase]]", 1
    )[0]
    assert "--with-shell" not in installation_phase
    assert "--write-shell-rc" not in installation_phase


def test_install_all_default_output_is_quiet_and_points_to_vc_start() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    step_runner = (REPO_ROOT / "scripts" / "install-step.sh").read_text(
        encoding="utf-8"
    )

    install_all_block = makefile.split("install-all:", 1)[1].split("\nskills:", 1)[0]
    receipt_start = install_all_block.index("Vibecrafted is ready.")
    vc_start_index = install_all_block.index("vc-start")
    doctor_index = install_all_block.index("vibecrafted doctor")
    log_index = install_all_block.index("~/.vibecrafted/install.log")

    assert 'printf "Installing Vibecrafted\\n"' in install_all_block
    for label in (
        "foundations",
        "frontier config",
        "skills and launchers",
        "runtime tools",
        "app binaries",
    ):
        assert f'$(INSTALL_STEP) "{label}" --' in install_all_block

    assert receipt_start < vc_start_index < doctor_index < log_index
    assert "VIBECRAFTED_INSTALL_LOG" in install_all_block
    assert ': > "$(INSTALL_LOG)"' in install_all_block
    assert "VERBOSE ?= 0" in makefile
    assert 'tee -a "$log_path"' in step_runner
    assert "Install failed during: %s" in step_runner
    assert "vibecrafted doctor" in step_runner


def test_install_step_preserves_tty_failure_status(tmp_path: Path) -> None:
    """The spinner must return the child status, not boolean-negation success."""
    pty = pytest.importorskip("pty")
    master_fd, slave_fd = pty.openpty()
    log_path = tmp_path / "install.log"
    environment = os.environ.copy()
    environment["VIBECRAFTED_INSTALL_LOG"] = str(log_path)
    environment["VERBOSE"] = "0"
    try:
        completed = subprocess.run(
            [
                "bash",
                str(REPO_ROOT / "scripts" / "install-step.sh"),
                "forced failure",
                "--",
                "bash",
                "-c",
                "exit 37",
            ],
            stdout=slave_fd,
            stderr=slave_fd,
            env=environment,
            check=False,
        )
    finally:
        os.close(slave_fd)
        os.close(master_fd)

    assert completed.returncode == 37


def test_install_all_user_facing_output_has_no_ghost_anxiety_copy() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    output_lines = [
        line for line in makefile.splitlines() if "echo " in line or "printf " in line
    ]
    output_text = "\n".join(output_lines).lower()

    for forbidden in ("cargo " + "ghosts", "ghost " + "symlink", "cargo-" + "ghost"):
        assert forbidden not in output_text


def test_install_all_installs_python_tools_with_uv_tool_install() -> None:
    """install-all owns Python console scripts through uv tool install.

    De-fragile contract: the uv-tool editable source is the STABLE runtime home
    (resolved via the shell root throne -> vibecrafted-current), NEVER the dev-workspace
    checkout ($(SOURCE)). An editable install pointed at the checkout breaks the
    `vibecrafted` CLI the moment the dev tree switches to a branch without
    vibecrafted_core/cli.py. The MCP server is installed as its own tool, with
    the stable-home vibecrafted-core injected as the dependency source.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")

    assert "install-python-tools:" in makefile

    install_all_block = makefile.split("install-all:", 1)[1].split("\nskills:", 1)[0]
    python_tools_block = makefile.split("install-python-tools:", 1)[1].split(
        "\n# install-all owns", 1
    )[0]

    assert "install-bundle-tools" in install_all_block
    # The uv-tool editable installs must source from the resolved stable home
    # ($$stable_root -> vibecrafted-current), not the dev checkout.
    assert (
        'uv tool install --force --reinstall --editable "$$stable_root/vibecrafted-core"'
    ) in python_tools_block
    assert (
        "uv tool install --force --reinstall --editable "
        '"$$stable_root/vibecrafted-mcp" --with-editable '
        '"$$stable_root/vibecrafted-core"'
    ) in python_tools_block
    # Non-fakeable: no `uv tool install` may take its editable source from the
    # dev-workspace checkout ($(SOURCE)). That is the whole point of de-fragiling.
    assert (
        'uv tool install --force --reinstall --editable "$(SOURCE)'
        not in python_tools_block
    )
    assert "vibecrafted-current" in python_tools_block
    assert (
        "v._install_launcher(Path(sys.argv[1]), dry_run=False, update_rc=False)"
        in python_tools_block
    )
    assert (
        "$$stable_root/vibecrafted-core/vibecrafted_core/deck/vibecrafted"
        in python_tools_block
    )
    assert 'if [ "$$entrypoint" = "vibecrafted" ]' in python_tools_block
    assert "vibecrafted-mcp" in (
        REPO_ROOT / "vibecrafted-mcp" / "pyproject.toml"
    ).read_text(encoding="utf-8")

    assert "make --no-print-directory install-bundle-tools" in manifest
    assert "make --no-print-directory install-python-tools" not in manifest


def test_install_all_covers_app_binaries_as_real_files() -> None:
    """install-all must own the shipped Rust binaries (voc, vc-admin, and
    vc-server): built from source in release and copied into
    ~/.local/bin as REAL files. `cargo install` is forbidden in that path because
    it can create ~/.local/bin -> ~/.cargo/bin symlink drift. The install.toml
    installation phase mirrors install-all line-by-line, so it must carry the
    same steps."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")

    install_all_block = makefile.split("install-all:", 1)[1].split("\nskills:", 1)[0]
    assert "install-vendored-binaries" in install_all_block
    assert "install-app-binaries" in install_all_block
    assert "install-bundle-tools" in install_all_block

    assert (
        "VENDORED_FOUNDATION_BINARIES := vc-frame loctree-mcp loct aicx aicx-mcp"
        in makefile
    )
    assert "bin/vendor/$(HOST_VENDOR_PLATFORM)" in makefile
    assert "APP_BINARIES := voc vc-admin" in makefile
    assert "SERVER_PACKAGE := vibecrafted-server-web" in makefile
    assert "SERVER_BIN  := vc-server" in makefile
    assert "SERVER_COMPAT_BIN := vibecrafted-server-web" in makefile
    assert "BIN_DIR := $(HOME)/.local/bin" in makefile

    app_block = makefile.split("\ninstall-app-binaries:", 1)[1].split("\nskills:", 1)[0]
    assert "cargo build --release --locked -p voc" in app_block
    assert "cargo install" not in app_block
    assert (
        "CARGO_BUILD_ROOT ?= $(INSTALLER_CACHE_HOME)/vibecrafted/build/$(INSTALLER_HOST_TAG)"
        in makefile
    )
    assert "APP_BUILD_TARGET := $(CARGO_BUILD_ROOT)/vibecrafted-app" in makefile
    assert 'CARGO_TARGET_DIR="$(APP_BUILD_TARGET)" cargo build' in app_block
    assert (
        'install -m 0755 "$(APP_BUILD_TARGET)/release/$$bin" "$(BIN_DIR)/$$bin"'
        in app_block
    )
    assert "$(APP_DIR)/target" not in app_block

    assert "make --no-print-directory install-vendored-binaries" in manifest
    assert "make --no-print-directory install-app-binaries" in manifest
    assert "make --no-print-directory install-server" not in manifest
    assert "make --no-print-directory install-bundle-tools" in manifest
    assert "build-server-release" in makefile
    server_build_block = makefile.split("\nbuild-server-release:", 1)[1].split(
        "\ninstall-server-payload:", 1
    )[0]
    assert "cargo leptos build --release" in server_build_block
    assert "SERVER_BUILD_TARGET := $(CARGO_BUILD_ROOT)/vibecrafted-server" in makefile
    assert "SERVER_BUILD_SITE_ROOT := $(SERVER_BUILD_TARGET)/site" in makefile
    assert 'CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)"' in server_build_block
    assert 'LEPTOS_SITE_ROOT="$(SERVER_BUILD_SITE_ROOT)"' in server_build_block
    assert '--bin-cargo-args="--locked"' in server_build_block
    assert '--lib-cargo-args="--locked"' in server_build_block
    assert "cargo tree --locked -p wasm-bindgen --depth 0 --prefix none" in (
        server_build_block
    )
    assert "tomllib" not in server_build_block
    assert "$(PYTHON)" not in server_build_block
    assert "could not resolve wasm-bindgen version from Cargo.lock" in (
        server_build_block
    )
    assert "wasm-bindgen CLI $$cli_version does not match Cargo.lock" in (
        server_build_block
    )
    assert (
        "cargo install --force wasm-bindgen-cli --version $$lock_version --locked"
        in (server_build_block)
    )
    assert "hydration wasm is missing" in server_build_block
    assert "$(SERVER_DIR)/target" not in server_build_block
    server_payload_block = makefile.split("\ninstall-server-payload:", 1)[1].split(
        "\nifneq", 1
    )[0]
    assert '"$(SERVER_BUILD_TARGET)/release/$(SERVER_PACKAGE)"' in (
        server_payload_block
    )
    assert "$(SERVER_DIR)/target" not in server_payload_block
    assert "install-server-payload" in makefile


def test_bin_dir_owned_entries_are_never_cargo_owned_symlink_drift() -> None:
    """Runtime contract: BIN (~/.local/bin) holds real files or symlinks into
    the vibecrafted runtime — never a symlink resolving into ~/.cargo/bin.
    A cargo-owned symlink is a side-installed binary the canonical installer
    does not own; the machine then drifts the moment `cargo install` re-runs."""
    bin_dir = Path.home() / ".local" / "bin"
    if not bin_dir.is_dir():
        pytest.skip("no ~/.local/bin on this machine")

    cargo_bin = Path.home() / ".cargo" / "bin"
    owned_names = {"voc", "vc-admin", "vibecraft", "vibecrafted", "telemetry"}

    offenders = []
    for entry in bin_dir.iterdir():
        owned = entry.name in owned_names or (
            entry.name.startswith("vc-") and entry.name != "vc-frame"
        )
        if not owned or not entry.is_symlink():
            continue
        resolved = Path(os.path.realpath(entry))
        if resolved.is_relative_to(cargo_bin):
            offenders.append(f"{entry.name} -> {resolved}")

    assert offenders == [], (
        "unexpected cargo-owned symlinks in ~/.local/bin (run `make install-all` to "
        f"install real files): {offenders}"
    )


def test_install_manifest_uses_four_human_checkpoints_with_artifact_reason() -> None:
    text = (REPO_ROOT / "install.toml").read_text(encoding="utf-8")

    phase_text = text.split("[branding]", 1)[0]
    labels = [
        line.split("=", 1)[1].strip().strip('"')
        for line in phase_text.splitlines()
        if line.startswith("label = ")
    ]
    assert labels == [
        "Introduction",
        "Diagnostics and plan",
        "Installation",
        "Onboarding",
    ]
    assert "Set your artifacts storage location." in text
    assert "keeps the persistent artifacts on developer's hard disks" in text
    assert 'installer_cmd = "make install"' in text


def test_update_target_never_rewrites_the_tree_from_another_branch() -> None:
    """`make update` must not plaster $(BRANCH)'s tree over the current
    branch. 2026-08-12: `git checkout "$(BRANCH)" -- .` silently reverted
    174 files of a release branch to a stale Aug-8 main (index + worktree,
    HEAD untouched) and then reinstalled from the poisoned source. The
    contract: fast-forward only when already on $(BRANCH); no recipe line
    may run `git checkout` at all."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    recipe_lines = [
        line
        for line in makefile.splitlines()
        if line.startswith("\t") and not line.lstrip("\t ").startswith("#")
    ]
    offenders = [line for line in recipe_lines if "git checkout" in line]
    assert offenders == []

    assert '[ "$$current" = "$(BRANCH)" ]' in makefile
    assert 'git merge --ff-only "origin/$(BRANCH)"' in makefile


def test_makefile_exposes_version_bump_contract() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "version-show:" in text
    assert "version-bump:" in text
    assert (
        "VERSION is required. Usage: make version-bump VERSION={patch|minor|major|x.y.z}"
        in text
    )
    assert "scripts/version_bump.py" in text


def test_make_version_bump_updates_configured_version_file(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.4.1\n", encoding="utf-8")

    result = subprocess.run(
        [
            "make",
            "-f",
            str(REPO_ROOT / "Makefile"),
            "version-bump",
            "VERSION=minor",
            f"VERSION_FILE={version_file}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Bumped: v1.4.1 -> v1.5.0" in result.stdout
    assert version_file.read_text(encoding="utf-8") == "1.5.0\n"


def test_foundations_product_binaries_are_validation_only() -> None:
    text = (REPO_ROOT / "scripts" / "install-foundations.sh").read_text(
        encoding="utf-8"
    )

    loctree_block = text.split("install_loctree() {", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# Generic cargo installer",
        1,
    )[0]
    aicx_block = text.split("install_aicx() {", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# vc-frame — product frame binary",
        1,
    )[0]

    forbidden = (
        "VIBECRAFTED_OWN_PRODUCT_BINARIES",
        "OWN_PRODUCT_BINARIES",
        "LOCTREE_VERSION",
        "AICX_VERSION",
        "install_from_bundled",
        "install_from_cargo",
        "install_from_npm",
        "github_release_asset_url",
        "cargo install",
        "LOCTREE_SOURCE",
        "AICX_SOURCE",
        "../loctree-suite",
        "../aicx",
    )
    for needle in forbidden:
        assert needle not in loctree_block
        assert needle not in aicx_block

    assert "curl -fsSL $LOCTREE_INSTALL_URL | sh" in loctree_block
    assert "curl -fsSL $LOCTREE_INSTALL_URL | sh" in aicx_block
    assert (
        "will not guess crates, npm packages, or local checkout paths" in loctree_block
    )
    assert "will not guess crates, npm packages, or local checkout paths" in aicx_block


def test_foundations_builds_vc_frame_outside_the_living_tree() -> None:
    text = (REPO_ROOT / "scripts" / "install-foundations.sh").read_text(
        encoding="utf-8"
    )
    block = text.split("install_vcframe() {", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# Claude Code",
        1,
    )[0]

    assert "${XDG_CACHE_HOME:-$HOME/.cache}/vibecrafted/build/vc-frame" in block
    assert 'CARGO_TARGET_DIR="$vcframe_target_root"' in block
    assert 'make -C "$sibling" --no-print-directory release' in block
    assert 'install -m 0755 "$donor_binary" "$LAUNCHER_PREFIX/vc-frame"' in block
    assert "tools/install.sh" not in block
    assert "VCFRAME_INSTALL_URL" not in block
    assert "releases/latest/download/install.sh" not in block


def test_foundations_never_overwrite_uv_owned_python_entrypoints() -> None:
    text = (REPO_ROOT / "scripts" / "install-foundations.sh").read_text(
        encoding="utf-8"
    )

    assert "install_vc_wrappers" not in text
    assert 'for src in "$source_bin"/vc-*' not in text


def test_setup_installer_uses_canonical_foundation_action_only() -> None:
    installer = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )
    skills_sync = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "skills_sync.sh"
    ).read_text(encoding="utf-8")

    for name in ("aicx-mcp", "loct", "loctree", "loctree-mcp"):
        block = installer.split(f'name="{name}"', 1)[1].split("verify_cmd=", 1)[0]
        assert 'channels=["canonical"]' in block
        assert "curl -fsSL https://loct.io/install.sh | sh" in block
        assert '"crates"' not in block
        assert '"npm"' not in block
        assert '"github"' not in block
        assert "LOCTREE_SOURCE" not in block
        assert "AICX_SOURCE" not in block
        assert "../loctree-suite" not in block
        assert "../aicx" not in block

    forbidden = (
        "install_foundation_cargo",
        "Install {f.name} with cargo?",
        "has_cargo = detect_cargo()",
        "cargo not found — cannot auto-install foundations",
        "fallback cargo install loctree-mcp",
        "fallback cargo install ai-contexters",
    )
    for needle in forbidden:
        assert needle not in installer
        assert needle not in skills_sync


def test_installer_publishes_async_dispatch_wrapper() -> None:
    installer = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )
    launcher_block = installer.split("LAUNCHER_WRAPPERS = [", 1)[1].split("\n]", 1)[0]

    assert '"vc-loop"' in launcher_block
    assert '"vc-ship"' in launcher_block
    assert '"vc-cron"' in launcher_block
    assert '"vc-dispatch"' in launcher_block
    assert '"vc-dashboard"' in launcher_block
    entrypoint_block = installer.split("PYTHON_ENTRYPOINT_LAUNCHERS = [", 1)[1].split(
        "\n]", 1
    )[0]
    for name in ("vc-audit", "vc-dou", "vc-hydrate", "vc-polarize", "vc-marbles"):
        assert f'"{name}"' in entrypoint_block


def test_installer_paths_do_not_write_shell_rc_without_consent_flag() -> None:
    install_shell = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "install-shell.sh"
    ).read_text(encoding="utf-8")
    installer = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )

    assert "write_rc=0" in install_shell
    assert "if (( write_rc && update_zshrc )); then" in install_shell
    assert "if (( write_rc && update_bashrc )); then" in install_shell
    assert '"--write-shell-rc"' in installer
    assert "update_rc=write_shell_rc" in installer
    assert "if write_shell_rc:\n        for rcname in" in installer

    # The host shell is PATH-only. Even explicitly consented rc mutation removes
    # legacy helper sourcing instead of re-wiring product functions globally.
    assert 'grep -Fq "vetcoders/vc-skills.sh"' not in install_shell
    assert "/vetcoders\\/vc-skills\\.sh/ { next }" in install_shell
    assert "path_line=" in install_shell


def test_product_mcp_paths_do_not_hardcode_cargo_bin() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    offenders = []
    product_markers = ("loctree-mcp", "aicx-mcp", "rust-memex")
    cargo_markers = ("~/.cargo/bin", "$HOME/.cargo/bin")
    for rel in tracked:
        path = REPO_ROOT / rel
        # `git ls-files` lists tracked symlinks-to-directories (e.g. `runtime`,
        # `skills`) and gitlinks as entries; those are not file content to
        # scan. is_file() follows symlinks, so file-symlinks are still read.
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            if any(product in line for product in product_markers) and any(
                cargo in line for cargo in cargo_markers
            ):
                offenders.append(f"{rel}:{line_no}: {line.strip()}")

    assert offenders == []


def test_install_server_is_in_install_all() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "install-server:" in text
    assert "install-server-payload:" in text

    install_all_block = text.split("install-all:", 1)[1].split("\nskills:", 1)[0]
    assert "install-bundle-tools" in install_all_block
    assert "make --no-print-directory install-server" not in install_all_block


def test_make_install_verifies_server_supervisor_entrypoint() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    installer_text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )
    cli_text = (
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "cli.py"
    ).read_text(encoding="utf-8")
    install_tools_block = text.split("install-tools:", 1)[1].split(
        "\n# install-all owns", 1
    )[0]
    handoff_block = installer_text.split("def run_with_tools_install_lease(", 1)[
        1
    ].split("\ndef _symlink_target(", 1)[0]

    assert "vc-server-supervisor" in install_tools_block
    assert "python_entrypoints=" in install_tools_block
    assert "PYTHON_ENTRYPOINT_LAUNCHERS" in install_tools_block
    assert "is not owned by the uv interpreter" in install_tools_block
    assert 'if [ "$$entrypoint" = "vibecrafted-mcp" ]' in install_tools_block
    assert "$${tool_root%/vibecrafted}/vibecrafted-mcp" in install_tools_block
    assert "reconnect the operator session after install" in install_tools_block
    assert "vibecrafted vc-workflow vc-guardian vc-server-supervisor" in (
        install_tools_block
    )
    assert "expected executable entrypoint" in install_tools_block
    assert '"$$resolved" --help' in install_tools_block
    # --color never is load-bearing: FORCE_COLOR-style env makes `uv tool dir`
    # emit ANSI codes into command substitution, producing a nonexistent path
    assert (
        'tool_root="$$(uv tool dir --color never)/vibecrafted"' in install_tools_block
    )
    assert "uv tool dir)" not in install_tools_block
    assert "expected installed target" in install_tools_block
    assert "uv tool imports vibecrafted_core" in install_tools_block
    assert "$$stable_root/vibecrafted-core" in install_tools_block
    assert (
        "unset PYTHONPATH; \\\n"
        "\tuv tool install --force --reinstall --editable "
        '"$$stable_root/vibecrafted-core"; \\' in install_tools_block
    )
    assert "uv tool uninstall" not in install_tools_block
    assert "run_with_tools_install_lease" in install_tools_block
    assert "INSTALL_TOOLS_SERVICE_POLICY ?= preserve" in text
    assert 'service_policy=os.environ["VIBECRAFTED_INSTALL_SERVICE_POLICY"]' in (
        install_tools_block
    )
    assert "install-bundle-tools:" in text
    assert "install-tools-held" in install_tools_block
    assert "VIBECRAFTED_INSTALL_LEASE_FD" in install_tools_block
    assert "def _installer_lease_pass_fds(" in cli_text
    assert "lease_pass_fds = _installer_lease_pass_fds(tools_home)" in cli_text
    assert "pass_fds=lease_pass_fds" in cli_text
    assert "staging runtime under the cross-process installer lease" in (
        install_tools_block
    )
    assert "trap rollback_tools_handoff EXIT" not in install_tools_block
    assert "runtime_service_active_for_install" not in install_tools_block
    assert "prepare_runtime_service_for_install" not in install_tools_block
    assert "activate_runtime_service_after_install" not in install_tools_block
    assert "complete_current_tools_handoff" not in install_tools_block
    assert install_tools_block.index(
        '$(PYTHON) $(INSTALLER) install --source "$(SOURCE)"'
    ) < install_tools_block.index("uv tool install --force --reinstall")
    assert "outer lease owner will reconcile service ownership" in install_tools_block
    assert "scripts/slack_provider.py install" in install_tools_block
    assert '--framework-source "$(SOURCE)"' in install_tools_block
    assert '--source "$(SLACK_AGENT_SOURCE)"' in install_tools_block
    assert install_tools_block.index("scripts/slack_provider.py install") < (
        install_tools_block.index("outer lease owner will reconcile service ownership")
    )
    assert "prepare_runtime_service_for_install" in handoff_block
    assert "_runtime_lifecycle_handoff_fence" in handoff_block
    assert "_runtime_supervisor_handoff_fence" in handoff_block
    assert "_bootout_owned_runtime_launchd_job" in handoff_block
    assert "_rollback_current_tools_locked" in handoff_block
    assert "activate_runtime_service_after_install" in handoff_block
    assert "_complete_current_tools_handoff_locked" in handoff_block
    assert handoff_block.index(
        "prepare_runtime_service_for_install"
    ) < handoff_block.index("_runtime_lifecycle_handoff_fence")
    assert handoff_block.index(
        "_runtime_lifecycle_handoff_fence"
    ) < handoff_block.index("_runtime_supervisor_handoff_fence")
    assert handoff_block.index(
        "_runtime_supervisor_handoff_fence"
    ) < handoff_block.index("_run_install_child_with_lifecycle_guard")
    assert handoff_block.index(
        "_run_install_child_with_lifecycle_guard"
    ) < handoff_block.index("activate_runtime_service_after_install")
    assert handoff_block.index(
        "activate_runtime_service_after_install"
    ) < handoff_block.index("_complete_current_tools_handoff_locked")
    assert "install-python-tools: install-tools" in text


def test_make_install_enables_service_after_server_payload() -> None:
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    install_block = text.split("\ninstall:\n", 1)[1].split("\n# `make install`", 1)[0]
    held_block = text.split("\ninstall-tools-held:\n", 1)[1].split(
        "\n# install-all owns", 1
    )[0]
    service_block = text.split("\ninstall-server-service:\n", 1)[1].split(
        "\nserver-smoke:", 1
    )[0]

    assert "make --no-print-directory install-server'" not in install_block
    assert "$(MAKE) --no-print-directory install-bundle-tools" in install_block
    assert "$(MAKE) --no-print-directory install-server-payload" in held_block
    assert held_block.index("install-server-payload") > held_block.index(
        "uv tool install --force --reinstall"
    )
    assert "INSTALL_SERVER_SERVICE_POLICY ?= ensure" in text
    assert "service_policy=service_policy" in text
    assert 'if [ "$$(uname -s)" != "Darwin" ]' in service_block
    assert 'export PATH="$(BIN_DIR):$$PATH"' in service_block
    assert "command -v vibecrafted" in service_block
    assert "command -v vc-server-supervisor" in service_block
    assert '(cd / && "$$launcher" server service install)' in service_block
    assert '(cd / && "$$launcher" server service restart)' in service_block


def test_public_install_server_uses_transaction_and_payload_target_is_internal() -> (
    None
):
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    install_server_block = text.rsplit("\ninstall-server:", 1)[1].split(
        "\ninstall-server-service:", 1
    )[0]
    payload_block = text.split("\ninstall-server-payload:", 1)[1].split(
        "\ninstall-server:", 1
    )[0]

    assert "build-server-release" in install_server_block
    assert "run_with_tools_install_lease" in install_server_block
    assert "require_tools_handoff=False" in install_server_block
    assert "runtime_payload_paths=payload" in install_server_block
    assert "install-server-payload" in install_server_block
    assert "VIBECRAFTED_INSTALL_LEASE_FD" in payload_block
    assert "_require_inherited_tools_install_lease" in payload_block
    assert 'cp -R "$(SERVER_BUILD_SITE_ROOT)/." "$(SERVER_INSTALL_SITE_ROOT)/"' in (
        payload_block
    )
    service_block = text.split("\ninstall-server-service:", 1)[1].split(
        "\nserver-smoke:", 1
    )[0]
    assert service_block.index("unset PYTHONPATH") < service_block.index(
        'launcher="$$(command -v vibecrafted'
    )
    assert payload_block.index("VIBECRAFTED_INSTALL_LEASE_FD") < payload_block.index(
        "command -v cargo"
    )
    assert payload_block.index(
        "_require_inherited_tools_install_lease"
    ) < payload_block.index("command -v cargo")

    result = subprocess.run(
        ["make", "--no-print-directory", "install-server-payload"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "VIBECRAFTED_INSTALL_LEASE_FD": "",
        },
    )

    assert result.returncode != 0
    assert "internal payload target requires" in result.stderr


def test_internal_make_targets_reject_nonexistent_lease_descriptor(
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
        "VIBECRAFTED_TOOLS_HOME": str(tmp_path / "tools"),
        "VIBECRAFTED_INSTALL_LEASE_FD": "999",
    }
    for target in ("install-tools-held", "install-server-payload"):
        result = subprocess.run(
            ["make", "--no-print-directory", target],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        assert result.returncode != 0
        assert "Bad file descriptor" in result.stderr


def test_transactional_make_targets_select_non_mutating_dry_run_recipes(
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
        "VIBECRAFTED_TOOLS_HOME": str(tmp_path / "tools"),
    }
    for target, marker in (
        ("install-server", "dry-run: install payload under runtime transaction"),
        (
            "install-bundle-tools",
            "dry-run: build payload and run bundled install under one lease",
        ),
    ):
        result = subprocess.run(
            [
                "make",
                "-n",
                "--no-print-directory",
                target,
                "PYTHON=/usr/bin/false",
                "MAKE=/usr/bin/false",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

        assert result.returncode == 0
        assert marker in result.stdout
        assert "run_with_tools_install_lease" not in result.stdout


def test_launcher_does_not_pin_stale_supervisor_binary() -> None:
    launcher = (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8")
    resolver = launcher.split("_server_supervisor_binary() {", 1)[1].split("\n}", 1)[0]

    assert "command -v vc-server-supervisor" in resolver
    assert "$HOME/.local/bin/vc-server-supervisor" not in resolver
