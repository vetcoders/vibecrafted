"""Claim fixtures live once in ``tests/conftest.py`` (``claim_doubles``).

Explicit claim behavior for legacy launcher doubles, never a global bypass.
Dispatch modules opt in with
``pytestmark = pytest.mark.usefixtures("worker_claims")``.

Quota isolation: the dispatch doctor consults the shared vc-server board. A
developer host may be running one, so every dispatch test sees a deterministic
all-``ok`` board instead of whatever listens on the default port. Modules that
exercise the real HTTP path set ``REAL_QUOTA_BOARD = True`` and bring their own
server.
"""

from __future__ import annotations

import pytest
from vibecrafted_core.dispatch import quota as quota_preflight


@pytest.fixture(autouse=True)
def _isolated_quota_board(request: pytest.FixtureRequest, monkeypatch) -> None:
    if getattr(request.module, "REAL_QUOTA_BOARD", False):
        return

    def ok_board(server_url, *, refresh, agents=(), fetch=None):
        agents = [
            "agy",
            "claude",
            "codex",
            "copilot",
            "cursor",
            "grok",
            "junie",
            "kimi",
        ]
        return {
            agent: [
                {
                    "id": agent,
                    "agent": agent,
                    "state": "ok",
                    "pool": agent,
                    "stale": False,
                    "age_s": 5,
                }
            ]
            for agent in agents
        }, ""

    monkeypatch.setattr(quota_preflight, "fetch_board", ok_board)
