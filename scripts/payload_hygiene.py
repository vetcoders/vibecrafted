#!/usr/bin/env python3
"""Refuse to ship bytes that remember who built them.

WHY THIS IS NOT A COMPILER FLAG
-------------------------------
`--remap-path-prefix` is a *rustc* lever. It rewrites the paths rustc itself
sees, and nothing else. Measured on the shipped
`Vibecrafted_4.1.0-20260817-237d2814.dmg` (2955 files), the assembled bundle
carried the operator's identity through **five** producers, only one of which
rustc can reach:

1. `Contents/Helpers/vc-frame` — 411 hits of `$HOME`. vc-frame embeds its WASM
   plugins with `include_bytes!`, and `make release-binary` builds
   `--no-plugins`, so the *git-tracked* blobs from an earlier, unremapped build
   are what ships. Already-compiled bytes cannot be remapped.
2. `Contents/MacOS/Vibecrafted` — 21 hits of
   `$HOME/.cargo/registry/.../ring-0.17.14/crypto/...`. Those are C source
   paths baked in by cc-rs; they need `-ffile-prefix-map` in `CFLAGS`, not
   `RUSTFLAGS`.
3. `Contents/MacOS/Vibecrafted` — 51 hits of the checkout root, from
   xcodebuild's `DerivedData` intermediates and Swift source locations. Swift
   has its own `-debug-prefix-map`; `RUSTFLAGS` never reaches it.
4. `Contents/Resources/runtime/python/lib/python3.12/_sysconfigdata__darwin_darwin.py`
   — 27 hits of the ephemeral `build/unified-release/python-seed.XXXXXX/` dir
   uv installed CPython into. A plain text file; no compiler involved at all.
5. `Contents/Resources/runtime/python-site/bin/jsonschema` — a console-script
   shebang pointing at that same seed path. Not merely a leak: that path does
   not exist on a customer machine, so the shipped script is dead on arrival.

Five producers, five different levers, and the set grows every time a new kind
of artifact is bundled. So the primary defence is not another flag — it is a
gate that reads the *finished* payload and knows nothing about how it was made.

Deliberately no allowlist. An allowlist is how a leak becomes normal.

Usage:
    payload_hygiene.py --root <dir> --label <name> --forbid <literal> [...]
    payload_hygiene.py --host-paths [--null] [--no-skip-unshipped] [file ...]

Exit 0 when the payload names none of the forbidden literals, 1 otherwise.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Read in chunks so a multi-hundred-megabyte payload never has to fit in RAM at
# once, with an overlap so a literal straddling a chunk boundary is still seen.
CHUNK = 4 * 1024 * 1024

# Account-home family for the commit-time gate. Same spirit as
# payload-hygiene.sh: `/Users` and `/home` are generic roots and say nothing
# about who built the tree; `/Users/<account>` and `/home/<account>` are
# identity. Assembled from parts so this file does not itself name a host.
_HOST_ACCOUNT_ROOTS = (b"Users", b"home")
_GENERIC_ACCOUNT_NAMES = frozenset({b"Shared"})
_HOST_ACCOUNT_PATH_RE = re.compile(
    rb"(?:^|[\s\"'`=:(<\[{,;])(?P<path>/("
    + b"|".join(_HOST_ACCOUNT_ROOTS)
    + rb")/[A-Za-z_][A-Za-z0-9._-]*)"
)
# Paths that never reach a customer (mirrors tests/tui/test_payload_hygiene.py).
UNSHIPPED_COMPONENTS = frozenset({"tests", "test", "__tests__", ".github", ".loctree"})
_BINARY_PROBE = 8192


def count_in_file(path: Path, needles: list[bytes]) -> dict[bytes, int]:
    """Count each needle in one file without holding the whole file in memory."""
    overlap = max((len(n) for n in needles), default=1) - 1
    found: dict[bytes, int] = {}
    tail = b""
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            window = tail + chunk
            for needle in needles:
                hits = window.count(needle)
                if hits:
                    found[needle] = found.get(needle, 0) + hits
            tail = window[-overlap:] if overlap else b""
    return found


def scan(root: Path, needles: list[bytes]) -> tuple[int, list[dict[str, object]]]:
    """Return (files_scanned, offenders) for every regular file under root."""
    offenders: list[dict[str, object]] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        # A symlink is not payload: its target is scanned on its own if it is
        # inside the tree, and following it out of the tree would scan the host.
        if path.is_symlink() or not path.is_file():
            continue
        scanned += 1
        try:
            found = count_in_file(path, needles)
        except OSError as error:  # unreadable payload is itself a release defect
            offenders.append(
                {
                    "file": str(path.relative_to(root)),
                    "needle": "<unreadable>",
                    "count": 0,
                    "error": str(error),
                }
            )
            continue
        for needle, count in found.items():
            offenders.append(
                {
                    "file": str(path.relative_to(root)),
                    "needle": needle.decode("utf-8", "replace"),
                    "count": count,
                }
            )
    offenders.sort(key=lambda row: (-int(row["count"] or 0), str(row["file"])))
    return scanned, offenders


def is_unshipped_path(path: str | Path) -> bool:
    """True when a relative path lives in a component the packer will not ship."""
    return bool(UNSHIPPED_COMPONENTS & set(Path(path).parts))


def find_host_account_paths(data: bytes) -> list[tuple[int, str]]:
    """Return (1-based line, matched account home) for each host-account path.

    Does not flag a generic root alone. `/Users/Shared` is a public macOS
    directory, not an account.
    """
    hits: list[tuple[int, str]] = []
    for match in _HOST_ACCOUNT_PATH_RE.finditer(data):
        raw = match.group("path")
        account = raw.rsplit(b"/", 1)[-1]
        if account in _GENERIC_ACCOUNT_NAMES:
            continue
        line = data.count(b"\n", 0, match.start()) + 1
        hits.append((line, raw.decode("utf-8", "replace")))
    return hits


def scan_text_files_for_host_accounts(
    files: list[str],
    *,
    repo_root: Path | None = None,
    skip_unshipped: bool = True,
) -> list[dict[str, object]]:
    """Scan named files for `/Users/<account>` and `/home/<account>`.

    Fail-closed on unreadable files. Skip binaries (NUL in the first probe)
    and, by default, unshipped test/github trees. Release `--root --forbid`
    scanning is unchanged.
    """
    root = repo_root if repo_root is not None else Path.cwd()
    offenders: list[dict[str, object]] = []
    for raw in files:
        if not raw:
            continue
        relative = raw
        path = Path(raw)
        if not path.is_absolute():
            path = root / raw
        else:
            try:
                relative = str(path.relative_to(root))
            except ValueError:
                relative = str(path)
        if skip_unshipped and is_unshipped_path(relative):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            offenders.append(
                {
                    "file": relative,
                    "line": 0,
                    "path": "<unreadable>",
                    "error": str(error),
                }
            )
            continue
        if b"\x00" in data[:_BINARY_PROBE]:
            continue
        for line, host_path in find_host_account_paths(data):
            offenders.append(
                {
                    "file": relative,
                    "line": line,
                    "path": host_path,
                }
            )
    offenders.sort(key=lambda row: (str(row["file"]), int(row["line"] or 0)))
    return offenders


def _read_nul_paths() -> list[str]:
    """Read NUL-delimited relative paths from stdin (git -z)."""
    return [
        chunk.decode("utf-8", "replace")
        for chunk in sys.stdin.buffer.read().split(b"\0")
        if chunk
    ]


def _host_paths_main(args: argparse.Namespace) -> int:
    """Commit-time account-home gate. Does not touch release --forbid semantics."""
    paths = list(args.files)
    if args.nul_paths:
        paths.extend(_read_nul_paths())
    if not paths and not args.nul_paths:
        print(
            "FATAL: no files given to --host-paths; the gate would prove nothing",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root.resolve() if args.repo_root else Path.cwd()
    offenders = scan_text_files_for_host_accounts(
        paths,
        repo_root=repo_root,
        skip_unshipped=not args.no_skip_unshipped,
    )
    if args.as_json:
        print(
            json.dumps(
                {
                    "schema": "io.vetcoders.vibecrafted.host-paths.v1",
                    "files_given": len(paths),
                    "offenders": offenders,
                },
                indent=2,
            )
        )
    else:
        print(
            f"host-paths: {len(paths)} files given, {len(offenders)} host-account hit(s)"
        )
        for row in offenders:
            where = row["file"]
            line = row.get("line") or 0
            loc = f"{where}:{line}" if line else str(where)
            print(f"  {loc}  ->  {row['path']}", file=sys.stderr)
            if row.get("error"):
                print(f"    error: {row['error']}", file=sys.stderr)

    if offenders:
        print(
            "FATAL: staged text names a host account path "
            "(/Users/<account> or /home/<account>); refuse the commit",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--host-paths",
        action="store_true",
        help="scan named text files for /Users/<account> and /home/<account>",
    )
    parser.add_argument(
        "--null",
        action="store_true",
        dest="nul_paths",
        help="with --host-paths, also read NUL-delimited paths from stdin",
    )
    parser.add_argument(
        "--no-skip-unshipped",
        action="store_true",
        help="with --host-paths, do not skip tests/.github/.loctree trees",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="with --host-paths, resolve relative paths against this root",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="with --host-paths, files to scan (git-relative or absolute)",
    )
    parser.add_argument("--root", type=Path, help="payload directory for --forbid")
    parser.add_argument("--label", default="payload")
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        metavar="LITERAL",
        help="an absolute path the payload must never contain; repeatable",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.host_paths:
        return _host_paths_main(args)
    if args.root is None:
        parser.error("--root is required unless --host-paths")

    root: Path = args.root
    if not root.is_dir():
        print(f"FATAL: payload root is not a directory: {root}", file=sys.stderr)
        return 2

    # An empty or single-character literal would match everything; refusing is
    # safer than a gate that reports the whole payload as a leak.
    needles: list[bytes] = []
    for raw in args.forbid:
        literal = raw.rstrip("/")
        if len(literal) < 2:
            print(f"FATAL: refusing to scan for the literal {raw!r}", file=sys.stderr)
            return 2
        encoded = literal.encode("utf-8")
        if encoded not in needles:
            needles.append(encoded)
    if not needles:
        print(
            "FATAL: no --forbid literal given; the gate would prove nothing",
            file=sys.stderr,
        )
        return 2

    scanned, offenders = scan(root, needles)

    if args.as_json:
        print(
            json.dumps(
                {
                    "schema": "io.vetcoders.vibecrafted.payload-hygiene.v1",
                    "label": args.label,
                    "root": str(root),
                    "files_scanned": scanned,
                    "forbidden": [n.decode() for n in needles],
                    "offenders": offenders,
                },
                indent=2,
            )
        )
    else:
        print(
            f"payload-hygiene: {args.label} — {scanned} files scanned, {len(needles)} literals"
        )
        for row in offenders:
            print(
                f"  {row['count']:>6}  {row['needle']}  ->  {row['file']}",
                file=sys.stderr,
            )

    if offenders:
        print(
            f"FATAL: {args.label} names the build host in {len(offenders)} place(s); "
            "a signed artifact must not carry the operator's account or checkout",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
