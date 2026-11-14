"""Execute the real hook against a scanner double that enforces its CLI contract."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def hook_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(REPO / "scripts/payload_hygiene.py", scripts / "payload_hygiene.py")
    _executable(
        scripts / "project-python",
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "staged.rs").write_text("fn main() {}\n")
    (repo / "space name.rs").write_text("fn other() {}\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "staged.rs", "space name.rs"], check=True
    )
    (repo / "foreign.rs").write_text("BLOCK_FINDING\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:/usr/bin:/bin"
    env["HOME"] = str(tmp_path / "home")
    env["SCANNER_CAPTURE"] = str(tmp_path / "scanner.json")
    env.pop("SEMGREP_SEND_METRICS", None)
    return repo, bindir, env


def _scanner(path: Path) -> None:
    _executable(
        path,
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "Path(os.environ['SCANNER_CAPTURE']).write_text(json.dumps({\n"
        "  'argv':args,'metrics':os.environ.get('SEMGREP_SEND_METRICS'),\n"
        "  'pythonpath':os.environ.get('PYTHONPATH'),'pythonhome':os.environ.get('PYTHONHOME')}))\n"
        "configs=[args[i+1] for i,a in enumerate(args) if a=='--config']\n"
        "if not configs or 'auto' in configs: raise SystemExit(42)\n"
        "if '--metrics' not in args or args[args.index('--metrics')+1]!='off': raise SystemExit(43)\n"
        "if os.environ.get('SEMGREP_SEND_METRICS')!='off': raise SystemExit(43)\n"
        "if '--error' not in args: raise SystemExit(44)\n"
        "targets=args[args.index('--')+1:]\n"
        "if set(targets)!={'staged.rs','space name.rs'}: raise SystemExit(45)\n"
        "if any('BLOCK_FINDING' in Path(target).read_text() for target in targets):\n"
        "  print('scanner: blocking staged security finding',file=sys.stderr)\n"
        "  raise SystemExit(1)\n",
    )


@pytest.mark.parametrize("launcher", ["semgrep", "uvx"])
@pytest.mark.parametrize("blocking", [False, True])
def test_real_hook_scans_only_staged_targets_without_metrics_and_preserves_blocking_exit(
    hook_repo, launcher: str, blocking: bool
) -> None:
    repo, bindir, env = hook_repo
    scanner = bindir / ("semgrep" if launcher == "semgrep" else "scanner-engine")
    _scanner(scanner)
    if launcher == "uvx":
        _executable(
            bindir / "uvx",
            f'#!/bin/sh\n[ "$1" = semgrep ] || exit 46\nshift\nexec {shlex.quote(str(scanner))} "$@"\n',
        )
    if blocking:
        (repo / "staged.rs").write_text("BLOCK_FINDING\n")
        subprocess.run(["git", "-C", str(repo), "add", "staged.rs"], check=True)
    result = subprocess.run(
        ["bash", str(REPO / "scripts/hooks/pre-commit")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    capture = json.loads(Path(env["SCANNER_CAPTURE"]).read_text())
    assert capture["metrics"] == "off"
    assert capture["pythonpath"] is capture["pythonhome"] is None
    configs = [
        capture["argv"][i + 1]
        for i, argument in enumerate(capture["argv"])
        if argument == "--config"
    ]
    assert set(configs) == {"p/ci", "p/rust"}
    if blocking:
        assert result.returncode != 0
        assert "blocking staged security finding" in result.stderr
        assert "Pre-commit passed" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Pre-commit passed" in result.stdout


def test_hook_overrides_ambient_metrics_opt_in_without_enabling_telemetry(
    hook_repo,
) -> None:
    repo, bindir, env = hook_repo
    _scanner(bindir / "semgrep")
    env["SEMGREP_SEND_METRICS"] = "on"
    env["PYTHONPATH"] = "foreign-runtime"
    result = subprocess.run(
        ["bash", str(REPO / "scripts/hooks/pre-commit")],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    capture = json.loads(Path(env["SCANNER_CAPTURE"]).read_text())
    assert capture["metrics"] == "off" and capture["pythonpath"] is None
