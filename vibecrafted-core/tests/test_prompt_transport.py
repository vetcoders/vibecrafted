"""Contract for provider stdin transports (vibecrafted_core.prompt_transport)."""

from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path

from vibecrafted_core.prompt_transport import (
    encode_stream_json_user_turn,
    main,
    materialize_stdin_file,
    stdin_transport,
    stream_json_sibling,
)
from vibecrafted_core.supervisor_async import AsyncSupervisor

PROMPT = 'Zażółć gęślą jaźń — "quotes", back\\slash\nsecond line\ttab, 日本語\n'


def test_only_agy_uses_the_stream_json_transport() -> None:
    assert stdin_transport("agy") == "stream-json"
    for agent in ("claude", "codex", "junie", "grok", "cursor", "python"):
        assert stdin_transport(agent) == "text"


def test_user_turn_is_one_ndjson_line_that_round_trips_the_prompt() -> None:
    encoded = encode_stream_json_user_turn(PROMPT)

    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1
    payload = json.loads(encoded)
    assert payload["event"] == "user"
    assert payload["message"]["role"] == "user"
    assert payload["message"]["content"] == PROMPT


def test_materialize_writes_a_private_sibling_for_agy_and_rewrites_it(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(PROMPT, encoding="utf-8")

    stdin_file = materialize_stdin_file("agy", prompt)

    assert stdin_file == stream_json_sibling(prompt) == tmp_path / "prompt.ndjson"
    assert stat.S_IMODE(stdin_file.stat().st_mode) == 0o600
    assert json.loads(stdin_file.read_bytes())["message"]["content"] == PROMPT

    prompt.write_text("second launch\n", encoding="utf-8")
    assert (
        json.loads(materialize_stdin_file("agy", prompt).read_bytes())["message"][
            "content"
        ]
        == "second launch\n"
    )


def test_materialize_hands_text_providers_the_prompt_file_itself(
    tmp_path: Path,
) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text(PROMPT, encoding="utf-8")

    assert materialize_stdin_file("claude", prompt) == prompt
    assert not (tmp_path / "prompt.ndjson").exists()


def test_cli_materializes_into_an_explicit_target(tmp_path: Path, capsys) -> None:
    prompt = tmp_path / "plan_agy_prompt.md"
    prompt.write_text(PROMPT, encoding="utf-8")
    target = tmp_path / "plan_agy_prompt.ndjson"

    assert main(["agy", str(prompt), str(target)]) == 0

    assert capsys.readouterr().out.strip() == str(target)
    assert json.loads(target.read_bytes())["message"]["content"] == PROMPT


def test_async_supervisor_feeds_agy_one_stream_json_turn_and_reads_its_result(
    tmp_path: Path, monkeypatch
) -> None:
    """End to end through the supervisor: the fake agy sees exactly the encoded
    turn on stdin (never the raw markdown, never argv) and the parser lifts
    model, conversation and usage from its stream-json result into the run.
    """
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    prompt = tmp_path / "prompt.md"
    prompt.write_text(PROMPT, encoding="utf-8")
    report = tmp_path / "report.md"
    transcript = tmp_path / "transcript.log"
    capture = tmp_path / "stdin.captured"
    fake_agy = tmp_path / "fake_agy.py"
    fake_agy.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        f"Path({str(capture)!r}).write_bytes(sys.stdin.buffer.read())\n"
        'print(json.dumps({"event": "init", "conversation_id": "conv-42",'
        ' "init": {"model": "gemini-3.8-flash-high"}}))\n'
        'print(json.dumps({"event": "step_update", "step_update": {"state": "ACTIVE",'
        ' "step_type": "agent_response", "text_delta": "pong"}}))\n'
        'print(json.dumps({"event": "result", "result": {"conversation_id": "conv-42",'
        ' "status": "SUCCESS", "response": "pong", "usage": {"input_tokens": 12,'
        ' "output_tokens": 3, "thinking_tokens": 1, "cache_read_tokens": 4,'
        ' "total_tokens": 16}}}))\n'
        f"Path({str(report)!r}).write_text('---\\nrun_id: agy-turn\\nagent: agy\\n"
        "skill: test\\nstatus: completed\\nclaim_status: completed\\n---\\npong\\n')\n",
        encoding="utf-8",
    )

    handle = asyncio.run(
        AsyncSupervisor().run(
            run_id="agy-turn",
            command=[sys.executable, str(fake_agy)],
            root=tmp_path,
            env={"VIBECRAFTED_AGENT": "agy"},
            report_path=report,
            transcript_path=transcript,
            prompt_file_path=prompt,
        )
    )

    assert handle.exit_code == 0
    assert capture.read_bytes() == encode_stream_json_user_turn(PROMPT)
    assert stat.S_IMODE((tmp_path / "prompt.ndjson").stat().st_mode) == 0o600
    assert handle.agent_session_id == "conv-42"
    assert handle.agent_model == "gemini-3.8-flash-high"
    assert (handle.tokens_input, handle.tokens_cached_input, handle.tokens_output) == (
        12,
        4,
        3,
    )
    assert handle.resume_command == f"cd {tmp_path} && agy --conversation conv-42"
    assert '"event": "result"' in transcript.read_text(encoding="utf-8")
