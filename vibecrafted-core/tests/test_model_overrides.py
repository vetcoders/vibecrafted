"""Behavioral contract for safe Agy model-pin injection."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.model_overrides import _with_model_override
from vibecrafted_core.spawn import _stdin_command


@pytest.fixture
def fleet_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "config" / "vibecrafted"
    directory.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(directory.parent))
    return directory / "config.toml"


@pytest.mark.parametrize(
    ("cli", "plan", "configured", "expected", "source"),
    [
        ("cli-model", "plan-model", "fleet-model", "cli-model", "cli"),
        ("", "plan-model", "fleet-model", "plan-model", "plan"),
        ("", "", "fleet-model", "fleet-model", "config.toml"),
        ("", "", "", "", "provider_default"),
    ],
)
def test_fleet_model_precedence_is_receipted(
    tmp_path, fleet_config, cli, plan, configured, expected, source
):
    from vibecrafted_core import workflow

    if configured:
        fleet_config.write_text(f'[agents.codex]\nmodel = "{configured}"\n')
    prompt = f"---\nmodel: {plan}\n---\ntask" if plan else "task"
    spec = workflow.normalize_launch_spec(
        {
            "agent": "codex",
            "skill": "workflow",
            "root": str(tmp_path),
            "prompt": prompt,
            "model": cli,
        },
        tmp_path,
    )
    assert spec.model == expected
    receipt = workflow.machine_launch_receipt(
        {**spec.to_payload(), "model_requested": spec.model}
    )
    assert receipt["model_requested"] == expected
    assert receipt["model_source"] == source


def test_dispatch_effort_reaches_codex_process_and_receipt(
    tmp_path, monkeypatch, fleet_config
):
    from vibecrafted_core import workflow
    from vibecrafted_core.dispatch import supervisor
    from vibecrafted_core.dispatch.schema import parse_dispatch

    fleet_config.write_text('[agents.codex]\nmodel = "fleet-model"\neffort = "low"\n')
    source = (
        Path(__file__).parent / "dispatch/fixtures/minimal.dispatch.toml"
    ).read_text()
    source = source.replace("/tmp/vibecrafted-dispatch-fixture", str(tmp_path))
    source = source.replace(
        'agent = "codex"', 'agent = "codex"\nmodel = "cut-model"\neffort = "high"'
    )
    dispatch = parse_dispatch(source)
    capture = tmp_path / "argv.json"
    fake_cli = tmp_path / "codex"
    fake_cli.write_text(
        f"#!{os.sys.executable}\nimport json, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
    )
    fake_cli.chmod(0o755)
    observed = {}

    def execute(spec, source_dir, **kwargs):
        command = workflow.build_launch_command(spec, source_dir)
        command[0] = str(fake_cli)
        completed = subprocess.run(command, input="task", text=True, check=False)
        observed.update(
            workflow.machine_launch_receipt(
                {
                    **spec.to_payload(),
                    "model_requested": spec.model,
                    "effort_requested": spec.effort,
                }
            )
        )
        return {"accepted": completed.returncode == 0, "run_id": "stub"}

    monkeypatch.setattr(supervisor, "launch_workflow", execute)
    run = supervisor.workflow_cell_launcher(dispatch)(
        dispatch.cuts[0], "task", "initial"
    )
    assert run.accepted
    argv = json.loads(capture.read_text())
    assert argv[0] == "exec"
    assert argv[argv.index("-m") + 1] == "cut-model"
    assert "model_reasoning_effort=high" in argv
    assert observed["model_source"] == "plan"
    assert observed["effort_requested"] == "high"
    assert observed["effort_source"] == "plan"


@pytest.mark.parametrize(
    "body",
    [
        "[agents.codex]\nmodel = 42\n",
        "[agents.codex]\neffort = false\n",
        '[agents.codex]\nmodle = "expensive"\n',
    ],
)
def test_invalid_fleet_config_refuses_launch_even_with_cli_pin(
    tmp_path, fleet_config, body
):
    from vibecrafted_core import workflow

    fleet_config.write_text(body)
    with pytest.raises(ValueError, match="agents.codex"):
        workflow.normalize_launch_spec(
            {
                "agent": "codex",
                "skill": "workflow",
                "root": str(tmp_path),
                "prompt": "task",
                "model": "explicit-model",
            },
            tmp_path,
        )


@pytest.mark.parametrize("effort", ["42", "true", '""', '"-bad"'])
def test_dispatch_rejects_invalid_effort(fleet_config, effort):
    from vibecrafted_core.dispatch.schema import DispatchSchemaError, parse_dispatch

    source = (
        Path(__file__).parent / "dispatch/fixtures/minimal.dispatch.toml"
    ).read_text()
    source = source.replace('agent = "codex"', f'agent = "codex"\neffort = {effort}')
    with pytest.raises(DispatchSchemaError, match="effort"):
        parse_dispatch(source)


@pytest.mark.parametrize(
    "explicit,configured,expected,source",
    [
        ("high", "low", "high", "cli"),
        ("", "low", "low", "config.toml"),
        ("", "", "", "provider_default"),
    ],
)
def test_effort_defaults_and_cli_precedence(
    tmp_path, fleet_config, explicit, configured, expected, source
):
    from vibecrafted_core import workflow

    if configured:
        fleet_config.write_text(f'[agents.codex]\neffort = "{configured}"\n')
    spec = workflow.normalize_launch_spec(
        {
            "agent": "codex",
            "skill": "workflow",
            "root": str(tmp_path),
            "prompt": "task",
            "effort": explicit,
        },
        tmp_path,
    )
    receipt = workflow.machine_launch_receipt(workflow.launch_selection_receipt(spec))
    assert spec.effort == expected
    assert receipt["effort_requested"] == expected
    assert receipt["effort_source"] == source
    assert receipt["effort_effective"] == expected


def test_provider_default_receipt_only_exposes_cost_controls(
    tmp_path, monkeypatch, fleet_config
):
    from vibecrafted_core import workflow
    from vibecrafted_core.env_allowlist import filter_headless_worker_env

    cli_home = tmp_path / "codex-home"
    cli_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(cli_home))
    (cli_home / "config.toml").write_text(
        'model = "interactive-model"\nmodel_reasoning_effort = "high"\n'
        'api_key = "private-sentinel"\n[profiles.secret]\nurl = "private-sentinel"\n'
    )
    spec = workflow.normalize_launch_spec(
        {
            "agent": "codex",
            "skill": "workflow",
            "root": str(tmp_path),
            "prompt": "task",
        },
        tmp_path,
    )
    receipt = workflow.machine_launch_receipt(workflow.launch_selection_receipt(spec))
    assert receipt["model_source"] == "provider_default"
    assert receipt["model_effective"] == ""
    assert receipt["provider_config_model"] == "interactive-model"
    assert receipt["provider_config_effort"] == "high"
    assert "private-sentinel" not in json.dumps(receipt)
    assert filter_headless_worker_env(
        {"CODEX_HOME": str(cli_home), "XDG_CONFIG_HOME": "config"}
    ) == {"CODEX_HOME": str(cli_home), "XDG_CONFIG_HOME": "config"}


def test_invalid_fleet_config_exits_public_cli_before_start(tmp_path, fleet_config):
    fleet_config.write_text("[agents.codex]\neffort = false\n")
    marker = tmp_path / "started"
    fake = tmp_path / "codex"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    fake.chmod(0o755)
    completed = subprocess.run(
        [
            os.sys.executable,
            "-m",
            "vibecrafted_core.cli",
            "implement",
            "codex",
            "--root",
            str(tmp_path),
            "--prompt",
            "task",
            "--model",
            "explicit",
        ],
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
            "XDG_CONFIG_HOME": str(fleet_config.parent.parent),
            "VIBECRAFTED_HOME": os.environ["VIBECRAFTED_HOME"],
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "agents.codex.effort" in completed.stderr + completed.stdout
    assert not marker.exists()


@pytest.mark.parametrize(
    "parent_effort,expected,source",
    [
        ("", "low", "config.toml"),
        ("high", "high", "plan"),
    ],
)
def test_supervised_children_apply_fleet_defaults_before_spawn(
    tmp_path, monkeypatch, fleet_config, parent_effort, expected, source
):
    import asyncio

    from vibecrafted_core import workflow_runtime

    fleet_config.write_text('[agents.codex]\nmodel = "fleet-model"\neffort = "low"\n')
    monkeypatch.setenv("VIBECRAFTED_REPORT_PATH", str(tmp_path / "parent.md"))
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "parent-test")
    monkeypatch.setenv("VIBECRAFTED_EFFORT_REQUESTED", parent_effort)
    monkeypatch.setenv("VIBECRAFTED_EFFORT_SOURCE", "plan" if parent_effort else "")
    monkeypatch.setattr(
        workflow_runtime, "_resolve_agent_command", lambda agent, command, env: command
    )
    observed = {}

    class AtSpawn(RuntimeError):
        pass

    class Supervisor:
        async def run(self, **kwargs):
            observed.update(kwargs)
            raise AtSpawn

    monkeypatch.setattr(workflow_runtime, "AsyncSupervisor", Supervisor)
    with pytest.raises(AtSpawn):
        asyncio.run(
            workflow_runtime._run_child(
                kind="marbles",
                label="iteration-1",
                agent="codex",
                root=str(tmp_path),
                prompt="task",
            )
        )
    argv = observed["command"]
    assert argv[argv.index("-m") + 1] == "fleet-model"
    assert f"model_reasoning_effort={expected}" in argv
    receipt = json.loads(observed["meta_path"].read_text())
    assert receipt["model_source"] == "config.toml"
    assert receipt["effort_source"] == source
    assert receipt["effort_requested"] == expected


def test_launch_admission_persists_model_and_effort_before_process_start(
    tmp_path, monkeypatch, fleet_config
):
    from vibecrafted_core import workflow

    fleet_config.write_text('[agents.codex]\nmodel = "fleet-model"\neffort = "low"\n')
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.setattr(workflow, "_sweep_stale_runs", lambda: None)
    monkeypatch.setattr(
        workflow, "_resolve_agent_command", lambda agent, command, env: command
    )
    observed = {}
    real_popen = workflow.subprocess.Popen

    def refuse_popen(command, **kwargs):
        if not kwargs.get("start_new_session"):
            return real_popen(command, **kwargs)
        observed.update(kwargs)
        meta = Path(kwargs["env"]["VIBECRAFTED_META_PATH"])
        observed["admission"] = json.loads(meta.read_text())
        raise OSError("fixture refuses process start")

    spec = workflow.normalize_launch_spec(
        {
            "agent": "codex",
            "skill": "workflow",
            "prompt": "task",
            "root": str(tmp_path),
        },
        tmp_path,
    )
    # Patch only after Git/root normalization, whose read-only subprocesses
    # belong to admission rather than provider process creation.
    monkeypatch.setattr(workflow.subprocess, "Popen", refuse_popen)
    result = workflow.launch_workflow(spec, tmp_path)
    assert result["accepted"] is False
    assert "fixture refuses process start" in result["error"]
    admission = observed["admission"]
    receipt = workflow.machine_launch_receipt(result)
    for surface in (admission, receipt):
        assert surface["model_requested"] == "fleet-model"
        assert surface["model_source"] == "config.toml"
        assert surface["effort_requested"] == "low"
        assert surface["effort_source"] == "config.toml"


def _agy_argv(*prefix: str) -> list[str]:
    """Build a canonical Agy argv with a controlled existing option region."""

    return [
        "agy",
        *prefix,
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]


def test_agy_single_pin_is_idempotent() -> None:
    pinned = _with_model_override("agy", _stdin_command("agy"), "gemini-test")

    assert pinned[:3] == ["agy", "--model", "gemini-test"]
    assert _with_model_override("agy", pinned, "gemini-test") == pinned


def test_agy_pin_survives_a_resolved_executable_path() -> None:
    resolved = ["/opt/agents/bin/agy", *_agy_argv()[1:]]

    pinned = _with_model_override("agy", resolved, "gemini-test")

    assert pinned[:3] == ["/opt/agents/bin/agy", "--model", "gemini-test"]


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (
            _agy_argv("--model", "first", "--model", "first"),
            "model_override_ambiguous_existing_model",
        ),
        (
            _agy_argv("--model"),
            "model_override_missing_existing_model",
        ),
        (
            _agy_argv("--model", "first"),
            "model_override_conflicts_with_existing_model",
        ),
        (
            ["bash", "-c", 'agy --print "$(cat)"'],
            "model_override_unsupported_agy_command_shape",
        ),
        (
            ["bash", "-c", "agy --print= --input-format stream-json"],
            "model_override_unsupported_agy_command_shape",
        ),
    ],
)
def test_agy_invalid_existing_pin_or_shell_shape_is_rejected_before_launch(
    command: list[str], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _with_model_override("agy", command, "requested")


def test_agy_model_metacharacters_reach_fake_cli_as_one_value(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "argv.txt"
    shell_payload_marker = tmp_path / "shell-payload-ran"
    command_substitution_marker = tmp_path / "command-substitution-ran"
    fake_agy = tmp_path / "agy"
    fake_agy.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8"
    )
    fake_agy.chmod(0o755)
    requested = (
        f"gemini test; touch {shell_payload_marker}; "
        f"$(touch {command_substitution_marker})"
    )

    completed = subprocess.run(
        _with_model_override("agy", _stdin_command("agy"), requested),
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE": str(capture),
        },
        input="prompt body",
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines()[:3] == [
        "--model",
        requested,
        "--dangerously-skip-permissions",
    ]
    assert not shell_payload_marker.exists()
    assert not command_substitution_marker.exists()


def _kimi_argv(*prefix: str) -> list[str]:
    """Build a canonical Kimi headless argv (prompt travels as the -p value)."""

    return [
        "kimi",
        *prefix,
        "-p",
        "do the thing",
        "--output-format",
        "stream-json",
    ]


def test_kimi_single_pin_is_idempotent() -> None:
    pinned = _with_model_override("kimi", _kimi_argv(), "kimi-test")

    assert pinned[:3] == ["kimi", "--model", "kimi-test"]
    assert _with_model_override("kimi", pinned, "kimi-test") == pinned


def test_kimi_pin_survives_a_resolved_executable_path() -> None:
    resolved = ["/opt/agents/bin/kimi", *_kimi_argv()[1:]]

    pinned = _with_model_override("kimi", resolved, "kimi-test")

    assert pinned[:3] == ["/opt/agents/bin/kimi", "--model", "kimi-test"]


def test_kimi_short_flag_alias_is_an_existing_pin() -> None:
    pinned = _with_model_override("kimi", _kimi_argv("-m", "kimi-test"), "kimi-test")

    assert pinned == _kimi_argv("-m", "kimi-test")
    with pytest.raises(
        ValueError, match="model_override_conflicts_with_existing_model"
    ):
        _with_model_override("kimi", _kimi_argv("-m", "other"), "kimi-test")


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (
            _kimi_argv("--model", "first", "--model", "first"),
            "model_override_ambiguous_existing_model",
        ),
        (
            _kimi_argv("--model"),
            "model_override_missing_existing_model",
        ),
        (
            _kimi_argv("--model", "first"),
            "model_override_conflicts_with_existing_model",
        ),
        (
            ["bash", "-c", 'kimi -p "$(cat)"'],
            "model_override_unsupported_kimi_command_shape",
        ),
        (
            ["bash", "-c", "kimi -p prompt --output-format stream-json"],
            "model_override_unsupported_kimi_command_shape",
        ),
    ],
)
def test_kimi_invalid_existing_pin_or_shell_shape_is_rejected_before_launch(
    command: list[str], reason: str
) -> None:
    with pytest.raises(ValueError, match=reason):
        _with_model_override("kimi", command, "requested")


def test_kimi_model_metacharacters_reach_fake_cli_as_one_value(
    tmp_path: Path,
) -> None:
    capture = tmp_path / "argv.txt"
    shell_payload_marker = tmp_path / "shell-payload-ran"
    command_substitution_marker = tmp_path / "command-substitution-ran"
    fake_kimi = tmp_path / "kimi"
    fake_kimi.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8"
    )
    fake_kimi.chmod(0o755)
    requested = (
        f"kimi test; touch {shell_payload_marker}; "
        f"$(touch {command_substitution_marker})"
    )

    completed = subprocess.run(
        _with_model_override("kimi", _kimi_argv(), requested),
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CAPTURE": str(capture),
        },
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines()[:3] == [
        "--model",
        requested,
        "-p",
    ]
    assert not shell_payload_marker.exists()
    assert not command_substitution_marker.exists()


def test_provider_model_choices_use_only_visible_provider_cache(tmp_path, monkeypatch):
    from vibecrafted_core.model_overrides import provider_model_choices

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "advertised", "visibility": "list"},
                    {"slug": "internal-review", "visibility": "hide"},
                    {"slug": "advertised", "visibility": "list"},
                    "malformed",
                ]
            }
        )
    )
    result = provider_model_choices("codex")
    assert result["choices"] == ["advertised"]
    assert result["source"] == "provider_cache"
    assert provider_model_choices("claude")["choices"] == []


def test_provider_model_cache_corruption_preserves_provider_default(
    tmp_path, monkeypatch
):
    from vibecrafted_core.model_overrides import provider_model_choices

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "models_cache.json").write_text("not json")
    assert provider_model_choices("codex")["choices"] == []
    assert provider_model_choices("codex")["reason"]
