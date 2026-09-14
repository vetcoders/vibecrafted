"""Provider stdin transports: how a private prompt file reaches a worker.

Every headless lane writes the operator prompt to a 0600 file and hands that
file to the worker on stdin (never argv: ``ps`` and ARG_MAX). Most providers
read that text verbatim. Agy's print mode has no text stdin lane at all; its
only private input is ``--input-format stream-json`` — one NDJSON user turn
per line, requiring ``--output-format stream-json``. This module owns that
shape so the async supervisor, the shell launcher and the tests agree on one
encoder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

STREAM_JSON_AGENTS = frozenset({"agy"})
TEXT_TRANSPORT = "text"
STREAM_JSON_TRANSPORT = "stream-json"


def stdin_transport(agent: str) -> str:
    """Name the stdin encoding a provider's headless argv expects."""
    return STREAM_JSON_TRANSPORT if agent in STREAM_JSON_AGENTS else TEXT_TRANSPORT


def encode_stream_json_user_turn(prompt: str) -> bytes:
    """Encode one prompt as a single stream-json user turn (one NDJSON line)."""
    payload = {"event": "user", "message": {"role": "user", "content": prompt}}
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def stream_json_sibling(prompt_file: Path) -> Path:
    """``prompt.md`` -> ``prompt.ndjson`` next to it (any suffix, or none)."""
    stem = prompt_file.stem if prompt_file.suffix else prompt_file.name
    return prompt_file.with_name(f"{stem}.ndjson")


def write_stream_json_file(prompt_file: Path, target: Path) -> Path:
    """Write *prompt_file*'s text as one user turn into a 0600 *target*."""
    text = prompt_file.read_text(encoding="utf-8")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encode_stream_json_user_turn(text))
    os.chmod(target, 0o600)
    return target


def materialize_stdin_file(
    agent: str, prompt_file: Path, target: Path | None = None
) -> Path:
    """Return the file to open as *agent*'s stdin for this prompt file.

    Text-transport providers get the prompt file itself. Stream-json providers
    get a 0600 sibling (or *target*) holding the encoded user turn; it is
    rewritten on every call so a re-launch never replays a stale turn.
    """
    if stdin_transport(agent) != STREAM_JSON_TRANSPORT:
        return prompt_file
    return write_stream_json_file(
        prompt_file, target if target is not None else stream_json_sibling(prompt_file)
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m vibecrafted_core.prompt_transport AGENT PROMPT_FILE [TARGET]``.

    Shell launchers call this instead of carrying their own encoder. Prints the
    materialized stdin path.
    """
    parser = argparse.ArgumentParser(prog="vibecrafted_core.prompt_transport")
    parser.add_argument("agent")
    parser.add_argument("prompt_file")
    parser.add_argument("target", nargs="?")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    target = Path(args.target) if args.target else None
    print(materialize_stdin_file(args.agent, Path(args.prompt_file), target))
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry
    raise SystemExit(main())
