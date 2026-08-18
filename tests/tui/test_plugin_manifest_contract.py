"""`plugin.json` rides in every payload, so it must not contradict it.

The file sits in `ALLOWED_TOP_LEVEL` here and in `install.sh`, which means a
copy lands next to `LICENSE` and `VERSION` on every installed host and inside
every distribution archive. Between 2026-07-02 and 4.1.0 it declared `2.0.0`
and `Apache-2.0` while the repository shipped BUSL-1.1 — stale rather than
wrong, which is exactly what a gate is for.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts import build_marketplace_bundle as bundle
from scripts import distribution_manifest as manifest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _shipped_manifest() -> dict[str, object]:
    return json.loads(_read("plugin.json"))


def _spdx_identifier(license_text: str) -> str:
    """Return the SPDX id declared on the LICENSE file's first line."""
    first_line = license_text.splitlines()[0].strip()
    prefix = "SPDX-License-Identifier:"
    assert first_line.startswith(prefix), (
        f"LICENSE must open with an SPDX identifier line, found: {first_line!r}"
    )
    return first_line[len(prefix) :].strip()


def test_shipped_plugin_manifest_states_the_version_and_licence_it_ships_with() -> None:
    plugin = _shipped_manifest()
    version = _read("VERSION").strip()
    licence = _spdx_identifier(_read("LICENSE"))

    assert plugin["version"] == version, (
        f"plugin.json version {plugin['version']!r} != VERSION {version!r}"
    )
    assert plugin["license"] == licence, (
        f"plugin.json license {plugin['license']!r} != LICENSE SPDX {licence!r}"
    )


def test_shipped_plugin_manifest_travels_with_every_distribution() -> None:
    """The contract above only matters because this file actually ships."""
    assert "plugin.json" in manifest.ALLOWED_TOP_LEVEL
    assert '"plugin.json",' in _read("install.sh")


def test_shipped_plugin_manifest_names_the_same_publisher_as_the_bundle() -> None:
    """One product, one publisher — on the payload and in the registry listing."""
    plugin = _shipped_manifest()
    listing = bundle.load_listing_metadata(REPO_ROOT)
    bundled = bundle.plugin_manifest(_read("VERSION").strip(), listing)

    assert plugin["author"] == bundled["author"]
    assert plugin["homepage"] == bundled["homepage"]
    assert plugin["repository"] == bundled["repository"]
