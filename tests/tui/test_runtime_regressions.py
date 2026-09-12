from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts import vetcoders_install

REPO_ROOT = Path(__file__).resolve().parents[2]
SHELL_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)


def _write_fake_command(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_fake_core_module(root: Path) -> Path:
    package = root / "vibecrafted_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "package_resources.py").write_text(
        """from pathlib import Path

def package_root() -> Path:
    return Path(__file__).resolve().parent
""",
        encoding="utf-8",
    )
    (package / "cli.py").write_text(
        """import json
import os
import subprocess
import sys
from pathlib import Path

capture = Path(os.environ['FAKE_CORE_CAPTURE'])
prompt_file = ''
if '--file' in sys.argv[1:]:
    index = sys.argv.index('--file')
    prompt_file = Path(sys.argv[index + 1]).read_text(encoding='utf-8')
capture.write_text(json.dumps({
    'argv': sys.argv[1:],
    'stdin': sys.stdin.read(),
    'prompt_file': prompt_file,
}) + '\\n', encoding='utf-8')
worker = os.environ.get('FAKE_CORE_WORKER', '')
if worker:
    proc = subprocess.Popen(
        [worker],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    control = Path(os.environ['FAKE_CORE_CONTROL'])
    control.write_text(json.dumps({
        'run_id': 'rsme-fake-1',
        'launcher_pid': proc.pid,
        'status': 'launching',
    }) + '\\n', encoding='utf-8')
print('MANUAL EXPLICIT RESUME RECEIPT')
print('run_id: rsme-fake-1')
""",
        encoding="utf-8",
    )
    return package


def test_operator_session_spawn_does_not_shadow_zsh_status(
    tmp_path: Path,
) -> None:
    zsh = shutil.which("zsh")
    if zsh is None:
        pytest.skip("zsh is required for the operator-shell regression")

    env = os.environ.copy()
    env["TEST_COMMAND_SCRIPT"] = str(tmp_path / "command.sh")
    env["VIBECRAFTED_OPERATOR_SESSION"] = "operator-session"
    result = subprocess.run(
        [
            zsh,
            "-f",
            "-c",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    "vc_raise_launcher_limits() { :; }",
                    '_vetcoders_path_with_bundled_bin_priority() { print -r -- "$1"; }',
                    '_vetcoders_repo_root() { print -r -- "$PWD"; }',
                    "_vetcoders_require_vc_frame() { return 0; }",
                    "_vetcoders_vc_frame_bin() { print -r -- /usr/bin/true; }",
                    "_vetcoders_in_vc_frame() { return 0; }",
                    '_vetcoders_tmp_script_path() { print -r -- "$TEST_COMMAND_SCRIPT"; }',
                    "_vetcoders_write_command_script() { return 0; }",
                    "_vetcoders_spawn_into_operator_session resume-codex true",
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "read-only variable: status" not in result.stderr
    assert result.returncode == 0, result.stderr
    assert "launch accepted:" in result.stdout


def _probe_codex_resume_contract(
    tmp_path: Path,
    args: list[str],
    *,
    agent: str = "codex",
    operator_available: bool = True,
    runtime: str | None = "terminal",
) -> tuple[subprocess.CompletedProcess[str], str, bool]:
    home = tmp_path / "home"
    context_file = tmp_path / "aicx-context.md"
    command_capture = tmp_path / "command.txt"
    aicx_capture = tmp_path / "aicx-called.txt"
    home.mkdir(parents=True)
    context_file.write_text("AICX OVERLAY BODY\n", encoding="utf-8")

    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_OPERATOR_SESSION",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "VETCODERS_SPAWN_RUNTIME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["TEST_AICX_CONTEXT"] = str(context_file)
    env["TEST_AICX_CAPTURE"] = str(aicx_capture)
    env["TEST_COMMAND_CAPTURE"] = str(command_capture)
    env["TEST_OPERATOR_AVAILABLE"] = "1" if operator_available else ""
    if operator_available:
        # Headless requests only enter the visible-host branch when an operator
        # surface is already known; interactive Codex may also prepare one.
        env["VIBECRAFTED_OPERATOR_SESSION"] = "operator-session"

    resume_invocation = f"vc-resume {shlex.quote(agent)}"
    if runtime is not None:
        resume_invocation += f" --runtime {shlex.quote(runtime)}"
    if args:
        resume_invocation += " " + shlex.join(args)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    "codex() {",
                    "  {",
                    "    printf 'codex'",
                    "    printf ' %s' \"$@\"",
                    "    printf '\\n'",
                    '  } > "$TEST_COMMAND_CAPTURE"',
                    "}",
                    "_vetcoders_aicx_resume_fallback() {",
                    "  printf 'called\\n' > \"$TEST_AICX_CAPTURE\"",
                    "  printf 'SESSION_ID=historical-codex-session\\n'",
                    "  printf 'CONTEXT_FILE=%s\\n' \"$TEST_AICX_CONTEXT\"",
                    "  printf 'MODE=native_resume\\n'",
                    "}",
                    "_vetcoders_prepare_operator_runtime() {",
                    '  if [[ -n "$TEST_OPERATOR_AVAILABLE" ]]; then',
                    "    export VIBECRAFTED_OPERATOR_SESSION=operator-session",
                    "  else",
                    "    unset VIBECRAFTED_OPERATOR_SESSION",
                    "  fi",
                    "}",
                    "_vetcoders_spawn_into_operator_session() {",
                    '  printf \'%s\\n\' "$2" > "$TEST_COMMAND_CAPTURE"',
                    "}",
                    "_vetcoders_launch_tracked_resume() {",
                    (
                        "  printf 'tracked agent=%s session=[%s] prompt=[%s]\\n'"
                        ' "$1" "$2" "$3" > "$TEST_COMMAND_CAPTURE"'
                    ),
                    "}",
                    resume_invocation,
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    command = (
        command_capture.read_text(encoding="utf-8").strip()
        if command_capture.exists()
        else ""
    )
    return result, command, aicx_capture.exists()


def _assert_command_points_at_aicx_overlay(command: str) -> None:
    """The resume prompt is a pointer, never the inline payload.

    Inlining the overlay put whole continuity packs into agent argv —
    world-readable in `ps`, capped by ARG_MAX, mangled on newlines
    (prompts.sh). Continuity therefore means: the command names a primary
    input file, and that file carries the overlay body.
    """
    match = re.search(r"Primary input file: (\S+)", command)
    assert match, f"resume command carries no primary-input pointer: {command!r}"
    pointed = Path(match.group(1))
    assert pointed.is_file(), f"pointer names a missing file: {pointed}"
    assert "AICX OVERLAY BODY" in pointed.read_text(encoding="utf-8")


def test_bare_codex_resume_uses_aicx_pack_in_fresh_interactive_session(
    tmp_path: Path,
) -> None:
    result, command, aicx_called = _probe_codex_resume_contract(tmp_path, [])

    assert result.returncode == 0, result.stderr
    assert aicx_called
    assert command.startswith("codex ")
    _assert_command_points_at_aicx_overlay(command)
    assert "codex exec" not in command
    assert "codex resume" not in command
    assert "historical-codex-session" not in command


def test_default_resume_runtime_keeps_bare_codex_interactive_and_prompt_headless(
    tmp_path: Path,
) -> None:
    bare_result, bare_command, _ = _probe_codex_resume_contract(
        tmp_path / "bare",
        [],
        runtime=None,
    )
    prompt_result, prompt_command, _ = _probe_codex_resume_contract(
        tmp_path / "prompt",
        ["--prompt", "carry on"],
        runtime=None,
    )

    assert bare_result.returncode == 0, bare_result.stderr
    assert bare_command.startswith("codex ")
    assert "codex exec" not in bare_command
    assert prompt_result.returncode == 0, prompt_result.stderr
    assert prompt_command == "tracked agent=codex session=[] prompt=[carry on]"


@pytest.mark.parametrize(
    ("agent", "headless_flag"),
    [
        ("claude", "--print"),
        ("agy", "--print"),
        ("grok", "--single"),
        ("junie", ""),
    ],
)
def test_bare_resume_keeps_aicx_continuity_in_operator_session(
    tmp_path: Path,
    agent: str,
    headless_flag: str,
) -> None:
    result, command, aicx_called = _probe_codex_resume_contract(
        tmp_path,
        [],
        agent=agent,
        runtime=None,
    )

    assert result.returncode == 0, result.stderr
    assert aicx_called
    assert command.startswith(f"{agent} ")
    _assert_command_points_at_aicx_overlay(command)
    assert not command.startswith("tracked ")
    assert "historical-codex-session" not in command
    assert "--resume" not in command
    assert "--conversation" not in command
    assert "--session-id=" not in command
    if headless_flag:
        assert headless_flag not in command


@pytest.mark.parametrize("agent", ["claude", "agy", "grok", "junie"])
def test_explicit_prompt_without_session_never_adopts_aicx_session(
    tmp_path: Path,
    agent: str,
) -> None:
    result, command, aicx_called = _probe_codex_resume_contract(
        tmp_path,
        ["--prompt", "carry on"],
        agent=agent,
        runtime=None,
    )

    assert result.returncode == 0, result.stderr
    assert not aicx_called
    assert command == f"tracked agent={agent} session=[] prompt=[carry on]"
    assert "historical-codex-session" not in command


def test_codex_session_only_is_exact_interactive_resume(tmp_path: Path) -> None:
    result, command, aicx_called = _probe_codex_resume_contract(
        tmp_path, ["--session", "sess-123"]
    )

    assert result.returncode == 0, result.stderr
    assert not aicx_called
    assert command == "codex resume sess-123"


def test_codex_explicit_prompt_and_file_are_fresh_noninteractive_runs(
    tmp_path: Path,
) -> None:
    prompt_result, prompt_command, prompt_aicx = _probe_codex_resume_contract(
        tmp_path / "prompt", ["--prompt", "carry on"]
    )
    input_file = tmp_path / "file" / "input.md"
    input_file.parent.mkdir(parents=True)
    input_file.write_text("FILE INPUT\n", encoding="utf-8")
    file_result, file_command, file_aicx = _probe_codex_resume_contract(
        tmp_path / "file", ["--file", str(input_file)]
    )

    assert prompt_result.returncode == 0, prompt_result.stderr
    assert file_result.returncode == 0, file_result.stderr
    assert not prompt_aicx and not file_aicx
    assert prompt_command.startswith(
        "codex exec --dangerously-bypass-approvals-and-sandbox "
    )
    assert "carry on" in prompt_command
    assert file_command.startswith(
        "codex exec --dangerously-bypass-approvals-and-sandbox "
    )
    pointer = re.search(r"Primary input file: (\S+)", file_command)
    assert pointer, f"file input must ride as a pointer: {file_command!r}"
    assert Path(pointer.group(1)).resolve() == input_file.resolve()


def test_codex_session_with_explicit_file_is_noninteractive_continuation(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.md"
    input_file.write_text("SESSION FILE INPUT\n", encoding="utf-8")

    result, command, aicx_called = _probe_codex_resume_contract(
        tmp_path / "probe",
        ["--session", "sess-file-123", "--file", str(input_file)],
    )

    assert result.returncode == 0, result.stderr
    assert not aicx_called
    assert command.startswith("codex exec --dangerously-bypass-approvals-and-sandbox ")
    assert "resume sess-file-123" in command
    pointer = re.search(r"Primary input file: (\S+)", command)
    assert pointer, f"file input must ride as a pointer: {command!r}"
    assert Path(pointer.group(1)).resolve() == input_file.resolve()


def test_codex_positional_resume_compatibility_preserves_mode_contract(
    tmp_path: Path,
) -> None:
    session_id = "019ec264-0b50-7bb2-9336-0aae5c841209"
    session_result, session_command, _ = _probe_codex_resume_contract(
        tmp_path / "session", [session_id]
    )
    continuation_result, continuation_command, _ = _probe_codex_resume_contract(
        tmp_path / "continuation", [session_id, "carry", "on"]
    )
    prompt_result, prompt_command, prompt_aicx = _probe_codex_resume_contract(
        tmp_path / "prompt", ["carry", "on"]
    )

    assert session_result.returncode == 0, session_result.stderr
    assert session_command == f"codex resume {session_id}"
    assert continuation_result.returncode == 0, continuation_result.stderr
    assert "codex exec" in continuation_command
    assert f"resume {session_id}" in continuation_command
    assert "carry on" in continuation_command
    assert prompt_result.returncode == 0, prompt_result.stderr
    assert "codex exec" in prompt_command
    assert " resume " not in prompt_command
    assert not prompt_aicx


@pytest.mark.parametrize("agent", ["claude", "codex", "agy", "grok", "junie"])
def test_interactive_resume_fails_without_operator_target_for_every_agent(
    tmp_path: Path,
    agent: str,
) -> None:
    """Provider-neutral: bare interactive resume never silently becomes headless."""
    result, command, aicx_called = _probe_codex_resume_contract(
        tmp_path / agent,
        ["--session", "sess-123"],
        agent=agent,
        operator_available=False,
    )

    assert result.returncode != 0
    assert not command
    assert not aicx_called
    assert "requires an explicit or detected operator target" in result.stderr
    assert "refusing to downgrade to a headless run" in result.stderr
    assert "VIBECRAFTED_OPERATOR_SESSION" in result.stderr


def _probe_interactive_operator_target(
    tmp_path: Path,
    *,
    sessions_body: str,
    repo_basename: str,
) -> subprocess.CompletedProcess[str]:
    """Resolve interactive target against a fake vc-frame listing only.

    Bundled PATH priority would otherwise surface the host's real sessions, so
    the probe pins ``_vetcoders_vc_frame_bin`` to the fixture binary.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    listing = tmp_path / "sessions.txt"
    listing.write_text(sessions_body, encoding="utf-8")
    fake_vc_frame = fake_bin / "vc-frame"
    _write_fake_command(
        fake_vc_frame,
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "list-sessions" || "${1:-}" == "ls" ]]; then\n'
        f'  cat "{listing}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )

    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_OPERATOR_SESSION",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_PANE_ID",
        "ZELLIJ_SESSION_NAME",
    ):
        env.pop(key, None)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["VIBECRAFTED_ROOT"] = str(tmp_path / repo_basename)
    (tmp_path / repo_basename).mkdir(exist_ok=True)

    return subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    # Host/bundled vc-frame must not leak into this unit probe.
                    f'_vetcoders_vc_frame_bin() {{ printf "%s\\n" "{fake_vc_frame}"; }}',
                    '_vetcoders_path_with_bundled_bin_priority() { printf "%s\\n" "$1"; }',
                    'target="$(_vetcoders_resolve_interactive_operator_target)"',
                    'printf "target=[%s]\\n" "$target"',
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_interactive_target_prefers_repo_bound_live_session(
    tmp_path: Path,
) -> None:
    """Detected target is not only (attached)/(current) — repo-bound live counts."""
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="other [Created]\nvibecrafted [Created]\n",
        repo_basename="vibecrafted",
    )
    assert result.returncode == 0, result.stderr
    assert "target=[vibecrafted]" in result.stdout


def test_interactive_target_ambiguous_live_sessions_fail_closed(
    tmp_path: Path,
) -> None:
    """Multiple live candidates without a unique pick must not invent a target."""
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="alpha [Created]\nbeta [Created]\ngamma [Created]\n",
        repo_basename="not-a-session",
    )
    assert result.returncode == 0, result.stderr
    assert "target=[]" in result.stdout
    assert "ambiguous" in result.stderr
    assert "alpha" in result.stderr
    assert "beta" in result.stderr
    assert "VIBECRAFTED_OPERATOR_SESSION" in result.stderr


def test_interactive_target_single_live_session_is_detected(
    tmp_path: Path,
) -> None:
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="solo-session [Created]\n",
        repo_basename="other-repo",
    )
    assert result.returncode == 0, result.stderr
    assert "target=[solo-session]" in result.stdout


def test_public_and_packaged_resume_help_describe_provider_neutral_contract() -> None:
    launchers = (
        REPO_ROOT / "scripts" / "vibecrafted",
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted",
    )

    for launcher in launchers:
        result = subprocess.run(
            ["bash", str(launcher), "resume", "--help"],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert "A bare resume stays interactive" in result.stdout
        assert "AICX pack is continuity transport" in result.stdout
        assert (
            "Explicit --prompt/--file without --session starts a new tracked"
            in result.stdout
        )
        assert "does not adopt a historical session" in result.stdout
        assert "Codex always starts" not in result.stdout
        assert "native-resumes it with the pack as prompt" not in result.stdout


def test_resume_terminal_runtime_routes_headless_codex_into_worker_session(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    home = tmp_path / "home"
    vc_frame_capture = tmp_path / "vc_frame.txt"
    codex_capture = tmp_path / "codex.txt"
    fake_bin.mkdir()
    home.mkdir()

    _write_fake_command(
        fake_bin / "vc-frame",
        '#!/usr/bin/env bash\nset -euo pipefail\n{\n  printf "%s\\n" "--CALL--"\n  printf "%s\\n" "$@"\n} >> "$VC_FRAME_CAPTURE"'
        + "\n",
    )
    _write_fake_command(
        fake_bin / "codex",
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" > "$CODEX_CAPTURE"'
        + "\n",
    )

    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_RUN_ID",
        "VIBECRAFTED_RUN_LOCK",
        "VIBECRAFTED_SKILL_CODE",
        "VIBECRAFTED_SKILL_NAME",
        "VIBECRAFTED_LOOP_NR",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_OPERATOR_SESSION"] = "operator-session"
    env["VIBECRAFTED_WORKER_SESSION"] = "worker-session"
    env["VC_FRAME_CAPTURE"] = str(vc_frame_capture)
    env["CODEX_CAPTURE"] = str(codex_capture)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{SHELL_SH}"; '
                "vc-resume codex --runtime terminal "
                "--session sess-123 --prompt 'carry on'"
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    # Explicit input makes this a non-interactive continuation, so G7 routes it
    # to the worker column rather than occupying the human operator seat.
    assert "Resume launched in worker session: worker-session" in result.stdout
    assert "mode:    headless (G7 workers column)" in result.stdout
    assert "Resume launched in operator session" not in result.stdout
    assert not codex_capture.exists()
    vc_frame_lines = vc_frame_capture.read_text(encoding="utf-8").splitlines()
    calls: list[list[str]] = []
    current: list[str] = []
    for line in vc_frame_lines:
        if line == "--CALL--":
            if current:
                calls.append(current)
            current = []
        else:
            current.append(line)
    if current:
        calls.append(current)
    new_tab_call = next(call for call in calls if call[2:4] == ["action", "new-tab"])
    assert new_tab_call[:5] == [
        "--session",
        "worker-session",
        "action",
        "new-tab",
        "--name",
    ]
    # Face tab: the workers column names tabs by agent face, not by verb
    # (place-named operator rail). The routing assertions above carry the
    # actual contract — worker session, new tab, non-interactive exec below.
    assert new_tab_call[5] == "codex"
    command_script = Path(new_tab_call[-1])
    command_body = command_script.read_text(encoding="utf-8")
    # Explicit --prompt means "continue the job": the visible tab must host the
    # NON-INTERACTIVE `codex exec ... resume`, never the interactive picker
    # (operator contract 2026-07-21). Bare resume without input keeps the TUI.
    assert "codex exec" in command_body
    assert "resume sess-123" in command_body
    assert "carry on" in command_body


def _fake_core_env(
    tmp_path: Path,
    capture: Path,
) -> tuple[dict[str, str], Path]:
    fake_core_root = tmp_path / "fake-core"
    package = _write_fake_core_module(fake_core_root)
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_RUN_ID",
        "VIBECRAFTED_RUN_LOCK",
        "VIBECRAFTED_SKILL_CODE",
        "VIBECRAFTED_SKILL_NAME",
        "VIBECRAFTED_LOOP_NR",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "VIBECRAFTED_OPERATOR_SESSION",
        "VIBECRAFTED_WORKER_SESSION",
    ):
        env.pop(key, None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PYTHON"] = sys.executable
    env["PYTHONPATH"] = str(fake_core_root)
    env["FAKE_CORE_CAPTURE"] = str(capture)
    return env, package


def test_resume_headless_routes_explicit_session_through_tracked_core(
    tmp_path: Path,
) -> None:
    core_capture = tmp_path / "core.json"
    env, package = _fake_core_env(tmp_path, core_capture)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{SHELL_SH}"; '
                "vc-resume codex --runtime headless "
                "--session sess-123 --prompt 'carry on'"
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = json.loads(core_capture.read_text(encoding="utf-8"))
    assert payload["argv"] == [
        "resume-session",
        "codex",
        "--agent-session-id",
        "sess-123",
        "--prompt-stdin",
        "--root",
        str(REPO_ROOT),
        "--source-dir",
        str(package),
    ]
    assert payload["stdin"] == "carry on"
    assert "MANUAL EXPLICIT RESUME RECEIPT" in result.stdout
    assert "rsme-fake-1" in result.stdout


def test_resume_headless_routes_fresh_input_through_tracked_workflow(
    tmp_path: Path,
) -> None:
    core_capture = tmp_path / "core.json"
    env, package = _fake_core_env(tmp_path, core_capture)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{SHELL_SH}"; '
                "vc-resume codex --runtime headless --prompt 'carry on'"
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = json.loads(core_capture.read_text(encoding="utf-8"))
    argv = payload["argv"]
    assert argv[:2] == ["workflow", "codex"]
    assert argv[argv.index("--runtime") + 1] == "headless"
    assert argv[argv.index("--root") + 1] == str(REPO_ROOT)
    assert argv[argv.index("--source-dir") + 1] == str(package)
    assert argv[argv.index("--mode") + 1] == "resume-new-session"
    assert "--prompt-stdin" in argv
    assert "--file" not in argv
    assert payload["prompt_file"] == ""
    assert payload["stdin"] == "carry on"
    assert "MANUAL EXPLICIT RESUME RECEIPT" in result.stdout


def test_resume_prompt_never_creates_temp_file_when_core_fails_under_errexit(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-e",
            "-c",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    "_vetcoders_core_source_dir() { printf '/tmp\\n'; }",
                    "_vetcoders_run_core_cli() { return 7; }",
                    "vc-resume codex --runtime headless --prompt 'secret input'",
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 7
    assert not list((home / ".vibecrafted").rglob("vc-resume-prompt-*")), (
        "tracked launches must not materialize plaintext prompt temp files"
    )


def test_resume_prompt_never_creates_temp_file_when_shell_is_terminated(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    "_vetcoders_core_source_dir() { printf '/tmp\\n'; }",
                    '_vetcoders_run_core_cli() { kill -TERM "$$"; }',
                    "vc-resume codex --runtime headless --prompt 'secret input'",
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode in {-signal.SIGTERM, 128 + signal.SIGTERM}
    assert not list((home / ".vibecrafted").rglob("vc-resume-prompt-*"))


def test_resume_headless_fails_closed_when_core_is_unavailable(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{SHELL_SH}"; '
                "_vetcoders_core_python_spec() { return 1; }; "
                "vc-resume codex --runtime headless "
                "--session sess-123 --prompt 'carry on'"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Tracked resume refused: Vibecrafted core is unavailable." in result.stderr


def test_tracked_core_resume_survives_parent_process_group_sigkill(
    tmp_path: Path,
) -> None:
    core_capture = tmp_path / "core.json"
    env, _ = _fake_core_env(tmp_path, core_capture)
    home = Path(env["HOME"])
    parent_ready = tmp_path / "parent-ready.txt"
    child_state = tmp_path / "resume-child-state.txt"
    child_complete = tmp_path / "resume-child-complete.txt"
    control = home / ".vibecrafted" / "control_plane" / "runs" / "rsme-fake-1.json"
    transcript = home / ".vibecrafted" / "artifacts" / "rsme-fake-1.transcript.log"
    exit_code = home / ".vibecrafted" / "artifacts" / "rsme-fake-1.exit-code"
    worker = tmp_path / "tracked-worker.py"
    control.parent.mkdir(parents=True)
    transcript.parent.mkdir(parents=True)
    _write_fake_command(
        worker,
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os",
                "import time",
                "from pathlib import Path",
                (
                    f"Path({str(child_state)!r}).write_text("
                    "f'{os.getpid()} {os.getsid(0)} {os.getpgrp()}\\n', "
                    "encoding='utf-8')"
                ),
                "print('detached resume output', flush=True)",
                "time.sleep(1.5)",
                (
                    f"Path({str(child_complete)!r}).write_text("
                    "'completed\\n', encoding='utf-8')"
                ),
                (
                    f"Path({str(transcript)!r}).write_text("
                    "'tracked transcript\\n', encoding='utf-8')"
                ),
                f"Path({str(exit_code)!r}).write_text('0\\n', encoding='utf-8')",
            ]
        )
        + "\n",
    )
    env["FAKE_CORE_WORKER"] = str(worker)
    env["FAKE_CORE_CONTROL"] = str(control)

    parent = subprocess.Popen(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{SHELL_SH}"; '
                "vc-resume codex --runtime headless "
                "--session sess-123 --prompt 'carry on'; "
                f'printf "ready\\n" > "{parent_ready}"; '
                "while :; do sleep 60; done"
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    parent_pgid: int | None = None
    detached_pgid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while (
            not parent_ready.exists() or not child_state.exists()
        ) and time.monotonic() < deadline:
            if parent.poll() is not None:
                break
            time.sleep(0.05)

        if not parent_ready.exists() or not child_state.exists():
            stdout, stderr = (
                parent.communicate(timeout=1) if parent.poll() is not None else ("", "")
            )
            pytest.fail(
                "resume failed before detached child launch: "
                f"stdout={stdout!r} stderr={stderr!r}"
            )

        child_pid, child_sid, child_pgid = map(
            int,
            child_state.read_text(encoding="utf-8").split(),
        )
        parent_pgid = os.getpgid(parent.pid)
        detached_pgid = child_pgid
        assert parent_pgid == parent.pid
        assert parent_pgid != os.getpgrp()
        assert child_pid > 1
        assert child_sid == child_pgid
        assert child_pgid != parent_pgid
        assert child_pgid != os.getpgrp()

        os.killpg(parent_pgid, signal.SIGKILL)
        stdout, stderr = parent.communicate(timeout=5)
        assert parent.returncode == -signal.SIGKILL, stderr
        assert "MANUAL EXPLICIT RESUME RECEIPT" in stdout
        control_payload = json.loads(control.read_text(encoding="utf-8"))
        assert control_payload == {
            "run_id": "rsme-fake-1",
            "launcher_pid": child_pid,
            "status": "launching",
        }

        deadline = time.monotonic() + 5
        while (
            not child_complete.exists() or not exit_code.exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_complete.read_text(encoding="utf-8") == "completed\n"
        assert transcript.read_text(encoding="utf-8") == "tracked transcript\n"
        assert exit_code.read_text(encoding="utf-8") == "0\n"
        core_payload = json.loads(core_capture.read_text(encoding="utf-8"))
        assert core_payload["argv"][:2] == ["resume-session", "codex"]
        assert core_payload["stdin"] == "carry on"
    finally:
        if parent.poll() is None:
            try:
                live_parent_pgid = os.getpgid(parent.pid)
            except ProcessLookupError:
                live_parent_pgid = None
            if live_parent_pgid == parent.pid and live_parent_pgid != os.getpgrp():
                try:
                    os.killpg(live_parent_pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            parent.wait(timeout=5)

        if detached_pgid is not None and not child_complete.exists():
            try:
                live_detached_pgid = os.getpgid(detached_pgid)
            except ProcessLookupError:
                live_detached_pgid = None
            if (
                live_detached_pgid == detached_pgid
                and detached_pgid > 1
                and detached_pgid != os.getpgrp()
            ):
                try:
                    os.killpg(detached_pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_copy_managed_launcher_replaces_broken_framework_symlink(
    tmp_path: Path,
) -> None:
    src = tmp_path / "src-vibecrafted"
    dst = tmp_path / "bin" / "vibecrafted"
    missing_target = tmp_path / ".vibecrafted" / "bin" / "vibecrafted"
    src.write_text("#!/usr/bin/env bash\nprintf 'ok\\n'\n", encoding="utf-8")
    src.chmod(0o755)
    dst.parent.mkdir()
    dst.symlink_to(missing_target)

    assert dst.is_symlink()
    assert not dst.exists()

    assert vetcoders_install._copy_managed_launcher(src, dst) is True

    assert dst.is_file()
    assert not dst.is_symlink()
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")


def test_spawn_launch_headless_detaches_into_new_session(tmp_path: Path) -> None:
    """A headless launcher must run in its OWN session (setsid), not the spawner's
    process group — otherwise a GUI app's Process teardown (the Pensieve dispatch)
    kills the 'detached' run ~2s after spawn, before it writes a transcript."""
    launcher = tmp_path / "launcher.sh"
    sid_file = tmp_path / "child_sid.txt"
    _write_fake_command(
        launcher,
        "\n".join(
            [
                "#!/usr/bin/env bash",
                f'python3 -c \'import os; open("{sid_file}","w").write(str(os.getsid(0)))\'',
                "sleep 2",
            ]
        )
        + "\n",
    )

    launcher_sh = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "launcher.sh"
    )
    # Spawn from a parent shell that exits immediately, then compare sessions.
    parent_sid = subprocess.run(
        [
            "bash",
            "-c",
            (
                "spawn_die(){ echo die >&2; exit 1; }; "
                f'eval "$(sed -n "/^spawn_launch_headless()/,/^}}/p" "{launcher_sh}")"; '
                f'spawn_launch_headless "{launcher}" >/dev/null; '
                "python3 -c 'import os; print(os.getsid(0))'"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    deadline = time.monotonic() + 5
    while not sid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert sid_file.exists(), "headless child never ran (died at spawn)"
    child_sid = sid_file.read_text(encoding="utf-8").strip()
    assert child_sid and child_sid != parent_sid, (
        f"headless child must be its own session leader (child={child_sid}, parent={parent_sid})"
    )


def test_spawn_launch_headless_survives_parent_process_group_sigkill(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launcher.py"
    child_state = tmp_path / "child-state.txt"
    child_complete = tmp_path / "child-complete.txt"
    parent_ready = tmp_path / "parent-ready.txt"
    _write_fake_command(
        launcher,
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os",
                "import time",
                "from pathlib import Path",
                (
                    f"Path({str(child_state)!r}).write_text("
                    "f'{os.getpid()} {os.getsid(0)} {os.getpgrp()}\\n', "
                    "encoding='utf-8')"
                ),
                "time.sleep(1.5)",
                (
                    f"Path({str(child_complete)!r}).write_text("
                    "'completed\\n', encoding='utf-8')"
                ),
            ]
        )
        + "\n",
    )

    launcher_sh = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "launcher.sh"
    )
    parent = subprocess.Popen(
        [
            "bash",
            "-c",
            (
                "set -euo pipefail; "
                "spawn_die(){ echo die >&2; exit 1; }; "
                f'eval "$(sed -n "/^spawn_launch_headless()/,/^}}/p" "{launcher_sh}")"; '
                f'spawn_launch_headless "{launcher}" >/dev/null; '
                f'printf "ready\\n" > "{parent_ready}"; '
                "while :; do sleep 60; done"
            ),
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )

    parent_pgid: int | None = None
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while (
            not parent_ready.exists() or not child_state.exists()
        ) and time.monotonic() < deadline:
            if parent.poll() is not None:
                break
            time.sleep(0.05)

        if not parent_ready.exists() or not child_state.exists():
            stderr = parent.stderr.read() if parent.poll() is not None else ""
            pytest.fail(f"sacrificial parent failed before child launch: {stderr}")

        child_pid, child_sid, child_pgid = map(
            int,
            child_state.read_text(encoding="utf-8").split(),
        )
        parent_pgid = os.getpgid(parent.pid)
        assert parent_pgid == parent.pid
        assert parent_pgid != os.getpgrp()
        assert child_pid > 1
        assert child_sid == child_pid
        assert child_pgid == child_pid
        assert child_pgid != parent_pgid

        os.killpg(parent_pgid, signal.SIGKILL)
        parent.wait(timeout=5)
        assert parent.returncode == -signal.SIGKILL

        deadline = time.monotonic() + 5
        while not child_complete.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_complete.read_text(encoding="utf-8") == "completed\n"
    finally:
        if parent.poll() is None:
            try:
                live_parent_pgid = os.getpgid(parent.pid)
            except ProcessLookupError:
                live_parent_pgid = None
            if (
                live_parent_pgid is not None
                and live_parent_pgid == parent.pid
                and live_parent_pgid != os.getpgrp()
            ):
                try:
                    os.killpg(live_parent_pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            parent.wait(timeout=5)

        if child_pid is not None and not child_complete.exists():
            try:
                live_child_pgid = os.getpgid(child_pid)
            except ProcessLookupError:
                live_child_pgid = None
            if live_child_pgid == child_pid and child_pid > 1:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("agent", ["agy", "claude", "codex", "grok", "junie"])
def test_worker_spawn_scripts_default_to_headless(
    tmp_path: Path,
    agent: str,
) -> None:
    home = tmp_path / "home"
    root = tmp_path / "repo"
    plan = tmp_path / "plan.md"
    home.mkdir()
    root.mkdir()
    plan.write_text("Headless default probe.\n", encoding="utf-8")

    env = os.environ.copy()
    env.pop("VETCODERS_SPAWN_RUNTIME", None)
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_INLINE_STARTUP_WATCH"] = "0"
    spawn_script = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / f"{agent}_spawn.sh"
    )
    result = subprocess.run(
        [
            "bash",
            str(spawn_script),
            "--root",
            str(root),
            "--dry-run",
            str(plan),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    clean_stdout = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "runtime: headless" in clean_stdout


@pytest.mark.parametrize("any_local_copy", [True, False])
def test_aicx_resume_fallback_skips_provider_pruned_candidates(
    tmp_path: Path, any_local_copy: bool
) -> None:
    """Bare resume must not native-attach, even when AICX still lists a
    live same-agent row. Pruned local copies stay catalog evidence.
    """
    import datetime as dt

    home = tmp_path / "home"
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    for d in (home, repo, fake_bin):
        d.mkdir()

    now = dt.datetime.now(dt.timezone.utc)
    fresh = now.isoformat().replace("+00:00", "Z")
    pruned_src = tmp_path / "pruned-session.jsonl"  # deliberately absent
    live_src = tmp_path / "live-session.jsonl"
    sessions = [
        {
            # Newest + repo-matching: the old code always picked this one.
            "session_id": "gone-aaaa-1111",
            "agent": "claude",
            "project": "repo",
            "repo_path": str(repo),
            "updated_at": fresh,
            "source_path": str(pruned_src),
            "title": "pruned upstream",
        },
    ]
    if any_local_copy:
        live_src.write_text("{}\n", encoding="utf-8")
        sessions.append(
            {
                "session_id": "live-bbbb-2222",
                "agent": "claude",
                "project": "repo",
                "repo_path": str(repo),
                "updated_at": fresh,
                "source_path": str(live_src),
                "title": "still resumable",
            }
        )
    sessions_json = tmp_path / "sessions.json"
    sessions_json.write_text(json.dumps(sessions), encoding="utf-8")

    _write_fake_command(
        fake_bin / "aicx",
        "#!/usr/bin/env bash\n"
        'if [[ "$1 $2" == "sessions list" ]]; then\n'
        f'  cat "{sessions_json}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{SHELL_SH}"\n'
                f"_vetcoders_aicx_resume_fallback claude {shlex.quote(str(repo))}"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields.get("SESSION_ID") == ""
    assert fields.get("MODE") == "new_session"
    meta_files = list((home / ".vibecrafted" / "tmp").glob("*.meta.json"))
    assert meta_files, "fallback must persist its meta sidecar"
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["mode"] == "new_session"
    assert meta["session_id"] == ""
    pack = next((home / ".vibecrafted" / "tmp").glob("resume-aicx-claude-*.md"))
    text = pack.read_text(encoding="utf-8")
    assert "prefer native resume" not in text.lower()
    assert "recover previous session" not in text.lower()
    assert "gone-aaaa-1111" in text
    if any_local_copy:
        assert "live-bbbb-2222" in text


def test_aicx_resume_fallback_resolves_cargo_foundation_without_shell_path(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    cargo_bin = home / ".cargo" / "bin"
    repo.mkdir(parents=True)
    cargo_bin.mkdir(parents=True)
    _write_fake_command(
        cargo_bin / "aicx",
        "#!/bin/bash\n"
        'if [[ "$1 $2" == "sessions list" ]]; then\n'
        "  printf '[]\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    python_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = f"{python_dir}:/usr/bin:/bin"

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{SHELL_SH}"\n'
                f"_vetcoders_aicx_resume_fallback codex {shlex.quote(str(repo))}"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "MODE=new_session" in result.stdout
    assert "aicx foundation not found" not in result.stderr


def test_aicx_resume_fallback_uses_cross_org_exact_repo_filter(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "codescribe"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "aicx-calls"
    for directory in (home, repo, fake_bin):
        directory.mkdir()
    _write_fake_command(
        fake_bin / "aicx",
        "#!/bin/bash\n"
        f'printf \'%s\\n\' "$*" >> "{calls}"\n'
        'if [[ "$1 $2" == "sessions list" ]]; then\n'
        "  printf '[]\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "tail" ]]; then exit 1; fi\n'
        'if [[ "$1" == "intents" ]]; then\n'
        "  printf '# Intent Report\\n\\n_No records._\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "overlay" ]]; then printf \'{}\\n\'; exit 0; fi\n'
        "exit 1\n",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{SHELL_SH}"\n'
                f"_vetcoders_aicx_resume_fallback grok {shlex.quote(str(repo))}"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    invoked = calls.read_text(encoding="utf-8").splitlines()
    continuity = [line for line in invoked if line.startswith("continuity ")]
    tail = [line for line in invoked if line.startswith("tail ")]
    intents = [line for line in invoked if line.startswith("intents ")]
    assert continuity, invoked
    assert any("-p /codescribe" in line for line in continuity)
    assert len(tail) == 1
    assert len(intents) == 1
    assert tail[0].endswith("-p /codescribe")
    assert intents[0].endswith("-p /codescribe")
    pack = next((home / ".vibecrafted" / "tmp").glob("resume-aicx-grok-*.md"))
    text = pack.read_text(encoding="utf-8")
    assert "aicx_project_filter: `/codescribe`" in text
    assert "prefer native resume" not in text.lower()
