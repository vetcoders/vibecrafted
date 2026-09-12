from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from vibecrafted_core import control_plane, process_control, spawn, trust, workflow
from vibecrafted_core.settlement import TrustReceiptV1

_NATIVE_RESUME_CLAIM_SCRIPT = r"""
import json
import os
import time

from vibecrafted_core import workflow
from vibecrafted_core.run_mutation import run_mutation_locks

key = os.environ["NATIVE_RESUME_TEST_KEY"]
parent = os.environ["NATIVE_RESUME_TEST_PARENT"]
agent = os.environ.get("NATIVE_RESUME_TEST_AGENT", "codex")
meta = {"attempt": 1}
run = {"attempt": 1}
with run_mutation_locks(
    workflow.control_plane_home(),
    run_id=parent,
    resume_root=parent,
    idempotency_key=key,
):
    record, created = workflow._claim_native_resume_idempotency(
        key=key,
        parent_run_id=parent,
        agent=agent,
        agent_session_id=f"{agent}-native-id",
        parent_runtime_session_id="runtime-parent-id",
        parent_meta=meta,
        parent_run=run,
        settlement_revision=7,
        trust_receipt_id=os.environ.get("NATIVE_RESUME_TEST_RECEIPT", ""),
    )
    print(json.dumps({"created": created, "record": record}), flush=True)
    if os.environ.get("NATIVE_RESUME_TEST_CRASH") == "1":
        os._exit(91)
    time.sleep(float(os.environ.get("NATIVE_RESUME_TEST_HOLD", "0")))
"""


def _source_dir(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    root.mkdir(parents=True)
    return root


def _write_run_meta(home: Path, payload: dict[str, object]) -> Path:
    run_dir = home / "artifacts" / "Vetcoders" / "vibecrafted" / "2026_0611" / "reports"
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"{payload['run_id']}.meta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_normalize_launch_spec_requires_prompt_or_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Launch requires"):
        workflow.normalize_launch_spec({"skill": "workflow"}, tmp_path)


def test_normalize_launch_spec_prune_without_input_uses_discovery_prompt(
    tmp_path: Path,
) -> None:
    spec = workflow.normalize_launch_spec(
        {"skill": "prune", "agent": "claude", "root": str(tmp_path)},
        tmp_path,
    )

    assert spec.skill == "prune"
    assert spec.agent == "claude"
    assert spec.file == ""
    assert "Repository health / prune ACTION run." in spec.prompt
    assert (
        "Never `--no-verify`. Never `git push` — push is an operator button."
        in spec.prompt
    )
    assert "Mode: DISCOVER -> PROVE -> CUT -> COMMIT." in spec.prompt


def test_normalize_launch_spec_uses_registry_for_marbles_defaults(
    tmp_path: Path,
) -> None:
    spec = workflow.normalize_launch_spec(
        {"skill": "marbles", "agent": "codex", "root": str(tmp_path)},
        tmp_path,
    )

    assert spec.prompt == ""
    assert spec.count == 3
    assert spec.depth == 3


def test_normalize_launch_spec_keeps_justdo_as_own_skill(tmp_path: Path) -> None:
    spec = workflow.normalize_launch_spec(
        {"skill": "justdo", "agent": "codex", "prompt": "ship"},
        tmp_path,
    )

    assert spec.skill == "justdo"
    assert spec.agent == "codex"


def test_normalize_launch_spec_reads_model_from_brief_frontmatter(
    tmp_path: Path,
) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text(
        "---\nmodel: opus\ntitle: sample cut\n---\n\ndo the work\n",
        encoding="utf-8",
    )

    spec = workflow.normalize_launch_spec(
        {"skill": "implement", "agent": "claude", "file": str(brief)},
        tmp_path,
    )

    assert spec.model == "opus"


def test_normalize_launch_spec_model_flag_wins_over_brief_frontmatter(
    tmp_path: Path,
) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("---\nmodel: opus\n---\n\ndo the work\n", encoding="utf-8")

    spec = workflow.normalize_launch_spec(
        {
            "skill": "implement",
            "agent": "claude",
            "file": str(brief),
            "model": "claude-sonnet-5",
        },
        tmp_path,
    )

    assert spec.model == "claude-sonnet-5"


def test_normalize_launch_spec_no_model_without_frontmatter(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("do the work\n", encoding="utf-8")

    spec = workflow.normalize_launch_spec(
        {"skill": "implement", "agent": "claude", "file": str(brief)},
        tmp_path,
    )

    assert spec.model == ""


def test_artifact_slug_skips_boilerplate_research_words() -> None:
    assert (
        workflow._artifact_slug(
            "perform the research workflow on ACP versus native cli agent", "fallback"
        )
        == "acp-versus-native"
    )


def test_artifact_org_repo_reads_git_config_with_duplicate_options(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        '[branch "release"]\n'
        "\tvscode-merge-base = a\n"
        "\tvscode-merge-base = b\n"
        '[remote "origin"]\n'
        "\turl = https://github.com/vetcoders/vibecrafted.git\n",
        encoding="utf-8",
    )

    assert workflow._artifact_org_repo(tmp_path) == ("vetcoders", "vibecrafted")


def test_launch_workflow_returns_pid_and_logs_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_CLAIM_DIGEST", "ambient-wrong")
    source = _source_dir(tmp_path)
    spec = workflow.normalize_launch_spec(
        {"skill": "workflow", "agent": "claude", "prompt": "go"},
        source,
    )

    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import os, sys; "
                "assert sys.stdin.read() == Path(os.environ['VIBECRAFTED_PROMPT_PATH']).read_text(); "
                "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text('ok\\n')"
            ),
        ],
    )
    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert isinstance(payload["pid"], int)
    assert payload["prompt_file"]
    assert ".vibecrafted/artifacts/local/src/" in payload["report"]
    assert "/reports/workflow/" in payload["report"]
    report_name = Path(payload["report"]).name
    assert "_go_" in report_name
    assert payload["run_id"].replace(".", "-") in report_name
    assert report_name.endswith("_report.md")
    assert ".vibecrafted/control_plane/runtime_runs/" in payload["transcript"]
    assert ".vibecrafted/control_plane/runtime_runs/" in payload["meta"]
    assert ".vibecrafted/control_plane/runtime_runs/" in payload["prompt_file"]
    assert payload["workflow"] == {
        "id": "workflow",
        "phase": "write",
        "can_modify_code": True,
        "runtime_kind": "direct_agent",
        "tooling": ["vc-init", "vc-research", "vc-justdo"],
        "lifecycle_order": 40,
    }
    assert "go" not in payload["worker_command"]
    log_lines = Path(payload["launch_log"]).read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line).get("event") == "spawned" for line in log_lines)
    assert payload["control_plane"] == {
        "sync": "deferred",
        "run_id": payload["run_id"],
    }


def test_launch_workflow_preseeds_machine_owned_claim_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    source = _source_dir(tmp_path)
    digest = "9e0d59e1dc48bc42"
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="close the bound mission",
        file="",
        runtime="headless",
        root=str(source),
        lifecycle_state_path="/control-plane/lifecycle/state.json",
        claim_digest=digest,
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [sys.executable, "-c", "pass"],
    )

    payload = workflow.launch_workflow(spec, source)

    truth = workflow.await_launch_truth(
        payload,
        timeout_seconds=10,
        interval_seconds=0.05,
        require_transcript_output=False,
    )
    assert truth["completed"] is True
    meta = json.loads(Path(payload["meta"]).read_text(encoding="utf-8"))
    assert meta["run_id"] == payload["run_id"]
    assert meta["runtime"] == "headless"
    assert meta["claim_digest"] == digest
    assert f"claim_digest: {digest}" in Path(payload["report"]).read_text(
        encoding="utf-8"
    )
    assert "ambient-wrong" not in Path(payload["report"]).read_text(encoding="utf-8")
    exports = workflow._runtime_script_exports(
        run_id=payload["run_id"],
        prompt_path=Path(payload["prompt_file"]),
        report_path=Path(payload["report"]),
        transcript_path=Path(payload["transcript"]),
        meta_path=Path(payload["meta"]),
        agent="codex",
        skill="implement",
        runtime="terminal",
        claim_digest=digest,
    )
    assert exports["VIBECRAFTED_CLAIM_DIGEST"] == digest


def test_launch_workflow_never_runs_global_sync_after_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    source = _source_dir(tmp_path)
    spec = workflow.normalize_launch_spec(
        {"skill": "workflow", "agent": "claude", "prompt": "go"}, source
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [sys.executable, "-c", "pass"],
    )
    monkeypatch.setattr(
        workflow,
        "sync_state",
        lambda: pytest.fail("launch acknowledgement must not acquire board-sync lock"),
    )

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert payload["control_plane"]["sync"] == "deferred"


def test_launch_workflow_records_skipped_model_override_for_unknown_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    source = _source_dir(tmp_path)
    spec = workflow.WorkflowLaunchSpec(
        agent="agy",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="headless",
        root=str(source),
        model="gemini-pro",
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import os; "
                "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text('ok\\n')"
            ),
        ],
    )

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert (
        payload["model_requested"] == "gemini-pro"
    )  # Google family label preserved for agy telemetry
    assert payload["model_override_supported"] is False
    assert payload["model_override_skipped"] is True
    assert payload["model_override_skip_reason"] == "unsupported_agent_model_flag"
    assert "gemini-pro" not in payload["worker_command"]


def test_launch_workflow_records_failure_event_when_spawn_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    source = _source_dir(tmp_path)
    spec = workflow.normalize_launch_spec(
        {"skill": "workflow", "agent": "claude", "prompt": "go"},
        source,
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [sys.executable, "-c", "pass"],
    )

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("no such dispatcher binary")

    monkeypatch.setattr(workflow.subprocess, "Popen", _boom)

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow,
        "append_event",
        lambda **kwargs: events.append(kwargs),
    )

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is False
    assert "no such dispatcher binary" in payload["error"]
    # A terminal "failed" event must be recorded — otherwise the earlier
    # "launch accepted" (state="created") strands the run as a phantom active
    # run that reconciliation never resolves.
    states = [event["payload"].get("state") for event in events]
    assert "failed" in states


def test_launch_workflow_reports_read_phase_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    spec = workflow.normalize_launch_spec(
        {"skill": "dou", "agent": "codex", "prompt": "audit launch readiness"},
        tmp_path,
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import os; "
                "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text('ok\\n')"
            ),
        ],
    )

    payload = workflow.launch_workflow(spec, tmp_path)

    assert payload["workflow"]["phase"] == "read"
    assert payload["workflow"]["can_modify_code"] is False
    assert payload["workflow"]["tooling"] == ["vc-init", "vc-intents", "vc-loctree"]


def test_launch_workflow_keeps_dispatcher_launch_even_if_worker_command_is_bad(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="workflow",
        skill="workflow",
        prompt="go",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )

    def _missing_command(*_args: Any, **_kwargs: Any) -> list[str]:
        return ["definitely-missing-vibecrafted-binary"]

    monkeypatch.setattr(workflow, "build_launch_command", _missing_command)
    payload = workflow.launch_workflow(spec, tmp_path)

    assert payload["accepted"] is True
    assert payload["run_id"]
    assert payload["worker_command"] == ["definitely-missing-vibecrafted-binary"]


def test_resolve_agent_command_uses_canonical_path_not_inherited_path(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    provider_bin = home / ".local/bin"
    provider_bin.mkdir(parents=True)
    executable = provider_bin / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)

    resolved = spawn._resolve_agent_command(
        "claude",
        ["claude", "-p"],
        {"HOME": str(home), "PATH": str(tmp_path / "rogue")},
    )

    assert resolved == [str(executable), "-p"]


def test_launch_workflow_rejects_missing_provider_before_acceptance_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="workflow",
        skill="workflow",
        prompt="go",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    monkeypatch.setattr(workflow, "_stdin_command", lambda _agent: ["claude", "-p"])

    def _missing(*_args: Any, **_kwargs: Any) -> list[str]:
        raise FileNotFoundError("provider executable 'claude' not found")

    monkeypatch.setattr(workflow, "_resolve_agent_command", _missing)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow, "append_event", lambda **event: events.append(event))

    payload = workflow.launch_workflow(spec, tmp_path)

    assert payload["accepted"] is False
    assert "provider executable 'claude' not found" in payload["error"]
    assert events == []


def test_build_launch_command_never_delegates_to_legacy_shell_runtime() -> None:
    """RC 3.2.0 polarize contract: vibecrafted-core is the single runtime.

    build_launch_command must spawn through the Python runtime
    (vibecrafted_core.workflow_runtime) or an agent stdin command — never a
    legacy runtime/scripts/*_spawn.sh launcher or the bash deck. This locks the
    split-brain shut so it cannot re-grow.
    """
    for skill, agent in (
        ("marbles", "codex"),
        ("polarize", "codex"),
        ("research", "claude"),
        ("implement", "codex"),
    ):
        spec = workflow.WorkflowLaunchSpec(
            agent=agent,
            mode=skill,
            skill=skill,
            prompt="x",
            file="",
            runtime="headless",
            root="/tmp",
        )
        cmd = workflow.build_launch_command(spec, "/tmp", prompt_file="/tmp/p.md")
        joined = " ".join(cmd)
        assert "_spawn.sh" not in joined, f"{skill} delegates to legacy shell: {joined}"
        assert "scripts/vibecrafted" not in joined, f"{skill} calls the deck: {joined}"

    marbles = workflow.build_launch_command(
        workflow.WorkflowLaunchSpec(
            agent="codex",
            mode="marbles",
            skill="marbles",
            prompt="x",
            file="",
            runtime="headless",
            root="/tmp",
        ),
        "/tmp",
        prompt_file="/tmp/p.md",
    )
    assert marbles[0] == sys.executable
    assert "vibecrafted_core.workflow_runtime" in marbles


def test_build_launch_command_applies_stage_model_flags_by_runner(
    tmp_path: Path,
) -> None:
    claude = workflow.build_launch_command(
        workflow.WorkflowLaunchSpec(
            agent="claude",
            mode="implement",
            skill="implement",
            prompt="x",
            file="",
            runtime="headless",
            root=str(tmp_path),
            model="opus",
        ),
        tmp_path,
        prompt_file=tmp_path / "p.md",
    )
    assert claude[:3] == ["claude", "--model", "opus"]

    codex = workflow.build_launch_command(
        workflow.WorkflowLaunchSpec(
            agent="codex",
            mode="implement",
            skill="implement",
            prompt="x",
            file="",
            runtime="headless",
            root=str(tmp_path),
            model="gpt-5.5",
        ),
        tmp_path,
        prompt_file=tmp_path / "p.md",
    )
    assert codex[:4] == ["codex", "exec", "-m", "gpt-5.5"]

    agy = workflow.build_launch_command(
        workflow.WorkflowLaunchSpec(
            agent="agy",
            mode="implement",
            skill="implement",
            prompt="x",
            file="",
            runtime="headless",
            root=str(tmp_path),
            model="gemini-pro",  # model name may be Google family even on agy
        ),
        tmp_path,
        prompt_file=tmp_path / "p.md",
    )
    assert "gemini-pro" not in agy
    assert "--model" not in agy
    assert "-m" not in agy

    marbles = workflow.build_launch_command(
        workflow.WorkflowLaunchSpec(
            agent="codex",
            mode="marbles",
            skill="marbles",
            prompt="x",
            file="",
            runtime="headless",
            root=str(tmp_path),
            model="gpt-5.5",
        ),
        tmp_path,
        prompt_file=tmp_path / "p.md",
    )
    assert marbles[marbles.index("--model") + 1] == "gpt-5.5"


def test_terminal_runtime_launches_worker_in_vc_frame_tab(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.delenv("VIBECRAFTED_OPERATOR_SESSION", raising=False)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "operator-live")
    source = _source_dir(tmp_path)
    vc_frame = tmp_path / "bin" / "vc-frame"
    vc_frame.parent.mkdir()
    vc_frame.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="terminal",
        root=str(tmp_path),
    )
    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: ["codex", "exec"],
    )
    monkeypatch.setattr(
        workflow,
        "_resolve_agent_command",
        lambda _agent, command, _env: list(command),
    )
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: str(vc_frame) if name == "vc-frame" else None,
    )
    monkeypatch.setattr(workflow, "_vc_frame_session_active", lambda _vc, _s: True)

    captured: dict[str, Any] = {}

    def fake_host_action(
        command: list[str], *, operator_session: str, timeout: float = 30.0
    ) -> workflow._HostActionResult:
        captured["command"] = command
        captured["operator_session"] = operator_session
        return workflow._HostActionResult(True, 4242, "", "", False)

    monkeypatch.setattr(workflow, "_vc_frame_run_host_action", fake_host_action)

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert payload["pid"] == 4242
    assert payload["transport"] == "vc-frame"
    from vibecrafted_core.workspace_catalog import resolve_worker_host_session

    worker_host = resolve_worker_host_session(root=str(tmp_path), env=dict(os.environ))
    assert worker_host.endswith("-w")
    assert " " not in worker_host
    assert worker_host != f"{tmp_path.name}-w" or "-" in worker_host
    assert captured["command"][:5] == [
        str(vc_frame),
        "--session",
        worker_host,
        "action",
        "new-tab",
    ]
    assert payload["operator_session"] == worker_host
    assert "--name" in captured["command"]
    assert (
        captured["command"][captured["command"].index("--name") + 1]
        == payload["run_id"]
    )
    assert "--cwd" in captured["command"]
    assert captured["command"][captured["command"].index("--cwd") + 1] == str(tmp_path)
    script = Path(captured["command"][-1])
    assert script.is_file()
    script_body = script.read_text(encoding="utf-8")
    assert "vibecrafted_core.dispatcher" in script_body
    assert f"export PYTHONPATH={workflow._core_package_root()}" in script_body
    assert "export PYTHONDONTWRITEBYTECODE=1" in script_body
    assert (
        f"export VIBECRAFTED_WORKER_SESSION={shlex.quote(worker_host)}" in script_body
    )
    assert (
        f"export VIBECRAFTED_OPERATOR_SESSION={shlex.quote(worker_host)}" in script_body
    )
    assert "--tee-output" in script_body
    assert "--quiet" in script_body
    assert "--json" not in script_body
    assert payload["command"] == captured["command"]
    assert payload["dispatch_command"] != payload["command"]
    assert payload["control"].endswith(f"{payload['run_id']}.json")


def test_headless_launch_opens_live_bucket_viewer_and_stamps_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cut C axis (a): the viewer lands in ``Live runs`` and stamps the origin.

    The worker itself must stay detached headless — the LIVE tab is a viewer,
    so it carries the run's transcript, never the dispatcher.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_LIVE_VIEWER", "1")
    source = _source_dir(tmp_path)
    vc_frame = tmp_path / "bin" / "vc-frame"
    vc_frame.parent.mkdir()
    vc_frame.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    monkeypatch.setattr(
        workflow, "_stdin_command", lambda _agent: [sys.executable, "-c", "pass"]
    )
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name, path=None: str(vc_frame) if name == "vc-frame" else None,
    )

    captured: dict[str, Any] = {}

    def fake_host_action(
        command: list[str], *, operator_session: str, timeout: float = 45.0
    ) -> workflow._HostActionResult:
        captured["command"] = command
        captured["operator_session"] = operator_session
        return workflow._HostActionResult(True, 909, "", "", False)

    monkeypatch.setattr(workflow, "_vc_frame_run_host_action", fake_host_action)

    payload = workflow.launch_workflow(spec, source)
    run_id = payload["run_id"]

    # The worker never bought a tab: headless transport, no worker host.
    assert payload["accepted"] is True
    assert payload["transport"] == "headless"
    assert payload["operator_session"] == ""

    # The viewer did, and it went to the LIVE bucket under the run's own name.
    assert captured["operator_session"] == "Live runs"
    assert captured["command"][:6] == [
        str(vc_frame),
        "--session",
        "Live runs",
        "action",
        "new-tab",
        "--name",
    ]
    assert captured["command"][6] == run_id
    viewer_script = Path(captured["command"][-1])
    assert viewer_script.is_file()
    body = viewer_script.read_text(encoding="utf-8")
    assert 'exec tail -n +1 -F "$human_transcript"' in body
    assert 'exec tail -n +1 -F "$transcript"' not in body
    assert "transcript.human.log" in body
    assert payload["transcript"] not in body
    assert f"vibecrafted observe codex --run-id {run_id}" in body
    assert f"vibecrafted codex observe --run-id {run_id}" not in body
    # A viewer tails; it must never carry the dispatcher itself.
    assert "vibecrafted_core.dispatcher" not in body

    assert payload["live_viewer"]["status"] == "opened"
    assert payload["live_viewer"]["session"] == "Live runs"
    assert payload["live_viewer"]["tab"] == run_id

    # The stamp is what lets the existing triage hook empty this bucket later.
    meta = json.loads(Path(payload["meta"]).read_text(encoding="utf-8"))
    assert meta["origin_session"] == "Live runs"
    assert meta["origin_tab"] == run_id
    assert meta["live_viewer"]["status"] == "opened"


def test_test_mode_never_opens_live_viewer() -> None:
    """Hermetic tests must not mutate the operator's real vc-frame surface."""
    assert (
        workflow._live_viewer_enabled(
            {
                "VIBECRAFTED_TEST_MODE": "1",
                "VIBECRAFTED_LIVE_VIEWER": "1",
            }
        )
        is False
    )


def test_headless_launch_fails_open_when_vc_frame_binary_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cut C axis (b): no binary → receipt, headless run continues, no origin.

    Fail-open mirrors triage: the viewer is a convenience on top of a launch
    that already succeeded, so it degrades to a recorded receipt and never an
    exception. Crucially it must not stamp an origin it did not create —
    triage would then try to capture and close a tab that never existed.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_LIVE_VIEWER", "1")
    source = _source_dir(tmp_path)

    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )
    monkeypatch.setattr(
        workflow, "_stdin_command", lambda _agent: [sys.executable, "-c", "pass"]
    )
    monkeypatch.setattr(workflow.shutil, "which", lambda name, path=None: None)

    def refuse_host_action(*args: Any, **kwargs: Any) -> workflow._HostActionResult:
        raise AssertionError("no binary must never reach a vc-frame host action")

    monkeypatch.setattr(workflow, "_vc_frame_run_host_action", refuse_host_action)

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert isinstance(payload["pid"], int)
    assert payload["live_viewer"] == {
        "schema": workflow.LIVE_VIEWER_SCHEMA,
        "status": "skipped",
        "reason": "no_binary",
        "session": "Live runs",
        "tab": payload["run_id"],
        "command": [],
    }

    meta = json.loads(Path(payload["meta"]).read_text(encoding="utf-8"))
    assert meta["live_viewer"]["reason"] == "no_binary"
    assert not str(meta.get("origin_session") or "").strip()
    assert not str(meta.get("origin_tab") or "").strip()

    log_lines = Path(payload["launch_log"]).read_text(encoding="utf-8").splitlines()
    receipts = [
        json.loads(line)
        for line in log_lines
        if json.loads(line).get("event") == "live_viewer"
    ]
    assert receipts and receipts[0]["status"] == "skipped"


def test_terminal_runtime_resurrects_missing_host_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """G3: missing host → one create-background + retry, not silent headless."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "host-session")
    source = _source_dir(tmp_path)
    vc_frame = tmp_path / "bin" / "vc-frame"
    vc_frame.parent.mkdir()
    state = tmp_path / "vc_state"
    state.mkdir()
    vc_frame.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f'STATE="{state}"',
                'args=("$@")',
                'printf -- "--CALL--\\n" >> "$STATE/calls"',
                'printf "%s\\n" "$@" >> "$STATE/calls"',
                'if [[ "${1:-}" == "list-sessions" ]]; then',
                '  if [[ -f "$STATE/live" ]]; then printf "host-session [Created]\\n"; fi',
                "  exit 0",
                "fi",
                'if [[ "${1:-}" == "attach" && "${2:-}" == "--create-background" ]]; then',
                '  touch "$STATE/live"',
                '  printf "created\\n" >> "$STATE/create"',
                "  exit 0",
                "fi",
                # action path: --session NAME action ...
                'if [[ "${1:-}" == "--session" ]]; then',
                '  if [[ ! -f "$STATE/live" ]]; then',
                '    printf "Session \'%s\' not found\\n" "${2:-}" >&2',
                "    exit 1",
                "  fi",
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)

    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="terminal",
        root=str(tmp_path),
    )
    monkeypatch.setattr(workflow, "_stdin_command", lambda _agent: ["codex", "exec"])
    monkeypatch.setattr(
        workflow,
        "_resolve_agent_command",
        lambda _agent, command, _env: list(command),
    )
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: str(vc_frame) if name == "vc-frame" else None,
    )

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert payload["transport"] == "vc-frame"
    assert (state / "create").is_file()
    calls = (state / "calls").read_text(encoding="utf-8")
    assert "attach" in calls
    assert "--create-background" in calls
    assert calls.count("--CALL--") >= 2  # action + list-sessions + create etc.


def test_terminal_runtime_host_double_fail_marks_failed_with_last_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """G3: create-background also fails → state=failed + last_error immediately."""
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "ghost-host")
    source = _source_dir(tmp_path)
    vc_frame = tmp_path / "bin" / "vc-frame"
    vc_frame.parent.mkdir()
    vc_frame.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nif [[ "${1:-}" == "list-sessions" ]]; then exit 0; fi\nif [[ "${1:-}" == "attach" ]]; then\n  printf "attach failed for %s\\n" "${3:-}" >&2\n  exit 1\nfi\nif [[ "${1:-}" == "--session" ]]; then\n  printf "Session \'%s\' not found\\n" "${2:-}" >&2\n  exit 1\nfi\nexit 1'
        + "\n",
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)

    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="implement",
        skill="implement",
        prompt="go",
        file="",
        runtime="terminal",
        root=str(tmp_path),
    )
    monkeypatch.setattr(workflow, "_stdin_command", lambda _agent: ["codex", "exec"])
    monkeypatch.setattr(
        workflow,
        "_resolve_agent_command",
        lambda _agent, command, _env: list(command),
    )
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: str(vc_frame) if name == "vc-frame" else None,
    )
    # Force the operator session name the stub fails on.
    monkeypatch.setattr(
        workflow,
        "_effective_operator_session",
        lambda **_kwargs: "ghost-host",
    )

    events: list[dict[str, Any]] = []

    def _capture_event(**kwargs: Any) -> None:
        events.append(kwargs)

    monkeypatch.setattr(workflow, "append_event", _capture_event)
    monkeypatch.setattr(workflow, "sync_state", lambda: {"sync": "test"})

    t0 = time.monotonic()
    payload = workflow.launch_workflow(spec, source)
    elapsed = time.monotonic() - t0

    assert payload["accepted"] is False
    assert (
        "not found" in (payload.get("last_error") or payload.get("error") or "").lower()
        or "create-background"
        in (payload.get("last_error") or payload.get("error") or "").lower()
    )
    assert elapsed < 5.0
    states = [e.get("payload", {}).get("state") for e in events]
    assert "failed" in states
    failed = next(e for e in events if e.get("payload", {}).get("state") == "failed")
    err = str(
        failed["payload"].get("last_error") or failed["payload"].get("error") or ""
    )
    assert err
    assert "not found" in err.lower() or "create-background" in err.lower()


def test_vc_frame_session_active_parses_list_sessions(tmp_path: Path) -> None:
    vc_frame = tmp_path / "vc-frame"
    vc_frame.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "list-sessions" ]; then '
        'printf "\\033[32;1mvc-frame\\033[m [Created 2h ago]\\n"; '
        'printf "\\033[32;1maicx\\033[m [Created 3h ago]\\n"; fi\n',
        encoding="utf-8",
    )
    vc_frame.chmod(0o755)

    assert workflow._vc_frame_session_active(str(vc_frame), "vc-frame") is True
    assert workflow._vc_frame_session_active(str(vc_frame), "aicx") is True
    assert workflow._vc_frame_session_active(str(vc_frame), "vibecrafted") is False
    assert workflow._vc_frame_session_active(str(vc_frame), "") is False


def test_effective_operator_session_g7_worker_host_routing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # G7 + Cut A: worker host is override → workspace-bound host.
    # Bare repo basename remains the operator interactive card and is never a
    # target. Liveness is G3's job; resolution always returns a host.
    from vibecrafted_core import workspace_catalog as wc

    vib_home = tmp_path / ".vibecrafted"
    vib_home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(vib_home))
    monkeypatch.delenv("VIBECRAFTED_WORKER_SESSION", raising=False)

    root_dir = tmp_path / "work" / "vibecrafted"
    root_foo_dir = tmp_path / "work" / "foo"
    root_dir.mkdir(parents=True)
    root_foo_dir.mkdir(parents=True)
    root = str(root_dir)
    root_foo = str(root_foo_dir)

    ws = wc.create_workspace(root=root, display_label="vibecrafted", select=True)
    ws_foo = wc.create_workspace(root=root_foo, display_label="foo", select=False)
    expected = wc.worker_host_session_name(
        workspace_id=ws.workspace_id, display_label="vibecrafted"
    )
    expected_foo = wc.worker_host_session_name(
        workspace_id=ws_foo.workspace_id, display_label="foo"
    )
    assert expected != "vibecrafted workers"
    assert expected_foo != "foo workers"
    assert expected.endswith("-w")
    assert expected_foo.endswith("-w")
    assert " " not in expected

    env_base = {"VIBECRAFTED_HOME": str(vib_home)}

    # 1. Outside any pane → workspace-bound host (not bare basename).
    assert (
        workflow._effective_operator_session(root=root, run_id="r1", env=dict(env_base))
        == expected
    )

    # 2. Ambient VIBECRAFTED_OPERATOR_SESSION (human seat) is ignored as target.
    assert (
        workflow._effective_operator_session(
            root=root,
            run_id="r2",
            env={**env_base, "VIBECRAFTED_OPERATOR_SESSION": "vc-workspace"},
        )
        == expected
    )

    # 3. Dispatch from a seat named unlike the repo → still workspace-bound host.
    assert (
        workflow._effective_operator_session(
            root=root_foo,
            run_id="r3",
            env={**env_base, "VC_FRAME_SESSION_NAME": "operator-X"},
        )
        == expected_foo
    )

    # 4. Seat name == repo basename → still workspace-bound, no special case.
    assert (
        workflow._effective_operator_session(
            root=root,
            run_id="r4",
            env={**env_base, "VC_FRAME_SESSION_NAME": "vibecrafted"},
        )
        == expected
    )

    # 5. Legacy ZELLIJ_SESSION_NAME seat is equally irrelevant to the host.
    assert (
        workflow._effective_operator_session(
            root=root_foo,
            run_id="r5",
            env={**env_base, "ZELLIJ_SESSION_NAME": "foo"},
        )
        == expected_foo
    )

    # 6. Explicit worker-session override wins over every derived name.
    assert (
        workflow._effective_operator_session(
            root=root,
            run_id="r6",
            env={
                **env_base,
                "VC_FRAME_SESSION_NAME": "vibecrafted",
                "VIBECRAFTED_WORKER_SESSION": "bar",
            },
        )
        == "bar"
    )


def test_research_terminal_runtime_uses_vc_frame_research_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    monkeypatch.delenv("VIBECRAFTED_OPERATOR_SESSION", raising=False)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "operator-live")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    source = _source_dir(tmp_path)
    vc_frame = tmp_path / "bin" / "vc-frame"
    vc_frame.parent.mkdir()
    vc_frame.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    spec = workflow.normalize_launch_spec(
        {
            "skill": "research",
            "agent": "claude",
            "prompt": "map it",
            "runtime": "terminal",
            "root": str(tmp_path),
        },
        source,
    )
    digest = "9e0d59e1dc48bc42"
    spec = workflow.WorkflowLaunchSpec(**{**spec.to_payload(), "claim_digest": digest})
    monkeypatch.setattr(
        workflow.shutil,
        "which",
        lambda name: str(vc_frame) if name == "vc-frame" else None,
    )
    monkeypatch.setattr(workflow, "_vc_frame_session_active", lambda _vc, _s: True)

    captured: dict[str, Any] = {}

    def fake_host_action(
        command: list[str], *, operator_session: str, timeout: float = 30.0
    ) -> workflow._HostActionResult:
        captured["command"] = command
        captured["kwargs"] = {"operator_session": operator_session}
        return workflow._HostActionResult(True, 4243, "", "", False)

    monkeypatch.setattr(workflow, "_vc_frame_run_host_action", fake_host_action)

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    runtime_bucket = (
        tmp_path / ".vibecrafted" / "control_plane" / "runtime_runs" / payload["run_id"]
    )
    assert Path(payload["report"]).parent.name == "research"
    assert "/artifacts/local/" in payload["report"]
    assert "/reports/research/" in payload["report"]
    assert Path(payload["transcript"]).parent == runtime_bucket
    assert Path(payload["meta"]).parent == runtime_bucket
    assert Path(payload["prompt_file"]).parent == runtime_bucket
    # Host-action path does not re-Popen; report path is still on the receipt.
    assert payload["report"]
    assert Path(payload["report"]).parent.name == "research"
    command = captured["command"]
    from vibecrafted_core.workspace_catalog import resolve_worker_host_session

    worker_host = resolve_worker_host_session(root=str(tmp_path), env=dict(os.environ))
    assert command[:5] == [
        str(vc_frame),
        "--session",
        worker_host,
        "action",
        "new-tab",
    ]
    assert "--layout" in command
    layout = Path(command[command.index("--layout") + 1])
    assert layout.is_file()
    layout_body = layout.read_text(encoding="utf-8")
    assert 'pane name="synthesis"' in layout_body
    assert 'pane name="claude"' in layout_body
    assert 'pane name="codex"' in layout_body
    assert 'pane name="agy"' in layout_body or 'pane name="codex"' in layout_body
    launch_dir = Path(payload["command_script"]).parent
    lane_bodies = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(launch_dir.glob(f"{payload['run_id']}-research-*.sh"))
    )
    assert "research-lane --agent claude" in lane_bodies
    assert "research-lane --agent codex" in lane_bodies
    assert (
        "research-lane --agent agy" in lane_bodies
        or "research-lane --agent codex" in lane_bodies
    )
    assert "export VIBECRAFTED_RUN_ID=" in lane_bodies
    assert "export VIBECRAFTED_REPORT_PATH=" in lane_bodies
    assert "export VIBECRAFTED_TRANSCRIPT_PATH=" in lane_bodies
    assert "export VIBECRAFTED_META_PATH=" in lane_bodies
    assert "export VIBECRAFTED_PROMPT_PATH=" in lane_bodies
    assert f"export VIBECRAFTED_CLAIM_DIGEST={digest}" in lane_bodies
    assert "export VIBECRAFTED_CANONICAL_REPORT_DIR=" in lane_bodies
    assert "export VIBECRAFTED_ARTIFACT_SLUG=map-it" in lane_bodies
    assert (
        f"export VIBECRAFTED_WORKER_SESSION={shlex.quote(worker_host)}" in lane_bodies
    )
    assert (
        f"export VIBECRAFTED_OPERATOR_SESSION={shlex.quote(worker_host)}" in lane_bodies
    )
    assert (
        f"export VIBECRAFTED_ARTIFACT_TS={workflow.time.strftime('%Y-%m-%d')}"
        in lane_bodies
    )
    assert "export VIBECRAFTED_TEE_OUTPUT=1" in lane_bodies
    assert "${VIBECRAFTED_PROMPT_PATH}" not in lane_bodies
    assert "--" not in command
    assert payload["worker_command"][3] == "research-synthesis"
    assert payload["transport"] == "vc-frame"


def test_claude_terminal_command_streams_visible_json(tmp_path: Path) -> None:
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="audit",
        skill="audit",
        prompt="prove it",
        file="",
        runtime="terminal",
        root=str(tmp_path),
    )

    command = workflow.build_launch_command(spec, tmp_path)

    assert command[:4] == ["claude", "-p", "--output-format", "stream-json"]
    assert "--verbose" in command
    assert "--dangerously-skip-permissions" in command


def test_stream_capable_agents_use_native_stream_commands(tmp_path: Path) -> None:
    expected = {
        "codex": ("--json",),
        "agy": ("bash", "-c"),  # agy uses bash -c shim containing agy
        "junie": ("--output-format", "json-stream"),
        "grok": ("--output-format", "streaming-json"),
    }

    for agent, required in expected.items():
        spec = workflow.WorkflowLaunchSpec(
            agent=agent,
            mode="audit",
            skill="audit",
            prompt="prove it",
            file="",
            runtime="terminal",
            root=str(tmp_path),
        )

        command = workflow.build_launch_command(spec, tmp_path)

        for token in required:
            assert token in command


def test_runtime_prompt_keeps_metadata_runtime_owned(tmp_path: Path) -> None:
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="workflow",
        skill="workflow",
        prompt="ship it",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )

    prompt = workflow._runtime_prompt(spec)

    assert "Write your final report to the path in VIBECRAFTED_REPORT_PATH" in prompt
    assert "`run_id` and `session_id`" in prompt
    assert "preserve those values and never copy or guess identity" in prompt
    assert "keeping `finalized: false`" in prompt
    assert "`finalized: true` plus a non-empty `claim`" in prompt
    assert "non-empty `agent`, `skill`, and `status` keys" in prompt
    assert "Preserve an honest blocked/partial/failed status" in prompt
    assert "runtime owns VIBECRAFTED_META_PATH" in prompt
    assert "If you create or update run metadata" not in prompt
    # Every dispatched worker is oriented first: the vc-init Step 0 rides in the
    # runtime prompt itself, not left to each skill's prose gate to self-enforce.
    assert "Step 0 — orient before you touch (the vc-init pass)." in prompt
    assert "Loctree context atlas" in prompt
    assert "AICX history" in prompt


def test_launch_workflow_artifact_paths_are_terminal_truth(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    source = _source_dir(tmp_path)
    spec = workflow.normalize_launch_spec(
        {"skill": "workflow", "agent": "claude", "prompt": "go"},
        source,
    )

    monkeypatch.setattr(
        workflow,
        "_stdin_command",
        lambda _agent: [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import os, sys; "
                "assert 'launcher truth worker complete' not in sys.stdin.read(); "
                "Path(os.environ['VIBECRAFTED_REPORT_PATH']).write_text("
                "'---\\nstatus: completed\\nnext_stage: polarize\\n"
                "next_agent: codex\\ndou_index: 3\\n---\\nbody\\n', "
                "encoding='utf-8'"
                "); "
                "print('launcher truth worker complete')"
            ),
        ],
    )

    payload = workflow.launch_workflow(spec, source)

    assert payload["accepted"] is True
    assert payload["run_id"]
    assert payload["report"]
    assert payload["transcript"]
    assert payload["meta"]
    assert payload["prompt_file"]
    assert payload["control_plane_identity"] == {
        "run_id": payload["run_id"],
        "session_id": payload["session_id"],
        "operator_session": payload["operator_session"],
    }

    truth = workflow.await_launch_truth(
        payload,
        timeout_seconds=10,
        interval_seconds=0.05,
        require_transcript_output=True,
    )

    assert truth["completed"] is True
    assert truth["terminal_evidence"] is True
    assert truth["worker_alive"] is False
    assert truth["artifact_ok"] is True
    assert truth["paths_exist"] == {
        "report": True,
        "transcript": True,
        "meta": True,
    }
    assert truth["run"]["state"] in {"completed", "report_validated"}
    assert truth["run"]["liveness"] == "terminal"
    assert truth["run"]["artifact_gate"] == "validated"
    assert truth["meta_payload"]["run_id"] == payload["run_id"]
    assert truth["meta_payload"]["terminal"] is True
    assert truth["meta_payload"]["state"] == truth["run"]["state"]
    assert truth["meta_payload"]["report"] == payload["report"]
    assert truth["next_stage"] == "polarize"
    assert truth["next_agent"] == "codex"
    assert truth["dou_index"] == 3


def test_report_requested_next_stage_reads_report_frontmatter(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nstatus: completed\nnext_stage: marbles\n---\nbody\n",
        encoding="utf-8",
    )
    assert workflow._report_requested_next_stage(str(report)) == "marbles"

    bare = tmp_path / "bare.md"
    bare.write_text("no frontmatter here\n", encoding="utf-8")
    assert workflow._report_requested_next_stage(str(bare)) == ""
    assert workflow._report_requested_next_stage("") == ""
    assert workflow._report_requested_next_stage(str(tmp_path / "missing.md")) == ""


def test_report_requested_next_agent_reads_report_frontmatter(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        "---\nstatus: completed\nnext_agent: junie\n---\nbody\n",
        encoding="utf-8",
    )
    assert workflow._report_requested_next_agent(str(report)) == "junie"

    bare = tmp_path / "bare.md"
    bare.write_text("no frontmatter here\n", encoding="utf-8")
    assert workflow._report_requested_next_agent(str(bare)) == ""
    assert workflow._report_requested_next_agent("") == ""
    assert workflow._report_requested_next_agent(str(tmp_path / "missing.md")) == ""


def test_report_dou_index_reads_report_frontmatter(tmp_path: Path) -> None:
    zero = tmp_path / "zero.md"
    zero.write_text(
        "---\nstatus: completed\ndou_index: 0\n---\nbody\n", encoding="utf-8"
    )
    # 0 is the whole point (ZERO DoU index) — it must survive as 0, not None.
    assert workflow.report_dou_index(str(zero)) == 0

    gaps = tmp_path / "gaps.md"
    gaps.write_text(
        "---\nstatus: completed\ndou_index: 7\n---\nbody\n", encoding="utf-8"
    )
    assert workflow.report_dou_index(str(gaps)) == 7

    for invalid in ("banana", "-2", "true"):
        bad = tmp_path / "bad.md"
        bad.write_text(
            f"---\nstatus: completed\ndou_index: {invalid}\n---\nbody\n",
            encoding="utf-8",
        )
        assert workflow.report_dou_index(str(bad)) is None

    bare = tmp_path / "bare.md"
    bare.write_text("no frontmatter here\n", encoding="utf-8")
    assert workflow.report_dou_index(str(bare)) is None
    assert workflow.report_dou_index("") is None
    assert workflow.report_dou_index(str(tmp_path / "missing.md")) is None


def test_stop_run_terms_live_launcher_process_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        launcher_identity = process_control.process_identity_receipt(
            proc.pid,
            run_id="wflw-live-stop",
        )
        assert launcher_identity is not None
        _write_run_meta(
            home,
            {
                "run_id": "wflw-live-stop",
                "status": "running",
                "agent": "codex",
                "mode": "workflow",
                "root": str(tmp_path),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "skill_code": "wflw",
                "launcher_pid": proc.pid,
                "launcher_identity": launcher_identity,
                "liveness": "pid_alive",
            },
        )

        payload = workflow.stop_run(
            "wflw-live-stop", reason="manual", grace_seconds=0.05
        )
        proc.wait(timeout=2)

        assert payload["accepted"] is True
        assert payload["target"] == "launcher_pid"
        assert payload["target_pid"] == proc.pid
        assert payload["target_pgid"] == proc.pid
        assert payload["signal_sent"] is True
        run = payload["run"]
        assert run["state"] == "stopped"
        assert run["exit_code"] == 143
        assert run["operator_state"] == "stopped"
        assert run["lifecycle"]["stop"] is False
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)


def test_stop_run_real_dispatcher_worker_is_sticky_and_headless(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.setenv("VIBECRAFTED_REAPER", "0")
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "operator-session-must-not-leak")
    monkeypatch.setenv("VC_FRAME_TAB_NAME", "operator-tab-must-not-leak")
    monkeypatch.setenv("VC_FRAME_PANE_ID", "999")
    run_id = "wflw-real-dispatch-stop"
    source = _source_dir(tmp_path)
    owned_receipts: list[dict[str, Any]] = []

    def wait_for(predicate: Any, *, timeout: float = 10.0) -> Any:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(0.05)
        raise AssertionError(f"condition not met; last={last!r}")

    def cleanup_owned(receipt: dict[str, Any]) -> None:
        pid = int(receipt["pid"])
        pgid = int(receipt["pgid"])
        current, _reason, _identity = process_control.validate_process_identity(
            receipt,
            expected_pid=pid,
            expected_pgid=pgid,
            expected_run_id=run_id,
            env_index={pid: run_id},
        )
        if not current:
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and workflow._pgid_is_alive(pgid):
            time.sleep(0.05)
        if not workflow._pgid_is_alive(pgid):
            return
        # Test cleanup may escalate only after revalidating the same receipt;
        # a recycled group number is never fair game.
        current, _reason, _identity = process_control.validate_process_identity(
            receipt,
            expected_pid=pid,
            expected_pgid=pgid,
            expected_run_id=run_id,
            env_index={pid: run_id},
        )
        if current:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        launched = workflow.launch_workflow(
            workflow.WorkflowLaunchSpec(
                agent="codex",
                mode="workflow",
                skill="workflow",
                prompt="harmless stop lifecycle probe",
                file="",
                runtime="headless",
                root=str(source),
                run_id=run_id,
            ),
            source,
            worker_command_override=[
                sys.executable,
                "-c",
                (
                    "import os,time; "
                    "print(f'worker-ready:{os.getpid()}', flush=True); "
                    "time.sleep(60)"
                ),
            ],
        )
        launcher_identity = launched["launcher_identity"]
        assert isinstance(launcher_identity, dict)
        owned_receipts.append(launcher_identity)

        live = wait_for(
            lambda: (
                current
                if (current := workflow.lookup_run(run_id))
                and isinstance(current.get("worker_identity"), dict)
                else None
            )
        )
        worker_identity = live["worker_identity"]
        owned_receipts.append(worker_identity)
        worker_pid = int(live["worker_pid"])
        launcher_pid = int(live["launcher_pid"])
        assert worker_pid != launcher_pid
        assert live["worker_pgid"] == worker_identity["pgid"]

        payload = workflow.stop_run(
            run_id,
            reason="manual operator stop",
            grace_seconds=1.0,
        )

        assert payload["accepted"] is True
        assert payload["target"] == "worker_pgid"
        assert payload["target_pid"] == worker_identity["pgid"]
        assert payload["target_pgid"] == worker_identity["pgid"]
        assert payload["signal_sent"] is True
        assert payload["identity_qualification"] == "process_identity_current"

        final = wait_for(
            lambda: (
                current
                if (current := workflow.lookup_run(run_id))
                and current.get("state") == "stopped"
                and not workflow._pid_is_alive(launcher_pid)
                else None
            )
        )
        assert final["operator_stop_accepted"] is True
        assert final["stop_reason"] == "manual operator stop"
        assert final["artifact_gate"] == "stopped"
        assert final["artifact_errors"] == []
        assert final["recovery_required"] is False
        assert final["lifecycle"]["stop"] is False
        assert not workflow._pid_is_alive(worker_pid)

        meta = json.loads(Path(final["meta"]).read_text(encoding="utf-8"))
        assert meta["status"] == "stopped"
        assert meta["operator_stop_accepted"] is True
        assert "origin_session" not in meta
        assert "operator_session" not in meta
        assert "origin_tab" not in meta
        assert "origin_pane_id" not in meta

        events = list(
            reversed(
                [
                    event
                    for event in control_plane.read_event_tail(limit=100)
                    if event.get("run_id") == run_id
                ]
            )
        )
        stop_index = next(
            index
            for index, event in enumerate(events)
            if event.get("kind") == "audit:stop"
        )
        later_kinds = {
            str(event.get("kind") or "") for event in events[stop_index + 1 :]
        }
        assert "lifecycle:failed" not in later_kinds
        assert "lifecycle:report_missing" not in later_kinds
    finally:
        for receipt in reversed(owned_receipts):
            cleanup_owned(receipt)


def test_stop_run_refuses_live_pid_when_identity_receipt_is_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        identity = process_control.process_identity_receipt(
            proc.pid,
            run_id="wflw-stale-identity",
        )
        assert identity is not None
        identity["command_sha256"] = "0" * 64
        _write_run_meta(
            home,
            {
                "run_id": "wflw-stale-identity",
                "status": "running",
                "agent": "codex",
                "mode": "workflow",
                "root": str(tmp_path),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "skill_code": "wflw",
                "worker_pid": proc.pid,
                "worker_pgid": proc.pid,
                "worker_identity": identity,
                "liveness": "pid_alive",
            },
        )

        payload = workflow.stop_run(
            "wflw-stale-identity",
            reason="manual operator stop",
            grace_seconds=0.05,
        )

        assert payload["accepted"] is False
        assert payload["reason"] == "process_identity_mismatch"
        assert payload["signal_sent"] is False
        assert proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)


def test_stop_run_records_already_dead_launcher_without_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_run_meta(
        home,
        {
            "run_id": "wflw-dead-stop",
            "status": "running",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "skill_code": "wflw",
            "launcher_pid": 999999999,
            "liveness": "pid_alive",
        },
    )

    payload = workflow.stop_run("wflw-dead-stop", reason="manual", grace_seconds=0)

    assert payload["accepted"] is True
    assert payload["already_dead"] is True
    assert payload["error"] == ""
    run = payload["run"]
    assert run["state"] == "stopped"
    assert run["liveness"] == "terminal"
    assert run["stop_already_dead"] is True
    assert run["lifecycle"]["stop"] is False


def test_stop_run_terminal_record_is_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    _write_run_meta(
        home,
        {
            "run_id": "wflw-terminal-stop",
            "status": "completed",
            "agent": "codex",
            "mode": "workflow",
            "root": str(tmp_path),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "skill_code": "wflw",
            "exit_code": 0,
            "launcher_pid": 999999999,
            "liveness": "terminal",
        },
    )

    payload = workflow.stop_run("wflw-terminal-stop", reason="manual")

    assert payload["accepted"] is False
    assert payload["reason"] == "run_terminal"
    run = workflow.lookup_run("wflw-terminal-stop")
    assert run is not None
    assert run["state"] == "completed"
    assert run["exit_code"] == 0
    assert run["lifecycle"]["stop"] is False


def test_block_run_marks_active_run_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(["active", "blocked"])
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda run_id: {
            "run_id": run_id,
            "state": next(states),
            "exit_code": None,
        },
    )
    calls: dict[str, Any] = {}
    monkeypatch.setattr(
        workflow,
        "append_event",
        lambda **kwargs: calls.setdefault("events", []).append(kwargs),
    )

    payload = workflow.block_run(
        "wflw-010101-0001", reason="needs creds", note="api key"
    )

    assert payload["accepted"] is True
    assert payload["run"]["state"] == "blocked"
    event = calls["events"][0]
    assert event["kind"] == "audit:block"
    assert event["payload"]["state"] == "blocked"
    assert event["payload"]["note"] == "api key"


def test_block_run_rejects_terminal_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda run_id: {"run_id": run_id, "state": "completed", "exit_code": 0},
    )
    calls: dict[str, Any] = {}
    monkeypatch.setattr(
        workflow,
        "append_event",
        lambda **kwargs: calls.setdefault("events", []).append(kwargs),
    )

    payload = workflow.block_run("wflw-010101-0001")

    assert payload["accepted"] is False
    assert payload["reason"] == "run_terminal"
    assert calls["events"][0]["payload"]["accepted"] is False


def test_retry_run_relaunches_terminal_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda run_id: {
            "run_id": run_id,
            "state": "failed",
            "agent": "claude",
            "skill": "workflow",
            "mode": "workflow",
            "runtime": "headless",
            "prompt": "go",
            "file": "",
            "root": str(tmp_path),
            "exit_code": 1,
        },
    )
    captured: dict[str, Any] = {}

    def _launch(
        spec: workflow.WorkflowLaunchSpec,
        source_dir: str | Path,
        *,
        env: dict[str, str] | None = None,
        retry_of: str = "",
    ) -> dict[str, Any]:
        captured["spec"] = spec
        captured["source_dir"] = str(source_dir)
        captured["retry_of"] = retry_of
        return {"accepted": True, "run_id": "wflw-020202-0002"}

    monkeypatch.setattr(workflow, "launch_workflow", _launch)
    monkeypatch.setattr(
        workflow,
        "append_event",
        lambda **kwargs: captured.setdefault("events", []).append(kwargs),
    )

    payload = workflow.retry_run("wflw-010101-0001", source_dir=tmp_path)

    assert payload["accepted"] is True
    assert payload["retry_run_id"] == "wflw-020202-0002"
    assert captured["retry_of"] == "wflw-010101-0001"


def _native_resume_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    agent: str = "codex",
    run_fields: dict[str, Any] | None = None,
    meta_fields: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    run_id = "impl-parent"
    claims = [
        {
            "claim": "recovery remains unfinished",
            "grade": "strong",
            "evidence": "worker exited before closure",
        }
    ]
    claim_digest = trust._claims_digest(claims)
    receipt = TrustReceiptV1.issue(
        repo_root=str(tmp_path.resolve()),
        run_id=run_id,
        commit_sha="a" * 40,
        trust_verdict="pass-with-gaps",
        settlement_verdict="needs_attention",
        settlement_tui="n",
        settlement_revision=7,
        claim_digest=claim_digest,
    )
    receipt_payload = receipt.to_payload()
    settled_at = "2026-07-26T00:00:00+00:00"
    settlement_reason = f"trust_pass_with_gaps:{receipt.commit_sha}"
    settlement_projection = {
        "run_id": run_id,
        "root": receipt.repo_root,
        "repo_root": receipt.repo_root,
        "commit_sha": receipt.commit_sha,
        "settlement_tui": receipt.settlement_tui,
        "settlement_verdict": receipt.settlement_verdict,
        "settlement_reason": settlement_reason,
        "settlement_at": settled_at,
        "settlement_source": "trust",
        "settlement_revision": receipt.settlement_revision,
        "settlement_waived": False,
        "settlement_claim_digest": receipt.claim_digest,
        "settlement": {
            "verdict": receipt.settlement_verdict,
            "reason": settlement_reason,
            "settled_at": settled_at,
            "source": "trust",
            "claim_digest": receipt.claim_digest,
            "waived": False,
            "tui": receipt.settlement_tui,
            "await_rc": None,
            "await_outcome": "",
        },
        "trust_receipt": receipt_payload,
    }
    run = {
        **settlement_projection,
        "settlement": dict(settlement_projection["settlement"]),
        "state": "failed",
        "agent": agent,
        "skill": "implement",
        "exit_code": 9,
        "worker_alive": False,
        "recovery_required": True,
    }
    run.update(run_fields or {})
    for top_level, nested in (
        ("settlement_verdict", "verdict"),
        ("settlement_reason", "reason"),
        ("settlement_at", "settled_at"),
        ("settlement_source", "source"),
        ("settlement_claim_digest", "claim_digest"),
        ("settlement_waived", "waived"),
        ("settlement_tui", "tui"),
        ("await_rc", "await_rc"),
        ("await_outcome", "await_outcome"),
    ):
        if run_fields is not None and top_level in run_fields:
            run["settlement"][nested] = run_fields[top_level]
    meta = {
        **settlement_projection,
        "settlement": dict(settlement_projection["settlement"]),
        "agent": agent,
        "agent_session_id": f"{agent}-native-id",
        "runtime_session_id": "runtime-parent-id",
        "attempt": 1,
    }
    meta.update(meta_fields or {})
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    journal = home / "trust" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text(
        json.dumps(
            {
                "schema": trust.TRUST_JOURNAL_SCHEMA_V2,
                "recorded_at": settled_at,
                "repo_root": str(tmp_path.resolve()),
                "sha": "a" * 40,
                "author_name": "Codex",
                "author_email": "agents@vetcoders.io",
                "authored_at": "2026-07-26T00:00:00+00:00",
                "subject": "[codex/codex] fix(runtime): retain recovery",
                "verdict": "pass-with-gaps",
                "settlement_tui": "n",
                "run_id": run_id,
                "claims": claims,
                "claim_digest": claim_digest,
                "trust_receipt": receipt_payload,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(run))
    return run_id, run


def _native_resume_claim_env(
    tmp_path: Path,
    *,
    key: str,
    parent: str,
) -> dict[str, str]:
    env = dict(os.environ)
    core_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (core_root, env.get("PYTHONPATH", "")) if part
    )
    env["VIBECRAFTED_HOME"] = str(tmp_path / "home")
    env["NATIVE_RESUME_TEST_KEY"] = key
    env["NATIVE_RESUME_TEST_PARENT"] = parent
    journal = tmp_path / "home" / "trust" / "journal.jsonl"
    if journal.is_file():
        latest = json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])
        env["NATIVE_RESUME_TEST_RECEIPT"] = latest["trust_receipt"]["receipt_id"]
    else:
        env["NATIVE_RESUME_TEST_RECEIPT"] = "b" * 64
    return env


def test_manual_explicit_resume_launches_own_tracked_headless_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-manual-1")
    monkeypatch.setattr(
        workflow, "ensure_session_id", lambda _value=None: "runtime-manual-1"
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[dict[str, Any]] = []

    def fake_launch(
        spec: workflow.WorkflowLaunchSpec,
        source_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        launches.append({"spec": spec, "source_dir": source_dir, **kwargs})
        return {
            "accepted": True,
            "run_id": spec.run_id,
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            "status": "launching",
        }

    monkeypatch.setattr(workflow, "launch_workflow", fake_launch)

    result = workflow.manual_resume_session(
        "codex",
        "codex-thread-42",
        tmp_path,
        prompt="continue from the verified session",
        root=tmp_path,
        model="gpt-5.5",
    )

    assert result["accepted"] is True
    assert result["run_id"] == "rsme-manual-1"
    assert result["resume_mode"] == "manual_explicit"
    assert result["agent_session_id"] == "codex-thread-42"
    assert result["runtime_session_id"] == "runtime-manual-1"
    launch = launches[0]
    assert launch["worker_command_override"] == [
        "/verified/bin/codex",
        "exec",
        "-m",
        "gpt-5.5",
        "resume",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "codex-thread-42",
        "-",
    ]
    assert launch["spec"].mode == "manual_explicit"
    assert launch["spec"].runtime == "headless"
    assert launch["spec"].run_id == "rsme-manual-1"
    assert launch["spec"].model == "gpt-5.5"
    assert launch["spec"].prompt == "continue from the verified session"
    assert launch["env"]["VIBECRAFTED_SESSION_ID"] == "runtime-manual-1"
    assert launch["env"]["VIBECRAFTED_AGENT_SESSION_ID"] == "codex-thread-42"
    assert launch["launch_meta"] == {
        "run_id": "rsme-manual-1",
        "agent": "codex",
        "agent_session_id": "codex-thread-42",
        "runtime_session_id": "runtime-manual-1",
        "native_resume": True,
        "resume_mode": "manual_explicit",
        "manual_explicit": True,
    }
    forbidden_parent_claims = {
        "resume_of",
        "parent_runtime_session_id",
        "resume_root",
        "attempt",
        "automatic_attempt_budget",
        "automatic_attempt_number",
        "resume_settlement_revision",
        "resume_trust_receipt_id",
        "resume_idempotency_key",
        "settlement_revision",
        "trust_receipt_id",
    }
    assert forbidden_parent_claims.isdisjoint(launch["launch_meta"])


@pytest.mark.parametrize("agent", ["agy", "junie"])
def test_manual_explicit_resume_fails_closed_for_unverified_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
) -> None:
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda _agent: (_ for _ in ()).throw(
            AssertionError("unverified provider must be rejected before probe")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unverified provider must never launch")
        ),
    )

    result = workflow.manual_resume_session(
        agent,
        f"{agent}-session",
        tmp_path,
        prompt="continue",
        root=tmp_path,
    )

    assert result["accepted"] is False
    assert result["reason"] == "native_resume_unverified"
    assert result["resume_mode"] == "manual_explicit"
    assert result["terminal"] is True


def test_manual_explicit_resume_requires_confirmed_runtime_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="probe_failed",
            executable=None,
            version=None,
            detail="codex not found",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("failed provider probe must never launch")
        ),
    )

    result = workflow.manual_resume_session(
        "codex",
        "codex-thread-42",
        tmp_path,
        prompt="continue",
        root=tmp_path,
    )

    assert result["accepted"] is False
    assert result["reason"] == "native_resume_probe_failed"
    assert result["detail"] == "codex not found"
    assert result["retryable"] is True
    assert result["terminal"] is False


def test_native_resume_creates_new_tracked_monotonic_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, _run = _native_resume_parent(
        monkeypatch,
        tmp_path,
        run_fields={"model_requested": "gpt-5.5"},
    )
    child_ids = iter(("rsme-child-1", "rsme-child-2"))
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: next(child_ids))
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[dict[str, Any]] = []

    def fake_launch(
        spec: workflow.WorkflowLaunchSpec,
        source_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        launches.append({"spec": spec, "source_dir": source_dir, **kwargs})
        return {"accepted": True, "run_id": spec.run_id}

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(workflow, "launch_workflow", fake_launch)
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )

    first = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        prompt="continue safely",
        expected_agent="codex",
        expected_agent_session_id="codex-native-id",
        expected_settlement_revision=7,
    )
    second = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent="codex",
    )

    assert first["accepted"] is True
    assert first["retryable"] is False
    assert first["terminal"] is True
    assert first["resume_run_id"] == "rsme-child-1"
    assert first["attempt"] == 2
    assert second["resume_run_id"] == "rsme-child-2"
    assert second["attempt"] == 3
    assert first["runtime_session_id"] != "runtime-parent-id"
    assert first["runtime_session_id"] != second["runtime_session_id"]
    assert launches[0]["worker_command_override"] == [
        "/verified/bin/codex",
        "exec",
        "-m",
        "gpt-5.5",
        "resume",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "codex-native-id",
        "-",
    ]
    assert launches[0]["env"]["VIBECRAFTED_AGENT_SESSION_ID"] == "codex-native-id"
    assert launches[0]["launch_meta"]["resume_of"] == run_id
    assert launches[0]["launch_meta"]["parent_runtime_session_id"] == (
        "runtime-parent-id"
    )
    assert launches[0]["launch_meta"]["attempt"] == 2
    assert launches[0]["launch_meta"]["resume_mode"] == "manual"
    assert launches[0]["launch_meta"]["automatic_attempt_budget"] == 1
    assert launches[0]["launch_meta"]["automatic_attempt_number"] == 0
    assert (
        launches[0]["launch_meta"]["resume_trust_receipt_id"]
        == (first["trust_receipt_id"])
    )
    assert len(first["trust_receipt_id"]) == 64
    assert launches[0]["spec"].runtime == "headless"
    assert launches[0]["spec"].model == "gpt-5.5"
    assert launches[0]["spec"].prompt == "continue safely"
    assert events[-1]["kind"] == "audit:native_resume"
    assert events[-1]["payload"]["new_run_id"] == "rsme-child-2"


@pytest.mark.parametrize(
    ("journal_mode", "expected_receipt_id", "reason"),
    [
        ("missing", "", "trust_journal_missing"),
        ("legacy", "", "legacy_trust_record_not_resume_authority"),
        ("current", "f" * 64, "expected_trust_receipt_mismatch"),
    ],
)
def test_native_resume_direct_boundary_cannot_bypass_receipt_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    journal_mode: str,
    expected_receipt_id: str,
    reason: str,
) -> None:
    run_id, _run = _native_resume_parent(monkeypatch, tmp_path)
    journal = tmp_path / "home" / "trust" / "journal.jsonl"
    if journal_mode == "missing":
        journal.unlink()
    elif journal_mode == "legacy":
        journal.write_text(
            json.dumps(
                {
                    "schema": "vibecrafted.trust-journal.v1",
                    "repo_root": str(tmp_path.resolve()),
                    "sha": "a" * 40,
                    "verdict": "pass-with-gaps",
                    "settlement_tui": "n",
                    "run_id": run_id,
                    "claims": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("receipt authority denial must launch nothing")
        ),
    )

    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_receipt_id=expected_receipt_id,
    )

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["terminal"] is True


def test_native_resume_never_uses_legacy_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, run = _native_resume_parent(
        monkeypatch,
        tmp_path,
        meta_fields={"agent_session_id": "", "session_id": "legacy-session-id"},
    )
    run["session_id"] = "legacy-control-plane-id"
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: dict(run))
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == "native_resume_candidate_missing"
    assert events[-1]["payload"]["reason"] == "native_resume_candidate_missing"


def test_native_resume_requires_explicit_runtime_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, _run = _native_resume_parent(
        monkeypatch,
        tmp_path,
        meta_fields={
            "runtime_session_id": "",
            "session_id": "legacy-ambiguous-id",
        },
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == "missing_runtime_session_id"


def test_native_resume_preserves_projected_lineage_when_meta_lacks_resume_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, _run = _native_resume_parent(
        monkeypatch,
        tmp_path,
        run_fields={"resume_root": "impl-original", "attempt": 4},
        meta_fields={"attempt": 4},
    )
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-child")
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: {"accepted": True, "run_id": "rsme-child"},
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is True
    assert result["resume_root"] == "impl-original"
    assert result["attempt"] == 5


def test_native_resume_idempotency_replay_returns_same_child_without_relaunch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, _run = _native_resume_parent(monkeypatch, tmp_path)
    reserved: list[str] = []

    def reserve(_skill: str) -> str:
        reserved.append("rsme-idempotent")
        return "rsme-idempotent"

    monkeypatch.setattr(workflow, "reserve_run_id", reserve)
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[dict[str, Any]] = []

    def fake_launch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        launches.append({"spec": spec, **kwargs})
        return {
            "accepted": True,
            "run_id": spec.run_id,
            "pid": 4242,
            "status": "launching",
        }

    monkeypatch.setattr(workflow, "launch_workflow", fake_launch)
    key = f"settlement:{run_id}:7"

    first = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )
    replay = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent_session_id="codex-native-id",
        expected_settlement_revision=7,
        idempotency_key=key,
    )
    monkeypatch.setattr(workflow, "lookup_run", lambda _target: None)
    archived_parent_replay = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )

    assert first["accepted"] is True
    assert first["deduplicated"] is False
    assert replay["accepted"] is True
    assert replay["deduplicated"] is True
    assert replay["reason"] == "idempotent_replay"
    assert replay["resume_run_id"] == first["resume_run_id"] == "rsme-idempotent"
    assert replay["attempt"] == first["attempt"] == 2
    assert archived_parent_replay["accepted"] is False
    assert archived_parent_replay["reason"] == "run_not_found"
    assert len(reserved) == 1
    assert len(launches) == 1
    assert launches[0]["launch_meta"]["resume_idempotency_key"] == key
    receipts = list(
        (tmp_path / "home" / "control_plane" / "native_resume_idempotency").glob(
            "*.json"
        )
    )
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["idempotency_key"] == key
    assert receipt["child_run_id"] == "rsme-idempotent"
    assert receipt["state"] == "dispatched"


def test_native_resume_idempotency_conflicts_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, run = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-owned")
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[str] = []
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda spec, *_args, **_kwargs: (
            launches.append(spec.run_id) or {"accepted": True, "run_id": spec.run_id}
        ),
    )
    key = f"settlement:{run_id}:8"
    first = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )

    wrong_agent = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent="claude",
        idempotency_key=key,
    )
    other_run = {**run, "run_id": "impl-other"}
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda target: dict(other_run) if target == "impl-other" else dict(run),
    )
    wrong_parent = workflow.native_resume_run(
        "impl-other",
        source_dir=tmp_path,
        idempotency_key=key,
    )

    assert first["accepted"] is True
    assert wrong_agent["accepted"] is False
    assert wrong_agent["reason"] == "idempotency_conflict"
    assert wrong_agent["retryable"] is False
    assert wrong_agent["terminal"] is True
    assert wrong_parent["accepted"] is False
    assert wrong_parent["reason"] == "idempotency_conflict"
    assert launches == ["rsme-owned"]


def test_native_resume_idempotency_recovers_kill_window_without_second_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, parent = _native_resume_parent(monkeypatch, tmp_path)
    children: dict[str, dict[str, Any]] = {}
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda target: dict(parent) if target == run_id else children.get(target),
    )
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-kill-window")
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[str] = []

    def crash_after_dispatch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        launches.append(spec.run_id)
        children[spec.run_id] = {
            "run_id": spec.run_id,
            "state": "process_spawned",
            "launcher_pid": 4444,
        }
        raise KeyboardInterrupt("simulated kill after spawn before receipt")

    monkeypatch.setattr(workflow, "launch_workflow", crash_after_dispatch)
    key = f"settlement:{run_id}:9"

    with pytest.raises(KeyboardInterrupt):
        workflow.native_resume_run(
            run_id,
            source_dir=tmp_path,
            idempotency_key=key,
        )
    replay = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )

    assert replay["accepted"] is True
    assert replay["deduplicated"] is True
    assert replay["reason"] == "idempotent_replay"
    assert replay["resume_run_id"] == "rsme-kill-window"
    assert replay["idempotency_state"] == "reserved"
    assert launches == ["rsme-kill-window"]


def test_native_resume_idempotency_serializes_concurrent_duplicate_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda target: dict(parent) if target == run_id else None,
    )
    reserved: list[str] = []

    def reserve(_skill: str) -> str:
        reserved.append("rsme-concurrent")
        return "rsme-concurrent"

    monkeypatch.setattr(workflow, "reserve_run_id", reserve)
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launch_started = threading.Event()
    release_launch = threading.Event()
    launches: list[str] = []

    def slow_launch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        launches.append(spec.run_id)
        launch_started.set()
        assert release_launch.wait(timeout=5)
        return {"accepted": True, "run_id": spec.run_id}

    monkeypatch.setattr(workflow, "launch_workflow", slow_launch)
    key = f"settlement:{run_id}:10"
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            workflow.native_resume_run,
            run_id,
            tmp_path,
            idempotency_key=key,
        )
        assert launch_started.wait(timeout=5)
        duplicate_future = executor.submit(
            workflow.native_resume_run,
            run_id,
            tmp_path,
            idempotency_key=key,
        )
        duplicate = duplicate_future.result(timeout=5)
        release_launch.set()
        first = first_future.result(timeout=5)

    assert first["accepted"] is True
    assert duplicate["accepted"] is False
    assert duplicate["reason"] == "idempotency_in_progress"
    assert duplicate["retryable"] is True
    assert duplicate["terminal"] is False
    assert duplicate["deduplicated"] is True
    assert duplicate["resume_run_id"] == first["resume_run_id"] == "rsme-concurrent"
    assert reserved == ["rsme-concurrent"]
    assert launches == ["rsme-concurrent"]


def test_native_resume_dead_owner_takes_over_pre_spawn_reservation_exactly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    key = f"settlement:{run_id}:7"
    env = _native_resume_claim_env(tmp_path, key=key, parent=run_id)
    env["NATIVE_RESUME_TEST_CRASH"] = "1"
    crashed = subprocess.run(
        [sys.executable, "-c", _NATIVE_RESUME_CLAIM_SCRIPT],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert crashed.returncode == 91
    reserved = json.loads(crashed.stdout)["record"]

    monkeypatch.setattr(
        workflow,
        "reserve_run_id",
        lambda _skill: (_ for _ in ()).throw(
            AssertionError("takeover must reuse the reserved child id")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[dict[str, Any]] = []

    def launch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **kwargs: Any,
    ) -> dict[str, Any]:
        launches.append({"spec": spec, **kwargs})
        return {"accepted": True, "run_id": spec.run_id, "status": "launching"}

    monkeypatch.setattr(workflow, "launch_workflow", launch)
    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent="codex",
        idempotency_key=key,
    )

    assert result["accepted"] is True
    assert result["resume_run_id"] == reserved["child_run_id"]
    assert result["runtime_session_id"] == reserved["runtime_session_id"]
    assert result["attempt"] == reserved["attempt"]
    assert launches[0]["spec"].run_id == reserved["child_run_id"]
    receipt = workflow._lookup_native_resume_idempotency(key)
    assert receipt is not None
    assert receipt["state"] == "dispatched"
    assert receipt["lease_generation"] == 2


def test_native_resume_reserved_receipt_ignores_unrelated_live_owner_pid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    key = f"settlement:{run_id}:live-pid-reuse"
    env = _native_resume_claim_env(tmp_path, key=key, parent=run_id)
    env["NATIVE_RESUME_TEST_CRASH"] = "1"
    crashed = subprocess.run(
        [sys.executable, "-c", _NATIVE_RESUME_CLAIM_SCRIPT],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert crashed.returncode == 91
    reserved = json.loads(crashed.stdout)["record"]

    unrelated_live_pid = os.getppid()
    os.kill(unrelated_live_pid, 0)
    registry = workflow._native_resume_idempotency_registry()
    receipt_path = workflow._native_resume_idempotency_path(registry, key)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["owner_token"] = "stale-owner-token"
    receipt["owner_pid"] = unrelated_live_pid
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    budget_path = workflow._native_resume_automatic_budget_path(run_id)
    budget = json.loads(budget_path.read_text(encoding="utf-8"))
    budget["owner_token"] = receipt["owner_token"]
    budget["owner_pid"] = unrelated_live_pid
    budget_path.write_text(json.dumps(budget), encoding="utf-8")

    monkeypatch.setattr(
        workflow,
        "reserve_run_id",
        lambda _skill: (_ for _ in ()).throw(
            AssertionError("takeover must preserve the reserved child identity")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launches: list[str] = []
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda spec, *_args, **_kwargs: (
            launches.append(spec.run_id)
            or {"accepted": True, "run_id": spec.run_id, "status": "launching"}
        ),
    )

    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent="codex",
        idempotency_key=key,
    )

    assert result["accepted"] is True
    assert result["resume_run_id"] == reserved["child_run_id"]
    assert result["runtime_session_id"] == reserved["runtime_session_id"]
    assert launches == [reserved["child_run_id"]]
    final_receipt = workflow._lookup_native_resume_idempotency(key)
    assert final_receipt is not None
    assert final_receipt["lease_generation"] == reserved["lease_generation"] + 1


def test_native_resume_unknown_receipt_state_is_terminal_before_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    key = f"settlement:{run_id}:unknown-state"
    registry = workflow._native_resume_idempotency_registry()
    receipt_path = workflow._native_resume_idempotency_path(registry, key)
    receipt_path.write_text(
        json.dumps(
            {
                "schema": workflow.NATIVE_RESUME_IDEMPOTENCY_SCHEMA,
                "idempotency_key": key,
                "parent_run_id": run_id,
                "agent": "codex",
                "agent_session_id": "codex-native-id",
                "parent_runtime_session_id": "runtime-parent-id",
                "child_run_id": "rsme-invalid-state",
                "runtime_session_id": "runtime-invalid-state",
                "resume_root": run_id,
                "attempt": 2,
                "settlement_revision": 7,
                "state": "corrupt_future_state",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda _target: (_ for _ in ()).throw(
            AssertionError("invalid receipt must fail before run lookup")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda _agent: (_ for _ in ()).throw(
            AssertionError("invalid receipt must fail before provider probe")
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid receipt must never launch")
        ),
    )

    results = [
        workflow.native_resume_run(
            run_id,
            source_dir=tmp_path,
            idempotency_key=key,
        )
        for _ in range(2)
    ]

    for result in results:
        assert result["accepted"] is False
        assert result["reason"] == "idempotency_record_invalid"
        assert result["retryable"] is False
        assert result["terminal"] is True
        assert "corrupt_future_state" in result["detail"]


def test_native_resume_unknown_ledger_state_is_terminal_without_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    key = f"settlement:{run_id}:unknown-ledger-state"
    budget_path = workflow._native_resume_automatic_budget_path(run_id)
    budget_path.write_text(
        json.dumps({"state": "corrupt_future_state"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid automatic ledger must never launch")
        ),
    )

    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )

    assert result["accepted"] is False
    assert result["reason"] == "idempotency_claim_failed"
    assert result["retryable"] is False
    assert result["terminal"] is True
    assert "corrupt_future_state" in result["detail"]


def test_native_resume_claim_is_atomic_across_processes(
    tmp_path: Path,
) -> None:
    run_id = "impl-multiprocess"
    key = f"settlement:{run_id}:7"
    env = _native_resume_claim_env(tmp_path, key=key, parent=run_id)
    env["NATIVE_RESUME_TEST_HOLD"] = "0.2"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _NATIVE_RESUME_CLAIM_SCRIPT],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=10) for process in processes]
    assert [process.returncode for process in processes] == [0, 0]
    results = [json.loads(stdout) for stdout, _stderr in outputs]

    assert sorted(result["created"] for result in results) == [False, True]
    assert len({result["record"]["child_run_id"] for result in results}) == 1
    assert len({result["record"]["runtime_session_id"] for result in results}) == 1
    assert len({result["record"]["attempt"] for result in results}) == 1
    receipts = list(
        (tmp_path / "home" / "control_plane" / "native_resume_idempotency").glob(
            "*.json"
        )
    )
    assert len(receipts) == 1


def test_native_resume_automatic_budget_is_one_per_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launched: list[str] = []
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda spec, *_args, **_kwargs: (
            launched.append(spec.run_id) or {"accepted": True, "run_id": spec.run_id}
        ),
    )

    first = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=f"settlement:{run_id}:7",
    )
    second = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=f"settlement:{run_id}:7:other-guardian",
    )

    assert first["accepted"] is True
    assert first["resume_mode"] == "automatic"
    assert first["automatic_attempt_budget"] == 1
    assert first["automatic_attempt_number"] == 1
    assert second["accepted"] is False
    assert second["reason"] == "automatic_resume_budget_exhausted"
    assert second["retryable"] is False
    assert second["terminal"] is True
    assert len(launched) == 1


def test_native_resume_retryable_launch_failure_reuses_same_automatic_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-retry")
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launched: list[str] = []

    def launch(spec, *_args, **_kwargs):
        launched.append(spec.run_id)
        accepted = len(launched) > 1
        return {"accepted": accepted, "run_id": spec.run_id}

    monkeypatch.setattr(workflow, "launch_workflow", launch)
    key = f"settlement:{run_id}:7"

    failed = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )
    recovered = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        idempotency_key=key,
    )

    assert failed["accepted"] is False
    assert failed["reason"] == "launch_failed"
    assert failed["retryable"] is True
    assert failed["terminal"] is False
    assert recovered["accepted"] is True
    assert recovered["resume_run_id"] == failed["resume_run_id"] == "rsme-retry"
    assert recovered["attempt"] == failed["attempt"]
    assert launched == ["rsme-retry", "rsme-retry"]


def test_native_resume_revision_cas_rejects_probe_window_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, parent = _native_resume_parent(monkeypatch, tmp_path)
    lookups = 0

    def lookup(target: str) -> dict[str, Any] | None:
        nonlocal lookups
        if target != run_id:
            return None
        lookups += 1
        current = dict(parent)
        current["settlement_revision"] = 7 if lookups == 1 else 8
        return current

    monkeypatch.setattr(workflow, "lookup_run", lookup)
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("changed settlement revision must not launch")
        ),
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == "settlement_revision_changed"
    assert result["detail"] == "expected=7 current=8"
    assert result["retryable"] is False
    assert result["terminal"] is True


@pytest.mark.parametrize(
    ("changed_identity", "reason", "detail"),
    [
        (
            "agent_session",
            "expected_agent_session_mismatch",
            "expected=codex-native-id current=codex-replaced-id",
        ),
        (
            "settlement_revision",
            "expected_settlement_revision_mismatch",
            "expected=7 current=8",
        ),
    ],
)
def test_native_resume_guardian_snapshot_rejects_probe_window_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    changed_identity: str,
    reason: str,
    detail: str,
) -> None:
    run_id, parent = _native_resume_parent(monkeypatch, tmp_path)
    key = f"settlement:{run_id}:guardian-snapshot"
    live_revision = 7

    def lookup(target: str) -> dict[str, Any] | None:
        if target != run_id:
            return None
        current = dict(parent)
        current["settlement_revision"] = live_revision
        return current

    def change_snapshot_during_probe(agent: str) -> SimpleNamespace:
        nonlocal live_revision
        if changed_identity == "agent_session":
            meta_path = (
                tmp_path
                / "home"
                / "control_plane"
                / "runtime_runs"
                / run_id
                / "meta.json"
            )
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["agent_session_id"] = "codex-replaced-id"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        else:
            live_revision = 8
        return SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        )

    monkeypatch.setattr(workflow, "lookup_run", lookup)
    monkeypatch.setattr(workflow, "probe_provider", change_snapshot_during_probe)
    launches: list[str] = []
    monkeypatch.setattr(
        workflow,
        "launch_workflow",
        lambda spec, *_args, **_kwargs: (
            launches.append(spec.run_id) or {"accepted": True, "run_id": spec.run_id}
        ),
    )

    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        expected_agent_session_id="codex-native-id",
        expected_settlement_revision=7,
        idempotency_key=key,
    )

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["detail"] == detail
    assert result["retryable"] is False
    assert result["terminal"] is True
    assert launches == []
    assert workflow._lookup_native_resume_idempotency(key) is None


@pytest.mark.parametrize(
    ("expectation", "reason"),
    [
        (
            {"expected_agent_session_id": "pending"},
            "invalid_expected_agent_session_id",
        ),
        (
            {"expected_settlement_revision": 0},
            "invalid_expected_settlement_revision",
        ),
        (
            {"expected_settlement_revision": True},
            "invalid_expected_settlement_revision",
        ),
    ],
)
def test_native_resume_rejects_invalid_guardian_expectations_before_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    expectation: dict[str, Any],
    reason: str,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflow,
        "lookup_run",
        lambda _target: (_ for _ in ()).throw(
            AssertionError("invalid Guardian expectation must fail before lookup")
        ),
    )

    result = workflow.native_resume_run(
        run_id,
        source_dir=tmp_path,
        **expectation,
    )

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["retryable"] is False
    assert result["terminal"] is True


def test_native_resume_holds_parent_mutation_lock_until_launch_handoff(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(workflow, "reserve_run_id", lambda _skill: "rsme-lock")
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launch_started = threading.Event()
    release_launch = threading.Event()

    def slow_launch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        launch_started.set()
        assert release_launch.wait(timeout=5)
        return {"accepted": True, "run_id": spec.run_id}

    monkeypatch.setattr(workflow, "launch_workflow", slow_launch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resume_future = executor.submit(
            workflow.native_resume_run,
            run_id,
            tmp_path,
        )
        assert launch_started.wait(timeout=5)
        block_future = executor.submit(workflow.block_run, run_id)
        time.sleep(0.05)
        assert block_future.done() is False
        release_launch.set()
        resumed = resume_future.result(timeout=5)
        blocked = block_future.result(timeout=5)

    assert resumed["accepted"] is True
    assert blocked["accepted"] is False
    assert blocked["reason"] == "run_terminal"


def test_retry_run_waits_for_guardian_and_refuses_second_launch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id, _parent = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflow,
        "reserve_run_id",
        lambda _skill: "rsme-guardian-owned",
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="confirmed",
            executable="/verified/bin/codex",
            version="codex 1.0",
            detail="confirmed",
        ),
    )
    launch_started = threading.Event()
    release_launch = threading.Event()
    launches: list[str] = []

    def slow_launch(
        spec: workflow.WorkflowLaunchSpec,
        _source_dir: str | Path,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        launches.append(spec.run_id)
        launch_started.set()
        assert release_launch.wait(timeout=5)
        return {"accepted": True, "run_id": spec.run_id}

    monkeypatch.setattr(workflow, "launch_workflow", slow_launch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        resume_future = executor.submit(
            workflow.native_resume_run,
            run_id,
            tmp_path,
            idempotency_key=f"settlement:{run_id}:guardian-owner",
        )
        assert launch_started.wait(timeout=5)
        retry_future = executor.submit(workflow.retry_run, run_id, tmp_path)
        time.sleep(0.05)
        assert retry_future.done() is False
        release_launch.set()
        resumed = resume_future.result(timeout=5)
        retried = retry_future.result(timeout=5)

    assert resumed["accepted"] is True
    assert retried["accepted"] is False
    assert retried["reason"] == "recovery_owned_by_guardian"
    assert retried["retryable"] is False
    assert retried["terminal"] is True
    assert launches == ["rsme-guardian-owned"]


@pytest.mark.parametrize(
    ("run_fields", "reason"),
    [
        (
            {"settlement_tui": "f", "settlement_verdict": "finalized"},
            "settlement_f_not_resumable",
        ),
        (
            {"settlement_tui": "", "settlement_verdict": ""},
            "settlement_unknown_not_resumable",
        ),
        ({"settlement_source": "auto"}, "vc_trust_authority_missing"),
        ({"recovery_required": False}, "recovery_not_required"),
        ({"worker_alive": True}, "worker_not_confirmed_dead"),
        ({"settlement_revision": None}, "settlement_revision_missing"),
        (
            {"state": "running", "exit_code": None, "liveness": "active"},
            "run_not_terminal",
        ),
    ],
)
def test_native_resume_public_boundary_rejects_unsafe_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_fields: dict[str, Any],
    reason: str,
) -> None:
    run_id, _parent = _native_resume_parent(
        monkeypatch,
        tmp_path,
        run_fields=run_fields,
    )
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda _agent: (_ for _ in ()).throw(
            AssertionError("policy rejection must happen before provider probe")
        ),
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["retryable"] is (
        reason
        in {"recovery_not_required", "worker_not_confirmed_dead", "run_not_terminal"}
    )
    assert result["terminal"] is not result["retryable"]


@pytest.mark.parametrize(
    ("run_fields", "reason"),
    [
        ({"state": "stopped", "stop_reason": "operator stop request"}, "manual_stop"),
        ({"state": "cancelled"}, "manual_stop"),
        ({"state": "blocked"}, "blocked"),
        ({"settlement_tui": "x", "settlement_source": "trust"}, "trust_x"),
    ],
)
def test_native_resume_refuses_operator_and_trust_terminals(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run_fields: dict[str, Any],
    reason: str,
) -> None:
    run_id, _run = _native_resume_parent(
        monkeypatch,
        tmp_path,
        run_fields=run_fields,
    )
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["retryable"] is False
    assert result["terminal"] is True
    assert events[-1]["payload"]["reason"] == reason


@pytest.mark.parametrize(
    ("agent", "reason"),
    [
        ("gemini", "native_resume_unsupported"),
        ("agy", "native_resume_unverified"),
        ("junie", "native_resume_unverified"),
    ],
)
def test_native_resume_refuses_unsupported_or_unverified_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    reason: str,
) -> None:
    run_id, _run = _native_resume_parent(monkeypatch, tmp_path, agent=agent)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == reason
    assert result["retryable"] is False
    assert result["terminal"] is True
    assert events[-1]["payload"]["reason"] == reason


def test_native_resume_requires_confirmed_runtime_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run_id, _run = _native_resume_parent(monkeypatch, tmp_path)
    monkeypatch.setattr(
        workflow,
        "probe_provider",
        lambda agent: SimpleNamespace(
            agent=agent,
            state="probe_failed",
            executable=None,
            version=None,
            detail="codex not found",
        ),
    )
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        workflow, "append_event", lambda **kwargs: events.append(kwargs)
    )

    result = workflow.native_resume_run(run_id, source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == "native_resume_probe_failed"
    assert result["detail"] == "codex not found"
    assert result["retryable"] is True
    assert result["terminal"] is False


def test_native_resume_missing_projection_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(workflow, "lookup_run", lambda _run_id: None)

    result = workflow.native_resume_run("run-not-projected", source_dir=tmp_path)

    assert result["accepted"] is False
    assert result["reason"] == "run_not_found"
    assert result["retryable"] is True
    assert result["terminal"] is False


def test_build_launch_command_uses_core_stdin_agent_command_not_legacy_deck(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    def _fake_stdin(agent: str) -> list[str]:
        captured["agent"] = agent
        return ["agent-bin", "-"]

    monkeypatch.setattr(workflow, "_stdin_command", _fake_stdin)
    spec = workflow.WorkflowLaunchSpec(
        agent="claude",
        mode="implement",
        skill="implement",
        prompt="ship it",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )

    command = workflow.build_launch_command(spec, tmp_path / "source")

    assert command[0] == "agent-bin"
    assert command[0:2] != [
        "bash",
        str(tmp_path / "source" / "scripts" / "vibecrafted"),
    ]
    assert command == ["agent-bin", "-"]
    assert captured["agent"] == "claude"


def test_research_swarm_uses_core_codex_coordinator(
    tmp_path: Path,
) -> None:
    spec = workflow.normalize_launch_spec(
        {"skill": "research", "prompt": "map the surface", "root": str(tmp_path)},
        tmp_path,
    )

    command = workflow.build_launch_command(spec, tmp_path)

    assert spec.agent == "swarm"
    assert command[:3] == [sys.executable, "-m", "vibecrafted_core.workflow_runtime"]
    assert command[3] == "research"
    assert "--prompt-file" in command
    assert "map the surface" not in command


def test_research_agentless_form_uses_runtime_config_swarm(tmp_path: Path) -> None:
    spec = workflow.normalize_launch_spec(
        {"skill": "research", "prompt": "map the surface", "root": str(tmp_path)},
        tmp_path,
    )

    assert spec.agent == "swarm"
    assert spec.research_agents == ()
    assert spec.research_synthesizer == ""


def test_research_single_agent_form_keeps_synthesizer_pick(tmp_path: Path) -> None:
    spec = workflow.normalize_launch_spec(
        {
            "skill": "research",
            "agent": ["claude"],
            "prompt": "map the surface",
            "root": str(tmp_path),
        },
        tmp_path,
    )

    command = workflow.build_launch_command(spec, tmp_path)

    assert spec.agent == "swarm"
    assert spec.research_agents == ()
    assert spec.research_synthesizer == "claude"
    assert command[command.index("--synthesizer") + 1] == "claude"


def test_research_multi_agent_form_overrides_lanes_and_first_synthesizes(
    tmp_path: Path,
) -> None:
    spec = workflow.normalize_launch_spec(
        {
            "skill": "research",
            "agent": ["codex", "agy"],
            "prompt": "map the surface",
            "root": str(tmp_path),
        },
        tmp_path,
    )

    assert spec.agent == "swarm"
    assert spec.research_agents == ("codex", "agy")
    assert spec.research_synthesizer == "codex"


def test_marbles_uses_supervised_core_runtime(tmp_path: Path) -> None:
    spec = workflow.normalize_launch_spec(
        {
            "skill": "marbles",
            "agent": "codex",
            "prompt": "converge",
            "root": str(tmp_path),
            "count": 2,
            "depth": 4,
        },
        tmp_path,
    )

    command = workflow.build_launch_command(spec, tmp_path)

    assert command[:3] == [sys.executable, "-m", "vibecrafted_core.workflow_runtime"]
    assert command[3] == "marbles"
    assert command[command.index("--workflow") + 1] == "marbles"
    assert "--prompt-file" in command
    assert command[command.index("--count") + 1] == "2"
    assert command[command.index("--depth") + 1] == "4"


def test_polarize_uses_supervised_marbles_runtime_with_polarize_prompt(
    tmp_path: Path,
) -> None:
    spec = workflow.normalize_launch_spec(
        {
            "skill": "polarize",
            "agent": "codex",
            "prompt": "cut excess",
            "root": str(tmp_path),
            "count": 2,
            "depth": 4,
        },
        tmp_path,
    )

    command = workflow.build_launch_command(spec, tmp_path)

    assert spec.prompt == "cut excess"
    assert command[:3] == [sys.executable, "-m", "vibecrafted_core.workflow_runtime"]
    assert command[3] == "marbles"
    assert command[command.index("--workflow") + 1] == "polarize"
    assert "--prompt-file" in command
    assert command[command.index("--count") + 1] == "2"
    assert command[command.index("--depth") + 1] == "4"


def test_runtime_prompt_carries_worker_signal_discipline(tmp_path: Path) -> None:
    spec = workflow.WorkflowLaunchSpec(
        agent="codex",
        mode="workflow",
        skill="workflow",
        prompt="ship it",
        file="",
        runtime="headless",
        root=str(tmp_path),
    )

    prompt = workflow._runtime_prompt(spec)

    # Gate-nap prevention (docs/runtime/AGENT_OPS.md, Class 1): every dispatched
    # worker is told WHY waiting is futile, not merely forbidden from doing it —
    # the bare prohibition was broken in the wild.
    assert "background-task completions will NEVER wake" in prompt
    assert "Never end your turn waiting" in prompt


def test_dispatcher_command_carries_lifecycle_state_flag() -> None:
    base = {
        "run_id": "impl-1",
        "root": "/repo",
        "meta_path": Path("/tmp/m.json"),
        "report_path": Path("/tmp/r.md"),
        "transcript_path": Path("/tmp/t.log"),
        "worker_command": ["codex", "exec"],
    }

    with_state = workflow._dispatcher_command(
        **base, lifecycle_state_path="/cp/lifecycle_runs/x/state.json"
    )
    assert "--lifecycle-state" in with_state
    flag_index = with_state.index("--lifecycle-state")
    assert with_state[flag_index + 1] == "/cp/lifecycle_runs/x/state.json"

    without_state = workflow._dispatcher_command(**base)
    assert "--lifecycle-state" not in without_state


def test_dispatcher_command_salvages_only_verified_native_resume_streams() -> None:
    base = {
        "run_id": "resume-child",
        "root": "/tmp/repo",
        "meta_path": Path("/tmp/meta.json"),
        "report_path": Path("/tmp/report.md"),
        "transcript_path": Path("/tmp/transcript.log"),
        "worker_command": ["codex", "exec", "resume", "native-id", "-"],
    }

    native_resume = workflow._dispatcher_command(
        **base,
        salvage_report_from_stream=True,
    )
    ordinary_worker = workflow._dispatcher_command(**base)

    assert "--salvage-report-from-stream" in native_resume
    assert "--salvage-report-from-stream" not in ordinary_worker
