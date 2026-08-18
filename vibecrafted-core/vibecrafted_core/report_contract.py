"""Canonical agent report frontmatter — claim surface for board f/x/n + vc-server.

Philosophy (dashboard-ready):

* Frontmatter is **mandatory** on every agent report markdown.
* Agent fields are a **claim**. ``finalized: true`` plus a non-empty ``claim``
  is an explicit self-attestation tier, never a delivery-kernel seal.
* Runtime triangulates claim against exit code, report/transcript artifacts,
  optional declared artifact paths, and delivery-kernel axes when present.
* Ship / lifecycle reports (skill ``ship``, ``vc-ship``, ``lifecycle``,
  ``vc-lifecycle``) must **lead** with delivery truth: ``dou_index`` and
  the cut table (``cuts_done`` / ``cuts_total``). An ``11/11 stages``
  header is not a substitute for ``3/9`` cuts.
* Supervisor defer of a cut is not acceptance. The existing operator verb
  is ``accept-dou``. A defer that does not go through that verb leaves the
  cut ``[ ]``. Do not invent another surface.

Contract id: ``vibecrafted.report-frontmatter.v1``
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTRACT_ID = "vibecrafted.report-frontmatter.v1"
CLAIM_DIGEST_ENV = "VIBECRAFTED_CLAIM_DIGEST"

# Required keys for a steerable, dashboard-visible report.
REQUIRED_KEYS: tuple[str, ...] = (
    "run_id",
    "agent",
    "skill",
    "status",
)

# Optional but recommended for board + aicx.
RECOMMENDED_KEYS: tuple[str, ...] = (
    "project",
    "date",
    "session_id",
    "claim_status",
    "claim_kind",
    "finalized",
    "claim",
    "claim_digest",
    "repo_path",
    "model",
    "dou_index",
    "cuts_done",
    "cuts_total",
    "accepted_dou",
)

# Skills whose reports must lead with DoU / cut-table fields. A stage
# theatre header ("11/11 stages") cannot stand in for delivery.
SHIP_LIFECYCLE_SKILLS = frozenset({"ship", "vc-ship", "lifecycle", "vc-lifecycle"})
SHIP_LIFECYCLE_LEAD_KEYS: tuple[str, ...] = (
    "dou_index",
    "cuts_done",
    "cuts_total",
)

# Existing lifecycle verb. Defer without it leaves the cut unchecked.
ACCEPT_DOU_VERB = "accept-dou"
DEFERRED_CUT_MARK = "[ ]"

_DOU_RATIO_RE = re.compile(r"\A(\d+)\s*/\s*(\d+)\Z")
_STAGES_THEATRE_RE = re.compile(r"\b\d+\s*/\s*\d+\s+stages\b", re.IGNORECASE)

# Agent claim vocabulary (status / claim_status).
CLAIM_COMPLETED = frozenset({"completed", "complete", "success", "ok", "done"})
CLAIM_FAILED = frozenset({"failed", "fail", "error"})
CLAIM_BLOCKED = frozenset({"blocked", "blocked_on_operator", "waived"})
CLAIM_PARTIAL = frozenset(
    {"partial", "in-progress", "in_progress", "pending", "running"}
)

_VALID_STATUS = CLAIM_COMPLETED | CLAIM_FAILED | CLAIM_BLOCKED | CLAIM_PARTIAL
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_LAUNCHER_TEMPLATE_KEY = "launcher_template"
_PENDING_TEMPLATE_STATUS = "pending-unset"

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)",
    re.DOTALL,
)


@dataclass(frozen=True)
class ReportFrontmatter:
    """Parsed report claim surface."""

    fields: dict[str, str] = field(default_factory=dict)
    body: str = ""
    has_frontmatter: bool = False
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when frontmatter parsed and no required-key/structural errors exist."""
        return self.has_frontmatter and not self.errors

    @property
    def run_id(self) -> str:
        """The ``run_id`` field, stripped; empty string if absent."""
        return self.fields.get("run_id", "").strip()

    @property
    def agent(self) -> str:
        """The ``agent`` field, stripped; empty string if absent."""
        return self.fields.get("agent", "").strip()

    @property
    def skill(self) -> str:
        """The ``skill`` field, stripped; empty string if absent."""
        return self.fields.get("skill", "").strip()

    @property
    def claim_status(self) -> str:
        """Normalized claim: claim_status wins over status when set."""
        raw = (
            (self.fields.get("claim_status") or self.fields.get("status") or "")
            .strip()
            .lower()
        )
        return raw

    @property
    def claim_kind(self) -> str:
        """``claim_kind`` field, falling back to ``skill`` when unset."""
        return (self.fields.get("claim_kind") or self.fields.get("skill") or "").strip()

    @property
    def finalized(self) -> bool:
        """Whether the worker deliberately asserted successful completion."""
        return (self.fields.get("finalized") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def claim(self) -> str:
        """Human-readable claim attached to a positive self-attestation."""
        return (self.fields.get("claim") or "").strip()

    @property
    def dou_index(self) -> str:
        """Raw ``dou_index`` field (open findings or ``done/total``)."""
        return (self.fields.get("dou_index") or "").strip()

    def as_payload(self) -> dict[str, Any]:
        """Serialize this frontmatter for JSON output / dashboard consumption."""
        return {
            "contract": CONTRACT_ID,
            "has_frontmatter": self.has_frontmatter,
            "ok": self.ok,
            "fields": dict(self.fields),
            "claim_status": self.claim_status,
            "claim_kind": self.claim_kind,
            "finalized": self.finalized,
            "claim": self.claim,
            "dou_index": self.dou_index,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def is_ship_or_lifecycle_skill(skill: str) -> bool:
    """True when *skill* is a ship / lifecycle umbrella report."""
    return (skill or "").strip().lower() in SHIP_LIFECYCLE_SKILLS


def parse_dou_index_value(raw: str) -> tuple[int | None, int | None, int | None]:
    """Parse ``dou_index``.

    ``6`` → ``(6, None, None)`` — open DoU findings (lifecycle contract).
    ``3/9`` → ``(None, 3, 9)`` — cut ratio; open findings not stated.
    Unparseable or negative → ``(None, None, None)``.
    """
    text = (raw or "").strip()
    if not text:
        return None, None, None
    ratio = _DOU_RATIO_RE.fullmatch(text)
    if ratio:
        return None, int(ratio.group(1)), int(ratio.group(2))
    try:
        value = int(text)
    except ValueError:
        return None, None, None
    if value < 0:
        return None, None, None
    return value, None, None


def _parse_non_negative_int(raw: str) -> int | None:
    """Parse a non-negative integer field; ``None`` if absent or invalid."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value >= 0 else None


def _validate_ship_lifecycle_lead(
    normalized: Mapping[str, str],
    body: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Require dou_index + cut table on ship/lifecycle; stages are a footnote."""
    if not is_ship_or_lifecycle_skill(normalized.get("skill", "")):
        return

    raw_index = (normalized.get("dou_index") or "").strip()
    open_findings, ratio_done, ratio_total = parse_dou_index_value(raw_index)
    if not raw_index:
        errors.append("report_frontmatter_missing_key:dou_index")
    elif open_findings is None and ratio_done is None:
        errors.append("report_frontmatter_dou_index_invalid")

    cuts_done = _parse_non_negative_int(normalized.get("cuts_done", ""))
    cuts_total = _parse_non_negative_int(normalized.get("cuts_total", ""))
    if cuts_done is None and ratio_done is not None:
        cuts_done = ratio_done
    if cuts_total is None and ratio_total is not None:
        cuts_total = ratio_total

    if (normalized.get("cuts_done") or "").strip() and cuts_done is None:
        errors.append("report_frontmatter_cuts_done_invalid")
    if (normalized.get("cuts_total") or "").strip() and cuts_total is None:
        errors.append("report_frontmatter_cuts_total_invalid")

    if cuts_done is None:
        errors.append("report_frontmatter_missing_key:cuts_done")
    if cuts_total is None:
        errors.append("report_frontmatter_missing_key:cuts_total")
    if cuts_done is not None and cuts_total is not None and cuts_done > cuts_total:
        errors.append("report_frontmatter_cuts_inverted")

    status = (
        (normalized.get("claim_status") or normalized.get("status") or "")
        .strip()
        .lower()
    )
    incomplete_cuts = (
        cuts_done is not None and cuts_total is not None and cuts_done < cuts_total
    )
    if incomplete_cuts and status in CLAIM_COMPLETED:
        warnings.append("report_frontmatter_cuts_incomplete")

    if incomplete_cuts and _STAGES_THEATRE_RE.search(body or ""):
        warnings.append("report_frontmatter_stages_hide_cuts")

    # Defer without accept-dou is not acceptance. A completed or blocked
    # ship report that still has open cuts and never names the verb is
    # a silent defer — the cut stays [ ].
    named_accept = ACCEPT_DOU_VERB in (body or "") or bool(
        (normalized.get("accepted_dou") or "").strip()
    )
    settled = status in (CLAIM_COMPLETED | CLAIM_BLOCKED)
    if incomplete_cuts and settled and not named_accept:
        warnings.append("report_frontmatter_defer_without_accept_dou")


def parse_report_text(text: str) -> tuple[dict[str, str], str, bool]:
    """Return (fields, body, has_frontmatter)."""
    if not text:
        return {}, "", False
    match = _FRONTMATTER_RE.match(text)
    if not match:
        # Tolerate BOM / leading blank lines.
        stripped = text.lstrip("\ufeff")
        match = _FRONTMATTER_RE.match(stripped)
        if not match:
            return {}, text, False
        text = stripped
    raw = match.group("body")
    fields: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        fields[key] = value.strip().strip("\"'")
    body = text[match.end() :]
    body = body.removeprefix("\n")
    return fields, body, True


def parse_report_path(path: str | Path) -> ReportFrontmatter:
    """Read and validate a report file's frontmatter; never raises on I/O errors."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ReportFrontmatter(
            errors=(f"report_unreadable:{type(exc).__name__}",),
        )
    fields, body, has_fm = parse_report_text(text)
    return validate_frontmatter_fields(fields, body, has_fm=has_fm)


def validate_frontmatter_fields(
    fields: Mapping[str, str],
    body: str = "",
    *,
    has_fm: bool,
    require_recommended: bool = False,
) -> ReportFrontmatter:
    """Validate parsed frontmatter fields against required/recommended keys and
    the known claim-status vocabulary, producing errors and warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []
    normalized = {
        str(k).strip(): str(v).strip() for k, v in fields.items() if str(k).strip()
    }

    if not has_fm:
        errors.append("report_frontmatter_missing")
        return ReportFrontmatter(
            fields=normalized,
            body=body,
            has_frontmatter=False,
            errors=tuple(errors),
        )

    for key in REQUIRED_KEYS:
        value = normalized.get(key, "").strip()
        if not value:
            errors.append(f"report_frontmatter_missing_key:{key}")
            continue
        # Runtime salvage may write "unknown" when the worker left no claim.
        # Structure is present (dashboard can index); quality is a warning.
        if value.lower() in {"unknown", "none", "null", "pending-unset"}:
            warnings.append(f"report_frontmatter_placeholder:{key}")

    # A launcher template is transport scaffolding, not worker evidence. Keep
    # the historical ``report_missing`` signal even though the runtime has
    # materialized a file, so exit-0-without-report still parks at n.
    if normalized.get(_LAUNCHER_TEMPLATE_KEY, "").strip().lower() in _TRUTHY:
        errors.append("report_missing")

    status = (
        (normalized.get("claim_status") or normalized.get("status") or "")
        .strip()
        .lower()
    )
    if status and status not in _VALID_STATUS:
        warnings.append(f"report_frontmatter_status_unrecognized:{status}")

    _validate_ship_lifecycle_lead(normalized, body, errors, warnings)

    if require_recommended:
        for key in RECOMMENDED_KEYS:
            if not normalized.get(key, "").strip():
                warnings.append(f"report_frontmatter_recommended_missing:{key}")

    # artifacts: optional comma-separated paths for dashboard proof list
    artifacts_raw = (
        normalized.get("artifacts") or normalized.get("artifact_paths") or ""
    )
    if artifacts_raw and artifacts_raw.lower() not in {"none", "[]", "-"}:
        # Keep as opaque string; triage may split later.
        pass

    return ReportFrontmatter(
        fields=normalized,
        body=body,
        has_frontmatter=True,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_report_file(
    path: str | Path | None,
    *,
    require_frontmatter: bool = True,
) -> ReportFrontmatter:
    """Validate a report file on disk; missing/unreadable files are errors.

    When ``require_frontmatter`` is False, a missing frontmatter block is
    downgraded from an error to a warning instead of failing validation.
    """
    if path is None:
        return ReportFrontmatter(errors=("report_path_missing",))
    p = Path(path)
    if not p.is_file():
        return ReportFrontmatter(errors=("report_missing",))
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ReportFrontmatter(errors=(f"report_unreadable:{type(exc).__name__}",))
    fields, body, has_fm = parse_report_text(text)
    result = validate_frontmatter_fields(fields, body, has_fm=has_fm)
    if not require_frontmatter and "report_frontmatter_missing" in result.errors:
        # Downgrade hard missing-block to warning when caller opts out.
        errors = tuple(e for e in result.errors if e != "report_frontmatter_missing")
        warnings = result.warnings + ("report_frontmatter_missing",)
        return ReportFrontmatter(
            fields=result.fields,
            body=result.body,
            has_frontmatter=result.has_frontmatter,
            errors=errors,
            warnings=warnings,
        )
    return result


def claim_bucket_hint(claim_status: str) -> str | None:
    """Map agent claim to a soft drawer hint (never alone decisive)."""
    status = (claim_status or "").strip().lower()
    if status in CLAIM_COMPLETED:
        return "completed"
    if status in CLAIM_FAILED:
        return "failed"
    if status in CLAIM_BLOCKED or status in CLAIM_PARTIAL:
        return "needs_attention"
    return None


def render_minimal_frontmatter(
    *,
    run_id: str,
    agent: str,
    skill: str,
    status: str,
    extra: Mapping[str, object] | None = None,
) -> str:
    """Canonical minimal block for salvage/fallback writers."""
    data: dict[str, object] = {
        "run_id": run_id or "unknown",
        "agent": agent or "unknown",
        "skill": skill or "unknown",
        "status": status or "completed",
        "claim_status": status or "completed",
        # The worker must flip this deliberately and add ``claim``. A fallback
        # or launcher-normalized report never self-finalizes by construction.
        "finalized": "false",
    }
    if extra:
        for key, value in extra.items():
            if value is not None and str(value).strip() != "":
                data[str(key)] = value
    order = [
        "run_id",
        "agent",
        "skill",
        # Delivery truth leads: stages are a footnote, not a substitute.
        "dou_index",
        "cuts_done",
        "cuts_total",
        "accepted_dou",
        "project",
        "status",
        "claim_status",
        "claim_kind",
        "finalized",
        "claim",
        "date",
        "session_id",
        "model",
        "repo_path",
    ]
    lines = ["---"]
    emitted: set[str] = set()
    for key in order:
        if key in data:
            lines.append(f"{key}: {data[key]}")
            emitted.add(key)
    for key in sorted(k for k in data if k not in emitted):
        lines.append(f"{key}: {data[key]}")
    lines.extend(["---", ""])
    return "\n".join(lines)


def _render_frontmatter_fields(fields: Mapping[str, str], body: str) -> str:
    """Re-render a frontmatter block from an already-parsed field mapping + body."""
    return render_minimal_frontmatter(
        run_id=fields.get("run_id", ""),
        agent=fields.get("agent", ""),
        skill=fields.get("skill", ""),
        status=fields.get("status", "completed"),
        extra={
            key: value
            for key, value in fields.items()
            if key not in REQUIRED_KEYS and key != "status"
        },
    ) + body.lstrip("\n")


def materialize_launcher_report_template(
    path: str | Path,
    *,
    run_id: str,
    agent: str,
    skill: str,
    claim_digest: str = "",
) -> bool:
    """Create the machine-owned identity shell before the worker writes.

    The marker makes the untouched shell fail artifact validation as
    ``report_missing``. The worker must add evidence or an explicit claim; the
    launcher later removes the marker and stamps the child agent session.
    """

    try:
        reserve_launcher_report_template(
            path,
            run_id=run_id,
            agent=agent,
            skill=skill,
            claim_digest=claim_digest,
        )
    except FileExistsError:
        return False
    return True


def reserve_launcher_report_template(
    path: str | Path,
    *,
    run_id: str,
    agent: str,
    skill: str,
    claim_digest: str = "",
) -> None:
    """Atomically reserve and seed a run's report before provider launch.

    ``O_EXCL`` is the allocation authority. A collision is a hard contract
    failure; callers must allocate a new run id instead of scanning for the
    next suffix.
    """
    report = Path(path)
    report.parent.mkdir(parents=True, exist_ok=True)
    extra = {
        "claim_status": "pending",
        "finalized": "false",
        "session_id": _PENDING_TEMPLATE_STATUS,
        _LAUNCHER_TEMPLATE_KEY: "true",
    }
    launcher_digest = str(claim_digest or "").strip()
    if launcher_digest:
        extra["claim_digest"] = launcher_digest
    payload = render_minimal_frontmatter(
        run_id=run_id,
        agent=agent,
        skill=skill,
        status=_PENDING_TEMPLATE_STATUS,
        extra=extra,
    ).encode("utf-8")
    descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def worker_authored_report(fields: Mapping[str, str], body: str) -> bool:
    """True when a worker wrote real evidence over the launcher template.

    The launcher seeds frontmatter and an EMPTY body. Workers are instructed to
    *preserve* machine-owned frontmatter, so the mere presence of
    ``launcher_template`` proves nothing about whether the worker wrote — only
    substance does. Any of these is proof of authorship: a non-empty body, an
    explicit ``finalized``, a non-empty ``claim``, or a status moved off the
    pending-template placeholder.

    Callers use this to decide whether a report may be replaced by a transcript
    salvage. Keying that decision on the preserved marker alone destroys
    authored reports.
    """
    return any(
        (
            bool(body.strip()),
            fields.get("finalized", "").strip().lower() in _TRUTHY,
            bool(fields.get("claim", "").strip()),
            fields.get("status", "").strip().lower()
            not in {"", _PENDING_TEMPLATE_STATUS},
        )
    )


def stamp_launcher_report_identity(
    path: str | Path,
    *,
    run_id: str,
    session_id: str,
    agent: str,
    skill: str,
    status: str,
    model: str = "",
    claim_digest: str = "",
) -> bool:
    """Authoritatively stamp launcher-owned identity without clobbering claims."""

    report = Path(path)
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False

    fields, body, has_fm = parse_report_text(text)
    if not has_fm:
        fields = {}
        body = text

    # run_id/session_id are runtime facts, never agent claims. Always replace
    # copied or guessed values. An unavailable child session stays explicit.
    fields["run_id"] = run_id or "unknown"
    fields["session_id"] = session_id or _PENDING_TEMPLATE_STATUS
    launcher_digest = str(claim_digest or "").strip()
    if launcher_digest:
        fields["claim_digest"] = launcher_digest
    if agent and not fields.get("agent"):
        fields["agent"] = agent
    if skill and not fields.get("skill"):
        fields["skill"] = skill
    if model and not fields.get("model"):
        fields["model"] = model
    if not fields.get("status"):
        fields["status"] = status or "completed"
    if not fields.get("claim_status"):
        fields["claim_status"] = fields["status"]
    if "finalized" not in fields:
        fields["finalized"] = "false"

    template_pending = fields.get(_LAUNCHER_TEMPLATE_KEY, "").strip().lower() in _TRUTHY
    worker_touched = worker_authored_report(fields, body)
    if template_pending and worker_touched:
        fields.pop(_LAUNCHER_TEMPLATE_KEY, None)

    normalized = _render_frontmatter_fields(fields, body)
    if normalized == text:
        return False
    report.write_text(normalized, encoding="utf-8")
    return True


def ensure_frontmatter_on_text(
    text: str,
    *,
    run_id: str = "",
    agent: str = "",
    skill: str = "",
    status: str = "completed",
    extra: Mapping[str, object] | None = None,
) -> str:
    """If text lacks a valid frontmatter block, prepend a minimal one."""
    fields, body, has_fm = parse_report_text(text)
    if has_fm:
        # Merge missing required keys without clobbering agent claim.
        changed = False
        for key, value in (
            ("run_id", run_id),
            ("agent", agent),
            ("skill", skill),
        ):
            if value and not fields.get(key):
                fields[key] = value
                changed = True
        if not fields.get("status") and status:
            fields["status"] = status
            changed = True
        if not fields.get("claim_status") and fields.get("status"):
            fields["claim_status"] = fields["status"]
            changed = True
        if "finalized" not in fields:
            fields["finalized"] = "false"
            changed = True
        if extra:
            for key, extra_value in extra.items():
                if (
                    extra_value is not None
                    and str(extra_value).strip()
                    and key not in fields
                ):
                    fields[key] = str(extra_value)
                    changed = True
        if not changed:
            return text if text.endswith("\n") else text + "\n"
        return render_minimal_frontmatter(
            run_id=fields.get("run_id", run_id),
            agent=fields.get("agent", agent),
            skill=fields.get("skill", skill),
            status=fields.get("status", status),
            extra={
                k: v
                for k, v in fields.items()
                if k not in REQUIRED_KEYS and k != "status"
            },
        ) + body.lstrip("\n")

    return render_minimal_frontmatter(
        run_id=run_id,
        agent=agent,
        skill=skill,
        status=status,
        extra=extra,
    ) + (text.lstrip("\n") if text else "")
