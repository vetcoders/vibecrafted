"""Artifact org/repo comes from the git origin, never from a symlink path."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.worktrees import canonical_artifact_root
from vibecrafted_core.runtime_receipt import (
    ArtifactIdentityError,
    _parse_owner_repo,
    artifact_org_repo,
)
from vibecrafted_core.workflow import _artifact_org_repo

REPO_ROOT = Path(__file__).resolve().parents[2]
INTENTS_CLI = (
    REPO_ROOT
    / "vibecrafted-core/vibecrafted_core/skills/vc-intents/scripts/intents_cli.py"
)
UTIL_SH = REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/scripts/lib/util.sh"
ORIGIN = "https://github.com/vetcoders/vibecrafted.git"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(path: Path, *, origin: str = ORIGIN) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "agents@vetcoders.io")
    _git(path, "config", "user.name", "artifact-identity")
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")
    if origin:
        _git(path, "remote", "add", "origin", origin)


def _symlink_checkout(tmp_path: Path, *, origin: str = ORIGIN) -> Path:
    """Physical tree under vibecrafted-suite, seen through vetcoders/vibecrafted."""
    physical = tmp_path / "physical" / "vibecrafted-suite" / "vibecrafted"
    _init_repo(physical, origin=origin)
    link_parent = tmp_path / "link" / "vetcoders"
    link_parent.mkdir(parents=True)
    link = link_parent / "vibecrafted"
    link.symlink_to(physical)
    return link


def test_parse_origin_accepts_forge_urls_and_rejects_checkout_paths() -> None:
    assert _parse_owner_repo(ORIGIN) == "vetcoders/vibecrafted"
    assert _parse_owner_repo("git@github.com:vetcoders/vibecrafted.git") == (
        "vetcoders/vibecrafted"
    )
    assert _parse_owner_repo("ssh://git@github.com/vetcoders/vibecrafted.git") == (
        "vetcoders/vibecrafted"
    )
    # An anonymized checkout-shaped path: a tracked test must never name the
    # real build host (tests/tui/test_no_tracked_file_names_this_checkout).
    assert (
        _parse_owner_repo("/Volumes/ws/vetcoders/vibecrafted-suite/vibecrafted") is None
    )
    assert (
        _parse_owner_repo("host:/Volumes/ws/vetcoders/vibecrafted-suite/vibecrafted")
        is None
    )
    assert _parse_owner_repo("") is None


def test_symlink_checkout_uses_origin_not_resolved_parent(tmp_path: Path) -> None:
    link = _symlink_checkout(tmp_path)
    assert artifact_org_repo(link) == ("vetcoders", "vibecrafted")
    assert _artifact_org_repo(link) == ("vetcoders", "vibecrafted")
    bucket = canonical_artifact_root(link, day="2026_0928")
    assert bucket.parts[-4:-1] == ("artifacts", "vetcoders", "vibecrafted")
    assert "vibecrafted-suite" not in bucket.parts


def test_path_shaped_origin_is_not_an_org(tmp_path: Path) -> None:
    physical = tmp_path / "physical" / "vibecrafted-suite" / "vibecrafted"
    _init_repo(physical, origin="")
    _git(physical, "remote", "add", "origin", str(physical))
    with pytest.raises(ArtifactIdentityError):
        artifact_org_repo(physical)
    assert _artifact_org_repo(physical) is None


def test_missing_origin_is_not_the_directory_name(tmp_path: Path) -> None:
    bare = tmp_path / "vibecrafted-suite"
    bare.mkdir()
    with pytest.raises(ArtifactIdentityError):
        artifact_org_repo(bare)
    assert _artifact_org_repo(bare) is None


def test_explicit_org_repo_overrides_a_missing_origin(tmp_path: Path) -> None:
    bare = tmp_path / "vibecrafted-suite"
    bare.mkdir()
    assert artifact_org_repo(bare, explicit="vetcoders/vibecrafted") == (
        "vetcoders",
        "vibecrafted",
    )


def test_intents_plan_bucket_follows_origin_through_a_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    link = _symlink_checkout(tmp_path)
    home = tmp_path / "vc-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    work = tmp_path / "workdir"
    work.mkdir()
    spec = importlib.util.spec_from_file_location("intents_cli_under_test", INTENTS_CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.main_args = None
        # plan dies later (no shards) but the bucket is chosen first.
        namespace = module.argparse.Namespace(
            workdir=work,
            repo=str(link),
            artifacts=None,
            stage="extract",
            host="fixture",
            agent=[],
            default_agent="codex",
            concurrency=1,
            timeout_min=1,
            limit=None,
            gate=None,
            gate_expect=None,
        )
        module.cmd_plan(namespace)
    day_dirs = list((home / "artifacts").glob("*/*/*"))
    assert day_dirs, "intents plan did not create an artifact day"
    rel = day_dirs[0].relative_to(home / "artifacts")
    assert rel.parts[:2] == ("vetcoders", "vibecrafted")
    assert "vibecrafted-suite" not in rel.parts


def test_spawn_org_repo_follows_origin_through_a_symlink(tmp_path: Path) -> None:
    link = _symlink_checkout(tmp_path)
    py = _python()
    script = f"""
set -euo pipefail
source {shlex_quote(UTIL_SH)}
export VIBECRAFTED_PYTHON={shlex_quote(py)}
org="$(spawn_org_repo {shlex_quote(link)} 0)"
printf '%s\\n' "$org"
"""
    env = os.environ.copy()
    env["VIBECRAFTED_PYTHON"] = py
    proc = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "vetcoders/vibecrafted"


def shlex_quote(value: Path | str) -> str:
    return shlex.quote(str(value))


def _python() -> str:
    return os.environ.get("VIBECRAFTED_PYTHON") or __import__("sys").executable
