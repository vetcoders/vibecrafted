"""``vibecrafted fork <agent> --session | --run-id`` through the real deck.

The deck is run as a subprocess with a fake vc-frame (developer mode), fake
provider binaries that answer the capability probe (``--version``/``--help``
with the markers each recipe declares) and record argv and stdin, an isolated
VIBECRAFTED_HOME with a seeded run record, and no inherited
PYTHONPATH/PYTHONHOME or parent session context. A bare fork's pane carries
the admitted handoff (36614036), read back with its private admission; a fork
with input is a tracked headless task fork (591b6dde), read back from the
provider the worker reached — never a real provider session.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from vibecrafted_core.spawn import (
    ContinuityPolicy,
    interactive_policy_command,
    resolve_provider_policy,
)

# --import-mode=importlib: the shared fixture module is loaded by file.
_FIXTURES_SPEC = importlib.util.spec_from_file_location(
    "declaration_fixtures", Path(__file__).with_name("_declaration_fixtures.py")
)
assert _FIXTURES_SPEC is not None and _FIXTURES_SPEC.loader is not None
_fixtures = importlib.util.module_from_spec(_FIXTURES_SPEC)
_FIXTURES_SPEC.loader.exec_module(_fixtures)

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
        # Fork admission probes each provider's `--help` for the continuity
        # markers its recipe declares (continuity/capabilities.py `probe`,
        # 591b6dde/4a09425a); a silent `exit 0` binary is "unsupported".
        # The fakes answer the probes, record argv and stdin, and win on PATH.
        _fixtures.write_provider_fakes(
            self.bin,
            ("claude", "codex", "grok", "cursor-agent", "agy", "junie"),
            body=(
                'printf "%s\\n" "$@" > "$FORK_TEST_ARGV.$(basename "$0")"\n'
                'cat > "$FORK_TEST_ARGV.$(basename "$0").stdin"\n'
                "exit 0\n"
            ),
        )
        self.provider_argv_prefix = tmp_path / "provider-argv"

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
            if key not in {"PYTHONPATH", "PYTHONHOME", *_fixtures.PARENT_CONTEXT_ENV}
        }
        env["FORK_TEST_ARGV"] = str(self.provider_argv_prefix)
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

    def task_fork_run(self, *, timeout: float = 30.0) -> dict:
        """The one tracked task-fork run, once its worker has settled.

        The provider is a fake that exits at once; waiting keeps the detached
        worker inside the test that launched it.
        """
        runs = self.vibecrafted_home / "control_plane" / "runtime_runs"
        deadline = time.monotonic() + timeout
        meta: dict = {}
        while time.monotonic() < deadline:
            found = sorted(runs.glob("fork-*/meta.json"))
            if len(found) == 1:
                try:
                    meta = json.loads(found[0].read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
                if meta.get("exit_code") is not None or meta.get("status") in {
                    "completed",
                    "failed",
                    "partial_success",
                }:
                    return meta
            time.sleep(0.1)
        pytest.fail(f"task fork run did not settle: {meta}")

    def provider_argv(self, binary: str) -> list[str]:
        return Path(f"{self.provider_argv_prefix}.{binary}").read_text().splitlines()

    def provider_stdin(self, binary: str) -> str:
        return Path(f"{self.provider_argv_prefix}.{binary}.stdin").read_text()

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


def _interactive_fork_argv(inner: list[str]) -> list[str]:
    """The provider argv the admitted bare fork resolves to for claude/grok.

    The pane carries only the admitted handoff (36614036); interactive-launch
    builds the provider argv from it through the one canonical owner,
    ``spawn.interactive_policy_command`` (launch_interactive_workspace), with
    the continuity parent the handoff names. Executable resolution, the
    requested child ``--session-id`` and the model override are applied on top
    of this by the same owner and proven in core
    (test_provider_policy / test_session_selection_fork).
    """
    parent = _fixtures.flag(inner, "--parent-session")
    return interactive_policy_command(
        inner[1],
        "<private task file>",
        _fixtures.flag(inner, "--runtime"),
        _fixtures.flag(inner, "--permissions"),
        continuity_policy=ContinuityPolicy(
            mode="bare-fork", lineage_id=parent, parent_provider_session_id=parent
        ),
    )


def _assert_bare_fork(
    inner: list[str], admission: dict, agent: str, source: str
) -> None:
    """A native fork of ``source``, never a resume of it: bare-fork continuity
    with the source as parent, and no identity seeded into the child."""
    assert inner[:2] == ["interactive-launch", agent], inner
    assert (admission["agent"], admission["skill"]) == (agent, "fork"), admission
    assert _fixtures.flag(inner, "--continuity") == "bare-fork", inner
    assert _fixtures.flag(inner, "--parent-session") == source, inner
    assert admission["agent_session_id"] == "", admission
    assert admission["session_selection"]["agent_session_id"] == source, admission


def test_claude_fork_by_session_from_outside_git_with_repo(world: _World) -> None:
    """With a prompt this is a tracked NONINTERACTIVE task fork (591b6dde;
    UNIFIED_LAUNCH_CONTRACT "Bare fork remains interactive. --prompt, --file or
    --prompt-stdin selects a tracked noninteractive fork"), so no pane opens.
    What the deck owes it is unchanged: the explicit repository from outside
    Git, the native `--resume <id> --fork-session`, the model and permission
    mapping, the prompt delivered -- now on stdin, never argv -- and no
    renamed or resumed source session.

    The prompt below is what makes this the task fork: merge 2c06fbfe kept
    88e0330d's task-fork assertions but took 17b4b033's bare argv, so the
    fork opened a pane and no task run ever settled."""
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
        "-p",
        "try the other approach",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    run = world.task_fork_run()
    assert not world.capture.exists(), "a task fork opened a pane"
    argv = world.provider_argv("claude")
    command = " ".join(argv)
    assert f"--resume {OTHER_SESSION} --fork-session" in command, argv
    assert "--model claude-fable-5-1" in command, argv
    assert "--permission-mode acceptEdits" in command, argv
    assert "try the other approach" not in command, argv
    assert "try the other approach" in world.provider_stdin("claude")
    # Source preservation: never a plain resume, never a renamed session.
    assert "--session-id" not in argv, argv
    assert run["native_fork"] is True, run
    assert run["fork_source_session_id"] == OTHER_SESSION, run
    assert Path(run["root"]).resolve() == world.repo.resolve(), run


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
    inner, admission = _fixtures.script_handoff(world.command())
    _assert_bare_fork(inner, admission, "grok", OTHER_SESSION)
    assert Path(admission["root"]) == world.repo.resolve(), admission
    argv = _interactive_fork_argv(inner)
    command = " ".join(argv)
    assert f"--resume {OTHER_SESSION} --fork-session" in command, argv
    assert "--cwd" in argv and "--no-alt-screen" in argv, argv
    assert "--permission-mode plan" in command, argv
    assert "--restore-code" not in argv and "--restore-code" not in inner
    assert "--worktree" not in argv and "--worktree" not in inner
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
    """The interactive Codex fork is native since 36614036/591b6dde: the pane
    carries the admitted bare fork, and interactive-launch forks through the
    selected Codex's app-server `thread/fork` before opening the acknowledged
    child (UNIFIED_LAUNCH_CONTRACT "Correlated interactive Codex fork"; core
    test_session_selection_fork). Proven here: the inline `--repo=` binding,
    the source as fork parent (never a resume target), model and the `auto`
    permission mapping the child argv takes its flags from."""
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
    inner, admission = _fixtures.script_handoff(world.command())
    _assert_bare_fork(inner, admission, "codex", OTHER_SESSION)
    assert Path(_fixtures.flag(inner, "--root")) == world.repo.resolve(), inner
    assert "repo with space" in _fixtures.flag(inner, "--root"), inner
    pane = world.pane()
    assert pane[pane.index("--cwd") + 1] == str(world.repo.resolve())
    assert admission["model_requested"] == "gpt-test", admission
    assert _fixtures.flag(inner, "--permissions") == "auto", inner
    flags = resolve_provider_policy(
        "codex", _fixtures.flag(inner, "--runtime"), "auto", "interactive"
    ).flags
    assert " ".join(flags) == "--ask-for-approval on-request --sandbox workspace-write"


def test_run_id_resolves_the_recorded_provider_session_and_its_repository(
    world: _World,
) -> None:
    world.seed_run()

    result = world.fork("claude", "--run-id", RUN_ID)

    assert result.returncode == 0, result.stderr
    inner, admission = _fixtures.script_handoff(world.command())
    _assert_bare_fork(inner, admission, "claude", PROVIDER_SESSION)
    command = " ".join(_interactive_fork_argv(inner))
    assert f"--resume {PROVIDER_SESSION} --fork-session" in command
    selection = admission["session_selection"]
    assert (selection["session_selector"], selection["source_run_id"]) == (
        "run-id",
        RUN_ID,
    ), selection
    # 4a09425a: the receipt names the SOURCE; the child's identity is pending.
    assert f"source-session: {PROVIDER_SESSION}" in result.stdout
    assert f"source:    {RUN_ID}" in result.stdout
    pane = world.pane()
    assert pane[pane.index("--cwd") + 1] == str(world.repo.resolve())
    assert str(world.outside.resolve()) not in pane


def test_explicit_repo_must_match_the_recorded_run_root(world: _World) -> None:
    """Formerly `..._outranks_the_recorded_run_root`. 591b6dde made selection
    checkout-scoped (UNIFIED_LAUNCH_CONTRACT "Shared session selection": "A
    recorded source must match the selected checkout";
    workflow.resolve_session_selection): an explicit repository that is not
    the run's recorded checkout is refused, and nothing opens. The explicit
    repository is still honoured when it is that checkout."""
    world.seed_run()
    other = _git_repo(world.tmp_path / "other repo")

    refused = world.fork("claude", "--run-id", RUN_ID, "--root", str(other))

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "session_selection_failed" in refused.stderr
    assert "belongs to a different repository checkout" in refused.stderr
    assert not world.capture.exists()
    assert _fixtures.admissions(world.vibecrafted_home) == []

    same = world.fork("claude", "--run-id", RUN_ID, "--root", str(world.repo))

    assert same.returncode == 0, same.stderr
    pane = world.pane()
    assert pane[pane.index("--cwd") + 1] == str(world.repo.resolve())


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


# 591b6dde reworded the capability evidence: the refusal describes the
# adapter limit the installed help shows, not a claim that the product can
# never fork (UNIFIED_LAUNCH_CONTRACT: "not exhaustive proof").
ADAPTER_LIMIT = {
    "cursor": "no verified native fork adapter",
    "agy": "no native fork flag on agy",
    "junie": "no verified native fork adapter",
}


@pytest.mark.parametrize("agent", ["cursor", "agy", "junie"])
def test_providers_without_a_fork_surface_are_refused_with_evidence(
    world: _World, agent: str
) -> None:
    result = world.fork(agent, "--session", PROVIDER_SESSION)

    assert result.returncode == 2
    assert "native_fork_unsupported" in result.stderr
    assert f"detail: {ADAPTER_LIMIT[agent]}" in result.stderr, result.stderr
    assert f"vibecrafted resume {agent} --session <id>" in result.stderr
    assert "a resume, not a fork" in result.stderr
    assert not world.capture.exists()


def test_headless_is_refused_per_provider_without_pretending(world: _World) -> None:
    """591b6dde wired headless TRACKED forks (they need input), so the old
    per-provider "codex fork is an interactive TUI" / "headless tracked fork is
    not wired" refusals are gone. A bare fork with an incompatible presentation
    is refused for every provider with one message, before any lookup, and
    never offers a resume in its place; the converse mismatch is refused too."""
    for agent in ("codex", "claude", "grok"):
        bare = world.fork(agent, "--session", PROVIDER_SESSION, "--runtime", "headless")
        assert bare.returncode == 2, (agent, bare.stderr)
        assert "Bare fork requires visible or terminal runtime." in bare.stderr
        assert "resume" not in bare.stderr, bare.stderr
        assert not world.capture.exists()

    task = world.fork(
        "claude", "--session", PROVIDER_SESSION, "--runtime", "visible", "-p", "x"
    )
    assert task.returncode == 2, task.stderr
    assert "Task fork is noninteractive; use --runtime headless." in task.stderr
    assert not world.capture.exists()
    assert _fixtures.admissions(world.vibecrafted_home) == []


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

    # 36614036 made `--worktree [true|false]` a declared fork execution
    # selector (help: `--execution-runtime living-tree|local-worktrees`), so
    # "fork has no --worktree" is gone. A worktree on the living-tree bare fork
    # is still refused -- by the launch-spec owner (5b25a6cd), before any
    # admission or pane exists.
    worktree = world.fork("claude", "--session", PROVIDER_SESSION, "--worktree", "true")
    assert worktree.returncode != 0, worktree.stdout
    assert "--worktree conflicts with execution runtime" in worktree.stderr
    assert not world.capture.exists()
    assert _fixtures.admissions(world.vibecrafted_home) == []

    codex_edits = world.fork(
        "codex", "--session", PROVIDER_SESSION, "--permissions", "accept-edits"
    )
    assert codex_edits.returncode == 2
    assert "no native accept-edits policy" in codex_edits.stderr


def test_help_documents_the_real_contract(world: _World) -> None:
    result = world.fork("--help")

    assert result.returncode == 0
    assert "--run-id <work-...>" in result.stdout
    # 36614036: --repo also accepts a configured org/name identity.
    assert "--repo <path|org/name>" in result.stdout
    assert "claude|codex|grok" in result.stdout
    # 591b6dde: adapter-limit wording, and input selects the task fork.
    assert "cursor, agy and junie have no verified native fork adapter" in result.stdout
    assert "task fork with --prompt/--file" in result.stdout
    assert (
        "vibecrafted fork claude --run-id work-260908-194325-30219 --repo ~/Projects/app"
        in result.stdout
    )
