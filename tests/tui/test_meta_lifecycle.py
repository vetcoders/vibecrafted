from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "scripts"
    / "common.sh"
)

_ENV_SANITIZE = """
unset VC_FRAME VC_FRAME_PANE_ID VC_FRAME_SESSION_NAME VC_FRAME_TAB_NAME VC_FRAME_CONFIG_DIR
unset VIBECRAFTED_OPERATOR_SESSION VIBECRAFTED_RUN_ID VIBECRAFTED_RUN_LOCK
unset VIBECRAFTED_SKILL_CODE VIBECRAFTED_SKILL_NAME VIBECRAFTED_LOOP_NR
unset VIBECRAFTED_PARENT_MODEL CLAUDE_MODEL CODEX_MODEL GEMINI_MODEL GROK_MODEL
unset SPAWN_LOOP_NR SPAWN_META SPAWN_TRANSCRIPT SPAWN_REPORT SPAWN_ROOT
unset SPAWN_RUN_ID SPAWN_RUN_LOCK SPAWN_AGENT SPAWN_SKILL_CODE SPAWN_SKILL_NAME
unset SPAWN_MODEL SPAWN_PROMPT_ID
"""


def _bash(script: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="vibecrafted-meta-test-") as state_root:
        env = os.environ.copy()
        env["VIBECRAFTED_HOME"] = str(Path(state_root) / ".vibecrafted")
        return subprocess.run(
            ["bash", "-lc", _ENV_SANITIZE + script],
            check=True,
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )


def _write_meta(meta: Path, status: str) -> None:
    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID=lcyc-test-001
        spawn_write_meta "{meta}" "{status}" claude implement / plan.md report.md t.log l.sh
        '''
    )


def _load(meta: Path) -> dict:
    return json.loads(meta.read_text(encoding="utf-8"))


def test_control_plane_script_prefers_explicit_root_then_checkout(
    tmp_path: Path,
) -> None:
    explicit_root = tmp_path / "explicit"
    tools_home = tmp_path / "tools"
    installed_root = tools_home / "vibecrafted-current"
    for root in (explicit_root, installed_root):
        script = root / "scripts" / "control_plane_state.py"
        script.parent.mkdir(parents=True)
        script.write_text("# test helper\n", encoding="utf-8")

    result = _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export VIBECRAFTED_ROOT="{explicit_root}"
        export VIBECRAFTED_TOOLS_HOME="{tools_home}"
        spawn_control_plane_script
        unset VIBECRAFTED_ROOT
        spawn_control_plane_script
        cd "{tmp_path}"
        spawn_control_plane_script
        '''
    )

    assert result.stdout.splitlines() == [
        str(explicit_root / "scripts" / "control_plane_state.py"),
        str(REPO_ROOT / "scripts" / "control_plane_state.py"),
        str(installed_root / "scripts" / "control_plane_state.py"),
    ]


def test_mark_meta_running_flips_launching_to_running(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    _write_meta(meta, "launching")
    before = _load(meta)

    _bash(f'source "{COMMON_SH}"; spawn_mark_meta_running "{meta}"')

    after = _load(meta)
    assert after["status"] == "running"
    assert after["run_id"] == before["run_id"]
    assert after["created_at"] == before["created_at"]
    assert after["report"] == before["report"]


def test_mark_meta_running_never_resurrects_terminal_states(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    _write_meta(meta, "launching")
    _bash(f'source "{COMMON_SH}"; spawn_finish_meta "{meta}" completed 0')

    _bash(f'source "{COMMON_SH}"; spawn_mark_meta_running "{meta}"')

    assert _load(meta)["status"] == "completed"


def test_finish_meta_delegates_to_python_terminal_writer(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    transcript = tmp_path / "transcript.log"
    _write_meta(meta, "running")
    transcript.write_text("[12:10:00] session: shell-finish-001\n", encoding="utf-8")
    payload = _load(meta)
    payload["transcript"] = str(transcript)
    meta.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    _bash(f'source "{COMMON_SH}"; spawn_finish_meta "{meta}" failed 9')

    final = _load(meta)
    assert final["status"] == "failed"
    assert final["exit_code"] == 9
    assert final["liveness"] == "terminal"
    assert final["session_id"] == "shell-finish-001"
    assert isinstance(final["duration_s"], (int, float))


def test_finalize_artifacts_persists_model_and_duration(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"
    report.write_text("# Report\n\nDone.\n", encoding="utf-8")
    transcript.write_text(
        "[12:40:43] session: telemetry-session-001\n", encoding="utf-8"
    )

    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID=lcyc-test-001
        export CODEX_MODEL=gpt-5.3-codex
        spawn_write_meta "{meta}" launching codex implement "{tmp_path}" plan.md "{report}" "{transcript}" l.sh
        spawn_finish_meta "{meta}" completed 0
        python3 - "{meta}" <<'PY'
import json
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["model"] = "unknown"
payload["duration_s"] = None
payload["created_at"] = "2026-06-10T08:00:00+00:00"
payload["completed_at"] = "2026-06-10T08:00:05+00:00"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
    handle.write("\\n")
PY
        spawn_finalize_artifacts "{meta}" "{report}" "{transcript}"
        '''
    )

    final = _load(meta)
    assert final["model"] == "gpt-5.3-codex"
    assert final["duration_s"] == 5.0
    assert final["run_id"] == "lcyc-test-001"
    assert final["status"] == "completed"
    assert final["session_id"] == "telemetry-session-001"


def test_finalize_handoff_returns_regular_meta_for_triage(tmp_path: Path) -> None:
    """The shell handoff carries the canonical meta, never its compat symlink."""

    home = tmp_path / "home" / ".vibecrafted"
    reports = home / "artifacts" / "Vetcoders" / "demo" / "2026_0726" / "reports"
    reports.mkdir(parents=True)
    meta = reports / "announced.meta.json"
    report = reports / "announced.md"
    transcript = reports / "announced.transcript.log"

    result = _bash(
        f'''
        set -euo pipefail
        unset ZELLIJ ZELLIJ_PANE_ID ZELLIJ_SESSION_NAME
        source "{COMMON_SH}"
        export VIBECRAFTED_HOME="{home}"
        export SPAWN_RUN_ID=handoff-test-001
        export SPAWN_SKILL_CODE=impl
        printf '# Report\\n\\nDone.\\n' > "{report}"
        printf 'session: canonical-handoff-001\\nwork done\\n' > "{transcript}"
        spawn_write_meta "{meta}" launching codex implement "{tmp_path}" plan.md "{report}" "{transcript}" l.sh
        spawn_finish_meta "{meta}" completed 0
        final_meta="$(spawn_finalize_artifacts "{meta}" "{report}" "{transcript}")"
        [[ -f "$final_meta" && ! -L "$final_meta" ]]
        spawn_triage_run "$final_meta"
        printf 'FINAL_META=%s\\n' "$final_meta"
        '''
    )

    marker = next(
        line for line in result.stdout.splitlines() if line.startswith("FINAL_META=")
    )
    final_meta = Path(marker.removeprefix("FINAL_META="))
    assert meta.is_symlink()
    assert meta.resolve() == final_meta.resolve()
    assert final_meta.is_file()
    assert not final_meta.is_symlink()
    payload = json.loads(final_meta.read_text(encoding="utf-8"))
    assert payload["triage"] == "skipped"
    assert payload["triage_reason"] == "no_session"


def test_generated_launcher_walks_full_lifecycle(tmp_path: Path) -> None:
    # Plan verifier (VC-vbcr-stabilize-031): a trivial run must pass through
    # "running" while the agent command executes and land on a terminal state.
    launcher = tmp_path / "launch.sh"
    meta = tmp_path / "run.meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"

    _write_meta(meta, "launching")
    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_ROOT="{tmp_path}"
        export SPAWN_AGENT=claude
        export SPAWN_RUN_ID=lcyc-test-001
        spawn_generate_launcher "{launcher}" "{meta}" "{report}" "{transcript}" "{COMMON_SH}" "sleep 2"
        chmod +x "{launcher}"
        '''
    )

    proc = subprocess.Popen(
        ["bash", str(launcher)],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "VIBECRAFTED_INLINE_STARTUP_WATCH": "0",
            "HOME": str(tmp_path),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        seen_running = False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status = _load(meta).get("status")
            if status == "running":
                seen_running = True
                break
            if status in {"completed", "failed"}:
                break
            time.sleep(0.1)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()

    assert seen_running, "meta.json never reported status=running mid-run"
    final = _load(meta)
    assert final["status"] == "completed"
    assert final["exit_code"] == 0
    assert final["liveness"] == "terminal"
    assert final["model"] == "claude-cli-default"
    assert isinstance(final["duration_s"], (int, float))


def test_spawn_write_meta_schema_contract_pin(tmp_path: Path) -> None:
    meta = tmp_path / "run.meta.json"
    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        export SPAWN_RUN_ID=lcyc-test-pin-001
        export SPAWN_PROMPT_ID=prompt-123
        export SPAWN_LOOP_NR=4
        export SPAWN_SKILL_CODE=just
        spawn_write_meta "{meta}" "launching" "claude" "implement" "{tmp_path}" "plan.md" "report.md" "t.log" "l.sh" "gpt-4"
        '''
    )

    data = _load(meta)

    # Assert fields are exactly compatible
    assert data["status"] == "launching"
    assert data["agent"] == "claude"
    assert data["mode"] == "implement"
    assert data["root"] == str(tmp_path)
    assert data["input"] == "plan.md"
    assert data["report"] == "report.md"
    assert data["transcript"] == "t.log"
    assert data["launcher"] == "l.sh"
    assert data["prompt_id"] == "prompt-123"
    assert data["run_id"] == "lcyc-test-pin-001"
    assert data["loop_nr"] == 4
    assert data["skill_code"] == "just"
    assert isinstance(data["framework_version"], str)
    assert data["exit_code"] is None
    assert data["launcher_pid"] is None
    assert data["liveness"] == "pid_pending"
    assert data["model"] == "gpt-4"
    assert isinstance(data["created_at"], str)
    assert isinstance(data["updated_at"], str)
    for identity_key in (
        "workspace_id",
        "workspace_instance_id",
        "workspace_display_label",
        "worker_host_session",
        "worker_host_display",
        "vibecrafted_session_id",
    ):
        assert isinstance(data[identity_key], str)
    assert isinstance(data["build_id"], dict)
    assert data["build_id"]["schema"] == "vibecrafted.build-id.v1"
    assert data["workspace_id"]
    assert data["worker_host_session"].endswith("-w")
    assert " " not in data["worker_host_session"]

    expected_keys = {
        "created_at",
        "updated_at",
        "status",
        "agent",
        "mode",
        "root",
        "input",
        "report",
        "transcript",
        "launcher",
        "prompt_id",
        "run_id",
        "loop_nr",
        "skill_code",
        "framework_version",
        "exit_code",
        "launcher_pid",
        "liveness",
        "model",
        "workspace_id",
        "workspace_instance_id",
        "workspace_display_label",
        "worker_host_session",
        "worker_host_display",
        "build_id",
        "vibecrafted_session_id",
    }
    assert set(data.keys()) == expected_keys


def test_write_meta_python_direct(tmp_path: Path) -> None:
    import sys

    package_root = REPO_ROOT / "vibecrafted-core"
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

    from vibecrafted_core.spawn import write_meta

    meta = tmp_path / "run.meta.json"
    write_meta(
        meta_path=meta,
        status="launching",
        agent="claude",
        mode="implement",
        root=tmp_path,
        input_ref="plan.md",
        report="report.md",
        transcript="t.log",
        launcher="l.sh",
        model="gpt-4",
        prompt_id="prompt-123",
        run_id="lcyc-test-py-001",
        loop_nr=5,
        skill_code="just",
        framework_version="v1.0",
    )

    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["status"] == "launching"
    assert data["agent"] == "claude"
    assert data["mode"] == "implement"
    assert data["root"] == str(tmp_path)
    assert data["input"] == "plan.md"
    assert data["report"] == "report.md"
    assert data["transcript"] == "t.log"
    assert data["launcher"] == "l.sh"
    assert data["prompt_id"] == "prompt-123"
    assert data["run_id"] == "lcyc-test-py-001"
    assert data["loop_nr"] == 5
    assert data["skill_code"] == "just"
    assert data["framework_version"] == "v1.0"
    assert data["exit_code"] is None
    assert data["launcher_pid"] is None
    assert data["liveness"] == "pid_pending"
    assert data["model"] == "gpt-4"
    assert isinstance(data["created_at"], str)
    assert isinstance(data["updated_at"], str)


def test_triage_run_is_the_last_step_of_a_generated_launcher() -> None:
    """Triage closes the tab the launcher is running in, so it must run last.

    Anything sequenced after `spawn_triage_run` in a successful transfer may
    simply never execute — the pane is gone. Pinning the order here keeps a
    later edit from quietly moving artifact closure behind it and losing the
    report on exactly the runs that finished cleanly.
    """
    launcher_src = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "launcher.sh"
    ).read_text(encoding="utf-8")
    assert (
        launcher_src.count(
            'meta="$(spawn_finalize_artifacts "$meta" "$report" "$transcript")"'
        )
        == 2
    )

    for branch, tail in (
        ("success", 'spawn_triage_run "$meta"\nelse'),
        ("failure", 'spawn_triage_run "$meta"\n  exit "$exit_code"'),
    ):
        assert tail in launcher_src, f"{branch} branch does not end with triage"

    # ...and in both branches artifact closure precedes it.
    first_triage = launcher_src.index('spawn_triage_run "$meta"')
    first_finalize = launcher_src.index('spawn_finalize_artifacts "$meta"')
    assert first_finalize < first_triage

    last_triage = launcher_src.rindex('spawn_triage_run "$meta"')
    last_finalize = launcher_src.rindex('spawn_finalize_artifacts "$meta"')
    assert last_finalize < last_triage


def test_reap_runs_after_artifact_closure_and_before_triage() -> None:
    """The reaper sits between artifact closure and triage, in both branches.

    Before triage, because a successful transfer closes this tab: sequenced after
    it, the sweep may never run and the survivors keep burning cores until reboot.
    After artifact closure, because the reap is only correct once the run's
    terminal state is on disk — that is what makes it a *terminal* run's residue.
    """
    launcher_src = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "launcher.sh"
    ).read_text(encoding="utf-8")

    assert launcher_src.count("spawn_reap_run") == 2, "both branches must sweep"

    for finder in ("index", "rindex"):
        finalize = getattr(launcher_src, finder)('spawn_finalize_artifacts "$meta"')
        reap = getattr(launcher_src, finder)("spawn_reap_run")
        triage = getattr(launcher_src, finder)('spawn_triage_run "$meta"')
        assert finalize < reap < triage


def test_reap_run_never_fails_a_finished_run(tmp_path: Path) -> None:
    """The shell wrapper is fail-open: a reaper problem cannot fail a done run."""
    proc = _bash(
        f"""
        source "{COMMON_SH}"
        export VIBECRAFTED_REAPER=0
        spawn_reap_run
        echo "survived"
        """
    )
    assert proc.returncode == 0
    assert "survived" in proc.stdout


def test_triage_run_never_fails_a_finished_run(tmp_path: Path) -> None:
    """The shell wrapper is fail-open: no meta, no session, no vc-frame — exit 0."""
    meta = tmp_path / "agent.meta.json"
    meta.write_text(
        json.dumps({"run_id": "r1", "exit_code": 0}) + "\n", encoding="utf-8"
    )

    # _ENV_SANITIZE clears VC_FRAME_* but not the legacy ZELLIJ_* aliases that
    # vc-frame still dual-emits, and this suite may itself be running inside a
    # live session. Clear both so the assertion is about the code, not the host.
    result = _bash(
        f'''
        set -euo pipefail
        export HOME="{tmp_path / "home"}"
        mkdir -p "$HOME"
        unset VIBECRAFTED_HOME VIBECRAFTED_CONTROL_PLANE
        unset ZELLIJ ZELLIJ_PANE_ID ZELLIJ_SESSION_NAME
        source "{COMMON_SH}"
        spawn_triage_run "{meta}"
        echo "survived=$?"
        '''
    )

    assert "survived=0" in result.stdout
    # Headless test env has no vc-frame pane: the receipt says so plainly.
    data = json.loads(meta.read_text(encoding="utf-8"))
    assert data["triage"] == "skipped"
    assert data["triage_reason"] == "no_session"


def test_triage_run_tolerates_a_missing_meta(tmp_path: Path) -> None:
    _bash(
        f'''
        set -euo pipefail
        source "{COMMON_SH}"
        spawn_triage_run "{tmp_path / "absent.meta.json"}"
        '''
    )


def test_meta_writers_replace_never_truncate_in_place() -> None:
    """Every meta.json writer must publish via tmp + os.replace, never open("w").

    run.meta.json is a cross-process contract: the launcher, the startup
    watcher, the control-plane sync, dashboards and the lifecycle tests all
    read it while someone else is writing. An in-place `open(path, "w")`
    truncates first and fills later, so a concurrent reader can observe an
    empty file — the exact JSONDecodeError flake this suite hit in CI on
    test_generated_launcher_walks_full_lifecycle. Atomic rename means a
    reader sees the old document or the new one, never a half-written one.
    """
    meta_sh = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "lib"
        / "meta.sh"
    ).read_text(encoding="utf-8")

    # No shell-embedded writer may truncate the destination in place.
    assert 'open(meta_path, "w"' not in meta_sh, (
        "meta.sh writes meta.json in place; use tmp + os.replace so "
        "concurrent readers never observe a truncated file"
    )

    # Each live-state writer publishes through an atomic rename.
    for writer in (
        "spawn_update_meta_pid",
        "spawn_mark_meta_running",
        "spawn_reap_dead_run",
        "spawn_mark_unknown_liveness",
    ):
        start = meta_sh.index(f"{writer}()")
        body = meta_sh[start : meta_sh.index("\n}", start)]
        assert "os.replace(" in body, f"{writer} must publish via os.replace"

    spawn_py = (
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "spawn.py"
    ).read_text(encoding="utf-8")
    start = spawn_py.index("def _write_meta(")
    body = spawn_py[start : spawn_py.index("\ndef ", start + 1)]
    assert "os.replace(" in body, (
        "_write_meta (write-meta/finish-meta/finalize) must publish via "
        "tmp + os.replace"
    )
