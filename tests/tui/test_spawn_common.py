from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "common.sh"
)
UTIL_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "lib"
    / "util.sh"
)
SHELL_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)
CLAUDE_SPAWN_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "claude_spawn.sh"
)
CODEX_SPAWN_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "codex_spawn.sh"
)
CODEX_STREAM_BRIDGE = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "codex_stream_bridge.py"
)
CODEX_STREAM_FILTER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "codex_stream_filter.jq"
)
CORE_RUNTIME_HELPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "helpers"
    / "vetcoders-runtime-core.sh"
)
CORE_PACKAGE_DIR = REPO_ROOT / "vibecrafted-core"


# Strip ambient env vars that affect spawn-routing decisions before each
# test script runs. Without this, tests that source common.sh inherit the
# parent shell's VC_FRAME / VIBECRAFTED_* state — and when pytest itself is
# launched from inside a marbles-spawned vc_frame session whose name happens
# to match the test's _expected_operator_session(run_id), the routing
# guard in spawn_in_operator_session collapses to in-session pane routing
# instead of the asserted new-tab path.
_ENV_SANITIZE = """
unset VC_FRAME VC_FRAME_PANE_ID VC_FRAME_SESSION_NAME VC_FRAME_TAB_NAME VC_FRAME_CONFIG_DIR
unset ZELLIJ ZELLIJ_PANE_ID ZELLIJ_SESSION_NAME ZELLIJ_SOCKET_DIR
unset VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION VIBECRAFTED_PANE_SEQ VIBECRAFTED_MARBLES_TAB_NAME
unset VIBECRAFTED_OPERATOR_SESSION VIBECRAFTED_WORKER_SESSION VIBECRAFTED_RUN_ID VIBECRAFTED_RUN_LOCK
unset VIBECRAFTED_SKILL_CODE VIBECRAFTED_SKILL_NAME VIBECRAFTED_LOOP_NR
unset VIBECRAFTED_VC_FRAME_CLOSE_AGENT_PANES VIBECRAFTED_VC_FRAME_KEEP_AGENT_PANES VIBECRAFTED_INLINE_STARTUP_WATCH
unset VIBECRAFTED_SPAWN_STAGGER VIBECRAFTED_SPAWN_STAGGER_SECONDS
unset SPAWN_LOOP_NR SPAWN_META SPAWN_TRANSCRIPT SPAWN_REPORT SPAWN_ROOT
unset SPAWN_RUN_ID SPAWN_RUN_LOCK SPAWN_AGENT SPAWN_SKILL_CODE SPAWN_SKILL_NAME
unset SPAWN_PROMPT_ID
export VIBECRAFTED_SPAWN_STAGGER_SECONDS=0
"""


@pytest.fixture(autouse=True)
def _isolate_spawn_test_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shell subprocesses must never fall through to the operator store."""
    home = tmp_path / "ambient-home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VIBECRAFTED_HOME", raising=False)


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    # `bash -l` may restore the account HOME on macOS. Re-assert pytest's
    # isolated HOME after login startup; individual scripts may override it.
    isolated_home = os.environ.get("HOME", "")
    home_guard = f"export HOME={shlex.quote(isolated_home)}\n" if isolated_home else ""
    return subprocess.run(
        ["bash", "-lc", _ENV_SANITIZE + home_guard + script],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _isolated_git_root(tmp_path: Path) -> Path:
    """Create a disposable canonical repo identity for spawn integration tests."""
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "remote",
            "add",
            "origin",
            "https://github.com/vetcoders/spawn-test.git",
        ],
        check=True,
    )
    return root


def _expected_operator_session(run_id: str | None = None) -> str:
    base = (
        re.sub(r"[^a-z0-9]+", "-", REPO_ROOT.name.lower()).strip("-") or "vibecrafted"
    )
    return f"{base}-{run_id}" if run_id else base


def _expected_worker_host_session(root: Path) -> str:
    """Resolve the G7 expectation through the production shell entrypoint."""
    result = _bash(
        f"""
        set -euo pipefail
        export SPAWN_ROOT={shlex.quote(str(root))}
        source {shlex.quote(str(COMMON_SH))}
        spawn_effective_operator_session
        """,
    )
    return result.stdout.strip()


def _mirror_fake_vc_frame(vc_frame: Path) -> None:
    vc_frame = vc_frame.with_name("vc-frame")
    vc_frame.write_text(vc_frame.read_text(encoding="utf-8"), encoding="utf-8")
    vc_frame.chmod(0o755)


def _legacy_expected_operator_session(run_id: str | None = None) -> str:
    base = (
        re.sub(r"[^a-z0-9]+", "-", REPO_ROOT.name.lower()).strip("-") or "vibecrafted"
    )
    return f"{base}-{run_id}" if run_id else base


def _write_fake_core_python(path: Path) -> None:
    """Capture core CLI calls; delegate ordinary Python work to a pinned interpreter."""
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-c" ]]; then
  if [[ "${2:-}" == *"package_root"* ]]; then
    printf "%s\\n" "$FAKE_CORE_SOURCE_DIR"
  fi
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "vibecrafted_core.cli" ]]; then
  shift 2
  [[ "${1:-}" == "resume-session" && "${2:-}" == "codex" ]] || {
    printf 'unexpected fake-core agent invocation: %s\\n' "$*" >&2
    exit 98
  }
  printf "%s\\0" "$@" > "$FAKE_CORE_ARGV_FILE"
  cat > "$FAKE_CORE_PROMPT_FILE"
  printf '%s\\n' '=============== MANUAL EXPLICIT RESUME RECEIPT ===============' 'run_id:             rsme-fixture-1' "agent_session_id:   ${FAKE_CORE_SESSION_ID}" 'resume_mode:        manual_explicit'
  exit 0
fi
exec "${FAKE_REAL_PYTHON:?FAKE_REAL_PYTHON must name the pinned test interpreter}" "$@"
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _read_nul_argv(path: Path) -> list[str]:
    return [item.decode("utf-8") for item in path.read_bytes().split(b"\0") if item]


def test_spawn_require_command_adds_curated_agent_tool_paths(tmp_path: Path) -> None:
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    fake_claude = local_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env bash\nprintf 'claude-ok\\n'\n", encoding="utf-8"
    )
    fake_claude.chmod(0o755)

    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{home}"
        export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
        source "{COMMON_SH}"
        spawn_require_command claude
        command -v claude
        '''
    )

    assert result.stdout.strip() == str(fake_claude)


def test_visible_launch_wrapper_foregrounds_transcript_tail(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.sh"
    transcript = tmp_path / "transcript.log"
    cmd_script = tmp_path / "visible.sh"
    launcher.write_text(
        "#!/usr/bin/env bash\nprintf 'live-line\\n' >> \"$1\"\n", encoding="utf-8"
    )
    launcher.chmod(0o755)

    result = _bash(
        f'''
        set -euo pipefail
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_TRANSCRIPT="{transcript}"
        source "{COMMON_SH}"
        spawn_write_visible_launch_script "{cmd_script}" "{launcher} {transcript}"
        sed -n '1,80p' "{cmd_script}"
        '''
    )

    assert str(transcript) in result.stdout
    assert "tail -n +1 -f" in result.stdout
    assert 'wait "$pid"' in result.stdout


def _write_probe_tool(directory: Path, name: str, marker: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / name
    tool.write_text(f"#!/bin/sh\nprintf '{marker}\\n'\n", encoding="utf-8")
    tool.chmod(0o755)
    return tool


def test_spawn_agent_path_keeps_founder_tools_and_drops_owned_generation_bins(
    tmp_path: Path,
) -> None:
    """A detached agent child resolves the Founder's tools, never a private copy.

    The launcher used to replace the inherited PATH with a closed allowlist, so
    an agent child could neither see the operator's own directories nor avoid
    the bundled generation bin that the allowlist put first.  Both halves are
    asserted behaviourally: a real executable is resolved through ``command -v``
    rather than by string-matching the PATH.
    """

    home = tmp_path / "home"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    stale_generation = runtime_home / "releases" / "4.3.0+gSTALE" / "bin"
    public_bin = home / ".local" / "bin"
    custom_bin = home / "custom" / "bin"
    # Not owned by Vibecrafted: a framework checkout in the operator's own
    # tree.  It merely looks like a generation bin and must be preserved.
    lookalike_bin = home / "dev" / "vibecrafted" / "releases" / "1.0" / "bin"

    _write_probe_tool(public_bin, "claude", "public-claude")
    _write_probe_tool(stale_generation, "claude", "private-claude")
    _write_probe_tool(custom_bin, "founder-tool", "founder-tool")
    _write_probe_tool(lookalike_bin, "checkout-tool", "checkout-tool")

    inherited = os.pathsep.join(
        (
            str(stale_generation),
            str(custom_bin),
            str(lookalike_bin),
            str(public_bin),
            "/usr/bin",
            "/bin",
        )
    )

    result = _bash(
        _ENV_SANITIZE
        + f"""
        set -euo pipefail
        unset VIBECRAFTED_RUNTIME_ROOT VIBECRAFTED_RUNTIME_BIN VIBECRAFTED_RUNTIME_HOME
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export PATH="{inherited}"
        source "{COMMON_SH}"
        spawn_prepend_agent_tool_paths
        printf 'PATH=%s\\n' "$PATH"
        printf 'claude=%s\\n' "$(command -v claude)"
        printf 'founder=%s\\n' "$(command -v founder-tool)"
        printf 'checkout=%s\\n' "$(command -v checkout-tool)"
        """
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    entries = fields["PATH"].split(os.pathsep)

    # The operator's own order survives verbatim, minus the owned generation.
    assert entries[:5] == [
        str(custom_bin),
        str(lookalike_bin),
        str(public_bin),
        "/usr/bin",
        "/bin",
    ]
    assert str(stale_generation) not in entries
    assert len(entries) == len(set(entries))

    # Behavioural proof, not a substring check.
    assert fields["claude"] == str(public_bin / "claude")
    assert fields["founder"] == str(custom_bin / "founder-tool")
    assert fields["checkout"] == str(lookalike_bin / "checkout-tool")


def test_spawn_agent_path_anchors_sanitation_on_custom_runtime_home(
    tmp_path: Path,
) -> None:
    """Sanitation follows VIBECRAFTED_RUNTIME_HOME, not a name-shaped glob.

    A custom runtime home has no ``vibecrafted`` path component, so a pattern
    match on ``*/vibecrafted/releases/*/bin`` leaves its stale generations on
    PATH — the exact leak this cut closes.
    """

    home = tmp_path / "home"
    runtime_home = tmp_path / "opt" / "vcrt"
    stale_generation = runtime_home / "releases" / "4.3.0+gSTALE" / "bin"
    public_bin = home / ".local" / "bin"

    _write_probe_tool(public_bin, "aicx", "public-aicx")
    _write_probe_tool(stale_generation, "aicx", "private-aicx")

    result = _bash(
        _ENV_SANITIZE
        + f"""
        set -euo pipefail
        unset VIBECRAFTED_RUNTIME_ROOT VIBECRAFTED_RUNTIME_BIN
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export VIBECRAFTED_RUNTIME_HOME="{runtime_home}"
        export PATH="{stale_generation}:{public_bin}:/usr/bin:/bin"
        source "{COMMON_SH}"
        spawn_prepend_agent_tool_paths
        printf 'PATH=%s\\n' "$PATH"
        printf 'aicx=%s\\n' "$(command -v aicx)"
        """
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert str(stale_generation) not in fields["PATH"].split(os.pathsep)
    assert fields["aicx"] == str(public_bin / "aicx")


def test_spawn_require_command_never_selects_owned_generation_copy(
    tmp_path: Path,
) -> None:
    """A missing public foundation stays missing instead of falling back.

    The bundled generation carries its own ``aicx``/``loct``/``prview``, so the
    dangerous outcome is not a hard failure — it is a silent success against a
    stale private binary.  Refusal is the product behaviour: the caller prints
    canonical install guidance rather than running the private copy.
    """

    home = tmp_path / "home"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    stale_generation = runtime_home / "releases" / "4.3.0+gSTALE" / "bin"
    custom_bin = home / "custom" / "bin"
    command_name = "vc-test-foundation-probe"

    _write_probe_tool(stale_generation, command_name, "private-copy")

    def _run(extra_path: Path | None) -> subprocess.CompletedProcess[str]:
        path_entries = [str(stale_generation)]
        if extra_path is not None:
            path_entries.append(str(extra_path))
        path_entries.extend(("/usr/bin", "/bin"))
        script = (
            _ENV_SANITIZE
            + f"""
        set -euo pipefail
        unset VIBECRAFTED_RUNTIME_ROOT VIBECRAFTED_RUNTIME_BIN VIBECRAFTED_RUNTIME_HOME
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export PATH="{os.pathsep.join(path_entries)}"
        source "{COMMON_SH}"
        spawn_require_command "{command_name}"
        command -v "{command_name}"
        """
        )
        return subprocess.run(
            ["bash", "-lc", script],
            check=False,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    refused = _run(None)
    assert refused.returncode == 1
    assert f"Required command not found: {command_name}" in refused.stderr
    assert "private-copy" not in refused.stdout

    # Control: the same name in the operator's own directory is legitimate.
    _write_probe_tool(custom_bin, command_name, "founder-copy")
    accepted = _run(custom_bin)
    assert accepted.returncode == 0, accepted.stderr
    assert accepted.stdout.strip() == str(custom_bin / command_name)


def _write_hostile_python(directory: Path) -> Path:
    """A public ``python3`` that would wreck any internal runtime call.

    It fails the resolver's 3.11 version probe and, when actually executed,
    prints a marker and exits 79 instead of doing the work — so selecting it is
    impossible to mistake for success.  This stands in for the real host
    interpreter that the launcher tree started reaching once the owned
    generation bin left PATH (macOS ``/usr/bin/python3`` 3.9.6, or any shim).
    """

    directory.mkdir(parents=True, exist_ok=True)
    tool = directory / "python3"
    tool.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  -c) exit 1 ;;\n"
        "esac\n"
        "printf 'HOST_PYTHON_SELECTED\\n'\n"
        "exit 79\n",
        encoding="utf-8",
    )
    tool.chmod(0o755)
    return tool


def _fake_generation_bin(tmp_path: Path) -> Path:
    """A generation bin whose ``python3`` is a real, capable interpreter."""

    generation_bin = tmp_path / "generation" / "bin"
    generation_bin.mkdir(parents=True, exist_ok=True)
    (generation_bin / "python3").symlink_to(sys.executable)
    return generation_bin


_NEEDS_MODERN_PYTHON = pytest.mark.skipif(
    sys.version_info < (3, 11),
    reason="needs a 3.11+ interpreter to stand in for the generation python",
)


@_NEEDS_MODERN_PYTHON
def test_internal_runtime_python_is_explicit_while_public_python3_stays_the_founders(
    tmp_path: Path,
) -> None:
    """Two owners, one PATH: public python3 is the Founder's, internal is ours.

    Removing the owned generation bin from PATH closed a real leak, but it also
    silently re-pointed every *internal* runtime Python call at whatever
    ``python3`` the Founder's PATH offers.  ``spawn_shell_quote`` is the proven
    casualty: it is called by every ``*_spawn.sh`` launcher, so a hostile or
    merely stale host interpreter corrupted the quoting of every dispatched
    command line.  Internal execution must name its interpreter; public
    resolution must stay untouched.
    """

    home = tmp_path / "home"
    home.mkdir()
    hostile_bin = tmp_path / "hostile-bin"
    _write_hostile_python(hostile_bin)
    generation_bin = _fake_generation_bin(tmp_path)

    result = _bash(
        _ENV_SANITIZE
        + f"""
        set -euo pipefail
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export PATH="{hostile_bin}:/usr/bin:/bin"
        export VIBECRAFTED_PYTHON="{generation_bin / "python3"}"
        export VIBECRAFTED_RUNTIME_BIN="{generation_bin}"
        source "{COMMON_SH}"
        spawn_prepend_agent_tool_paths
        printf 'quote=%s\\n' "$(spawn_shell_quote 'file with spaces')"
        printf 'public_python3=%s\\n' "$(command -v python3)"
        printf 'internal_python=%s\\n' "$(spawn_python_bin)"
        printf 'python3_kind=%s\\n' "$(type -t python3)"
        printf 'PATH=%s\\n' "$PATH"
        """
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )

    # The regression: this returned exit 79 / HOST_PYTHON_SELECTED.
    assert fields["quote"] == "'file with spaces'"
    assert "HOST_PYTHON_SELECTED" not in result.stdout

    # Public resolution is still the Founder's, hostile or not — we do not
    # repair the host's python3, and we never shadow it with a shell function.
    assert fields["public_python3"] == str(hostile_bin / "python3")
    assert fields["python3_kind"] == "file"

    # Internal execution names the runtime interpreter explicitly...
    assert fields["internal_python"] == str(generation_bin / "python3")
    # ...without the private carrier re-entering ambient lookup.
    assert str(generation_bin) not in fields["PATH"].split(os.pathsep)


@_NEEDS_MODERN_PYTHON
def test_internal_python_owner_resolves_from_the_selected_generation_bin(
    tmp_path: Path,
) -> None:
    """``VIBECRAFTED_RUNTIME_BIN`` alone is enough to own internal execution.

    A private-only foundation must stay absent from PATH, so the owner has to be
    reachable purely through the explicit runtime environment — with no
    ``VIBECRAFTED_PYTHON`` set and a hostile public ``python3`` in the lead.
    """

    home = tmp_path / "home"
    home.mkdir()
    hostile_bin = tmp_path / "hostile-bin"
    _write_hostile_python(hostile_bin)
    generation_bin = _fake_generation_bin(tmp_path)

    result = _bash(
        _ENV_SANITIZE
        + f"""
        set -euo pipefail
        unset VIBECRAFTED_PYTHON
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export PATH="{hostile_bin}:/usr/bin:/bin"
        export VIBECRAFTED_RUNTIME_BIN="{generation_bin}"
        source "{COMMON_SH}"
        spawn_prepend_agent_tool_paths
        printf 'quote=%s\\n' "$(spawn_shell_quote 'file with spaces')"
        printf 'internal_python=%s\\n' "$(spawn_python_bin)"
        printf 'framework_version=%s\\n' "$(spawn_framework_version)"
        printf 'PATH=%s\\n' "$PATH"
        """
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields["quote"] == "'file with spaces'"
    assert fields["internal_python"] == str(generation_bin / "python3")
    # The sibling internal reader in the same module must not regress either.
    assert fields["framework_version"] != ""
    assert "HOST_PYTHON_SELECTED" not in result.stdout
    assert str(generation_bin) not in fields["PATH"].split(os.pathsep)


@_NEEDS_MODERN_PYTHON
def test_internal_python_owner_survives_standalone_util_sourcing(
    tmp_path: Path,
) -> None:
    """``util.sh`` is sourced on its own (capability probes do exactly this).

    The interpreter owner therefore lives in ``util.sh`` beside the PATH
    sanitizer that created the need for it, so no module has to guard on
    ``common.sh`` load order to reach it.
    """

    home = tmp_path / "home"
    home.mkdir()
    hostile_bin = tmp_path / "hostile-bin"
    _write_hostile_python(hostile_bin)
    generation_bin = _fake_generation_bin(tmp_path)

    result = _bash(
        _ENV_SANITIZE
        + f"""
        set -euo pipefail
        export HOME="{home}"
        export XDG_DATA_HOME="{home / ".local" / "share"}"
        export PATH="{hostile_bin}:/usr/bin:/bin"
        export VIBECRAFTED_PYTHON="{generation_bin / "python3"}"
        source "{UTIL_SH}"
        spawn_prepend_agent_tool_paths
        printf 'quote=%s\\n' "$(spawn_shell_quote 'file with spaces')"
        """
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields["quote"] == "'file with spaces'"
    assert "HOST_PYTHON_SELECTED" not in result.stdout


def test_skill_dry_run_reaches_spawn_launcher_without_launching(tmp_path: Path) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    local_bin = home / ".local" / "bin"
    plan = tmp_path / "brief.md"
    plan.write_text("# Brief\n", encoding="utf-8")
    local_bin.mkdir(parents=True)
    fake_claude = local_bin / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env bash\nprintf 'claude-ok\\n'\n", encoding="utf-8"
    )
    fake_claude.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PYTHON"] = sys.executable
    env["PATH"] = f"{local_bin}:/usr/bin:/bin:/usr/sbin:/sbin"

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{SHELL_SH}"; '
                f'vc-audit claude --runtime detached --dry-run --file "{plan}"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Dry run mode: launcher generated only:" in result.stdout
    assert "Dry run: agent not launched." in result.stdout
    assert "Spawned headless launcher" not in result.stdout
    assert "Agent launched." not in result.stdout


def test_terminal_spawn_refuses_osascript_fallback_when_vc_frame_fails(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    launcher = tmp_path / "launch.sh"
    vc_frame_capture = tmp_path / "vc-frame.txt"
    osa_capture = tmp_path / "osascript.txt"
    home = tmp_path / "home"

    fake_bin.mkdir()
    home.mkdir()
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    (fake_bin / "vc-frame").write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$VC_FRAME_CAPTURE"\nexit 1'
        + "\n",
        encoding="utf-8",
    )
    (fake_bin / "vc-frame").chmod(0o755)
    _mirror_fake_vc_frame(fake_bin / "vc-frame")
    (fake_bin / "osascript").write_text(
        '#!/usr/bin/env bash\ncat >> "$OSA_CAPTURE"\nexit 0' + "\n",
        encoding="utf-8",
    )
    (fake_bin / "osascript").chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            _ENV_SANITIZE
            + f'''
            set -euo pipefail
            export HOME="{home}"
            export PATH="{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
            export VC_FRAME_CAPTURE="{vc_frame_capture}"
            export OSA_CAPTURE="{osa_capture}"
            export VIBECRAFTED_OPERATOR_SESSION="operator-session"
            export SPAWN_ROOT="{tmp_path}"
            source "{COMMON_SH}"
            spawn_launch "{launcher}" terminal 0 "probe"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    # Degrade, don't die: with no vc-frame operator session the terminal runtime
    # falls back to headless and hands off to observe — it must NEVER reach for
    # the AppleScript/iTerm fallback.
    assert result.returncode == 0
    assert "running headless" in result.stderr
    assert "observe" in result.stderr
    assert vc_frame_capture.exists()
    assert not osa_capture.exists()


def test_operator_session_names_are_run_scoped_by_default() -> None:
    base = _expected_operator_session()
    legacy_a = _legacy_expected_operator_session("agnt-111111-111")
    legacy_b = _legacy_expected_operator_session("agnt-222222-222")

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"

        spawn_operator_session_name_for_run_id "agnt-111111-111"
        spawn_operator_session_name_for_run_id "agnt-222222-222"

        VIBECRAFTED_VC_FRAME_GROUP_BY_CWD=1 spawn_operator_session_name_for_run_id "agnt-111111-111"
        VIBECRAFTED_VC_FRAME_GROUP_BY_CWD=1 spawn_operator_session_name_for_run_id "agnt-222222-222"
        '''
    )

    assert result.stdout.splitlines() == [legacy_a, legacy_b, base, base]


def test_spawn_prepare_paths_include_run_id_for_durable_artifacts(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    plan = tmp_path / "plan.md"
    root = tmp_path / "repo"
    home.mkdir()
    root.mkdir()
    plan.write_text("# Plan\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "-lc",
            _ENV_SANITIZE
            + f'''
            set -euo pipefail
            export HOME="{home}"
            export VIBECRAFTED_HOME="{home / ".vibecrafted"}"
            export VIBECRAFTED_SPAWN_TS="20260613_120000"
            source "{COMMON_SH}"

            spawn_prepare_paths codex "{plan}" "{root}" implement
            printf '%s\\n' "$SPAWN_RUN_ID" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_META" "$SPAWN_LAUNCHER"

            unset SPAWN_RUN_ID SPAWN_RUN_LOCK VIBECRAFTED_RUN_ID VIBECRAFTED_RUN_LOCK
            spawn_prepare_paths codex "{plan}" "{root}" implement
            printf '%s\\n' "$SPAWN_RUN_ID" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$SPAWN_META" "$SPAWN_LAUNCHER"
            ''',
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    (
        first_run,
        first_report,
        first_transcript,
        first_meta,
        first_launcher,
        second_run,
        second_report,
        second_transcript,
        second_meta,
        second_launcher,
    ) = result.stdout.splitlines()

    assert first_run != second_run
    for path in (first_report, first_transcript, first_meta, first_launcher):
        assert first_run in path
        assert second_run not in path
    for path in (second_report, second_transcript, second_meta, second_launcher):
        assert second_run in path
        assert first_run not in path
    assert first_report != second_report
    assert first_transcript != second_transcript
    assert first_meta != second_meta


def test_spawn_prepare_paths_dry_run_never_bootstraps_perception(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    plan = tmp_path / "plan.md"
    root = tmp_path / "repo"
    marker = tmp_path / "perception-started"
    home.mkdir()
    root.mkdir()
    plan.write_text("# Plan\n", encoding="utf-8")

    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{home}"
        export VIBECRAFTED_HOME="{home / ".vibecrafted"}"
        source "{COMMON_SH}"
        spawn_ensure_perception() {{ : > "{marker}"; }}

        spawn_prepare_paths codex "{plan}" "{root}" implement 1
        test ! -e "{marker}"
        '''
    )

    assert result.returncode == 0


def test_spawn_prepare_paths_real_run_bootstraps_perception(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    plan = tmp_path / "plan.md"
    root = tmp_path / "repo"
    marker = tmp_path / "perception-started"
    home.mkdir()
    root.mkdir()
    plan.write_text("# Plan\n", encoding="utf-8")

    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{home}"
        export VIBECRAFTED_HOME="{home / ".vibecrafted"}"
        source "{COMMON_SH}"
        spawn_ensure_perception() {{ : > "{marker}"; }}

        spawn_prepare_paths codex "{plan}" "{root}" implement 0
        test -e "{marker}"
        '''
    )

    assert result.returncode == 0


def _split_vc_frame_calls(payload: str) -> list[list[str]]:
    calls: list[list[str]] = []
    current: list[str] = []
    for line in payload.splitlines():
        if line == "--CALL--":
            if current:
                calls.append(current)
                current = []
            continue
        current.append(line)
    if current:
        calls.append(current)
    return calls


def _read_json_or_none(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _wait_for_meta_payload(
    artifacts_root: Path, pattern: str, timeout: float = 20.0
) -> tuple[Path | None, dict | None]:
    deadline = time.time() + timeout
    latest_meta: Path | None = None
    while time.time() < deadline:
        meta_files = sorted(artifacts_root.rglob(pattern))
        if meta_files:
            latest_meta = meta_files[0]
            payload = _read_json_or_none(latest_meta)
            if payload and payload.get("status") in {"completed", "failed"}:
                return latest_meta, payload
        time.sleep(0.1)
    if latest_meta is None:
        return None, None
    return latest_meta, _read_json_or_none(latest_meta)


def test_runtime_prompt_guards_report_path_from_bare_slash(tmp_path: Path) -> None:
    source_file = tmp_path / "source.md"
    runtime_file = tmp_path / "runtime.md"
    report_path = tmp_path / "report.md"
    source_file.write_text("# Prompt\n", encoding="utf-8")

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_PROMPT_ID="prompt-123"
        spawn_build_runtime_prompt "{source_file}" "{runtime_file}" "{report_path}" claude
        '''
    )

    payload = runtime_file.read_text(encoding="utf-8")
    assert f"Report path: {report_path}" in payload
    assert f"\n{report_path}\n" not in payload


def test_spawn_prepare_paths_preserves_loop_nr_before_ambient_cleanup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    plan = tmp_path / "plan.md"
    bogus_lock = tmp_path / "bogus.lock"
    home.mkdir()
    crafted_home.mkdir(parents=True)
    plan.write_text("# Loop\n", encoding="utf-8")
    bogus_lock.write_text("", encoding="utf-8")

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export HOME="{home}"
        export VIBECRAFTED_HOME="{crafted_home}"
        export SPAWN_LOOP_NR=2
        export VIBECRAFTED_LOOP_NR=2
        export VIBECRAFTED_RUN_ID=marb-test-002
        export VIBECRAFTED_RUN_LOCK="{bogus_lock}"
        spawn_prepare_paths codex "{plan}" "{REPO_ROOT}" implement
        printf '%s\n' "$SPAWN_LOOP_NR"
        '''
    )

    assert result.stdout.strip() == "2"


def test_generated_launcher_preserves_marbles_watcher_mode(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "run.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{REPO_ROOT}"
        export SPAWN_AGENT=codex
        export SPAWN_PROMPT_ID=prompt
        export SPAWN_RUN_ID=marb-test-002
        export SPAWN_RUN_LOCK="{tmp_path / "marb-test.lock"}"
        export SPAWN_LOOP_NR=2
        export SPAWN_SKILL_CODE=marb
        export SPAWN_SKILL_NAME=marbles
        export VIBECRAFTED_MARBLES_WATCHER=1
        export VIBECRAFTED_MARBLES_TAB_NAME=marbles-marb-test
        export VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION=right
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "true"
        grep -E 'SPAWN_LOOP_NR|VIBECRAFTED_MARBLES_WATCHER' "{launcher}"
        '''
    )

    assert "export SPAWN_LOOP_NR=2" in result.stdout
    assert (
        "export VIBECRAFTED_MARBLES_WATCHER=${VIBECRAFTED_MARBLES_WATCHER:-1}"
        in result.stdout
    )


def test_generated_launcher_preseeds_and_stamps_report_identity(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "run.meta.json"
    report = tmp_path / "report.md"
    observed_template = tmp_path / "observed-template.md"
    transcript = tmp_path / "transcript.log"

    _bash(
        f'''
        set -euo pipefail
        export VIBECRAFTED_HOME="{tmp_path / ".vibecrafted"}"
        export VIBECRAFTED_INLINE_STARTUP_WATCH=0
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT=codex
        export SPAWN_PROMPT_ID=prompt
        export SPAWN_RUN_ID=marb-identity-001
        export SPAWN_RUN_LOCK="{tmp_path / "marb-identity.lock"}"
        export SPAWN_LOOP_NR=3
        export SPAWN_SKILL_CODE=marb
        export SPAWN_SKILL_NAME=marbles
        cmd='cp "{report}" "{observed_template}"; printf "# Worker evidence\\n" >> "{report}"; printf "[12:40:43] session: codex-shell-session-001\\n" >> "{transcript}"'
        spawn_write_meta "{meta}" "launching" "codex" "marbles" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
        chmod +x "{launcher}"
        bash "{launcher}"
        '''
    )

    template = observed_template.read_text(encoding="utf-8")
    assert "run_id: marb-identity-001" in template
    assert "session_id: pending-unset" in template
    assert "finalized: false" in template
    assert "launcher_template: true" in template

    finalized = report.read_text(encoding="utf-8")
    assert "run_id: marb-identity-001" in finalized
    assert "session_id: codex-shell-session-001" in finalized
    assert "finalized: false" in finalized
    assert "launcher_template:" not in finalized
    assert "# Worker evidence" in finalized


def test_generated_launcher_preloads_curated_agent_tool_paths(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "run.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{REPO_ROOT}"
        export SPAWN_AGENT=claude
        export SPAWN_PROMPT_ID=prompt
        export SPAWN_RUN_ID=fwup-test-001
        export SPAWN_RUN_LOCK="{tmp_path / "fwup-test.lock"}"
        export SPAWN_LOOP_NR=0
        export SPAWN_SKILL_CODE=fwup
        export SPAWN_SKILL_NAME=followup
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "true"
        '''
    )

    body = launcher.read_text(encoding="utf-8")
    assert 'export PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}"' in body
    assert "spawn_prepend_agent_tool_paths" in body


def test_runtime_prompt_includes_vc_agents_worker_charter(tmp_path: Path) -> None:
    source_file = tmp_path / "source.md"
    runtime_file = tmp_path / "runtime.md"
    report_path = tmp_path / "report.md"
    source_file.write_text("# Prompt\n", encoding="utf-8")

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_PROMPT_ID="prompt-123"
        spawn_build_runtime_prompt "{source_file}" "{runtime_file}" "{report_path}" codex
        '''
    )

    payload = runtime_file.read_text(encoding="utf-8")
    assert "## VC Agents Worker Charter" in payload
    assert "finalized: false" in payload
    assert "already created the report file with machine-owned" in payload
    assert "Only when you believe the run succeeded" in payload
    assert "Do NOT invoke vc-agents" in payload
    assert "do not reinterpret it" in payload
    assert "record the boundary clearly in your report" in payload
    # Native in-process delegation (Task tool / vc-delegate) must be explicitly
    # permitted so worker agents do not over-compress the charter into a
    # blanket "no delegation" rule.
    assert "Native in-process delegation is allowed" in payload
    assert "vc-delegate" in payload
    assert "External fleet escalation is forbidden" in payload
    # Scope is bounded by the dispatched plan, not by the charter — workers on
    # vc-justdo / vc-ownership / vc-workflow must not self-narrow scope.
    assert "Read the plan, not the charter, for scope" in payload
    assert "**REPORT**: mandatory" in payload
    assert "**COMMIT**:" in payload
    assert "NO empty commits" in payload
    assert "`--allow-empty`" in payload
    assert "If you have nothing to stage, do not commit" in payload


def test_spawn_clean_model_normalizes_placeholders_and_passes_real_values(
    tmp_path: Path,
) -> None:
    # Single source of truth for the placeholder-model filter. Marbles
    # dispatch sites (marbles_spawn.sh, marbles_next.sh L1 and L2+) all
    # route through this helper so adding a new placeholder token here
    # propagates everywhere.
    out_file = tmp_path / "out.txt"
    proc = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        {{
          printf 'empty=[%s]\n' "$(spawn_clean_model "")"
          printf 'pending=[%s]\n' "$(spawn_clean_model "pending")"
          printf 'unknown=[%s]\n' "$(spawn_clean_model "unknown")"
          printf 'null=[%s]\n' "$(spawn_clean_model "null")"
          printf 'real=[%s]\n' "$(spawn_clean_model "claude-opus-4-7")"
          printf 'sonnet=[%s]\n' "$(spawn_clean_model "claude-sonnet-4-6")"
          printf 'no_arg=[%s]\n' "$(spawn_clean_model)"
          printf 'mixed=[%s]\n' "$(spawn_clean_model "Pending")"
        }} > "{out_file}"
        '''
    )
    assert proc.returncode == 0
    payload = out_file.read_text(encoding="utf-8")
    assert "empty=[]" in payload
    assert "pending=[]" in payload
    assert "unknown=[]" in payload
    assert "null=[]" in payload
    assert "no_arg=[]" in payload
    assert "real=[claude-opus-4-7]" in payload
    assert "sonnet=[claude-sonnet-4-6]" in payload
    # Case-sensitive: only lowercase tokens are placeholders. Capitalized
    # variants pass through so the helper does not silently swallow real
    # model names that happen to share a prefix.
    assert "mixed=[Pending]" in payload


def test_marbles_dispatch_sites_route_placeholder_filter_through_helper() -> None:
    # Lock convergence: the three legacy `pending|unknown|null` chains in
    # marbles_spawn.sh and marbles_next.sh have been collapsed into a single
    # spawn_clean_model() helper. If a future change reintroduces the
    # inline chain, this test fires before the regression ships.
    spawn_text = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "marbles_spawn.sh"
    ).read_text(encoding="utf-8")
    next_text = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "marbles_next.sh"
    ).read_text(encoding="utf-8")
    util_text = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "util.sh"
    ).read_text(encoding="utf-8")

    # Helper exists in exactly one place.
    assert "spawn_clean_model()" in util_text
    assert spawn_text.count('!= "pending"') == 0
    assert next_text.count('!= "pending"') == 0
    # Both dispatch sites call the helper.
    assert 'spawn_clean_model "$ancestor_model"' in spawn_text
    assert 'spawn_clean_model "$loop_model"' in next_text
    # No leftover inline `pending|unknown|null` case branches outside the helper.
    assert next_text.count("pending|unknown|null") == 0


def test_research_runtime_prompt_forbids_commits_and_source_mutation(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "source.md"
    runtime_file = tmp_path / "runtime.md"
    report_path = tmp_path / "report.md"
    source_file.write_text("# Research prompt\n", encoding="utf-8")

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID="rsch-123"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_SKILL_NAME="research"
        export SPAWN_SKILL_CODE="rsch"
        spawn_build_runtime_prompt "{source_file}" "{runtime_file}" "{report_path}" codex
        '''
    )

    payload = runtime_file.read_text(encoding="utf-8")
    assert "## Research Safety Contract" in payload
    assert "finalized: false" in payload
    assert "claim:" in payload
    assert "**GIT WRITES forbidden**" in payload
    assert "do not stage, commit, amend" in payload
    assert "**SOURCE MUTATION**: forbidden" in payload
    assert "Do not edit repo source files" in payload
    assert "Working tree must be" in payload
    assert "unchanged at the end of the run" in payload
    assert "**COMMIT**: mandatory. One commit when done." not in payload


def test_codex_research_prompt_uses_clean_research_payload(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "source.md"
    runtime_file = tmp_path / "runtime.md"
    report_path = tmp_path / "report.md"
    source_file.write_text(
        "---\nrun_id: rsch-123\nskill: vc-research\nstatus: in-progress\n---\n\n# Research Prompt\n\nQuestion: How should clean worker prompts behave?\n",
        encoding="utf-8",
    )

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID="rsch-123"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_SKILL_NAME="research"
        spawn_build_runtime_prompt "{source_file}" "{runtime_file}" "{report_path}" codex
        '''
    )

    payload = runtime_file.read_text(encoding="utf-8")
    assert "# Research Prompt" in payload
    assert "Question: How should clean worker prompts behave?" in payload
    assert "## Codex Report Write Contract" in payload
    assert "`codex exec --output-last-message`" in payload
    assert "write the COMPLETE markdown report to the exact `Report path`" in payload
    assert "using a shell command such as a heredoc" in payload
    assert "must not be the only place where the report exists" in payload
    assert "skill: vc-research" not in payload
    assert "Perform the vc-research skill" not in payload
    assert "## VC Agents Worker Charter" not in payload
    assert "Do NOT invoke vc-agents" not in payload
    assert "Codex Research Report Capture Contract" not in payload
    assert "triple-agent research swarm" not in payload.lower()
    assert "delegate" not in payload.lower()


def test_codex_implement_prompt_does_not_get_research_capture_contract(
    tmp_path: Path,
) -> None:
    source_file = tmp_path / "source.md"
    runtime_file = tmp_path / "runtime.md"
    report_path = tmp_path / "report.md"
    source_file.write_text("# Implement Prompt\n", encoding="utf-8")

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID="impl-123"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_SKILL_NAME="implement"
        spawn_build_runtime_prompt "{source_file}" "{runtime_file}" "{report_path}" codex
        '''
    )

    payload = runtime_file.read_text(encoding="utf-8")
    assert "## Codex Research Report Capture Contract" not in payload
    assert (
        "final assistant message MUST be the complete markdown report verbatim"
        not in payload
    )


def test_generated_launcher_runs_from_spawn_root(tmp_path: Path) -> None:
    root_dir = tmp_path / "project"
    root_dir.mkdir()
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{root_dir}"
        export SPAWN_AGENT="claude"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_LOOP_NR="2"
        export SPAWN_SKILL_CODE="marb"
        cmd='pwd > "{report}"'
        spawn_write_meta "{meta}" "launching" "claude" "marbles" "{root_dir}" "{launcher}" "{report}" "{transcript}" "{launcher}"
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
        chmod +x "{launcher}"
        bash "{launcher}"
        '''
    )

    assert report.read_text(encoding="utf-8").strip() == str(root_dir)


def test_generated_launcher_fails_fast_on_invalid_hook_syntax(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            source "{COMMON_SH}"
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_AGENT="claude"
            export SPAWN_PROMPT_ID="prompt-123"
            export SPAWN_RUN_ID="run-123"
            export SPAWN_LOOP_NR="1"
            export SPAWN_SKILL_CODE="marb"
            cmd='printf "ok\\n" > "{report}"'
            bad_hook="echo '"
            spawn_write_meta "{meta}" "launching" "claude" "marbles" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
            spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd" "" "$bad_hook"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Generated launcher has invalid shell syntax" in result.stderr
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1


def test_spawn_watch_startup_reports_pass_and_dashboard_hint(tmp_path: Path) -> None:
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "launching" "codex" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_write_frontmatter "{transcript}" "codex" "unknown" "transcript"
        (
          sleep 0.2
          printf '[12:40:43] session: 54865595-899c-4402-b957-911433e46199\\nWorking...\\n' >> "{transcript}"
        ) &
        spawn_watch_startup "{meta}" "{transcript}" "{report}" 1
        '''
    )

    assert "Startup check: passed in the first 1s." in result.stdout
    assert "vibecrafted dashboard" in result.stdout


def test_spawn_finalize_artifacts_canonicalizes_by_date_repo_session_and_kind(
    tmp_path: Path,
) -> None:
    reports = (
        tmp_path
        / "home"
        / ".vibecrafted"
        / "artifacts"
        / "Vetcoders"
        / "vibecrafted"
        / "2026_0604"
        / "reports"
    )
    reports.mkdir(parents=True)
    meta = reports / "old.meta.json"
    report = reports / "old.md"
    transcript = reports / "old.transcript.log"
    launcher = tmp_path / "launcher.sh"
    plan = tmp_path / "plan.md"
    session_id = "019e90db-cdfe-7ad2-ab53-d62bef636222"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export HOME="{tmp_path / "home"}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        printf '# Report\\n\\nDone.\\n' > "{report}"
        printf -- '---\\n---\\n[12:40:43] session: {session_id}\\nWorking...\\n' > "{transcript}"
        spawn_write_meta "{meta}" "launching" "codex" "implement" "{tmp_path}" "{plan}" "{report}" "{transcript}" "{launcher}"
        spawn_finish_meta "{meta}" "completed" "0"
        spawn_finalize_artifacts "{meta}" "{report}" "{transcript}"
        '''
    )

    assert result.returncode == 0
    matches = sorted(
        reports.glob(f"*_Vetcoders_vibecrafted_{session_id}-report.meta.json")
    )
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == session_id
    assert payload["artifact_stem"].endswith(f"_{session_id}-report")
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)",
        payload["date"],
    )
    assert Path(payload["report"]).name == matches[0].name.replace(".meta.json", ".md")
    assert Path(payload["transcript"]).name == matches[0].name.replace(
        ".meta.json", ".transcript.log"
    )
    # Announced paths survive canonicalization as compat symlinks — watchers
    # keyed on the spawn-time announcement must keep resolving
    # (VC-vbcr-stabilize-032: one truth, two names).
    assert report.is_symlink()
    assert report.resolve() == Path(payload["report"]).resolve()
    assert meta.is_symlink()
    assert json.loads(meta.read_text(encoding="utf-8"))["status"] == "completed"

    final_report = Path(payload["report"])
    assert final_report.exists()
    assert "date:" in final_report.read_text(encoding="utf-8")


def test_spawn_watch_startup_reports_failure_without_dashboard_hint(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_AGENT="claude"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "launching" "claude" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_write_frontmatter "{transcript}" "claude" "unknown" "transcript"
        (
          sleep 0.2
          printf 'Not logged in · Please run /login\\n' >> "{transcript}"
          spawn_finish_meta "{meta}" "failed" "1"
        ) &
        spawn_watch_startup "{meta}" "{transcript}" "{report}" 1
        '''
    )

    assert "Startup check: failed in the first 1s." in result.stdout
    assert "vibecrafted dashboard" not in result.stdout


def test_spawn_watch_startup_reports_still_launching_when_quiet(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_AGENT="gemini"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "launching" "gemini" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_write_frontmatter "{transcript}" "gemini" "unknown" "transcript"
        spawn_watch_startup "{meta}" "{transcript}" "{report}" 1
        '''
    )

    assert "Startup check: still launching after 1s." in result.stdout
    assert "vibecrafted dashboard" in result.stdout


def test_spawn_watch_startup_can_probe_without_echoing_transcript(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        export VIBECRAFTED_STARTUP_WATCH_ECHO=0
        spawn_write_meta "{meta}" "launching" "codex" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_write_frontmatter "{transcript}" "codex" "unknown" "transcript"
        (
          sleep 0.2
          printf '[12:40:43] session: 54865595-899c-4402-b957-911433e46199\\nWorking...\\n' >> "{transcript}"
        ) &
        spawn_watch_startup "{meta}" "{transcript}" "{report}" 1
        '''
    )

    assert "Startup check: passed in the first 1s." in result.stdout
    assert "session: 54865595-899c-4402-b957-911433e46199" not in result.stdout
    assert "Working..." not in result.stdout


def test_spawn_finish_meta_does_not_parse_codex_core_session_error_as_id(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"
    launcher = tmp_path / "launcher.sh"
    plan = tmp_path / "plan.md"

    transcript.write_text(
        "2026-05-08T19:51:31.928244Z ERROR codex_core::session: failed to record rollout items: thread 019e0905-1eb8-7890-a73a-74bbb2171341 not found\n[21:51:32] session: 019e09051eb87890a73a74bbb2171341"
        + "\n",
        encoding="utf-8",
    )

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "launching" "codex" "implement" "{tmp_path}" "{plan}" "{report}" "{transcript}" "{launcher}"
        spawn_finish_meta "{meta}" "completed" "0"
        '''
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["session_id"] == "019e09051eb87890a73a74bbb2171341"


def test_codex_stream_filter_handles_structured_turn_failed_payload() -> None:
    payload = (
        '{"type":"turn.failed","error":{"message":"stream exploded","code":"EPIPE"}}\n'
    )

    result = subprocess.run(
        ["jq", "-rj", "-f", str(CODEX_STREAM_FILTER)],
        check=True,
        cwd=REPO_ROOT,
        input=payload,
        capture_output=True,
        text=True,
    )

    assert "stream exploded" in result.stdout
    assert "cannot be added" not in result.stderr


def test_codex_stream_bridge_tolerates_turn_abort_and_malformed_json(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "trace.log"
    payload = '{"type":"thread.started","thread_id":"fake-session-001"}\n{"type":"turn.aborted","message":"refresh token already used"}\n{"type":"item.completed"'

    subprocess.run(
        [
            "python3",
            str(CODEX_STREAM_BRIDGE),
            "--transcript",
            str(transcript),
        ],
        check=True,
        cwd=REPO_ROOT,
        input=payload,
        capture_output=True,
        text=True,
    )

    transcript_text = transcript.read_text(encoding="utf-8")
    assert "session: fake-session-001" in transcript_text
    assert "refresh token already used" in transcript_text
    assert '{"type":"item.completed"' in transcript_text


def test_codex_spawn_marks_meta_failed_when_codex_emits_non_json_auth_error(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = home / ".local" / "bin"
    plan = tmp_path / "plan.md"
    repo_root = _isolated_git_root(tmp_path)

    home.mkdir()
    fake_bin.mkdir(parents=True)
    plan.write_text("# Plan\n", encoding="utf-8")

    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nreport=""\nwhile [[ $# -gt 0 ]]; do\n  case "$1" in\n    --output-last-message) shift; report="$1" ;;\n  esac\n  shift || true\ndone\ncat >/dev/null || true\nprintf "Your access token could not be refreshed because your refresh token was already used.\\n" >&2\nexit 17'
        + "\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(crafted_home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "VIBECRAFTED_INLINE_STARTUP_WATCH": "0",
    }

    result = subprocess.run(
        [
            "bash",
            str(CODEX_SPAWN_SH),
            "--runtime",
            "headless",
            "--root",
            str(repo_root),
            str(plan),
        ],
        check=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Agent launched." in result.stdout
    assert "Await:" in result.stdout

    artifacts_root = crafted_home / "artifacts"
    meta_file, meta_payload = _wait_for_meta_payload(
        artifacts_root, "*_plan_codex.meta.json"
    )

    assert meta_file is not None, "codex spawn did not write meta.json"
    assert meta_payload is not None, "codex spawn did not finish writing meta.json"
    assert meta_payload["status"] == "failed"
    assert meta_payload["exit_code"] == 17

    report_file = Path(meta_payload["report"])
    deadline = time.time() + 5
    report_text = ""
    while time.time() < deadline:
        if report_file.exists():
            report_text = report_file.read_text(encoding="utf-8")
            if "Codex failed before writing a standalone report file." in report_text:
                break
        time.sleep(0.1)

    assert report_file.exists()
    assert "Codex failed before writing a standalone report file." in report_text
    transcript_file = meta_file.with_name(
        meta_file.name.replace(".meta.json", ".transcript.log")
    )
    assert "refresh token was already used" in transcript_file.read_text(
        encoding="utf-8"
    )


def test_codex_spawn_preserves_standalone_report_when_last_message_is_handoff(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = home / ".local" / "bin"
    plan = tmp_path / "research-plan.md"
    repo_root = _isolated_git_root(tmp_path)

    home.mkdir()
    fake_bin.mkdir(parents=True)
    plan.write_text("# Research Plan\n", encoding="utf-8")

    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nlast_message=""\nwhile [[ $# -gt 0 ]]; do\n  case "$1" in\n    --output-last-message) shift; last_message="${1:-}" ;;\n  esac\n  shift || true\ndone\nprompt="$(cat)"\nreport_path="$(printf "%s\\n" "$prompt" | sed -n \'s/^Report path: //p\' | tail -n 1)"\n[[ -n "$report_path" ]] || exit 22\nmkdir -p "$(dirname "$report_path")"\ncat > "$report_path" <<EOF_REPORT\n---\nagent: codex\nstatus: completed\n---\n\n# Full Research Report\n\nThis is the durable report body.\nEOF_REPORT\nif [[ -n "$last_message" ]]; then\n  mkdir -p "$(dirname "$last_message")"\n  cat > "$last_message" <<EOF_LAST\nDone. Report saved at: $report_path\nEOF_LAST\nfi\nprintf \'{"type":"thread.started","thread_id":"fake-session-standalone"}\\n\'\nprintf \'{"type":"item.completed","item":{"type":"agent_message","text":"structured report was streamed earlier"}}\\n\'\nprintf \'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\\n\''
        + "\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(crafted_home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "VIBECRAFTED_INLINE_STARTUP_WATCH": "0",
        "VIBECRAFTED_SKILL_CODE": "rsch",
        "VIBECRAFTED_SKILL_NAME": "research",
    }

    result = subprocess.run(
        [
            "bash",
            str(CODEX_SPAWN_SH),
            "--mode",
            "research",
            "--runtime",
            "headless",
            "--root",
            str(repo_root),
            str(plan),
        ],
        check=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Agent launched." in result.stdout

    artifacts_root = crafted_home / "artifacts"
    meta_file, meta_payload = _wait_for_meta_payload(
        artifacts_root, "*_research-plan_codex.meta.json"
    )

    assert meta_file is not None, "codex spawn did not write research meta.json"
    assert meta_payload is not None, "codex spawn did not finish writing meta.json"
    assert meta_payload["status"] == "completed"

    report_file = Path(meta_payload["report"])
    report_text = report_file.read_text(encoding="utf-8")
    assert "# Full Research Report" in report_text
    assert "This is the durable report body." in report_text
    assert "Done. Report saved at" not in report_text

    last_message_file = Path(meta_payload["transcript"]).with_suffix(".last-message.md")
    assert last_message_file.exists()
    assert "Done. Report saved at" in last_message_file.read_text(encoding="utf-8")


def test_codex_research_does_not_copy_pointer_last_message_as_report(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = home / ".local" / "bin"
    plan = tmp_path / "research-plan.md"
    repo_root = _isolated_git_root(tmp_path)

    home.mkdir()
    fake_bin.mkdir(parents=True)
    plan.write_text("# Research Plan\n", encoding="utf-8")

    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nlast_message=""\nwhile [[ $# -gt 0 ]]; do\n  case "$1" in\n    --output-last-message) shift; last_message="${1:-}" ;;\n  esac\n  shift || true\ndone\ncat >/dev/null\nif [[ -n "$last_message" ]]; then\n  mkdir -p "$(dirname "$last_message")"\n  cat > "$last_message" <<EOF_LAST\nDone. Report saved at: /tmp/research/codex.md\nEOF_LAST\nfi\nprintf \'{"type":"thread.started","thread_id":"fake-session-pointer"}\\n\'\nprintf \'{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\\n\''
        + "\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(crafted_home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "VIBECRAFTED_INLINE_STARTUP_WATCH": "0",
        "VIBECRAFTED_SKILL_CODE": "rsch",
        "VIBECRAFTED_SKILL_NAME": "research",
    }

    result = subprocess.run(
        [
            "bash",
            str(CODEX_SPAWN_SH),
            "--mode",
            "research",
            "--runtime",
            "headless",
            "--root",
            str(repo_root),
            str(plan),
        ],
        check=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Agent launched." in result.stdout

    artifacts_root = crafted_home / "artifacts"
    meta_file, meta_payload = _wait_for_meta_payload(
        artifacts_root, "*_research-plan_codex.meta.json"
    )

    assert meta_file is not None, "codex spawn did not write research meta.json"
    assert meta_payload is not None, "codex spawn did not finish writing meta.json"
    assert meta_payload["status"] == "failed"
    assert meta_payload["exit_code"] == 65

    report_file = Path(meta_payload["report"])
    deadline = time.time() + 5
    report_text = ""
    while time.time() < deadline:
        if report_file.exists():
            report_text = report_file.read_text(encoding="utf-8")
            if "Codex failed before writing a standalone report file." in report_text:
                break
        time.sleep(0.1)
    assert report_file.exists()
    assert "Codex failed before writing a standalone report file." in report_text
    assert "Done. Report saved at" not in report_text

    last_message_file = Path(meta_payload["transcript"]).with_suffix(".last-message.md")
    assert last_message_file.exists()
    assert "Done. Report saved at" in last_message_file.read_text(encoding="utf-8")


def test_claude_spawn_marks_meta_failed_when_stream_has_no_json(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = home / ".local" / "bin"
    plan = tmp_path / "plan.md"
    repo_root = _isolated_git_root(tmp_path)

    home.mkdir()
    fake_bin.mkdir(parents=True)
    plan.write_text("# Plan\n", encoding="utf-8")

    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\ncat >/dev/null || true\nprintf "Not logged in · Please run /login\\n" >&2\nexit 19'
        + "\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(crafted_home),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "VIBECRAFTED_INLINE_STARTUP_WATCH": "0",
    }

    result = subprocess.run(
        [
            "bash",
            str(CLAUDE_SPAWN_SH),
            "--runtime",
            "headless",
            "--root",
            str(repo_root),
            str(plan),
        ],
        check=True,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Agent launched." in result.stdout
    assert "Await:" in result.stdout

    artifacts_root = crafted_home / "artifacts"
    meta_file, meta_payload = _wait_for_meta_payload(
        artifacts_root, "*_plan_claude.meta.json"
    )

    assert meta_file is not None, "claude spawn did not write meta.json"
    assert meta_payload is not None, "claude spawn did not finish writing meta.json"
    assert meta_payload["status"] == "failed"
    assert meta_payload["exit_code"] != 0


def test_generated_launcher_includes_startup_watch(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT="claude"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_LOOP_NR="2"
        export SPAWN_SKILL_CODE="impl"
        cmd='printf "ok\\n" > "{report}"'
        spawn_write_meta "{meta}" "launching" "claude" "implement" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
        '''
    )

    body = launcher.read_text(encoding="utf-8")
    assert (
        'VIBECRAFTED_STARTUP_WATCH_ECHO=0 spawn_watch_startup "$meta" "$transcript" "$report" &'
        in body
    )
    assert 'wait "$startup_watch_pid"' in body


def test_research_launcher_blocks_git_write_operations(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            source "{COMMON_SH}"
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_AGENT="codex"
            export SPAWN_PROMPT_ID="prompt-123"
            export SPAWN_RUN_ID="rsch-014520-002"
            export SPAWN_LOOP_NR="0"
            export SPAWN_SKILL_CODE="rsch"
            export SPAWN_SKILL_NAME="research"
            export VIBECRAFTED_INLINE_STARTUP_WATCH=0
            cmd='git commit --allow-empty -m blocked'
            spawn_write_meta "{meta}" "launching" "codex" "research" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
            spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
            chmod +x "{launcher}"
            bash "{launcher}"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 126
    assert "vibecrafted research mode blocks git write operation: git commit" in (
        result.stderr + result.stdout
    )
    assert json.loads(meta.read_text(encoding="utf-8"))["status"] == "failed"


def test_spawn_in_vc_frame_pane_honors_requested_direction(tmp_path: Path) -> None:
    run_id = "marb-014520"
    operator_session = _expected_operator_session(run_id)
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-args.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                # The W1-06 liveness gate resolves the effective operator
                # session via list-sessions; without a live listing the
                # explicit session is rejected and the spawn refuses.
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{operator_session}"',
                "  exit 0",
                "fi",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VC_FRAME=1
        export VC_FRAME_PANE_ID=terminal_1
        export VIBECRAFTED_RUN_ID="{run_id}"
        export VC_FRAME_SESSION_NAME="{operator_session}"
        export ZELLIJ_SESSION_NAME="{operator_session}"
        export VIBECRAFTED_OPERATOR_SESSION="{operator_session}"
        # G7: in-pane path only fires when current seat == worker host.
        # Simulate "already inside the project worker session".
        export VIBECRAFTED_WORKER_SESSION="{operator_session}"
        export VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION=down
        source "{COMMON_SH}"
        spawn_in_vc_frame_pane "{launcher}" "workflow"
        '''
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--name" in payload
    assert "workflow" in payload
    assert "--direction" in payload
    assert "down" in payload


def test_spawn_context_preserves_legacy_vc_frame_emitted_env() -> None:
    result = _bash(
        f'''
        set -euo pipefail
        export ZELLIJ=1
        export ZELLIJ_PANE_ID=terminal_legacy
        export ZELLIJ_SESSION_NAME=legacy-session
        source "{COMMON_SH}"
        spawn_in_vc_frame_context
        spawn_current_vc_frame_session_name
        '''
    )

    assert result.stdout.strip() == "legacy-session"


def test_generated_launcher_preserves_operator_session_contract(tmp_path: Path) -> None:
    run_id = "marb-014520"
    operator_session = _expected_operator_session(run_id)
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT="claude"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="run-123"
        export SPAWN_LOOP_NR="2"
        export SPAWN_SKILL_CODE="marb"
        export VIBECRAFTED_RUN_ID="{run_id}"
        export VIBECRAFTED_OPERATOR_SESSION="{operator_session}"
        export VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION="right"
        cmd='printf "%s\\n%s\\n" "$VIBECRAFTED_OPERATOR_SESSION" "$VIBECRAFTED_VC_FRAME_SPAWN_DIRECTION" > "{report}"'
        spawn_write_meta "{meta}" "launching" "claude" "marbles" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
        chmod +x "{launcher}"
        bash "{launcher}"
        '''
    )

    payload = report.read_text(encoding="utf-8").splitlines()
    assert payload == [operator_session, "right"]


def test_generated_launcher_completes_meta_before_success_hook_failure(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            source "{COMMON_SH}"
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_AGENT="claude"
            export SPAWN_PROMPT_ID="prompt-123"
            export SPAWN_RUN_ID="marb-014520-002"
            export SPAWN_LOOP_NR="2"
            export SPAWN_SKILL_CODE="marb"
            cmd='printf "ok\\n" > "{report}"'
            spawn_write_meta "{meta}" "launching" "claude" "marbles" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
            spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd" "" "exit 23"
            chmod +x "{launcher}"
            bash "{launcher}"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 23
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0


def test_generated_launcher_adds_uniform_artifact_closure(tmp_path: Path) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"
    root_dir = tmp_path / "repo"
    root_dir.mkdir()

    _bash(
        f'''
        set -euo pipefail
        export VIBECRAFTED_HOME="{tmp_path / ".vibecrafted"}"
        source "{COMMON_SH}"
        export SPAWN_ROOT="{root_dir}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="impl-010203-999"
        export SPAWN_LOOP_NR="0"
        export SPAWN_SKILL_CODE="impl"
        cmd='printf "[12:40:43] session: sess-abc-123\\n[12:40:44] tokens: 10 in (3 cached) / 5 out\\n" >> "{transcript}"; printf "body\\n" > "{report}"'
        spawn_write_meta "{meta}" "launching" "codex" "implement" "{root_dir}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{launcher}"
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd"
        chmod +x "{launcher}"
        bash "{launcher}"
        '''
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["session_id"] == "sess-abc-123"
    assert payload["tokens_input"] == 10
    assert payload["tokens_cached_input"] == 3
    assert payload["tokens_output"] == 5
    assert payload["tokens_total"] == 15
    assert (
        payload["resume_hint"]
        == f"Use `cd {root_dir} && vc-resume --session sess-abc-123` to continue work with this Agent."
    )

    report_text = report.read_text(encoding="utf-8")
    transcript_text = transcript.read_text(encoding="utf-8")
    for text in (report_text, transcript_text):
        assert text.startswith("---\n")
        assert "session_id: sess-abc-123" in text
        assert "tokens_input: 10" in text
        assert "tokens_output: 5" in text
        assert "tokens_total: 15" in text
        assert "cost_usd: unknown" in text
        assert "<!-- vibecrafted-artifact-footer:impl-010203-999 -->" in text
        assert "vc-resume --session sess-abc-123" in text


def test_vc_resume_can_infer_agent_from_session_meta(tmp_path: Path) -> None:
    crafted_home = tmp_path / ".vibecrafted"
    fake_core = tmp_path / "fake-core-python"
    core_argv = tmp_path / "core-argv.bin"
    core_prompt = tmp_path / "core-prompt.txt"
    core_source = tmp_path / "core-source"
    provider_called = tmp_path / "provider-called"
    hostile_python = _write_hostile_python(tmp_path / "hostile public bin")
    core_source.mkdir()
    _write_fake_core_python(fake_core)
    meta_dir = (
        crafted_home / "artifacts" / "Vetcoders" / "repo" / "2026_0528" / "reports"
    )
    meta_dir.mkdir(parents=True)
    (meta_dir / "run.meta.json").write_text(
        json.dumps({"session_id": "sess-abc-123", "agent": "codex"}),
        encoding="utf-8",
    )

    result = _bash(
        f'''
        set -euo pipefail
        export VIBECRAFTED_HOME="{crafted_home}"
        export VIBECRAFTED_PYTHON="{fake_core}"
        export FAKE_REAL_PYTHON="{sys.executable}"
        export PATH="{hostile_python.parent}:/usr/bin:/bin:/usr/sbin:/sbin"
        export FAKE_CORE_ARGV_FILE="{core_argv}"
        export FAKE_CORE_PROMPT_FILE="{core_prompt}"
        export FAKE_CORE_SOURCE_DIR="{core_source}"
        export FAKE_CORE_SESSION_ID="sess-abc-123"
        export PROVIDER_CALLED="{provider_called}"
        source "{SHELL_SH}"
        codex() {{ : > "$PROVIDER_CALLED"; return 97; }}
        vc-resume --session sess-abc-123 --prompt hello
        '''
    )

    assert "MANUAL EXPLICIT RESUME RECEIPT" in result.stdout
    assert "agent_session_id:   sess-abc-123" in result.stdout
    assert _read_nul_argv(core_argv) == [
        "resume-session",
        "codex",
        "--agent-session-id",
        "sess-abc-123",
        "--prompt-stdin",
        "--root",
        str(REPO_ROOT),
        "--source-dir",
        str(core_source),
    ]
    assert core_prompt.read_text(encoding="utf-8") == "hello"
    assert not provider_called.exists()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_vc_frame_helpers_use_internal_python_with_hostile_public_python(
    tmp_path: Path, shell: str
) -> None:
    """ANSI parsing and worker-host inference do not select public ``python3``."""
    hostile_python = _write_hostile_python(tmp_path / "hostile public bin")
    project_root = tmp_path / "project root with spaces"
    project_root.mkdir()
    script = f'''
    set -euo pipefail
    export HOME="{tmp_path / "home with spaces"}"
    export PATH="{hostile_python.parent}:/usr/bin:/bin:/usr/sbin:/sbin"
    export VIBECRAFTED_PYTHON="{sys.executable}"
    export SPAWN_ROOT="{project_root}"
    source "{SHELL_SH}"
    printf 'ansi='
    printf '\\033[31mready\\033[0m' | _vetcoders_strip_ansi
    printf '\\nhost=%s\\n' "$(_vetcoders_effective_worker_session)"
    '''
    if shell == "bash":
        result = _bash(script)
    else:
        env = os.environ.copy()
        env["HOME"] = str(tmp_path / "ambient home")
        result = subprocess.run(
            ["zsh", "-f", "-c", _ENV_SANITIZE + script],
            check=True,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    lines = result.stdout.splitlines()
    assert lines[0] == "ansi=ready"
    assert lines[1].startswith("host=project-root-with-spaces-")
    assert lines[1].endswith("-w")
    assert "HOST_PYTHON_SELECTED" not in result.stdout + result.stderr


def test_generated_launcher_marks_meta_failed_before_failure_hook(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "meta.json"
    report = tmp_path / "report.txt"
    transcript = tmp_path / "trace.log"
    failure_seen = tmp_path / "failure-meta.json"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            source "{COMMON_SH}"
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_AGENT="claude"
            export SPAWN_PROMPT_ID="prompt-123"
            export SPAWN_RUN_ID="marb-014520-002"
            export SPAWN_LOOP_NR="2"
            export SPAWN_SKILL_CODE="marb"
            cmd='printf "boom\\n" >&2; exit 23'
            failure_hook='python3 - <<'"'"'PY'"'"'
import json
from pathlib import Path
Path("{failure_seen}").write_text(Path("{meta}").read_text(encoding="utf-8"), encoding="utf-8")
PY'
            spawn_write_meta "{meta}" "launching" "claude" "marbles" "{tmp_path}" "{launcher}" "{report}" "{transcript}" "{launcher}"
            spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "$cmd" "" "" "$failure_hook"
            chmod +x "{launcher}"
            bash "{launcher}"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 23
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 23
    failure_payload = json.loads(failure_seen.read_text(encoding="utf-8"))
    assert failure_payload["status"] == "failed"
    assert failure_payload["exit_code"] == 23


def test_gc_marks_dead_launcher_pid_as_ghost(tmp_path: Path) -> None:
    meta = tmp_path / "dead.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="impl-010203-999"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "running" "codex" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        python3 - <<'PY'
import json
from pathlib import Path
path = Path("{meta}")
payload = json.loads(path.read_text(encoding="utf-8"))
payload["launcher_pid"] = 999999999
payload["liveness"] = "pid_alive"
path.write_text(json.dumps(payload), encoding="utf-8")
PY
        spawn_gc_dead_runs "{tmp_path}"
        '''
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "ghost"
    assert payload["liveness"] == "pid_dead"
    assert payload["ghost_reason"] == "launcher_pid dead at reap"


def test_gc_marks_live_meta_without_pid_as_unknown_schema(tmp_path: Path) -> None:
    meta = tmp_path / "older.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT="claude"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="impl-010203-998"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "running" "claude" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_gc_dead_runs "{tmp_path}"
        '''
    )

    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "running"
    assert payload["liveness"] == "unknown_legacy"
    assert payload["liveness_reason"] == "live status without launcher_pid"


def test_operator_intervention_is_run_scoped_auditable_jsonl(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "agent.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT="codex"
        export SPAWN_PROMPT_ID="prompt-123"
        export SPAWN_RUN_ID="impl-010203-997"
        export SPAWN_SKILL_CODE="impl"
        spawn_write_meta "{meta}" "running" "codex" "implement" "{tmp_path}" "{tmp_path / "plan.md"}" "{report}" "{transcript}" "{tmp_path / "launcher.sh"}"
        spawn_append_operator_intervention "{meta}" "Please narrow the next pass to liveness tests." "operator"
        '''
    )

    intervention_path = Path(result.stdout.strip().splitlines()[-1])
    payload = json.loads(meta.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in intervention_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert payload["intervention_path"] == str(intervention_path)
    assert payload["intervention_count"] == 1
    assert events[0]["schema"] == "vibecrafted.operator_intervention.v1"
    assert events[0]["run_id"] == "impl-010203-997"
    assert events[0]["consumer_contract"] == "compatible-watchers-and-bridges-only"
    transcript_text = transcript.read_text(encoding="utf-8")
    assert "operator intervention" in transcript_text
    assert "run_id=impl-010203-997" in transcript_text


def test_spawn_prepare_paths_generates_real_run_context_when_missing(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Prompt\n", encoding="utf-8")

    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{tmp_path / "home"}"
        export VIBECRAFTED_ROOT="{REPO_ROOT}"
            mkdir -p "$VIBECRAFTED_ROOT/"
        source "{COMMON_SH}"
        unset VIBECRAFTED_RUN_ID
        unset VIBECRAFTED_RUN_LOCK
        unset VIBECRAFTED_SKILL_CODE
        export VIBECRAFTED_LOOP_NR="0"
        spawn_prepare_paths claude "{prompt_file}" "{tmp_path}" "followup"
        printf 'RUN_ID=%s\\n' "$SPAWN_RUN_ID"
        printf 'SKILL_CODE=%s\\n' "$SPAWN_SKILL_CODE"
        printf 'RUN_LOCK=%s\\n' "$SPAWN_RUN_LOCK"
        '''
    )

    payload = dict(
        line.split("=", 1) for line in result.stdout.strip().splitlines() if "=" in line
    )
    assert re.fullmatch(r"fwup-\d{6}-\d{6}-\d{5}", payload["RUN_ID"])
    assert payload["SKILL_CODE"] == "fwup"
    lock_path = Path(payload["RUN_LOCK"])
    expected_lock = (
        tmp_path
        / "home"
        / ".vibecrafted"
        / "locks"
        / tmp_path.name
        / f"{payload['RUN_ID']}.lock"
    )
    assert lock_path == expected_lock
    assert "skill=followup" in lock_path.read_text(encoding="utf-8")
    assert result.stderr == ""


def test_spawn_in_operator_session_targets_named_session(tmp_path: Path) -> None:
    run_id = "marb-014520"
    # G7: host is workspace-bound, not the ambient operator session or the
    # bare repo card.
    project_root = tmp_path / "proj-foo"
    project_root.mkdir()
    host_session = _expected_worker_host_session(project_root)
    launcher = project_root / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-args.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host_session}"',
                "  exit 0",
                "fi",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VIBECRAFTED_RUN_ID="{run_id}"
        # Ambient human seat must not steal the worker host.
        export VIBECRAFTED_OPERATOR_SESSION="operator-seat"
        export SPAWN_ROOT="{project_root}"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "workflow"
        '''
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--session" in payload
    assert host_session in payload
    assert "operator-seat" not in payload
    assert "action" in payload
    # When spawning from outside a vc_frame context (no VC_FRAME/VC_FRAME_PANE_ID),
    # the routing guard forces a new-tab to avoid landing in a stale operator tab.
    assert "new-tab" in payload
    assert "--name" in payload
    assert run_id in payload


def test_spawn_in_operator_session_suppresses_vc_frame_tab_number_output(
    tmp_path: Path,
) -> None:
    run_id = "marb-014520"
    host_session = _expected_worker_host_session(tmp_path)
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-args.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host_session}"',
                "  exit 0",
                "fi",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
                'printf "7\\n"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VIBECRAFTED_RUN_ID="{run_id}"
        export SPAWN_ROOT="{tmp_path}"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "workflow"
        ''',
    )

    assert result.stdout == ""


def test_spawn_in_vc_frame_pane_marbles_tab_suppresses_tab_number_output(
    tmp_path: Path,
) -> None:
    run_id = "marb-014520"
    operator_session = _expected_operator_session(run_id)
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{operator_session}"',
                "  exit 0",
                "fi",
                "{",
                '  printf -- "--CALL--\\n"',
                '  printf "%s\\n" "$@"',
                '} >> "$CAPTURE_FILE"',
                'if [[ "${1:-}" == "action" && "${2:-}" == "list-tabs" ]]; then',
                '  printf \'[{"name":"operator-tab","tab_id":2},{"name":"marbles","tab_id":7}]\\n\'',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "action" && "${2:-}" == "new-pane" ]]; then',
                '  printf "terminal_13\\n"',
                "  exit 0",
                "fi",
                'printf "12\\n"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            export PATH="{fake_bin}:$PATH"
            export CAPTURE_FILE="{capture_file}"
            export VC_FRAME=1
            export VC_FRAME_PANE_ID=terminal_1
            export VC_FRAME_SESSION_NAME="{operator_session}"
            export ZELLIJ_SESSION_NAME="{operator_session}"
            export VC_FRAME_TAB_NAME="operator-tab"
            export VIBECRAFTED_RUN_ID="{run_id}"
            export VIBECRAFTED_OPERATOR_SESSION="{operator_session}"
            export VIBECRAFTED_WORKER_SESSION="{operator_session}"
            export VIBECRAFTED_MARBLES_TAB_NAME="marbles"
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_LOOP_NR=1
            source "{COMMON_SH}"
            spawn_in_vc_frame_pane "{launcher}" "workflow"
            ''',
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""
    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert calls[0][:3] == ["action", "list-tabs", "--json"]
    assert calls[1][:2] == ["action", "new-pane"]
    assert "--tab-id" in calls[1]
    assert "7" in calls[1]
    assert "--stacked" in calls[1]
    assert "--close-on-exit" in calls[1]
    assert not any("go-to-tab-name" in call for call in calls)


def test_spawn_in_vc_frame_pane_marbles_tab_can_keep_agent_panes_for_forensics(
    tmp_path: Path,
) -> None:
    run_id = "marb-014520"
    operator_session = _expected_operator_session(run_id)
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{operator_session}"',
                "  exit 0",
                "fi",
                "{",
                '  printf -- "--CALL--\\n"',
                '  printf "%s\\n" "$@"',
                '} >> "$CAPTURE_FILE"',
                'if [[ "${1:-}" == "action" && "${2:-}" == "list-tabs" ]]; then',
                '  printf \'[{"name":"marbles","tab_id":7}]\\n\'',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "action" && "${2:-}" == "new-pane" ]]; then',
                '  printf "terminal_13\\n"',
                "  exit 0",
                "fi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            export PATH="{fake_bin}:$PATH"
            export CAPTURE_FILE="{capture_file}"
            export VC_FRAME=1
            export VC_FRAME_PANE_ID=terminal_1
            export VC_FRAME_SESSION_NAME="{operator_session}"
            export ZELLIJ_SESSION_NAME="{operator_session}"
            export VIBECRAFTED_RUN_ID="{run_id}"
            export VIBECRAFTED_OPERATOR_SESSION="{operator_session}"
            export VIBECRAFTED_WORKER_SESSION="{operator_session}"
            export VIBECRAFTED_MARBLES_TAB_NAME="marbles"
            export VIBECRAFTED_VC_FRAME_KEEP_AGENT_PANES=1
            export SPAWN_ROOT="{tmp_path}"
            export SPAWN_LOOP_NR=1
            source "{COMMON_SH}"
            spawn_in_vc_frame_pane "{launcher}" "workflow"
            ''',
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    assert calls[1][:2] == ["action", "new-pane"]
    assert "--stacked" in calls[1]
    assert "--close-on-exit" not in calls[1]


def test_spawn_probe_uses_active_tab_and_restores_focus(tmp_path: Path) -> None:
    transcript = tmp_path / "trace.log"
    transcript.write_text("hello\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n{\n  printf -- "--CALL--\\n"\n  printf "%s\\n" "$@"\n} >> "$CAPTURE_FILE"\nif [[ "${1:-}" == "action" && "${2:-}" == "current-tab-info" ]]; then\n  printf \'{"name":"operator-tab","tab_id":9}\\n\'\n  exit 0\nfi\nif [[ "${1:-}" == "action" && "${2:-}" == "list-panes" ]]; then\n  printf \'[{"pane_id":"terminal_42","is_focused":true}]\\n\'\n  exit 0\nfi\nif [[ "${1:-}" == "action" && "${2:-}" == "new-pane" ]]; then\n  printf "terminal_99\\n"\n  exit 0\nfi'
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'''
            set -euo pipefail
            export PATH="{fake_bin}:$PATH"
            export CAPTURE_FILE="{capture_file}"
            export VC_FRAME=1
            export VC_FRAME_PANE_ID=terminal_1
            export VC_FRAME_SESSION_NAME="operator-session"
            export ZELLIJ_SESSION_NAME="operator-session"
            export VC_FRAME_TAB_NAME="operator-tab"
            export SPAWN_AGENT="gemini"
            export VIBECRAFTED_SPAWN_PROBE_SECONDS=1
            export VIBECRAFTED_SPAWN_PROBE_DELAY_SECONDS=0
            source "{COMMON_SH}"
            spawn_probe "{transcript}"
            sleep 0.2
            ''',
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.stdout == ""
    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    assert any(call[:3] == ["action", "current-tab-info", "--json"] for call in calls)
    assert any(
        call[:4] == ["action", "list-panes", "--json", "--state"] for call in calls
    )
    probe_calls = [call for call in calls if call[:2] == ["action", "new-pane"]]
    assert len(probe_calls) == 1
    probe_call = probe_calls[0]
    assert "--floating" in probe_call
    assert "--tab-id" in probe_call
    assert "9" in probe_call
    assert "--name" in probe_call
    assert any("probe-gemini" in part for part in probe_call)
    assert any(call[:3] == ["action", "focus-pane-id", "terminal_42"] for call in calls)


def test_spawn_await_watch_uses_active_meta_floating_pane_and_restores_focus(
    tmp_path: Path,
) -> None:
    run_id = "just-104043-8314"
    meta = tmp_path / "run.meta.json"
    transcript = tmp_path / "run.transcript.log"
    transcript.write_text("", encoding="utf-8")
    meta.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "running",
                "agent": "codex",
                "mode": "justdo",
                "transcript": str(transcript),
                "launcher_pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n{\n  printf -- "--CALL--\\n"\n  printf "%s\\n" "$@"\n} >> "$CAPTURE_FILE"\nif [[ "${1:-}" == "action" && "${2:-}" == "list-panes" ]]; then\n  printf \'[{"pane_id":"terminal_42","is_focused":true}]\\n\'\n  exit 0\nfi\nif [[ "${1:-}" == "action" && "${2:-}" == "new-pane" ]]; then\n  printf "terminal_99\\n"\n  exit 0\nfi'
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    jq = fake_bin / "jq"
    jq.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "${1:-}" == "-r" ]]; then filter="${2:-}"; file="${3:-}"; else filter="${1:-}"; file="${2:-}"; fi\npython3 - "$filter" "$file" <<\'PY\'\nimport json, sys\nkey = sys.argv[1].split()[0].lstrip(\'.\')\nwith open(sys.argv[2], \'r\', encoding=\'utf-8\') as fh:\n    payload = json.load(fh)\nvalue = payload.get(key, \'\')\nprint(\'\' if value is None else value)\nPY'
        + "\n",
        encoding="utf-8",
    )
    jq.chmod(0o755)

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VC_FRAME=1
        export VC_FRAME_PANE_ID=terminal_1
        export SPAWN_RUN_ID="{run_id}"
        export SPAWN_AGENT="codex"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_META="{meta}"
        source "{COMMON_SH}"
        spawn_await_watch_pane "7" "{run_id}" "worker"
        '''
    )

    assert result.stdout == ""
    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    assert any(
        call[:4] == ["action", "list-panes", "--json", "--state"] for call in calls
    )
    await_calls = [call for call in calls if call[:2] == ["action", "new-pane"]]
    assert len(await_calls) == 1
    await_call = await_calls[0]
    assert "--tab-id" in await_call
    assert "7" in await_call
    assert "--floating" in await_call
    assert "--stacked" not in await_call
    assert "--name" in await_call
    assert "await:codex:8314" in await_call
    assert "--meta" in await_call
    assert str(meta) in await_call
    assert "--run-id" not in await_call
    assert any(call[:3] == ["action", "focus-pane-id", "terminal_42"] for call in calls)


def test_spawn_probe_watch_does_not_fail_live_worker_on_transient_error(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "trace.log"
    transcript.write_text(
        "2026-05-26T04:44:37Z ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed\n[22:44:37] session: 019e6299-554a-76b2-900d-6dde67314658\nI will use the VC Workflow skill and continue."
        + "\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    notify_capture = tmp_path / "notifications.txt"
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "vc-mux-tray").write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$NOTIFY_CAPTURE"\n',
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "vc-mux-tray").chmod(0o755)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export NOTIFY_CAPTURE="{notify_capture}"
        source "{COMMON_SH}"
        spawn_probe_watch "{transcript}" 1 codex wflw-224433-38831
        '''
    )

    assert not notify_capture.exists()


def test_spawn_probe_watch_reports_transient_error_as_warning_not_failure(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "trace.log"
    transcript.write_text(
        "2026-05-26T04:44:37Z ERROR rmcp::transport::worker: request failed\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    notify_capture = tmp_path / "notifications.txt"
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "vc-mux-tray").write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$NOTIFY_CAPTURE"\n',
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "vc-mux-tray").chmod(0o755)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export NOTIFY_CAPTURE="{notify_capture}"
        source "{COMMON_SH}"
        spawn_probe_watch "{transcript}" 1 codex wflw-224433-38831
        '''
    )

    notification = notify_capture.read_text(encoding="utf-8")
    assert "notify" in notification
    assert "--title" in notification
    assert "--message" in notification
    assert "Worker startup warning" in notification
    assert "Worker FAILED" not in notification


def test_spawn_probe_notify_does_not_fallback_to_osascript_on_macos(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    notify_capture = tmp_path / "notifications.txt"
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "osascript").write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$NOTIFY_CAPTURE"\n',
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "osascript").chmod(0o755)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export NOTIFY_CAPTURE="{notify_capture}"
        source "{COMMON_SH}"
        spawn_probe_notify "Worker silent on startup" "gemini:96923 - check logs"
        '''
    )

    assert not notify_capture.exists()


def test_spawn_in_operator_session_new_tab_uses_run_tab_without_startup_monitor(
    tmp_path: Path,
) -> None:
    run_id = "rsch-014520"
    host_session = _expected_worker_host_session(tmp_path)
    expected_tmp_root = tmp_path / ".vibecrafted" / "tmp"
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    meta = tmp_path / "meta.json"
    transcript = tmp_path / "trace.log"
    report = tmp_path / "report.md"
    meta.write_text("{}", encoding="utf-8")
    transcript.write_text("", encoding="utf-8")
    report.write_text("", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host_session}"',
                "  exit 0",
                "fi",
                "{",
                '  printf -- "--CALL--\\n"',
                '  printf "%s\\n" "$@"',
                '} >> "$CAPTURE_FILE"',
                'if [[ "$*" == "action new-tab --help" ]]; then',
                "  printf '%s\\n' 'Usage: vc-frame action new-tab [--after-base] [--no-focus]'",
                "  exit 0",
                "fi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VIBECRAFTED_RUN_ID="{run_id}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_META="{meta}"
        export SPAWN_TRANSCRIPT="{transcript}"
        export SPAWN_REPORT="{report}"
        export SPAWN_SKILL_NAME="research"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "workflow"
        '''
    )

    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    # G3 after-base probe may insert `action new-tab --help` between list-tabs
    # and the real new-tab; filter help probes so the contract stays stable.
    material = [
        c
        for c in calls
        if not (
            len(c) >= 3 and c[0] == "action" and c[1] == "new-tab" and "--help" in c
        )
    ]
    assert len(material) == 2

    list_tabs_call, workflow_call = material
    assert list_tabs_call[:5] == [
        "--session",
        host_session,
        "action",
        "list-tabs",
        "--json",
    ]
    assert workflow_call[:4] == ["--session", host_session, "action", "new-tab"]
    assert "--after-base" in workflow_call
    assert "--no-focus" in workflow_call
    assert "--name" in workflow_call
    assert run_id in workflow_call
    assert "workflow" not in workflow_call[workflow_call.index("--name") + 1]
    assert not any("startup-monitor" in arg for call in material for arg in call)

    workflow_script = Path(workflow_call[workflow_call.index("--") + 1])
    assert workflow_script.parent == expected_tmp_root
    workflow_cmd = workflow_script.read_text(encoding="utf-8")
    assert "VIBECRAFTED_INLINE_STARTUP_WATCH=0" not in workflow_cmd
    assert str(launcher) in workflow_cmd


def test_spawn_in_operator_session_existing_run_tab_stacks_and_restores_focus(
    tmp_path: Path,
) -> None:
    run_id = "ownr-014520"
    host_session = _expected_worker_host_session(tmp_path)
    expected_tmp_root = tmp_path / ".vibecrafted" / "tmp"
    launcher = tmp_path / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture_file = tmp_path / "vc_frame-calls.txt"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host_session}"',
                "  exit 0",
                "fi",
                "{",
                '  printf -- "--CALL--\\n"',
                '  printf "%s\\n" "$@"',
                '} >> "$CAPTURE_FILE"',
                'if [[ "${1:-}" == "--session" && "${3:-}" == "action" && "${4:-}" == "list-tabs" ]]; then',
                f'  printf \'[{{"name":"operator","tab_id":2}},{{"name":"{run_id}","tab_id":7}}]\\n\'',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "--session" && "${3:-}" == "action" && "${4:-}" == "current-tab-info" ]]; then',
                "  printf '{\"tab_id\":2}\\n'",
                "  exit 0",
                "fi",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export CAPTURE_FILE="{capture_file}"
        export VIBECRAFTED_RUN_ID="{run_id}"
        export SPAWN_ROOT="{tmp_path}"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "ownership-codex"
        '''
    )

    calls = _split_vc_frame_calls(capture_file.read_text(encoding="utf-8"))
    assert len(calls) == 4
    assert calls[0][:5] == [
        "--session",
        host_session,
        "action",
        "list-tabs",
        "--json",
    ]
    assert calls[1][:5] == [
        "--session",
        host_session,
        "action",
        "current-tab-info",
        "--json",
    ]

    pane_call = calls[2]
    assert pane_call[:4] == ["--session", host_session, "action", "new-pane"]
    assert "--tab-id" in pane_call
    assert "7" in pane_call
    assert "--stacked" in pane_call
    assert "--close-on-exit" in pane_call
    assert "--name" in pane_call
    assert "ownership-codex" in pane_call
    assert not any(
        call[:4] == ["--session", host_session, "action", "new-tab"] for call in calls
    )
    assert not any("startup-monitor" in arg for call in calls for arg in call)

    workflow_script = Path(pane_call[pane_call.index("--") + 1])
    assert workflow_script.parent == expected_tmp_root
    workflow_cmd = workflow_script.read_text(encoding="utf-8")
    assert "VIBECRAFTED_INLINE_STARTUP_WATCH=0" not in workflow_cmd
    assert str(launcher) in workflow_cmd

    assert calls[3][:5] == [
        "--session",
        host_session,
        "action",
        "go-to-tab-by-id",
        "2",
    ]


def test_vc_frame_launch_slot_serializes_parallel_spawns(tmp_path: Path) -> None:
    lock_root = tmp_path / "locks"
    done_file = tmp_path / "done"

    result = _bash(
        f'''
        set -euo pipefail
        export TMPDIR="{lock_root}"
        export VIBECRAFTED_SPAWN_STAGGER_SECONDS=0.2
        source "{COMMON_SH}"
        (
          lock="$(spawn_acquire_vc_frame_launch_slot session-a)"
          printf 'first-acquired\n' >> "{done_file}"
          sleep 0.4
          spawn_release_vc_frame_launch_slot "$lock"
        ) &
        first_pid=$!
        sleep 0.05
        start=$(python3 - <<'PY'
import time
print(time.time())
PY
)
        lock="$(spawn_acquire_vc_frame_launch_slot session-a)"
        end=$(python3 - <<'PY'
import time
print(time.time())
PY
)
        spawn_release_vc_frame_launch_slot "$lock"
        wait "$first_pid"
        python3 - "$start" "$end" <<'PY'
import sys
start = float(sys.argv[1])
end = float(sys.argv[2])
if end - start < 0.25:
    raise SystemExit(f"slot was not serialized long enough: {{end - start:.3f}}s")
PY
        '''
    )

    assert result.stderr == ""
    assert done_file.read_text(encoding="utf-8") == "first-acquired\n"


def test_spawn_vc_frame_session_action_resurrects_missing_host_once(
    tmp_path: Path,
) -> None:
    """G3: first action Session-not-found → one create-background → retry ok."""
    host = "host-resurrect"
    state = tmp_path / "state"
    state.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                '  if [[ -f "$STATE/live" ]]; then printf "%s [Created]\\n" "host-resurrect"; fi',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" && "${2:-}" == "--create-background" ]]; then',
                '  touch "$STATE/live"',
                '  printf "create-ok\\n" >> "$STATE/create"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "--session" ]]; then',
                '  if [[ ! -f "$STATE/live" ]]; then',
                '    printf "Session \'%s\' not found\\n" "${2:-}" >&2',
                "    # Intentionally exit 0: real binary has been observed doing this.",
                "    exit 0",
                "  fi",
                '  printf "action-ok\\n" >> "$STATE/action"',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        source "{COMMON_SH}"
        spawn_vc_frame_session_action vc-frame "{host}" action new-tab --name t --cwd "{tmp_path}" -- /bin/true
        printf 'status=%s\\n' "$?"
        printf 'create=%s\\n' "$(cat "{state}/create" 2>/dev/null || true)"
        printf 'action=%s\\n' "$(cat "{state}/action" 2>/dev/null || true)"
        '''
    )

    assert "status=0" in result.stdout
    assert "create-ok" in result.stdout
    assert "action-ok" in result.stdout
    calls = (state / "calls").read_text(encoding="utf-8")
    assert calls.count("attach") == 1
    assert "--create-background" in calls
    # Two action invocations (first miss, second after resurrect).
    assert calls.count("new-tab") == 2


def test_spawn_vc_frame_session_action_double_fail_is_loud(tmp_path: Path) -> None:
    """G3: create-background also fails → return 2 + SPAWN_VC_FRAME_LAST_ERROR."""
    host = "ghost-host"
    state = tmp_path / "state"
    state.mkdir()
    meta = tmp_path / "meta.json"
    meta.write_text(
        json.dumps({"status": "launching", "run_id": "g3-double-fail"}),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then exit 0; fi',
                'if [[ "${1:-}" == "attach" ]]; then',
                '  printf "attach boom\\n" >&2',
                "  exit 1",
                "fi",
                'if [[ "${1:-}" == "--session" ]]; then',
                '  printf "Session \'%s\' not found\\n" "${2:-}" >&2',
                "  exit 1",
                "fi",
                "exit 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            _ENV_SANITIZE
            + f'''
            set +e
            export PATH="{fake_bin}:$PATH"
            export SPAWN_META="{meta}"
            source "{COMMON_SH}"
            spawn_vc_frame_session_action vc-frame "{host}" action new-tab --name t --cwd "{tmp_path}" -- /bin/true
            status=$?
            printf 'status=%s\\n' "$status"
            printf 'last_error=%s\\n' "${{SPAWN_VC_FRAME_LAST_ERROR:-}}"
            if [[ "$status" -eq 2 ]]; then
              spawn_record_host_session_failure
            fi
            exit 0
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "status=2" in result.stdout
    assert "not found" in result.stdout.lower() or "not found" in result.stderr.lower()
    meta_payload = json.loads(meta.read_text(encoding="utf-8"))
    assert meta_payload.get("status") == "failed"
    assert (
        "not found" in str(meta_payload.get("last_error") or "").lower()
        or "create-background" in str(meta_payload.get("last_error") or "").lower()
    )


def test_spawn_vc_frame_session_action_happy_path_no_create_background(
    tmp_path: Path,
) -> None:
    """G3: live session — zero create-background, single action."""
    host = "live-host"
    state = tmp_path / "state"
    state.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host}"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" ]]; then',
                '  printf "UNEXPECTED_CREATE\\n" >> "$STATE/create"',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        source "{COMMON_SH}"
        spawn_vc_frame_session_action vc-frame "{host}" action new-tab --name t --cwd "{tmp_path}" -- /bin/true
        '''
    )

    calls = (state / "calls").read_text(encoding="utf-8")
    assert "attach" not in calls
    assert "--create-background" not in calls
    assert calls.count("new-tab") == 1
    assert not (state / "create").exists()


def test_spawn_vc_frame_session_action_ack_timeout_presence_is_success(
    tmp_path: Path,
) -> None:
    """G3b: NewTab ACK timeout but tab already listed → success, no retry."""
    host = "ack-host"
    tab = "worker-run-ack"
    state = tmp_path / "state"
    state.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                f'TAB="{tab}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host}"',
                "  exit 0",
                "fi",
                # Presence probe path: list-tabs --json after ACK fail.
                'if [[ "$*" == *list-tabs* ]]; then',
                '  printf \'[{"name":"%s","tab_id":7}]\\n\' "$TAB"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" ]]; then',
                '  printf "UNEXPECTED_CREATE\\n" >> "$STATE/create"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "--session" ]]; then',
                "  # First (and only) new-tab: ambiguous ACK. Presence probe succeeds.",
                "  printf \"action 'NewTab' did not acknowledge completion within 25s\\n\" >&2",
                "  exit 1",
                "fi",
                "exit 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        source "{COMMON_SH}"
        spawn_vc_frame_session_action vc-frame "{host}" action new-tab --name "{tab}" --cwd "{tmp_path}" -- /bin/true
        printf 'status=%s\\n' "$?"
        '''
    )

    assert "status=0" in result.stdout
    assert "treating as success" in result.stderr
    calls = (state / "calls").read_text(encoding="utf-8")
    # One new-tab only — presence probe must not trigger a second create.
    assert calls.count("new-tab") == 1
    assert "list-tabs" in calls
    assert not (state / "create").exists()


def test_spawn_vc_frame_session_action_ack_timeout_retries_once(
    tmp_path: Path,
) -> None:
    """G3b: ACK timeout + tab absent → one retry succeeds."""
    host = "ack-retry-host"
    tab = "worker-run-retry"
    state = tmp_path / "state"
    state.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                f'  printf "%s [Created]\\n" "{host}"',
                "  exit 0",
                "fi",
                'if [[ "$*" == *list-tabs* ]]; then',
                # Empty inventory until the successful retry lands.
                '  if [[ -f "$STATE/action" ]]; then',
                f'    printf \'[{{"name":"{tab}","tab_id":9}}]\\n\'',
                "  else",
                "    printf '[]\\n'",
                "  fi",
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" ]]; then',
                '  printf "UNEXPECTED_CREATE\\n" >> "$STATE/create"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "--session" ]]; then',
                '  count="$(wc -l < "$STATE/newtab" 2>/dev/null || echo 0)"',
                '  printf "x\\n" >> "$STATE/newtab"',
                '  if [[ "${count// /}" -lt 1 ]]; then',
                "    printf \"action 'NewTab' did not acknowledge completion within 25s\\n\" >&2",
                "    exit 1",
                "  fi",
                '  printf "action-ok\\n" >> "$STATE/action"',
                "  exit 0",
                "fi",
                "exit 1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        source "{COMMON_SH}"
        spawn_vc_frame_session_action vc-frame "{host}" action new-tab --name "{tab}" --cwd "{tmp_path}" -- /bin/true
        printf 'status=%s\\n' "$?"
        printf 'action=%s\\n' "$(cat "{state}/action" 2>/dev/null || true)"
        '''
    )

    assert "status=0" in result.stdout
    assert "action-ok" in result.stdout
    assert "one retry after brief backoff" in result.stderr
    calls = (state / "calls").read_text(encoding="utf-8")
    assert calls.count("new-tab") == 2
    assert not (state / "create").exists()


# ---------------------------------------------------------------------------
# G7 — worker tabs host in per-project sessions, never the operator seat
# ---------------------------------------------------------------------------


def _g7_fake_vc_frame(
    tmp_path: Path, *, live_sessions: list[str] | None = None
) -> tuple[Path, Path, Path]:
    """Stub vc-frame that logs argv and optional create-background."""
    state = tmp_path / "g7-state"
    state.mkdir(exist_ok=True)
    (state / "live").write_text(
        "\n".join(live_sessions or []) + ("\n" if live_sessions else ""),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    capture = state / "calls"
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" || "${1:-}" == "ls" ]]; then',
                '  if [[ -f "$STATE/live" ]]; then cat "$STATE/live"; fi',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" && "${2:-}" == "--create-background" ]]; then',
                '  printf "%s [Created]\\n" "${3:-}" >> "$STATE/live"',
                '  printf "create:%s\\n" "${3:-}" >> "$STATE/create"',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "--session" ]]; then',
                '  sess="${2:-}"',
                '  if ! grep -qxF "$sess [Created]" "$STATE/live" 2>/dev/null \\',
                '     && ! grep -qxF "$sess" "$STATE/live" 2>/dev/null; then',
                "    # Accept bare name lines too.",
                '    if ! awk -v s="$sess" \'$1 == s { found=1 } END { exit found ? 0 : 1 }\' "$STATE/live" 2>/dev/null; then',
                '      printf "Session \'%s\' not found\\n" "$sess" >&2',
                "      exit 0",
                "    fi",
                "  fi",
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)
    _mirror_fake_vc_frame(vc_frame)
    return fake_bin, state, capture


def _g7_session_args(calls: str) -> list[str]:
    """Every value passed as `--session <value>` to the vc-frame stub."""
    lines = calls.splitlines()
    return [
        lines[i + 1]
        for i, line in enumerate(lines)
        if line == "--session" and i + 1 < len(lines)
    ]


def test_g7_worker_tab_never_lands_in_operator_session(tmp_path: Path) -> None:
    """Dispatch from seat X for repo foo → workspace host, never bare foo.

    2026-08-09 regression: the host suffix used to be conditional on the seat
    name colliding with the repo basename, so a dispatch fired from any
    differently-named seat resolved to bare ``foo`` — which is the operator's
    own interactive card in the rail. The suffix is now unconditional.
    """
    project = tmp_path / "foo"
    project.mkdir()
    launcher = project / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    operator_seat = "operator-X"
    host = _expected_worker_host_session(project)
    fake_bin, state, _capture = _g7_fake_vc_frame(
        tmp_path,
        live_sessions=[
            f"{operator_seat} [Created]",
            "foo [Created]",
            f"{host} [Created]",
        ],
    )

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export VC_FRAME=1
        export VC_FRAME_PANE_ID=pane-1
        export VC_FRAME_SESSION_NAME="{operator_seat}"
        export SPAWN_ROOT="{project}"
        export VIBECRAFTED_RUN_ID="impl-g7-001"
        export VIBECRAFTED_OPERATOR_SESSION="{operator_seat}"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "worker"
        printf 'resolved=%s\\n' "$(spawn_effective_operator_session)"
        '''
    )

    assert f"resolved={host}" in result.stdout
    session_args = _g7_session_args((state / "calls").read_text(encoding="utf-8"))
    assert host in session_args
    # Neither the operator's seat nor the bare repo card is ever a target.
    assert operator_seat not in session_args
    assert "foo" not in session_args


def test_g7_missing_project_session_create_background_then_tab(tmp_path: Path) -> None:
    """Missing workspace host → one create-background + tab; live → none."""
    project = tmp_path / "foo"
    project.mkdir()
    launcher = project / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    host = _expected_worker_host_session(project)

    # Missing host first.
    fake_bin, state, _ = _g7_fake_vc_frame(tmp_path, live_sessions=[])
    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export SPAWN_ROOT="{project}"
        export VIBECRAFTED_RUN_ID="impl-g7-miss"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "worker"
        '''
    )
    create = (state / "create").read_text(encoding="utf-8")
    calls = (state / "calls").read_text(encoding="utf-8")
    assert f"create:{host}" in create
    assert "--create-background" in calls
    # Real actions only (exclude `action new-tab --help` capability probe).
    material_new_tabs = [
        c
        for c in _split_vc_frame_calls(calls)
        if len(c) >= 2 and "new-tab" in c and "--help" not in c
    ]
    assert len(material_new_tabs) == 2  # miss + after resurrect

    # Happy path: host already live → zero create-background.
    state2 = tmp_path / "g7-live"
    state2.mkdir()
    project2 = state2 / "foo"
    project2.mkdir()
    host2 = _expected_worker_host_session(project2)
    fake_bin2, state_live, _ = _g7_fake_vc_frame(
        state2, live_sessions=[f"{host2} [Created]"]
    )
    launcher2 = project2 / "launch.sh"
    launcher2.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher2.chmod(0o755)
    _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin2}:$PATH"
        export SPAWN_ROOT="{launcher2.parent}"
        export VIBECRAFTED_RUN_ID="impl-g7-live"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher2}" "worker"
        '''
    )
    live_calls = (state_live / "calls").read_text(encoding="utf-8")
    assert "--create-background" not in live_calls
    live_new_tabs = [
        c
        for c in _split_vc_frame_calls(live_calls)
        if len(c) >= 2 and "new-tab" in c and "--help" not in c
    ]
    assert len(live_new_tabs) == 1


def test_g7_worker_session_env_override(tmp_path: Path) -> None:
    """VIBECRAFTED_WORKER_SESSION=bar → tab in bar regardless of repo."""
    project = tmp_path / "foo"
    project.mkdir()
    launcher = project / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    fake_bin, state, _ = _g7_fake_vc_frame(tmp_path, live_sessions=["bar [Created]"])

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export SPAWN_ROOT="{project}"
        export VC_FRAME_SESSION_NAME="operator-X"
        export VIBECRAFTED_WORKER_SESSION="bar"
        export VIBECRAFTED_RUN_ID="impl-g7-ov"
        source "{COMMON_SH}"
        printf 'host=%s\\n' "$(spawn_effective_operator_session)"
        spawn_in_operator_session "{launcher}" "worker"
        '''
    )
    assert "host=bar" in result.stdout
    sessions = _g7_session_args((state / "calls").read_text(encoding="utf-8"))
    assert "bar" in sessions
    assert "foo" not in sessions
    assert "foo-workers" not in sessions


def test_g7_seat_named_like_repo_uses_the_same_workers_host(tmp_path: Path) -> None:
    """Dispatch from seat foo for repo foo → workspace host, not bare foo.

    Once the suffix became unconditional this stopped being a special case; the
    test stays as the seat==repo corner of the same single rule.
    """
    project = tmp_path / "foo"
    project.mkdir()
    launcher = project / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    host = _expected_worker_host_session(project)
    fake_bin, state, _ = _g7_fake_vc_frame(
        tmp_path, live_sessions=[f"{host} [Created]", "foo [Created]"]
    )

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export VC_FRAME=1
        export VC_FRAME_PANE_ID=pane-1
        export VC_FRAME_SESSION_NAME="foo"
        export SPAWN_ROOT="{project}"
        export VIBECRAFTED_RUN_ID="impl-g7-col"
        source "{COMMON_SH}"
        printf 'host=%s\\n' "$(spawn_effective_operator_session)"
        spawn_in_operator_session "{launcher}" "worker"
        '''
    )
    assert f"host={host}" in result.stdout
    calls = (state / "calls").read_text(encoding="utf-8")
    assert host in calls
    # Worker action must not target the bare operator seat name as host.
    session_args = _g7_session_args(calls)
    assert host in session_args
    assert session_args.count("foo") == 0


def test_g7_receipt_operator_session_is_worker_host(tmp_path: Path) -> None:
    """After resolve, VIBECRAFTED_OPERATOR_SESSION export = actual worker host."""
    project = tmp_path / "proj-bar"
    project.mkdir()
    launcher = project / "launch.sh"
    launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    host = _expected_worker_host_session(project)
    fake_bin, _state, _ = _g7_fake_vc_frame(
        tmp_path, live_sessions=[f"{host} [Created]"]
    )

    result = _bash(
        f'''
        set -euo pipefail
        export PATH="{fake_bin}:$PATH"
        export VC_FRAME_SESSION_NAME="vc-workspace"
        export SPAWN_ROOT="{project}"
        export VIBECRAFTED_OPERATOR_SESSION="vc-workspace"
        export VIBECRAFTED_RUN_ID="impl-g7-rcpt"
        source "{COMMON_SH}"
        spawn_in_operator_session "{launcher}" "worker"
        printf 'receipt=%s\\n' "${{VIBECRAFTED_OPERATOR_SESSION}}"
        '''
    )
    assert f"receipt={host}" in result.stdout


def _reserve_run_id(skill: str) -> str:
    """Mint a canonical run id through the Python allocator (parity oracle)."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]);"
                " from vibecrafted_core.workflow import reserve_run_id;"
                " print(reserve_run_id(sys.argv[2]))"
            ),
            str(CORE_PACKAGE_DIR),
            skill,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_shell_run_id_allocators_share_canonical_grammar() -> None:
    """Both shell run-id allocators mint the same grammar as reserve_run_id.

    Regression for the scaffold run_id split (P1): the shell fallback minted
    ``prefix-HHMMSS-<pid><entropy>`` (no date, 10-digit tail) while the Python
    dispatcher minted ``prefix-YYMMDD-HHMMSS-entropy``. The divergent shapes
    could not be reconciled by observe/await/settlement, orphaning a phantom
    control-plane record beside the live run. One grammar, one identity space.
    """
    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        source "{CORE_RUNTIME_HELPER}"
        printf 'spawn=%s\\n' "$(spawn_generate_run_id scaf)"
        printf 'core=%s\\n' "$(_vetcoders_generate_run_id scaf)"
        '''
    )
    lines = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    spawn_id = lines["spawn"]
    core_id = lines["core"]

    canonical = re.compile(r"scaf-\d{6}-\d{6}-\d{5}")
    assert canonical.fullmatch(spawn_id), f"spawn_generate_run_id shape: {spawn_id!r}"
    assert canonical.fullmatch(core_id), (
        f"_vetcoders_generate_run_id shape: {core_id!r}"
    )

    # Both shell allocators agree with the Python allocator that owns the durable
    # dispatcher identity — three-way grammar parity, no HHMMSS-vs-YYMMDD split.
    python_id = _reserve_run_id("scaffold")
    assert canonical.fullmatch(python_id), f"reserve_run_id shape: {python_id!r}"

    # Old shape leaked a concatenated <pid><entropy> tail (3 segments, ~10 digits);
    # the canonical id is exactly 4 dash-segments with a 5-digit entropy tail.
    for run_id in (spawn_id, core_id, python_id):
        segments = run_id.split("-")
        assert len(segments) == 4, run_id
        assert len(segments[-1]) == 5 and segments[-1].isdigit(), run_id


# ── Top-level entrypoints own their interpreter ───────────────────────────
#
# The library above is closed, but the scripts a Founder actually types were
# still executing bare `python3` in their own heredocs.  These probes drive the
# real entrypoints, not their source text.

RUNTIME_SCRIPTS_DIR = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"
)
CODEX_LAUNCH_PREFIX = "Dry run mode: launcher generated only: "


def _generation_bin_carrying_core(tmp_path: Path) -> Path:
    """A generation bin whose ``python3`` can import ``vibecrafted_core``.

    A real release bin ships the package alongside its own interpreter, so the
    module entrypoints (``python3 -m vibecrafted_core.…``) only prove something
    if the stand-in carries it too.  The directory name holds a space on
    purpose: the resolved path travels through command substitution and, for
    the codex bridge, into a generated command line.
    """

    generation_bin = tmp_path / "gene ration" / "bin"
    generation_bin.mkdir(parents=True, exist_ok=True)
    interpreter = generation_bin / "python3"
    interpreter.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={shlex.quote(str(CORE_PACKAGE_DIR))} "
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    return generation_bin


def _entrypoint_env(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    """Hostile public ``python3``, valid runtime interpreter, isolated home.

    PYTHONPATH is absent rather than emptied: the runtime exports a global one
    on installed hosts, and inheriting it would let the host answer for the
    package the entrypoint is supposed to reach through its own interpreter.
    """

    home = tmp_path / "home with space"
    home.mkdir(parents=True, exist_ok=True)
    hostile_bin = tmp_path / "hostile-bin"
    _write_hostile_python(hostile_bin)
    generation_bin = _generation_bin_carrying_core(tmp_path)

    env = {
        "PATH": f"{hostile_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "VIBECRAFTED_PYTHON": str(generation_bin / "python3"),
        "VIBECRAFTED_RUNTIME_BIN": str(generation_bin),
    }
    return env, hostile_bin, generation_bin


def _run_entrypoint(
    script: str,
    args: list[str],
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(RUNTIME_SCRIPTS_DIR / script), *args],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


_ENTRYPOINT_CASES = [
    # The independently proven failure: await.sh sources common.sh and then
    # execs a bare python3 heredoc, so it exited 79 on a hostile host.
    pytest.param("await.sh", ["--help"], id="await-help"),
    pytest.param("observe.sh", ["--help"], id="observe-help"),
    pytest.param(
        "marbles_ctl.sh", ["session", "--json"], id="marbles-ctl-session-json"
    ),
    pytest.param("marbles_ctl.sh", ["gc", "--dry-run"], id="marbles-ctl-gc-dry-run"),
    pytest.param("marbles_spawn.sh", ["--help"], id="marbles-spawn-help"),
    pytest.param("codex_spawn.sh", ["--help"], id="codex-spawn-help"),
    # Module entrypoints: no sourced library at all before this cut.
    pytest.param("vibecrafted-cron.sh", ["--help"], id="cron-help"),
    pytest.param("vibecrafted-loop.sh", ["--help"], id="loop-help"),
    pytest.param("vibecrafted-recall.sh", [], id="recall"),
    pytest.param("vibecrafted-precompact.sh", [], id="precompact"),
    pytest.param("vibecrafted-postcompact.sh", [], id="postcompact"),
]


@_NEEDS_MODERN_PYTHON
@pytest.mark.parametrize(("script", "args"), _ENTRYPOINT_CASES)
def test_top_level_entrypoint_runs_on_the_runtime_interpreter(
    tmp_path: Path,
    script: str,
    args: list[str],
) -> None:
    """A hostile host ``python3`` must not reach a help/describe/dry-run path.

    Each of these exits 79 with ``HOST_PYTHON_SELECTED`` before the cut: the
    heredocs inside the entrypoints never named their interpreter, so closing
    the PATH leak left them reaching for whatever the Founder's PATH offers.
    """

    env, _hostile_bin, _generation_bin = _entrypoint_env(tmp_path)
    result = _run_entrypoint(script, args, env)

    combined = result.stdout + result.stderr
    assert "HOST_PYTHON_SELECTED" not in combined, combined
    assert result.returncode == 0, combined


@_NEEDS_MODERN_PYTHON
def test_entrypoints_leave_public_tool_resolution_to_the_founder(
    tmp_path: Path,
) -> None:
    """Owning internal execution must not repair or shadow the public surface.

    ``python3`` still resolves to the Founder's hostile binary as a file (never
    a shell function), and a private-only foundation stays missing rather than
    being answered by a bundled generation copy.
    """

    env, hostile_bin, generation_bin = _entrypoint_env(tmp_path)
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"\n'
                "spawn_prepend_agent_tool_paths\n"
                'printf "public_python3=%s\\n" "$(command -v python3)"\n'
                'printf "python3_kind=%s\\n" "$(type -t python3)"\n'
                'printf "internal_python=%s\\n" "$(spawn_python_bin)"\n'
                "for tool in aicx loct prview screenscribe; do\n"
                '  printf "%s=%s\\n" "$tool" "$(command -v "$tool" || echo MISSING)"\n'
                "done\n"
                'printf "PATH=%s\\n" "$PATH"\n'
            ),
            "_",
            str(COMMON_SH),
        ],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields["public_python3"] == str(hostile_bin / "python3")
    assert fields["python3_kind"] == "file"
    assert fields["internal_python"] == str(generation_bin / "python3")
    # Public foundations either resolve to a user-owned tool or stay missing.
    # What they must never do is get answered by the generation bin we just
    # named for internal work — that is the leak the PATH closure removed.
    for tool in ("aicx", "loct", "prview", "screenscribe"):
        resolved = fields[tool]
        assert not resolved.startswith(str(generation_bin)), f"{tool} -> {resolved}"
    assert str(generation_bin) not in fields["PATH"].split(os.pathsep)


@_NEEDS_MODERN_PYTHON
def test_generated_codex_launcher_bridge_runs_on_the_runtime_interpreter(
    tmp_path: Path,
) -> None:
    """The stream bridge is ours, so its interpreter is ours — generated or not.

    ``codex exec`` in the same command line is the Founder's tool and stays
    untouched; ``codex_stream_bridge.py`` beside it is a runtime internal, and
    a hostile host python3 took the whole codex pipeline down with it.  The
    check is behavioural: the interpreter the launcher actually names is pulled
    out and made to run the real bridge.
    """

    env, _hostile_bin, generation_bin = _entrypoint_env(tmp_path)
    root = _isolated_git_root(tmp_path)
    plan = root / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")

    dry_run = _run_entrypoint(
        "codex_spawn.sh", ["--dry-run", "--root", str(root), str(plan)], env
    )
    combined = dry_run.stdout + dry_run.stderr
    assert "HOST_PYTHON_SELECTED" not in combined, combined
    assert dry_run.returncode == 0, combined

    launcher = next(
        Path(line.split(CODEX_LAUNCH_PREFIX, 1)[1].strip())
        for line in combined.splitlines()
        if CODEX_LAUNCH_PREFIX in line
    )
    # The launch command is embedded in the launcher single-quoted, so any
    # quoted argument inside it appears in the `'"'"'` escape form. Undo that
    # one deterministic transform, then read the two tokens that were piped
    # into -- shlex-ing the whole body is not stable, because the fallback
    # heredocs legitimately carry unbalanced quotes.
    body = launcher.read_text(encoding="utf-8").replace("""'"'"'""", "'")
    piped = re.search(
        r"\|\s*(?P<interp>'[^']*'|[^\s|]+)\s+(?P<bridge>'[^']*'|[^\s|]+)\s+--transcript",
        body,
    )
    assert piped is not None, body
    interpreter = shlex.split(piped.group("interp"))[0]
    assert shlex.split(piped.group("bridge"))[0] == str(CODEX_STREAM_BRIDGE)

    # Run the real bridge under exactly the interpreter the launcher named.
    proof = subprocess.run(
        [interpreter, str(CODEX_STREAM_BRIDGE), "--help"],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "HOST_PYTHON_SELECTED" not in proof.stdout + proof.stderr
    assert proof.returncode == 0, proof.stderr
    assert interpreter == str(generation_bin / "python3")


@_NEEDS_MODERN_PYTHON
def test_interactive_shell_quoting_survives_a_hostile_host_python(
    tmp_path: Path,
) -> None:
    """The shell facade's quoter is the launcher casualty's twin.

    ``_vetcoders_shell_quote`` did not merely fail on a hostile host: it
    returned the marker string *as the quoted value*, and
    ``_vetcoders_write_command_script`` writes that result into a script it
    then executes.  The user's shell owns public resolution; it does not own
    the interpreter our own helpers need.
    """

    env, hostile_bin, generation_bin = _entrypoint_env(tmp_path)
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"\n'
                'PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"\n'
                "export PATH\n"
                'printf "public_python3=%s\\n" "$(command -v python3)"\n'
                'printf "python3_kind=%s\\n" "$(type -t python3)"\n'
                'printf "internal_python=%s\\n" "$(_vetcoders_internal_python)"\n'
                'printf "quote=%s\\n" "$(_vetcoders_shell_quote "file with spaces")"\n'
                'printf "join=%s\\n" "$(_vetcoders_shell_quote_join "a b" "c;d")"\n'
            ),
            "_",
            str(SHELL_SH),
        ],
        env=env,
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = result.stdout + result.stderr
    assert "HOST_PYTHON_SELECTED" not in combined, combined
    assert result.returncode == 0, combined

    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields["quote"] == "'file with spaces'"
    assert fields["join"] == "'a b' 'c;d'"
    assert fields["internal_python"] == str(generation_bin / "python3")
    # Public resolution is still the Founder's, hostile or not.
    assert fields["public_python3"] == str(hostile_bin / "python3")
    assert fields["python3_kind"] == "file"
