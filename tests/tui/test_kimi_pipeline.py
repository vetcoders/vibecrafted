from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
SPAWN_DIR = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"


def _write_plan(tmp_path: Path, body: str = "Do the bounded task.\n") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"---\nrun_id: test\nagent: test\nstatus: prompt\n---\n\n{body}",
        encoding="utf-8",
    )
    return plan


def _run_spawn(
    tmp_path: Path, plan: Path, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home" / ".vibecrafted")
    env.update(env_overrides)

    return subprocess.run(
        [
            "bash",
            str(SPAWN_DIR / "kimi_spawn.sh"),
            "--dry-run",
            "--runtime",
            "headless",
            "--root",
            str(root),
            str(plan),
        ],
        check=False,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _dry_run_launcher(tmp_path: Path) -> Path:
    result = _run_spawn(tmp_path, _write_plan(tmp_path))
    assert result.returncode == 0, result.stderr

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


def test_command_deck_exposes_kimi_help_topic() -> None:
    result = subprocess.run(
        [str(LAUNCHER), "help", "kimi"],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert "Canonical commands for kimi. Actions come first" in result.stdout
    assert "implement kimi <plan.md>" in result.stdout
    assert "await     kimi --last" in result.stdout


def test_kimi_spawn_dry_run_inlines_prompt_on_argv(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path)
    text = launcher.read_text(encoding="utf-8")

    assert "SPAWN_AGENT=kimi" in text
    # kimi 0.42.0 has no stdin prompt lane: -p takes the prompt as its argv
    # value, expanded from the private 0600 prompt file at run time.
    assert 'kimi -p "$(cat ' in text
    assert "_kimi_prompt.md" in text
    assert "_kimi_prompt.ndjson" not in text
    assert "--output-format stream-json" in text
    # Print mode is never-ask by construction; no permission flags exist that
    # combine with --prompt.
    assert "--yolo" not in text
    assert "--plan" not in text
    # Model rides SPAWN_MODEL inside the launcher when the spawn caller did
    # not pin one (mirrors the agy wrapper).
    assert '${SPAWN_MODEL:+--model "$SPAWN_MODEL"}' in text
    # Human pane through the shared parser; raw stream teed for await/meta.
    assert "vibecrafted_core.agent_stream --agent kimi --last-message" in text
    assert "tee -a" in text
    assert "Kimi completed without writing a standalone report file" in text
    assert "Kimi failed before writing a standalone report file" in text


def test_kimi_spawn_dry_run_meta_records_kimi(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path)
    meta_line = next(
        line
        for line in launcher.read_text(encoding="utf-8").splitlines()
        if line.startswith("meta=")
    )
    meta_path = Path(meta_line.split("=", 1)[1].strip().strip("'\""))
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["agent"] == "kimi"


def test_kimi_spawn_warns_then_refuses_as_the_prompt_approaches_arg_max(
    tmp_path: Path,
) -> None:
    # ARG_MAX is ~1MiB on macOS and kimi takes the whole prompt on argv; the
    # wrapper warns from 200KiB and dies from 900KiB instead of truncating.
    big = _write_plan(tmp_path / "warn", body="x" * (250 * 1024))
    warned = _run_spawn(tmp_path / "warn", big)
    assert warned.returncode == 0, warned.stderr
    assert "kimi takes the prompt on argv" in warned.stderr

    huge = _write_plan(tmp_path / "die", body="x" * (950 * 1024))
    refused = _run_spawn(tmp_path / "die", huge)
    assert refused.returncode != 0
    assert "ARG_MAX" in refused.stderr


def test_kimi_stdin_lane_is_refused_and_default_command_inlines_prompt() -> None:
    """The supervised stdin contract cannot carry kimi; the argv-inline
    builder is the only kimi lane (ps/ARG_MAX tradeoff documented in
    prompt_transport)."""
    from vibecrafted_core.prompt_transport import stdin_transport
    from vibecrafted_core.spawn import _default_command, _stdin_command

    assert stdin_transport("kimi") == "argv"
    with pytest.raises(ValueError, match="no stdin prompt lane"):
        _stdin_command("kimi")
    assert _default_command("kimi", "go") == [
        "kimi",
        "-p",
        "go",
        "--output-format",
        "stream-json",
    ]


def test_kimi_build_launch_command_inlines_prompt_and_pins_model(
    tmp_path: Path,
) -> None:
    from vibecrafted_core import workflow

    spec = workflow.WorkflowLaunchSpec(
        agent="kimi",
        mode="workflow",
        skill="workflow",
        prompt="go",
        file="",
        runtime="headless",
        root="/tmp/repo",
        model="kimi-k2-test",
    )
    command = workflow.build_launch_command(spec, tmp_path)
    assert command == [
        "kimi",
        "--model",
        "kimi-k2-test",
        "-p",
        "go",
        "--output-format",
        "stream-json",
    ]

    # A materialized prompt file wins over spec.prompt (the supervisor path).
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("from the file\n", encoding="utf-8")
    command = workflow.build_launch_command(spec, tmp_path, prompt_file=prompt_file)
    assert command[command.index("-p") + 1] == "from the file\n"
