#!/usr/bin/env python3
"""Transparent hook-to-harness bridge.

Invocation: hook_bridge.py --to HARNESS --timeout SECONDS -- CMD [ARGS...]

Runs CMD as a subprocess, forwarding this process's stdin to the child and
the child's stdout/stderr back out unchanged, then exits with the child's
exit code. HARNESS is accepted but currently unused: the wrapped hook
scripts (loctree-first-guard.py, aicx-sessionstart.sh, ...) already emit
harness-appropriate output on stdout/stderr themselves. This bridge's job is
only to enforce the timeout contract so a hung wrapped command can never
block the calling agent CLI indefinitely.

Restored 2026-09-30: this file was missing from every runtime that installed
`~/.copilot/hooks/vibecrafted-fleet.json`, which meant every sessionStart /
preCompact / preToolUse hook command failed outright (python3 could not find
the target), and the Copilot CLI harness treated that failure as a hard
"deny" for every bash/powershell tool call. On timeout or any other
subprocess failure this bridge fails OPEN (exit 0), matching the documented
hook doctrine: hook transport failures must never block agent work.
"""

import subprocess
import sys


def main() -> int:
    argv = sys.argv[1:]
    timeout = None
    i = 0
    while i < len(argv):
        if argv[i] == "--to" and i + 1 < len(argv):
            i += 2
            continue
        if argv[i] == "--timeout" and i + 1 < len(argv):
            try:
                timeout = float(argv[i + 1])
            except ValueError:
                timeout = None
            i += 2
            continue
        if argv[i] == "--":
            i += 1
            break
        i += 1
    cmd = argv[i:]
    if not cmd:
        return 0

    try:
        stdin_data = b"" if sys.stdin.isatty() else sys.stdin.buffer.read()
    except (OSError, ValueError):
        stdin_data = b""

    try:
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"hook_bridge: {cmd!r} timed out after {timeout}s (fail-open)\n"
        )
        return 0
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"hook_bridge: failed to run {cmd!r}: {exc} (fail-open)\n")
        return 0

    try:
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
    except OSError as exc:
        # A closed pipe must not turn the wrapped hook's verdict into a deny.
        sys.stderr.write(f"hook_bridge: could not relay output: {exc}\n")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
