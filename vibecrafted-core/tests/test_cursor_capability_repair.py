"""Cursor capability repair — probe selected binary flags; never silent-downgrade.

Pinned against the Founder incident (2026-09-08): old cursor-agent 2025.09.17
lacks ``--trust``/``--yolo`` while the launcher hard-required them; ``--resume``
takes chatId not a brief path; ``--best-of-n`` must never be invented.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from vibecrafted_core import spawn
from vibecrafted_core.continuity import capabilities as continuity

OLD_HELP = textwrap.dedent(
    """\
    Usage: cursor-agent [options] [command] [prompt...]
      -v, --version
      -p, --print
      --output-format <format>
      --resume [chatId]
      --model <model>
      -f, --force
      -h, --help
    """
)

MODERN_HELP = textwrap.dedent(
    """\
    Usage: agent [options] [command] [prompt...]
      -v, --version
      -p, --print
      --output-format <format>
      --stream-partial-output
      --mode <mode>
      --resume [chatId]
      --continue
      --model <model>
      -f, --force
      --yolo
      --sandbox <mode>
      --trust
      --workspace <path-or-name>
      -h, --help
    """
)


@pytest.fixture(autouse=True)
def _clean_cursor_caches() -> None:
    continuity.clear_probe_cache()
    yield
    continuity.clear_probe_cache()


def _write_fake_cursor(
    directory: Path, *, version: str, help_text: str, exit_on_run: int | None = None
) -> Path:
    """Fake cursor-agent: --version/--help only; optional nonzero on real run.

    Builtins only — tests pin PATH to the fake dir, so ``cat``/``sed`` would miss.
    """
    help_lines = help_text.splitlines() or [""]
    # Emit help via successive printf args (same pattern as test_capability_probe).
    printf_args = " ".join(f"'{line}'" for line in help_lines)
    run_branch = "  *) printf '%s\\n' 'unexpected' >&2; exit 2 ;;\n"
    if exit_on_run is not None:
        run_branch = (
            "  *)\n"
            "    printf '%s\\n' 'error: unknown option' >&2\n"
            f"    exit {int(exit_on_run)}\n"
            "    ;;\n"
        )
    script = directory / "cursor-agent"
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  --version) printf '%s\\n' '{version}' ;;\n"
        f"  --help|-h) printf '%s\\n' {printf_args} ;;\n"
        f"{run_branch}"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _pin_path(monkeypatch: pytest.MonkeyPatch, directory: Path) -> None:
    # Keep system builtins reachable; only put the fake CLI first.
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setattr(
        continuity,
        "agent_tool_search_path",
        lambda _env=None: str(directory),
    )
    monkeypatch.setattr(
        spawn,
        "agent_tool_search_path",
        lambda _env=None: str(directory),
    )


def test_modern_cli_exposes_force_trust_yolo_not_best_of_n(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cursor(tmp_path, version="2026.08.25-3e8eec8", help_text=MODERN_HELP)
    _pin_path(monkeypatch, tmp_path)

    surface = continuity.probe_cursor_cli_surface(refresh=True)
    assert surface.state == continuity.PROBE_CONFIRMED
    assert surface.supports("--force")
    assert surface.supports("--trust")
    assert surface.supports("--yolo")
    assert surface.supports("--resume")
    assert not surface.supports("--best-of-n")

    flags = continuity.require_cursor_flags(
        ("--force", "--trust"), surface, permissions="bypass"
    )
    assert flags == ("--force", "--trust")

    stdin = spawn._stdin_command("cursor")
    assert stdin[:4] == ["cursor-agent", "-p", "--output-format", "stream-json"]
    assert "--force" in stdin and "--trust" in stdin


def test_old_cli_missing_trust_fails_closed_no_silent_downgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cursor(tmp_path, version="2025.09.17-25b418f", help_text=OLD_HELP)
    _pin_path(monkeypatch, tmp_path)

    surface = continuity.probe_cursor_cli_surface(refresh=True)
    assert surface.supports("--force")
    assert not surface.supports("--trust")
    assert not surface.supports("--yolo")

    with pytest.raises(ValueError, match="no silent downgrade"):
        continuity.require_cursor_flags(
            ("--force", "--trust"), surface, permissions="bypass"
        )

    with pytest.raises(ValueError, match="lacks required flag"):
        spawn._stdin_command("cursor")

    with pytest.raises(ValueError, match="lacks required flag"):
        spawn._default_command("cursor", "hello")

    # Never approximate by dropping --trust.
    with pytest.raises(ValueError, match="--trust"):
        spawn.interactive_policy_command(
            "cursor", "hi", "local-native", "bypass"
        )


def test_invalid_resume_brief_path_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cursor(tmp_path, version="2026.09.08-58eaf97", help_text=MODERN_HELP)
    _pin_path(monkeypatch, tmp_path)

    brief = tmp_path / "briefs" / "B_settings.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# brief\n", encoding="utf-8")

    with pytest.raises(ValueError, match="chatId"):
        continuity.validate_cursor_resume_chat_id(str(brief))

    with pytest.raises(ValueError, match="chatId"):
        continuity.validate_cursor_resume_chat_id(
            "~/.vibecrafted/artifacts/vetcoders/codescribe/brief.md"
        )

    with pytest.raises(ValueError, match="chatId"):
        spawn.interactive_policy_command(
            "cursor",
            "continue",
            "local-native",
            "bypass",
            provider_session_id=str(brief),
        )

    ok = continuity.validate_cursor_resume_chat_id("abc-chat-123")
    assert ok == "abc-chat-123"
    argv = spawn.interactive_policy_command(
        "cursor",
        "continue",
        "local-native",
        "bypass",
        provider_session_id="abc-chat-123",
    )
    assert argv[argv.index("--resume") + 1] == "abc-chat-123"


def test_best_of_n_never_invented(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_fake_cursor(tmp_path, version="2026.08.25-3e8eec8", help_text=MODERN_HELP)
    _pin_path(monkeypatch, tmp_path)
    surface = continuity.probe_cursor_cli_surface(refresh=True)

    with pytest.raises(ValueError, match="best-of-n"):
        continuity.require_cursor_flags(("--best-of-n", "3"), surface)

    with pytest.raises(ValueError, match="best-of-n"):
        continuity.reject_unsupported_cursor_argv(
            ["cursor-agent", "--best-of-n", "3", "hi"]
        )

    assert "--best-of-n" in continuity.capability_for("cursor").forbidden_flags


def test_failure_exit_and_receipt_paths_preserved_in_spawn_wrapper() -> None:
    """Public shell launch path keeps prompt-file stdin + nonzero salvage."""
    repo = Path(__file__).resolve().parents[2]
    wrapper = (
        repo
        / "vibecrafted-core/vibecrafted_core/runtime/scripts/cursor_spawn.sh"
    )
    body = wrapper.read_text(encoding="utf-8")
    assert "cursor-agent --help" in body
    assert "no silent downgrade" in body
    assert "lacks required flag" in body
    # Capability probe must precede spawn_write_meta (no launching ghost).
    assert body.index("cursor-agent --help") < body.index("spawn_write_meta")
    assert "< $qruntime" in body
    assert "pipeline_status" in body
    assert "status: failed" in body
    assert "$qtranscript" in body
    # Launch argv uses probed permission flags; must not invent --best-of-n.
    assert "cursor-agent -p --output-format stream-json $cursor_perm_flags" in body
    assert "--best-of-n" not in body.split("launch_cmd=")[1].split("\n")[0]


def test_fake_old_cli_nonzero_exit_is_observable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a binary rejects unknown flags at runtime, exit code stays nonzero."""
    fake = _write_fake_cursor(
        tmp_path,
        version="2025.09.17-25b418f",
        help_text=OLD_HELP,
        exit_on_run=1,
    )
    # Simulate the pre-repair failure mode: caller passes --trust anyway.
    completed = subprocess.run(
        [str(fake), "-p", "--force", "--trust", "hi"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": str(tmp_path)},
    )
    # Our fake exits 2 on unexpected argv shape; real old CLI exits 1 on
    # unknown option. Either way the important contract is nonzero + stderr.
    assert completed.returncode != 0
    assert completed.stderr.strip()

    # Repair path must refuse before that spawn when probing help.
    _pin_path(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="--trust"):
        spawn._stdin_command("cursor")


def test_injected_help_surface_skips_subprocess() -> None:
    surface = continuity.probe_cursor_cli_surface(
        help_text=MODERN_HELP,
        version="injected",
        executable="/tmp/fake-cursor-agent",
    )
    assert surface.version == "injected"
    assert surface.supports("--trust")
    assert not surface.supports("--best-of-n")
