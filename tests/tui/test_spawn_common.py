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
    """Capture a tracked core launch without importing core or spawning an agent."""
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
  printf "%s\\0" "$@" > "$FAKE_CORE_ARGV_FILE"
  cat > "$FAKE_CORE_PROMPT_FILE"
  printf '%s\\n' '=============== MANUAL EXPLICIT RESUME RECEIPT ===============' 'run_id:             rsme-fixture-1' "agent_session_id:   ${FAKE_CORE_SESSION_ID}" 'resume_mode:        manual_explicit'
  exit 0
fi
printf "unexpected fake-core invocation: %s\\n" "$*" >&2
exit 98
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


def test_spawn_tool_paths_follow_silver_runtime_contract(tmp_path: Path) -> None:
    home = tmp_path / "home"
    rogue_bin = tmp_path / "rogue" / "bin"
    for rel in (
        "tools/scripts",
        ".local/bin",
        ".local/share/vibecrafted/bin",
        ".cargo/bin",
        ".claude/plugins/cache/example/tool/bin",
        "bin",
        "tools",
        "Git/tools",
    ):
        (home / rel).mkdir(parents=True, exist_ok=True)
    rogue_bin.mkdir(parents=True)

    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{home}"
        export PATH="{rogue_bin}:{home / ".local" / "share" / "vibecrafted" / "bin"}:{home / ".cargo" / "bin"}:{home / ".claude" / "plugins" / "cache" / "example" / "tool" / "bin"}:{home / "tools"}:{home / "bin"}:{home / ".local" / "bin"}:/usr/bin:/bin:/usr/bin"
        source "{COMMON_SH}"
        spawn_prepend_agent_tool_paths
        printf '%s\n' "$PATH" | tr ':' '\n'
        '''
    )

    expected_prefix = [
        str(home / ".local" / "share" / "vibecrafted" / "bin"),
        str(home / ".local" / "bin"),
        str(home / ".cargo" / "bin"),
        str(home / "tools" / "scripts"),
    ]
    if Path("/opt/homebrew/bin").is_dir():
        expected_prefix.append("/opt/homebrew/bin")
    if Path("/opt/homebrew/sbin").is_dir():
        expected_prefix.append("/opt/homebrew/sbin")
    expected_prefix.extend(
        [
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )

    entries = result.stdout.splitlines()
    assert entries[: len(expected_prefix)] == expected_prefix
    assert len(entries) == len(set(entries))
    assert str(rogue_bin) not in entries
    assert (
        str(home / ".claude" / "plugins" / "cache" / "example" / "tool" / "bin")
        not in entries
    )
    assert str(home / "bin") not in entries
    assert str(home / "tools") not in entries
    assert str(home / "Git" / "tools") not in entries


def test_spawn_require_command_rejects_non_contract_path_entries(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    rogue_bin = tmp_path / "rogue" / "bin"
    rogue_bin.mkdir(parents=True)
    command_name = "vc-test-rogue-agent"
    fake_agent = rogue_bin / command_name
    fake_agent.write_text(
        "#!/usr/bin/env bash\nprintf 'rogue-agent\\n'\n", encoding="utf-8"
    )
    fake_agent.chmod(0o755)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            _ENV_SANITIZE
            + f'''
            set -euo pipefail
            export HOME="{home}"
            export PATH="{rogue_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
            source "{COMMON_SH}"
            spawn_require_command "{command_name}"
            ''',
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert f"Required command not found: {command_name}" in result.stderr


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
