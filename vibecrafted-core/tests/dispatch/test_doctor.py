from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.doctor import diagnose_file, diagnose_runtime, main
from vibecrafted_core.dispatch.schema import parse_dispatch
from vibecrafted_core.repository_claims import RepositoryClaimRegistry

FIXTURES = Path(__file__).parent / "fixtures"
INVALID = FIXTURES / "invalid"


@pytest.mark.parametrize(
    ("fixture", "path", "message"),
    [
        ("unknown-schema.dispatch.toml", "schema", "unsupported schema"),
        ("missing-meta-repo.dispatch.toml", "meta.repo", "required"),
        ("unreadable-brief.dispatch.toml", "cuts[0].brief", "missing or unreadable"),
        ("missing-prompt-and-brief.dispatch.toml", "cuts[0]", "prompt or brief"),
        ("missing-verify.dispatch.toml", "cuts[0].verify", "required unless"),
        ("unknown-workflow.dispatch.toml", "cuts[0].workflow", "unsupported workflow"),
        ("unknown-phase.dispatch.toml", "cuts[0].phase", "unknown phase"),
        ("duplicate-id.dispatch.toml", "cuts[1].id", "duplicate cut id"),
        (
            "concurrency-without-policy.dispatch.toml",
            "policy.concurrency",
            "require allow_concurrency",
        ),
        (
            "unknown-matcher.dispatch.toml",
            "cuts[0].verify[0].expect.stderr",
            "unsupported matcher",
        ),
        ("hard-stop-push.dispatch.toml", "cuts[0].verify[0].run", "git push"),
        ("hard-stop-release.dispatch.toml", "cuts[0].verify[0].run", "release"),
        ("hard-stop-no-verify.dispatch.toml", "cuts[0].verify[0].run", "--no-verify"),
        (
            "read-without-mutation.dispatch.toml",
            "cuts[0].mutation",
            "required for READ",
        ),
    ],
)
def test_dispatch_doctor_rejects_design_refusal_fixtures(
    fixture: str, path: str, message: str
) -> None:
    report = diagnose_file(INVALID / fixture)

    assert report.ok is False
    assert any(
        error.path == path and message in error.message for error in report.errors
    ), report.errors


def test_dispatch_doctor_allows_observational_read_with_mutation_policy() -> None:
    report = diagnose_file(FIXTURES / "observational-read.dispatch.toml")

    assert report.ok is True
    assert report.errors == ()


def test_dispatch_doctor_allows_release_in_branch_name_probe(tmp_path: Path) -> None:
    dispatch = tmp_path / "release-branch-probe.dispatch.toml"
    dispatch.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/vibecrafted-dispatch-fixture"
[[cuts]]
id = "branch-probe"
agent = "codex"
workflow = "implement"
prompt = "check branch"
  [[cuts.verify]]
  run = "git branch --show-current | grep release-"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(dispatch)

    assert report.ok is True, report.errors


def test_dispatch_doctor_warns_model_pin_is_not_provider_validated() -> None:
    report = diagnose_file(FIXTURES / "model-pin.dispatch.toml")

    assert report.ok is True
    assert report.errors == ()
    assert len(report.warnings) == 1
    assert report.warnings[0].path == "cuts[0].model"
    assert (
        "provider/account availability is not validated" in report.warnings[0].message
    )


def test_dispatch_doctor_json_includes_model_pin_warning_and_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(FIXTURES / "model-pin.dispatch.toml"), "--json"])

    payload = __import__("json").loads(capsys.readouterr().out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["warnings"][0]["path"] == "cuts[0].model"


def test_dispatch_doctor_rejects_legacy_stage0_review_no_edit_read() -> None:
    report = diagnose_file(FIXTURES / "legacy-stage0-read-no-edit.dispatch.toml")

    assert report.ok is False
    assert any(
        error.path == "cuts[0].mutation" and "required for READ" in error.message
        for error in report.errors
    ), report.errors


def test_dispatch_doctor_cli_returns_nonzero_for_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(INVALID / "unknown-schema.dispatch.toml")])

    captured = capsys.readouterr()
    assert code == 1
    assert "schema: unsupported schema" in captured.out


def test_dispatch_doctor_cli_returns_zero_for_valid_fixture(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main([str(FIXTURES / "minimal.dispatch.toml")])

    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "dispatch-doctor: ok"


def test_dispatch_doctor_names_stale_claim_owner_and_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    RepositoryClaimRegistry(emit_events=False).acquire(
        repo=repo,
        owned_paths=("src",),
        run_id="stale-run",
        session_id="stale-session",
        agent="codex",
        pid=999_999_999,
    )
    dispatch = parse_dispatch(
        f'''schema = "vibecrafted.dispatch.v1"
[meta]
repo = "{repo}"
[[cuts]]
id = "doctor"
agent = "codex"
workflow = "implement"
prompt = "doctor"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ contains = "ok" }}
'''
    )

    errors = diagnose_runtime(dispatch)

    stale = next(error for error in errors if "stale mutation claim" in error.message)
    assert "stale-run/stale-session" in stale.message
    assert "paths src" in stale.message
    assert "is dead" in stale.message


def test_quota_preflight_blocks_when_needed_pool_exhausted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "status": "blocked",
                "limits": {"five_hour": {"fraction": 1.0}},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "kimi"
workflow = "implement"
prompt = "run kimi cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is False
    assert any(
        "quota confirmed exhausted for agent 'kimi'" in err.message
        for err in report.errors
    )
    assert any(err.path == "quota.moonshot" for err in report.errors)


def test_quota_preflight_unneeded_pool_exhaustion_does_not_block(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    # Kimi is exhausted, but plan only uses claude
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "status": "blocked",
                "limits": {"five_hour": {"fraction": 1.0}},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )
    claude_telemetry = home / "telemetry" / "claude"
    claude_telemetry.mkdir(parents=True)
    (claude_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "claude",
                "status": "ok",
                "quota": {"fraction": 0.4, "status": "ok"},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "claude"
workflow = "implement"
prompt = "run claude cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is True
    assert not report.errors


def test_quota_preflight_low_95_percent_is_warning_not_blocked(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "limits": {"five_hour": {"fraction": 0.95}},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "kimi"
workflow = "implement"
prompt = "run kimi cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is True
    assert not report.errors
    assert any("quota utilization high" in w.message for w in report.warnings)


def test_quota_preflight_stale_exhausted_is_warning_not_blocked(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    agy_telemetry = home / "telemetry" / "agy"
    agy_telemetry.mkdir(parents=True)
    # Stale: updated 400s ago
    (agy_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "agy",
                "fraction": 1.0,
                "status": "RESOURCE_EXHAUSTED",
                "updated_at": time.time() - 400,
            }
        ),
        encoding="utf-8",
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "agy"
workflow = "implement"
prompt = "run agy cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is True
    assert not report.errors
    assert any("is stale" in w.message for w in report.warnings)


def test_quota_preflight_absent_and_malformed_sources_warn(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    codex_telemetry = home / "telemetry" / "codex"
    codex_telemetry.mkdir(parents=True)
    (codex_telemetry / "quota.json").write_text("NOT_VALID_JSON{{{", encoding="utf-8")

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "codex"
workflow = "implement"
prompt = "run codex cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
[[cuts]]
id = "c2"
agent = "grok"
workflow = "implement"
prompt = "run grok cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is True
    assert not report.errors
    assert any("malformed" in w.message for w in report.warnings)


def test_quota_preflight_override_bypasses_exhaustion_with_receipt(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "status": "blocked",
                "limits": {"five_hour": {"fraction": 1.0}},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "kimi"
workflow = "implement"
prompt = "run kimi cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(
        plan,
        check_quotas=True,
        ignore_usage_quota_warnings=True,
        home=home,
    )
    assert report.ok is True
    assert not report.errors
    assert report.quota_override_receipt is not None
    assert report.quota_override_receipt["flag"] == "--ignore-usage-quota-warnings"
    assert len(report.quota_override_receipt["overrides"]) == 1
    assert report.quota_override_receipt["overrides"][0]["agent"] == "kimi"
    assert report.quota_override_receipt["overrides"][0]["pool"] == "moonshot"


def test_quota_preflight_override_does_not_bypass_unrelated_admission(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "status": "blocked",
                "limits": {"five_hour": {"fraction": 1.0}},
                "updated_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    # Invalid plan: missing repo
    plan = tmp_path / "invalid.dispatch.toml"
    plan.write_text(
        """schema = "vibecrafted.dispatch.v1"
[meta]
[[cuts]]
id = "c1"
agent = "kimi"
workflow = "implement"
prompt = "run kimi cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = { exit_code = 0 }
""",
        encoding="utf-8",
    )

    report = diagnose_file(
        plan,
        check_quotas=True,
        ignore_usage_quota_warnings=True,
        home=home,
    )
    assert report.ok is False
    assert any("meta.repo" in err.path for err in report.errors)


def _quota_plan(tmp_path: Path, agent: str, model_line: str = "") -> Path:
    plan = tmp_path / f"plan-{agent}.dispatch.toml"
    plan.write_text(
        f"""schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "c1"
agent = "{agent}"
workflow = "implement"
{model_line}prompt = "run {agent} cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ exit_code = 0 }}
""",
        encoding="utf-8",
    )
    return plan


def _serve_quota_payload(payload: dict) -> tuple[str, object]:
    """Serve one quota-dashboard payload over loopback HTTP; returns (url, server)."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def _quota_card(agent: str, status: str, **extra: object) -> dict:
    card: dict = {
        "id": agent,
        "name": agent.title(),
        "present": True,
        "status": status,
        "headline": status,
        "detail": "",
        "stale": False,
        "age_s": 3,
        "model": None,
        "account": None,
        "pool": None,
        "source": "vc-server",
        "bars": [],
    }
    card.update(extra)
    return card


def test_quota_preflight_missing_observation_warns_unknown(tmp_path: Path) -> None:
    """Parent counterexample: a needed pool without any observation must be a
    visible unknown warning, not 0 errors / 0 warnings."""
    home = tmp_path / "home"
    home.mkdir()
    plan = _quota_plan(tmp_path, "codex")

    report = diagnose_file(plan, check_quotas=True, home=home)

    assert report.ok is True
    assert not report.errors
    unknown = [w for w in report.warnings if "unknown" in w.message]
    assert unknown, report.warnings
    assert any(w.path == "quota.openai" for w in unknown)
    assert any("no local observation" in w.message for w in unknown)


def test_quota_preflight_malformed_fraction_warns_unknown_without_raising(
    tmp_path: Path,
) -> None:
    """Parent counterexample: fraction='invalid' must degrade to an explicit
    unknown/malformed warning, never an uncaught ValueError."""
    home = tmp_path / "home"
    codex = home / "telemetry" / "codex"
    codex.mkdir(parents=True)
    (codex / "quota.json").write_text(
        json.dumps(
            {"agent": "codex", "fraction": "invalid", "updated_at": time.time()}
        ),
        encoding="utf-8",
    )
    plan = _quota_plan(tmp_path, "codex")

    report = diagnose_file(plan, check_quotas=True, home=home)

    assert report.ok is True
    assert any(
        "malformed" in w.message and "unknown" in w.message for w in report.warnings
    ), report.warnings


def test_quota_preflight_consumes_real_server_card_by_id(tmp_path: Path) -> None:
    """The server serializes cards with `id`; the doctor must not drop them."""
    home = tmp_path / "home"
    home.mkdir()
    payload = {
        "schema": "vibecrafted.quota-dashboard.v1",
        "agents": [_quota_card("codex", "blocked", pool="openai")],
    }
    url, server = _serve_quota_payload(payload)
    try:
        plan = _quota_plan(tmp_path, "codex")
        report = diagnose_file(plan, check_quotas=True, home=home, server_url=url)
    finally:
        server.shutdown()

    assert report.ok is False
    assert any(
        "quota confirmed exhausted for agent 'codex'" in error.message
        for error in report.errors
    )
    assert any(error.path == "quota.openai" for error in report.errors)


def test_quota_preflight_server_stale_blocked_is_unconfirmed_warning(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload = {
        "schema": "vibecrafted.quota-dashboard.v1",
        "agents": [
            _quota_card("codex", "blocked", pool="openai", stale=True, age_s=900)
        ],
    }
    url, server = _serve_quota_payload(payload)
    try:
        plan = _quota_plan(tmp_path, "codex")
        report = diagnose_file(plan, check_quotas=True, home=home, server_url=url)
    finally:
        server.shutdown()

    assert report.ok is True
    assert not report.errors
    assert any("stale" in w.message for w in report.warnings)


def test_quota_preflight_server_absent_card_is_visible_unknown(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload = {
        "schema": "vibecrafted.quota-dashboard.v1",
        "agents": [_quota_card("codex", "absent", pool="openai", present=False)],
    }
    url, server = _serve_quota_payload(payload)
    try:
        plan = _quota_plan(tmp_path, "codex")
        report = diagnose_file(plan, check_quotas=True, home=home, server_url=url)
    finally:
        server.shutdown()

    assert report.ok is True
    assert any(
        "unknown" in w.message and "absent" in w.message for w in report.warnings
    ), report.warnings


def test_quota_preflight_scopes_messages_to_effective_model(tmp_path: Path) -> None:
    """The gate reasons over the plan's effective model selection, not only the
    bare agent name: the scope must be visible in the refusal."""
    home = tmp_path / "home"
    home.mkdir()
    payload = {
        "schema": "vibecrafted.quota-dashboard.v1",
        "agents": [_quota_card("codex", "blocked", pool="openai")],
    }
    url, server = _serve_quota_payload(payload)
    try:
        plan = _quota_plan(tmp_path, "codex", model_line='model = "gpt-5.6-terra"\n')
        report = diagnose_file(plan, check_quotas=True, home=home, server_url=url)
    finally:
        server.shutdown()

    assert report.ok is False
    message = next(
        error.message
        for error in report.errors
        if "quota confirmed exhausted" in error.message
    )
    assert "gpt-5.6-terra" in message
    assert "via cli" in message  # the TOML cut pin resolves as a CLI-grade pin


def test_doctor_main_probes_configured_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """doctor.main must reach the central server path from operator config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    payload = {
        "schema": "vibecrafted.quota-dashboard.v1",
        "agents": [_quota_card("codex", "blocked", pool="openai")],
    }
    url, server = _serve_quota_payload(payload)
    try:
        config_dir = tmp_path / "xdg" / "vibecrafted"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text(
            f'[server]\npublic_url = "{url}"\n', encoding="utf-8"
        )
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        plan = _quota_plan(tmp_path, "codex")
        code = main([str(plan), "--check-quotas", "--json"])
    finally:
        server.shutdown()

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert any(
        "quota confirmed exhausted for agent 'codex'" in error["message"]
        for error in out["errors"]
    )
