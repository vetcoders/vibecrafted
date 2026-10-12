"""Launcher operational policy: one packaged source, provider-true transports.

Every expectation below is derived from the packaged resource itself (bytes,
digest, body) and from the argv/prompt files the real launch owners produce.
No test keeps its own copy of the policy text. Composition is proven here;
whether a provider model actually read the policy is native acceptance and is
deliberately out of reach of these tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from vibecrafted_core import operational_policy as op
from vibecrafted_core import spawn, workflow, workflow_runtime
from vibecrafted_core.execution_controls import PERMISSION_POLICIES
from vibecrafted_core.package_resources import package_root

PACKAGE = package_root()
REPO_ROOT = PACKAGE.parents[1]
COMMON_SH = PACKAGE / "runtime" / "scripts" / "common.sh"
NATIVE_PROVIDERS = {"claude": "--append-system-prompt", "grok": "--rules"}
TURN_PROVIDERS = ("codex", "agy", "cursor", "copilot")


def _body() -> str:
    return op.policy_text()


def _count(haystack: str, needle: str) -> int:
    return haystack.count(needle)


# --------------------------------------------------------------------------
# One source
# --------------------------------------------------------------------------


def test_policy_is_one_packaged_resource_with_stable_digest() -> None:
    path = op.policy_path()
    assert path == PACKAGE.joinpath(*op.POLICY_RESOURCE)
    assert path.is_file()
    assert op.policy_digest() == hashlib.sha256(path.read_bytes()).hexdigest()
    text = path.read_text(encoding="utf-8")
    assert "Operator prompt:" not in text  # workflow wrapper delimiter
    assert len(path.read_bytes()) < 6000, "policy must stay concise"
    # Public, packaged text: no private host paths or account names.
    assert not re.search(r"/Users/|/home/[a-z]|polyversai|\.scripts/", text)


def test_no_source_or_shell_file_carries_a_second_copy_of_the_policy() -> None:
    probes = [
        line.strip()
        for line in _body().splitlines()
        if len(line.strip()) > 60 and not line.startswith("#")
    ][:6]
    assert probes
    offenders: list[str] = []
    for candidate in PACKAGE.rglob("*"):
        if not candidate.is_file() or candidate == op.policy_path():
            continue
        if candidate.suffix not in {".py", ".sh", ".md", ".json", ".toml", ""}:
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if any(probe in text for probe in probes):
            offenders.append(str(candidate.relative_to(PACKAGE)))
    assert offenders == []


def test_policy_teaches_only_commands_the_installed_cli_exposes(capsys) -> None:
    text = " ".join(_body().split())
    for topic in (
        "loct",
        "aicx search",
        "aicx intents",
        ".vc-build-lease",
        "flock",
        "Agent-Operator",
        "explicit override",
        "integration",
    ):
        assert topic in text, topic
    # No future quota/capacity API is advertised as working.
    assert "no command yet" in text
    deck = (PACKAGE / "deck" / "vibecrafted").read_text(encoding="utf-8")
    verbs = set(re.findall(r"`vibecrafted ([a-z][a-z-]*)", text))
    assert verbs >= {"usage", "doctor", "message"}
    for verb in verbs:
        assert re.search(rf"(^|[|( ]){re.escape(verb)}([|)])", deck, re.MULTILINE), verb
    from vibecrafted_core import cli

    assert cli._usage_main(["--help"]) == 0
    usage_help = capsys.readouterr().out
    for flag in ("--run-id", "--since", "--json"):
        assert flag in usage_help
    assert "--json" in (PACKAGE / "cli.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Transport matrix
# --------------------------------------------------------------------------


def test_matrix_covers_all_eight_providers_and_every_launch_lane() -> None:
    rows = op.delivery_matrix(env={})
    assert {row["provider"] for row in rows} == set(spawn.POLICY_PROVIDERS)
    assert set(op.POLICY_PROVIDERS) == set(spawn.POLICY_PROVIDERS)
    expected = sum(len(c) for c in op.LANE_CONTINUATIONS.values()) * 8
    assert len(rows) == expected
    for row in rows:
        assert row["digest"] == op.policy_digest()
        assert row["delivery"] in {op.NATIVE, op.CURRENT_TURN, op.UNSUPPORTED}
        assert row["reason"]
        native = row["delivery"] == op.NATIVE
        assert native == (
            row["provider"] in op.NATIVE_TRANSPORTS
            and row["continuation"] == op.FRESH
            and row["lane"] in op.NATIVE_ARGV_LANES
        ), row
        if row["continuation"] == op.IDLE_FORK or (
            row["provider"],
            row["lane"],
        ) == ("kimi", op.INTERACTIVE):
            assert row["delivery"] == op.UNSUPPORTED, row


def test_native_option_shapes_are_additive_only() -> None:
    text = _body()
    claude = op.plan_delivery("claude", lane=op.HEADLESS, env={})
    grok = op.plan_delivery("grok", lane=op.HEADLESS, env={})
    junie = op.plan_delivery("junie", lane=op.HEADLESS, env={})
    assert claude.native_args() == ["--append-system-prompt", text]
    assert grok.native_args() == ["--rules", text]
    assert junie.native_args() == [f"--system-prompt={text}"]
    for provider, option in (
        ("claude", "--system-prompt"),
        ("claude", "--system-prompt-file"),
        ("grok", "--system-prompt-override"),
        ("kimi", "--agent-file"),
    ):
        delivery = op.plan_delivery(provider, lane=op.HEADLESS, env={})
        with pytest.raises(ValueError, match="replaces the provider's default"):
            op.with_native_args([provider, f"{option}=x"], delivery)


def test_compatibility_guard_forces_current_turn() -> None:
    delivery = op.plan_delivery("claude", lane=op.INTERACTIVE, env={op.NATIVE_ENV: "0"})
    assert delivery.mode == op.CURRENT_TURN
    assert delivery.native_args() == []
    assert _body() in delivery.turn_block()


# --------------------------------------------------------------------------
# Interactive owner: launch_interactive_workspace -> real provider argv
# --------------------------------------------------------------------------

_FAKE_PROVIDER = """#!{python}
import json, os, sys
with open(os.environ["POLICY_CAPTURE"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"argv": sys.argv}}) + "\\n")
"""


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ("init", "-q", "-b", "trunk"),
        ("config", "user.name", "Fixture"),
        ("config", "user.email", "fixture@example.invalid"),
        ("commit", "-q", "--allow-empty", "-m", "base"),
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True)
    return path


@pytest.fixture
def provider_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    fake = tmp_path / "fake-bin"
    fake.mkdir()
    for name in (*spawn.POLICY_PROVIDERS, "cursor-agent"):
        path = fake / name
        path.write_text(_FAKE_PROVIDER.format(python=sys.executable))
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    capture = tmp_path / "provider-argv.jsonl"
    monkeypatch.setenv("POLICY_CAPTURE", str(capture))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_BIN", str(fake))
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(op.NATIVE_ENV, raising=False)
    monkeypatch.setattr(
        spawn,
        "resolve_provider_usage_capability",
        lambda provider, **_: spawn.ProviderUsageCapability(
            provider, False, reason="fixture: unmetered"
        ),
    )
    # Cursor validates its permission flags against the real binary's --help;
    # that probe is a separate contract (test_cursor_capability_repair).
    monkeypatch.setattr(
        spawn,
        "_materialize_cursor_permission_flags",
        lambda flags, **_: tuple(flags),
    )
    return capture


def _permission(provider: str) -> str:
    for permission in PERMISSION_POLICIES:
        if spawn.resolve_provider_policy(
            provider, "local-native", permission, "interactive"
        ).supported:
            return permission
    raise AssertionError(f"no interactive permission for {provider}")


def _interactive(
    tmp_path: Path,
    capture: Path,
    provider: str,
    *,
    run_id: str,
    admission: dict[str, Any] | None = None,
    continuity: str = "fresh",
    parent_session_id: str = "",
    expected_status: int = 0,
) -> tuple[list[str], str, dict[str, Any]]:
    repo = tmp_path / f"repo-{run_id}"
    if not repo.exists():
        _git_repo(repo)
    capture.write_text("")
    status = spawn.launch_interactive_workspace(
        provider,
        f"PRIVATE-TASK-{run_id}",
        "local-native",
        _permission(provider),
        repo,
        token_budget="unmetered",
        continuity=continuity,
        parent_session_id=parent_session_id,
        admission={"run_id": run_id, "skill": "init", **(admission or {})},
    )
    assert status == expected_status
    run_dir = spawn.control_plane_home() / "runtime_runs" / run_id
    calls = [json.loads(line) for line in capture.read_text().splitlines()]
    assert len(calls) == 1, calls
    prompt = (run_dir / "prompt.md").read_text(encoding="utf-8")
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    return calls[0]["argv"], prompt, meta


@pytest.mark.parametrize("provider", ["claude", "grok", "junie"])
def test_interactive_fresh_native_provider_gets_policy_once_in_system_lane(
    tmp_path: Path, provider_bin: Path, provider: str
) -> None:
    run_id = f"init-20261012-100000-{provider[:4]}"
    argv, prompt, meta = _interactive(tmp_path, provider_bin, provider, run_id=run_id)
    body = _body()
    joined = "\n".join(argv)
    if provider == "junie":
        assert argv[1] == f"--system-prompt={body}"
    else:
        option = NATIVE_PROVIDERS[provider]
        assert argv[1:3] == [option, body]
    assert _count(joined, body) == 1
    # Private task stays private: only the task-file pointer is on argv.
    assert f"PRIVATE-TASK-{run_id}" not in joined
    assert f"PRIVATE-TASK-{run_id}" in prompt
    # The private task carries the digest reference, not a second copy.
    assert body not in prompt
    assert f"sha256:{op.policy_digest()[:16]}" in prompt
    receipt = meta["operational_policy"]
    assert receipt["delivery"] == op.NATIVE
    assert receipt["digest"] == op.policy_digest()
    assert receipt["continuation"] == op.FRESH


@pytest.mark.parametrize("provider", TURN_PROVIDERS)
def test_interactive_fresh_turn_provider_gets_policy_once_in_private_task(
    tmp_path: Path, provider_bin: Path, provider: str
) -> None:
    run_id = f"init-20261012-110000-{provider[:4]}"
    argv, prompt, meta = _interactive(tmp_path, provider_bin, provider, run_id=run_id)
    body = _body()
    joined = "\n".join(argv)
    assert body not in joined
    assert not any(
        token.startswith(("--append-system-prompt", "--rules", "--system-prompt"))
        for token in argv
    )
    assert f"PRIVATE-TASK-{run_id}" not in joined
    assert _count(prompt, body) == 1
    assert meta["operational_policy"]["delivery"] == op.CURRENT_TURN


def test_interactive_kimi_reports_unsupported_and_says_so_visibly(
    tmp_path: Path, provider_bin: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    run_id = "init-20261012-120000-kimi"
    argv, prompt, meta = _interactive(tmp_path, provider_bin, "kimi", run_id=run_id)
    assert _body() not in "\n".join(argv)
    assert not any(token.startswith("--agent") for token in argv)
    receipt = meta["operational_policy"]
    assert receipt["delivery"] == op.UNSUPPORTED
    assert "kimi interactive" in receipt["reason"]
    assert "operational policy NOT delivered to kimi" in capfd.readouterr().err
    # The private task file still holds the policy for a later explicit read.
    assert _count(prompt, _body()) == 1


def test_interactive_claude_resume_never_relies_on_snapshot_flag(
    tmp_path: Path, provider_bin: Path
) -> None:
    """Negative control: Claude replays the recorded system prompt on resume.

    The fresh lane uses --append-system-prompt; the resumed conversation must
    carry the policy in the current turn instead, with the exact native id.
    """
    fresh_argv, _, _ = _interactive(
        tmp_path, provider_bin, "claude", run_id="init-20261012-130000-clfr"
    )
    assert "--append-system-prompt" in fresh_argv
    native = "22222222-2222-4222-8222-222222222222"
    argv, prompt, meta = _interactive(
        tmp_path,
        provider_bin,
        "claude",
        run_id="init-20261012-130001-clrs",
        admission={"agent_session_id": native},
    )
    assert argv[argv.index("--resume") + 1] == native
    assert "--session-id" not in argv
    assert "--append-system-prompt" not in argv
    assert _count(prompt, _body()) == 1
    assert "supersedes any earlier copy" in prompt
    receipt = meta["operational_policy"]
    assert receipt["delivery"] == op.CURRENT_TURN
    assert receipt["continuation"] == op.RESUME
    assert "replays it" in receipt["reason"]


def test_interactive_idle_bare_fork_is_explicitly_unsupported(
    tmp_path: Path,
    provider_bin: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from vibecrafted_core.continuity import capabilities

    monkeypatch.setattr(
        capabilities,
        "probe",
        lambda *_a, **_k: SimpleNamespace(
            state=capabilities.PROBE_CONFIRMED, detail="confirmed"
        ),
    )
    argv, _, meta = _interactive(
        tmp_path,
        provider_bin,
        "claude",
        run_id="fork-20261012-140000-clbf",
        admission={"skill": "fork"},
        continuity="bare-fork",
        parent_session_id="parent-session-42",
        # The fixture provider writes no forked transcript, so the launcher
        # honestly refuses to invent the child identity after exit.
        expected_status=1,
    )
    assert meta["terminal_reason"] == "native_fork_identity_unconfirmed"
    assert argv[argv.index("--resume") + 1] == "parent-session-42"
    assert "--fork-session" in argv
    assert "--append-system-prompt" not in argv
    assert not any("private task file" in token for token in argv)
    receipt = meta["operational_policy"]
    assert receipt["delivery"] == op.UNSUPPORTED
    assert receipt["continuation"] == op.IDLE_FORK
    assert "NOT delivered to claude" in capfd.readouterr().err


def test_interactive_policy_block_follows_orientation_and_precedes_task() -> None:
    delivery = op.plan_delivery("codex", lane=op.INTERACTIVE, env={})
    prompt = spawn.compose_interactive_task_prompt(
        skill="init",
        source="PRIVATE-EXTRA",
        root="/repo",
        resume_block="",
        admission={"run_id": "init-x", "agent": "codex"},
        policy_delivery=delivery,
    )
    assert prompt.startswith("You are in an interactive Vibecrafted session.")
    assert prompt.index("Skill and launcher resolution:") < prompt.index(_body())
    assert prompt.index(_body()) < prompt.index("PRIVATE-EXTRA")
    assert _count(prompt, _body()) == 1


# --------------------------------------------------------------------------
# Headless owner: launch_workflow -> worker argv + private prompt file
# --------------------------------------------------------------------------


class _Dispatcher:
    pid = 4242

    def wait(self) -> int:
        return 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def headless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("VIBECRAFTED_GUARD", "0")
    monkeypatch.delenv(op.NATIVE_ENV, raising=False)
    monkeypatch.setattr(workflow, "_sweep_stale_runs", lambda: None)
    monkeypatch.setattr(
        workflow, "_resolve_agent_command", lambda _agent, command, _env: command
    )
    monkeypatch.setattr(
        spawn,
        "_materialize_cursor_permission_flags",
        lambda flags, **_: tuple(flags),
    )
    real_popen = workflow.subprocess.Popen

    def dispatcher(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("start_new_session"):
            return _Dispatcher()
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(workflow.subprocess, "Popen", dispatcher)
    source = tmp_path / "src"
    source.mkdir()
    return source


def _launch(
    source: Path, agent: str, task: str, **kwargs: Any
) -> tuple[list[str], str, dict[str, Any]]:
    spec = workflow.normalize_launch_spec(
        {"skill": "workflow", "agent": agent, "prompt": task}, source
    )
    payload = workflow.launch_workflow(spec, source, **kwargs)
    assert payload["accepted"] is True, payload
    prompt = Path(payload["prompt_file"]).read_text(encoding="utf-8")
    meta = json.loads(Path(payload["meta"]).read_text(encoding="utf-8"))
    return list(payload["worker_command"]), prompt, meta


@pytest.mark.parametrize("agent", sorted(spawn.POLICY_PROVIDERS))
def test_headless_fresh_matrix_for_all_eight_providers(
    headless: Path, agent: str
) -> None:
    task = f"HEADLESS-TASK-{agent}"
    argv, prompt, meta = _launch(headless, agent, task)
    body = _body()
    joined = "\n".join(argv)
    receipt = meta["operational_policy"]
    assert receipt["digest"] == op.policy_digest()
    if agent in {"claude", "grok", "junie"}:
        assert receipt["delivery"] == op.NATIVE
        assert _count(joined, body) == 1
        assert body not in prompt
        assert f"sha256:{op.policy_digest()[:16]}" in prompt
    else:
        assert receipt["delivery"] == op.CURRENT_TURN
        assert _count(prompt, body) == 1
    if agent == "kimi":
        # Kimi print mode has no stdin lane: the whole private prompt rides
        # argv (existing, documented ARG_MAX/ps debt). The policy travels with
        # it exactly once; nothing else is added.
        assert _count(joined, body) == 1
        assert task in joined
    elif agent not in {"claude", "grok", "junie"}:
        assert body not in joined
    if agent != "kimi":
        assert task not in joined
    # Ordering: contract header, then policy, then the operator task last.
    assert prompt.startswith("You are running under Vibecrafted core runtime.\n")
    marker = f"sha256:{op.policy_digest()[:16]}"
    assert prompt.index(marker) < prompt.index("Operator prompt:\n")
    assert prompt.rstrip().endswith(task)


def test_headless_wrapper_keeps_legacy_source_recovery(headless: Path) -> None:
    _, prompt, _ = _launch(headless, "codex", "RECOVER-ME")
    _wrapper, sep, source = prompt.partition("Operator prompt:\n")
    assert sep and source == "RECOVER-ME\n"


@pytest.mark.parametrize(
    "agent,meta_key,continuation,command",
    [
        (
            "claude",
            "native_resume",
            op.RESUME,
            ["claude", "--resume", "native-1", "-p", "--output-format", "stream-json"],
        ),
        (
            "grok",
            "native_resume",
            op.RESUME,
            ["grok", "--resume", "native-1", "--prompt-file", "/dev/stdin"],
        ),
        (
            "claude",
            "native_fork",
            op.FORK,
            ["claude", "-p", "--resume", "native-1", "--fork-session"],
        ),
        ("codex", "native_fork", op.FORK, ["codex", "exec", "fork", "native-1", "-"]),
    ],
)
def test_headless_continuations_carry_policy_in_current_turn(
    headless: Path,
    agent: str,
    meta_key: str,
    continuation: str,
    command: list[str],
) -> None:
    argv, prompt, meta = _launch(
        headless,
        agent,
        "CONTINUE-TASK",
        worker_command_override=command,
        launch_meta={meta_key: True},
    )
    # The native continuation argv is preserved verbatim; no system flag added.
    assert argv == command
    assert _count(prompt, _body()) == 1
    receipt = meta["operational_policy"]
    assert receipt["delivery"] == op.CURRENT_TURN
    assert receipt["continuation"] == continuation


def test_headless_resume_and_fork_owners_mark_their_continuation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The real resume/fork owners pass the meta keys launch_workflow reads."""
    launches: list[dict[str, Any]] = []

    def capture(spec: Any, source_dir: Any, **kwargs: Any) -> dict[str, Any]:
        launches.append(kwargs)
        return {"accepted": True, "run_id": spec.run_id, "agent": spec.agent}

    monkeypatch.setattr(workflow, "launch_workflow", capture)
    monkeypatch.setattr(
        workflow,
        "_verified_native_resume_command",
        lambda agent, session: ([agent, "--resume", session, "-p"], "confirmed", "1"),
    )
    monkeypatch.setattr(workflow, "reserve_run_id", lambda code: f"{code}-fixture")
    monkeypatch.setattr(workflow, "ensure_session_id", lambda *_a: "runtime-fixture")
    repo = _git_repo(tmp_path / "repo")
    workflow.manual_resume_session(
        "claude", "native-1", tmp_path, prompt="go on", root=repo
    )
    assert launches[-1]["launch_meta"]["native_resume"] is True
    monkeypatch.setattr(
        workflow,
        "resolve_session_selection",
        lambda agent, session, root, selection=None: {
            "accepted": True,
            "agent_session_id": session,
        },
    )
    workflow.manual_fork_session(
        "claude", "native-1", tmp_path, prompt="branch", root=repo
    )
    assert launches[-1]["launch_meta"]["native_fork"] is True


# --------------------------------------------------------------------------
# Supervised children (research / marbles / synthesis)
# --------------------------------------------------------------------------


class _AtSpawn(RuntimeError):
    pass


def _child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    agent: str,
    **kwargs: Any,
) -> tuple[list[str], str, dict[str, Any]]:
    monkeypatch.setenv("VIBECRAFTED_REPORT_PATH", str(tmp_path / "parent.md"))
    monkeypatch.setenv("VIBECRAFTED_RUN_ID", "parent-policy")
    monkeypatch.delenv(op.NATIVE_ENV, raising=False)
    monkeypatch.setattr(
        workflow_runtime, "_resolve_agent_command", lambda _a, command, _e: command
    )
    monkeypatch.setattr(
        spawn,
        "_materialize_cursor_permission_flags",
        lambda flags, **_: tuple(flags),
    )
    observed: dict[str, Any] = {}

    class Supervisor:
        async def run(self, **run_kwargs: Any) -> Any:
            observed.update(run_kwargs)
            raise _AtSpawn

    monkeypatch.setattr(workflow_runtime, "AsyncSupervisor", Supervisor)
    with pytest.raises(_AtSpawn):
        asyncio.run(
            workflow_runtime._run_child(
                kind="marbles",
                label="iteration-1",
                agent=agent,
                root=str(tmp_path),
                prompt="CHILD-TASK",
                **kwargs,
            )
        )
    prompt = Path(observed["prompt_file_path"]).read_text(encoding="utf-8")
    meta = json.loads(Path(observed["meta_path"]).read_text(encoding="utf-8"))
    return list(observed["command"]), prompt, meta


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_supervised_child_receives_policy_before_operator_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: str
) -> None:
    argv, prompt, meta = _child(monkeypatch, tmp_path, agent)
    body = _body()
    if agent == "claude":
        assert argv[1:3] == ["--append-system-prompt", body]
        assert body not in prompt
        anchor = f"sha256:{op.policy_digest()[:16]}"
    else:
        assert body not in "\n".join(argv)
        assert _count(prompt, body) == 1
        anchor = body
    assert prompt.index(anchor) < prompt.index("Operator prompt:\n")
    assert meta["operational_policy"]["lane"] == op.SUPERVISED_CHILD


def test_synthesis_native_resume_child_uses_current_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    command = workflow_runtime.native_resume_argv("claude", "lane-session")
    argv, prompt, meta = _child(
        monkeypatch,
        tmp_path,
        "claude",
        command=command,
        continuation=op.RESUME,
    )
    assert "--append-system-prompt" not in argv
    assert argv[argv.index("--resume") + 1] == "lane-session"
    assert _count(prompt, _body()) == 1
    assert meta["operational_policy"]["continuation"] == op.RESUME


# --------------------------------------------------------------------------
# Legacy shell spawners (runtime/scripts/*_spawn.sh via lib/prompt.sh)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("research", [False, True])
@pytest.mark.parametrize("agent", ["claude", "kimi"])
def test_legacy_shell_prompt_renders_policy_from_the_same_source(
    tmp_path: Path, agent: str, research: bool
) -> None:
    source = tmp_path / "plan.md"
    source.write_text("---\nagent: x\n---\nSHELL-TASK\n", encoding="utf-8")
    runtime = tmp_path / "runtime.md"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("VIBECRAFTED_", "SPAWN_", "PYTHON"))
    }
    env.update(
        {
            "HOME": str(tmp_path),
            "VIBECRAFTED_PYTHON": sys.executable,
            "SPAWN_RUN_ID": "legacy-run",
            "SPAWN_PROMPT_ID": "legacy-prompt",
            "SPAWN_SKILL_NAME": "research" if research else "implement",
        }
    )
    subprocess.run(
        [
            "bash",
            "-c",
            (
                'set -euo pipefail; source "$1"; '
                'spawn_build_runtime_prompt "$2" "$3" "$4" "$5"'
            ),
            "policy-test",
            str(COMMON_SH),
            str(source),
            str(runtime),
            str(tmp_path / "report.md"),
            agent,
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    payload = runtime.read_text(encoding="utf-8")
    assert _count(payload, _body()) == 1
    assert f"sha256:{op.policy_digest()[:16]}" in payload
    assert payload.index(_body()) < payload.index("SHELL-TASK")
