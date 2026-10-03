#!/usr/bin/env python3
"""Cursor local token engine.

Cursor keeps no local token ledger. The engine reports status unavailable
because usage lives in Cursor cloud. It writes that receipt to
``$VIBECRAFTED_HOME/telemetry/cursor/quota.json`` and does not invent zeros.
Product launcher: ``telemetry cursor line|once|sessions|daemon``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_engine import main

if __name__ == "__main__":
    raise SystemExit(main("cursor"))
