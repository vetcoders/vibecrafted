"""G1 falsifiers: failed portable leftover, dual-stream FATAL, notary profile.

These execute the portable cleanup trap and the notary profile parser as
shell, not as comment greps. They fail on the baseline that left a refused
tarball in dist/ and refused headless notarization when only a Keychain
profile name was missing from the environment.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PORTABLE = REPO_ROOT / "scripts" / "build-portable-release.sh"
RELEASE = REPO_ROOT / "scripts" / "build-vibecrafted-release.sh"
PACKAGER = REPO_ROOT / "scripts" / "package-runtime-pack.sh"


def _run(
    script: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("PYTHONPATH", None)
    merged.pop("PYTHONHOME", None)
    for key in (
        "NOTARY_PROFILE",
        "NOTARY_ENV",
        "NOTARY_API_KEY_PATH",
        "NOTARY_API_KEY_ID",
        "NOTARY_API_ISSUER",
        "NOTARY_FALLBACK_PROFILE",
        "NOTARY_APPLE_ID",
        "NOTARY_PASSWORD",
    ):
        merged.pop(key, None)
    if env:
        merged.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=merged,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def _cleanup_script(builder: Path) -> str:
    text = builder.read_text(encoding="utf-8")
    if builder == PORTABLE:
        return text[
            text.index("cleanup_failed_portable() {") : text.index("# The packer")
        ]
    start = text.index("\n", text.index('work="$(mktemp -d ')) + 1
    return text[start : text.index('root="$work/VibecraftedRuntime"')]


def _failing_owned_rm() -> str:
    return r"""
rm() {
  if [[ "$1" == -rf ]]; then
    [[ "${!#}" == "$OWNED_DIR" ]] || exit 97
    local count=0
    if [[ -f "$COUNT_FILE" ]]; then read -r count < "$COUNT_FILE"; fi
    count=$((count + 1))
    printf '%s\n' "$count" > "$COUNT_FILE"
    if ((count <= RM_FAILURES)); then return 1; fi
  fi
  command rm "$@"
}
sleep() { :; }
"""


@pytest.mark.parametrize("builder", [PORTABLE, PACKAGER], ids=["portable", "packager"])
@pytest.mark.parametrize("rm_failures", [2, 99], ids=["transient", "permanent"])
@pytest.mark.parametrize("status", [0, 17], ids=["ready", "failed"])
def test_owned_cleanup_retries_and_preserves_build_status(
    tmp_path: Path, builder: Path, rm_failures: int, status: int
) -> None:
    owned = tmp_path / "private-staging"
    owned.mkdir()
    (owned / ".DS_Store").write_bytes(b"host metadata")
    sibling = tmp_path / "donor-cache"
    sibling.write_bytes(b"foreign")
    outputs = [
        tmp_path / name for name in ("candidate.tar.gz", "checksum", "output.json")
    ]
    for output in outputs:
        output.write_bytes(b"candidate")
    count = tmp_path / "rm-count"
    script = (
        "set -euo pipefail\n"
        + _failing_owned_rm()
        + _cleanup_script(builder)
        + f"exit {status}\n"
    )
    result = _run(
        script,
        {
            "OWNED_DIR": str(owned),
            "WORK_DIR": str(owned),
            "work": str(owned),
            "COUNT_FILE": str(count),
            "RM_FAILURES": str(rm_failures),
            "PORTABLE": str(outputs[0]),
            "PORTABLE_CHECKSUM": str(outputs[1]),
            "PORTABLE_OUTPUT": str(outputs[2]),
            "PORTABLE_READY": "1" if status == 0 else "0",
        },
    )
    assert result.returncode == status, result.stderr
    assert count.read_text().strip() == "3"
    assert owned.exists() == (rm_failures == 99)
    assert sibling.read_bytes() == b"foreign"
    if rm_failures == 99:
        assert "warning: could not remove temporary directory" in result.stderr
        assert str(owned) in result.stderr
    else:
        assert "warning" not in result.stderr
    for output in outputs:
        assert output.exists() == (builder == PACKAGER or status == 0)


@pytest.mark.parametrize("signal,status", [("INT", 130), ("TERM", 143), ("HUP", 129)])
def test_packager_signal_preserves_failure_and_cleans_once(
    tmp_path: Path, signal: str, status: int
) -> None:
    owned = tmp_path / "private-staging"
    owned.mkdir()
    count = tmp_path / "rm-count"
    result = _run(
        "set -euo pipefail\n"
        + _failing_owned_rm()
        + _cleanup_script(PACKAGER)
        + f"kill -s {signal} $$\nprintf 'unexpected-continued\\n'\n",
        {
            "OWNED_DIR": str(owned),
            "work": str(owned),
            "COUNT_FILE": str(count),
            "RM_FAILURES": "2",
        },
    )
    assert result.returncode == status, result.stderr
    assert count.read_text().strip() == "3"
    assert not owned.exists()
    assert "unexpected-continued" not in result.stdout


def test_portable_output_cleanup_failure_preserves_build_failure(
    tmp_path: Path,
) -> None:
    output = tmp_path / "refused.tar.gz"
    output.write_bytes(b"refused")
    result = _run(
        "set -euo pipefail\n"
        "rm() { return 1; }\n" + _cleanup_script(PORTABLE) + "exit 23\n",
        {
            "WORK_DIR": "",
            "PORTABLE_READY": "0",
            "PORTABLE": str(output),
            "PORTABLE_CHECKSUM": str(output) + ".sha256",
            "PORTABLE_OUTPUT": str(tmp_path / "output.json"),
        },
    )
    assert result.returncode == 23
    assert "warning: could not remove refused outputs" in result.stderr
    assert output.read_bytes() == b"refused"


def _materialize_script() -> str:
    text = RELEASE.read_text(encoding="utf-8")
    start = text.index("materialize_runtime_payload() {")
    end = text.index("\n}\n", start) + 3
    return text[start:end]


@pytest.mark.parametrize("rm_failures", [2, 99], ids=["transient", "permanent"])
def test_materialize_cleanup_retries_and_refuses_exhaustion(
    tmp_path: Path, rm_failures: int
) -> None:
    build = tmp_path / "unified-release"
    owned = build / "runtime-pack-payload" / "VibecraftedRuntime"
    owned.mkdir(parents=True)
    (owned / ".DS_Store").write_bytes(b"host metadata")
    sibling = build / "cargo"
    sibling.mkdir()
    sentinel = sibling / "keep"
    sentinel.write_bytes(b"foreign")
    count = tmp_path / "rm-count"
    script = (
        "set -euo pipefail\n"
        "log() { :; }\n"
        "die() { printf 'FATAL: %s\\n' \"$*\" >&2; exit 1; }\n"
        "mkdir() { printf 'materialization-started\\n'; exit 0; }\n"
        + _failing_owned_rm()
        + _materialize_script()
        + 'materialize_runtime_payload "$OWNED_DIR" a b c d e f g h i j\n'
    )
    result = _run(
        script,
        {
            "BUILD_DIR": str(build),
            "OWNED_DIR": str(owned),
            "COUNT_FILE": str(count),
            "RM_FAILURES": str(rm_failures),
        },
    )
    assert count.read_text().strip() == "3"
    assert sentinel.read_bytes() == b"foreign"
    if rm_failures == 99:
        assert result.returncode == 1
        assert (
            "could not clear owned Runtime Pack payload after 3 attempts"
            in result.stderr
        )
        assert "materialization-started" not in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert "materialization-started" in result.stdout
        assert not owned.exists()


@pytest.mark.parametrize("target", ["", "cargo"])
def test_materialize_cleanup_refuses_unowned_path(tmp_path: Path, target: str) -> None:
    build = tmp_path / "unified-release"
    sibling = build / "cargo"
    sibling.mkdir(parents=True)
    sentinel = sibling / "keep"
    sentinel.write_bytes(b"foreign")
    result = _run(
        "set -euo pipefail\n"
        "log() { :; }\n"
        "die() { printf 'FATAL: %s\\n' \"$*\" >&2; exit 1; }\n"
        "rm() { printf 'unexpected-remove\\n'; exit 97; }\n"
        + _materialize_script()
        + 'materialize_runtime_payload "$TARGET" a b c d e f g h i j\n',
        {"BUILD_DIR": str(build), "TARGET": str(sibling) if target else ""},
    )
    assert result.returncode == 1
    assert "refusing to clear unowned Runtime Pack payload" in result.stderr
    assert "unexpected-remove" not in result.stdout
    assert sentinel.read_bytes() == b"foreign"


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
{_cleanup_script(PORTABLE)}
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
{_cleanup_script(PORTABLE)}
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


def _notary_submit_script(helper: str, body: str) -> str:
    log_die = helper[helper.index("log() {") : helper.index("require() {")]
    profile = helper[
        helper.index("notary_profile_from_env_file() {") : helper.index(
            "notary_submit() {"
        )
    ]
    submit = helper[
        helper.index("notary_submit() {") : helper.index("strip_debug_stabs() {")
    ]
    # notary_profile_from_keychain admits a conventional profile only when
    # `notarytool history` authenticates it (6b5b108f). The stub models the
    # Keychain: a profile authenticates only if the test stored it in
    # FAKE_KEYCHAIN_PROFILES. Every other xcrun call is recorded on stdout.
    xcrun_stub = (
        "xcrun() {\n"
        '  if [[ "${1:-}" == notarytool && "${2:-}" == history ]]; then\n'
        '    [[ " ${FAKE_KEYCHAIN_PROFILES:-} " == *" ${4:-} "* ]]\n'
        "    return\n"
        "  fi\n"
        "  printf 'XCRUN:%s\\n' \"$*\"\n"
        "}\n"
    )
    return "set -euo pipefail\n" + log_die + profile + submit + xcrun_stub + body


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
    # store-credentials may name a new profile; submit must not invent one.
    assert 'fallback_profile="${NOTARY_FALLBACK_PROFILE:-vibecrafted-notary}"' in notary
    invented = notary.split('if [[ -n "$profile" ]]; then', 1)[0]
    assert "vibecrafted-notary" not in invented


def test_fail_g1_notary_explicit_profile_beats_api_keys(tmp_path: Path) -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    key = tmp_path / "AuthKey_TEST.p8"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    script = _notary_submit_script(
        helper,
        "NOTARY_ENV=/nonexistent-notary.env\n"
        "NOTARY_PROFILE=founder-profile\n"
        f"NOTARY_API_KEY_PATH={key.as_posix()}\n"
        "NOTARY_API_KEY_ID=KEYID\n"
        "NOTARY_API_ISSUER=ISSUER\n"
        "notary_submit artifact.zip\n",
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "--keychain-profile founder-profile" in result.stdout
    assert "--key " not in result.stdout
    assert "not-a-real-key" not in result.stdout
    assert "KEYID" not in result.stdout


def test_fail_g1_notary_api_keys_used_when_profile_unset(tmp_path: Path) -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    key = tmp_path / "AuthKey_TEST.p8"
    key.write_text("not-a-real-key\n", encoding="utf-8")
    script = _notary_submit_script(
        helper,
        "NOTARY_ENV=/nonexistent-notary.env\n"
        "unset NOTARY_PROFILE\n"
        f"NOTARY_API_KEY_PATH={key.as_posix()}\n"
        "NOTARY_API_KEY_ID=KEYID\n"
        "NOTARY_API_ISSUER=ISSUER\n"
        "notary_submit artifact.zip\n",
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert f"--key {key.as_posix()}" in result.stdout
    assert "--keychain-profile" not in result.stdout
    assert "vibecrafted-notary" not in result.stdout
    assert "not-a-real-key" not in result.stdout


def test_fail_g1_headless_notary_does_not_invent_a_profile() -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    script = _notary_submit_script(
        helper,
        "NOTARY_ENV=/nonexistent-notary.env\n"
        "unset NOTARY_PROFILE NOTARY_API_KEY_PATH NOTARY_API_KEY_ID NOTARY_API_ISSUER\n"
        "notary_submit artifact.zip\n",
    )
    result = _run(script)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "NOTARY_PROFILE is unset" in combined
    assert "XCRUN:" not in combined
    assert "vibecrafted-notary" not in combined


def test_fail_g1_notary_reuses_a_keychain_profile_only_after_apple_authenticates_it() -> (
    None
):
    helper = RELEASE.read_text(encoding="utf-8")
    script = _notary_submit_script(
        helper,
        "NOTARY_ENV=/nonexistent-notary.env\n"
        "unset NOTARY_PROFILE NOTARY_API_KEY_PATH NOTARY_API_KEY_ID NOTARY_API_ISSUER\n"
        "FAKE_KEYCHAIN_PROFILES=vibecrafted-notary\n"
        "notary_submit artifact.zip\n"
        'printf "EXPORTED:%s\\n" "$NOTARY_PROFILE"\n',
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert (
        "XCRUN:notarytool submit artifact.zip --keychain-profile vibecrafted-notary"
        in result.stdout
    )
    assert "EXPORTED:vibecrafted-notary" in result.stdout
    assert "--key " not in result.stdout


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
        helper.index("Embedding a private Python runtime") : helper.index(
            "uv pip install --python"
        )
    ]
    assert "install_portable_python" in loop
    assert "portable-python.sh" in loop
    assert "uv python install 3.12.3" not in helper
    pin = (REPO_ROOT / "scripts/lib/portable-python-artifact.json").read_text(
        encoding="utf-8"
    )
    assert '"cpython": "3.14.7"' in pin
    assert "30daa970c7d223530120f1693cd3c6fa4c0c0d31ef158710b0dd77f286a5b23e" in pin


def test_fail_g1_release_prefers_rustup_cargo_before_cargo_runs() -> None:
    helper = RELEASE.read_text(encoding="utf-8")
    assert "prefer_rustup_cargo" in helper
    assert "rustup which cargo" in helper
    assert helper.index("prefer_rustup_cargo") < helper.index(
        "for command in cargo codesign"
    )
    # 6b5b108f moved the target list into the one toolchain contract; the
    # release script sources it and preflights every listed target before the
    # first cargo-driven make.
    contract = (REPO_ROOT / "scripts/lib/release-toolchain-contract.sh").read_text(
        encoding="utf-8"
    )
    targets = re.search(
        r"^readonly VIBECRAFTED_RELEASE_RUST_TARGETS='([^']*)'$", contract, re.MULTILINE
    )
    assert targets is not None
    assert "wasm32-unknown-unknown" in targets.group(1).split()
    target_preflight = helper.index(
        "for release_target in $VIBECRAFTED_RELEASE_RUST_TARGETS; do"
    )
    assert helper.index('. "$RELEASE_TOOLCHAIN_CONTRACT"') < target_preflight
    assert target_preflight < helper.index('make -C "$SOURCE_ROOT" CARGO_BUILD_ROOT=')
