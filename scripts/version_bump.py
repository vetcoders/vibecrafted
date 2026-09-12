#!/usr/bin/env python3
"""CLI: bump VERSION and mirror the new semver into every packaged declaration
(plugin.json, vibecrafted-core/vibecrafted-mcp pyproject.toml, packaged VERSION
files, and the server crates' Cargo.toml [package] versions)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
PROJECT_VERSION_RE = re.compile(
    r'^(?P<prefix>\s*version\s*=\s*")(?P<version>[^"]+)(?P<suffix>".*)$'
)
PYPROJECT_RELATIVES = (
    Path("vibecrafted-core/pyproject.toml"),
    Path("vibecrafted-mcp/pyproject.toml"),
)
PACKAGED_VERSION_RELATIVES = (
    Path("vibecrafted-core/vibecrafted_core/VERSION"),
    Path("vibecrafted-mcp/vibecrafted_mcp/VERSION"),
)
# Server crates version like the rest of the product: their `[package]`
# version mirrors VERSION so `vc-server --version` (build.rs stamps VERSION +
# git sha on top) agrees with `vibecrafted --version`.
CARGO_RELATIVES = (
    Path("vibecrafted-server/web/Cargo.toml"),
    Path("vibecrafted-server/control-core/Cargo.toml"),
)
CARGO_LOCK_PACKAGES = {
    Path("vibecrafted-server/Cargo.lock"): (
        "control-core",
        "vibecrafted-server-web",
    ),
    Path("vibecrafted-app/Cargo.lock"): ("control-core",),
}
PLUGIN_MANIFEST_RELATIVE = Path("plugin.json")
RELEASE_TEXT_PROJECTIONS = {
    Path("vibecrafted-app/shell-agent/app/project.yml"): (
        re.compile(
            r'^\s*MARKETING_VERSION:\s*"(?P<version>\d+\.\d+\.\d+)"',
            re.MULTILINE,
        ),
    ),
    Path("packaging/homebrew/Formula/vibecrafted.rb"): (
        re.compile(r'^\s*version\s+"(?P<version>\d+\.\d+\.\d+)"', re.MULTILINE),
    ),
    Path("packaging/homebrew/Casks/vibecrafted-app.rb"): (
        re.compile(r'^\s*version\s+"(?P<version>\d+\.\d+\.\d+),', re.MULTILINE),
    ),
    Path("README.md"): (
        re.compile(r'<img alt="Version (?P<version>\d+\.\d+\.\d+)"'),
        re.compile(r"badge/version-(?P<version>\d+\.\d+\.\d+)-informational"),
    ),
    Path("docs/RELEASE_CHECKLIST.md"): (
        re.compile(r"^# Cut (?P<version>\d+\.\d+\.\d+)\b", re.MULTILINE),
        re.compile(r"One GitHub Release `v(?P<version>\d+\.\d+\.\d+)`"),
        re.compile(r"`VERSION` is already `(?P<version>\d+\.\d+\.\d+)`"),
        re.compile(
            r'test "\$\(tr -d \'\[:space:\]\' < VERSION\)" = "(?P<version>\d+\.\d+\.\d+)"'
        ),
        re.compile(r"git tag -a v(?P<version>\d+\.\d+\.\d+) -m"),
        re.compile(r"git push origin v(?P<version>\d+\.\d+\.\d+)"),
    ),
}


def check_version_declarations(version_file: Path) -> str:
    """Fail when a packaged product projection drifts from root VERSION."""
    current = version_file.read_text(encoding="utf-8").strip()
    _parse_version(current)
    projections = _version_projections(version_file)
    if projections is None:
        return current
    pyprojects, packaged_versions, cargos = projections
    declared: dict[Path, str] = {
        path: _table_version(path.read_text(encoding="utf-8"), "project")
        for path in pyprojects
    }
    declared.update(
        {path: path.read_text(encoding="utf-8").strip() for path in packaged_versions}
    )
    declared.update(
        {
            path: _table_version(path.read_text(encoding="utf-8"), "package")
            for path in cargos
        }
    )
    plugin_path = version_file.parent / PLUGIN_MANIFEST_RELATIVE
    if plugin_path.exists():
        declared[plugin_path] = str(
            json.loads(plugin_path.read_text(encoding="utf-8")).get("version", "")
        )
    for relative, patterns in RELEASE_TEXT_PROJECTIONS.items():
        path = version_file.parent / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for index, pattern in enumerate(patterns, start=1):
            match = pattern.search(text)
            if match is None:
                raise ValueError(
                    f"version declaration missing: {path} projection {index}"
                )
            declared[Path(f"{path}#projection-{index}")] = match.group("version")
    drift = {path: value for path, value in declared.items() if value != current}
    if drift:
        details = ", ".join(f"{path}={value}" for path, value in drift.items())
        raise ValueError(
            f"Version drift detected; expected {current} in every declaration: {details}"
        )
    return current


def _parse_version(value: str) -> tuple[int, int, int]:
    """Parse a plain ``X.Y.Z`` semver string; raise ValueError on any other shape."""
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"VERSION must be plain semver X.Y.Z, got {value!r}")
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def resolve_next_version(current: str, requested: str) -> str:
    """Bump patch/minor/major from ``current``, or validate and pass through an
    explicit ``X.Y.Z`` in ``requested``."""
    major, minor, patch = _parse_version(current)
    if requested == "patch":
        patch += 1
    elif requested == "minor":
        minor += 1
        patch = 0
    elif requested == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        _parse_version(requested)
        return requested
    return f"{major}.{minor}.{patch}"


def _table_version(text: str, table: str) -> str:
    """Extract the ``version = "..."`` value from a TOML ``[table]`` section
    (``[project]`` for pyproject, ``[package]`` for Cargo); raise ValueError if
    that table or its version key is absent."""
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = stripped == f"[{table}]"
            continue
        if in_table and (match := PROJECT_VERSION_RE.match(line)):
            return match.group("version")
    raise ValueError(f"TOML file has no [{table}] version declaration")


def _replace_table_version(text: str, version: str, table: str) -> str:
    """Return ``text`` with the first ``[table]`` ``version = "..."`` line
    rewritten to ``version``, preserving line endings; raise ValueError if no
    such declaration is found."""
    in_table = False
    output: list[str] = []
    replaced = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = stripped == f"[{table}]"
        if in_table and not replaced:
            body = line.rstrip("\r\n")
            newline = line[len(body) :]
            if match := PROJECT_VERSION_RE.match(body):
                line = (
                    f"{match.group('prefix')}{version}{match.group('suffix')}{newline}"
                )
                replaced = True
        output.append(line)
    if not replaced:
        raise ValueError(f"TOML file has no [{table}] version declaration")
    return "".join(output)


def _replace_lock_package_versions(
    text: str,
    package_names: tuple[str, ...],
    current: str,
    next_version: str,
) -> str:
    """Update local product packages in a Cargo.lock without invoking Cargo.

    Registry dependencies may legitimately share a package name or product-like
    version, so only the explicitly owned package blocks are rewritten. Every
    expected package must occur exactly once and agree with VERSION before any
    file is written.
    """
    lines = text.splitlines(keepends=True)
    found: dict[str, tuple[int, str]] = {}
    block_name: str | None = None
    block_version_index: int | None = None
    block_version: str | None = None

    def finish_block() -> None:
        if block_name not in package_names:
            return
        if block_version_index is None or block_version is None:
            raise ValueError(f"Cargo.lock package {block_name!r} has no version")
        if block_name in found:
            raise ValueError(f"Cargo.lock package {block_name!r} is duplicated")
        found[block_name] = (block_version_index, block_version)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[[package]]":
            finish_block()
            block_name = None
            block_version_index = None
            block_version = None
            continue
        if block_name is None and stripped.startswith('name = "'):
            block_name = stripped.removeprefix('name = "').removesuffix('"')
        elif block_version_index is None and stripped.startswith('version = "'):
            block_version_index = index
            block_version = stripped.removeprefix('version = "').removesuffix('"')
    finish_block()

    missing = [name for name in package_names if name not in found]
    if missing:
        raise ValueError(f"Cargo.lock package missing: {', '.join(missing)}")
    drift = {name: value for name, (_, value) in found.items() if value != current}
    if drift:
        details = ", ".join(f"{name}={value}" for name, value in drift.items())
        raise ValueError(
            f"Version drift detected; expected {current} in Cargo.lock: {details}"
        )
    for index, _value in found.values():
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f'version = "{next_version}"{newline}'
    return "".join(lines)


def _version_projections(
    version_file: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...], tuple[Path, ...]] | None:
    """Locate the sibling pyproject.toml/VERSION/Cargo.toml files this VERSION
    mirrors into.

    Returns None when none of the python projection paths exist (VERSION stands
    alone). Raises ValueError if only some of a group exist (partial layout).
    The Cargo group is optional as a whole — a python-only layout stays valid —
    but must be internally complete when any crate manifest is present.
    """
    project_root = version_file.parent
    pyprojects = tuple(project_root / path for path in PYPROJECT_RELATIVES)
    packaged = tuple(project_root / path for path in PACKAGED_VERSION_RELATIVES)
    cargos = tuple(project_root / path for path in CARGO_RELATIVES)
    projections = pyprojects + packaged
    existing = tuple(path.exists() for path in projections)
    cargo_existing = tuple(path.exists() for path in cargos)
    if not any(existing) and not any(cargo_existing):
        return None
    if any(existing) and not all(existing):
        missing = [
            str(path) for path, present in zip(projections, existing) if not present
        ]
        raise ValueError(f"version declaration missing: {', '.join(missing)}")
    if any(cargo_existing) and not all(cargo_existing):
        missing = [
            str(path) for path, present in zip(cargos, cargo_existing) if not present
        ]
        raise ValueError(f"version declaration missing: {', '.join(missing)}")
    return pyprojects, packaged, cargos if all(cargo_existing) else ()


def update_version_declarations(version_file: Path, requested: str) -> tuple[str, str]:
    """Bump ``version_file`` and every mirrored pyproject/VERSION declaration.

    Verifies all declarations agree with the current VERSION first (raises
    ValueError on drift), writes the new version to each, and returns
    ``(current, next_version)``.
    """
    current = version_file.read_text(encoding="utf-8").strip()
    next_version = resolve_next_version(current, requested)
    projections = _version_projections(version_file)

    updates = {version_file: next_version + "\n"}
    if projections is not None:
        pyprojects, packaged_versions, cargos = projections
        pyproject_texts = {
            path: path.read_text(encoding="utf-8") for path in pyprojects
        }
        cargo_texts = {path: path.read_text(encoding="utf-8") for path in cargos}
        declared = {version_file: current}
        declared.update(
            {
                path: _table_version(text, "project")
                for path, text in pyproject_texts.items()
            }
        )
        plugin_path = version_file.parent / PLUGIN_MANIFEST_RELATIVE
        plugin_payload: dict[str, object] | None = None
        if plugin_path.exists():
            plugin_payload = json.loads(plugin_path.read_text(encoding="utf-8"))
            declared[plugin_path] = str(plugin_payload.get("version", ""))
        release_texts: dict[Path, tuple[str, tuple[re.Pattern[str], ...]]] = {}
        for relative, patterns in RELEASE_TEXT_PROJECTIONS.items():
            path = version_file.parent / relative
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for index, pattern in enumerate(patterns, start=1):
                match = pattern.search(text)
                if match is None:
                    raise ValueError(
                        f"version declaration missing: {path} projection {index}"
                    )
                declared[Path(f"{path}#projection-{index}")] = match.group("version")
            release_texts[path] = (text, patterns)
        declared.update(
            {
                path: _table_version(text, "package")
                for path, text in cargo_texts.items()
            }
        )
        declared.update(
            {
                path: path.read_text(encoding="utf-8").strip()
                for path in packaged_versions
            }
        )
        drift = {path: value for path, value in declared.items() if value != current}
        if drift:
            details = ", ".join(f"{path}={value}" for path, value in drift.items())
            raise ValueError(
                f"Version drift detected; expected {current} in every declaration: {details}"
            )
        updates.update(
            {
                path: _replace_table_version(text, next_version, "project")
                for path, text in pyproject_texts.items()
            }
        )
        updates.update(
            {
                path: _replace_table_version(text, next_version, "package")
                for path, text in cargo_texts.items()
            }
        )
        updates.update({path: next_version + "\n" for path in packaged_versions})
        if plugin_payload is not None:
            plugin_payload["version"] = next_version
            updates[plugin_path] = json.dumps(plugin_payload, indent=2) + "\n"
        for path, (text, patterns) in release_texts.items():
            updated = text
            for pattern in patterns:
                updated = pattern.sub(
                    lambda match: match.group(0).replace(
                        match.group("version"), next_version, 1
                    ),
                    updated,
                    count=1,
                )
            updates[path] = updated
        for relative, package_names in CARGO_LOCK_PACKAGES.items():
            lock_path = version_file.parent / relative
            if not lock_path.exists():
                continue
            updates[lock_path] = _replace_lock_package_versions(
                lock_path.read_text(encoding="utf-8"),
                package_names,
                current,
                next_version,
            )

    for path, content in updates.items():
        path.write_text(content, encoding="utf-8")
    return current, next_version


def main() -> int:
    """CLI entrypoint: bump VERSION (and mirrors) per argv, print the result."""
    parser = argparse.ArgumentParser(
        description="Bump VERSION and every packaged version declaration.",
    )
    parser.add_argument("version", nargs="?", help="{patch|minor|major|x.y.z}")
    parser.add_argument("--file", default="VERSION", help="VERSION file path")
    parser.add_argument("--check", action="store_true", help="verify projections only")
    args = parser.parse_args()

    version_file = Path(args.file)
    try:
        if args.check:
            current = check_version_declarations(version_file)
            print(f"Version projections agree: v{current}")
            return 0
        if args.version is None:
            parser.error("version is required unless --check is used")
        current, next_version = update_version_declarations(version_file, args.version)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Bumped: v{current} -> v{next_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
