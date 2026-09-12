from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import textwrap
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


def _write_fake_marbles_spawn(script_path: Path) -> None:
    script_path.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"'
        + "\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_replaying_vc_frame(script_path: Path) -> None:
    payload = (
        '#!/usr/bin/env python3\nimport os\nimport shutil\nimport subprocess\nimport sys\nfrom pathlib import Path\n\nargs = sys.argv[1:]\nPath(os.environ["VC_FRAME_CAPTURE_FILE"]).write_text("\\n".join(args) + "\\n", encoding="utf-8")\nif args:\n    cmd_script = Path(args[-1])\n    expected_spawn = os.environ.get("EXPECTED_MARBLES_SPAWN", "")\n    if expected_spawn and cmd_script.is_file():\n        payload = cmd_script.read_text(encoding="utf-8", errors="ignore")\n        if expected_spawn not in payload:\n            print(f"unsafe vc_frame replay target: {cmd_script}", file=sys.stderr)\n            sys.exit(97)\n    shell = shutil.which(\'zsh\') or shutil.which(\'bash\') or \'/bin/sh\'\n    subprocess.run([shell, \'-lc\', str(cmd_script)], check=True, env=os.environ.copy())'
        + "\n"
    )
    script_path.write_text(payload, encoding="utf-8")
    script_path.chmod(0o755)
    if script_path.name == "vc-frame":
        vc_frame = script_path.with_name("vc-frame")
        vc_frame.write_text(payload, encoding="utf-8")
        vc_frame.chmod(0o755)


def _prepare_fake_marbles_bundle(tmp_path: Path) -> tuple[Path, Path]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copytree(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/scripts/lib",
        scripts_dir / "lib",
    )

    for name in (
        "common.sh",
        "marbles_spawn.sh",
        "marbles_watcher.sh",
        "marbles_verify_watch.sh",
        "marbles_next.sh",
    ):
        source = (
            REPO_ROOT
            / "vibecrafted-core"
            / "vibecrafted_core"
            / "runtime"
            / "scripts"
            / name
        )
        target = scripts_dir / name
        shutil.copy2(source, target)
        target.chmod(0o755)

    capture_file = tmp_path / "spawn-events.jsonl"
    fake_spawn = textwrap.dedent(
        r"""
        #!/usr/bin/env bash
        set -euo pipefail

        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        # shellcheck source=common.sh
        source "$SCRIPT_DIR/common.sh"

        agent="$(basename "${BASH_SOURCE[0]}" _spawn.sh)"
        mode="implement"
        runtime="terminal"
        model=""
        root=""
        success_hook=""
        failure_hook=""
        plan_file=""

        while [[ $# -gt 0 ]]; do
          case "$1" in
            --mode) shift; mode="$1" ;;
            --runtime) shift; runtime="$1" ;;
            --root) shift; root="$1" ;;
            --model) shift; model="$1" ;;
            --success-hook) shift; success_hook="$1" ;;
            --failure-hook) shift; failure_hook="$1" ;;
            *)
              [[ -z "$plan_file" ]] || { echo "unexpected arg: $1" >&2; exit 1; }
              plan_file="$1"
              ;;
          esac
          shift
        done

        spawn_prepare_paths "$agent" "$plan_file" "$root" "$mode"
        if [[ -n "${MARBLES_TEST_FAIL_BEFORE_META_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" == "${MARBLES_TEST_FAIL_BEFORE_META_LOOP}" ]]; then
          printf 'synthetic pre-meta failure for loop %s\n' "${SPAWN_LOOP_NR:-0}" >&2
          exit 42
        fi
        spawn_write_meta "$SPAWN_META" "launching" "$agent" "$mode" "$SPAWN_ROOT" "$SPAWN_PLAN" "$SPAWN_REPORT" "$SPAWN_TRANSCRIPT" "$0" "$model"

        python3 - "$MARBLES_SPAWN_CAPTURE" "$SPAWN_PLAN" "$SPAWN_REPORT" "$success_hook" "$failure_hook" "$agent" "$model" "$SPAWN_RUN_ID" "$SPAWN_LOOP_NR" <<'PY'
        import json
        import os
        import pathlib
        import sys

        capture, plan, report, success_hook, failure_hook, agent, model, run_id, loop_nr = sys.argv[1:10]
        payload = {
            "plan": plan,
            "report": report,
            "success_hook": success_hook,
            "failure_hook": failure_hook,
            "agent": agent,
            "model": model,
            "run_id": run_id,
            "loop": int(loop_nr or "0"),
            "suppress_report_hint": os.environ.get("VIBECRAFTED_SUPPRESS_REPORT_HINT", ""),
        }
        capture_path = pathlib.Path(capture)
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        with capture_path.open("a", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
        PY

        cat > "$SPAWN_TRANSCRIPT" <<EOF
        ---
        run_id: ${SPAWN_RUN_ID}
        prompt_id: ${SPAWN_PROMPT_ID}
        agent: ${SPAWN_AGENT}
        skill: marb
        model: ${model:-unknown}
        status: transcript
        ---

        session: 019dad3e-8f9c-000${SPAWN_LOOP_NR}
        working...
        EOF

        p0=1
        if [[ -n "${MARBLES_TEST_ZERO_METRICS_FROM_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" -ge "${MARBLES_TEST_ZERO_METRICS_FROM_LOOP}" ]]; then
          p0=0
        fi

        report_status="completed"
        meta_status="completed"
        final_exit_code=0
        if [[ -n "${MARBLES_TEST_FAIL_WITH_REPORT_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" == "${MARBLES_TEST_FAIL_WITH_REPORT_LOOP}" ]]; then
          report_status="failed"
          meta_status="failed"
          final_exit_code=17
        fi
        if [[ -n "${MARBLES_TEST_REPORT_FAILED_META_COMPLETED_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" == "${MARBLES_TEST_REPORT_FAILED_META_COMPLETED_LOOP}" ]]; then
          report_status="failed"
          meta_status="completed"
          final_exit_code=0
        fi

        cat > "$SPAWN_REPORT" <<EOF
        ---
        run_id: ${SPAWN_RUN_ID}
        prompt_id: ${SPAWN_PROMPT_ID}
        agent: ${SPAWN_AGENT}
        skill: marb
        model: ${model:-unknown}
        status: ${report_status}
        ---

        P0: ${p0}
        P1: 0
        P2: 0
        Commit: abc1234
        EOF

        if [[ "$report_status" == "completed" && "${MARBLES_TEST_SKIP_VERIFIED_REPORT_LOOP:-}" != "${SPAWN_LOOP_NR:-0}" ]]; then
          cp "$SPAWN_REPORT" "${SPAWN_REPORT%.md}_verified.md"
        fi

        if [[ -n "${MARBLES_TEST_ANCESTOR_SEQUENCE:-}" ]]; then
          base_run_id="${SPAWN_RUN_ID%-???}"
          ancestor_path="$(spawn_marbles_state_dir "$base_run_id")/ancestor.md"
          IFS=';' read -r -a ancestor_steps <<< "$MARBLES_TEST_ANCESTOR_SEQUENCE"
          step_index=$((SPAWN_LOOP_NR - 1))
          step="${ancestor_steps[$step_index]:-}"
          if [[ -n "$step" ]]; then
            IFS='|' read -r next_agent next_focus next_model <<< "$step"
            {
              printf -- '---\n'
              printf 'agent: %s\n' "$next_agent"
              printf 'focus: %s\n' "${next_focus:-initial prompt}"
              printf 'priority: P0\n'
              if [[ -n "${next_model:-}" ]]; then
                printf 'model: %s\n' "$next_model"
              fi
              printf -- '---\n\n'
              printf 'Steer the next loop toward %s.\n' "${next_focus:-initial prompt}"
            } > "$ancestor_path"
          fi
        elif [[ "${MARBLES_TEST_EDIT_ANCESTOR:-}" == "1" && "${SPAWN_LOOP_NR:-0}" == "1" ]]; then
          base_run_id="${SPAWN_RUN_ID%-???}"
          ancestor_path="$(spawn_marbles_state_dir "$base_run_id")/ancestor.md"
          cat > "$ancestor_path" <<'EOF_ANCESTOR'
        ---
        agent: agy
        focus: accessibility
        priority: P0
        ---

        Steer the next loop toward accessibility.
        EOF_ANCESTOR
        fi

        if [[ -n "${MARBLES_TEST_DELAY_META_AFTER_REPORT_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" == "${MARBLES_TEST_DELAY_META_AFTER_REPORT_LOOP}" ]]; then
          sleep "${MARBLES_TEST_DELAY_META_AFTER_REPORT_S:-3}"
        fi

        spawn_finish_meta "$SPAWN_META" "$meta_status" "$final_exit_code"

        if [[ "$meta_status" == "failed" ]]; then
          if [[ -n "$failure_hook" ]]; then
            bash -lc "$failure_hook"
          fi
          exit "$final_exit_code"
        fi

        if [[ -n "${MARBLES_TEST_SKIP_SUCCESS_HOOK_LOOP:-}" && "${SPAWN_LOOP_NR:-0}" == "${MARBLES_TEST_SKIP_SUCCESS_HOOK_LOOP}" ]]; then
          exit 0
        fi

        if [[ -n "$success_hook" ]]; then
          bash -lc "$success_hook"
        fi
        """
    ).lstrip()

    for agent in ("claude", "codex", "agy"):
        script = scripts_dir / f"{agent}_spawn.sh"
        script.write_text(fake_spawn, encoding="utf-8")
        script.chmod(0o755)

    return scripts_dir, capture_file


def _delay_next_plan_write(script_path: Path) -> None:
    marker = 'next_plan="$(_loop_child_plan "$next")"'
    replacement = 'sleep "${MARBLES_TEST_DELAY_NEXT_PLAN:-0}"\n' + marker
    script = script_path.read_text(encoding="utf-8")
    assert marker in script
    script_path.write_text(script.replace(marker, replacement, 1), encoding="utf-8")


def _load_spawn_events(capture_file: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in capture_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _expected_operator_session(run_id: str | None = None) -> str:
    base = (
        re.sub(r"[^a-z0-9]+", "-", REPO_ROOT.name.lower()).strip("-") or "vibecrafted"
    )
    return f"{base}-{run_id}" if run_id else base


def _marbles_state_dirs(crafted_home: Path) -> list[Path]:
    marbles_dir = crafted_home / "marbles"
    live = [
        path
        for path in marbles_dir.iterdir()
        if path.is_dir() and path.name != "_archived"
    ]
    archived = list((marbles_dir / "_archived").glob("*/*"))
    return live + archived


def _org_repo() -> str:
    remote = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "remote", "get-url", "origin"],
        text=True,
    ).strip()
    match = re.search(r"[:/]([^/]+)/([^/.]+?)(?:\.git)?$", remote)
    assert match is not None
    return f"{match.group(1)}/{match.group(2)}"


def test_marbles_spawn_chains_with_agent_and_ancestor_plan_contract() -> None:
    script = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "marbles_spawn.sh"
    ).read_text(encoding="utf-8")

    assert (
        'success_hook="bash $q_scripts/marbles_next.sh $q_state $count 1 '
        '$marbles_run_id $q_root $q_runtime $q_scripts $q_lock $q_store"' in script
    )
    assert (
        'failure_hook="bash $q_scripts/marbles_next.sh --failed $q_state '
        '$count 1 $marbles_run_id $q_root $q_runtime $q_scripts $q_lock $q_store"'
        in script
    )


def _run_marbles_prompt(
    tmp_path: Path, *, inside_vc_frame: bool
) -> tuple[list[str], list[str], Path]:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    isolated_root = tmp_path / "isolated-root"
    tmpdir_root = tmp_path / "tmpdir"
    capture_file = tmp_path / "marbles-args.txt"
    vc_frame_capture_file = tmp_path / "vc_frame-args.txt"
    spawn_script = crafted_home / "runtime" / "scripts" / "marbles_spawn.sh"

    home.mkdir()
    fake_bin.mkdir()
    isolated_root.mkdir()
    tmpdir_root.mkdir()
    spawn_script.parent.mkdir(parents=True)
    _write_fake_marbles_spawn(spawn_script)
    _write_replaying_vc_frame(fake_bin / "vc-frame")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["VIBECRAFTED_ROOT"] = str(isolated_root)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME_CAPTURE_FILE"] = str(vc_frame_capture_file)
    env["EXPECTED_MARBLES_SPAWN"] = str(spawn_script)
    env["TMPDIR"] = f"{tmpdir_root}/"
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    env["VIBECRAFTED_RUN_ID"] = "marb-014520"
    operator_session = _expected_operator_session(env["VIBECRAFTED_RUN_ID"])

    if inside_vc_frame:
        env["VC_FRAME"] = "operator"
        env["VC_FRAME_PANE_ID"] = "terminal_7"
        env["VC_FRAME_SESSION_NAME"] = operator_session
    else:
        env["VIBECRAFTED_OPERATOR_SESSION"] = operator_session

    subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                'claude-marbles --count 1 --prompt "weź i vc-justdo wszystko co marbles znajdzie"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    return (
        capture_file.read_text(encoding="utf-8").splitlines(),
        vc_frame_capture_file.read_text(encoding="utf-8").splitlines(),
        crafted_home,
    )


def test_vc_marbles_preserves_prompt_as_single_argument_inside_vc_frame(
    tmp_path: Path,
) -> None:
    payload, vc_frame_payload, crafted_home = _run_marbles_prompt(
        tmp_path, inside_vc_frame=True
    )
    expected_tmp_root = (
        crafted_home
        / "artifacts"
        / _org_repo()
        / datetime.now().astimezone().strftime("%Y_%m%d")
        / "tmp"
    )

    assert "--agent" in payload
    assert "claude" in payload
    assert "--count" in payload
    assert "1" in payload
    assert "--prompt" in payload
    assert "weź i vc-justdo wszystko co marbles znajdzie" in payload
    assert "new-pane" in vc_frame_payload
    assert any("vibecrafted-marbles." in line for line in vc_frame_payload)
    expected_tmp_spellings = {
        str(expected_tmp_root),
        str(expected_tmp_root).removeprefix("/private"),
    }
    assert any(
        any(spelling in line for spelling in expected_tmp_spellings)
        for line in vc_frame_payload
    )
    assert not any("//vibecrafted-marbles." in line for line in vc_frame_payload)
    assert not any(
        "weź i vc-justdo wszystko co marbles znajdzie" in line
        for line in vc_frame_payload
    )


def test_vc_marbles_inside_vc_frame_prints_launch_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    isolated_root = tmp_path / "isolated-root"
    tmpdir_root = tmp_path / "tmpdir"
    capture_file = tmp_path / "marbles-args.txt"
    vc_frame_capture_file = tmp_path / "vc_frame-args.txt"
    spawn_script = crafted_home / "runtime" / "scripts" / "marbles_spawn.sh"

    home.mkdir()
    fake_bin.mkdir()
    isolated_root.mkdir()
    tmpdir_root.mkdir()
    spawn_script.parent.mkdir(parents=True)
    _write_fake_marbles_spawn(spawn_script)
    _write_replaying_vc_frame(fake_bin / "vc-frame")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["VIBECRAFTED_ROOT"] = str(isolated_root)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME_CAPTURE_FILE"] = str(vc_frame_capture_file)
    env["EXPECTED_MARBLES_SPAWN"] = str(spawn_script)
    env["TMPDIR"] = f"{tmpdir_root}/"
    env["VC_FRAME"] = "operator"
    env["VC_FRAME_PANE_ID"] = "terminal_7"
    env["VC_FRAME_SESSION_NAME"] = "ambient-session"
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; codex-marbles --count 1 --depth 3',
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert "Marbles run launched in vc_frame tab: marbles-marb-" in result.stdout
    assert "run_id:  marb-" in result.stdout
    assert "inspect: vc-marbles inspect marb-" in result.stdout


def test_vc_marbles_defaults_to_headless_and_uses_no_watch(tmp_path: Path) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    fake_bin = tmp_path / "bin"
    isolated_root = tmp_path / "isolated-root"
    capture_file = tmp_path / "marbles-args.txt"
    vc_frame_capture = tmp_path / "vc_frame-args.txt"
    spawn_script = crafted_home / "runtime" / "scripts" / "marbles_spawn.sh"

    home.mkdir()
    fake_bin.mkdir()
    isolated_root.mkdir()
    spawn_script.parent.mkdir(parents=True)
    _write_fake_marbles_spawn(spawn_script)
    (fake_bin / "vc-frame").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nprintf \'%s\\n\' "$@" > "$VC_FRAME_CAPTURE_FILE"\n',
        encoding="utf-8",
    )
    (fake_bin / "vc-frame").chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["VIBECRAFTED_ROOT"] = str(isolated_root)
    env["CAPTURE_FILE"] = str(capture_file)
    env["VC_FRAME_CAPTURE_FILE"] = str(vc_frame_capture)
    env.pop("VETCODERS_SPAWN_RUNTIME", None)
    env["VC_FRAME"] = "operator"
    env["VC_FRAME_PANE_ID"] = "terminal_7"
    env["VC_FRAME_SESSION_NAME"] = "ambient-session"
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; codex-marbles --count 1 --prompt "telemetry smoke"',
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = capture_file.read_text(encoding="utf-8").splitlines()
    assert "--runtime" in payload
    assert "headless" in payload
    assert "--no-watch" in payload
    assert payload.index("--no-watch") < payload.index("--prompt")
    assert not vc_frame_capture.exists()
    assert re.search(
        r"Agent launched\. Report will land at: .*/marbles/reports/\d{8}_\d{4}_marbles-ancestor_L1_codex\.md",
        result.stdout,
    )


def test_vc_marbles_preserves_prompt_as_single_argument_in_operator_session(
    tmp_path: Path,
) -> None:
    payload, vc_frame_payload, crafted_home = _run_marbles_prompt(
        tmp_path, inside_vc_frame=False
    )
    expected_tmp_root = (
        crafted_home
        / "artifacts"
        / _org_repo()
        / datetime.now().astimezone().strftime("%Y_%m%d")
        / "tmp"
    )

    assert "--agent" in payload
    assert "claude" in payload
    assert "--count" in payload
    assert "1" in payload
    assert "--prompt" in payload
    assert "weź i vc-justdo wszystko co marbles znajdzie" in payload
    assert "new-tab" in vc_frame_payload
    assert any("vc-spawn-cmd." in line for line in vc_frame_payload)
    expected_tmp_spellings = {
        str(expected_tmp_root),
        str(expected_tmp_root).removeprefix("/private"),
    }
    assert any(
        any(spelling in line for spelling in expected_tmp_spellings)
        for line in vc_frame_payload
    )
    assert not any("//vc-spawn-cmd." in line for line in vc_frame_payload)
    assert not any(
        "weź i vc-justdo wszystko co marbles znajdzie" in line
        for line in vc_frame_payload
    )


def test_vetcoders_shell_quote_join_stays_utf8_clean_for_multiline_prompt(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["PROMPT_PAYLOAD"] = (
        "Siemka! weź i zażółć gęślą jaźń.\n"
        "_Run inside vc_frame - if there is any open window attach to it._\n"
        "Don't drop UTF-8 on the floor."
    )

    quoted = subprocess.run(
        [
            "bash",
            "-lc",
            f'source "{HELPER_SCRIPT}"; _vetcoders_shell_quote_join "$PROMPT_PAYLOAD"',
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
    ).stdout.decode("utf-8")

    roundtrip_script = tmp_path / "roundtrip.sh"
    roundtrip_script.write_text(
        f"#!/usr/bin/env bash\nprintf %s {quoted}\n",
        encoding="utf-8",
    )
    roundtrip_script.chmod(0o755)

    roundtrip = subprocess.run(
        ["bash", str(roundtrip_script)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
    ).stdout.decode("utf-8")

    assert roundtrip == env["PROMPT_PAYLOAD"]


def test_parse_contract_treats_everything_after_prompt_as_prompt_block() -> None:
    env = os.environ.copy()
    env["PROMPT_HEAD"] = "Portable musi działać."

    payload = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_parse_contract --count 8 --prompt "$PROMPT_HEAD" '
                "--runtime headless --depth 99; "
                'printf "COUNT=%s\\n" "$_vetcoders_contract_count"; '
                'printf "RUNTIME=%s\\n" "$_vetcoders_contract_runtime"; '
                'printf "DEPTH=%s\\n" "$_vetcoders_contract_depth"; '
                'printf "PROMPT=%s\\n" "$_vetcoders_contract_prompt"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    ).stdout

    lines = dict(line.split("=", 1) for line in payload.strip().splitlines())
    assert lines["COUNT"] == "8"
    assert lines["RUNTIME"] == ""
    assert lines["DEPTH"] == ""
    assert lines["PROMPT"] == "Portable musi działać. --runtime headless --depth 99"


def test_parse_contract_fails_closed_on_unknown_flag() -> None:
    # A leaked `--fork-session` once became the literal operator prompt of a
    # fresh dispatched worker; unknown flags must abort, never become job text.
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_parse_contract --bogus-flag some prompt"
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "Unknown flag: --bogus-flag" in result.stderr


def test_skill_contract_accepts_model_and_dispatches_it_to_provider_spawn(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    brief = tmp_path / "audit.md"
    brief.write_text("Audit this completed plan.\n", encoding="utf-8")
    capture = tmp_path / "dispatch-argv"
    lock = tmp_path / "run.lock"
    lock.write_text("owned\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "MODEL_ROOT": str(root),
            "MODEL_BRIEF": str(brief),
            "MODEL_CAPTURE": str(capture),
            "MODEL_LOCK": str(lock),
        }
    )

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_effective_run_id() { printf "audt-model-test\\n"; }; '
                '_vetcoders_effective_run_lock() { printf "%s\\n" "$MODEL_LOCK"; }; '
                "_vetcoders_prepare_operator_runtime() { return 0; }; "
                "_vetcoders_vc_frame_bin() { return 1; }; "
                "_vetcoders_print_launch_receipt() { return 0; }; "
                "_vetcoders_maybe_spawn_await_pane() { return 0; }; "
                '_vetcoders_dispatch_skill_prompt() { printf "%s\\n" "$@" > "$MODEL_CAPTURE"; }; '
                "_vetcoders_skill claude audit --model claude-opus-5 "
                '--file "$MODEL_BRIEF" --root "$MODEL_ROOT"'
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    dispatched = capture.read_text(encoding="utf-8").splitlines()
    assert dispatched[-4:] == ["--root", str(root), "--model", "claude-opus-5"]


def test_parse_contract_double_dash_still_passes_literal_dash_text() -> None:
    payload = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_parse_contract -- --literal-text; "
                'printf "TAIL=%s\\n" "$_vetcoders_contract_tail"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    assert "TAIL=--literal-text" in payload


def test_parse_contract_accepts_run_id_and_last_flags() -> None:
    payload = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_parse_contract --run-id work-260816-213657-08420 --last; "
                'printf "RUN=%s\\n" "$_vetcoders_contract_run_id"; '
                'printf "LAST=%s\\n" "$_vetcoders_contract_last"; '
                'printf "PROMPT=%s\\n" "$_vetcoders_contract_prompt"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    lines = dict(line.split("=", 1) for line in payload.strip().splitlines())
    assert lines["RUN"] == "work-260816-213657-08420"
    assert lines["LAST"] == "1"
    assert lines["PROMPT"] == ""


def test_parse_contract_accepts_fork_session_flag() -> None:
    payload = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_parse_contract --fork-session; "
                'printf "FORK=%s\\n" "$_vetcoders_contract_fork_session"; '
                'printf "PROMPT=%s\\n" "$_vetcoders_contract_prompt"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    lines = dict(line.split("=", 1) for line in payload.strip().splitlines())
    assert lines["FORK"] == "1"
    assert lines["PROMPT"] == ""


def test_resume_command_composes_claude_fork_session() -> None:
    payload = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                '_vetcoders_resume_command claude abc-123 "" headless 1; '
                '_vetcoders_resume_command claude abc-123 "go on" headless 1; '
                '_vetcoders_resume_command claude abc-123 "" interactive ""'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert payload[0] == (
        "claude --print --dangerously-skip-permissions --resume abc-123 --fork-session"
    )
    assert payload[1] == (
        "claude --print --dangerously-skip-permissions --resume abc-123 "
        "--fork-session 'go on'"
    )
    assert payload[2] == "claude --resume abc-123"


def test_resume_agent_rejects_fork_session_for_non_claude() -> None:
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_resume_agent codex --fork-session --session abc-123 "
                '--prompt "go"'
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "--fork-session is only supported for claude resume" in result.stderr


def test_resume_agent_fork_session_fails_closed_on_tracked_core_path() -> None:
    # Tracked core resume (no vc-frame worker host) has no fork contract yet;
    # dropping the flag would silently write into the session being preserved.
    env = os.environ.copy()
    env["VIBECRAFTED_RUNTIME"] = "headless"
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)
    env.pop("VIBECRAFTED_WORKER_SESSION", None)
    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                "_vetcoders_resume_agent claude --fork-session --session abc-123 "
                '--prompt "go"'
            ),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "not supported on the tracked core resume path" in result.stderr


def test_write_command_script_falls_back_to_bash_when_zsh_missing(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "bash").symlink_to(Path("/bin/bash"))
    (fake_bin / "chmod").symlink_to(Path("/bin/chmod"))
    # "zsh missing" must hold on hosts where zsh lives in /usr/bin (Linux CI
    # installs it there) — mirror the system dirs minus zsh instead of
    # trusting a raw /usr/bin on PATH.
    safe_bin = tmp_path / "safe-bin"
    safe_bin.mkdir()
    for src_dir in ("/usr/bin", "/bin"):
        for tool in Path(src_dir).iterdir():
            if tool.name == "zsh":
                continue
            target = safe_bin / tool.name
            if not target.exists():
                try:
                    target.symlink_to(tool)
                except OSError:
                    pass

    command_script = tmp_path / "spawn-cmd"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{safe_bin}"

    subprocess.run(
        [
            "/bin/bash",
            "-c",
            (
                f'source "{HELPER_SCRIPT}"; '
                f'_vetcoders_write_command_script "{command_script}" "printf %s fallback-ok"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )

    script_body = command_script.read_text(encoding="utf-8")
    assert str(fake_bin / "bash") in script_body
    assert "zsh" not in script_body
    assert "trap 'rm -f \"$0\"'" not in script_body

    result = subprocess.run(
        [str(command_script)],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "fallback-ok"
    assert command_script.exists()


def test_marbles_runtime_steers_next_loop_from_ancestor_frontmatter(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_EDIT_ANCESTOR"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Fix installer drift end to end",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_dir = state_dirs[0]
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    assert state["god_plan"] == str(state_dir / "god.md")
    assert state["ancestor_plan"] == str(state_dir / "ancestor.md")
    assert state["plan"] == str(state_dir / "ancestor.md")
    assert [loop["agent"] for loop in state["loops"][:2]] == ["codex", "agy"]
    assert state["loops"][1]["focus"] == "accessibility"

    god_plan = state_dir / "god.md"
    ancestor_plan = state_dir / "ancestor.md"
    assert god_plan.read_text(encoding="utf-8").startswith("---\nkind: god\n")
    assert oct(god_plan.stat().st_mode & 0o777) == "0o444"
    assert ancestor_plan.read_text(encoding="utf-8").startswith("---\nagent: agy\n")

    events = _load_spawn_events(capture_file)
    assert [event["agent"] for event in events] == ["codex", "agy"]
    assert state["archived_from"] in str(events[0]["success_hook"])
    assert str(events[0]["plan"]).endswith("marbles-ancestor_L1.md")
    assert str(events[1]["plan"]).endswith("marbles-ancestor_L2.md")
    assert str(events[0]["plan"]) not in str(events[0]["success_hook"])


def test_marbles_runtime_keeps_ancestor_focus_when_next_plan_write_lags(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    _delay_next_plan_write(scripts_dir / "marbles_next.sh")
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_EDIT_ANCESTOR"] = "1"
    env["MARBLES_TEST_DELAY_NEXT_PLAN"] = "2"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Keep the steering audit trail intact.",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state = json.loads((state_dirs[0] / "state.json").read_text(encoding="utf-8"))

    assert [loop["agent"] for loop in state["loops"][:2]] == ["codex", "agy"]
    assert [loop["agent_source"] for loop in state["loops"][:2]] == [
        "rotation",
        "user",
    ]
    assert state["loops"][1]["focus"] == "accessibility"


def test_marbles_runtime_applies_rotation_schedule_without_ancestor_override(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "3",
            "--rotation",
            "trio",
            "--runtime",
            "headless",
            "--prompt",
            "Fix installer drift end to end",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_dir = state_dirs[0]
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    assert state["god_plan"] == str(state_dir / "god.md")
    assert state["ancestor_plan"] == str(state_dir / "ancestor.md")
    assert state["plan"] == str(state_dir / "ancestor.md")
    assert state["rotation"] == "trio"
    assert state["rotation_pool"] == ["codex", "claude", "agy"]
    assert [loop["agent"] for loop in state["loops"][:3]] == [
        "codex",
        "claude",
        "agy",
    ]
    assert [loop["focus"] for loop in state["loops"][:3]] == [
        "initial prompt",
        "initial prompt",
        "initial prompt",
    ]
    assert (
        (state_dir / "ancestor.md")
        .read_text(encoding="utf-8")
        .startswith(
            "---\nagent: codex\nskill_name: marbles\n"
            "focus: initial prompt\npriority: P0\n---\n"
        )
    )

    events = _load_spawn_events(capture_file)
    assert [event["agent"] for event in events] == ["codex", "claude", "agy"]
    child_plans = [Path(event["plan"]) for event in events]
    assert [plan.name for plan in child_plans] == [
        "marbles-ancestor_L1.md",
        "marbles-ancestor_L2.md",
        "marbles-ancestor_L3.md",
    ]
    child_plan_1 = child_plans[0].read_text(encoding="utf-8")
    child_plan_2 = child_plans[1].read_text(encoding="utf-8")
    child_plan_3 = child_plans[2].read_text(encoding="utf-8")
    assert child_plan_1.startswith(
        "---\nagent: codex\nskill_name: marbles\n"
        "focus: initial prompt\npriority: P0\n---\n"
    )
    assert child_plan_2.startswith(
        "---\nagent: claude\nskill_name: marbles\n"
        "focus: initial prompt\npriority: P0\n---\n"
    )
    assert child_plan_3.startswith(
        "---\nagent: agy\nskill_name: marbles\n"
        "focus: initial prompt\npriority: P0\n---\n"
    )
    assert "The worker must remain on the operator-assigned substrate." in child_plan_1
    assert "Do not switch branches." in child_plan_1
    assert "Do not create or move to a worktree." in child_plan_1
    assert "Do not relocate execution to another lane or clone." in child_plan_1


def test_marbles_runtime_consumes_ancestor_override_sequence_across_children(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_ANCESTOR_SEQUENCE"] = "agy|accessibility|;claude|auth hardening|"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "3",
            "--rotation",
            "trio",
            "--runtime",
            "headless",
            "--prompt",
            "Fix installer drift end to end",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_dir = state_dirs[0]
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    assert state["god_plan"] == str(state_dir / "god.md")
    assert state["ancestor_plan"] == str(state_dir / "ancestor.md")
    assert state["plan"] == str(state_dir / "ancestor.md")
    assert state["rotation"] == "trio"
    assert state["rotation_pool"] == ["codex", "claude", "agy"]
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", state["ancestor_mtime"]
    )
    assert [loop["agent"] for loop in state["loops"][:3]] == [
        "codex",
        "agy",
        "claude",
    ]
    assert [loop["focus"] for loop in state["loops"][:3]] == [
        "initial prompt",
        "accessibility",
        "auth hardening",
    ]
    assert "model" not in state["loops"][2]
    assert all(loop["ancestor_slug"] == "ancestor" for loop in state["loops"][:3])
    assert state["ancestor_mtime"] == datetime.fromtimestamp(
        (state_dir / "ancestor.md").stat().st_mtime,
        timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    events = _load_spawn_events(capture_file)
    assert [event["agent"] for event in events] == ["codex", "agy", "claude"]
    child_plans = [Path(event["plan"]) for event in events]
    assert [plan.name for plan in child_plans] == [
        "marbles-ancestor_L1.md",
        "marbles-ancestor_L2.md",
        "marbles-ancestor_L3.md",
    ]
    child_plan_1 = child_plans[0].read_text(encoding="utf-8")
    child_plan_2 = child_plans[1].read_text(encoding="utf-8")
    child_plan_3 = child_plans[2].read_text(encoding="utf-8")
    assert child_plan_1.startswith(
        "---\nagent: codex\nskill_name: marbles\n"
        "focus: initial prompt\npriority: P0\n---\n"
    )
    assert child_plan_2.startswith(
        "---\nagent: agy\nfocus: accessibility\npriority: P0\n---\n"
    )
    assert child_plan_3.startswith(
        "---\nagent: claude\nfocus: auth hardening\npriority: P0\n---\n"
    )
    assert "The worker must remain on the operator-assigned substrate." in child_plan_2
    assert "Do not switch branches." in child_plan_2
    assert "Do not create or move to a worktree." in child_plan_2
    assert "model:" not in child_plans[2].read_text(encoding="utf-8")

    convergence_reports = sorted((crafted_home / "artifacts").rglob("*_CONVERGENCE.md"))
    assert len(convergence_reports) == 1
    convergence = convergence_reports[0].read_text(encoding="utf-8")
    assert "## Steering Surfaces" in convergence
    assert f"- GOD: {state['archived_from']}/god.md" in convergence
    assert f"- ANCESTOR: {state['archived_from']}/ancestor.md" in convergence


def test_marbles_contract_docs_forbid_worker_worktree_escape() -> None:
    checked_files = [
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "skills"
        / "vc-marbles"
        / "SKILL.md",
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "skills"
        / "vc-marbles"
        / "RECEPTION.md",
        REPO_ROOT / "workflows" / "MARBLES.md",
    ]
    forbidden = [
        "create or use a `git worktree`",
        "creates a `git worktree`",
        "worktree escape hatch",
        "current repo/worktree path",
        "Work only inside your assigned tree, worktree, or lane.",
    ]

    offenders = []
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(REPO_ROOT)} contains {phrase!r}"
            for phrase in forbidden
            if phrase in text
        )

    assert not offenders, "\n".join(offenders)


def test_marbles_no_watch_still_creates_god_and_ancestor_contract(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "claude",
            "--count",
            "1",
            "--runtime",
            "headless",
            "--no-watch",
            "--prompt",
            "Harden the installer quick path",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_dir = state_dirs[0]
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    assert state["status"] == "completed"
    assert state["previous_status"] == "initialized"
    assert state["god_plan"] == str(state_dir / "god.md")
    assert state["ancestor_plan"] == str(state_dir / "ancestor.md")
    assert (state_dir / "god.md").exists()
    assert (state_dir / "ancestor.md").exists()

    events = _load_spawn_events(capture_file)
    assert len(events) == 1
    assert state["archived_from"] in str(events[0]["success_hook"])
    assert str(events[0]["plan"]).endswith("marbles-ancestor_L1.md")


def test_marbles_materializes_failed_loop_when_child_spawn_dies_before_meta(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_FAIL_BEFORE_META_LOOP"] = "2"
    env["VIBECRAFTED_MARBLES_META_TIMEOUT_S"] = "3"
    env["VIBECRAFTED_MARBLES_REPORT_TIMEOUT_S"] = "2"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Stabilize runtime truth",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_dir = state_dirs[0]
    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert [loop["status"] for loop in state["loops"]] == ["done", "failed"]
    failed_loop = state["loops"][1]
    assert failed_loop["loop"] == 2
    assert failed_loop["report"].endswith("_L2_codex.md")
    assert failed_loop["failure_reason"] == "spawn-failed"
    assert failed_loop["exit_code"] == 42
    assert Path(failed_loop["transcript"]).exists()
    assert (
        "failure surfaced from launch metadata" in result.stdout
        or "failure surfaced as:" in result.stdout
    )
    assert "Exit code: 42" in result.stdout
    assert "Traceback" not in result.stderr
    assert "NameError" not in result.stderr

    meta_records = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f"find '{crafted_home / 'artifacts'}' -type f -name '*.meta.json' -print0 "
                f"| xargs -0 jq -r 'select(.run_id==\"{state['run_id']}-002\") | .status'"
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert "failed" in meta_records.stdout.splitlines()
    assert "no meta.json within" not in result.stdout


def test_marbles_spawn_fails_fast_when_watcher_script_is_invalid(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    watcher = scripts_dir / "marbles_watcher.sh"
    watcher.write_text(
        watcher.read_text(encoding="utf-8") + "\necho '\n", encoding="utf-8"
    )
    watcher.chmod(0o755)

    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Guard marbles against broken watcher syntax",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Shell syntax check failed" in result.stderr
    assert "marbles_watcher.sh" in result.stderr
    assert not capture_file.exists()


def test_marbles_watcher_waits_for_meta_completion_before_advancing(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_DELAY_META_AFTER_REPORT_LOOP"] = "1"
    env["MARBLES_TEST_DELAY_META_AFTER_REPORT_S"] = "4"
    env["VIBECRAFTED_MARBLES_META_TIMEOUT_S"] = "8"
    env["VIBECRAFTED_MARBLES_REPORT_TIMEOUT_S"] = "10"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "agy",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Wait for the loop to finish before advancing",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state = json.loads((state_dirs[0] / "state.json").read_text(encoding="utf-8"))

    assert state["status"] == "completed"
    assert [loop["status"] for loop in state["loops"]] == ["done", "done"]
    assert "no meta.json within" not in result.stdout

    events = _load_spawn_events(capture_file)
    assert len(events) == 2
    assert [event["loop"] for event in events] == [1, 2]
    assert all(event["suppress_report_hint"] == "1" for event in events)

    meta_paths = list((crafted_home / "artifacts").rglob("*.meta.json"))
    assert meta_paths
    assert {
        json.loads(path.read_text(encoding="utf-8"))["liveness"] for path in meta_paths
    } == {"terminal"}


def test_marbles_no_watch_keeps_report_hint_enabled(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "agy",
            "--count",
            "1",
            "--runtime",
            "headless",
            "--no-watch",
            "--prompt",
            "Keep the extra report hint outside watcher mode",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    events = _load_spawn_events(capture_file)
    assert len(events) == 1
    assert events[0]["suppress_report_hint"] == ""


def test_marbles_watcher_does_not_consume_failed_fallback_report(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_FAIL_WITH_REPORT_LOOP"] = "1"
    env["VIBECRAFTED_MARBLES_META_TIMEOUT_S"] = "2"
    env["VIBECRAFTED_MARBLES_REPORT_TIMEOUT_S"] = "2"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "2",
            "--runtime",
            "headless",
            "--prompt",
            "Stabilize runtime truth",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state = json.loads((state_dirs[0] / "state.json").read_text(encoding="utf-8"))

    assert state["status"] == "failed"
    assert [loop["status"] for loop in state["loops"]] == ["failed"]
    failed_loop = state["loops"][0]
    assert failed_loop["report"].endswith("_L1_codex.md")
    assert failed_loop["failure_reason"] == "spawn-failed"
    assert failed_loop["exit_code"] == 17
    assert Path(failed_loop["transcript"]).exists()
    assert "report ✓" not in result.stdout
    assert "failed" in result.stdout

    events = _load_spawn_events(capture_file)
    assert len(events) == 1


def test_marbles_next_fails_zero_exit_failed_report_before_convergence(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env["MARBLES_TEST_REPORT_FAILED_META_COMPLETED_LOOP"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "1",
            "--runtime",
            "headless",
            "--no-watch",
            "--prompt",
            "Stop on failed report frontmatter",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state = json.loads((state_dirs[0] / "state.json").read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert state["status"] == "failed"
    assert state["previous_status"] == "failed"
    assert [loop["status"] for loop in state["loops"]] == ["failed"]
    failed_loop = state["loops"][0]
    assert failed_loop["failure_reason"] == "report-failed"
    assert failed_loop["exit_code"] == 0
    assert failed_loop["report"].endswith("_L1_codex.md")

    convergence_reports = sorted((crafted_home / "artifacts").rglob("*_CONVERGENCE.md"))
    assert len(convergence_reports) == 1
    convergence = convergence_reports[0].read_text(encoding="utf-8")
    assert "status: FAILED" in convergence
    assert "reason: report-failed" in convergence
    assert "produced a failed report/status" in convergence
    assert "loops completed successfully" not in convergence


def test_marbles_watcher_reception_guard_flags_missing_convergence(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VIBECRAFTED_MARBLES_CONVERGENCE_GRACE_S", None)
    env["VIBECRAFTED_MARBLES_CONVERGENCE_ATTEMPTS"] = "3"
    env["VIBECRAFTED_MARBLES_CONVERGENCE_BACKOFF_S"] = "0"
    env["VIBECRAFTED_MARBLES_CONVERGENCE_BACKOFF_MAX_S"] = "0"
    env["MARBLES_TEST_SKIP_SUCCESS_HOOK_LOOP"] = "1"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "codex",
            "--count",
            "1",
            "--runtime",
            "headless",
            "--prompt",
            "Surface missing convergence handoff",
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state = json.loads((state_dirs[0] / "state.json").read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert state["status"] == "failed"
    assert state["previous_status"] == "failed"
    assert [loop["status"] for loop in state["loops"]] == ["done"]
    assert state["loops"][0]["metrics"]["commits"] == 1
    assert "missing convergence report" in result.stdout

    convergence_reports = sorted((crafted_home / "artifacts").rglob("*_CONVERGENCE.md"))
    assert len(convergence_reports) == 1
    convergence = convergence_reports[0].read_text(encoding="utf-8")
    assert "status: FAILED" in convergence
    assert "reason: missing_convergence_after_completed" in convergence
    assert "guard: pani_krysia" in convergence
    assert "guard_kind: reception_guard" in convergence
    assert "failure_kind: missing_convergence_handoff" in convergence
    assert "guard_policy: convergence_handoff_backoff_v1" in convergence
    assert "fallback_attempts: 3" in convergence
    assert "backoff_initial_s: 0" in convergence
    assert "failover: reception_guard_failure_report" in convergence
    assert (
        "Reception guard observed terminal watcher status without a convergence report"
        in convergence
    )
    assert "- Guard: Pani Krysia" in convergence
    assert "- Policy: convergence_handoff_backoff_v1" in convergence


def test_marbles_verification_poll_survives_watcher_exit_without_job_noise(
    tmp_path: Path,
) -> None:
    scripts_dir, capture_file = _prepare_fake_marbles_bundle(tmp_path)
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    home.mkdir()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["MARBLES_SPAWN_CAPTURE"] = str(capture_file)
    env["MARBLES_TEST_SKIP_VERIFIED_REPORT_LOOP"] = "1"
    env["VIBECRAFTED_MARBLES_VERIFICATION_TIMEOUT_S"] = "1"
    env["VIBECRAFTED_MARBLES_VERIFICATION_POLL_S"] = "1"
    # Force the terminal watcher to archive the state directory before the
    # detached verifier reaches its one-second timeout. The verifier must
    # follow that owned atomic move instead of writing only to the stale live
    # path.
    env["VIBECRAFTED_MARBLES_VERIFICATION_GRACE_S"] = "0"
    env.pop("VC_FRAME", None)
    env.pop("VC_FRAME_PANE_ID", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VIBECRAFTED_OPERATOR_SESSION", None)

    result = subprocess.run(
        [
            "bash",
            str(scripts_dir / "marbles_spawn.sh"),
            "--agent",
            "agy",
            "--count",
            "1",
            "--runtime",
            "headless",
            "--prompt",
            "Leave verification to the detached watcher",
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    state_dirs = _marbles_state_dirs(crafted_home)
    assert len(state_dirs) == 1
    state_path = state_dirs[0] / "state.json"

    # The detached watcher reaches "timed_out" after timeout(1)+poll(1), after
    # the terminal watcher has already moved the state into `_archived`.
    # Scheduling and FS-flush latency are still bounded generously.
    deadline = time.monotonic() + 30
    verification_status = ""
    while time.monotonic() < deadline:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        verification_status = state["loops"][0].get("verification_status", "")
        if verification_status == "timed_out":
            break
        time.sleep(0.2)

    assert verification_status == "timed_out"
    assert "Terminated: 15" not in result.stdout
    assert "Terminated: 15" not in result.stderr


def _pending_verification_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "loops": [
                    {
                        "loop": 1,
                        "verification_status": "pending",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def _run_verification_timeout(state_path: Path, tmp_path: Path) -> None:
    report_path = tmp_path / "report.md"
    env = os.environ.copy()
    env["VIBECRAFTED_MARBLES_VERIFICATION_TIMEOUT_S"] = "0"
    subprocess.run(
        [
            "bash",
            str(
                REPO_ROOT
                / "vibecrafted-core/vibecrafted_core/runtime/scripts/marbles_verify_watch.sh"
            ),
            str(state_path),
            "1",
            str(report_path),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
    )


def test_marbles_verifier_rejects_ambiguous_live_and_archived_state(
    tmp_path: Path,
) -> None:
    marbles_root = tmp_path / "marbles"
    live_state = marbles_root / "impl-260727-010000-00001/state.json"
    archived_state = (
        marbles_root / "_archived/2026-07-27/impl-260727-010000-00001/state.json"
    )
    _pending_verification_state(live_state)
    _pending_verification_state(archived_state)

    _run_verification_timeout(live_state, tmp_path)

    for state_path in (live_state, archived_state):
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["loops"][0]["verification_status"] == "pending"


def test_marbles_verifier_rejects_symlinked_archived_run(tmp_path: Path) -> None:
    marbles_root = tmp_path / "marbles"
    run_id = "impl-260727-010000-00002"
    outside_state = tmp_path / "outside" / run_id / "state.json"
    _pending_verification_state(outside_state)
    archived_date = marbles_root / "_archived/2026-07-27"
    archived_date.mkdir(parents=True)
    (archived_date / run_id).symlink_to(outside_state.parent, target_is_directory=True)

    _run_verification_timeout(marbles_root / run_id / "state.json", tmp_path)

    state = json.loads(outside_state.read_text(encoding="utf-8"))
    assert state["loops"][0]["verification_status"] == "pending"
