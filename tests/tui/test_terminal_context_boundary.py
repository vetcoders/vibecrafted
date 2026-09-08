"""The native vc-terminal wrapper is an attachment-context boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ENTRY = REPO_ROOT / "scripts" / "vc-terminal-product-entry.sh"
ATTACHMENT_MARKERS = (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
)


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def _generation(tmp_path: Path) -> tuple[Path, Path]:
    generation = tmp_path / "generation"
    wrapper = generation / "bin" / "vc-terminal"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(PRODUCT_ENTRY.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    capture = tmp_path / "host-capture.json"
    _write(
        generation / "libexec" / "vc-terminal",
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "Path = __import__('pathlib').Path\n"
        "Path(os.environ['TEST_VC_TERMINAL_CAPTURE']).write_text(json.dumps({\n"
        "    'argv': sys.argv[1:],\n"
        "    'markers': {key: os.environ.get(key) for key in "
        + repr(ATTACHMENT_MARKERS)
        + "},\n"
        "}))\n",
        0o755,
    )
    return wrapper, capture


@pytest.mark.parametrize("inherited", [False, True], ids=["detached", "attached-parent"])
def test_new_terminal_drops_inherited_attachment_context_but_preserves_root_and_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inherited: bool
) -> None:
    wrapper, capture = _generation(tmp_path)
    home = tmp_path / "home"
    project = tmp_path / "explicit-project"
    project.mkdir()
    config = home / ".config" / "vibecrafted" / "vc-terminal" / "vc-terminal.toml"
    _write(config, "[window]\ntitle = 'VC Terminal'\n")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TEST_VC_TERMINAL_CAPTURE", str(capture))
    inherited_values = {
        "VC_FRAME": "0",
        "VC_FRAME_PANE_ID": "2",
        "VC_FRAME_SESSION_NAME": "vibecrafted",
        "ZELLIJ": "0",
        "ZELLIJ_PANE_ID": "legacy-2",
        "ZELLIJ_SESSION_NAME": "legacy-vibecrafted",
    }
    for key, value in inherited_values.items():
        if inherited:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)

    result = subprocess.run(
        [
            str(wrapper),
            "--working-directory",
            str(project),
            "-e",
            "vc-start",
            "--root",
            str(project),
            "--provider",
            "codex",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["markers"] == {key: None for key in ATTACHMENT_MARKERS}
    assert observed["argv"] == [
        "--config-file",
        str(config),
        "--working-directory",
        str(project),
        "-e",
        "vc-start",
        "--root",
        str(project),
        "--provider",
        "codex",
    ]
    if inherited:
        assert {key: os.environ[key] for key in ATTACHMENT_MARKERS} == inherited_values
