#!/usr/bin/env python3
"""Seed renderer + freshness gate for docs/katalog-launcherow.html.

The catalog page is hand-designed prose over machine facts. The prose stays
curated; the FACTS come from the runtime's single sources of truth:

  * ``vibecrafted_core.workflows.registry`` — ship stages (order + read/write
    phase) and every workflow definition,
  * ``vibecrafted_core.cli.SHELL_WRAPPER_VERBS`` — the deck-verb wrapper family,
  * ``scripts/vetcoders_install.py`` ``LAUNCHER_WRAPPERS`` — the installed family,
  * the deck's dispatch ``case`` block — ghost detection (commands advertised
    by help but never wired).

``--write`` regenerates the marked region in place; ``--check`` (default)
exits non-zero when the committed page drifted from the sources. The contract
test in ``tests/tui/test_launcher_catalog_contract.py`` runs the check, so a
deck/registry change without a re-render turns the gate red instead of
silently aging the page.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "docs" / "katalog-launcherow.html"
DECK = REPO / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"

sys.path.insert(0, str(REPO / "vibecrafted-core"))
sys.path.insert(0, str(REPO))

from vibecrafted_core.workflows.registry import (
    SHIP_STAGES,
    WORKFLOW_DEFINITIONS,
)

GEN_START = "<!-- gen:pipeline -->"
GEN_END = "<!-- /gen:pipeline -->"

# Help advertises these; the dispatch case must either wire them (then this
# list shrinks) or the page's "Rozjazdy" section keeps naming them as ghosts.
# settlements left this list when the deck wired `vibecrafted settlements`
# (read-only f/x/n ledger query).
KNOWN_GHOSTS = ("resume-session",)


def render_pipeline() -> str:
    """The 11-stage strip, straight from SHIP_STAGES with read/write phases."""
    parts: list[str] = []
    for i, stage in enumerate(SHIP_STAGES):
        cls = "r" if stage.phase == "read" else "w"
        if i:
            parts.append('<span class="arr">→</span>')
        parts.append(f'<span class="st {cls}">{stage.id}</span>')
    inner = "\n    ".join(parts)
    return (
        f'  {GEN_START}\n  <div class="pipeline">\n    {inner}\n  </div>\n  {GEN_END}'
    )


def splice(page: str) -> str:
    pattern = re.compile(
        r"^  " + re.escape(GEN_START) + r".*?" + re.escape(GEN_END),
        re.DOTALL | re.MULTILINE,
    )
    if not pattern.search(page):
        raise SystemExit("gen:pipeline markers missing from the page")
    return pattern.sub(lambda _: render_pipeline(), page, count=1)


def deck_dispatch_block() -> str:
    text = DECK.read_text(encoding="utf-8")
    start = text.index('case "$cmd" in')
    return text[start : text.index("esac", start)]


def coverage_errors(page: str) -> list[str]:
    """Fact assertions binding the hand-written prose to the sources of truth."""
    errors: list[str] = []
    for wf_id, definition in WORKFLOW_DEFINITIONS.items():
        if definition.runtime_kind == "internal":
            continue
        if not re.search(rf"\b{re.escape(wf_id)}\b", page):
            errors.append(f"workflow '{wf_id}' is absent from the catalog page")
    from vibecrafted_core.cli import SHELL_WRAPPER_VERBS

    for name in SHELL_WRAPPER_VERBS:
        if name == "telemetry":
            continue
        if name not in page:
            errors.append(f"wrapper '{name}' (deck-verb family) missing from the page")
    dispatch = deck_dispatch_block()
    for ghost in KNOWN_GHOSTS:
        if re.search(rf"^\s*{re.escape(ghost)}[|)]", dispatch, re.MULTILINE):
            errors.append(
                f"'{ghost}' is now wired in the deck — page still lists it as a ghost"
            )
        elif ghost not in page:
            errors.append(f"ghost '{ghost}' not documented in the Rozjazdy section")
    return errors


def main(argv: list[str]) -> int:
    write = "--write" in argv
    page = PAGE.read_text(encoding="utf-8")
    fresh = splice(page)
    errors = coverage_errors(fresh)
    if write:
        if fresh != page:
            PAGE.write_text(fresh, encoding="utf-8")
            print("katalog: region gen:pipeline przepisany")
        else:
            print("katalog: bez zmian")
        for e in errors:
            print(f"UWAGA: {e}")
        return 1 if errors else 0
    drift = fresh != page
    if drift:
        print(
            "DRIFT: strona różni się od renderu — uruchom: python3 docs/_render_launcher_catalog.py --write"
        )
    for e in errors:
        print(f"FAKT: {e}")
    return 1 if (drift or errors) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
