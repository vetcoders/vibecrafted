"""Run real child boundaries under polluted parents, without real providers/Frame."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from vibecrafted_core import spawn, workflow
from vibecrafted_core.env_allowlist import filter_headless_worker_env

POLLUTED = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "CLICOLOR": "0",
    "CLICOLOR_FORCE": "0",
    "NODE_DISABLE_COLORS": "1",
    "ANSI_COLORS_DISABLED": "1",
}
EXPECTED = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
    "FORCE_COLOR": "3",
    "CLICOLOR": "1",
    "CLICOLOR_FORCE": "1",
}


def _color(env):
    return {key: env[key] for key in POLLUTED if key in env}


@pytest.mark.parametrize(
    "provider,entry",
    [
        ("codex", "fresh"),
        ("codex", "resume"),
        ("codex", "fork"),
        ("claude", "fresh"),
        ("claude", "fork"),
    ],
)
def test_interactive_provider_receives_color_and_sanitized_identity(
    tmp_path, monkeypatch, provider, entry
):
    repo = tmp_path / "repo"
    repo.mkdir()
    capture = tmp_path / "provider.json"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    stub = fake_bin / provider
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if '--version' in sys.argv: print('2.1.232 (Claude Code)'); sys.exit(0)\n"
        "if '--help' in sys.argv: print('--session-id <uuid>'); sys.exit(0)\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{'argv':sys.argv[1:], 'env':{{k:v for k,v in os.environ.items() if k in ('TERM', 'COLORTERM', 'NO_COLOR', 'FORCE_COLOR', 'CLICOLOR', 'CLICOLOR_FORCE', 'NODE_DISABLE_COLORS', 'ANSI_COLORS_DISABLED', 'CODEX_SESSION_ID', 'VIBECRAFTED_RESUME_CONTEXT', 'AICX_CONTINUITY_FILE', 'PYTHONNOUSERSITE', 'VIBECRAFTED_RUN_ID', 'VIBECRAFTED_WORKER_SESSION', 'PYTHONDONTWRITEBYTECODE')}}}}))\n"
    )
    stub.chmod(0o755)
    for key, value in POLLUTED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake_bin))
    monkeypatch.setenv("CODEX_SESSION_ID", "stale-parent")
    monkeypatch.setenv("VIBECRAFTED_RESUME_CONTEXT", "/stale-pack")
    monkeypatch.setenv("AICX_CONTINUITY_FILE", "/stale-aicx")
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv(
        "PYTHONPATH", str(tmp_path / "runtime/releases/fixture/python-site")
    )
    from vibecrafted_core.continuity import capabilities

    monkeypatch.setattr(
        capabilities,
        "probe",
        lambda *a, **kw: SimpleNamespace(
            state=capabilities.PROBE_CONFIRMED, detail="fixture"
        ),
    )
    if provider == "codex" and entry == "fork":
        from vibecrafted_core.continuity import native_fork

        def confirmed_fork(**kwargs):
            assert _color(kwargs["env"]) == EXPECTED
            assert kwargs["parent"] == "explicit-parent"
            return {
                "provider_session_id": "11111111-1111-4111-8111-111111111111",
                "native_child_session_id": "11111111-1111-4111-8111-111111111111",
                "native_identity_status": "confirmed",
            }

        monkeypatch.setattr(native_fork, "confirm_codex_native_fork", confirmed_fork)
    admission = (
        {"agent_session_id": "11111111-1111-4111-8111-111111111111"}
        if entry == "resume"
        else {}
    )
    result = spawn.launch_interactive_workspace(
        provider,
        "color task",
        "local-native",
        "bypass",
        repo,
        "unmetered",
        continuity="bare-fork" if entry == "fork" else "fresh",
        parent_session_id="explicit-parent" if entry == "fork" else "",
        admission=admission,
    )
    assert result == (
        1 if entry == "fork" and provider == "claude" else 0
    )  # stub emits no native fork ACK
    observed = json.loads(capture.read_text())
    assert _color(observed["env"]) == EXPECTED
    assert "CODEX_SESSION_ID" not in observed["env"]
    assert "PYTHONNOUSERSITE" not in observed["env"]
    if entry != "fork":
        assert "VIBECRAFTED_RESUME_CONTEXT" not in observed["env"]
        assert "AICX_CONTINUITY_FILE" not in observed["env"]
    if entry == "resume" or (entry == "fork" and provider == "codex"):
        assert observed["argv"][:2] == [
            "resume",
            "11111111-1111-4111-8111-111111111111",
        ]
    if entry == "fork" and provider == "claude":
        assert "--fork-session" in observed["argv"]
        assert (
            observed["argv"][observed["argv"].index("--resume") + 1]
            == "explicit-parent"
        )
    assert _color(os.environ) == POLLUTED


@pytest.mark.parametrize("runtime", ["terminal", "visible", "headless"])
@pytest.mark.parametrize("skill", ["workflow", "implement", "research"])
def test_transport_script_normalizes_frame_environment_only_for_visible(
    tmp_path, monkeypatch, runtime, skill
):
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: "/stub/vc-frame" if name == "vc-frame" else None,
    )
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="workflow",
        skill=skill,
        prompt="go",
        file="",
        runtime=runtime,
        root=str(tmp_path),
    )
    capture = tmp_path / "child.json"
    command = [
        sys.executable,
        "-c",
        f"import json,os;from pathlib import Path;Path({str(capture)!r}).write_text(json.dumps({{k:v for k,v in os.environ.items() if k in ('TERM', 'COLORTERM', 'NO_COLOR', 'FORCE_COLOR', 'CLICOLOR', 'CLICOLOR_FORCE', 'NODE_DISABLE_COLORS', 'ANSI_COLORS_DISABLED', 'CODEX_SESSION_ID', 'VIBECRAFTED_RESUME_CONTEXT', 'AICX_CONTINUITY_FILE', 'PYTHONNOUSERSITE', 'VIBECRAFTED_RUN_ID', 'VIBECRAFTED_WORKER_SESSION', 'PYTHONDONTWRITEBYTECODE')}}))",
    ]
    kwargs = {
        "run_id": "work-color",
        "prompt_path": tmp_path / "prompt.md",
        "report_path": tmp_path / "report.md",
        "transcript_path": tmp_path / "transcript.log",
        "meta_path": tmp_path / "meta.json",
        "canonical_report_dir": tmp_path,
        "artifact_slug": "color",
        "artifact_ts": "fixture",
        "artifact_suffix": "",
    }
    argv, transport, script = workflow._launch_transport_command(
        spec=spec,
        operator_session="existing-frame",
        dispatch_command=command,
        launch_dir=tmp_path,
        **kwargs,
    )
    env = {**os.environ, **POLLUTED}  # independent long-lived Frame parent
    result = subprocess.run(
        [str(script)] if script else argv,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(capture.read_text())
    assert _color(observed) == (POLLUTED if runtime == "headless" else EXPECTED)
    assert transport == ("headless" if runtime == "headless" else "vc-frame")
    if script:
        assert observed["VIBECRAFTED_RUN_ID"] == "work-color"
        assert observed["VIBECRAFTED_WORKER_SESSION"] == "existing-frame"
        assert observed["PYTHONDONTWRITEBYTECODE"] == "1"


def test_headless_color_intent_and_secret_gate_are_preserved():
    child = filter_headless_worker_env(
        {**POLLUTED, "CLAUDE_CODE_MESSAGING_TOKEN": "bus", "PORKBUN_API_KEY": "foreign"}
    )
    assert _color(child) == POLLUTED
    assert "CLAUDE_CODE_MESSAGING_TOKEN" not in child
    assert "PORKBUN_API_KEY" not in child


@pytest.mark.parametrize(
    "term,colorterm,level",
    [
        ("xterm-256color", "truecolor", "3"),
        ("xterm", "24bit", "3"),
        ("screen-256color", "", "2"),
        ("linux", "", "1"),
        ("dumb", "", "2"),
        ("", "", "2"),
    ],
)
def test_visible_color_preserves_terminal_capabilities(term, colorterm, level):
    from vibecrafted_core.env_allowlist import visible_color_environment

    source = {**POLLUTED, "TERM": term, "COLORTERM": colorterm}
    child = visible_color_environment(source)
    assert _color(child) == {
        **EXPECTED,
        "TERM": term if term and term != "dumb" else "xterm-256color",
        "COLORTERM": colorterm,
        "FORCE_COLOR": level,
    }
    assert source["NO_COLOR"] == "1"


def test_research_lanes_normalize_their_own_frame_parent(tmp_path, monkeypatch):
    stub = tmp_path / "interpreter"
    keys = tuple(POLLUTED)
    stub.write_text(
        f"#!{sys.executable}\nimport os,json,sys\nfrom pathlib import Path\nagent=sys.argv[sys.argv.index('--agent')+1]\nPath({str(tmp_path)!r},agent+'.json').write_text(json.dumps({{k:v for k,v in os.environ.items() if k in {keys!r}}}))\n"
    )
    stub.chmod(0o755)
    monkeypatch.setattr(workflow, "sys", SimpleNamespace(executable=str(stub)))
    selection = SimpleNamespace(
        agents=("codex", "claude", "grok"), lane_model=lambda *a: ""
    )
    scripts = workflow._write_research_lane_scripts(
        launch_dir=tmp_path,
        run_id="work-lanes",
        root=str(tmp_path),
        prompt_path=tmp_path / "prompt.md",
        report_path=tmp_path / "report.md",
        transcript_path=tmp_path / "transcript.log",
        meta_path=tmp_path / "meta.json",
        canonical_report_dir=tmp_path,
        artifact_slug="color",
        artifact_ts="fixture",
        artifact_suffix="",
        research_selection=selection,
        worker_session="frame-parent",
    )
    assert len(scripts) == 3
    for agent, script in scripts.items():
        result = subprocess.run(
            [str(script)],
            env={**os.environ, **POLLUTED},
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads((tmp_path / (agent + ".json")).read_text()) == EXPECTED


def test_visible_request_without_frame_keeps_headless_fallback_policy(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(workflow.shutil, "which", lambda name: None)
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="workflow",
        skill="workflow",
        prompt="go",
        file="",
        runtime="visible",
        root=str(tmp_path),
    )
    command = [sys.executable, "-c", "pass"]
    argv, transport, script = workflow._launch_transport_command(
        spec=spec,
        run_id="work-fallback",
        operator_session="",
        dispatch_command=command,
        launch_dir=tmp_path,
        prompt_path=tmp_path / "prompt",
        report_path=tmp_path / "report",
        transcript_path=tmp_path / "transcript",
        meta_path=tmp_path / "meta",
        canonical_report_dir=tmp_path,
        artifact_slug="",
        artifact_ts="",
        artifact_suffix="",
    )
    assert argv == command and transport == "headless" and script is None


def test_headless_supervisor_child_keeps_no_color_and_drops_bus(tmp_path, monkeypatch):
    for key, value in POLLUTED.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CLAUDE_CODE_MESSAGING_TOKEN", "fixture-bus")
    monkeypatch.setenv("PORKBUN_API_KEY", "fixture-secret")
    capture = tmp_path / "headless.json"
    keys = (*POLLUTED, "CLAUDE_CODE_MESSAGING_TOKEN", "PORKBUN_API_KEY")
    script = f"import json,os;from pathlib import Path;Path({str(capture)!r}).write_text(json.dumps({{k:v for k,v in os.environ.items() if k in {keys!r}}}))"
    handle = spawn.Supervisor().spawn(
        "command",
        "",
        skill="implement",
        mode="headless",
        root=tmp_path,
        command=[sys.executable, "-c", script],
        run_id="work-headless-color",
    )
    assert handle.wait(timeout=30) == 0
    assert json.loads(capture.read_text()) == POLLUTED
