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


_PROVIDER_FAKES = ("codex", "claude", "agy", "grok", "junie", "cursor-agent", "aicx")
# Admission probes a provider CLI's surface (`claude --version` / `--help`);
# that is capability evidence, not a launch.
_PROVIDER_SURFACE_PROBES = ("--version", "--help")


class _ResumeProbe:
    """What one public resume vector did at every seam the harness owns."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.generation = tmp_path / "generation"
        self.fake_bin = tmp_path / "bin"
        self.context_file = tmp_path / "aicx-context.md"
        self.command_capture = tmp_path / "command.txt"
        self.tab_capture = tmp_path / "tab.txt"
        self.attach_capture = tmp_path / "attach.txt"
        self.aicx_capture = tmp_path / "aicx-called.txt"
        self.core_argv_capture = tmp_path / "core-argv.bin"
        self.core_stdin_capture = tmp_path / "core-stdin.txt"
        self.provider_log = tmp_path / "provider-calls.txt"
        self.front_door_log = tmp_path / "front-door-calls.txt"
        self.result = subprocess.CompletedProcess[str]([], -1, "", "")

    @staticmethod
    def _text(path: Path) -> str:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    @property
    def command(self) -> str:
        """The command the operator tab would host."""
        return self._text(self.command_capture)

    @property
    def tab(self) -> str:
        return self._text(self.tab_capture)

    @property
    def attach(self) -> str:
        return self._text(self.attach_capture)

    @property
    def aicx_called(self) -> bool:
        return self.aicx_capture.exists()

    @property
    def core(self) -> tuple[list[str], str] | None:
        """(argv, stdin) of the tracked core CLI call, if one happened."""
        if not self.core_argv_capture.exists():
            return None
        argv = [
            item.decode("utf-8")
            for item in self.core_argv_capture.read_bytes().split(b"\0")
            if item
        ]
        return argv, self.core_stdin_capture.read_text(encoding="utf-8")

    def provider_calls(self) -> list[str]:
        return self._text(self.provider_log).splitlines()

    def provider_launches(self) -> list[str]:
        return [
            call
            for call in self.provider_calls()
            if call.partition(" ")[2] not in _PROVIDER_SURFACE_PROBES
        ]

    def front_door_calls(self) -> list[str]:
        return self._text(self.front_door_log).splitlines()

    def admission(self) -> dict[str, object]:
        """The private receipt the admitted handoff names.

        Since 36614036 the tab never hosts an expanded provider command: it
        carries `spawn interactive-launch ... --admission-file <receipt>`, and
        the receipt is the authority for agent, identity and input.
        """
        tokens = shlex.split(self.command)
        assert "interactive-launch" in tokens, self.command
        receipt = Path(tokens[tokens.index("--admission-file") + 1])
        return json.loads(receipt.read_text(encoding="utf-8"))


def _probe_codex_resume_contract(
    tmp_path: Path,
    args: list[str],
    *,
    agent: str = "codex",
    operator_available: bool = True,
    runtime: str | None = "terminal",
) -> _ResumeProbe:
    """Run the public resume boundary as the terminal child it now requires.

    A resume without a TTY first opens the product terminal and is decided in
    the child that terminal starts (`_vetcoders_declaration_escalate_if_needed`,
    vc_frame.sh). The probe IS that child: the owned boundary is the marker AND
    the loaded generation's `bin/vibecrafted` front door (a raw marker stopped
    being a boundary in fcfe87c3/4d49a362). The fixture front door records any
    call, so a second terminal would be visible.

    The harness owns AICX assembly, Frame preparation/attach, the operator tab
    and the tracked core CLI (`_vetcoders_run_core_cli`, owner of every
    explicit-input route since 5b25a6cd/36614036). Provider CLIs are recording
    fakes that win on PATH, so nothing real can launch.
    """
    probe = _ResumeProbe(tmp_path)
    probe.home.mkdir(parents=True)
    (probe.generation / "bin").mkdir(parents=True)
    probe.fake_bin.mkdir()
    probe.context_file.write_text("AICX OVERLAY BODY\n", encoding="utf-8")
    front_door = probe.generation / "bin" / "vibecrafted"
    _write_fake_command(
        front_door,
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{probe.front_door_log}"\nexit 97\n',
    )
    for provider in _PROVIDER_FAKES:
        _write_fake_command(
            probe.fake_bin / provider,
            f'#!/bin/sh\nprintf "{provider} %s\\n" "$*" >> "{probe.provider_log}"\n'
            "exit 97\n",
        )

    env = os.environ.copy()
    for key in (
        "VIBECRAFTED_OPERATOR_SESSION",
        "VIBECRAFTED_WORKER_SESSION",
        "VIBECRAFTED_TERMINAL_ENTRY",
        "VIBECRAFTED_TERMINAL_ENTRY_OWNER",
        "VIBECRAFTED_RUN_ID",
        "VIBECRAFTED_AGENT",
        "VIBECRAFTED_AGENT_SESSION_ID",
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "VETCODERS_SPAWN_RUNTIME",
        "CODEX_THREAD_ID",
        "CODEX_SESSION_ID",
        "CLAUDE_CODE_SESSION_ID",
        "GROK_SESSION_ID",
    ):
        env.pop(key, None)
    env["HOME"] = str(probe.home)
    env["VIBECRAFTED_HOME"] = str(probe.home / ".vibecrafted")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PYTHON"] = sys.executable
    env["PATH"] = f"{probe.fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_TERMINAL_ENTRY"] = "1"
    env["VIBECRAFTED_TERMINAL_ENTRY_OWNER"] = str(front_door)
    env["TEST_AICX_CONTEXT"] = str(probe.context_file)
    env["TEST_AICX_CAPTURE"] = str(probe.aicx_capture)
    env["TEST_COMMAND_CAPTURE"] = str(probe.command_capture)
    env["TEST_TAB_CAPTURE"] = str(probe.tab_capture)
    env["TEST_ATTACH_CAPTURE"] = str(probe.attach_capture)
    env["TEST_CORE_ARGV"] = str(probe.core_argv_capture)
    env["TEST_CORE_STDIN"] = str(probe.core_stdin_capture)
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

    probe.result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            "\n".join(
                [
                    f'source "{SHELL_SH}"',
                    # A source checkout has no bin/vibecrafted; bind this shell
                    # to the fixture generation that owns the front door, as
                    # tests/tui/test_resume_declared_workspace.py does.
                    f'_vetcoders_vc_frame_loaded_root="{probe.generation}"',
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
                    '  printf \'%s\\n\' "$1" > "$TEST_TAB_CAPTURE"',
                    '  printf \'%s\\n\' "$2" > "$TEST_COMMAND_CAPTURE"',
                    "}",
                    # defer-attach: the client may only be handed over once the
                    # provider tab exists.
                    "_vetcoders_attach_prepared_vc_frame_session() {",
                    '  if [[ -s "$TEST_COMMAND_CAPTURE" ]]; then',
                    "    printf 'after-tab\\n' > \"$TEST_ATTACH_CAPTURE\"",
                    "  else",
                    "    printf 'before-tab\\n' > \"$TEST_ATTACH_CAPTURE\"",
                    "  fi",
                    "}",
                    "_vetcoders_run_core_cli() {",
                    '  printf \'%s\\0\' "$@" > "$TEST_CORE_ARGV"',
                    '  cat > "$TEST_CORE_STDIN"',
                    "}",
                    resume_invocation,
                ]
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return probe


def _assert_admission_points_at_aicx_overlay(probe: _ResumeProbe, agent: str) -> None:
    """The resume prompt is a pointer, never the inline payload.

    Inlining the overlay put whole continuity packs into agent argv —
    world-readable in `ps`, capped by ARG_MAX, mangled on newlines
    (prompts.sh). Since the admitted handoff (36614036) continuity means: the
    tab command carries only the receipt path, the receipt names the pack as
    its source file, and the pack bytes are frozen in a private snapshot.
    """
    assert "AICX OVERLAY BODY" not in probe.command
    admission = probe.admission()
    assert admission["agent"] == agent
    assert admission["skill"] == "resume"
    assert admission["source_origin"] == "file"
    assert Path(str(admission["source_path"])).resolve() == probe.context_file.resolve()
    snapshot = Path(str(admission["source_snapshot"]))
    assert snapshot.is_file(), f"receipt names a missing snapshot: {snapshot}"
    assert "AICX OVERLAY BODY" in snapshot.read_text(encoding="utf-8")


def _assert_interactive_operator_tab(
    probe: _ResumeProbe, agent: str
) -> dict[str, object]:
    """A bare or session-only resume is a visible PTY session on the operator tab."""
    result = probe.result
    assert result.returncode == 0, result.stderr
    assert probe.core is None, f"interactive resume became a tracked run: {probe.core}"
    assert probe.tab == agent
    assert probe.attach == "after-tab"
    assert not probe.front_door_calls(), "the terminal child opened another terminal"
    assert not probe.provider_launches(), probe.provider_calls()
    admission = probe.admission()
    assert admission["agent"] == agent
    assert admission["skill"] == "resume"
    assert admission["presentation"] == "visible"
    assert admission["requires_pty"] is True
    return admission


def _assert_fresh_tracked_workflow(
    probe: _ResumeProbe, agent: str, *, stdin: str
) -> list[str]:
    """Explicit input without a native identity is a tracked headless workflow.

    marbles.sh fresh route (36614036): never an operator tab, never a session.
    """
    assert probe.result.returncode == 0, probe.result.stderr
    assert probe.command == ""
    assert probe.provider_calls() == []
    assert probe.core is not None, "explicit input never reached the tracked core"
    argv, received = probe.core
    assert argv[:4] == ["workflow", agent, "--runtime", "headless"], argv
    assert "resume-session" not in argv
    assert "--agent-session-id" not in argv
    assert received == stdin
    return argv


def test_bare_codex_resume_uses_aicx_pack_in_fresh_interactive_session(
    tmp_path: Path,
) -> None:
    probe = _probe_codex_resume_contract(tmp_path, [])

    admission = _assert_interactive_operator_tab(probe, "codex")
    assert probe.aicx_called
    _assert_admission_points_at_aicx_overlay(probe, "codex")
    # A fresh session: the pack is continuity transport, never a native attach.
    assert admission["agent_session_id"] == ""
    assert admission["session_selection"] == {}
    assert "historical-codex-session" not in probe.command
    assert "historical-codex-session" not in json.dumps(admission)


def test_default_resume_runtime_keeps_bare_codex_interactive_and_prompt_headless(
    tmp_path: Path,
) -> None:
    bare = _probe_codex_resume_contract(tmp_path / "bare", [], runtime=None)
    prompt = _probe_codex_resume_contract(
        tmp_path / "prompt",
        ["--prompt", "carry on"],
        runtime=None,
    )

    _assert_interactive_operator_tab(bare, "codex")
    argv = _assert_fresh_tracked_workflow(prompt, "codex", stdin="carry on")
    assert "--prompt-stdin" in argv
    assert "carry on" not in argv


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
    probe = _probe_codex_resume_contract(
        tmp_path,
        [],
        agent=agent,
        runtime=None,
    )

    admission = _assert_interactive_operator_tab(probe, agent)
    assert probe.aicx_called
    _assert_admission_points_at_aicx_overlay(probe, agent)
    assert admission["agent_session_id"] == ""
    assert admission["session_selection"] == {}
    handoff = probe.command
    assert "historical-codex-session" not in handoff
    assert "--resume" not in handoff
    assert "--conversation" not in handoff
    assert "--session-id=" not in handoff
    if headless_flag:
        assert headless_flag not in handoff


@pytest.mark.parametrize("agent", ["claude", "agy", "grok", "junie"])
def test_explicit_prompt_without_session_never_adopts_aicx_session(
    tmp_path: Path,
    agent: str,
) -> None:
    probe = _probe_codex_resume_contract(
        tmp_path,
        ["--prompt", "carry on"],
        agent=agent,
        runtime=None,
    )

    argv = _assert_fresh_tracked_workflow(probe, agent, stdin="carry on")
    assert not probe.aicx_called
    assert "--prompt-stdin" in argv
    assert "historical-codex-session" not in probe.result.stdout + probe.result.stderr


def test_codex_session_only_is_exact_interactive_resume(tmp_path: Path) -> None:
    probe = _probe_codex_resume_contract(tmp_path, ["--session", "sess-123"])

    admission = _assert_interactive_operator_tab(probe, "codex")
    assert not probe.aicx_called
    # Exact: the operator's id is the admitted native identity, never re-picked.
    assert admission["agent_session_id"] == "sess-123"
    assert admission["source_origin"] == "inline"
    selection = admission["session_selection"]
    assert isinstance(selection, dict)
    assert selection["agent_session_id"] == "sess-123"
    assert selection["session_selector"] == "sess-123"
    assert selection["identity_source"] == "explicit_session"
    assert Path(str(selection["selection_root"])).resolve() == REPO_ROOT.resolve()


def test_codex_explicit_prompt_and_file_are_fresh_noninteractive_runs(
    tmp_path: Path,
) -> None:
    # Task resume is noninteractive (591b6dde): explicit input is declared
    # headless, and the tracked core owns the run.
    prompt = _probe_codex_resume_contract(
        tmp_path / "prompt", ["--prompt", "carry on"], runtime="headless"
    )
    input_file = tmp_path / "file" / "input.md"
    input_file.parent.mkdir(parents=True)
    input_file.write_text("FILE INPUT\n", encoding="utf-8")
    file_probe = _probe_codex_resume_contract(
        tmp_path / "file", ["--file", str(input_file)], runtime="headless"
    )

    assert not prompt.aicx_called and not file_probe.aicx_called
    prompt_argv = _assert_fresh_tracked_workflow(prompt, "codex", stdin="carry on")
    # The prompt rides stdin, never argv.
    assert "--prompt-stdin" in prompt_argv
    assert "carry on" not in prompt_argv
    file_argv = _assert_fresh_tracked_workflow(file_probe, "codex", stdin="")
    # The file is handed over by its exact path, not inlined.
    assert Path(file_argv[file_argv.index("--file") + 1]).resolve() == (
        input_file.resolve()
    )
    assert "--prompt-stdin" not in file_argv

    # A declared root is the repository contract: normalized once (physical
    # path) and forwarded as --repo.
    declared_root = tmp_path / "declared-repo"
    declared_root.mkdir()
    declared = _probe_codex_resume_contract(
        tmp_path / "declared",
        ["--prompt", "carry on", "--root", str(declared_root)],
        runtime="headless",
    )
    declared_argv = _assert_fresh_tracked_workflow(declared, "codex", stdin="carry on")
    assert declared_argv[declared_argv.index("--repo") + 1] == str(
        declared_root.resolve()
    )

    refused = _probe_codex_resume_contract(
        tmp_path / "terminal", ["--prompt", "carry on"], runtime="terminal"
    )
    assert refused.result.returncode == 2
    assert (
        "Task resume is noninteractive; use --runtime headless."
        in refused.result.stderr
    )
    assert refused.core is None
    assert refused.command == ""
    assert not refused.aicx_called


def test_codex_session_with_explicit_file_is_noninteractive_continuation(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.md"
    input_file.write_text("SESSION FILE INPUT\n", encoding="utf-8")

    probe = _probe_codex_resume_contract(
        tmp_path / "probe",
        ["--session", "sess-file-123", "--file", str(input_file)],
        runtime="headless",
    )

    assert probe.result.returncode == 0, probe.result.stderr
    assert not probe.aicx_called
    assert probe.command == ""
    assert probe.provider_calls() == []
    assert probe.core is not None
    argv, stdin = probe.core
    # Native continuation of the operator's own session (resume-session,
    # 5b25a6cd); the file is the prompt, by exact path.
    assert argv[:5] == [
        "resume-session",
        "codex",
        "--agent-session-id",
        "sess-file-123",
        "--prompt-file",
    ]
    assert Path(argv[5]).resolve() == input_file.resolve()
    assert len(argv) == 6
    assert stdin == ""

    # A declared root is pinned as the repository contract, normalized once.
    declared_root = tmp_path / "declared-repo"
    declared_root.mkdir()
    declared = _probe_codex_resume_contract(
        tmp_path / "declared",
        [
            "--session",
            "sess-file-123",
            "--file",
            str(input_file),
            "--root",
            str(declared_root),
        ],
        runtime="headless",
    )
    assert declared.result.returncode == 0, declared.result.stderr
    assert declared.core is not None
    declared_argv, _ = declared.core
    assert declared_argv[:7] == [
        "resume-session",
        "codex",
        "--agent-session-id",
        "sess-file-123",
        "--repo",
        str(declared_root.resolve()),
        "--prompt-file",
    ]
    assert Path(declared_argv[7]).resolve() == input_file.resolve()


def test_codex_positional_resume_compatibility_preserves_mode_contract(
    tmp_path: Path,
) -> None:
    # Spelled without --runtime, as the positional form is typed. Positional
    # words are not an explicit --prompt to the noninteractive guard
    # (marbles.sh `Task resume is noninteractive`), so their behaviour under an
    # explicit `--runtime terminal` is deliberately not pinned here.
    session_id = "019ec264-0b50-7bb2-9336-0aae5c841209"
    session = _probe_codex_resume_contract(
        tmp_path / "session", [session_id], runtime=None
    )
    continuation = _probe_codex_resume_contract(
        tmp_path / "continuation", [session_id, "carry", "on"], runtime=None
    )
    prompt = _probe_codex_resume_contract(
        tmp_path / "prompt", ["carry", "on"], runtime=None
    )

    admission = _assert_interactive_operator_tab(session, "codex")
    assert admission["agent_session_id"] == session_id
    assert continuation.result.returncode == 0, continuation.result.stderr
    assert continuation.command == ""
    assert continuation.core == (
        [
            "resume-session",
            "codex",
            "--agent-session-id",
            session_id,
            "--prompt-stdin",
        ],
        "carry on",
    )
    prompt_argv = _assert_fresh_tracked_workflow(prompt, "codex", stdin="carry on")
    assert "--prompt-stdin" in prompt_argv
    assert not prompt.aicx_called


@pytest.mark.parametrize("agent", ["claude", "codex", "agy", "grok", "junie"])
def test_interactive_resume_fails_without_operator_target_for_every_agent(
    tmp_path: Path,
    agent: str,
) -> None:
    """Provider-neutral: bare interactive resume never silently becomes headless."""
    probe = _probe_codex_resume_contract(
        tmp_path / agent,
        ["--session", "sess-123"],
        agent=agent,
        operator_available=False,
    )

    result = probe.result
    assert result.returncode != 0
    assert probe.command == ""
    assert probe.tab == ""
    assert probe.attach == ""
    assert not probe.aicx_called
    # No downgrade: no tracked headless run, no provider launch, and the
    # terminal child did not escalate again.
    assert probe.core is None
    assert not probe.provider_launches(), probe.provider_calls()
    assert not probe.front_door_calls()
    # 36614036 replaced "requires an explicit or detected operator target;
    # refusing to downgrade to a headless run" with this refusal.
    assert f"Interactive {agent} resume requires an admitted Frame target" in (
        result.stderr
    )
    assert "no provider was downgraded to headless" in result.stderr


BOUND_WORKSPACE_ID = "01a06f41-ebc6-706b-990e-b7ba921310b3"


def _probe_interactive_operator_target(
    tmp_path: Path,
    *,
    sessions_body: str,
    repo_basename: str,
    bound_session: str = "vibecrafted-921310b3",
    extra_env: dict[str, str] | None = None,
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
    # VIBECRAFTED_ROOT cannot carry the project here: sourcing vetcoders.sh
    # rebinds it (and VIBECRAFTED_RUNTIME_ROOT) to the runtime generation
    # (shell/lib/core.sh). The project is the caller's location, so the probe
    # states it the way an operator does -- by standing in the repository.
    for stale in (
        "SPAWN_ROOT",
        "VIBECRAFTED_ROOT",
        "VIBECRAFTED_RUNTIME_ROOT",
        # A dispatched worker exports its own workspace identities and pytest
        # inherits them; the operator's terminal starts without them.
        "VIBECRAFTED_WORKSPACE_ID",
        "VIBECRAFTED_SESSION_ID",
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
        "VIBECRAFTED_WORKSPACE_ROOT",
        "VIBECRAFTED_BUILD_ID",
    ):
        env.pop(stale, None)
    project_dir = tmp_path / repo_basename
    project_dir.mkdir(exist_ok=True)

    # The canonical workspace owner: the selected generation's CLI. Ownership
    # is a catalogue binding, never a name that happens to match the checkout
    # directory, so the probe answers as the catalogue does and records which
    # root it was asked about.
    owner_calls = tmp_path / "owner-calls"
    owner_cli = tmp_path / "owner-cli"
    _write_fake_command(
        owner_cli,
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{owner_calls}"\n'
        'if [[ "$1 $2" == "workspace resolve" ]]; then\n'
        f"  echo VIBECRAFTED_WORKSPACE_ID={BOUND_WORKSPACE_ID}\n"
        "  echo VIBECRAFTED_SESSION_ID=sess-canonical\n"
        "  echo VIBECRAFTED_WORKSPACE_INSTANCE_ID=inst-canonical\n"
        f"  echo VIBECRAFTED_OPERATOR_SESSION={bound_session}\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    env["VIBECRAFTED_PRODUCT_CORE_CLI"] = str(owner_cli)
    env["VIBECRAFTED_TEST_OWNER_CALLS"] = str(owner_calls)
    env.update(extra_env or {})

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
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_interactive_target_prefers_repo_bound_live_session(
    tmp_path: Path,
) -> None:
    """Detected target is the session the catalogue BINDS to this checkout.

    Migrated 2026-09-07 (S1 R8): this used to assert that a live session named
    after the repository directory is "repo-bound". It is not — a same-named
    checkout elsewhere produces the identical name and owns nothing here. The
    acceptance is now the stronger one: the workspace-bound session wins even
    while a live basename twin sits next to it.
    """
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="other [Created]\nvibecrafted [Created]\n"
        "vibecrafted-921310b3 [Created]\n",
        repo_basename="vibecrafted",
    )
    assert result.returncode == 0, result.stderr
    assert "target=[vibecrafted-921310b3]" in result.stdout, result.stdout
    assert "target=[vibecrafted]" not in result.stdout, result.stdout


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
    assert "unrelated live vc-frame session" in result.stderr
    assert "alpha" in result.stderr
    assert "beta" in result.stderr
    assert "VIBECRAFTED_OPERATOR_SESSION" in result.stderr


def test_interactive_target_single_live_session_is_not_adopted(
    tmp_path: Path,
) -> None:
    """A lone live session elsewhere is a coincidence, not ownership.

    Reversed on 2026-09-06. Adopting "the only live session" is what let a
    3more-studio window capture a resume launched from mlx-batch-runner: the
    provider tab was dispatched into somebody else's project. Ownership must be
    proven (this caller's frame, an explicit target, or this project's own
    session), never inferred from a global count.
    """
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="solo-session [Created]\n",
        repo_basename="other-repo",
    )
    assert result.returncode == 0, result.stderr
    assert "target=[]" in result.stdout
    assert "solo-session" in result.stderr


def test_interactive_target_ignores_the_runtime_generation_as_project(
    tmp_path: Path,
) -> None:
    """The generation is not a project, even though every front door pins it.

    vc_start.rs, vc-terminal-product-entry.sh and shell/lib/core.sh all export
    VIBECRAFTED_ROOT == VIBECRAFTED_RUNTIME_ROOT. Reading that as the project
    named the operator's session after the release directory.
    """
    generation = tmp_path / "runtime-generation"
    generation.mkdir()
    result = _probe_interactive_operator_target(
        tmp_path,
        sessions_body="runtime-generation [Created]\nvibecrafted [Created]\n"
        "vibecrafted-921310b3 [Created]\n",
        repo_basename="vibecrafted",
        extra_env={
            "VIBECRAFTED_ROOT": str(generation),
            "VIBECRAFTED_RUNTIME_ROOT": str(generation),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "target=[vibecrafted-921310b3]" in result.stdout, result.stdout
    assert "target=[runtime-generation]" not in result.stdout, result.stdout
    # Stronger than the old basename assertion: the canonical owner was asked
    # about the PROJECT, never about the release directory.
    asked = (tmp_path / "owner-calls").read_text(encoding="utf-8")
    assert str(tmp_path / "vibecrafted") in asked, asked
    assert str(generation) not in asked, asked


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


def test_resume_terminal_runtime_refuses_task_input_before_any_frame_surface(
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
        check=False,
        cwd=REPO_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    # Explicit input is a non-interactive continuation and must never occupy
    # the human operator seat. The G7 worker-column tab that used to host it
    # was removed in 36614036 (the route became the tracked core
    # `resume-session`), and 591b6dde refuses a visible runtime for task
    # resume outright. The guarantee is therefore stronger than before: the
    # request is refused before ANY Frame surface -- operator or worker -- or
    # the provider is touched. The headless route itself is pinned by
    # test_resume_headless_routes_explicit_session_through_tracked_core.
    assert result.returncode == 2, result.stderr
    assert "Task resume is noninteractive; use --runtime headless." in result.stderr
    assert "Resume launched in operator session" not in result.stdout
    assert "Resume launched in worker session" not in result.stdout
    assert not vc_frame_capture.exists()
    assert not codex_capture.exists()


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
    env, _ = _fake_core_env(tmp_path, core_capture)

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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    payload = json.loads(core_capture.read_text(encoding="utf-8"))
    # Native continuation (marbles.sh, 5b25a6cd): the core resolves the
    # checkout and its own source, so an undeclared resume names neither
    # --root nor --source-dir; the prompt rides stdin, never argv. The
    # declared-root --repo form is pinned by the probe tests above: this fake
    # core shadows the whole package, including the repo selection that a
    # declared root runs (prompts.sh _vetcoders_select_repo).
    assert payload["argv"] == [
        "resume-session",
        "codex",
        "--agent-session-id",
        "sess-123",
        "--prompt-stdin",
    ]
    assert payload["stdin"] == "carry on"
    assert "MANUAL EXPLICIT RESUME RECEIPT" in result.stdout
    assert "rsme-fake-1" in result.stdout


def test_resume_headless_routes_fresh_input_through_tracked_workflow(
    tmp_path: Path,
) -> None:
    core_capture = tmp_path / "core.json"
    env, _ = _fake_core_env(tmp_path, core_capture)

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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    payload = json.loads(core_capture.read_text(encoding="utf-8"))
    argv = payload["argv"]
    # Fresh input (marbles.sh, 36614036) is a headless core workflow; the
    # core owns root and source resolution (no --root/--source-dir). The
    # `--mode resume-new-session` label rode only the retired
    # _vetcoders_launch_tracked_resume.
    assert argv[:4] == ["workflow", "codex", "--runtime", "headless"]
    assert "--prompt-stdin" in argv
    assert "--repo" not in argv and "--root" not in argv
    assert "--agent-session-id" not in argv
    assert "--file" not in argv
    assert "carry on" not in argv
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
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    # Nothing was admitted or recorded without the owned core.
    runs = home / ".vibecrafted" / "control_plane" / "runtime_runs"
    assert not list(runs.glob("*")), sorted(p.name for p in runs.glob("*"))
    # The refusal must say why. Its old owner, _vetcoders_launch_tracked_resume
    # ("Tracked resume refused: Vibecrafted core is unavailable."), left this
    # route in 5b25a6cd/36614036; any refusal that names the unavailable core
    # meets the contract. An empty stderr does not.
    assert "Vibecrafted core" in result.stderr, result.stderr
    assert "unavailable" in result.stderr, result.stderr


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
                # spawn_launch_headless detaches through spawn_python_bin
                # (util.sh, bfbd1309). Extracted alone, it silently fell back
                # to `nohup &` in the spawner's session -- the bug under test.
                f'source "{launcher_sh.with_name("util.sh")}"; '
                f"export VIBECRAFTED_PYTHON={shlex.quote(sys.executable)}; "
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
                # spawn_launch_headless detaches through spawn_python_bin
                # (util.sh, bfbd1309). Extracted alone, it silently fell back
                # to `nohup &` in the spawner's session -- the bug under test.
                f'source "{launcher_sh.with_name("util.sh")}"; '
                f"export VIBECRAFTED_PYTHON={shlex.quote(sys.executable)}; "
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
    # Candidates are an exact-project answer: the checkout carries the canonical
    # origin a real one has. Without it the assembler refuses to guess (see
    # test_aicx_resume_fallback_refuses_basename_union_without_origin).
    _git_origin_checkout(repo, "https://github.com/Fixture/repo.git")

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
    pack = _resume_pack(home, "claude")
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


def _resume_pack(home: Path, agent: str) -> Path:
    """The injected pack, not the full retrieval artifact written beside it."""
    packs = [
        path
        for path in (home / ".vibecrafted" / "tmp").glob(f"resume-aicx-{agent}-*.md")
        if not path.name.endswith(".full.md")
    ]
    assert len(packs) == 1, packs
    return packs[0]


def _git_origin_checkout(path: Path, origin: str | None) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if origin is not None:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", origin], check=True
        )


def _run_resume_fallback(
    tmp_path: Path, home: Path, fake_bin: Path, agent: str, repo: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                f'source "{SHELL_SH}"\n'
                f"_vetcoders_aicx_resume_fallback {agent} {shlex.quote(str(repo))}"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_aicx_resume_fallback_refuses_basename_union_without_origin(
    tmp_path: Path,
) -> None:
    """No canonical owner/repo means no catalog question at all.

    A basename filter (`-p /codescribe`) unions every same-named repository
    across orgs. The shell entry must reach the assembler (it runs the module,
    not a script path) and the assembler must say why it asked nothing.
    """
    home = tmp_path / "home"
    repo = tmp_path / "codescribe"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "aicx-calls"
    for directory in (home, repo, fake_bin):
        directory.mkdir()
    _git_origin_checkout(repo, None)
    _write_fake_command(
        fake_bin / "aicx",
        f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{calls}"\nexit 1\n',
    )

    result = _run_resume_fallback(tmp_path, home, fake_bin, "grok", repo)

    assert result.returncode == 0, result.stderr
    assert "ImportError" not in result.stderr, result.stderr
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert fields.get("MODE") == "new_session"
    assert fields.get("EMPTY_KIND") == "unknown_identity"
    assert not calls.exists(), calls.read_text(encoding="utf-8")
    pack = _resume_pack(home, "grok")
    text = pack.read_text(encoding="utf-8")
    assert "aicx_project_filter: unresolved" in text
    assert "aicx_project_filter: `/codescribe`" not in text


def test_aicx_resume_fallback_uses_canonical_origin_filter(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "codescribe"
    fake_bin = tmp_path / "bin"
    calls = tmp_path / "aicx-calls"
    for directory in (home, repo, fake_bin):
        directory.mkdir()
    _git_origin_checkout(repo, "https://github.com/Other-Org/codescribe.git")
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
    intents = [line for line in invoked if line.startswith("intents ")]
    assert continuity and intents, invoked
    # Every project-scoped question names the canonical origin, never the
    # cross-org basename union (`-p /codescribe`) the old filter used.
    scoped = [line for line in invoked if " -p " in line]
    assert all(" -p Other-Org/codescribe " in f"{line} " for line in scoped), invoked
    assert not any("-p /codescribe" in line for line in invoked), invoked
    pack = _resume_pack(home, "grok")
    text = pack.read_text(encoding="utf-8")
    assert "aicx_project_filter: `Other-Org/codescribe`" in text
    assert "prefer native resume" not in text.lower()
