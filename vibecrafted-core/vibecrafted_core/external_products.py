"""Read-only resolution of independently released terminal and multiplexer products."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# This file is also called by the bootstrap installer before package deps exist.
terminal_host = None
if sys.platform != "win32":
    _spec = importlib.util.spec_from_file_location(
        "_external_terminal_host", Path(__file__).with_name("terminal_host.py")
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("cannot load terminal admission owner")
    terminal_host = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = terminal_host
    _spec.loader.exec_module(terminal_host)

TEAM_ID = "MW223P3NPX"
FRAME_MIN = (4, 5, 1)
FRAME_MAX = (5, 0, 0)
TERMINAL_HINT = "Install vc-terminal >=0.18.0 in /Applications/vc-terminal.app from https://github.com/vetcoders/vc-terminal/releases"
FRAME_HINT = "Install vc-frame >=4.5.1,<5.0.0 on PATH from https://github.com/vetcoders/vc-frame/releases"
PRODUCTS = {
    "vc-terminal": {
        "id": "io.vetcoders.vc-terminal",
        "min_version": "0.18.0",
        "team_id": TEAM_ID,
        "resolve": "/Applications/vc-terminal.app",
        "install_hint": TERMINAL_HINT,
    },
    "vc-frame": {
        "id": "io.vetcoders.vc-frame",
        "min_version": "4.5.1",
        "max_version_exclusive": "5.0.0",
        "resolve": "PATH",
        "install_hint": FRAME_HINT,
    },
}


class ProductUnavailable(RuntimeError):
    """A missing or incompatible external product; resolution never downloads it."""


def resolve_terminal() -> Path:
    if terminal_host is None:
        raise ProductUnavailable(f"vc-terminal.app requires macOS. {TERMINAL_HINT}")
    path = Path(terminal_host.STANDALONE_HOST)
    try:
        for node in (path, *path.parents[:4]):
            if node.is_symlink():
                raise ProductUnavailable(f"symlink in terminal product path: {node}")
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ProductUnavailable(f"terminal executable missing: {path}")
        terminal_host.require_standalone_bundle_metadata(str(path))
        for target in (path, path.parents[2]):
            identity = terminal_host.static_code_identity(str(target))
            if identity.adhoc or identity.team != TEAM_ID:
                raise ProductUnavailable(
                    f"terminal must be Developer ID signed by {TEAM_ID}: {target}"
                )
    except (OSError, terminal_host.Refusal, ProductUnavailable) as exc:
        raise ProductUnavailable(f"{exc}. {TERMINAL_HINT}") from exc
    return path


def resolve_frame(
    *, search_path: str | None = None, runtime: Path | None = None
) -> Path:
    """Choose the first external PATH command; an incompatible first hit is final."""
    runtime = (runtime or Path(__file__).resolve().parents[2]).resolve()
    for directory in (
        os.environ.get("PATH", "") if search_path is None else search_path
    ).split(os.pathsep):
        if not directory or not Path(directory).is_absolute():
            continue
        candidate = Path(directory) / "vc-frame"
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            physical = candidate.resolve()
            if physical.is_relative_to(runtime):
                continue
            # Ignore old runtime launchers to prevent recursion or stale engines.
            with physical.open("rb") as stream:
                prefix = stream.read(4096)
        except (OSError, RuntimeError) as exc:
            raise ProductUnavailable(
                f"cannot inspect vc-frame at {candidate}: {exc}. {FRAME_HINT}"
            ) from exc
        if (
            re.search(rb"(?m)^(?:export )?VIBECRAFTED_RUNTIME_ROOT=", prefix)
            or b"vc-frame-product-entry.sh" in prefix
        ):
            continue
        try:
            result = subprocess.run(
                [str(candidate), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            match = re.fullmatch(
                r"vc-frame\s+(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?\s*",
                result.stdout,
            )
            version = tuple(map(int, match.groups())) if match else None
            if (
                result.returncode != 0
                or version is None
                or not FRAME_MIN <= version < FRAME_MAX
            ):
                raise ProductUnavailable(
                    f"incompatible vc-frame at {candidate}: {result.stdout.strip()!r}"
                )
        except (OSError, subprocess.TimeoutExpired, ProductUnavailable) as exc:
            raise ProductUnavailable(f"{exc}. {FRAME_HINT}") from exc
        return physical
    raise ProductUnavailable(f"vc-frame missing from PATH. {FRAME_HINT}")


def product_status() -> dict[str, dict[str, str]]:
    result = {}
    for name, resolver in (
        ("vc-terminal", resolve_terminal),
        ("vc-frame", resolve_frame),
    ):
        try:
            result[name] = {"status": "available", "path": str(resolver())}
        except ProductUnavailable as exc:
            result[name] = {"status": "unavailable", "message": str(exc)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("product", choices=("terminal", "frame", "status"))
    args = parser.parse_args()
    try:
        if args.product == "status":
            print(json.dumps(product_status(), sort_keys=True))
        else:
            print(resolve_terminal() if args.product == "terminal" else resolve_frame())
        return 0
    except ProductUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 69


if __name__ == "__main__":
    raise SystemExit(main())
