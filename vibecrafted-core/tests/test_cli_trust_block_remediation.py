"""Public launcher admission for a recorded trust BLOCK.

Runs ``python -m vibecrafted_core.cli`` as a subprocess with GUARD enabled.
``VIBECRAFTED_GUARD=0`` is not used here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from vibecrafted_core import trust

CORE_ROOT = Path(__file__).resolve().parents[1]
REASON = "Founder-authorized repair of the recorded BLOCK"


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agents@vetcoders.io"], cwd=path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "contract-test"], cwd=path, check=True
    )
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _fake_claude(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(
        '#!/usr/bin/env bash\ncat >/dev/null\nprintf \'{"type":"assistant","message":"ok"}\\n\'\nexit 0\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


def _env(tmp_path: Path, fake_bin: Path, journal: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "VC_FRAME", "ZELLIJ"))
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(tmp_path / "crafted")
    env["VIBECRAFTED_GUARD"] = "1"
    env["VIBECRAFTED_TRUST_JOURNAL"] = str(journal)
    env["PYTHONPATH"] = str(CORE_ROOT)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def _cli(
    args: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "vibecrafted_core.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _block_head(repo: Path, journal: Path, sha: str) -> None:
    trust.note_verdict(
        repo=repo,
        journal=journal,
        sha=sha,
        verdict="block",
        claims=[
            {
                "claim": "agent fairness",
                "grade": "strong",
                "evidence": "subject != Authored-By",
            }
        ],
    )


def test_public_launcher_authorized_remediation_and_refusals(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sha = _git_repo(repo)
    journal = tmp_path / "journal.jsonl"
    _block_head(repo, journal, sha)
    fake_bin = tmp_path / "bin"
    _fake_claude(fake_bin)
    outside = tmp_path / "outside"
    outside.mkdir()
    env = _env(tmp_path, fake_bin, journal)

    ordinary = _cli(
        ["workflow", "claude", "--repo", str(repo), "--json", "-p", "ordinary work"],
        cwd=outside,
        env=env,
    )
    assert ordinary.returncode != 0, ordinary.stdout
    assert "vc-guard" in ordinary.stderr or "trust recorded block" in ordinary.stderr
    refused = (
        json.loads(ordinary.stdout) if ordinary.stdout.strip().startswith("{") else {}
    )
    if refused:
        assert refused.get("accepted") is False
        assert refused.get("guard", {}).get("blocking_verdict") == "block"

    missing_reason = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo),
            "--json",
            "--remediate-trust-block",
            "--remediation-task",
            "repair",
            "-p",
            "fix it",
        ],
        cwd=outside,
        env=env,
    )
    assert missing_reason.returncode == 2
    assert "remediation-reason" in missing_reason.stderr

    invalid_task = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo),
            "--json",
            "--remediate-trust-block",
            "--remediation-task",
            "workflow",
            "--remediation-reason",
            REASON,
            "-p",
            "fix it",
        ],
        cwd=outside,
        env=env,
    )
    assert invalid_task.returncode == 2
    assert "remediation-task" in invalid_task.stderr

    authorized = _cli(
        [
            "workflow",
            "claude",
            "--repo",
            str(repo),
            "--json",
            "--remediate-trust-block",
            "--remediation-task",
            "repair",
            "--remediation-reason",
            REASON,
            "-p",
            "authorized repair of guard admission",
        ],
        cwd=outside,
        env=env,
    )
    assert authorized.returncode == 0, authorized.stderr
    payload = json.loads(authorized.stdout)
    assert payload["accepted"] is True
    assert payload["guard"]["blocking_verdict"] == "block"
    assert payload["guard"]["continuation"] == "authorized_remediation"
    assert payload["guard"]["remediation_task"] == "repair"
    assert payload["guard"]["remediation_reason"] == REASON
    assert payload["guard"]["trust_verdict_unchanged"] is True
    assert payload["guard"]["blocking_sha"].startswith(sha[:8])

    audit = subprocess.run(
        [
            sys.executable,
            "-m",
            "vibecrafted_core.guard",
            "--repo",
            str(repo),
            "--journal",
            str(journal),
            "check",
            "--sha",
            sha,
        ],
        cwd=outside,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert audit.returncode == 1, audit.stdout
    check_payload = json.loads(audit.stdout)
    assert check_payload["allowed"] is False
    assert check_payload["blocking_verdict"] == "block"

    help_out = _cli(["help", "workflow"], cwd=outside, env=env)
    assert help_out.returncode == 0
    assert "--remediate-trust-block" in help_out.stdout
    argparse_help = _cli(["workflow", "--help"], cwd=outside, env=env)
    assert argparse_help.returncode == 0
    assert "--remediate-trust-block" in argparse_help.stdout
    assert "--remediation-reason" in argparse_help.stdout
    assert "--remediation-task" in argparse_help.stdout
