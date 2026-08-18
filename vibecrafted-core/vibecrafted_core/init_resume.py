"""Resume as an automatic payload of the init pass, not a remembered step.

Every Vibecrafted entry point already runs an init pass: the operator-facing
``vibecrafted init <agent>`` composes ``/vc-init``, and every tracked pipeline
launch carries the "Step 0 — orient before you touch" contract. Until this
module, neither of them knew whether the repository they were opening had
unfinished work waiting on the control plane. Resuming was a separate verb the
operator had to remember (``vibecrafted <agent> resume --run-id ...``), which
means it was skipped exactly when it mattered: after a crash, days later, by a
different agent.

This module turns that into a computed payload of init itself.

Three properties drive every decision here:

**Silence when clean.** :func:`render_init_resume_block` returns ``""`` when the
repository has nothing resumable. A block that always prints something ("no
resumable runs") is a block agents learn to skip, and an automatic payload that
gets skipped is automatic noise. The payload earns attention by only appearing
when it has news.

**Fail-open.** Init is the gate to all repository work; a corrupt or unreadable
settlement ledger must never brick it. Every read is contained, and a failure
degrades to an honest one-line note inside the payload — never an exception.
Same discipline as :mod:`vibecrafted_core.run_triage`.

**The ledger decides, not this module.** ``settlement_tui == "n"`` is the
durable, trust-sourced statement that a run needs attention. This module
projects that history onto one checkout and renders the exact command; it never
re-judges a settlement, never invents resumability, and never widens the ``n``
bucket. Guardian auto-resume eligibility in particular is *reported*, not
decided: rows carrying ``native_resume_candidate`` + a trust receipt belong to
the Guardian's single-attempt budget, and a hand-resume there would burn it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

INIT_RESUME_SCHEMA = "vibecrafted.init-resume-payload.v1"

#: How many runs of one class the rendered block will name before summarizing.
DEFAULT_RESUME_LIMIT = 5

#: Resume classes, most-actionable first. The order is the render order.
GUARDIAN_AUTO = "guardian_auto"
OPERATOR_RESUME = "operator_resume"
EVIDENCE_ONLY = "evidence_only"
RESUME_CLASSES = (GUARDIAN_AUTO, OPERATOR_RESUME, EVIDENCE_ONLY)


def _resolved(raw: object) -> str:
    """Best-effort absolute path string; '' when the value is unusable."""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve())
    except (OSError, RuntimeError, ValueError):
        return text


def _same_checkout(row_root: object, target_root: str) -> bool:
    """True when a settlement row belongs to the checkout init was called on.

    Compared on resolved paths so that a symlinked or relative ``--root`` still
    matches the recorded snapshot root. An unrecorded root never matches: an
    unattributable run is not this repository's business.
    """
    resolved_row = _resolved(row_root)
    return bool(resolved_row) and bool(target_root) and resolved_row == target_root


def classify_resume_row(row: Mapping[str, Any]) -> str:
    """Return the resume class of one enriched ``n`` settlement row.

    ``guardian_auto`` rows carry both a native-resume candidate and a trust
    receipt: the Guardian owns them under a one-attempt budget, so init reports
    them and tells the reader not to spend that attempt by hand.

    ``operator_resume`` rows still have their checkout and their evidence on
    disk, so a deliberate operator resume can pick them up.

    ``evidence_only`` rows lost their checkout or their artifacts; only the
    report is left to read.
    """
    if row.get("native_resume_candidate") and row.get("trust_receipt_present"):
        return GUARDIAN_AUTO
    if row.get("revalidatable") and row.get("checkout_exists"):
        return OPERATOR_RESUME
    return EVIDENCE_ONLY


def resume_command(row: Mapping[str, Any]) -> str:
    """Exact one-line resume command for a row, or '' when none is honest.

    Both ``vibecrafted <agent> resume --run-id`` and
    ``vibecrafted resume <agent> --run-id`` are real (docs/public/cli/commands.md);
    the agent-first form is emitted because it reads as "this agent, continue".
    A row without a recorded agent gets no command rather than a guessed one.
    """
    agent = str(row.get("agent") or "").strip()
    run_id = str(row.get("run_id") or "").strip()
    if not agent or not run_id:
        return ""
    return f"vibecrafted {agent} resume --run-id {run_id}"


def resume_payload(
    root: str | Path,
    *,
    limit: int = DEFAULT_RESUME_LIMIT,
) -> dict[str, Any]:
    """Compute the init-time resume payload for one checkout.

    Never raises. A ledger that cannot be read yields ``available: False`` plus
    the reason, so the caller can say so honestly instead of implying the
    repository is clean.
    """
    target = _resolved(root)
    payload: dict[str, Any] = {
        "schema": INIT_RESUME_SCHEMA,
        "root": target,
        "available": True,
        "error": "",
        "matched": 0,
        "classes": {name: [] for name in RESUME_CLASSES},
        "counts": dict.fromkeys(RESUME_CLASSES, 0),
    }
    try:
        from .settlements_query import list_settlements

        listed = list_settlements(bucket="n")
        rows = listed.get("runs") or []
    except Exception as exc:  # noqa: BLE001 - init must survive any ledger fault
        payload["available"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"
        return payload

    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if not _same_checkout(row.get("root"), target):
            continue
        entry = {
            "run_id": str(row.get("run_id") or ""),
            "agent": str(row.get("agent") or ""),
            "skill": str(row.get("skill") or ""),
            "reason": str(row.get("reason") or ""),
            "state": str(row.get("state") or ""),
            "report_path": str(row.get("report_path") or ""),
            "settled_at": str(row.get("settled_at") or ""),
            "command": resume_command(row),
        }
        bucket = classify_resume_row(row)
        payload["classes"][bucket].append(entry)
        payload["counts"][bucket] += 1
        payload["matched"] += 1

    # Newest first. A checkout with months of history would otherwise show its
    # oldest five runs — technically honest, operationally useless. Rows without
    # a stamp sink to the bottom rather than pretending to be recent.
    for entries in payload["classes"].values():
        entries.sort(key=_recency_key, reverse=True)

    payload["limit"] = max(0, int(limit))
    return payload


def _recency_key(entry: Mapping[str, Any]) -> tuple[int, str]:
    """Sort key putting stamped rows above unstamped, newest stamp first."""
    stamp = str(entry.get("settled_at") or "").strip()
    return (1 if stamp else 0, stamp or str(entry.get("run_id") or ""))


_CLASS_HEADINGS = {
    GUARDIAN_AUTO: (
        "Guardian owns these (one automatic attempt each — do not resume by hand):"
    ),
    OPERATOR_RESUME: "Resumable now (checkout and evidence still on disk):",
    EVIDENCE_ONLY: "Unfinished, no live checkout — read the report before redoing it:",
}


def _render_entry(entry: Mapping[str, Any], *, with_command: bool) -> list[str]:
    """Render one run as a bullet plus, when useful, its exact command line."""
    label = entry.get("run_id") or "<unknown run>"
    facets = [str(entry.get(key) or "") for key in ("skill", "agent", "state")]
    described = " · ".join(part for part in facets if part)
    head = f"- `{label}`" + (f" — {described}" if described else "")
    reason = str(entry.get("reason") or "").strip()
    if reason:
        head += f" — {reason}"
    lines = [head]
    command = str(entry.get("command") or "").strip()
    if with_command and command:
        lines.append(f"  `{command}`")
    report = str(entry.get("report_path") or "").strip()
    if not with_command and report:
        lines.append(f"  report: {report}")
    return lines


def render_init_resume_block(payload: Mapping[str, Any]) -> str:
    """Render the payload as a prompt block, or '' when there is no news.

    Silence is the point: a clean repository adds nothing to the init prompt.
    An unreadable ledger is news, because the alternative is implying "clean".
    """
    if not payload.get("available", True):
        reason = str(payload.get("error") or "unknown error")
        return (
            "Resume payload (part of this init pass): the settlement ledger could "
            f"not be read ({reason}). Treat unfinished-work status as UNKNOWN — "
            "check `vibecrafted settlements list --bucket n` before assuming this "
            "checkout is clean."
        )
    if not payload.get("matched"):
        return ""

    limit = int(payload.get("limit") or DEFAULT_RESUME_LIMIT)
    classes = payload.get("classes") or {}
    counts = payload.get("counts") or {}
    lines = [
        "Resume payload (computed by this init pass — you did not have to ask for it).",
        (
            f"This checkout has {payload['matched']} run(s) settled `n` "
            "(needs attention). Unfinished work here is not hypothetical; "
            "read it before starting anything new."
        ),
    ]
    for name in RESUME_CLASSES:
        entries = list(classes.get(name) or [])
        if not entries:
            continue
        lines.append("")
        lines.append(_CLASS_HEADINGS[name])
        for entry in entries[:limit]:
            lines.extend(_render_entry(entry, with_command=(name == OPERATOR_RESUME)))
        hidden = int(counts.get(name, 0)) - limit
        if hidden > 0:
            lines.append(
                f"  … and {hidden} older run(s) in this class, newest shown first."
            )
    lines.append("")
    lines.append(
        "Full inventory: `vibecrafted settlements list --bucket n --revalidatable`."
    )
    return "\n".join(lines)


def init_resume_block(
    root: str | Path,
    *,
    limit: int = DEFAULT_RESUME_LIMIT,
) -> str:
    """Compute and render the init resume payload in one call. Never raises."""
    try:
        return render_init_resume_block(resume_payload(root, limit=limit))
    except Exception:  # noqa: BLE001 - a rendering fault must not brick init
        return ""


def main(argv: list[str] | None = None) -> int:
    """``python -m vibecrafted_core.init_resume`` — print the block for a root."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="vibecrafted-init-resume",
        description="Resume payload carried automatically by every init pass.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--limit", type=int, default=DEFAULT_RESUME_LIMIT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = resume_payload(args.root, limit=args.limit)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    block = render_init_resume_block(payload)
    if block:
        print(block)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    raise SystemExit(main())
