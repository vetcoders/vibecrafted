from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_PAGE = "https://github.com/vetcoders/vibecrafted/releases/latest"

# Pinned so the Windows entry point cannot drift silently. Measured
# 2026-08-18; both copies agreed at this digest. See
# test_windows_entry_point_does_not_drift_between_its_two_copies for why a
# constant is needed on top of the cross-repo comparison.
INSTALL_PS1_SHA256 = "12c2ca5b95195a2fcee0f4987962fd35ec52dde85588c226f68bcab4680450b6"

# Binaries a developer laptop always has and the GitHub macos-15 image does
# not. Measured 2026-08-18 against actions/runner-images
# `images/macos/macos-15-Readme.md`: zero occurrences of either, and zero of
# shellcheck — which this same workflow independently confirms by having to
# brew install it. Calling one of these from a `run:` line is fatal at the
# step, with no fallback.
ABSENT_FROM_MACOS_RUNNER_IMAGE = ("rg", "fd")


def test_public_install_surfaces_name_all_release_carriers() -> None:
    surfaces = (
        "README.md",
        "docs/QUICK_START.md",
        "docs/public/getting-started/install.md",
    )
    for relative in surfaces:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert RELEASE_PAGE in text, f"{relative} must point to the unified release"
        assert "Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg" in text
        assert (
            "Vibecrafted_RuntimePack_<version>-<YYYYMMDD>-<sha8>-darwin-<arch>.tar.gz"
            in text
        ), f"{relative} must name the macOS CLI Runtime Pack"
        # A non-macOS reader must find a version-pinned artifact on the same
        # page, not only a curl-pipe-bash line that tracks a moving branch.
        assert "Vibecrafted_<version>-<YYYYMMDD>-<sha8>-portable.tar.gz" in text, (
            f"{relative} must name the portable channel asset"
        )
        assert "vc-frame/releases/latest/download/install.sh" not in text


def test_tag_workflow_is_a_read_only_source_gate() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "persist-credentials: false" in workflow
    assert 'test "$GITHUB_REF_TYPE" = "tag"' in workflow
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' in workflow
    assert (
        'release_tag_ref="refs/vibecrafted-release-tags/$GITHUB_REF_NAME"' in workflow
    )
    assert '"refs/tags/$GITHUB_REF_NAME:$release_tag_ref"' in workflow
    assert 'test "$(git cat-file -t "$release_tag_ref")" = "tag"' in workflow
    assert 'test "$(git rev-list -n 1 "$release_tag_ref")" = "$GITHUB_SHA"' in workflow
    assert "run: make unified-product-contract-gate" in workflow
    assert "run: make test-core" in workflow
    assert "run: make semgrep" in workflow
    assert "runs-on: macos-15" in workflow
    assert "run: brew install shellcheck" in workflow
    assert "ubuntu-latest" not in workflow
    assert "apt-get" not in workflow
    assert "contents: write" not in workflow
    assert "gh release create" not in workflow
    assert "gh release upload" not in workflow
    assert "install.sh" not in workflow
    assert "vibecrafted-framework.plugin" not in workflow


def test_tag_gate_only_calls_tools_its_own_runner_provides() -> None:
    """The gate may only shell out to binaries `macos-15` actually ships.

    Every tag since v3.7.0 failed this workflow, and v4.0.0 failed after 7m43s
    with `483 passed ... VCPC033: xcrun is required` — a green test suite killed
    by a host tool the runner did not have. `54a98b23` moved the job to
    `macos-15` to cure exactly that, but the final publication-boundary step was
    still calling `rg`, which the GitHub macos-15 image does not ship either.
    Measured 2026-08-18 against the published image manifest
    (actions/runner-images `images/macos/macos-15-Readme.md`): zero occurrences
    of ripgrep, and zero of shellcheck — which is independently corroborated by
    this same workflow having to `brew install shellcheck` before it can lint.
    shellcheck is checked separately at the bottom, because its absence
    degrades a step rather than failing it.

    That step arrived in ef700e52 and has never executed, because every tag
    since died at an earlier step. So the tool gap could not be caught by
    "the last release worked". It has to be caught here.

    Scope: the workflow's own `run:` lines. A tool reached through a `make`
    target is out of scope — those resolve at recipe level and several fall
    back to `uvx`.
    """
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    run_lines: list[str] = []
    in_run = False
    for raw in workflow.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^-?\s*(run|name|uses|with):", stripped):
            in_run = stripped.startswith(("run:", "- run:"))
            if in_run:
                run_lines.append(stripped.split("run:", 1)[1])
            continue
        if in_run and stripped:
            run_lines.append(stripped)

    assert run_lines, "no run: lines parsed out of the release workflow"

    # Read the installs out of the run lines, never out of the raw file: a
    # comment mentioning `brew install shellcheck` would otherwise satisfy the
    # allowance below without a single package being installed. Measured while
    # writing this test — the first draft passed for exactly that reason.
    installed = {
        match.group(1)
        for line in run_lines
        for match in re.finditer(r"brew install ([\w-]+)", line)
    }

    for tool in ABSENT_FROM_MACOS_RUNNER_IMAGE:
        if tool in installed:
            continue
        pattern = re.compile(rf"(?:^|[|;&]|\bcommand\s+)\s*{tool}\b")
        offenders = [line for line in run_lines if pattern.search(line)]
        assert not offenders, (
            f"release.yml calls `{tool}`, which the macos-15 runner image does "
            f"not ship and this workflow does not brew install: {offenders}"
        )

    # shellcheck is the quieter half of the same gap. `make check` does not die
    # without it — `scripts/check_shell.py` falls back to `bash -n`, so the
    # release gate would keep reporting green while silently degrading from a
    # linter to a syntax check. That is worse than a hard failure, because
    # nobody would ever see it. The brew install is what keeps the step honest.
    if any(line.strip() == "make check" for line in run_lines):
        assert "shellcheck" in installed, (
            "release.yml runs `make check` without brew installing shellcheck; "
            "check_shell.py would silently degrade to a syntax-only fallback "
            "on the macos-15 image"
        )


def test_publication_boundary_step_still_asserts_all_carrier_names() -> None:
    """The boundary step pins the exact three-carrier release shape.

    Rewriting its matcher (rg -> grep) must not quietly drop what it matches:
    one canonically named DMG and one portable tarball, each resolved by the
    publisher from a build script and documented in the kickoff.
    """
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "Vibecrafted_.*YYYYMMDD|DMG_NAME|\\.dmg\\.sha256" in workflow
    assert "PORTABLE_NAME|portable\\.tar\\.gz|portable-output\\.json" in workflow
    assert (
        "RUNTIME_PACK_NAME|RuntimePack_.*tar\\.gz|install-runtime-pack\\.sh" in workflow
    )
    for target in (
        "scripts/build-vibecrafted-release.sh",
        "scripts/build-portable-release.sh",
        "scripts/package-runtime-pack.sh",
        "scripts/install-runtime-pack.sh",
        "scripts/publish-vibecrafted-release.sh",
        "docs/RELEASE_KICKOFF.md",
    ):
        assert target in workflow, f"boundary step stopped covering {target}"


def test_native_carrier_embeds_every_required_agent_foundation() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    stager = (REPO_ROOT / "scripts/stage-runtime-foundations.sh").read_text(
        encoding="utf-8"
    )
    installer = (REPO_ROOT / "scripts/vetcoders_install.py").read_text(encoding="utf-8")

    foundation_stage = builder.index('stage-runtime-foundations.sh" "$runtime/bin"')
    for required_runtime_install in (
        '"$runtime/bin/vc-start"',
        '"$runtime/bin/vc-server"',
        '"$runtime/bin/vibecrafted-server-web"',
    ):
        assert builder.index(required_runtime_install) < foundation_stage
    assert "'screenscribe==0.1.19'" in builder
    assert '"$runtime/bin/screenscribe" --version' in builder
    assert '"$runtime/source-provenance.json"' in builder
    assert 'carrier --source "$REPO_ROOT"' in builder
    assert "provenance_stage" not in builder
    assert '"$runtime/scripts/vc-frame-product-entry.sh"' in builder
    for command in ("loct", "loctree-mcp", "aicx", "aicx-mcp", "prview"):
        assert command in stager
        assert f'generation / "bin/{command}"' in installer
    assert 'generation / "bin/screenscribe"' in installer
    assert 'generation / "libexec/vc-frame"' in installer
    assert "_write_runtime_generation_manifest(" in installer
    assert "runtime-foundations.json" in stager
    assert "OPENSSL_STATIC=1" in stager
    assert "PRView retains a non-system dynamic library dependency" in stager
    assert '"$AICX_REVISION" "$AICX_ARCHIVE_SHA256" <<\'PY\'' in stager
    assert '"aicx": aicx_revision' in stager
    assert "remap-path-prefix" in stager
    assert "cargo install --locked" in stager
    assert 'rm -rf "$WORK" 2>/dev/null || true' in stager


def test_macos_publisher_cold_verifies_exact_uploaded_bytes() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    publisher = (REPO_ROOT / "scripts/publish-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert "publish-release:" in makefile
    assert "scripts/publish-vibecrafted-release.sh" in makefile
    assert 'DMG_NAME="$(uv run python3' in publisher
    assert 'DMG="$DIST/$DMG_NAME"' in publisher
    assert 'DMG_CHECKSUM="$DMG.sha256"' in publisher
    assert 'test "$(uname -s)" = "Darwin"' in publisher
    assert "verify-vibecrafted-walkaround verify-release" in publisher
    assert "verify-vibecrafted-walkaround walkaround" in publisher
    assert 'gh release download "$TAG"' in publisher
    assert 'cmp "$DMG" "$DOWNLOAD_DIR/$DMG_NAME"' in publisher
    assert 'shasum -a 256 -c "$DMG_NAME.sha256"' in publisher
    assert "xcrun stapler validate" in publisher
    assert "spctl --assess --type open" in publisher
    assert "code-scanning/alerts?state=open&ref=refs/heads/main" in publisher
    assert "per_page=1" in publisher
    assert "gh api --paginate" not in publisher
    assert "--slurp --jq" not in publisher
    assert 'gh release edit "$TAG"' in publisher

    # The allowlist is enumerated, then sorted the way the downloaded listing is
    # sorted. Keep it an exact list: a wildcard would let an unaudited asset ride.
    allowlist = publisher.split('EXPECTED_ASSETS="$(printf ', 1)[1].split(
        "| LC_ALL=C sort)", 1
    )[0]
    for entry in (
        '"$DMG_NAME"',
        '"$DMG_NAME.sha256"',
        '"$RUNTIME_PACK_NAME"',
        '"$RUNTIME_PACK_NAME.sha256"',
        '"$RUNTIME_PACK_NAME.sig"',
        '"$PORTABLE_NAME"',
        '"$PORTABLE_NAME.sha256"',
        '"release-output.json"',
        '"release-output.json.sig"',
    ):
        assert entry in allowlist
    assert "LC_ALL=C sort" in publisher


def test_macos_publisher_cold_verifies_the_portable_channel() -> None:
    publisher = (REPO_ROOT / "scripts/publish-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    # Both channels must name the same commit, or the release ships two truths.
    assert 'test -s "$PORTABLE_OUTPUT" || die' in publisher
    assert '"portable-output does not name the exact root revision"' in publisher
    assert 'cmp "$PORTABLE" "$DOWNLOAD_DIR/$PORTABLE_NAME"' in publisher
    assert 'shasum -a 256 -c "$PORTABLE_NAME.sha256"' in publisher
    # A checksum proves delivery; the provenance check proves identity.
    assert 'tar -xzf "$DOWNLOAD_DIR/$PORTABLE_NAME"' in publisher
    assert '"$DISTRIBUTION_MANIFEST" check' in publisher
    assert '--expected-source-revision "$HEAD_SHA"' in publisher
    assert (
        'bash "$PORTABLE_UNPACK_DIR/$PORTABLE_ROOT_NAME/install.sh" --help' in publisher
    )


def test_macos_publisher_cold_verifies_runtime_pack_install_and_uninstall() -> None:
    publisher = (REPO_ROOT / "scripts/publish-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert '["runtime_pack"]["path"]' in publisher
    assert 'shasum -a 256 -c "$RUNTIME_PACK_NAME.sha256"' in publisher
    assert 'openssl dgst -sha256 -verify "$RUNTIME_PACK_PUBLIC_KEY"' in publisher
    assert 'cmp "$RUNTIME_PACK" "$DOWNLOAD_DIR/$RUNTIME_PACK_NAME"' in publisher
    assert publisher.count("--verify-only") == 2
    assert '--expected-terminal-revision "$VC_TERMINAL_SHA"' in publisher
    assert '--expected-frame-revision "$VC_FRAME_SHA"' in publisher
    assert "make --no-print-directory install RUNTIME_PACK=" in publisher
    assert "make --no-print-directory uninstall" in publisher
    assert 'find "$RUNTIME_PACK_SMOKE_HOME" -mindepth 1 -print -quit' in publisher


def test_portable_builder_binds_one_commit_and_proves_its_own_bytes() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    builder = (REPO_ROOT / "scripts/build-portable-release.sh").read_text(
        encoding="utf-8"
    )

    assert "portable:" in makefile
    assert "scripts/build-portable-release.sh" in makefile

    assert (
        'PORTABLE_NAME="Vibecrafted_${VERSION}-${RELEASE_DATE}-${ROOT_SHA:0:8}-portable.tar.gz"'
        in builder
    )
    assert 'RELEASE_DATE="${VIBECRAFTED_RELEASE_DATE:-$(date -u +%Y%m%d)}"' in builder
    assert 'die "source tree is dirty' in builder
    # The builder must not be able to emit bytes it has not re-validated.
    assert '"$MANIFEST" archive' in builder
    assert '"$MANIFEST" check' in builder
    assert '--expected-source-revision "$ROOT_SHA"' in builder
    assert 'tar -xzf "$PORTABLE" -C "$VERIFY_DIR"' in builder
    assert "io.vetcoders.vibecrafted.portable-output.v1" in builder
    # No signing identity, no notary account: this channel must build on Linux.
    assert "codesign" not in builder
    assert "xcrun" not in builder
    assert "KEYS" not in builder


def test_builder_emits_the_canonical_versioned_dmg_and_checksum() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    assert 'RELEASE_DATE="${VIBECRAFTED_RELEASE_DATE:-$(date -u +%Y%m%d)}"' in builder
    assert (
        'DMG_NAME="Vibecrafted_${VERSION}-${RELEASE_DATE}-${ROOT_SHA:0:8}.dmg"'
        in builder
    )
    assert 'RUNTIME_VERSION="${VERSION}+g${ROOT_SHA:0:8}"' in builder
    assert 'printf \'%s\\n\' "$RUNTIME_VERSION" > "$runtime/VERSION"' in builder
    assert 'DMG_CHECKSUM="$DMG.sha256"' in builder
    assert 'LEGACY_DMG="$DIST_DIR/Vibecrafted.dmg"' in builder
    assert (
        'RUNTIME_PACK_NAME="Vibecrafted_RuntimePack_${VERSION}-${RELEASE_DATE}-${ROOT_SHA:0:8}-${RUNTIME_PACK_PLATFORM}.tar.gz"'
        in builder
    )
    assert 'RUNTIME_PACK_PLATFORM="darwin-arm64"' in builder
    assert (
        'printf \'%s\\n\' "$RUNTIME_VERSION" > "$RUNTIME_PACK_RESOURCE_DIR/VERSION"'
        in builder
    )
    assert '"$REPO_ROOT/scripts/package-runtime-pack.sh"' in builder
    assert '-out "$RUNTIME_PACK_SIGNATURE" "$RUNTIME_PACK"' in builder
    assert 'install -m 0644 "$RUNTIME_PACK" "$EMBEDDED_RUNTIME_PACK"' in builder
    assert 'cmp "$EMBEDDED_RUNTIME_PACK" "$RUNTIME_PACK"' in builder
    assert '--runtime-pack "$RUNTIME_PACK"' in builder
    assert 'rm -f "$DMG_CHECKSUM" "$LEGACY_DMG"' in builder
    assert '/usr/bin/shasum -a 256 "$DMG_NAME"' in builder
    assert "-type d -name __pycache__" in builder
    assert "-name '*.pyc'" in builder
    assert "-name '.DS_Store'" in builder
    assert "build-server-release" in builder
    assert 'install -m 0755 "$server_source" "$runtime/bin/vc-server"' in builder
    assert '"$runtime/server/site/"' in builder
    assert '"$REPO_ROOT/scripts/render-python-entrypoint-launchers.py"' in builder
    assert '"$REPO_ROOT/vibecrafted-core/pyproject.toml"' in builder
    assert '"$runtime/runtime"' not in builder


def test_runtime_pack_signing_happens_after_final_copy_and_before_archive() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    packager = (REPO_ROOT / "scripts/package-runtime-pack.sh").read_text(
        encoding="utf-8"
    )

    copied = packager.index('cp -R "$payload_root/." "$root/"')
    signed = packager.index('sign_macho_tree "$root"')
    verified = packager.index('verify_macho_tree "$root" 1')
    foundations = packager.index("refresh-foundations")
    inventoried = packager.index("vibecrafted_core.runtime_pack_contract write")
    archived = packager.index('-czf "$candidate"')
    assert copied < signed < verified < foundations < inventoried < archived

    producer = builder.split("produce_runtime_pack() {", 1)[1].split(
        "\nembed_runtime_pack() {", 1
    )[0]
    materializer = builder.split("materialize_runtime_payload() {", 1)[1].split(
        "\nbuild_product() {", 1
    )[0]
    embed = builder.split("embed_runtime_pack() {", 1)[1].split(
        "\nmaterialize_runtime_payload() {", 1
    )[0]
    assert '--payload-root "$RUNTIME_PAYLOAD"' in producer
    assert '--app "$APP"' not in producer
    assert (
        'install -m 0755 "$terminal_source" "$runtime/libexec/vc-terminal"'
        in materializer
    )
    assert (
        'install -m 0755 "$runtime/scripts/vc-terminal-product-entry.sh"'
        in materializer
    )
    assert '"$runtime/bin/vc-terminal"' in materializer
    assert (
        'install -m 0755 "$terminal_source" "$runtime/bin/vc-terminal"'
        not in materializer
    )
    assert 'install -m 0755 "$frame_source" "$runtime/libexec/vc-frame"' in materializer
    assert 'install -m 0644 "$RUNTIME_PACK" "$EMBEDDED_RUNTIME_PACK"' in embed
    assert '--codesign-identity "$SIGNING_IDENTITY"' in builder
    assert (
        'packager_codesign_args+=(--codesign-keychain "$TEMP_KEYCHAIN_PATH")' in builder
    )
    standalone_preflight = builder.index(
        'verify_runtime_pack_macho_signatures "$RUNTIME_PACK"'
    )
    carrier_signature = builder.index('-out "$RUNTIME_PACK_SIGNATURE" "$RUNTIME_PACK"')
    assert standalone_preflight < carrier_signature


def test_exact_release_gate_is_release_only_and_repeats_the_repo_verifier() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    source_workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    release_workflow = (REPO_ROOT / ".github/workflows/release-dmg.yml").read_text(
        encoding="utf-8"
    )
    exact_gate = makefile.split("exact-release-contract-gate:", 1)[1].split(
        "\nrelease-version-gate:", 1
    )[0]

    assert "for pass in 1 2" in exact_gate
    assert "verify-vibecrafted-walkaround verify-release" in exact_gate
    assert "run: make exact-release-contract-gate" in release_workflow
    assert "exact-release-contract-gate" not in source_workflow


def test_notary_authentication_never_puts_the_password_in_process_argv() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    notary_function = builder[
        builder.index("notary_submit() {") : builder.index("strip_debug_stabs() {")
    ]

    assert '--keychain-profile "$NOTARY_PROFILE"' in notary_function
    assert '--key "$NOTARY_API_KEY_PATH"' in notary_function
    assert "--password" not in notary_function
    assert "NOTARY_PASSWORD" not in notary_function
    assert 'source "$NOTARY_ENV"' not in notary_function
    assert 'notarytool store-credentials "$fallback_profile"' in notary_function
    assert "if [[ ! -t 0 || ! -t 1 ]]; then" in notary_function
    assert "raw Apple-ID notarization credentials are not accepted headlessly" in (
        notary_function
    )


def test_xcodegen_project_is_generated_from_one_tracked_source() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert (REPO_ROOT / "vibecrafted-app/shell-agent/app/project.yml").is_file()
    assert "/vibecrafted-app/shell-agent/app/Vibecrafted.xcodeproj/" in ignore
    assert (
        'git -C "$REPO_ROOT" ls-files --error-unmatch "$generated_project"' in builder
    )
    assert "generated Xcode project must not be tracked" in builder


def test_release_entrypoint_renderer_uses_manifest_and_preserves_existing(
    tmp_path: Path,
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "entrypoint-fixture"
version = "1.0.0"

[project.scripts]
demo-cli = "demo_module:main"
native-cli = "demo_module:main"
""".lstrip(),
        encoding="utf-8",
    )
    module = tmp_path / "demo_module.py"
    module.write_text(
        "import json, sys\n"
        "def main():\n"
        "    print(json.dumps(sys.argv))\n"
        "    return 0\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    native = bin_dir / "native-cli"
    native.write_text("native implementation\n", encoding="utf-8")

    subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/render-python-entrypoint-launchers.py"),
            "--pyproject",
            str(pyproject),
            "--bin-dir",
            str(bin_dir),
        ],
        check=True,
    )

    assert native.read_text(encoding="utf-8") == "native implementation\n"
    result = subprocess.run(
        [str(bin_dir / "demo-cli"), "one"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path)},
        check=True,
        capture_output=True,
        text=True,
    )
    # argv[0] is the shim's ABSOLUTE path now (identity-guard contract:
    # the declared launcher path must be visible in process argv), with
    # VIBECRAFTED_DECLARED_LAUNCHER as the wrapper-chain override.
    assert json.loads(result.stdout) == [str(bin_dir / "demo-cli"), "one"]


def test_release_bundle_binds_the_vibecrafted_app_icon() -> None:
    project = (REPO_ROOT / "vibecrafted-app/shell-agent/app/project.yml").read_text(
        encoding="utf-8"
    )
    info_plist = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/Info.plist"
    ).read_text(encoding="utf-8")
    manifest = (REPO_ROOT / "scripts/unified_product_manifest.py").read_text(
        encoding="utf-8"
    )
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    icon_builder = (REPO_ROOT / "scripts/build-vibecrafted-icon.sh").read_text(
        encoding="utf-8"
    )
    icon = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/Vibecrafted.icns"

    assert "INFOPLIST_FILE: Vibecrafted/Info.plist" in project
    assert 'MARKETING_VERSION: "4.3.0"' in project
    assert '- "Vibecrafted.icns"' in project
    assert "<key>CFBundleIconFile</key>" in info_plist
    assert "<string>Vibecrafted.icns</string>" in info_plist
    assert 'plist["CFBundleIconFile"] = contract.PRODUCT_ICON_FILE' in manifest
    assert icon.is_file()
    assert icon.stat().st_size > 100_000
    assert "$TERMINAL_REPO/assets/icon/vc-terminal-icon.png" in builder
    assert "$TERMINAL_REPO/assets/icon/terminal.png" in builder
    assert '"$ICON_SOURCE" "$resources/Vibecrafted.icns" "$ICON_REFERENCE"' in builder
    assert "! -name 'Vibecrafted.icns'" in builder
    assert "iconutil -c icns" in icon_builder
    assert 'cmp -s "$ICONSET/icon_128x128.png" "$REFERENCE"' in icon_builder


def test_release_bundle_binds_the_canonical_terminal_policy_and_font() -> None:
    terminal = (REPO_ROOT / "config/vc-terminal/vibecrafted.toml").read_text(
        encoding="utf-8"
    )
    dark = (REPO_ROOT / "config/vc-terminal/themes/dark.toml").read_text(
        encoding="utf-8"
    )
    light = (REPO_ROOT / "config/vc-terminal/themes/light.toml").read_text(
        encoding="utf-8"
    )
    app_delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    installer = (REPO_ROOT / "scripts/vetcoders_install.py").read_text(encoding="utf-8")

    assert 'family = "Spot Mono"' in terminal
    assert "size = 18.5" in terminal
    assert "live_config_reload = true" in terminal
    assert 'background = "#0b0b12"' in dark
    assert 'background = "#fafafa"' in light
    assert 'chars = "\\u001b[101;9u"' in terminal
    assert "/Users/" not in terminal
    assert "Contents/Resources/fonts/SpotMono.ttc" in app_delegate
    assert "CTFontManagerRegisterFontsForURL" in app_delegate
    assert "kCTFontFamilyNameAttribute as String" in app_delegate
    assert 'CTFontDescriptorCreateWithNameAndSize("Spot Mono"' not in app_delegate
    assert (
        'terminal_policy_source = generation / "config/vc-terminal/vibecrafted.toml"'
        in installer
    )
    assert 'terminal_policy = product_config / "terminal-policy.toml"' in installer
    assert 'terminal_policy_source.read_text(encoding="utf-8")' in installer
    assert 'product_config / "vc-terminal" / "vc-terminal.toml"' in installer
    assert 'product_config / "terminal-entry.toml"' not in installer
    assert "_reclaim_product_terminal_debris" in installer
    assert "vc-terminal/alacritty.toml" in installer
    assert "launch-alt-screen" not in installer
    assert 'product_config / "terminal-theme.toml"' in installer
    assert 'product_config / "terminal.toml"' not in installer
    assert (
        'install -m 0755 "$terminal_source" "$runtime/libexec/vc-terminal"' in builder
    )
    assert "vc-terminal-product-entry.sh" in builder
    assert (
        'install -m 0644 "$SPOT_MONO_FONT" "$resources/fonts/SpotMono.ttc"' in builder
    )
    assert "missing licensed Spot Mono input" in builder
    assert "(OpenType|TrueType) font collection data" in builder


def test_mission_control_failure_board_exposes_absolute_failure_time() -> None:
    view = (
        REPO_ROOT
        / "vibecrafted-app/shell-agent/app/Vibecrafted/Views/MissionControlViewController.swift"
    ).read_text(encoding="utf-8")
    ffi = (REPO_ROOT / "vibecrafted-app/shell-agent/ffi/src/lib.rs").read_text(
        encoding="utf-8"
    )
    mission = (
        REPO_ROOT / "vibecrafted-app/tui-agent/src/mission_control.rs"
    ).read_text(encoding="utf-8")

    assert '("Date", "DATE", 145)' in view
    assert 'case "DATE": return dateTime(item.occurredAt)' in view
    assert "private static let iso8601DateFormatter" in view
    assert "private static let failureDateFormatter" in view
    assert "ISO8601DateFormatter().date" not in view
    assert "pub occurred_at: Option<String>" in ffi
    assert "occurred_at: Some(record.completed_at.to_rfc3339())" in mission


def test_signed_bundle_runtime_cannot_write_python_bytecode() -> None:
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    app_delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    vc_start = (REPO_ROOT / "vibecrafted-app/tui-agent/src/bin/vc_start.rs").read_text(
        encoding="utf-8"
    )

    assert "export PYTHONDONTWRITEBYTECODE=1" in builder
    assert 'environment["PYTHONDONTWRITEBYTECODE"] = "1"' in app_delegate
    assert '.env("PYTHONDONTWRITEBYTECODE", "1")' in vc_start
    assert '"$runtime/bin/python3" -c' in builder
    assert "bundled Python mutated the signed application payload" in builder


def test_publisher_writes_the_mandatory_release_report() -> None:
    publisher = (REPO_ROOT / "scripts/publish-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    for heading in (
        "## 1. Security gate",
        "## 2. Exposed surface inventory",
        "## 3. Deployment mode decision",
        "## 4. Post-release install smoke",
        "## Sign-off",
    ):
        assert heading in publisher
    assert ".vibecrafted/artifacts" in publisher
    assert "100.82.232.70:3025" in publisher


def test_vc_release_skill_locks_four_mandatory_report_sections() -> None:
    skill = (
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills/vc-release/SKILL.md"
    ).read_text(encoding="utf-8")
    template = (
        REPO_ROOT
        / "vibecrafted-core/vibecrafted_core/skills/vc-release/references/release-report-template.md"
    )

    assert "## Release Report Contract" in skill
    for required in (
        "**Security gate**",
        "**Exposed surface inventory**",
        "**Deployment mode decision**",
        "**Post-release install smoke**",
    ):
        assert required in skill
    assert "make semgrep" in skill
    assert "references/release-report-template.md" in skill

    template_text = template.read_text(encoding="utf-8")
    for heading in (
        "## 1. Security gate",
        "## 2. Exposed surface inventory",
        "## 3. Deployment mode decision",
        "## 4. Post-release install smoke",
        "## Sign-off",
    ):
        assert heading in template_text


def test_dirty_donors_are_a_release_flag_with_a_reaper_not_a_manual_ritual() -> None:
    """`--snapshot-donors` must build from detached worktrees and always reap.

    Roadmap 4.2.0 D2. Before this flag the operator hand-rolled
    `git worktree add --detach` into a temp dir; the dir disappeared first and
    left a ghost registration in the donor for a week.
    """

    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    library = (REPO_ROOT / "scripts/lib/donor-snapshot.sh").read_text(encoding="utf-8")
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "--snapshot-donors) SNAPSHOT_DONORS=1 ;;" in builder
    assert '. "$REPO_ROOT/scripts/lib/donor-snapshot.sh"' in builder
    # The reaper runs from the same trap that ends the keychain session, so it
    # fires on success, on error, and on Ctrl-C during a notarization wait.
    assert "donor_snapshot_reap || true" in builder
    assert "trap cleanup EXIT INT TERM HUP" in builder
    assert "materialize_donor_snapshots" in builder
    assert "VIBECRAFTED_RELEASE_FAIL_AFTER_SNAPSHOT" in builder

    # Regenerated plugin assets are deterministic derived output. Their
    # mutation must not make the binary claim that the immutable donor commit
    # itself was dirty.
    assert 'frame_release_sha="$(git_sha "$FRAME_REPO")"' in builder
    assert 'VC_FRAME_GIT_SHA="$frame_release_sha"' in builder
    assert "VC_FRAME_GIT_DIRTY=0" in builder

    # Reaping goes through git; `rm -rf` alone is what creates ghosts.
    assert "worktree add --detach" in library
    assert "worktree remove --force" in library
    assert "worktree prune" in library

    # Without the flag the refusal is unchanged: a receipt must not be built
    # from a tree that can move underneath it.
    assert "$label is dirty; release receipts refuse moving source" in builder

    assert "RELEASE_FLAGS ?=" in makefile
    # The flags must reach the builder as argv WORDS. Splicing $(RELEASE_FLAGS)
    # into the single-quoted `zsh -ic` argument handed the value to zsh as
    # source: measured 2026-08-18, `RELEASE_FLAGS='--snapshot-donors
    # $(touch /tmp/proof)'` created the file, because zsh evaluates command
    # substitution while building the exec's argv. (A `;` lands after `exec`
    # and never runs — the vector is narrower than it looks, and real.)
    for flag in (
        "--app-only",
        "--runtime-pack-only",
        "--no-notarize",
        "--notarize-only",
    ):
        assert f"{flag} $${{=VC_RELEASE_FLAGS}}" in makefile, flag
    assert makefile.count("VC_RELEASE_FLAGS='$(RELEASE_FLAGS)'") == 5
    # The only place $(RELEASE_FLAGS) may still be expanded is that leading
    # environment assignment. Nothing after `zsh -ic` may name it, because
    # everything after `zsh -ic` is source.
    spliced = [
        line.strip()
        for line in makefile.splitlines()
        if "$(RELEASE_SCRIPT)" in line
        and "$(RELEASE_FLAGS)" in line.partition("zsh -ic")[2]
    ]
    assert not spliced, spliced


def test_donor_remap_prefixes_are_resolved_never_concatenated() -> None:
    """A `..` inside a --remap-path-prefix never matches; measured on 4.1.0.

    In `Vibecrafted_4.1.0-20260817-237d2814.dmg` the strings `/usr/src/vc-frame`
    and `/usr/src/vc-terminal` are absent from every shipped binary while the
    living checkout path is present, because both donor prefixes were built as
    `"$REPO_ROOT/../vc-frame"` and the compiler matches prefixes textually.
    """

    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert "canonical_dir()" in builder
    assert (
        'TERMINAL_DONOR="$(canonical_dir "${VIBECRAFTED_TERMINAL_REPO:-$REPO_ROOT/../vc-terminal}")"'
        in builder
    )
    assert (
        'FRAME_DONOR="$(canonical_dir "${VIBECRAFTED_FRAME_REPO:-$REPO_ROOT/../vc-frame}")"'
        in builder
    )
    assert '"$TERMINAL_DONOR=/usr/src/vc-terminal"' in builder
    assert '"$FRAME_DONOR=/usr/src/vc-frame"' in builder


def test_remaps_run_broadest_prefix_first() -> None:
    """rustc applies the LAST matching --remap-path-prefix.

    Measured 2026-08-18 with two overlapping prefixes: outer-then-inner reports
    the inner mapping, inner-then-outer reports the outer one. So `$HOME` has to
    come FIRST or it shadows every specific root on any host whose checkout
    lives under it, and the donor snapshots — which sit under `$REPO_ROOT/build`
    — have to come AFTER `$REPO_ROOT`.
    """
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    order = [
        '"$HOME=/usr/src/operator-home"',
        '"$REPO_ROOT=/usr/src/vibecrafted"',
        '"$TERMINAL_DONOR=/usr/src/vc-terminal"',
        '"$FRAME_DONOR=/usr/src/vc-frame"',
        '"$TERMINAL_REPO=/usr/src/vc-terminal"',
        '"$FRAME_REPO=/usr/src/vc-frame"',
    ]
    positions = [builder.index(entry) for entry in order]
    assert positions == sorted(positions), "remap order is no longer broadest-first"

    # The snapshot pair is emitted only when it can differ from the donors.
    snapshot_block = builder[
        builder.index("PATH_REMAPS=(") : builder.index('RUSTFLAGS=""')
    ]
    assert "if (( SNAPSHOT_DONORS )); then" in snapshot_block


def test_every_compiler_in_the_build_gets_a_prefix_map() -> None:
    """RUSTFLAGS is one of four producers; the other three need their own.

    Measured on the shipped 4.1.0 payload: cc-rs left 21 `$HOME` paths from
    ring's C sources in Contents/MacOS/Vibecrafted, and xcodebuild left 51
    checkout paths from Swift sources and DerivedData intermediates. Neither
    reads RUSTFLAGS.
    """
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert "-ffile-prefix-map=$mapping" in builder
    assert "export CFLAGS=" in builder
    assert "export CXXFLAGS=" in builder
    assert "-debug-prefix-map $mapping" in builder
    assert "OTHER_SWIFT_FLAGS=" in builder
    assert "OTHER_CFLAGS=" in builder


def test_bundled_wasm_plugins_are_rebuilt_only_inside_a_snapshot() -> None:
    """The blobs are git-tracked build output; a flag cannot reach them.

    `make release-binary` builds `--no-plugins` and the binary embeds
    zellij-utils/assets/plugins/*.wasm via include_bytes!. Measured 2026-08-18:
    276 occurrences of `$HOME/.cargo/registry` across the 14 tracked blobs, 411
    of which reached Contents/Helpers/vc-frame in the shipped DMG. Rebuilding
    them under the release remaps drops both counts to zero — measured on all
    fourteen inside a real snapshot worktree.

    It must not happen against the living donor: that would rewrite tracked
    files another agent may be mid-edit on.
    """
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    rebuild_at = builder.index('make -C "$FRAME_REPO" plugins-assets')
    guard_at = builder.rindex("if (( SNAPSHOT_DONORS )); then", 0, rebuild_at)
    assert rebuild_at - guard_at < 400, "the plugin rebuild escaped its snapshot guard"

    binary_at = builder.index('make -C "$FRAME_REPO" release-binary')
    assert rebuild_at < binary_at, (
        "plugins must be rebuilt before the binary embeds them"
    )


def test_the_clean_repo_allowance_is_exactly_the_regenerated_plugin_assets() -> None:
    """Rebuilding into the snapshot makes it dirty; the allowance is narrow.

    Measured on a real snapshot: `git status --porcelain` afterwards lists
    exactly SHA256SUMS and the fourteen .wasm files under
    zellij-utils/assets/plugins/, and nothing else. The receipt still binds the
    donor HEAD, and those files are a deterministic function of it.
    """
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'FRAME_DERIVED=("zellij-utils/assets/plugins/")' in builder
    assert (
        'require_clean_repo "$FRAME_REPO" vc-frame ${FRAME_DERIVED+"${FRAME_DERIVED[@]}"}'
        in builder
    )
    # The pre-build assertion stays strict: a snapshot that is dirty on arrival
    # is a refusal, not an allowance. Exactly one of the two frame assertions
    # carries the allowance, and vc-terminal never gets one — nothing
    # regenerates into it.
    frame_calls = [
        line.strip()
        for line in builder.splitlines()
        if 'require_clean_repo "$FRAME_REPO"' in line
    ]
    assert len(frame_calls) == 2, frame_calls
    assert sum("FRAME_DERIVED" in line for line in frame_calls) == 1, frame_calls
    assert not any(
        "DERIVED" in line
        for line in builder.splitlines()
        if 'require_clean_repo "$TERMINAL_REPO"' in line
    )


def test_the_embedded_interpreter_forgets_where_it_was_seeded() -> None:
    """Two leaks, one of which was also a broken script.

    Measured on the 4.1.0 payload: 27 mentions of the ephemeral
    `build/unified-release/python-seed.XXXXXX/` directory inside
    `runtime/python/lib/python3.12/_sysconfigdata__darwin_darwin.py`, and a pip
    console script at `runtime/python-site/bin/jsonschema` whose shebang points
    at that same directory — a path no customer has, so the script could never
    have run. python-site is on PYTHONPATH and never on PATH, so nothing
    invoked it.
    """
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert 'rm -rf "$runtime/python-site/bin"' in builder
    assert "normalize_embedded_python_paths()" in builder
    assert 'normalize_embedded_python_paths "$runtime" "$python_seed"' in builder


def test_release_binaries_never_probe_the_machine_that_compiled_them() -> None:
    """`env!("CARGO_MANIFEST_DIR")` is opaque to --remap-path-prefix.

    Both call sites used the constant as a runtime filesystem probe, so the
    shipped binary carried the builder's checkout AND behaved differently on
    that one machine — the machine the release is walked around on.
    """
    config = (REPO_ROOT / "vibecrafted-app/tui-agent/src/config.rs").read_text(
        encoding="utf-8"
    )
    tray = (REPO_ROOT / "vibecrafted-app/mux-agent/src/tray.rs").read_text(
        encoding="utf-8"
    )

    for source, name in ((config, "config.rs"), (tray, "tray.rs")):
        lines = source.splitlines()
        call_sites = [
            index
            for index, line in enumerate(lines)
            if 'env!("CARGO_MANIFEST_DIR")' in line
            and not line.lstrip().startswith("//")
        ]
        assert call_sites, f"{name} no longer reads CARGO_MANIFEST_DIR at all"
        for index in call_sites:
            window = lines[max(0, index - 8) : index]
            assert any("#[cfg(debug_assertions)]" in line for line in window), (
                f"{name}:{index + 1} probes CARGO_MANIFEST_DIR outside a "
                "debug-only guard"
            )


def test_release_strips_linker_paths_and_pins_frame_source_identity() -> None:
    """Final Mach-O bytes must not retain snapshot or DerivedData object paths."""
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )

    assert (
        'CARGO_PROFILE_RELEASE_STRIP=false make -C "$FRAME_REPO" plugins-assets'
        in builder
    )
    assert "VC_FRAME_SOURCE_MANIFEST_DIR=/usr/src/vc-frame/zellij-utils" in builder
    assert '"$APP/Contents/MacOS/Vibecrafted"' in builder
    assert '"$terminal_app/Contents/MacOS/alacritty"' in builder
    assert '"$APP/Contents/Helpers/vc-frame"' in builder
    strip_at = builder.index("/usr/bin/strip -S")
    hygiene_at = builder.index('assert_payload_is_anonymous "$APP"')
    assert strip_at < hygiene_at


def test_windows_entry_point_does_not_drift_between_its_two_copies() -> None:
    """`install.ps1` lives here and in vibecrafted-io; two copies means drift.

    Roadmap 4.2.0 cut W1-c. The site copy is what a Windows user would fetch
    over HTTPS, so the moment the two differ the served script is a lie about
    this repository. Measured 2026-08-18: identical, and
    `https://vibecrafted.io/install.ps1` still answers 404 — not because the
    pipeline drops non-HTML assets (`/install.sh` answers 200) but because the
    site repo's deploy branch has not received the commit that added it.
    """

    framework = REPO_ROOT / "install.ps1"
    assert framework.is_file()
    digest = hashlib.sha256(framework.read_bytes()).hexdigest()

    # The cross-repo comparison below is opportunistic: it skips wherever
    # vibecrafted-io is not checked out, which is everywhere except this
    # laptop, so on its own it guards nothing in CI. The pin is what bites.
    # Editing install.ps1 now forces a deliberate bump of this constant, and
    # that bump is the reminder that the served copy has to move too.
    assert digest == INSTALL_PS1_SHA256, (
        "install.ps1 changed. Update the served copy in vibecrafted-io "
        f"(site/public/install.ps1) and set INSTALL_PS1_SHA256 to {digest}"
    )

    served = REPO_ROOT.parent / "vibecrafted-io/site/public/install.ps1"
    if not served.is_file():
        pytest.skip("vibecrafted-io is not checked out beside this repository")
    assert digest == hashlib.sha256(served.read_bytes()).hexdigest(), (
        "install.ps1 drifted between the framework repo and the served site copy"
    )


def test_python_entrypoint_launcher_declares_identity_path(tmp_path):
    """The rendered shim must put a DECLARED absolute path into process argv.

    The deck's identity guard matches the declared launcher path against the
    live argv. A bare `launcher=vc-guardian` made capture-identity fail through
    every wrapper chain: `server start` rolled a healthy server back
    (2026-08-19). The wrapper exports VIBECRAFTED_DECLARED_LAUNCHER="$0"; the
    shim must honor it and fall back to its own absolute path.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "render_launchers_mod",
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "render-python-entrypoint-launchers.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\nversion = "0"\n'
        '[project.scripts]\nvc-guardian = "vibecrafted_core.guardian:main"\n',
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    created = mod.render_launchers(pyproject, bin_dir)
    assert created == ["vc-guardian"]
    payload = (bin_dir / "vc-guardian").read_text(encoding="utf-8")
    assert (
        'launcher="${VIBECRAFTED_DECLARED_LAUNCHER:-$bin_dir/vc-guardian}"' in payload
    )
    assert "launcher=vc-guardian\n" not in payload


def test_deck_server_service_translates_short_host_port_flags(tmp_path):
    """`vibecrafted server service install -h X -p Y` must reach the supervisor
    as --host/--port — argparse owns `-h` as --help, so the raw forward printed
    usage and exited 0: an install that never happened (2026-08-19).
    """
    import os
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    deck = repo / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    capture = tmp_path / "argv.txt"
    supervisor = fake_bin / "vc-server-supervisor"
    supervisor.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"\nexit 0\n',
        encoding="utf-8",
    )
    supervisor.chmod(0o755)
    # _server_launcher_path requires a sibling `vibecrafted` executable.
    (fake_bin / "vibecrafted").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (fake_bin / "vibecrafted").chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CAPTURE_FILE"] = str(capture)
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home")
    result = subprocess.run(
        [
            "bash",
            str(deck),
            "server",
            "service",
            "status",
            "-h",
            "9.9.9.9",
            "-p",
            "3025",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").split()
    assert "--host" in argv and argv[argv.index("--host") + 1] == "9.9.9.9"
    assert "--port" in argv and argv[argv.index("--port") + 1] == "3025"
    assert "-h" not in argv
