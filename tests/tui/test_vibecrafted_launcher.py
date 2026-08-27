# ruff: noqa: FLY002,PLW1510
# Fixture-heavy launcher harness builds shell/python stubs via "\n".join; FLY002
# and bare subprocess.run (PLW1510) are intentional and predate this cut.
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"


def _write_fake_agent(bin_dir: Path, name: str, capture_file: Path) -> None:
    script = bin_dir / name
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "--help" ]]; then printf "  --session-id <uuid>\\n"; exit 0; fi',
                'if [[ "${1:-}" == "--version" ]]; then printf "2.1.232 (Claude Code)\\n"; exit 0; fi',
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_core_python(path: Path) -> None:
    """Capture a tracked core launch without importing core or spawning an agent."""
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "-c" ]]; then',
                '  if [[ "${2:-}" == *"package_root"* ]]; then',
                '    printf "%s\\n" "$FAKE_CORE_SOURCE_DIR"',
                "  fi",
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "-m" && "${2:-}" == "vibecrafted_core.cli" ]]; then',
                "  shift 2",
                '  printf "%s\\0" "$@" > "$FAKE_CORE_ARGV_FILE"',
                '  cat > "$FAKE_CORE_PROMPT_FILE"',
                "  printf '%s\\n' \\",
                "    '=============== MANUAL EXPLICIT RESUME RECEIPT ===============' \\",
                "    'run_id:             rsme-fixture-1' \\",
                '    "agent_session_id:   ${FAKE_CORE_SESSION_ID}" \\',
                "    'resume_mode:        manual_explicit'",
                "  exit 0",
                "fi",
                'printf "unexpected fake-core invocation: %s\\n" "$*" >&2',
                "exit 98",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _read_nul_argv(path: Path) -> list[str]:
    return [item.decode("utf-8") for item in path.read_bytes().split(b"\0") if item]


def _tracked_resume_fixture(
    tmp_path: Path,
    *,
    session_id: str,
) -> tuple[dict[str, str], Path, Path, Path]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    provider_called = tmp_path / "provider-called"
    core_argv = tmp_path / "core-argv.bin"
    core_prompt = tmp_path / "core-prompt.txt"
    core_source = tmp_path / "core-source"
    fake_core = tmp_path / "fake-core-python"

    home.mkdir()
    fake_bin.mkdir()
    core_source.mkdir()
    _write_fake_agent(fake_bin, "codex", provider_called)
    _write_fake_core_python(fake_core)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["CAPTURE_FILE"] = str(provider_called)
    env["VIBECRAFTED_PYTHON"] = str(fake_core)
    env["FAKE_CORE_ARGV_FILE"] = str(core_argv)
    env["FAKE_CORE_PROMPT_FILE"] = str(core_prompt)
    env["FAKE_CORE_SOURCE_DIR"] = str(core_source)
    env["FAKE_CORE_SESSION_ID"] = session_id
    return env, provider_called, core_argv, core_prompt


def _assert_tracked_resume(
    result: subprocess.CompletedProcess[str],
    *,
    provider_called: Path,
    core_argv: Path,
    core_prompt: Path,
    session_id: str,
    prompt: str,
) -> None:
    assert "MANUAL EXPLICIT RESUME RECEIPT" in result.stdout
    assert f"agent_session_id:   {session_id}" in result.stdout
    payload = _read_nul_argv(core_argv)
    assert payload[:6] == [
        "resume-session",
        "codex",
        "--agent-session-id",
        session_id,
        "--prompt-stdin",
        "--root",
    ]
    assert payload[6] == str(REPO_ROOT)
    assert payload[7] == "--source-dir"
    assert Path(payload[8]).name == "core-source"
    assert core_prompt.read_text(encoding="utf-8") == prompt
    assert not provider_called.exists()


def _write_fake_python(bin_dir: Path, capture_file: Path) -> None:
    script = bin_dir / "python3"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_trimmed_launcher(script_path: Path) -> None:
    source = LAUNCHER.read_text(encoding="utf-8").splitlines()
    script_path.write_text("\n".join(source[:-1]) + "\n", encoding="utf-8")
    script_path.chmod(0o755)


def test_python_resolver_skips_bash_product_launchers(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    launcher_copy = tmp_path / "vibecrafted-deck"
    fake_python = fake_bin / "python3"
    fake_bin.mkdir()
    _write_trimmed_launcher(launcher_copy)

    for name in ("vc-server-supervisor", "vibecrafted"):
        wrapper = fake_bin / name
        wrapper.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        wrapper.chmod(0o755)
    fake_python.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    fake_python.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    result = subprocess.run(
        ["bash", "-c", f'source "{launcher_copy}"; _vibecrafted_python'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == fake_python


def test_python_resolver_prefers_own_runtime_python(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_bin = runtime_root / "bin"
    runtime_bin.mkdir(parents=True)
    (runtime_root / "server/site").mkdir(parents=True)
    launcher_copy = runtime_bin / "vibecrafted"
    _write_trimmed_launcher(launcher_copy)
    runtime_python = runtime_bin / "python3"
    runtime_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime_python.chmod(0o755)
    runtime_server = runtime_bin / "vc-server"
    runtime_server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime_server.chmod(0o755)

    ambient_bin = tmp_path / "ambient-bin"
    ambient_bin.mkdir()
    ambient_python = ambient_bin / "python3"
    ambient_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ambient_python.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", f'source "{launcher_copy}"; _vibecrafted_python'],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{ambient_bin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == runtime_python


def _write_fake_command(bin_dir: Path, name: str, capture_file: Path) -> None:
    script_names = [name]
    if name == "vc-frame":
        script_names.insert(0, "vc-frame")
    for script_name in script_names:
        script = bin_dir / script_name
        script.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    "set -euo pipefail",
                    "{",
                    '  printf "%s\\n" "$@"',
                    '  printf "VC_FRAME_CONFIG_DIR=%s\\n" "${VC_FRAME_CONFIG_DIR:-}"',
                    '} > "$CAPTURE_FILE"',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        script.chmod(0o755)


def _write_fake_vc_frame_with_live_session(
    bin_dir: Path, capture_file: Path, session_name: str
) -> None:
    script = bin_dir / "vc-frame"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "ls" || "${1:-}" == "list-sessions" ]]; then',
                f'  printf "{session_name} (attached)\\n"',
                "  exit 0",
                "fi",
                "{",
                '  printf "%s\\n" "$@"',
                '  printf "VC_FRAME_CONFIG_DIR=%s\\n" "${VC_FRAME_CONFIG_DIR:-}"',
                '} > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    vc_frame = bin_dir / "vc-frame"
    vc_frame.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    vc_frame.chmod(0o755)


def _write_gc_vc_frame(bin_dir: Path, capture_file: Path, listing: str) -> None:
    capture_file.touch()
    script = bin_dir / "vc-frame"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                "args = sys.argv[1:]",
                'capture = Path(os.environ["CAPTURE_FILE"])',
                'listing = os.environ.get("FAKE_VC_FRAME_LISTING", "")',
                'with capture.open("a", encoding="utf-8") as fh:',
                '    fh.write(" ".join(args) + "\\n")',
                'if args[:1] == ["list-sessions"]:',
                "    print(listing, end='')",
                "    sys.exit(0)",
                "sys.exit(0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    vc_frame = bin_dir / "vc-frame"
    vc_frame.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    vc_frame.chmod(0o755)


def _write_capture_script(script_path: Path, capture_file: Path) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf "%s\\n" "$*" >> "{capture_file}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_core_package(root: Path) -> None:
    """Give a fake tools root the one file the deck probes for vibecrafted-core."""
    pkg = root / "vibecrafted-core" / "vibecrafted_core"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "dispatcher.py").write_text("", encoding="utf-8")


def _write_fake_python3(bin_dir: Path, capture_file: Path) -> None:
    script = bin_dir / "python3"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$*" >> "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_curl(bin_dir: Path) -> None:
    script = bin_dir / "curl"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                "args = sys.argv[1:]",
                "routes = json.loads(os.environ.get('FAKE_CURL_ROUTES', '{}'))",
                "capture = os.environ.get('CURL_CAPTURE_FILE')",
                "url = None",
                "output_path = None",
                "idx = 0",
                "while idx < len(args):",
                "    arg = args[idx]",
                "    if arg == '-o' and idx + 1 < len(args):",
                "        output_path = args[idx + 1]",
                "        idx += 2",
                "        continue",
                "    if not arg.startswith('-'):",
                "        url = arg",
                "    idx += 1",
                "if capture and url:",
                "    with Path(capture).open('a', encoding='utf-8') as fh:",
                "        fh.write(url + '\\n')",
                "if not url or url not in routes:",
                "    sys.exit(22)",
                "payload = routes[url]",
                "if output_path:",
                "    Path(output_path).write_text(payload, encoding='utf-8')",
                "else:",
                "    sys.stdout.write(payload)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_marbles_spawn(script_path: Path) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_helper(script_path: Path, spawn_script: Path) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "_vetcoders_spawn_script() {",
                f'  printf "%s\\n" "{spawn_script}"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_generic_skill_helper(script_path: Path) -> None:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(
        "\n".join(
            [
                "_vetcoders_skill_entry() {",
                '  printf "%s\\n" "$1" "$2" > "$CAPTURE_FILE"',
                "  shift 2",
                '  printf "%s\\n" "$@" >> "$CAPTURE_FILE"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_stateful_vc_frame(
    bin_dir: Path, capture_file: Path, session_state_file: Path
) -> None:
    default_session = _expected_operator_session()
    script = bin_dir / "vc-frame"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                "args = sys.argv[1:]",
                'capture = Path(os.environ["CAPTURE_FILE"])',
                'state_file = Path(os.environ["SESSION_STATE_FILE"])',
                'state = state_file.read_text(encoding="utf-8").strip() if state_file.exists() else "missing"',
                f'session = os.environ.get("FAKE_VC_FRAME_SESSION", "{default_session}")',
                'if "--session" in args:',
                '    idx = args.index("--session")',
                "    if idx + 1 < len(args):",
                "        session = args[idx + 1]",
                'elif args[:1] == ["attach"] and len(args) > 1:',
                "    session = args[-1]",
                'with capture.open("a", encoding="utf-8") as fh:',
                '    fh.write("VC_FRAME " + " ".join(args) + "\\n")',
                'if args[:1] == ["ls"]:',
                '    if state == "live":',
                '        print(f"{session} [Created 1m ago]")',
                '    elif state == "dead":',
                '        print(f"{session} [Created 1m ago] (EXITED - attach to resurrect)")',
                "    sys.exit(0)",
                'if args[:1] == ["attach"]:',
                '    if "--force-run-commands" in args:',
                '        state_file.write_text("live", encoding="utf-8")',
                "    sys.exit(0)",
                'if args[:1] == ["delete-session"]:',
                '    state_file.write_text("missing", encoding="utf-8")',
                "    sys.exit(0)",
                'if "--new-session-with-layout" in args:',
                '    state_file.write_text("live", encoding="utf-8")',
                "    sys.exit(0)",
                'if "action" in args and ("new-pane" in args or "new-tab" in args):',
                "    sys.exit(0)",
                "sys.exit(0)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    vc_frame = bin_dir / "vc-frame"
    vc_frame.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    vc_frame.chmod(0o755)


def _write_fake_osascript(
    bin_dir: Path, capture_file: Path, session_state_file: Path
) -> None:
    script = bin_dir / "osascript"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import os",
                "import sys",
                "from pathlib import Path",
                "",
                "payload = sys.stdin.read()",
                'capture = Path(os.environ["CAPTURE_FILE"])',
                'state_file = Path(os.environ["SESSION_STATE_FILE"])',
                'with capture.open("a", encoding="utf-8") as fh:',
                '    fh.write("OSA " + payload.replace("\\n", "\\\\n") + "\\n")',
                'if "new-session-with-layout" in payload or "attach --force-run-commands" in payload:',
                '    state_file.write_text("live", encoding="utf-8")',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _spawned_command_script(capture_payload: str) -> Path:
    match = re.search(r"VC_FRAME .* action new-tab .* -- (\S+)", capture_payload)
    assert match, capture_payload
    return Path(match.group(1))


def _expected_operator_session(run_id: str | None = None) -> str:
    base = (
        re.sub(r"[^a-z0-9]+", "-", REPO_ROOT.name.lower()).strip("-") or "vibecrafted"
    )
    return f"{base}-{run_id}" if run_id else base


def _resolved_workspace_session(env: dict[str, str]) -> str:
    result = subprocess.run(
        ["bash", str(LAUNCHER), "workspace", "resolve", "--env"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    match = re.search(
        r"^VIBECRAFTED_OPERATOR_SESSION=([^\s]+)$",
        result.stdout,
        re.MULTILINE,
    )
    assert match, result.stdout
    value = match.group(1).strip()
    assert value
    assert not re.fullmatch(r"workspace-[0-9a-f]{8}", value), value
    return value


def test_init_claude_uses_interactive_tab_without_print_mode(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)
    _write_fake_agent(fake_bin, "claude", tmp_path / "unused-claude.txt")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    # Sanitize real vc_frame env to prevent leaks from the host session.
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    subprocess.run(
        ["bash", str(LAUNCHER), "init", "claude", "--operator", "auto"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8")
    # When vc_frame operator session exists, spawn routes directly through vc_frame
    # without opening a new terminal via osascript.
    assert (
        f"VC_FRAME --session {_expected_operator_session()} action new-tab" in payload
    )

    command_script = _spawned_command_script(payload)
    script_body = command_script.read_text(encoding="utf-8")
    assert (
        "vibecrafted_core.spawn interactive-launch claude --runtime local-native "
        "--permissions bypass --token-budget safe --operator auto --continuity fresh --root"
    ) in script_body
    assert "/vc-init" in script_body
    assert " -p " not in script_body


def test_init_shell_and_deck_accept_the_same_typed_continuity_flags() -> None:
    expected = "--continuity full-lineage --continuity-parent <run-id>"
    for launcher in (
        LAUNCHER,
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
    ):
        result = subprocess.run(
            ["bash", str(launcher), "init", "--help"],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert "--continuity full-lineage|fresh|bare-fork" in result.stdout
        assert expected in result.stdout
        assert "bare-fork is expert-only" in result.stdout


def test_init_codex_fails_closed_without_measured_usage_capability(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)
    _write_fake_agent(fake_bin, "codex", tmp_path / "unused-codex.txt")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    # Sanitize real vc_frame env to prevent leaks from the host session.
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "init", "codex"],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "no verified live, child-attributable, monotonic usage" in result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "action new-tab" not in payload


@pytest.mark.parametrize(
    "agent",
    ["agy", "junie", "grok"],
)
def test_init_fleet_agents_fail_closed_without_measured_usage_capability(
    agent: str, tmp_path: Path
) -> None:
    """Unsupported measured-quota cells stay visible but cannot launch."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)
    _write_fake_agent(fake_bin, agent, tmp_path / f"unused-{agent}.txt")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "init", agent],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Missing helper" not in result.stderr
    assert "no verified live, child-attributable, monotonic usage" in result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "action new-tab" not in payload


def test_init_grok_rejects_quota_before_any_single_shot_or_tab(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)
    _write_fake_agent(fake_bin, "grok", tmp_path / "unused-grok.txt")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "init", "grok"],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "no verified live, child-attributable, monotonic usage" in result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "action new-tab" not in payload
    assert "--single" not in payload


def test_init_gemini_returns_actionable_agy_migration() -> None:
    result = subprocess.run(
        ["bash", str(LAUNCHER), "init", "gemini"],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "gemini CLI is deprecated" in result.stderr
    assert "Use agy" in result.stderr
    assert "Unknown agent" not in result.stderr


def test_vc_help_wrapper_symlink_renders_main_help(tmp_path: Path) -> None:
    wrapper = tmp_path / "vc-help"
    wrapper.symlink_to(LAUNCHER)

    result = subprocess.run(
        ["bash", str(wrapper)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍." in result.stdout
    assert "Commands:" in result.stdout
    assert "Ship cycle:" in result.stdout
    assert (
        "scaffold → implement → review → workflow → followup → marbles → "
        "audit → polarize → dou → hydrate → release"
    ) in result.stdout
    assert 'vibecrafted implement codex -p "Ship dark mode"' in result.stdout


def test_vc_help_wrapper_forwards_topic_help(tmp_path: Path) -> None:
    wrapper = tmp_path / "vc-help"
    wrapper.symlink_to(LAUNCHER)

    result = subprocess.run(
        ["bash", str(wrapper), "init"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Start an interactive repository orientation session" in result.stdout
    assert "vc-init [claude|codex|agy|junie|grok]" in result.stdout
    assert "Ship cycle:" not in result.stdout


def test_dispatch_help_documents_async_lifecycle_contract() -> None:
    result = subprocess.run(
        ["bash", str(LAUNCHER), "help", "dispatch"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "vibecrafted.dispatch.v1 TOML plan" in result.stdout
    assert "vibecrafted dispatch <plan.dispatch.toml>" in result.stdout
    assert "--doctor validates only" in result.stdout
    assert "transcript capture" in result.stdout
    assert "artifact contract failed" in result.stdout


def test_dispatch_launcher_runs_async_lifecycle(tmp_path: Path) -> None:
    home = tmp_path / "home"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "from pathlib import Path\n"
        f"Path({str(report)!r}).write_text('---\\nstatus: completed\\n---\\nbody\\n', encoding='utf-8')\n"
        "print('launcher dispatcher hello')\n",
        encoding="utf-8",
    )
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")

    result = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "dispatch",
            "run",
            "--run-id",
            "launcher-dispatch-test",
            "--root",
            str(tmp_path),
            "--report",
            str(report),
            "--transcript",
            str(transcript),
            "--require-transcript-output",
            "--json",
            "--",
            sys.executable,
            str(worker),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)
    assert payload["run_id"] == "launcher-dispatch-test"
    assert payload["artifact_ok"] is True
    assert payload["state"] == "report_validated"
    assert "process_spawned" in payload["states"]
    assert "first_output_seen" in payload["states"]
    assert "report_validated" in payload["states"]
    assert "launcher dispatcher hello" in transcript.read_text(encoding="utf-8")


def test_vetcoders_shell_entrypoint_stays_thin_facade() -> None:
    facade = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    )
    lib_dir = facade.parent / "lib"
    body = facade.read_text(encoding="utf-8")

    assert len(body.splitlines()) <= 120
    assert lib_dir.is_dir()
    for module in [
        "core",
        "vc_frame",
        "prompts",
        "dispatch_core",
        "dispatch_wrappers",
        "marbles",
        "dispatch",
    ]:
        assert f"_vetcoders_source_shell_module {module}" in body
        assert (lib_dir / f"{module}.sh").is_file()

    runtime_root = facade.parent.parent
    for workflow, module in [
        ("vc-research", "research_prompts"),
        ("vc-research", "research"),
    ]:
        assert f"_vetcoders_source_workflow_module {workflow} {module}" in body
        assert (runtime_root / workflow / "shell" / f"{module}.sh").is_file()


def test_telemetry_wrapper_smokes_headless_marbles_runtime(tmp_path: Path) -> None:
    home = tmp_path / "home"
    wrapper = tmp_path / "telemetry"
    capture_file = tmp_path / "marbles-args.txt"
    isolated_root = tmp_path / "isolated-root"
    spawn_script = isolated_root / "runtime" / "scripts" / "marbles_spawn.sh"

    home.mkdir()
    wrapper.symlink_to(LAUNCHER)
    (isolated_root / "runtime" / "scripts").mkdir(parents=True)
    (isolated_root / "scripts").mkdir(parents=True)
    (isolated_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    (isolated_root / "scripts" / "vibecrafted").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    _write_fake_marbles_spawn(spawn_script)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VIBECRAFTED_ROOT"] = str(isolated_root)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"

    subprocess.run(
        ["bash", str(wrapper), "smoke", "--count", "1", "--no-watch"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--agent" in payload
    assert "codex" in payload
    assert "--runtime" in payload
    assert "headless" in payload
    assert "--count" in payload
    assert payload[payload.index("--count") + 1] == "1"
    assert "--no-watch" in payload
    assert "--root" in payload
    smoke_root = Path(payload[payload.index("--root") + 1])
    assert smoke_root.exists()
    assert smoke_root != REPO_ROOT
    assert (smoke_root / ".git").exists()
    assert "--file" in payload
    smoke_plan = Path(payload[payload.index("--file") + 1])
    assert smoke_plan.is_file()
    assert smoke_root in smoke_plan.parents
    plan_body = smoke_plan.read_text(encoding="utf-8")
    assert "SMOKE_OK.md" in plan_body
    assert "Do not run `telemetry smoke`" in plan_body
    assert "--prompt" not in payload


def test_telemetry_wrapper_clears_ambient_marbles_context(tmp_path: Path) -> None:
    home = tmp_path / "home"
    wrapper = tmp_path / "telemetry"
    capture_file = tmp_path / "marbles-env.txt"
    isolated_root = tmp_path / "isolated-root"
    spawn_script = isolated_root / "runtime" / "scripts" / "marbles_spawn.sh"

    home.mkdir()
    wrapper.symlink_to(LAUNCHER)
    (isolated_root / "runtime" / "scripts").mkdir(parents=True)
    (isolated_root / "scripts").mkdir(parents=True)
    (isolated_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    (isolated_root / "scripts" / "vibecrafted").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    spawn_script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "{",
                '  printf "MARBLES_RUN_ID=%s\\n" "${VIBECRAFTED_MARBLES_RUN_ID:-}"',
                '  printf "RUN_ID=%s\\n" "${VIBECRAFTED_RUN_ID:-}"',
                '  printf "RUN_LOCK=%s\\n" "${VIBECRAFTED_RUN_LOCK:-}"',
                '  printf "SKILL_CODE=%s\\n" "${VIBECRAFTED_SKILL_CODE:-}"',
                '  printf "SKILL_NAME=%s\\n" "${VIBECRAFTED_SKILL_NAME:-}"',
                '  printf "OPERATOR_SESSION=%s\\n" "${VIBECRAFTED_OPERATOR_SESSION:-}"',
                '  printf "SPAWN_RUN_ID=%s\\n" "${SPAWN_RUN_ID:-}"',
                '  printf "SPAWN_SKILL_CODE=%s\\n" "${SPAWN_SKILL_CODE:-}"',
                '} > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spawn_script.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VIBECRAFTED_ROOT"] = str(isolated_root)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    env["VIBECRAFTED_MARBLES_RUN_ID"] = "marb-parent"
    env["VIBECRAFTED_RUN_ID"] = "marb-parent-003"
    env["VIBECRAFTED_RUN_LOCK"] = str(tmp_path / "parent.lock")
    env["VIBECRAFTED_SKILL_CODE"] = "impl"
    env["VIBECRAFTED_SKILL_NAME"] = "implement"
    env["VIBECRAFTED_OPERATOR_SESSION"] = "parent-session"
    env["SPAWN_RUN_ID"] = "stale-spawn"
    env["SPAWN_SKILL_CODE"] = "stale"

    subprocess.run(
        ["bash", str(wrapper), "smoke", "--count", "1", "--no-watch"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = dict(
        line.split("=", 1)
        for line in capture_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert payload["MARBLES_RUN_ID"] == ""
    assert payload["RUN_ID"] == ""
    assert payload["RUN_LOCK"] == ""
    assert payload["SKILL_CODE"] == ""
    assert payload["SKILL_NAME"] == ""
    assert payload["OPERATOR_SESSION"] == ""
    assert payload["SPAWN_RUN_ID"] == ""
    assert payload["SPAWN_SKILL_CODE"] == ""


def test_installed_launcher_prefers_current_control_plane_helper_over_home_store(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    installed_root = home / ".vibecrafted"
    runtime_root = home / ".local" / "share" / "vibecrafted"
    launcher = home / ".local" / "bin" / "vibecrafted"
    stale_capture = tmp_path / "stale-args.txt"
    fresh_capture = tmp_path / "fresh-args.txt"
    stale_spawn = installed_root / "runtime" / "scripts" / "marbles_spawn.sh"
    fresh_spawn = (
        runtime_root
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "marbles_spawn.sh"
    )
    stale_helper = installed_root / "skills" / "runtime" / "shell" / "vetcoders.sh"
    fresh_helper = (
        runtime_root
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    )

    home.mkdir(parents=True)
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    _write_fake_marbles_spawn(stale_spawn)
    _write_fake_marbles_spawn(fresh_spawn)
    _write_fake_helper(stale_helper, stale_spawn)
    _write_fake_helper(fresh_helper, fresh_spawn)

    stale_spawn.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf "%s\\n" "$@" > "{stale_capture}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stale_spawn.chmod(0o755)
    fresh_spawn.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'printf "%s\\n" "$@" > "{fresh_capture}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fresh_spawn.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)

    subprocess.run(
        ["bash", str(launcher), "telemetry", "smoke", "--count", "1", "--no-watch"],
        check=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert fresh_capture.exists()
    assert not stale_capture.exists()


def test_repo_launcher_is_directly_executable() -> None:
    expected_version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    result = subprocess.run(
        [str(LAUNCHER), "help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍." in result.stdout
    assert expected_version in result.stdout
    assert "Commands:" in result.stdout
    assert "vibecrafted init claude" in result.stdout
    # Bounded deck: plumbing stays out of first contact.
    assert "vibecrafted dashboard" not in result.stdout
    assert "telemetry smoke" not in result.stdout


def test_installed_deck_version_is_owned_by_deck_not_checkout_cwd(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "generation" / "vibecrafted_core"
    deck = package_root / "deck" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_text(
        (REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    deck.chmod(0o755)
    (package_root / "VERSION").write_text("3.7.0+ginstalled\n", encoding="utf-8")
    (package_root / "runtime").mkdir()
    (package_root / "skills").mkdir()
    checkout = tmp_path / "checkout"
    (checkout / "scripts").mkdir(parents=True)
    (checkout / "scripts/vibecrafted").write_text("fixture\n", encoding="utf-8")
    (checkout / "skills").mkdir()
    (checkout / "runtime").mkdir()
    (checkout / "VERSION").write_text("3.7.0\n", encoding="utf-8")
    public_bin = tmp_path / "bin"
    public_bin.mkdir()
    public_launcher = public_bin / "vibecrafted"
    public_launcher.symlink_to(deck)

    result = subprocess.run(
        [str(public_launcher), "--version"],
        check=True,
        cwd=checkout,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "vibecrafted 3.7.0+ginstalled"


def test_update_web_fallback_verifies_install_sh_against_sha256sums(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    wrapper = tmp_path / "vibecrafted"
    install_capture = tmp_path / "install-args.txt"
    curl_capture = tmp_path / "curl-urls.txt"

    home.mkdir()
    fake_bin.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_fake_curl(fake_bin)

    install_body = (
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$@" > "$INSTALL_CAPTURE"',
            ]
        )
        + "\n"
    )
    install_sha = hashlib.sha256(install_body.encode("utf-8")).hexdigest()
    routes = {
        "https://vibecrafted.io/channel/main.json": json.dumps(
            {
                "version": "9.9.9",
                "archive_url": "https://downloads.example/vibecrafted-9.9.9.tar.gz",
            }
        ),
        "https://downloads.example/install.sh": install_body,
        "https://downloads.example/SHA256SUMS": (
            f"{install_sha}  install.sh\ndeadbeef  vibecrafted-9.9.9.tar.gz\n"
        ),
    }

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["INSTALL_CAPTURE"] = str(install_capture)
    env["CURL_CAPTURE_FILE"] = str(curl_capture)
    env["FAKE_CURL_ROUTES"] = json.dumps(routes)

    result = subprocess.run(
        ["bash", str(wrapper), "update", "--ref", "main"],
        check=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert install_capture.read_text(encoding="utf-8").splitlines() == ["--ref", "main"]
    assert curl_capture.read_text(encoding="utf-8").splitlines() == [
        "https://vibecrafted.io/channel/main.json",
        "https://downloads.example/install.sh",
        "https://downloads.example/SHA256SUMS",
    ]
    assert "Verifying install.sh via SHA256SUMS" in (result.stdout + result.stderr)
    assert "SHA256" in (result.stdout + result.stderr)


def test_update_web_fallback_aborts_on_install_sh_sha256_mismatch(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    wrapper = tmp_path / "vibecrafted"
    install_capture = tmp_path / "install-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_fake_curl(fake_bin)

    install_body = (
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'printf "%s\\n" "$@" > "$INSTALL_CAPTURE"',
            ]
        )
        + "\n"
    )
    routes = {
        "https://vibecrafted.io/channel/main.json": json.dumps(
            {
                "version": "9.9.9",
                "archive_url": "https://downloads.example/vibecrafted-9.9.9.tar.gz",
            }
        ),
        "https://downloads.example/install.sh": install_body,
        "https://downloads.example/SHA256SUMS": (
            "0000000000000000000000000000000000000000000000000000000000000000  install.sh\n"
        ),
    }

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"
    env["INSTALL_CAPTURE"] = str(install_capture)
    env["FAKE_CURL_ROUTES"] = json.dumps(routes)

    result = subprocess.run(
        ["bash", str(wrapper), "update", "--ref", "main"],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not install_capture.exists()
    assert "SHA256 mismatch for install.sh" in (result.stdout + result.stderr)


def test_installed_launcher_gui_uses_python_control_plane_surface(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "python3-calls.txt"

    home.mkdir(parents=True)
    fake_bin.mkdir()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (current_root / "scripts").mkdir(parents=True, exist_ok=True)
    (current_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    (current_root / "scripts" / "installer_gui.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    _write_fake_core_package(current_root)
    _write_fake_python3(fake_bin, capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    # Isolate PATH: a python-shebang `vibecrafted` console script in a venv would
    # otherwise own the -m call and the fake python3 would never see the sync.
    env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    env["CAPTURE_FILE"] = str(capture_file)

    result = subprocess.run(
        ["bash", str(launcher), "gui", "--no-open", "--port", "4173"],
        check=True,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = capture_file.read_text(encoding="utf-8")
    assert "-m vibecrafted_core.control_plane sync" in payload
    assert (
        f"{current_root / 'scripts' / 'installer_gui.py'} --source {current_root} --no-open --port 4173"
        in payload
    )
    assert "Listening URL: http://127.0.0.1:4173/" in result.stdout
    assert "Press Ctrl-C to stop." in result.stdout


def test_installed_launcher_doctor_forwards_fix_flags(tmp_path: Path) -> None:
    home = tmp_path / "home"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "python3-calls.txt"

    home.mkdir(parents=True)
    fake_bin.mkdir()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (current_root / "scripts").mkdir(parents=True, exist_ok=True)
    (current_root / "vibecrafted-core").mkdir(parents=True, exist_ok=True)
    (current_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    (current_root / "scripts" / "vetcoders_install.py").write_text(
        "#!/usr/bin/env python3\n", encoding="utf-8"
    )
    _write_fake_python3(fake_bin, capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/bin:/usr/bin"
    env["CAPTURE_FILE"] = str(capture_file)

    result = subprocess.run(
        [
            "bash",
            str(launcher),
            "doctor",
            "--fix-rc",
            "--fix-launchers",
        ],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    calls = capture_file.read_text(encoding="utf-8").splitlines()
    assert (
        f"{current_root / 'scripts' / 'vetcoders_install.py'} "
        "doctor --fix-rc --fix-launchers"
    ) in calls


def test_installed_launcher_doctor_reconciles_server_service_then_rechecks(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    fake_bin = tmp_path / "bin"
    python_capture = tmp_path / "python3-calls.txt"
    service_capture = tmp_path / "service-calls.txt"

    home.mkdir(parents=True)
    fake_bin.mkdir()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (current_root / "scripts").mkdir(parents=True, exist_ok=True)
    core_package = current_root / "vibecrafted-core" / "vibecrafted_core"
    core_package.mkdir(parents=True, exist_ok=True)
    # _dispatcher_core_dir deliberately requires the dispatcher entrypoint:
    # make this an installed-runtime fixture, not a loose directory look-up.
    (core_package / "dispatcher.py").write_text("\n", encoding="utf-8")
    (current_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "-c" || "${1:-}" == "-" ]]; then exec /usr/bin/python3 "$@"; fi\n'
        'printf "%s\\n" "$*" >> "$CAPTURE_FILE"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_supervisor = fake_bin / "vc-server-supervisor"
    fake_supervisor.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$*" >> "$SERVICE_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_supervisor.chmod(0o755)
    fake_service_launcher = fake_bin / "vibecrafted"
    fake_service_launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_service_launcher.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:/bin:/usr/bin"
    env["CAPTURE_FILE"] = str(python_capture)
    env["SERVICE_CAPTURE"] = str(service_capture)

    result = subprocess.run(
        ["bash", str(launcher), "doctor", "--fix-server-service"],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    service_args = service_capture.read_text(encoding="utf-8")
    assert service_args.startswith("service install ")
    assert f"--launcher {fake_bin / 'vibecrafted'}" in service_args
    assert f"--supervisor-bin {fake_bin / 'vc-server-supervisor'}" in service_args
    assert "-m vibecrafted_core.cli doctor" in python_capture.read_text(
        encoding="utf-8"
    )


def test_doctor_help_documents_server_service_repair() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "doctor", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "vibecrafted doctor --fix-server-service" in result.stdout


def test_installed_launcher_tui_uses_shared_state_and_voc_binary(
    tmp_path: Path,
) -> None:
    """Product contract: `vibecrafted tui` launches `voc` (install-app-binaries)."""
    home = tmp_path / "home"
    installed_root = home / ".vibecrafted"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    fake_bin = tmp_path / "bin"
    python_capture = tmp_path / "python3-calls.txt"
    tui_capture = tmp_path / "tui-calls.txt"

    home.mkdir(parents=True)
    fake_bin.mkdir()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (current_root / "scripts").mkdir(parents=True, exist_ok=True)
    app_root = current_root / "vibecrafted-app"
    (app_root / "tui-agent").mkdir(parents=True, exist_ok=True)
    (app_root / "target" / "debug").mkdir(parents=True, exist_ok=True)
    (app_root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["tui-agent"]\n',
        encoding="utf-8",
    )
    (app_root / "tui-agent" / "Cargo.toml").write_text(
        '[package]\nname = "voc"\nversion = "0.0.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (current_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    _write_fake_core_package(current_root)
    (current_root / "scripts" / "vibecrafted").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    _write_fake_python3(fake_bin, python_capture)
    _write_capture_script(app_root / "target" / "debug" / "voc", tui_capture)

    env = os.environ.copy()
    env["HOME"] = str(home)
    # Isolate PATH so a host ~/.local/bin/voc cannot steal resolution from the
    # local vibecrafted-app debug fixture. Keep /bin for bash/coreutils.
    env["PATH"] = f"{fake_bin}:/bin:/usr/bin"
    env["CAPTURE_FILE"] = str(python_capture)

    subprocess.run(
        ["bash", str(launcher), "tui", "--tick-ms", "500"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    assert "-m vibecrafted_core.control_plane sync" in python_capture.read_text(
        encoding="utf-8"
    )
    tui_args = tui_capture.read_text(encoding="utf-8")
    assert f"--state-root {installed_root / 'control_plane'}" in tui_args
    assert f"--deck {current_root / 'scripts' / 'vibecrafted'}" in tui_args
    assert "--tick-ms 500" in tui_args


def test_tui_uses_voc_from_path_when_local_build_missing(
    tmp_path: Path,
) -> None:
    """When no local vibecrafted-app build exists, PATH `voc` is the product bin."""
    home = tmp_path / "home"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current_root = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    fake_bin = tmp_path / "bin"
    python_capture = tmp_path / "python3-calls.txt"
    tui_capture = tmp_path / "tui-calls.txt"

    home.mkdir(parents=True)
    fake_bin.mkdir()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    launcher.chmod(0o755)
    (current_root / "scripts").mkdir(parents=True, exist_ok=True)
    (current_root / "VERSION").write_text("0.0.0-test\n", encoding="utf-8")
    _write_fake_core_package(current_root)
    (current_root / "scripts" / "vibecrafted").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    _write_fake_python3(fake_bin, python_capture)
    env = os.environ.copy()
    env["HOME"] = str(home)
    # Product install path: only PATH-owned voc (no local app tree build).
    env["PATH"] = f"{fake_bin}:/bin:/usr/bin"
    env["CAPTURE_FILE"] = str(python_capture)
    _write_capture_script(fake_bin / "voc", tui_capture)

    subprocess.run(
        ["bash", str(launcher), "tui", "--runtime", "headless"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    assert "-m vibecrafted_core.control_plane sync" in python_capture.read_text(
        encoding="utf-8"
    )
    tui_args = tui_capture.read_text(encoding="utf-8")
    assert "--runtime headless" in tui_args
    assert f"--deck {current_root / 'scripts' / 'vibecrafted'}" in tui_args


def test_gui_help_exposes_local_server_flags() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "gui", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "--host <host>" in result.stdout
    assert "--port <port>" in result.stdout
    assert "--no-open" in result.stdout
    assert "--bundle-dir <path>" in result.stdout


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("init", "vc-init [claude|codex|agy|junie|grok]"),
        ("vc-init", "vc-init [claude|codex|agy|junie|grok]"),
        ("vc-review", 'vibecrafted review codex --prompt "Review PR #14"'),
        ("status", "vibecrafted stats"),
    ],
)
def test_help_topics_route_to_specific_command_or_skill_help(
    topic: str, expected: str
) -> None:
    result = subprocess.run(
        [str(LAUNCHER), "help", topic],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert expected in result.stdout
    assert "Ship cycle:" not in result.stdout


def test_status_empty_state_is_explicit_when_artifact_dirs_exist(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home_artifacts = home / ".vibecrafted" / "artifacts"
    local_reports = repo / ".vibecrafted" / "reports"

    home_artifacts.mkdir(parents=True)
    local_reports.mkdir(parents=True)

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "status"],
        check=True,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )

    # Empty board: the stranger is told the one command that creates a run,
    # and the one that waits for it — not sent to a cockpit-only init.
    assert "No runs yet." in result.stdout
    assert "vibecrafted implement claude --prompt" in result.stdout
    assert "vibecrafted await claude --last" in result.stdout


def test_stats_skills_reports_context_inventory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    skill_dir = home / ".codex" / "skills" / "vc-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nname: vc-demo\ndescription: "Short demo skill."\n---\n',
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "stats", "skills"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Vibecrafted Skill Context Stats" in result.stdout
    assert "Codex announced skills metadata budget: 2%" in result.stdout
    assert "skill files:        1" in result.stdout
    assert "unique names:       1" in result.stdout
    assert "duplicate groups:   0" in result.stdout
    assert "vc-demo" in result.stdout


def test_implement_help_is_the_canonical_autonomous_delivery_surface() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "implement", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "implement" in result.stdout
    assert "VC-ship WRITE stage: structured end-to-end implementation" in result.stdout
    assert (
        "vibecrafted implement <claude|codex|agy|junie|grok> [flags]" in result.stdout
    )
    assert "vc-implement <claude|codex|agy|junie|grok> [flags]" in result.stdout
    assert "Not the same skill as justdo." in result.stdout


def test_justdo_help_is_a_distinct_standalone_posture() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "justdo", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "justdo" in result.stdout
    assert "Standalone Just Do posture" in result.stdout
    assert "vibecrafted justdo <claude|codex|agy|junie|grok> [flags]" in result.stdout
    assert "vc-justdo <claude|codex|agy|junie|grok> [flags]" in result.stdout
    assert "Not implement." in result.stdout


@pytest.mark.parametrize("skill", ["implement", "justdo"])
def test_autonomous_delivery_skills_route_to_core_async_launcher(
    tmp_path: Path, skill: str
) -> None:
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "python-args.txt"
    fake_bin.mkdir()
    _write_fake_python(fake_bin, capture_file)

    env = os.environ.copy()
    env["CAPTURE_FILE"] = str(capture_file)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PYTHON"] = str(fake_bin / "python3")

    subprocess.run(
        ["bash", str(LAUNCHER), skill, "codex", "--prompt", "Ship the cut"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    args = capture_file.read_text(encoding="utf-8").splitlines()
    assert args[:4] == ["-m", "vibecrafted_core.cli", "implement", "codex"]
    assert "--source-dir" in args
    assert str(REPO_ROOT) in args
    assert "--prompt" in args
    assert "Ship the cut" in args


@pytest.mark.parametrize(
    ("research_args", "expected_prefix"),
    [
        (
            ["--prompt", "Check Codescribe"],
            ["research", "--prompt", "Check Codescribe"],
        ),
        (
            ["codex", "--prompt", "Check Codescribe"],
            ["research", "codex", "--prompt", "Check Codescribe"],
        ),
        (
            ["codex", "agy", "--prompt", "Check Codescribe"],
            ["research", "codex", "agy", "--prompt", "Check Codescribe"],
        ),
        (
            ["trio", "claude", "codex", "agy", "--prompt", "Check Codescribe"],
            [
                "research",
                "trio",
                "claude",
                "codex",
                "agy",
                "--prompt",
                "Check Codescribe",
            ],
        ),
    ],
)
def test_research_preserves_optional_variadic_agents_for_core_parser(
    tmp_path: Path,
    research_args: list[str],
    expected_prefix: list[str],
) -> None:
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "python-args.txt"
    fake_bin.mkdir()
    _write_fake_python(fake_bin, capture_file)

    env = os.environ.copy()
    env["CAPTURE_FILE"] = str(capture_file)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PYTHON"] = str(fake_bin / "python3")

    result = subprocess.run(
        ["bash", str(LAUNCHER), "research", *research_args],
        check=False,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Unknown agent" not in result.stderr
    args = capture_file.read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["-m", "vibecrafted_core.cli"]
    assert args[2 : 2 + len(expected_prefix)] == expected_prefix
    assert args[-2:] == ["--source-dir", str(REPO_ROOT)]


def test_compact_help_teaches_implement_before_alias() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert 'vibecrafted implement codex -p "Ship dark mode"' in result.stdout
    assert "justdo" not in result.stdout
    assert "leg" + "acy alias" not in result.stdout


def test_review_and_followup_help_separate_bounded_review_from_direction_audit() -> (
    None
):
    review = subprocess.run(
        [str(LAUNCHER), "review", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    followup = subprocess.run(
        [str(LAUNCHER), "followup", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    audit = subprocess.run(
        [str(LAUNCHER), "audit", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "version 1.0.0" in audit.stdout
    assert "READ-ONLY falsification of a completed plan" in audit.stdout
    assert "Bounded PR, branch, commit-range, or artifact-pack review" in review.stdout
    assert "version 2.0.0" in review.stdout
    assert 'vibecrafted review codex --prompt "Review PR #14"' in review.stdout
    assert "Post-implementation direction audit" in followup.stdout
    assert "version 2.2.0" in followup.stdout
    assert (
        'vibecrafted followup codex --prompt "Audit post-implementation direction"'
        in followup.stdout
    )


@pytest.mark.parametrize(
    ("wrapper_name", "skill", "description"),
    [
        ("vc-followup", "followup", "Post-implementation direction audit"),
        ("vc-audit", "audit", "READ-ONLY falsification of a completed plan"),
        ("vc-intents", "intents", "Plan-to-runtime truth audit"),
        (
            "vc-ownership",
            "ownership",
            "Full-spectrum operational ownership",
        ),
    ],
)
def test_skill_wrapper_help_is_human_readable_without_agent(
    tmp_path: Path, wrapper_name: str, skill: str, description: str
) -> None:
    wrapper = tmp_path / wrapper_name
    wrapper.symlink_to(LAUNCHER)

    result = subprocess.run(
        [str(wrapper), "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert skill in result.stdout
    assert description in result.stdout
    assert f"{wrapper_name} <claude|codex|agy|junie|grok> [flags]" in result.stdout


@pytest.mark.parametrize(
    ("skill", "prompt"),
    [
        ("intents", "Audit what from the plan really landed"),
        ("ownership", "Take the repo from diagnosis to finished surface"),
    ],
)
def test_generic_skill_fallback_routes_unwrapped_skills(
    tmp_path: Path, skill: str, prompt: str
) -> None:
    home = tmp_path / "home"
    wrapper = tmp_path / "vibecrafted"
    capture_file = tmp_path / "generic-skill-args.txt"
    helper = (
        home
        / ".local"
        / "share"
        / "vibecrafted"
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    )

    home.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_generic_skill_helper(helper)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CAPTURE_FILE"] = str(capture_file)

    subprocess.run(
        ["bash", str(wrapper), skill, "codex", "--prompt", prompt],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert payload == ["codex", skill, "--prompt", prompt]


@pytest.mark.parametrize(
    ("wrapper_name", "skill", "prompt"),
    [
        ("vc-intents", "intents", "Audit what from the plan really landed"),
        (
            "vc-ownership",
            "ownership",
            "Take the repo from diagnosis to finished surface",
        ),
    ],
)
def test_generic_skill_fallback_routes_skill_wrappers(
    tmp_path: Path, wrapper_name: str, skill: str, prompt: str
) -> None:
    home = tmp_path / "home"
    wrapper = tmp_path / wrapper_name
    capture_file = tmp_path / "generic-wrapper-args.txt"
    helper = (
        home
        / ".local"
        / "share"
        / "vibecrafted"
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    )

    home.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_generic_skill_helper(helper)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CAPTURE_FILE"] = str(capture_file)

    subprocess.run(
        ["bash", str(wrapper), "codex", "--prompt", prompt],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert payload == ["codex", skill, "--prompt", prompt]


def test_marbles_help_lists_delete_control_subcommand() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "marbles", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert (
        "vibecrafted marbles <pause|stop|resume|session|inspect|delete> [args]"
        in result.stdout
    )
    assert (
        "vc-marbles <pause|stop|resume|session|inspect|delete> [args]" in result.stdout
    )


def test_marbles_flags_without_agent_get_actionable_error() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "marbles", "--count", "8", "--depth", "10"],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Missing marbles agent before flags." in result.stderr
    assert "Try: vibecrafted marbles codex --count 8 --depth 10" in result.stderr
    assert "Unknown agent: --count" not in result.stderr


def test_generic_skill_entry_preserves_marbles_options_for_junie(
    tmp_path: Path,
) -> None:
    capture_file = tmp_path / "marbles-entry-args.txt"
    script = "\n".join(
        [
            "set -euo pipefail",
            f"source {REPO_ROOT / 'vibecrafted-core' / 'vibecrafted_core' / 'runtime' / 'shell' / 'vetcoders.sh'}",
            "_vetcoders_marbles() {",
            '  printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            "}",
            "_vetcoders_skill_entry junie marbles --count 3 --file /tmp/plan.md",
        ]
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["CAPTURE_FILE"] = str(capture_file)

    subprocess.run(
        ["bash", "-lc", script],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    assert capture_file.read_text(encoding="utf-8").splitlines() == [
        "junie",
        "--count",
        "3",
        "--file",
        "/tmp/plan.md",
    ]


def test_marbles_delete_control_subcommand_routes_to_helper(tmp_path: Path) -> None:
    home = tmp_path / "home"
    wrapper = tmp_path / "vibecrafted"
    capture_file = tmp_path / "marbles-delete-args.txt"
    helper = (
        home
        / ".local"
        / "share"
        / "vibecrafted"
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    )

    home.mkdir()
    wrapper.symlink_to(LAUNCHER)
    helper.parent.mkdir(parents=True, exist_ok=True)
    helper.write_text(
        "\n".join(
            [
                "marbles-delete() {",
                '  printf "%s\\n" "$@" > "$CAPTURE_FILE"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CAPTURE_FILE"] = str(capture_file)

    subprocess.run(
        ["bash", str(wrapper), "marbles", "delete", "marb-424242"],
        check=True,
        cwd=tmp_path,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert payload == ["marb-424242"]


def test_loop_help_exposes_interactive_operator_runtime() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "loop", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Interactive Agent-Operator continuation" in result.stdout
    assert "vibecrafted loop await-run --run-id" in result.stdout
    assert "vc-loop status" in result.stdout
    assert "<repo-root>/.vibecrafted/operator-loop.local.md" in result.stdout
    assert "Operator-approved argv command" in result.stdout


def test_loop_start_next_and_max_iteration_stop(tmp_path: Path) -> None:
    subprocess.run(
        [
            str(LAUNCHER),
            "loop",
            "start",
            "--prompt",
            "Keep conducting the dispatch",
            "--max-iterations",
            "2",
            "--completion-promise",
            "READY",
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    first_next = subprocess.run(
        [str(LAUNCHER), "loop", "next"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert "CONTINUE: operator loop iteration 2" in first_next.stdout
    assert "Keep conducting the dispatch" in first_next.stdout
    assert "<promise>READY</promise>" in first_next.stdout

    second_next = subprocess.run(
        [str(LAUNCHER), "loop", "next"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert "STOP: max iterations reached (2)." in second_next.stdout


def test_loop_state_defaults_to_git_root_from_subdirectory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    nested = repo / "nested" / "dir"
    nested.mkdir(parents=True)
    subprocess.run(
        ["git", "init"], cwd=repo, check=True, capture_output=True, text=True
    )

    subprocess.run(
        [
            str(LAUNCHER),
            "loop",
            "start",
            "--prompt",
            "Keep conducting from a subdirectory",
            "--max-iterations",
            "2",
        ],
        check=True,
        cwd=nested,
        capture_output=True,
        text=True,
    )

    root_state = repo / ".vibecrafted" / "operator-loop.local.md"
    nested_state = nested / ".vibecrafted" / "operator-loop.local.md"
    assert root_state.exists()
    assert not nested_state.exists()

    result = subprocess.run(
        [str(LAUNCHER), "loop", "next"],
        check=True,
        cwd=nested,
        capture_output=True,
        text=True,
    )

    assert "CONTINUE: operator loop iteration 2" in result.stdout
    assert "Keep conducting from a subdirectory" in result.stdout


def test_loop_completion_promise_allows_colons(tmp_path: Path) -> None:
    subprocess.run(
        [
            str(LAUNCHER),
            "loop",
            "start",
            "--prompt",
            "Finish the cut",
            "--completion-promise",
            "READY: audited",
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    result = subprocess.run(
        [
            str(LAUNCHER),
            "loop",
            "complete",
            "--promise",
            "READY: audited",
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert (
        "Completed operator loop with <promise>READY: audited</promise>."
        in result.stdout
    )


def test_vc_loop_wrapper_routes_to_loop_command(tmp_path: Path) -> None:
    wrapper = tmp_path / "vc-loop"
    wrapper.symlink_to(LAUNCHER)

    result = subprocess.run(
        ["bash", str(wrapper), "--help"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert "Interactive Agent-Operator continuation" in result.stdout


def test_agent_help_topic_lists_canonical_action_first_commands() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "help", "codex"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Canonical commands for codex. Actions come first" in result.stdout
    assert "vibecrafted init codex" in result.stdout
    assert "implement codex <plan.md>" in result.stdout
    assert "observe   codex --last" in result.stdout
    assert "await     codex --last" in result.stdout
    assert "stop      codex --run-id|--last" in result.stdout
    assert "agent-first" not in result.stdout.lower()


def test_agent_first_mode_is_rejected_with_action_first_migration() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "codex", "stop", "--help"],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "Agent-first grammar was removed" in result.stderr
    assert "Use: vibecrafted stop codex --help" in result.stderr


@pytest.mark.parametrize("verb", ["observe", "await", "stop"])
def test_action_first_lifecycle_help_is_canonical(verb: str) -> None:
    result = subprocess.run(
        [str(LAUNCHER), verb, "codex", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert f"vibecrafted {verb} codex --last|--run-id <id>" in result.stdout
    assert "agent-first" not in result.stdout.lower()
    assert "Unknown" not in result.stderr


def test_swarm_alias_routes_to_research_help() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "swarm", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Multi-agent research pass" in result.stdout
    assert "vibecrafted swarm [agents...] [flags]" in result.stdout
    assert "not in the command deck" not in result.stdout


def test_swarm_lifecycle_help_uses_existing_core_route() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "swarm", "observe", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Check an agent report or transcript." in result.stdout
    assert "vibecrafted swarm observe --last" in result.stdout


def test_canary_launcher_has_canonical_help() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "canary", "--help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert "Ownership catalog" in result.stdout
    assert "vibecrafted canary" in result.stdout
    assert "not in the command deck" not in result.stdout


def test_dashboard_subcommand_launches_repo_owned_vc_frame_layout(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    # Redirecting HOME is not enough. The launcher resolves the frontier config
    # under $XDG_CONFIG_HOME, which the operator's shell sets independently of
    # HOME, so os.environ.copy() carries the real one straight into the test.
    # MEASURED 2026-08-18: this case was red on the release machine and green
    # everywhere else, because ~/.config/vetcoders/frontier/vc-frame EXISTS
    # there and the launcher preferred it over the repo-owned layout that is
    # the whole subject of the assertion below. A suite that cannot be trusted
    # on the host which builds the release is not a release gate.
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_WORKSPACE_ID", None)
    env.pop("VIBECRAFTED_SESSION_ID", None)
    env.pop("VIBECRAFTED_WORKSPACE_INSTANCE_ID", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        ["bash", str(LAUNCHER), "dashboard"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--session" in payload
    # dashboard (default layout) uses the canonical operator session, no suffix.
    assert _expected_operator_session() in payload
    assert "--new-session-with-layout" in payload
    assert (
        str(
            REPO_ROOT
            / "vibecrafted-core"
            / "vibecrafted_core"
            / "config"
            / "vc-frame"
            / "layouts"
            / "dashboard.kdl"
        )
        in payload
    )
    assert (
        f"VC_FRAME_CONFIG_DIR={REPO_ROOT / 'vibecrafted-core' / 'vibecrafted_core' / 'config' / 'vc-frame'}"
        in payload
    )


def test_start_subcommand_launches_operator_entrypoint_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    # Same host-config leak as the dashboard case above; this assertion also
    # names a repo-owned layout, so it must not be able to see the operator's.
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_WORKSPACE_ID", None)
    env.pop("VIBECRAFTED_SESSION_ID", None)
    env.pop("VIBECRAFTED_WORKSPACE_INSTANCE_ID", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    expected_session = _resolved_workspace_session(env)

    subprocess.run(
        ["bash", str(LAUNCHER), "start"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--session" in payload
    assert expected_session in payload
    assert "--new-session-with-layout" in payload
    assert (
        str(
            REPO_ROOT
            / "vibecrafted-core"
            / "vibecrafted_core"
            / "config"
            / "vc-frame"
            / "layouts"
            / "operator.kdl"
        )
        in payload
    )
    assert (
        f"VC_FRAME_CONFIG_DIR={REPO_ROOT / 'vibecrafted-core' / 'vibecrafted_core' / 'config' / 'vc-frame'}"
        in payload
    )


def test_resume_subcommand_forwards_session_and_prompt_to_agent(
    tmp_path: Path,
) -> None:
    session_id = "resume-session-123"
    prompt = "Continue the fix"
    env, provider_called, core_argv, core_prompt = _tracked_resume_fixture(
        tmp_path,
        session_id=session_id,
    )

    result = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "resume",
            "codex",
            "--session",
            session_id,
            "--prompt",
            prompt,
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    _assert_tracked_resume(
        result,
        provider_called=provider_called,
        core_argv=core_argv,
        core_prompt=core_prompt,
        session_id=session_id,
        prompt=prompt,
    )


def test_resume_subcommand_wraps_headless_codex_in_vc_frame_worker_session(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_vc_frame_with_live_session(fake_bin, capture_file, "operator-test")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    env["VIBECRAFTED_OPERATOR_SESSION"] = "operator-test"
    env["VIBECRAFTED_WORKER_SESSION"] = "worker-test"
    env["CAPTURE_FILE"] = str(capture_file)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_RUN_ID", None)
    env.pop("VIBECRAFTED_RUN_LOCK", None)
    env.pop("VIBECRAFTED_SKILL_CODE", None)
    env.pop("VIBECRAFTED_SKILL_NAME", None)

    subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "resume",
            "codex",
            "--session",
            "resume-session-789",
            "--prompt",
            "Continue inside vc_frame",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--session" in payload
    assert "worker-test" in payload
    assert "operator-test" not in payload
    assert "action" in payload
    assert "new-tab" in payload
    assert "--name" in payload
    assert "codex" in payload
    assert "resume-codex" not in payload
    assert "--cwd" in payload
    assert str(REPO_ROOT) in payload


def test_resume_wrapper_symlink_forwards_session_and_prompt_to_agent(
    tmp_path: Path,
) -> None:
    session_id = "resume-session-456"
    prompt = "Continue from wrapper"
    wrapper = tmp_path / "vc-resume"
    wrapper.symlink_to(LAUNCHER)
    env, provider_called, core_argv, core_prompt = _tracked_resume_fixture(
        tmp_path,
        session_id=session_id,
    )

    result = subprocess.run(
        [
            "bash",
            str(wrapper),
            "codex",
            "--session",
            session_id,
            "--prompt",
            prompt,
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    _assert_tracked_resume(
        result,
        provider_called=provider_called,
        core_argv=core_argv,
        core_prompt=core_prompt,
        session_id=session_id,
        prompt=prompt,
    )


def test_resume_wrapper_accepts_positional_session_id(tmp_path: Path) -> None:
    """`vc-resume <agent> <session_id> [prompt...]` works without --session."""
    session_id = "resume-session-456"
    prompt = "Continue from wrapper"
    wrapper = tmp_path / "vc-resume"
    wrapper.symlink_to(LAUNCHER)
    env, provider_called, core_argv, core_prompt = _tracked_resume_fixture(
        tmp_path,
        session_id=session_id,
    )

    result = subprocess.run(
        [
            "bash",
            str(wrapper),
            "codex",
            session_id,
            prompt,
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    _assert_tracked_resume(
        result,
        provider_called=provider_called,
        core_argv=core_argv,
        core_prompt=core_prompt,
        session_id=session_id,
        prompt=prompt,
    )


def test_resume_wrapper_accepts_bare_positional_session_id(tmp_path: Path) -> None:
    """A positional Codex session id is the interactive `--session` alias."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc-frame-args.txt"
    wrapper = tmp_path / "vc-resume"

    home.mkdir()
    fake_bin.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_fake_vc_frame_with_live_session(fake_bin, capture_file, "operator-test")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    env["VIBECRAFTED_OPERATOR_SESSION"] = "operator-test"
    env["CAPTURE_FILE"] = str(capture_file)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    subprocess.run(
        ["bash", str(wrapper), "codex", "resume-session-789"],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert payload[:4] == ["--session", "operator-test", "action", "new-tab"]
    separator = payload.index("--")
    command_script = Path(payload[separator + 1])
    command_body = command_script.read_text(encoding="utf-8")
    assert "codex resume resume-session-789" in command_body
    assert "codex exec" not in command_body


def test_vc_dashboard_wrapper_dispatches_to_dashboard(tmp_path: Path) -> None:
    """vc-dashboard wrapper (symlink) reaches cmd_dashboard, not run_skill."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    wrapper = tmp_path / "vc-dashboard"

    home.mkdir()
    fake_bin.mkdir()
    wrapper.symlink_to(LAUNCHER)
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(wrapper)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--session" in payload
    assert "--new-session-with-layout" in payload


def test_dashboard_ls_delegates_to_vc_frame_list_sessions(tmp_path: Path) -> None:
    """vibecrafted dashboard ls calls vc-frame list-sessions, not layout load."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "ls"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "list-sessions" in payload


def test_dashboard_switch_inside_vc_frame_uses_switch_session(tmp_path: Path) -> None:
    """dashboard switch from inside vc-frame uses 'action switch-session', not attach."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    # Simulate being inside vc-frame
    env["VC_FRAME"] = "0"
    env["VC_FRAME_PANE_ID"] = "1"
    env["VC_FRAME_SESSION_NAME"] = "existing-session"

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "switch", "target-session"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "action" in payload
    assert "switch-session" in payload
    assert "target-session" in payload
    # Must NOT use 'attach' when inside vc-frame
    assert "attach" not in payload


def test_dashboard_attach_inside_vc_frame_uses_switch_session(tmp_path: Path) -> None:
    """dashboard attach from inside vc-frame falls through to switch-session."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME"] = "0"
    env["VC_FRAME_PANE_ID"] = "1"
    env["VC_FRAME_SESSION_NAME"] = "existing-session"

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "attach", "other-session"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "action" in payload
    assert "switch-session" in payload
    assert "other-session" in payload
    assert "attach" not in payload


def test_dashboard_switch_outside_vc_frame_uses_attach(tmp_path: Path) -> None:
    """dashboard switch from outside vc-frame falls through to attach."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "switch", "target-session"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "attach" in payload
    assert "target-session" in payload


def test_dashboard_switch_with_stale_frame_env_uses_attach(tmp_path: Path) -> None:
    """Stale VC_FRAME/session-name leaks (no pane id) must not fake 'inside'.

    A shell that once ran vc-start carries exported VC_FRAME_SESSION_NAME (and
    a leaked VC_FRAME/ZELLIJ) without any pane id. From such a shell 'switch'
    must attach — an 'action switch-session' has no live client to act on.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_fake_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME"] = "0"
    env["ZELLIJ"] = "0"
    env["VC_FRAME_SESSION_NAME"] = "stale-session"
    env["ZELLIJ_SESSION_NAME"] = "stale-session"
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("ZELLIJ_PANE_ID", None)

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "switch", "target-session"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "attach" in payload
    assert "target-session" in payload
    assert "switch-session" not in payload


def test_dashboard_gc_ignores_untyped_listing_in_dry_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    listing = "abandoned-evidence [Created 72h ago] (EXITED - attach to resurrect)\n"
    _write_gc_vc_frame(fake_bin, capture_file, listing)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["FAKE_VC_FRAME_LISTING"] = listing

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "gc"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8")
    assert "list-sessions" not in payload
    assert "kill-session" not in payload
    assert "vc_frame-tab-gc: dry-run; candidates=0 closed=0" in result.stdout


def test_dashboard_gc_apply_without_proof_does_not_kill_sessions(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_gc_vc_frame(
        fake_bin,
        capture_file,
        "\n".join(
            [
                "vc-runtime [Created 144h ago]",
                "joyous-hill [Created 72h ago] (EXITED - attach to resurrect)",
                "didactic-cactus [Created 5h ago] (EXITED - attach to resurrect)",
                "",
            ]
        ),
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["FAKE_VC_FRAME_LISTING"] = "\n".join(
        [
            "vc-runtime [Created 144h ago]",
            "joyous-hill [Created 72h ago] (EXITED - attach to resurrect)",
            "didactic-cactus [Created 5h ago] (EXITED - attach to resurrect)",
            "",
        ]
    )

    result = subprocess.run(
        ["bash", str(LAUNCHER), "dashboard", "gc", "--apply"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8")
    assert "list-sessions" not in payload
    assert "kill-session" not in payload
    assert "vc_frame-tab-gc: applied; candidates=0 closed=0" in result.stdout


def test_dashboard_gc_include_live_fails_closed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_gc_vc_frame(
        fake_bin,
        capture_file,
        "\n".join(
            [
                "active-one [Created 72h ago] (current)",
                "stale-live [Created 72h ago]",
                "fresh-live [Created 2h ago]",
                "",
            ]
        ),
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["CAPTURE_FILE"] = str(capture_file)
    env["FAKE_VC_FRAME_LISTING"] = "\n".join(
        [
            "active-one [Created 72h ago] (current)",
            "stale-live [Created 72h ago]",
            "fresh-live [Created 2h ago]",
            "",
        ]
    )

    result = subprocess.run(
        [
            "bash",
            str(LAUNCHER),
            "dashboard",
            "gc",
            "--apply",
            "--include-live",
            "--max-age-hours",
            "24",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = capture_file.read_text(encoding="utf-8")
    assert payload == ""
    assert result.stdout == ""
    assert (
        "--include-live is unsafe: vc-frame kill-session has no typed "
        "incarnation selector"
    ) in result.stderr


def test_run_helper_blocks_self_looping_path_resolution(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    launcher_copy = tmp_path / "vibecrafted"

    home.mkdir()
    fake_bin.mkdir()
    _write_trimmed_launcher(launcher_copy)
    (fake_bin / "vc-loop").symlink_to(launcher_copy)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{launcher_copy}"; _run_helper vc-loop --file /tmp/demo.md',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "resolved back to vibecrafted itself" in result.stderr
    assert "missing function definition to vetcoders.sh" in result.stderr


def test_server_service_uses_one_installed_generation(tmp_path: Path) -> None:
    home = tmp_path / "home"
    stale_bin = tmp_path / "stale-bin"
    current_bin = tmp_path / "current-bin"
    launcher_copy = tmp_path / "vibecrafted-deck"
    capture_file = tmp_path / "supervisor-args.txt"

    home.mkdir()
    stale_bin.mkdir()
    current_bin.mkdir()
    _write_trimmed_launcher(launcher_copy)

    stale_launcher = stale_bin / "vibecrafted"
    stale_launcher.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
    stale_launcher.chmod(0o755)

    current_launcher = current_bin / "vibecrafted"
    current_launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    current_launcher.chmod(0o755)

    current_supervisor = current_bin / "vc-server-supervisor"
    current_supervisor.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "{",
                '  printf "%s\\n" "$0"',
                '  printf "%s\\n" "$@"',
                '  printf "PYTHONPATH=%s\\n" "${PYTHONPATH:-}"',
                '  printf "PYTHONHOME=%s\\n" "${PYTHONHOME:-}"',
                '} > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    current_supervisor.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stale_bin}:{current_bin}:/usr/bin:/bin"
    env["CAPTURE_FILE"] = str(capture_file)
    env["PYTHONPATH"] = "inherited-sentinel"
    env["PYTHONHOME"] = "inherited-home-sentinel"

    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{launcher_copy}"; _server_supervisor_cli service status --json',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert Path(payload[0]).resolve() == current_supervisor.resolve()
    assert payload[1:4] == ["service", "status", "--json"]
    assert payload[payload.index("--launcher") + 1] == str(current_launcher.resolve())
    assert payload[payload.index("--supervisor-bin") + 1] == str(
        current_supervisor.resolve()
    )
    assert str(stale_launcher) not in payload
    assert payload[-2:] == ["PYTHONPATH=", "PYTHONHOME="]

    current_launcher.unlink()
    capture_file.unlink()
    missing_sibling = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{launcher_copy}"; _server_supervisor_cli service status --json',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert missing_sibling.returncode == 78
    assert "Cannot resolve an absolute Vibecrafted launcher" in missing_sibling.stderr
    assert not capture_file.exists()


def test_server_service_prefers_declared_public_identity_over_generation_path(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    public_bin = home / ".local" / "bin"
    generation_bin = tmp_path / "generation" / "bin"
    launcher_copy = tmp_path / "vibecrafted-deck"
    capture_file = tmp_path / "supervisor-args.txt"

    public_bin.mkdir(parents=True)
    generation_bin.mkdir(parents=True)
    _write_trimmed_launcher(launcher_copy)

    for bin_dir, marker in ((public_bin, "public"), (generation_bin, "generation")):
        launcher = bin_dir / "vibecrafted"
        launcher.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        supervisor = bin_dir / "vc-server-supervisor"
        supervisor.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            f'printf "{marker}\\n" > "$CAPTURE_FILE"\n'
            'printf "%s\\n" "$@" >> "$CAPTURE_FILE"\n',
            encoding="utf-8",
        )
        supervisor.chmod(0o755)

    declared_launcher = public_bin / "vibecrafted"
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{generation_bin}:{public_bin}:/usr/bin:/bin",
        "CAPTURE_FILE": str(capture_file),
        "VIBECRAFTED_DECLARED_LAUNCHER": str(declared_launcher),
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{launcher_copy}"; _server_supervisor_cli service status --json',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert payload[0] == "public"
    assert payload[payload.index("--launcher") + 1] == str(declared_launcher.resolve())
    assert payload[payload.index("--supervisor-bin") + 1] == str(
        (public_bin / "vc-server-supervisor").resolve()
    )


def test_server_service_preserves_high_installer_lease_fd_through_launcher(
    tmp_path: Path,
) -> None:
    import fcntl

    home = tmp_path / "home"
    current_bin = tmp_path / "current-bin"
    current_launcher = current_bin / "vibecrafted"
    current_supervisor = current_bin / "vc-server-supervisor"
    capture_file = tmp_path / "lease-capture.txt"
    lease_file = tmp_path / "tools-install.lock"

    home.mkdir()
    current_bin.mkdir()
    current_launcher.write_text(LAUNCHER.read_text(encoding="utf-8"), encoding="utf-8")
    current_launcher.chmod(0o755)
    current_supervisor.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "import os",
                "from pathlib import Path",
                'descriptor = int(os.environ["VIBECRAFTED_INSTALL_LEASE_FD"])',
                "metadata = os.fstat(descriptor)",
                'Path(os.environ["CAPTURE_FILE"]).write_text(',
                '    f"fd={descriptor} size={metadata.st_size}\\n",',
                '    encoding="utf-8",',
                ")",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    current_supervisor.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{current_bin}:/usr/bin:/bin"
    env["CAPTURE_FILE"] = str(capture_file)
    descriptor = os.open(lease_file, os.O_RDWR | os.O_CREAT, 0o600)
    inherited_descriptor = fcntl.fcntl(descriptor, fcntl.F_DUPFD, 64)
    try:
        os.set_inheritable(inherited_descriptor, True)
        env["VIBECRAFTED_INSTALL_LEASE_FD"] = str(inherited_descriptor)
        result = subprocess.run(
            [str(current_launcher), "server", "service", "install"],
            cwd=REPO_ROOT,
            env=env,
            pass_fds=(inherited_descriptor,),
            capture_output=True,
            text=True,
        )
    finally:
        os.close(inherited_descriptor)
        os.close(descriptor)

    assert result.returncode == 0, result.stderr
    assert (
        capture_file.read_text(encoding="utf-8")
        == f"fd={inherited_descriptor} size=0\n"
    )


def _write_fake_claude_stream_agent(bin_dir: Path, final_message: str) -> None:
    script = bin_dir / "claude"
    stream = "\n".join(
        [
            "Claude CLI banner that should be ignored by the JSON filter",
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "claude-fixture-session",
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Intermediate assistant text is transcript-only.",
                            }
                        ]
                    },
                }
            ),
            json.dumps({"type": "result", "result": final_message}),
        ]
    )
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "cat <<'JSONL'",
                stream,
                "JSONL",
                'exit "${FAKE_CLAUDE_EXIT:-0}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_codex_last_message_agent(bin_dir: Path, final_message: str) -> None:
    script = bin_dir / "codex"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'last_message=""',
                "while [[ $# -gt 0 ]]; do",
                '  if [[ "$1" == "--output-last-message" ]]; then',
                "    shift",
                '    last_message="${1:-}"',
                "  fi",
                "  shift || true",
                "done",
                'if [[ -n "$last_message" ]]; then',
                '  printf "%s\\n" "$FAKE_CODEX_LAST_MESSAGE" > "$last_message"',
                "fi",
                'printf "%s\\n" \'{"type":"thread.started","thread_id":"codex-fixture-session"}\'',
                'printf "%s\\n" \'{"type":"item.completed","item":{"type":"agent_message","text":"Codex stream body."}}\'',
                'exit "${FAKE_CODEX_EXIT:-0}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _strip_ansi(payload: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", payload)


def _generate_and_run_spawn_launcher(
    tmp_path: Path,
    agent: str,
    env: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    plan = tmp_path / f"{agent}-plan.md"
    root = tmp_path / f"{agent}-root"
    plan.write_text("Hermetic launcher salvage fixture.\n", encoding="utf-8")
    root.mkdir()

    spawn_script = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / f"{agent}_spawn.sh"
    )
    dry_run = subprocess.run(
        [
            "bash",
            str(spawn_script),
            "--runtime",
            "headless",
            "--root",
            str(root),
            "--dry-run",
            str(plan),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    clean_stdout = _strip_ansi(dry_run.stdout)
    launcher_match = re.search(r"launcher generated only: (.+)", clean_stdout)
    report_match = re.search(r"report:\s+(.+)", clean_stdout)
    assert launcher_match, clean_stdout
    assert report_match, clean_stdout
    launcher = Path(launcher_match.group(1).strip())
    report = Path(report_match.group(1).strip())
    transcript = report.with_suffix(".transcript.log")
    last_message = Path(str(transcript).removesuffix(".log") + ".last-message.md")

    result = subprocess.run(
        ["bash", str(launcher)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, report, transcript, last_message


def test_codex_and_claude_launchers_salvage_last_message_on_missing_report(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    home.mkdir()
    fake_bin.mkdir()

    claude_final = "# Claude final message\n\nSalvaged from result JSONL."
    codex_final = "# Codex final message\n\nSalvaged from --output-last-message."
    _write_fake_claude_stream_agent(fake_bin, claude_final)
    _write_fake_codex_last_message_agent(fake_bin, codex_final)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_INLINE_STARTUP_WATCH"] = "0"
    env["FAKE_CODEX_LAST_MESSAGE"] = codex_final

    claude_result, claude_report, _, claude_last_message = (
        _generate_and_run_spawn_launcher(tmp_path, "claude", env)
    )
    codex_result, codex_report, _, codex_last_message = (
        _generate_and_run_spawn_launcher(tmp_path, "codex", env)
    )

    assert claude_result.returncode == 0, claude_result.stderr
    assert codex_result.returncode == 0, codex_result.stderr
    claude_body = claude_report.read_text(encoding="utf-8")
    codex_body = codex_report.read_text(encoding="utf-8")
    assert "status: completed" in claude_body
    assert "status: completed" in codex_body
    assert claude_final in claude_body
    assert codex_final in codex_body
    assert "no final message was captured" not in claude_body
    assert claude_last_message.read_text(encoding="utf-8").strip() == claude_final
    assert codex_last_message.read_text(encoding="utf-8").strip() == codex_final


def test_claude_launcher_salvages_final_message_on_failure(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    home.mkdir()
    fake_bin.mkdir()

    final_message = (
        "# Claude failed final message\n\nThe useful failure report survived."
    )
    _write_fake_claude_stream_agent(fake_bin, final_message)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_INLINE_STARTUP_WATCH"] = "0"
    env["FAKE_CLAUDE_EXIT"] = "7"

    result, report, _, last_message = _generate_and_run_spawn_launcher(
        tmp_path,
        "claude",
        env,
    )

    assert result.returncode == 7
    body = report.read_text(encoding="utf-8")
    assert "status: failed" in body
    assert final_message in body
    assert "no final message was captured" not in body
    assert last_message.read_text(encoding="utf-8").strip() == final_message


def _fake_agent_script(agent: str, final_message: str, stream_json: bool) -> str:
    env_prefix = agent.upper()
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'prompt_file=""',
        'task_text=""',
        "while [[ $# -gt 0 ]]; do",
        '  case "$1" in',
        "    --prompt-file)",
        "      shift",
        '      prompt_file="${1:-}"',
        "      ;;",
        "    --prompt-file=*)",
        '      prompt_file="${1#--prompt-file=}"',
        "      ;;",
        "    --task=*)",
        '      task_text="${1#--task=}"',
        "      ;;",
        "    --task)",
        "      shift",
        '      task_text="${1:-}"',
        "      ;;",
        # agy >= 1.1 delivers the prompt as the value of --print (no stdin),
        # so the fake agent must read it from there like the real CLI.
        "    --print)",
        "      shift",
        '      task_text="${1:-}"',
        "      ;;",
        "    --print=*)",
        '      task_text="${1#--print=}"',
        "      ;;",
        "  esac",
        "  shift || true",
        "done",
        'if [[ -n "$prompt_file" ]]; then',
        '  prompt_text="$(cat "$prompt_file")"',
        'elif [[ -n "$task_text" ]]; then',
        '  prompt_text="$task_text"',
        "else",
        '  prompt_text="$(cat)"',
        "fi",
        'report_path="$(printf "%s\\n" "$prompt_text" | awk -F": " \'/^Report path: / { print $2; exit }\')"',
        'if [[ "${FAKE_WRITE_REPORT:-0}" == "1" && -n "$report_path" ]]; then',
        '  mkdir -p "$(dirname "$report_path")"',
        "  cat > \"$report_path\" <<'REPORT'",
        "---",
        f"agent: {agent}",
        "status: completed",
        "---",
        "",
        f"{agent} worker-authored report survived.",
        "REPORT",
        "fi",
    ]
    if stream_json:
        stream = "\n".join(
            [
                f"{agent} CLI banner ignored by grep",
                json.dumps(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": final_message,
                    }
                ),
                json.dumps({"type": "result", "status": "done"}),
            ]
        )
        lines.extend(["cat <<'JSONL'", stream, "JSONL"])
    else:
        lines.extend(["cat <<'TXT'", final_message, "TXT"])
    lines.append(f'exit "${{FAKE_{env_prefix}_EXIT:-0}}"')
    return "\n".join(lines) + "\n"


def _write_fake_salvage_agent(
    bin_dir: Path,
    agent: str,
    final_message: str,
    *,
    stream_json: bool = False,
) -> None:
    script = bin_dir / agent
    script.write_text(
        _fake_agent_script(agent, final_message, stream_json),
        encoding="utf-8",
    )
    script.chmod(0o755)


def _fleet_salvage_env(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_RUNTIME_BIN"] = str(fake_bin)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["VIBECRAFTED_INLINE_STARTUP_WATCH"] = "0"
    return env


@pytest.mark.parametrize(
    ("agent", "stream_json"),
    [
        ("agy", False),
        ("grok", False),
        ("junie", False),
    ],
)
def test_remaining_launchers_salvage_final_message_on_missing_report_success(
    tmp_path: Path,
    agent: str,
    stream_json: bool,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    final_message = f"# {agent} final message\n\nSalvaged from captured stdout."
    _write_fake_salvage_agent(
        fake_bin,
        agent,
        final_message,
        stream_json=stream_json,
    )
    env = _fleet_salvage_env(tmp_path, fake_bin)

    result, report, _, last_message = _generate_and_run_spawn_launcher(
        tmp_path,
        agent,
        env,
    )

    assert result.returncode == 0, result.stderr
    body = report.read_text(encoding="utf-8")
    assert "status: completed" in body
    assert final_message in body
    assert "no final message was captured" not in body
    assert final_message in last_message.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("agent", "stream_json", "exit_code"),
    [
        ("agy", False, "12"),
        ("grok", False, "13"),
        ("junie", False, "14"),
    ],
)
def test_remaining_launchers_salvage_final_message_on_missing_report_failure(
    tmp_path: Path,
    agent: str,
    stream_json: bool,
    exit_code: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    final_message = f"# {agent} failed final message\n\nFailure details survived."
    _write_fake_salvage_agent(
        fake_bin,
        agent,
        final_message,
        stream_json=stream_json,
    )
    env = _fleet_salvage_env(tmp_path, fake_bin)
    env[f"FAKE_{agent.upper()}_EXIT"] = exit_code

    result, report, _, last_message = _generate_and_run_spawn_launcher(
        tmp_path,
        agent,
        env,
    )

    assert result.returncode == int(exit_code)
    body = report.read_text(encoding="utf-8")
    assert "status: failed" in body
    assert final_message in body
    assert "no final message was captured" not in body
    assert final_message in last_message.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("agent", "stream_json"),
    [
        ("agy", False),
        ("grok", False),
        ("junie", False),
    ],
)
def test_remaining_launchers_do_not_overwrite_worker_authored_reports(
    tmp_path: Path,
    agent: str,
    stream_json: bool,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    final_message = f"# {agent} stdout final message\n\nThis stays out of the report."
    _write_fake_salvage_agent(
        fake_bin,
        agent,
        final_message,
        stream_json=stream_json,
    )
    env = _fleet_salvage_env(tmp_path, fake_bin)
    env["FAKE_WRITE_REPORT"] = "1"

    result, report, _, last_message = _generate_and_run_spawn_launcher(
        tmp_path,
        agent,
        env,
    )

    assert result.returncode == 0, result.stderr
    body = report.read_text(encoding="utf-8")
    assert f"{agent} worker-authored report survived." in body
    assert final_message not in body
    assert final_message in last_message.read_text(encoding="utf-8")


def test_launcher_server_help_and_invalid_verb() -> None:
    result_help = subprocess.run(
        [str(LAUNCHER), "server", "help"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert "Manage the local control-plane viewer server" in result_help.stdout
    assert "vibecrafted server [start|stop|status|open|doctor]" in result_help.stdout

    result_invalid = subprocess.run(
        [str(LAUNCHER), "server", "invalidaction"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result_invalid.returncode != 0
    assert "Unknown server action: invalidaction" in result_invalid.stderr


CANONICAL_DECK = REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
_SOCKET_DIR_GUARD = 'if [[ -z "${VC_FRAME_SOCKET_DIR:-}" ]]; then'


def _socket_dir_stanza(deck: Path) -> str:
    """The deck's vc-frame socket-dir block, located by its guard line."""
    lines = deck.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == _SOCKET_DIR_GUARD)
    end = next(i for i, line in enumerate(lines[start:], start) if line == "fi")
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize(
    "deck",
    [
        pytest.param(LAUNCHER, id="scripts/vibecrafted"),
        pytest.param(CANONICAL_DECK, id="deck/vibecrafted"),
    ],
)
def test_deck_binds_a_short_per_uid_vc_frame_socket_dir(deck: Path) -> None:
    """Darwin caps AF_UNIX sun_path near 104 bytes and the macOS per-user $TMPDIR
    already spends ~81 of them, so vc-frame computed a negative name budget and
    refused any non-trivial session name. The deck is the single door every
    vc-frame surface walks through, so the short socket home is bound there."""

    stanza = _socket_dir_stanza(deck)
    probe = f'{stanza}\nprintf "%s" "${{VC_FRAME_SOCKET_DIR:-UNSET}}"\n'

    default = subprocess.run(
        ["bash", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "VC_FRAME_SOCKET_DIR"},
    ).stdout
    override = subprocess.run(
        ["bash", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "VC_FRAME_SOCKET_DIR": "/tmp/operator-choice"},
    ).stdout

    assert default == f"/tmp/vc-frame-{os.getuid()}"
    assert len(default) < 30, "socket home must leave room for a session name"
    assert os.path.isdir(default)
    assert os.stat(default).st_mode & 0o777 == 0o700
    assert override == "/tmp/operator-choice", "explicit operator override wins"
