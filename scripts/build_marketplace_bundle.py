#!/usr/bin/env python3
"""Build the vibecrafted-framework.plugin marketplace bundle from repo state.

Zips generated manifest/README/license files, discovered ``vc-*`` skills
(pipeline + foundation), and any notarized ``tools/bin/`` binaries into a
single deterministic-timestamp zip archive.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

try:
    _vetcoders_install = importlib.import_module("vetcoders_install")
except ModuleNotFoundError:  # import path depends on entrypoint
    _vetcoders_install = importlib.import_module("scripts.vetcoders_install")

discover_skills = _vetcoders_install.discover_skills

OUTPUT_FILENAME = "vibecrafted-framework.plugin"
PLUGIN_NAME = "vibecrafted-framework"
FIXED_ZIP_DATE_TIME = (2026, 3, 30, 0, 0, 0)
DEFAULT_FILE_MODE = 0o100644
IGNORED_PATH_PARTS = {".DS_Store", "__pycache__", ".pytest_cache"}
SUPPORT_DOC_PATHS = (
    "docs/QUICK_START.md",
    "docs/FAQ.md",
    "docs/RELEASE_KICKOFF.md",
    "docs/SUBMISSION_FORMS.md",
)


@dataclass(frozen=True)
class ListingMetadata:
    """Registry metadata parsed out of docs/MARKETPLACE_LISTING.md."""

    description: str
    keywords: tuple[str, ...]
    homepage: str
    repository: str
    documentation: str
    faq: str
    license: str


REPO_ROOT = Path(__file__).resolve().parent.parent


def read_version(repo_root: Path) -> str:
    """Read the release version string from the repo's VERSION file."""
    return (repo_root / "VERSION").read_text(encoding="utf-8").strip()


def parse_listing_metadata(text: str) -> ListingMetadata:
    """Parse the ``## Registry Metadata`` section of the listing doc.

    Raises ``ValueError`` if any required key is missing.
    """
    in_registry_section = False
    values: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.strip() in {"## Registry Metadata", "## Registry Metadata Draft"}:
            in_registry_section = True
            continue
        if not in_registry_section:
            continue
        if line.startswith("## "):
            break
        match = re.match(r"-\s+([a-z]+):\s*(.+)", line)
        if match:
            values[match.group(1)] = match.group(2).strip()

    required = {
        "description",
        "keywords",
        "homepage",
        "repository",
        "documentation",
        "faq",
        "license",
    }
    missing = sorted(required - values.keys())
    if missing:
        raise ValueError(
            f"Missing registry metadata in docs/MARKETPLACE_LISTING.md: {', '.join(missing)}"
        )

    keywords = tuple(
        keyword.strip() for keyword in values["keywords"].split(",") if keyword.strip()
    )
    return ListingMetadata(
        description=values["description"],
        keywords=keywords,
        homepage=values["homepage"],
        repository=values["repository"],
        documentation=values["documentation"],
        faq=values["faq"],
        license=values["license"],
    )


def load_listing_metadata(repo_root: Path) -> ListingMetadata:
    """Read and parse docs/MARKETPLACE_LISTING.md into `ListingMetadata`."""
    listing_path = repo_root / "docs" / "MARKETPLACE_LISTING.md"
    return parse_listing_metadata(listing_path.read_text(encoding="utf-8"))


def discover_bundle_skills(repo_root: Path) -> list[Path]:
    """Find top-level ``vc-*`` skill directories to ship in the bundle."""
    return sorted(
        (skill for skill in discover_skills(repo_root) if skill.name.startswith("vc-")),
        key=lambda path: path.name,
    )


def discover_foundation_skills(repo_root: Path) -> list[Path]:
    """Find foundation skill directories under skills/foundations/vc-*.

    The main discover_skills helper (in vetcoders_install) scans only direct
    children of skills/ with a vc- or vetcoders- prefix, so foundation skills
    nested under skills/foundations/ are invisible to it. The bundle must still
    ship them so relative links from pipeline skills (e.g. ../foundations/vc-loctree)
    resolve inside the extracted plugin tree.
    """
    foundations_dir = repo_root / "skills" / "foundations"
    if not foundations_dir.exists() or not foundations_dir.is_dir():
        return []
    return sorted(
        (
            entry
            for entry in foundations_dir.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(".")
            and entry.name.startswith("vc-")
            and (entry / "SKILL.md").exists()
        ),
        key=lambda path: path.name,
    )


def should_skip_path(path: Path) -> bool:
    """True if `path` is a compiled artifact or lives under an ignored dir part."""
    if path.name.endswith(".pyc"):
        return True
    return any(part in IGNORED_PATH_PARTS for part in path.parts)


def iter_skill_files(skill_dir: Path) -> list[Path]:
    """List every non-ignored file under `skill_dir`, sorted for stable zips."""
    return sorted(
        (
            path
            for path in skill_dir.rglob("*")
            if path.is_file() and not should_skip_path(path.relative_to(skill_dir))
        ),
        key=lambda path: path.relative_to(skill_dir).as_posix(),
    )


def iter_bundled_tool_files(repo_root: Path) -> list[Path]:
    """Yield notarized drop-in binaries staged under tools/bin/.

    Layout expected by install-foundations.sh / installer_gui.py:
      tools/bin/<os>-<arch>/<binary>   — per-arch (preferred for multi-target releases)
      tools/bin/<binary>               — flat fallback for single-arch dev drops
      tools/bin/README.md              — drop-in convention docs (always shipped)

    Missing or empty directory returns []. Subdirectories other than the
    recognized <os>-<arch> shape (e.g. .DS_Store) still ship but should be
    avoided by convention.
    """
    bin_dir = repo_root / "tools" / "bin"
    if not bin_dir.is_dir():
        return []
    return sorted(
        (
            path
            for path in bin_dir.rglob("*")
            if path.is_file()
            and not should_skip_path(path.relative_to(bin_dir))
            and path.name != ".gitkeep"
        ),
        key=lambda path: path.relative_to(bin_dir).as_posix(),
    )


def plugin_manifest(version: str, metadata: ListingMetadata) -> dict[str, object]:
    """Build the ``.claude-plugin/plugin.json`` manifest dict."""
    return {
        "name": PLUGIN_NAME,
        "version": version,
        "description": metadata.description,
        "author": {
            "name": "Vetcoders",
            "email": "hello@vetcoders.io",
        },
        "homepage": metadata.homepage,
        "repository": metadata.repository,
        "license": metadata.license,
        "keywords": list(metadata.keywords),
    }


def mcp_config() -> dict[str, object]:
    """Build the bundle's ``.mcp.json`` default loctree server config.

    Canon transport for runs is streamable HTTP, not a stdio server spawned
    per run: a single scan-only `loct watch --bg` keeps the root indexed;
    127.0.0.1:5174/mcp and every agent shell on that root shares it. This is a
    shipped, root-less template, so it documents the default port; vibecrafted's
    own per-run wiring derives the port per root. Source of truth for this shape
    is vibecrafted_core.perception.default_loctree_mcp_config_entry().
    """
    return {
        "mcpServers": {
            "loctree": {
                "type": "http",
                "url": "http://127.0.0.1:5174/mcp",
            }
        }
    }


def write_zip_entry(
    bundle: zipfile.ZipFile, arcname: str, data: bytes, mode: int
) -> None:
    """Write one deterministic-timestamp, mode-preserving entry into `bundle`."""
    info = zipfile.ZipInfo(arcname, FIXED_ZIP_DATE_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (mode & 0xFFFF) << 16
    bundle.writestr(info, data)


def build_bundle_bytes(repo_root: Path) -> bytes:
    """Assemble the full marketplace .plugin zip in memory and return its bytes."""
    version = read_version(repo_root)
    metadata = load_listing_metadata(repo_root)
    listing_path = repo_root / "docs" / "MARKETPLACE_LISTING.md"

    generated_files = {
        ".claude-plugin/plugin.json": json.dumps(
            plugin_manifest(version, metadata), indent=2
        )
        + "\n",
        ".mcp.json": json.dumps(mcp_config(), indent=2) + "\n",
        "README.md": listing_path.read_text(encoding="utf-8").rstrip() + "\n",
        "LICENSE": (repo_root / "LICENSE").read_text(encoding="utf-8").rstrip() + "\n",
        "VERSION": version + "\n",
    }
    for relative_path in SUPPORT_DOC_PATHS:
        generated_files[relative_path] = (repo_root / relative_path).read_text(
            encoding="utf-8"
        ).rstrip() + "\n"

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for arcname, text in generated_files.items():
            write_zip_entry(
                bundle,
                arcname,
                text.encode("utf-8"),
                DEFAULT_FILE_MODE,
            )

        for skill_dir in discover_bundle_skills(repo_root):
            for source_file in iter_skill_files(skill_dir):
                relative = source_file.relative_to(skill_dir).as_posix()
                arcname = f"skills/{skill_dir.name}/{relative}"
                write_zip_entry(
                    bundle,
                    arcname,
                    source_file.read_bytes(),
                    source_file.stat().st_mode,
                )

        for foundation_dir in discover_foundation_skills(repo_root):
            for source_file in iter_skill_files(foundation_dir):
                relative = source_file.relative_to(foundation_dir).as_posix()
                arcname = f"skills/foundations/{foundation_dir.name}/{relative}"
                write_zip_entry(
                    bundle,
                    arcname,
                    source_file.read_bytes(),
                    source_file.stat().st_mode,
                )

        bundled_bin_root = repo_root / "tools" / "bin"
        for source_file in iter_bundled_tool_files(repo_root):
            relative = source_file.relative_to(bundled_bin_root).as_posix()
            arcname = f"tools/bin/{relative}"
            write_zip_entry(
                bundle,
                arcname,
                source_file.read_bytes(),
                source_file.stat().st_mode,
            )

    return buffer.getvalue()


def write_bundle(repo_root: Path, output_path: Path) -> None:
    """Build the bundle bytes and write them to `output_path`, making dirs as needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(build_bundle_bytes(repo_root))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the ``--output`` CLI flag for the bundle build script."""
    parser = argparse.ArgumentParser(
        description="Build the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. marketplace bundle from current repo state."
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / OUTPUT_FILENAME),
        help="Path to the .plugin zip to write.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: build the marketplace bundle and print a summary."""
    args = parse_args(argv)
    output_path = Path(args.output).expanduser().resolve()

    write_bundle(REPO_ROOT, output_path)
    print(
        f"Built {output_path} from 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. {read_version(REPO_ROOT)} "
        f"with {len(discover_bundle_skills(REPO_ROOT))} current skills."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
