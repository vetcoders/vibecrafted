from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ULIMIT_HELPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "lib"
    / "ulimits.sh"
)


def test_wheel_deck_uses_its_own_packaged_ulimits(tmp_path: Path) -> None:
    package = tmp_path / "site-packages" / "vibecrafted_core"
    deck = package / "deck" / "vibecrafted"
    helper = package / "runtime" / "scripts" / "lib" / "ulimits.sh"
    deck.parent.mkdir(parents=True)
    helper.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts" / "vibecrafted", deck)
    shutil.copy2(ULIMIT_HELPER, helper)

    result = subprocess.run(
        ["bash", str(deck), "init", "--help"],
        check=False,
        text=True,
        capture_output=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "selected launcher limit helper missing" not in result.stderr
    assert "vibecrafted init" in result.stdout


def test_launcher_ulimit_helper_raises_fsize_and_best_effort_nofile() -> None:
    script = f"""
set -euo pipefail
hard_nofile="$(ulimit -Hn 2>/dev/null || true)"
hard_fsize="$(ulimit -Hf 2>/dev/null || true)"
ulimit -S -n 256 2>/dev/null || true
ulimit -S -f 1024 2>/dev/null || true
source "{ULIMIT_HELPER}"
vc_raise_launcher_limits
printf 'hard_nofile=%s\\n' "$hard_nofile"
printf 'nofile=%s\\n' "$(ulimit -n)"
printf 'hard_fsize=%s\\n' "$hard_fsize"
printf 'fsize=%s\\n' "$(ulimit -f)"
"""
    result = subprocess.run(
        ["bash", "-lc", script],
        check=True,
        text=True,
        capture_output=True,
    )
    values = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())

    hard_nofile = values["hard_nofile"]
    nofile = values["nofile"]
    if hard_nofile.isdigit() and int(hard_nofile) > 256 and nofile.isdigit():
        assert int(nofile) > 256

    if values["hard_fsize"] == "unlimited":
        assert values["fsize"] == "unlimited"


def test_launcher_ulimit_helper_warns_without_aborting_on_bad_override() -> None:
    script = f"""
set -euo pipefail
source "{ULIMIT_HELPER}"
VC_ULIMIT_NOFILE=definitely-not-a-limit VC_ULIMIT_FSIZE=also-bad vc_raise_launcher_limits
printf 'still-running\\n'
"""
    result = subprocess.run(
        ["bash", "-lc", script],
        check=True,
        text=True,
        capture_output=True,
    )

    assert "still-running" in result.stdout
    assert "could not raise open-file limit" in result.stderr
    assert "could not raise file-size limit" in result.stderr


def test_named_launchers_source_shared_ulimit_helper() -> None:
    common = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "common.sh"
    ).read_text()
    launcher = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "launcher.sh"
    ).read_text()
    shell_facade = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    ).read_text()
    command_deck = (REPO_ROOT / "scripts" / "vibecrafted").read_text()
    dashboard = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "lib"
        / "dashboard.sh"
    ).read_text()
    scripts_vc_frame = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "vc_frame.sh"
    ).read_text()
    shell_vc_frame = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "lib"
        / "vc_frame.sh"
    ).read_text()

    assert 'source "$_SPAWN_LIB_DIR/ulimits.sh"' in common
    assert "source $q_ulimits" in launcher
    assert "_vetcoders_source_shell_module ulimits" in shell_facade
    assert "_vc_source_launcher_ulimits" in command_deck
    assert "vc_raise_launcher_limits" in dashboard
    assert scripts_vc_frame.count("vc_raise_launcher_limits") >= 4
    assert shell_vc_frame.count("vc_raise_launcher_limits") >= 2

    assert 'ulimit -n "$(ulimit -Hn' not in command_deck
    assert "ulimit -f unlimited 2>/dev/null || true" not in launcher
