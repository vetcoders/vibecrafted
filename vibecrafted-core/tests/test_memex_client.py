"""Tests for the memex cross-session retrieval client (Plan 09).

Covers:
- Config loading from TOML, env vars, and pure defaults.
- ``search()`` success path via mock ``mcp_call`` injection.
- Graceful degradation on every documented failure mode (no token,
  malformed response, transport exception).
- ``MemexChunk`` authority label invariant.

The HTTP path is exercised through monkey-patching of
``memex_client._http_search`` so no real network I/O is performed
(the transport now uses ``http.client`` directly with a scheme allowlist).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import memex_client as mc

# ----------------------------------------------------------- config tests


def test_load_config_pure_defaults(tmp_path: Path) -> None:
    cfg = mc.load_config(config_path=tmp_path / "nope.toml", environ={})
    assert cfg.endpoint == mc.DEFAULT_ENDPOINT
    assert cfg.token == ""
    assert cfg.default_namespace == "local"
    assert cfg.timeout_seconds == mc.DEFAULT_TIMEOUT_SECONDS
    assert cfg.enabled is False
    assert cfg.source == "default"
    assert cfg.auth_header == {}


def test_load_config_env_only_enables_when_token_present(tmp_path: Path) -> None:
    env = {
        "MEMEX_ENDPOINT": "http://host-d.local:11211",
        "MEMEX_TOKEN": "tok-abc",
        "MEMEX_NAMESPACE": "vibecrafted",
        "MEMEX_TIMEOUT_SECONDS": "2.5",
    }
    cfg = mc.load_config(config_path=tmp_path / "absent.toml", environ=env)
    assert cfg.endpoint == "http://host-d.local:11211"
    assert cfg.token == "tok-abc"
    assert cfg.default_namespace == "vibecrafted"
    assert cfg.timeout_seconds == 2.5
    assert cfg.enabled is True
    assert cfg.source == "env"
    assert cfg.auth_header == {"Authorization": "Bearer tok-abc"}


def test_load_config_env_endpoint_without_token_stays_disabled(
    tmp_path: Path,
) -> None:
    env = {"MEMEX_ENDPOINT": "http://only.local:11211"}
    cfg = mc.load_config(config_path=tmp_path / "absent.toml", environ=env)
    assert cfg.endpoint == "http://only.local:11211"
    assert cfg.enabled is False


def test_load_config_invalid_timeout_falls_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    env = {
        "MEMEX_ENDPOINT": "http://x.local",
        "MEMEX_TOKEN": "t",
        "MEMEX_TIMEOUT_SECONDS": "not-a-float",
    }
    with caplog.at_level(logging.WARNING, logger="vibecrafted_core.memex_client"):
        cfg = mc.load_config(config_path=tmp_path / "absent.toml", environ=env)
    assert cfg.timeout_seconds == mc.DEFAULT_TIMEOUT_SECONDS
    assert any("MEMEX_TIMEOUT_SECONDS" in rec.message for rec in caplog.records)


def test_load_config_toml_wins_over_env(tmp_path: Path) -> None:
    cfg_path = tmp_path / "memex.toml"
    cfg_path.write_text(
        'endpoint = "http://memex.local:11211"\n'
        'token = "tok-from-toml"\n'
        'default_namespace = "team-ns"\n'
        "timeout_seconds = 7.5\n",
        encoding="utf-8",
    )
    env = {
        "MEMEX_ENDPOINT": "http://env.local",
        "MEMEX_TOKEN": "tok-from-env",
        "MEMEX_NAMESPACE": "env-ns",
        "MEMEX_TIMEOUT_SECONDS": "1.0",
    }
    cfg = mc.load_config(config_path=cfg_path, environ=env)
    # Config-file source: env should NOT override the explicit values.
    assert cfg.endpoint == "http://memex.local:11211"
    assert cfg.token == "tok-from-toml"
    assert cfg.default_namespace == "team-ns"
    assert cfg.timeout_seconds == 7.5
    assert cfg.enabled is True
    assert "config:" in cfg.source


def test_load_config_toml_without_token_can_borrow_env(tmp_path: Path) -> None:
    cfg_path = tmp_path / "memex.toml"
    cfg_path.write_text(
        'endpoint = "http://memex.local:11211"\ndefault_namespace = "ops"\n',
        encoding="utf-8",
    )
    env = {"MEMEX_TOKEN": "tok-borrowed"}
    cfg = mc.load_config(config_path=cfg_path, environ=env)
    assert cfg.token == "tok-borrowed"
    assert cfg.enabled is True
    assert "env-token" in cfg.source


def test_load_config_strips_trailing_slash(tmp_path: Path) -> None:
    cfg_path = tmp_path / "memex.toml"
    cfg_path.write_text(
        'endpoint = "http://memex.local:11211/"\ntoken = "t"\n',
        encoding="utf-8",
    )
    cfg = mc.load_config(config_path=cfg_path, environ={})
    assert cfg.endpoint == "http://memex.local:11211"


# ----------------------------------------------------------- chunk parsing


def test_memex_chunk_authority_label_locked() -> None:
    chunk = mc.MemexChunk(
        text="t", score=0.9, source="s", namespace="n", retrieved_at="now"
    )
    assert chunk.authority == mc.MEMEX_AUTHORITY_LABEL == "memex_derived"


def test_search_parses_chunks_via_mcp_bridge() -> None:
    calls: list[tuple[str, str, int]] = []

    def fake_mcp(query: str, namespace: str, limit: int) -> dict[str, Any]:
        calls.append((query, namespace, limit))
        return {
            "chunks": [
                {
                    "text": "doctrine 2026-05-05 mesh topology",
                    "score": 0.93,
                    "source": "aicx/chronicle.md",
                    "namespace": namespace,
                },
                {
                    "text": "Plan 09 memex fallthrough",
                    "score": 0.81,
                    "source": "META_22",
                },
            ]
        }

    cfg = mc.MemexConfig(
        endpoint="http://x",
        token="t",
        default_namespace="ns",
        timeout_seconds=1.0,
        enabled=True,
        source="test",
    )
    out = mc.search("vc-init Sense 1", limit=3, config=cfg, mcp_call=fake_mcp)
    assert len(out) == 2
    assert calls == [("vc-init Sense 1", "ns", 3)]
    assert all(c.authority == "memex_derived" for c in out)
    assert out[0].score == pytest.approx(0.93)
    assert out[1].namespace == "ns"  # default fill-in


def test_search_empty_query_short_circuits() -> None:
    assert mc.search("") == []
    assert mc.search("   ") == []


def test_search_mcp_failure_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def boom(query: str, namespace: str, limit: int) -> dict[str, Any]:
        raise RuntimeError("memex MCP socket closed")

    cfg = mc.MemexConfig(
        endpoint="x",
        token="t",
        default_namespace="ns",
        timeout_seconds=1.0,
        enabled=True,
    )
    with caplog.at_level(logging.WARNING, logger="vibecrafted_core.memex_client"):
        out = mc.search("q", config=cfg, mcp_call=boom)
    assert out == []
    assert any("MCP call failed" in rec.message for rec in caplog.records)


def test_search_disabled_config_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cfg = mc.MemexConfig(
        endpoint=mc.DEFAULT_ENDPOINT,
        token="",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=False,
        source="default",
    )
    with caplog.at_level(logging.INFO, logger="vibecrafted_core.memex_client"):
        out = mc.search("q", config=cfg)
    assert out == []
    assert any("disabled" in rec.message for rec in caplog.records)


def test_search_http_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch _http_search so the search() pipeline exercises real parsing
    without touching the actual http.client transport (covered separately
    in test_http_search_unsupported_scheme + integration sandbox).
    """
    captured: dict[str, Any] = {}

    def fake_http_search(
        config: mc.MemexConfig, query: str, namespace: str, limit: int
    ) -> dict[str, Any]:
        captured["endpoint"] = config.endpoint
        captured["timeout"] = config.timeout_seconds
        captured["query"] = query
        captured["namespace"] = namespace
        captured["limit"] = limit
        return {
            "chunks": [
                {"text": "mesh topology host-d", "score": 0.7, "source": "chronicle"},
                {"content": "fallback content field", "score": "0.5"},  # tolerant
                {"no_text_field": True},  # filtered out
            ]
        }

    monkeypatch.setattr(mc, "_http_search", fake_http_search)

    cfg = mc.MemexConfig(
        endpoint="http://memex.local:11211",
        token="tok",
        default_namespace="local",
        timeout_seconds=3.0,
        enabled=True,
    )
    out = mc.search("vc-init", namespace="team-ns", limit=5, config=cfg)
    assert len(out) == 2
    assert out[0].text == "mesh topology host-d"
    assert out[1].text == "fallback content field"
    assert out[1].score == pytest.approx(0.5)
    assert all(c.authority == mc.MEMEX_AUTHORITY_LABEL for c in out)
    # Request invariants reached the transport helper unchanged.
    assert captured["endpoint"] == "http://memex.local:11211"
    assert captured["timeout"] == 3.0
    assert captured["query"] == "vc-init"
    assert captured["namespace"] == "team-ns"
    assert captured["limit"] == 5


def test_search_http_unreachable_returns_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import http.client

    def fake_http_search(
        config: mc.MemexConfig, query: str, namespace: str, limit: int
    ) -> dict[str, Any]:
        raise http.client.HTTPException("Connection refused")

    monkeypatch.setattr(mc, "_http_search", fake_http_search)
    cfg = mc.MemexConfig(
        endpoint="http://memex.local:11211",
        token="t",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=True,
    )
    with caplog.at_level(logging.WARNING, logger="vibecrafted_core.memex_client"):
        out = mc.search("q", config=cfg)
    assert out == []
    assert any("unreachable" in rec.message for rec in caplog.records)


def test_search_http_os_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_http_search(
        config: mc.MemexConfig, query: str, namespace: str, limit: int
    ) -> dict[str, Any]:
        raise OSError("Network is unreachable")

    monkeypatch.setattr(mc, "_http_search", fake_http_search)
    cfg = mc.MemexConfig(
        endpoint="http://memex.local:11211",
        token="t",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=True,
    )
    with caplog.at_level(logging.WARNING, logger="vibecrafted_core.memex_client"):
        out = mc.search("q", config=cfg)
    assert out == []
    assert any("unreachable" in rec.message for rec in caplog.records)


def test_search_http_client_error_returns_empty(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fake_http_search(
        config: mc.MemexConfig, query: str, namespace: str, limit: int
    ) -> dict[str, Any]:
        raise mc.MemexClientError("malformed JSON from memex: line 1 col 1")

    monkeypatch.setattr(mc, "_http_search", fake_http_search)
    cfg = mc.MemexConfig(
        endpoint="http://memex.local:11211",
        token="t",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=True,
    )
    with caplog.at_level(logging.WARNING, logger="vibecrafted_core.memex_client"):
        out = mc.search("q", config=cfg)
    assert out == []


def test_http_search_rejects_unsupported_scheme() -> None:
    """The transport refuses non-http(s) schemes to block config tampering."""
    cfg = mc.MemexConfig(
        endpoint="file:///etc/passwd",
        token="t",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=True,
    )
    with pytest.raises(mc.MemexClientError, match="unsupported scheme"):
        mc._http_search(cfg, "q", "local", 5)


def test_http_search_rejects_missing_host() -> None:
    cfg = mc.MemexConfig(
        endpoint="http://",
        token="t",
        default_namespace="local",
        timeout_seconds=1.0,
        enabled=True,
    )
    with pytest.raises(mc.MemexClientError, match="missing host"):
        mc._http_search(cfg, "q", "local", 5)


def test_search_limit_is_clamped() -> None:
    seen: list[int] = []

    def fake_mcp(q: str, n: str, lim: int) -> dict[str, Any]:
        seen.append(lim)
        return {"chunks": []}

    cfg = mc.MemexConfig(
        endpoint="x",
        token="t",
        default_namespace="ns",
        timeout_seconds=1.0,
        enabled=True,
    )
    mc.search("q", limit=0, config=cfg, mcp_call=fake_mcp)
    mc.search("q", limit=999, config=cfg, mcp_call=fake_mcp)
    assert seen == [1, 50]


def test_sparse_aicx_threshold_is_five() -> None:
    # Plan 09 contract: < 5 chunks triggers fallthrough.
    assert mc.SPARSE_AICX_THRESHOLD == 5


def test_public_surface_exports_remain_stable() -> None:
    """Lock the __all__ surface so renames are caught by CI."""
    assert set(mc.__all__) == {
        "MEMEX_AUTHORITY_LABEL",
        "DEFAULT_CONFIG_PATH",
        "DEFAULT_ENDPOINT",
        "DEFAULT_TIMEOUT_SECONDS",
        "SPARSE_AICX_THRESHOLD",
        "MemexChunk",
        "MemexConfig",
        "MemexClientError",
        "load_config",
        "search",
    }


def test_payload_helper_unused_json_keeps_module_importable() -> None:
    # Smoke: stdlib imports the test file uses stay reachable.
    assert json.dumps({"k": 1}) == '{"k": 1}'
