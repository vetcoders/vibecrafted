from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import pr_closure_receipts as receipts
from vibecrafted_core.pr_closure_receipts import validate_receipt, verify_candidate

COUNTS = r"(?P<passed>\d+) passed, (?P<failed>\d+) failed, (?P<skipped>\d+) skipped"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


@pytest.fixture
def candidate(tmp_path: Path) -> tuple[dict, Path, dict, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Receipt tests")
    (repo / "tracked.txt").write_text("base\n")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("candidate\n")
    git(repo, "add", "feature.txt")
    git(repo, "commit", "-m", "feature")
    head = git(repo, "rev-parse", "HEAD")
    profile = {
        "schema": "vibecrafted.pr-closure.v1",
        "repository": "example/product",
        "target_branches": ["main"],
        "remote_checks": [{"name": "CI", "app_id": 1}],
        "require_approval": True,
        "checks": [
            {
                "name": "tests",
                "argv": [
                    sys.executable,
                    "-c",
                    "print('3 passed, 0 failed, 0 skipped')",
                ],
                "timeout_seconds": 5,
                "test_counts_regex": COUNTS,
            }
        ],
    }
    snapshot = {
        "repository": "example/product",
        "number": 7,
        "headRefOid": head,
        "baseRefOid": base,
        "headRefName": "feature",
        "baseRefName": "main",
        "review_digest": "a" * 64,
    }
    return profile, repo, snapshot, tmp_path / "evidence" / "receipt.json"


def test_real_candidate_records_full_combined_logs_and_revalidates(
    candidate, monkeypatch
):
    profile, repo, snapshot, output = candidate
    monkeypatch.setenv("PR_TEST_SECRET", "must-not-reach-command")
    script = (
        "import os,sys; assert 'PR_TEST_SECRET' not in os.environ; "
        "print('x' * 15000); print('3 passed, 0 failed, 0 skipped'); "
        "print('stderr evidence', file=sys.stderr)"
    )
    profile["checks"][0]["argv"][-1] = script
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "pass"
    assert result["observed_head_sha"] == snapshot["headRefOid"]
    assert result["tested_head_sha"] == git(repo, "rev-parse", "HEAD")
    log = Path(result["checks"][0]["log_path"]).read_bytes()
    assert len(log) > 15000 and b"stderr evidence" in log
    assert result["checks"][0]["log_sha256"] == hashlib.sha256(log).hexdigest()
    assert "do not authenticate source" in result["provenance"]
    assert validate_receipt(profile, repo, snapshot, output) == result


def test_gate_runtime_home_is_external_private_and_ambient_home_is_replaced(
    candidate, monkeypatch
):
    profile, repo, snapshot, output = candidate
    ambient = output.parent / "ambient-home"
    monkeypatch.setenv("VIBECRAFTED_HOME", str(ambient))
    monkeypatch.setenv("GATE_SECRET", "must-not-reach-the-gate")
    expected = output.parent / "receipt.json.check-000.log.home"
    profile["checks"][0]["argv"] = [
        sys.executable,
        "-c",
        (
            "import os,sys; from pathlib import Path; "
            "assert 'GATE_SECRET' not in os.environ; "
            "home=Path(os.environ['VIBECRAFTED_HOME']); assert home==Path(sys.argv[1]); "
            "(home/'gate-marker').write_text('isolated'); print('1 passed, 0 failed, 0 skipped')"
        ),
        str(expected),
    ]
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "pass"
    assert result["checks"][0]["vibecrafted_home"] == str(expected)
    assert not expected.is_relative_to(repo)
    assert expected.stat().st_mode & 0o777 == 0o700
    assert (expected / "gate-marker").read_text() == "isolated"
    assert not ambient.exists()
    assert validate_receipt(profile, repo, snapshot, output) == result


def test_gate_runtime_home_never_reuses_existing_state(candidate):
    profile, repo, snapshot, output = candidate
    home = output.parent / "receipt.json.check-000.log.home"
    home.mkdir(parents=True)
    (home / "old-state").write_text("do not reuse")
    with pytest.raises(ValueError, match="overwrite"):
        verify_candidate(profile, repo, snapshot, output)
    assert not output.exists()


@pytest.mark.parametrize(
    "summary,exit_code",
    [
        ("0 passed, 0 failed, 0 skipped", 0),
        ("3 passed, 1 failed, 0 skipped", 0),
        ("3 passed, 0 failed, 1 skipped", 0),
        ("3 passed, 0 failed, 0 skipped", 4),
        ("nothing was collected", 0),
    ],
)
def test_empty_failed_skipped_nonzero_or_missing_summary_cannot_pass(
    candidate, summary, exit_code
):
    profile, repo, snapshot, output = candidate
    profile["checks"][0]["argv"][-1] = (
        f"import sys; print({summary!r}); sys.exit({exit_code})"
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == result["checks"][0]["status"] == "failed"
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


def test_all_summary_counts_are_aggregated(candidate):
    profile, repo, snapshot, output = candidate
    profile["checks"][0]["argv"][-1] = (
        "print('2 passed, 0 failed, 0 skipped'); print('1 passed, 0 failed, 1 skipped')"
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["checks"][0]["test_counts"] == {
        "passed": 3,
        "failed": 0,
        "skipped": 1,
    }
    assert result["status"] == "failed"


def test_every_gate_must_pass_including_checks_without_test_counts(candidate):
    profile, repo, snapshot, output = candidate
    profile["checks"].append(
        {
            "name": "lint",
            "argv": [sys.executable, "-c", "raise SystemExit(2)"],
            "timeout_seconds": 5,
        }
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert [check["status"] for check in result["checks"]] == ["pass", "failed"]
    assert result["status"] == "failed"


def test_timeout_retains_output_and_fails(candidate):
    profile, repo, snapshot, output = candidate
    profile["checks"][0]["timeout_seconds"] = 1
    profile["checks"][0]["argv"][-1] = (
        "import time; print('3 passed, 0 failed, 0 skipped', flush=True); time.sleep(60)"
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "failed"
    assert result["checks"][0]["timed_out"] is True
    assert result["checks"][0]["test_counts"]["passed"] == 3


@pytest.mark.parametrize("name", ["tracked.txt", "new-untracked.txt"])
def test_gate_mutation_dirties_candidate_and_receipt(candidate, name):
    profile, repo, snapshot, output = candidate
    profile["checks"][0]["argv"][-1] = (
        f"from pathlib import Path; Path({name!r}).write_text('mutated'); "
        "print('3 passed, 0 failed, 0 skipped')"
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "failed"
    assert result["checkout_after"]["clean"] is False


def test_gate_commit_change_fails_even_when_checkout_is_clean(candidate):
    profile, repo, snapshot, output = candidate
    profile["checks"][0]["argv"][-1] = (
        "import subprocess; subprocess.run(['git','-c','user.name=T','-c',"
        "'user.email=t@example.invalid','commit','--allow-empty','-m','changed'],check=True); "
        "print('3 passed, 0 failed, 0 skipped')"
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["checkout_after"]["clean"] is True
    assert result["checkout_after"]["head_sha"] != result["tested_head_sha"]
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("baseRefOid", "b" * 40),
        ("headRefOid", "b" * 40),
        ("review_digest", "b" * 64),
        ("headRefName", "other-feature"),
        ("number", 8),
        ("baseRefName", "develop"),
    ],
)
def test_stale_upstream_identity_refuses_receipt(candidate, field, value):
    profile, repo, snapshot, output = candidate
    verify_candidate(profile, repo, snapshot, output)
    snapshot[field] = value
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


def test_changed_profile_or_local_head_or_untracked_file_refuses_receipt(candidate):
    profile, repo, snapshot, output = candidate
    verify_candidate(profile, repo, snapshot, output)
    with pytest.raises(ValueError, match="mismatch"):
        validate_receipt(
            profile, repo, {**snapshot, "baseRefOid": snapshot["headRefOid"]}, output
        )
    changed = copy.deepcopy(profile)
    changed["require_approval"] = False
    with pytest.raises(ValueError):
        validate_receipt(changed, repo, snapshot, output)
    (repo / "untracked").write_text("dirty")
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)
    (repo / "untracked").unlink()
    git(repo, "commit", "--allow-empty", "-m", "new HEAD")
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


@pytest.mark.parametrize("tamper", ["log", "count", "argv", "exit_bool", "missing_log"])
def test_corrupt_logs_and_forged_result_fields_refuse(candidate, tamper):
    profile, repo, snapshot, output = candidate
    result = verify_candidate(profile, repo, snapshot, output)
    log = Path(result["checks"][0]["log_path"])
    if tamper == "log":
        log.write_text("changed\n")
    elif tamper == "missing_log":
        log.unlink()
    else:
        if tamper == "count":
            result["checks"][0]["test_counts"]["passed"] = 99
        elif tamper == "exit_bool":
            result["checks"][0]["exit_code"] = False
        else:
            result["checks"][0]["argv"] = ["something-else"]
        output.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


@pytest.mark.parametrize("existing", ["receipt", "log", "dangling_symlink"])
def test_evidence_never_overwrites_existing_paths(candidate, existing):
    profile, repo, snapshot, output = candidate
    output.parent.mkdir()
    target = (
        output
        if existing != "log"
        else output.with_name(output.name + ".check-000.log")
    )
    if existing == "dangling_symlink":
        target.symlink_to(output.parent / "missing")
    else:
        target.write_bytes(b"existing")
    with pytest.raises(ValueError, match="overwrite"):
        verify_candidate(profile, repo, snapshot, output)
    if existing != "dangling_symlink":
        assert target.read_bytes() == b"existing"


def test_evidence_inside_candidate_or_initial_dirty_candidate_refuses(candidate):
    profile, repo, snapshot, output = candidate
    with pytest.raises(ValueError, match="outside"):
        verify_candidate(profile, repo, snapshot, repo / "receipt.json")
    (repo / "untracked").write_text("dirty")
    with pytest.raises(ValueError, match="dirty"):
        verify_candidate(profile, repo, snapshot, output)
    assert not output.exists()


def test_candidate_must_contain_both_tips_but_may_be_a_merge_commit(candidate):
    profile, repo, snapshot, output = candidate
    git(repo, "checkout", "main")
    (repo / "base-advance.txt").write_text("new base")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "advance base")
    snapshot["baseRefOid"] = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "feature")
    with pytest.raises(ValueError, match="Git observation"):
        verify_candidate(profile, repo, snapshot, output)
    git(repo, "merge", "--no-edit", "main")
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["tested_head_sha"] != snapshot["headRefOid"]
    assert validate_receipt(profile, repo, snapshot, output)["status"] == "pass"
    published = {**snapshot, "headRefOid": result["tested_head_sha"]}
    assert validate_receipt(profile, repo, published, output) == result
    third_head = {**snapshot, "headRefOid": snapshot["baseRefOid"]}
    with pytest.raises(ValueError, match="neither"):
        validate_receipt(profile, repo, third_head, output)


@pytest.mark.parametrize("pattern", [None, "(?P<passed>\\d+)", "["])
def test_missing_or_invalid_count_contract_refuses_before_execution(candidate, pattern):
    profile, repo, snapshot, output = candidate
    if pattern is None:
        profile["checks"][0].pop("test_counts_regex")
    else:
        profile["checks"][0]["test_counts_regex"] = pattern
    with pytest.raises(ValueError):
        verify_candidate(profile, repo, snapshot, output)
    assert not output.exists()


def _junit_profile(profile, xml):
    check = profile["checks"][0]
    check.pop("test_counts_regex")
    check["junit"] = True
    check["argv"] = [
        sys.executable,
        "-c",
        "import sys; from pathlib import Path; Path(sys.argv[1]).write_text(sys.argv[2])",
        "{junit}",
        xml,
    ]


def test_real_pytest_junit_gate_counts_cases_and_revalidates(candidate, tmp_path):
    profile, repo, snapshot, output = candidate
    test = tmp_path / "test_real.py"
    test.write_text("def test_real():\n    assert 2 + 2 == 4\n")
    check = profile["checks"][0]
    check.pop("test_counts_regex")
    check["junit"] = True
    check["argv"] = [
        sys.executable,
        "-m",
        "pytest",
        str(test),
        "--junitxml={junit}",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "pass"
    check_result = result["checks"][0]
    assert check_result["test_counts"] == {"passed": 1, "failed": 0, "skipped": 0}
    report = Path(check_result["junit_path"])
    assert report.name == "receipt.json.check-000.junit.xml"
    assert (
        check_result["junit_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    )
    assert "--junitxml={junit}" in check_result["argv"]
    assert f"--junitxml={report}" in check_result["executed_argv"]
    assert validate_receipt(profile, repo, snapshot, output) == result


def test_nested_junit_suite_counters_do_not_double_count_testcases(candidate):
    profile, repo, snapshot, output = candidate
    _junit_profile(
        profile,
        '<testsuites tests="2"><testsuite name="outer" tests="2">'
        '<testsuite name="inner" tests="2"><testcase name="a"/><testcase name="b"/>'
        "</testsuite></testsuite></testsuites>",
    )
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "pass"
    assert result["checks"][0]["test_counts"]["passed"] == 2
    assert validate_receipt(profile, repo, snapshot, output) == result


@pytest.mark.parametrize(
    "xml",
    [
        "",
        "<testsuite>",
        "<other/>",
        "<testsuite tests='0'/>",
        "<testsuite><testcase name='skip'><skipped/></testcase></testsuite>",
        "<testsuite><testcase name='fail'><failure/></testcase></testsuite>",
        "<testsuite><testcase name='error'><error/></testcase></testsuite>",
        "<testsuite><testcase name='same'/><testcase name='same'/></testsuite>",
        "<testsuite tests='-1'><testcase name='a'/></testsuite>",
        "<testsuite tests='2'><testcase name='a'/></testsuite>",
        "<testsuite skipped='1'><testcase name='a'/></testsuite>",
        "<testsuite><testcase/></testsuite>",
        "<testsuite><testcase name='a'><testcase name='b'/></testcase></testsuite>",
        "<testsuite><testcase name='a'/><properties><testcase name='hidden'><skipped/></testcase></properties></testsuite>",
        '<!DOCTYPE testsuite [<!ENTITY test "expanded">]><testsuite><testcase name="&test;"/></testsuite>',
        '<!DOCTYPE testsuite SYSTEM "file:///etc/passwd"><testsuite><testcase name="a"/></testsuite>',
    ],
)
def test_invalid_empty_skipped_failed_or_ambiguous_junit_fails(candidate, xml):
    profile, repo, snapshot, output = candidate
    _junit_profile(profile, xml)
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "failed"
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


def test_missing_junit_report_fails_even_with_success_exit(candidate):
    profile, repo, snapshot, output = candidate
    _junit_profile(profile, "<testsuite><testcase name='a'/></testsuite>")
    profile["checks"][0]["argv"] = [sys.executable, "-c", "pass", "{junit}"]
    result = verify_candidate(profile, repo, snapshot, output)
    assert result["status"] == "failed"
    assert "missing" in result["checks"][0]["error"]


@pytest.mark.parametrize(
    "tamper", ["corrupt", "missing", "symlink", "digest", "executed_argv"]
)
def test_junit_report_and_execution_identity_are_revalidated(candidate, tamper):
    profile, repo, snapshot, output = candidate
    _junit_profile(profile, "<testsuite><testcase name='a'/></testsuite>")
    result = verify_candidate(profile, repo, snapshot, output)
    report = Path(result["checks"][0]["junit_path"])
    if tamper == "corrupt":
        report.write_text("<testsuite><testcase name='other'/></testsuite>")
    elif tamper == "missing":
        report.unlink()
    elif tamper == "symlink":
        moved = report.with_suffix(".saved")
        report.rename(moved)
        report.symlink_to(moved)
    else:
        result["checks"][0][
            "junit_sha256" if tamper == "digest" else "executed_argv"
        ] = "changed"
        output.write_text(json.dumps(result))
    with pytest.raises(ValueError):
        validate_receipt(profile, repo, snapshot, output)


def test_junit_report_never_overwrites_preexisting_evidence(candidate):
    profile, repo, snapshot, output = candidate
    _junit_profile(profile, "<testsuite><testcase name='a'/></testsuite>")
    output.parent.mkdir()
    report = output.with_name(output.name + ".check-000.junit.xml")
    report.write_text("existing evidence")
    with pytest.raises(ValueError, match="overwrite"):
        verify_candidate(profile, repo, snapshot, output)
    assert report.read_text() == "existing evidence"


@pytest.mark.parametrize("mode", ["missing_placeholder", "both", "false"])
def test_junit_profile_contract_refuses_before_running(candidate, mode):
    profile, repo, snapshot, output = candidate
    _junit_profile(profile, "<testsuite><testcase name='a'/></testsuite>")
    if mode == "missing_placeholder":
        profile["checks"][0]["argv"] = [sys.executable, "-c", "pass"]
    elif mode == "both":
        profile["checks"][0]["test_counts_regex"] = COUNTS
    else:
        profile["checks"][0]["junit"] = False
    with pytest.raises(ValueError):
        verify_candidate(profile, repo, snapshot, output)
    assert not output.exists()


def test_unsupported_executor_refuses_before_any_subprocess(candidate, monkeypatch):
    profile, repo, snapshot, output = candidate
    monkeypatch.setattr(receipts, "_POSIX_PROCESS_GROUPS", False)

    def unexpected(*args, **kwargs):
        raise AssertionError("subprocess started on unsupported executor")

    monkeypatch.setattr(receipts.subprocess, "run", unexpected)
    monkeypatch.setattr(receipts.subprocess, "Popen", unexpected)
    with pytest.raises(ValueError, match="requires POSIX"):
        verify_candidate(profile, repo, snapshot, output)
    with pytest.raises(ValueError, match="requires POSIX"):
        validate_receipt(profile, repo, snapshot, output)
