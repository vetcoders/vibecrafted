"""A fake installed generation for shell tests that reach vc-frame / vc-terminal.

The shell facade no longer discovers its engines on PATH or through
``VIBECRAFTED_RUNTIME_BIN`` / ``VIBECRAFTED_RUNTIME_HOME``:

* ``vc-frame`` resolves only from the generation that loaded the shell --
  ``<generation>/bin/vc-frame`` as entry and ``<generation>/libexec/vc-frame``
  as engine, both real, executable, non-symlink files
  (runtime/shell/lib/vc_frame.sh ``_vetcoders_vc_frame_bin``, 3d9da4dc);
* ``vc-terminal`` is its strict twin under the same generation
  (``_vetcoders_vc_terminal_bin``, 0e5e6b03), and a caller without a PTY is
  handed that terminal before any interactive entry runs;
* the only other route is developer mode: ``VIBECRAFTED_PREFER_REPO_VC_FRAME=1``
  on a directly sourced Git checkout, which then honours
  ``VIBECRAFTED_VC_FRAME_BIN`` and reads Frame assets from the checkout's
  ``vibecrafted-core/vibecrafted_core/config/vc-frame``
  (``_vetcoders_vc_frame_developer_mode`` and frontier.sh
  ``_vetcoders_vc_frame_config_dir``);
* outside developer mode the Frame config home is pinned to
  ``$HOME/.config/vibecrafted/vc-frame`` (frontier.sh
  ``_vetcoders_pin_vc_frame_config_dir``).

The loaded generation is captured at source time
(``_vetcoders_vc_frame_loaded_root``). Tests that model an installed generation
point that pin at the fixture right after sourcing the facade -- the same seam
tests/tui/test_terminal_entry_escalation.py uses -- so every product resolver
answers from ``tmp_path`` and never from the host.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import shlex
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_VC_FRAME_CONFIG = (
    REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "config" / "vc-frame"
)
PRIMARY_SHELL = REPO_ROOT / "config" / "alacritty" / "launch-primary-shell.zsh"

# Real, executable, non-symlink engine. Its behaviour is irrelevant: the shell
# resolvers only prove it exists; every call goes through bin/<entry>.
_ENGINE = "#!/bin/sh\nexit 0\n"


def write_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_generation(root: Path, *, front_doors: tuple[str, ...] = ()) -> Path:
    """Create ``<root>/generation`` with an executable ``libexec/vc-frame``.

    The caller installs its own ``bin/vc-frame`` stub (any of the recording
    stubs the test files already own) into ``generation / "bin"``.
    """
    generation = root / "generation"
    write_executable(generation / "libexec" / "vc-frame", _ENGINE)
    (generation / "bin").mkdir(parents=True, exist_ok=True)
    for verb in front_doors:
        write_executable(generation / "bin" / verb, "#!/bin/sh\nexit 0\n")
    return generation


def install_vc_terminal(generation: Path, capture: Path) -> Path:
    """Strict vc-terminal entry + engine; the entry records the launch."""
    write_executable(generation / "libexec" / "vc-terminal", _ENGINE)
    return write_executable(
        generation / "bin" / "vc-terminal",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(capture)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(),"
        " 'boundary': os.environ.get('VIBECRAFTED_TERMINAL_ENTRY', ''),"
        " 'created': os.environ.get('VIBECRAFTED_START_CREATED_SESSION', '')}))\n",
    )


def loaded_root_prelude(generation: Path) -> str:
    """Shell statement pinning the loaded generation after ``source``."""
    return f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(generation))}"


def owned_terminal_child_env(generation: Path) -> dict[str, str]:
    """Env of the child a product-owned terminal launch created.

    The boundary is honoured only with the owner set to the generation's own
    ``bin/vibecrafted`` front door (``_vetcoders_has_owned_vc_terminal_entry``);
    a bare ``VIBECRAFTED_TERMINAL_ENTRY=1`` is inherited ancestry, not a PTY.
    """
    front_door = generation / "bin" / "vibecrafted"
    if not front_door.exists():
        write_executable(front_door, "#!/bin/sh\nexit 0\n")
    return {
        "VIBECRAFTED_TERMINAL_ENTRY": "1",
        "VIBECRAFTED_TERMINAL_ENTRY_OWNER": str(front_door),
    }


def product_vc_frame_config_dir(home: Path) -> Path:
    return home / ".config" / "vibecrafted" / "vc-frame"


def install_product_vc_frame_config(
    home: Path, *, layouts: tuple[str, ...] = ("operator",)
) -> Path:
    """Installer-owned Frame config at the pinned product home.

    Copies the shipped files so a layout path in an assertion names real
    product content, never a placeholder the product would reject.
    """
    config_dir = product_vc_frame_config_dir(home)
    (config_dir / "layouts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHIPPED_VC_FRAME_CONFIG / "config.kdl", config_dir / "config.kdl")
    for name in layouts:
        shutil.copyfile(
            SHIPPED_VC_FRAME_CONFIG / "layouts" / f"{name}.kdl",
            config_dir / "layouts" / f"{name}.kdl",
        )
    return config_dir


def install_primary_shell_launcher(home: Path) -> Path:
    """The one physical vc-terminal launcher: $HOME/.config/vibecrafted/vc-terminal/."""
    return write_executable(
        home / ".config" / "vibecrafted" / "vc-terminal" / "launch-primary-shell.zsh",
        PRIMARY_SHELL.read_text(encoding="utf-8"),
    )


def developer_mode_env(vc_frame_bin: Path) -> dict[str, str]:
    """Developer mode on this Git checkout, with an explicit engine entry."""
    return {
        "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
        "VIBECRAFTED_VC_FRAME_BIN": str(vc_frame_bin),
    }


def read_terminal_launch(capture: Path, *, timeout: float = 10.0) -> dict | None:
    """The terminal host is detached on purpose; give its record time to land."""
    deadline = time.monotonic() + timeout
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not capture.exists():
        return None
    return json.loads(capture.read_text(encoding="utf-8"))
