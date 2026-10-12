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


def test_quota_preflight_malformed_fraction_does_not_crash(
    tmp_path: Path,
) -> None:
    """fraction='invalid' must be reported as a malformed warning, not raise."""
    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "limits": {"five_hour": {"fraction": "invalid"}},
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
    assert any(
        "malformed" in w.message or "invalid" in w.message.lower()
        for w in report.warnings
    ), report.warnings


def test_quota_preflight_missing_observation_warns_unknown(
    tmp_path: Path,
) -> None:
    """Needed codex with NO observation at all must be visible unknown."""
    home = tmp_path / "home"
    # Do not create any codex telemetry — no observation exists.

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
""",
        encoding="utf-8",
    )

    report = diagnose_file(plan, check_quotas=True, home=home)
    assert report.ok is True
    assert not report.errors
    assert any(
        "unmeasured" in w.message or "unknown" in w.message for w in report.warnings
    ), report.warnings


def test_quota_preflight_server_response_uses_id_key(
    tmp_path: Path,
) -> None:
    """Server /api/usage/quota serializes QuotaAgent.id; doctor must read that key.

    Drives the central vc-server path end-to-end through diagnose_file with a
    server_url that returns a stub payload, proving the response is no longer
    dropped because the doctor looked for the wrong key.
    """
    from vibecrafted_core.dispatch import doctor as doctor_module

    home = tmp_path / "home"
    kimi_telemetry = home / "telemetry" / "kimi"
    kimi_telemetry.mkdir(parents=True)
    (kimi_telemetry / "quota.json").write_text(
        json.dumps(
            {
                "agent": "kimi",
                "status": "ok",
                "limits": {"five_hour": {"fraction": 0.4}},
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

    server_payload = {
        "agents": [
            {
                "id": "kimi",  # server uses `id`, doctor used to look for `agent`
                "name": "Kimi",
                "present": True,
                "status": "blocked",
                "headline": "blocked",
                "detail": "no quota",
                "stale": False,
                "age_s": 1,
                "model": "moonshot-v1-128k",
                "account": "primary",
                "pool": "moonshot",
                "source": "live",
            }
        ]
    }

    captured: dict[str, str] = {}

    def fake_urlopen(req, timeout=1.0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["accept"] = req.headers.get("Accept")

        class _Resp:
            def __init__(self) -> None:
                self.status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self) -> bytes:
                return json.dumps(server_payload).encode("utf-8")

        return _Resp()

    original = doctor_module.urllib.request.urlopen
    doctor_module.urllib.request.urlopen = fake_urlopen
    try:
        report = diagnose_file(
            plan,
            check_quotas=True,
            server_url="http://127.0.0.1:8765",
            home=home,
        )
    finally:
        doctor_module.urllib.request.urlopen = original

    assert captured.get("url") == "http://127.0.0.1:8765/api/usage/quota"
    # Server payload must reach the diagnosis: blocked kimi is now seen as
    # exhausted (no override), not silently dropped as zero-observation.
    assert report.ok is False
    assert any(
        "kimi" in err.message
        and ("exhausted" in err.message or "blocked" in err.message)
        for err in report.errors
    ), report.errors


def test_quota_preflight_override_receipt_write_failure_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Failed override receipt publication must NOT silently proceed.

    A dispatch with --ignore-usage-quota-warnings must persist the override
    receipt before any launch; if the write fails the operator must see the
    failure and the launch must be refused.
    """
    from vibecrafted_core.dispatch import cli as dispatch_cli

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

    # Use a real git repo so the dispatch CLI can resolve artifact identity.
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.com/testorg/testrepo"],
        cwd=repo,
        check=True,
    )

    plan = tmp_path / "plan.dispatch.toml"
    plan.write_text(
        f"""schema = "vibecrafted.dispatch.v1"
[meta]
repo = "{repo}"
[[cuts]]
id = "c1"
agent = "kimi"
workflow = "implement"
prompt = "run kimi cut"
  [[cuts.verify]]
  run = "echo ok"
  expect = {{ exit_code = 0 }}
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("VIBECRAFTED_HOME", str(home))
    # Patch the writer before the dispatch CLI attempts the write so the
    # override receipt write raises OSError.
    import pathlib

    real_write_text = pathlib.Path.write_text

    def _failing_write_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name == "quota-override-receipt.json":
            raise OSError("simulated: read-only filesystem")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "write_text", _failing_write_text)

    code = dispatch_cli.main(
        [
            str(plan),
            "--ignore-usage-quota-warnings",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert code != 0, (
        "override receipt write failure must not return 0; got "
        f"stdout={captured.out!r} stderr={captured.err!r}"
    )
    combined = captured.out + captured.err
    assert "receipt" in combined.lower() or "read-only" in combined.lower(), combined


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
