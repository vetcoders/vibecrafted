"""Contract tests for the local fleet token engines.

Fixtures follow the host record shapes measured on 2026-10-03. They are two or
three synthetic records per format, not copies of private sessions.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def test_omp_sums_per_event_assistant_usage_with_separate_cache_buckets(
    tmp_path: Path, capsys
) -> None:
    engine = _engine()
    store = tmp_path / "store"
    quota = tmp_path / "quota"
    session = (
        store
        / "agent"
        / "sessions"
        / "--repo-slug--"
        / "2026-10-14T19-39-23-385Z_01a13bed-3379-7342-ac6b-95b7c6bfe626.jsonl"
    )
    events = [
        {
            "type": "session",
            "version": 3,
            "id": "01a13bed-3379-7342-ac6b-95b7c6bfe626",
            "timestamp": "2026-10-14T19:39:23.385Z",
            "cwd": "/repo",
        },
        {
            "type": "message",
            "id": "a",
            "timestamp": "2026-10-14T19:55:09.117Z",
            "message": {
                "role": "assistant",
                "provider": "kimi-code",
                "model": "k3",
                "usage": {
                    "input": 100,
                    "output": 10,
                    "cacheRead": 40,
                    "cacheWrite": 5,
                    "totalTokens": 155,
                    "cost": {"total": 0.01},
                },
            },
        },
        {
            "type": "message",
            "id": "b",
            "timestamp": "2026-10-14T20:00:00.000Z",
            "message": {"role": "user", "content": "hi"},
        },
        {
            "type": "message",
            "id": "c",
            "timestamp": "2026-10-15T01:00:00.000Z",
            "message": {
                "role": "assistant",
                "provider": "kimi-code",
                "model": "k3",
                "usage": {
                    "input": 7,
                    "output": 3,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 10,
                },
            },
        },
    ]
    _write(session, "\n".join(json.dumps(event) for event in events) + "\n")
    # Lock sidecars next to transcripts are not usage files.
    _write(session.with_suffix(session.suffix + ".lock.os"), "")
    before = session.read_bytes()

    code = engine.main(
        "omp", ["sessions", "--store", str(store), "--quota-dir", str(quota)]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "processed_total 165" in out
    assert "cache_semantics separate_buckets" in out
    assert "sessions 1" in out
    saved = json.loads((quota / "quota.json").read_text(encoding="utf-8"))
    assert saved["metrics"]["fleet_processed_total"] == 165
    assert saved["cache_semantics"] == "separate_buckets"
    assert saved["last_session"]["id"] == "01a13bed-3379-7342-ac6b-95b7c6bfe626"
    archive = quota.parent / "archive" / "omp.jsonl"
    text = archive.read_text(encoding="utf-8")
    assert "kimi-code/k3" in text
    assert '"day":"2026-10-14"' in text
    assert '"day":"2026-10-15"' in text
    assert session.read_bytes() == before


def _omp_event(total: int) -> str:
    return (
        json.dumps(
            {
                "type": "message",
                "timestamp": "2026-10-15T01:00:00Z",
                "message": {
                    "role": "assistant",
                    "provider": "fixture",
                    "model": "fixture-model",
                    "content": "synthetic \u0142 usage",
                    "usage": {
                        "input": total - 3,
                        "output": 3,
                        "cacheRead": 0,
                        "cacheWrite": 0,
                        "totalTokens": total,
                    },
                },
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def _private_omp_environment(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    for key in ("OMP_PROFILE", "PI_PROFILE", "PI_CONFIG_DIR", "PI_CODING_AGENT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "product"))
    for kind in ("DATA", "STATE", "CACHE", "CONFIG"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / f"xdg-{kind.lower()}"))
    return home


def test_omp_default_profile_reads_only_native_sessions(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    session = home / ".omp" / "agent" / "sessions" / "default.jsonl"
    _write(session, _omp_event(10))
    _write(
        home / ".omp" / "profiles" / "work" / "agent" / "sessions" / "other.jsonl",
        _omp_event(900),
    )
    private_config = home / ".omp" / "agent" / "auth.json"
    _write(private_config, '{"private_fixture":true}\n')
    original_open = Path.open

    def deny_private_config(path, *args, **kwargs):
        assert path != private_config, "telemetry must not read native credentials"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_private_config)
    engine = _engine()
    assert engine.default_store("omp") == session.parent.parent
    assert engine.main("omp", ["once"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["processed_total"] == 10
    assert session.read_text(encoding="utf-8") == _omp_event(10)
    assert (tmp_path / "product" / "telemetry" / "omp" / "quota.json").is_file()


def test_omp_named_profile_overrides_legacy_profile_and_agent_dir(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    selected = (
        home / ".omp" / "profiles" / "work" / "agent" / "sessions" / "selected.jsonl"
    )
    legacy = (
        home / ".omp" / "profiles" / "legacy" / "agent" / "sessions" / "legacy.jsonl"
    )
    custom = tmp_path / "custom-agent"
    _write(selected, _omp_event(17))
    _write(legacy, _omp_event(700))
    _write(custom / "sessions" / "custom.jsonl", _omp_event(800))
    _write(home / ".omp" / "agent" / "sessions" / "default.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", " work ")
    monkeypatch.setenv("PI_PROFILE", "legacy")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    engine = _engine()
    assert engine.default_store("omp") == selected.parent.parent
    assert engine.main("omp", ["sessions"]) == 0
    assert "processed_total 17" in capsys.readouterr().out
    saved = json.loads(
        (tmp_path / "product" / "telemetry" / "omp" / "quota.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["metrics"]["fleet_processed_total"] == 17
    assert saved["metrics"]["session_count"] == 1


def test_omp_legacy_profile_uses_custom_config_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    selected = (
        home
        / ".omp-private"
        / "profiles"
        / "legacy"
        / "agent"
        / "sessions"
        / "selected.jsonl"
    )
    _write(selected, _omp_event(23))
    _write(home / ".omp" / "agent" / "sessions" / "default.jsonl", _omp_event(900))
    monkeypatch.setenv("PI_CONFIG_DIR", ".omp-private")
    monkeypatch.setenv("PI_PROFILE", "legacy")
    engine = _engine()
    assert engine.default_store("omp") == selected.parent.parent
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 23


@pytest.mark.parametrize("default_profile", ["", "default", "   "])
def test_omp_explicit_default_drops_inherited_legacy_profile_directory(
    tmp_path: Path, monkeypatch, capsys, default_profile: str
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    default = home / ".omp" / "agent"
    inherited = home / ".omp" / "profiles" / "legacy" / "agent"
    _write(default / "sessions" / "default.jsonl", _omp_event(29))
    _write(inherited / "sessions" / "legacy.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", default_profile)
    monkeypatch.setenv("PI_PROFILE", "legacy")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(inherited))
    engine = _engine()
    assert engine.default_store("omp") == default
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 29


def test_omp_explicit_agent_directory_is_not_a_config_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    custom = tmp_path / "private-agent"
    _write(custom / "sessions" / "selected.jsonl", _omp_event(31))
    _write(custom / "agent" / "sessions" / "wrong-geometry.jsonl", _omp_event(800))
    _write(home / ".omp" / "agent" / "sessions" / "default.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", "default")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    engine = _engine()
    assert engine.default_store("omp") == custom
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 31


def test_omp_explicit_store_ignores_native_selection_and_keeps_config_root_geometry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _private_omp_environment(tmp_path, monkeypatch)
    store = tmp_path / "manual-store"
    _write(store / "agent" / "sessions" / "selected.jsonl", _omp_event(37))
    _write(store / "sessions" / "wrong-geometry.jsonl", _omp_event(800))
    custom = tmp_path / "other-agent"
    _write(custom / "sessions" / "custom.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", "../invalid")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    assert _engine().main("omp", ["once", "--store", str(store)]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 37


def test_omp_invalid_native_profile_refuses_instead_of_scanning_default(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    _write(home / ".omp" / "agent" / "sessions" / "private.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", "../invalid")
    with pytest.raises(SystemExit) as error:
        _engine().main("omp", ["once"])
    assert error.value.code == 2
    assert "Invalid OMP profile" in capsys.readouterr().err
    assert not (tmp_path / "product").exists()


@pytest.mark.skipif(
    os.sys.platform not in {"darwin", "linux"}, reason="native XDG platforms"
)
def test_omp_xdg_selected_profile_requires_its_own_migrated_directory(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    native = home / ".omp" / "profiles" / "work" / "agent" / "sessions" / "native.jsonl"
    xdg_root = tmp_path / "xdg-data" / "omp"
    _write(native, _omp_event(41))
    _write(xdg_root / "sessions" / "default.jsonl", _omp_event(900))
    monkeypatch.setenv("OMP_PROFILE", "work")
    engine = _engine()
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 41
    selected = xdg_root / "profiles" / "work" / "sessions" / "migrated.jsonl"
    _write(selected, _omp_event(43))
    assert engine.default_store("omp") == selected.parent.parent
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 43


@pytest.mark.skipif(
    os.sys.platform not in {"darwin", "linux"}, reason="native XDG platforms"
)
def test_omp_xdg_default_flattens_agent_prefix_but_not_explicit_agent_dir(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    data = tmp_path / "xdg-data" / "omp"
    _write(data / "sessions" / "migrated.jsonl", _omp_event(47))
    _write(home / ".omp" / "agent" / "sessions" / "unmigrated.jsonl", _omp_event(900))
    engine = _engine()
    assert engine.default_store("omp") == data
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 47
    custom = tmp_path / "private-agent"
    _write(custom / "sessions" / "custom.jsonl", _omp_event(53))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    assert engine.default_store("omp") == custom
    assert engine.main("omp", ["once"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics"]["processed_total"] == 53


@pytest.mark.parametrize("split", ["inside_json", "inside_utf8", "before_newline"])
def test_omp_partial_tail_completion_matches_clean_scan_without_duplicates(
    tmp_path: Path, split: str
) -> None:
    engine = _engine()
    session = tmp_path / "session.jsonl"
    complete = _omp_event(10).encode("utf-8")
    cut = {
        "inside_json": len(complete) // 2,
        "inside_utf8": complete.index("\u0142".encode("utf-8")) + 1,
        "before_newline": len(complete) - 1,
    }[split]
    session.write_bytes(complete[:cut])
    partial, cache, scanned = engine.scan_file("omp", session, None)
    assert scanned and partial["processed_total"] == 0
    assert cache["offset"] == 0
    with session.open("ab") as handle:
        handle.write(complete[cut:])
    resumed, cache, scanned = engine.scan_file("omp", session, cache)
    clean, clean_cache, _ = engine.scan_file("omp", session, None)
    assert scanned
    assert resumed["processed_total"] == clean["processed_total"] == 10
    assert cache["offset"] == len(complete)
    assert cache["days"] == clean_cache["days"]
    unchanged, cache, scanned = engine.scan_file("omp", session, cache)
    assert not scanned and unchanged["processed_total"] == 10
    with session.open("ab") as handle:
        handle.write(
            b'{"type":"message","message":{"role":"user","content":"no usage"}}\n'
        )
    further, cache, scanned = engine.scan_file("omp", session, cache)
    assert scanned and further["processed_total"] == 10


def test_omp_partial_tail_retains_complete_prefix_only(tmp_path: Path) -> None:
    engine = _engine()
    session = tmp_path / "session.jsonl"
    prefix = _omp_event(13).encode("utf-8")
    tail = _omp_event(17).encode("utf-8")
    cut = len(tail) // 2
    session.write_bytes(prefix + tail[:cut])
    first, cache, _ = engine.scan_file("omp", session, None)
    assert first["processed_total"] == 13
    assert cache["offset"] == len(prefix)
    with session.open("ab") as handle:
        handle.write(tail[cut:])
    resumed, cache, _ = engine.scan_file("omp", session, cache)
    clean, clean_cache, _ = engine.scan_file("omp", session, None)
    assert resumed["processed_total"] == clean["processed_total"] == 30
    assert cache["days"] == clean_cache["days"]
    assert engine.scan_file("omp", session, cache)[0]["processed_total"] == 30


def test_omp_old_eof_cache_is_rebuilt_even_for_unchanged_file(tmp_path: Path) -> None:
    engine = _engine()
    session = tmp_path / "session.jsonl"
    _write(session, _omp_event(10))
    record, cache, _ = engine.scan_file("omp", session, None)
    cache.pop("complete_lines")
    cache["native"] = dict.fromkeys(engine.OMP_KEYS, 0)
    cache["record"] = {**record, "native": cache["native"], "processed_total": 0}
    cache["days"] = []
    repaired, cache, scanned = engine.scan_file("omp", session, cache)
    assert scanned and repaired["processed_total"] == 10
    assert cache["complete_lines"] is True


def test_omp_backend_refuses_daemon_without_dispatch_or_projection(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _private_omp_environment(tmp_path, monkeypatch)
    engine = _engine()

    def forbidden_daemon(*args, **kwargs):
        pytest.fail("OMP must not dispatch a resident collector")

    monkeypatch.setattr(engine, "_daemon", forbidden_daemon)
    with pytest.raises(SystemExit) as error:
        engine.main("omp", ["daemon"])
    assert error.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert engine.run("omp", "daemon", tmp_path / "store", tmp_path / "quota") == 2
    assert "bounded collection only" in capsys.readouterr().err
    assert not (tmp_path / "quota").exists()
    assert not (tmp_path / "product").exists()


def test_legacy_backend_daemon_dispatch_is_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    engine = _engine()
    calls = []

    def inert_daemon(*args):
        calls.append(args)
        return 51

    monkeypatch.setattr(engine, "_daemon", inert_daemon)
    store, quota = tmp_path / "store", tmp_path / "quota"
    assert (
        engine.main(
            "codex", ["daemon", "--store", str(store), "--quota-dir", str(quota)]
        )
        == 51
    )
    assert calls == [("codex", store, quota, 30)]


@pytest.mark.parametrize("mode", ["line", "once", "sessions", "archive"])
def test_omp_bounded_modes_remain_available(
    tmp_path: Path, monkeypatch, capsys, mode: str
) -> None:
    home = _private_omp_environment(tmp_path, monkeypatch)
    session = home / ".omp" / "agent" / "sessions" / "session.jsonl"
    _write(session, _omp_event(10))
    assert _engine().main("omp", [mode]) == 0
    assert capsys.readouterr().out
    saved = json.loads(
        (tmp_path / "product" / "telemetry" / "omp" / "quota.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["metrics"]["processed_total"] == 10
    assert saved["quota"]["status"] == "ok"
    assert "limit" not in saved["quota"]


@pytest.mark.parametrize("owner", ["canonical", "mirror"])
def test_deck_omp_daemon_refusal_preserves_legacy_and_bounded_dispatch(
    tmp_path: Path, owner: str
) -> None:
    package = ENGINE_PATH.parents[2]
    launcher = (
        package / "deck" / "vibecrafted"
        if owner == "canonical"
        else package.parents[1] / "scripts" / "vibecrafted"
    )
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(tmp_path / "product"),
        "VC_FRAME_SOCKET_DIR": str(tmp_path / "sockets"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
    }
    script = """
source "$1" telemetry help >/dev/null
_telemetry_run_engine() { printf 'DISPATCH:%s:%s\\n' "$1" "$2"; }
cmd_telemetry omp daemon
"""
    refused = subprocess.run(
        ["/bin/bash", "-c", script, "vibecrafted", str(launcher)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert refused.returncode != 0
    assert "unknown omp mode: daemon" in refused.stderr
    assert "DISPATCH:" not in refused.stdout
    assert "telemetry omp line|once|sessions|archive" in refused.stderr
    admitted = subprocess.run(
        [
            "/bin/bash",
            "-c",
            script.rsplit("cmd_telemetry omp daemon", 1)[0]
            + "cmd_telemetry codex daemon\n"
            + 'for mode in line once sessions archive; do cmd_telemetry omp "$mode"; done\n',
            "vibecrafted",
            str(launcher),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert admitted.returncode == 0, admitted.stderr
    assert admitted.stdout.splitlines() == [
        "DISPATCH:codex:daemon",
        "DISPATCH:omp:line",
        "DISPATCH:omp:once",
        "DISPATCH:omp:sessions",
        "DISPATCH:omp:archive",
    ]


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
