from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPAWN_DIR = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "runtime" / "scripts"


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VIBECRAFTED_RUNTIME_ROOT",
        "VIBECRAFTED_RUNTIME_BIN",
        "VIBECRAFTED_RUNTIME_HOME",
        "KIMI_MODEL",
        "SPAWN_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIBECRAFTED_PYTHON", sys.executable)
    monkeypatch.setenv(
        "VIBECRAFTED_CORE_PYTHONPATH", str(REPO_ROOT / "vibecrafted-core")
    )
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "vibecrafted-core"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home" / ".vibecrafted"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


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
            "--permissions",
            "bypass",
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


def _dry_run_launcher(tmp_path: Path, **env_overrides: str) -> Path:
    result = _run_spawn(tmp_path, _write_plan(tmp_path), **env_overrides)
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


def _launcher_value(launcher: Path, name: str) -> str:
    line = next(
        line
        for line in launcher.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{name}=")
    )
    return shlex.split(line.split("=", 1)[1])[0]


@pytest.mark.parametrize("model", [None, "kimi-k2-test"])
def test_kimi_launcher_passes_prompt_on_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str | None
) -> None:
    launcher = _dry_run_launcher(
        tmp_path, **({"KIMI_MODEL": model} if model is not None else {})
    )
    recording = tmp_path / "kimi-call.json"
    provider = tmp_path / "bin" / "kimi"
    provider.parent.mkdir()
    provider.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['KIMI_RECORDING']).write_text(json.dumps({\n"
        "    'argv': sys.argv[1:], 'stdin': sys.stdin.read(),\n"
        "}))\n",
        encoding="utf-8",
    )
    provider.chmod(0o755)
    monkeypatch.setenv("PATH", f"{provider.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("KIMI_RECORDING", str(recording))

    # Execute the generated provider payload, without the unrelated launcher
    # lifecycle hooks or any real provider process.
    result = subprocess.run(
        [
            "bash",
            "-c",
            _launcher_value(launcher, "SPAWN_CMD"),
        ],
        env={
            **os.environ,
            "SPAWN_MODEL": _launcher_value(launcher, "export SPAWN_MODEL"),
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    call = json.loads(recording.read_text(encoding="utf-8"))
    argv = call["argv"]
    assert call["stdin"] == ""
    prompt = argv[argv.index("-p") + 1]
    assert "Do the bounded task." in prompt
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    if model is None:
        assert "--model" not in argv
    else:
        assert argv[argv.index("--model") + 1] == model


def test_kimi_spawn_dry_run_meta_records_kimi(tmp_path: Path) -> None:
    launcher = _dry_run_launcher(tmp_path)
    meta_path = Path(_launcher_value(launcher, "meta"))
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
    from vibecrafted_core.execution_controls import resolve_execution_controls
    from vibecrafted_core.prompt_transport import stdin_transport
    from vibecrafted_core.spawn import _default_command, _stdin_command

    controls = resolve_execution_controls("kimi", permissions="bypass")
    assert stdin_transport("kimi") == "argv"
    with pytest.raises(ValueError, match="no stdin prompt lane"):
        _stdin_command("kimi", controls)
    assert _default_command("kimi", "go", controls) == [
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
        permissions="bypass",
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


def test_terminal_presentation_cannot_admit_interactive_policy_into_print_lane(
    tmp_path: Path,
) -> None:
    """A visible worker pane still runs -p; auto must refuse before artifacts."""
    result = subprocess.run(
        [
            "bash",
            str(SPAWN_DIR / "kimi_spawn.sh"),
            "--dry-run",
            "--runtime",
            "terminal",
            "--root",
            str(tmp_path),
            str(_write_plan(tmp_path)),
        ],
        cwd=REPO_ROOT,
        env=os.environ | {"VIBECRAFTED_TEST_MODE": "1", "VIBECRAFTED_GUARD": "0"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "interactive-only" in result.stderr
    assert "bypass" in result.stderr
    assert not (tmp_path / "home" / ".vibecrafted" / "control_plane").exists()
