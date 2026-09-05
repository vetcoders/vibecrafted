from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import cli, lifecycle_delivery


def _accepted_launch_payload() -> dict[str, object]:
    return {
        "accepted": True,
        "message": "Launched implement via Vibecrafted core runtime.",
        "run_id": "impl-260613-145127-33000",
        "agent": "codex",
        "skill": "implement",
        "root": "/repo",
        "dispatch": 0,
        "status": "launching",
        "control": "/home/.vibecrafted/control_plane/runs/impl-260613.json",
        "report": "/home/.vibecrafted/artifacts/report.md",
        "transcript": "/home/.vibecrafted/artifacts/report.transcript.log",
    }


def _stub_server_observation(
    monkeypatch: pytest.MonkeyPatch, run: dict[str, object]
) -> None:
    monkeypatch.setattr(
        cli,
        "resolve_server_run_id",
        lambda _agent, run_id, *, last: run_id or (str(run["run_id"]) if last else ""),
    )
    monkeypatch.setattr(
        cli,
        "observe_run_from_server",
        lambda _run_id: {
            "schema": "vibecrafted.run-observation.v1",
            "found": True,
            "run": run,
        },
    )


def test_root_cli_without_command_returns_product_help(capsys) -> None:
    assert cli.main([]) == 0

    output = capsys.readouterr().out
    assert "release engine for AI-developed software" in output
    assert "Ship cycle:" in output
    assert "Vibecrafted core command surface" not in output


@pytest.mark.parametrize("launcher", cli.LAUNCHERS)
def test_every_workflow_help_uses_the_core_product_surface(
    launcher: str, capsys
) -> None:
    assert cli.main([launcher, "--help"]) == 0

    output = capsys.readouterr().out
    assert "Usage:" in output
    assert "Flow:" in output
    assert "Examples:" in output
    assert f"launch vc-{launcher} through core runtime" not in output


def test_help_topic_and_direct_flag_render_identically(capsys) -> None:
    assert cli.main(["help", "marbles"]) == 0
    topic_output = capsys.readouterr().out

    assert cli.main(["marbles", "codex", "--help"]) == 0
    direct_output = capsys.readouterr().out

    assert topic_output == direct_output
    assert "one dedicated orchestrator tab" in topic_output
    assert "L1…LN" in topic_output


def test_resume_session_help_topic_matches_direct_flag(capsys) -> None:
    assert cli.main(["help", "resume-session"]) == 0
    topic_output = capsys.readouterr().out

    assert cli.main(["resume-session", "--help"]) == 0
    direct_output = capsys.readouterr().out

    assert topic_output == direct_output
    assert "--agent-session-id <id>" in topic_output
    assert "tracked, detached headless run" in topic_output


def test_bare_partner_delegates_to_deck_not_launch_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[object] = []
    runs: list[list[str]] = []

    def fake_launch(*_args, **_kwargs):
        launches.append(1)
        raise AssertionError("bare partner must not call launch_workflow")

    def fake_run(cmd, **_kwargs):
        runs.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cli.main(["partner", "claude"]) == 0
    assert launches == []
    assert runs
    assert runs[0][1:] == ["partner", "claude"]


def test_partner_with_prompt_delegates_to_deck_not_launch_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[object] = []
    runs: list[list[str]] = []

    def fake_launch(*_args, **_kwargs):
        launches.append(1)
        raise AssertionError("partner --prompt must not call launch_workflow")

    def fake_run(cmd, **_kwargs):
        runs.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert cli.main(["partner", "claude", "--prompt", "do the cut"]) == 0
    assert launches == []
    assert runs
    assert runs[0][1:] == ["partner", "claude", "--prompt", "do the cut"]


def test_core_parser_accepts_the_short_prompt_and_file_flags() -> None:
    parser = cli._build_parser()

    prompt = parser.parse_args(["implement", "codex", "-p", "ship it"])
    file_input = parser.parse_args(["review", "claude", "-f", "brief.md"])

    assert prompt.prompt == "ship it"
    assert file_input.file == "brief.md"


def test_workflow_prompt_stdin_stays_out_of_argv_and_temp_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    seen: dict[str, object] = {}

    def fake_launch(spec, source_dir):
        seen["spec"] = spec
        seen["source_dir"] = source_dir
        return {
            "accepted": True,
            "run_id": "impl-stdin-1",
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            "status": "launching",
        }

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("secret prompt from stdin"))

    rc = cli.main(
        [
            "implement",
            "codex",
            "--prompt-stdin",
            "--runtime",
            "headless",
            "--root",
            str(tmp_path),
            "--json",
        ]
    )

    assert rc == 0
    spec = seen["spec"]
    assert spec.prompt == "secret prompt from stdin"
    assert spec.file == ""
    body = json.loads(capsys.readouterr().out)
    assert body["run_id"] == "impl-stdin-1"
    assert body["accepted"] is True
    assert body["agent"] == "codex"
    assert body["root"] == str(tmp_path)
    assert body["status"] == "launching"


def test_review_from_home_uses_selected_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "repo"
    home.mkdir()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    seen: dict[str, object] = {}

    def fake_launch(spec, source_dir):
        seen["root"] = spec.root
        return {"accepted": True, "run_id": "revi-home-1"}

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(workspace))
    monkeypatch.chdir(home)
    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    rc = cli.main(
        [
            "review",
            "codex",
            "--prompt",
            "look",
            "--runtime",
            "headless",
            "--json",
        ]
    )

    assert rc == 0
    assert Path(str(seen["root"])) == workspace.resolve()


def test_review_from_home_without_workspace_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VIBECRAFTED_WORKSPACE_ROOT", raising=False)
    monkeypatch.chdir(home)

    def fail_launch(_spec, _source_dir):
        raise AssertionError("must not launch against $HOME")

    monkeypatch.setattr(cli, "launch_workflow", fail_launch)
    rc = cli.main(
        [
            "review",
            "codex",
            "--prompt",
            "look",
            "--runtime",
            "headless",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "refusing to launch against the home directory" in captured.err


def test_resume_session_reads_prompt_from_stdin_and_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    seen: dict[str, object] = {}

    def fake_resume(
        agent: str,
        agent_session_id: str,
        source_dir: str | Path,
        **kwargs: object,
    ) -> dict[str, object]:
        seen.update(
            {
                "agent": agent,
                "agent_session_id": agent_session_id,
                "source_dir": source_dir,
                **kwargs,
            }
        )
        return {
            "schema": "vibecrafted.manual_explicit_resume.v1",
            "accepted": True,
            "run_id": "rsme-manual-1",
            "agent": agent,
            "agent_session_id": agent_session_id,
            "runtime_session_id": "runtime-manual-1",
            "resume_mode": "manual_explicit",
            "skill": "workflow",
            "root": str(tmp_path),
            "status": "launching",
        }

    monkeypatch.setattr(cli, "manual_resume_session", fake_resume)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("continue without argv"))

    rc = cli.main(
        [
            "resume-session",
            "codex",
            "--agent-session-id",
            "codex-thread-42",
            "--prompt-stdin",
            "--root",
            str(tmp_path),
            "--json",
        ]
    )

    assert rc == 0
    assert seen["agent"] == "codex"
    assert seen["agent_session_id"] == "codex-thread-42"
    assert seen["prompt"] == "continue without argv"
    assert seen["root"] == str(tmp_path)
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "vibecrafted.manual_explicit_resume.v1"
    assert payload["resume_mode"] == "manual_explicit"
    assert payload["run_id"] == "rsme-manual-1"


def test_resume_session_prints_dedicated_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setattr(
        cli,
        "manual_resume_session",
        lambda agent, agent_session_id, _source_dir, **_kwargs: {
            "schema": "vibecrafted.manual_explicit_resume.v1",
            "accepted": True,
            "run_id": "rsme-manual-2",
            "agent": agent,
            "agent_session_id": agent_session_id,
            "runtime_session_id": "runtime-manual-2",
            "resume_mode": "manual_explicit",
            "skill": "workflow",
            "root": str(tmp_path),
            "status": "launching",
            "control": "/tmp/rsme-manual-2.json",
            "transcript": "/tmp/rsme-manual-2.log",
        },
    )

    rc = cli.main(
        [
            "resume-session",
            "claude",
            "--agent-session-id",
            "claude-session-7",
            "--prompt",
            "continue",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "MANUAL EXPLICIT RESUME RECEIPT" in output
    assert "rsme-manual-2" in output
    assert "claude-session-7" in output
    assert "runtime-manual-2" in output
    assert "resume_mode:        manual_explicit" in output


def test_lifecycle_deck_inherits_verified_installer_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("installer lease descriptors are a POSIX contract")

    import fcntl

    tools_home = tmp_path / "tools"
    deck = tools_home / "vibecrafted-current" / "scripts" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_text(
        "#!/bin/sh\n"
        'exec "$VIBECRAFTED_TEST_PYTHON" -c '
        "'import os; "
        'os.fstat(int(os.environ["VIBECRAFTED_INSTALL_LEASE_FD"]))'
        "'\n",
        encoding="utf-8",
    )
    deck.chmod(0o755)
    lock_path = tools_home / ".vibecrafted-install.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setenv("VIBECRAFTED_INSTALL_LEASE_FD", str(descriptor))
    monkeypatch.setenv("VIBECRAFTED_TEST_PYTHON", sys.executable)

    try:
        assert cli.main(["server", "service", "install"]) == 0
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def test_lifecycle_deck_refuses_unverified_installer_descriptor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if os.name != "posix":
        pytest.skip("installer lease descriptors are a POSIX contract")

    tools_home = tmp_path / "tools"
    deck = tools_home / "vibecrafted-current" / "scripts" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (tools_home / ".vibecrafted-install.lock").touch(mode=0o600)
    descriptor = os.open("/dev/null", os.O_RDONLY)
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setenv("VIBECRAFTED_INSTALL_LEASE_FD", str(descriptor))
    monkeypatch.setattr(
        "subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an unverified descriptor must never reach the deck")
        ),
    )

    try:
        assert cli.main(["server", "service", "install"]) == 75
    finally:
        os.close(descriptor)

    assert "installer coordination descriptor does not own" in capsys.readouterr().err


def test_resettle_names_automatic_sources_and_explicit_override(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        lifecycle_delivery,
        "resettle_retained_snapshots",
        lambda **_kwargs: {
            "ok": True,
            "scanned": 1,
            "rewritten": 0,
            "unchanged": 1,
            "skipped": 0,
            "dry_run": True,
            "before": {"f": 1, "x": 0, "n": 0, "invalid": 0},
            "after": {"f": 1, "x": 0, "n": 0, "invalid": 0},
        },
    )

    assert cli._cmd_resettle(SimpleNamespace(dry_run=True, json=False)) == 0

    output = capsys.readouterr().out
    assert "automatic FINALIZED" in output
    assert "operator waive remains an explicit override" in output
    assert "never from bare exit 0" in output


def test_literal_help_prompt_still_launches(monkeypatch, capsys) -> None:
    seen = {}

    def fake_launch(spec, _source_dir):
        seen["prompt"] = spec.prompt
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["implement", "codex", "--prompt", "help"]) == 0

    assert seen["prompt"] == "help"
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("wrapper", "verb"),
    [
        ("telemetry", "telemetry"),
        ("vc-dashboard", "dashboard"),
        ("vc-dispatch", "dispatch"),
        ("vc-help", "help"),
        ("vc-init", "init"),
        ("vc-justdo", "justdo"),
        ("vc-resume", "resume"),
        ("vc-start", "start"),
    ],
)
def test_shell_wrapper_entrypoints_preserve_their_deck_verb(
    monkeypatch, tmp_path: Path, wrapper: str, verb: str
) -> None:
    tools_home = tmp_path / "tools"
    deck = tools_home / "vibecrafted-current" / "scripts" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    seen: dict[str, object] = {}

    def fake_run(argv, check=False):
        seen["argv"] = argv
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setattr("sys.argv", [wrapper, "sentinel"])
    monkeypatch.setattr("subprocess.run", fake_run)

    assert cli.main() == 0
    assert seen["argv"] == [str(deck), verb, "sentinel"]


def test_shell_wrapper_missing_deck_fails_loudly(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    tools_home = tmp_path / "tools"
    missing_deck = tmp_path / "missing" / "vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setattr("sys.argv", ["vc-init", "codex"])
    monkeypatch.setattr(cli, "deck_path", lambda: missing_deck)

    assert cli.main() == 1
    assert (
        f"error: vc-init cannot find the runtime deck at {missing_deck}"
        in capsys.readouterr().err
    )


def test_version_reads_installed_package_never_delegates_to_deck(
    monkeypatch, capsys
) -> None:
    # `--version` / `-v` / `version` must report the INSTALLED runtime version
    # straight from the package, never shell out to the legacy bash deck (whose
    # `_version()` resolves VERSION from the current working directory, so invoked
    # inside a checkout it reports that checkout's version, not the installed one).
    # Make any deck subprocess fail loudly so a regression that re-delegates
    # `--version` cannot pass silently.
    from vibecrafted_core import __version__ as expected

    def _no_deck(*_args, **_kwargs):
        raise AssertionError("--version must not shell out to the deck")

    monkeypatch.setattr("subprocess.run", _no_deck)

    for flag in ("--version", "-v", "version"):
        assert cli.main([flag]) == 0
        assert capsys.readouterr().out.strip() == f"vibecrafted {expected}"


def test_root_cli_accepts_justdo_as_own_skill(monkeypatch, capsys) -> None:
    seen = {}

    def fake_launch(spec, source_dir):
        seen["skill"] = spec.skill
        seen["agent"] = spec.agent
        seen["source_dir"] = source_dir
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["justdo", "codex", "--prompt", "ship it"]) == 0

    assert seen["skill"] == "justdo"
    assert seen["agent"] == "codex"
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


def test_root_cli_research_agentless_and_positional_forms(monkeypatch, capsys) -> None:
    seen = []

    def fake_launch(spec, _source_dir):
        seen.append(spec)
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["research", "--prompt", "map it"]) == 0
    assert cli.main(["research", "codex", "agy", "--prompt", "map it"]) == 0

    assert seen[0].agent == "swarm"
    assert seen[0].research_agents == ()
    assert seen[0].research_synthesizer == ""
    assert seen[1].agent == "swarm"
    assert seen[1].research_agents == ("codex", "agy")
    assert seen[1].research_synthesizer == "codex"
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


def test_root_cli_launch_missing_work_prints_friendly_error(
    monkeypatch, capsys
) -> None:
    def fail_launch(_spec, _source_dir):
        raise AssertionError("launch_workflow should not run for invalid input")

    monkeypatch.setattr(cli, "launch_workflow", fail_launch)

    assert cli.main(["implement", "claude"]) == 2

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "Traceback" not in combined
    assert "ValueError" not in combined
    assert "error: Launch requires either --prompt text or --file path." in captured.err
    assert "vibecrafted implement claude --prompt 'what to do'" in captured.err
    assert "vibecrafted implement claude --file /path/to/brief.md" in captured.err


def test_root_cli_missing_file_fails_before_launch(
    monkeypatch, tmp_path, capsys
) -> None:
    def fail_launch(_spec, _source_dir):
        raise AssertionError("launch_workflow should not run for a missing file")

    monkeypatch.setattr(cli, "launch_workflow", fail_launch)
    missing = tmp_path / "missing-brief.md"

    assert cli.main(["implement", "codex", "--file", str(missing)]) == 2
    assert (
        f"Prompt file does not exist or is not a file: {missing}"
        in capsys.readouterr().err
    )


def test_root_cli_prune_without_work_uses_discovery_prompt(monkeypatch, capsys) -> None:
    seen = {}

    def fake_launch(spec, _source_dir):
        seen["skill"] = spec.skill
        seen["prompt"] = spec.prompt
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["prune", "claude"]) == 0

    assert seen["skill"] == "prune"
    assert "Repository health / prune ACTION run." in seen["prompt"]
    assert "No deletion on vibes. Prove every cut." in seen["prompt"]
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


def test_root_cli_prune_without_agent_defaults_to_claude(monkeypatch, capsys) -> None:
    seen = {}

    def fake_launch(spec, _source_dir):
        seen["agent"] = spec.agent
        seen["prompt"] = spec.prompt
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["prune"]) == 0

    assert seen["agent"] == "claude"
    assert "Repository health / prune ACTION run." in seen["prompt"]
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


def test_root_cli_defaults_headless_but_honors_explicit_terminal(
    monkeypatch, capsys
) -> None:
    seen = {}
    monkeypatch.setenv("VIBECRAFTED_OPERATOR_SESSION", "vc-frame")

    def fake_launch(spec, _source_dir):
        seen["runtime"] = spec.runtime
        return _accepted_launch_payload()

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    assert cli.main(["implement", "codex", "--prompt", "ship it"]) == 0

    assert seen["runtime"] == "headless"
    assert (
        cli.main(
            [
                "implement",
                "codex",
                "--prompt",
                "show it",
                "--runtime",
                "terminal",
            ]
        )
        == 0
    )
    assert seen["runtime"] == "terminal"
    assert "VIBECRAFTED LAUNCH RECEIPT" in capsys.readouterr().out


def test_root_cli_prints_full_launch_receipt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli, "launch_workflow", lambda _spec, _source: _accepted_launch_payload()
    )

    assert cli.main(["implement", "codex", "--prompt", "ship it"]) == 0

    out = capsys.readouterr().out
    assert "==================== VIBECRAFTED LAUNCH RECEIPT ====================" in out
    assert "run_id:     impl-260613-145127-33000" in out
    assert "agent:      codex" in out
    assert "skill:      implement" in out
    assert "root:       /repo" in out
    assert "dispatch:   0" in out
    assert "status:     launching" in out
    assert "control:    /home/.vibecrafted/control_plane/runs/impl-260613.json" in out
    assert "report:     /home/.vibecrafted/artifacts/report.md" in out
    assert "transcript: /home/.vibecrafted/artifacts/report.transcript.log" in out
    assert (
        "observe:    vibecrafted observe codex --run-id impl-260613-145127-33000" in out
    )
    assert (
        "await (ARM NOW, supervisor-side): vibecrafted await codex --run-id impl-260613-145127-33000"
        in out
    )


def test_blocked_launch_receipt_prints_reasons_inline(capsys) -> None:
    payload = _accepted_launch_payload()
    payload.update(
        {
            "status": "blocked",
            "reasons": ["Foundation authority is unbound"],
        }
    )

    cli._print_launch_receipt(payload)

    assert "reasons:    Foundation authority is unbound" in capsys.readouterr().out


def test_root_cli_agent_observe_accepts_receipt_command(monkeypatch, capsys) -> None:
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "impl-1",
            "state": "process_spawned",
            "agent": "codex",
            "skill": "implement",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": "/tmp/transcript.log",
        },
    )

    assert cli.main(["codex", "observe", "--run-id", "impl-1"]) == 0

    out = capsys.readouterr().out
    assert "run_id:     impl-1" in out
    assert "report:     /tmp/report.md" in out


def test_root_cli_swarm_observe_accepts_research_receipt(monkeypatch, capsys) -> None:
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "rese-1",
            "state": "process_spawned",
            "agent": "swarm",
            "skill": "research",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": "/tmp/transcript.log",
        },
    )

    assert cli.main(["swarm", "observe", "--run-id", "rese-1"]) == 0

    assert "agent:      swarm" in capsys.readouterr().out


def test_root_cli_observe_hides_stale_error_after_report_validated(
    monkeypatch, capsys
) -> None:
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "rese-1",
            "state": "report_validated",
            "agent": "swarm",
            "skill": "research",
            "root": "/repo",
            "liveness": "terminal",
            "last_error": "launcher_pid is not alive; recovery_required",
            "latest_report": "/tmp/report.md",
            "latest_transcript": "/tmp/transcript.log",
        },
    )

    assert cli.main(["swarm", "observe", "--run-id", "rese-1"]) == 0

    out = capsys.readouterr().out
    assert "state:      report_validated" in out
    assert "last_error:" not in out


def test_root_cli_agent_observe_prints_transcript_tail(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        "\n".join(f"line {idx}" for idx in range(1, 66)) + "\n",
        encoding="utf-8",
    )
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "impl-1",
            "state": "stalled",
            "agent": "codex",
            "skill": "implement",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": str(transcript),
        },
    )

    assert cli.main(["codex", "observe", "--run-id", "impl-1"]) == 0

    out = capsys.readouterr().out
    assert "state:      stalled" in out
    assert "transcript_tail:" in out
    assert "line 65" in out
    assert "line 1" not in out


def test_root_cli_agent_observe_renders_json_transcript_tail(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        '{"type":"system","subtype":"hook_response","session_id":"claude-sess","output":"very noisy hook payload"}\n'
        '{"type":"system","subtype":"init","session_id":"claude-sess","model":"claude-opus-4-8"}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
        '{"type":"result","result":"done","usage":{"input_tokens":10,"cache_read_input_tokens":4,"output_tokens":2},"total_cost_usd":0.01}\n',
        encoding="utf-8",
    )
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "impl-1",
            "state": "report_validated",
            "agent": "claude",
            "skill": "implement",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": str(transcript),
        },
    )

    assert cli.main(["claude", "observe", "--run-id", "impl-1"]) == 0

    out = capsys.readouterr().out
    assert "transcript_tail:" in out
    assert "session: claude-sess" in out
    assert "model: claude-opus-4-8" in out
    assert "ok" in out
    assert "hook_response" not in out
    assert "very noisy hook payload" not in out


def test_root_cli_agent_observe_recovers_model_when_tail_starts_after_init(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        '{"type":"system","subtype":"hook_response","session_id":"claude-sess","output":"noise"}\n'
        '{"type":"assistant","session_id":"claude-sess","message":{"model":"claude-opus-4-8","content":[{"type":"text","text":"late body"}]}}\n',
        encoding="utf-8",
    )
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "impl-1",
            "state": "report_validated",
            "agent": "claude",
            "skill": "implement",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": str(transcript),
        },
    )

    assert cli.main(["claude", "observe", "--run-id", "impl-1"]) == 0

    out = capsys.readouterr().out
    assert "session: claude-sess model: claude-opus-4-8" in out
    assert "late body" in out
    assert "noise" not in out


def test_root_cli_agent_observe_uses_codex_config_model(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        '{"type":"thread.started","thread_id":"codex-thread"}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"codex body"}}\n',
        encoding="utf-8",
    )
    _stub_server_observation(
        monkeypatch,
        {
            "run_id": "impl-1",
            "state": "report_validated",
            "agent": "codex",
            "skill": "implement",
            "root": "/repo",
            "latest_report": "/tmp/report.md",
            "latest_transcript": str(transcript),
        },
    )

    assert cli.main(["codex", "observe", "--run-id", "impl-1"]) == 0

    out = capsys.readouterr().out
    assert "session: codex-thread model: gpt-5.5" in out
    assert "codex body" in out


def test_root_cli_agent_await_accepts_receipt_command(monkeypatch, capsys) -> None:
    run = {
        "run_id": "impl-1",
        "agent": "codex",
        "state": "report_validated",
        "skill": "implement",
        "root": "/repo",
        "artifact_ok": True,
        "latest_report": "/tmp/report.md",
        "latest_transcript": "/tmp/transcript.log",
    }
    monkeypatch.setattr(
        cli, "resolve_server_run_id", lambda *_args, **_kwargs: "impl-1"
    )
    monkeypatch.setattr(
        cli,
        "await_run_from_server",
        lambda run_id, **_kwargs: {
            "outcome": "terminal",
            "run_id": run_id,
            "found": True,
            "completed": True,
            "timed_out": False,
            "reason": "terminal",
            "worker_alive": False,
            "run": run,
        },
    )

    assert cli.main(["codex", "await", "--run-id", "impl-1", "--timeout", "0"]) == 0

    out = capsys.readouterr().out
    assert "await: completed" in out
    assert "state:      report_validated" in out


def test_root_cli_agent_await_fails_dead_stale_worker(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text("last useful line\n", encoding="utf-8")
    run = {
        "run_id": "impl-1",
        "agent": "codex",
        "state": "stalled",
        "liveness": "pid_gone",
        "skill": "implement",
        "root": "/repo",
        "updated_at": "2000-01-01T00:00:00+00:00",
        "latest_report": "/tmp/report.md",
        "latest_transcript": str(transcript),
    }
    monkeypatch.setattr(
        cli, "resolve_server_run_id", lambda *_args, **_kwargs: "impl-1"
    )
    monkeypatch.setattr(
        cli,
        "await_run_from_server",
        lambda run_id, **_kwargs: {
            "outcome": "idle_stall",
            "run_id": run_id,
            "found": True,
            "completed": False,
            "timed_out": True,
            "reason": "idle_stall",
            "worker_alive": False,
            "run": run,
        },
    )

    rc = cli.main(
        [
            "codex",
            "await",
            "--run-id",
            "impl-1",
            "--timeout",
            "30",
            "--stale-after",
            "600",
        ]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "await: timed out (idle_stall)" in captured.out
    assert "state:      stalled" in captured.out
    assert "last useful line" in captured.out


def test_root_cli_agent_await_rejects_completed_payload_when_worker_alive(
    monkeypatch, capsys
) -> None:
    run = {
        "run_id": "impl-live",
        "agent": "codex",
        "state": "running",
        "liveness": "pid_alive",
        "skill": "implement",
        "root": "/repo",
        "artifact_ok": True,
        "latest_report": "/tmp/report.md",
        "latest_transcript": "/tmp/transcript.log",
    }
    monkeypatch.setattr(
        cli, "resolve_server_run_id", lambda *_args, **_kwargs: "impl-live"
    )
    monkeypatch.setattr(
        cli,
        "await_run_from_server",
        lambda run_id, **_kwargs: {
            "outcome": "evidence_disagreement",
            "run_id": run_id,
            "found": True,
            "completed": True,
            "timed_out": False,
            "reason": "report_delivered",
            "worker_alive": True,
            "run": run,
        },
    )

    rc = cli.main(["codex", "await", "--run-id", "impl-live", "--timeout", "0"])

    assert rc == 3
    captured = capsys.readouterr()
    assert "non-terminal completion disagreement" in captured.err
    assert "state:      running" in captured.out


def test_root_cli_agent_await_does_not_require_server(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "await_run_from_server",
        lambda run_id, **_kwargs: {
            "run_id": run_id,
            "completed": True,
            "outcome": "terminal",
            "reason": "terminal",
            "worker_alive": False,
            "run": {"run_id": run_id, "state": "completed", "exit_code": 0},
        },
    )

    assert cli.main(["codex", "await", "--run-id", "impl-1"]) == 0
    assert "await: completed" in capsys.readouterr().out


@pytest.mark.parametrize("outcome", ["idle_stall", "hard_cap"])
def test_root_cli_agent_await_json_names_distinct_timeout_axes(
    outcome: str, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        cli, "resolve_server_run_id", lambda *_args, **_kwargs: "impl-1"
    )
    monkeypatch.setattr(
        cli,
        "await_run_from_server",
        lambda run_id, **_kwargs: {
            "schema": "vibecrafted.run-await-verdict.v1",
            "outcome": outcome,
            "reason": outcome,
            "run_id": run_id,
            "found": True,
            "completed": False,
            "timed_out": True,
            "idle_timeout_seconds": 7.0,
            "hard_cap_seconds": 11.0,
            "run": {"run_id": run_id, "state": "running"},
        },
    )

    assert (
        cli.main(
            [
                "codex",
                "await",
                "--run-id",
                "impl-1",
                "--timeout",
                "7",
                "--hard-cap",
                "11",
                "--json",
            ]
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == outcome
    assert payload["idle_timeout_seconds"] == 7.0
    assert payload["hard_cap_seconds"] == 11.0


def test_root_cli_doctor_routes_to_installer_doctor(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli.doctor_module,
        "doctor_run",
        lambda **_kwargs: [
            SimpleNamespace(level="ok", component="runtime", message="ready")
        ],
    )

    assert cli.main(["doctor", "--json"]) == 0

    out = capsys.readouterr().out
    assert '"component": "runtime"' in out
    assert '"failures": 0' in out


def test_root_cli_doctor_returns_failure_for_failed_findings(monkeypatch) -> None:
    monkeypatch.setattr(
        cli.doctor_module,
        "doctor_run",
        lambda **_kwargs: [
            SimpleNamespace(level="fail", component="runtime", message="broken")
        ],
    )

    assert cli.main(["doctor"]) == 1


def test_root_cli_doctor_release_forwards_the_flag(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _run(**kwargs):
        seen.update(kwargs)
        return [SimpleNamespace(level="ok", component="runtime", message="ready")]

    monkeypatch.setattr(cli.doctor_module, "doctor_run", _run)

    assert cli.main(["doctor", "--release"]) == 0
    assert seen.get("release") is True


def test_apply_live_liveness_flags_dead_launcher() -> None:
    """observe must not echo the snapshot's stale 'heartbeat' for a run whose
    launcher has already died (verification gap). A live pid check overrides it."""
    import os

    dead = cli._apply_live_liveness(
        {"launcher_pid": 999999, "liveness": "heartbeat", "state": "process_spawned"}
    )
    assert dead["liveness"] == "pid_gone"
    assert "not yet reconciled" in dead["liveness_note"]

    alive = cli._apply_live_liveness(
        {
            "launcher_pid": os.getpid(),
            "liveness": "heartbeat",
            "state": "process_spawned",
        }
    )
    assert alive["liveness"] == "heartbeat"

    # A completed run whose launcher legitimately exited stays terminal, not flagged.
    done = cli._apply_live_liveness(
        {"launcher_pid": 999999, "liveness": "terminal", "state": "completed"}
    )
    assert done["liveness"] == "terminal"

    # No pid recorded → cannot tell, leave the snapshot untouched.
    unknown = cli._apply_live_liveness({"liveness": "heartbeat", "state": "launching"})
    assert unknown["liveness"] == "heartbeat"


def test_apply_live_liveness_prefers_live_worker_over_dead_launcher() -> None:
    import os

    run = cli._apply_live_liveness(
        {
            "launcher_pid": 999999999,
            "worker_pid": os.getpid(),
            "worker_pgid": os.getpgrp(),
            "liveness": "heartbeat",
            "state": "process_spawned",
        }
    )

    assert run["liveness"] == "heartbeat"


# --- _tail_lines: coalescence + window-after-render (W1-A) ---------------


def _ansi_free(line: str) -> str:
    return cli.ANSI_PATTERN.sub("", line)


def _grok_token_transcript() -> tuple[list[str], list[str]]:
    """Per-token grok stream: one {"type":"thought","data":<token>} JSON event
    per word, newline tokens between sentences. >=120 events tokenizing
    >=3 full sentences — mirrors real grok streaming granularity. Sentences
    are kept short so each rendered line (with per-token ANSI dim wrappers)
    stays under the 500-raw-char _clip_line budget."""
    ordinals = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
    sentences = [
        f"Sentence {ordinal} of the grok stream keeps flowing token by token "
        "toward the final stop."
        for ordinal in ordinals
    ]
    events: list[str] = []
    for index, sentence in enumerate(sentences):
        if index:
            events.append(json.dumps({"type": "thought", "data": "\n"}))
        words = sentence.split(" ")
        events.append(json.dumps({"type": "thought", "data": words[0]}))
        for word in words[1:]:
            events.append(json.dumps({"type": "thought", "data": " " + word}))
    assert len(events) >= 120
    return sentences, events


def test_tail_lines_coalesces_grok_per_token_thoughts_into_sentences(
    tmp_path: Path,
) -> None:
    sentences, events = _grok_token_transcript()
    transcript = tmp_path / "transcript.log"
    transcript.write_text("\n".join(events) + "\n", encoding="utf-8")

    lines, error = cli._tail_lines(str(transcript), agent="grok")

    assert error == ""
    stripped = [_ansi_free(line) for line in lines]
    # A1: every sentence's tokens land in ONE rendered line, not token-per-line.
    for sentence in sentences:
        matches = [line for line in stripped if sentence in line]
        assert matches, f"sentence not coalesced into one line: {sentence!r}"
    # Not token-per-line: 8 sentences -> 8 rendered lines, not 120 token lines.
    assert len(lines) == len(sentences)


def test_tail_lines_window_is_rendered_lines_not_raw_events(
    tmp_path: Path,
) -> None:
    sentences, events = _grok_token_transcript()
    transcript = tmp_path / "transcript.log"
    transcript.write_text("\n".join(events) + "\n", encoding="utf-8")

    lines, error = cli._tail_lines(str(transcript), agent="grok")

    assert error == ""
    stripped = "\n".join(_ansi_free(line) for line in lines)
    # A2: content from beyond the last 40 raw JSON lines must be visible.
    # Sentence one lives >120 raw events before EOF; the old raw-first cut
    # (lines[-40:]) could never show it.
    assert sentences[0] in stripped
    last_40_raw_payload = "".join(
        json.loads(event).get("data", "") for event in events[-40:]
    )
    assert "Sentence one" not in last_40_raw_payload


def test_tail_lines_codex_shaped_fat_events_render_unchanged(
    tmp_path: Path,
) -> None:
    """Regression contract (A3): captured from _tail_lines BEFORE the
    coalescence change — fat multi-line codex events must render identically:
    one session header, each embedded text line on its own line, one tokens
    line. 7 lines total."""
    events = [
        {"type": "thread.started", "thread_id": "codex-thread"},
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "first paragraph line one\nfirst paragraph line two",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "second message\nwith three\nrendered lines",
            },
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 11, "cached_input_tokens": 4, "output_tokens": 6},
        },
    ]
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8"
    )

    lines, error = cli._tail_lines(str(transcript), agent="codex")

    assert error == ""
    stripped = [_ansi_free(line) for line in lines]
    assert len(stripped) == 7
    assert "session: codex-thread" in stripped[0]
    assert stripped[1:6] == [
        "first paragraph line one",
        "first paragraph line two",
        "second message",
        "with three",
        "rendered lines",
    ]
    assert "tokens: 11 in (4 cached) / 6 out" in stripped[6]


@pytest.mark.parametrize("agent", ["", "grok", "codex"])
def test_tail_lines_missing_path_and_file_edges(agent: str, tmp_path: Path) -> None:
    assert cli._tail_lines("", agent=agent) == ([], "missing_path")
    assert cli._tail_lines(str(tmp_path / "absent.log"), agent=agent) == (
        [],
        "missing_file",
    )


@pytest.mark.parametrize("agent", ["", "grok", "codex"])
def test_tail_lines_empty_file_edge(agent: str, tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text("", encoding="utf-8")
    assert cli._tail_lines(str(transcript), agent=agent) == ([], "empty")


def test_tail_lines_json_without_renderable_events(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text('{"type":"end","sessionId":"grok-sess"}\n', encoding="utf-8")
    assert cli._tail_lines(str(transcript), agent="grok") == (
        [],
        "no_renderable_events",
    )


def test_tail_lines_no_agent_returns_last_max_lines_raw(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        "\n".join(f"line {idx}" for idx in range(1, 66)) + "\n", encoding="utf-8"
    )
    lines, error = cli._tail_lines(str(transcript))
    assert error == ""
    assert lines == [f"line {idx}" for idx in range(26, 66)]


def test_tail_lines_agent_with_plain_text_passes_through_raw_tail(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        "\n".join(f"line {idx}" for idx in range(1, 66)) + "\n", encoding="utf-8"
    )
    lines, error = cli._tail_lines(str(transcript), agent="codex")
    assert error == ""
    assert lines[-1] == "line 65"
    assert len(lines) == 40


def test_startup_watch_names_the_auth_failure_instead_of_silence(
    tmp_path, capsys, monkeypatch
):
    """A worker that dies on login must not leave the receipt as the last word."""
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        '{"type":"assistant","error":"authentication_failed",'
        '"text":"Not logged in · Please run /login"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_SPAWN_WATCH_SECONDS", "2")

    cli._watch_launch_startup(
        {"accepted": True, "agent": "claude", "transcript": str(transcript)}
    )

    err = capsys.readouterr().err
    assert "Not logged in" in err
    assert "claude" in err
    assert str(transcript) in err


def test_startup_watch_stays_silent_while_the_worker_works(
    tmp_path, capsys, monkeypatch
):
    transcript = tmp_path / "transcript.log"
    transcript.write_text('{"type":"assistant","text":"working"}\n', encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_SPAWN_WATCH_SECONDS", "2")

    cli._watch_launch_startup(
        {"accepted": True, "agent": "claude", "transcript": str(transcript)}
    )

    assert capsys.readouterr().err == ""


def test_startup_watch_is_disabled_by_zero_seconds(tmp_path, capsys, monkeypatch):
    transcript = tmp_path / "transcript.log"
    transcript.write_text("Not logged in\n", encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_SPAWN_WATCH_SECONDS", "0")

    cli._watch_launch_startup(
        {"accepted": True, "agent": "claude", "transcript": str(transcript)}
    )

    assert capsys.readouterr().err == ""


def test_startup_watch_survives_a_null_accepted_field(tmp_path, capsys, monkeypatch):
    """Launch payloads carry `accepted: null` in practice — a .get(_, True)
    default reads that as refusal and silences the guard entirely."""
    transcript = tmp_path / "transcript.log"
    transcript.write_text("Not logged in\n", encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_SPAWN_WATCH_SECONDS", "2")

    cli._watch_launch_startup(
        {"accepted": None, "agent": "claude", "transcript": str(transcript)}
    )

    assert "Not logged in" in capsys.readouterr().err


def test_json_launch_prints_one_parseable_receipt_even_with_unserializable_extras(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    launches = []

    def fake_launch(spec, _source_dir):
        launches.append(spec)
        return {
            "accepted": True,
            "run_id": "work-260826-json-1",
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            "status": "launching",
            "weird": object(),
        }

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)

    rc = cli.main(
        [
            "workflow",
            "claude",
            "--prompt",
            "one invocation one run",
            "--json",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    assert len(launches) == 1
    captured = capsys.readouterr()
    assert captured.out.strip()
    body = json.loads(captured.out)
    assert body["run_id"] == "work-260826-json-1"
    assert body["agent"] == "claude"
    assert body["skill"] == "workflow"
    assert body["root"] == str(tmp_path)
    assert body["accepted"] is True
    assert body["status"] == "launching"
    assert "schema" in body


def test_json_launch_exception_after_run_created_emits_recovered_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    def fake_launch(_spec, _source_dir):
        raise RuntimeError("viewer exploded after spawn")

    def fake_recover(spec):
        return {
            "accepted": True,
            "run_id": "work-260826-recovered",
            "agent": spec.agent,
            "skill": spec.skill,
            "root": spec.root,
            "status": "launching",
            "replayed": True,
        }

    monkeypatch.setattr(cli, "launch_workflow", fake_launch)
    monkeypatch.setattr(cli, "recover_launch_receipt", fake_recover)

    rc = cli.main(
        [
            "workflow",
            "claude",
            "--prompt",
            "same brief",
            "--json",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "viewer exploded after spawn" in captured.err
    body = json.loads(captured.out)
    assert body["run_id"] == "work-260826-recovered"
    assert body["accepted"] is True
    assert body["replayed"] is True
    assert body["agent"] == "claude"


def test_json_launch_never_returns_empty_success_without_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "launch_workflow",
        lambda _spec, _source: {"accepted": True, "status": "launching"},
    )

    rc = cli.main(
        [
            "workflow",
            "claude",
            "--prompt",
            "missing id",
            "--json",
            "--root",
            str(tmp_path),
        ]
    )

    assert rc != 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["accepted"] is True
    assert body["run_id"] == ""
    assert "missing run_id" in captured.err
