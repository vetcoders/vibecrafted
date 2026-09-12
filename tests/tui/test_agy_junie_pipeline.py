from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
SPAWN_DIR = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"


def _write_plan(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan = tmp_path / "plan.md"
    plan.write_text(
        "---\nrun_id: test\nagent: test\nstatus: prompt\n---\n\nDo the bounded task.\n",
        encoding="utf-8",
    )
    return plan


def _dry_run_launcher(tmp_path: Path, agent: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    plan = _write_plan(tmp_path)
    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home" / ".vibecrafted")

    result = subprocess.run(
        [
            "bash",
            str(SPAWN_DIR / f"{agent}_spawn.sh"),
            "--dry-run",
            "--runtime",
            "headless",
            "--root",
            str(root),
            str(plan),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    marker = "Dry run mode: launcher generated only: "
    launcher_lines = [
        line.removeprefix(marker)
        for line in result.stdout.splitlines()
        if line.startswith(marker)
    ]
    assert launcher_lines
    launcher = Path(launcher_lines[-1])
    assert launcher.is_file()
    return launcher


def test_command_deck_exposes_agy_junie_and_grok_help_topics() -> None:
    for agent in ("agy", "junie", "grok", "cursor"):
        result = subprocess.run(
            [str(LAUNCHER), "help", agent],
            check=True,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert f"Canonical commands for {agent}. Actions come first" in result.stdout
        assert f"implement {agent} <plan.md>" in result.stdout
        assert f"await     {agent} --last" in result.stdout


def test_agy_spawn_dry_run_uses_antigravity_print_contract(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path, "agy")
    text = launcher.read_text(encoding="utf-8")

    assert "SPAWN_AGENT=agy" in text
    assert "agy --dangerously-skip-permissions --add-dir" in text
    assert "--print-timeout 30m" in text
    assert '--print "$(cat ' in text
    assert "agy --print --dangerously-skip-permissions" not in text
    assert "Agy completed without writing a standalone report file" in text
    assert "Agy failed before writing a standalone report file" in text
    assert "pipeline_status=65" not in text


def test_junie_spawn_dry_run_uses_project_task_contract(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path, "junie")
    text = launcher.read_text(encoding="utf-8")

    assert "SPAWN_AGENT=junie" in text
    assert "junie --project=" in text
    assert "--task=" in text
    assert "--skip-update-check" in text
    assert "--input-format=text" in text
    assert "--output-format=json-stream" in text
    assert "--output-format=text" not in text


def test_junie_spawn_dry_run_pipes_json_stream_to_transcript(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path, "junie")
    text = launcher.read_text(encoding="utf-8")

    assert "junie --project=" in text
    assert "--output-format=json-stream" in text
    assert "2>&1 | tee -a $qtranscript" not in text
    assert "tee -a" in text


def test_junie_retries_the_known_transient_issue_template_failure_once(
    tmp_path: Path,
) -> None:
    launcher = _dry_run_launcher(tmp_path, "junie")
    text = launcher.read_text(encoding="utf-8")

    assert "for junie_attempt in 1 2" in text
    assert "issue.md.junie_standalone" in text
    assert "transient Junie issue-template failure; retrying once" in text
    assert "JUNIE_ISSUE_RETRY_DELAY_SECONDS" in text


def test_grok_spawn_dry_run_uses_prompt_file_contract(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path, "grok")
    text = launcher.read_text(encoding="utf-8")

    # Contract alignment (grok 0.2.97): --prompt-file delivery, permission, streaming-json
    # for transcript capture (matches verified python _stdin_command lane), --cwd.
    assert "SPAWN_AGENT=grok" in text
    assert "grok --cwd" in text
    assert "--permission-mode bypassPermissions" in text
    assert "--prompt-file" in text
    assert "--output-format streaming-json" in text
    assert (
        "tee -a " in text
    )  # transcript capture (expanded path in generated SPAWN_CMD)
    assert "--prompt-file " in text
    # resume flag shape covered in dedicated grok test below (source contract)


def test_cursor_spawn_dry_run_uses_stream_json_stdin_contract(
    tmp_path: Path,
) -> None:
    launcher = _dry_run_launcher(tmp_path, "cursor")
    text = launcher.read_text(encoding="utf-8")

    # cursor-agent headless contract: `-p` reads the prompt from stdin,
    # stream-json events tee'd into the transcript, --force --trust mirror
    # the headless bypass policy used by the other fleet wrappers.
    assert "SPAWN_AGENT=cursor" in text
    assert "cursor-agent -p --output-format stream-json --force --trust" in text
    assert "tee -a" in text
    assert "Cursor failed before writing a standalone report file" in text


def test_dry_run_meta_records_new_agents(tmp_path: Path) -> None:
    for agent in ("agy", "junie", "grok", "cursor"):
        launcher = _dry_run_launcher(tmp_path / agent, agent)
        meta_line = next(
            line
            for line in launcher.read_text(encoding="utf-8").splitlines()
            if line.startswith("meta=")
        )
        meta_path = Path(meta_line.split("=", 1)[1].strip().strip("'\""))
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        assert payload["agent"] == agent


def test_grok_resume_uses_resume_flag_not_session_id_and_streams_json() -> None:
    """Regression on grok resume contract (grok 0.2.97):
    - MUST use --resume (never -s/--session-id for resume per help)
    - headless uses --output-format streaming-json for parseable transcript
    - --single for prompt continuation; --permission-mode and --no-alt-screen
    - non-interactive wraps streaming-json through AgentStreamParser
    - non-interactive lands in G7 worker host (not operator seat)
    Source-of-truth is the grok case in marbles.sh (shell resume builder).
    """
    marbles_path = (
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/marbles.sh"
    )
    src = marbles_path.read_text(encoding="utf-8")

    # Isolate the resume_command grok) block (not fresh_session_command).
    assert "_vetcoders_resume_command()" in src
    resume_fn = src.split("_vetcoders_resume_command()", 1)[1]
    assert "    grok)" in resume_fn
    grok_block = resume_fn.split("    grok)", 1)[1].split(";;", 1)[0]

    # resume flag shape
    assert "grok --resume " in grok_block
    assert "--session-id" not in grok_block
    assert "-s " not in grok_block and "--session-id=" not in grok_block

    # output format for capture + prompt delivery via --single in resume context
    assert "--output-format streaming-json" in grok_block
    assert (
        "--single " in grok_block
        or "--single\n" in grok_block
        or "--single " in grok_block
    )

    # other contract flags present in headless resume path
    assert "--permission-mode bypassPermissions" in grok_block
    assert "--no-alt-screen" in grok_block
    assert "--cwd " in grok_block

    # AgentStreamParser + G7 worker host are resume-agent contracts (not only
    # inside the grok case of the command builder).
    assert "_vetcoders_wrap_with_agent_stream" in src
    assert "vibecrafted_core.agent_stream" in src
    assert "Resume launched in worker session:" in src
    assert "_vetcoders_effective_worker_session" in src
    assert "headless (G7 workers column)" in src

    # Fixture-based unit test for spawn.py extraction against grok 0.2.97 streaming-json shape
    # (no real grok call). Covers session-id (sessionId in end event) + JSON_TOKEN_PATTERNS usage.
    from vibecrafted_core.spawn import _extract_cost, _extract_session, _extract_tokens

    # Realistic grok streaming-json transcript fragment (end event + usage if emitted; patterns are general)
    grok_stream = (
        '{"type":"thought","data":"..."}\n'
        '{"type":"text","data":"done"}\n'
        '{"type":"end","stopReason":"EndTurn",'
        '"sessionId":"019ec430-9888-78e3-8ca0-b29387444fdb",'
        '"usage":{"input_tokens":42,"output_tokens":17,"cache_read_input_tokens":5}}\n'
    )
    assert _extract_session(grok_stream) == "019ec430-9888-78e3-8ca0-b29387444fdb"
    toks = _extract_tokens(grok_stream)
    assert toks["input"] == 42
    assert toks["output"] == 17
    assert toks["cached_input"] == 5
    # Grok reports cached input as a subset of input, so total must not count
    # those five tokens twice.
    assert toks["total"] == 59
    # cost may be absent in raw stream (footer supplies); ensure no crash
    assert _extract_cost(grok_stream) is None or isinstance(
        _extract_cost(grok_stream), float
    )
