#!/usr/bin/env python3
"""Deterministic local provider used by installed-wheel lifecycle smoke tests."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

if "--help" in sys.argv:
    print("  --session-id <uuid>")
    raise SystemExit(0)
if "--version" in sys.argv:
    print("2.1.232 (Claude Code)")
    raise SystemExit(0)

session_id = sys.argv[sys.argv.index("--session-id") + 1]
role = os.environ.get("VIBECRAFTED_AGENT_ROLE", "agent")
captures_root = os.environ.get("SUPERVISION_CAPTURES", "").strip()
capture = (
    Path(captures_root) / f"{role}.json"
    if captures_root
    else Path(os.environ["SMOKE_CAPTURE"])
)
capture.write_text(
    json.dumps(
        {
            "pid": os.getpid(),
            "stdin_tty": os.isatty(0),
            "stdout_tty": os.isatty(1),
            "stderr_tty": os.isatty(2),
            "run_id": os.environ["VIBECRAFTED_RUN_ID"],
            "provider_session_id": session_id,
            "parent_root": os.environ["VIBECRAFTED_PARENT_ROOT"],
            "effective_root": os.environ["VIBECRAFTED_EFFECTIVE_ROOT"],
            "role": role,
            "relation_id": os.environ.get("VIBECRAFTED_SUPERVISION_RELATION_ID", ""),
            "peer_run_id": os.environ.get("VIBECRAFTED_SUPERVISION_PEER_RUN_ID", ""),
            "prompt_role": os.environ.get("VIBECRAFTED_PROMPT_ROLE", ""),
        }
    )
    + "\n",
    encoding="utf-8",
)
if role == "operator" and os.environ.get("SMOKE_OPERATOR_ACTION") == "stop":
    child_meta = Path(os.environ["VIBECRAFTED_SUPERVISED_CHILD_META"])
    while True:
        if child_meta.is_file():
            child = json.loads(child_meta.read_text(encoding="utf-8"))
            if child.get("status") == "active" and child.get("worker_pid"):
                break
        time.sleep(0.01)
    protocol = Path(os.environ["VIBECRAFTED_OPERATOR_PROTOCOL"])
    common = {
        "actor_run_id": os.environ["VIBECRAFTED_RUN_ID"],
        "child_run_id": child["run_id"],
        "relation_id": child["supervision"]["relation_id"],
    }
    protocol.write_text(
        json.dumps(
            {
                **common,
                "kind": "observation",
                "child_status": child["status"],
                "child_worker_pid": child["worker_pid"],
                "measured_usage": child["measured_usage"],
            }
        )
        + "\n"
        + json.dumps(
            {
                **common,
                "kind": "action",
                "action": "stop",
                "reason": "operator_policy_stop",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    while True:
        child = json.loads(child_meta.read_text(encoding="utf-8"))
        if child.get("liveness") == "terminal":
            break
        time.sleep(0.01)
    with protocol.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    **common,
                    "kind": "observation",
                    "child_status": child["status"],
                    "child_worker_pid": child["worker_pid"],
                    "measured_usage": child["measured_usage"],
                }
            )
            + "\n"
        )
    raise SystemExit(0)
usage_tokens = int(os.environ.get("SMOKE_USAGE_TOKENS", "0"))
transcript: Path | None = None
if usage_tokens and role == "agent":
    transcript = (
        Path(os.environ["CLAUDE_CONFIG_DIR"])
        / "projects"
        / "installed-wheel-smoke"
        / f"{session_id}.jsonl"
    )
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "cwd": os.getcwd(),
                "version": "2.1.232",
                "message": {
                    "id": "installed-wheel-message-1",
                    "usage": {
                        "input_tokens": usage_tokens,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
second_usage_tokens = int(os.environ.get("SMOKE_SECOND_USAGE_TOKENS", "0"))
if second_usage_tokens and transcript is not None:
    time.sleep(float(os.environ.get("SMOKE_SECOND_USAGE_DELAY", "0.5")))
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "assistant",
                    "sessionId": session_id,
                    "cwd": os.getcwd(),
                    "version": "2.1.232",
                    "message": {
                        "id": "installed-wheel-message-2",
                        "usage": {
                            "input_tokens": second_usage_tokens,
                            "cache_creation_input_tokens": 0,
                            "cache_read_input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            )
            + "\n"
        )
if os.environ.get("SMOKE_BLOCK") == "1":
    while True:
        time.sleep(0.05)
raise SystemExit(int(os.environ.get("SMOKE_EXIT", "0")))
