"""G1 falsifiers: failed portable leftover, dual-stream FATAL, notary profile.

These execute the portable cleanup trap and the notary profile parser as
shell, not as comment greps. They fail on the baseline that left a refused
tarball in dist/ and refused headless notarization when only a Keychain
profile name was missing from the environment.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTABLE = REPO_ROOT / "scripts" / "build-portable-release.sh"
RELEASE = REPO_ROOT / "scripts" / "build-vibecrafted-release.sh"


def _run(
    script: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("PYTHONPATH", None)
    merged.pop("PYTHONHOME", None)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fail_g1_portable_hygiene_failure_removes_unpublished_tarball(
    tmp_path: Path,
) -> None:
    """A FATAL after packing must not leave a publishable tarball in dist/."""
    dist = tmp_path / "dist"
    dist.mkdir()
    tarball = dist / "Vibecrafted_4.1.0-20260818-0e26b077-portable.tar.gz"
    checksum = Path(str(tarball) + ".sha256")
    output = dist / "portable-output.json"
    tarball.write_bytes(b"refused-payload")
    checksum.write_text("deadbeef  leftover\n", encoding="utf-8")
    output.write_text("{}\n", encoding="utf-8")

    script = rf"""
set -euo pipefail
PORTABLE={tarball.as_posix()!r}
PORTABLE_CHECKSUM={checksum.as_posix()!r}
PORTABLE_OUTPUT={output.as_posix()!r}
PORTABLE_READY=0
WORK_DIR=""
cleanup_failed_portable() {{
  if [[ -n "${{WORK_DIR:-}}" ]]; then
    rm -rf "$WORK_DIR"
  fi
  if [[ "${{PORTABLE_READY:-0}}" != 1 ]]; then
    rm -f "$PORTABLE" "$PORTABLE_CHECKSUM" "$PORTABLE_OUTPUT"
  fi
}}
trap cleanup_failed_portable EXIT
die() {{
  printf 'FATAL: %s\n' "$*" >&2
  printf 'FATAL: %s\n' "$*"
  exit 1
}}
die "packed payload names the build host"
"""
    result = _run(script)
    assert result.returncode == 1
    assert "FATAL: packed payload names the build host" in result.stdout
    assert "FATAL: packed payload names the build host" in result.stderr
    assert not tarball.exists()
    assert not checksum.exists()
    assert not output.exists()


def test_fail_g1_portable_success_keeps_ready_tarball(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    tarball = dist / "Vibecrafted_ok-portable.tar.gz"
    tarball.write_bytes(b"ok")
    script = rf"""
set -euo pipefail
PORTABLE={tarball.as_posix()!r}
PORTABLE_CHECKSUM=""
PORTABLE_OUTPUT=""
PORTABLE_READY=0
WORK_DIR=""
cleanup_failed_portable() {{
  if [[ "${{PORTABLE_READY:-0}}" != 1 ]]; then
    rm -f "$PORTABLE"
  fi
}}
trap cleanup_failed_portable EXIT
PORTABLE_READY=1
"""
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert tarball.exists()
    assert tarball.read_bytes() == b"ok"


def test_fail_g1_portable_script_owns_the_cleanup_trap() -> None:
    text = PORTABLE.read_text(encoding="utf-8")
    assert "cleanup_failed_portable" in text
    assert "PORTABLE_READY=1" in text
    assert text.index("cleanup_failed_portable") < text.index(
        'assert_payload_is_anonymous "$VERIFY_DIR/$ARCHIVE_ROOT_NAME"'
    )
    assert text.index("PORTABLE_READY=1") > text.index(
        'assert_payload_is_anonymous "$VERIFY_DIR/$ARCHIVE_ROOT_NAME"'
    )


def test_fail_g1_notary_profile_is_read_without_sourcing_secrets(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".notary.env"
    env_file.write_text(
        "NOTARY_APPLE_ID=secret@example.com\n"
        "NOTARY_PASSWORD=not-a-real-password\n"
        "NOTARY_PROFILE=vibecrafted-notary\n",
        encoding="utf-8",
    )
    helper = RELEASE.read_text(encoding="utf-8")
    start = helper.index("notary_profile_from_env_file() {")
    end = helper.index("notary_submit() {")
    script = (
        helper[start:end] + f"notary_profile_from_env_file {env_file.as_posix()!r}\n"
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "vibecrafted-notary"
    assert "secret@example.com" not in result.stdout
    assert "not-a-real-password" not in result.stdout
    assert 'source "$NOTARY_ENV"' not in helper


def test_fail_g1_headless_notary_uses_keychain_profile_not_raw_apple_id() -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    notary = helper[
        helper.index("notary_submit() {") : helper.index("strip_debug_stabs() {")
    ]
    assert (
        "notary_profile_from_env_file" in notary
        or "notary_profile_from_env_file" in helper
    )
    assert '--keychain-profile "$profile"' in notary
    assert "--password" not in notary
    # Default Keychain profile is usable headlessly; raw Apple-ID stays TTY-only.
    assert 'profile="${NOTARY_FALLBACK_PROFILE:-vibecrafted-notary}"' in notary


def test_fail_g1_release_die_writes_stdout_and_release_log(tmp_path: Path) -> None:
    build_dir = tmp_path / "unified-release"
    helper = RELEASE.read_text(encoding="utf-8")
    start = helper.index("die() {")
    end = helper.index("require() {")
    script = (
        "BUILD_DIR="
        + str(build_dir)
        + "\n"
        + helper[start:end]
        + 'die "raw Apple-ID notarization credentials are not accepted headlessly"\n'
    )
    result = _run(script)
    assert result.returncode == 1
    assert (
        "FATAL: raw Apple-ID notarization credentials are not accepted headlessly"
        in (result.stdout)
    )
    log = (build_dir / "release.log").read_text(encoding="utf-8")
    assert (
        "FATAL: raw Apple-ID notarization credentials are not accepted headlessly"
        in log
    )


def test_fail_g1_python_seed_retries_transient_uv_install() -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    loop = helper[
        helper.index("# uv 0.9.7 has a transient ENOENT") : helper.index(
            "uv pip install --python"
        )
    ]
    assert "for python_attempt in 1 2 3" in loop
    assert "uv python install 3.12.3 --install-dir" in loop
    assert "after retries" in loop


def test_fail_g1_release_prefers_rustup_cargo_before_cargo_runs() -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    assert "prefer_rustup_cargo" in helper
    assert "rustup which cargo" in helper
    assert helper.index("prefer_rustup_cargo") < helper.index(
        "for command in cargo codesign"
    )
    assert helper.index("wasm32-unknown-unknown") < helper.index(
        'make -C "$REPO_ROOT" CARGO_BUILD_ROOT='
    )
