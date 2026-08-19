from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)


def _write_capture_command(bin_dir: Path, name: str, capture_file: Path) -> None:
    script_names = [name]
    if name == "vc-frame":
        script_names.insert(0, "vc-frame")
    for script_name in script_names:
        script = bin_dir / script_name
        script.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'state_file="${CAPTURE_FILE}.session"\n'
            'if [[ "$(basename "$0")" == "vc-frame" && "${1:-}" == "ls" ]]; then\n'
            '  [[ -f "$state_file" ]] && printf "%s [Created now]\\n" "$(<"$state_file")"\n'
            "  exit 0\n"
            "fi\n"
            'if [[ "$*" == "action new-tab --help" ]]; then\n'
            '  printf "%s\\n" "${FAKE_VC_FRAME_NEW_TAB_HELP:-}"\n'
            "  exit 0\n"
            "fi\n"
            'printf "%s\\n" "$@" > "$CAPTURE_FILE"\n'
            'if [[ "$(basename "$0")" == "vc-frame" && "$*" == *"--new-session-with-layout"* ]]; then\n'
            '  previous=""\n'
            '  for argument in "$@"; do\n'
            '    if [[ "$previous" == "--session" ]]; then\n'
            '      printf "%s" "$argument" > "$state_file"\n'
            "      break\n"
            "    fi\n"
            '    previous="$argument"\n'
            "  done\n"
            "fi\n",
            encoding="utf-8",
        )
        script.chmod(0o755)


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
                'name_file = state_file.with_suffix(".name")',
                'state = state_file.read_text(encoding="utf-8").strip() if state_file.exists() else "missing"',
                f'session = os.environ.get("FAKE_VC_FRAME_SESSION", "{default_session}")',
                "if name_file.exists():",
                '    session = name_file.read_text(encoding="utf-8").strip()',
                'if "--session" in args:',
                '    idx = args.index("--session")',
                "    if idx + 1 < len(args):",
                "        session = args[idx + 1]",
                'elif args[:1] == ["attach"] and len(args) > 1:',
                "    session = args[-1]",
                'with capture.open("a", encoding="utf-8") as fh:',
                '    fh.write("VC_FRAME " + " ".join(args) + "\\n")',
                'if args[:1] == ["ls"]:',
                '    if os.environ.get("FAKE_VC_FRAME_DUPLICATE") == "1":',
                '        print(f"{session} [Created 2m ago]")',
                '        print(f"{session} [Created 1m ago] (EXITED - attach to resurrect)")',
                "        sys.exit(0)",
                '    if state == "live":',
                '        print(f"{session} [Created 1m ago]")',
                '    elif state == "dead":',
                '        print(f"{session} [Created 1m ago] (EXITED - attach to resurrect)")',
                "    sys.exit(0)",
                # list-sessions feeds spawn_session_is_live (awk drops EXITED), so it
                # only needs to report a LIVE session. Emitting an EXITED line here
                # regresses the dead-session recreate tests, which rely on the
                # ls-based recovery path; keep list-sessions live-only.
                'if args[:1] == ["list-sessions"]:',
                '    if state == "live":',
                '        print(f"{session} [Created 1m ago]")',
                "    sys.exit(0)",
                'if args[:1] == ["attach"]:',
                '    if "--force-run-commands" in args:',
                '        state_file.write_text("live", encoding="utf-8")',
                '        name_file.write_text(session, encoding="utf-8")',
                "    sys.exit(0)",
                'if args[:1] == ["kill-session"]:',
                '    state_file.write_text("missing", encoding="utf-8")',
                "    name_file.unlink(missing_ok=True)",
                "    sys.exit(0)",
                'if args[:1] == ["delete-session"]:',
                '    state_file.write_text("missing", encoding="utf-8")',
                "    name_file.unlink(missing_ok=True)",
                "    sys.exit(0)",
                'if "--new-session-with-layout" in args:',
                '    state_file.write_text("live", encoding="utf-8")',
                '    name_file.write_text(session, encoding="utf-8")',
                "    sys.exit(0)",
                'if "action" in args and ("new-pane" in args or "new-tab" in args):',
                '    if state != "live":',
                '        print("There is no active session!", file=sys.stderr)',
                "        sys.exit(1)",
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


def _write_implicit_gc_probe_vc_frame(bin_dir: Path) -> None:
    script = bin_dir / "vc-frame"
    script.write_text(
        """#!/usr/bin/env python3
import os
import sys
from pathlib import Path

args = sys.argv[1:]
capture = Path(os.environ["CAPTURE_FILE"])
state_file = capture.with_suffix(".session")
with capture.open("a", encoding="utf-8") as fh:
    fh.write("VC_FRAME " + " ".join(args) + "\\n")
if args[:1] == ["ls"]:
    if state_file.exists():
        print(f"{state_file.read_text(encoding='utf-8').strip()} [Created now]")
    sys.exit(0)
if args[:1] == ["list-sessions"]:
    print("abandoned-evidence [Created 72h ago] (EXITED - attach to resurrect)")
if "--new-session-with-layout" in args and "--session" in args:
    state_file.write_text(args[args.index("--session") + 1], encoding="utf-8")
sys.exit(0)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_osascript(
    bin_dir: Path, capture_file: Path, session_state_file: Path
) -> None:
    script = bin_dir / "osascript"
    script.write_text(
        '#!/usr/bin/env python3\nimport os\nimport sys\nfrom pathlib import Path\n\npayload = sys.stdin.read()\ncapture = Path(os.environ["CAPTURE_FILE"])\nstate_file = Path(os.environ["SESSION_STATE_FILE"])\nwith capture.open("a", encoding="utf-8") as fh:\n    fh.write("OSA " + payload.replace("\\n", "\\\\n") + "\\n")\nif "new-session-with-layout" in payload or "attach --force-run-commands" in payload:\n    state_file.write_text("live", encoding="utf-8")'
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _expected_operator_session(run_id: str | None = None) -> str:
    base = (
        re.sub(r"[^a-z0-9]+", "-", REPO_ROOT.name.lower()).strip("-") or "vibecrafted"
    )
    return f"{base}-{run_id}" if run_id else base


def _resolved_workspace_session(env: dict[str, str]) -> str:
    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "vibecrafted"),
            "workspace",
            "resolve",
            "--env",
        ],
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


def _org_repo() -> str:
    remote = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    match = re.search(r"[:/]([^/]+)/([^/.]+)(?:\.git)?$", remote)
    assert match
    return f"{match.group(1)}/{match.group(2)}"


def test_vc_start_launches_operator_entrypoint_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("missing", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    expected_session = _resolved_workspace_session(env)
    env["FAKE_VC_FRAME_SESSION"] = expected_session

    subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-start'],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8")
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


def test_vc_start_with_stale_frame_env_creates_session_foreground(
    tmp_path: Path,
) -> None:
    """Stale frame env without pane ids must not fake 'inside vc-frame'.

    A bare shell that once ran vc-start (or inherited leaked VC_FRAME/ZELLIJ)
    carries the session-name exports but no pane id. The launcher must take
    the outside path: create the session in the FOREGROUND. The old loose
    check silently no-opped when the stale name equalled the target session
    and otherwise raced a background create + switch-session at a session
    with no live client.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("missing", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME_CONFIG_DIR", None)
    expected_session = _resolved_workspace_session(env)
    env["FAKE_VC_FRAME_SESSION"] = expected_session
    # Stale leak: session-name exports and pane markers WITHOUT pane ids,
    # with the stale name equal to the target operator session.
    env["VC_FRAME"] = "0"
    env["ZELLIJ"] = "0"
    env["VC_FRAME_SESSION_NAME"] = expected_session
    env["ZELLIJ_SESSION_NAME"] = expected_session
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("ZELLIJ_PANE_ID", None)

    subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-start'],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8")
    assert "--session" in payload
    assert expected_session in payload
    assert "--new-session-with-layout" in payload
    assert "switch-session" not in payload


def test_operator_console_first_screen_is_actionable() -> None:
    payload = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "vc-operator"
        / "mission-control"
        / "operator-console.sh"
    ).read_text(encoding="utf-8")

    expected_fragments = [
        "Vibecrafted Operator",
        "vc-start",
        "vibecrafted workflow codex --prompt",
        "vibecrafted implement codex --file",
        "vibecrafted research --prompt",
        "vibecrafted codex observe --run-id",
        "vibecrafted codex await --run-id",
        "~/.vibecrafted/artifacts",
        "close terminal: detach",
        "Ctrl+q: quit intentionally",
    ]

    for fragment in expected_fragments:
        assert fragment in payload


def test_vc_start_does_not_run_implicit_session_gc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    home.mkdir()
    fake_bin.mkdir()
    _write_implicit_gc_probe_vc_frame(fake_bin)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "CAPTURE_FILE": str(capture_file),
            "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME": "1",
        }
    )
    for name in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "VIBECRAFTED_OPERATOR_SESSION",
        "VIBECRAFTED_OPERATOR_MODE",
    ):
        env.pop(name, None)

    result = subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-start'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8")
    assert "VC_FRAME list-sessions" not in payload
    assert "VC_FRAME kill-session abandoned-evidence" not in payload


def test_explicit_gc_apply_never_selects_untyped_sessions(tmp_path: Path) -> None:
    """EXITED/stale session text is not authority for an untyped kill-session."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    home.mkdir()
    fake_bin.mkdir()
    _write_implicit_gc_probe_vc_frame(fake_bin)
    script = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "vc-operator"
        / "mission-control"
        / "vc-frame-gc.sh"
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
        }
    )

    result = subprocess.run(
        ["bash", str(script), "--apply", "--max-age-hours", "1"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "VC_FRAME list-sessions" not in payload
    assert "VC_FRAME kill-session" not in payload

    refused = subprocess.run(
        ["bash", str(script), "--apply", "--include-live"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "no typed incarnation selector" in refused.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "VC_FRAME kill-session" not in payload


def test_prepare_operator_runtime_does_not_run_gc_for_headless(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    home.mkdir()
    fake_bin.mkdir()
    _write_implicit_gc_probe_vc_frame(fake_bin)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "CAPTURE_FILE": str(capture_file),
        }
    )

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; _vetcoders_prepare_operator_runtime headless',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "VC_FRAME list-sessions" not in payload
    assert "VC_FRAME kill-session abandoned-evidence" not in payload


def test_generic_skill_does_not_run_implicit_session_gc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    home.mkdir()
    fake_bin.mkdir()
    _write_implicit_gc_probe_vc_frame(fake_bin)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "CAPTURE_FILE": str(capture_file),
        }
    )
    command = (
        f'source "{HELPER_SCRIPT}"; '
        "_vetcoders_dispatch_skill_prompt() { :; }; "
        "_vetcoders_print_launch_receipt() { :; }; "
        "_vetcoders_maybe_spawn_await_pane() { :; }; "
        'codex-followup --runtime headless --prompt "Check runtime"'
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "VC_FRAME list-sessions" not in payload
    assert "VC_FRAME kill-session abandoned-evidence" not in payload


def test_operator_console_does_not_run_implicit_session_gc(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    mission_control = tmp_path / "mission-control"
    capture_file = tmp_path / "capture.log"
    fake_shell = tmp_path / "fake-shell"
    home.mkdir()
    fake_bin.mkdir()
    mission_control.mkdir()
    _write_implicit_gc_probe_vc_frame(fake_bin)
    fake_shell.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_shell.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
            "SHELL": str(fake_shell),
        }
    )
    source_mission_control = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "vc-operator"
        / "mission-control"
    )
    operator_console = mission_control / "operator-console.sh"
    gc_script = mission_control / "vc-frame-gc.sh"
    shutil.copy2(source_mission_control / operator_console.name, operator_console)
    shutil.copy2(source_mission_control / gc_script.name, gc_script)
    operator_console.chmod(0o755)
    gc_script.chmod(0o755)

    result = subprocess.run(
        [str(operator_console)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
    assert "VC_FRAME list-sessions" not in payload
    assert "VC_FRAME kill-session abandoned-evidence" not in payload


def test_helper_exports_vc_skill_wrappers() -> None:
    expected_wrappers = [
        "vc-agents",
        "vc-audit",
        "vc-decorate",
        "vc-delegate",
        "vc-dou",
        "vc-followup",
        "vc-guard",
        "vc-hydrate",
        "vc-init",
        "vc-intents",
        "vc-justdo",
        "vc-marbles",
        "vc-ownership",
        "vc-partner",
        "vc-prune",
        "vc-release",
        "vc-review",
        "vc-scaffold",
        "vc-trust",
        "vc-workflow",
    ]
    command = f'source "{HELPER_SCRIPT}"; ' + " ".join(
        f"command -v {wrapper} >/dev/null || {{ echo missing:{wrapper} >&2; exit 1; }};"
        for wrapper in expected_wrappers
    )

    result = subprocess.run(
        ["bash", "-lc", command],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_vc_init_finds_bundled_vc_frame_and_creates_missing_operator_session(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    bundled_bin = runtime_home / "bin"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    bundled_bin.mkdir(parents=True)
    fake_bin.mkdir()
    _write_stateful_vc_frame(bundled_bin, capture_file, session_state_file)
    (fake_bin / "osascript").write_text(
        "#!/usr/bin/env bash\nexit 1\n", encoding="utf-8"
    )
    (fake_bin / "osascript").chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    # This test exercises the real session-create path; allow it without a TTY.
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    # Scrub any operator-session context leaked from a running operator shell so
    # the runtime computes a fresh session instead of latching onto the ambient one.
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VIBECRAFTED_OPERATOR_MODE", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; vc-init claude --prompt "Check runtime"',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = capture_file.read_text(encoding="utf-8")
    expected_session = _expected_operator_session()
    assert f"--session {expected_session} --new-session-with-layout" in payload
    assert f"--session {expected_session} action new-tab" in payload
    assert f"run_id=interactive target={expected_session}/claude" in result.stdout
    assert f"watch=vc-frame attach {expected_session}" in result.stdout
    assert "There is no active session!" not in result.stderr


def test_operator_spawn_success_prints_actionable_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    home.mkdir()
    fake_bin.mkdir()
    _write_capture_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
            "VIBECRAFTED_OPERATOR_SESSION": "receipt-session",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_RUN_ID": "impl-receipt-1",
            "FAKE_VC_FRAME_NEW_TAB_HELP": "--after-base --no-focus",
        }
    )
    for name in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_SESSION_NAME",
        "ZELLIJ_PANE_ID",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_spawn_into_operator_session "codex-init" "true"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "launch accepted: run_id=impl-receipt-1 "
        "target=receipt-session/codex-init "
        "watch=vc-frame attach receipt-session"
    ) in result.stdout
    captured = capture_file.read_text(encoding="utf-8")
    assert "action\nnew-tab" in captured
    assert "--after-base" in captured
    assert "--no-focus" not in captured


def test_worker_session_spawn_uses_no_focus_when_supported(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    home.mkdir()
    fake_bin.mkdir()
    _write_capture_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
            "VIBECRAFTED_OPERATOR_SESSION": "operator-seat",
            "VIBECRAFTED_WORKER_SESSION": "worker-host",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_RUN_ID": "impl-worker-1",
            "FAKE_VC_FRAME_NEW_TAB_HELP": "--after-base --no-focus",
        }
    )
    for name in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_SESSION_NAME",
        "ZELLIJ_PANE_ID",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_spawn_into_operator_session "resume-codex" "true"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    captured = capture_file.read_text(encoding="utf-8")
    assert "--session\nworker-host\naction\nnew-tab" in captured
    assert "--after-base" in captured
    assert "--no-focus" in captured


def test_operator_spawn_failure_is_loud_and_preserves_status(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    home.mkdir()
    fake_bin.mkdir()
    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text("#!/usr/bin/env bash\nexit 37\n", encoding="utf-8")
    vc_frame.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "VIBECRAFTED_OPERATOR_SESSION": "receipt-session",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_RUN_ID": "impl-receipt-2",
        }
    )
    for name in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "ZELLIJ",
        "ZELLIJ_SESSION_NAME",
        "ZELLIJ_PANE_ID",
    ):
        env.pop(name, None)

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_spawn_into_operator_session "marbles" "true"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 37
    assert (
        "launch failed: run_id=impl-receipt-2 target=receipt-session/marbles status=37"
    ) in result.stderr


def test_vc_init_missing_vc_frame_message_has_fresh_install_path_hint(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"

    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; vc-init claude --prompt "Check runtime"',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "vc-frame is required for the Vibecrafted operator runtime." in result.stderr
    assert (
        "Run 'vc-start' first to create or attach the operator vc-frame session, then retry."
        in result.stderr
    )
    assert (
        f"Expected vc-frame on PATH or bundled at: {home}/.local/share/vibecrafted/bin/vc-frame"
        in result.stderr
    )


def test_explicit_terminal_marbles_from_operator_mode_spawns_fresh_tab(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    fake_bin.mkdir()
    _write_capture_command(fake_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME"] = "operator"
    env["VIBECRAFTED_RUN_ID"] = "marb-014520"
    env["VIBECRAFTED_MARBLES_RUN_ID"] = "marb-014520"
    expected_session = _expected_operator_session()
    env["VC_FRAME_SESSION_NAME"] = expected_session
    subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                'codex-marbles --runtime terminal --prompt "Check runtime" --count 2'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    # One marbles run owns a dedicated host session. Its first surface is a
    # fresh marbles tab, never a pane in the operator's active session.
    assert payload[:4] == [
        "--session",
        expected_session,
        "action",
        "new-tab",
    ]
    assert "--name" in payload
    assert "marbles" in " ".join(payload) or "marb-014520" in payload
    assert expected_session in payload


def test_explicit_terminal_marbles_inside_vc_frame_prefers_bundled_vc_frame(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    bundled_bin = runtime_home / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"

    home.mkdir()
    bundled_bin.mkdir(parents=True)
    _write_capture_command(bundled_bin, "vc-frame", capture_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["PATH"] = os.defpath
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME"] = "operator"
    env["VIBECRAFTED_RUN_ID"] = "marb-014520"
    env["VIBECRAFTED_MARBLES_RUN_ID"] = "marb-014520"
    expected_session = _expected_operator_session()
    env["VC_FRAME_SESSION_NAME"] = expected_session

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                'codex-marbles --runtime terminal --prompt "Check runtime" --count 2 && '
                'printf "PATH=%s\\n" "$PATH"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = capture_file.read_text(encoding="utf-8")
    # Bundled vc-frame must create the tab in the dedicated marbles host.
    assert f"--session\n{expected_session}\naction\nnew-tab\n" in payload
    assert result.stdout.endswith(f"PATH={os.defpath}\n")


def test_explicit_terminal_marbles_manual_spawn_omits_l1_transcript_tail(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "vc_frame-args.txt"
    run_id = "marb-424242"
    transcript = tmp_path / "l1.transcript.log"
    reports_dir = (
        home
        / ".vibecrafted"
        / "artifacts"
        / _org_repo()
        / datetime.now(timezone.utc).strftime("%Y_%m%d")
        / "marbles"
        / "reports"
    )
    meta = reports_dir / "fixture.meta.json"

    home.mkdir()
    fake_bin.mkdir()
    reports_dir.mkdir(parents=True)
    _write_capture_command(fake_bin, "vc-frame", capture_file)
    _write_capture_command(fake_bin, "codex", capture_file)
    (fake_bin / "osascript").write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    (fake_bin / "osascript").chmod(0o755)

    transcript.write_text(
        "\n".join(f"line {idx}" for idx in range(1, 21)) + "\n",
        encoding="utf-8",
    )
    meta.write_text(
        json.dumps(
            {
                "run_id": f"{run_id}-001",
                "transcript": str(transcript),
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME"] = "operator"
    env["VIBECRAFTED_RUN_ID"] = "marb-014521"
    env["VC_FRAME_SESSION_NAME"] = _expected_operator_session(env["VIBECRAFTED_RUN_ID"])
    env["VIBECRAFTED_MARBLES_RUN_ID"] = run_id
    env["VIBECRAFTED_MARBLES_PROBE_TTL"] = "10"
    env["VIBECRAFTED_PREFER_REPO_SPAWN"] = "1"

    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                'codex-marbles --runtime terminal --prompt "Check runtime" --count 2'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "--- marbles L1 transcript tail" not in result.stdout
    assert "line 6" not in result.stdout
    assert "line 20" not in result.stdout
    assert result.stderr == ""
    assert "action\nnew-tab" in capture_file.read_text(encoding="utf-8")


def test_spawn_script_prefers_repo_runtime_over_installed_copy(tmp_path: Path) -> None:
    home = tmp_path / "home"
    installed = home / ".vibecrafted" / "skills" / "vc-agents" / "scripts"

    installed.mkdir(parents=True)
    (installed / "marbles_spawn.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_PREFER_REPO_SPAWN"] = "1"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_spawn_script claude marbles_spawn.sh"
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == str(
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "marbles_spawn.sh"
    )


def test_vc_start_resume_resurrects_dead_session(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("dead", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-start resume'],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = capture_file.read_text(encoding="utf-8")
    # Dead sessions are recovery evidence: preserve them and launch a fresh
    # frame session with the layout file.
    expected = _expected_operator_session()
    created = re.search(r"creating '([^']+)'", result.stderr)
    assert created is not None
    recovery_session = created.group(1)
    assert f"Session '{expected}' is dead; preserving it" in result.stderr
    assert recovery_session != expected
    assert f"kill-session {expected}" not in payload
    assert f"--session {recovery_session}" in payload
    assert "--new-session-with-layout" in payload


def test_dead_session_recovery_failure_is_not_reported_as_prepared(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("dead", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    vc_frame = fake_bin / "vc-frame"
    source = vc_frame.read_text(encoding="utf-8")
    vc_frame.write_text(
        source.replace(
            'if "--new-session-with-layout" in args:\n'
            '    state_file.write_text("live", encoding="utf-8")\n'
            '    name_file.write_text(session, encoding="utf-8")\n'
            "    sys.exit(0)",
            'if "--new-session-with-layout" in args:\n'
            '    print("socket path rejected", file=sys.stderr)\n'
            "    sys.exit(2)",
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_ensure_vc_frame_session "workspace-deadbeef" '
                '"$(_vetcoders_operator_layout_file)"; '
                'rc=$?; printf "prepared=%s\\n" '
                '"${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-}"; exit "$rc"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout.strip() == "prepared="
    assert "socket path rejected" in result.stderr


def test_legacy_frame_namespace_is_attached_to_wes_before_new_window(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    frame_capture = tmp_path / "frame.log"
    wes_capture = tmp_path / "wes.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("dead", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, frame_capture, session_state_file)
    vibecrafted = fake_bin / "vibecrafted"
    vibecrafted.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$WES_CAPTURE_FILE"\n',
        encoding="utf-8",
    )
    vibecrafted.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(frame_capture)
    env["WES_CAPTURE_FILE"] = str(wes_capture)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["FAKE_VC_FRAME_SESSION"] = "workspace-deadbeef"
    env["FAKE_VC_FRAME_DUPLICATE"] = "1"
    env["VIBECRAFTED_WORKSPACE_ID"] = "019ca123-1234-7123-8123-123456789abc"
    env["VIBECRAFTED_SESSION_ID"] = "019ca124-1234-7123-8123-123456789abc"
    env["VIBECRAFTED_WORKSPACE_INSTANCE_ID"] = "019ca125-1234-7123-8123-123456789abc"
    env["VC_FRAME_SOCKET_DIR"] = "/tmp/vc-frame-501"
    env["ZELLIJ_SOCKET_DIR"] = "/tmp/vc-frame-501"
    env["VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR"] = "/legacy/vc-frame-501"

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; _vetcoders_import_legacy_vc_frame_sessions',
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    args = wes_capture.read_text(encoding="utf-8").splitlines()
    assert args[:2] == ["workspace", "session-attach"]
    assert args[args.index("--runtime-session-id") + 1] == "workspace-deadbeef"
    # vc-frame may expose a live socket and stale EXITED cache with the same
    # name. One live incarnation wins over duplicate dead metadata.
    assert args[args.index("--state") + 1] == "live"
    assert args[args.index("--socket-dir") + 1] == "/legacy/vc-frame-501"


def test_new_external_frame_session_is_live_in_wes_before_client_detaches(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    events_file = tmp_path / "events.log"
    session_state_file = tmp_path / "session-state.txt"
    release_file = tmp_path / "release-client"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("missing", encoding="utf-8")

    vc_frame = fake_bin / "vc-frame"
    vc_frame.write_text(
        """#!/usr/bin/env python3
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
events = Path(os.environ["EVENTS_FILE"])
state_file = Path(os.environ["SESSION_STATE_FILE"])
release_file = Path(os.environ["RELEASE_FILE"])
session = os.environ["FAKE_VC_FRAME_SESSION"]
state = state_file.read_text(encoding="utf-8").strip()

if args[:1] == ["ls"]:
    if state == "live":
        print(f"{session} [Created now]")
    raise SystemExit(0)

if "--new-session-with-layout" in args:
    with events.open("a", encoding="utf-8") as handle:
        handle.write("FRAME start\\n")
    state_file.write_text("live", encoding="utf-8")
    while not release_file.exists():
        time.sleep(0.02)
    with events.open("a", encoding="utf-8") as handle:
        handle.write("FRAME exit\\n")
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)

    vibecrafted = fake_bin / "vibecrafted"
    vibecrafted.write_text(
        '#!/usr/bin/env bash\nprintf "WES %s\\n" "$*" >> "$EVENTS_FILE"\n',
        encoding="utf-8",
    )
    vibecrafted.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["EVENTS_FILE"] = str(events_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["RELEASE_FILE"] = str(release_file)
    env["FAKE_VC_FRAME_SESSION"] = "workspace-deadbeef"
    env["VIBECRAFTED_WORKSPACE_ID"] = "019ca123-1234-7123-8123-123456789abc"
    env["VIBECRAFTED_SESSION_ID"] = "019ca124-1234-7123-8123-123456789abc"
    env["VIBECRAFTED_WORKSPACE_INSTANCE_ID"] = "019ca125-1234-7123-8123-123456789abc"
    env["VC_FRAME_SOCKET_DIR"] = "/tmp/vc-frame-501"
    env["ZELLIJ_SOCKET_DIR"] = "/tmp/vc-frame-501"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    process = subprocess.Popen(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_ensure_vc_frame_session "workspace-deadbeef" '
                '"/tmp/operator.kdl"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    deadline = time.monotonic() + 5
    payload = ""
    while time.monotonic() < deadline:
        payload = (
            events_file.read_text(encoding="utf-8") if events_file.exists() else ""
        )
        if "--state live" in payload:
            break
        assert process.poll() is None, payload
        time.sleep(0.02)

    assert "--state live" in payload
    assert process.poll() is None
    assert payload.index("--state missing") < payload.index("FRAME start")
    assert payload.index("FRAME start") < payload.index("--state live")

    release_file.write_text("release", encoding="utf-8")
    _stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr


def test_vc_dashboard_recreates_dead_place_session_without_layout_suffix(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("dead", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_RUN_ID"] = "marb-014520"
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    # Scrub any operator-session context leaked from a running operator shell so
    # the dashboard joins the place session rather than minting a run-id host.
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VIBECRAFTED_OPERATOR_MODE", None)

    result = subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-dashboard vc-marbles'],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = capture_file.read_text(encoding="utf-8")
    expected_session = _expected_operator_session()
    # Dead sessions are preserved; a fresh recovery session gets the layout.
    created = re.search(r"creating '([^']+)'", result.stderr)
    assert created is not None
    recovery_session = created.group(1)
    assert f"Session '{expected_session}' is dead; preserving it" in result.stderr
    assert recovery_session != expected_session
    assert f"kill-session {expected_session}" not in payload
    assert f"--session {recovery_session}" in payload
    assert "--new-session-with-layout" in payload
    assert f"{expected_session}-marbles" not in payload


def test_explicit_terminal_skill_bootstraps_operator_session_before_spawning(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = home / ".local" / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir(parents=True)
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)
    _write_capture_command(fake_bin, "codex", tmp_path / "unused-codex.txt")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    # This test exercises the real session-bootstrap path; allow it without a TTY.
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    # Scrub any operator-session context leaked from a running operator shell so
    # the runtime bootstraps a session instead of latching onto the ambient one.
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VIBECRAFTED_OPERATOR_MODE", None)

    subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                'codex-followup --runtime terminal --prompt "Check runtime"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8")
    assert "OSA " not in payload
    assert "VC_FRAME ls" in payload
    assert f"--session {_expected_operator_session()}" in payload
    assert "--new-session-with-layout" in payload
    assert "vc-spawn-cmd" in payload
    assert re.search(r"\bfwup-\d{6}-\d{6}-\d{5}\b", payload)


def test_skill_bootstraps_fresh_operator_session_when_existing_one_is_dead(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("dead", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)
    _write_fake_osascript(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["VIBECRAFTED_OSASCRIPT_BIN"] = str(fake_bin / "osascript")
    env["VIBECRAFTED_RUN_ID"] = "fwup-014520"
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    # This test exercises the real dead-session recreate path; allow it without a TTY.
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    # Scrub any operator-session context leaked from a running operator shell so
    # the runtime recreates the dead place session instead of minting a run-id host.
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VIBECRAFTED_OPERATOR_MODE", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_prepare_operator_runtime terminal; "
                'printf "%s\\n" "$VIBECRAFTED_OPERATOR_SESSION"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    expected_session = _expected_operator_session()
    assert result.returncode == 0
    assert env["VIBECRAFTED_RUN_ID"] not in result.stdout
    created = re.search(r"creating '([^']+)'", result.stderr)
    assert created is not None
    recovery_session = created.group(1)
    assert result.stdout.strip() == recovery_session
    payload = capture_file.read_text(encoding="utf-8")
    assert f"Session '{expected_session}' is dead; preserving it" in result.stderr
    assert f"kill-session {expected_session}" not in payload
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
    assert "--new-session-with-layout" in payload and recovery_session in payload
    assert "OSA " not in payload


def test_operator_session_name_is_place_not_run_id(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home")
    env["VIBECRAFTED_RUN_ID"] = "work-260819-044606-42152"
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (f'source "{HELPER_SCRIPT}"; _vetcoders_operator_session_name'),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    name = result.stdout.strip()
    assert name
    assert env["VIBECRAFTED_RUN_ID"] not in name
    assert not re.fullmatch(r"workspace-[0-9a-f]{8}", name)


def test_legacy_workspace_token_operator_session_is_rewritten(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home")
    env["VIBECRAFTED_OPERATOR_SESSION"] = "workspace-8831606a"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_prepare_operator_runtime terminal; "
                'printf "%s\\n" "$VIBECRAFTED_OPERATOR_SESSION"'
            ),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    name = result.stdout.strip()
    assert name
    assert name != "workspace-8831606a"
    assert not re.fullmatch(r"workspace-[0-9a-f]{8}", name)


def test_dashboard_alt_layout_reuses_live_repo_session_instead_of_layout_session(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture_file = tmp_path / "capture.log"
    session_state_file = tmp_path / "session-state.txt"

    home.mkdir()
    fake_bin.mkdir()
    session_state_file.write_text("live", encoding="utf-8")
    _write_stateful_vc_frame(fake_bin, capture_file, session_state_file)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["CAPTURE_FILE"] = str(capture_file)
    env["SESSION_STATE_FILE"] = str(session_state_file)
    env["FAKE_VC_FRAME_SESSION"] = _expected_operator_session()
    env["VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)

    subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-dashboard vc-marbles'],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    payload = capture_file.read_text(encoding="utf-8")
    expected_session = _expected_operator_session()
    assert f"--session {expected_session} action new-tab --layout" in payload
    assert f"attach {expected_session}" in payload
    assert "--new-session-with-layout" not in payload
    assert f"{expected_session}-marbles" not in payload
