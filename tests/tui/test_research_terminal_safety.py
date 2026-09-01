from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)

sys.path.insert(0, str(REPO_ROOT / "vibecrafted-core"))

from vibecrafted_core.wrappers import _launcher_paths

BLOCKING_VC_FRAME_VERBS = ("attach", "--new-session-with-layout", "switch-session")


def _stub_vc_frame(bin_dir: Path, log: Path) -> None:
    # Records every invocation; reports no sessions. Lets the test prove that
    # vc-research never starts a blocking mux client in the calling shell.
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("vc-frame", "vc-frame"):
        stub = bin_dir / name
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{log}"\n'
            'case "${1:-}" in list-sessions|ls) exit 0 ;; esac\n'
            "exit 0\n",
            encoding="utf-8",
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


def test_vc_research_terminal_without_session_degrades_to_headless(
    tmp_path: Path,
) -> None:
    # VC-vbcr-stabilize-033 (operator-reported terminal kill): with terminal
    # runtime and no live vc_frame session, the research swarm used to hand the
    # calling terminal to a blocking vc_frame client. It must degrade to
    # headless, leave the shell alive, and never invoke a blocking vc_frame
    # verb. Since 7e83fb60 the public `vc-research` is a deck passthrough, so
    # the degrade guarantee is exercised on its owner: the legacy swarm helper
    # `_vetcoders_research` (still shipped, still used by internal callers).
    root = tmp_path / "repo"
    root.mkdir()
    crafted_home = tmp_path / "home" / ".vibecrafted"
    stub_bin = tmp_path / "stub-bin"
    vc_frame_log = tmp_path / "vc_frame-invocations.log"
    _stub_vc_frame(stub_bin, vc_frame_log)

    env = os.environ.copy()
    env["VIBECRAFTED_HOME"] = str(crafted_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["VETCODERS_SPAWN_RUNTIME"] = "terminal"
    # Hermetic vc-frame: the shell helpers re-prepend the bundled runtime bin
    # ahead of PATH (_vetcoders_path_with_bundled_bin_priority), so on a host
    # with an installed runtime and a LIVE vc-frame session the real binary
    # would beat the stub and report a session — no degradation, red test.
    empty_runtime_bin = tmp_path / "empty-runtime-bin"
    empty_runtime_bin.mkdir()
    env["VIBECRAFTED_RUNTIME_BIN"] = str(empty_runtime_bin)
    for ambient in (
        "VC_FRAME",
        "VC_FRAME_PANE_ID",
        "VC_FRAME_SESSION_NAME",
        "VIBECRAFTED_OPERATOR_SESSION",
        "VIBECRAFTED_RUN_ID",
        "VIBECRAFTED_RUN_LOCK",
    ):
        env.pop(ambient, None)

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                f'export PATH="{stub_bin}:$PATH"; '
                f'source "{HELPER_SCRIPT}"; '
                f'source "{REPO_ROOT}/vibecrafted-core/vibecrafted_core/runtime/vc-research/shell/research.sh"; '
                f'_vetcoders_research --root "{root}" --prompt "probe terminal safety"; '
                'rc=$?; echo "SHELL-ALIVE rc=$rc"; exit $rc'
            ),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert "SHELL-ALIVE rc=0" in result.stdout, (
        f"calling shell did not survive: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "Degrading to headless" in result.stderr
    assert "Research swarm prepared" in result.stdout
    assert "Launchers:" in result.stdout

    if vc_frame_log.exists():
        calls = vc_frame_log.read_text(encoding="utf-8")
        for verb in BLOCKING_VC_FRAME_VERBS:
            assert verb not in calls, f"blocking vc_frame verb invoked: {verb}\n{calls}"


def test_launcher_paths_recognise_every_supported_agent() -> None:
    # The old regex matched only a subset of agents while the configured swarm
    # can include every supported lane — the venv vc-research entrypoint could never
    # collect its own launchers and always exited 1 before spawning.
    output = "Launchers:\n  claude: /tmp/claude_launch.sh\n  codex: /tmp/codex_launch.sh\n  junie: /tmp/junie_launch.sh\n  agy: /tmp/agy_launch.sh\n  grok: /tmp/grok_launch.sh"
    launchers = _launcher_paths(output)
    assert sorted(launchers) == ["agy", "claude", "codex", "grok", "junie"]
    assert launchers["junie"] == Path("/tmp/junie_launch.sh")
