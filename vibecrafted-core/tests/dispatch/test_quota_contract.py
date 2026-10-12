"""Counterexamples at the shared server / public admission boundary."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from vibecrafted_core.dispatch import cli
from vibecrafted_core.dispatch.doctor import diagnose_quotas, diagnose_text
from vibecrafted_core.dispatch.doctor import main as doctor_main

PLAN = """schema = "vibecrafted.dispatch.v1"
[meta]
repo = "/tmp/repo"
[[cuts]]
id = "one"
agent = "codex"
workflow = "implement"
prompt = "work"
[[cuts.verify]]
run = "true"
expect = { exit_code = 0 }
"""


def dispatch():
    result = diagnose_text(PLAN)
    assert result.dispatch is not None
    return result.dispatch


@contextmanager
def server(agents):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"schema": "vibecrafted.quota-dashboard.v1", "agents": agents}
                ).encode()
            )

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}"
    finally:
        http.shutdown()
        thread.join()
        http.server_close()


def observation(**changes):
    return {
        "id": "codex",
        "status": "blocked",
        "observed_at": datetime.now(UTC).isoformat(),
        "source": "native rate-limit event",
        "pool": "openai",
        "account": "default",
        "model": None,
        "stale": False,
        "age_s": 0,
        "authoritative": True,
        "applies": True,
        **changes,
    }


def test_absent_observation_is_explicit_unknown(tmp_path):
    errors, warnings, _ = diagnose_quotas(
        dispatch(), home=tmp_path, server_url="http://127.0.0.1:1"
    )
    assert not errors
    assert warnings and "unknown" in warnings[0].message


@pytest.mark.parametrize("fraction", ["invalid", [], True, 10**400])
def test_malformed_observation_never_raises_or_admits_as_known(tmp_path, fraction):
    with server([observation(fraction=fraction)]) as url:
        errors, warnings, _ = diagnose_quotas(dispatch(), server_url=url, home=tmp_path)
    assert not errors
    assert any("malformed" in warning.message for warning in warnings)


def test_actual_server_id_wire_blocks_public_doctor_and_zero_batch_starts(
    tmp_path, monkeypatch, capsys
):
    plan = tmp_path / "plan.toml"
    plan.write_text(PLAN)
    monkeypatch.setattr(
        cli, "run_dispatch", lambda *a, **k: pytest.fail("batch started")
    )
    with server([observation()]) as url:
        monkeypatch.setenv("VIBECRAFTED_SERVER_URL", url)
        assert doctor_main([str(plan), "--json", "--check-quotas"]) == 1
        assert cli.main([str(plan), "--json"]) == 1
    assert "confirmed exhausted" in capsys.readouterr().out


def test_effective_model_and_mismatched_scope_remain_unknown(tmp_path):
    plan = dispatch()
    plan = replace(plan, cuts=(replace(plan.cuts[0], model="requested-model"),))
    with server([observation(model="other-model", applies=False)]) as url:
        errors, warnings, _ = diagnose_quotas(plan, server_url=url, home=tmp_path)
    assert not errors
    assert warnings and "unknown" in warnings[0].message


@pytest.mark.parametrize(
    "changes,word",
    [
        ({"status": "warn", "fraction": 0.95}, "high"),
        ({"stale": True, "status": "blocked"}, "stale"),
        ({"observed_at": None}, "time"),
        ({"authoritative": False}, "non-authoritative"),
        ({"account": "other", "applies": False}, "effective"),
        ({"status": "absent"}, "unknown"),
    ],
)
def test_uncertain_low_or_unneeded_scope_never_blocks(tmp_path, changes, word):
    with server([observation(**changes)]) as url:
        errors, warnings, _ = diagnose_quotas(dispatch(), server_url=url, home=tmp_path)
    assert not errors and any(word in w.message for w in warnings)


def test_override_publication_failure_is_zero_start(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "plan.toml"
    plan.write_text(PLAN)
    monkeypatch.setattr(cli, "_with_runtime_baseline", lambda d: d)
    monkeypatch.setattr(cli, "_verify_baseline", lambda report, **kwargs: report)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(cli, "_artifacts_dir", lambda *a, **k: artifacts)
    monkeypatch.setattr(
        cli, "run_dispatch", lambda *a, **k: pytest.fail("partial start")
    )

    def fail(*a, **k):
        raise OSError("receipt disk failure")

    monkeypatch.setattr(cli, "atomic_write_json", fail)
    with server([observation()]) as url:
        monkeypatch.setenv("VIBECRAFTED_SERVER_URL", url)
        assert cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "receipt publication failed" in capsys.readouterr().out


def test_override_is_persisted_with_exact_observation_before_launch(
    tmp_path, monkeypatch, capsys
):
    from types import SimpleNamespace

    plan = tmp_path / "plan.toml"
    plan.write_text(PLAN)
    monkeypatch.setattr(cli, "_with_runtime_baseline", lambda d: d)
    monkeypatch.setattr(cli, "_verify_baseline", lambda report, **kwargs: report)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(cli, "_artifacts_dir", lambda *a, **k: artifacts)
    seen = []
    observed = observation(pool="shared-paid-pool", account="default")

    def launch(*a, **k):
        receipt = json.loads((artifacts / "quota-override-receipt.json").read_text())
        assert receipt["observations"][0]["observation"] == observed
        assert receipt["selections"] == [
            {"agent": "codex", "model": "", "cuts": ["one"]}
        ]
        assert receipt["source_sha256"] and receipt["run_id"] == k["run_id"]
        seen.append(receipt)
        return SimpleNamespace(
            states={"one": "[x]"},
            artifacts={},
            baton=SimpleNamespace(verified=1, total=1),
        )

    monkeypatch.setattr(cli, "run_dispatch", launch)
    with server([observed]) as url:
        monkeypatch.setenv("VIBECRAFTED_SERVER_URL", url)
        assert cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 0
    assert len(seen) == 1


def test_frontmatter_plan_override_and_config_use_canonical_model_selection(
    tmp_path, monkeypatch
):
    from vibecrafted_core.dispatch.schema import parse_dispatch

    requests = []

    def observations(url, agent, model):
        requests.append((agent, model))
        return [observation(model=model)]

    monkeypatch.setattr(
        "vibecrafted_core.dispatch.doctor._quota_observations", observations
    )
    config = tmp_path / "config/vibecrafted/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('[agents.codex]\nmodel="configured-model"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for source, model in [
        (PLAN, "configured-model"),
        (
            PLAN.replace(
                'prompt = "work"',
                'prompt = """---\nagent: codex\nmodel: frontmatter-model\n---\nwork"""',
            ),
            "frontmatter-model",
        ),
        (
            PLAN.replace('prompt = "work"', 'prompt = "work"\nmodel = "plan-model"'),
            "plan-model",
        ),
    ]:
        plan = parse_dispatch(source)
        errors, _, _ = diagnose_quotas(plan, server_url="http://unused")
        assert errors and requests[-1] == ("codex", model)


def test_override_keeps_unrelated_schema_refusal(tmp_path, monkeypatch):
    plan = tmp_path / "invalid.toml"
    plan.write_text(PLAN.replace('repo = "/tmp/repo"', ""))
    monkeypatch.setattr(
        cli, "run_dispatch", lambda *a, **k: pytest.fail("invalid batch started")
    )
    assert cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1


@pytest.mark.parametrize(
    "changes",
    [{"status": []}, {"pool": {}}, {"source": []}, {"model": []}, {"account": {}}],
)
def test_malformed_scope_and_status_are_unknown_not_exceptions(tmp_path, changes):
    with server([observation(**changes)]) as url:
        errors, warnings, _ = diagnose_quotas(dispatch(), server_url=url, home=tmp_path)
    assert not errors and any("malformed" in w.message for w in warnings)


def test_second_required_pool_exhaustion_refuses_entire_new_batch(
    tmp_path, monkeypatch, capsys
):
    plan = tmp_path / "batch.toml"
    plan.write_text(
        PLAN
        + """\n[[cuts]]\nid="two"\nagent="kimi"\nworkflow="implement"\nprompt="second required task"\n[[cuts.verify]]\nrun="true"\nexpect={exit_code=0}\n"""
    )
    monkeypatch.setattr(
        cli, "run_dispatch", lambda *a, **k: pytest.fail("partial batch start")
    )
    with server(
        [observation(status="ok"), observation(id="kimi", pool="moonshot")]
    ) as url:
        monkeypatch.setenv("VIBECRAFTED_SERVER_URL", url)
        assert cli.main([str(plan), "--json"]) == 1
    assert "quota confirmed exhausted" in capsys.readouterr().out


def test_override_cannot_launch_after_source_drift(tmp_path, monkeypatch, capsys):
    plan = tmp_path / "changed.toml"
    plan.write_text(PLAN)
    monkeypatch.setattr(cli, "_with_runtime_baseline", lambda d: d)

    def verify(report, **kwargs):
        plan.write_text(PLAN.replace('agent = "codex"', 'agent = "kimi"'))
        return report

    monkeypatch.setattr(cli, "_verify_baseline", verify)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(cli, "_artifacts_dir", lambda *a, **k: artifacts)
    monkeypatch.setattr(
        cli, "run_dispatch", lambda *a, **k: pytest.fail("changed batch started")
    )
    with server([observation()]) as url:
        monkeypatch.setenv("VIBECRAFTED_SERVER_URL", url)
        assert cli.main([str(plan), "--ignore-usage-quota-warnings"]) == 1
    assert "source changed" in capsys.readouterr().out


def test_effective_external_copilot_profile_does_not_inherit_github_pool(
    tmp_path, monkeypatch
):
    config = tmp_path / "config/vibecrafted/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[agents.copilot.provider]\nbase_url="http://127.0.0.1:7777"\nmodel="external-model"\n'
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    from vibecrafted_core.dispatch.schema import parse_dispatch

    selected = parse_dispatch(PLAN.replace('agent = "codex"', 'agent = "copilot"'))
    requests = []

    def readings(url, agent, model):
        requests.append((agent, model))
        return [observation(id="copilot", pool="github")]

    monkeypatch.setattr(
        "vibecrafted_core.dispatch.doctor._quota_observations", readings
    )
    errors, warnings, receipt = diagnose_quotas(
        selected, server_url="http://unused", ignore_warnings=True
    )
    assert not errors and any("pool is unknown" in w.message for w in warnings)
    assert requests == [("copilot", "external-model")]
    assert receipt["selections"][0]["model"] == "external-model"
    assert receipt["overrides"] == []
