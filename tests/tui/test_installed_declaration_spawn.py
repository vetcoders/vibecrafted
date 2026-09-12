"""A declared workspace reaches its provider from an INSTALLED generation.

Founder repro (2026-09-09, exact signed candidate 8177a33d, tester-release
acceptance in an isolated upgrade home)::

    vibecrafted init codex --repo <isolated project> --token-budget unmetered \
        --prompt <fixture>            # cwd /, stdin DEVNULL, stale Frame markers

The real terminal and the real Frame created the provider pane; the pane then
died before any provider ran::

    …/releases/4.3.1+g8177a33d/python/bin/python3.12: Error while finding
    module specification for 'vibecrafted_core.spawn'
    (ModuleNotFoundError: No module named 'vibecrafted_core')

The pane re-enters core with the composer's ``sys.executable``. In a Runtime
Pack the composer (``spawn interactive-command``) runs under the generation's
``bin/python3`` bootstrap -- a bash wrapper that exports the generation-private
``PYTHONPATH`` and execs ``python/bin/python3.12`` -- so ``sys.executable`` is
the RAW interpreter, and the Frame's fresh login shell hands it no
``PYTHONPATH``. The existing declaration scenes never saw this: their
generation symlinks ``bin/python3`` to the pytest interpreter, which imports
core on its own, and no scene ever executed the tab script it captured.

Proven here on a packaged-layout generation (a raw interpreter that cannot
import core, the release build's exact ``bin/python3`` wrapper, the package
tree beside it), from OUTSIDE the checkout with ``PYTHONPATH``/``PYTHONHOME``
unset and a hermetic login shell, executing the generated command for real:

* the shared owner (``spawn interactive-command`` under the generation
  bootstrap) composes an invocation that reaches the provider exactly once,
  on the inherited TTY, in the declared repository, with the run receipt
  published under the isolated home and every execution option intact;
* the public deck's own tab script (the REAL deck, the REAL composer; the
  Frame stub only records the tab) does the same for init, operator and
  partner;
* the raw-interpreter form of the very same invocation still fails exactly
  the way the candidate failed -- the fixture reproduces the defect rather
  than dodging it;
* the provider environment boundary holds (HAK-32): core bootstrapped through
  the wrapper chain, yet the provider sees no ``PYTHONPATH`` and the project's
  own Python cannot import the runtime's private package.

Only the provider executable is a stub.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

# --import-mode=importlib: sibling scenes are loaded by file, as they load
# each other.
_ENTRY_SPEC = importlib.util.spec_from_file_location(
    "declaration_entry_scene",
    Path(__file__).with_name("test_declaration_entry.py"),
)
assert _ENTRY_SPEC is not None and _ENTRY_SPEC.loader is not None
_entry = importlib.util.module_from_spec(_ENTRY_SPEC)
_ENTRY_SPEC.loader.exec_module(_entry)

DeckScene = _entry.DeckScene
FOREIGN_LIVE = _entry.FOREIGN_LIVE
MARKER_KEYS = _entry.MARKER_KEYS
VC_FRAME_STUB = _entry.VC_FRAME_STUB
_PTY_SPAWN = _entry._PTY_SPAWN
_attaches = _entry._attaches
_creates = _entry._creates
_new_tabs = _entry._new_tabs
_write = _entry._write

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK = REPO_ROOT / "scripts" / "vibecrafted"
CORE_PACKAGE = REPO_ROOT / "vibecrafted-core" / "vibecrafted_core"

RAW_INTERPRETER = f"python{sys.version_info.major}.{sys.version_info.minor}"
GENERATION_VERSION = "0.0.0+g8177a33d"
PROMPT = "Release acceptance fixture. Stay within this isolated workspace."

# The bootstrap scripts/build-vibecrafted-release.sh renders into every
# Runtime Pack, byte for byte. The pane must go through it: it is the only
# invocation that reaches vibecrafted_core from a fresh login shell.
PACKAGED_BOOTSTRAP = (
    "#!/bin/bash\n"
    "set -euo pipefail\n"
    'runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"\n'
    "export PYTHONNOUSERSITE=1\n"
    "export PYTHONDONTWRITEBYTECODE=1\n"
    'export PYTHONPATH="$runtime_root/vibecrafted-core:'
    '$runtime_root/vibecrafted-mcp:$runtime_root/python-site"\n'
    f'exec "$runtime_root/python/bin/{RAW_INTERPRETER}" "$@"\n'
)

# The provider: records how it was reached, once per invocation, and returns.
PROVIDER_STUB = (
    "#!/bin/bash\n"
    'if [ "${1:-}" = "--version" ]; then echo "codex-cli 0.0.0-fixture"; exit 0; fi\n'
    "stdin_tty=false; [ -t 0 ] && stdin_tty=true\n"
    "stdout_tty=false; [ -t 1 ] && stdout_tty=true\n"
    # The project's own Python, as a provider would run it: it must not see
    # the runtime's private package through inherited bootstrap state.
    'project_python="$(command -v python3 || true)"\n'
    "core_import_rc=127\n"
    'if [ -n "$project_python" ]; then'
    ' "$project_python" -c "import vibecrafted_core" >/dev/null 2>&1;'
    " core_import_rc=$?; fi\n"
    'printf \'{"pid": %s, "cwd": "%s", "stdin_tty": %s, "stdout_tty": %s,'
    ' "argc": %s, "run_id": "%s", "session_id": "%s", "workspace_id": "%s",'
    ' "pythonpath": "%s", "nousersite": "%s", "project_python": "%s",'
    ' "core_import_rc": %s}\\n\' '
    '"$$" "$PWD" "$stdin_tty" "$stdout_tty" "$#" "${VIBECRAFTED_RUN_ID:-}"'
    ' "${VIBECRAFTED_SESSION_ID:-}" "${VIBECRAFTED_WORKSPACE_ID:-}"'
    ' "${PYTHONPATH:-}" "${PYTHONNOUSERSITE:-}" "$project_python"'
    ' "$core_import_rc" >> "$VC_FIXTURE_EVENTS"\n'
    'printf \'%s\\n\' "$@" > "$VC_FIXTURE_EVENTS.argv"\n'
    "printf 'PROVIDER FIXTURE reached\\n'\n"
    "exit 0\n"
)


def _raw_base_interpreter() -> Path:
    """The interpreter behind the test venv: no venv, no core on its path."""
    candidate = Path(sys.base_prefix) / "bin" / RAW_INTERPRETER
    if not candidate.is_file():
        pytest.skip(f"no raw base interpreter at {candidate}")
    probe = subprocess.run(
        [str(candidate), "-c", "import vibecrafted_core"],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/", "LANG": "en_US.UTF-8"},
    )
    if probe.returncode == 0:
        pytest.skip(f"{candidate} imports vibecrafted_core on its own; no raw proof")
    return candidate


@pytest.fixture(scope="module")
def packaged_generation(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A Runtime-Pack-shaped generation: raw interpreter, bootstrap wrapper,
    the package beside it, the tracked deck, stub engine and front doors."""
    raw = _raw_base_interpreter()
    gen = tmp_path_factory.mktemp("installed") / "releases" / GENERATION_VERSION
    gen.mkdir(parents=True)
    (gen / "VERSION").write_text(GENERATION_VERSION + "\n", encoding="utf-8")
    (gen / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    (gen / "python" / "bin").mkdir(parents=True)
    (gen / "python" / "bin" / RAW_INTERPRETER).symlink_to(raw)
    _write(gen / "bin" / "python3", PACKAGED_BOOTSTRAP)
    shutil.copytree(
        CORE_PACKAGE,
        gen / "vibecrafted-core" / "vibecrafted_core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (gen / "vibecrafted-mcp").mkdir()
    # The pack carries its third-party wheels here; the test interpreter's
    # site-packages stands in for them. It is reachable ONLY through the
    # bootstrap's PYTHONPATH -- the raw interpreter never sees it.
    (gen / "python-site").symlink_to(Path(sysconfig.get_paths()["purelib"]))
    shutil.copy2(DECK, _write(gen / "scripts" / "vibecrafted", "#!/bin/bash\n"))
    _write(gen / "libexec" / "vc-frame", VC_FRAME_STUB)
    _write(gen / "bin" / "vc-frame", VC_FRAME_STUB)
    _write(gen / "libexec" / "vc-terminal", "#!/bin/bash\nexit 0\n")
    _write(
        gen / "bin" / "vc-terminal",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"MARKER_KEYS = {MARKER_KEYS!r}\n"
        "open(os.environ['VC_TERMINAL_CAPTURE'], 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', ''),"
        " 'markers': {k: os.environ.get(k) for k in MARKER_KEYS}}))\n"
        "sys.exit(int(os.environ.get('VC_TERMINAL_EXIT', '0')))\n",
    )
    for verb in ("vc-start", "vibecrafted"):
        _write(gen / "bin" / verb, "#!/bin/bash\nexit 0\n")
    return gen


class PaneHost:
    """Where the generated command really runs: outside the checkout, a
    hermetic login shell (own HOME, no rc files, no PYTHONPATH/PYTHONHOME, no
    runtime selection inherited), the provider stub as the only ``codex``."""

    def __init__(self, tmp_path: Path, generation: Path) -> None:
        self.generation = generation
        self.home = tmp_path / "pane-home"
        self.home.mkdir()
        self.stubs = tmp_path / "pane-stubs"
        _write(self.stubs / "codex", PROVIDER_STUB)
        self.events = tmp_path / "provider-events.jsonl"
        self.repo = tmp_path / "declared-project"
        self.repo.mkdir()
        subprocess.run(
            ["/usr/bin/git", "init", "-q", str(self.repo)],
            check=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)},
        )
        self.elsewhere = tmp_path / "elsewhere"
        self.elsewhere.mkdir()

    def env(self, **extra: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "ZDOTDIR": str(self.home),
            "PATH": f"{self.stubs}{os.pathsep}/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": "/tmp",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "VIBECRAFTED_HOME": str(self.home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_HOME": str(
                self.home / ".local" / "share" / "vibecrafted"
            ),
            "VIBECRAFTED_PERCEPTION_WATCH": "0",
            "VC_FIXTURE_EVENTS": str(self.events),
        }
        env.update(extra)
        return env

    def launcher_env(self) -> dict[str, str]:
        """What the public launcher exports before exec'ing the deck."""
        return self.env(
            VIBECRAFTED_RUNTIME_ROOT=str(self.generation),
            VIBECRAFTED_PYTHON=str(self.generation / "bin" / "python3"),
            VIBECRAFTED_RUNTIME_BIN=str(self.generation / "bin"),
        )

    def assert_provider_is_the_stub(self) -> None:
        """A login shell rebuilds PATH (path_helper); the stub must still be the
        provider the pane resolves, or a real provider would be launched."""
        found = subprocess.run(
            ["/bin/zsh", "-lc", "command -v codex"],
            check=False,
            capture_output=True,
            text=True,
            env=self.env(),
            cwd=self.elsewhere,
        )
        if found.stdout.strip() != str(self.stubs / "codex"):
            pytest.fail(
                "the pane login shell resolves another codex: "
                f"{found.stdout.strip()!r} {found.stderr.strip()!r}"
            )

    def run_pane(self, script: Path) -> subprocess.CompletedProcess[str]:
        """Execute a pane command script the way the Frame does: as a file,
        from outside the checkout, on a real pty."""
        self.assert_provider_is_the_stub()
        return subprocess.run(
            [sys.executable, "-c", _PTY_SPAWN + repr([str(script)]) + ")))"],
            check=False,
            cwd=self.elsewhere,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=180,
        )

    def write_pane_script(self, name: str, command_text: str) -> Path:
        """The shape ``_vetcoders_write_command_script`` gives a pane."""
        return _write(
            self.home / "panes" / name,
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            f"/bin/zsh -lc {shlex.quote(command_text)}\n",
        )

    def provider_events(self) -> list[dict]:
        if not self.events.exists():
            return []
        return [
            json.loads(line)
            for line in self.events.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def provider_argv(self) -> list[str]:
        path = Path(str(self.events) + ".argv")
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    def receipts(self) -> list[dict]:
        runs = self.home / ".vibecrafted" / "control_plane" / "runtime_runs"
        return [
            json.loads(meta.read_text(encoding="utf-8"))
            for meta in sorted(runs.glob("*/meta.json"))
        ]


def _compose(host: PaneHost, root: Path) -> list[str]:
    """The shared owner, exactly as the deck calls it in an installed
    generation: under the bootstrap, prompt on stdin, from cwd ``/``."""
    composed = subprocess.run(
        [
            str(host.generation / "bin" / "python3"),
            "-m",
            "vibecrafted_core.spawn",
            "interactive-command",
            "codex",
            "--runtime",
            "local-native",
            "--permissions",
            "bypass",
            "--token-budget",
            "unmetered",
            "--operator",
            "none",
            "--continuity",
            "fresh",
            "--root",
            str(root),
        ],
        input=PROMPT,
        check=False,
        cwd="/",
        env=host.launcher_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert composed.returncode == 0, (
        composed.returncode,
        composed.stdout,
        composed.stderr,
    )
    return shlex.split(composed.stdout.strip())


def _assert_reached_once(host: PaneHost, result, root: Path) -> dict:
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    events = host.provider_events()
    assert len(events) == 1, (events, result.stdout, result.stderr)
    event = events[0]
    assert event["stdin_tty"] and event["stdout_tty"], event
    assert Path(event["cwd"]).resolve() == root.resolve(), event
    assert event["run_id"] and event["session_id"] and event["workspace_id"], event
    # Provider environment boundary: core bootstrapped through the wrapper
    # chain, but the provider -- and the project Python it runs -- inherits
    # none of the generation-private import state (HAK-32).
    assert event["pythonpath"] == "" and event["nousersite"] == "", event
    assert event["project_python"], event
    assert event["core_import_rc"] != 0, event
    argv = host.provider_argv()
    assert PROMPT in argv, argv
    receipts = [r for r in host.receipts() if r.get("run_id") == event["run_id"]]
    assert len(receipts) == 1, host.receipts()
    receipt = receipts[0]
    assert receipt["agent"] == "codex" and receipt["mode"] == "interactive", receipt
    assert receipt["quota_policy"]["selection"] == "unmetered", receipt
    assert Path(receipt["root"]).resolve() == root.resolve(), receipt
    assert receipt["vibecrafted_session_id"] == event["session_id"], receipt
    assert receipt["status"] == "completed", receipt
    return event


# --------------------------------------------------------------------------
# shared owner: spawn interactive-command under the generation bootstrap
# --------------------------------------------------------------------------


def test_shared_owner_invocation_reaches_the_provider_from_a_fresh_login_shell(
    tmp_path: Path, packaged_generation: Path
) -> None:
    """Baseline (8177a33d): the composed argv names the raw
    ``python/bin/python3.12``; executed in the pane it dies with
    ``ModuleNotFoundError: No module named 'vibecrafted_core'`` and no
    provider ever runs. Now the invocation goes through the generation's own
    ``bin/python3`` bootstrap and reaches the provider exactly once, on the
    inherited TTY, in the declared repository."""
    host = PaneHost(tmp_path, packaged_generation)
    command = _compose(host, host.repo)

    assert command[0] == str(packaged_generation / "bin" / "python3"), command
    assert command[1:4] == ["-m", "vibecrafted_core.spawn", "interactive-launch"], (
        command
    )
    assert "env" not in command and not any(
        a.startswith("PYTHONPATH=") for a in command
    ), command
    for option in (
        ("--runtime", "local-native"),
        ("--permissions", "bypass"),
        ("--token-budget", "unmetered"),
        ("--operator", "none"),
        ("--continuity", "fresh"),
        ("--root", str(host.repo.resolve())),
        ("--prompt", PROMPT),
    ):
        assert command[command.index(option[0]) + 1] == option[1], command

    script = host.write_pane_script("vc-spawn-cmd.sh", shlex.join(command))
    result = host.run_pane(script)

    _assert_reached_once(host, result, host.repo)
    assert "PROVIDER FIXTURE reached" in result.stdout, result.stdout


def test_raw_interpreter_form_of_the_same_invocation_still_fails_like_the_candidate(
    tmp_path: Path, packaged_generation: Path
) -> None:
    """The fixture reproduces the defect: the very same invocation with the
    raw interpreter in front (what 8177a33d composed) cannot find core from
    the pane and starts no provider."""
    host = PaneHost(tmp_path, packaged_generation)
    command = _compose(host, host.repo)
    raw = [str(packaged_generation / "python" / "bin" / RAW_INTERPRETER), *command[1:]]

    script = host.write_pane_script("vc-spawn-cmd-raw.sh", shlex.join(raw))
    result = host.run_pane(script)

    assert result.returncode != 0, (result.stdout, result.stderr)
    assert "No module named 'vibecrafted_core'" in result.stdout + result.stderr, (
        result.stdout,
        result.stderr,
    )
    assert host.provider_events() == [], host.provider_events()
    assert host.receipts() == [], host.receipts()


# --------------------------------------------------------------------------
# public deck: the real deck's own tab script, executed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("verb", ["init", "operator", "partner"])
def test_public_deck_tab_script_reaches_the_provider_from_an_installed_generation(
    tmp_path: Path, packaged_generation: Path, verb: str
) -> None:
    """The Founder's declaration through the REAL deck of a packaged
    generation, from cwd ``/`` with ``--repo``: the child creates the
    repository's workspace, hangs the provider tab on it with the script the
    real composer produced, and that script -- run where the Frame would run
    it -- reaches the provider once."""
    host = PaneHost(tmp_path, packaged_generation)
    scene = DeckScene(
        tmp_path, packaged_generation, live=[FOREIGN_LIVE], project="declared"
    )
    scene.root.rmdir()
    scene.root = host.repo
    _write(scene.stubs / "codex", PROVIDER_STUB)
    result = scene.run(
        verb,
        "codex",
        "--repo",
        str(host.repo),
        "--token-budget",
        "unmetered",
        "--prompt",
        PROMPT,
        cwd=Path("/"),
        tty=True,
        terminal_entry=True,
        extra_env={
            "VIBECRAFTED_RUNTIME_ROOT": str(packaged_generation),
            "VIBECRAFTED_PYTHON": str(packaged_generation / "bin" / "python3"),
            "VIBECRAFTED_RUNTIME_BIN": str(packaged_generation / "bin"),
            "VC_FIXTURE_EVENTS": str(host.events),
        },
    )
    calls = scene.calls()

    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert len(_creates(calls)) == 1 and len(_attaches(calls)) == 1, calls
    tabs = _new_tabs(calls)
    assert len(tabs) == 1, calls
    assert host.provider_events() == [], "the deck itself started the provider"
    argv = tabs[0]["argv"]
    script = Path(argv[argv.index("--") + 1])
    text = script.read_text(encoding="utf-8")
    assert (
        f"{packaged_generation}/bin/python3 -m vibecrafted_core.spawn interactive-launch codex"
        in text
    ), text
    assert "PYTHONPATH" not in text, text
    assert "--token-budget unmetered" in text and f"/vc-{verb}" in text, text
    assert f"{packaged_generation}/python/bin/{RAW_INTERPRETER}" not in text, text

    pane = host.run_pane(script)

    _assert_reached_once(host, pane, host.repo)
    assert "PROVIDER FIXTURE reached" in pane.stdout, pane.stdout
