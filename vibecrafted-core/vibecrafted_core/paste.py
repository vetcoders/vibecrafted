"""``vc-paste`` CLI: launch an agent whose task prompt is the clipboard contents."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .repo_selection import RepoSelectionError, add_repo_arguments, select_repository
from .workflow import WorkflowLaunchSpec, launch_workflow, normalize_launch_spec

BOOTSTRAP_PASTE_PROMPT = """BOOTSTRAP — CLIPBOARD RUN

Najpierw odczytaj aktualną zawartość schowka przez `pbpaste` (macOS) lub równoważnik
(`wl-paste` / `xclip -selection clipboard -o` na Linux). Zawartość schowka jest JEDYNYM
właściwym promptem zadaniowym tego runu — traktuj ją jako specyfikację, nie jako przykład,
komentarz ani kontekst pomocniczy.

Jeśli schowek jest pusty albo nie wygląda jak zadanie — zatrzymaj się i zgłoś to operatorowi,
nic nie zmieniaj.

Po odczytaniu schowka:
1. Streść zadanie w maksymalnie 5 punktach.
2. Zbadaj repo/kontekst potrzebny do wykonania (loctree-first).
3. Wykonaj wyłącznie zadanie opisane w schowku, w bieżącym repo i branchu.
4. Sprawdź integralność wedle możliwości projektu: format, lint, typy, testy.
5. Na końcu raportuj: co zmieniono, jak sprawdzono, czego nie udało się sprawdzić, jakie ryzyka.
""".strip()


def build_paste_payload(
    agent: str | None, *, skill: str = "workflow", root: str | None = None
) -> dict[str, Any]:
    """Payload for normalize_launch_spec; clipboard resolution is deferred."""
    return {
        "skill": skill,
        "agent": agent,
        "prompt": BOOTSTRAP_PASTE_PROMPT,
        "file": "",
        "runtime": "",
        "root": root or str(Path.cwd()),
        "mode": "paste",
        "count": None,
        "depth": None,
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``vc-paste`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="vc-paste",
        description="Launch an agent with a deferred clipboard bootstrap prompt.",
    )
    parser.add_argument("agent", nargs="?")
    parser.add_argument("--skill", default="workflow")
    add_repo_arguments(parser)
    parser.add_argument("--print-prompt", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _source_dir() -> Path:
    """Directory this module lives in, used as the default launch-spec source."""
    return Path(__file__).resolve().parent


def _spec_payload(spec: WorkflowLaunchSpec) -> dict[str, Any]:
    """Serialize a launch spec for ``--dry-run``/``--json`` output."""
    return spec.to_payload()


def _print_json(payload: dict[str, Any]) -> None:
    """Print ``payload`` as pretty-printed, non-ASCII-escaped JSON."""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_namespace(
    args: argparse.Namespace, *, source_dir: str | Path | None = None
) -> int:
    """Execute one parsed ``vc-paste`` invocation; return the process exit code."""
    if args.print_prompt:
        print(BOOTSTRAP_PASTE_PROMPT)
        return 0

    payload = build_paste_payload(
        args.agent,
        skill=args.skill,
        root=args.root or None,
    )
    try:
        spec = normalize_launch_spec(payload, source_dir or _source_dir())
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        _print_json(_spec_payload(spec))
        return 0

    if not args.agent:
        print("error: agent is required", file=sys.stderr)
        return 2

    result = launch_workflow(spec, source_dir or _source_dir())
    if args.json:
        _print_json(result)
    else:
        from .cli import _print_launch_receipt

        _print_launch_receipt(result)
    return 0 if result.get("accepted") else 1


def paste_main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``vc-paste`` console script."""
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        args.root = select_repository(
            args.repo, args.root, fallback=Path.cwd, label="vc-paste"
        ).path
    except RepoSelectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run_namespace(args)
