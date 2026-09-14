"""``vibecrafted fork <agent> --session | --run-id`` through the real deck.

The deck is run as a subprocess with a fake vc-frame (developer mode), fake
provider binaries that only need to exist, an isolated VIBECRAFTED_HOME with
a seeded run record, and no inherited PYTHONPATH/PYTHONHOME. The composed
provider command is read back from the pane script vc-frame received, so the
assertions are about what would actually run — never a real provider session.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
PROVIDER_SESSION = "3f2b8c1e-0d2a-4f6b-9c1d-5e7a8b9c0d1e"
OTHER_SESSION = "7a1c2d3e-4f50-4617-8a9b-0c1d2e3f4a5b"
RUNTIME_SESSION = "0d1e5e7a-8b9c-4f6b-9c1d-3f2b8c1e0d2a"
RUN_ID = "work-260908-120000-11111"


def _write_exec(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _provider_stub(name: str) -> str:
    """Installed-CLI probe reads --version/--help; spawn never executes these."""
    help_markers = {
        "claude": "--resume --fork-session --print",
        "codex": "exec resume fork",
        "grok": "--resume --fork-session --prompt-file",
        "cursor-agent": "--resume --print --output-format",
        "agy": "--continue --conversation --print",
        "junie": "--resume --session-id",
    }
    markers = help_markers.get(name, "")
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "${1:-}" in\n'
        f"  --version|-V) printf '%s\\n' '{name} 0.0.0-test'; exit 0 ;;\n"
        f"  --help|-h) printf 'Usage: {name} {markers}\\n'; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "agents@vetcoders.io"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "fork-test"], cwd=path, check=True)
    (path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)
    return path


class _World:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.vibecrafted_home = self.home / ".vibecrafted"
        self.bin = tmp_path / "bin"
        self.capture = tmp_path / "vc-frame-args.txt"
        self.outside = tmp_path / "no git here"
        self.repo = _git_repo(tmp_path / "repo with space")
        self.outside.mkdir()
        self.vibecrafted_home.mkdir(parents=True)
        _write_exec(
            self.bin / "vc-frame",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "${1:-}" == "ls" || "${1:-}" == "list-sessions" ]]; then\n'
            '  printf "operator-test (attached)\\n"\n'
            "  exit 0\n"
            "fi\n"
            '{ printf "%s\\n" "$@"; } > "$CAPTURE_FILE"\n',
        )
        for provider in ("claude", "codex", "grok", "cursor-agent", "agy", "junie"):
            _write_exec(self.bin / provider, _provider_stub(provider))

    def seed_run(
        self, *, agent: str = "claude", session: str | None = PROVIDER_SESSION
    ) -> None:
        meta = {
            "run_id": RUN_ID,
            "agent": agent,
            "skill_code": "work",
            "status": "completed",
            "runtime_session_id": RUNTIME_SESSION,
            "root": str(self.repo),
        }
        if session:
            meta["agent_session_id"] = session
        reports = (
            self.vibecrafted_home
            / "artifacts"
            / "local"
            / "repo"
            / "2026_0908"
            / "reports"
        )
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{RUN_ID}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        runtime_dir = self.vibecrafted_home / "control_plane" / "runtime_runs" / RUN_ID
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        }
        env["HOME"] = str(self.home)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["VIBECRAFTED_HOME"] = str(self.vibecrafted_home)
        env["VIBECRAFTED_RUNTIME_BIN"] = str(self.bin)
        env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
        env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
        env["VIBECRAFTED_VC_FRAME_BIN"] = str(self.bin / "vc-frame")
        env["VC_FRAME_PANE_ID"] = "7"
        env["VC_FRAME_SESSION_NAME"] = "operator-test"
        env["CAPTURE_FILE"] = str(self.capture)
        return env

    def fork(
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        if self.capture.exists():
            self.capture.unlink()
        return subprocess.run(
            ["bash", str(LAUNCHER), "fork", *args],
            cwd=cwd or self.outside,
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def pane(self) -> list[str]:
        return self.capture.read_text(encoding="utf-8").splitlines()

    def command(self) -> str:
        pane = self.pane()
        script = Path(pane[pane.index("--") + 1])
        return script.read_text(encoding="utf-8")

    def launch_tokens(self) -> list[str]:
        import shlex

        return shlex.split(shlex.split(self.command(), comments=True)[-1])

    def admission(self) -> dict:
        tokens = self.launch_tokens()
        return json.loads(
            Path(tokens[tokens.index("--admission-file") + 1]).read_text(
                encoding="utf-8"
            )
        )


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_claude_fork_by_session_from_outside_git_with_repo(world: _World) -> None:
    result = world.fork(
        "claude",
        "--session",
        OTHER_SESSION,
        "--repo",
        str(world.repo),
        "--model",
        "claude-fable-5-1",
        "--permissions",
        "accept-edits",
    )

    assert result.returncode == 0, result.stderr
    pane = world.pane()
    assert "new-pane" in pane
    assert "new-tab" not in pane
    assert pane[pane.index("--cwd") + 1] == str(world.repo.resolve())
    assert (
        pane[pane.index("--name") + 1]
        == f"claude fork @{world.tmp_path.name}/repo with space {OTHER_SESSION}"
    )
    tokens = world.launch_tokens()
    assert "interactive-launch" in tokens
    assert tokens[tokens.index("--continuity") + 1] == "bare-fork"
    assert tokens[tokens.index("--parent-session") + 1] == OTHER_SESSION
    assert "--session-id" not in tokens
    admission = world.admission()
    assert admission["agent"] == "claude"
    assert admission["skill"] == "fork"
    assert admission["model_requested"] == "claude-fable-5-1"
    assert admission["root"] == str(world.repo.resolve())
    assert f"repo:      {world.repo.resolve()}" in result.stdout
    assert f"source-session: {OTHER_SESSION}" in result.stdout


def test_grok_fork_never_restores_code_or_moves_to_a_worktree(world: _World) -> None:
    result = world.fork(
        "grok",
        "--session",
        OTHER_SESSION,
        "--repo",
        str(world.repo),
        "--placement",
        "floating",
        "--permissions",
        "read-only",
    )

    assert result.returncode == 0, result.stderr
    pane = world.pane()
    assert "new-pane" in pane
    assert "--floating" in pane
    assert "--direction" not in pane
    assert "--near-current-pane" in pane
    tokens = world.launch_tokens()
    assert "interactive-launch" in tokens
    assert tokens[tokens.index("--continuity") + 1] == "bare-fork"
    assert tokens[tokens.index("--parent-session") + 1] == OTHER_SESSION
    joined = " ".join(tokens)
    assert "--restore-code" not in joined
    assert "--worktree" not in joined
    admission = world.admission()
    assert admission["agent"] == "grok"
    assert admission["skill"] == "fork"


def test_claude_fork_right_placement_uses_direction_right(world: _World) -> None:
    result = world.fork(
        "claude",
        "--session",
        OTHER_SESSION,
        "--repo",
        str(world.repo),
        "--placement",
        "right",
    )

    assert result.returncode == 0, result.stderr
    pane = world.pane()
    assert "new-pane" in pane
    assert "--near-current-pane" in pane
    assert "--direction" in pane
    assert pane[pane.index("--direction") + 1] == "right"
    assert "--floating" not in pane
    assert "pane:      right" in result.stdout


def test_unknown_fork_placement_is_refused_before_spawn(world: _World) -> None:
    result = world.fork(
        "claude",
        "--session",
        OTHER_SESSION,
        "--repo",
        str(world.repo),
        "--placement",
        "bottom",
    )
    assert result.returncode == 2
    assert "Unknown fork placement" in result.stderr
    assert not world.capture.exists()


def test_frame_pane_refusal_does_not_replace_the_source_session(
    world: _World,
) -> None:
    _write_exec(
        world.bin / "vc-frame",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == "ls" || "${1:-}" == "list-sessions" ]]; then\n'
        '  printf "operator-test (attached)\\n"\n'
        "  exit 0\n"
        "fi\n"
        'if [[ " $* " == *" new-pane "* ]]; then\n'
        "  printf 'no current pane in this guest context\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        '{ printf "%s\\n" "$@"; } > "$CAPTURE_FILE"\n',
    )
    result = world.fork(
        "claude",
        "--session",
        OTHER_SESSION,
        "--repo",
        str(world.repo),
        "--placement",
        "right",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "no replacement session or tab was created" in result.stderr
    assert "new-tab" not in result.stdout + result.stderr
    if world.capture.exists():
        captured = " ".join(world.pane())
        assert "new-tab" not in captured
        assert "new-pane" not in captured


def test_codex_fork_uses_native_fork_subcommand_with_inline_repo(world: _World) -> None:
    result = world.fork(
        "codex",
        "--session",
        OTHER_SESSION,
        f"--repo={world.repo}",
        "--permissions",
        "auto",
        "--model",
        "gpt-test",
    )

    assert result.returncode == 0, result.stderr
    pane = world.pane()
    assert "new-pane" in pane
    assert "new-tab" not in pane
    tokens = world.launch_tokens()
    assert "interactive-launch" in tokens
    assert tokens[tokens.index("interactive-launch") + 1] == "codex"
    assert tokens[tokens.index("--continuity") + 1] == "bare-fork"
    assert tokens[tokens.index("--parent-session") + 1] == OTHER_SESSION
    assert "codex resume" not in " ".join(tokens)
    admission = world.admission()
    assert admission["agent"] == "codex"
    assert admission["skill"] == "fork"
    assert admission["model_requested"] == "gpt-test"
    assert "repo with space" in admission["root"]


def test_run_id_resolves_the_recorded_provider_session_and_its_repository(
    world: _World,
) -> None:
    world.seed_run()

    result = world.fork("claude", "--run-id", RUN_ID)

    assert result.returncode == 0, result.stderr
    tokens = world.launch_tokens()
    assert tokens[tokens.index("--parent-session") + 1] == PROVIDER_SESSION
    assert tokens[tokens.index("--continuity") + 1] == "bare-fork"
    assert f"source-session: {PROVIDER_SESSION}" in result.stdout
    assert f"source:    {RUN_ID}" in result.stdout
    pane = world.pane()
    assert pane[pane.index("--cwd") + 1] == str(world.repo.resolve())
    assert str(world.outside.resolve()) not in pane


def test_explicit_root_of_a_different_checkout_is_refused(world: _World) -> None:
    world.seed_run()
    other = _git_repo(world.tmp_path / "other repo")

    result = world.fork("claude", "--run-id", RUN_ID, "--root", str(other))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "different repository checkout" in result.stderr
    assert not world.capture.exists()


def test_session_and_run_id_shapes_are_not_confused(world: _World) -> None:
    world.seed_run()

    as_session = world.fork("claude", "--session", RUN_ID)
    assert as_session.returncode == 2
    assert "control-plane run id, not a provider session" in as_session.stderr
    assert f"--run-id {RUN_ID}" in as_session.stderr
    assert not world.capture.exists()

    as_run = world.fork("claude", "--run-id", PROVIDER_SESSION)
    assert as_run.returncode == 2
    assert "provider_session_not_run_id" in as_run.stderr
    assert f"--session {PROVIDER_SESSION}" in as_run.stderr

    runtime = world.fork("claude", "--session", RUNTIME_SESSION)
    assert runtime.returncode == 2
    assert "vibecrafted_session_not_provider_session" in runtime.stderr
    assert PROVIDER_SESSION in runtime.stderr

    both = world.fork("claude", "--session", PROVIDER_SESSION, "--run-id", RUN_ID)
    assert both.returncode == 2
    assert "one identity" in both.stderr

    neither = world.fork("claude")
    assert neither.returncode == 2
    assert "--session" in neither.stderr and "--run-id" in neither.stderr


def test_run_recorded_for_another_agent_or_without_session_is_refused(
    world: _World,
) -> None:
    world.seed_run(agent="claude")
    mismatch = world.fork("codex", "--run-id", RUN_ID)
    assert mismatch.returncode == 2
    assert "agent_mismatch" in mismatch.stderr
    assert "recorded=claude requested=codex" in mismatch.stderr

    world.seed_run(agent="grok", session=None)
    bare = world.fork("grok", "--run-id", RUN_ID)
    assert bare.returncode == 2
    assert "no_provider_session" in bare.stderr
    assert "never replays the prompt as a fork" in bare.stderr
    assert not world.capture.exists()

    missing = world.fork("claude", "--run-id", "work-260908-999999-00000")
    assert missing.returncode == 2
    assert "run_not_found" in missing.stderr


@pytest.mark.parametrize("agent", ["cursor", "agy", "junie"])
def test_providers_without_a_fork_surface_are_refused_with_evidence(
    world: _World, agent: str
) -> None:
    result = world.fork(agent, "--session", PROVIDER_SESSION)

    assert result.returncode == 2
    assert "native_fork_unsupported" in result.stderr
    assert "no verified native fork adapter" in result.stderr or (
        "no native fork flag" in result.stderr
    )
    assert f"vibecrafted resume {agent} --session <id>" in result.stderr
    assert "a resume, not a fork" in result.stderr
    assert not world.capture.exists()


def test_headless_is_refused_per_provider_without_pretending(world: _World) -> None:
    codex = world.fork("codex", "--session", PROVIDER_SESSION, "--runtime", "headless")
    assert codex.returncode == 2
    assert "Bare fork requires visible or terminal runtime." in codex.stderr
    assert not world.capture.exists()

    claude = world.fork(
        "claude", "--session", PROVIDER_SESSION, "--runtime", "headless"
    )
    assert claude.returncode == 2
    assert "Bare fork requires visible or terminal runtime." in claude.stderr
    assert not world.capture.exists()


def test_repo_root_conflict_invalid_path_and_worktree_flag(world: _World) -> None:
    conflict = world.fork(
        "claude",
        "--session",
        PROVIDER_SESSION,
        "--repo",
        str(world.repo),
        "--root",
        str(world.tmp_path),
    )
    assert conflict.returncode == 2
    assert "conflicting --repo" in conflict.stderr

    invalid = world.fork(
        "claude", "--session", PROVIDER_SESSION, "--repo", str(world.tmp_path / "nope")
    )
    assert invalid.returncode == 2
    assert "--repo is not an existing directory" in invalid.stderr

    legacy_invalid = world.fork(
        "claude", "--session", PROVIDER_SESSION, "--root", str(world.tmp_path / "nope")
    )
    assert legacy_invalid.returncode == 2
    assert "--root is not an existing directory" in legacy_invalid.stderr

    worktree = world.fork(
        "claude",
        "--session",
        PROVIDER_SESSION,
        "--repo",
        str(world.repo),
        "--worktree",
        "true",
    )
    assert "Unknown fork argument: --worktree" not in worktree.stderr
    assert "fork has no --worktree" not in worktree.stderr

    codex_edits = world.fork(
        "codex", "--session", PROVIDER_SESSION, "--permissions", "accept-edits"
    )
    assert codex_edits.returncode == 2
    assert "no native accept-edits policy" in codex_edits.stderr


def test_help_documents_the_real_contract(world: _World) -> None:
    result = world.fork("--help")

    assert result.returncode == 0
    assert "--run-id <work-...>" in result.stdout
    assert "--repo <path|org/name>" in result.stdout
    assert "--placement right|floating" in result.stdout
    assert "claude|codex|grok" in result.stdout
    assert "cursor, agy and junie have no verified native fork adapter" in result.stdout
    assert (
        "vibecrafted fork claude --run-id work-260908-194325-30219 --repo ~/Projects/app"
        in result.stdout
    )
