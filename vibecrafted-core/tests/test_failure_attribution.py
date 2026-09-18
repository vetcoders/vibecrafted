"""Per-run failure attribution: ``failed`` must name its cause.

Fixtures under ``fixtures/telemetry/`` are copies of two real host runs
(2026-09-18, kimi ``justdo``) with host paths redacted. Provider texts in the
parametrized table are the exact specimens already pinned elsewhere in this
repo (stream bridge, spawn, triage and agent-stream tests); providers without
such a specimen are deliberately not classified.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from vibecrafted_core import cli
from vibecrafted_core.failure_attribution import (
    FAILURE_KINDS,
    NO_PATTERN_REASON,
    attribute_failure,
    redact_message,
)
from vibecrafted_core.report_contract import parse_report_text
from vibecrafted_core.supervisor_async import AsyncSupervisor

FIXTURES = Path(__file__).parent / "fixtures" / "telemetry"
QUOTA_RUN = "just-260918-060432-54797"
COMPLETED_RUN = "just-260918-060907-73299"
FOUNDER_FORM = "exit_code=1 (provider's code 403 quota exhausted)"


def _copy_transcript(tmp_path: Path, run_id: str) -> Path:
    target = tmp_path / f"{run_id}.transcript.log"
    shutil.copyfile(FIXTURES / run_id / "transcript.log", target)
    return target


def _install_fixture_run(home: Path, run_id: str) -> Path:
    run_dir = home / "control_plane" / "runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("meta.json", "transcript.log"):
        shutil.copyfile(FIXTURES / run_id / name, run_dir / name)
    return run_dir


def _stub_server_transport(monkeypatch: pytest.MonkeyPatch, run: dict) -> None:
    """vc-server is not part of the unit suite; stub only its transport.

    The run row mirrors what the live server returned on the host for the
    real 403 run: it carries ``exit_code`` and the transcript path, and no
    cause. Everything asserted below is computed by production readers.
    """
    monkeypatch.setattr(
        cli,
        "resolve_server_run_id",
        lambda _agent, run_id, *, last: run_id or str(run["run_id"]),
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


def test_kimi_monthly_limit_403_is_quota_exhausted_with_line_evidence(
    tmp_path: Path,
) -> None:
    transcript = _copy_transcript(tmp_path, QUOTA_RUN)

    failure = attribute_failure(transcript, 1, agent="kimi")

    assert failure is not None
    assert failure.exit_code == 1
    assert failure.kind == "quota_exhausted"
    assert failure.provider_code == 403
    assert failure.evidence == {"path": str(transcript), "line": 2}
    assert "monthly usage limit" in str(failure.message)
    assert failure.source == "transcript"
    assert failure.summary() == FOUNDER_FORM
    payload = failure.as_dict()
    assert payload["kind"] == "quota_exhausted"
    assert payload["provider_code"] == 403
    assert payload["evidence"] == {"path": str(transcript), "line": 2}
    assert payload["summary"] == FOUNDER_FORM
    assert set(FAILURE_KINDS) == {
        "quota_exhausted",
        "auth_error",
        "rate_limited",
        "network",
        "tool_error",
        "unknown",
    }


@pytest.mark.parametrize(
    "line,kind,code",
    [
        # codex exec stream (tests/tui/test_codex_stream_bridge.py)
        ('{"type": "turn.failed", "error": "429 rate limit"}', "rate_limited", 429),
        # codex non-JSON auth failure (tests/tui/test_spawn_common.py)
        (
            (
                "Your access token could not be refreshed because your refresh "
                "token was already used."
            ),
            "auth_error",
            None,
        ),
        # claude logged-out CLI (tests/test_cli.py, tests/tui/test_spawn_common.py)
        ("Not logged in · Please run /login", "auth_error", None),
        # anthropic overload specimen (postmortem 2026-08-19, test_run_triage.py)
        ("API 529 Overloaded", "rate_limited", 529),
        # grok streaming-json error envelope (tests/test_agent_stream.py)
        ('{"type":"error","error":{"code":429,"retriable":true}}', "rate_limited", 429),
        # kimi provider retry meta event (tests/test_agent_stream.py)
        (
            (
                '{"role":"meta","type":"turn.step.retrying","failed_attempt":1,'
                '"next_attempt":2,"max_attempts":5,"delay_ms":1000,'
                '"error_name":"RateLimitError","error_message":"slow down",'
                '"status_code":429}'
            ),
            "rate_limited",
            429,
        ),
        # codex plan window (tests/test_run_triage.py)
        ("codex usage limit reached", "quota_exhausted", None),
        # openai HTTP specimen (tests/test_run_triage.py)
        ("HTTP 429 Too Many Requests", "rate_limited", 429),
    ],
)
def test_repo_pinned_provider_specimens_are_classified(
    tmp_path: Path, line: str, kind: str, code: int | None
) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text("starting\n" + line + "\n", encoding="utf-8")

    failure = attribute_failure(transcript, 7)

    assert failure is not None
    assert failure.kind == kind
    if code is None:
        assert isinstance(failure.provider_code, dict)
        assert failure.provider_code["value"] == "unknown"
        assert failure.provider_code["reason"]
    else:
        assert failure.provider_code == code
    assert failure.evidence == {"path": str(transcript), "line": 2}


def test_unmatched_failure_is_unknown_with_reason_not_a_guess(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.log"
    transcript.write_text(
        # grok MCP transport chatter is retry noise, never the run's cause
        "ERROR worker quit with fatal: Transport channel closed, when "
        "Auth(AuthorizationRequired)\n"
        # a traceback line number is not an HTTP status
        '  File "worker.py", line 429, in run\n'
        "RuntimeError: tool exploded\n",
        encoding="utf-8",
    )

    failure = attribute_failure(transcript, 1, agent="grok")

    assert failure is not None
    assert failure.kind == "unknown"
    assert failure.reason == NO_PATTERN_REASON
    assert failure.provider_code["value"] == "unknown"
    assert failure.evidence["value"] == "unknown"
    assert failure.summary() == f"exit_code=1 (cause unknown: {NO_PATTERN_REASON})"


def test_tool_output_quoting_a_403_is_not_the_run_failure(tmp_path: Path) -> None:
    """A worker that *read* a quota error (tool result) did not die of one."""
    transcript = tmp_path / "transcript.log"
    quoted = (FIXTURES / QUOTA_RUN / "transcript.log").read_text().splitlines()[1]
    transcript.write_text(
        json.dumps({"role": "tool", "tool_call_id": "t1", "content": quoted})
        + "\n"
        + json.dumps({"role": "assistant", "content": "I read the old 403 log."})
        + "\n",
        encoding="utf-8",
    )

    failure = attribute_failure(transcript, 1, agent="kimi")

    assert failure is not None
    assert failure.kind == "unknown"


def test_last_provider_error_wins_over_earlier_retries(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.log"
    quota_line = (FIXTURES / QUOTA_RUN / "transcript.log").read_text().splitlines()[1]
    transcript.write_text(
        '{"role":"meta","type":"turn.step.retrying","error_name":"RateLimitError",'
        '"error_message":"slow down","status_code":429}\n' + quota_line + "\n",
        encoding="utf-8",
    )

    failure = attribute_failure(transcript, 1, agent="kimi")

    assert failure is not None
    assert failure.kind == "quota_exhausted"
    assert failure.provider_code == 403
    assert failure.evidence["line"] == 2


def test_success_and_missing_transcript(tmp_path: Path) -> None:
    transcript = _copy_transcript(tmp_path, QUOTA_RUN)
    assert attribute_failure(transcript, 0, agent="kimi") is None

    missing = attribute_failure(tmp_path / "absent.log", 1, agent="kimi")
    assert missing is not None
    assert missing.kind == "unknown"
    assert missing.reason == "transcript unavailable"


def test_failure_message_is_redacted_and_bounded() -> None:
    text = (
        "error: 401 invalid api key sk-ant-api03-AbCdEf0123456789xyz "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.secret.part "
        "contact ops@example.com " + "x" * 400
    )

    redacted = redact_message(text)

    assert "sk-ant-api03-AbCdEf0123456789xyz" not in redacted
    assert "eyJhbGciOiJIUzI1NiJ9" not in redacted
    assert "ops@example.com" not in redacted
    assert "[redacted]" in redacted
    assert len(redacted) <= 240


def test_supervisor_closes_real_403_run_with_attributed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production close path: meta.json and report carry the cause."""
    home = tmp_path / "home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    run_id = "w3-quota-403"
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"
    fixture = FIXTURES / QUOTA_RUN / "transcript.log"
    kimi = tmp_path / "kimi"
    # Replays the recorded kimi 0.42.0 output: the version banner on stdout,
    # the provider error on stderr (the supervisor merges both), exit 1.
    kimi.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"lines = open({str(fixture)!r}, encoding='utf-8').read().splitlines(True)\n"
        "sys.stdout.write(lines[0]); sys.stdout.flush()\n"
        "sys.stderr.write(''.join(lines[1:])); sys.stderr.flush()\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    kimi.chmod(0o755)

    handle = asyncio.run(
        AsyncSupervisor().run(
            run_id=run_id,
            command=[str(kimi)],
            root=tmp_path,
            meta_path=meta,
            report_path=report,
            transcript_path=transcript,
        )
    )

    assert handle.exit_code == 1
    payload = json.loads(meta.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 1
    assert payload["status"] == "failed"
    failure = payload["failure"]
    assert failure["kind"] == "quota_exhausted"
    assert failure["provider_code"] == 403
    assert failure["exit_code"] == 1
    assert failure["source"] == "transcript"
    assert failure["summary"] == FOUNDER_FORM
    evidence = failure["evidence"]
    assert evidence["path"] == str(transcript)
    evidence_line = transcript.read_text(encoding="utf-8").splitlines()[
        evidence["line"] - 1
    ]
    assert "403" in evidence_line and "monthly usage limit" in evidence_line

    fields, _body, has_frontmatter = parse_report_text(
        report.read_text(encoding="utf-8")
    )
    assert has_frontmatter
    assert fields["failure"] == FOUNDER_FORM
    assert fields["failure_kind"] == "quota_exhausted"
    assert fields["failure_provider_code"] == "403"
    assert fields["failure_evidence"] == f"{transcript}:{evidence['line']}"


def test_observe_names_the_403_for_a_legacy_run_lazily(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run closed before this cut: meta has no cause, observe derives it."""
    from vibecrafted_core.control_plane import control_plane_home

    home = control_plane_home().parent
    run_dir = _install_fixture_run(home, QUOTA_RUN)
    legacy = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert "failure" not in legacy  # the gap this cut closes
    _stub_server_transport(
        monkeypatch,
        {
            "run_id": QUOTA_RUN,
            "state": "failed",
            "agent": "kimi",
            "skill": "justdo",
            "exit_code": 1,
            "last_error": "",
            "latest_transcript": str(run_dir / "transcript.log"),
        },
    )

    assert cli.main(["kimi", "observe", "--run-id", QUOTA_RUN]) == 0
    out = capsys.readouterr().out
    assert "exit:       1 (provider's code 403 quota exhausted)" in out
    assert "failure:    quota_exhausted" in out
    assert f"{run_dir / 'transcript.log'}:2" in out
    assert "source: transcript(lazy)" in out
    assert "usage:      unknown (provider emitted no usage events)" in out
    assert "cost:       unknown" in out

    assert cli.main(["kimi", "observe", "--run-id", QUOTA_RUN, "--json"]) == 0
    observation = json.loads(capsys.readouterr().out)
    telemetry = observation["telemetry"]
    assert telemetry["failure"]["kind"] == "quota_exhausted"
    assert telemetry["failure"]["provider_code"] == 403
    assert telemetry["failure"]["exit_code"] == 1
    assert telemetry["failure"]["evidence"]["line"] == 2
    assert telemetry["failure"]["source"] == "transcript(lazy)"
    # observe never rewrites someone else's closed run
    assert json.loads((run_dir / "meta.json").read_text(encoding="utf-8")) == legacy


def test_observe_prefers_the_recorded_cause_of_a_new_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from vibecrafted_core.control_plane import control_plane_home

    run_id = "w3-observe-recorded"
    home = control_plane_home().parent
    meta = home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    transcript = meta.parent / "transcript.log"
    kimi = tmp_path / "kimi"
    fixture = FIXTURES / QUOTA_RUN / "transcript.log"
    kimi.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"sys.stderr.write(open({str(fixture)!r}, encoding='utf-8').read())\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    kimi.chmod(0o755)
    monkeypatch.delenv("VIBECRAFTED_AGENT", raising=False)
    asyncio.run(
        AsyncSupervisor().run(
            run_id=run_id,
            command=[str(kimi)],
            root=tmp_path,
            meta_path=meta,
            transcript_path=transcript,
        )
    )
    _stub_server_transport(
        monkeypatch,
        {
            "run_id": run_id,
            "state": "failed",
            "agent": "kimi",
            "exit_code": 1,
            "latest_transcript": str(transcript),
        },
    )

    assert cli.main(["kimi", "observe", "--run-id", run_id]) == 0
    out = capsys.readouterr().out
    assert "exit:       1 (provider's code 403 quota exhausted)" in out
    assert f"{transcript}:2 · source: transcript\n" in out
    assert "transcript(lazy)" not in out
