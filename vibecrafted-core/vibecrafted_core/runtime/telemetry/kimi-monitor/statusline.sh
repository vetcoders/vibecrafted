#!/usr/bin/env python3
"""
~/.kimi-code/statusline.sh
Custom status line runner for Kimi Code TUI.
Delegates directly to unified kimi_monitor accounting & statusline engine.
"""

import os
import sys

sys.path.insert(0, os.path.expanduser("~/.kimi-code"))

try:
    import kimi_monitor
    sys.exit(kimi_monitor.cmd_tui())
except Exception:
    sys.exit(0)
