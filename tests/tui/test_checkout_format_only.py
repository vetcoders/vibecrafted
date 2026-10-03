"""Exercise the Founder-facing Make target against real Git and Prettier."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
# Resolve the already-installed formatter before the autouse fixture isolates HOME.
_prettier = subprocess.run(
    [
        "npx",
        "--no-install",
        "--offline",
        "--package=prettier",
        "-c",
        "command -v prettier",
    ],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=False,
)
if _prettier.returncode or not _prettier.stdout.strip():
    pytest.skip(
        "existing offline Prettier installation required", allow_module_level=True
    )
PRETTIER_PACKAGE = Path(_prettier.stdout.strip()).resolve().parents[1]


def command(repo, *args):
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path):
    target = tmp_path / "repo"
    target.mkdir()
    (target / "node_modules" / ".bin").mkdir(parents=True)
    (target / "node_modules" / "prettier").symlink_to(PRETTIER_PACKAGE)
    (target / "node_modules" / ".bin" / "prettier").symlink_to(
        PRETTIER_PACKAGE / "bin" / "prettier.cjs"
    )
    (target / "scripts").mkdir()
    shutil.copy(ROOT / "Makefile", target)
    shutil.copy(ROOT / "scripts/checkout_format_only.py", target / "scripts")
    for args in [
        ("init", "-q"),
        ("config", "user.name", "test"),
        ("config", "user.email", "test@example.test"),
        ("config", "core.hooksPath", "/dev/null"),
    ]:
        assert command(target, "git", *args).returncode == 0
    (target / ".gitignore").write_text("node_modules/\n")
    (target / "selected.json").write_text('{"value":1}\n')
    (target / "foreign.json").write_text('{"value":2}\n')
    assert command(target, "git", "add", ".").returncode == 0
    assert command(target, "git", "commit", "-qm", "baseline").returncode == 0
    return target


def select(repo, *extra):
    return command(
        repo,
        "make",
        "checkout-format-only",
        "FILES=selected.json",
        f"PYTHON={sys.executable}",
        *extra,
    )


@pytest.mark.parametrize("staged", [False, True])
def test_preview_then_restore_only_selection(repo, staged):
    source = repo / "selected.json"
    source.write_text('{\n  "value": 1\n}\n')
    if staged:
        command(repo, "git", "add", "selected.json")
    foreign = repo / "foreign.json"
    foreign.write_text('{"value":3}\n')
    command(repo, "git", "add", "foreign.json")
    before = command(repo, "git", "diff", "--cached", "--", "foreign.json").stdout
    preview = select(repo)
    assert preview.returncode == 0, preview.stderr
    assert "Would restore format-only" in preview.stdout
    assert source.read_text() == '{\n  "value": 1\n}\n'
    applied = select(repo, "APPLY=1")
    assert applied.returncode == 0, applied.stderr
    assert source.read_text() == '{"value":1}\n'
    assert command(repo, "git", "diff", "HEAD", "--", "selected.json").stdout == ""
    assert (
        command(repo, "git", "diff", "--cached", "--", "foreign.json").stdout == before
    )
    assert foreign.read_text() == '{"value":3}\n'


@pytest.mark.parametrize("contents", ['{"value":99}\n', "{invalid json"])
def test_content_or_parser_failure_restores_nothing(repo, contents):
    source = repo / "selected.json"
    source.write_text(contents)
    result = select(repo, "APPLY=1")
    assert result.returncode != 0
    assert source.read_text() == contents


def test_separate_staged_content_is_preserved(repo):
    source = repo / "selected.json"
    source.write_text('{"value":99}\n')
    command(repo, "git", "add", "selected.json")
    source.write_text('{ "value": 1 }\n')
    result = select(repo, "APPLY=1")
    assert result.returncode != 0
    assert "separately staged" in result.stderr
    assert command(repo, "git", "show", ":selected.json").stdout == '{"value":99}\n'
    assert source.read_text() == '{ "value": 1 }\n'


def test_mixed_selection_is_validated_before_restore(repo):
    (repo / "selected.json").write_text('{ "value": 1 }\n')
    (repo / "foreign.json").write_text('{"value":99}\n')
    result = select(repo, "FILES=selected.json foreign.json", "APPLY=1")
    assert result.returncode != 0
    assert (repo / "selected.json").read_text() == '{ "value": 1 }\n'


@pytest.mark.parametrize(
    "path",
    [
        "../selected.json",
        "/selected.json",
        "missing.json",
        "*.json",
        "scripts/checkout_format_only.py",
    ],
)
def test_invalid_or_unsupported_selection(repo, path):
    result = select(repo, f"FILES={path}", "APPLY=1")
    assert result.returncode != 0


def test_mode_change_is_preserved(repo):
    source = repo / "selected.json"
    source.write_text('{ "value": 1 }\n')
    source.chmod(0o755)
    assert select(repo, "APPLY=1").returncode != 0
    assert source.stat().st_mode & 0o111


def test_path_with_spaces_uses_direct_cli(repo):
    source = repo / "with spaces.json"
    source.write_text('{"value":1}\n')
    command(repo, "git", "add", source.name)
    command(repo, "git", "commit", "-qm", "space path")
    source.write_text('{ "value": 1 }\n')
    result = command(
        repo,
        sys.executable,
        "scripts/checkout_format_only.py",
        "--apply",
        "--",
        source.name,
    )
    assert result.returncode == 0, result.stderr
    assert source.read_text() == '{"value":1}\n'


def test_symlink_is_preserved(repo):
    source = repo / "selected.json"
    source.unlink()
    source.symlink_to(repo / "foreign.json")
    assert select(repo, "APPLY=1").returncode != 0
    assert source.is_symlink()


def test_missing_selection_refuses(repo):
    result = select(repo, "FILES=", "APPLY=1")
    assert result.returncode != 0
    assert "select at least one file" in result.stderr


def test_missing_formatter_refuses_without_writing(repo):
    (repo / "node_modules" / ".bin" / "prettier").unlink()
    (repo / "node_modules" / "prettier").unlink()
    source = repo / "selected.json"
    source.write_text('{ "value": 1 }\n')
    result = select(repo, "APPLY=1")
    assert result.returncode != 0
    assert source.read_text() == '{ "value": 1 }\n'


def test_whitespace_inside_string_is_content(repo):
    source = repo / "selected.json"
    source.write_text('{"value":"a b"}\n')
    command(repo, "git", "add", "selected.json")
    command(repo, "git", "commit", "-qm", "string baseline")
    source.write_text('{"value":"ab"}\n')
    assert select(repo, "APPLY=1").returncode != 0
    assert source.read_text() == '{"value":"ab"}\n'


@pytest.mark.parametrize(
    ("name", "original", "formatted"),
    [
        ("selected.md", "-   item\n", "- item\n"),
        ("selected.yaml", "value:    1\n", "value: 1\n"),
        ("selected.js", "const value=1;\n", "const value = 1;\n"),
    ],
)
def test_other_prettier_formats(repo, name, original, formatted):
    source = repo / name
    source.write_text(original)
    command(repo, "git", "add", name)
    command(repo, "git", "commit", "-qm", "format baseline")
    source.write_text(formatted)
    result = select(repo, f"FILES={name}", "APPLY=1")
    assert result.returncode == 0, result.stderr
    assert source.read_text() == original
