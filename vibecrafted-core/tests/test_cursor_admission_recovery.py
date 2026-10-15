"""Admission recovery: nonzero/error --help must never mint confirmed flags.

Root reproduced false capability success: successful --version + failing --help
that mentions ``--force --trust`` in error prose still yielded ``state=confirmed``
and ``require_cursor_flags`` accepted those flags. Shell ``|| true`` had the
same hole. Only a successful bounded --help may establish flags.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from vibecrafted_core.capabilities import ProbeResult as CliProbe
from vibecrafted_core.continuity import capabilities as continuity

MODERN_HELP = textwrap.dedent(
    """\
    Usage: agent [options]
      -f, --force
      --trust
      --yolo
      -h, --help
    """
)

OLD_HELP = textwrap.dedent(
    """\
    Usage: cursor-agent [options]
      -f, --force
      -h, --help
    """
)


@pytest.fixture(autouse=True)
def _clean_cursor_caches() -> None:
    continuity.clear_probe_cache()
    yield
    continuity.clear_probe_cache()


def _write_fake_cursor(
    directory: Path, *, version: str, help_text: str, help_rc: int = 0
) -> Path:
    help_lines = help_text.splitlines() or [""]
    printf_args = " ".join(f"'{line}'" for line in help_lines)
    stream = ">&2 " if help_rc != 0 else ""
    script = directory / "cursor-agent"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf '%s\\n' '{version}' ;;\n"
        f"  --help|-h)\n"
        f"    printf '%s\\n' {printf_args} {stream}\n"
        f"    exit {int(help_rc)}\n"
        "    ;;\n"
        "  *) printf '%s\\n' 'unexpected' >&2; exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_nonzero_help_error_prose_is_not_capability_evidence() -> None:
    """Exact Root reproduction: failing --help that names flags must refuse."""

    class Runner:
        def __call__(self, cmd: list[str]) -> CliProbe:
            if "--version" in cmd:
                return CliProbe(
                    ok=True, returncode=0, stdout="2026.09.08-fake\n", stderr=""
                )
            return CliProbe(
                ok=False,
                returncode=1,
                stdout="",
                stderr="error: --force --trust unavailable",
            )

    surface = continuity.probe_cursor_cli_surface(
        executable="/tmp/fake-cursor-agent-admission",
        runner=Runner(),
        refresh=True,
    )
    assert surface.state == continuity.PROBE_FAILED
    assert not surface.supports("--force")
    assert not surface.supports("--trust")
    with pytest.raises(ValueError, match="cannot verify|refusing|probe failed"):
        continuity.require_cursor_flags(("--force", "--trust"), surface)


def test_empty_and_timeout_help_stay_unverified() -> None:
    class EmptyHelp:
        def __call__(self, cmd: list[str]) -> CliProbe:
            if "--version" in cmd:
                return CliProbe(ok=True, returncode=0, stdout="v\n", stderr="")
            return CliProbe(ok=True, returncode=0, stdout="", stderr="")

    class TimeoutHelp:
        def __call__(self, cmd: list[str]) -> CliProbe:
            if "--version" in cmd:
                return CliProbe(ok=True, returncode=0, stdout="v\n", stderr="")
            return CliProbe(ok=False, returncode=None, stdout="", stderr="timeout")

    empty = continuity.probe_cursor_cli_surface(
        executable="/tmp/fake-empty-help", runner=EmptyHelp(), refresh=True
    )
    assert empty.state == continuity.PROBE_FAILED
    assert not empty.supports("--force")

    timed = continuity.probe_cursor_cli_surface(
        executable="/tmp/fake-timeout-help", runner=TimeoutHelp(), refresh=True
    )
    assert timed.state == continuity.PROBE_FAILED
    assert "timeout" in timed.detail.lower()
    with pytest.raises(ValueError):
        continuity.require_cursor_flags(("--force", "--trust"), timed)


def test_surface_cache_invalidates_when_binary_content_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _write_fake_cursor(
        tmp_path, version="2026.08.25-modern", help_text=MODERN_HELP, help_rc=0
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setattr(
        continuity, "agent_tool_search_path", lambda _env=None: str(tmp_path)
    )

    first = continuity.probe_cursor_cli_surface(refresh=True)
    assert first.state == continuity.PROBE_CONFIRMED
    assert first.supports("--trust")

    # Same path, new content (provider upgrade/downgrade on host).
    _write_fake_cursor(
        tmp_path, version="2025.09.17-old", help_text=OLD_HELP, help_rc=0
    )
    # Touch size/mtime identity without refresh=True — cache must not stay modern.
    second = continuity.probe_cursor_cli_surface(refresh=False)
    assert second.version == "2025.09.17-old"
    assert second.supports("--force")
    assert not second.supports("--trust")
    assert fake.exists()


def _resolve_permissions_cli(
    tmp_path: Path, *, permissions: str
) -> subprocess.CompletedProcess[str]:
    """Run the exact resolver cursor_spawn.sh calls, against the fake binary on PATH."""
    core = Path(__file__).resolve().parents[1]
    env = {
        "PATH": f"{tmp_path}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path / "home"),
        "VIBECRAFTED_HOME": str(tmp_path / "vh"),
        "PYTHONPATH": str(core),
    }
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.permission_flags",
            "cursor",
            "--mode",
            "headless",
            "--permissions",
            permissions,
            "--shell-env",
        ],
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
    )


def test_shell_spawn_resolves_flags_through_canonical_probe_before_launch_meta() -> (
    None
):
    """cursor_spawn.sh no longer probes inline: spawn_resolve_permissions owns it.

    The probe moved into ``vibecrafted_core.permission_flags`` (auto-first
    controls). The wrapper must still resolve before any launch side effect and
    must not re-grow an error-text parser.
    """
    repo = Path(__file__).resolve().parents[2]
    body = (
        repo / "vibecrafted-core/vibecrafted_core/runtime/scripts/cursor_spawn.sh"
    ).read_text(encoding="utf-8")
    assert "spawn_resolve_permissions cursor" in body
    assert body.index("spawn_resolve_permissions cursor") < body.index(
        'spawn_write_meta "$SPAWN_META"'
    )
    assert "cursor-agent --help 2>&1 || true" not in body
    assert "cursor-agent --version 2>/dev/null | head -n1 || true" not in body
    assert "$SPAWN_PERMISSION_FLAGS" in body


def test_resolver_confirms_bypass_flags_from_successful_help(tmp_path: Path) -> None:
    _write_fake_cursor(tmp_path, version="2026.08.25", help_text=MODERN_HELP)
    done = _resolve_permissions_cli(tmp_path, permissions="bypass")
    assert done.returncode == 0, done.stderr
    assert "SPAWN_PERMISSION_FLAGS=" in done.stdout
    assert "--force --trust" in done.stdout


def test_resolver_refuses_bypass_when_help_fails_with_flag_prose(
    tmp_path: Path,
) -> None:
    _write_fake_cursor(
        tmp_path,
        version="broken-help",
        help_text="error: --force --trust unavailable",
        help_rc=2,
    )
    done = _resolve_permissions_cli(tmp_path, permissions="bypass")
    assert done.returncode == 2
    assert "SPAWN_PERMISSION_FLAGS" not in done.stdout


def test_fake_cli_nonzero_help_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cursor(
        tmp_path,
        version="broken-help",
        help_text="error: --force --trust unavailable",
        help_rc=1,
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setattr(
        continuity, "agent_tool_search_path", lambda _env=None: str(tmp_path)
    )
    surface = continuity.probe_cursor_cli_surface(refresh=True)
    assert surface.state == continuity.PROBE_FAILED
    assert not surface.supports("--force")
    assert not surface.supports("--trust")
    with pytest.raises(ValueError):
        continuity.require_cursor_flags(("--force", "--trust"), surface)
