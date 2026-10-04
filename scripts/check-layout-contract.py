#!/usr/bin/env python3
"""Fail-closed hash and ordinary-session checks for shipped vc-frame layouts."""

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


def _guard_peer_sessions(layouts_dir: Path, config_path: Path) -> None:
    # Operator Frame session 00 belongs to the vc-frame binary. The Runtime
    # Pack carries ordinary project/tool layouts, each with its own chrome.
    for name in ("host.kdl", "vibecrafted-host.kdl"):
        if (layouts_dir / name).exists() or (layouts_dir / name).is_symlink():
            raise ValueError(f"{name}: host chrome belongs to the vc-frame binary")
    operator = _code((layouts_dir / "operator.kdl").read_text(encoding="utf-8"))
    config = _code(config_path.read_text(encoding="utf-8"))
    if "default_layout" in config:
        raise ValueError("config.kdl cannot select Frame host chrome")

    operator_layer = _block(operator, "session_layer")
    operator_content = operator.replace(operator_layer, "", 1)
    for kind in ("compact-bar", "session-manager", "status-bar"):
        marker = f'session_canvas_kind "{kind}"'
        if operator_layer.count(marker) != 1 or marker in operator_content:
            raise ValueError(f"operator.kdl must own one {kind} in session_layer")

    for path in sorted(layouts_dir.glob("*.kdl")):
        code = _code(path.read_text(encoding="utf-8"))
        for marker in ("frame_host", "workspace_surface"):
            if marker in code:
                raise ValueError(
                    f"{path.name}: ordinary session cannot declare {marker}"
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--layouts-dir", type=Path, default=DEFAULT_LAYOUTS)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()

    observed = _hashes(args.layouts_dir)
    _guard_peer_sessions(args.layouts_dir, args.config)
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
            "layout hashes drifted; inspect the session layout change, then run make layouts-check UPDATE=1"
        )
    print(f"layout contract OK ({len(observed)} locked layouts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
