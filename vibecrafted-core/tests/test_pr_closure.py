"""PR closure must refuse stale or incomplete evidence before a remote mutation."""

import copy
import json
import subprocess
import sys

import pytest
from vibecrafted_core import cli
from vibecrafted_core import pr_closure as pr


@pytest.fixture
def profile():
    return {
        "schema": pr.SCHEMA,
        "repository": "example/project",
        "target_branches": ["develop", "main"],
        "require_approval": False,
        "checks": [
            {
                "name": "tests",
                "argv": ["make", "verify"],
                "timeout_seconds": 60,
                "test_counts_regex": r"(?P<passed>\d+) passed; (?P<failed>\d+) failed; (?P<skipped>\d+) skipped",
            }
        ],
        "remote_checks": [{"name": "CI", "app_id": 15368}],
    }


@pytest.fixture
def snapshot(profile):
    return {
        "repository": profile["repository"],
        "number": 12,
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "headRefName": "feat/one",
        "baseRefName": "develop",
        "review_digest": "c" * 64,
        "head_repository": profile["repository"],
        "state": "open",
        "merged": False,
        "isDraft": False,
        "mergeable": True,
        "title": "A real fix",
        "body": "Acceptance",
        "reviews": [],
        "comments": [],
        "threads": [],
    }


def test_profile_roundtrip_and_unknown_binding_refusal(tmp_path, profile):
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(profile))
    assert pr.load_profile(path) == profile
    profile["trust_worker_says_pass"] = True
    path.write_text(json.dumps(profile))
    with pytest.raises(Exception, match="Additional properties"):
        pr.load_profile(path)


def test_profile_requires_actual_test_count_contract(tmp_path, profile):
    del profile["checks"][0]["test_counts_regex"]
    path = tmp_path / "workflow.json"
    path.write_text(json.dumps(profile))
    with pytest.raises(ValueError, match="test gate"):
        pr.load_profile(path)


def test_order_stacks_and_develop_promotion_before_oldest_age(monkeypatch, profile):
    def row(n, head, base):
        return {
            "number": n,
            "headRefName": head,
            "baseRefName": base,
            "createdAt": f"2026-01-{n:02d}",
        }

    monkeypatch.setattr(
        pr,
        "_run",
        lambda *a, **k: json.dumps(
            [
                row(1, "develop", "main"),
                row(2, "feat/parent", "develop"),
                row(3, "feat/child", "feat/parent"),
            ]
        ),
    )
    assert pr.batch_plan(profile)["order"] == [2, 3, 1]
    # Stacked child remains explicitly blocked until retargeted; ordering never
    # treats its custom base as permission to merge into a feature branch.
    assert pr.batch_plan(profile)["pull_requests"][2]["depends_on"] == [2]


def test_cycle_refuses_partial_plan(monkeypatch, profile):
    monkeypatch.setattr(
        pr,
        "_run",
        lambda *a, **k: json.dumps(
            [
                {
                    "number": 1,
                    "headRefName": "one",
                    "baseRefName": "two",
                    "createdAt": "2026",
                },
                {
                    "number": 2,
                    "headRefName": "two",
                    "baseRefName": "one",
                    "createdAt": "2026",
                },
            ]
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        pr.batch_plan(profile)


def test_generated_dispatch_uses_canonical_parser_and_worktree_head(
    tmp_path, profile, snapshot
):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    git("init", "-q")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "--allow-empty",
        "-m",
        "baseline",
    )
    snapshot["headRefOid"] = snapshot["baseRefOid"] = git("rev-parse", "HEAD")
    snapshot["title"] = "Naprawa 🚀\u007f"
    text = pr.dispatch_plan(profile, tmp_path, snapshot, tmp_path.parent / "reports")
    result = pr.doctor_dispatch(text, base_dir=tmp_path)
    assert result.ok, result.errors
    assert result.dispatch.policy.concurrency == 1
    assert result.dispatch.policy.repair_rounds == 3
    cut = result.dispatch.cuts[0]
    assert cut.base == snapshot["headRefOid"]
    assert not cut.integrator
    assert "Do not push" in cut.prompt
    assert "Acceptance" in cut.prompt
    changed = {**snapshot, "review_digest": "d" * 64}
    next_plan = pr.doctor_dispatch(
        pr.dispatch_plan(profile, tmp_path, changed, tmp_path.parent / "reports"),
        base_dir=tmp_path,
    )
    assert next_plan.dispatch.cuts[0].id != cut.id
    assert not result.dispatch.policy.require_commit
    clean_gate = cut.verify[1]
    clean = git("status", "--porcelain=v1", "--untracked-files=all")
    assert all(matcher.check(clean, exit_code=0) for matcher in clean_gate.matchers)
    (tmp_path / "uncommitted.txt").write_text(
        "must be committed or removed by its owner"
    )
    dirty = git("status", "--porcelain=v1", "--untracked-files=all")
    assert not all(matcher.check(dirty, exit_code=0) for matcher in clean_gate.matchers)


@pytest.mark.parametrize(
    "changed",
    [
        {"isDraft": True},
        {"state": "closed"},
        {"baseRefName": "feature"},
        {"head_repository": "fork/project"},
    ],
)
def test_dispatch_refuses_ineligible_candidate(tmp_path, profile, snapshot, changed):
    snapshot.update(changed)
    with pytest.raises(ValueError):
        pr.dispatch_plan(profile, tmp_path, snapshot, tmp_path.parent / "reports")


def _server_rules(*, strict=True, queue=False, approval=0):
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": "CI", "integration_id": 15368}],
                "strict_required_status_checks_policy": strict,
            },
        },
        {
            "type": "pull_request",
            "parameters": {
                "required_review_thread_resolution": True,
                "required_approving_review_count": approval,
            },
        },
    ]
    if queue:
        rules.append({"type": "merge_queue", "parameters": {}})
    return rules


def _admission_setup(monkeypatch, snapshot, runs=None, rules=None):
    calls = []
    monkeypatch.setattr(pr, "snapshot", lambda *a: copy.deepcopy(snapshot))
    monkeypatch.setattr(
        pr, "validate_receipt", lambda *a: {"tested_head_sha": snapshot["headRefOid"]}
    )

    def run(argv, **kwargs):
        calls.append(argv)
        if "/rules/branches/" in " ".join(argv):
            return json.dumps([_server_rules() if rules is None else rules])
        if "check-runs" in " ".join(argv):
            return json.dumps(
                [
                    {
                        "check_runs": runs
                        if runs is not None
                        else [
                            {
                                "id": 10,
                                "name": "CI",
                                "app": {"id": 15368},
                                "head_sha": snapshot["headRefOid"],
                                "status": "completed",
                                "conclusion": "success",
                            }
                        ]
                    }
                ]
            )
        return ""

    monkeypatch.setattr(pr, "_run", run)
    return calls


def test_admit_read_only_by_default_and_exact_head_mutation_when_requested(
    monkeypatch, profile, snapshot, tmp_path
):
    calls = _admission_setup(monkeypatch, snapshot)
    result = pr.admission(profile, tmp_path, 12, tmp_path / "receipt")
    assert result["ready"] and not result["merged"]
    assert not any("merge" in call for call in calls)
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12],
        "pull_requests": [{"number": 12}],
    }
    pr.admission(profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch)
    assert calls[-1][-2:] == ["--match-head-commit", snapshot["headRefOid"]]
    assert "--admin" not in calls[-1]


@pytest.mark.parametrize("strict,queue", [(True, False), (False, True)])
def test_auto_merge_requires_pinned_checks_and_server_base_freshness(
    monkeypatch, profile, snapshot, tmp_path, strict, queue
):
    calls = _admission_setup(
        monkeypatch, snapshot, rules=_server_rules(strict=strict, queue=queue)
    )
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12],
        "pull_requests": [{"number": 12}],
    }
    result = pr.admission(
        profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch
    )
    assert result["auto_merge_requested"]
    assert calls[-2][-1].endswith("/rules/branches/develop?per_page=100")
    assert calls[-1][:3] == ["gh", "pr", "merge"]


@pytest.mark.parametrize(
    "damage",
    [
        "no_rules",
        "missing_check",
        "missing_pin",
        "wrong_pin",
        "boolean_pin",
        "stale_base",
        "unresolved_threads",
        "missing_pr_rule",
        "malformed",
    ],
)
def test_weak_effective_branch_policy_never_requests_auto_merge(
    monkeypatch, profile, snapshot, tmp_path, damage
):
    rules = _server_rules()
    if damage == "no_rules":
        rules = []
    elif damage == "missing_check":
        rules[0]["parameters"]["required_status_checks"][0]["context"] = "other check"
    elif damage == "missing_pin":
        rules[0]["parameters"]["required_status_checks"][0].pop("integration_id")
    elif damage == "wrong_pin":
        rules[0]["parameters"]["required_status_checks"][0]["integration_id"] = 999
    elif damage == "boolean_pin":
        rules[0]["parameters"]["required_status_checks"][0]["integration_id"] = True
    elif damage == "stale_base":
        rules[0]["parameters"]["strict_required_status_checks_policy"] = False
    elif damage == "unresolved_threads":
        rules[1]["parameters"]["required_review_thread_resolution"] = False
    elif damage == "missing_pr_rule":
        rules.pop()
    else:
        rules = [None]
    calls = _admission_setup(monkeypatch, snapshot, rules=rules)
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12],
        "pull_requests": [{"number": 12}],
    }
    with pytest.raises(ValueError):
        pr.admission(
            profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch
        )
    assert not any(call[:3] == ["gh", "pr", "merge"] for call in calls)


@pytest.mark.parametrize("approval", [0, 1])
def test_profile_approval_must_also_be_required_by_server(
    monkeypatch, profile, snapshot, tmp_path, approval
):
    profile["require_approval"] = True
    snapshot["reviews"] = [{"state": "APPROVED", "user": {"login": "reviewer"}}]
    calls = _admission_setup(
        monkeypatch, snapshot, rules=_server_rules(approval=approval)
    )
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12],
        "pull_requests": [{"number": 12}],
    }
    if approval:
        assert pr.admission(
            profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch
        )["auto_merge_requested"]
    else:
        with pytest.raises(ValueError, match="server-enforced approval"):
            pr.admission(
                profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch
            )
        assert not any(call[:3] == ["gh", "pr", "merge"] for call in calls)


def test_read_only_admission_preserves_snapshot_readiness_without_server_policy_check(
    monkeypatch, profile, snapshot, tmp_path
):
    calls = _admission_setup(monkeypatch, snapshot, rules=[])
    result = pr.admission(profile, tmp_path, 12, tmp_path / "receipt")
    assert result["ready"] and "when enabling" in result["note"]
    assert not any("/rules/branches/" in " ".join(call) for call in calls)


def test_effective_branch_rule_request_quotes_branch_and_collects_all_pages(
    monkeypatch, profile
):
    rules = _server_rules()
    calls = []

    def run(argv):
        calls.append(argv)
        return json.dumps([[rules[0]], [rules[1]]])

    monkeypatch.setattr(pr, "_run", run)
    pr._require_server_policy(profile, "release/0.18")
    assert calls == [
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            "repos/example/project/rules/branches/release%2F0.18?per_page=100",
        ]
    ]


@pytest.mark.parametrize(
    "conclusion", ["failure", "skipped", "neutral", "cancelled", None]
)
def test_non_success_native_check_never_requests_merge(
    monkeypatch, profile, snapshot, tmp_path, conclusion
):
    calls = _admission_setup(
        monkeypatch,
        snapshot,
        [
            {
                "id": 1,
                "name": "CI",
                "app": {"id": 15368},
                "head_sha": snapshot["headRefOid"],
                "status": "completed",
                "conclusion": conclusion,
            }
        ],
    )
    with pytest.raises(ValueError, match="native check"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt", enable=True)
    assert not any("merge" in call for call in calls)


def test_matching_name_from_wrong_app_is_not_evidence(
    monkeypatch, profile, snapshot, tmp_path
):
    _admission_setup(
        monkeypatch,
        snapshot,
        [
            {
                "id": 1,
                "name": "CI",
                "app": {"id": 999},
                "head_sha": snapshot["headRefOid"],
                "status": "completed",
                "conclusion": "success",
            }
        ],
    )
    with pytest.raises(ValueError, match="native check"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt")


def test_latest_failed_rerun_invalidates_earlier_green(
    monkeypatch, profile, snapshot, tmp_path
):
    _admission_setup(
        monkeypatch,
        snapshot,
        [
            {
                "id": n,
                "name": "CI",
                "app": {"id": 15368},
                "head_sha": snapshot["headRefOid"],
                "status": "completed",
                "conclusion": outcome,
            }
            for n, outcome in [(1, "success"), (2, "failure")]
        ],
    )
    with pytest.raises(ValueError, match="native check"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt")


def test_unresolved_review_blocks_before_any_mutation(
    monkeypatch, profile, snapshot, tmp_path
):
    snapshot["threads"] = [{"isResolved": False}]
    calls = _admission_setup(monkeypatch, snapshot)
    with pytest.raises(ValueError, match="threads"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt", enable=True)
    assert calls == []


def test_changed_review_during_admission_refuses_merge(
    monkeypatch, profile, snapshot, tmp_path
):
    calls = _admission_setup(monkeypatch, snapshot)
    changed = {**snapshot, "review_digest": "changed"}
    responses = iter([snapshot, changed])
    monkeypatch.setattr(pr, "snapshot", lambda *a: next(responses))
    with pytest.raises(ValueError, match="changed"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt", enable=True)
    assert not any("merge" in call for call in calls)


def test_auto_merge_requires_batch_and_waits_for_actual_predecessor_merge(
    monkeypatch, profile, snapshot, tmp_path
):
    calls = _admission_setup(monkeypatch, snapshot)
    with pytest.raises(ValueError, match="frozen batch"):
        pr.admission(profile, tmp_path, 12, tmp_path / "receipt", enable=True)
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [11, 12],
        "pull_requests": [{"number": 11}, {"number": 12}],
    }
    monkeypatch.setattr(pr, "_api", lambda *a: {"merged": False})
    with pytest.raises(ValueError, match="confirmed target merge"):
        pr.admission(
            profile, tmp_path, 12, tmp_path / "receipt", enable=True, batch=batch
        )
    assert not any("merge" in call for call in calls)


def test_confirmation_proves_current_destination_ancestry(monkeypatch, profile):
    row = {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": "a" * 40}
    monkeypatch.setattr(
        pr,
        "_api",
        lambda endpoint: {"status": "diverged"} if "/compare/" in endpoint else row,
    )
    with pytest.raises(ValueError, match="absent"):
        pr._confirmed_merge(profile, 12)
    monkeypatch.setattr(
        pr,
        "_api",
        lambda endpoint: {"status": "ahead"} if "/compare/" in endpoint else row,
    )
    assert pr._confirmed_merge(profile, 12)["merge_sha"] == "a" * 40


def test_batch_rejects_inverted_dependency_even_with_complete_number_set(profile):
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12, 11],
        "pull_requests": [{"number": 11}, {"number": 12, "depends_on": [11]}],
    }
    with pytest.raises(ValueError, match="dependencies"):
        pr._validate_batch(profile, batch)


def test_malformed_batch_is_a_controlled_cli_refusal(tmp_path, profile, capsys):
    path, batch = tmp_path / "workflow.json", tmp_path / "batch.json"
    path.write_text(json.dumps(profile))
    batch.write_text("[]")
    assert pr.main(["confirm", str(path), "--batch", str(batch)]) == 2
    assert "JSON object" in capsys.readouterr().err


def test_snapshot_refuses_truncated_threads(monkeypatch):
    row = {"head": {"sha": "a"}, "base": {"sha": "b"}, "updated_at": "now"}
    monkeypatch.setattr(pr, "_api", lambda *a: row)
    monkeypatch.setattr(
        pr,
        "_run",
        lambda *a, **k: json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "pageInfo": {"hasNextPage": True},
                                "nodes": [],
                            }
                        }
                    }
                }
            }
        ),
    )
    with pytest.raises(ValueError, match="pagination"):
        pr.snapshot("example/project", 12)


def test_closed_unmerged_pr_never_completes_batch(monkeypatch, profile):
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "pull_requests": [{"number": 12}],
        "order": [12],
    }
    monkeypatch.setattr(pr, "_api", lambda *a: {"merged": False})
    with pytest.raises(ValueError, match="confirmed"):
        pr.confirm_batch(profile, batch)


def test_command_help_routes_through_real_cli(capsys):
    assert cli.main(["help", "pr-closure"]) == 0
    assert "--help" in capsys.readouterr().out
    assert cli.main(["pr-closure", "--help"]) == 0
    assert "enable-auto-merge" not in capsys.readouterr().out  # admission owns its flag


def test_export_is_portable_json_not_an_implicit_launch(tmp_path, capsys):
    output = tmp_path / "workflow.json"
    assert (
        pr.main(
            [
                "export",
                "--repository",
                "example/project",
                "--target",
                "main",
                "--remote-check",
                "CI",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert pr.load_profile(output)["repository"] == "example/project"
    assert (
        pr.main(
            [
                "export",
                "--repository",
                "example/project",
                "--target",
                "main",
                "--remote-check",
                "CI",
                "--output",
                str(output),
            ]
        )
        == 2
    )


def test_json_profile_through_real_local_verification_and_read_only_admission(
    tmp_path, monkeypatch, profile, snapshot, capsys
):
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-m",
            "candidate",
        ],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    snapshot["headRefOid"] = snapshot["baseRefOid"] = sha
    profile["checks"] = [
        {
            "name": "JUnit tests",
            "argv": [
                sys.executable,
                "-c",
                'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(\'<testsuites><testsuite tests="1"><testcase classname="fixture" name="works"/></testsuite></testsuites>\')',
                "{junit}",
            ],
            "timeout_seconds": 10,
            "junit": True,
        }
    ]
    path, receipt = tmp_path / "workflow.json", tmp_path / "receipt.json"
    path.write_text(json.dumps(profile))
    monkeypatch.setattr(pr, "snapshot", lambda *a: copy.deepcopy(snapshot))
    assert (
        pr.main(
            [
                "verify",
                str(path),
                "--repo",
                str(repo),
                "--pr",
                "12",
                "--output",
                str(receipt),
            ]
        )
        == 0
    )
    proof = json.loads(receipt.read_text())
    assert proof["tested_head_sha"] == sha
    assert proof["checks"][0]["test_counts"] == {"passed": 1, "failed": 0, "skipped": 0}
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return json.dumps(
            [
                {
                    "check_runs": [
                        {
                            "id": 10,
                            "name": "CI",
                            "app": {"id": 15368},
                            "head_sha": sha,
                            "status": "completed",
                            "conclusion": "success",
                        }
                    ]
                }
            ]
        )

    monkeypatch.setattr(pr, "_run", run)
    assert (
        pr.main(
            [
                "admit",
                str(path),
                "--repo",
                str(repo),
                "--pr",
                "12",
                "--receipt",
                str(receipt),
            ]
        )
        == 0
    )
    assert not any("merge" in call for call in calls)
    assert '"ready": true' in capsys.readouterr().out


def test_release_handoff_pins_explicit_canonical_source_and_native_workflow(
    tmp_path, monkeypatch, profile
):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "--allow-empty",
            "-m",
            "release source",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "https://github.com/public/release.git",
        ],
        check=True,
    )
    head = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
    ).strip()
    with pytest.raises(ValueError, match="explicit canonical"):
        pr.release_plan(profile, tmp_path, {}, tmp_path.parent / "reports")
    profile["release_source"] = {"repository": "public/release", "branch": "main"}
    batch = {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": pr._digest(profile),
        "order": [12],
        "pull_requests": [{"number": 12}],
    }
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        if "/git/ref/" in endpoint:
            return {"object": {"type": "commit", "sha": head}}
        if "/compare/" in endpoint:
            return {"status": "ahead"}
        return {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": head}

    monkeypatch.setattr(pr, "_api", api)
    text = pr.release_plan(profile, tmp_path, batch, tmp_path.parent / "reports")
    result = pr.doctor_dispatch(text, base_dir=tmp_path)
    assert result.ok, result.errors
    assert result.dispatch.cuts[0].workflow == "release"
    assert result.dispatch.cuts[0].base == head
    assert not result.dispatch.policy.require_commit
    assert "repos/public/release/git/ref/heads/main" in calls
    assert "not proof of release" in result.dispatch.cuts[0].prompt
    monkeypatch.setattr(pr, "_api", lambda *a: {"merged": False})
    with pytest.raises(ValueError, match="confirmed target merge"):
        pr.release_plan(profile, tmp_path, batch, tmp_path.parent / "reports")
