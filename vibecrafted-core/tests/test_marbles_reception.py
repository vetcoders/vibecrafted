"""Executable convergence counterexamples, including the public source launcher."""

from __future__ import annotations

import json
import os
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
        prompt = argv[argv.index('-p') + 1] if Path(sys.argv[0]).name == 'kimi' else sys.stdin.read()
        if Path(sys.argv[0]).name == 'agy' and prompt.startswith('{'):
            prompt = json.loads(prompt)['message']['content'][0]['text']
        report = Path(os.environ['VIBECRAFTED_REPORT_PATH'])
        root = Path.cwd()
        receiver = prompt.startswith('You are the informed READ-only')
        round_id = os.environ['VIBECRAFTED_RUN_ID']
        index = int(re.search(r'-L([0-9]+)', round_id).group(1))
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
        for _ in range(12):
            # No human display fragment: still real productive stream activity.
            print(json.dumps({'type':'item.started','item':{'type':'command_execution'}}), flush=True)
            time.sleep(.025)
        _, header, _ = report.read_text().split('---', 2)
        fields = yaml.safe_load(header)
        status = 'blocked' if scenario == 'worker-blocked' and not receiver else 'completed'
        fields.update(status=status, finalized=True, claim='fixture verified actual assigned source')
        body = 'one bounded fixture cut'
        if not receiver:
            assert 'prior accepted evidence' not in prompt
            assert 'marbles-reception.v1' not in prompt
            assert 'scope_evidence' not in prompt
            assert 'reception.json' not in prompt
            if scenario == 'silent':
                time.sleep(120)
            if scenario == 'empty-commits':
                subprocess.run(['git','commit','--allow-empty','-q','-m',f'empty fixture {index}'],check=True)
            elif scenario != 'no-progress':
                (root / 'assigned.txt').write_text(str(index))
                subprocess.run(['git','add','assigned.txt'],check=True)
                subprocess.run(['git','commit','-q','-m',f'fixture cut {index}'],check=True)
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
            if scenario == 'receiver-mutates':
                (root / 'assigned.txt').write_text('illegal READ edit')
            if scenario == 'plain-blocked':
                data.update(scope_verified=False, gate='unknown', blocker='required external acceptance unavailable')
            if scenario == 'external-blocker':
                data['blocker'] = 'external decision needed'
            body = '```marbles-reception\n' + json.dumps(data) + '\n```\n'
        final_report = '---\n' + yaml.safe_dump(fields) + '---\n' + body + '\n'
        if receiver and scenario in {'stdout','plain-stdout','plain-blocked','plain-invalid'}:
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


def _public_run(tmp_path, scenario, *, count=3, agent="codex", silence="1.5"):
    from test_cli_execution_controls import _git_repo

    candidate = tmp_path / "candidate"
    baseline = _git_repo(candidate)
    (candidate / "assigned.txt").write_text("0")
    subprocess.run(["git", "add", "assigned.txt"], cwd=candidate, check=True)
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
    result = subprocess.run(
        [
            str(Path(sys.executable).parent / "vibecrafted"),
            "marbles",
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
            "--await",
        ],
        cwd=source_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
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
def test_public_marbles_verifies_then_continues_without_worker_history(tmp_path, agent):
    from vibecrafted_core.report_contract import parse_report_path

    result, receipt, completion, parent, plan = _public_run(
        tmp_path, "converge", agent=agent
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert parent["exit_code"] == 0 and parent["status"] == "completed"
    assert parent["reception"]["verdict"] == "converged"
    assert len(parent["children"]) == len(parent["receivers"]) == 2
    assigned = Path(receipt["root"])
    assert assigned != Path(receipt["parent_root"])
    assert (assigned / "assigned.txt").read_text() == "2"
    assert (Path(receipt["parent_root"]) / "assigned.txt").read_text() == "0"
    rounds = parent["reception"]["rounds"]
    assert rounds[0]["decision"] == "continue"
    assert rounds[1]["decision"] == "stop" and rounds[1]["delta_weighted"] == 3
    assert (
        rounds[0]["source"]["root"]
        == rounds[1]["source"]["root"]
        == str(assigned.resolve())
    )
    assert rounds[0]["candidate_sha"] != rounds[1]["candidate_sha"]
    mode = "native_resume" if agent in {"codex", "claude"} else "fresh_informed"
    assert all(round["receiver_mode"] == mode for round in rounds)
    for child in parent["children"]:
        captured = json.loads(
            Path(child["report"]).with_suffix(".capture.json").read_text()
        )
        assert plan.read_text() in captured["prompt"]
        assert not captured["receiver"] and child["artifact_ok"]
        assert parse_report_path(Path(child["report"])).ok
    receiver_prompt = json.loads(
        Path(parent["receivers"][1]["report"]).with_suffix(".capture.json").read_text()
    )["prompt"]
    assert "assigned-gap" in receiver_prompt and "delta_weighted" in receiver_prompt
    report = parse_report_path(Path(receipt["report"]))
    assert report.ok and report.fields["finalized"] == "true"
    assert report.fields["claim"] and report.run_id == receipt["run_id"]
    assert completion["timed_out"] is False
    for receiver in parent["receivers"]:
        captured = json.loads(
            Path(receiver["report"]).with_suffix(".capture.json").read_text()
        )
        assert captured["receiver"]
        if agent == "codex":
            assert "--dangerously-bypass-approvals-and-sandbox" in captured["argv"]
            assert 'sandbox_mode="read-only"' not in captured["argv"]
        meta = json.loads(
            Path(receiver["report"]).with_suffix(".meta.json").read_text()
        )
        assert meta["receiver_guard"]["kind"] == "macos_source_write_deny"
        if agent == "codex":
            assert (
                meta["receiver_guard"]["native_tool_sandbox"]
                == "bypassed_under_external_guard"
            )


@pytest.mark.parametrize(
    ("scenario", "count", "verdict", "status", "children"),
    [
        ("converge", 1, "not_converged", "completed", 1),
        ("worker-blocked", 3, "blocked", "blocked", 1),
        ("worker-failed", 3, "blocked", "failed", 1),
        ("invalid", 3, "blocked", "blocked", 1),
        ("identity-mismatch", 3, "blocked", "blocked", 1),
        ("missing-disposition", 3, "blocked", "blocked", 1),
        ("receiver-mutates", 3, "blocked", "blocked", 1),
        ("external-blocker", 3, "blocked", "blocked", 1),
        ("red", 3, "blocked", "blocked", 2),
        ("no-progress", 3, "blocked", "blocked", 2),
        ("empty-commits", 3, "blocked", "blocked", 2),
    ],
)
@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_marbles_execution_is_not_convergence(
    tmp_path, scenario, count, verdict, status, children
):
    _, receipt, completion, parent, _ = _public_run(tmp_path, scenario, count=count)
    assert parent["status"] == status
    assert parent["reception"]["verdict"] == verdict
    assert len(parent["children"]) == children
    assert parent["exit_code"] == (0 if status == "completed" else 1)
    assert completion["timed_out"] is False
    assert Path(receipt["report"]).is_file()
    if scenario == "converge":
        assert parent["reception"]["reason"] == "round_budget_exhausted"
    if scenario == "receiver-mutates":
        assert (Path(receipt["root"]) / "assigned.txt").read_text() == "1"
        assert (
            "PermissionError" in Path(parent["receivers"][0]["transcript"]).read_text()
        )


@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_receiver_can_emit_report_without_file_write(tmp_path):
    result, _, _, parent, _ = _public_run(tmp_path, "stdout", count=2)
    assert result.returncode == 0
    assert parent["reception"]["verdict"] == "converged"
    assert len(parent["receivers"]) == 2


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS guarded public workflow")
def test_public_parent_report_retains_prelaunch_machine_identity(tmp_path):
    from vibecrafted_core.report_contract import parse_report_path

    result, launch, _, parent, _ = _public_run(tmp_path, "converge", count=1)
    assert result.returncode == 0
    report = parse_report_path(Path(launch["report"]))
    canonical = parent["runtime_session_id"]
    assert canonical and canonical not in {"pending-unset", "aggregated", "unknown"}
    assert report.run_id == parent["run_id"] == launch["run_id"]
    assert report.fields["session_id"] == parent["session_id"] == canonical
    assert parent["launch_session_id"] == launch["session_id"]
    assert parent["runtime_session_id"] == parent["vibecrafted_session_id"]
    assert parent["agent_session_id"] == ""
    assert parent["provider_session_id"]["value"] == "unknown"
    assert f"session_id: {canonical}" in report.body.split("run_closure:", 1)[1]


@pytest.mark.parametrize(
    ("scenario", "status", "finalized"),
    [
        ("plain-stdout", "completed", "true"),
        ("plain-blocked", "blocked", "true"),
        ("plain-invalid", "completed", "false"),
    ],
)
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS public receiver fixture")
def test_plain_stdout_receiver_attestation_follows_verified_evidence(
    tmp_path, scenario, status, finalized
):
    from vibecrafted_core.report_contract import parse_report_path

    _, launch, _, parent, _ = _public_run(tmp_path, scenario, count=2)
    for receiver in parent["receivers"]:
        parsed = parse_report_path(Path(receiver["report"]))
        assert parsed.ok and parsed.run_id == receiver["run_id"]
        assert parsed.fields["session_id"] == receiver["agent_session_id"]
        assert parsed.fields["status"] == parsed.fields["claim_status"] == status
        assert parsed.fields["finalized"] == finalized
        assert bool(parsed.fields.get("claim")) is (finalized == "true")
        meta = json.loads(
            Path(receiver["report"]).with_suffix(".meta.json").read_text()
        )
        assert meta["status"] == "completed" and meta["exit_code"] == 0
    assert parent["reception"]["verdict"] == (
        "converged" if scenario == "plain-stdout" else "blocked"
    )
    assert parent["run_id"] == launch["run_id"]


@pytest.mark.parametrize("measured", [True, False])
@pytest.mark.skipif(sys.platform != "darwin", reason="requires macOS source READ guard")
def test_public_parent_settlement_preserves_measured_phase_usage(
    tmp_path, monkeypatch, measured
):
    from vibecrafted_core.report_contract import parse_report_path

    provider = _provider

    def instrumented(directory, *, scenario):
        provider(directory, scenario=scenario)
        with (directory / "codex").open("a") as stream:
            # Deliberately unpriced: usage is measured, dollar cost is not zero.
            stream.write(
                "\nprint('[12:00:00] model: unpriced-fixture-model',flush=True)\n"
            )
            if measured:
                stream.write(
                    "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':20,'output_tokens':10}}),flush=True)\n"
                )

    monkeypatch.setattr(sys.modules[__name__], "_provider", instrumented)
    result, launch, _, meta, _ = _public_run(tmp_path, "converge", count=2)
    assert result.returncode == 0
    phases = meta["children"] + meta["receivers"]
    assert len(phases) == 4
    report = parse_report_path(Path(launch["report"]))
    assert meta["usage"]["source"] == report.fields["usage_source"] == "children_sum"
    expected = 400 if measured else "unknown"
    assert meta["tokens_input"] == expected
    assert report.fields["tokens_input"] == str(expected)
    assert meta["tokens_output"] == (40 if measured else "unknown")
    assert meta["tokens_total"] == (440 if measured else "unknown")
    assert meta["cost_usd"] == report.fields["cost_usd"] == "unknown"
    assert meta["provider_session_id"]["value"] == "unknown"
    assert meta["provider_session_source"] == "none"
    assert meta["agent_model"] == report.fields["model"] == "aggregated"
    closure = report.body.split("run_closure:", 1)[1]
    assert f"tokens_input: {expected}" in closure
    assert "cost_usd: unknown" in closure
    assert "cost_source: unknown" in closure
    assert meta["cost_source"] == report.fields["cost_source"] == "unknown"
    if measured:
        assert sum(row["tokens_input"] for row in phases) == meta["tokens_input"]
        assert meta["usage"]["events"] == 4
    else:
        assert all(row["tokens_input"] == "unknown" for row in phases)


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
    assert parent["status"] == parent["reception"]["verdict"] == "blocked"
    assert parent["reception"]["reason"].startswith("receiver_source_guard_unavailable")
    assert parent["reception"]["execution_status"] == "completed"
    assert len(parent["children"]) == 1 and parent["children"][0]["exit_code"] == 0
    assert parent["receivers"] == []


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
