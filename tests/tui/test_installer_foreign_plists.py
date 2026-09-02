"""A foreign LaunchAgent that plistlib cannot parse must never abort the install.

Regression for the 4.3.0 field failure: ``~/Library/LaunchAgents`` held a
third-party plist with ``--`` inside an XML comment. ``plutil`` tolerates it,
``plistlib`` raises ``xml.parsers.expat.ExpatError`` — which the installer's
``except (plistlib.InvalidFileException, ...)`` did not cover, so one stranger's
comment blocked the whole VC Terminal bootstrap.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path
from xml.parsers.expat import ExpatError

import pytest

from scripts import vetcoders_install

MALFORMED_FOREIGN_PLIST = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>ai.libraxis.aicx-push</string>
    <!-- mirrors the store to the tailnet peer; never --delete on the remote -->
    <key>ProgramArguments</key>
    <array><string>/usr/bin/rsync</string></array>
</dict>
</plist>
"""

DEPENDENT_PLIST = {
    "Label": "ai.libraxis.aicx-serve",
    "ProgramArguments": ["/opt/homebrew/bin/aicx", "serve"],
}


def test_fixture_reproduces_the_field_error() -> None:
    with pytest.raises(ExpatError):
        plistlib.loads(MALFORMED_FOREIGN_PLIST)


def test_plist_decode_errors_cover_expat() -> None:
    assert ExpatError in vetcoders_install._PLIST_DECODE_ERRORS
    assert plistlib.InvalidFileException in vetcoders_install._PLIST_DECODE_ERRORS


def test_malformed_foreign_launch_agent_is_skipped_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "ai.libraxis.aicx-push.plist").write_bytes(MALFORMED_FOREIGN_PLIST)
    (agents / "ai.libraxis.fleet-session-pull.plist").write_bytes(
        MALFORMED_FOREIGN_PLIST.replace(b"aicx-push", b"fleet-session-pull")
    )
    with (agents / "ai.libraxis.aicx-serve.plist").open("wb") as handle:
        plistlib.dump(DEPENDENT_PLIST, handle)
    (agents / "io.vetcoders.vibecrafted.plist").write_bytes(b"not a plist at all")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(sys, "platform", "darwin")

    dependents = vetcoders_install._foundation_service_dependent_plists()

    assert [path.name for path, _payload in dependents] == [
        "ai.libraxis.aicx-serve.plist"
    ]
    assert dependents[0][1]["Label"] == "ai.libraxis.aicx-serve"
