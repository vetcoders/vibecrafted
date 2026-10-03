"""Contract for the in-flight release-rehearsal verifier.

C2: delivery verification must be executable inside a cut without operator
publish buttons. `make release-rehearsal` prints OLD/CURRENT identity, dry-runs
the real recipes, runs portable inventory / payload-hygiene when it can, and
fail-closes if a tag/publish/upload/release-build command would be invoked.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
SCRIPT = REPO_ROOT / "scripts/release-rehearsal.sh"
CHECKLIST = REPO_ROOT / "docs/RELEASE_CHECKLIST.md"

FORBIDDEN_SNIPPETS = (
    "gh release",
    "git tag ",
    "notarytool",
    "cargo --release",
    "cargo publish",
    "npm publish",
    "twine upload",
)


def _run(
    arguments: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_makefile_exposes_release_rehearsal_target() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    phony = next(line for line in text.splitlines() if line.startswith(".PHONY:"))

    assert "release-rehearsal:" in text
    assert "release-rehearsal" in phony
    assert "RELEASE_REHEARSAL_SCRIPT := scripts/release-rehearsal.sh" in text
    assert "release-rehearsal ·" in text or "· release-rehearsal" in text
    recipe = text.split("\nrelease-rehearsal:\n", 1)[1].split("\npublish-release:", 1)[
        0
    ]
    assert 'bash "$(RELEASE_REHEARSAL_SCRIPT)"' in recipe
    assert "publish-vibecrafted-release.sh" not in recipe
    assert "build-vibecrafted-release.sh" not in recipe
    assert "cargo --release" not in recipe


def test_checklist_points_at_the_in_flight_target() -> None:
    text = CHECKLIST.read_text(encoding="utf-8")
    assert "make release-rehearsal" in text
    assert "no publish button" in text.lower() or "without tagging" in text


def test_make_n_release_rehearsal_does_not_publish() -> None:
    result = _run(["make", "--no-print-directory", "-n", "release-rehearsal"])
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined
    assert "release-rehearsal.sh" in combined
    assert "publish-vibecrafted-release.sh" not in combined
    assert "build-vibecrafted-release.sh" not in combined
    for snippet in FORBIDDEN_SNIPPETS:
        assert snippet not in combined, snippet


def test_script_fail_closes_forbidden_commands() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "command_is_forbidden" in text
    assert "refuse: would invoke a publish/tag/upload/release-build command" in text
    assert "make --no-print-directory -n" in text
    # Live builders are syntax-checked (`bash -n`), never executed.
    assert "bash -n" in text
    assert "cargo --release" in text  # named only as something to refuse


def test_rehearsal_prints_old_current_and_passes_without_artifact(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["VIBECRAFTED_RELEASE_DIR"] = str(tmp_path / "dist")
    (tmp_path / "dist").mkdir()
    version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()

    result = _run(["bash", str(SCRIPT)], env=env)
    combined = result.stdout + result.stderr

    assert result.returncode == 0, combined
    assert f"CURRENT:   {version}" in result.stdout
    assert (
        result.stdout.startswith("\n==> identity (OLD / CURRENT)")
        or "OLD:" in result.stdout
    )
    assert "OLD:" in result.stdout
    assert "artifact:  none" in result.stdout
    assert "portable inventory:" in result.stdout
    assert "release-rehearsal: pass (no publish)" in result.stdout
    assert "gh release" not in combined
    assert "pass (no publish)" in result.stdout


def test_rehearsal_does_not_invoke_publish_binaries(tmp_path: Path) -> None:
    """A trap PATH must never see git tag / gh / cargo / notarytool from rehearsal."""

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    trap = """#!/bin/sh
printf 'FORBIDDEN %s\\n' "$0 $*" >&2
exit 99
"""
    for name in ("gh", "cargo", "notarytool", "stapler", "npm", "twine"):
        _write_executable(fake_bin / name, trap)
    _write_executable(
        fake_bin / "git",
        f"""#!/bin/sh
case " $* " in
  *" tag "*|*" push "*)
    printf 'FORBIDDEN git %s\\n' "$*" >&2
    exit 99
    ;;
esac
exec {real_git} "$@"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["VIBECRAFTED_RELEASE_DIR"] = str(tmp_path / "dist")
    (tmp_path / "dist").mkdir()

    result = _run(["bash", str(SCRIPT)], env=env)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "FORBIDDEN" not in combined
    assert "release-rehearsal: pass (no publish)" in result.stdout


def test_rehearsal_runs_payload_hygiene_when_artifact_given(tmp_path: Path) -> None:
    payload = tmp_path / "clean.app"
    payload.mkdir()
    (payload / "Info.plist").write_text("bundle-id only", encoding="utf-8")
    env = os.environ.copy()
    env["VIBECRAFTED_RELEASE_DIR"] = str(tmp_path / "empty-dist")
    (tmp_path / "empty-dist").mkdir()

    result = _run(["bash", str(SCRIPT), str(payload)], env=env)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "does not name this build host" in combined
    assert "release-rehearsal: pass (no publish)" in result.stdout


def test_rehearsal_fail_closes_when_artifact_names_the_host(tmp_path: Path) -> None:
    payload = tmp_path / "leaky.app"
    payload.mkdir()
    (payload / "leaky.txt").write_text(str(Path.home()), encoding="utf-8")
    env = os.environ.copy()
    env["VIBECRAFTED_RELEASE_DIR"] = str(tmp_path / "empty-dist")
    (tmp_path / "empty-dist").mkdir()

    result = _run(["bash", str(SCRIPT), str(payload)], env=env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "pass (no publish)" not in result.stdout
