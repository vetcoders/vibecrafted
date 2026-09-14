"""Render relocatable launchers from the package's canonical script manifest."""

from __future__ import annotations

import argparse
import re
import shlex
import stat
from pathlib import Path

import tomllib

_LAUNCHER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ENTRYPOINT_TARGET = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_.]*\Z")
_DISPATCH = (
    "from importlib import import_module; import sys; "
    "target = sys.argv.pop(1); launcher = sys.argv.pop(1); "
    "sys.argv[0] = launcher; module, attribute = target.split(':', 1); "
    "raise SystemExit(getattr(import_module(module), attribute)())"
)


def render_launchers(pyproject: Path, bin_dir: Path) -> list[str]:
    """Create every missing ``project.scripts`` launcher in ``bin_dir``."""

    with pyproject.open("rb") as handle:
        scripts = tomllib.load(handle).get("project", {}).get("scripts", {})
    if not isinstance(scripts, dict) or not scripts:
        raise ValueError(f"{pyproject} has no [project.scripts] manifest")

    bin_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for name, target in sorted(scripts.items()):
        if not isinstance(name, str) or not _LAUNCHER_NAME.fullmatch(name):
            raise ValueError(f"invalid launcher name in {pyproject}: {name!r}")
        if not isinstance(target, str) or not _ENTRYPOINT_TARGET.fullmatch(target):
            raise ValueError(f"invalid launcher target for {name}: {target!r}")

        destination = bin_dir / name
        if destination.exists() or destination.is_symlink():
            continue

        dispatch_command = (
            'exec "$bin_dir/python3" -c '
            f'{shlex.quote(_DISPATCH)} "$target" "$launcher" "$@"'
        )
        payload = "\n".join(
            (
                "#!/bin/bash",
                "set -euo pipefail",
                'bin_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
                f"target={shlex.quote(target)}",
                # The launcher value rides into the process argv, and the deck's
                # identity guard matches the DECLARED absolute path against that
                # argv. A bare name here made every python-entrypoint sidecar
                # (vc-guardian) fail capture-identity through any wrapper chain.
                f'launcher="${{VIBECRAFTED_DECLARED_LAUNCHER:-$bin_dir/{name}}}"',
                dispatch_command,
                "",
            )
        )
        destination.write_text(payload, encoding="utf-8")
        destination.chmod(
            destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        created.append(name)
    return created


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, required=True)
    args = parser.parse_args()
    created = render_launchers(args.pyproject, args.bin_dir)
    print(f"rendered {len(created)} Python entrypoint launcher(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
