from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_INSTALLER = REPO_ROOT / "templates" / "hooks" / "install.sh"


def run(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args],
        cwd=repo,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def init_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repo = tmp_path / "target"
    repo.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIBECRAFTED_ROOT", None)
    env["HOME"] = str(tmp_path / "home")
    env["PATH"] = os.environ["PATH"]
    assert run(repo, "git", "init", "-q", env=env).returncode == 0
    run(repo, "git", "config", "user.name", "hooks-test", env=env)
    run(repo, "git", "config", "user.email", "hooks@vetcoders.io", env=env)
    installed = run(
        repo,
        "bash",
        str(HOOK_INSTALLER),
        "--activator",
        "manual",
        "--no-auto",
        "--force",
        "--no-gitignore",
        env=env,
    )
    assert installed.returncode == 0, installed.stderr
    return repo, env


def write_config(repo: Path, **flags: int) -> None:
    defaults = {
        "HUSKY_STRICT": 1,
        "HUSKY_WARN_MODE_ON_FEATURE": 0,
        "HUSKY_PRECOMMIT_CLAIMS": 0,
        "HUSKY_PRECOMMIT_SECRETS": 0,
        "HUSKY_PRECOMMIT_ENV_FILES": 0,
        "HUSKY_PRECOMMIT_LINT_STAGED": 0,
        "HUSKY_PRECOMMIT_PRETTIER_STAGED": 0,
        "HUSKY_PRECOMMIT_ESLINT_STAGED": 0,
        "HUSKY_PRECOMMIT_STYLELINT_STAGED": 0,
        "HUSKY_PRECOMMIT_TSC": 0,
        "HUSKY_PRECOMMIT_SEMGREP_STAGED": 0,
        "HUSKY_PRECOMMIT_LOCT_HEALTH": 0,
        "HUSKY_PRECOMMIT_LOCT_SUPPRESSIONS": 0,
        "HUSKY_PRECOMMIT_RUSTFMT_STAGED": 0,
        "HUSKY_PRECOMMIT_RUST_CARGO_CHECK": 0,
        "HUSKY_PRECOMMIT_PY_RUFF": 0,
        "HUSKY_PRECOMMIT_SH_SHELLCHECK": 0,
        "HUSKY_PREPUSH_PRETTIER_FULL": 0,
        "HUSKY_PREPUSH_RUFF_FULL": 0,
        "HUSKY_PREPUSH_SEMGREP_FULL": 0,
        "HUSKY_PREPUSH_TSC": 0,
        "HUSKY_PREPUSH_LOCT_CYCLES": 0,
        "HUSKY_PREPUSH_LOCT_COMMANDS": 0,
        "HUSKY_PREPUSH_VITEST": 0,
        "HUSKY_PREPUSH_CARGO_CLIPPY": 0,
        "HUSKY_PREPUSH_CARGO_TEST": 0,
        "HUSKY_PREPUSH_SECRETS": 0,
    }
    defaults.update(flags)
    (repo / ".husky" / "config.env").write_text(
        "\n".join(f"{key}={value}" for key, value in defaults.items()) + "\n",
        encoding="utf-8",
    )


def install_fake_ruff(tmp_path: Path, env: dict[str, str]) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    fake = bindir / "ruff"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "roots = [pathlib.Path(a) for a in args if not a.startswith('-') "
        "and a not in {'check', 'format'}]\n"
        "files = []\n"
        "for root in roots:\n"
        "    files.extend(root.rglob('*.py') if root.is_dir() else [root])\n"
        "if '--fix' in args or (args and args[0] == 'format' and '--check' not in args):\n"
        "    for path in files:\n"
        "        path.write_text(path.read_text().replace('x=1', 'x = 1'))\n"
        "bad = any('x=1' in path.read_text() for path in files)\n"
        "raise SystemExit(1 if bad else 0)\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"


def test_precommit_refuses_file_claimed_by_another_live_session(
    tmp_path: Path,
) -> None:
    repo, env = init_repo(tmp_path)
    fake = tmp_path / "bin" / "vibecrafted"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' \'{"action": "check", "ok": false, '
        '"conflicts": [{"run_id": "foreign"}]}\'\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env["PATH"] = f"{fake.parent}{os.pathsep}{env['PATH']}"
    write_config(repo, HUSKY_PRECOMMIT_CLAIMS=1)
    (repo / "foreign.py").write_text("x = 1\n", encoding="utf-8")
    run(repo, "git", "add", "foreign.py", env=env)

    result = run(repo, "bash", ".husky/pre-commit", env=env)

    assert result.returncode != 0
    assert "overlap another live session claim" in result.stdout + result.stderr


def test_precommit_formats_only_staged_index_and_preserves_unstaged_hunk(
    tmp_path: Path,
) -> None:
    repo, env = init_repo(tmp_path)
    install_fake_ruff(tmp_path, env)
    write_config(repo, HUSKY_PRECOMMIT_PY_RUFF=1)
    source = repo / "a.py"
    source.write_text("x = 0\n", encoding="utf-8")
    run(repo, "git", "add", "a.py", env=env)
    run(repo, "git", "commit", "-q", "-m", "seed", env=env)
    source.write_text("x=1\n", encoding="utf-8")
    run(repo, "git", "add", "a.py", env=env)
    source.write_text("x=2\n", encoding="utf-8")

    result = run(repo, "bash", ".husky/pre-commit", env=env)

    assert result.returncode == 0, result.stderr
    staged = run(repo, "git", "show", ":a.py", env=env)
    assert staged.stdout == "x = 1\n"
    assert source.read_text(encoding="utf-8") == "x=2\n"


def test_prepush_checks_commit_projection_not_foreign_dirty_worktree(
    tmp_path: Path,
) -> None:
    repo, env = init_repo(tmp_path)
    install_fake_ruff(tmp_path, env)
    write_config(repo, HUSKY_PREPUSH_RUFF_FULL=1)
    source = repo / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    run(repo, "git", "add", "a.py", env=env)
    run(repo, "git", "commit", "-q", "-m", "seed", env=env)
    head = run(repo, "git", "rev-parse", "HEAD", env=env).stdout.strip()
    source.write_text("x=1\n", encoding="utf-8")
    refs = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"

    result = run(repo, "bash", ".husky/pre-push", env=env, input_text=refs)

    assert result.returncode == 0, result.stderr
    assert source.read_text(encoding="utf-8") == "x=1\n"
