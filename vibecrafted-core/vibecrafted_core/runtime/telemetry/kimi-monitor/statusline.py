#!/usr/bin/env python3
"""
~/.kimi-code/statusline.sh
Custom status line runner for Kimi Code TUI.
Delegates directly to unified kimi_monitor accounting & statusline engine.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.kimi-code"))

try:
    import kimi_monitor

    sys.exit(kimi_monitor.cmd_tui())
except Exception:
    # Top-level TUI hook: never break the Kimi footer, but leave a traceback on stderr.
    logging.getLogger(__name__).exception("[kimi-monitor] statusline failed")
    sys.exit(0)
