"""Deck + shell twin of the execution-controls contract.

* the shell skill contract parses ``--sandbox`` with the same words as core
  and the shell helpers refuse ``--permissions`` / ``--sandbox`` instead of
  accepting-and-ignoring them;
* the deck routes any skill launch carrying a control to the core launcher
  (proven through the real deck subprocess, fake providers, isolated home);
* ``vibecrafted tui --help`` documents ``--repo``.

No real provider, vc-frame session, or operator home is ever touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
FACADE = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)


def _shell(
    script: str, *, cwd: Path, home: Path, shell: str = "bash"
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    env["HOME"] = str(home)
    env["VIBECRAFTED_HOME"] = str(home / ".vibecrafted")
    prelude = f'source "{FACADE}" || exit 97\n'
    argv = (
        ["zsh", "-f", "-c", prelude + script]
        if shell == "zsh"
        else ["bash", "--noprofile", "--norc", "-c", prelude + script]
    )
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".vibecrafted").mkdir(parents=True)
    return path


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_shell_contract_parses_sandbox_words_like_core(
    tmp_path: Path, home: Path, shell: str
) -> None:
    script = """
_vetcoders_parse_skill_contract --sandbox --permissions auto --prompt go || exit $?
printf 'sandbox=%s perms=%s prompt=%s\\n' "$_vetcoders_contract_sandbox" "$_vetcoders_contract_permissions" "$_vetcoders_contract_prompt"
_vetcoders_parse_skill_contract --sandbox false -p again || exit $?
printf 'sandbox2=%s\\n' "$_vetcoders_contract_sandbox"
_vetcoders_parse_skill_contract --sandbox=off --model m -p x || exit $?
printf 'sandbox3=%s model=%s\\n' "$_vetcoders_contract_sandbox" "$_vetcoders_contract_model"
_vetcoders_parse_skill_contract -p plain || exit $?
printf 'sandbox4=[%s] perms4=[%s]\\n' "$_vetcoders_contract_sandbox" "$_vetcoders_contract_permissions"
"""
    result = _shell(script, cwd=tmp_path, home=home, shell=shell)
    assert result.returncode == 0, result.stderr
    assert "sandbox=true perms=auto prompt=go" in result.stdout
    assert "sandbox2=false" in result.stdout
    assert "sandbox3=false model=m" in result.stdout
    assert "sandbox4=[] perms4=[]" in result.stdout

    bad = _shell(
        "_vetcoders_parse_skill_contract --sandbox=maybe -p x",
        cwd=tmp_path,
        home=home,
        shell=shell,
    )
    assert bad.returncode != 0
    assert "--sandbox expects true or false, got: maybe" in bad.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_argv_rewrite_steps_over_the_sandbox_word(
    tmp_path: Path, home: Path, shell: str
) -> None:
    script = """
_vetcoders_rewrite_contract_root_argv /abs/root claude --sandbox true --repo child -p go
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
echo ---
_vetcoders_rewrite_contract_root_argv /abs/root claude --sandbox --root child
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
echo ---
_vetcoders_rewrite_contract_root_argv /abs/root claude --permissions auto --sandbox=false --repo=child -p go
printf '%s\\n' "${_vetcoders_contract_argv[@]}"
"""
    result = _shell(script, cwd=tmp_path, home=home, shell=shell)
    assert result.returncode == 0, result.stderr
    blocks = [block.strip().splitlines() for block in result.stdout.split("---\n")]
    assert blocks[0] == [
        "claude",
        "--sandbox",
        "true",
        "--repo",
        "/abs/root",
        "-p",
        "go",
    ]
    # A word-less --sandbox must not swallow the following --root flag.
    assert blocks[1] == ["claude", "--sandbox", "--root", "/abs/root"]
    assert blocks[2] == [
        "claude",
        "--permissions",
        "auto",
        "--sandbox=false",
        "--repo=/abs/root",
        "-p",
        "go",
    ]


def test_shell_skill_helper_refuses_controls_instead_of_ignoring_them(
    tmp_path: Path, home: Path
) -> None:
    for flags in ("--permissions auto", "--sandbox true", "--sandbox=false"):
        result = _shell(
            f"_vetcoders_skill claude audit {flags} --prompt go",
            cwd=tmp_path,
            home=home,
        )
        assert result.returncode != 0, flags
        assert "honoured by the core launcher only" in result.stderr, flags
        assert "vibecrafted audit claude --permissions" in result.stderr, flags


def test_interactive_entrypoints_refuse_sandbox(tmp_path: Path, home: Path) -> None:
    for verb in ("init", "operator", "partner"):
        result = _shell(
            f"_vetcoders_skill_{verb} claude --sandbox true",
            cwd=tmp_path,
            home=home,
        )
        assert result.returncode != 0, verb
        assert f"--sandbox is not carried into vibecrafted {verb}" in result.stderr


class _DeckWorld:
    """Real deck subprocess: fake providers + fake vc-frame, isolated home."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.vibecrafted_home = self.home / ".vibecrafted"
        self.vibecrafted_home.mkdir(parents=True)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.outside = tmp_path / "no git here"
        self.outside.mkdir()
        self.repo = tmp_path / "repo with space"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.argv_file = tmp_path / "claude-argv.txt"
        self.capture = tmp_path / "vc-frame-args.txt"
        self._exe(
            self.bin / "vc-frame",
            "#!/usr/bin/env bash\n"
            'if [[ "${1:-}" == "ls" || "${1:-}" == "list-sessions" ]]; then\n'
            '  printf "operator-test (attached)\\n"; exit 0\nfi\n'
            '{ printf "%s\\n" "$@"; } > "$CAPTURE_FILE"\n',
        )
        self._exe(
            self.bin / "claude",
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$@" > {json.dumps(str(self.argv_file))}\n'
            "cat >/dev/null\n"
            'printf \'{"type":"assistant","message":"ok"}\\n\'\n'
            "exit 0\n",
        )
        for provider in ("codex", "grok", "cursor-agent", "agy", "junie"):
            self._exe(self.bin / provider, "#!/usr/bin/env bash\nexit 0\n")

    @staticmethod
    def _exe(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    def env(self) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
            and not key.startswith(("VIBECRAFTED_", "VC_FRAME", "ZELLIJ"))
        }
        env["HOME"] = str(self.home)
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["VIBECRAFTED_HOME"] = str(self.vibecrafted_home)
        env["VIBECRAFTED_RUNTIME_BIN"] = str(self.bin)
        env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
        env["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
        env["VIBECRAFTED_VC_FRAME_BIN"] = str(self.bin / "vc-frame")
        env["VIBECRAFTED_GUARD"] = "0"
        env["CAPTURE_FILE"] = str(self.capture)
        return env

    def deck(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(LAUNCHER), *args],
            cwd=self.outside,
            env=self.env(),
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def deck(tmp_path: Path) -> _DeckWorld:
    return _DeckWorld(tmp_path)


def test_deck_routes_a_shell_skill_with_controls_to_the_core_launcher(
    deck: _DeckWorld,
) -> None:
    """`audit` is a shell-helper skill; with a control it must go to core."""
    result = deck.deck(
        "audit",
        "claude",
        "--permissions",
        "auto",
        "--sandbox",
        "true",
        "--repo",
        str(deck.repo),
        "--prompt",
        "prove it",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["accepted"] is True
    assert receipt["skill"] == "audit"
    controls = receipt["execution_controls"]
    assert controls["permissions_effective"] == "auto"
    assert controls["sandbox_effective"] == "enabled"
    assert receipt["worker_command"][-4:] == [
        "--permission-mode",
        "auto",
        "--settings",
        '{"sandbox":{"enabled":true}}',
    ]
    # No vc-frame pane was opened: the shell helper never ran.
    assert not deck.capture.exists()
    deadline = time.monotonic() + 20
    while not deck.argv_file.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    argv = deck.argv_file.read_text(encoding="utf-8").splitlines()
    assert argv[-4:] == [
        "--permission-mode",
        "auto",
        "--settings",
        '{"sandbox":{"enabled":true}}',
    ]


def test_deck_refuses_unenforceable_controls_before_launch(deck: _DeckWorld) -> None:
    result = deck.deck(
        "audit",
        "junie",
        "--sandbox",
        "true",
        "--repo",
        str(deck.repo),
        "--prompt",
        "x",
        "--json",
    )
    assert result.returncode == 2, result.stdout
    assert "junie 26.8.31 exposes no sandbox control" in result.stderr
    assert result.stdout.strip() == ""
    runtime_runs = deck.vibecrafted_home / "control_plane" / "runtime_runs"
    assert not runtime_runs.exists() or not any(runtime_runs.iterdir())


def test_tui_help_documents_repo_selector(deck: _DeckWorld) -> None:
    result = deck.deck("tui", "--help")
    assert result.returncode == 0, result.stderr
    assert "--repo <path>" in result.stdout
    assert "--root: legacy spelling" in result.stdout
