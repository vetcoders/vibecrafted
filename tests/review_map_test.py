"""Focused gate for scripts/review_map.py (intent s01-038).

The map must separate formatting-only noise from real change: a whitespace
reflow is map filler, while a word change, a deletion, a rename, or a new
file lands in the review-first list — never swept into the formatting bag.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "scripts" / "review_map.py"

spec = importlib.util.spec_from_file_location("review_map", MODULE_PATH)
review_map = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = review_map
spec.loader.exec_module(review_map)


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


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_area_for_maps_structural_layers() -> None:
    assert (
        review_map.area_for("docs/public/getting-started/install.md") == "docs-public"
    )
    assert review_map.area_for("docs/runtime/CONTRACT.md") == "docs-runtime"
    assert review_map.area_for("docs/FAQ.md") == "docs-general"
    assert (
        review_map.area_for("vibecrafted-core/vibecrafted_core/skills/pl/vc-x/SKILL.md")
        == "skills-pl"
    )
    assert (
        review_map.area_for("vibecrafted-core/vibecrafted_core/skills/vc-x/SKILL.md")
        == "skills"
    )
    assert review_map.area_for("vibecrafted-server/web/src/app.rs") == "server-rust"
    assert review_map.area_for("Makefile") == "root"


def test_reflowed_markdown_is_formatting_only(repo: Path) -> None:
    rel = "docs/public/getting-started/install.md"
    _commit_file(repo, rel, "alpha beta gamma\ndelta epsilon\n\nzeta\n")
    _write(repo, rel, "alpha beta\n  gamma delta\nepsilon\n\n\n\nzeta  \n")

    review = review_map.build_review_map(repo)
    assert len(review.changes) == 1
    change = review.changes[0]
    assert change.kind == "modified"
    assert change.formatting_only is True
    assert review.review_first == []


def test_word_change_is_semantic_and_review_first(repo: Path) -> None:
    rel = "docs/runtime/CONTRACT.md"
    _commit_file(repo, rel, "the runtime owns the gate\n")
    _write(repo, rel, "the operator owns the gate\n")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.formatting_only is False
    assert review.review_first == [change]


def test_deletion_rename_and_untracked_are_review_first(repo: Path) -> None:
    _commit_file(repo, "docs/adr/0001-old.md", "retired decision\n")
    _commit_file(repo, "docs/design/map.md", "design body\n")
    _git(repo, "rm", "-q", "docs/adr/0001-old.md")
    _git(repo, "mv", "docs/design/map.md", "docs/design/atlas.md")
    _write(repo, "scripts/new_helper.py", "print('hello')\n")

    review = review_map.build_review_map(repo)
    by_path = {c.path: c for c in review.changes}
    assert by_path["docs/adr/0001-old.md"].kind == "deleted"
    assert by_path["docs/design/atlas.md"].kind == "renamed"
    assert by_path["docs/design/atlas.md"].orig_path == "docs/design/map.md"
    assert by_path["scripts/new_helper.py"].kind == "added"
    first_paths = {c.path for c in review.review_first}
    assert first_paths == {
        "docs/adr/0001-old.md",
        "docs/design/atlas.md",
        "scripts/new_helper.py",
    }


def test_pure_rename_reports_content_unchanged(repo: Path) -> None:
    _commit_file(repo, "docs/operator/RUNBOOK.md", "same words stay\n")
    _git(repo, "mv", "docs/operator/RUNBOOK.md", "docs/operator/GUIDE.md")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.kind == "renamed"
    assert change.formatting_only is True
    assert change in review.review_first
    assert "content unchanged" in review_map._describe(change)


def test_python_indent_change_is_semantic(repo: Path) -> None:
    rel = "scripts/m.py"
    _commit_file(repo, rel, "def f(x):\n    if x:\n        a()\n        b()\n")
    _write(repo, rel, "def f(x):\n    if x:\n        a()\n    b()\n")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.kind == "modified"
    assert change.formatting_only is False
    assert review.review_first == [change]


def test_python_pure_formatting_stays_formatting_only(repo: Path) -> None:
    rel = "scripts/m.py"
    _commit_file(repo, rel, "a=b\n")
    _write(repo, rel, "a = b\n")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.formatting_only is True
    assert review.review_first == []


def test_python_syntax_error_is_never_formatting_only(repo: Path) -> None:
    rel = "scripts/m.py"
    _commit_file(repo, rel, "a = 1\n")
    _write(repo, rel, "a = (1\n")

    review = review_map.build_review_map(repo)
    assert review.changes[0].formatting_only is False


def test_yaml_indent_change_is_semantic(repo: Path) -> None:
    rel = "config/app.yaml"
    _commit_file(repo, rel, "root:\n  child: 1\n  other: 2\n")
    _write(repo, rel, "root:\n  child: 1\nother: 2\n")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.formatting_only is False
    assert review.review_first == [change]


def test_yaml_trailing_whitespace_is_formatting_only(repo: Path) -> None:
    rel = "config/app.yaml"
    _commit_file(repo, rel, "root:\n  child: 1\n")
    _write(repo, rel, "root:  \n  child: 1\n\n\n")

    review = review_map.build_review_map(repo)
    change = review.changes[0]
    assert change.formatting_only is True
    assert review.review_first == []


def test_makefile_leading_whitespace_is_semantic(repo: Path) -> None:
    rel = "Makefile"
    _commit_file(repo, rel, "target:\n\techo hi\n\techo bye\n")
    _write(repo, rel, "target:\n\techo hi\necho bye\n")

    review = review_map.build_review_map(repo)
    assert review.changes[0].formatting_only is False


def test_render_markdown_separates_noise_from_signal(repo: Path) -> None:
    _commit_file(repo, "docs/runtime/CONTRACT.md", "alpha beta\n")
    _commit_file(repo, "docs/runtime/RULES.md", "one two\n")
    _write(repo, "docs/runtime/CONTRACT.md", "alpha\n beta\n")
    _write(repo, "docs/runtime/RULES.md", "one three\n")

    review = review_map.build_review_map(repo)
    rendered = review_map.render_markdown(review)
    assert "# Review map — worktree vs HEAD" in rendered
    assert "1 formatting-only · 1 need real review" in rendered
    assert "## Not just formatting — review these first" in rendered
    assert (
        "M docs/runtime/RULES.md"
        in rendered.split("## Not just formatting")[1].split("## Map by area")[0]
    )
    assert "### docs-runtime — 2 paths (1 formatting-only, 1 semantic)" in rendered
    assert "M docs/runtime/CONTRACT.md (formatting-only)" in rendered


def test_render_json_counts_are_coherent(repo: Path) -> None:
    _commit_file(repo, "docs/FAQ.md", "a b c\n")
    _write(repo, "docs/FAQ.md", "a b  c\n")
    _write(repo, "bin/vc-new", "#!/bin/sh\n")

    review = review_map.build_review_map(repo)
    payload = json.loads(review_map.render_json(review))
    assert payload["total"] == 2
    assert payload["formatting_only"] == 1
    assert len(payload["review_first"]) == 1
    assert payload["areas"]["docs-general"]["formatting_only"] == 1
    assert payload["areas"]["launchers"]["semantic"] == 1


def test_main_end_to_end_on_clean_and_dirty_tree(repo: Path, capsys) -> None:
    assert review_map.main(["--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Tree is clean against HEAD" in out

    _commit_file(repo, "docs/runtime/CONTRACT.md", "alpha beta\n")
    _write(repo, "docs/runtime/CONTRACT.md", "alpha  beta\n")
    assert review_map.main(["--repo", str(repo), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"] == 1
    assert payload["formatting_only"] == 1
    assert payload["review_first"] == []
