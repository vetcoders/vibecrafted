"""iTerm2 Dynamic Profiles generator + runtime installer.

Source-of-truth lives in ``PROFILE_SPECS`` below: a small, generic
``Vibecrafted`` profile that carries the managed trigger rows.
``build_profiles_document()`` materializes the iTerm2-compatible JSON.
``install_profiles()`` writes that JSON to the user's
``~/Library/Application Support/iTerm2/DynamicProfiles/`` directory, where
iTerm2 hot-reloads it.

The default install filename is ``vibecrafted.json``. Profile names use
the stable ``Vibecrafted`` namespace and ship alongside the user's existing
iTerm2 profiles without replacing them.

Status: **GA since v1.8.0 / 2026-05-12** (Plan 10, META_22). Wire contract
stable: profile GUIDs are derived deterministically from
``namespace + name`` via :func:`stable_guid`; if an operator already has
the v1.7 experimental file (``vibecrafted-experimental.json``) on disk,
the :func:`migrate_from_experimental` helper (also exposed as the
``migrate-from-experimental`` CLI subcommand) cleans the names in place,
preserves GUIDs (so iTerm2 keeps the same profile rows rather than
duplicating them), backs the old file up to ``*.bak``, and removes the
experimental file once the new ``vibecrafted.json`` is written.

References:
- https://iterm2.com/documentation-dynamic-profiles.html
- Profile schema: ``Settings → Profiles → Other Actions → Save Profile as JSON``
"""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .vc_triggers import VIBECRAFTED_TRIGGERS, triggers_as_iterm2_payload

# --------------------------------------------------------------------- helpers


def hex_to_iterm2(hex_color: str, alpha: float = 1.0) -> dict[str, Any]:
    """Convert a ``#rrggbb`` (or ``rrggbb``) hex string to iTerm2 color dict.

    iTerm2 stores colors as floats 0..1 with explicit color space.
    """
    s = hex_color.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise ValueError(
            f"hex_to_iterm2: expected 3 or 6 hex digits, got {hex_color!r}"
        )
    r, g, b = (int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {
        "Red Component": round(r, 6),
        "Green Component": round(g, 6),
        "Blue Component": round(b, 6),
        "Alpha Component": round(alpha, 6),
        "Color Space": "sRGB",
    }


def stable_guid(namespace: str, name: str) -> str:
    """Deterministic UUID derived from namespace+name.

    Same input always produces the same GUID across runs, which lets
    iTerm2 reuse existing profiles instead of duplicating them on each
    install. Uses uuid5 with the standard DNS namespace.

    Note: when names changed from ``[experimental] Vibecrafted / X`` to
    ``Vibecrafted / X`` at v1.8.0 GA, the resulting GUIDs *also* changed
    because ``name`` is part of the seed. :func:`migrate_from_experimental`
    works around this by reading the old GUIDs out of the experimental
    JSON file and writing them straight through into the new file, so the
    operator does not see profile duplication in iTerm2.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vibecrafted.{namespace}.{name}"))


# --------------------------------------------------------------------- specs


@dataclass(frozen=True)
class ProfileSpec:
    """Source-of-truth entry for a generated iTerm2 profile.

    `parent` is the ``Name`` of another profile (built-in or generated);
    iTerm2 inherits unspecified attributes from it. Use ``None`` for the
    parent profile itself.

    `extras` is merged verbatim into the output JSON, so any iTerm2
    profile key may be overridden (Triggers, Smart Selection Rules,
    custom font sizes, etc.).
    """

    name: str
    namespace: str
    parent: str | None
    tags: tuple[str, ...] = ()
    badge: str | None = None
    foreground: str | None = None  # hex
    background: str | None = None  # hex
    cursor: str | None = None  # hex
    tab_color: str | None = None  # hex
    custom_window_title: str | None = None
    custom_command: str | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    def to_iterm2_profile(self) -> dict[str, Any]:
        """Render this spec into the iTerm2 DynamicProfile JSON dict shape."""
        out: dict[str, Any] = {
            "Name": self.name,
            "Guid": stable_guid(self.namespace, self.name),
            "Tags": list(self.tags),
        }
        if self.parent is not None:
            out["Dynamic Profile Parent Name"] = self.parent
        if self.badge is not None:
            out["Badge Text"] = self.badge
        if self.foreground is not None:
            out["Foreground Color"] = hex_to_iterm2(self.foreground)
        if self.background is not None:
            out["Background Color"] = hex_to_iterm2(self.background)
        if self.cursor is not None:
            out["Cursor Color"] = hex_to_iterm2(self.cursor)
        if self.tab_color is not None:
            out["Tab Color"] = hex_to_iterm2(self.tab_color)
            out["Use Tab Color"] = True
        if self.custom_window_title is not None:
            out["Use Custom Window Title"] = True
            out["Custom Window Title"] = self.custom_window_title
        if self.custom_command is not None:
            out["Custom Command"] = "Yes"
            out["Command"] = self.custom_command
        out.update(self.extras)
        return out


# --------------------------------------------------------------------- profile specs

# Profile name — GA stable shape for the standalone plugin.
PROFILE_NAME = "Vibecrafted"

# Filenames — GA shape vs. the v1.7 experimental shape kept around for
# migration logic only. New code should reference :data:`DEFAULT_FILENAME`.
DEFAULT_FILENAME = "vibecrafted.json"
LEGACY_EXPERIMENTAL_FILENAME = "vibecrafted-experimental.json"


# Standalone profile. It inherits unspecified attributes from iTerm2's
# implicit fallback and carries only Vibecrafted-managed trigger rows.
VIBECRAFTED_PROFILE = ProfileSpec(
    name=PROFILE_NAME,
    namespace="plugin",
    parent=None,
    tags=("vibecrafted", "plugin"),
    badge="Vibecrafted",
    tab_color="#fbbf24",
    custom_window_title=r"Vibecrafted - \(session.path)",
    extras={
        "Triggers": triggers_as_iterm2_payload(VIBECRAFTED_TRIGGERS),
        "Working Directory": "Recycle",
        "Custom Directory": "Recycle",
        "Allow Title Setting": True,
        "Allow Title Reporting": True,
    },
)


PROFILE_SPECS: tuple[ProfileSpec, ...] = (VIBECRAFTED_PROFILE,)


# --------------------------------------------------------------------- builders


def build_profiles_document(
    specs: Iterable[ProfileSpec] = PROFILE_SPECS,
) -> dict[str, Any]:
    """Materialize the iTerm2 DynamicProfile JSON document."""
    return {"Profiles": [spec.to_iterm2_profile() for spec in specs]}


def serialize(doc: Mapping[str, Any]) -> str:
    """Stable JSON serialization for diff-friendly output."""
    return json.dumps(doc, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------- install


def default_install_dir() -> Path:
    """iTerm2's monitored DynamicProfiles directory."""
    return (
        Path.home() / "Library" / "Application Support" / "iTerm2" / "DynamicProfiles"
    )


def install_profiles(
    *,
    target_dir: Path | None = None,
    filename: str = DEFAULT_FILENAME,
    force: bool = False,
    specs: Iterable[ProfileSpec] = PROFILE_SPECS,
    backup: bool = True,
) -> Path:
    """Write the dynamic profile JSON to iTerm2's monitored directory.

    Returns the path written. Creates the parent directory if missing.

    If a file already exists at the target and `force` is False, raises
    ``FileExistsError``. Pass ``force=True`` to overwrite (the previous
    file is preserved as ``<filename>.bak`` when ``backup=True``).
    """
    target_dir = target_dir or default_install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename

    payload = serialize(build_profiles_document(specs))

    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if existing == payload:
            return target  # idempotent no-op
        if force:
            if backup:
                backup_path = target.with_suffix(target.suffix + ".bak")
                backup_path.write_text(existing, encoding="utf-8")
        else:
            raise FileExistsError(
                f"{target} exists with different content; pass force=True to overwrite"
            )

    target.write_text(payload, encoding="utf-8")
    return target


def uninstall_profiles(
    *,
    target_dir: Path | None = None,
    filename: str = DEFAULT_FILENAME,
) -> bool:
    """Remove a previously installed profiles file. Returns True if removed."""
    target_dir = target_dir or default_install_dir()
    target = target_dir / filename
    if not target.exists():
        return False
    target.unlink()
    return True


# --------------------------------------------------------------------- migration


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of :func:`migrate_from_experimental`.

    `status` is one of:
      - ``"migrated"`` — old file was present, new file written, .bak created
      - ``"already-migrated"`` — new file already present; nothing to do
      - ``"nothing-to-migrate"`` — no old file and no new file; nothing to do
    """

    status: str
    target_dir: Path
    new_file: Path
    legacy_file: Path
    backup_file: Path | None
    migrated_profiles: int


# Per-name cleanup: ``[experimental] Vibecrafted / X`` → ``Vibecrafted / X``.
_LEGACY_PREFIX = "[experimental] "


def _clean_profile_name(name: str) -> str:
    """Strip the v1.7 ``[experimental]`` prefix from a profile name.

    Idempotent — names that already lack the prefix are returned as-is.
    """
    if name.startswith(_LEGACY_PREFIX):
        return name[len(_LEGACY_PREFIX) :]
    return name


def migrate_from_experimental(
    *,
    target_dir: Path | None = None,
    legacy_filename: str = LEGACY_EXPERIMENTAL_FILENAME,
    new_filename: str = DEFAULT_FILENAME,
    backup: bool = True,
) -> MigrationResult:
    """Migrate v1.7 ``vibecrafted-experimental.json`` to GA ``vibecrafted.json``.

    Behaviour:
      - reads the legacy file (if present)
      - rewrites each profile ``Name`` with the ``[experimental]`` prefix
        stripped, and rewrites each ``Dynamic Profile Parent Name`` the
        same way so child→parent inheritance still resolves
      - preserves every ``Guid`` verbatim — iTerm2 keys profiles by GUID,
        so reusing them avoids duplicate rows in Settings → Profiles
      - writes the cleaned document to the GA filename
      - moves the legacy file to ``<legacy>.bak`` (only if ``backup=True``;
        otherwise deletes it) so re-running the migration is safe

    Idempotent — running on a tree that has already been migrated (legacy
    file gone, GA file present) is a no-op and returns
    ``MigrationResult(status="already-migrated", ...)``.

    Returns the :class:`MigrationResult` describing what happened. Never
    raises on a clean state; raises ``FileNotFoundError`` only when the
    legacy file exists but cannot be read.
    """
    target_dir = target_dir or default_install_dir()
    legacy_path = target_dir / legacy_filename
    new_path = target_dir / new_filename
    backup_path = legacy_path.with_suffix(legacy_path.suffix + ".bak")

    if not legacy_path.exists():
        if new_path.exists():
            return MigrationResult(
                status="already-migrated",
                target_dir=target_dir,
                new_file=new_path,
                legacy_file=legacy_path,
                backup_file=None,
                migrated_profiles=0,
            )
        return MigrationResult(
            status="nothing-to-migrate",
            target_dir=target_dir,
            new_file=new_path,
            legacy_file=legacy_path,
            backup_file=None,
            migrated_profiles=0,
        )

    legacy_text = legacy_path.read_text(encoding="utf-8")
    legacy_doc = json.loads(legacy_text)

    profiles_in = legacy_doc.get("Profiles", [])
    profiles_out: list[dict[str, Any]] = []
    for entry in profiles_in:
        cleaned = dict(entry)
        if "Name" in cleaned:
            cleaned["Name"] = _clean_profile_name(cleaned["Name"])
        if "Dynamic Profile Parent Name" in cleaned:
            cleaned["Dynamic Profile Parent Name"] = _clean_profile_name(
                cleaned["Dynamic Profile Parent Name"]
            )
        profiles_out.append(cleaned)

    new_doc = dict(legacy_doc)
    new_doc["Profiles"] = profiles_out

    target_dir.mkdir(parents=True, exist_ok=True)
    new_path.write_text(serialize(new_doc), encoding="utf-8")

    if backup:
        backup_path.write_text(legacy_text, encoding="utf-8")
    else:
        backup_path = None

    legacy_path.unlink()

    return MigrationResult(
        status="migrated",
        target_dir=target_dir,
        new_file=new_path,
        legacy_file=legacy_path,
        backup_file=backup_path,
        migrated_profiles=len(profiles_out),
    )


# --------------------------------------------------------------------- CLI


def _cli(argv: list[str]) -> int:
    """Dispatch the show/install/refresh/uninstall/path/migrate subcommands."""
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "Usage: python -m vibecrafted_iterm2.iterm2_profiles <op>\n"
            "\n"
            "iTerm2 Dynamic Profile for Vibecrafted triggers (standalone plugin).\n"
            "\n"
            "Operations:\n"
            "  show                        Print the JSON document to stdout\n"
            "  install                     Write to iTerm2 DynamicProfiles dir (idempotent)\n"
            "  install --force             Overwrite existing file (creates .bak first)\n"
            "  refresh                     Alias for `install --force`\n"
            "  uninstall                   Remove the installed file\n"
            "  path                        Print the install target path\n"
            "  migrate-from-experimental   Migrate v1.7 vibecrafted-experimental.json\n"
            "                              → vibecrafted.json (preserves GUIDs, .bak backup,\n"
            "                              idempotent)\n"
        )
        return 0

    op = argv[0]
    flags = argv[1:]
    force = "--force" in flags or "-f" in flags

    if op == "show":
        print(serialize(build_profiles_document()), end="")
        return 0
    if op == "path":
        print(default_install_dir() / DEFAULT_FILENAME)
        return 0
    if op in ("install", "refresh"):
        try:
            target = install_profiles(force=force or op == "refresh")
        except FileExistsError as err:
            print(f"error: {err}", file=sys.stderr)
            print("hint: pass --force to overwrite (creates a .bak first)")
            return 3
        print(f"installed: {target}")
        return 0
    if op == "uninstall":
        removed = uninstall_profiles()
        print("removed" if removed else "nothing to remove")
        return 0
    if op == "migrate-from-experimental":
        result = migrate_from_experimental()
        if result.status == "migrated":
            print(
                f"migrated: {result.legacy_file.name} -> {result.new_file.name} "
                f"({result.migrated_profiles} profiles)"
            )
            if result.backup_file is not None:
                print(f"backup:   {result.backup_file}")
        elif result.status == "already-migrated":
            print(f"already migrated: {result.new_file} present, nothing to do")
        else:
            print(f"nothing to migrate: {result.legacy_file} not present")
        return 0

    print(f"unknown op: {op!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
