"""Focused gate for scripts/formatting_tree.py (intent s01-037).

The accidental formatter pass revealed a map of the repo; this tool must
make that map deliberate: lane membership mirrors the repo's own
pre-push case arms, the tree aggregates file and line counts per
directory, and the whole map is reachable end-to-end through main().
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "scripts" / "formatting_tree.py"
PRE_PUSH = REPO_ROOT / "scripts" / "hooks" / "pre-push"

spec = importlib.util.spec_from_file_location("formatting_tree", MODULE_PATH)
formatting_tree = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = formatting_tree
spec.loader.exec_module(formatting_tree)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    return repo


def _commit_file(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"add {rel}")


def _seed(repo: Path) -> None:
    _commit_file(repo, "docs/guide.md", "# Guide\n\nbody\n")
    _commit_file(repo, "docs/spec.yaml", "key: value\n")
    _commit_file(repo, "src/tool.py", "x = 1\n")
    _commit_file(repo, "bin/run.sh", "#!/bin/sh\necho ok\n")
    _commit_file(repo, "src/lib.rs", "fn main() {}\n")
    _commit_file(repo, "Makefile", "all:\n\techo ok\n")


# scripts/hooks/pre-push routes files into shell-style arrays per case arm;
# the formatting tree calls those same sets lanes. Parsing the hook keeps
# the two from drifting apart silently.
HOOK_ARM_LANES = {
    "MD_YAML_FILES": "prettier",
    "PY_FILES": "ruff",
    "SH_FILES": "shellcheck",
}

_ARM_RE = re.compile(r"^\s*((?:\*\.[\w-]+\|)*\*\.[\w-]+)\)\s+([A-Z_]+)\+=")


def _hook_lane_suffixes() -> dict[str, set[str]]:
    lanes: dict[str, set[str]] = {}
    for line in PRE_PUSH.read_text(encoding="utf-8").splitlines():
        match = _ARM_RE.match(line)
        if not match:
            continue
        patterns, var = match.groups()
        lane = HOOK_ARM_LANES.get(var)
        assert lane is not None, f"unmapped pre-push case arm: {line.strip()}"
        suffixes = {pattern[1:] for pattern in patterns.split("|")}
        lanes.setdefault(lane, set()).update(suffixes)
    return lanes


def test_lanes_match_pre_push_case_arms() -> None:
    module = {lane: set(suffixes) for lane, suffixes in formatting_tree.LANES}
    assert _hook_lane_suffixes() == module


def test_lane_for_routes_hook_suffixes_and_outsiders() -> None:
    for lane, suffixes in _hook_lane_suffixes().items():
        for suffix in suffixes:
            assert formatting_tree.lane_for(f"docs/a{suffix}") == lane
    assert formatting_tree.lane_for("src/lib.rs") == "outside"
    assert formatting_tree.lane_for("Makefile") == "outside"


def test_collect_tracks_only_committed_files(repo: Path) -> None:
    _seed(repo)
    (repo / "untracked.md").write_text("# stray\n", encoding="utf-8")
    entries = formatting_tree.collect(repo)
    paths = {e.path for e in entries}
    assert "docs/guide.md" in paths
    assert "untracked.md" not in paths
    assert len(entries) == 6


def test_line_counts_and_binary_detection(repo: Path) -> None:
    _commit_file(repo, "a.md", "one\ntwo\nthree\n")
    _commit_file(repo, "no_trailing_newline.md", "one\ntwo")
    blob = repo / "logo.png"
    blob.write_bytes(b"\x89PNG\0\0\0binary")
    _git(repo, "add", "logo.png")
    _git(repo, "commit", "-q", "-m", "add png")
    entries = {e.path: e for e in formatting_tree.collect(repo)}
    assert entries["a.md"].lines == 3
    assert entries["no_trailing_newline.md"].lines == 2
    assert entries["logo.png"].lines is None


def test_tree_aggregates_counts_per_directory(repo: Path) -> None:
    _seed(repo)
    root = formatting_tree.build_tree(formatting_tree.collect(repo))
    docs = root.dirs["docs"]
    assert docs.files_count == 2
    assert docs.lane_counts == {"prettier": 2}
    assert docs.lines == 4
    src = root.dirs["src"]
    assert src.lane_counts == {"ruff": 1, "outside": 1}
    assert root.files_count == 6
    assert {f.path for f in root.files} == {"Makefile"}


def test_area_rows_sort_by_lines_desc(repo: Path) -> None:
    _seed(repo)
    rows = formatting_tree.area_rows(
        formatting_tree.build_tree(formatting_tree.collect(repo))
    )
    areas = [row["area"] for row in rows]
    assert areas[0] == "docs/"
    assert "(root)" in areas


def test_render_tree_lines_respects_max_depth(repo: Path) -> None:
    _seed(repo)
    root = formatting_tree.build_tree(formatting_tree.collect(repo))
    shallow = formatting_tree.render_tree_lines(root, "", 0, max_depth=0)
    assert any("…" in line for line in shallow)
    assert not any("guide.md" in line for line in shallow)
    full = formatting_tree.render_tree_lines(root, "", 0, max_depth=None)
    assert any("guide.md" in line for line in full)


def test_main_end_to_end_text_and_json(repo: Path, capsys) -> None:
    _seed(repo)
    assert formatting_tree.main(["--repo", str(repo)]) == 0
    text = capsys.readouterr().out
    assert "# Vibecrafted formatting tree" in text
    assert "6 tracked files" in text
    assert "prettier 2" in text and "ruff 1" in text and "shellcheck 1" in text
    assert "outside lanes 2" in text
    assert "[P] guide.md" in text
    assert formatting_tree.main(["--repo", str(repo), "--json", "--top", "2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total_files"] == 6
    assert payload["lanes"]["prettier"] == 2
    assert payload["lanes"]["outside"] == 2
    assert payload["top"][0]["path"] == "docs/guide.md"
    assert payload["tree"]["dirs"]["docs"]["files"] == 2


def test_main_areas_only_skips_tree(repo: Path, capsys) -> None:
    _seed(repo)
    assert formatting_tree.main(["--repo", str(repo), "--areas-only"]) == 0
    text = capsys.readouterr().out
    assert "## Areas" in text
    assert "## Tree" not in text
    assert formatting_tree.main(["--repo", str(repo), "--json", "--areas-only"]) == 0
    assert json.loads(capsys.readouterr().out)["tree"] is None
