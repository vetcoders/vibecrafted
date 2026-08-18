"""Public vc-research / vibecrafted research surface tests.

These assert the **deck/Python** contract after interactive zsh pass-through
(`vc-research` → `command vibecrafted research`). Legacy shell swarm layout
(`rsch-*` run dirs and `Research override (…) prepared`) is not required, but
the stable uno|duo|trio public arity contract remains supported by core.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from vibecrafted_core.cli import _normalize_research_arity_args

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)


def _env(tmp_path: Path, *, crafted_home: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    home = crafted_home or (tmp_path / "home" / ".vibecrafted")
    home.mkdir(parents=True, exist_ok=True)
    # MUST be canonical. `run_mutation._canonical_directory` refuses a mutation
    # root that differs from its own realpath — a deliberate TOCTOU and
    # symlink-redirection guard around run meta — and the refusal surfaces only
    # as `RunMetaMutationError` inside the supervisor, so the caller sees a run
    # that never settles rather than an error.
    #
    # pytest's own tmp_path happens to be canonical on this host
    # (/private/var/folders/...), so this is defence, not the fix for the three
    # reds below; MEASURED 2026-08-18 by reproducing the same launch under
    # /tmp, where /tmp -> /private/tmp makes the guard fire every time. Any host
    # whose TMPDIR is reached through a symlink would hit it here.
    home = home.resolve()
    env["VIBECRAFTED_HOME"] = str(home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["VETCODERS_SPAWN_RUNTIME"] = "headless"
    env["VIBECRAFTED_LIVE_VIEWER"] = "0"
    # Prefer headless / no terminal transport in CI.
    env.pop("VIBECRAFTED_FORCE_TERMINAL", None)
    # VIBECRAFTED_TEST_MODE=1 (conftest autouse) forbids PATH deck discovery;
    # tests that want the deck must name it explicitly — use the repo deck,
    # never the operator's installed launcher.
    env["VIBECRAFTED_DECK_BIN"] = str(REPO_ROOT / "scripts" / "vibecrafted")
    fake_bin = tmp_path / "fake-agent-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for agent in ("claude", "codex", "agy", "junie", "grok"):
        executable = fake_bin / agent
        executable.write_text(
            "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    return env


def _wait_for_test_run_exit(
    crafted_home: Path, run_id: str, *, timeout: float = 20.0
) -> None:
    """Keep an accepted async fixture inside the test that launched it."""
    deadline = time.monotonic() + timeout
    meta_path = crafted_home / "control_plane" / "runtime_runs" / run_id / "meta.json"
    while time.monotonic() < deadline:
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            payload = {}
        if payload.get("exit_code") is not None or payload.get("status") in {
            "completed",
            "failed",
            "partial_success",
        }:
            return
        time.sleep(0.05)
    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    subprocess.run(
        [
            str(REPO_ROOT / "scripts" / "vibecrafted"),
            "swarm",
            "stop",
            "--run-id",
            run_id,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    leaked = _reap_run_process_group(meta_path)
    pytest.fail(
        f"test research run {run_id} did not settle within {timeout}s"
        + (f" (reaped {leaked} leaked process(es))" if leaked else "")
    )


def _reap_run_process_group(meta_path: Path) -> int:
    """Kill the worker this test started, so a red test cannot poison the host.

    `swarm stop` above is the polite request and it is not enough. MEASURED
    2026-08-18: each timing-out case left a `vibecrafted_core.dispatcher run`
    blocked in wait4 and its `workflow_runtime research` child blocked in the
    asyncio kevent loop, both reparented to init and still alive six minutes
    later. SIGTERM to the group took the dispatcher only; the child needed
    SIGKILL.

    Only ever the pgid this run recorded in its own meta under the test's
    private VIBECRAFTED_HOME, and never the test runner's own group.
    """
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

    pgid = payload.get("worker_pgid")
    pid = payload.get("worker_pid")
    if not isinstance(pgid, int) or pgid <= 1 or pgid == os.getpgrp():
        pgid = None

    reaped = 0
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        for target, killer in ((pgid, os.killpg), (pid, os.kill)):
            if not isinstance(target, int) or target <= 1:
                continue
            try:
                killer(target, signal_number)
                reaped += 1
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(0.2)
    return reaped


def _run_vc_research(
    root: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    """Invoke public shell entry after loading repo helpers (zsh/bash function)."""
    quoted = " ".join(f'"{a}"' if " " in a else a for a in args)
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{HELPER_SCRIPT}"; vc-research {quoted}',
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _wait_for_completed_launch(result, env)
    return result


def _run_deck_research(
    root: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    # Exec the repo deck directly — argv[0] "command" is a shell builtin, not
    # a binary, and PATH discovery would silently hit the operator's deck.
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "vibecrafted"), "research", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _wait_for_completed_launch(result, env)
    return result


def _parse_receipt(stdout: str) -> dict[str, str]:
    """Extract fields from VIBECRAFTED LAUNCH RECEIPT or JSON --json body."""
    out = stdout.strip()
    # Prefer human receipt lines (always present on terminal launch).
    m = re.search(r"run_id:\s+(\S+)", out)
    if m:
        return {"run_id": m.group(1), "status": "launching", "raw": out}
    # JSON body may be first object line or the whole stdout.
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "run_id" in payload:
            return {
                "run_id": str(payload["run_id"]),
                "status": str(payload.get("status") or ""),
                "raw": out,
            }
        if payload.get("accepted") and "command" in payload:
            cmd = payload["command"]
            rid = ""
            for i, part in enumerate(cmd):
                if part == "--run-id" and i + 1 < len(cmd):
                    rid = str(cmd[i + 1])
                    break
            if rid:
                return {"run_id": rid, "status": "launching", "raw": out}
    # Fallback: rese-/rsch- token anywhere
    m2 = re.search(r"\b((?:rese|rsch)-[0-9a-zA-Z-]+)\b", out)
    assert m2 is not None, out
    return {"run_id": m2.group(1), "status": "launching", "raw": out}


def _wait_for_completed_launch(
    result: subprocess.CompletedProcess[str], env: dict[str, str]
) -> None:
    """Drain an accepted async launch before its pytest temp tree disappears."""
    if result.returncode != 0:
        return
    combined = result.stdout + result.stderr
    if "rese-" not in combined and '"accepted": true' not in combined:
        return
    receipt = _parse_receipt(combined)
    _wait_for_test_run_exit(Path(env["VIBECRAFTED_HOME"]), receipt["run_id"])


@pytest.mark.parametrize(
    ("keyword", "agents"),
    [
        ("uno", ["codex"]),
        ("duo", ["codex", "agy"]),
        ("trio", ["claude", "codex", "agy"]),
    ],
)
def test_research_arity_keywords_expand_to_exact_agent_lists(
    keyword: str, agents: list[str]
) -> None:
    args = ["research", keyword, *agents, "--prompt", "compare"]
    assert _normalize_research_arity_args(args) == [
        "research",
        *agents,
        "--prompt",
        "compare",
    ]


def test_research_arity_keyword_fails_closed_on_wrong_lane_count() -> None:
    with pytest.raises(ValueError, match="trio expects exactly 3 agent"):
        _normalize_research_arity_args(
            ["research", "trio", "claude", "codex", "--prompt", "compare"]
        )


def test_vc_research_help_is_pure_help() -> None:
    """Public vc-research --help must match the deck (not legacy shell help)."""
    env = os.environ.copy()
    # Test mode blocks PATH deck discovery — name the repo deck explicitly.
    env["VIBECRAFTED_DECK_BIN"] = str(REPO_ROOT / "scripts" / "vibecrafted")
    result = subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-research --help'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    deck = subprocess.run(
        ["bash", "-lc", "command vibecrafted research --help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert deck.returncode == 0
    for token in (
        "--json",
        "--model",
        "--prompt-stdin",
        "--synthesizer-model",
        "vibecrafted research",
    ):
        assert token in result.stdout, f"missing canonical token {token!r}"
        assert token in deck.stdout, f"deck missing {token!r}"
    assert "Research swarm launched" not in result.stdout
    assert "command not found" not in result.stdout
    assert "command not found" not in result.stderr
    # Legacy shell-only help strings must not reappear (split-brain).
    assert "Configurable triple-agent research swarm launcher" not in result.stdout


def test_vc_research_shell_matches_deck_help_text() -> None:
    env = os.environ.copy()
    # Test mode blocks PATH deck discovery — name the repo deck explicitly.
    env["VIBECRAFTED_DECK_BIN"] = str(REPO_ROOT / "scripts" / "vibecrafted")
    shell = subprocess.run(
        ["bash", "-lc", f'source "{HELPER_SCRIPT}"; vc-research --help'],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    deck = subprocess.run(
        ["bash", "-lc", "command vibecrafted research --help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shell.returncode == 0 and deck.returncode == 0
    # Same product surface — flags present on both (not byte-identical due to wrappers).
    for tok in (
        "--json",
        "--model",
        "--prompt-stdin",
        "--synthesizer-model",
        "--runtime",
    ):
        assert tok in shell.stdout
        assert tok in deck.stdout


def test_vc_research_launch_emits_control_plane_receipt(tmp_path: Path) -> None:
    """Sourced vc-research launches via core receipt (rese-*), not legacy rsch layout."""
    root = tmp_path / "repo"
    root.mkdir()
    crafted_home = tmp_path / "home" / ".vibecrafted"
    env = _env(tmp_path, crafted_home=crafted_home)

    result = _run_vc_research(
        root,
        env,
        "codex",
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--prompt",
        "Check auth providers",
        "--json",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    combined = result.stdout + result.stderr
    assert "LAUNCH RECEIPT" in combined or '"accepted": true' in combined
    receipt = _parse_receipt(combined if "run_id" in combined else result.stdout)
    run_id = receipt["run_id"]
    assert run_id.startswith(("rese-", "rsch-")), run_id
    # Durable launch artifacts under VIBECRAFTED_HOME (control JSON may lag;
    # prompt + launch log are written at accept time).
    runtime = crafted_home / "control_plane" / "runtime_runs" / run_id
    assert (runtime / "prompt.md").is_file(), f"missing prompt at {runtime}"
    launch_logs = list((crafted_home / "control_plane" / "launches").glob("*research*"))
    assert launch_logs, "expected a research launch log under control_plane/launches"
    assert "Research override (codex) prepared" not in result.stdout


def test_vc_research_and_deck_launch_same_skill(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    env = _env(tmp_path)

    shell = _run_vc_research(
        root,
        env,
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--prompt",
        "parity probe",
        "--json",
    )
    # Second launch under same env should still accept.
    deck = _run_deck_research(
        root,
        env,
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--prompt",
        "parity probe deck",
        "--json",
    )
    assert shell.returncode == 0, shell.stdout + shell.stderr
    assert deck.returncode == 0, deck.stdout + deck.stderr
    for out in (shell.stdout + shell.stderr, deck.stdout + deck.stderr):
        assert "rese-" in out or "accepted" in out


def test_vc_research_unsupported_agent_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    env = _env(tmp_path)

    result = _run_vc_research(
        root,
        env,
        "not-an-agent",
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--prompt",
        "must not launch",
    )

    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    assert "unsupported" in combined or "unknown" in combined or "error" in combined
    # No successful receipt for a bogus agent-only invocation without work flags.
    assert "status:     launching" not in result.stdout or "error" in combined


def test_vc_research_requires_prompt_or_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    env = _env(tmp_path)

    result = _run_vc_research(
        root,
        env,
        "codex",
        "--runtime",
        "headless",
        "--root",
        str(root),
    )
    # Deck refuses empty work (no -p/-f).
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert (
        "prompt" in combined.lower()
        or "file" in combined.lower()
        or "provide work" in combined.lower()
        or "error" in combined.lower()
    )


def test_vc_research_file_plan_launches(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    crafted_home = tmp_path / "home" / ".vibecrafted"
    plan = tmp_path / "research-plan.md"
    plan.write_text(
        textwrap.dedent(
            """\
            # Research Plan: Prompt Hygiene

            ## Questions
            1. Which prompt content reaches the worker?
            """
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, crafted_home=crafted_home)

    result = _run_vc_research(
        root,
        env,
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--file",
        str(plan),
        "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = _parse_receipt(result.stdout + result.stderr)
    assert receipt["run_id"]
    runtime = crafted_home / "control_plane" / "runtime_runs" / receipt["run_id"]
    assert (runtime / "prompt.md").is_file()


def test_legacy_shell_research_helper_still_exists_for_internal_use() -> None:
    """Internal _vetcoders_research may remain for non-public paths; public entry must not call it."""
    dispatch = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "lib"
        / "dispatch.sh"
    )
    text = dispatch.read_text(encoding="utf-8")
    assert "vc-research() { _vetcoders_research" not in text
    assert "_vetcoders_vc_passthrough research" in text
    research_sh = (
        REPO_ROOT
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "vc-research"
        / "shell"
        / "research.sh"
    )
    # Legacy helper may still exist for internal/compat scripts.
    assert research_sh.is_file()
