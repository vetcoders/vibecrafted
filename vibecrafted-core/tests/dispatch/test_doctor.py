from __future__ import annotations

import subprocess
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
