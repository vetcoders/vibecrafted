"""Contract tests for the vc-server-owned one-shot usage collection driver.

The driver runs each provider engine exactly once (no daemons) and reports
per-agent outcomes honestly. Monitor agents (kimi, agy) are probed through
their own scripts; their absence is an explicit ``absent`` outcome, and their
live network probing stays out of unit tests by design.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

DRIVER = (
    Path(__file__).resolve().parents[1]
    / "vibecrafted_core"
    / "runtime"
    / "telemetry"
    / "collect_all.py"
)


def _run_driver(
    home: Path,
    *extra: str,
    env: dict[str, str] | None = None,
    driver: Path = DRIVER,
) -> tuple[int, dict]:
    merged = dict(os.environ)
    merged.pop("PYTHONPATH", None)
    if env:
        merged.update(env)
    proc = subprocess.run(
        [sys.executable, str(driver), "--home", str(home), *extra],
        capture_output=True,
        text=True,
        env=merged,
        check=False,
        timeout=120,
    )
    assert proc.stdout.strip(), f"driver printed no receipt: {proc.stderr[-500:]}"
    return proc.returncode, json.loads(proc.stdout)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codex_store(home: Path) -> None:
    record = {
        "type": "token_count",
        "payload": {
            "info": {
                "total_token_usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 150,
                }
            }
        },
    }
    _write(
        home / ".codex" / "sessions" / "sess-collect-1.jsonl", json.dumps(record) + "\n"
    )


def test_collect_all_runs_fleet_engines_once_and_writes_quota_files(
    tmp_path: Path,
) -> None:
    fake_home = tmp_path / "operator-home"
    vc_home = tmp_path / "vc-home"
    _codex_store(fake_home)

    rc, receipt = _run_driver(
        vc_home,
        "--only",
        "codex,claude",
        env={"HOME": str(fake_home), "VIBECRAFTED_HOME": str(vc_home)},
    )

    assert rc == 0
    assert receipt["schema"] == "vibecrafted.usage-collection.v1"
    assert receipt["ok"] is True
    outcomes = {entry["agent"]: entry for entry in receipt["outcomes"]}
    assert set(outcomes) == {"codex", "claude"}
    assert outcomes["codex"]["status"] == "ok"

    quota = json.loads(
        (vc_home / "telemetry" / "codex" / "quota.json").read_text(encoding="utf-8")
    )
    assert quota["agent"] == "codex"
    assert quota["metrics"]["fleet_processed_total"] >= 130
    scan_cache = json.loads(
        (vc_home / "telemetry" / "codex" / "scan-cache.json").read_text(
            encoding="utf-8"
        )
    )
    assert scan_cache["files"], "collection must leave a scan-cache for the server"


def test_collect_all_marks_missing_monitor_engine_absent_not_ok(tmp_path: Path) -> None:
    staged = tmp_path / "engines"
    staged.mkdir()
    driver = staged / "collect_all.py"
    driver.write_text(DRIVER.read_text(encoding="utf-8"), encoding="utf-8")
    vc_home = tmp_path / "vc-home"

    rc, receipt = _run_driver(vc_home, "--only", "kimi", driver=driver)

    assert rc == 0
    assert receipt["ok"] is True
    (outcome,) = receipt["outcomes"]
    assert outcome["agent"] == "kimi"
    assert outcome["status"] == "absent"
    assert "not installed" in outcome["detail"]


def test_collect_all_requires_a_home() -> None:
    proc = subprocess.run(
        [sys.executable, str(DRIVER)],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k != "VIBECRAFTED_HOME"},
        check=False,
    )
    assert proc.returncode == 2
    assert "VIBECRAFTED_HOME" in proc.stderr
