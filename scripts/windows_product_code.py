#!/usr/bin/env python3
"""One ProductCode per MSI version, shared by the builder and install smoke.

Keep the existing 4.3.1 identity for repair/reinstall. Later versions use UUIDv5
under the stable UpgradeCode, so rebuilds agree and upgrades change identity.
Python is already required by the Windows builder's LICENSE renderer.
"""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path


def product_code(version: str, upgrade_code: uuid.UUID) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:[+-][0-9A-Za-z.-]+)?", version)
    if not match:
        raise ValueError("VERSION must be SemVer major.minor.patch")
    base = match[1]
    if base == "4.3.1":
        return "2B1BF36C-C680-48EE-BDCA-648C09D41BB3"
    return str(uuid.uuid5(upgrade_code, f"Vibecrafted.ProductVersion:{base}")).upper()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=None)
    args = parser.parse_args()
    identity = (root / "packaging/windows/Identity.wxi").read_text(encoding="utf-8")
    match = re.search(r'UpgradeCode = "([0-9A-Fa-f-]+)"', identity)
    if not match:
        parser.error("Identity.wxi has no UpgradeCode")
    version = args.version or (root / "VERSION").read_text(encoding="utf-8").strip()
    try:
        print(product_code(version, uuid.UUID(match[1])))
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
