"""Shell twin of the repository selector: parse_contract, argv rewrite, vc-start."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FACADE = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)


def _shell(
    script: str, *, cwd: Path, home: Path, shell: str = "bash"
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    prelude = f'source "{FACADE}" || exit 97\n'
    argv = (
        ["zsh", "-f", "-c", prelude + script]
        if shell == "zsh"
        else ["bash", "--noprofile", "--norc", "-c", prelude + script]
    )
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".vibecrafted").mkdir(parents=True)
    return path


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_parse_contract_repo_and_root_reconcile_through_one_selector(
    tmp_path: Path, home: Path, shell: str
) -> None:
    repo = tmp_path / "repo with space"
    repo.mkdir()
    script = """
_vetcoders_parse_skill_contract --repo "$1" --model m --prompt hello world || exit $?
printf 'root=%s\\nprompt=%s\\n' "$_vetcoders_contract_root" "$_vetcoders_contract_prompt"
_vetcoders_parse_skill_contract --root "$1" -p again || exit $?
printf 'root2=%s\\n' "$_vetcoders_contract_root"
_vetcoders_parse_skill_contract --repo="$1" --root "$1/." || exit $?
printf 'root3=%s\\n' "$_vetcoders_contract_root"
"""
    result = _shell(
        f"set -- {str(repo)!r}\n" + script, cwd=tmp_path, home=home, shell=shell
    )

    assert result.returncode == 0, result.stderr
    assert f"root={repo.resolve()}" in result.stdout
    assert "prompt=hello world" in result.stdout
    assert f"root2={repo.resolve()}" in result.stdout
    assert f"root3={repo.resolve()}" in result.stdout


def test_parse_contract_refuses_conflicts_and_missing_paths(
    tmp_path: Path, home: Path
) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    conflict = _shell(
        f"_vetcoders_parse_skill_contract --repo {left} --root {right} --prompt x",
        cwd=tmp_path,
        home=home,
    )
    assert conflict.returncode != 0
    assert "conflicting --repo" in conflict.stderr

    missing = _shell(
        f"_vetcoders_parse_skill_contract --repo {tmp_path / 'nope'} --prompt x",
        cwd=tmp_path,
        home=home,
    )
    assert missing.returncode != 0
    assert "--repo is not an existing directory" in missing.stderr

    legacy = _shell(
        f"_vetcoders_parse_skill_contract --root {tmp_path / 'nope'} --prompt x",
        cwd=tmp_path,
        home=home,
    )
    assert legacy.returncode != 0
    assert "--root is not an existing directory" in legacy.stderr


def test_parse_contract_worktree_forms(tmp_path: Path, home: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = f"""
_vetcoders_parse_skill_contract --worktree --repo {repo} --prompt x || exit $?
printf 'a=%s|%s\\n' "$_vetcoders_contract_worktree" "$_vetcoders_contract_root"
_vetcoders_parse_skill_contract --worktree true --prompt x || exit $?
printf 'b=%s\\n' "$_vetcoders_contract_worktree"
_vetcoders_parse_skill_contract --worktree=false --prompt x || exit $?
printf 'c=%s\\n' "$_vetcoders_contract_worktree"
_vetcoders_parse_skill_contract --worktree no --prompt x || exit $?
printf 'd=%s\\n' "$_vetcoders_contract_worktree"
_vetcoders_parse_skill_contract --prompt x || exit $?
printf 'e=%s\\n' "$_vetcoders_contract_worktree"
_vetcoders_parse_skill_contract --worktree=maybe --prompt x && exit 88
printf 'f=refused\\n'
"""
    result = _shell(script, cwd=tmp_path, home=home)

    assert result.returncode == 0, result.stderr
    assert f"a=true|{repo.resolve()}" in result.stdout
    assert "b=true" in result.stdout
    assert "c=false" in result.stdout
    assert "d=false" in result.stdout
    assert "e=" in result.stdout
    assert "f=refused" in result.stdout
    assert "--worktree expects true or false" in result.stderr
    assert "Git repository" not in result.stderr
    extra = _shell(
        f"""
_vetcoders_parse_skill_contract --base HEAD --repo {repo} --prompt x || exit $?
printf 'base=%s|%s\\n' "$_vetcoders_contract_base" "$_vetcoders_contract_root"
_vetcoders_parse_skill_contract --execution-runtime living-tree --repo {repo} --prompt x || exit $?
printf 'rt=%s|%s\\n' "$_vetcoders_contract_execution_runtime" "$_vetcoders_contract_root"
""",
        cwd=tmp_path,
        home=home,
    )
    assert extra.returncode == 0, extra.stderr
    assert f"base=HEAD|{repo.resolve()}" in extra.stdout
    assert f"rt=living-tree|{repo.resolve()}" in extra.stdout
    assert "Git repository" not in extra.stderr
    assert not any(
        path.is_dir() and path.name.startswith(repo.name) and path != repo
        for path in tmp_path.iterdir()
    )


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_rewrite_contract_root_argv_handles_repo_spellings(
    tmp_path: Path, home: Path, shell: str
) -> None:
    script = """
_vetcoders_rewrite_contract_root_argv /abs/root claude --worktree true --repo child --runtime terminal --prompt go
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
echo ---
_vetcoders_rewrite_contract_root_argv /abs/root claude --repo=child -p go
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
echo ---
_vetcoders_rewrite_contract_root_argv /abs/root claude --worktree --root child
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
echo ---
_vetcoders_rewrite_contract_root_argv /abs/root claude --prompt --repo stays
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
"""
    result = _shell(script, cwd=tmp_path, home=home, shell=shell)

    assert result.returncode == 0, result.stderr
    blocks = [block.strip().splitlines() for block in result.stdout.split("---\n")]
    assert blocks[0] == [
        "claude",
        "--worktree",
        "true",
        "--repo",
        "/abs/root",
        "--runtime",
        "terminal",
        "--prompt",
        "go",
    ]
    assert blocks[1] == ["claude", "--repo=/abs/root", "-p", "go"]
    assert blocks[2] == ["claude", "--worktree", "--root", "/abs/root"]
    assert blocks[3] == ["claude", "--prompt", "--repo", "stays"]


def test_select_repo_helper_words(tmp_path: Path, home: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    script = f"""
printf 'none=[%s]\\n' "$(_vetcoders_select_repo label '' '')"
printf 'repo=%s\\n' "$(_vetcoders_select_repo label {repo} '')"
printf 'root=%s\\n' "$(_vetcoders_select_repo label '' {repo})"
_vetcoders_select_repo label {repo} {tmp_path} >/dev/null; printf 'conflict=%s\\n' $?
_vetcoders_select_repo label {file_path} '' >/dev/null; printf 'file=%s\\n' $?
"""
    result = _shell(script, cwd=tmp_path, home=home)

    assert result.returncode == 0, result.stderr
    assert "none=[]" in result.stdout
    assert f"repo={repo.resolve()}" in result.stdout
    assert f"root={repo.resolve()}" in result.stdout
    assert "conflict=2" in result.stdout
    assert "file=2" in result.stdout
    assert "label: conflicting --repo" in result.stderr
    assert "label: --repo is not a directory" in result.stderr


def test_vc_start_prepare_arguments_accept_repo(tmp_path: Path, home: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = f"""
_vetcoders_start_prepare_arguments --repo {repo} operator || exit $?
printf 'start=%s|%s\\n' "$VIBECRAFTED_START_ROOT" "${{_vetcoders_start_frame_argv[*]}}"
_vetcoders_start_prepare_arguments --repo={repo} --root {repo} resume || exit $?
printf 'same=%s\\n' "$VIBECRAFTED_START_ROOT"
_vetcoders_start_prepare_arguments --repo {repo} --root {tmp_path} && exit 88
printf 'conflict=refused\\n'
_vetcoders_start_prepare_arguments --repo {tmp_path / "nope"} && exit 89
printf 'missing=refused\\n'
"""
    result = _shell(script, cwd=tmp_path, home=home)

    assert result.returncode == 0, result.stderr
    assert f"start={repo.resolve()}|operator" in result.stdout
    assert f"same={repo.resolve()}" in result.stdout
    assert "conflict=refused" in result.stdout
    assert "missing=refused" in result.stdout
    assert "vc-start: conflicting --repo" in result.stderr
    assert "vc-start: --repo is not an existing directory" in result.stderr
