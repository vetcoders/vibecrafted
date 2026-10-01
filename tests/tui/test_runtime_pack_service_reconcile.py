"""Runtime Pack wrapper: a successful install reconciles an installed LaunchAgent.

F6 regression coverage: `install-runtime-pack.sh` used to publish a new
generation while the installed LaunchAgent still pointed at the previous one,
stranding the supervisor in "launcher hash differs" backoff until reboot. The
wrapper now ends a successful install with `vibecrafted server service
reconcile` whenever the service is opted in (plist present).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "scripts/install-runtime-pack.sh"
PLIST_RELATIVE = "Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist"


def _run_sourced(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "case.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # LaunchAgent cases select their platform explicitly on every host.
        # The non-Darwin case below overrides this function inside its body.
        "uname() { printf 'Darwin\\n'; }\n"
        f'source "{WRAPPER}"\n'
        f'export HOME="{home}"\n'
        f"{body}\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    env = {**os.environ, "HOME": str(home)}
    env.pop("VIBECRAFTED_LAUNCHER_BIN", None)
    return subprocess.run(
        ["bash", str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _write_launcher_stub(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    bin_dir = tmp_path / "launcher-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    calls = tmp_path / "launcher-calls.log"
    stub = bin_dir / "vibecrafted"
    stub.write_text(
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{calls}"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    stub.chmod(stat.S_IRWXU)
    return bin_dir, calls


def _write_plist(tmp_path: Path) -> None:
    plist = tmp_path / "home" / PLIST_RELATIVE
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text("<?xml version='1.0'?><plist version='1.0'></plist>\n")


def test_install_reconciles_installed_service(tmp_path: Path) -> None:
    bin_dir, calls = _write_launcher_stub(tmp_path)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents"\n'
        f'touch "$HOME/{PLIST_RELATIVE}"\n'
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "reconciling installed LaunchAgent" in result.stdout
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "server service reconcile"
    ]


def test_reconcile_queues_behind_concurrent_install_with_bounded_wait(
    tmp_path: Path,
) -> None:
    """d5-installer-self-lock: the post-install reconcile must hand the
    supervisor a bounded lease wait so a transient concurrent install
    finishes instead of failing the whole install."""
    bin_dir = tmp_path / "launcher-bin"
    bin_dir.mkdir(parents=True)
    capture = tmp_path / "wait-capture.log"
    stub = bin_dir / "vibecrafted"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "${{VIBECRAFTED_SERVICE_MUTATION_LOCK_TIMEOUT:-unset}}" > "{capture}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(stat.S_IRWXU)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents"\n'
        f'touch "$HOME/{PLIST_RELATIVE}"\n'
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert capture.read_text(encoding="utf-8") == "120\n"

    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents"\n'
        f'touch "$HOME/{PLIST_RELATIVE}"\n'
        "export VIBECRAFTED_SERVICE_MUTATION_LOCK_TIMEOUT=7\n"
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert capture.read_text(encoding="utf-8") == "7\n"


def test_install_without_launchagent_is_a_noop(tmp_path: Path) -> None:
    bin_dir, calls = _write_launcher_stub(tmp_path)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\nreconcile_server_service\n',
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not calls.exists()
    assert "reconciling" not in result.stdout


def test_dry_run_never_touches_the_service(tmp_path: Path) -> None:
    bin_dir, calls = _write_launcher_stub(tmp_path)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents" && touch "$HOME/{PLIST_RELATIVE}"\n'
        "dry_run=1\n"
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not calls.exists()


def test_non_darwin_skips_reconcile(tmp_path: Path) -> None:
    bin_dir, calls = _write_launcher_stub(tmp_path)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents" && touch "$HOME/{PLIST_RELATIVE}"\n'
        "uname() { printf 'Linux\\n'; }\n"
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert not calls.exists()


def test_reconcile_failure_is_loud_and_fails_the_wrapper_step(tmp_path: Path) -> None:
    bin_dir, _calls = _write_launcher_stub(tmp_path, exit_code=75)
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{bin_dir}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents" && touch "$HOME/{PLIST_RELATIVE}"\n'
        "if reconcile_server_service; then exit 0; else exit $?; fi\n",
    )
    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "did not reconcile" in result.stderr
    assert "server service reconcile" in result.stderr


def test_missing_launcher_warns_but_does_not_fail(tmp_path: Path) -> None:
    result = _run_sourced(
        tmp_path,
        f'export VIBECRAFTED_LAUNCHER_BIN="{tmp_path / "empty-bin"}"\n'
        f'mkdir -p "$HOME/Library/LaunchAgents" && touch "$HOME/{PLIST_RELATIVE}"\n'
        "PATH=/usr/bin:/bin\n"
        "reconcile_server_service\n",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "launcher is unavailable" in result.stderr


def test_wrapper_body_reconciles_after_successful_install() -> None:
    """Both installer exit paths must gate reconcile on installer success."""
    body = WRAPPER.read_text(encoding="utf-8")
    guard = 'if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then'
    installer_body = body.split(guard, 1)[1]
    assert installer_body.count("reconcile_server_service || exit $?") == 2
    assert 'if [[ "$installer_status" -eq 0 && "$fail_after" == "published" ]]' in (
        installer_body
    )
