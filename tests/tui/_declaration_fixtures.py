"""Shared, hermetic fixtures for the declaration / fork / launch scenes.

Not a test module (no ``test_`` prefix): the suite runs with
``--import-mode=importlib``, so consumers load this file by path.

Three current contracts every scene here has to meet:

* a launch root is a Git work tree with one available commit
  (``repo_selection.select_repository(require_git=True)`` and
  ``resolve_repository_base`` for the default ``--base HEAD``, 36614036 /
  5b25a6cd);
* a fork is admitted only after the provider capability probe reads the
  declared markers from the installed CLI's ``--help``
  (``continuity/capabilities.py`` ``probe`` + ``resolve_continuity_policy``,
  591b6dde / 4a09425a) -- so a fake provider has to print a help surface, and
  it must win on PATH over any host CLI;
* an interactive declaration crosses a terminal/tab boundary only as the
  canonical admitted handoff: ``… -m vibecrafted_core.spawn interactive-handoff
  --command '<python> -m vibecrafted_core.spawn interactive-launch <agent> …
  --admission-file <runtime_runs/<run>/admission.json>'`` (36614036).

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

# Process context that `--session current` reads as an explicit parent
# identity (workflow.resolve_session_selection). A scene must never inherit the
# host agent's own ids; a case that wants `current` seeds exactly one.
PARENT_CONTEXT_ENV = (
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CLAUDE_CODE_SESSION_ID",
    "GROK_SESSION_ID",
    "VIBECRAFTED_AGENT",
    "VIBECRAFTED_AGENT_SESSION_ID",
    "VIBECRAFTED_RUN_ID",
)


def commit_fixture_repo(path: Path, *, env: dict[str, str] | None = None) -> Path:
    """A real repository at ``path``: its own top level, one commit on HEAD.

    Identity and signing are passed per command so neither the host's global
    Git config nor a sandboxed HOME without one decides whether it works.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    ident = [
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
    ]
    for argv in (
        ["git", "init", "-q", str(path)],
        ["git", "-C", str(path), "add", "README.md"],
        ["git", *ident, "-C", str(path), "commit", "-q", "-m", "fixture"],
    ):
        subprocess.run(argv, check=True, capture_output=True, text=True, env=env)
    return path


# Help excerpts in the installed CLIs' own shape. Each carries the literal
# markers its capability recipe declares (capabilities.CAPABILITIES[*]
# .probe_recipe.required_markers) -- written out here, not read from the
# table, so a probe that stops finding them fails these scenes.
PROVIDER_HELP: dict[str, str] = {
    "codex": (
        "Codex CLI\n\n"
        "Usage: codex [OPTIONS] [PROMPT]\n"
        "       codex [OPTIONS] <COMMAND> [ARGS]\n\n"
        "Commands:\n"
        "  exec        Run Codex non-interactively [aliases: e]\n"
        "  resume      Resume a previous interactive session\n"
        "  fork        Fork a previous interactive session\n"
    ),
    "claude": (
        "Usage: claude [options] [command] [prompt]\n\n"
        "Options:\n"
        "  -p, --print                 Print response and exit\n"
        "  -r, --resume [value]        Resume a conversation by session ID\n"
        "  --fork-session              When resuming, create a new session ID\n"
        "  --session-id <uuid>         Use a specific session ID\n"
    ),
    "grok": (
        "Usage: grok [OPTIONS] [PROMPT]\n\n"
        "Options:\n"
        "      --resume <SESSION_ID>   Resume a session\n"
        "      --fork-session          Fork the resumed session\n"
        "      --session-id <UUID>     Name the new session\n"
        "      --prompt-file <PATH>    Read the prompt from a file\n"
    ),
    "agy": "Usage: agy [options]\n  --continue\n  --conversation <id>\n  --print\n",
    "junie": "Usage: junie [options]\n  --resume\n  --session-id <id>\n",
    "cursor-agent": "Usage: cursor-agent [options]\n  --resume [chatId]\n  --print\n  --output-format <format>\n",
}

# `codex exec fork --help`: the task-fork admission checks this surface too
# (workflow native task fork: `<SESSION_ID>` and stdin transport).
CODEX_EXEC_FORK_HELP = (
    "Usage: codex exec fork [OPTIONS] <SESSION_ID> [PROMPT]\n\n"
    "Arguments:\n"
    "  <SESSION_ID>  Session to fork\n"
    "  [PROMPT]      Initial instructions; use `-` to read them from stdin\n"
)


def provider_fake(binary: str, body: str = "exit 0\n") -> str:
    """A fake provider CLI: answers the read-only probes, then runs ``body``."""
    help_text = PROVIDER_HELP.get(binary, f"Usage: {binary} [options]\n")
    lines = [
        "#!/usr/bin/env bash",
        'if [ "${1:-}" = "--version" ]; then',
        f"  echo {shlex.quote(binary + ' 0.0.0-fixture')}",
        "  exit 0",
        "fi",
        'if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then',
        f"  printf '%s' {shlex.quote(help_text)}",
        "  exit 0",
        "fi",
    ]
    if binary == "codex":
        lines += [
            'if [ "${1:-} ${2:-} ${3:-}" = "exec fork --help" ]; then',
            f"  printf '%s' {shlex.quote(CODEX_EXEC_FORK_HELP)}",
            "  exit 0",
            "fi",
        ]
    return "\n".join(lines) + "\n" + body


def write_provider_fakes(
    bin_dir: Path, binaries: tuple[str, ...], body: str = "exit 0\n"
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for binary in binaries:
        path = bin_dir / binary
        path.write_text(provider_fake(binary, body), encoding="utf-8")
        path.chmod(0o755)


def admissions(vibecrafted_home: Path) -> list[dict]:
    """Every interactive admission the control plane holds, oldest first."""
    runs = vibecrafted_home / "control_plane" / "runtime_runs"
    found = sorted(runs.glob("*/admission.json"), key=lambda p: p.stat().st_mtime)
    return [json.loads(path.read_text(encoding="utf-8")) for path in found]


def claimed(vibecrafted_home: Path, admission: dict) -> bool:
    """Whether a provider execution ever claimed this admission."""
    run_dir = vibecrafted_home / "control_plane" / "runtime_runs" / admission["run_id"]
    return (run_dir / "execution.claim").exists()


def launch_tokens(command_text: str) -> list[str]:
    """``interactive-launch <agent> …`` out of an admitted command text."""
    tokens = shlex.split(command_text)
    assert "vibecrafted_core.spawn" in tokens, tokens
    assert "interactive-launch" in tokens, tokens
    assert "--admission-file" in tokens, tokens
    return tokens[tokens.index("interactive-launch") :]


def flag(argv: list[str], name: str) -> str:
    assert name in argv, (name, argv)
    return argv[argv.index(name) + 1]


def read_admission(launch_argv: list[str]) -> dict:
    return json.loads(
        Path(flag(launch_argv, "--admission-file")).read_text(encoding="utf-8")
    )


def spawn_handoff(hosted: list[str]) -> tuple[list[str], dict]:
    """The terminal child's protocol: the inner launch argv and its admission.

    ``hosted`` is everything after ``vc-terminal … -e``: the primary shell,
    then ``[/usr/bin/env PYTHONPATH=<core>] <python> -m vibecrafted_core.spawn
    interactive-handoff --command <admitted command>``.
    """
    assert hosted and hosted[0].endswith("launch-primary-shell.zsh"), hosted
    owner = hosted[1:]
    assert owner[owner.index("-m") + 1] == "vibecrafted_core.spawn", owner
    assert owner.count("interactive-handoff") == 1, owner
    inner = launch_tokens(flag(owner, "--command"))
    return inner, read_admission(inner)


def script_handoff(script_text: str) -> tuple[list[str], dict]:
    """The admitted command a Frame tab/pane script carries, and its admission."""
    command = shlex.split(script_text, comments=True)[-1]
    inner = launch_tokens(command)
    return inner, read_admission(inner)
