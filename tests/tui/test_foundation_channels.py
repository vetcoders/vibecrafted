"""Loctree, AICX, PRView and ScreenScribe come from their own channels.

npm carries @loctree/loctree and @loctree/aicx, GitHub releases carry
vetcoders/prview-rs, PyPI carries screenscribe. The Runtime Pack carried
pinned copies from before those channels existed; agents never resolved them
(runtime_paths.agent_tool_search_path strips the generation bin), so they only
lagged their channels and blocked the platforms the channels do not publish.
"""

from __future__ import annotations

import hashlib
import platform
import subprocess
import tarfile
from pathlib import Path

import pytest
from vibecrafted_core import runtime_pack_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "scripts/install-foundations.sh"

BUILDERS = (
    "scripts/build-vibecrafted-release.sh",
    "scripts/build-linux-arm64-runtime-pack.sh",
    "scripts/build-windows-x64-runtime-pack.ps1",
)


def _executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_installer(
    tmp_path: Path, *targets: str, path: list[Path], **extra: str
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": ":".join([*map(str, path), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
        "TMPDIR": str(tmp_path),
        **extra,
    }
    return subprocess.run(
        ["bash", str(INSTALLER), *targets],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


# --- the pack carries none of them -------------------------------------------


def test_the_pack_staging_of_channel_tools_is_gone() -> None:
    for retired in (
        "scripts/stage-runtime-foundations.sh",
        "scripts/lib/darwin-relocate-openssl.sh",
        "scripts/lib/published-foundation-digests.json",
    ):
        assert not (REPO_ROOT / retired).exists(), retired
    for builder in BUILDERS:
        text = (REPO_ROOT / builder).read_text(encoding="utf-8")
        for token in (
            "stage-runtime-foundations",
            "screenscribe==",
            "npm pack",
            "@loctree/",
            "prview-rs/releases",
        ):
            assert token not in text, (builder, token)
    for builder in BUILDERS[:2]:
        text = (REPO_ROOT / builder).read_text(encoding="utf-8")
        assert "runtime_pack_contract write-foundations" in text, builder


def _payload(root: Path) -> Path:
    for name in sorted(runtime_pack_contract.REQUIRED_FOUNDATION_EXECUTABLES):
        _executable(root / "bin" / name)
    return root


def test_write_foundations_records_only_the_packs_own_executables(
    tmp_path: Path,
) -> None:
    root = _payload(tmp_path / "runtime")

    manifest = runtime_pack_contract.write_runtime_foundations(root)

    assert set(manifest["files"]) == set(
        runtime_pack_contract.REQUIRED_FOUNDATION_EXECUTABLES
    )
    for field in ("versions", "source_revisions", "source_archives", "licenses"):
        assert manifest[field] == {}


@pytest.mark.parametrize(
    "carried",
    [
        "bin/loct",
        "bin/aicx-mcp",
        "bin/prview.exe",
        "libexec/prview",
        "python-site/screenscribe",
    ],
)
def test_the_pack_contract_refuses_a_carried_channel_tool(
    tmp_path: Path, carried: str
) -> None:
    root = _payload(tmp_path / "runtime")
    target = root / carried
    if carried.startswith("python-site/"):
        target.mkdir(parents=True)
    else:
        _executable(target)

    with pytest.raises(
        runtime_pack_contract.RuntimePackContractError,
        match="carries tools that ship through their own channel",
    ):
        runtime_pack_contract.write_runtime_foundations(root)


def test_no_platform_inventory_lists_a_channel_tool() -> None:
    channel = runtime_pack_contract.CHANNEL_FOUNDATION_EXECUTABLES
    assert channel == {
        "aicx",
        "aicx-mcp",
        "loct",
        "loctree",
        "loctree-lsp",
        "loctree-mcp",
        "prview",
        "screenscribe",
    }
    for inventory in (
        runtime_pack_contract.REQUIRED_FOUNDATION_EXECUTABLES,
        runtime_pack_contract.LINUX_EXECUTABLES,
        runtime_pack_contract.WINDOWS_X64_MANDATORY_EXECUTABLES,
        runtime_pack_contract.WINDOWS_X64_OPTIONAL_EXECUTABLES,
        frozenset(runtime_pack_contract.WINDOWS_X64_CLASSIFICATIONS),
    ):
        assert not inventory & channel


# --- the installer takes each from its channel --------------------------------


def test_make_install_asks_every_channel() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "scripts/install-foundations.sh loctree aicx prview screenscribe" in makefile
    assert 'LOCTREE_NPM_PACKAGE="@loctree/loctree"' in installer
    assert 'AICX_NPM_PACKAGE="@loctree/aicx"' in installer
    assert "https://github.com/$PRVIEW_REPO/releases/latest/download" in installer
    assert 'PRVIEW_REPO="vetcoders/prview-rs"' in installer
    assert 'SCREENSCRIBE_PACKAGE="screenscribe"' in installer
    for gone in ("loct.io/install.sh", "stage-runtime-foundations", "cargo install"):
        assert gone not in installer


def _fake_node_and_npm(fake_bin: Path, prefix: Path, capture: Path) -> None:
    _executable(fake_bin / "node")
    _executable(
        fake_bin / "npm",
        "#!/bin/sh\n"
        "set -eu\n"
        'printf "%s\\n" "$*" >> "$NPM_CAPTURE"\n'
        'case "$3" in\n'
        '  @loctree/loctree) names="loct loctree loctree-mcp loctree-lsp" ;;\n'
        '  @loctree/aicx) names="aicx aicx-mcp" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
        "for name in $names; do\n"
        '  printf "#!/bin/sh\\nexit 0\\n" > "$NPM_PREFIX_BIN/$name"\n'
        '  chmod +x "$NPM_PREFIX_BIN/$name"\n'
        "done\n",
    )
    prefix.mkdir(parents=True, exist_ok=True)


def test_loctree_and_aicx_install_from_npm_when_missing(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    npm_bin = tmp_path / "npm-prefix/bin"
    capture = tmp_path / "npm-args.txt"
    _fake_node_and_npm(fake_bin, npm_bin, capture)

    # npm's global bin is on PATH, as it is for a real npm prefix.
    result = _run_installer(
        tmp_path,
        "loctree",
        "aicx",
        path=[npm_bin, fake_bin],
        NPM_CAPTURE=str(capture),
        NPM_PREFIX_BIN=str(npm_bin),
        REQUIRE_FOUNDATIONS="1",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = capture.read_text(encoding="utf-8").splitlines()
    assert "install -g @loctree/loctree" in calls
    assert "install -g @loctree/aicx" in calls


def test_a_working_foundation_is_left_exactly_as_the_user_installed_it(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    capture = tmp_path / "npm-args.txt"
    for name in ("loct", "loctree", "loctree-mcp", "aicx", "aicx-mcp"):
        _executable(fake_bin / name)
    _executable(fake_bin / "node")
    _executable(fake_bin / "npm", '#!/bin/sh\necho "$*" >> "$NPM_CAPTURE"\n')

    result = _run_installer(
        tmp_path,
        "loctree",
        "aicx",
        path=[fake_bin],
        NPM_CAPTURE=str(capture),
        REQUIRE_FOUNDATIONS="1",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not capture.exists()


def _prview_release(tmp_path: Path, *, checksum: str | None = None) -> str:
    """A file:// release directory shaped like vetcoders/prview-rs releases."""
    target = {
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    }.get((platform.system(), platform.machine()), "unpublished-host")
    release = tmp_path / "release"
    staged = tmp_path / "staged"
    _executable(staged / "prview", "#!/bin/sh\necho 'prview 0.0.0-test'\n")
    release.mkdir()
    asset = release / f"prview-{target}.tar.gz"
    with tarfile.open(asset, "w:gz") as archive:
        archive.add(staged / "prview", arcname="prview")
    digest = checksum or hashlib.sha256(asset.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  {asset.name}\n", encoding="utf-8")
    return target


def test_a_prview_asset_that_misses_its_sha256sums_is_refused(tmp_path: Path) -> None:
    target = _prview_release(tmp_path, checksum="0" * 64)

    result = _run_installer(
        tmp_path,
        "prview",
        path=[tmp_path / "bin"],
        PRVIEW_RELEASE_BASE=f"file://{tmp_path / 'release'}",
    )
    output = result.stdout + result.stderr

    assert not (tmp_path / "home/.local/bin/prview").exists()
    if target == "unpublished-host":
        assert "publishes no GitHub release asset" in output
    else:
        assert "does not match the release SHA256SUMS" in output


def test_a_matching_prview_asset_still_needs_the_publishers_signature_on_macos(
    tmp_path: Path,
) -> None:
    target = _prview_release(tmp_path)

    result = _run_installer(
        tmp_path,
        "prview",
        path=[tmp_path / "bin"],
        PRVIEW_RELEASE_BASE=f"file://{tmp_path / 'release'}",
    )
    output = result.stdout + result.stderr
    installed = tmp_path / "home/.local/bin/prview"

    if target == "aarch64-apple-darwin":
        assert "is not signed by Developer ID team MW223P3NPX" in output
        assert not installed.exists()
    elif target == "x86_64-unknown-linux-gnu":
        assert installed.is_file(), output
        assert "Installed prview 0.0.0-test from GitHub releases" in output
    else:
        assert "publishes no GitHub release asset" in output


def test_prview_never_overwrites_another_install_in_the_launcher_bin(
    tmp_path: Path,
) -> None:
    target = _prview_release(tmp_path)
    broken = _executable(tmp_path / "home/.local/bin/prview", "#!/bin/sh\nexit 3\n")

    result = _run_installer(
        tmp_path,
        "prview",
        path=[tmp_path / "bin"],
        PRVIEW_RELEASE_BASE=f"file://{tmp_path / 'release'}",
    )

    assert broken.read_text(encoding="utf-8") == "#!/bin/sh\nexit 3\n"
    if target != "unpublished-host":
        assert (
            "exists but does not run; not overwriting" in result.stdout + result.stderr
        )


# --- screenscribe from PyPI through pipx ---------------------------------------


def test_screenscribe_installs_from_pypi_through_pipx(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    capture = tmp_path / "pipx-args.txt"
    screenscribe = fake_bin / "screenscribe"
    _executable(
        fake_bin / "pipx",
        "#!/bin/sh\n"
        "set -eu\n"
        'printf "%s\\n" "$@" > "$PIPX_CAPTURE"\n'
        "printf '#!/bin/sh\\nexit 0\\n' > \"$SCREENSCRIBE_BIN\"\n"
        'chmod +x "$SCREENSCRIBE_BIN"\n',
    )

    result = _run_installer(
        tmp_path,
        "screenscribe",
        path=[fake_bin],
        PIPX_CAPTURE=str(capture),
        SCREENSCRIBE_BIN=str(screenscribe),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "install",
        "--force",
        "screenscribe",
    ]


def _run_screenscribe_install_with_failing_pipx(
    tmp_path: Path, *, require_foundations: str
) -> subprocess.CompletedProcess[str]:
    # Ubuntu 22.04 CI, 2026-09-24: pipx's default interpreter is 3.10 while
    # screenscribe requires >=3.11, so pipx exits non-zero.
    fake_bin = tmp_path / "bin"
    _executable(fake_bin / "pipx", "#!/bin/sh\nexit 1\n")
    return _run_installer(
        tmp_path,
        "screenscribe",
        path=[fake_bin],
        REQUIRE_FOUNDATIONS=require_foundations,
    )


def test_a_screenscribe_install_failure_is_named_but_does_not_abort_make_install(
    tmp_path: Path,
) -> None:
    result = _run_screenscribe_install_with_failing_pipx(
        tmp_path, require_foundations="0"
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "pipx failed to install screenscribe." in output
    assert "Python >= 3.11" in output
    assert "screenscribe unavailable" in output


def test_strict_foundations_still_refuse_a_missing_screenscribe(
    tmp_path: Path,
) -> None:
    result = _run_screenscribe_install_with_failing_pipx(
        tmp_path, require_foundations="1"
    )

    assert result.returncode != 0, result.stdout + result.stderr
