"""Whole-batch quota admission through the public doctor and dispatch CLI.

Every test talks to a real HTTP listener serving the quota board shape
``vc-server`` emits; nothing here reads provider quota files.
"""

from __future__ import annotations

import json
import subprocess
import sys
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
    # The Rust golden normalizes wall-clock-dependent ages, not the wire types.
    for card in payload["agents"]:
        if card["age_s"] == "<fresh>":
            card["age_s"] = 5
        elif card["age_s"] == "<old>":
            card["age_s"] = 9000
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

    report = diagnose_file(
        _plan(tmp_path, (agent, items[agent]["scope"]["model"] or "")),
        check_quotas=True,
    )

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


@pytest.mark.parametrize(
    "reported,wanted", [("model-a", "model-a-plus"), ("model-a-plus", "model-a")]
)
def test_model_scope_is_an_identity_not_a_substring(
    tmp_path: Path, board: Board, reported: str, wanted: str
) -> None:
    board.agents = [item("codex", "exhausted", scope_model=reported)]
    report = diagnose_file(_plan(tmp_path, ("codex", wanted)), check_quotas=True)
    assert report.ok is True
    assert report.quota_findings[0]["state"] == "unknown"


@pytest.mark.parametrize(
    "reported,wanted,blocked",
    [
        ("model-a", "model-b", False),
        ("model-a", "model-a", True),
        (None, "model-b", True),
    ],
)
def test_shared_pool_preserves_reporter_model_scope(
    tmp_path: Path, board: Board, reported: str | None, wanted: str, blocked: bool
) -> None:
    board.agents = [
        item(
            "copilot", "exhausted", pool="shared", account="team", scope_model=reported
        ),
        item("cursor", "unknown", pool="shared", account="team"),
    ]
    report = diagnose_file(_plan(tmp_path, ("cursor", wanted)), check_quotas=True)
    assert report.ok is not blocked
    assert report.quota_findings[0]["state"] == ("exhausted" if blocked else "unknown")


@pytest.mark.parametrize(
    "reporter_account,target_account,blocked",
    [
        ("team", None, False),
        (None, "team", False),
        ("other", "team", False),
        ("team", "team", True),
    ],
)
def test_shared_pool_requires_a_confirmed_same_account(
    tmp_path: Path,
    board: Board,
    reporter_account: str | None,
    target_account: str | None,
    blocked: bool,
) -> None:
    board.agents = [
        item("copilot", "exhausted", pool="shared", account=reporter_account),
        item("cursor", "unknown", pool="shared", account=target_account),
    ]
    report = diagnose_file(_plan(tmp_path, ("cursor", "model-a")), check_quotas=True)
    assert report.ok is not blocked


def test_shared_pool_retains_every_model_scoped_observation(
    tmp_path: Path, board: Board
) -> None:
    board.agents = [
        item(
            "copilot", "exhausted", pool="shared", account="team", scope_model="model-a"
        ),
        item(
            "codex", "exhausted", pool="shared", account="team", scope_model="model-b"
        ),
        item("cursor", "unknown", pool="shared", account="team"),
    ]
    report = diagnose_file(_plan(tmp_path, ("cursor", "model-b")), check_quotas=True)
    assert report.ok is False
    assert "codex" in report.errors[0].message


@pytest.mark.parametrize("age", [float("inf"), float("nan"), -1, True, "invalid", None])
@pytest.mark.parametrize("shared", [False, True])
def test_unusable_observation_age_warns_without_blocking_or_crashing(
    tmp_path: Path, board: Board, age: Any, shared: bool
) -> None:
    board.agents = [
        item(
            "copilot" if shared else "cursor",
            "exhausted",
            pool="shared",
            account="team",
            age_s=age,
        )
    ]
    if shared:
        board.agents.append(item("cursor", "unknown", pool="shared", account="team"))
    report = diagnose_file(_plan(tmp_path, ("cursor", "model-a")), check_quotas=True)
    assert report.ok is True
    assert report.quota_findings[0]["state"] != "exhausted"
    assert _quota_messages(report)


@pytest.mark.parametrize("shared", [False, True])
def test_stale_flag_cannot_carry_current_exhaustion(
    tmp_path: Path, board: Board, shared: bool
) -> None:
    board.agents = [
        item(
            "copilot" if shared else "cursor",
            "exhausted",
            pool="shared",
            account="team",
            stale=True,
        )
    ]
    if shared:
        board.agents.append(item("cursor", "unknown", pool="shared", account="team"))
    report = diagnose_file(_plan(tmp_path, ("cursor", "model-a")), check_quotas=True)
    assert report.ok is True
    assert _quota_messages(report)


def test_unknown_effective_model_cannot_match_a_model_scoped_card(
    tmp_path: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "vibecrafted_core.agent_stream.resolve_default_model", lambda _agent: ""
    )
    board.agents = [item("cursor", "exhausted", scope_model="model-a")]
    report = diagnose_file(_plan(tmp_path, ("cursor", "")), check_quotas=True)
    assert report.ok is True
    assert report.quota_findings[0]["state"] == "unknown"


def test_shared_scoped_exhaustion_refuses_the_whole_batch_before_reservation(
    tmp_path: Path, board: Board, monkeypatch: pytest.MonkeyPatch
) -> None:
    board.agents = [
        item("claude", "ok"),
        item(
            "copilot", "exhausted", pool="shared", account="team", scope_model="model-a"
        ),
        item("cursor", "unknown", pool="shared", account="team"),
    ]
    plan = _plan(tmp_path, ("claude", ""), ("cursor", "model-a"))

    def forbidden(*_a: object, **_k: object) -> None:
        pytest.fail("shared exhaustion must refuse before reservation or first launch")

    monkeypatch.setattr(dispatch_cli, "reserve_run_id", forbidden)
    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--json"]) == 1


def _one_cut_dispatch() -> Any:
    """``requirements`` reads only ``cuts[*].{id,agent,model}``."""
    from types import SimpleNamespace

    return SimpleNamespace(cuts=[SimpleNamespace(id="c1", agent="codex", model="")])


def test_truncated_http_body_is_a_visible_warning_not_an_exception() -> None:
    """``IncompleteRead`` is an ``HTTPException``, not an ``OSError``."""
    import http.client

    def torn(_url: str, _timeout: float) -> Any:
        raise http.client.IncompleteRead(b"{", 10)

    diagnosis = quota_preflight.diagnose(
        _one_cut_dispatch(), server_url="http://127.0.0.1:1", fetch=torn
    )
    assert not diagnosis.errors
    assert any("quota board unavailable" in m for _p, m in diagnosis.warnings)
    assert diagnosis.findings[0].state == "absent"


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("state", ["ok", "exhausted"])
def test_same_agent_cards_preserve_required_model_in_both_orders(
    tmp_path, board, monkeypatch, capsys, reverse, state
) -> None:
    board.agents = [
        item("codex", state, scope_model="needed", pool="needed", account="team"),
        item("codex", "exhausted", scope_model="other", pool="other", account="team"),
    ][:: -1 if reverse else 1]
    plan = _plan(tmp_path, ("claude", "claude-needed"), ("codex", "needed"))
    board.agents.append(item("claude", "ok"))
    code = doctor_main([str(plan), "--check-quotas", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == (1 if state == "exhausted" else 0)
    finding = next(f for f in result["quota"] if f["agent"] == "codex")
    assert (finding["state"], finding["pool"], finding["account"]) == (
        state,
        "needed",
        "team",
    )
    if state == "exhausted":

        def forbidden(*args, **kwargs):
            pytest.fail("required exhaustion must refuse before reservation/launch")

        monkeypatch.setattr(dispatch_cli, "reserve_run_id", forbidden)
        monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
        assert dispatch_cli.main([str(plan), "--json"]) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_and_malformed_required_cards_remain_visible(
    tmp_path, board, reverse
) -> None:
    board.agents = [
        item("codex", "exhausted", scope_model="needed", account="a"),
        item("codex", "ok", scope_model="needed", account="a"),
        item("codex", "exhausted", scope_model="needed", account="b", age_s=-1),
        item("codex", "stale", scope_model="needed", account="c"),
    ][:: -1 if reverse else 1]
    report = diagnose_file(_plan(tmp_path, ("codex", "needed")), check_quotas=True)
    assert not report.ok
    assert {f["state"] for f in report.quota_findings} >= {
        "exhausted",
        "ok",
        "malformed",
        "stale",
    }
    assert any("conflict" in m for m in _quota_messages(report))


def test_ambient_claude_model_cannot_hide_codex_config_exhaustion(
    tmp_path, board, monkeypatch
) -> None:
    codex = tmp_path / "codex"
    codex.mkdir()
    (codex / "config.toml").write_text('model = "gpt-required-model"\n')
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setenv("CLAUDE_MODEL", "claude-unrelated-model")
    monkeypatch.delenv("VIBECRAFTED_PARENT_MODEL", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    board.agents = [item("codex", "exhausted", scope_model="gpt-required-model")]
    plan = _plan(tmp_path, ("codex", ""))
    report = diagnose_file(plan, check_quotas=True)
    assert not report.ok
    assert report.quota_findings[0]["model"] == "gpt-required-model"

    def forbidden(*args, **kwargs):
        pytest.fail("ambient contamination must not admit the batch")

    monkeypatch.setattr(dispatch_cli, "reserve_run_id", forbidden)
    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--json"]) == 1


def test_parallel_process_receipt_publication_never_overwrites(tmp_path) -> None:
    path = tmp_path / "receipt.json"
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from vibecrafted_core.dispatch.quota import publish_receipt
print("ready", flush=True)
sys.stdin.readline()
try:
    publish_receipt(Path(sys.argv[2]), {"decision": sys.argv[3]})
except OSError:
    print("refused", flush=True)
else:
    print("published", flush=True)
"""
    core = str(Path(quota_preflight.__file__).resolve().parents[2])
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, core, str(path), decision],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for decision in ("first", "second")
    ]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == "ready"
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        outcomes = [process.communicate(timeout=15) for process in processes]
        assert all(process.returncode == 0 for process in processes), outcomes
        assert sorted(out.strip() for out, err in outcomes) == ["published", "refused"]
        assert json.loads(path.read_text())["decision"] in {"first", "second"}
        assert list(tmp_path.iterdir()) == [path]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()


@pytest.mark.parametrize(
    "kind", ["legacy", "symlink", "temporary-symlink", "parent-symlink"]
)
def test_receipt_publication_preserves_preexisting_paths(tmp_path, kind) -> None:
    target = tmp_path / "foreign"
    target.write_text("preserve me")
    path = tmp_path / "receipt.json"
    if kind == "legacy":
        path.write_text("legacy")
    elif kind == "symlink":
        path.symlink_to(target)
    elif kind == "temporary-symlink":
        path.with_name(path.name + ".tmp").symlink_to(target)
    else:
        directory = tmp_path / "foreign-directory"
        directory.mkdir()
        parent = tmp_path / "linked-parent"
        parent.symlink_to(directory, target_is_directory=True)
        path = parent / "receipt.json"
    if kind == "temporary-symlink":
        quota_preflight.publish_receipt(path, {"decision": "new"})
        assert path.with_name(path.name + ".tmp").is_symlink()
    else:
        with pytest.raises(OSError):
            quota_preflight.publish_receipt(path, {"decision": "new"})
    assert target.read_text() == "preserve me"
    if kind == "legacy":
        assert path.read_text() == "legacy"
    if kind == "parent-symlink":
        assert not (directory / "receipt.json").exists()


def test_foreign_model_card_cannot_assign_a_required_shared_pool(
    tmp_path, board
) -> None:
    board.agents = [
        item("cursor", "ok", pool="other", account="team", scope_model="foreign"),
        item(
            "copilot", "exhausted", pool="other", account="team", scope_model="needed"
        ),
    ]
    report = diagnose_file(_plan(tmp_path, ("cursor", "needed")), check_quotas=True)
    assert report.ok
    assert report.quota_findings[0]["state"] == "unknown"


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_pool_retains_duplicate_reporter_scopes(
    tmp_path, board, reverse
) -> None:
    board.agents = [
        item(
            "copilot", "exhausted", pool="shared", account="team", scope_model="needed"
        ),
        item("copilot", "ok", pool="foreign", account="other", scope_model="other"),
    ][:: -1 if reverse else 1]
    board.agents.append(item("cursor", "unknown", pool="shared", account="team"))
    report = diagnose_file(_plan(tmp_path, ("cursor", "needed")), check_quotas=True)
    assert not report.ok
    assert report.quota_findings[0]["state"] == "exhausted"
    assert report.quota_findings[0]["account"] == "team"


def test_cli_retries_atomic_collision_and_preserves_the_legacy_decision(
    tmp_path, board, monkeypatch
) -> None:
    board.agents = [item("codex", "exhausted")]
    plan = _plan(tmp_path, ("codex", "needed"))
    artifacts = tmp_path / "override"
    artifacts.mkdir()
    legacy = artifacts / "quota-override-receipt.json"
    legacy.write_text('{"decision": "legacy"}')
    monkeypatch.setattr(dispatch_cli, "_artifacts_dir", lambda *_a, **_kw: artifacts)

    def launch(*args, **kwargs):
        assert json.loads(legacy.read_text()) == {"decision": "legacy"}
        receipt = json.loads((artifacts / "quota-override-receipt-1.json").read_text())
        assert receipt["bypassed"][0]["model"] == "needed"
        raise RuntimeError("stop after durable receipt check")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", launch)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1


@pytest.mark.parametrize(
    "stage", ["file-fsync", "directory-fsync", "link", "collision-limit"]
)
def test_durability_failures_and_collision_limit_refuse_launch(
    tmp_path, board, monkeypatch, stage, capsys
) -> None:
    import os
    import stat

    board.agents = [item("codex", "exhausted")]
    plan = _plan(tmp_path, ("codex", "needed"))
    artifacts = tmp_path / "override"
    artifacts.mkdir()
    monkeypatch.setattr(dispatch_cli, "_artifacts_dir", lambda *_a, **_kw: artifacts)
    fsync = os.fsync

    def fail_sync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory == (stage == "directory-fsync"):
            raise OSError("injected fsync failure")
        fsync(fd)

    def fail_link(*args, **kwargs):
        raise OSError("injected link failure")

    def forbidden(*args, **kwargs):
        pytest.fail("failed durable publication must refuse launch")

    if "fsync" in stage:
        monkeypatch.setattr(os, "fsync", fail_sync)
    elif stage == "link":
        monkeypatch.setattr(os, "link", fail_link)
    else:
        for attempt in range(32):
            name = (
                "quota-override-receipt.json"
                if attempt == 0
                else f"quota-override-receipt-{attempt}.json"
            )
            (artifacts / name).write_text("legacy")
    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "could not be published" in capsys.readouterr().err
    if stage == "collision-limit":
        assert all(
            path.read_text() == "legacy" for path in artifacts.glob("quota-override*")
        )


@pytest.mark.parametrize("selection", ["cut", "frontmatter", "config"])
def test_quota_uses_canonical_selection_before_provider_environment(
    tmp_path, board, monkeypatch, selection
) -> None:
    directory = tmp_path / "config" / "vibecrafted"
    directory.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(directory.parent))
    (directory / "config.toml").write_text('[agents.codex]\nmodel = "configured"\n')
    monkeypatch.setenv("CODEX_MODEL", "ambient-codex")
    monkeypatch.setenv("CLAUDE_MODEL", "ambient-claude")
    model = {"cut": "pinned", "frontmatter": "brief", "config": "configured"}[selection]
    plan = _plan(tmp_path, ("codex", "pinned" if selection == "cut" else ""))
    if selection != "config":
        plan.write_text(
            plan.read_text().replace(
                'prompt = "hello"', 'prompt = """---\nmodel: brief\n---\nhello"""'
            )
        )
    board.agents = [item("codex", "exhausted", scope_model=model)]
    report = diagnose_file(plan, check_quotas=True)
    assert not report.ok
    assert report.dispatch.cuts[0].model == model
    assert report.quota_findings[0]["model"] == model


@pytest.mark.parametrize(
    "scope", ["invalid", {"pool": []}, {"account": True}, {"model": 42}]
)
def test_malformed_scope_cannot_prove_required_exhaustion(
    tmp_path, board, scope
) -> None:
    board.agents = [item("codex", "exhausted", scope=scope)]
    report = diagnose_file(_plan(tmp_path, ("codex", "needed")), check_quotas=True)
    assert report.ok
    assert report.quota_findings[0]["state"] == "malformed"


@pytest.mark.parametrize("field", ["account", "pool"])
def test_malformed_top_level_identity_cannot_confirm_shared_exhaustion(
    tmp_path, board, field
) -> None:
    reporter = item("copilot", "exhausted", pool="shared", account="team")
    reporter["scope"][field] = None
    reporter[field] = True
    target = item("cursor", "unknown", pool="shared", account="team")
    target["scope"][field] = "True"
    board.agents = [reporter, target]
    report = diagnose_file(_plan(tmp_path, ("cursor", "needed")), check_quotas=True)
    assert report.ok


@pytest.mark.parametrize("swap_target", ["directory", "receipt"])
def test_publication_directory_swap_refuses_cli_launch(
    tmp_path, board, monkeypatch, capsys, swap_target
) -> None:
    import os

    board.agents = [item("codex", "exhausted")]
    plan = _plan(tmp_path, ("codex", "needed"))
    directory = tmp_path / "override"
    directory.mkdir()
    moved = tmp_path / "moved"
    link = os.link

    def swap(*args, **kwargs):
        link(*args, **kwargs)
        if swap_target == "directory":
            directory.rename(moved)
            directory.mkdir()
        else:
            moved.mkdir()
            receipt = directory / "quota-override-receipt.json"
            receipt.rename(moved / receipt.name)
            receipt.write_text((moved / receipt.name).read_text())
        (directory / "foreign").write_text("preserve replacement")

    def forbidden(*args, **kwargs):
        pytest.fail("receipt absent at the launch-visible path must refuse launch")

    monkeypatch.setattr(dispatch_cli, "_artifacts_dir", lambda *_a, **_kw: directory)
    monkeypatch.setattr(os, "link", swap)
    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "could not be published" in capsys.readouterr().err
    if swap_target == "directory":
        assert not (directory / "quota-override-receipt.json").exists()
    assert (
        json.loads((moved / "quota-override-receipt.json").read_text())["bypassed"][0][
            "model"
        ]
        == "needed"
    )
    assert (directory / "foreign").read_text() == "preserve replacement"


def test_unsupported_publication_platform_refuses_launch(
    tmp_path, board, monkeypatch, capsys
) -> None:
    import os

    board.agents = [item("codex", "exhausted")]
    plan = _plan(tmp_path, ("codex", "needed"))
    monkeypatch.setattr(os, "supports_dir_fd", set())

    def forbidden(*args, **kwargs):
        pytest.fail("unsupported durable publication must refuse launch")

    monkeypatch.setattr(dispatch_cli, "run_dispatch", forbidden)
    assert dispatch_cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "requires no-follow directory descriptors" in capsys.readouterr().err
