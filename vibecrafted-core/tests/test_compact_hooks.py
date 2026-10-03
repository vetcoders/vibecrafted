from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vibecrafted_core import compact_hooks


def test_precompact_extracts_conversation_and_user_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    capture = tmp_path / "aicx-args.txt"
    home.mkdir()
    fake_bin.mkdir()
    aicx = fake_bin / "aicx"
    aicx.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$AICX_CAPTURE"\n',
        encoding="utf-8",
    )
    aicx.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("AICX_CAPTURE", str(capture))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "claude")

    assert compact_hooks.precompact('{"session_id":"sess-1"}') == 0

    calls = capture.read_text(encoding="utf-8").splitlines()
    assert calls == [
        "extract --agent claude --session sess-1 --conversation",
        "extract --agent claude --session sess-1 --conversation --user-only",
    ]
    journal = home / ".vibecrafted" / "runtime" / "compact-hooks.jsonl"
    assert '"event":"precompact"' in journal.read_text(encoding="utf-8")


def test_precompact_resolves_codex_session_from_sessions_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    other = tmp_path / "other"
    fake_bin = tmp_path / "bin"
    capture = tmp_path / "aicx-args.txt"
    sessions = home / ".codex" / "sessions" / "2026" / "06" / "13"
    home.mkdir()
    project.mkdir()
    other.mkdir()
    fake_bin.mkdir()
    sessions.mkdir(parents=True)
    session_file = sessions / "rollout-2026-06-13T19-09-57-019ec264.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-13T19:09:57.871Z",
                "type": "session_meta",
                "payload": {
                    "id": "019ec264-0b50-7bb2-9336-0aae5c841209",
                    "cwd": str(project),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    other_session = sessions / "rollout-2026-06-13T19-10-57-other.jsonl"
    other_session.write_text(
        json.dumps(
            {
                "timestamp": "2026-06-13T19:10:57.871Z",
                "type": "session_meta",
                "payload": {
                    "id": "other-session-id",
                    "cwd": str(other),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    aicx = fake_bin / "aicx"
    aicx.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$AICX_CAPTURE"\n',
        encoding="utf-8",
    )
    aicx.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("AICX_CAPTURE", str(capture))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".vibecrafted"))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "codex")
    monkeypatch.chdir(project)

    assert compact_hooks.precompact("{}") == 0

    assert capture.read_text(encoding="utf-8").splitlines() == [
        "extract --agent codex --session 019ec264-0b50-7bb2-9336-0aae5c841209 --conversation",
        "extract --agent codex --session 019ec264-0b50-7bb2-9336-0aae5c841209 --conversation --user-only",
    ]


def test_aicx_extract_timeout_is_bounded(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        assert kwargs["timeout"] == 0.25
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setenv("VIBECRAFTED_AICX_EXTRACT_TIMEOUT_SECONDS", "0.25")
    monkeypatch.setattr(compact_hooks.shutil, "which", lambda _: "/tmp/aicx")
    monkeypatch.setattr(compact_hooks.subprocess, "run", fake_run)

    assert compact_hooks.run_aicx_extract("codex", "sess-timeout") == "timeout"


def test_aicx_extract_default_timeout_allows_precompact_work(monkeypatch) -> None:
    monkeypatch.delenv("VIBECRAFTED_AICX_EXTRACT_TIMEOUT_SECONDS", raising=False)

    assert compact_hooks.aicx_extract_timeout_seconds() == 90.0


def test_postcompact_noop_is_schema_safe_json(capsys) -> None:
    assert compact_hooks.main(["postcompact-noop"]) == 0

    assert json.loads(capsys.readouterr().out) == {}


def test_postcompact_emits_manifest_and_chunks_extract(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    extract_dir = home / ".aicx" / "extracts" / "claude"
    recall_dir = tmp_path / "recall"
    extract_dir.mkdir(parents=True)
    (extract_dir / "sess-2_conversation.md").write_text(
        "old intent\nold intent\nrecent claim\nverified outcome\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RECALL_DIR", str(recall_dir))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "claude")
    monkeypatch.setenv("AICX_RECALL_CHUNK_LINES", "2")

    assert compact_hooks.postcompact('{"session_id":"sess-2"}') == 0
    payload = json.loads(capsys.readouterr().out)
    context = payload["hookSpecificOutput"]["additionalContext"]

    assert "LOOP is the foundation" in context
    assert "loct context --full --markdown" in context
    assert "verified outcome" in context
    assert (recall_dir / "claude" / "sess-2" / "chunk-000").is_file()
    assert (recall_dir / "claude" / "sess-2" / "chunk-001").is_file()


def test_recall_mode_emits_plain_context_not_postcompact_json(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    extract_dir = home / ".aicx" / "extracts" / "codex"
    extract_dir.mkdir(parents=True)
    (extract_dir / "sess-plain_conversation.md").write_text(
        "fresh operator ask\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "codex")
    monkeypatch.setenv("VIBECRAFTED_COMPACT_STATE", str(tmp_path / "state.json"))

    assert (
        compact_hooks.postcompact(
            '{"session_id":"sess-plain"}',
            output_format="plain",
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "fresh operator ask" in output
    assert "hookSpecificOutput" not in output
    try:
        json.loads(output)
    except json.JSONDecodeError:
        pass
    else:  # guardrail clarity
        raise AssertionError("recall mode must emit plain SessionStart context")


def test_postcompact_reads_existing_extract_without_running_aicx(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    extract_dir = home / ".aicx" / "extracts" / "codex"
    extract_dir.mkdir(parents=True)
    (extract_dir / "sess-read_conversation.md").write_text(
        "only existing extract\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "codex")
    monkeypatch.setattr(compact_hooks.shutil, "which", lambda _: "/tmp/aicx")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("postcompact must not run aicx extract")

    monkeypatch.setattr(compact_hooks, "run_aicx_extract", fail_if_called)

    assert compact_hooks.postcompact('{"session_id":"sess-read"}') == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]

    assert "only existing extract" in context


def test_postcompact_emits_only_delta_after_first_recall(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    home = tmp_path / "home"
    extract_dir = home / ".aicx" / "extracts" / "codex"
    recall_dir = tmp_path / "recall"
    state = tmp_path / "compact-state.json"
    extract_dir.mkdir(parents=True)
    extract = extract_dir / "sess-delta_conversation.md"
    extract.write_text("first claim\nsecond claim\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_RECALL_DIR", str(recall_dir))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_STATE", str(state))
    monkeypatch.setenv("VIBECRAFTED_COMPACT_AGENT", "codex")
    monkeypatch.setenv("AICX_RECALL_CHUNK_LINES", "20")

    assert compact_hooks.postcompact('{"session_id":"sess-delta","cwd":"/repo"}') == 0
    first = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "first claim" in first
    assert "second claim" in first

    extract.write_text(
        "first claim\nsecond claim\nthird claim\n",
        encoding="utf-8",
    )

    assert compact_hooks.postcompact('{"session_id":"sess-delta","cwd":"/repo"}') == 0
    second = json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "third claim" in second
    assert "first claim" not in second
    assert "raw lines 2..3" in second


def test_postcompact_missing_extract_is_loud(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert compact_hooks.postcompact('{"session_id":"missing"}') == 0
    payload = json.loads(capsys.readouterr().out)

    assert (
        "POSTCOMPACT RECALL DEGRADED"
        in payload["hookSpecificOutput"]["additionalContext"]
    )
