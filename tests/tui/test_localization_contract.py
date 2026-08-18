"""Localization contract: the PL doctrine mirror must not drift silently.

Three legs:
1. Coverage — every canonical skill/doctrine .md has a PL mirror under
   skills/pl/ (explicit allowlist for deliberate gaps, never silence).
2. Freshness — the canonical file's last git commit is not newer than its
   PL mirror's (a canonical edit without a PL follow-up is drift by
   definition; the operator rewrites doctrine in Polish and must trust it).
3. Presence i18n — docs/presence FRAMEWORK_I18N: every EN key has a PL
   counterpart, every phase in framework.html has a full PL override, and
   no PL override points at a phase that no longer exists.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills"
PL = SKILLS / "pl"
PRESENCE = REPO_ROOT / "docs" / "presence"

# Deliberate, operator-visible gaps. Adding a canonical .md without a PL
# mirror requires either the translation or an entry here — never silence.
COVERAGE_EXEMPT: set[str] = set()

# The runtime feedback ledger is appended per-run; its PL mirror follows in
# batches, so commit-timestamp freshness would flap on every ledger entry.
FRESHNESS_EXEMPT: set[str] = {"RUNTIME_FEEDBACK.md"}

# PL-only editorial assets that have no canonical counterpart by design.
PL_ONLY: set[str] = {
    "vc-decorate-PL-przykład.md",
    "vibecrafted_glossary_rules-PL.md",
    "vibecrafted-skill-PL-localization-spec.md",
}


def _canonical_md() -> list[str]:
    out: list[str] = []
    for path in SKILLS.rglob("*.md"):
        rel = path.relative_to(SKILLS).as_posix()
        if rel.startswith("pl/"):
            continue
        out.append(rel)
    return sorted(out)


def _git_commit_ts(rel_repo_path: str) -> int | None:
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", rel_repo_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    text = proc.stdout.strip()
    return int(text) if text.isdigit() else None


def _is_shallow() -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() == "true"


def test_every_canonical_doctrine_file_has_a_pl_mirror() -> None:
    missing = [
        rel
        for rel in _canonical_md()
        if rel not in COVERAGE_EXEMPT and not (PL / rel).is_file()
    ]
    assert not missing, (
        "canonical doctrine without a PL mirror (translate or add an explicit "
        f"COVERAGE_EXEMPT entry): {missing}"
    )


def test_pl_mirror_has_no_orphans() -> None:
    orphans = [
        rel.as_posix()
        for path in PL.rglob("*.md")
        if (rel := path.relative_to(PL)).as_posix() not in PL_ONLY
        and not (SKILLS / rel).is_file()
    ]
    assert not orphans, f"PL mirror files with no canonical counterpart: {orphans}"


def test_pl_mirror_is_not_older_than_canonical() -> None:
    if _is_shallow():
        pytest.skip("shallow clone: per-file git history is not trustworthy")
    stale: list[str] = []
    for rel in _canonical_md():
        if rel in COVERAGE_EXEMPT or rel in FRESHNESS_EXEMPT:
            continue
        pl_file = PL / rel
        if not pl_file.is_file():
            continue  # coverage leg reports this
        en_ts = _git_commit_ts(f"vibecrafted-core/vibecrafted_core/skills/{rel}")
        pl_ts = _git_commit_ts(f"vibecrafted-core/vibecrafted_core/skills/pl/{rel}")
        if en_ts is None or pl_ts is None:
            continue  # untracked yet — coverage/commit flow owns this
        if en_ts > pl_ts:
            stale.append(rel)
    assert not stale, (
        "canonical doctrine moved after its PL mirror (refresh the translation "
        f"or add a FRESHNESS_EXEMPT entry with a reason): {stale}"
    )


def _framework_i18n() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable: cannot evaluate FRAMEWORK_I18N literal")
    script = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const m = src.match(/var FRAMEWORK_I18N = (\{[\s\S]*?\n  \});/);
if (!m) { console.log("null"); process.exit(0); }
console.log(JSON.stringify(eval("(" + m[1] + ")")));
"""
    proc = subprocess.run(
        [node, "-e", script, str(PRESENCE / "framework.js")],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload is not None, "FRAMEWORK_I18N literal not found in framework.js"
    return payload


def _leaf_keys(obj: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in obj.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out |= _leaf_keys(value, path)
        else:
            out.add(path)
    return out


def test_presence_i18n_en_keys_all_have_pl_counterparts() -> None:
    i18n = _framework_i18n()
    missing = sorted(_leaf_keys(i18n["en"]) - _leaf_keys(i18n.get("pl", {})))
    assert not missing, f"EN i18n keys without a PL counterpart: {missing}"


def test_presence_pl_phase_overrides_match_html_phases() -> None:
    i18n = _framework_i18n()
    overrides = i18n.get("pl", {}).get("phaseOverrides", {})
    html = (PRESENCE / "framework.html").read_text(encoding="utf-8")
    html_phases = set(re.findall(r'data-phase="([a-z0-9-]+)"', html))
    if not html_phases:
        pytest.skip("framework.html carries no data-phase markers")
    missing = sorted(html_phases - set(overrides))
    dead = sorted(set(overrides) - html_phases)
    assert not missing, f"HTML phases without PL overrides: {missing}"
    assert not dead, f"PL phase overrides for phases absent from HTML: {dead}"
