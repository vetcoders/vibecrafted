from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def test_tui_enters_voc_without_foreground_control_plane_sync(tmp_path: Path) -> None:
    deck = (REPO_ROOT / "scripts/vibecrafted").read_text(encoding="utf-8")
    start = deck.index("cmd_tui() {")
    stop = deck.index("\n_help_topic_alias()", start)
    binary = tmp_path / "voc"
    binary.write_text(
        "#!/bin/sh\nprintf 'VOC entered %s\\n' \"$*\"\n", encoding="utf-8"
    )
    binary.chmod(0o755)
    script = "\n".join(
        [
            "set -euo pipefail",
            deck[start:stop],
            "_is_help_flag() { return 1; }",
            "_voc_app_dir() { return 1; }",
            "_runtime_payload_root() { printf '%s\\n' \"$TEST_ROOT\"; }",
            "_resolve_voc_binary() { printf '%s\\n' \"$TEST_ROOT/voc\"; }",
            "_repo_source_root() { printf '%s\\n' \"$TEST_ROOT\"; }",
            "_sync_control_plane_best_effort() { echo 'foreground sync invoked'; return 83; }",
            "crafted_home=$TEST_ROOT/home",
            "crafted_tools=$TEST_ROOT/tools",
            "cmd_tui --repo /selected/project",
        ]
    )
    result = subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "TEST_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VOC entered" in result.stdout
    assert "--repo /selected/project" in result.stdout
    assert "foreground sync" not in result.stdout


def _run_launcher_help(tmp_path: Path, *args: str) -> str:
    home = tmp_path / "home"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")

    result = subprocess.run(
        ["bash", "scripts/vibecrafted", *args],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return ANSI_RE.sub("", result.stdout)


def _run_launcher_update_with_stubs(tmp_path: Path, *args: str) -> tuple[str, str]:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    make_log = tmp_path / "make.log"
    bin_dir.mkdir(parents=True)
    (bin_dir / "curl").write_text("#!/usr/bin/env bash\nexit 22\n", encoding="utf-8")
    (bin_dir / "make").write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" > "$MAKE_LOG"\nexit 0\n',
        encoding="utf-8",
    )
    (bin_dir / "curl").chmod(0o755)
    (bin_dir / "make").chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["MAKE_LOG"] = str(make_log)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "scripts/vibecrafted", "update", *args],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return ANSI_RE.sub("", result.stdout), make_log.read_text(encoding="utf-8")


def test_compact_help_uses_release_engine_contract(tmp_path: Path) -> None:
    output = _run_launcher_help(tmp_path, "help")

    assert "release engine for AI-developed software" in output
    assert "Commands:" in output
    assert "Ship cycle:" in output
    assert (
        "scaffold → implement → review → workflow → followup → marbles → "
        "audit → polarize → dou → hydrate → release"
    ) in output
    assert "More workflows: vibecrafted help --all" in output
    assert 'vibecrafted implement codex -p "Ship dark mode"' in output
    assert "justdo" not in output
    assert "compatibility alias" not in output
    assert "leg" + "acy alias" not in output
    assert "Founders' Framework" not in output
    # Bounded deck: operator consoles and plumbing live in help --all only.
    assert "dashboard" not in output
    assert "telemetry" not in output
    assert "resume-session" in output
    assert "relocate" in output
    assert "Codex queue receipts" in output
    assert "claims" in output
    assert "settlements" in output


def test_full_help_examples_keep_decorate_between_dou_and_hydrate(
    tmp_path: Path,
) -> None:
    output = _run_launcher_help(tmp_path, "help", "--full")

    assert "vibecrafted dou claude" in output
    assert "vibecrafted decorate codex" in output
    assert "vibecrafted hydrate codex" in output
    assert "justdo = Just Do posture (not implement)" in output
    assert "Single-agent research" not in output
    assert "Launch Claude + codex + junie swarm" not in output
    assert "Triple-agent research (claude + codex + junie)" not in output
    assert "Alias for vibecrafted dashboard" not in output
    assert "Enter the repository workspace" in output
    assert "uno|duo|trio" in output
    assert "resume-session" in output
    assert "Send to tracked run inbox" in output
    assert "Codex queue only" not in output
    assert "relocate" in output
    assert "claims" in output


def test_implement_help_is_ship_write_and_distinct_from_justdo(tmp_path: Path) -> None:
    output = _run_launcher_help(tmp_path, "implement", "--help")

    assert (
        "VC-ship WRITE stage: structured end-to-end implementation with followup "
        "and marbles built in." in output
    )
    assert (
        "vibecrafted implement <claude|codex|agy|junie|grok|cursor|kimi|copilot|omp> [flags]"
        in output
    )
    assert (
        "vc-implement <claude|codex|agy|junie|grok|cursor|kimi|copilot|omp> [flags]"
        in output
    )
    assert "Not the same skill as justdo." in output
    assert 'vibecrafted implement codex --prompt "Ship the feature"' in output


def test_shell_and_core_render_the_same_workflow_help(tmp_path: Path) -> None:
    shell_output = _run_launcher_help(tmp_path, "marbles", "--help")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT / "vibecrafted-core")

    core = subprocess.run(
        [sys.executable, "-m", "vibecrafted_core.cli", "marbles", "--help"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert shell_output == ANSI_RE.sub("", core.stdout)


def test_update_ref_pair_reaches_make_branch(tmp_path: Path) -> None:
    output, make_invocation = _run_launcher_update_with_stubs(
        tmp_path, "--ref", "preview"
    )

    assert "Vibecrafted Update" in output
    assert "update" in make_invocation
    assert "BRANCH=preview" in make_invocation


def test_review_and_followup_help_stay_semantically_separate(tmp_path: Path) -> None:
    review = _run_launcher_help(tmp_path, "review", "--help")
    followup = _run_launcher_help(tmp_path, "followup", "--help")
    audit = _run_launcher_help(tmp_path, "audit", "--help")

    assert "version 1.0.0" in audit
    assert "READ-ONLY falsification of a completed plan" in audit
    assert "Bounded PR, branch, commit-range, or artifact-pack review" in review
    assert "version 2.0.0" in review
    assert 'vibecrafted review codex --prompt "Review PR #14"' in review
    assert "Post-implementation direction audit" in followup
    assert "version 2.2.0" in followup
    assert (
        'vibecrafted followup codex --prompt "Audit post-implementation direction"'
        in followup
    )


def test_docs_skill_index_locks_command_semantics() -> None:
    skills = (REPO_ROOT / "docs" / "SKILLS.md").read_text(encoding="utf-8")
    workflows = (REPO_ROOT / "docs" / "WORKFLOWS.md").read_text(encoding="utf-8")

    assert (
        "`vc-implement` / `vibecrafted implement` is the official ship-cycle WRITE"
        in skills
    )
    assert "`vc-justdo`" in skills
    assert "`justdo`    | `justdo`" in skills
    assert "Standalone Just Do posture" in skills
    assert (
        "Findings-first review over a bounded PR, branch, commit range, or artifact pack."
        in skills
    )
    assert "Post-implementation direction audit" in skills
    assert "`vibecrafted justdo` / `vc-justdo` is a **standalone**" in workflows
