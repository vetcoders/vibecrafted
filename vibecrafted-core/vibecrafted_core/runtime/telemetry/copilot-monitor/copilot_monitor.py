#!/usr/bin/env python3
"""copilot local token engine.

Read-only on ~/.copilot/session-state/*/events.jsonl. Writes quota.json under $VIBECRAFTED_HOME/telemetry/copilot/.
Product launcher: ``telemetry copilot line|once|sessions|daemon``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_engine import main

if __name__ == "__main__":
    raise SystemExit(main("copilot"))
