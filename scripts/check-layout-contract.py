#!/usr/bin/env python3
"""Fail-closed hash and host/guest checks for shipped vc-frame layouts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAYOUTS = (
    REPO_ROOT / "vibecrafted-core/vibecrafted_core/config/vc-frame/layouts"
)
DEFAULT_LOCK = DEFAULT_LAYOUTS.parent / "layouts.sha256.json"
DEFAULT_CONFIG = DEFAULT_LAYOUTS.parent / "config.kdl"


def _code(text: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def _block(text: str, declaration: str) -> str:
    start = text.index(declaration)
    opening = text.index("{", start)
    depth = 0
    for offset, character in enumerate(text[opening:], start=opening):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[opening + 1 : offset]
    raise ValueError(f"unterminated KDL block: {declaration}")


def _hashes(layouts_dir: Path) -> dict[str, str]:
    layouts = sorted(layouts_dir.glob("*.kdl"))
    if not layouts:
        raise ValueError(f"no layouts found under {layouts_dir}")
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in layouts
    }


def _guard_host_guest(layouts_dir: Path, config_path: Path) -> None:
    host = _code((layouts_dir / "host.kdl").read_text(encoding="utf-8"))
    operator = _code((layouts_dir / "operator.kdl").read_text(encoding="utf-8"))
    config = _code(config_path.read_text(encoding="utf-8"))

    host_layer = _block(host, "session_layer")
    host_workspace = _block(host, 'tab name="Workspace"')
    if host.count("frame_host true") != 1:
        raise ValueError("host.kdl must declare exactly one frame_host owner")
    if "frame_host true" in host_layer:
        raise ValueError("host.kdl session_layer is cloned and cannot own frame_host")
    if "frame_host true" not in host_workspace:
        raise ValueError("host.kdl Workspace tab must own frame_host")
    if host_workspace.count("workspace_surface true") != 1:
        raise ValueError("host.kdl must expose exactly one guest workspace surface")

    operator_layer = _block(operator, "session_layer")
    operator_content = operator.replace(operator_layer, "", 1)
    if operator.count("frame_host true") != 1:
        raise ValueError("operator.kdl must declare exactly one fallback host owner")
    if "frame_host true" not in operator_layer:
        raise ValueError("operator.kdl fallback host owner must stay in session_layer")
    if "frame_host true" in operator_content:
        raise ValueError("operator.kdl guest content cannot retain host ownership")
    if 'default_layout "host"' not in config:
        raise ValueError("config.kdl must boot the dedicated host layout")

    for path in sorted(layouts_dir.glob("*.kdl")):
        if path.name in {"host.kdl", "operator.kdl"}:
            continue
        if "frame_host true" in _code(path.read_text(encoding="utf-8")):
            raise ValueError(f"{path.name} cannot claim frame_host ownership")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--layouts-dir", type=Path, default=DEFAULT_LAYOUTS)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    observed = _hashes(args.layouts_dir)
    _guard_host_guest(args.layouts_dir, args.config)
    payload = {
        "schema": "io.vetcoders.vibecrafted.layout-hashes.v1",
        "algorithm": "sha256",
        "layouts": observed,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.update:
        args.lock.write_text(rendered, encoding="utf-8")

    if not args.lock.is_file():
        raise SystemExit(
            f"layout hash lock missing: {args.lock}; run make layouts-check UPDATE=1"
        )
    expected = json.loads(args.lock.read_text(encoding="utf-8"))
    if expected != payload:
        raise SystemExit(
            "layout hashes drifted; inspect the host/guest change, then run make layouts-check UPDATE=1"
        )
    print(f"layout contract OK ({len(observed)} locked layouts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
