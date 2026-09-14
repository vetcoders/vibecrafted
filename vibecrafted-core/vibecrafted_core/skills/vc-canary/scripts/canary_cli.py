#!/usr/bin/env -S uv run --python 3.12 --with nothing
# canary_cli.py — skill-first repo-atlas helpers for vc-canary.
# Stdlib only. Does not load full snapshot into memory as agent context —
# streams to JSONL with coverage receipts.
"""Ownership-catalog atlas CLI (vc-canary).

Subcommands:
  snapshot-path  Resolve loctree cache snapshot for a project root
  repo-view      Write loct repo-view JSON under ./.loctree/
  atlas          Build repo-atlas.v1 (sense pack + inventory JSONL + signals)
  merge-catalog  Merge per-scope catalog JSON files
  diff-audit     Report suspicious deletions in a git diff (no revert)
  coverage       Print coverage receipt for an existing atlas dir

All data subcommands write files (default under ./.loctree/atlas/) and print a
short status line to stdout.
"""

from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATLAS_SCHEMA = "loctree.repo-atlas.v1"
DEFAULT_COVERAGE_FLOOR = 0.95
# Default atlas inventory focuses on code-ish languages (canary ownership surface).
# Pass --all-languages to include md/json/html/…
DEFAULT_CODE_LANGUAGES = frozenset(
    {
        "rs",
        "py",
        "swift",
        "ts",
        "tsx",
        "js",
        "jsx",
        "shell",
        "bash",
        "zsh",
        "make",
        "toml",
        "go",
        "java",
        "kt",
        "rb",
        "c",
        "cpp",
        "h",
        "hpp",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _git_ls_files_count(root: Path) -> int | None:
    r = _run(["git", "ls-files"], root)
    if r.returncode != 0:
        return None
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return len(lines)


def _git_rev(root: Path) -> tuple[str | None, str | None]:
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root)
    commit = _run(["git", "rev-parse", "--short=8", "HEAD"], root)
    b = branch.stdout.strip() if branch.returncode == 0 else None
    c = commit.stdout.strip() if commit.returncode == 0 else None
    return b, c


@dataclass
class CacheHit:
    pack_dir: Path
    snapshot: Path
    agent: Path | None
    findings: Path | None
    analysis: Path | None
    manifest: Path | None
    mtime: float


@dataclass(frozen=True)
class LanguagePlugin:
    """Validated catalog contract loaded from one vc-canary plugin file."""

    name: str
    globs: tuple[str, ...]
    kind_enum: tuple[str, ...]
    required_fields: tuple[str, ...]


def load_language_plugins() -> tuple[LanguagePlugin, ...]:
    """Load and validate every language contract shipped beside this CLI."""
    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    if not plugins_dir.is_dir():
        _die(f"canary language plugin directory missing: {plugins_dir}")

    plugins: list[LanguagePlugin] = []
    for plugin_path in sorted(plugins_dir.glob("*.py")):
        if plugin_path.name.startswith("_"):
            continue
        module_name = f"_vc_canary_plugin_{plugin_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, plugin_path)
        if spec is None or spec.loader is None:
            _die(f"cannot load language plugin: {plugin_path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:  # noqa: BLE001 - surface plugin failures as catalog errors.
            _die(f"cannot load language plugin {plugin_path.name}: {e}")

        values = {
            field: getattr(module, field, None)
            for field in ("GLOBS", "KIND_ENUM", "REQUIRED_FIELDS")
        }
        invalid = [
            field
            for field, value in values.items()
            if not isinstance(value, tuple)
            or not value
            or not all(isinstance(item, str) and item for item in value)
        ]
        if invalid:
            _die(f"language plugin {plugin_path.name} has invalid {', '.join(invalid)}")
        plugins.append(
            LanguagePlugin(
                name=plugin_path.name,
                globs=values["GLOBS"],
                kind_enum=values["KIND_ENUM"],
                required_fields=values["REQUIRED_FIELDS"],
            )
        )

    if not plugins:
        _die(f"no language plugins found in {plugins_dir}")
    return tuple(plugins)


def plugin_for_file(
    file_path: str, plugins: tuple[LanguagePlugin, ...]
) -> LanguagePlugin | None:
    """Resolve one plugin by the catalog unit's source filename."""
    filename = Path(file_path).name
    matches = [
        plugin
        for plugin in plugins
        if any(fnmatch.fnmatch(filename, glob) for glob in plugin.globs)
    ]
    if len(matches) > 1:
        _die(
            f"ambiguous language plugins for catalog file {file_path!r}: "
            f"{', '.join(plugin.name for plugin in matches)}"
        )
    return matches[0] if matches else None


def validate_unit_path_integrity(
    catalog_path: Path, unit_index: int, unit: dict
) -> str:
    """File-path shape checks that hold in BOTH strict and --no-strict merges."""
    from pathlib import PurePosixPath

    location = f"catalog {catalog_path} unit[{unit_index}]"
    raw_file = unit.get("file")
    if not isinstance(raw_file, str) or not raw_file.strip():
        _die(f"{location}: missing required field 'file'")
    unit_path = PurePosixPath(raw_file.strip())
    if unit_path.is_absolute() or ".." in unit_path.parts:
        # An ownership catalog settles THIS repository only: absolute paths and
        # parent-escapes would let a unit claim files outside the root.
        _die(
            f"{location}: 'file' must be a relative, non-escaping repository "
            f"path ({raw_file!r})"
        )
    if raw_file != raw_file.strip():
        # A trailing/leading space would dodge the language plugin AND leak an
        # unresolvable path into the merged catalog — reject, never normalize.
        _die(
            f"{location}: 'file' has surrounding whitespace ({raw_file!r}); "
            "emit the exact repository path"
        )
    return raw_file


SHARED_REQUIRED_FIELDS = (
    "file",
    "name",
    "line",
    "kind",
    "role",
    "docstring_added",
    "authority",
)


def validate_catalog_unit(
    catalog_path: Path,
    unit_index: int,
    unit: Any,
    plugins: tuple[LanguagePlugin, ...],
) -> None:
    """Fail closed on the first per-unit plugin contract violation."""
    location = f"catalog {catalog_path} unit[{unit_index}]"
    if not isinstance(unit, dict):
        _die(f"{location}: unit must be an object")

    raw_file = validate_unit_path_integrity(catalog_path, unit_index, unit)
    plugin = plugin_for_file(raw_file, plugins)
    if plugin is None:
        # Languages the atlas inventories without a dedicated plugin (swift,
        # make, go, java, …) still validate fail-closed against the shared
        # required-field contract; only the per-language kind enum is waived.
        for field in SHARED_REQUIRED_FIELDS:
            value = unit.get(field)
            if (
                field not in unit
                or value is None
                or (isinstance(value, str) and not value.strip())
            ):
                _die(
                    f"{location} file {raw_file!r} (no language plugin; shared "
                    f"contract): missing required field {field!r}"
                )
        kind = unit.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            _die(
                f"{location} file {raw_file!r} (no language plugin; shared "
                "contract): 'kind' must be a non-empty string"
            )
        return

    for field in plugin.required_fields:
        value = unit.get(field)
        if (
            field not in unit
            or value is None
            or (isinstance(value, str) and not value.strip())
        ):
            _die(
                f"{location} file {raw_file!r} plugin {plugin.name}: "
                f"missing required field {field!r}"
            )

    kind = unit.get("kind")
    if not isinstance(kind, str) or kind not in plugin.kind_enum:
        _die(
            f"{location} file {raw_file!r} plugin {plugin.name}: "
            f"invalid kind {kind!r}; expected one of {', '.join(plugin.kind_enum)}"
        )


def validate_catalog(
    catalog_path: Path,
    catalog: list[Any],
    plugins: tuple[LanguagePlugin, ...],
) -> None:
    """Validate every unit before merge output can be written."""
    for unit_index, unit in enumerate(catalog):
        validate_catalog_unit(catalog_path, unit_index, unit, plugins)


def resolve_cache_pack(root: Path) -> CacheHit | None:
    """Find newest loctree cache pack whose agent.json project matches root."""
    cache_root = Path.home() / "Library/Caches/loctree/projects"
    if not cache_root.is_dir():
        # Linux / other
        xdg = os.environ.get("XDG_CACHE_HOME")
        if xdg:
            cache_root = Path(xdg) / "loctree/projects"
        else:
            cache_root = Path.home() / ".cache/loctree/projects"
    if not cache_root.is_dir():
        return None

    root_res = root.resolve()
    hits: list[CacheHit] = []
    for proj in cache_root.iterdir():
        if not proj.is_dir():
            continue
        for pack in proj.iterdir():
            if not pack.is_dir():
                continue
            snap = pack / "snapshot.json"
            if not snap.is_file():
                continue
            agent_p = pack / "agent.json"
            proj_path: str | None = None
            if agent_p.is_file():
                try:
                    data = json.loads(agent_p.read_text(encoding="utf-8"))
                    proj_path = data.get("project")
                except (OSError, json.JSONDecodeError):
                    proj_path = None
            if not proj_path:
                # fall back: snapshot metadata roots
                try:
                    # only read first 64KB — metadata is at top of file as JSON
                    # Full parse of 80MB is OK once when matched; skip if project unknown
                    pass
                except OSError:
                    continue
                continue
            try:
                if Path(proj_path).resolve() != root_res:
                    continue
            except OSError:
                continue
            hits.append(
                CacheHit(
                    pack_dir=pack,
                    snapshot=snap,
                    agent=agent_p if agent_p.is_file() else None,
                    findings=(pack / "findings.json")
                    if (pack / "findings.json").is_file()
                    else None,
                    analysis=(pack / "analysis.json")
                    if (pack / "analysis.json").is_file()
                    else None,
                    manifest=(pack / "manifest.json")
                    if (pack / "manifest.json").is_file()
                    else None,
                    mtime=snap.stat().st_mtime,
                )
            )
    if not hits:
        return None
    hits.sort(key=lambda h: h.mtime, reverse=True)
    # Prefer 'latest' when present among top mtime cluster
    for h in hits:
        if h.pack_dir.name == "latest":
            return h
    return hits[0]


def load_snapshot_files(
    snapshot_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Load snapshot JSON (may be large). Returns (metadata, files)."""
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        _die(f"snapshot is not an object: {snapshot_path}")
    files = data.get("files")
    if not isinstance(files, list):
        _die(f"snapshot missing files[]: {snapshot_path}")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return meta, files


def unit_count(file_rec: dict[str, Any]) -> int:
    exports = file_rec.get("exports") or []
    locals_ = file_rec.get("local_symbols") or []
    n_exp = len(exports) if isinstance(exports, list) else 0
    n_loc = len(locals_) if isinstance(locals_, list) else 0
    # +1 module entry (playbook convention)
    return n_exp + n_loc + 1


def inventory_rows(
    files: Iterable[dict[str, Any]],
    *,
    include_tests: bool = False,
    include_generated: bool = False,
    languages: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path") or ""
        if not path:
            continue
        is_test = bool(f.get("is_test"))
        is_gen = bool(f.get("is_generated"))
        if is_test and not include_tests:
            continue
        if is_gen and not include_generated:
            continue
        lang = (f.get("language") or "").strip().lower()
        # empty lang still included unless filter set
        if languages is not None and lang != "" and lang not in languages:
            continue
        yield {
            "path": path,
            "lang": lang or "unknown",
            "loc": int(f.get("loc") or 0),
            "units": unit_count(f),
            "is_test": is_test,
            "is_generated": is_gen,
            "kind": f.get("kind") or f.get("resource_kind") or "",
        }


def top_dir(path: str) -> str:
    parts = Path(path).parts
    if not parts:
        return "."
    if parts[0] in (".",):
        return parts[1] if len(parts) > 1 else "."
    return parts[0]


def build_planes_hint(
    rows: list[dict[str, Any]], hubs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Heuristic planes from inventory + hubs (sense input, not final scopes)."""
    by_dir: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"files": 0, "loc": 0, "units": 0, "langs": Counter()}
    )
    for r in rows:
        d = top_dir(r["path"])
        by_dir[d]["files"] += 1
        by_dir[d]["loc"] += r["loc"]
        by_dir[d]["units"] += r["units"]
        by_dir[d]["langs"][r["lang"]] += 1

    hub_dirs = Counter()
    for h in hubs:
        p = h.get("path") if isinstance(h, dict) else None
        if p:
            hub_dirs[top_dir(p)] += 1

    ranked = sorted(
        by_dir.items(),
        key=lambda kv: (hub_dirs.get(kv[0], 0), kv[1]["loc"], kv[1]["units"]),
        reverse=True,
    )
    planes: list[dict[str, Any]] = []
    for name, stats in ranked:
        if name.startswith(".") and name not in {".github"}:
            continue
        if stats["files"] < 2 and hub_dirs.get(name, 0) == 0:
            continue
        top_lang = stats["langs"].most_common(1)[0][0] if stats["langs"] else "unknown"
        why_bits = [
            f"{stats['files']} files",
            f"{stats['loc']} loc",
            f"lang={top_lang}",
        ]
        if hub_dirs.get(name):
            why_bits.append(f"{hub_dirs[name]} hubs")
        planes.append(
            {
                "id": name.replace("/", "-") or "root",
                "paths": [f"{name}/"] if name != "." else ["."],
                "why": ", ".join(why_bits),
                "est_files": stats["files"],
                "est_units": stats["units"],
                "est_loc": stats["loc"],
            }
        )
    return planes


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def cmd_snapshot_path(args: argparse.Namespace) -> int:
    root = Path(args.root or os.getcwd()).resolve()
    hit = resolve_cache_pack(root)
    if not hit:
        _die(f"no loctree cache pack for {root}; run: loct auto  (in that directory)")
    out = {
        "project": str(root),
        "pack_dir": str(hit.pack_dir),
        "snapshot": str(hit.snapshot),
        "agent": str(hit.agent) if hit.agent else None,
        "findings": str(hit.findings) if hit.findings else None,
        "analysis": str(hit.analysis) if hit.analysis else None,
        "manifest": str(hit.manifest) if hit.manifest else None,
        "snapshot_bytes": hit.snapshot.stat().st_size,
    }
    if args.output:
        write_json(Path(args.output), out)
        print(f"Success! snapshot-path written to: {args.output}")
    else:
        print(json.dumps(out, indent=2))
    return 0


def cmd_repo_view(args: argparse.Namespace) -> int:
    root = Path(args.root or os.getcwd()).resolve()
    out_path = Path(args.output or root / ".loctree" / "loct-repo-view.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["loct", "repo-view", "--json"], root)
    if r.returncode != 0:
        _die(f"loct repo-view failed: {r.stderr.strip() or r.stdout.strip()}")
    out_path.write_text(r.stdout, encoding="utf-8")
    print(f"Success! repo-view written to: {out_path}")
    return 0


def cmd_atlas(args: argparse.Namespace) -> int:
    root = Path(args.root or os.getcwd()).resolve()
    atlas_dir = Path(args.atlas_dir or root / ".loctree" / "atlas")
    floor = float(args.coverage_floor)
    include_tests = bool(args.include_tests)
    include_generated = bool(args.include_generated)
    if args.languages:
        lang_filter = {
            x.strip().lower() for x in args.languages.split(",") if x.strip()
        }
    elif args.all_languages:
        lang_filter = None
    else:
        lang_filter = set(DEFAULT_CODE_LANGUAGES)

    if args.refresh:
        print("running loct auto …", file=sys.stderr)
        ar = _run(["loct", "auto"], root)
        if ar.returncode != 0:
            _die(f"loct auto failed: {ar.stderr.strip() or ar.stdout.strip()}")

    # Always refresh repo-view for sense (cheap)
    rv_path = root / ".loctree" / "loct-repo-view.json"
    rv_path.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["loct", "repo-view", "--json"], root)
    if r.returncode != 0:
        _die(f"loct repo-view failed: {r.stderr.strip() or r.stdout.strip()}")
    rv_path.write_text(r.stdout, encoding="utf-8")
    try:
        repo_view = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        _die(f"repo-view JSON invalid: {e}")

    hit = resolve_cache_pack(root)
    if not hit:
        _die(
            "no loctree snapshot cache for this project; loct auto may have failed "
            "or project path mismatch"
        )

    meta, files = load_snapshot_files(hit.snapshot)
    snap_count = int(meta.get("file_count") or len(files))
    rows = list(
        inventory_rows(
            files,
            include_tests=include_tests,
            include_generated=include_generated,
            languages=lang_filter,
        )
    )
    inv_path = atlas_dir / "inventory.jsonl"
    inv_n = write_jsonl(inv_path, rows)

    # Coverage: inventory vs snapshot (after filters inventory may be lower)
    # Primary receipt uses snapshot file_count as denominator for "did we see full scan"
    ratio_vs_snapshot = (len(files) / snap_count) if snap_count else 0.0
    git_n = _git_ls_files_count(root)
    branch, commit = _git_rev(root)

    coverage = {
        "snapshot_files": snap_count,
        "snapshot_files_loaded": len(files),
        "inventory_files": inv_n,
        "inventory_ratio_vs_snapshot_loaded": round(inv_n / len(files), 4)
        if files
        else 0.0,
        "snapshot_load_ratio": round(ratio_vs_snapshot, 4),
        "git_tracked": git_n,
        "filters": {
            "include_tests": include_tests,
            "include_generated": include_generated,
            "languages": sorted(lang_filter) if lang_filter else None,
        },
        "coverage_floor": floor,
        "pass": ratio_vs_snapshot >= floor and len(files) > 0,
    }

    hubs = []
    if isinstance(repo_view, dict):
        hubs = repo_view.get("hub_files") or []
        if not isinstance(hubs, list):
            hubs = []

    planes = build_planes_hint(rows, hubs if isinstance(hubs, list) else [])

    # Thin signals from findings if present
    signals: dict[str, Any] = {"source": None, "summary": None, "top": {}}
    if hit.findings and hit.findings.is_file():
        try:
            findings = json.loads(hit.findings.read_text(encoding="utf-8"))
            signals["source"] = str(hit.findings)
            signals["summary"] = findings.get("summary")
            signals["top"] = {
                "dead_parrots": (findings.get("dead_parrots") or [])[:20],
                "cycles": (findings.get("cycles") or [])[:10],
                "duplicates": (findings.get("duplicates") or [])[:15],
                "quick_wins": (findings.get("quick_wins") or [])[:10],
            }
            write_json(atlas_dir / "signals.json", signals)
        except (OSError, json.JSONDecodeError) as e:
            signals["error"] = str(e)

    hubs_out = hubs[:25] if isinstance(hubs, list) else []
    write_json(atlas_dir / "hubs.json", hubs_out)
    write_json(atlas_dir / "planes_hint.json", planes)
    write_json(atlas_dir / "coverage.json", coverage)

    # lang histogram
    langs = Counter(r["lang"] for r in rows)

    atlas = {
        "schema": ATLAS_SCHEMA,
        "generated": _utc_now(),
        "meta": {
            "project": str(root),
            "branch": branch or meta.get("git_branch"),
            "commit": commit or meta.get("git_commit"),
            "snapshot_path": str(hit.snapshot),
            "pack_dir": str(hit.pack_dir),
            "repo_view_path": str(rv_path),
            "loctree": None,
            "coverage": coverage,
        },
        "summary": {
            "inventory_files": inv_n,
            "inventory_units": sum(r["units"] for r in rows),
            "inventory_loc": sum(r["loc"] for r in rows),
            "languages": dict(langs.most_common()),
            "planes_hint_count": len(planes),
            "hubs_count": len(hubs_out),
            "repo_view_files_analyzed": (repo_view.get("summary") or {}).get(
                "files_analyzed"
            )
            if isinstance(repo_view, dict)
            else None,
        },
        "planes_hint": planes,
        "inventory_uri": str(inv_path.relative_to(root))
        if inv_path.is_relative_to(root)
        else str(inv_path),
        "hubs_uri": ".loctree/atlas/hubs.json",
        "signals_uri": ".loctree/atlas/signals.json" if hit.findings else None,
        "doctrine": {
            "sense_primary": "repo-view / agent.json DNA",
            "inventory_primary": "snapshot.json via stream→JSONL (never context.files)",
            "signals_primary": "findings.json",
            "forbidden": [
                "loct context --full as inventory",
                "loading full snapshot into LLM context",
                "hardcoded cache paths",
            ],
        },
    }
    if hit.manifest and hit.manifest.is_file():
        try:
            man = json.loads(hit.manifest.read_text(encoding="utf-8"))
            atlas["meta"]["loctree"] = man.get("loctree")
        except (OSError, json.JSONDecodeError):
            pass

    atlas_path = atlas_dir / "repo-atlas.json"
    write_json(atlas_path, atlas)

    # README for humans/agents
    readme = atlas_dir / "README.md"
    readme.write_text(
        "\n".join(
            [
                "# repo-atlas (vc-canary)",
                "",
                f"schema: `{ATLAS_SCHEMA}`",
                f"generated: {atlas['generated']}",
                "",
                "## Allowed",
                "- Read `repo-atlas.json` + `planes_hint.json` + `hubs.json` for SENSE",
                "- Stream `inventory.jsonl` (one file per line) for unit budgets",
                "- Cross-check `signals.json` after catalog fleet",
                "",
                "## Forbidden",
                "- Treat `loct context --full` structural.files as full inventory",
                "- Paste raw `snapshot.json` into the model context",
                "",
                (
                    f"## Coverage pass: {coverage['pass']} "
                    f"(snapshot_load_ratio={coverage['snapshot_load_ratio']}, "
                    f"inventory_files={inv_n})"
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )

    status = {
        "ok": True,
        "atlas": str(atlas_path),
        "inventory_jsonl": str(inv_path),
        "inventory_files": inv_n,
        "coverage_pass": coverage["pass"],
        "planes_hint": len(planes),
        "snapshot": str(hit.snapshot),
    }
    if args.output:
        write_json(Path(args.output), status)
        print(f"Success! atlas status written to: {args.output}")
    else:
        print(json.dumps(status, indent=2))

    if not coverage["pass"]:
        print(
            f"warning: coverage below floor {floor}: "
            f"snapshot_load_ratio={coverage['snapshot_load_ratio']}",
            file=sys.stderr,
        )
        return 2
    return 0


def cmd_merge_catalog(args: argparse.Namespace) -> int:
    root = Path(args.root or os.getcwd()).resolve()
    src = Path(args.input_dir or root / ".loctree" / "canary" / "catalogs")
    out = Path(args.output or root / ".loctree" / "canary" / "catalog.json")
    if not src.is_dir():
        _die(f"catalogs dir missing: {src}")
    if out.resolve().parent == src.resolve() or src.resolve() in out.resolve().parents:
        _die(
            f"--output {out} lives inside --input-dir {src}; the pre-merge "
            "cleanup would destroy its own input — pick an output path outside "
            "the catalogs dir"
        )
    # A rerun must never leave yesterday's settled-looking output behind a
    # failed merge: drop any existing artifact before validation so failure
    # states are unambiguous (output exists ⇔ this merge succeeded).
    out.unlink(missing_ok=True)
    plugins = load_language_plugins() if args.strict else ()
    if not args.strict:
        print(
            "warning: merge-catalog contract validation disabled by --no-strict",
            file=sys.stderr,
        )
    catalogs = []
    units = 0
    added = 0
    for p in sorted(src.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            _die(f"bad catalog {p}: {e}")
        cat = data.get("catalog") if isinstance(data, dict) else data
        if (
            cat is None
            and isinstance(data, dict)
            and isinstance(data.get("units"), list)
        ):
            cat = data["units"]
            print(
                f"warning: {p.name} uses legacy top-level key 'units'; the contract key is 'catalog'",
                file=sys.stderr,
            )
        if not isinstance(cat, list):
            _die(f"catalog not a list in {p}")
        # Path integrity is output integrity, not a language contract:
        # it holds even under --no-strict.
        for ui, u in enumerate(cat):
            if isinstance(u, dict):
                validate_unit_path_integrity(p, ui, u)
        if args.strict:
            validate_catalog(p, cat, plugins)
        catalogs.append(
            {
                "chunk": p.stem,
                "catalog": cat,
                "files_touched": data.get("files_touched")
                if isinstance(data, dict)
                else [],
                "notes": data.get("notes") if isinstance(data, dict) else None,
            }
        )
        units += len(cat)
        added += sum(1 for u in cat if isinstance(u, dict) and u.get("docstring_added"))
    if not catalogs:
        _die(
            f"no catalog files found in {src} — an empty merge is not a settled canary"
        )
    if units == 0:
        _die(
            "merged catalog has zero units — refusing to write a settled-looking empty catalog"
        )
    merged = {
        "schema": "canary-catalog.v1",
        "generated": _utc_now(),
        "repo": str(root),
        "counts": {
            "scopes": len(catalogs),
            "units_cataloged": units,
            "docstrings_added": added,
        },
        "catalogs": catalogs,
        "catalog": [u for c in catalogs for u in c["catalog"]],
    }
    write_json(out, merged)
    print(f"Success! merged catalog written to: {out} (units={units}, added={added})")
    return 0


def cmd_diff_audit(args: argparse.Namespace) -> int:
    """Report deleted lines in git diff — never reverts (operator decides)."""
    root = Path(args.root or os.getcwd()).resolve()
    r = _run(["git", "diff", "-U0", "HEAD"], root)
    if r.returncode != 0 and not r.stdout:
        # also check staged
        r = _run(["git", "diff", "-U0", "--cached"], root)
    text = r.stdout
    current_file = None
    deletions: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("--- a/"):
            continue
        elif line.startswith("-") and not line.startswith("---"):
            deletions.append({"file": current_file, "line": line[1:]})
    # heuristic: suspicious if looks like code not just quote rewrite
    suspicious = []
    for d in deletions:
        s = d["line"].strip()
        if not s or s.startswith(("#", "//", "*")):
            continue
        if s in ("pass", "...", "pass;", "{", "}", ");"):
            continue
        suspicious.append(d)
    report = {
        "schema": "canary-diff-audit.v1",
        "generated": _utc_now(),
        "project": str(root),
        "deletion_count": len(deletions),
        "suspicious_count": len(suspicious),
        "policy": "no_revert — examine why and ask operator",
        "suspicious_sample": suspicious[:50],
        "all_deletions_sample": deletions[:30],
    }
    out = Path(args.output or root / ".loctree" / "canary" / "diff-audit.json")
    write_json(out, report)
    print(
        f"Success! diff-audit written to: {out} "
        f"(deletions={len(deletions)}, suspicious={len(suspicious)})"
    )
    return 0 if not suspicious else 3


def cmd_coverage(args: argparse.Namespace) -> int:
    root = Path(args.root or os.getcwd()).resolve()
    cov_path = Path(args.input or root / ".loctree" / "atlas" / "coverage.json")
    if not cov_path.is_file():
        _die(f"missing {cov_path}; run: canary_cli atlas")
    data = json.loads(cov_path.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2))
    return 0 if data.get("pass") else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="canary_cli",
        description="vc-canary atlas helpers (skill-first repo-atlas)",
    )
    # Parent so `--root` works both before and after the subcommand
    # (SKILL docs: `canary_cli atlas --root .`).
    root_parent = argparse.ArgumentParser(add_help=False)
    root_parent.add_argument(
        "--root",
        default=None,
        help="Project root (default: cwd)",
    )
    p.add_argument(
        "--root",
        default=None,
        help="Project root (default: cwd)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser(
        "snapshot-path",
        parents=[root_parent],
        help="Resolve loctree cache snapshot path",
    )
    sp.add_argument("--output", required=False, help="Write JSON path info to file")
    sp.set_defaults(func=cmd_snapshot_path)

    rv = sub.add_parser(
        "repo-view",
        parents=[root_parent],
        help="Write loct repo-view JSON under .loctree/",
    )
    rv.add_argument("--output", required=False, help="Output path")
    rv.set_defaults(func=cmd_repo_view)

    at = sub.add_parser(
        "atlas",
        parents=[root_parent],
        help="Build repo-atlas.v1: sense pack + inventory.jsonl + coverage",
    )
    at.add_argument("--atlas-dir", default=None, help="Default: ./.loctree/atlas")
    at.add_argument(
        "--refresh",
        action="store_true",
        help="Run loct auto before building",
    )
    at.add_argument(
        "--coverage-floor",
        type=float,
        default=DEFAULT_COVERAGE_FLOOR,
        help="Min snapshot load ratio (default 0.95)",
    )
    at.add_argument("--include-tests", action="store_true")
    at.add_argument("--include-generated", action="store_true")
    at.add_argument(
        "--languages",
        default=None,
        help="Comma list e.g. rs,py,swift (overrides default code set)",
    )
    at.add_argument(
        "--all-languages",
        action="store_true",
        help="Include every snapshot language (md/json/html/…)",
    )
    at.add_argument("--output", required=False, help="Status JSON path")
    at.set_defaults(func=cmd_atlas)

    mg = sub.add_parser(
        "merge-catalog",
        parents=[root_parent],
        help="Merge per-scope catalog JSON files",
    )
    mg.add_argument("--input-dir", default=None)
    mg.add_argument("--output", default=None)
    strict_group = mg.add_mutually_exclusive_group()
    strict_group.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="Validate each catalog unit against its language plugin (default)",
    )
    strict_group.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="Explicitly bypass language-plugin validation",
    )
    mg.set_defaults(func=cmd_merge_catalog)

    da = sub.add_parser(
        "diff-audit",
        parents=[root_parent],
        help="Audit git deletions (no revert — examine + ask)",
    )
    da.add_argument("--output", default=None)
    da.set_defaults(func=cmd_diff_audit)

    cv = sub.add_parser(
        "coverage",
        parents=[root_parent],
        help="Print atlas coverage receipt",
    )
    cv.add_argument("--input", default=None)
    cv.set_defaults(func=cmd_coverage)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
