"""Quota admission for a new dispatch batch.

The shared observation lives in ``vc-server`` (``GET /api/usage/quota``); this
module never reads provider quota files or interprets raw percentages. It maps
each cut's *effective* agent/model to the server's observation, then applies
the admission rule:

- ``exhausted`` (fresh, confirmed, in a pool the plan needs) refuses the WHOLE
  new batch before the first launch;
- ``low``, ``stale``, ``unknown``, ``absent``, ``malformed`` and an unreachable
  server are visible warnings, never silent zeros and never blocks;
- ``--ignore-usage-quota-warnings`` turns the refusal into a warning, keeps
  the diagnosis and yields an exact receipt the caller must publish durably
  before launching anything.

Nothing here switches provider or model: correcting the cast is the Operator's
call.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import Dispatch

QUOTA_PATH = "/api/usage/quota"
RECEIPT_SCHEMA = "vibecrafted.quota-override-receipt.v1"
RECEIPT_FLAG = "--ignore-usage-quota-warnings"
#: States the server may report. Anything else is a malformed observation.
KNOWN_STATES = frozenset(
    {"ok", "low", "exhausted", "stale", "unknown", "absent", "malformed"}
)
_PROBE_TIMEOUT_S = 30.0

Fetch = Callable[[str, float], Any]


@dataclass(frozen=True)
class Requirement:
    """One needed capacity: an effective agent/model and the cuts that use it."""

    agent: str
    model: str
    cuts: tuple[str, ...]


@dataclass(frozen=True)
class Finding:
    """Admission verdict for one required agent against the shared observation."""

    agent: str
    model: str
    cuts: tuple[str, ...]
    state: str
    pool: str
    account: str
    source: str
    observed_age_s: float | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        """Render this finding as a JSON-safe mapping (receipt/report shape)."""
        return {
            "agent": self.agent,
            "model": self.model,
            "cuts": list(self.cuts),
            "state": self.state,
            "pool": self.pool,
            "account": self.account,
            "source": self.source,
            "observed_age_s": self.observed_age_s,
            "detail": self.detail,
        }


@dataclass
class QuotaDiagnosis:
    """Errors, warnings and receipt material produced by one preflight."""

    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[tuple[str, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    bypassed: list[Finding] = field(default_factory=list)
    server_url: str = ""
    queried_at: str = ""
    override_requested: bool = False

    def receipt(self) -> dict[str, Any] | None:
        """Exact override record, or ``None`` when the flag was not used."""
        if not self.override_requested:
            return None
        return {
            "schema": RECEIPT_SCHEMA,
            "flag": RECEIPT_FLAG,
            "scope": "quota-admission-only",
            "overridden_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "queried_at": self.queried_at,
            "server_url": self.server_url,
            "bypassed": [finding.to_dict() for finding in self.bypassed],
            "diagnosis": [finding.to_dict() for finding in self.findings],
            "warnings": [{"path": p, "message": m} for p, m in self.warnings],
        }


def default_server_url() -> str | None:
    """Server origin from the same sources the Guardian uses; ``None`` if invalid."""
    from vibecrafted_core.server_config import ServerConfigError, load_server_config

    explicit = os.environ.get("VC_SERVER_URL") or os.environ.get(
        "VIBECRAFTED_SERVER_URL"
    )
    if explicit:
        return explicit.strip().rstrip("/") or None
    try:
        return load_server_config().public_url.rstrip("/") or None
    except ServerConfigError:
        return None


def requirements(dispatch: Dispatch) -> tuple[Requirement, ...]:
    """Effective agent/model per cut: plan model, else the launcher's default.

    ``cut.model`` is already the launcher-resolved selection (CLI, plan
    frontmatter, dispatch pin); only a provider-default cut falls back to the
    same default-model resolver the stream parser uses.
    """
    from vibecrafted_core.agent_stream import resolve_default_model

    order: list[tuple[str, str]] = []
    cuts: dict[tuple[str, str], list[str]] = {}
    for cut in dispatch.cuts:
        agent = (cut.agent or "").strip().lower()
        if not agent:
            continue
        model = (cut.model or "").strip() or resolve_default_model(agent)
        key = (agent, model)
        if key not in cuts:
            order.append(key)
            cuts[key] = []
        cuts[key].append(cut.id)
    return tuple(Requirement(a, m, tuple(cuts[(a, m)])) for a, m in order)


def _http_get_json(url: str, timeout: float) -> Any:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported quota URL scheme {parsed.scheme!r}")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    # Fixed internal quota endpoint of the operator's own vc-server.
    # nosemgrep: dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_board(
    server_url: str,
    *,
    refresh: bool,
    agents: Sequence[str] = (),
    fetch: Fetch | None = None,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Query the shared board; returns ``(items_by_agent, error)``.

    ``refresh`` asks the server to refresh authoritative quota for exactly
    ``agents`` (the plan's needed providers) -- the only moment the network is
    allowed to be touched.
    """
    query = ""
    if refresh:
        query = "?" + urllib.parse.urlencode(
            {"refresh": "preflight", "agents": ",".join(sorted(set(agents)))}
        )
    url = f"{server_url.rstrip('/')}{QUOTA_PATH}{query}"
    try:
        payload = (fetch or _http_get_json)(url, _PROBE_TIMEOUT_S)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    agents = payload.get("agents") if isinstance(payload, dict) else None
    if not isinstance(agents, list):
        return {}, "response has no 'agents' list"
    items: dict[str, dict[str, Any]] = {}
    for item in agents:
        if not isinstance(item, dict):
            continue
        key = item.get("agent") or item.get("id")
        if isinstance(key, str) and key:
            items[key.lower()] = item
    return items, ""


def _model_covered(scope_model: str, wanted: str) -> bool:
    if not scope_model or not wanted:
        return True
    left, right = scope_model.lower(), wanted.lower()
    return left in right or right in left


def _evaluate(requirement: Requirement, item: dict[str, Any] | None) -> Finding:
    agent = requirement.agent
    if item is None:
        return _finding(
            requirement,
            "absent",
            detail="server has no quota card for this agent",
        )
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
    pool = str(scope.get("pool") or item.get("pool") or agent)
    account = str(scope.get("account") or item.get("account") or "")
    source = str(item.get("source") or "")
    age = item.get("age_s")
    age_s = float(age) if isinstance(age, (int, float)) and age >= 0 else None
    state = item.get("state")
    detail = str(item.get("detail") or item.get("headline") or "")
    if not isinstance(state, str) or state not in KNOWN_STATES:
        return _finding(
            requirement,
            "malformed",
            pool=pool,
            account=account,
            source=source,
            age_s=age_s,
            detail=f"observation carries no recognised state ({state!r})",
        )
    scope_model = str(scope.get("model") or "")
    if not _model_covered(scope_model, requirement.model):
        return _finding(
            requirement,
            "unknown",
            pool=pool,
            account=account,
            source=source,
            age_s=age_s,
            detail=(
                f"observation is scoped to model {scope_model!r}; "
                f"nothing measured for {requirement.model!r}"
            ),
        )
    return _finding(
        requirement,
        state,
        pool=pool,
        account=account,
        source=source,
        age_s=age_s,
        detail=detail,
    )


def _finding(
    requirement: Requirement,
    state: str,
    *,
    pool: str = "",
    account: str = "",
    source: str = "",
    age_s: float | None = None,
    detail: str = "",
) -> Finding:
    return Finding(
        agent=requirement.agent,
        model=requirement.model,
        cuts=requirement.cuts,
        state=state,
        pool=pool or requirement.agent,
        account=account,
        source=source,
        observed_age_s=age_s,
        detail=detail,
    )


def _pool_key(item: dict[str, Any]) -> tuple[str, str]:
    scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
    pool = str(scope.get("pool") or item.get("pool") or "")
    account = str(scope.get("account") or item.get("account") or "")
    return pool, account


def _exhausted_pools(
    items: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
    """Pools (pool, account) that a fresh, confirmed observation says are empty."""
    found: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    for agent, item in items.items():
        pool, account = _pool_key(item)
        if pool and item.get("state") == "exhausted":
            found.setdefault((pool, account), (agent, item))
    return found


def _apply_shared_pool(
    finding: Finding, exhausted: dict[tuple[str, str], tuple[str, dict[str, Any]]]
) -> Finding:
    """A pool is one capacity: any agent drawing on an exhausted pool is blocked.

    Accounts must match when both sides name one; an unnamed side joins the pool.
    """
    if finding.state == "exhausted" or not finding.pool:
        return finding
    for (pool, account), (reporter, item) in exhausted.items():
        if pool != finding.pool:
            continue
        if account and finding.account and account != finding.account:
            continue
        if reporter == finding.agent:
            continue
        return Finding(
            agent=finding.agent,
            model=finding.model,
            cuts=finding.cuts,
            state="exhausted",
            pool=finding.pool,
            account=finding.account or account,
            source=str(item.get("source") or ""),
            observed_age_s=item.get("age_s")
            if isinstance(item.get("age_s"), (int, float))
            else None,
            detail=f"shared pool reported exhausted by agent '{reporter}'",
        )
    return finding


def _describe(finding: Finding) -> str:
    who = f"agent '{finding.agent}'"
    if finding.model:
        who += f" model '{finding.model}'"
    where = f"pool '{finding.pool}'"
    if finding.account:
        where += f" account '{finding.account}'"
    age = (
        f", observed {int(finding.observed_age_s)}s ago"
        if finding.observed_age_s is not None
        else ""
    )
    src = f" via {finding.source}" if finding.source else ""
    tail = f" — {finding.detail}" if finding.detail else ""
    label = {
        "ok": "quota ok",
        "low": "quota running low",
        "exhausted": "quota confirmed exhausted",
        "stale": "quota observation is stale (not current evidence)",
        "unknown": "quota unmeasured",
        "absent": "no quota observation",
        "malformed": "quota observation malformed",
    }[finding.state]
    return f"{label} for {who} ({where}{age}){src}{tail}"


def diagnose(
    dispatch: Dispatch,
    *,
    ignore_warnings: bool = False,
    enforce: bool = True,
    server_url: str | None = None,
    refresh: bool = True,
    fetch: Fetch | None = None,
) -> QuotaDiagnosis:
    """Preflight the whole batch against the shared server observation.

    ``enforce=False`` is for resuming an already-admitted batch: exhaustion is
    reported as a warning and nothing already running is refused.
    """
    diagnosis = QuotaDiagnosis(
        override_requested=ignore_warnings,
        queried_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    needed = requirements(dispatch)
    if not needed:
        return diagnosis
    url = server_url if server_url is not None else default_server_url()
    diagnosis.server_url = url or ""
    if not url:
        items: dict[str, dict[str, Any]] = {}
        failure = "no vc-server URL configured"
    else:
        items, failure = fetch_board(
            url,
            refresh=refresh,
            agents=[requirement.agent for requirement in needed],
            fetch=fetch,
        )
    if failure:
        diagnosis.warnings.append(
            (
                "quota.server",
                f"quota board unavailable ({failure}); nothing measured for "
                + ", ".join(sorted({r.agent for r in needed})),
            )
        )
    exhausted_pools = _exhausted_pools(items)
    for requirement in needed:
        finding = _evaluate(requirement, items.get(requirement.agent))
        finding = _apply_shared_pool(finding, exhausted_pools)
        diagnosis.findings.append(finding)
        path = f"quota.{finding.pool}"
        if finding.state == "exhausted":
            if not enforce:
                diagnosis.warnings.append(
                    (path, _describe(finding) + " [resume of an admitted batch]")
                )
            elif ignore_warnings:
                diagnosis.bypassed.append(finding)
                diagnosis.warnings.append(
                    (path, _describe(finding) + f" [bypassed via {RECEIPT_FLAG}]")
                )
            else:
                diagnosis.errors.append((path, _describe(finding)))
        elif finding.state != "ok":
            diagnosis.warnings.append((path, _describe(finding)))
    return diagnosis


def publish_receipt(path: Path, receipt: dict[str, Any]) -> None:
    """Write the override receipt durably (tmp + fsync + rename + dir fsync).

    Raises ``OSError`` on any failure: the caller must then refuse to launch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(receipt, ensure_ascii=False, indent=2).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
