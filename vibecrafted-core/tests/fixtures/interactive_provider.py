#!/usr/bin/env python3
"""Deterministic local provider used by installed-wheel lifecycle smoke tests."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

capture = Path(os.environ["SMOKE_CAPTURE"])
capture.write_text(
    json.dumps(
        {
            "pid": os.getpid(),
            "stdin_tty": os.isatty(0),
            "stdout_tty": os.isatty(1),
            "stderr_tty": os.isatty(2),
            "run_id": os.environ["VIBECRAFTED_RUN_ID"],
            "parent_root": os.environ["VIBECRAFTED_PARENT_ROOT"],
            "effective_root": os.environ["VIBECRAFTED_EFFECTIVE_ROOT"],
        }
    )
    + "\n",
    encoding="utf-8",
)
if os.environ.get("SMOKE_BLOCK") == "1":
    while True:
        time.sleep(0.05)
raise SystemExit(int(os.environ.get("SMOKE_EXIT", "0")))
