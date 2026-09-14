"""Product configuration has one home: ``~/.config/vibecrafted``.

Founder intent (2026-09-14): configuration is read from and written to
``${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/**`` only. Shipped production
sources must not read, write, symlink into, or fall back to the sibling config
directories ``vetcoders``, ``vc-frame``, ``vc-terminal`` or ``zellij``. A
reference is legal only as an explicit refusal, cleanup or diagnostic, and every
such line is named in ``ALLOWED`` with its reason.

``tests/`` and ``docs/`` are outside the scan: tests pin refusals with decoy
paths and docs describe them. ``vibecrafted-vm/`` is excluded pending a decision:
its onboarding wizard and compose file still mount the retired vetcoders
directory into the container, and that open work is tracked outside this guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_ROOTS = (
    "scripts",
    "vibecrafted-core/vibecrafted_core",
    "config",
    "bin",
    "install.toml",
)
SKIP_DIRS = frozenset(
    {
        "__pycache__",
        ".loctree",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        "target",
    }
)

_SIBLING = r"(?:vetcoders|vc-frame|vc-terminal|zellij)\b"
SIBLING_CONFIG = re.compile(
    "|".join(
        (
            # ~/.config/vetcoders, ${XDG_CONFIG_HOME:-$HOME/.config}/vc-frame
            rf"\.config\}}?/{_SIBLING}",
            # Python: home / ".config" / "vetcoders"
            rf"\"\.config\"\s*/\s*\"{_SIBLING}",
            # Python: xdg_config_home() / "vetcoders", config_root / "vc-frame"
            rf"(?:config_home|config_root|config_base)(?:\(\))?\s*/\s*\"{_SIBLING}",
            # Shell: $config_base/vetcoders, ${CONFIG_HOME}/zellij
            (
                r"\$\{?(?:xdg_config_home|config_home|config_root|config_base|"
                rf"remote_config_root)\}}?/{_SIBLING}"
            ),
        )
    ),
    re.IGNORECASE,
)

#: (repo-relative file, substring of the one allowed line, reason)
ALLOWED: tuple[tuple[str, str, str], ...] = (
    (
        "scripts/aicx-sync.sh",
        'RETIRED_CONFIG_FILE="$CONFIG_HOME/vetcoders/aicx-sync.toml"',
        "names the retired standalone file only to print the one 'no longer read' notice",
    ),
    (
        "vibecrafted-core/vibecrafted_core/memex_client.py",
        'retired = config_home / "vetcoders" / "memex.toml"',
        "names the retired standalone file only to log the one 'no longer read' notice",
    ),
    (
        "scripts/vetcoders_install.py",
        '[[ -r "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh" ]]',
        "rc-file cleanup matcher: recognises the retired source line so uninstall and doctor --fix-rc strip it",
    ),
    (
        "scripts/vetcoders_install.py",
        'config_root / "vc-frame",',
        "uninstall cleanup: removes a leftover framework vc-frame tree",
    ),
    (
        "scripts/vetcoders_install.py",
        'config_root / "vetcoders" / "frontier",',
        "uninstall cleanup: removes a leftover retired sidecar tree",
    ),
)


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if base.is_file():
            files.append(base)
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts):
                continue
            files.append(path)
    return files


def _sibling_config_lines() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in _scanned_files():
        data = path.read_bytes()
        if b"\0" in data:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if SIBLING_CONFIG.search(line):
                hits.append((relative, number, line.strip()))
    return hits


def test_shipped_sources_keep_configuration_in_the_one_config_home() -> None:
    unexpected: list[str] = []
    matched: set[int] = set()
    for relative, number, line in _sibling_config_lines():
        entry = next(
            (
                index
                for index, (path, needle, _reason) in enumerate(ALLOWED)
                if path == relative and needle in line
            ),
            None,
        )
        if entry is None:
            unexpected.append(f"{relative}:{number}: {line}")
        else:
            matched.add(entry)

    assert not unexpected, (
        "product configuration lives only under ~/.config/vibecrafted; move these "
        "reads/writes there, or allow-list an explicit refusal/cleanup/diagnostic "
        "line with its reason:\n" + "\n".join(unexpected)
    )
    stale = [
        f"{path}: {needle}"
        for index, (path, needle, _reason) in enumerate(ALLOWED)
        if index not in matched
    ]
    assert not stale, "allow-list entries match no line any more; remove them:\n" + (
        "\n".join(stale)
    )


def test_every_allowed_line_carries_a_reason() -> None:
    for path, needle, reason in ALLOWED:
        assert (REPO_ROOT / path).is_file(), path
        assert needle.strip() and reason.strip(), (path, needle)


@pytest.mark.parametrize(
    "line",
    [
        'DEFAULT_CONFIG_FILE="${HOME}/.config/vetcoders/aicx-sync.toml"',
        'frontier_root="${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/frontier"',
        'return home / ".config" / "vetcoders" / "frontier"',
        'config_dir = xdg_config_home() / "vetcoders"',
        'config_root / "vc-frame",',
        'target_dir="$config_base/vetcoders"',
        'remote_helper_dir="$remote_config_root/vetcoders"',
        'export ZELLIJ_CONFIG_DIR="$HOME/.config/zellij"',
        'policy="$HOME/.config/vc-terminal/alacritty.toml"',
    ],
)
def test_sibling_config_pattern_catches_known_offender_shapes(line: str) -> None:
    assert SIBLING_CONFIG.search(line)


@pytest.mark.parametrize(
    "line",
    [
        'export VC_FRAME_CONFIG_DIR="$HOME/.config/vibecrafted/vc-frame"',
        'return root / ".config" / "vibecrafted" / "vc-frame"',
        '"$repo_root/vibecrafted-core/vibecrafted_core/config/vc-frame"',
        'product / "vc-terminal" / "vc-terminal.toml"',
        '"${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/shell/vc-skills.sh"',
    ],
)
def test_sibling_config_pattern_accepts_the_product_config_home(line: str) -> None:
    assert not SIBLING_CONFIG.search(line)
