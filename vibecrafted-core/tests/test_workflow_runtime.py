from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecrafted_core import workflow_runtime


def _fake_agent(bin_dir: Path, name: str) -> None:
    path = bin_dir / name
    path.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\"\n"
        "cat\n"
        f"printf '[12:00:00] model: {name}-model\\n'\n"
        f"printf '[12:00:00] session: {name}-session\\n'\n"
        "printf '[12:00:01] tokens: 10 in (3 cached) / 5 out\\n'\n"
        "printf 'cost_usd: $0.015\\n'\n"
        "printf 'fake worker ok\\n'\n"
        'printf "%s\\n" "---" "run_id: ${VIBECRAFTED_RUN_ID:-unknown}" '
        f'"agent: {name}" "skill: test" "status: completed" '
        '"claim_status: completed" "---" "report for $0" '
        '> "$VIBECRAFTED_REPORT_PATH"\n',
        encoding="utf-8",
    )
    path.chmod(0o755)


def _runtime_env(monkeypatch, tmp_path: Path, run_id: str) -> Path:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("claude", "codex", "agy", "junie", "grok"):
        _fake_agent(bin_dir, name)
    for name in (
        "VIBECRAFTED_ARTIFACT_SLUG",
        "VIBECRAFTED_ARTIFACT_SUFFIX",
        "VIBECRAFTED_ARTIFACT_TS",
        "VIBECRAFTED_CANONICAL_REPORT_DIR",
        "VIBECRAFTED_RESEARCH_AGENTS",
        "VIBECRAFTED_RESEARCH_QUORUM_IDLE_TIMEOUT",
        "VIBECRAFTED_RESEARCH_SYNTHESIS_TIMEOUT",
        "VIBECRAFTED_TEE_OUTPUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(bin_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", run_id)
    monkeypatch.setenv("VIBECRAFTED_REPORT_PATH", str(home / "parent.md"))
    monkeypatch.setenv("VIBECRAFTED_TRANSCRIPT_PATH", str(home / "parent.log"))
    monkeypatch.setenv("VIBECRAFTED_META_PATH", str(home / "parent.meta.json"))
    return home


def _write_finished_lane_meta(
    child_dir: Path, run_id: str, agent: str, completed_at: str
) -> None:
    report = child_dir / f"research-{agent}.md"
    transcript = child_dir / f"research-{agent}.transcript.log"
    report.write_text("---\nstatus: completed\n---\n", encoding="utf-8")
    transcript.write_text("done\n", encoding="utf-8")
    (child_dir / f"research-{agent}.meta.json").write_text(
        json.dumps(
            {
                "run_id": f"{run_id}-research-{agent}",
                "agent": agent,
                "agent_session_id": f"{agent}-session",
                "agent_model": f"{agent}-model",
                "report": str(report),
                "transcript": str(transcript),
                "exit_code": 0,
                "artifact_errors": [],
                "resume_command": f"{agent} resume {agent}-session",
                "completed_at": completed_at,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (
            "claude",
            [
                "claude",
                "--resume",
                "native-123",
                "-p",
                "--output-format",
                "stream-json",
                "--verbose",
                "--dangerously-skip-permissions",
            ],
        ),
        (
            "codex",
            [
                "codex",
                "exec",
                "resume",
                "--json",
                "--dangerously-bypass-approvals-and-sandbox",
                "native-123",
                "-",
            ],
        ),
        (
            "grok",
            [
                "grok",
                "--resume",
                "native-123",
                "--cwd",
                ".",
                "--permission-mode",
                "bypassPermissions",
                "--no-alt-screen",
                "--output-format",
                "streaming-json",
                "--prompt-file",
                "/dev/stdin",
            ],
        ),
    ],
)
def test_native_resume_argv_is_provider_specific_and_shell_free(
    agent: str, expected: list[str]
) -> None:
    command = workflow_runtime.native_resume_argv(agent, "native-123")

    assert command == expected
    assert command[0] != "bash"
    assert "-c" not in command


@pytest.mark.parametrize("agent", ["gemini", "agy", "junie", "swarm"])
def test_native_resume_argv_fails_closed_for_unverified_agents(agent: str) -> None:
    with pytest.raises(ValueError, match="native_resume_unsupported"):
        workflow_runtime.native_resume_argv(agent, "native-123")


def test_child_meta_never_promotes_legacy_session_id_to_native_identity(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "legacy.meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "legacy-run",
                "agent": "codex",
                "session_id": "runtime-or-legacy-id",
                "exit_code": 1,
            }
        ),
        encoding="utf-8",
    )

    result = workflow_runtime._child_result_from_meta("legacy", meta)

    assert result is not None
    assert result.agent_session_id == ""


def test_child_result_uses_canonical_meta_siblings(tmp_path: Path) -> None:
    report = tmp_path / "2026-07-26_agy_topic_report.md"
    transcript = tmp_path / "2026-07-26_agy_topic_report.transcript.log"
    meta = tmp_path / "2026-07-26_agy_topic_report.meta.json"
    report.write_text("---\nstatus: completed\n---\nbody\n", encoding="utf-8")
    transcript.write_text("done\n", encoding="utf-8")
    meta.write_text(
        json.dumps({"run_id": "research-agy", "agent": "agy"}),
        encoding="utf-8",
    )

    result = workflow_runtime._child_result_from_meta("research-agy", meta)

    assert result is not None
    assert result.report == report
    assert result.transcript == transcript
    assert result.exit_code == 0
    assert result.artifact_ok is True


def test_research_runtime_supervises_three_tracks(monkeypatch, tmp_path: Path) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-test")

    rc = workflow_runtime.main(
        ["research", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    assert "vc-research supervised run" in report
    assert "Research Lane Selection" in report
    assert "agents: claude, codex, agy" in report
    assert "research-claude" in report
    assert "research-codex" in report
    assert "research-agy" in report
    assert "research-synthesis" in report
    assert "agent_session_id: claude-session" in report
    assert "agent_model: claude-model" in report
    assert "tokens: 10 in (3 cached) / 5 out" in report
    assert "claude --resume claude-session" in report
    assert (home / "rsch-test-children" / "research-claude.md").is_file()
    assert (home / "rsch-test-children" / "research-codex.md").is_file()
    assert (home / "rsch-test-children" / "research-agy.md").is_file()
    assert (home / "rsch-test-children" / "research-synthesis.md").is_file()


def test_research_runtime_uses_user_configured_agents(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-config")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex", "agy"]\n',
        encoding="utf-8",
    )

    rc = workflow_runtime.main(
        ["research", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    meta = (home / "parent.meta.json").read_text(encoding="utf-8")
    assert "agents: grok, codex, agy" in report
    assert "research-grok" in report
    assert "research-codex" in report
    assert "research-agy" in report
    assert "research-claude" not in report
    assert '"research_agents": [\n    "grok",\n    "codex",\n    "agy"\n  ]' in meta


def test_research_runtime_yaml_wins_over_legacy_toml_and_applies_lane_models(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-yaml")
    legacy_dir = tmp_path / "xdg" / "vibecrafted"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["claude", "agy"]\n',
        encoding="utf-8",
    )
    config_dir = home / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "research.yaml").write_text(
        "lanes:\n  - agent: codex\n    model: gpt-yaml\n    enabled: true\n  - agent: agy\n    model: agy-yaml\n    enabled: true\n  - agent: claude\n    enabled: false\nsynthesizer:\n  agent: agy\n  model: agy-synth\n",
        encoding="utf-8",
    )

    rc = workflow_runtime.main(
        ["research", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    meta = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert f"source: {config_dir / 'research.yaml'}" in report
    assert "agents: codex, agy" in report
    assert "synthesizer: agy" in report
    assert "synthesizer_model: agy-synth" in report
    assert "research-claude" not in report
    codex_transcript = (
        home / "rsch-yaml-children" / "research-codex.transcript.log"
    ).read_text(encoding="utf-8")
    agy_transcript = (
        home / "rsch-yaml-children" / "research-agy.transcript.log"
    ).read_text(encoding="utf-8")
    synthesis_transcript = (
        home / "rsch-yaml-children" / "research-synthesis.transcript.log"
    ).read_text(encoding="utf-8")
    assert "exec\n-m\ngpt-yaml\n--json" in codex_transcript
    assert agy_transcript.splitlines()[:8] == [
        "--model",
        "agy-yaml",
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print",
    ]
    assert synthesis_transcript.splitlines()[:8] == [
        "--model",
        "agy-synth",
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print",
    ]
    children = {child["agent"]: child for child in meta["children"]}
    assert children["codex"]["model_requested"] == "gpt-yaml"
    assert children["codex"]["model_override_supported"] is True
    assert children["agy"]["model_requested"] == "agy-yaml"
    assert children["agy"]["model_override_supported"] is True
    assert children["agy"]["model_override_skipped"] is False
    assert "model_override_skip_reason" not in children["agy"]
    assert meta["research_synthesizer"] == "agy"
    assert meta["research_synthesizer_model"] == "agy-synth"
    assert meta["synthesis"]["model_requested"] == "agy-synth"
    assert meta["synthesis"]["model_override_supported"] is True
    assert meta["synthesis"]["model_override_skipped"] is False


def test_research_runtime_applies_model_request_per_child_runner(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-models")
    config_dir = home / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "research.yaml").write_text(
        "lanes:\n  - agent: codex\n    model: yaml-codex\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_AGENTS", "claude,codex,agy")

    rc = workflow_runtime.main(
        [
            "research",
            "--root",
            str(tmp_path),
            "--prompt",
            "map it",
            "--model",
            "frontier",
        ]
    )

    assert rc == 0
    claude_transcript = (
        home / "rsch-models-children" / "research-claude.transcript.log"
    ).read_text(encoding="utf-8")
    codex_transcript = (
        home / "rsch-models-children" / "research-codex.transcript.log"
    ).read_text(encoding="utf-8")
    agy_transcript = (
        home / "rsch-models-children" / "research-agy.transcript.log"
    ).read_text(encoding="utf-8")
    assert "--model\nfrontier\n-p" in claude_transcript
    assert "exec\n-m\nfrontier\n--json" in codex_transcript
    assert "yaml-codex" not in codex_transcript
    assert agy_transcript.splitlines()[:8] == [
        "--model",
        "frontier",
        "--dangerously-skip-permissions",
        "--add-dir",
        ".",
        "--print-timeout",
        "30m",
        "--print",
    ]

    meta = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert meta["model_requested"] == "frontier"
    children = {child["agent"]: child for child in meta["children"]}
    assert children["claude"]["model_requested"] == "frontier"
    assert children["claude"]["model_override_supported"] is True
    assert children["claude"]["model_override_skipped"] is False
    assert children["codex"]["model_override_supported"] is True
    assert children["agy"]["model_override_supported"] is True
    assert children["agy"]["model_override_skipped"] is False
    assert "model_override_skip_reason" not in children["agy"]


def test_research_runtime_writes_canonical_named_lane_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-canonical")
    canonical = (
        home / "artifacts" / "local" / "repo" / "2026_0613" / "reports" / "research"
    )
    monkeypatch.setenv("VIBECRAFTED_CANONICAL_REPORT_DIR", str(canonical))
    monkeypatch.setenv("VIBECRAFTED_ARTIFACT_TS", "2026-06-13")
    monkeypatch.setenv("VIBECRAFTED_ARTIFACT_SLUG", "acp-versus-native")
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_AGENTS", "grok,codex")

    rc = workflow_runtime.main(
        [
            "research",
            "--root",
            str(tmp_path),
            "--prompt",
            "ACP versus native cli agent versus Plugin",
        ]
    )

    assert rc == 0
    assert (canonical / "2026-06-13_grok_acp-versus-native_report.md").is_file()
    assert (canonical / "2026-06-13_codex_acp-versus-native_report.md").is_file()
    assert (canonical / "2026-06-13_synthesis_acp-versus-native_report.md").is_file()
    assert (home / "parent.md").is_file()


def test_research_runtime_env_agents_override_user_config(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-env")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["claude", "codex", "agy"]\n',
        encoding="utf-8",
    )
    runtime_config_dir = home / "config"
    runtime_config_dir.mkdir(parents=True)
    (runtime_config_dir / "research.yaml").write_text(
        "lanes:\n  - agent: claude\n  - agent: agy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_AGENTS", "grok,codex")

    rc = workflow_runtime.main(
        ["research", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    assert "source: env:VIBECRAFTED_RESEARCH_AGENTS" in report
    assert "agents: grok, codex" in report
    assert "research-grok" in report
    assert "research-codex" in report
    assert "research-agy" not in report


def test_research_synthesis_waits_for_lane_meta_and_resumes_last_finisher(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-layout")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-layout-children"
    child_dir.mkdir(parents=True)
    for agent, completed_at in (
        ("grok", "2026-06-13T10:00:00+00:00"),
        ("codex", "2026-06-13T10:01:00+00:00"),
    ):
        report = child_dir / f"research-{agent}.md"
        transcript = child_dir / f"research-{agent}.transcript.log"
        report.write_text("---\nstatus: completed\n---\n", encoding="utf-8")
        transcript.write_text("done\n", encoding="utf-8")
        (child_dir / f"research-{agent}.meta.json").write_text(
            json.dumps(
                {
                    "run_id": f"rsch-layout-research-{agent}",
                    "agent": agent,
                    "agent_session_id": f"{agent}-session",
                    "agent_model": f"{agent}-model",
                    "report": str(report),
                    "transcript": str(transcript),
                    "exit_code": 0,
                    "artifact_errors": [],
                    "resume_command": f"cd {tmp_path} && {agent} resume {agent}-session",
                    "completed_at": completed_at,
                }
            ),
            encoding="utf-8",
        )

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    parent_report = (home / "parent.md").read_text(encoding="utf-8")
    assert "agents: grok, codex" in parent_report
    assert "research-synthesis (codex)" in parent_report
    assert "agent_session_id: codex-session" in parent_report
    assert (child_dir / "research-synthesis.md").is_file()


def test_research_synthesis_codex_exec_resume_preserves_prompt_and_report_contract(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-codex-resume-contract")
    strict_codex = tmp_path / "bin" / "codex"
    strict_codex.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'expected="exec resume --json '
        '--dangerously-bypass-approvals-and-sandbox codex-session -"\n'
        'if [[ "$*" != "$expected" ]]; then\n'
        '  printf "unexpected argv: %s\\n" "$*" >&2\n'
        "  exit 64\n"
        "fi\n"
        'printf "%s\\n" "$@" > "$VIBECRAFTED_HOME/codex-resume.argv"\n'
        "prompt=$(cat)\n"
        'printf "%s\\n" "$prompt" > "$VIBECRAFTED_HOME/codex-resume.prompt"\n'
        'if [[ "$prompt" != *"Original operator prompt:"* '
        '|| "$prompt" != *"map it"* '
        '|| "$prompt" != *"research-codex.md"* ]]; then\n'
        '  printf "synthesis prompt contract missing\\n" >&2\n'
        "  exit 65\n"
        "fi\n"
        "printf '[12:00:00] model: codex-model\\n'\n"
        "printf '[12:00:00] session: codex-synthesis-session\\n'\n"
        "printf '[12:00:01] tokens: 10 in (3 cached) / 5 out\\n'\n"
        'printf "%s\\n" "---" "status: completed" "---" '
        '"strict codex synthesis ok" > "$VIBECRAFTED_REPORT_PATH"\n',
        encoding="utf-8",
    )
    strict_codex.chmod(0o755)
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["codex"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-codex-resume-contract-children"
    child_dir.mkdir(parents=True)
    _write_finished_lane_meta(
        child_dir,
        "rsch-codex-resume-contract",
        "codex",
        "2026-07-26T05:01:00+00:00",
    )

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    assert (home / "codex-resume.argv").read_text(encoding="utf-8").splitlines() == [
        "exec",
        "resume",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "codex-session",
        "-",
    ]
    synthesis_prompt = (home / "codex-resume.prompt").read_text(encoding="utf-8")
    assert "Original operator prompt:\nmap it" in synthesis_prompt
    assert str(child_dir / "research-codex.md") in synthesis_prompt
    synthesis_report = child_dir / "research-synthesis.md"
    assert "strict codex synthesis ok" in synthesis_report.read_text(encoding="utf-8")
    parent = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert parent["status"] == "completed"
    assert parent["synthesis"]["artifact_ok"] is True
    assert parent["synthesis"]["exit_code"] == 0


def test_research_synthesis_recovers_legacy_lane_meta_without_exit_code(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-legacy-meta")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-legacy-meta-children"
    child_dir.mkdir(parents=True)
    report = child_dir / "research-grok.md"
    transcript = child_dir / "research-grok.transcript.log"
    report.write_text("---\nstatus: completed\n---\nbody\n", encoding="utf-8")
    transcript.write_text("done\n", encoding="utf-8")
    (child_dir / "research-grok.meta.json").write_text(
        json.dumps(
            {
                "run_id": "rsch-legacy-meta-research-grok",
                "agent": "grok",
                "agent_session_id": "grok-session",
                "report": str(report),
                "transcript": str(transcript),
                "resume_command": f"cd {tmp_path} && grok --resume grok-session",
            }
        ),
        encoding="utf-8",
    )

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    parent = (home / "parent.md").read_text(encoding="utf-8")
    assert "research-grok" in parent
    assert "exit_code: 0" in parent
    assert "research-synthesis (grok)" in parent


def test_research_synthesis_closes_when_lane_failed(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-lane-failed")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex"]\n',
        encoding="utf-8",
    )
    canonical = (
        home / "artifacts" / "local" / "repo" / "2026_0613" / "reports" / "research"
    )
    monkeypatch.setenv("VIBECRAFTED_CANONICAL_REPORT_DIR", str(canonical))
    monkeypatch.setenv("VIBECRAFTED_ARTIFACT_TS", "2026-06-13")
    monkeypatch.setenv("VIBECRAFTED_ARTIFACT_SLUG", "acp-versus-native")
    failed_report = canonical / "2026-06-13_grok_acp-versus-native_report.md"
    failed_report.parent.mkdir(parents=True, exist_ok=True)
    failed_report.write_text("---\nstatus: failed\n---\n", encoding="utf-8")
    (canonical / "2026-06-13_grok_acp-versus-native_report.transcript.log").write_text(
        "boom\n",
        encoding="utf-8",
    )
    (canonical / "2026-06-13_grok_acp-versus-native_report.meta.json").write_text(
        json.dumps(
            {
                "run_id": "rsch-lane-failed-research-grok",
                "agent": "grok",
                "report": str(failed_report),
                "exit_code": 1,
                "artifact_errors": ["worker_failed"],
                "completed_at": "2026-06-13T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 1
    report = (home / "parent.md").read_text(encoding="utf-8")
    assert "status: failed" in report
    assert "research-grok" in report
    assert "artifact_errors: worker_failed" in report


def test_research_synthesis_marks_pending_when_quorum_is_impossible(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-quorum-impossible")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex", "agy"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-quorum-impossible-children"
    child_dir.mkdir(parents=True)
    for agent in ("grok", "codex"):
        report = child_dir / f"research-{agent}.md"
        report.write_text("---\nstatus: failed\n---\n", encoding="utf-8")
        (child_dir / f"research-{agent}.meta.json").write_text(
            json.dumps(
                {
                    "run_id": f"rsch-quorum-impossible-research-{agent}",
                    "agent": agent,
                    "report": str(report),
                    "exit_code": 1,
                    "artifact_errors": ["worker_failed"],
                }
            ),
            encoding="utf-8",
        )

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 1
    parent = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert parent["status"] == "failed"
    assert len(parent["children"]) == 3
    by_agent = {child["agent"]: child for child in parent["children"]}
    assert by_agent["grok"]["artifact_errors"] == ["worker_failed"]
    assert by_agent["codex"]["artifact_errors"] == ["worker_failed"]
    assert by_agent["agy"]["exit_code"] == 124
    assert by_agent["agy"]["artifact_errors"] == [
        "worker_timeout",
        "lane_quorum_impossible",
    ]


def test_research_synthesis_degrades_to_partial_success_on_quorum(
    monkeypatch, tmp_path: Path
) -> None:
    # Emil's first swarm: Codex + Grok dowiozły, Gemini padł. Większość (2/3)
    # ⇒ synteza z ocalałych i status partial_success, nie zawalenie całego runu.
    home = _runtime_env(monkeypatch, tmp_path, "rsch-partial")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex", "agy"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-partial-children"
    child_dir.mkdir(parents=True)

    for agent, completed_at in (
        ("grok", "2026-06-23T10:00:00+00:00"),
        ("codex", "2026-06-23T10:01:00+00:00"),
    ):
        report = child_dir / f"research-{agent}.md"
        transcript = child_dir / f"research-{agent}.transcript.log"
        report.write_text("---\nstatus: completed\n---\n", encoding="utf-8")
        transcript.write_text("done\n", encoding="utf-8")
        (child_dir / f"research-{agent}.meta.json").write_text(
            json.dumps(
                {
                    "run_id": f"rsch-partial-research-{agent}",
                    "agent": agent,
                    "agent_session_id": f"{agent}-session",
                    "agent_model": f"{agent}-model",
                    "report": str(report),
                    "transcript": str(transcript),
                    "exit_code": 0,
                    "artifact_errors": [],
                    "resume_command": f"cd {tmp_path} && {agent} resume {agent}-session",
                    "completed_at": completed_at,
                }
            ),
            encoding="utf-8",
        )

    failed_report = child_dir / "research-agy.md"
    failed_report.write_text("---\nstatus: failed\n---\n", encoding="utf-8")
    (child_dir / "research-agy.transcript.log").write_text("boom\n", encoding="utf-8")
    (child_dir / "research-agy.meta.json").write_text(
        json.dumps(
            {
                "run_id": "rsch-partial-research-agy",
                "agent": "agy",
                "report": str(failed_report),
                "transcript": str(child_dir / "research-agy.transcript.log"),
                "exit_code": 1,
                "artifact_errors": ["worker_failed"],
                "completed_at": "2026-06-23T10:02:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    parent_report = (home / "parent.md").read_text(encoding="utf-8")
    assert "status: partial_success" in parent_report
    assert "lanes_failed: agy" in parent_report
    # synteza odpaliła z ostatniego ocalałego (codex), agy odnotowany jako fail
    assert "research-synthesis (codex)" in parent_report
    assert "research-agy" in parent_report
    assert "artifact_errors: worker_failed" in parent_report
    assert (child_dir / "research-synthesis.md").is_file()


def test_research_synthesis_times_out_missing_lane_after_quorum(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-quorum-timeout")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex", "agy"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-quorum-timeout-children"
    child_dir.mkdir(parents=True)
    _write_finished_lane_meta(
        child_dir, "rsch-quorum-timeout", "grok", "2026-07-26T05:00:00+00:00"
    )
    _write_finished_lane_meta(
        child_dir, "rsch-quorum-timeout", "codex", "2026-07-26T05:01:00+00:00"
    )
    (child_dir / "research-agy.meta.json").write_text(
        json.dumps({"run_id": "rsch-quorum-timeout-research-agy", "agent": "agy"}),
        encoding="utf-8",
    )
    (child_dir / "research-agy.transcript.log").write_text(
        "Error: timeout waiting for response\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_SYNTHESIS_TIMEOUT", "30")
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_QUORUM_IDLE_TIMEOUT", "0")

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    parent = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert parent["status"] == "partial_success"
    assert parent["status"] != "completed"
    assert parent["lanes_failed"] == ["agy"]
    assert len(parent["children"]) == 3
    agy = next(child for child in parent["children"] if child["agent"] == "agy")
    assert agy["exit_code"] == 124
    assert agy["artifact_ok"] is False
    assert agy["artifact_errors"] == [
        "worker_timeout",
        "lane_quorum_idle_timeout",
    ]


def test_research_synthesis_hard_timeout_preserves_survivor_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "rsch-hard-timeout")
    config_dir = tmp_path / "xdg" / "vibecrafted"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[runtime.picking.research]\ndefault_agents = ["grok", "codex", "agy"]\n',
        encoding="utf-8",
    )
    child_dir = home / "rsch-hard-timeout-children"
    child_dir.mkdir(parents=True)
    _write_finished_lane_meta(
        child_dir, "rsch-hard-timeout", "grok", "2026-07-26T05:00:00+00:00"
    )
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_SYNTHESIS_TIMEOUT", "0")
    monkeypatch.setenv("VIBECRAFTED_RESEARCH_QUORUM_IDLE_TIMEOUT", "0")

    rc = workflow_runtime.main(
        ["research-synthesis", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 1
    parent = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert parent["status"] == "failed"
    assert len(parent["children"]) == 3
    by_agent = {child["agent"]: child for child in parent["children"]}
    assert by_agent["grok"]["exit_code"] == 0
    assert by_agent["grok"]["artifact_ok"] is True
    for agent in ("codex", "agy"):
        assert by_agent[agent]["exit_code"] == 124
        assert by_agent[agent]["artifact_errors"] == [
            "worker_timeout",
            "lane_hard_timeout",
        ]


def test_research_runtime_tees_child_output(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _runtime_env(monkeypatch, tmp_path, "rsch-visible")
    monkeypatch.setenv("VIBECRAFTED_TEE_OUTPUT", "1")

    rc = workflow_runtime.main(
        ["research", "--root", str(tmp_path), "--prompt", "map it"]
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "===== research:research-claude:claude =====" in out
    assert "===== research:research-codex:codex =====" in out
    assert "===== research:research-agy:agy =====" in out
    assert "===== research:research-synthesis:" in out
    assert "fake worker ok" in out


def test_marbles_runtime_supervises_loops(monkeypatch, tmp_path: Path) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "marb-test")

    rc = workflow_runtime.main(
        [
            "marbles",
            "--agent",
            "codex",
            "--root",
            str(tmp_path),
            "--prompt",
            "converge",
            "--count",
            "2",
            "--depth",
            "4",
        ]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    assert "vc-marbles supervised run" in report
    assert "marbles-L1" in report
    assert "marbles-L2" in report
    assert "agent_session_id: codex-session" in report
    assert "agent_model: codex-model" in report
    assert "session_id: aggregated" in report
    # parent: input 20 + output 10; cached 6 subset (not double-counted)
    assert "tokens_total: 30" in report
    assert "cost_usd: 0.03" in report
    assert "cost_source: children_sum" in report
    assert "codex resume codex-session" in report
    meta = json.loads((home / "parent.meta.json").read_text(encoding="utf-8"))
    assert meta["session_id"] == "aggregated"
    assert meta["tokens_input"] == 20
    assert meta["tokens_cached_input"] == 6
    assert meta["tokens_output"] == 10
    assert meta["tokens_total"] == 30
    assert meta["cost_usd"] == 0.03
    assert meta["cost_source"] == "children_sum"
    assert meta["children"][0]["tokens_total"] == 15
    assert meta["children"][1]["cost_usd"] == 0.015
    assert (home / "marb-test-children" / "marbles-L1.md").is_file()
    assert (home / "marb-test-children" / "marbles-L2.md").is_file()
    l2_transcript = (
        home / "marb-test-children" / "marbles-L2.transcript.log"
    ).read_text(encoding="utf-8")
    assert "intentionally blind to prior marbles runs" in l2_transcript
    assert "Previous loop report" not in l2_transcript
    assert "marbles-L1.md" not in l2_transcript


def test_polarize_runtime_reuses_loop_with_polarize_identity(
    monkeypatch, tmp_path: Path
) -> None:
    home = _runtime_env(monkeypatch, tmp_path, "plrz-test")

    rc = workflow_runtime.main(
        [
            "marbles",
            "--workflow",
            "polarize",
            "--agent",
            "codex",
            "--root",
            str(tmp_path),
            "--prompt",
            "cut excess",
            "--count",
            "1",
            "--depth",
            "4",
        ]
    )

    assert rc == 0
    report = (home / "parent.md").read_text(encoding="utf-8")
    prompt = (home / "plrz-test-children" / "polarize-L1.prompt.md").read_text(
        encoding="utf-8"
    )
    assert "vc-polarize supervised run" in report
    assert "polarize-L1" in report
    assert "- Skill: vc-polarize" in prompt
    assert "Polarize loop: L1/1. Depth target: 4." in prompt
    assert "Marbles loop" not in prompt
    assert "agent_model: codex-model" in report
    assert "codex resume codex-session" in report
    assert (home / "plrz-test-children" / "polarize-L1.md").is_file()
    transcript = (home / "plrz-test-children" / "polarize-L1.transcript.log").read_text(
        encoding="utf-8"
    )
    assert "intentionally blind to prior marbles runs" not in transcript
    assert "Previous loop report" not in transcript
    assert "marbles-L1.md" not in transcript


def test_child_prompt_carries_worker_signal_discipline() -> None:
    prompt = workflow_runtime._child_prompt("marbles", "L1", "/repo", "find gaps")

    # Supervised children (marbles/polarize/research) are subagent-shaped too:
    # the gate-nap preamble must ride in their contract, not only in the main
    # dispatched-worker prompt.
    assert "background-task completions will NEVER wake" in prompt
    assert "Never end your turn waiting" in prompt
    assert "intentionally blind to prior marbles runs" in prompt
