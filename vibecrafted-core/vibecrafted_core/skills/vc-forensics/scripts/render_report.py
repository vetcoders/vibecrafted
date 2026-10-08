#!/usr/bin/env python3
"""Join explicit JSON evidence into a portable, offline forensic notebook."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

STATES = (
    "hypothesis",
    "proven",
    "source_fixed",
    "verified",
    "integrated",
    "installed",
    "live_accepted",
    "refuted",
)


def validate_findings(data):
    """Validate before replacement; preserve extra receipt fields losslessly."""
    if isinstance(data, dict):
        if data.get("schema") != "vc-forensics.findings.v1":
            raise ValueError(
                "Expected vc-forensics.findings.v1 or an array of findings"
            )
        data = data.get("findings")
    if not isinstance(data, list):
        raise TypeError("findings must be an array")
    seen = set()
    for finding in data:
        if not isinstance(finding, dict):
            raise TypeError("Each finding must be an object")
        for field in ("id", "title"):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                raise ValueError(f"Finding requires a nonempty {field}")
        if finding["id"] in seen:
            raise ValueError(f"Duplicate finding id: {finding['id']}")
        seen.add(finding["id"])
        if finding.get("state", "hypothesis") not in STATES:
            raise ValueError(f"Unknown state for {finding['id']}")
    return data


def read_source(path, head, kind):
    if path is None:
        return {"status": "NOT_ASSESSED", "data": None}
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, (dict, list)):
        raise TypeError(f"{kind} report must be a JSON object or array")
    revision = None
    if isinstance(data, dict):
        if kind == "loctree":
            revision = data.get("git_ref")
        elif kind == "prview":
            meta = data.get("meta", {})
            if isinstance(meta, dict) and isinstance(meta.get("range"), dict):
                revision = meta["range"].get("head")
    match = None
    if isinstance(revision, str) and re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
        match = head.lower().startswith(revision.lower())
    return {
        "status": "IMPORTED",
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "reported_revision": revision,
        "revision_match": match,
        "data": data,
    }


def build_report(repo, head, baseline, loctree, prview, findings):
    sources = {
        "loctree": read_source(loctree, head, "loctree"),
        "prview": read_source(prview, head, "prview"),
        "manual": read_source(findings, head, "manual"),
    }
    manual = sources["manual"]["data"]
    records = validate_findings(manual) if findings else []
    if isinstance(manual, dict):
        if manual.get("head") and manual["head"] != head:
            raise ValueError("Manual findings describe a different head SHA")
        if manual.get("repo") and manual["repo"] != repo:
            raise ValueError("Manual findings describe a different repository")
    return {
        "schema": "vc-forensics.report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "head": head,
        "baseline": baseline,
        "sources": sources,
        "findings": records,
        "states": list(STATES),
    }


def render_html(report):
    # HTML parsers close even non-executable script elements on </script>.
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False)
    payload = (
        payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    template = Path(__file__).resolve().parents[1] / "assets/report.html"
    return template.read_text(encoding="utf-8").replace(
        "__FORENSICS_DATA__", payload, 1
    )


def commit_sha(value):
    if not re.fullmatch(r"[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("Provide the full 40-character source SHA")
    return value.lower()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", required=True, help="Identity of the investigated repository"
    )
    parser.add_argument("--head", required=True, type=commit_sha)
    parser.add_argument("--baseline", type=commit_sha)
    for name in ("loctree", "prview", "findings"):
        parser.add_argument(
            f"--{name}",
            type=Path,
            help="Explicit JSON input; omitted means NOT_ASSESSED",
        )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = [
            path.resolve()
            for path in (args.loctree, args.prview, args.findings)
            if path
        ]
        if args.out.resolve() in inputs:
            raise ValueError("Output must not overwrite an input report")
        report = build_report(
            args.repo,
            args.head,
            args.baseline,
            args.loctree,
            args.prview,
            args.findings,
        )
        html = render_html(report)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Do not silently replace an existing notebook with unexported edits.
        with args.out.open("x", encoding="utf-8") as handle:
            handle.write(html)
    except (OSError, ValueError, TypeError) as error:
        parser.error(str(error))
    print(args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
