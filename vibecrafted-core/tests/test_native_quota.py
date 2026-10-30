from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

TELEMETRY = Path(__file__).resolve().parents[1] / "vibecrafted_core/runtime/telemetry"
sys.path.insert(0, str(TELEMETRY))
import native_quota

NOW = datetime(2026, 10, 30, 0, 0, tzinfo=UTC).timestamp()


def _write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _event(
    *,
    at=NOW - 10,
    used=39,
    minutes=10080,
    reset=NOW + 86400,
    limit_id="codex",
    secondary=None,
):
    return {
        "timestamp": datetime.fromtimestamp(at, UTC).isoformat(),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {
                "limit_id": limit_id,
                "plan_type": "pro",
                "primary": {
                    "used_percent": used,
                    "window_minutes": minutes,
                    "resets_at": reset,
                },
                "secondary": secondary,
            },
        },
    }


def _engine():
    spec = importlib.util.spec_from_file_location(
        "quota_fleet_engine", TELEMETRY / "fleet_engine.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fleet_once_projects_native_weekly_quota_without_token_totals(tmp_path):
    engine = _engine()
    path = tmp_path / "store/sessions/latest.jsonl"
    _write(
        path,
        [
            _event(
                at=datetime.now(UTC).timestamp() - 2,
                reset=int(datetime.now(UTC).timestamp() + 86400),
            )
        ],
    )
    snapshot, _, _ = engine.snapshot_for(
        "codex", tmp_path / "store", mode="once", cache={}
    )
    windows = snapshot["quota"].get("windows", [])
    assert len(windows) == 1
    assert windows[0]["used_ratio"] == 0.39
    assert windows[0]["window_minutes"] == 10080
    assert snapshot["metrics"]["processed_total"] == 0


def test_native_weekly_window_keeps_scope_reset_and_derived_remaining(tmp_path):
    path = tmp_path / "latest.jsonl"
    event = _event()
    _write(path, [event])
    quota = native_quota.collect_codex([path], {}, now=NOW)
    window = quota["windows"][0]
    assert window["used_ratio"] == 0.39 and window["remaining_ratio"] == 0.61
    assert window["window_minutes"] == 10080 and window["resets_at"] == NOW + 86400
    assert window["observed_at"] == event["timestamp"] == quota["observed_at"]
    assert window["scope"] == {
        "pool": "openai",
        "limit_id": "codex",
        "plan_type": "pro",
    }
    assert "account" not in window["scope"] and "model" not in window["scope"]
    assert window["stale"] is False


@pytest.mark.parametrize("used", [-1, 101, True, float("nan"), float("inf"), "39"])
def test_invalid_native_percent_never_becomes_capacity(tmp_path, used):
    path = tmp_path / "latest.jsonl"
    _write(path, [_event(used=used)])
    quota = native_quota.collect_codex([path], {}, now=NOW)
    assert quota["status"] == "unavailable" and quota["windows"] == []


@pytest.mark.parametrize("at,reset", [(NOW - 301, NOW + 100), (NOW - 10, NOW - 1)])
def test_old_or_expired_observation_is_not_fresh_after_collection(tmp_path, at, reset):
    path = tmp_path / "latest.jsonl"
    event = _event(at=at, used=100, reset=reset)
    _write(path, [event])
    cache = {}
    first = native_quota.collect_codex([path], cache, now=NOW)
    second = native_quota.collect_codex([path], cache, now=NOW + 60)
    assert first["status"] == second["status"] == "stale"
    assert (
        first["windows"][0]["observed_at"]
        == second["windows"][0]["observed_at"]
        == event["timestamp"]
    )
    assert second["windows"][0]["remaining_ratio"] == 0


def test_warm_cache_never_reopens_unchanged_transcript(tmp_path, monkeypatch):
    path = tmp_path / "latest.jsonl"
    _write(path, [_event()])
    cache = {}
    native_quota.collect_codex([path], cache, now=NOW)

    def forbidden(*a, **k):
        raise AssertionError("warm quota refresh reread transcript")

    monkeypatch.setattr(Path, "open", forbidden)
    assert (
        native_quota.collect_codex([path], cache, now=NOW)["windows"][0]["used_ratio"]
        == 0.39
    )


def test_latest_native_observation_wins_per_limit_without_summing(tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    _write(
        first,
        [
            _event(at=NOW - 20, used=20),
            _event(at=NOW - 15, used=80, limit_id="model-pool"),
        ],
    )
    _write(second, [_event(at=NOW - 5, used=39)])
    quota = native_quota.collect_codex([first, second], {}, now=NOW)
    assert {w["scope"]["limit_id"]: w["used_ratio"] for w in quota["windows"]} == {
        "codex": 0.39,
        "model-pool": 0.8,
    }


def test_partial_record_and_token_totals_are_not_quota(tmp_path):
    path = tmp_path / "latest.jsonl"
    _write(
        path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"total_tokens": 999999999}},
                },
            }
        ],
    )
    with path.open("a") as out:
        out.write(json.dumps(_event()))
    assert native_quota.collect_codex([path], {}, now=NOW)["windows"] == []


def test_native_tail_bound_and_newest_file_bound(tmp_path, monkeypatch):
    paths = []
    for n in range(native_quota.MAX_FILES + 4):
        p = tmp_path / f"{n}.jsonl"
        _write(p, [_event()])
        paths.append(p)
    count = []
    real = native_quota._read_limits

    def record(path, now):
        count.append(path)
        return real(path, now)

    monkeypatch.setattr(native_quota, "_read_limits", record)
    assert native_quota.collect_codex(paths, {}, now=NOW)["windows"]
    assert len(count) == native_quota.MAX_FILES
    path = tmp_path / "oversized.jsonl"
    _write(path, [_event()])
    with path.open("a") as out:
        out.write("x" * (native_quota.MAX_TAIL_BYTES + 10))
    assert native_quota.collect_codex([path], {}, now=NOW)["windows"] == []


def test_missing_source_has_no_zero_or_percent_projection(tmp_path):
    quota = native_quota.collect_codex([], {}, now=NOW)
    assert quota["status"] == "unavailable" and quota["windows"] == []
    assert "observed_at" not in quota


def test_append_retains_verified_native_event_without_freshening(tmp_path):
    path = tmp_path / "latest.jsonl"
    event = _event()
    _write(path, [event])
    cache = {}
    initial = native_quota.collect_codex([path], cache, now=NOW)
    with path.open("a") as out:
        out.write("tool output\n" * (native_quota.MAX_TAIL_BYTES // 8))
    later = native_quota.collect_codex([path], cache, now=NOW + 301)
    assert initial["observed_at"] == later["observed_at"] == event["timestamp"]
    assert later["status"] == "stale" and later["windows"][0]["age_s"] == 311


@pytest.mark.parametrize("change", ["truncate", "rewrite_larger", "replace"])
def test_old_native_observation_is_not_inherited_after_source_changes(tmp_path, change):
    path = tmp_path / "latest.jsonl"
    _write(path, [_event()])
    cache = {}
    native_quota.collect_codex([path], cache, now=NOW)
    if change == "replace":
        replacement = tmp_path / "replacement"
        replacement.write_text("unrelated data\n")
        replacement.replace(path)
    else:
        path.write_text(
            "unrelated data\n"
            if change == "truncate"
            else "x" * (native_quota.MAX_TAIL_BYTES + 100)
        )
    assert native_quota.collect_codex([path], cache, now=NOW)["windows"] == []


def test_latest_explicit_null_secondary_removes_old_secondary(tmp_path):
    path = tmp_path / "latest.jsonl"
    secondary = {"used_percent": 80, "window_minutes": 10080, "resets_at": NOW + 86400}
    _write(
        path,
        [
            _event(at=NOW - 20, minutes=300, secondary=secondary),
            _event(at=NOW - 10, minutes=300, secondary=None),
        ],
    )
    quota = native_quota.collect_codex([path], {}, now=NOW)
    assert [window["id"] for window in quota["windows"]] == ["primary"]


def test_explicit_account_change_never_inherits_previous_account_capacity(tmp_path):
    path = tmp_path / "latest.jsonl"
    before = _event(at=NOW - 20, used=99)
    before["payload"]["rate_limits"]["account_id"] = "old-synthetic-account"
    after = _event(at=NOW - 10, used=15)
    after["payload"]["rate_limits"]["account_id"] = "new-synthetic-account"
    _write(path, [before, after])
    windows = native_quota.collect_codex([path], {}, now=NOW)["windows"]
    assert (
        len(windows) == 1 and windows[0]["scope"]["account"] == "new-synthetic-account"
    )
    assert windows[0]["used_ratio"] == 0.15


def test_quota_only_refresh_never_runs_token_scanner_or_archive(tmp_path, monkeypatch):
    engine = _engine()
    store = tmp_path / "store"
    root = tmp_path / "projection"
    event = _event(
        at=datetime.now(UTC).timestamp() - 2,
        reset=int(datetime.now(UTC).timestamp() + 86400),
    )
    _write(store / "sessions/latest.jsonl", [event])

    def forbidden(*a, **k):
        raise AssertionError("quota refresh replayed usage history")

    monkeypatch.setattr(engine, "scan_file", forbidden)
    monkeypatch.setattr(engine, "annotate_archive", forbidden)
    assert engine.refresh_native_quota("codex", store, root)
    snapshot = json.loads((root / "quota.json").read_text())
    assert snapshot["quota"]["observed_at"] == event["timestamp"]
    assert snapshot["metrics"] is None
    assert not (root.parent / "archive").exists()
    assert engine.refresh_native_quota("claude", store, root) is False


def test_native_profile_store_override_is_respected(tmp_path, monkeypatch):
    engine = _engine()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-profile"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-profile"))
    assert engine.default_store("codex") == tmp_path / "codex-profile"
    assert engine.default_store("claude") == tmp_path / "claude-profile"


def test_normal_once_preserves_quota_event_cache_across_large_append(tmp_path):
    engine = _engine()
    store = tmp_path / "store"
    path = store / "sessions/latest.jsonl"
    event = _event(
        at=datetime.now(UTC).timestamp() - 2,
        reset=int(datetime.now(UTC).timestamp() + 86400),
    )
    _write(path, [event])
    first, _, cache = engine.snapshot_for("codex", store, mode="once", cache={})
    with path.open("a") as out:
        out.write("tool output\n" * (native_quota.MAX_TAIL_BYTES // 8))
    second, _, _ = engine.snapshot_for("codex", store, mode="once", cache=cache)
    assert (
        first["quota"]["observed_at"]
        == second["quota"]["observed_at"]
        == event["timestamp"]
    )


def test_malformed_cached_window_cannot_invent_quota_or_crash(tmp_path):
    path = tmp_path / "latest.jsonl"
    _write(path, [_event()])
    cache = {}
    native_quota.collect_codex([path], cache, now=NOW)
    cache[str(path)]["native_quota"]["limits"][0]["windows"] = [
        {"scope": {"pool": "openai"}, "source": native_quota.SOURCE, "used_ratio": True}
    ]
    assert native_quota.collect_codex([path], cache, now=NOW)["windows"] == []


def test_unchanged_legacy_token_cache_is_inspected_once_for_native_quota(
    tmp_path, monkeypatch
):
    engine = _engine()
    store = tmp_path / "store"
    path = store / "sessions/latest.jsonl"
    event = _event(
        at=datetime.now(UTC).timestamp() - 2,
        reset=int(datetime.now(UTC).timestamp() + 86400),
    )
    _write(path, [event])
    _record, legacy, _ = engine.scan_file("codex", path, None)
    assert "native_quota" not in legacy
    cache = {"files": {str(path): legacy}}
    calls = []
    real = native_quota._read_limits

    def inspect(path, now):
        calls.append(path)
        return real(path, now)

    monkeypatch.setattr(native_quota, "_read_limits", inspect)
    first, _, cache = engine.snapshot_for("codex", store, mode="once", cache=cache)
    second, _, _ = engine.snapshot_for("codex", store, mode="once", cache=cache)
    assert first["quota"]["windows"] == second["quota"]["windows"]
    assert calls == [path]


def test_cross_process_writer_overlap_is_busy_without_lost_updates(tmp_path):
    import subprocess
    import time

    engine = _engine()
    store = tmp_path / "store"
    root = tmp_path / "projection"
    root.mkdir()
    event = _event(
        at=datetime.now(UTC).timestamp() - 2,
        reset=int(datetime.now(UTC).timestamp() + 86400),
    )
    event["payload"]["info"] = {
        "total_token_usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        }
    }
    _write(store / "sessions/latest.jsonl", [event])
    engine.atomic_write_json(
        root / "scan-cache.json", {"preserved_archive_metadata": "old", "files": {}}
    )
    engine.atomic_write_json(
        root / "quota.json", {"agent": "codex", "metrics": {"processed_total": 3}}
    )
    ready, release = tmp_path / "ready", tmp_path / "release"
    script = """import importlib.util,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import fleet_engine as engine
store,root,ready,release=map(Path,sys.argv[2:])
original=engine.snapshot_for
def slow(*args,**kwargs):
 ready.touch()
 while not release.exists():time.sleep(.01)
 kwargs["cache"]["preserved_archive_metadata"]="newer"
 return original(*args,**kwargs)
engine.snapshot_for=slow
raise SystemExit(engine.run("codex","once",store,root))
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(TELEMETRY),
            str(store),
            str(root),
            str(ready),
            str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.01)
        assert ready.exists()
        before = {
            name: (root / name).read_bytes()
            for name in ("scan-cache.json", "quota.json")
        }
        start = time.monotonic()
        with pytest.raises(engine.TelemetryBusy):
            engine.refresh_native_quota("codex", store, root)
        assert time.monotonic() - start < 0.5
        assert before == {name: (root / name).read_bytes() for name in before}
        release.touch()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert json.loads(stdout)["metrics"]["processed_total"] == 12
        assert engine.refresh_native_quota("codex", store, root)
        cache = json.loads((root / "scan-cache.json").read_text())
        assert cache["preserved_archive_metadata"] == "newer"
        assert (
            cache["files"][str(store / "sessions/latest.jsonl")]["record"][
                "processed_total"
            ]
            == 12
        )
        assert (
            json.loads((root / "quota.json").read_text())["metrics"]["processed_total"]
            == 12
        )
    finally:
        release.touch()
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=10)


def test_cli_publisher_reports_busy_without_overwriting_snapshot(tmp_path, capsys):
    engine = _engine()
    root = tmp_path / "projection"
    engine.atomic_write_json(root / "quota.json", {"preserve": "existing"})
    before = (root / "quota.json").read_bytes()
    with engine.publication_lease(root):
        assert engine.run("codex", "once", tmp_path / "store", root) == 75
    assert json.loads(capsys.readouterr().out)["status"] == "busy"
    assert (root / "quota.json").read_bytes() == before


def test_daemon_releases_publication_lease_between_ticks(tmp_path, monkeypatch):
    import signal

    engine = _engine()
    handlers = {}
    root = tmp_path / "projection"
    monkeypatch.setattr(
        engine.signal,
        "signal",
        lambda kind, callback: handlers.setdefault(kind, callback),
    )
    acquired = []

    def sleep(_):
        with engine.publication_lease(root):
            acquired.append(True)
        handlers[signal.SIGTERM]()

    monkeypatch.setattr(engine.time, "sleep", sleep)
    assert engine._daemon("codex", tmp_path / "store", root, 1) == 0
    assert acquired == [True]


def test_canonical_portable_lock_is_used_when_fcntl_import_is_unavailable(monkeypatch):
    import builtins

    original = builtins.__import__
    blocked = []

    def imports(name, *args, **kwargs):
        if name == "fcntl" and not blocked:
            blocked.append(True)
            raise ImportError("native Windows shape")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", imports)
    engine = _engine()
    assert engine.fcntl.__name__ == "vibecrafted_core.portable_lock"
