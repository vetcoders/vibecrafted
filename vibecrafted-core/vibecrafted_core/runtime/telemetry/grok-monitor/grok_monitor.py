#!/usr/bin/env python3
"""grok local token engine.

Read-only on ~/.grok/sessions/*/*/usage.json. Writes quota.json under $VIBECRAFTED_HOME/telemetry/grok/.
Product launcher: ``telemetry grok line|once|sessions|daemon``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fleet_engine import main

if __name__ == "__main__":
    raise SystemExit(main("grok"))
