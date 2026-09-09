"""Both public spellings must share one launch boundary.

`vc-<verb>` and `vibecrafted <verb>` are compared by invoking the same deck
(or the same `cli.main` → deck argv) and asserting help, refusal, and
normalized child/request semantics. No live provider is started.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import cli
from vibecrafted_core.runtime_paths import version_is_stamped

from scripts import vetcoders_install

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = REPO_ROOT / "scripts" / "vibecrafted"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# Founder launches use a heredoc-shaped --prompt. A newline-free paragraph
# cannot prove argv/stdin preservation across the public spellings.
LONG_PROMPT = (
    'Preserve this exact argv payload: quotes "inner", dollars $HOME,\n'
    "and a heredoc paragraph\n\n" + ("word " * 40) + "\ntrailing line"
)

STAMPED_GENERATION_VERSION = "4.3.0+g1234567"


def _run_deck(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(DECK), *argv],
        cwd=cwd or REPO_ROOT,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_named(
    wrapper_name: str,
    argv: list[str],
    tmp_path: Path,
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    wrapper = tmp_path / wrapper_name
    if wrapper.exists() or wrapper.is_symlink():
        wrapper.unlink()
    wrapper.symlink_to(DECK)
    return subprocess.run(
        ["bash", str(wrapper), *argv],
        cwd=cwd or REPO_ROOT,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )


def _normalize(text: str) -> str:
    return ANSI.sub("", text).replace("\r\n", "\n")


def _assert_same_public_result(
    alias: subprocess.CompletedProcess[str],
    deck: subprocess.CompletedProcess[str],
) -> None:
    assert alias.returncode == deck.returncode
    assert _normalize(alias.stdout) == _normalize(deck.stdout)
    assert _normalize(alias.stderr) == _normalize(deck.stderr)


@pytest.mark.parametrize(
    ("wrapper", "verb", "argv"),
    [
        ("vc-fork", "fork", ["--help"]),
        ("vc-fork", "fork", []),
        ("vc-fork", "fork", ["codex"]),
        ("vc-fork", "fork", ["codex", "--runtime", "headless"]),
        ("vc-fork", "fork", ["codex", "--session", "sess-1", "--runtime", "headless"]),
        ("vc-fork", "fork", ["not-an-agent", "--session", "sess-1"]),
        ("vc-fork", "fork", ["codex", "--session", "sess-1", "--unknown-flag"]),
        ("vc-operator", "operator", ["--help"]),
        ("vc-canary", "canary", ["--help"]),
    ],
)
def test_public_spellings_match_help_and_failure(
    tmp_path: Path, wrapper: str, verb: str, argv: list[str]
) -> None:
    alias = _run_named(wrapper, argv, tmp_path)
    deck = _run_deck([verb, *argv])
    _assert_same_public_result(alias, deck)


def test_fork_help_names_both_spellings(tmp_path: Path) -> None:
    result = _run_named("vc-fork", ["--help"], tmp_path)
    assert result.returncode == 0
    assert "vc-fork" in result.stdout
    assert "vibecrafted fork" in result.stdout
    assert "Bare fork requires visible or terminal" not in result.stderr


def _write_fork_launch_boundary_python(
    path: Path, *, capture: Path, session_id: str, root: Path
) -> None:
    """Intercept fork-source / fork-session; exec the host interpreter otherwise."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"REAL = {sys.executable!r}\n"
        f"CAPTURE = {str(capture)!r}\n"
        f"SESSION = {session_id!r}\n"
        f"ROOT = {str(root)!r}\n"
        "args = sys.argv[1:]\n"
        "stripped = [item for item in args if item != '-I']\n"
        "if stripped[:2] == ['-m', 'vibecrafted_core.cli'] and len(stripped) > 2:\n"
        "    command = stripped[2]\n"
        "    rest = stripped[3:]\n"
        "    if command == 'fork-source':\n"
        "        print(json.dumps({\n"
        "            'accepted': True,\n"
        "            'agent_session_id': SESSION,\n"
        "            'source_run_id': 'source',\n"
        "            'selection_root': ROOT,\n"
        "            'source_root': ROOT,\n"
        "        }))\n"
        "        raise SystemExit(0)\n"
        "    if command == 'fork-session':\n"
        "        open(CAPTURE, 'w', encoding='utf-8').write(json.dumps({\n"
        "            'argv': [command, *rest],\n"
        "            'stdin': sys.stdin.read(),\n"
        "        }))\n"
        "        print(json.dumps({'accepted': True, 'run_id': 'fork-fixture-1'}))\n"
        "        raise SystemExit(0)\n"
        "os.execv(REAL, [REAL, *args])\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _isolated_alias_env(
    *,
    home: Path,
    extra_bin: Path,
    python: Path,
) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "PYTHONPATH",
            "PYTHONHOME",
            "VIBECRAFTED_ROOT",
            "VIBECRAFTED_RUNTIME_ROOT",
            "VIBECRAFTED_RUNTIME_BIN",
            "VIBECRAFTED_PYTHON",
            "VIBECRAFTED_PREFER_REPO_SPAWN",
            "VIBECRAFTED_AGENT",
            "VIBECRAFTED_AGENT_SESSION_ID",
            "VIBECRAFTED_RUN_ID",
            "CODEX_SESSION_ID",
            "CODEX_THREAD_ID",
        }
    }
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    env["PATH"] = f"{extra_bin}:{env.get('PATH', '')}"
    env["VIBECRAFTED_PYTHON"] = str(python)
    return env


def test_fork_prompt_spellings_share_normalized_launch_boundary(tmp_path: Path) -> None:
    """`--prompt` is a task launch. Compare captured argv/stdin, not live stdout."""
    home = tmp_path / "home"
    extra_bin = tmp_path / "bin"
    root = tmp_path / "repo"
    capture = tmp_path / "fork-session.json"
    spy = extra_bin / "python3"
    home.mkdir()
    extra_bin.mkdir()
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    (extra_bin / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (extra_bin / "codex").chmod(0o755)
    _write_fork_launch_boundary_python(
        spy, capture=capture, session_id="sess-1", root=root
    )
    env = _isolated_alias_env(home=home, extra_bin=extra_bin, python=spy)
    payload = [
        "codex",
        "--session",
        "sess-1",
        "--prompt",
        LONG_PROMPT,
        "--runtime",
        "headless",
        "--root",
        str(root),
        "--base",
        "538576ac",
        "--model",
        "gpt-test",
    ]

    results: list[dict[str, object]] = []
    for label, runner in (
        (
            "wrapper",
            lambda: _run_named(
                "vc-fork", payload, tmp_path / "wrapper", env=env, cwd=root
            ),
        ),
        ("deck", lambda: _run_deck(["fork", *payload], env=env, cwd=root)),
    ):
        if capture.exists():
            capture.unlink()
        proc = runner()
        assert proc.returncode == 0, f"{label}: {proc.stderr}\n{proc.stdout}"
        assert "Bare fork requires" not in proc.stderr
        assert "accepted" in proc.stdout
        recorded = json.loads(capture.read_text(encoding="utf-8"))
        argv = recorded["argv"]
        assert isinstance(argv, list)
        assert argv[0] == "fork-session"
        assert argv[1] == "codex"
        assert argv[argv.index("--session") + 1] == "sess-1"
        assert argv[argv.index("--root") + 1] == str(root)
        assert argv[argv.index("--model") + 1] == "gpt-test"
        assert argv[argv.index("--base") + 1] == "538576ac"
        assert "--prompt-stdin" in argv
        assert LONG_PROMPT not in argv
        assert recorded["stdin"] == LONG_PROMPT
        results.append(recorded)

    assert results[0]["argv"] == results[1]["argv"]
    assert results[0]["stdin"] == results[1]["stdin"] == LONG_PROMPT


@pytest.mark.parametrize(
    ("wrapper", "verb"),
    [
        ("vc-fork", "fork"),
        ("vc-operator", "operator"),
        ("vc-resume", "resume"),
        ("vc-init", "init"),
    ],
)
def test_python_entry_and_deck_verb_share_child_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wrapper: str, verb: str
) -> None:
    tools_home = tmp_path / "tools"
    deck = tools_home / "vibecrafted-current" / "scripts" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    seen: dict[str, list[str]] = {}

    def fake_run(argv, check=False, **_kwargs):
        seen.setdefault("calls", []).append(list(argv))
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools_home))
    monkeypatch.setattr("subprocess.run", fake_run)

    monkeypatch.setattr("sys.argv", [wrapper, "codex", "--prompt", LONG_PROMPT])
    assert cli.main() == 0
    monkeypatch.setattr(
        "sys.argv", ["vibecrafted", verb, "codex", "--prompt", LONG_PROMPT]
    )
    assert cli.main([verb, "codex", "--prompt", LONG_PROMPT]) == 0

    wrapper_argv, deck_argv = seen["calls"]
    assert wrapper_argv == [str(deck), verb, "codex", "--prompt", LONG_PROMPT]
    assert deck_argv == wrapper_argv


def _write_json_argv_capture(script: Path) -> None:
    """Record argv as JSON so a multiline --prompt stays one field."""
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CAPTURE_FILE']).write_text("
        "json.dumps(sys.argv), encoding='utf-8')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def test_runtime_shim_and_deck_verb_share_child_argv(tmp_path: Path) -> None:
    capture = tmp_path / "deck-argv.json"
    generation = tmp_path / "generation"
    fake_deck = generation / "bin" / "vibecrafted"
    _write_json_argv_capture(fake_deck)
    verb = vetcoders_install._RUNTIME_WRAPPER_VERBS["vc-fork"]
    assert "vc-fork" in vetcoders_install._runtime_published_launcher_names(
        fake_deck.parent
    )
    body = vetcoders_install._runtime_launcher_body(
        generation=generation,
        config_home=tmp_path / "config",
        crafted_home=tmp_path / "crafted",
        runtime_home=tmp_path / "runtime",
        frame_config=tmp_path / "frame",
        executable=fake_deck,
        leading_arguments=(verb,),
    )
    shim = tmp_path / "vc-fork"
    shim.write_text(body, encoding="utf-8")
    shim.chmod(0o755)

    env = os.environ.copy()
    env["CAPTURE_FILE"] = str(capture)
    payload = ["codex", "--session", "sess-1", "--prompt", LONG_PROMPT]
    shim_run = subprocess.run(
        [str(shim), *payload],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert shim_run.returncode == 0, shim_run.stderr
    shim_argv = json.loads(capture.read_text(encoding="utf-8"))
    capture.write_text("", encoding="utf-8")
    deck_run = subprocess.run(
        [str(fake_deck), verb, *payload],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert deck_run.returncode == 0, deck_run.stderr
    deck_argv = json.loads(capture.read_text(encoding="utf-8"))
    assert shim_argv[1:] == [verb, *payload]
    assert deck_argv[1:] == shim_argv[1:]
    assert LONG_PROMPT in shim_argv


def _write_fake_vc_frame(bin_dir: Path, capture_file: Path, session_name: str) -> None:
    script = bin_dir / "vc-frame"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                'if [[ "${1:-}" == "ls" || "${1:-}" == "list-sessions" ]]; then',
                f'  printf "{session_name} (attached)\\n"',
                "  exit 0",
                "fi",
                'printf "%s\\n" "$@" > "$CAPTURE_FILE"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_fake_aicx(bin_dir: Path, current_id: str, previous_id: str) -> None:
    script = bin_dir / "aicx"
    script.write_text(
        "\n".join(
            [
                "#!/usr/bin/env python3",
                "import json",
                "import sys",
                "args = sys.argv[1:]",
                "if args[:2] == ['sessions', 'current']:",
                f"    print(json.dumps({{'session_id': '{current_id}', 'agent': 'codex'}}))",
                "elif args[:2] == ['sessions', 'list']:",
                "    print(json.dumps([",
                f"        {{'session_id': '{current_id}', 'agent': 'codex'}},",
                f"        {{'session_id': '{previous_id}', 'agent': 'codex'}},",
                "    ]))",
                "else:",
                "    raise SystemExit(97)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _installed_generation(tmp_path: Path, home: Path, frame_source: Path) -> Path:
    generation = tmp_path / "generation"
    deck = generation / "bin" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted", deck)
    deck.chmod(0o755)
    shutil.copytree(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core",
        generation / "vibecrafted-core" / "vibecrafted_core",
    )
    # Same immutable stamp the production guard requires: X.Y.Z+g + hex SHA.
    # `+gtest` is not stamped (`s` is outside hex). Receipt file is the
    # deck's installed-generation marker; a checkout never carries it.
    assert version_is_stamped(STAMPED_GENERATION_VERSION)
    assert not version_is_stamped("4.3.1+gtest")
    (generation / "VERSION").write_text(
        f"{STAMPED_GENERATION_VERSION}\n", encoding="utf-8"
    )
    (generation / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    python = generation / "bin" / "python3"
    python.write_text(f'#!/bin/sh\nexec {sys.executable!r} "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    frame = generation / "bin" / "vc-frame"
    shutil.copy2(frame_source, frame)
    frame.chmod(0o755)
    engine = generation / "libexec" / "vc-frame"
    engine.parent.mkdir(parents=True)
    shutil.copy2(frame_source, engine)
    engine.chmod(0o755)
    return generation


def _admission_from_capture(payload: list[str]) -> dict[str, object]:
    separator = payload.index("--")
    command_body = Path(payload[separator + 1]).read_text(encoding="utf-8")
    tokens = shlex.split(shlex.split(command_body, comments=True)[-1])
    return json.loads(Path(tokens[tokens.index("--admission-file") + 1]).read_text())


def test_fork_spellings_share_normalized_child_admission(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    root = tmp_path / "Loctree" / "aicx"
    home.mkdir()
    fake_bin.mkdir()
    root.mkdir(parents=True)
    _write_fake_aicx(fake_bin, "current-codex-session", "last-codex-session")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    record = home / ".vibecrafted/control_plane/runtime_runs/source/meta.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "run_id": "source",
                "agent": "codex",
                "agent_session_id": "current-codex-session",
                "root": str(root),
                "started_at": "2026-09-09T00:00:00Z",
            }
        )
    )

    results: list[tuple[list[str], dict[str, object], str]] = []
    for label in ("deck", "wrapper"):
        capture = tmp_path / f"{label}-vc-frame-args.txt"
        _write_fake_vc_frame(fake_bin, capture, "operator-test")
        generation = _installed_generation(
            tmp_path / label, home, fake_bin / "vc-frame"
        )
        if label == "wrapper":
            wrapper = tmp_path / "vc-fork"
            if wrapper.exists() or wrapper.is_symlink():
                wrapper.unlink()
            wrapper.symlink_to(generation / "bin" / "vibecrafted")
            argv = ["bash", str(wrapper)]
        else:
            argv = ["bash", str(generation / "bin" / "vibecrafted"), "fork"]
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
        env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
        env["VC_FRAME_PANE_ID"] = "7"
        env["VC_FRAME_SESSION_NAME"] = "operator-test"
        env["CAPTURE_FILE"] = str(capture)
        env["CODEX_THREAD_ID"] = "current-codex-session"
        for key in (
            "CODEX_SESSION_ID",
            "VIBECRAFTED_AGENT",
            "VIBECRAFTED_AGENT_SESSION_ID",
            "VIBECRAFTED_RUN_ID",
            "VIBECRAFTED_ROOT",
            "VIBECRAFTED_RUNTIME_ROOT",
            "VIBECRAFTED_RUNTIME_BIN",
            "VIBECRAFTED_PYTHON",
            "VIBECRAFTED_PREFER_REPO_SPAWN",
            "PYTHONPATH",
            "PYTHONHOME",
        ):
            env.pop(key, None)
        proc = subprocess.run(
            [
                *argv,
                "codex",
                "--session",
                "current",
                "--runtime",
                "visible",
                "--root",
                str(root),
                "--model",
                "gpt-test",
                "--permissions",
                "auto",
            ],
            check=False,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, proc.stderr
        payload = capture.read_text(encoding="utf-8").splitlines()
        admission = _admission_from_capture(payload)
        results.append((payload, admission, proc.stdout))

    (
        (deck_payload, deck_admission, deck_out),
        (wrap_payload, wrap_admission, wrap_out),
    ) = results
    assert deck_payload[:3] == wrap_payload[:3]
    assert "new-pane" in deck_payload and "new-pane" in wrap_payload
    assert "new-tab" not in deck_payload and "new-tab" not in wrap_payload
    assert (
        deck_admission["model_requested"]
        == wrap_admission["model_requested"]
        == "gpt-test"
    )
    assert deck_admission["root"] == wrap_admission["root"] == str(root)
    assert deck_admission["session_selection"] == wrap_admission["session_selection"]
    assert deck_admission["session_selection"]["session_selector"] == "current"
    assert (
        deck_admission["session_selection"]["agent_session_id"]
        == "current-codex-session"
    )
    assert "source-session: current-codex-session" in deck_out
    assert "source-session: current-codex-session" in wrap_out


def test_alias_maps_stay_coordinated() -> None:
    shell_wrappers = set(vetcoders_install.LAUNCHER_WRAPPERS) - set(
        vetcoders_install.PYTHON_ENTRYPOINT_LAUNCHERS
    )
    assert set(cli.SHELL_WRAPPER_VERBS) == shell_wrappers
    assert "vc-fork" in shell_wrappers
    assert "vc-operator" in shell_wrappers
    assert "vc-canary" in shell_wrappers
    assert "vc-fork" not in vetcoders_install.PYTHON_ENTRYPOINT_LAUNCHERS
    expected_runtime = {
        name: verb
        for name, verb in cli.SHELL_WRAPPER_VERBS.items()
        if name != "vc-start"
    }
    assert vetcoders_install._RUNTIME_WRAPPER_VERBS == expected_runtime
    pyproject = (REPO_ROOT / "vibecrafted-core/pyproject.toml").read_text(
        encoding="utf-8"
    )
    assert "vc-fork =" not in pyproject
    dispatch = (
        REPO_ROOT / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/dispatch.sh"
    ).read_text(encoding="utf-8")
    assert 'vc-fork() { _vetcoders_vc_passthrough fork "$@"; }' in dispatch
    assert 'vc-canary() { _vetcoders_vc_passthrough canary "$@"; }' in dispatch
    assert 'vc-operator() { _vetcoders_vc_passthrough operator "$@"; }' in dispatch
