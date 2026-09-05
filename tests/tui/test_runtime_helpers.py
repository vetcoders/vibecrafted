from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
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
RUNTIME_HELPER = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "helpers"
    / "vetcoders-runtime-core.sh"
)


def _run_vetcoders_helper(
    helper_script: Path,
    command: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", f'source "{helper_script}"; {command}'],
        cwd=str(REPO_ROOT),
        env=run_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_fake_loct(fake_bin: Path, score: int, args_file: Path | None = None) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_loct = fake_bin / "loct"
    args_line = (
        'printf "%s\\n" "$@" > "$LOCT_ARGS_FILE"' if args_file is not None else ":"
    )
    fake_loct.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            {args_line}
            cat <<'JSON'
            {{"schema_version":"loctree.prism.v1","total_score":{score},"task_framings":[{{"task":"installer public contract"}}]}}
            JSON
            """
        ),
        encoding="utf-8",
    )
    fake_loct.chmod(0o755)


def _write_capture_command(bin_dir: Path, name: str, capture_file: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    script.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            printf "%s\\n" "$@" >> "$CAPTURE_FILE"
            """
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)


def _install_runtime_probe_helper(helper_root: Path, marker: str) -> None:
    helper_target = (
        helper_root
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "helpers"
        / "vetcoders-runtime-core.sh"
    )
    helper_target.parent.mkdir(parents=True, exist_ok=True)
    helper_target.write_text(
        textwrap.dedent(
            f'''
            # shellcheck shell=bash
            source "{RUNTIME_HELPER}"
            _vetcoders_spawn_home() {{
              printf "{marker}\\n"
            }}
            '''
        ),
        encoding="utf-8",
    )


def test_vetcoders_shim_prefers_runtime_helper_from_repo_root(tmp_path: Path) -> None:
    marker = "runtime-helper-from-repo-root"
    helper_root = tmp_path / "probe-runtime"
    _install_runtime_probe_helper(helper_root, marker)

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        'printf "%s\\n" "$(_vetcoders_spawn_home codex)"',
        {"VIBECRAFTED_ROOT": str(helper_root)},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == marker
    assert result.stderr == ""


def test_vetcoders_shim_prefers_staged_tools_runtime_helper(tmp_path: Path) -> None:
    marker = "runtime-helper-from-staged-tools"
    staged_home = tmp_path / "vibecrafted-home" / ".vibecrafted"
    tools_home = (
        tmp_path / "vibecrafted-home" / ".local" / "share" / "vibecrafted" / "tools"
    )
    staged_root = tools_home / "vibecrafted-current"
    _install_runtime_probe_helper(staged_root, marker)

    installed_script = (
        tmp_path / "installed-tree" / "skills" / "vc-agents" / "shell" / "vetcoders.sh"
    )
    installed_script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HELPER_SCRIPT, installed_script)

    result = _run_vetcoders_helper(
        installed_script,
        'printf "%s\\n" "$(_vetcoders_spawn_home codex)"',
        {
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_TOOLS_HOME": str(tools_home),
            "VIBECRAFTED_ROOT": "",
        },
    )

    assert result.returncode == 0
    assert result.stdout.strip() == marker
    assert result.stderr == ""


def test_vetcoders_spawn_script_path_stays_command_compatible() -> None:
    env = os.environ.copy()
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        'printf "%s\\n" "$(_vetcoders_spawn_script codex codex_spawn.sh)"',
        env=env,
    )

    assert result.returncode == 0
    spawn_script = Path(result.stdout.strip())
    assert spawn_script.name == "codex_spawn.sh"
    assert (
        spawn_script.parent
        == REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"
    )
    assert spawn_script.is_file()


def test_vetcoders_keeps_launcher_entrypoints_available() -> None:
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        "command -v vc-implement && command -v vc-research && command -v vc-polarize && command -v codex-implement",
        {"VIBECRAFTED_ROOT": str(REPO_ROOT)},
    )

    assert result.returncode == 0
    assert "vc-implement" in result.stdout
    assert "vc-research" in result.stdout
    assert "vc-polarize" in result.stdout
    assert "codex-implement" in result.stdout
    assert "command not found" not in result.stderr


def test_vetcoders_helper_source_does_not_prepend_bundled_bin_to_path(
    tmp_path: Path,
) -> None:
    preferred_bin = tmp_path / "preferred" / "bin"
    preferred_bin.mkdir(parents=True)
    preferred_vibecrafted = preferred_bin / "vibecrafted"
    preferred_vibecrafted.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    preferred_vibecrafted.chmod(0o755)

    staged_home = tmp_path / "home" / ".vibecrafted"
    runtime_home = tmp_path / "home" / ".local" / "share" / "vibecrafted"
    bundled_bin = runtime_home / "bin"
    bundled_bin.mkdir(parents=True)
    bundled_vibecrafted = bundled_bin / "vibecrafted"
    bundled_vibecrafted.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    bundled_vibecrafted.chmod(0o755)

    initial_path = f"{preferred_bin}{os.pathsep}{os.defpath}"
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        'printf "%s\\n" "$PATH"; command -v vibecrafted',
        {
            "PATH": initial_path,
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0
    path_after_source, resolved_vibecrafted = result.stdout.strip().splitlines()
    assert path_after_source == initial_path
    assert resolved_vibecrafted == str(preferred_vibecrafted)


def test_vetcoders_require_vc_frame_uses_bundled_vc_frame_priority_without_path_leak(
    tmp_path: Path,
) -> None:
    staged_home = tmp_path / "home" / ".vibecrafted"
    runtime_home = tmp_path / "home" / ".local" / "share" / "vibecrafted"
    bundled_bin = runtime_home / "bin"
    bundled_bin.mkdir(parents=True)
    bundled_vc_frame = bundled_bin / "vc-frame"
    bundled_vc_frame.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    bundled_vc_frame.chmod(0o755)

    initial_path = os.defpath
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            "_vetcoders_require_vc_frame; "
            'printf "PATH=%s\\n" "$PATH"; '
            "command -v vc-frame || true"
        ),
        {
            "PATH": initial_path,
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == f"PATH={initial_path}\n"


def test_vetcoders_vc_frame_bin_prefers_vc_frame_on_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_capture_command(fake_bin, "vc-frame", tmp_path / "vc_frame-args.txt")
    _write_capture_command(fake_bin, "vc-frame", tmp_path / "vc-frame-args.txt")

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        "_vetcoders_vc_frame_bin",
        {
            "PATH": f"{fake_bin}:{os.defpath}",
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.strip() == str(fake_bin / "vc-frame")


def test_dashboard_uses_bundled_vc_frame_priority_without_path_leak(
    tmp_path: Path,
) -> None:
    staged_home = tmp_path / "home" / ".vibecrafted"
    runtime_home = tmp_path / "home" / ".local" / "share" / "vibecrafted"
    capture_file = tmp_path / "vc-frame-args.txt"
    _write_capture_command(runtime_home / "bin", "vc-frame", capture_file)

    initial_path = os.defpath
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        '_vetcoders_launch_dashboard ls && printf "PATH=%s\\n" "$PATH"',
        {
            "CAPTURE_FILE": str(capture_file),
            "PATH": initial_path,
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert capture_file.read_text(encoding="utf-8").splitlines() == ["list-sessions"]
    assert result.stdout == f"PATH={initial_path}\n"


def test_dashboard_names_the_idempotent_same_workspace_noop(tmp_path: Path) -> None:
    layout = tmp_path / "operator.kdl"
    layout.write_text("layout {}\n", encoding="utf-8")
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            '_vetcoders_vc_frame_bin() { printf "/bin/true\\n"; }; '
            "_vetcoders_load_frontier_sidecars() { :; }; "
            '_vetcoders_dashboard_layout_file() { printf "%s\\n" "$TEST_LAYOUT"; }; '
            '_vetcoders_dashboard_session_name() { printf "workspace-42\\n"; }; '
            '_vetcoders_vc_frame_session_state() { printf "live\\n"; }; '
            "_vetcoders_in_vc_frame() { return 0; }; "
            "_vetcoders_launch_dashboard operator"
        ),
        {
            "TEST_LAYOUT": str(layout),
            "VC_FRAME_PANE_ID": "1",
            "VC_FRAME_SESSION_NAME": "workspace-42",
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == "Already in Vibecrafted workspace: workspace-42\n"


def test_await_pane_stays_silent_without_meta_helper(
    tmp_path: Path,
) -> None:
    staged_home = tmp_path / "home" / ".vibecrafted"
    runtime_home = tmp_path / "home" / ".local" / "share" / "vibecrafted"
    capture_file = tmp_path / "vc-frame-args.txt"
    _write_capture_command(runtime_home / "bin", "vc-frame", capture_file)

    initial_path = os.defpath
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            '_vetcoders_maybe_spawn_await_pane codex review run-424242 "$PWD"; '
            "sleep 0.2; "
            'printf "PATH=%s\\n" "$PATH"'
        ),
        {
            "CAPTURE_FILE": str(capture_file),
            "PATH": initial_path,
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VC_FRAME": "operator",
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert not capture_file.exists()
    assert result.stdout == f"PATH={initial_path}\n"


def test_await_pane_targets_operator_tab_with_bundled_vc_frame_without_path_leak(
    tmp_path: Path,
) -> None:
    staged_home = tmp_path / "home" / ".vibecrafted"
    runtime_home = tmp_path / "home" / ".local" / "share" / "vibecrafted"
    capture_file = tmp_path / "vc-frame-args.txt"
    artifacts_dir = staged_home / "artifacts" / "repo" / "2026_0611" / "reports"
    artifacts_dir.mkdir(parents=True)
    meta = artifacts_dir / "run.meta.json"
    transcript = artifacts_dir / "run.transcript.log"
    transcript.write_text("", encoding="utf-8")
    meta.write_text(
        json.dumps(
            {
                "run_id": "run-424242",
                "status": "running",
                "agent": "codex",
                "mode": "review",
                "transcript": str(transcript),
                "launcher_pid": os.getpid(),
            }
        ),
        encoding="utf-8",
    )
    helper_root = tmp_path / "frontier"
    helper = (
        helper_root / "config" / "agents" / "scripts" / "vibecrafted-await-watch.sh"
    )
    helper.parent.mkdir(parents=True)
    helper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    helper.chmod(0o755)
    (helper_root / "config" / "starship.toml").write_text("", encoding="utf-8")

    _write_capture_command(runtime_home / "bin", "vc-frame", capture_file)
    jq = runtime_home / "bin" / "jq"
    jq.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            if [[ "${1:-}" == "-r" ]]; then
              filter="${2:-}"
              file="${3:-}"
            else
              filter="${1:-}"
              file="${2:-}"
            fi
            python3 - "$filter" "$file" <<'PY'
            import json
            import sys

            key = sys.argv[1].split()[0].lstrip(".")
            with open(sys.argv[2], "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            value = payload.get(key, "")
            print("" if value is None else value)
            PY
            """
        ),
        encoding="utf-8",
    )
    jq.chmod(0o755)

    initial_path = os.defpath
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            '_vetcoders_maybe_spawn_await_pane codex review run-424242 "$PWD" 7 3; '
            "sleep 1.2; "
            'printf "PATH=%s\\n" "$PATH"'
        ),
        {
            "CAPTURE_FILE": str(capture_file),
            "PATH": initial_path,
            "VIBECRAFTED_HOME": str(staged_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_ROOT": str(helper_root),
            "VC_FRAME": "operator",
            # _vetcoders_in_vc_frame trusts only the VC_FRAME_* attached-context
            # pair; without it the spawn is skipped (legacy VC_FRAME alone is
            # not an attachment signal). CI has no frame, so set it explicitly.
            "VC_FRAME_PANE_ID": "1",
            "VC_FRAME_SESSION_NAME": "operator",
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert capture_file.exists()
    payload = capture_file.read_text(encoding="utf-8")
    assert "action\nnew-pane" in payload
    assert "--tab-id\n7" in payload
    assert "--floating" in payload
    assert "--name\nawait:codex:424242" in payload
    assert f"--meta\n{meta}" in payload
    assert "--run-id" not in payload
    assert "action\nfocus-pane-id\n3" in payload
    assert result.stdout == f"PATH={initial_path}\n"


def test_compact_session_name_is_zsh_compatible() -> None:
    if shutil.which("zsh") is None:
        return

    result = subprocess.run(
        [
            "zsh",
            "-fc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_compact_session_name "
                '"lbrx-services-owne-135739-94539" "owne-135739-94539"'
            ),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "VIBECRAFTED_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip().endswith("owne-135739-94539")
    assert "unrecognized modifier" not in result.stderr


def test_recovery_session_name_fits_macos_default_socket_budget() -> None:
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_recovery_vc_frame_session_name "workspace-9082c14d"'
            ),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "VIBECRAFTED_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    name = result.stdout.strip()
    assert result.returncode == 0, result.stderr
    assert len(name.encode("utf-8")) <= 24
    assert re.search(r"-r[0-9]{6}-[0-9]+$", name) is None


def test_recovery_session_name_keeps_the_place_label() -> None:
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_recovery_vc_frame_session_name "vibecrafted"'
            ),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "VIBECRAFTED_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    name = result.stdout.strip()
    assert result.returncode == 0, result.stderr
    assert name.startswith("vibecrafted")
    assert name != "vibecrafted"
    assert len(name.encode("utf-8")) <= 24


def test_recovery_session_name_does_not_keep_hashed_incarnation() -> None:
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_recovery_vc_frame_session_name "3m-4ad4-r034605-2072"'
            ),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "VIBECRAFTED_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )

    name = result.stdout.strip()
    assert result.returncode == 0, result.stderr
    assert "4ad4" not in name
    assert "-r034605-" not in name
    assert len(name.encode("utf-8")) <= 24


def test_vc_marbles_wrapper_routes_control_subcommands(tmp_path: Path) -> None:
    capture_file = tmp_path / "inspect-args.txt"
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            'marbles-inspect() { printf "%s\\n" "$@" > "$CAPTURE_FILE"; }; '
            "vc-marbles inspect marb-205740-3318"
        ),
        {"VIBECRAFTED_ROOT": str(REPO_ROOT), "CAPTURE_FILE": str(capture_file)},
    )

    assert result.returncode == 0
    assert capture_file.read_text(encoding="utf-8").splitlines() == ["marb-205740-3318"]


def test_vc_skill_wrapper_help_after_agent_does_not_launch_worker() -> None:
    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            "_vetcoders_skill_entry() { printf 'launched\\n'; return 99; }; "
            "vc-audit codex --help"
        ),
        {"VIBECRAFTED_ROOT": str(REPO_ROOT)},
    )

    assert result.returncode == 0
    assert "Usage: vc-audit <claude|codex|agy|junie|grok|cursor>" in result.stderr
    assert "launched" not in result.stdout


def test_skill_dispatch_prints_launch_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    control_dir = home / ".vibecrafted" / "control_plane" / "runs"
    report = tmp_path / "report.md"
    transcript = tmp_path / "trace.log"
    launcher = tmp_path / "launcher.sh"

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            "_vetcoders_generate_run_id() { printf 'prun-010203-44444\\n'; }; "
            "_vetcoders_default_runtime() { printf 'headless\\n'; }; "
            "_vetcoders_dispatch_skill_prompt() { "
            '  mkdir -p "$CONTROL_DIR"; '
            '  cat > "$CONTROL_DIR/$5.json" <<JSON\n'
            "{"
            f'"state":"launching",'
            f'"latest_report":"{report}",'
            f'"latest_transcript":"{transcript}",'
            f'"launcher":"{launcher}"'
            "}\n"
            "JSON\n"
            "  printf 'stub dispatch\\n'; "
            "}; "
            "vc-prune claude --prompt 'triage gems'"
        ),
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "CONTROL_DIR": str(control_dir),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "stub dispatch" in result.stdout
    assert "VIBECRAFTED LAUNCH RECEIPT" in result.stdout
    assert "run_id:     prun-010203-44444" in result.stdout
    assert "report:     " + str(report) in result.stdout
    assert "transcript: " + str(transcript) in result.stdout
    assert (
        "observe:    vibecrafted observe claude --run-id prun-010203-44444"
        in result.stdout
    )
    assert (
        "await (ARM NOW, supervisor-side): vibecrafted await claude --run-id prun-010203-44444"
        in result.stdout
    )


def test_vc_polarize_task_injects_prism_payload(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    args_file = tmp_path / "loct-args.txt"
    capture_file = tmp_path / "prompt.md"
    _write_fake_loct(fake_bin, 11, args_file)

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            "_vetcoders_default_runtime() { printf 'headless\\n'; }; "
            '_vetcoders_prompt_text() { printf \'%s\' "$3" > "$CAPTURE_FILE"; }; '
            "vc-polarize codex --task 'marbles versus polarize skills: polarize them' --no-context-corpus"
        ),
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "LOCT_ARGS_FILE": str(args_file),
            "CAPTURE_FILE": str(capture_file),
        },
    )

    assert result.returncode == 0, result.stderr
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert args[:4] == ["prism", "--project", str(REPO_ROOT), "--with-aicx"]
    assert "marbles versus polarize skills: polarize them" in args
    assert "marbles versus polarize skills: polarize them code truth" in args
    assert "marbles versus polarize skills: polarize them product truth" in args
    assert "--json" in args

    prompt = capture_file.read_text(encoding="utf-8")
    assert "Perform the vc-polarize skill on this repository." in prompt
    assert "Polarize task: marbles versus polarize skills: polarize them" in prompt
    assert "Band: pass (score 11/15)" in prompt
    assert "Runner action: pass" in prompt
    assert "Prism preflight command: loct prism" in prompt
    assert "--with-aicx" in prompt
    assert '"schema_version":"loctree.prism.v1"' in prompt
    assert '"total_score":11' in prompt


def test_polarize_band_abort_low_score(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 3)
    capture_file = tmp_path / "prompt.md"

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            '_vetcoders_prompt_text() { printf launched > "$CAPTURE_FILE"; }; '
            "vc-polarize codex --task 'too local'"
        ),
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
        },
    )

    assert result.returncode != 0
    assert "below threshold" in result.stderr
    assert "prism.json" in result.stderr
    assert not capture_file.exists()


def test_polarize_band_memo_mid(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 7)

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            "_vetcoders_prompt_text() { printf 'should-not-launch'; return 99; }; "
            "vc-polarize codex --task 'memo tier concept'"
        ),
        {
            "HOME": str(tmp_path / "home"),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "band 5..8" in result.stdout
    assert "No agent dispatched" in result.stdout
    assert "should-not-launch" not in result.stdout


def test_polarize_band_pass_high(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 11)
    capture_file = tmp_path / "prompt.md"

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            "_vetcoders_default_runtime() { printf 'headless\\n'; }; "
            '_vetcoders_prompt_text() { printf \'%s\' "$3" > "$CAPTURE_FILE"; printf "session: b63af6c1-dd0e-4d2c-ad31-a52df443f4ad\\n"; }; '
            "vc-polarize codex --task 'pass tier concept' --no-context-corpus"
        ),
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
        },
    )

    assert result.returncode == 0, result.stderr
    prompt = capture_file.read_text(encoding="utf-8")
    assert "Band: pass (score 11/15)" in prompt


def test_polarize_band_doctrine_max(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 14)
    capture_file = tmp_path / "prompt.md"

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            "_vetcoders_default_runtime() { printf 'headless\\n'; }; "
            '_vetcoders_prompt_text() { printf \'%s\' "$3" > "$CAPTURE_FILE"; printf "session: b63af6c1-dd0e-4d2c-ad31-a52df443f4ad\\n"; }; '
            "vc-polarize codex --task 'doctrine tier concept' --no-context-corpus"
        ),
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "CAPTURE_FILE": str(capture_file),
        },
    )

    assert result.returncode == 0, result.stderr
    prompt = capture_file.read_text(encoding="utf-8")
    assert "Band: doctrine (score 14/15)" in prompt


def test_polarize_band_abort_emits_prism_path(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 3)

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        f'export PATH="{fake_bin}:$PATH"; vc-polarize codex --task "reject me"',
        {
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )

    assert result.returncode != 0
    prism_paths = list((tmp_path / "home" / ".vibecrafted").rglob("prism.json"))
    assert prism_paths
    assert str(prism_paths[0]) in result.stderr


def test_polarize_emit_context_pack_pass_band(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_aicx = fake_bin / "aicx"
    fake_aicx.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            out=""
            while [[ $# -gt 0 ]]; do
              if [[ "$1" == "--output" ]]; then
                shift
                out="$1"
              fi
              shift || true
            done
            mkdir -p "$(dirname "$out")"
            printf '# extracted context\n' > "$out"
            """
        ),
        encoding="utf-8",
    )
    fake_aicx.chmod(0o755)
    prism_json = tmp_path / "prism.json"
    prism_json.write_text(
        json.dumps(
            {
                "schema_version": "loctree.prism.v1",
                "total_score": 11,
                "task_framings": [{"task": "installer public contract"}],
            }
        ),
        encoding="utf-8",
    )

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'export PATH="{fake_bin}:$PATH"; '
            "_vetcoders_polarize_emit_context_pack "
            f'codex b63af6c1-dd0e-4d2c-ad31-a52df443f4ad "{prism_json}" run-pack "{REPO_ROOT}" pass "installer public contract"'
        ),
        {
            "HOME": str(tmp_path / "home"),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )

    assert result.returncode == 0, result.stderr
    corpus_root = tmp_path / "home" / ".aicx" / "context-corpus"
    index_files = list(corpus_root.rglob("index.jsonl"))
    assert len(index_files) == 1
    index_entry = json.loads(index_files[0].read_text(encoding="utf-8").strip())
    assert index_entry["artifact_family"] == "loct-context-pack"
    assert index_entry["schema_version"] == "context_corpus.v1"
    assert index_entry["truth_status.role"] == "example"
    sidecar_files = list(corpus_root.rglob("sidecars/run-pack_pass.json"))
    assert len(sidecar_files) == 1
    sidecar = json.loads(sidecar_files[0].read_text(encoding="utf-8"))
    assert sidecar["truth_status"]["role"] == "example"
    assert sidecar["truth_status"]["runtime_authoritative"] is False
    assert sidecar["truth_status"]["current_head_when_ingested"]
    assert sidecar["learning_use"]["allowed"] == [
        "format_examples",
        "section_order",
        "keyword_index",
    ]
    assert sidecar["learning_use"]["forbidden"] == [
        "current_code_truth",
        "implementation_claims",
        "gate_status",
    ]
    assert sidecar["band"] == "pass"
    assert sidecar["total_score"] == 11
    assert "installer" in sidecar["keywords"]


def test_polarize_emit_context_pack_abort_no_write(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    _write_fake_loct(fake_bin, 3)

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        f'export PATH="{fake_bin}:$PATH"; vc-polarize codex --task "abort corpus"',
        {
            "HOME": str(tmp_path / "home"),
            "VIBECRAFTED_ROOT": str(REPO_ROOT),
            "VIBECRAFTED_HOME": str(tmp_path / "home" / ".vibecrafted"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        },
    )

    assert result.returncode != 0
    assert not (tmp_path / "home" / ".aicx" / "context-corpus").exists()


def test_runtime_core_preserves_origin_org_repo_resolution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", str(repo)], check=True, capture_output=True, text=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            "https://github.com/vetcoders/vibecrafted.git",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        f'_vetcoders_org_repo "{repo}"',
        {"VIBECRAFTED_ROOT": str(REPO_ROOT)},
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "vetcoders/vibecrafted"


def test_research_summary_does_not_execute_await_command(tmp_path: Path) -> None:
    run_dir = tmp_path / "research" / "rsch-test"
    run_dir.mkdir(parents=True)
    prompt_file = run_dir / "plans" / "plan.md"
    prompt_file.parent.mkdir()
    prompt_file.write_text("research plan\n", encoding="utf-8")

    result = _run_vetcoders_helper(
        HELPER_SCRIPT,
        (
            f'_vetcoders_write_research_summary "{run_dir}" "rsch-test" '
            f'"{tmp_path}" "{prompt_file}" claude.sh codex.sh gemini.sh'
        ),
        {"VIBECRAFTED_ROOT": str(REPO_ROOT)},
    )

    assert result.returncode == 0
    summary_file = run_dir / "summary.md"
    assert result.stdout.strip() == str(summary_file)
    assert "Await: vc-research-await --run-id rsch-test" in summary_file.read_text(
        encoding="utf-8"
    )
    assert "No matching launchers or metadata found yet" not in result.stderr
