"""Render relocatable launchers from the package's canonical script manifest."""

from __future__ import annotations

import argparse
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

_LAUNCHER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ENTRYPOINT_TARGET = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_.]*\Z")
_DISPATCH = (
    "from importlib import import_module; import sys; "
    "target = sys.argv.pop(1); launcher = sys.argv.pop(1); "
    "sys.argv[0] = launcher; module, attribute = target.split(':', 1); "
    "raise SystemExit(getattr(import_module(module), attribute)())"
)


def render_launchers(
    pyproject: Path,
    bin_dir: Path,
    *,
    windows: bool = False,
    native_library: str | None = None,
) -> list[str]:
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

        destination = bin_dir / (f"{name}.cmd" if windows else name)
        if native_library and (
            name.startswith(("vc-", "vibecrafted-"))
            or name == "verify-vibecrafted-walkaround"
        ):
            render_native_host(destination, target, native_library)
            created.append(name)
            continue
        if destination.exists() or destination.is_symlink():
            continue

        if windows:
            payload = "\r\n".join(
                (
                    "@echo off",
                    "setlocal EnableExtensions",
                    'set "bin_dir=%~dp0"',
                    f'set "target={target}"',
                    'set "VIBECRAFTED_DECLARED_LAUNCHER=%~f0"',
                    (
                        "if defined VIBECRAFTED_DECLARED_LAUNCHER_OVERRIDE set "
                        '"VIBECRAFTED_DECLARED_LAUNCHER=%VIBECRAFTED_DECLARED_LAUNCHER_OVERRIDE%"'
                    ),
                    'set "PYTHONIOENCODING=utf-8"',
                    'set "PYTHONUTF8=1"',
                    (
                        f'"%bin_dir%python.exe" -c "{_DISPATCH}" "%target%" '
                        '"%VIBECRAFTED_DECLARED_LAUNCHER%" %*'
                    ),
                    "",
                )
            )
            destination.write_text(payload, encoding="ascii")
        else:
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
    if native_library and "vc-server-supervisor" in scripts:
        render_native_host(
            bin_dir / "vc-build-lease",
            "vibecrafted_core.cache_lifecycle:main",
            native_library,
        )
        created.append("vc-build-lease")
        render_native_host(
            bin_dir / "vc-python",
            "vibecrafted_core.cli:main",
            native_library,
            python_only=True,
        )
        created.append("vc-python")
        # Retain the private interpreter door, but the executed process has an
        # honest owned product name. Never publish python3 on the host PATH.
        (bin_dir / "python3").write_text(
            '#!/bin/bash\nset -euo pipefail\nbin_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\nexec "$bin_dir/vc-python" "$@"\n'
        )
        (bin_dir / "python3").chmod(0o755)
    return created


def render_native_host(
    destination: Path, target: str, library: str, *, python_only: bool = False
) -> None:
    """Compile a small named host against the pinned payload's stable Python ABI."""
    if not _ENTRYPOINT_TARGET.fullmatch(target) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", library
    ):
        raise ValueError("invalid native host entrypoint or library")
    source = Path(__file__).with_name("native-python-entrypoint.c")
    link_arguments = ["-ldl"]
    if sys.platform == "darwin":
        pinned_library = destination.parent.parent / "python/lib" / library
        install_names = subprocess.run(
            ["otool", "-D", str(pinned_library)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()[1:]
        if install_names != [f"@rpath/{library}"]:
            raise ValueError(
                "native host requires the generation-local runpath library identity"
            )
        link_arguments = [
            str(pinned_library),
            "-Wl,-rpath,@executable_path/../python/lib",
        ]
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-D_GNU_SOURCE",
            "-Wall",
            "-Wextra",
            "-Werror",
            f'-DVC_ENTRYPOINT="{target}"',
            f'-DVC_PYTHON_LIB="{library}"',
            *(["-DVC_PYTHON_ONLY"] if python_only else []),
            str(source),
            "-o",
            str(destination),
            *link_arguments,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyproject", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, required=True)
    parser.add_argument(
        "--native-library", help="Pinned generation-local CPython library filename"
    )
    parser.add_argument(
        "--windows",
        action="store_true",
        help="Render .cmd launchers that exec generation python.exe",
    )
    args = parser.parse_args()
    created = render_launchers(
        args.pyproject,
        args.bin_dir,
        windows=args.windows,
        native_library=args.native_library,
    )
    print(f"rendered {len(created)} Python entrypoint launcher(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
