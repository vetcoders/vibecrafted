"""Print resolved permission flags for shell hint generators.

Shell libraries that RENDER provider commands for operators (marbles resume
hints, operator entrypoint suggestions) must consume the shared permission
decision instead of embedding historical bypass literals (Founder decision
13716da4a2b8af7049ea1565). This module is their one call:

    python -m vibecrafted_core.permission_flags <agent> [--mode headless|interactive]

Stdout: the resolved provider flags, shell-quoted, space-separated (empty
string when the policy needs no flags). Exit 2 with the refusal on stderr
when the combination cannot be enforced — the caller renders that refusal as
the hint, never a downgrade.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys

from .execution_controls import parse_sandbox_word, resolve_execution_controls
from .server_config import durable_permission_selection
from .spawn import _materialize_cursor_permission_flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vibecrafted_core.permission_flags")
    parser.add_argument("agent")
    parser.add_argument(
        "--mode", choices=("headless", "interactive"), default="headless"
    )
    parser.add_argument("--permissions", default="")
    parser.add_argument("--sandbox", default="")
    parser.add_argument("--shell-env", action="store_true")
    args = parser.parse_args(argv)

    try:
        configured_word, configured_source = durable_permission_selection(args.agent)
        controls = resolve_execution_controls(
            args.agent,
            permissions=args.permissions,
            sandbox=parse_sandbox_word(args.sandbox),
            configured_permissions=configured_word,
            permissions_source=configured_source,
            mode=args.mode,
        )
        if args.agent == "cursor":
            _materialize_cursor_permission_flags(
                controls.provider_flags, permissions=controls.permissions_effective
            )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    flags = " ".join(shlex.quote(flag) for flag in controls.provider_flags)
    if args.shell_env:
        print(f"SPAWN_PERMISSION_FLAGS={shlex.quote(flags)}")
        print(f"SPAWN_EXECUTION_CONTROLS={shlex.quote(json.dumps(controls.receipt()))}")
    else:
        print(flags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
