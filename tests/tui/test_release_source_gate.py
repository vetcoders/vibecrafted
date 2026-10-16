"""Release triggers share executable steps; ceremony admits only proven source."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40


@pytest.mark.parametrize(
    ("filename", "job", "verify_tag"),
    [
        ("release.yml", "source-gate", "true"),
        ("gate-rehearsal.yml", "gate-rehearsal", "false"),
    ],
)
def test_release_triggers_cannot_define_independent_gate_steps(
    filename: str, job: str, verify_tag: str
) -> None:
    workflow = (REPO_ROOT / ".github/workflows" / filename).read_text()
    # Pin the entire job mapping, including absence of extra jobs/steps/env.
    jobs = workflow.split("\njobs:\n", 1)[1]
    assert jobs == (
        f"  {job}:\n"
        "    uses: ./.github/workflows/source-gate.yml\n"
        "    with:\n"
        f"      verify_tag: {verify_tag}\n"
    )
    assert "permissions:\n  contents: read\n" in workflow


def test_shared_gate_verifies_immutable_source_only_for_tags() -> None:
    workflow = (REPO_ROOT / ".github/workflows/source-gate.yml").read_text()
    assert "  workflow_call:\n" in workflow
    assert "      verify_tag:\n" in workflow
    assert "        type: boolean\n" in workflow
    assert "        default: false\n" in workflow
    assert re.search(
        r"- name: Verify immutable release source\n"
        r"        if: inputs.verify_tag\n",
        workflow,
    )
    rehearsal = (REPO_ROOT / ".github/workflows/gate-rehearsal.yml").read_text()
    assert '".github/workflows/source-gate.yml"' in rehearsal
    assert "  workflow_dispatch:\n" in rehearsal


@pytest.fixture
def rehearsal_guard(tmp_path: Path):
    jq = shutil.which("jq")
    assert jq, "guard fixture needs jq to evaluate gh's actual --jq predicate"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$GH_ARGS"\n'
        '[[ "${GH_FAIL:-0}" == 0 ]] || exit 1\n'
        "while [[ $# -gt 0 ]]; do\n"
        '  if [[ "$1" == --jq ]]; then\n'
        '    exec "$REAL_JQ" -r "$2" "$GH_FIXTURE"\n'
        "  fi\n"
        "  shift\n"
        "done\n"
        "exit 2\n"
    )
    fake_gh.chmod(0o755)
    fixture = tmp_path / "runs.json"
    args_path = tmp_path / "args"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "GH_FIXTURE": str(fixture),
        "GH_ARGS": str(args_path),
        "REAL_JQ": jq,
        "GH_FAIL": "0",
        "VIBECRAFTED_RELEASE_REPO": "fixture/repository",
    }

    def run(runs: list[dict], *, gh_fail: bool = False):
        fixture.write_text(json.dumps(runs))
        env["GH_FAIL"] = "1" if gh_fail else "0"
        result = subprocess.run(
            [str(REPO_ROOT / "scripts/check-release-rehearsal.sh"), SHA],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        args = args_path.read_text().splitlines()
        assert args[:2] == ["run", "list"]
        for flag, value in (
            ("--repo", "fixture/repository"),
            ("--workflow", "gate-rehearsal.yml"),
            ("--commit", SHA),
            ("--event", "workflow_dispatch"),
            ("--status", "success"),
        ):
            assert args[args.index(flag) + 1] == value
        return result

    return run


@pytest.mark.parametrize(
    ("runs", "gh_fail"),
    [
        ([], False),
        (
            [
                {
                    "databaseId": 7,
                    "headSha": "b" * 40,
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
            False,
        ),
        (
            [
                {
                    "databaseId": 7,
                    "headSha": SHA,
                    "status": "in_progress",
                    "conclusion": "success",
                }
            ],
            False,
        ),
        (
            [
                {
                    "databaseId": 7,
                    "headSha": SHA,
                    "status": "completed",
                    "conclusion": "failure",
                }
            ],
            False,
        ),
        ([], True),
    ],
    ids=["missing", "wrong-sha", "incomplete", "red", "gh-error"],
)
def test_rehearsal_guard_refuses_unproven_sha(rehearsal_guard, runs, gh_fail) -> None:
    result = rehearsal_guard(runs, gh_fail=gh_fail)
    assert result.returncode != 0
    assert SHA in result.stderr
    assert (
        "gh workflow run gate-rehearsal.yml --repo fixture/repository --ref main"
        in result.stderr
    )


def test_rehearsal_guard_admits_exact_success_among_other_runs(rehearsal_guard) -> None:
    result = rehearsal_guard(
        [
            {
                "databaseId": 1,
                "headSha": "b" * 40,
                "status": "completed",
                "conclusion": "success",
            },
            {
                "databaseId": 2,
                "headSha": SHA,
                "status": "completed",
                "conclusion": "failure",
            },
            {
                "databaseId": 3,
                "headSha": SHA,
                "status": "completed",
                "conclusion": "success",
            },
        ]
    )
    assert result.returncode == 0, result.stderr
    assert SHA in result.stdout
    assert "run 3" in result.stdout


def test_publisher_checks_exact_head_before_release_mutations() -> None:
    publisher = (REPO_ROOT / "scripts/publish-vibecrafted-release.sh").read_text()
    guard = 'bash "$ROOT/scripts/check-release-rehearsal.sh" "$HEAD_SHA"'
    assert guard in publisher
    for mutation in ("gh release create", "gh release upload", "gh release edit"):
        assert publisher.index(guard) < publisher.index(mutation)
