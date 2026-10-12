"""The server sweep reuses local native parsers without provider tasks."""

from __future__ import annotations

import json

from vibecrafted_core.runtime.telemetry.fleet_engine import collect_local_usage


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def test_all_provider_collection_unknowns_identity_retention_and_dedup(tmp_path):
    user, vc = tmp_path / "user", tmp_path / "vc"
    codex = user / ".codex/sessions/2026/10/12/rollout-real-id.jsonl"
    codex.parent.mkdir(parents=True)
    codex.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "real-id"}})
        + "\n"
        + json.dumps(
            {
                "type": "event_msg",
                "timestamp": "2026-10-12T10:00:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 20,
                            "cached_input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 25,
                        }
                    },
                },
            }
        )
        + "\n"
    )
    # A repeated import has the same native identity, regardless of basename.
    imported = codex.with_name("rollout-imported-real-id.jsonl")
    imported.write_text(codex.read_text())
    write(
        user / ".claude/projects/project/claude-id.jsonl",
        {
            "type": "assistant",
            "timestamp": "2026-10-12T10:00:00Z",
            "message": {
                "id": "msg-one",
                "model": "claude-test",
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 3,
                    "output_tokens": 4,
                },
            },
        },
    )
    write(
        user / ".grok/sessions/grok-id/leaf/usage.json",
        {
            "model": "grok-test",
            "inputTokens": 100,
            "cachedReadTokens": 60,
            "outputTokens": 10,
            "totalTokens": 110,
        },
    )
    write(
        user / ".junie/sessions/junie-id/events.jsonl",
        {
            "timestampMs": 1791799200000,
            "event": {
                "agentEvent": {
                    "kind": "LlmResponseMetadataEvent",
                    "modelUsage": [
                        {
                            "model": "junie-test",
                            "inputTokens": 10,
                            "cacheInputTokens": 2,
                            "cacheCreateTokens": 3,
                            "outputTokens": 4,
                        }
                    ],
                }
            },
        },
    )
    write(
        user / ".copilot/session-state/copilot-id/events.jsonl",
        {
            "type": "session.shutdown",
            "timestamp": "2026-10-12T10:00:00Z",
            "data": {
                "sessionStartTime": "2026-10-12T09:00:00Z",
                "modelMetrics": {
                    "copilot-test": {
                        "usage": {
                            "inputTokens": 100,
                            "outputTokens": 5,
                            "cacheReadTokens": 80,
                        }
                    }
                },
            },
        },
    )
    write(
        user / ".kimi-code/sessions/project/session_kimi-id/wire.jsonl",
        {
            "type": "usage.record",
            "usageScope": "turn",
            "time": 1791799200000,
            "model": "kimi-test",
            "usage": {
                "inputOther": 10,
                "inputCacheRead": 3,
                "inputCacheCreation": 2,
                "output": 4,
            },
        },
    )
    write(
        user / ".cursor/projects/project/agent-transcripts/cursor-id/cursor-id.jsonl",
        {
            "type": "result",
            "timestamp": "2026-10-12T10:00:00Z",
            "session_id": "cursor-id",
            "model": "cursor-test",
            "usage": {"inputTokens": 100, "outputTokens": 5, "cacheReadTokens": 80},
        },
    )
    write(
        user
        / ".gemini/antigravity-cli/brain/agy-id/.system_generated/logs/transcript.jsonl",
        {"type": "text", "text": "no authoritative token usage"},
    )
    projection = collect_local_usage(vc, user_home=user)
    assert len(projection["providers"]) == 8
    by_agent = {r["agent"]: r for r in projection["records"]}
    assert len(projection["records"]) == 8
    assert by_agent["codex"]["id"] == "real-id"
    assert by_agent["codex"]["processed_total"] == 25
    assert by_agent["claude"]["processed_total"] == 19
    assert by_agent["grok"]["processed_total"] == 110
    assert by_agent["junie"]["processed_total"] == 19
    assert by_agent["copilot"]["processed_total"] == 105
    assert by_agent["kimi"]["processed_total"] == 19
    assert by_agent["cursor"]["processed_total"] == 105
    assert by_agent["agy"]["processed_total"] is None
    codex.unlink()
    imported.unlink()
    retained = collect_local_usage(vc, user_home=user)
    assert len([r for r in retained["records"] if r["agent"] == "codex"]) == 1
    assert (
        json.loads((vc / "telemetry/local-usage.json").read_text())["owner"]
        == "vc-server"
    )


def test_unmeasured_and_malformed_are_not_zero(tmp_path):
    user, vc = tmp_path / "user", tmp_path / "vc"
    write(
        user / ".codex/sessions/2026/10/12/empty.jsonl",
        {"type": "session_meta", "payload": {"id": "empty"}},
    )
    write(
        user / ".kimi-code/sessions/project/session_empty/wire.jsonl",
        {
            "type": "usage.record",
            "usageScope": "turn",
            "time": 1791799200000,
            "usage": {"inputOther": "bad", "output": 2},
        },
    )
    result = collect_local_usage(vc, user_home=user)
    assert result["records"] and all(
        r["processed_total"] is None for r in result["records"]
    )
