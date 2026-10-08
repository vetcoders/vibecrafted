"""Freshness gate for docs/katalog-launcherow.html.

The catalog page mixes curated prose with machine facts. The facts must track
their sources of truth (workflow registry, cli wrapper map, installer family,
deck dispatch case). The seed renderer owns the generated region and the
coverage assertions; this test just refuses a page that drifted.

Red here means: re-render with
``python3 docs/_render_launcher_catalog.py --write`` and update the prose the
renderer flagged — never hand-edit the generated region.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_launcher_catalog_page_matches_runtime_truth() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "docs" / "_render_launcher_catalog.py")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        "katalog launcherów zdryfował od źródeł prawdy:\n"
        + result.stdout
        + result.stderr
    )
