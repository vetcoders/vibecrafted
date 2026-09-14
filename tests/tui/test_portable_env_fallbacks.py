from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FOUNDATIONS_SCRIPT = REPO_ROOT / "scripts" / "install-foundations.sh"
BASE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"


def _write_fake_command(bin_dir: Path, name: str, body: str | None = None) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(
        body
        or "#!/usr/bin/env bash\n"
        'case "${1:-}" in --version|--help) exit 0 ;; *) exit 0 ;; esac\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def test_install_foundations_check_falls_back_to_home_without_vibecrafted_root(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    # This test's world has NO loctree anywhere; a host with brew-installed
    # loct in /opt/homebrew/bin must not satisfy the suite probe.
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env.pop("VIBECRAFTED_ROOT", None)
    env.pop("VIBECRAFTED_HOME", None)
    env.pop("VIBECRAFTED_BIN", None)

    result = subprocess.run(
        ["bash", str(FOUNDATIONS_SCRIPT), "--check"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert str(home / ".local" / "share" / "vibecrafted" / "bin") in result.stdout
    assert str(home / ".local" / "bin") in result.stdout
    assert "Would install Loctree foundations from canonical installer" in result.stdout
    assert "curl -fsSL https://loct.io/install.sh | sh" in result.stdout


def test_install_foundations_default_treats_agent_cli_bootstrap_as_best_effort(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    prefix = tmp_path / "prefix"
    home.mkdir()

    for command in (
        "loct",
        "loctree",
        "loctree-mcp",
        "aicx-mcp",
        "vc-frame",
        "node",
    ):
        _write_fake_command(fake_bin, command)
    _write_fake_command(
        fake_bin,
        "npm",
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "install" ]]; then exit 1; fi\n'
        "exit 0\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{BASE_PATH}"
    # Keep the operator's real cockpit config out of the sandbox so the
    # vc-frame gate result is deterministic on every host.
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env.pop("VIBECRAFTED_ROOT", None)
    env.pop("VIBECRAFTED_HOME", None)

    result = subprocess.run(
        ["bash", str(FOUNDATIONS_SCRIPT), "--prefix", str(prefix)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    combined = result.stdout + result.stderr
    # vc-frame is externally released — and today its repo has no release,
    # so the default spine must DEFER a failed cockpit install with a loud
    # warn instead of killing the whole bootstrap (REQUIRE_FOUNDATIONS=1
    # re-arms the hard gate). The agents leg stays best-effort: its failure
    # is the warn line below, never the exit code.
    assert result.returncode == 0, combined
    assert "cockpit" in combined
    assert (
        "agent CLIs incomplete — optional, install later: vibecrafted doctor"
        in combined
    )


def test_install_foundations_explicit_agents_target_fails_when_bootstrap_fails(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    prefix = tmp_path / "prefix"
    home.mkdir()

    _write_fake_command(fake_bin, "node")
    _write_fake_command(
        fake_bin,
        "npm",
        "#!/usr/bin/env bash\n"
        'if [[ "${1:-}" == "install" ]]; then exit 1; fi\n'
        "exit 0\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{BASE_PATH}"
    env.pop("VIBECRAFTED_ROOT", None)
    env.pop("VIBECRAFTED_HOME", None)

    result = subprocess.run(
        ["bash", str(FOUNDATIONS_SCRIPT), "--prefix", str(prefix), "agents"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Agent CLIs:" in result.stdout
