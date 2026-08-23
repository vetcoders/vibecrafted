"""The one clock.

Every receipt, ledger entry, meta stamp and heartbeat in the control plane
carries a timestamp, and until 2026-08-23 eleven modules each had their own
``now`` — three ``utc_now``, seven ``_now_iso``, one ``_now`` — in three
formats, one of them local time. Two machines comparing "which is newer" could
disagree by a timezone. This module is the only place that reads the wall
clock for product state.

Serialisations (the truth is one aware UTC instant; the spelling is the
consumer's contract with its readers):

* :func:`utc_now_iso` — RFC 3339 with ``+00:00`` offset; the control plane,
  settlement, trust, spawn and perception write this.
* :func:`utc_now_z` — the same instant with a ``Z`` suffix; operator-loop and
  cron journals write this.
* :func:`utc_now_compact` — ``YYYYMMDDTHHMMSSZ`` for file names.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Current UTC time as RFC 3339 text with a ``+00:00`` offset."""
    return utc_now().isoformat()


def utc_now_z() -> str:
    """Current UTC time as ISO 8601 text with a trailing ``Z``."""
    return utc_now().isoformat().replace("+00:00", "Z")


def utc_now_compact() -> str:
    """Current UTC time as ``YYYYMMDDTHHMMSSZ`` — safe inside file names."""
    return utc_now().strftime("%Y%m%dT%H%M%SZ")
