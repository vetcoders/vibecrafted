"""Stdlib-only vc-frame config materialization for unpublished runtimes.

This module deliberately has no package-relative imports.  The installer loads
the copy inside a candidate runtime before publishing that runtime, while the
installed package reuses the same functions for explicit config maintenance.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path

_PANE_ZSH_RE = re.compile(r'command="zsh"')
_DEFAULT_ZSH_RE = re.compile(r'default_shell\s+"zsh"')
_EXEC_ZSH_RE = re.compile(r"exec\s+(?:/bin/)?zsh\s+-l")
_COPY_PBCOPY_RE = re.compile(r'copy_command\s+"pbcopy"')
_PBCOPY_STDIN_RE = re.compile(r"\bpbcopy(?=\s*<)")
_EXECUTABLE_CONFIG_NAMES = frozenset(
    {"pane-python", "vc-agent-workshop.py", "vc-start-here.py"}
)


def resolve_pane_shell(path_env: str | None = None) -> str:
    """Select the first usable pane shell: zsh, $SHELL, bash, then sh."""
    path = path_env if path_env is not None else os.environ.get("PATH", "")
    if shutil.which("zsh", path=path):
        return "zsh"
    shell = os.environ.get("SHELL", "")
    if shell:
        base = Path(shell).name
        if base and shutil.which(base, path=path):
            return base
    if shutil.which("bash", path=path):
        return "bash"
    return "sh"


def resolve_clipboard_command(path_env: str | None = None) -> str | None:
    """Return the first host clipboard command available on PATH."""
    path = path_env if path_env is not None else os.environ.get("PATH", "")
    for executable, command in (
        ("pbcopy", "pbcopy"),
        ("wl-copy", "wl-copy"),
        ("xclip", "xclip -selection clipboard"),
        ("xsel", "xsel --clipboard --input"),
    ):
        if shutil.which(executable, path=path):
            return command
    return None


def substitute_host_commands(
    kdl_text: str, shell: str, clipboard_command: str | None
) -> str:
    """Adapt every shipped shell and clipboard entrypoint to the current host."""
    text = kdl_text
    if shell != "zsh":
        text = _PANE_ZSH_RE.sub(f'command="{shell}"', text)
        text = _DEFAULT_ZSH_RE.sub(f'default_shell "{shell}"', text)
        text = _EXEC_ZSH_RE.sub(f"exec {shell} -l", text)
    if clipboard_command != "pbcopy":
        if clipboard_command:
            text = _COPY_PBCOPY_RE.sub(f'copy_command "{clipboard_command}"', text)
            text = _PBCOPY_STDIN_RE.sub(clipboard_command, text)
        else:
            text = _COPY_PBCOPY_RE.sub(
                "// copy_command omitted: no host clipboard command", text
            )
            text = _PBCOPY_STDIN_RE.sub("cat >/dev/null", text)
    return text


def substitute_pane_shell(kdl_text: str, shell: str) -> str:
    """Backward-compatible shell-only adapter used by existing callers."""
    return substitute_host_commands(kdl_text, shell, "pbcopy")


def materialize_vc_frame_config(
    source: Path,
    destination: Path,
    *,
    pane_shell: str,
    clipboard_command: str | None,
) -> None:
    """Build a complete host-adapted config tree at an unpublished destination."""
    try:
        source_root = source.resolve(strict=True)
    except OSError as exc:
        raise OSError(f"vc-frame config source is unavailable: {source}") from exc
    if not source_root.is_dir():
        raise OSError(f"vc-frame config source is not a directory: {source}")
    if not (source_root / "config.kdl").is_file():
        raise OSError(f"vc-frame config source has no config.kdl: {source}")
    if destination.is_symlink():
        raise OSError(
            f"refusing to materialize vc-frame config through symlink: {destination}"
        )
    if destination.exists():
        if not destination.is_dir():
            raise OSError(
                f"vc-frame config destination is not a directory: {destination}"
            )
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=False)

    for root, directories, files in os.walk(source_root):
        directories.sort()
        files.sort()
        relative = Path(root).relative_to(source_root)
        output_dir = destination / relative
        output_dir.mkdir(parents=True, exist_ok=True)
        for name in files:
            source_file = Path(root) / name
            destination_file = output_dir / name
            if name.endswith(".kdl"):
                text = source_file.read_text(encoding="utf-8")
                destination_file.write_text(
                    substitute_host_commands(
                        text,
                        pane_shell,
                        clipboard_command,
                    ),
                    encoding="utf-8",
                )
            else:
                shutil.copy2(source_file, destination_file)
            if name.endswith(".sh") or name in _EXECUTABLE_CONFIG_NAMES:
                mode = destination_file.stat().st_mode
                destination_file.chmod(
                    mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                )
