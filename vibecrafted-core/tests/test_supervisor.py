from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path

import pytest
from vibecrafted_core import control_plane
from vibecrafted_core.agent_dispatch import extract_session_id
from vibecrafted_core.events import append_event
from vibecrafted_core.spawn import Supervisor, finalize_artifacts, finish_meta


def test_supervisor_spawn_lifecycle_extracts_session_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    transcript = tmp_path / "agent.transcript.log"
    meta = tmp_path / "agent.meta.json"
    meta.write_text(json.dumps({"transcript": str(transcript)}), encoding="utf-8")

    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; "
            f"Path({str(transcript)!r}).write_text('[12:00:00] session: codex-test-123\\n', encoding='utf-8')"
        ),
    ]

    handle = Supervisor().spawn(
        "codex",
        "fixture",
        skill="test",
        mode="unit",
        root=tmp_path,
        command=command,
        run_id="test-001",
        meta_path=meta,
        transcript_path=transcript,
    )

    assert handle.wait(timeout=5) == 0
    assert handle.exit_code == 0
    assert handle.session_id == "codex-test-123"
    assert (
        json.loads(meta.read_text(encoding="utf-8"))["session_id"] == "codex-test-123"
    )
    kinds = [event["kind"] for event in control_plane.read_event_tail(10)]
    assert "spawn-completed" in kinds
    assert "spawn-started" in kinds


def test_supervisor_propagates_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))

    handle = Supervisor().spawn(
        "codex",
        "fixture",
        skill="test",
        mode="unit",
        root=tmp_path,
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        run_id="test-002",
    )

    assert handle.wait(timeout=5) == 7
    assert handle.exit_code == 7
    kinds = [event["kind"] for event in control_plane.read_event_tail(10)]
    assert "spawn-failed" in kinds


def test_supervisor_failed_child_leaves_failed_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))
    transcript = tmp_path / "agent.transcript.log"
    report = tmp_path / "agent.md"
    meta = tmp_path / "agent.meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "test-report-on-death",
                "prompt_id": "prompt-report-on-death",
                "agent": "codex",
                "skill": "implement",
                "model": "gpt-5.3-codex",
                "status": "running",
                "root": str(tmp_path),
                "report": str(report),
                "transcript": str(transcript),
                "meta": str(meta),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    handle = Supervisor().spawn(
        "codex",
        "fixture",
        skill="implement",
        mode="unit",
        root=tmp_path,
        command=[
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                f"Path({str(transcript)!r}).write_text('partial transcript\\n', encoding='utf-8'); "
                "raise SystemExit(9)"
            ),
        ],
        run_id="test-report-on-death",
        meta_path=meta,
        transcript_path=transcript,
    )

    assert handle.wait(timeout=5) == 9
    assert report.is_file()
    report_text = report.read_text(encoding="utf-8")
    assert "status: failed" in report_text
    assert str(transcript) in report_text
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 9


def test_session_id_extractors_use_shared_pattern() -> None:
    repo = Path(__file__).resolve().parents[2]
    fixtures = repo / "tests" / "fixtures" / "transcripts"

    assert (
        extract_session_id("claude", (fixtures / "claude_session.log").read_text())
        == "claude-session-123"
    )
    assert (
        extract_session_id("codex", (fixtures / "codex_session.log").read_text())
        == "codex-session-456"
    )
    assert (
        extract_session_id("gemini", (fixtures / "gemini_session.log").read_text())
        == "gemini-session-789"
    )


def test_finalize_artifacts_python_owns_launcher_artifact_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.3-codex")
    reports = tmp_path / "Vetcoders" / "vibecrafted" / "2026_0610" / "reports"
    reports.mkdir(parents=True)
    report = reports / "announced.md"
    transcript = reports / "announced.transcript.log"
    meta = reports / "announced.meta.json"

    report.write_text(
        "---\nrun_id: copied-by-worker\nsession_id: copied-by-worker\nagent: codex\nskill: implement\nstatus: completed\nfinalized: true\nclaim: the bounded implementation passed its gates\nlauncher_template: true\n---\n\n# Report\n\nDone.\n",
        encoding="utf-8",
    )
    transcript.write_text(
        "[12:40:43] session: codex-finalize-001\n"
        "tokens: 12 in (3 cached) / 7 out\n"
        "cost_usd: $0.045\n",
        encoding="utf-8",
    )
    meta.write_text(
        json.dumps(
            {
                "run_id": "finalize-test-001",
                "prompt_id": "prompt-finalize",
                "agent": "codex",
                "skill": "implement",
                "model": "unknown",
                "model_requested": "gpt-5.5",
                "status": "completed",
                "root": str(tmp_path),
                "report": str(report),
                "transcript": str(transcript),
                "meta": str(meta),
                "created_at": "2026-06-10T08:00:00+00:00",
                "completed_at": "2026-06-10T08:00:05+00:00",
                "duration_s": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    final_meta = finalize_artifacts(meta, report, transcript)

    assert final_meta is not None
    payload = json.loads(final_meta.read_text(encoding="utf-8"))
    assert payload["session_id"] == "codex-finalize-001"
    assert payload["model"] == "gpt-5.3-codex"
    assert payload["model_requested"] == "gpt-5.5"
    assert payload["duration_s"] == 5.0
    assert payload["tokens_input"] == 12
    assert payload["tokens_cached_input"] == 3
    assert payload["tokens_output"] == 7
    # input + output only; cached is subset of input
    assert payload["tokens_total"] == 19
    assert "tokens_cache_write" not in payload
    assert payload["cost_usd"] == 0.045
    assert payload["artifact_contract"] == "vibecrafted.agent-artifact.v1"

    final_report = Path(payload["report"])
    final_transcript = Path(payload["transcript"])
    assert final_report.name.endswith("-report.md")
    assert final_report.is_file()
    assert final_transcript.is_file()
    transcript_manifest = Path(f"{final_transcript}.manifest.json")
    manifest_payload = json.loads(transcript_manifest.read_text(encoding="utf-8"))
    transcript_bytes = final_transcript.read_bytes()
    assert manifest_payload == {
        "version": 1,
        "run_id": "finalize-test-001",
        "transcript": str(final_transcript.resolve()),
        "root": str(final_transcript.parent.resolve()),
        "bytes": len(transcript_bytes),
        "sha256": hashlib.sha256(transcript_bytes).hexdigest(),
    }
    assert stat.S_IMODE(transcript_manifest.stat().st_mode) == 0o600
    report_text = final_report.read_text(encoding="utf-8")
    assert "run_id: finalize-test-001" in report_text
    assert "session_id: codex-finalize-001" in report_text
    assert "run_id: copied-by-worker" not in report_text
    assert "session_id: copied-by-worker" not in report_text
    assert "finalized: true" in report_text
    assert "claim: the bounded implementation passed its gates" in report_text
    assert "launcher_template:" not in report_text
    assert "model_requested: gpt-5.5" in report_text
    assert report.exists()
    assert transcript.exists()
    assert meta.exists()
    assert report.resolve() == final_report.resolve()
    assert transcript.resolve() == final_transcript.resolve()
    assert meta.resolve() == final_meta.resolve()


def test_finish_meta_python_owns_terminal_state(tmp_path: Path) -> None:
    transcript = tmp_path / "agent.transcript.log"
    meta = tmp_path / "agent.meta.json"
    transcript.write_text(
        "\x1b[31m[12:00:00] session: codex-finish-001\x1b[0m\n",
        encoding="utf-8",
    )
    meta.write_text(
        json.dumps(
            {
                "status": "running",
                "agent": "codex",
                "created_at": "2026-06-10T08:00:00+00:00",
                "updated_at": "2026-06-10T08:00:01+00:00",
                "transcript": str(transcript),
                "exit_code": None,
                "liveness": "pid_alive",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = finish_meta(meta, "failed", "7")

    assert result == meta
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 7
    assert payload["liveness"] == "terminal"
    assert payload["session_id"] == "codex-finish-001"
    assert isinstance(payload["duration_s"], (int, float))
    assert payload["completed_at"] == payload["updated_at"]


def test_finish_meta_emits_terminal_lifecycle_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / ".vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    meta = tmp_path / "shell.meta.json"
    meta.write_text(
        json.dumps(
            {
                "run_id": "shell-finish-001",
                "status": "running",
                "root": str(tmp_path),
                "agent": "codex",
                "skill_code": "impl",
            }
        ),
        encoding="utf-8",
    )

    finish_meta(meta, "completed", 0)

    events = control_plane.read_event_tail(10)
    terminal = next(event for event in events if event["run_id"] == "shell-finish-001")
    assert terminal["kind"] == "lifecycle:completed"
    assert terminal["payload"]["liveness"] == "terminal"


def test_finalize_artifacts_maps_junie_json_stream_receipt(tmp_path: Path) -> None:
    report = tmp_path / "junie.md"
    transcript = tmp_path / "junie.transcript.log"
    meta = tmp_path / "junie.meta.json"

    report.write_text("# Junie report\n\nDone.\n", encoding="utf-8")
    transcript.write_text(
        json.dumps({"type": "session", "session_id": "junie-session-123"})
        + "\n"
        + json.dumps(
            {
                "type": "result",
                "session_id": "junie-session-123",
                "usage": {
                    "prompt_tokens": 1200,
                    "cached_prompt_tokens": 300,
                    "completion_tokens": 125,
                    "total_tokens": 1625,
                },
                "cost_usd": 0.01925,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    meta.write_text(
        json.dumps(
            {
                "run_id": "junie-json-001",
                "prompt_id": "prompt-junie-json",
                "agent": "junie",
                "skill": "implement",
                "model": "junie-cli-default",
                "status": "completed",
                "root": str(tmp_path),
                "report": str(report),
                "transcript": str(transcript),
                "meta": str(meta),
                "created_at": "2026-07-12T03:50:39+00:00",
                "completed_at": "2026-07-12T03:50:42+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    final_meta = finalize_artifacts(meta, report, transcript)

    assert final_meta == meta
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["session_id"] == "junie-session-123"
    assert payload["tokens_input"] == 1200
    assert payload["tokens_cached_input"] == 300
    assert payload["tokens_output"] == 125
    assert payload["tokens_total"] == 1325
    assert payload["cost_usd"] == 0.01925
    report_text = report.read_text(encoding="utf-8")
    assert "session_id: junie-session-123" in report_text
    assert "tokens_input: 1200" in report_text


def test_subscribe_events_reads_appended_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / ".vibecrafted"))

    append_event("unit", "run-1", "hello", {"ok": True})
    events = list(control_plane.subscribe_events(kinds={"unit"}))

    assert len(events) == 1
    assert events[0].kind == "unit"
    assert events[0].payload == {"ok": True}


def test_extract_tokens_prefers_run_closure_footer_across_agents() -> None:
    """The footer totals are written for every agent; the regex extractor must
    read them so research-swarm meta never lands at 0 for gemini/junie/grok
    (only codex/claude formatters render the per-event token line)."""
    from vibecrafted_core.spawn import _extract_tokens

    # gemini-style: only the closure footer carries usage, no per-event line.
    gemini_transcript = (
        "[04:09] gemini body text\n"
        "\x1b[32m[04:49] done\x1b[0m\n"
        "---\n"
        "runner: vibecrafted\n"
        "model: gemini-3.1-pro-preview\n"
        "tokens_input: 648618\n"
        "tokens_cached_input: 50\n"
        "tokens_output: 1680\n"
        "tokens_total: 650348\n"
    )
    # 648618 in + 1680 out; footer tokens_total was old double-count (650348)
    assert _extract_tokens(gemini_transcript)["total"] == 650298

    claude_footer = (
        "tokens_input: 100\n"
        "tokens_cached_input: 400\n"
        "tokens_cache_write: 25\n"
        "tokens_output: 30\n"
    )
    claude_tokens = _extract_tokens(claude_footer)
    # fixture has cached (400) > input (100) → additive (junie-like shape)
    assert claude_tokens["total"] == 530
    assert claude_tokens["cache_write"] == 25

    # junie-style footer, no per-event render line either.
    junie_transcript = "tokens_input: 5000\ntokens_output: 200\ntokens_total: 5200\n"
    assert _extract_tokens(junie_transcript)["output"] == 200

    # Footer present alongside a per-event line must NOT double count.
    both = "tokens: 12 in / 7 out\ntokens_input: 12\ntokens_output: 7\n"
    assert _extract_tokens(both)["total"] == 19

    # No footer: fall back to the per-event line (backward compatible).
    legacy = "[12:00] tokens: 12 in / 7 out\n"
    assert _extract_tokens(legacy)["total"] == 19


def test_extract_tokens_does_not_let_zero_footer_mask_junie_json() -> None:
    from vibecrafted_core.spawn import _extract_tokens

    transcript = (
        '{"modelUsage":[{"model":"gpt-5.5","inputTokens":807,'
        '"cacheInputTokens":49792,"outputTokens":563,"cost":0.045821}]}\n'
        "tokens_input: 0\ntokens_cached_input: 0\ntokens_output: 0\n"
    )
    tokens = _extract_tokens(transcript)
    assert tokens == {
        "input": 807,
        "cached_input": 49792,
        "cache_write": None,
        "output": 563,
        "total": 51162,
        # one provider usage event; the zero footer is not evidence
        "events": 1,
    }


def test_extract_cost_sums_junie_per_call_model_usage() -> None:
    from vibecrafted_core.spawn import _extract_cost

    transcript = (
        '{"modelUsage":[{"model":"gpt-5.5","cost":0.045821}]}\n'
        '{"modelUsage":[{"model":"gpt-4.1-mini","cost":0.0002904}]}\n'
        "cost_usd: unknown\n"
    )
    assert _extract_cost(transcript) == 0.046111
