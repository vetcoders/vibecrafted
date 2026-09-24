#!/usr/bin/env python3
"""Render packaging/windows/License.rtf from the repo LICENSE.

WiX WixUI ScrollableText and Burn RtfLicense paint an empty license box when
the file is plain text. LICENSE stays the only legal source. This renderer
is deterministic: same LICENSE bytes, same ASCII RTF.

MSI's license control reads ANSI RTF. Characters outside ASCII, including
the mathematical-monospace brand in LICENSE, are signed UTF-16 ``\\uN?``
escapes (RTF spec). The unsigned form (``\\u55349?``) is rejected by the
installer contract. No BOM, no raw UTF-8, no Word stylesheet.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LICENSE = REPO_ROOT / "LICENSE"
DEFAULT_RTF = REPO_ROOT / "packaging" / "windows" / "License.rtf"

# WordPad-simple header. Word stylesheets make WixUI_Minimal's first dialog
# look empty until the user scrolls; keep this header free of them.
_HEADER = (
    "{\\rtf1\\ansi\\ansicpg1252\\deff0"
    "{\\fonttbl{\\f0\\fmodern\\fprq1\\fcharset0 Consolas;}}\n"
    "\\viewkind4\\uc1\\pard\\f0\\fs16\n"
)


def _rtf_escape_line(line: str) -> str:
    parts: list[str] = []
    for char in line:
        if char == "\\":
            parts.append("\\\\")
        elif char == "{":
            parts.append("\\{")
        elif char == "}":
            parts.append("\\}")
        elif ord(char) < 128:
            parts.append(char)
        else:
            encoded = char.encode("utf-16-le")
            for offset in range(0, len(encoded), 2):
                unit = int.from_bytes(encoded[offset : offset + 2], "little")
                signed = unit - 0x10000 if unit >= 0x8000 else unit
                parts.append(f"\\u{signed}?")
    return "".join(parts)


def render_license_rtf(license_text: str) -> str:
    """Return ASCII RTF for license_text. Newlines in the result are LF."""
    normalized = license_text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.removesuffix("\n")
    body = "".join(
        f"{_rtf_escape_line(line)}\\par\n" for line in normalized.split("\n")
    )
    rendered = _HEADER + body + "\n}\n"
    if not rendered.startswith("{\\rtf1") or not rendered.isascii():
        raise ValueError(
            "license RTF renderer produced a non-RTF or non-ASCII document"
        )
    return rendered


def normalize_rtf_text(text: str) -> str:
    """Compare RTF ignoring a leading BOM and checkout newline translation."""
    text = text.removeprefix("\ufeff")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_license_rtf(path: Path) -> str:
    return normalize_rtf_text(path.read_text(encoding="utf-8"))


def write_license_rtf(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(rendered, encoding="ascii", newline="\n")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--license", type=Path, default=DEFAULT_LICENSE)
    parser.add_argument("--output", type=Path, default=DEFAULT_RTF)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write",
        action="store_true",
        help="overwrite --output with RTF rendered from --license",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when --output is not the render of --license",
    )
    args = parser.parse_args(argv)
    license_path = args.license
    output_path = args.output
    if not license_path.is_file():
        print(f"LICENSE missing: {license_path}", file=sys.stderr)
        return 1
    rendered = render_license_rtf(license_path.read_text(encoding="utf-8"))
    if args.write:
        write_license_rtf(output_path, rendered)
        return 0
    if not output_path.is_file():
        print(f"License.rtf missing: {output_path}", file=sys.stderr)
        return 1
    on_disk = read_license_rtf(output_path)
    if on_disk != rendered:
        print(
            f"License.rtf drifted from {license_path}; regenerate with --write",
            file=sys.stderr,
        )
        return 1
    if not on_disk.startswith("{\\rtf1"):
        print("License.rtf is not RTF", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
