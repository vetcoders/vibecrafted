#!/usr/bin/env python3
"""W1-02 gate: AppDelegate.composeRuntimeEnvironment is guest, not landlord.

Reads vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift, locates
the composeRuntimeEnvironment body, and asserts:

- A1: the composition starts from ProcessInfo.processInfo.environment as the
  base (full inherited env), with Vibecrafted pins overlaid — no bare
  allow-list dictionary rebuild remains.
- A2: the canonical generation bin is PREPENDED to the inherited PATH, never
  a PATH replacement.
- A3: the file still compiles (xcrun swiftc -parse, captured through a pipe —
  shell-redirected swiftc logs can come out empty on this host).

Prints APP_GUEST_OK and exits 0 when all checks pass, otherwise prints
APP_GUEST_MISSING (one line per failed check) and exits 1.
Dependency-free: stdlib only.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DELEGATE = REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"


def function_body(source: str, signature: str) -> str:
    """Extract the brace-balanced body of a Swift func from its signature."""
    start = source.find(signature)
    if start == -1:
        return ""
    brace = source.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace : index + 1]
    return ""


def main() -> int:
    failures: list[str] = []

    if not DELEGATE.is_file():
        print(f"APP_GUEST_MISSING: {DELEGATE} not found")
        return 1
    source = DELEGATE.read_text(encoding="utf-8")

    body = function_body(source, "private func composeRuntimeEnvironment(")
    if not body:
        print("APP_GUEST_MISSING: composeRuntimeEnvironment body not located")
        return 1

    # A1: the full host environment is the base of the composition.
    if "ProcessInfo.processInfo.environment" not in body:
        failures.append(
            "A1: composeRuntimeEnvironment does not read processInfo.environment"
        )
    if not re.search(r"var\s+environment\s*=\s*host\b", body):
        failures.append("A1: composition does not start from the full host environment")
    # No bare allow-list dictionary rebuild may remain: neither a compactMap
    # over a fixed name list nor a Dictionary(uniqueKeysWithValues:) rebuild.
    if "uniqueKeysWithValues" in body or "compactMap" in body:
        failures.append("A1: bare allow-list dictionary rebuild still present")
    # Scrubbing must go through an explicit deny-list.
    if not re.search(r"let\s+denied\b", body) or "removeValue(forKey:" not in body:
        failures.append("A1: no explicit deny-list scrub found")
    # Pins must still overlay the inherited base.
    for pin in ("VIBECRAFTED_HOME", "VIBECRAFTED_RUNTIME_ROOT", "VIBECRAFTED_PYTHON"):
        if f'environment["{pin}"]' not in body:
            failures.append(f"A1: pin {pin} no longer overlaid")

    # A2: canonical bin prepended to the inherited PATH, never a replacement.
    if 'environment["PATH"] = composedPath(' not in body:
        failures.append("A2: PATH is not composed from the inherited PATH")
    path_body = function_body(source, "private func composedPath(")
    if not path_body:
        failures.append("A2: composedPath body not located")
    else:
        if (
            '"/usr/bin:/bin:/usr/sbin:/sbin"' in path_body
            and "isEmpty ?" not in path_body
        ):
            failures.append("A2: PATH replaced by the bare system set")
        if "([generationBin] + entries)" not in path_body:
            failures.append("A2: canonical bin is not prepended to the inherited PATH")
        if "(entries + [generationBin])" in path_body:
            failures.append("A2: canonical bin is appended, not prepended")

    # A3: the file still compiles. swiftc diagnostics are captured through the
    # subprocess pipe because a shell-redirected swiftc log can come out empty
    # on this host.
    try:
        parsed = subprocess.run(
            ["xcrun", "swiftc", "-parse", str(DELEGATE)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        failures.append(f"A3: swiftc invocation failed: {exc}")
    else:
        diagnostics = (parsed.stdout + parsed.stderr).strip()
        if parsed.returncode != 0:
            failures.append(
                f"A3: swiftc -parse exited {parsed.returncode}:\n{diagnostics}"
            )
        elif diagnostics:
            print(f"swiftc diagnostics (exit 0):\n{diagnostics}")
        else:
            print("swiftc diagnostics: none (exit 0)")

    if failures:
        for failure in failures:
            print(f"APP_GUEST_MISSING: {failure}")
        return 1
    print("APP_GUEST_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
