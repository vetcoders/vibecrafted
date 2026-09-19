"""Memex cross-session retrieval client (Plan 09).

Wires the locally-installed ``rust-memex`` foundation into the
``/vc-init`` Sense 1 (intentions) perception layer as a fallthrough.
When local AICX search returns sparse results for the current scope
(< 5 chunks by default), the agent SHOULD call :func:`search` to pull
cross-session semantic context from the configured memex
endpoint.

Design rules:

- **Optional, opt-in, never load-bearing.** Vibecrafted MUST work
  without memex configured. Endpoint unreachable, missing token,
  malformed config — every degradation path returns an empty list
  and emits a single ``logging`` warning. Never raise.
- **Authority tier.** Chunks returned here carry the new authority
  label ``memex_derived`` — lower trust than ``aicx_operator``
  (sticky operator intent) but useful for cross-session pattern
  matching. See ``skills/vc-init/SKILL.md`` Sense 1 for the trust
  ranking.
- **Configuration precedence.** The ``[memex]`` table of the one
  operator config, ``${XDG_CONFIG_HOME:-~/.config}/vibecrafted/config.toml``
  (the file that also holds ``[server]``), wins over environment
  variables; environment is the fallback for ephemeral / CI use. Either
  path may set ``endpoint``, ``token``, ``default_namespace``,
  ``timeout_seconds``. A token in that file makes it a secret: keep the
  file mode ``0600``.
- **Transport.** HTTP POST to ``{endpoint}/search`` with a JSON body
  ``{"query": ..., "namespace": ..., "limit": ...}`` and bearer
  authorization. The rust-memex SSE wire form is consumed via the
  same HTTP surface (POST returns JSON for non-streaming search).
  An MCP bridge (``mcp__rust-memex__memory_search``) provides the
  same semantics when MCP transport is preferred — wire that
  through the optional ``mcp_call`` injection point.

The client is intentionally tiny: it has no third-party dependencies,
type-checks under strict mypy, and degrades gracefully so the absence
of memex is invisible to operators who haven't opted in.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomllib

from .server_config import config_path as operator_config_path

__all__ = [
    "CONFIG_SECTION",
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "MEMEX_AUTHORITY_LABEL",
    "SPARSE_AICX_THRESHOLD",
    "MemexChunk",
    "MemexClientError",
    "MemexConfig",
    "load_config",
    "search",
]

_logger = logging.getLogger(__name__)

# ----------------------------------------------------------- module constants

#: Authority label tag for memex-sourced context. New label introduced by
#: Plan 09. Lower trust than ``aicx_operator``; equal or slightly higher
#: than ``aicx_agent`` depending on operator review.
MEMEX_AUTHORITY_LABEL: str = "memex_derived"

#: Table of the operator config (``server_config.config_path()``) that holds
#: the memex settings. Precedence over env vars.
CONFIG_SECTION: str = "memex"

#: Default memex endpoint. Override via config or env.
DEFAULT_ENDPOINT: str = "http://memex.local:11211"

#: Default HTTP timeout in seconds.
DEFAULT_TIMEOUT_SECONDS: float = 5.0

#: Sense 1 fallthrough threshold. If local AICX returns fewer than this many
#: chunks for the current scope, Sense 1 SHOULD fall through to memex.
SPARSE_AICX_THRESHOLD: int = 5

# ----------------------------------------------------------- data classes


@dataclass(frozen=True)
class MemexChunk:
    """A single retrieved memex chunk.

    The authority field is always set to :data:`MEMEX_AUTHORITY_LABEL` so
    that downstream consumers (Sense 1 ranking, context atlas merge) can
    weight memex hits below first-party AICX without further bookkeeping.
    """

    text: str
    score: float
    source: str
    namespace: str
    retrieved_at: str
    authority: str = MEMEX_AUTHORITY_LABEL


@dataclass(frozen=True)
class MemexConfig:
    """Resolved memex client configuration.

    Construct via :func:`load_config`. ``enabled`` is True iff both an
    endpoint and a token are resolved; without those the client
    short-circuits every call to an empty list.
    """

    endpoint: str
    token: str
    default_namespace: str
    timeout_seconds: float
    enabled: bool
    source: str = "unknown"

    @property
    def auth_header(self) -> dict[str, str]:
        """Return the Authorization header dict, or empty when disabled."""
        if not self.enabled or not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}


class MemexClientError(RuntimeError):
    """Internal-only error type.

    Public :func:`search` NEVER raises this — it converts to an empty
    list + warning log. The type exists for test fixtures that want
    to assert on classification of degradation paths.
    """


# ----------------------------------------------------------- config loading


def _coerce_str(value: Any, default: str) -> str:
    """Return ``value`` if it is a non-empty string, else ``default``."""
    if isinstance(value, str) and value:
        return value
    return default


def _coerce_float(value: Any, default: float) -> float:
    """Return ``value`` as a positive float, else ``default``."""
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return default


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML config file; return ``{}`` on any I/O or decode failure."""
    try:
        with path.open("rb") as handle:
            loaded: dict[str, Any] = tomllib.load(handle)
            return loaded
    except (OSError, tomllib.TOMLDecodeError) as exc:  # pragma: no cover
        _logger.warning("memex: failed to parse %s: %s", path, exc)
        return {}


_retired_notice_emitted = False


def _notice_retired_config(cfg_path: Path) -> None:
    """Name the retired standalone memex file once per process; never read it."""
    global _retired_notice_emitted
    if _retired_notice_emitted:
        return
    config_home = cfg_path.parent.parent
    retired = config_home / "vetcoders" / "memex.toml"
    if retired.exists():
        _retired_notice_emitted = True
        _logger.warning(
            "memex: %s is no longer read; move its keys into the [%s] table of %s",
            retired,
            CONFIG_SECTION,
            cfg_path,
        )


def load_config(
    *,
    config_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> MemexConfig:
    """Resolve memex configuration from config file, env, or defaults.

    Resolution order:

    1. The ``[memex]`` table of the operator config file (by default
       ``server_config.config_path()``, i.e.
       ``${XDG_CONFIG_HOME:-~/.config}/vibecrafted/config.toml``) when the
       file parses cleanly and carries that table. ``endpoint`` + ``token``
       MUST both be present for ``enabled=True``.
    2. Environment variables: ``MEMEX_ENDPOINT``, ``MEMEX_TOKEN``,
       ``MEMEX_NAMESPACE``, ``MEMEX_TIMEOUT_SECONDS``.
    3. Pure defaults (``enabled=False`` — endpoint set, token empty).

    The returned :class:`MemexConfig` is always usable; ``enabled``
    reflects whether :func:`search` will attempt network I/O.

    :param config_path: override the operator config file (tests pass a
        sandbox path); its ``[memex]`` table is read.
    :param environ: override ``os.environ`` (tests inject a dict).
    """
    env = environ if environ is not None else dict(os.environ)
    if config_path is None:
        cfg_path = operator_config_path()
        _notice_retired_config(cfg_path)
    else:
        cfg_path = config_path

    endpoint = DEFAULT_ENDPOINT
    token = ""
    namespace = "local"
    timeout = DEFAULT_TIMEOUT_SECONDS
    source = "default"

    # Layer 1: [memex] table of the config file (highest precedence).
    section: Any = (
        _read_toml(cfg_path).get(CONFIG_SECTION) if cfg_path.is_file() else None
    )
    if section is not None and not isinstance(section, dict):
        _logger.warning(
            "memex: [%s] in %s is not a table; ignoring it", CONFIG_SECTION, cfg_path
        )
        section = None
    if section is not None:
        endpoint = _coerce_str(section.get("endpoint"), endpoint)
        token = _coerce_str(section.get("token"), token)
        namespace = _coerce_str(section.get("default_namespace"), namespace)
        timeout = _coerce_float(section.get("timeout_seconds"), timeout)
        source = f"config:{cfg_path}[{CONFIG_SECTION}]"

    # Layer 2: env vars (fill any gaps, never override an explicit
    # config-file value — except token, where an empty config token +
    # populated env token still enables the client).
    env_endpoint = env.get("MEMEX_ENDPOINT", "").strip()
    env_token = env.get("MEMEX_TOKEN", "").strip()
    env_namespace = env.get("MEMEX_NAMESPACE", "").strip()
    env_timeout_raw = env.get("MEMEX_TIMEOUT_SECONDS", "").strip()

    if source == "default":
        if env_endpoint:
            endpoint = env_endpoint
            source = "env"
        if env_token:
            token = env_token
            source = "env"
        if env_namespace:
            namespace = env_namespace
        if env_timeout_raw:
            try:
                timeout = max(0.1, float(env_timeout_raw))
            except ValueError:
                _logger.warning(
                    "memex: invalid MEMEX_TIMEOUT_SECONDS=%r; keeping %.1f",
                    env_timeout_raw,
                    timeout,
                )
    else:
        # Config file already set values; env tokens only fill missing token.
        if not token and env_token:
            token = env_token
            source = f"{source}+env-token"

    enabled = bool(endpoint and token)
    return MemexConfig(
        endpoint=endpoint.rstrip("/"),
        token=token,
        default_namespace=namespace or "local",
        timeout_seconds=timeout,
        enabled=enabled,
        source=source,
    )


# ----------------------------------------------------------- search


def _parse_chunks(
    payload: Any,
    *,
    namespace: str,
    retrieved_at: str,
) -> list[MemexChunk]:
    """Parse a memex JSON response into chunks. Defensive on shape drift."""
    if not isinstance(payload, dict):
        return []
    raw_chunks = payload.get("chunks") or payload.get("results") or payload.get("hits")
    if not isinstance(raw_chunks, list):
        return []
    out: list[MemexChunk] = []
    for item in raw_chunks:
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content") or item.get("body")
        if not isinstance(text, str) or not text:
            continue
        score_raw: Any = item.get("score", item.get("similarity", 0.0))
        try:
            score = float(score_raw) if score_raw is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        source_raw = item.get("source") or item.get("id") or "memex"
        if not isinstance(source_raw, str):
            source_raw = str(source_raw)
        ns_raw = item.get("namespace") or namespace
        if not isinstance(ns_raw, str):
            ns_raw = namespace
        out.append(
            MemexChunk(
                text=text,
                score=score,
                source=source_raw,
                namespace=ns_raw,
                retrieved_at=retrieved_at,
            )
        )
    return out


_ALLOWED_HTTP_SCHEMES = frozenset({"http", "https"})


def _http_search(
    config: MemexConfig,
    query: str,
    namespace: str,
    limit: int,
) -> dict[str, Any]:
    """Issue the HTTP search request. May raise; caller catches.

    Uses ``http.client`` directly with an explicit scheme allowlist so the
    endpoint cannot resolve to ``file://`` / ``ftp://`` / other schemes
    (a defence against config tampering). The operator-controlled endpoint
    is parsed into ``(scheme, host, port, path)`` and a fresh connection
    object is constructed per call.
    """
    body = json.dumps({"query": query, "namespace": namespace, "limit": limit}).encode(
        "utf-8"
    )
    parsed = urllib.parse.urlparse(config.endpoint)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_HTTP_SCHEMES:
        raise MemexClientError(
            f"refusing memex fetch on unsupported scheme: {scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise MemexClientError("memex endpoint missing host component")
    port = parsed.port or (443 if scheme == "https" else 80)
    base_path = parsed.path.rstrip("/")
    path = f"{base_path}/search" if base_path else "/search"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    headers.update(config.auth_header)
    connection_cls: type[http.client.HTTPConnection]
    if scheme == "https":
        connection_cls = http.client.HTTPSConnection
    else:
        connection_cls = http.client.HTTPConnection
    conn = connection_cls(host, port, timeout=config.timeout_seconds)
    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
    finally:
        conn.close()
    if status >= 400:
        raise MemexClientError(f"memex returned HTTP {status} for POST {path}")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MemexClientError(f"malformed JSON from memex: {exc}") from exc
    if not isinstance(decoded, dict):
        raise MemexClientError(f"unexpected response shape: {type(decoded).__name__}")
    return decoded


def search(
    query: str,
    *,
    namespace: str | None = None,
    limit: int = 10,
    config: MemexConfig | None = None,
    mcp_call: Callable[[str, str, int], dict[str, Any]] | None = None,
) -> list[MemexChunk]:
    """Search memex for cross-session context. Always degrades gracefully.

    :param query: free-text query (e.g. "vc-init Sense 1 sparse AICX").
    :param namespace: memex namespace to query; defaults to
        ``config.default_namespace``.
    :param limit: max chunks to return (1..50 reasonable).
    :param config: pre-resolved config; loads from disk if omitted.
    :param mcp_call: optional alternate transport. When provided, the
        function calls ``mcp_call(query, namespace, limit)`` and expects
        a dict with the same shape as the HTTP endpoint. Used when the
        agent prefers the ``mcp__rust-memex__memory_search`` MCP bridge
        over direct HTTP. Test fixtures inject a stub here.
    :returns: list of :class:`MemexChunk` (possibly empty). Never raises.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    cfg = config if config is not None else load_config()
    ns = namespace or cfg.default_namespace
    limit = max(1, min(int(limit), 50))
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if mcp_call is not None:
        try:
            payload = mcp_call(query, ns, limit)
        except Exception as exc:  # noqa: BLE001 - an injected MCP transport may raise anything; recall degrades
            _logger.warning("memex MCP call failed: %s", exc)
            return []
        return _parse_chunks(payload, namespace=ns, retrieved_at=retrieved_at)

    if not cfg.enabled:
        _logger.info(
            "memex: disabled (configuration incomplete; source=%s) — "
            "returning empty result list",
            cfg.source,
        )
        return []

    try:
        payload = _http_search(cfg, query, ns, limit)
    except (http.client.HTTPException, OSError) as exc:
        _logger.warning(
            "memex: endpoint unreachable (%s): %s — degrading to empty list",
            cfg.endpoint,
            exc,
        )
        return []
    except MemexClientError as exc:
        _logger.warning("memex: %s — degrading to empty list", exc)
        return []
    except Exception as exc:  # noqa: BLE001  # last-resort net: never raise to caller
        _logger.warning("memex: unexpected failure: %s — degrading", exc)
        return []

    return _parse_chunks(payload, namespace=ns, retrieved_at=retrieved_at)
