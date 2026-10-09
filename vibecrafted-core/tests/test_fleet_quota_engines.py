"""Contract tests for the local fleet token engines.

Fixtures follow the host record shapes measured on 2026-10-03. They are two or
three synthetic records per format, not copies of private sessions.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import UTC, datetime
from pathlib import Path

ENGINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "runtime"
    / "telemetry"
    / "fleet_engine.py"
)


def _engine():
    spec = importlib.util.spec_from_file_location(
        "fleet_engine_under_test", ENGINE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_codex_uses_last_cumulative_total_and_does_not_add_cache() -> None:
    engine = _engine()
    early = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 4,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 12,
                }
            }
        },
    }
    last = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "cache_write_input_tokens": 3,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 2,
                    "total_tokens": 105,
                },
                "last_token_usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                },
            }
        },
    }
    wrapped = {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": 488,
                    "cached_input_tokens": 400,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 490,
                }
            },
        },
    }
    native = engine.parse_codex_tail(
        "\n".join(json.dumps(item) for item in (early, last, wrapped)) + "\n"
    )
    assert native is not None
    assert native["input_tokens"] == 488
    assert native["cached_input_tokens"] == 400
    assert engine.codex_processed(native) == 490
    assert engine.cache_semantics("codex") == "subset_of_input"


def test_codex_tail_window_reads_the_record_past_the_prefix(tmp_path: Path) -> None:
    engine = _engine()
    path = tmp_path / "sessions" / "rollout-session.jsonl"
    usage = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 900,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                    "total_tokens": 1020,
                }
            }
        },
    }
    prefix = (" " * 500_000) + "\n"
    _write(path, prefix + json.dumps(usage) + "\n")
    record, _entry, scanned = engine.scan_file("codex", path, None)
    assert scanned is True
    assert record["processed_total"] == 1020
    _again, _entry2, scanned_again = engine.scan_file("codex", path, _entry)
    assert scanned_again is False
    assert _again["processed_total"] == 1020


def test_codex_finds_usage_behind_a_large_trailing_event(tmp_path: Path) -> None:
    engine = _engine()
    path = tmp_path / "sessions" / "rollout-late.jsonl"
    usage = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 70,
                    "cached_input_tokens": 60,
                    "output_tokens": 7,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 77,
                }
            }
        },
    }
    trailing = ("tool-output " + ("x" * 80) + "\n") * 8000
    _write(path, json.dumps(usage) + "\n" + trailing)
    assert path.stat().st_size > engine.TAIL_BYTES
    record, _entry, scanned = engine.scan_file("codex", path, None)
    assert scanned is True
    assert record["processed_total"] == 77


def test_claude_sums_separate_buckets() -> None:
    engine = _engine()
    native = engine._blank_native(engine.CLAUDE_KEYS)
    first = {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 57813,
                "cache_read_input_tokens": 0,
                "output_tokens": 842,
            }
        },
    }
    second = {
        "type": "assistant",
        "message": {
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 1,
                "cache_read_input_tokens": 4,
                "output_tokens": 8,
            }
        },
    }
    assert engine._claude_from_line(json.dumps(first), native)
    assert engine._claude_from_line(json.dumps(second), native)
    assert engine.claude_processed(native) == 2 + 57813 + 0 + 842 + 10 + 1 + 4 + 8
    assert engine.cache_semantics("claude") == "separate_buckets"


def test_grok_processed_total_excludes_cached_read() -> None:
    engine = _engine()
    native, model = engine.parse_grok_document(
        {
            "session": {
                "inputTokens": 164841003,
                "outputTokens": 574065,
                "cachedReadTokens": 157562112,
                "cacheCreationTokens": 0,
                "reasoningTokens": 491643,
                "totalTokens": 165415068,
                "modelCalls": 4,
                "costUsdTicks": 304351724200,
                "primaryModelId": "grok-4",
            }
        }
    )
    assert model == "grok-4"
    assert native["cachedReadTokens"] == 157562112
    assert engine.grok_processed(native) == 165415068
    assert (
        engine.grok_processed(native) == native["inputTokens"] + native["outputTokens"]
    )
    assert engine.cache_semantics("grok") == "subset_of_input"


def test_junie_sums_separate_event_buckets(tmp_path: Path) -> None:
    engine = _engine()
    path = tmp_path / "sessions" / "session-1" / "events.jsonl"
    rows = [
        {
            "kind": "AgentEvent",
            "event": {
                "agentEvent": {
                    "modelUsage": [
                        {
                            "inputTokens": 3,
                            "cacheInputTokens": 0,
                            "cacheCreateTokens": 22845,
                            "outputTokens": 129,
                        }
                    ]
                }
            },
        },
        {
            "kind": "AgentEvent",
            "event": {
                "agentEvent": {
                    "modelUsage": [
                        {
                            "inputTokens": 3,
                            "cacheInputTokens": 22845,
                            "cacheCreateTokens": 1377,
                            "cacheReadTokens": 0,
                            "outputTokens": 194,
                        }
                    ]
                }
            },
        },
    ]
    _write(path, "".join(json.dumps(row) + "\n" for row in rows))
    record, entry, scanned = engine.scan_file("junie", path, None)
    assert scanned is True
    expected = 3 + 0 + 22845 + 129 + 3 + 22845 + 1377 + 0 + 194
    assert record["processed_total"] == expected
    extra = {
        "kind": "AgentEvent",
        "event": {
            "agentEvent": {
                "modelUsage": [
                    {
                        "inputTokens": 1,
                        "cacheInputTokens": 0,
                        "cacheCreateTokens": 0,
                        "cacheReadTokens": 2,
                        "outputTokens": 4,
                    }
                ]
            }
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(extra) + "\n")
    resumed, _entry2, scanned_again = engine.scan_file("junie", path, entry)
    assert scanned_again is True
    assert resumed["processed_total"] == expected + 1 + 0 + 0 + 2 + 4
    assert engine.cache_semantics("junie") == "separate_buckets"


def test_copilot_shutdown_is_not_double_counted_and_cache_is_not_added() -> None:
    engine = _engine()
    state = engine._empty_copilot_state()
    compaction = {
        "type": "session.compaction_complete",
        "data": {
            "compactionTokensUsed": {
                "inputTokens": 92792,
                "outputTokens": 6620,
                "cacheReadTokens": 90559,
                "cacheWriteTokens": 0,
            }
        },
    }
    shutdown = {
        "type": "session.shutdown",
        "data": {
            "modelMetrics": {
                "gpt-5.4": {
                    "usage": {
                        "inputTokens": 4924749,
                        "outputTokens": 23611,
                        "cacheReadTokens": 4762112,
                        "cacheWriteTokens": 0,
                    }
                }
            },
            "agentMetrics": {
                "main": {
                    "modelMetrics": {
                        "gpt-5.4": {
                            "usage": {
                                "inputTokens": 4924749,
                                "outputTokens": 23611,
                                "cacheReadTokens": 4762112,
                                "cacheWriteTokens": 0,
                            }
                        }
                    }
                }
            },
        },
    }
    assert engine._copilot_from_line(json.dumps(compaction), state)
    assert engine._copilot_from_line(json.dumps(shutdown), state)
    native = engine._copilot_native_from_state(state)
    assert native["inputTokens"] == 4924749
    assert native["cacheReadTokens"] == 4762112
    assert engine.copilot_processed(native) == 4924749 + 23611
    assert engine.cache_semantics("copilot") == "subset_of_input"


def test_cursor_is_unavailable_without_invented_zeros(tmp_path: Path) -> None:
    engine = _engine()
    snapshot, records, _cache = engine.snapshot_for(
        "cursor",
        tmp_path / ".cursor",
        mode="once",
        cache={},
    )
    assert records == []
    assert snapshot["status"] == "unavailable"
    assert snapshot["reason"] == engine.CURSOR_REASON
    assert "Cursor cloud" in snapshot["reason"]
    assert snapshot["metrics"] is None
    assert snapshot["cache_semantics"] is None
    assert "processed_total" not in snapshot["quota"]
    line = engine.render_statusline(snapshot)
    assert "unavailable" in line
    assert "0" not in line


def test_codex_sessions_sums_session_totals(tmp_path: Path, capsys) -> None:
    engine = _engine()
    store = tmp_path / "store"
    quota = tmp_path / "quota"

    def _session(name: str, total: int, cached: int) -> None:
        path = store / "sessions" / f"{name}.jsonl"
        payload = {
            "type": "token_count",
            "payload": {
                "info": {
                    "total_token_usage": {
                        "input_tokens": total - 5,
                        "cached_input_tokens": cached,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 1,
                        "total_tokens": total,
                    }
                }
            },
        }
        _write(path, json.dumps(payload) + "\n")

    _session("one", 1000, 800)
    archived = store / "archived_sessions" / "old.jsonl"
    archived_payload = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 50,
                    "cached_input_tokens": 40,
                    "output_tokens": 5,
                    "total_tokens": 55,
                }
            }
        },
    }
    _write(archived, json.dumps(archived_payload) + "\n")
    before = {
        path: path.read_bytes() for path in (store / "sessions" / "one.jsonl", archived)
    }
    code = engine.main(
        "codex",
        ["sessions", "--store", str(store), "--quota-dir", str(quota)],
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "processed_total 1055" in out
    assert "cache_semantics subset_of_input" in out
    assert "sessions 2" in out
    saved = json.loads((quota / "quota.json").read_text(encoding="utf-8"))
    assert saved["metrics"]["fleet_processed_total"] == 1055
    assert saved["cache_semantics"] == "subset_of_input"
    after = {
        path: path.read_bytes() for path in (store / "sessions" / "one.jsonl", archived)
    }
    assert after == before


def test_empty_store_is_explicit_and_second_once_reuses_cache(
    tmp_path: Path, capsys
) -> None:
    engine = _engine()
    store = tmp_path / "empty-codex"
    store.mkdir()
    quota = tmp_path / "quota"
    code = engine.main(
        "codex", ["once", "--store", str(store), "--quota-dir", str(quota)]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "empty"
    assert payload["metrics"]["processed_total"] == 0
    assert payload["cache_semantics"] == "subset_of_input"
    assert payload["live_total"] == 0
    assert payload["archived_total"] == 0
    assert (quota / "quota.json").is_file()
    assert not (quota.parent / "archive" / "codex.jsonl").exists()


def _codex_event(total: int, cached: int, iso: str) -> str:
    payload = {
        "timestamp": iso,
        "type": "token_count",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": total - 5,
                    "cached_input_tokens": cached,
                    "output_tokens": 5,
                    "total_tokens": total,
                }
            },
        },
    }
    return json.dumps(payload) + "\n"


def test_archive_keeps_rotated_days_and_skips_unchanged_files(
    tmp_path: Path, capsys
) -> None:
    """A day observed once stays after the source file is gone.

    The second pass chmods the sources to 000. Owner stat still works and
    owner read does not, so a cache miss would raise or drop the total.
    """

    engine = _engine()
    store = tmp_path / "store"
    quota = tmp_path / "quota"
    day1 = store / "sessions" / "2026" / "10" / "01" / "one.jsonl"
    day2 = store / "sessions" / "2026" / "10" / "02" / "two.jsonl"
    _write(day1, _codex_event(1000, 800, "2026-10-01T12:00:00Z"))
    _write(day2, _codex_event(55, 40, "2026-10-02T12:00:00Z"))

    def _run() -> dict:
        code = engine.main(
            "codex",
            ["archive", "--store", str(store), "--quota-dir", str(quota)],
        )
        assert code == 0, capsys.readouterr()
        return json.loads(capsys.readouterr().out)

    first = _run()
    archive = quota.parent / "archive" / "codex.jsonl"
    assert first["live_total"] == 1055
    assert first["archived_total"] == 1055
    assert first["appended"] == 2
    body = archive.read_text(encoding="utf-8")
    assert archive.is_file()

    day1.chmod(0)
    day2.chmod(0)
    second = _run()
    assert second["live_total"] == 1055
    assert second["archived_total"] == 1055
    assert second["appended"] == 0
    assert archive.read_text(encoding="utf-8") == body

    day2.unlink()
    third = _run()
    assert third["live_total"] == 1000
    assert third["archived_total"] == 1055
    assert "2026-10-02" in archive.read_text(encoding="utf-8")
    assert archive.read_text(encoding="utf-8") == body

    day1.unlink()
    fourth = _run()
    assert fourth["live_total"] == 0
    assert fourth["archived_total"] == 1055
    assert archive.read_text(encoding="utf-8") == body

    code = engine.main(
        "cursor",
        ["archive", "--store", str(store), "--quota-dir", str(quota)],
    )
    assert code == 0
    cursor = json.loads(capsys.readouterr().out)
    assert cursor["status"] == "unavailable"
    assert cursor["live_total"] is None
    assert cursor["archived_total"] is None
    assert not (quota.parent / "archive" / "cursor.jsonl").exists()


def test_archive_keeps_same_day_tokens_when_one_file_rotates(
    tmp_path: Path, capsys
) -> None:
    """Two sessions on one day. Deleting one must not shrink that day."""

    engine = _engine()
    store = tmp_path / "store"
    quota = tmp_path / "quota"
    day = store / "sessions" / "2026" / "10" / "01"
    kept = day / "kept.jsonl"
    gone = day / "gone.jsonl"
    _write(kept, _codex_event(1000, 800, "2026-10-01T01:00:00Z"))
    _write(gone, _codex_event(55, 40, "2026-10-01T02:00:00Z"))

    def _run() -> dict:
        code = engine.main(
            "codex",
            ["archive", "--store", str(store), "--quota-dir", str(quota)],
        )
        assert code == 0, capsys.readouterr()
        return json.loads(capsys.readouterr().out)

    first = _run()
    assert first["live_total"] == 1055
    assert first["archived_total"] == 1055
    gone.unlink()
    second = _run()
    assert second["live_total"] == 1000
    assert second["archived_total"] == 1055
    assert second["appended"] == 0


def test_archive_does_not_double_count_a_moved_session(tmp_path: Path, capsys) -> None:
    engine = _engine()
    store = tmp_path / "store"
    quota = tmp_path / "quota"
    source = store / "sessions" / "2026" / "10" / "01" / "rollout.jsonl"
    _write(source, _codex_event(1000, 800, "2026-10-01T01:00:00Z"))

    def _run() -> dict:
        code = engine.main(
            "codex",
            ["archive", "--store", str(store), "--quota-dir", str(quota)],
        )
        assert code == 0, capsys.readouterr()
        return json.loads(capsys.readouterr().out)

    first = _run()
    assert first["archived_total"] == 1000
    moved = store / "archived_sessions" / "rollout.jsonl"
    moved.parent.mkdir(parents=True, exist_ok=True)
    source.rename(moved)
    second = _run()
    assert second["live_total"] == 1000
    assert second["archived_total"] == 1000
    assert second["appended"] == 0


def test_kimi_archive_keeps_rotated_wire_days(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location(
        "kimi_monitor_under_test",
        ENGINE_PATH.parent / "kimi-monitor" / "kimi_monitor.py",
    )
    assert spec is not None and spec.loader is not None
    kimi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kimi)

    root = tmp_path / "sessions"
    first = root / "one" / "wire.jsonl"
    second = root / "two" / "wire.jsonl"
    stamp = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
    later = int(datetime(2026, 10, 2, tzinfo=UTC).timestamp() * 1000)

    def _event(
        kind: str, when: int, fresh: int, output: int, create: int, read: int
    ) -> str:
        return json.dumps(
            {
                "type": kind,
                "time": when,
                "model": "kimi-code/k3",
                "usage": {
                    "inputOther": fresh,
                    "output": output,
                    "inputCacheCreation": create,
                    "inputCacheRead": read,
                    "usageScope": "turn" if kind == "usage.record" else "subagent",
                },
            }
        )

    _write(
        first,
        "\n".join(
            [
                _event("usage.record", stamp, 100, 10, 5, 20),
                _event("subagent.completed", stamp, 7, 3, 0, 0),
                '{"type":"message","text":"ignored"}',
            ]
        )
        + "\n",
    )
    _write(second, _event("usage.record", later, 15, 5, 0, 0) + "\n")

    summary = kimi.harvest_kimi(root, mode="archive")
    assert summary["live_total"] == 145 + 20
    assert summary["archived_total"] == 165
    archive = Path(summary["archive"])
    text = archive.read_text(encoding="utf-8")
    assert "kimi-code/k3" in text
    assert '"day":"2026-10-01"' in text
    assert '"day":"2026-10-02"' in text

    first.chmod(0)
    second.chmod(0)
    again = kimi.harvest_kimi(root, mode="archive")
    assert again["live_total"] == 165
    assert again["archived_total"] == 165
    assert again["appended"] == 0
    assert archive.read_text(encoding="utf-8") == text

    second.unlink()
    shrunk = kimi.harvest_kimi(root, mode="archive")
    assert shrunk["live_total"] == 145
    assert shrunk["archived_total"] == 165
    assert "2026-10-02" in archive.read_text(encoding="utf-8")

    first.unlink()
    gone = kimi.harvest_kimi(root, mode="archive")
    assert gone["live_total"] == 0
    assert gone["archived_total"] == 165
    assert archive.read_text(encoding="utf-8") == text
    assert os.environ["VIBECRAFTED_HOME"] == str(home)
