"""Executable convergence counterexamples, including the public source launcher."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def _finding(key="gap", severity="high", cluster="assigned"):
    return {
        "id": key,
        "severity": severity,
        "cluster": cluster,
        "evidence": "verified current assigned file",
    }


def _evidence(**changes):
    return {
        "schema": "vibecrafted.marbles-reception.v1",
        "candidate_run_id": "child",
        "candidate_sha": "abc",
        "root": "/assigned",
        "scope_verified": True,
        "scope_evidence": "coverage verified",
        "gate": "pass",
        "blocker": "",
        "checks": [
            {"command": "pytest assigned", "exit_code": 0, "evidence": "2 passed"}
        ],
        **{
            key: []
            for key in (
                "attacked",
                "resolved",
                "still_open",
                "discovered",
                "regressions",
            )
        },
        **changes,
    }


def test_reception_retains_unmentioned_findings_and_clamps_failed_gate():
    from vibecrafted_core.marbles_reception import receive

    a, b, c = (
        _finding("old"),
        _finding("instant", "low"),
        _finding("regression", "medium"),
    )
    baseline = receive(
        None, _evidence(discovered=[a]), report=Path("L1"), no_source_progress=False
    )
    assert baseline["delta_raw"] == baseline["stepper"] == 0
    unchanged = receive(
        baseline, _evidence(), report=Path("L2"), no_source_progress=False
    )
    assert unchanged["open_findings"] == [a]
    resolved = receive(
        baseline,
        _evidence(attacked=[a, b], resolved=[a, b], regressions=[c], gate="fail"),
        report=Path("L2"),
        no_source_progress=False,
    )
    assert resolved["open_findings"] == [c]
    assert resolved["delta_raw"] == 0 and resolved["delta_weighted"] == 1
    assert resolved["closed"] == ["old"] and resolved["instant_fixes"] == ["instant"]
    assert resolved["opened"] == ["regression"]
    assert resolved["stepper"] == resolved["convergence_rate"] == 0
    assert resolved["verdict"] != "converged"
    good = receive(
        baseline,
        _evidence(attacked=[a], resolved=[a]),
        report=Path("L2"),
        no_source_progress=False,
    )
    assert good["delta_weighted"] == 3 and good["stepper"] == 1
    assert good["decision"] == "stop" and good["verdict"] == "converged"


def test_fresh_write_prompts_keep_complete_unchanged_task_in_common_prefix():
    from vibecrafted_core import workflow_runtime

    task = (
        "Keep the complete unchanged plan: żółw\nScope and acceptance\n"
        + "tail evidence " * 100
    )
    prompts = [
        workflow_runtime._child_prompt(
            "marbles",
            f"marbles-L{index}",
            "/same-root",
            workflow_runtime._loop_prompt("marbles", task, index, 3, 4),
        )
        for index in (1, 2)
    ]
    prefix = os.path.commonprefix(prompts)
    assert task in prefix and "Contract:" in prefix
    assert "Track: marbles-L1" in prompts[0] and "Track: marbles-L2" in prompts[1]
    assert "reception.json" not in prefix and "delta_weighted" not in prefix


def test_generated_read_finding_examples_validate_and_preserve_open_debt(tmp_path):
    from types import SimpleNamespace

    from vibecrafted_core.marbles_reception import read_evidence, receive
    from vibecrafted_core.workflow_runtime import _reception_prompt

    prompt = _reception_prompt(
        "/assigned",
        "same unchanged task",
        SimpleNamespace(run_id="child", report=Path("candidate.md")),
        {"root": "/assigned", "head": "abc"},
        [],
    )
    examples = json.loads(re.search(r"```json\n(.*?)```", prompt, re.DOTALL).group(1))
    report = tmp_path / "example.md"
    report.write_text(
        "```marbles-reception\n" + json.dumps(_evidence(**examples)) + "\n```\n"
    )
    evidence = read_evidence(
        report, candidate_run_id="child", tree={"root": "/assigned", "head": "abc"}
    )
    result = receive(None, evidence, report=report, no_source_progress=False)
    assert {item["id"] for item in result["open_findings"]} == {
        "example-open",
        "example-new",
        "example-regression",
    }
    assert result["verdict"] == "not_converged"


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "future"},
        {"candidate_sha": "wrong"},
        {"root": "/other"},
        {"checks": []},
        {
            "gate": "pass",
            "checks": [{"command": "gate", "exit_code": 1, "evidence": "red"}],
        },
        {"scope_verified": "yes"},
        {"resolved": [_finding()]},
        {"attacked": [_finding()]},
        {
            "attacked": [_finding()],
            "resolved": [_finding()],
            "still_open": [_finding()],
        },
        {"discovered": [_finding(), _finding()]},
        {"regressions": [{"id": "no evidence"}]},
    ],
)
def test_reception_refuses_malformed_or_unsupported_evidence(tmp_path, change):
    from vibecrafted_core.marbles_reception import read_evidence

    report = tmp_path / "receiver.md"
    report.write_text(
        "```marbles-reception\n" + json.dumps(_evidence(**change)) + "\n```\n"
    )
    with pytest.raises((ValueError, TypeError)):
        read_evidence(
            report, candidate_run_id="child", tree={"head": "abc", "root": "/assigned"}
        )


def test_reception_repeated_red_and_negative_progress_escalate():
    from vibecrafted_core.marbles_reception import receive

    first = receive(
        None,
        _evidence(discovered=[_finding()], gate="fail"),
        report=Path("L1"),
        no_source_progress=False,
    )
    second = receive(
        first,
        _evidence(still_open=[_finding()], gate="fail"),
        report=Path("L2"),
        no_source_progress=False,
    )
    assert second["decision"] == "escalate" and second["gate_failures"] == 2
    previous = receive(
        None,
        _evidence(discovered=[_finding()]),
        report=Path("L1"),
        no_source_progress=False,
    )
    for i in range(2):
        previous = receive(
            previous,
            _evidence(discovered=[_finding(f"new-{i}")]),
            report=Path(f"L{i + 2}"),
            no_source_progress=False,
        )
    assert previous["negative_rounds"] == 2 and previous["decision"] == "escalate"


def _provider(bin_dir: Path, *, scenario: str):
    """A real process writes and commits assigned files, then verifies in READ."""
    script = textwrap.dedent(r"""
        import json, os, re, subprocess, sys, time
        from pathlib import Path
        import yaml

        scenario = SCENARIO
        argv = sys.argv[1:]
        if argv[:1] == ['queue']:
            Path(os.environ['R2_QUEUE_CAPTURE']).write_text(json.dumps(argv))
            print('private fixture queue accepted', flush=True)
            sys.exit(0)
        prompt = argv[argv.index('-p') + 1] if Path(sys.argv[0]).name == 'kimi' else sys.stdin.read()
        if Path(sys.argv[0]).name == 'agy' and prompt.startswith('{'):
            prompt = json.loads(prompt)['message']['content'][0]['text']
        report = Path(os.environ['VIBECRAFTED_REPORT_PATH'])
        root = Path.cwd()
        receiver = 'You are the informed READ-only' in prompt
        round_id = os.environ['VIBECRAFTED_RUN_ID']
        index = int((root / 'assigned.txt').read_text()) if receiver else int(re.search(r'-L([0-9]+)', round_id).group(1))
        capture = {'prompt': prompt, 'argv': argv, 'cwd': str(root), 'pid': os.getpid(), 'receiver': receiver}
        report.with_suffix('.capture.json').write_text(json.dumps(capture))
        native_id = round_id+'-native'
        if receiver and 'resume' in argv:
            native_id = argv[-2]
        elif receiver and '--resume' in argv:
            native_id = argv[argv.index('--resume')+1]
        if receiver and scenario == 'identity-mismatch':
            native_id = round_id+'-wrong-native'
        print(json.dumps({'type':'thread.started','thread_id':native_id}), flush=True)
        print('[12:00:00] session: ' + native_id, flush=True)
        for _ in range(120 if scenario == 'productive' and not receiver else 12):
            # No human display fragment: still real productive stream activity.
            print(json.dumps({'type':'item.started','item':{'type':'command_execution'}}), flush=True)
            time.sleep(.1 if scenario == 'productive' and not receiver else .025)
        _, header, _ = report.read_text().split('---', 2)
        fields = yaml.safe_load(header)
        status = 'blocked' if scenario == 'worker-blocked' and not receiver else 'failed' if scenario == 'reader-failed-report' and receiver else 'completed'
        fields.update(status=status, finalized=True, claim='fixture verified actual assigned source')
        body = 'one bounded fixture cut'
        if not receiver:
            assert 'prior accepted evidence' not in prompt
            assert 'marbles-reception.v1' not in prompt
            assert 'scope_evidence' not in prompt
            assert 'reception.json' not in prompt
            if scenario in {'silent', 'idle'}:
                time.sleep(120)
            if scenario == 'empty-commits':
                subprocess.run(['git','commit','--allow-empty','-q','-m',f'empty fixture {index}'],check=True)
            elif scenario != 'no-progress':
                (root / 'assigned.txt').write_text(str(index))
                subprocess.run(['git','add','assigned.txt'],check=True)
                subprocess.run(['git','commit','-q','-m',f'fixture cut {index}'],check=True)
            if scenario == 'dirty':
                (root / 'assigned.txt').write_text('uncommitted')
            if scenario == 'reset' and index == 2:
                subprocess.run(['git','reset','--hard','HEAD~2'],check=True)
            if scenario == 'branch':
                subprocess.run(['git','checkout','-qb','foreign'],check=True)
            if scenario == 'badreport':
                fields['finalized'] = False
            if scenario == 'worker-failed':
                sys.exit(7)
        else:
            data = json.loads(re.search(r'```marbles-reception\n(.*?)```',prompt,re.S).group(1))
            expected = 0 if scenario in {'no-progress','empty-commits'} else index
            command = [sys.executable,'-c',f"from pathlib import Path; assert int(Path('assigned.txt').read_text()) == {expected}" + ('; assert False' if scenario == 'red' else '')]
            check = subprocess.run(command, capture_output=True, text=True)
            data.update(scope_verified=True, scope_evidence='Read assigned.txt, verified candidate commit and task',
                        gate='pass' if check.returncode == 0 else 'fail', checks=[{'command':' '.join(command), 'exit_code':check.returncode,
                                             'evidence':check.stderr or 'assigned file assertion passed'}])
            finding = {'id':'assigned-gap','severity':'high','cluster':'assigned','evidence':'assigned.txt requires a second cut'}
            if int((root / 'assigned.txt').read_text()) < 2 or scenario == 'red':
                data['discovered'] = [finding]
            else:
                data['attacked'] = data['resolved'] = [finding]
            if scenario == 'missing-disposition':
                data['attacked'] = [finding]
                data['resolved'] = data['discovered'] = []
            if scenario in {'invalid','plain-invalid'}:
                data['schema'] = 'unknown'
            if scenario == 'claude-invalid':
                data['attacked'] = ['assigned-gap']
            if scenario == 'claude-missing-disposition':
                data['attacked'] = [finding]
                data['resolved'] = data['discovered'] = []
            if scenario == 'receiver-mutates':
                (root / 'assigned.txt').write_text('illegal READ edit')
            if scenario == 'plain-blocked':
                data.update(scope_verified=False, gate='unknown', blocker='required external acceptance unavailable')
            if scenario == 'external-blocker':
                data['blocker'] = 'external decision needed'
            body = '```marbles-reception\n' + json.dumps(data) + '\n```\n'
        final_report = '---\n' + yaml.safe_dump(fields) + '---\n' + body + '\n'
        if not receiver and scenario == 'claude-invalid':
            fields.update(finalized=False, claim='')
            final_report = '---\n' + yaml.safe_dump(fields) + '---\n' + body + '\n'
        if receiver and scenario.startswith('claude-'):
            emitted = '# READ verification\n\n' + body
            print(json.dumps({'type':'user','message':{'role':'user','content':[{'type':'text','text':emitted}]}}),flush=True)
            print(json.dumps({'type':'assistant','message':{'role':'assistant','content':[
                {'type':'thinking','thinking':emitted},
                {'type':'tool_use','name':'Bash','input':{'command':emitted}},
                {'type':'text','text':emitted}]}}),flush=True)
            print(json.dumps({'type':'result','subtype':'success','is_error':False,'result':emitted}),flush=True)
        elif receiver and scenario in {'stdout','plain-stdout','plain-blocked','plain-invalid'}:
            emitted = '# READ verification\n\n' + body if scenario.startswith('plain-') else final_report
            print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':emitted}}),flush=True)
        else:
            report.write_text(final_report)
        print('fixture phase settled',flush=True)
    """).replace("SCENARIO", repr(scenario))
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("codex", "claude", "kimi", "junie", "grok", "agy", "copilot"):
        executable = bin_dir / name
        executable.write_text(f"#!{sys.executable}\n" + script)
        executable.chmod(0o755)


def _public_run(
    tmp_path,
    scenario,
    *,
    count=3,
    agent="codex",
    silence="30",
    workflow="marbles",
    deck=False,
    extra_args=(),
    await_run=True,
):
    from test_cli_execution_controls import _git_repo

    candidate = tmp_path / "candidate"
    baseline = _git_repo(candidate)
    (candidate / "assigned.txt").write_text("0")
    (candidate / ".gitignore").write_text(".loctree/\n")
    subprocess.run(
        ["git", "add", "assigned.txt", ".gitignore"], cwd=candidate, check=True
    )
    subprocess.run(
        ["git", "commit", "-qm", "seed assigned source"], cwd=candidate, check=True
    )
    baseline = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=candidate, text=True
    ).strip()
    source_root = Path(__file__).resolve().parents[2]
    user_home = tmp_path / "user"
    user_home.mkdir()
    _provider(user_home / ".local" / "bin", scenario=scenario)
    plan = tmp_path / "unchanged-plan.md"
    plan.write_text(
        "Make two bounded cuts to assigned.txt. Keep this exact task and constraints. żółw\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_FRAME", "ZELLIJ"))
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        VIBECRAFTED_HOME=str(tmp_path / "vc-home"),
        VIBECRAFTED_ROOT=str(source_root),
        VIBECRAFTED_CORE_DIR=str(source_root / "vibecrafted-core"),
        VIBECRAFTED_PYTHON=sys.executable,
        PYTHONPATH=str(source_root / "vibecrafted-core"),
        HOME=str(user_home),
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
        VIBECRAFTED_SILENCE_TIMEOUT_SECONDS=silence,
        PATH=str(user_home / ".local" / "bin")
        + os.pathsep
        + os.environ.get("PATH", os.defpath),
    )
    if agent == "kimi":
        # kimi headless auto is refused (interactive-only); the supervised
        # marbles loop opts into bypass through the durable config cell.
        kimi_config = tmp_path / "xdg" / "vibecrafted"
        kimi_config.mkdir(parents=True, exist_ok=True)
        (kimi_config / "config.toml").write_text(
            '[agents.kimi]\npermissions = "bypass"\n', encoding="utf-8"
        )
    result = subprocess.run(
        [
            str(source_root / "scripts" / "vibecrafted")
            if deck
            else str(Path(sys.executable).parent / "vibecrafted"),
            workflow,
            agent,
            "--runtime",
            "headless",
            "--repo",
            str(candidate),
            "--base",
            baseline,
            "--worktree",
            "true",
            "--count",
            str(count),
            "--file",
            str(plan),
            "--json",
            *(["--await"] if await_run else []),
            *extra_args,
        ],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if not await_run:
        receipt = json.loads(result.stdout)
        assert receipt["accepted"] is True
        return result, receipt, {}, {}, plan
    rows = [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]
    assert len(rows) == 2, result.stdout + result.stderr
    receipt, completion = rows
    assert receipt["accepted"] is True and completion["completed"] is True
    parent = json.loads(Path(receipt["meta"]).read_text())
    return result, receipt, completion, parent, plan


@pytest.mark.parametrize("agent", ["codex", "claude", "kimi", "junie"])
@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_cycle_preserves_task_and_fresh_independent_reader(tmp_path, agent):
    from vibecrafted_core.report_contract import parse_report_path

    result, launch, completion, parent, plan = _public_run(
        tmp_path, "converge", count=2, agent=agent
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr
    write, read = parent["lifecycle"]["stages"]
    cycle = write["write_cycle"]
    assert len(cycle["workers"]) == 2
    assert read["cycle_read_evidence"]["verdict"] == "converged"
    assert (Path(launch["root"]) / "assigned.txt").read_text() == "2"
    assert (Path(launch["parent_root"]) / "assigned.txt").read_text() == "0"
    for worker in cycle["workers"]:
        capture = json.loads(
            Path(worker["report"]).with_suffix(".capture.json").read_text()
        )
        assert plan.read_text() in capture["prompt"] and not capture["receiver"]
        assert parse_report_path(Path(worker["report"])).fields["finalized"] == "true"
    read_meta = json.loads(Path(read["launch"]["meta"]).read_text())
    assert read_meta["receiver_guard"]["kind"] == "macos_source_write_deny"
    assert all(
        read_meta["agent_session_id"]
        != json.loads(Path(w["report"]).with_suffix(".meta.json").read_text())[
            "agent_session_id"
        ]
        for w in cycle["workers"]
    )
    assert not completion["timed_out"]


@pytest.mark.parametrize(
    "scenario",
    [
        "worker-blocked",
        "worker-failed",
        "invalid",
        "missing-disposition",
        "receiver-mutates",
        "external-blocker",
        "red",
        "no-progress",
        "empty-commits",
        "dirty",
        "reset",
        "branch",
        "badreport",
        "reader-failed-report",
    ],
)
@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_cycle_refuses_unverified_or_failed_candidate(tmp_path, scenario):
    result, launch, completion, parent, _ = _public_run(
        tmp_path, scenario, count=2, silence="30"
    )
    assert result.returncode == 1
    lifecycle = parent["lifecycle"]
    assert lifecycle["status"] == "failed"
    cycle = lifecycle["stages"][0]["write_cycle"]
    if scenario in {
        "worker-blocked",
        "worker-failed",
        "dirty",
        "reset",
        "branch",
        "badreport",
    }:
        assert len(cycle["workers"]) == (2 if scenario == "reset" else 1)
        assert cycle["status"] != "completed"
    else:
        assert len(cycle["workers"]) == cycle["workers_completed"] == 2
        assert lifecycle["stages"][-1]["cycle_read_error"]
    assert not completion["timed_out"] and Path(launch["report"]).is_file()
    if scenario == "worker-failed":
        assert cycle["workers"][0]["exit_code"] == 7
        assert lifecycle["failed_stages"]
    if scenario == "receiver-mutates":
        assert (Path(launch["root"]) / "assigned.txt").read_text() == "2"


@pytest.mark.parametrize(
    "scenario,agent,success",
    [
        ("stdout", "codex", True),
        ("plain-stdout", "codex", True),
        ("plain-blocked", "codex", False),
        ("plain-invalid", "codex", False),
        ("claude-stdout", "claude", True),
        ("claude-missing-disposition", "claude", False),
    ],
)
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS guarded stdout READ")
def test_public_final_read_stdout_is_validated_once(tmp_path, scenario, agent, success):
    result, _, _, parent, _ = _public_run(tmp_path, scenario, count=2, agent=agent)
    assert (result.returncode == 0) is success
    read = parent["lifecycle"]["stages"][-1]
    report = Path(read["launch"]["report"]).read_text()
    assert report.count("```marbles-reception") == 1
    if success:
        assert read["cycle_read_evidence"]["verdict"] == "converged"
    else:
        assert read["status"] == "failed"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS guarded public workflow")
def test_public_parent_report_retains_prelaunch_machine_identity(tmp_path):
    from vibecrafted_core.report_contract import parse_report_path

    result, launch, _, parent, _ = _public_run(tmp_path, "converge", count=2)
    assert result.returncode == 0
    report = parse_report_path(Path(launch["report"]))
    canonical = parent["runtime_session_id"]
    assert canonical and canonical not in {"pending-unset", "aggregated", "unknown"}
    assert report.run_id == parent["run_id"] == launch["run_id"]
    assert report.fields["session_id"] == parent["session_id"] == canonical
    assert parent["launch_session_id"] == launch["session_id"]
    assert parent["agent_session_id"] == ""
    assert parent["provider_session_id"]["value"] == "unknown"


@pytest.mark.parametrize("measured", [True, False])
@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_parent_settlement_preserves_measured_phase_usage(
    tmp_path, monkeypatch, measured
):
    provider = _provider

    def instrumented(directory, *, scenario):
        provider(directory, scenario=scenario)
        with (directory / "codex").open("a") as stream:
            stream.write(
                "\nprint('[12:00:00] model: unpriced-fixture-model',flush=True)\n"
            )
            if measured:
                stream.write(
                    "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':10}}),flush=True)\n"
                )

    monkeypatch.setattr(sys.modules[__name__], "_provider", instrumented)
    result, _, _, meta, _ = _public_run(tmp_path, "converge", count=2)
    assert result.returncode == 0
    expected = 300 if measured else "unknown"
    assert meta["tokens_input"] == expected, meta["usage"]
    assert meta["tokens_output"] == (30 if measured else "unknown")
    assert meta["tokens_total"] == (330 if measured else "unknown")
    assert meta["cost_usd"] == "unknown"
    assert meta["provider_session_id"]["value"] == "unknown"


def test_receiver_guard_unsupported_host_is_explicit(tmp_path, monkeypatch):
    from vibecrafted_core import marbles_reception

    monkeypatch.setattr(marbles_reception.sys, "platform", "linux")
    with pytest.raises(ValueError, match="receiver_source_guard_unavailable"):
        marbles_reception.readonly_command(
            str(tmp_path), [sys.executable, "-c", "pass"]
        )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux public READ guard refusal")
def test_public_linux_records_completed_write_but_blocked_reception(tmp_path):
    result, _, completion, parent, _ = _public_run(
        tmp_path, "converge", count=2, agent="kimi"
    )
    assert result.returncode == 1 and completion["completed"]
    assert parent["status"] == "failed"
    lifecycle = parent["lifecycle"]
    cycle = lifecycle["stages"][0]["write_cycle"]
    assert cycle["status"] == "completed" and len(cycle["workers"]) == 2
    assert (
        "receiver_source_guard_unavailable"
        in lifecycle["stages"][-1]["launch"]["error"]
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS source guard")
def test_receiver_guard_protects_unicode_root_and_git_metadata(tmp_path):
    from test_cli_execution_controls import _git_repo
    from vibecrafted_core.marbles_reception import readonly_command

    root = tmp_path / 'żółw "quoted"'
    _git_repo(root)
    script = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('forbidden')"
    for target in (root / "README.md", root / ".git" / "forbidden"):
        command, guard = readonly_command(
            str(root), [sys.executable, "-c", script, str(target)]
        )
        assert command[0] == "/usr/bin/sandbox-exec"
        assert guard["root"] == str(root.resolve())
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode != 0 and "PermissionError" in result.stderr
    assert (root / "README.md").read_text() == "seed\n"
    assert not (root / ".git" / "forbidden").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS Codex tool sandbox")
def test_codex_single_external_boundary_runs_gates_with_external_outputs(tmp_path):
    import shutil

    from test_cli_execution_controls import _git_repo
    from vibecrafted_core.marbles_reception import readonly_command

    codex = shutil.which("codex")
    if not codex:
        pytest.skip("installed Codex sandbox command unavailable")
    parent_root = tmp_path / "parent-repo"
    _git_repo(parent_root)
    root = tmp_path / "assigned-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-qb", "receiver-test", str(root)],
        cwd=parent_root,
        check=True,
    )
    external_report = tmp_path / "outside-report.md"
    external_report.write_text("receipt outside source")
    external_output = tmp_path / "gate-output"
    read = (
        "from pathlib import Path; import subprocess; assert Path('README.md').read_text().strip() == 'seed'; assert Path("
        + repr(str(external_report))
        + ").read_text() == 'receipt outside source'; print(subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())"
    )
    readonly = [
        codex,
        "sandbox",
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'approval_policy="never"',
        "--",
        sys.executable,
        "-c",
        read,
    ]
    # Reproduce the actual restrictive inner profile rejected under the outer.
    stacked, _ = readonly_command(str(root), readonly)
    failed = subprocess.run(
        stacked, cwd=root, capture_output=True, text=True, timeout=20, check=False
    )
    assert (
        failed.returncode != 0
        and "sandbox_apply: Operation not permitted" in failed.stderr
    )
    bypass = [
        codex,
        "sandbox",
        "-c",
        'sandbox_mode="danger-full-access"',
        "-c",
        'approval_policy="never"',
        "--",
        sys.executable,
        "-c",
        read,
    ]
    single, guard = readonly_command(str(root), bypass, "codex")
    assert single[0] == "/usr/bin/sandbox-exec"
    assert guard["kind"] == "macos_source_write_deny"
    assert guard["native_tool_sandbox"] == "bypassed_under_external_guard"
    success = subprocess.run(
        single, cwd=root, capture_output=True, text=True, timeout=20, check=False
    )
    assert success.returncode == 0 and len(success.stdout.strip()) == 40, success.stderr
    write_output = (
        "from pathlib import Path;Path("
        + repr(str(external_output))
        + ").write_text('gate output')"
    )
    no_output = subprocess.run(
        [*readonly[:-1], write_output],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert no_output.returncode != 0 and not external_output.exists()
    output = subprocess.run(
        [*single[:-1], write_output],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert output.returncode == 0 and external_output.read_text() == "gate output", (
        output.stderr
    )
    # Worktree shared metadata is outside source and must still be protected.
    for target in (root / "README.md", parent_root / ".git" / "forbidden"):
        write = (
            "from pathlib import Path;Path("
            + repr(str(target))
            + ").write_text('forbidden')"
        )
        denied = subprocess.run(
            [*single[:-1], write],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert denied.returncode != 0 and "PermissionError" in denied.stderr
    assert (root / "README.md").read_text().strip() == "seed"
    assert not (parent_root / ".git" / "forbidden").exists()


def test_current_report_schema_preserves_machine_identity_and_foreign_meta(
    tmp_path, monkeypatch
):
    from vibecrafted_core import workflow_runtime
    from vibecrafted_core.control_plane import control_plane_home
    from vibecrafted_core.report_contract import (
        materialize_launcher_report_template,
        parse_report_path,
        stamp_launcher_report_identity,
    )

    control_plane_home().mkdir(parents=True, exist_ok=True)

    report, meta = tmp_path / "report.md", tmp_path / "meta.json"
    materialize_launcher_report_template(
        report, run_id="parent", agent="codex", skill="marbles"
    )
    stamp_launcher_report_identity(
        report,
        run_id="parent",
        session_id="machine-seeded-session",
        agent="codex",
        skill="marbles",
        status="pending",
    )
    before = parse_report_path(report).fields.copy()
    meta.write_text(
        json.dumps(
            {
                "run_id": "parent",
                "worker_pid": 123,
                "baseline_sha": "exact",
                "foreign": {"keep": True},
            }
        )
    )
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "parent")
    monkeypatch.setenv("VIBECRAFTED_REPORT_PATH", str(report))
    monkeypatch.setenv("VIBECRAFTED_META_PATH", str(meta))
    workflow_runtime._write_parent_report(
        "marbles",
        str(tmp_path),
        "task",
        [],
        reception={"verdict": "blocked"},
        status_override="blocked",
    )
    after = parse_report_path(report)
    assert after.ok and after.run_id == before["run_id"]
    if before.get("session_id"):
        assert after.fields["session_id"] == before["session_id"]
    assert after.fields["status"] == "blocked" and after.fields["agent"] == "codex"
    assert after.fields["claim"] and after.fields["finalized"] == "true"
    preserved = json.loads(meta.read_text())
    assert preserved["worker_pid"] == 123 and preserved["foreign"] == {"keep": True}
    assert preserved["baseline_sha"] == "exact"


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_terminal_aggregate_refuses_contradictory_receipts(tmp_path):
    import copy
    from types import SimpleNamespace

    from vibecrafted_core.supervisor_async import _workflow_parent_telemetry

    result, launch, _, parent, _ = _public_run(tmp_path, "converge", count=2)
    assert result.returncode == 0
    meta_path = Path(launch["meta"])
    child_path = Path(parent["children"][0]["report"]).with_suffix(".meta.json")
    original_child = json.loads(child_path.read_text())
    handle = SimpleNamespace(
        command=tuple(launch["worker_command"]),
        meta_path=meta_path,
        run_id=launch["run_id"],
        root=Path(launch["root"]),
    )
    mutations = [
        {"run_id": "foreign-run"},
        {"tokens_input": 100},
        {"cost": {"amount": 0.0, "currency": "USD", "source": "unknown"}},
        {"usage": {**original_child["usage"], "events": 1, "tokens_input": -1}},
    ]
    for mutation in mutations:
        child = copy.deepcopy(original_child)
        child.update(mutation)
        child_path.write_text(json.dumps(child))
        refused = _workflow_parent_telemetry(handle, None)
        assert refused.usage.flat()["tokens_input"] == "unknown"
        assert "refused" in refused.usage.tokens_input.reason
        assert refused.cost.flat()["cost_usd"] == "unknown"
        child_path.write_text(json.dumps(original_child))
    duplicate = {**parent, "children": parent["children"] + [parent["children"][0]]}
    meta_path.write_text(json.dumps(duplicate))
    refused = _workflow_parent_telemetry(handle, None)
    assert "identity_conflict" in refused.usage.tokens_input.reason


@pytest.mark.skipif(
    sys.platform != "darwin", reason="requires actual macOS source guard"
)
@pytest.mark.parametrize("workflow", ["marbles", "polarize"])
@pytest.mark.parametrize("count", [2, 5])
def test_public_cumulative_cycle_has_one_independent_final_read(
    tmp_path, workflow, count
):
    result, receipt, completion, parent, plan = _public_run(
        tmp_path,
        "converge",
        count=count,
        workflow=workflow,
        silence="30",
        deck=True,
        extra_args=("--model", "gpt-6.1-sol", "--depth", "7", "--effort", "high"),
    )
    assert result.returncode == 0, (
        result.stdout[-2000:]
        + result.stderr
        + json.dumps(parent.get("lifecycle", {}).get("error"))
    )
    lifecycle = parent["lifecycle"]
    assert lifecycle["status"] == "completed", json.dumps(lifecycle)
    assert [stage["workflow"] for stage in lifecycle["stages"]] == [workflow, "audit"]
    write, audit = lifecycle["stages"]
    cycle = write["write_cycle"]
    assert len(cycle["workers"]) == cycle["workers_completed"] == count
    assert cycle["workers"][0]["input"] == cycle["input"]
    for one, two in zip(cycle["workers"], cycle["workers"][1:]):
        assert one["output"] == two["input"]
        assert one["input"]["head"] != one["output"]["head"] != two["output"]["head"]
    assert (
        cycle["workers"][-1]["output"]
        == cycle["output"]
        == audit["cycle_read"]["output"]
    )
    assert audit["cycle_read_evidence"]["verdict"] == "converged"
    metadata = json.loads(Path(audit["launch"]["meta"]).read_text())
    assert metadata["receiver_guard"]["kind"] == "macos_source_write_deny"
    assert metadata["model_requested"] == "gpt-6.1-sol"
    for worker in cycle["workers"]:
        capture = json.loads(
            Path(worker["report"]).with_suffix(".capture.json").read_text()
        )
        assert plan.read_text().strip() in capture["prompt"]
        assert worker["input"]["head"] in capture["prompt"]
        assert "Depth target: 7" in capture["prompt"]
        child_meta = json.loads(
            Path(worker["report"]).with_suffix(".meta.json").read_text()
        )
        assert child_meta["model_requested"] == "gpt-6.1-sol"
        assert child_meta["effort_requested"] == "high"
        assert not capture["receiver"]
    assert all(stage["status"] == "completed" for stage in lifecycle["stages"])
    assert not completion["timed_out"]
    assert receipt["worker_command"][-1] == "--lifecycle"
    assert receipt["root"] == cycle["input"]["root"]


@pytest.mark.skipif(sys.platform != "darwin", reason="public lifecycle source guard")
def test_public_stop_uses_lifecycle_owner_and_reaps_inert_write(
    tmp_path, monkeypatch, request
):
    import time

    from vibecrafted_core.workflow import stop_run

    result, launch, _, _, _ = _public_run(tmp_path, "silent", count=2, await_run=False)
    assert result.returncode == 0
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "vc-home"))
    request.addfinalizer(
        lambda: stop_run(launch["run_id"], reason="inert stop fixture finalizer")
    )
    home = tmp_path / "vc-home"
    deadline = time.monotonic() + 15
    leaf = None
    while time.monotonic() < deadline:
        leaves = list(home.rglob("*marbles-L1.meta.json"))
        if leaves:
            leaf = json.loads(leaves[0].read_text())
            if leaf.get("worker_pid"):
                break
        time.sleep(0.05)
    assert leaf and leaf.get("worker_pid"), "inert WRITE never started"
    state_path = (
        home / "control_plane" / "lifecycle_runs" / launch["run_id"] / "state.json"
    )
    state = json.loads(state_path.read_text())
    stage = state["stages"][-1]
    stage_meta = json.loads(Path(stage["launch"]["meta"]).read_text())
    parent_meta = json.loads(Path(launch["meta"]).read_text())
    pids = [leaf["worker_pid"], stage_meta["worker_pid"], parent_meta["worker_pid"]]
    stopped = stop_run(
        launch["run_id"], reason="private inert-provider stop acceptance"
    )
    assert stopped["accepted"]

    def productive(pid):
        observed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(observed.stdout.strip()) and not observed.stdout.strip().startswith(
            "Z"
        )

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and any(productive(pid) for pid in pids):
        time.sleep(0.1)
    assert not any(productive(pid) for pid in pids), pids
    terminal = json.loads(state_path.read_text())
    assert terminal["status"] == terminal["execution_state"] == "interrupted"
    assert terminal["operator_actions"][-1]["action"] == "interrupt_workflow"
    assert not list(home.rglob("*marbles-L2.meta.json"))


@pytest.mark.skipif(sys.platform != "darwin", reason="public source READ guard")
def test_public_productive_child_outlives_outer_silence_bound(tmp_path):
    result, launch, completion, parent, _ = _public_run(
        tmp_path, "productive", count=2, silence="8", deck=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not completion["timed_out"]
    write, audit = parent["lifecycle"]["stages"]
    assert audit["workflow"] == "audit"
    workers = write["write_cycle"]["workers"]
    assert len(workers) == write["write_cycle"]["workers_completed"] == 2
    assert workers[0]["output"] == workers[1]["input"]
    assert workers[0]["input"]["head"] != workers[0]["output"]["head"]
    assert workers[1]["input"]["head"] != workers[1]["output"]["head"]
    assert (Path(launch["root"]) / "assigned.txt").read_text() == "2"
    assert len(parent["lifecycle"]["stages"]) == 2


@pytest.mark.skipif(sys.platform != "darwin", reason="public source child identity")
def test_public_live_child_resolves_exact_run_and_native_session(
    tmp_path, monkeypatch, request
):
    import time

    from vibecrafted_core import message_control
    from vibecrafted_core.workflow import stop_run

    result, launch, _, _, _ = _public_run(
        tmp_path, "silent", count=2, deck=True, await_run=False
    )
    assert result.returncode == 0
    home = tmp_path / "vc-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    request.addfinalizer(
        lambda: stop_run(launch["run_id"], reason="private fixture cleanup")
    )
    deadline = time.monotonic() + 20
    leaf = None
    while time.monotonic() < deadline:
        for path in home.rglob("*marbles-L1.meta.json"):
            leaf = json.loads(path.read_text())
            if leaf.get("agent_session_id"):
                break
        if leaf and leaf.get("agent_session_id"):
            break
        time.sleep(0.05)
    assert leaf and leaf.get("agent_session_id"), "inert child identity never published"
    child_id = leaf["run_id"]
    assert message_control.resolve_message_run(run_id=child_id) == child_id
    assert (
        message_control.resolve_message_run(session=leaf["agent_session_id"])
        == child_id
    )
    queue_capture = tmp_path / "queue.json"
    fixture_env = dict(os.environ)
    fixture_env.update(
        HOME=str(tmp_path / "user"),
        PATH=str(tmp_path / "user/.local/bin") + os.pathsep + os.environ["PATH"],
        R2_QUEUE_CAPTURE=str(queue_capture),
    )
    # Only the private executable's queue branch runs; no provider API exists.
    sent = message_control.send_message(
        session=leaf["agent_session_id"],
        text="private fixture checkpoint",
        env=fixture_env,
    )
    assert sent["run_id"] == child_id
    assert sent["delivery_state"] == "provider_accepted"
    assert json.loads(queue_capture.read_text()) == [
        "queue",
        "--thread",
        leaf["agent_session_id"],
        "--message=private fixture checkpoint",
    ]
    assert message_control.receive_messages(run_id=child_id) == [sent]
    assert message_control.receive_messages(run_id=launch["run_id"]) == []
    with pytest.raises(
        message_control.MessageControlError, match="message_target_mismatch"
    ):
        message_control.acknowledge_message(sent["message_id"], run_id=launch["run_id"])
    ack = message_control.acknowledge_message(sent["message_id"], run_id=child_id)
    assert ack["delivery_state"] == "agent_acknowledged"
    assert message_control.receive_messages(run_id=child_id) == []
    from vibecrafted_core.control_plane import await_run, resolve_run

    assert (
        resolve_run(child_id).meta
        == home / "control_plane/runtime_runs" / child_id / "meta.json"
    )
    stopped = stop_run(child_id, reason="private exact-child stop", grace_seconds=2)
    assert stopped["accepted"] and stopped["alive_after_grace"] is False
    awaited = await_run(child_id, timeout_seconds=3, interval_seconds=0.05)
    assert awaited["completed"] and not awaited["worker_alive"]
    assert (
        message_control.resolve_message_run(session=leaf["agent_session_id"])
        == child_id
    )
    assert not list(home.rglob("*marbles-L2.meta.json"))


@pytest.mark.skipif(sys.platform != "darwin", reason="public source nested silence")
def test_public_nested_output_stops_and_true_silence_is_bounded(tmp_path):
    result, _launch, _completion, parent, _ = _public_run(
        tmp_path, "idle", count=2, silence="4", deck=True
    )
    assert result.returncode != 0
    assert parent["status"] != "completed"
    assert not list((tmp_path / "vc-home").rglob("*marbles-L2.meta.json"))
