"""Whole-batch quota admission through the public doctor and dispatch CLI.

Every test talks to a real HTTP listener serving the quota board shape
``vc-server`` emits; nothing here reads provider quota files.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from test_cli import _dispatch_file
from vibecrafted_core.dispatch import cli as dispatch_cli
from vibecrafted_core.dispatch import quota as quota_preflight
from vibecrafted_core.dispatch.doctor import diagnose_file
from vibecrafted_core.dispatch.doctor import main as doctor_main

REAL_QUOTA_BOARD = True
pytestmark = pytest.mark.usefixtures("worker_claims")


def item(agent: str, state: str, **extra: Any) -> dict[str, Any]:
    """One card in the shape of ``QuotaAgent`` (``id`` + ``agent`` + ``state``)."""
    scope = {"pool": extra.pop("pool", agent), "account": extra.pop("account", None)}
    scope["model"] = extra.pop("scope_model", None)
    return {
        "id": agent,
        "agent": agent,
        "name": agent.title(),
        "present": True,
        "status": "blocked" if state == "exhausted" else "ok",
        "state": state,
        "headline": state.upper(),
        "detail": extra.pop("detail", f"{agent} {state}"),
        "stale": state == "stale",
        "age_s": extra.pop("age_s", 5),
        "model": extra.pop("model", None),
        "source": f"~/.vibecrafted/telemetry/{agent}/quota.json",
        "pool": scope["pool"],
        "account": scope["account"],
        "scope": scope,
        **extra,
    }


class Board:
    def __init__(self) -> None:
        self.agents: list[Any] = []
        self.requests: list[str] = []
        self.status = 200
        self.raw: bytes | None = None


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Iterator[Board]:
    state = Board()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            state.requests.append(self.path)
            body = state.raw
            if body is None:
                body = json.dumps(
                    {"schema": "vibecrafted.quota-dashboard.v1", "agents": state.agents}
                ).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("VC_SERVER_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.delenv("VIBECRAFTED_SERVER_URL", raising=False)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


def _plan(tmp_path: Path, *cuts: tuple[str, str]) -> Path:
    plan, _, _ = _dispatch_file(tmp_path)
    text = plan.read_text(encoding="utf-8")
    head = text[: text.index("[[cuts]]")]
    blocks = []
    for index, (agent, model) in enumerate(cuts):
        pin = f'model = "{model}"\n' if model else ""
        blocks.append(
            f"""[[cuts]]
id = "c{index + 1}"
agent = "{agent}"
workflow = "implement"
{pin}prompt = "hello"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
"""
        )
    plan.write_text(head + "\n".join(blocks), encoding="utf-8")
    return plan


def _quota_messages(report) -> list[str]:
    return [w.message for w in report.warnings if w.path.startswith("quota")]


# --- the parent counterexamples, through the public path -------------------


def test_needed_agent_with_no_observation_is_an_explicit_warning(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [item("claude", "ok")]
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True
    messages = _quota_messages(report)
    assert len(messages) == 1 and "no quota observation" in messages[0]
    assert "codex" in messages[0]


def test_malformed_fraction_is_a_warning_not_a_crash(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [{"id": "codex", "agent": "codex", "fraction": "invalid"}]
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True
    assert "malformed" in _quota_messages(report)[0]


def test_public_doctor_main_reaches_the_server_and_refuses(
    tmp_path: Path, board: Board, capsys: pytest.CaptureFixture[str]
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai", model="gpt-6.1-sol")]
    plan = _plan(tmp_path, ("codex", "gpt-6.1-sol"))

    code = doctor_main([str(plan), "--check-quotas", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["errors"][0]["path"] == "quota.openai"
    assert "confirmed exhausted" in payload["errors"][0]["message"]
    assert payload["quota"][0]["model"] == "gpt-6.1-sol"
    assert any(path.startswith("/api/usage/quota") for path in board.requests)


def test_dispatch_cli_reaches_the_server_and_starts_nothing(
    tmp_path: Path,
    board: Board,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai")]
    plan = _plan(tmp_path, ("claude", ""), ("codex", ""))
    board.agents.append(item("claude", "ok"))

    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("exhausted pool must refuse before any launch or run reservation")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    monkeypatch.setattr(dispatch_cli, "reserve_run_id", forbidden)

    assert dispatch_cli.main([str(plan), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "quota.openai" == payload["errors"][0]["path"]


# --- states are distinct ----------------------------------------------------


@pytest.mark.parametrize(
    ("state", "needle"),
    [
        ("low", "running low"),
        ("stale", "stale"),
        ("unknown", "unmeasured"),
        ("absent", "no quota observation"),
        ("malformed", "malformed"),
    ],
)
def test_non_exhausted_states_warn_but_never_block(
    tmp_path: Path, board: Board, state: str, needle: str
) -> None:
    board.agents = [item("codex", state)]
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True and report.errors == ()
    assert needle in _quota_messages(report)[0]


def test_stale_exhaustion_is_not_current_confirmation(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [item("codex", "stale", detail="was EXHAUSTED", age_s=9000)]
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True
    assert "not current evidence" in _quota_messages(report)[0]
    assert "9000s ago" in _quota_messages(report)[0]


def test_unneeded_pool_exhaustion_does_not_block(tmp_path: Path, board: Board) -> None:
    board.agents = [item("kimi", "exhausted", pool="moonshot"), item("claude", "ok")]
    report = diagnose_file(_plan(tmp_path, ("claude", "")), check_quotas=True)

    assert report.ok is True and not _quota_messages(report)


def test_server_down_is_a_visible_unknown_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VC_SERVER_URL", "http://127.0.0.1:1")
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True
    assert any("quota board unavailable" in m for m in _quota_messages(report))
    assert any("no quota observation" in m for m in _quota_messages(report))


def test_http_error_body_is_malformed_board_warning(
    tmp_path: Path, board: Board
) -> None:
    board.raw = b"<html>nope</html>"
    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is True
    assert any("quota board unavailable" in m for m in _quota_messages(report))


# --- effective selection and shared pools -----------------------------------


def test_model_scoped_observation_does_not_cover_other_models(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [item("agy", "exhausted", pool="google", scope_model="gemini-3-pro")]
    pinned_other = diagnose_file(
        _plan(tmp_path, ("agy", "gemini-3-flash")), check_quotas=True
    )
    assert pinned_other.ok is True
    assert "scoped to model 'gemini-3-pro'" in _quota_messages(pinned_other)[0]

    (tmp_path / "again").mkdir()
    pinned_same = diagnose_file(
        _plan(tmp_path / "again", ("agy", "gemini-3-pro")), check_quotas=True
    )
    assert pinned_same.ok is False


def test_effective_model_comes_from_the_cut_and_requirements_group_by_it(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [item("codex", "ok")]
    plan = _plan(tmp_path, ("codex", "m-a"), ("codex", "m-b"), ("codex", "m-a"))
    report = diagnose_file(plan, check_quotas=True)
    needed = quota_preflight.requirements(report.dispatch)

    assert [(r.agent, r.model, r.cuts) for r in needed] == [
        ("codex", "m-a", ("c1", "c3")),
        ("codex", "m-b", ("c2",)),
    ]


def test_shared_pool_exhaustion_applies_to_every_agent_in_the_pool(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [
        item("copilot", "exhausted", pool="shared-credits", account="team"),
        item("cursor", "unknown", pool="shared-credits", account="team"),
    ]
    report = diagnose_file(_plan(tmp_path, ("cursor", "")), check_quotas=True)

    assert report.ok is False
    assert "shared-credits" in report.errors[0].message
    assert "copilot" in report.errors[0].message


# --- override ---------------------------------------------------------------


def test_override_keeps_diagnosis_and_publishes_exact_receipt_before_launch(
    tmp_path: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai", account="acct-1")]
    plan = _plan(tmp_path, ("codex", "gpt-x"))
    seen: dict[str, Any] = {}

    def launch(dispatch, *, artifacts_dir, run_id, **_kw):
        receipt = artifacts_dir / "quota-override-receipt.json"
        seen["receipt"] = json.loads(receipt.read_text(encoding="utf-8"))
        raise RuntimeError("stop after the receipt check")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", launch)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) != 0

    receipt = seen["receipt"]
    assert receipt["schema"] == quota_preflight.RECEIPT_SCHEMA
    assert receipt["scope"] == "quota-admission-only"
    assert receipt["bypassed"][0]["agent"] == "codex"
    assert receipt["bypassed"][0]["model"] == "gpt-x"
    assert receipt["bypassed"][0]["pool"] == "openai"
    assert receipt["bypassed"][0]["account"] == "acct-1"
    assert receipt["bypassed"][0]["state"] == "exhausted"
    assert receipt["diagnosis"] == receipt["bypassed"]


def test_override_receipt_failure_refuses_the_launch(
    tmp_path: Path,
    board: Board,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai")]
    plan = _plan(tmp_path, ("codex", ""))

    def broken(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("no durable override decision, no launch")

    monkeypatch.setattr(dispatch_cli, "publish_quota_receipt", broken)
    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)

    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "could not be published" in capsys.readouterr().err


def test_override_never_bypasses_unrelated_admission(
    tmp_path: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai")]
    plan = _plan(tmp_path, ("codex", ""))
    plan.write_text(
        plan.read_text().replace('run = "echo ok"', 'run = "echo broken; exit 7"')
    )

    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("red baseline must still refuse")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1


def test_resume_of_an_admitted_batch_is_not_refused_by_exhaustion(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [item("codex", "exhausted", pool="openai")]
    report = diagnose_file(
        _plan(tmp_path, ("codex", "")), check_quotas=True, quota_enforce=False
    )

    assert report.ok is True
    assert "resume of an admitted batch" in _quota_messages(report)[0]


def test_preflight_asks_the_server_to_refresh(tmp_path: Path, board: Board) -> None:
    board.agents = [item("codex", "ok")]
    diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert board.requests == ["/api/usage/quota?refresh=preflight&agents=codex"]


# --- the committed wire contract (generated by the Rust server's own test) --

CONTRACT = Path(__file__).parent / "fixtures" / "quota_board_contract.json"


def _contract_board(board: Board) -> dict[str, Any]:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    board.raw = json.dumps(payload).encode()
    return {item["agent"]: item for item in payload["agents"]}


def test_contract_exhausted_pool_refuses_the_batch(
    tmp_path: Path, board: Board
) -> None:
    items = _contract_board(board)
    assert items["codex"]["state"] == "exhausted"

    report = diagnose_file(_plan(tmp_path, ("codex", "")), check_quotas=True)

    assert report.ok is False
    assert report.errors[0].path == "quota.openai"
    assert "acct-1" in report.errors[0].message


@pytest.mark.parametrize(
    ("agent", "state", "needle"),
    [
        ("claude", "low", "running low"),
        ("grok", "stale", "not current evidence"),
        ("junie", "unknown", "unmeasured"),
        ("copilot", "malformed", "malformed"),
        ("cursor", "unknown", "unmeasured"),
        ("kimi", "unknown", "unmeasured"),
        ("agy", "stale", "not current evidence"),
    ],
)
def test_contract_every_other_state_warns_and_never_blocks(
    tmp_path: Path, board: Board, agent: str, state: str, needle: str
) -> None:
    items = _contract_board(board)
    assert items[agent]["state"] == state

    report = diagnose_file(_plan(tmp_path, (agent, "")), check_quotas=True)

    assert report.ok is True and report.errors == ()
    assert needle in _quota_messages(report)[0]


def test_contract_agy_is_scoped_to_its_model(tmp_path: Path, board: Board) -> None:
    items = _contract_board(board)
    assert items["agy"]["scope"]["model"] == "gemini-3.1-pro-high"

    report = diagnose_file(
        _plan(tmp_path, ("agy", "gemini-3-flash")), check_quotas=True
    )

    assert report.ok is True
    assert "scoped to model" in _quota_messages(report)[0]
