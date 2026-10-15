#!/usr/bin/env python3
"""omp local token engine.

Reads native selected-profile/agent-directory session JSONL files.
Writes quota.json under $VIBECRAFTED_HOME/telemetry/omp/.
Product launcher: ``telemetry omp line|once|sessions|archive``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_engine import main

if __name__ == "__main__":
    raise SystemExit(main("omp"))
