"""Portable PR profiles, native dispatch preparation and SHA-bound admission.

This module owns PR evidence, not worker execution. Workers remain ordinary
dispatch cuts; GitHub remains the authority for protected-branch admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .dispatch.schema import doctor_dispatch
from .dispatch.verify import sanitize_env
from .pr_closure_receipts import validate_receipt, verify_candidate

SCHEMA = "vibecrafted.pr-closure.v1"
PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema",
        "repository",
        "target_branches",
        "checks",
        "remote_checks",
        "require_approval",
    ],
    "properties": {
        "schema": {"const": SCHEMA},
        "repository": {"type": "string", "pattern": r"^[\w.-]+/[\w.-]+$"},
        "target_branches": {
            "type": "array",
            "minItems": 1,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        },
        "require_approval": {"type": "boolean"},
        "release_source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["repository", "branch"],
            "properties": {
                "repository": {"type": "string", "pattern": r"^[\w.-]+/[\w.-]+$"},
                "branch": {"type": "string", "minLength": 1},
            },
        },
        "checks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "argv", "timeout_seconds"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "argv": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 7200,
                    },
                    "test_counts_regex": {"type": "string", "minLength": 1},
                    "junit": {"type": "boolean", "const": True},
                    "allow_skips": {"type": "boolean"},
                    "skip_reason": {"type": "string", "pattern": r"\S"},
                },
            },
        },
        "remote_checks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "app_id"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "app_id": {"type": "integer", "minimum": 1},
                },
            },
        },
    },
}

THREAD_QUERY = """query($owner:String!,$name:String!,$number:Int!) {
  repository(owner:$owner,name:$name) { pullRequest(number:$number) {
    reviewThreads(first:100) { pageInfo { hasNextPage } nodes {
      id isResolved isOutdated path line
      comments(first:100) { pageInfo { hasNextPage } nodes {
        id body updatedAt author { login }
      } }
    } }
  } }
}"""


def _run(argv: list[str], *, repo: Path | None = None) -> str:
    """Run a bounded helper without exposing credentials or inheriting TTY colors."""
    env = dict(os.environ)
    for key in ("GH_FORCE_TTY", "FORCE_COLOR", "CLICOLOR_FORCE"):
        env.pop(key, None)
    env.update(NO_COLOR="1", GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0")
    if argv[0] == "git":
        extra = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
        if os.environ.get("SSH_AUTH_SOCK"):
            extra["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
        env = sanitize_env(extra=extra)
    result = subprocess.run(
        argv,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ValueError(
            f"{argv[0]} failed (exit {result.returncode}): {result.stderr.strip()[:1000]}"
        )
    return result.stdout


def _api(endpoint: str) -> Any:
    return json.loads(_run(["gh", "api", endpoint]))


def _pages(endpoint: str) -> list[dict[str, Any]]:
    pages = json.loads(_run(["gh", "api", "--paginate", "--slurp", endpoint]))
    return [row for page in pages for row in page]


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def load_profile(path: Path) -> dict[str, Any]:
    """Reject unknown bindings and profiles that cannot prove tests ran."""
    profile = json.loads(path.read_text())
    Draft202012Validator(PROFILE_SCHEMA).validate(profile)
    names = [check["name"] for check in profile["checks"]]
    if len(set(names)) != len(names):
        raise ValueError("local check names must be unique")
    for check in profile["checks"]:
        if check.get("allow_skips", False):
            if "test_counts_regex" not in check and not check.get("junit"):
                raise ValueError("allow_skips requires a counted regex or JUnit gate")
            if (
                not isinstance(check.get("skip_reason"), str)
                or not check["skip_reason"].strip()
            ):
                raise ValueError("allow_skips requires a nonblank skip_reason")
        elif "skip_reason" in check:
            raise ValueError("skip_reason requires allow_skips=true")
    tests = [
        check
        for check in profile["checks"]
        if "test_counts_regex" in check or check.get("junit")
    ]
    if not tests:
        raise ValueError("at least one test gate with explicit counts is required")
    for check in tests:
        if check.get("junit"):
            if "test_counts_regex" in check or not any(
                "{junit}" in arg for arg in check["argv"]
            ):
                raise ValueError(
                    "JUnit gate requires an argv {junit} placeholder and no regex"
                )
            continue
        try:
            pattern = re.compile(check["test_counts_regex"])
        except re.error as exc:
            raise ValueError("invalid test_counts_regex") from exc
        if not {"passed", "failed", "skipped"} <= pattern.groupindex.keys():
            raise ValueError(
                "test_counts_regex needs named passed, failed and skipped groups"
            )
    remote = [(check["name"], check["app_id"]) for check in profile["remote_checks"]]
    if len(set(remote)) != len(remote):
        raise ValueError("remote check identities must be unique")
    return profile


def snapshot(repository: str, number: int) -> dict[str, Any]:
    """Read PR identity and every review page; refuse incomplete thread evidence."""
    row = _api(f"repos/{repository}/pulls/{number}")
    owner, name = repository.split("/")
    data = json.loads(
        _run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={THREAD_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
                "-F",
                f"number={number}",
            ]
        )
    )
    if data.get("errors"):
        raise ValueError("GitHub returned incomplete review-thread evidence")
    threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
    if threads["pageInfo"]["hasNextPage"] or any(
        t["comments"]["pageInfo"]["hasNextPage"] for t in threads["nodes"]
    ):
        raise ValueError(
            "review-thread pagination exceeds bounded snapshot; admission refused"
        )
    reviews = _pages(f"repos/{repository}/pulls/{number}/reviews")
    comments = _pages(f"repos/{repository}/issues/{number}/comments")
    # A second read fences concurrent head/base edits while gathering review pages.
    fresh = _api(f"repos/{repository}/pulls/{number}")
    if (row["head"]["sha"], row["base"]["sha"], row["updated_at"]) != (
        fresh["head"]["sha"],
        fresh["base"]["sha"],
        fresh["updated_at"],
    ):
        raise ValueError("PR changed during snapshot; retry with fresh evidence")
    return {
        "repository": repository,
        "number": number,
        "headRefOid": row["head"]["sha"],
        "baseRefOid": row["base"]["sha"],
        "headRefName": row["head"]["ref"],
        "baseRefName": row["base"]["ref"],
        "head_repository": (row["head"].get("repo") or {}).get("full_name"),
        "state": row["state"],
        "merged": row["merged"],
        "isDraft": row["draft"],
        "mergeable": row["mergeable"],
        "title": row["title"],
        "body": row["body"],
        "review_digest": _digest(
            {"reviews": reviews, "comments": comments, "threads": threads["nodes"]}
        ),
        "reviews": reviews,
        "comments": comments,
        "threads": threads["nodes"],
    }


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        row[key]
        for key in (
            "repository",
            "number",
            "headRefOid",
            "baseRefOid",
            "headRefName",
            "baseRefName",
            "review_digest",
        )
    )


def _candidate(profile: dict[str, Any], row: dict[str, Any]) -> None:
    if row["state"] != "open" or row["isDraft"] or row["merged"]:
        raise ValueError("candidate must be an open, non-draft PR")
    if row["baseRefName"] not in profile["target_branches"]:
        raise ValueError(
            "PR base is outside permitted targets; verify its dependency or intended retargeting"
        )
    if row["head_repository"] != profile["repository"]:
        raise ValueError("fork publication is outside this profile's owned repository")


def batch_plan(profile: dict[str, Any]) -> dict[str, Any]:
    """Freeze open PR identities; stacks precede dependants, promotions come last."""
    rows = json.loads(
        _run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                profile["repository"],
                "--state",
                "open",
                "--limit",
                "1000",
                "--json",
                "number,title,headRefName,headRefOid,baseRefName,isDraft,createdAt",
            ]
        )
    )
    if len(rows) >= 1000:
        raise ValueError("open PR inventory may be truncated")
    deps: dict[int, set[int]] = {row["number"]: set() for row in rows}
    targets = profile["target_branches"]
    parents = {row["headRefName"]: row["baseRefName"] for row in rows}
    for row in rows:
        for other in rows:
            if row is other:
                continue
            if (
                row["baseRefName"] not in targets
                and row["baseRefName"] == other["headRefName"]
            ):
                deps[row["number"]].add(other["number"])
            if row["headRefName"] in targets:
                base, seen = other["baseRefName"], set()
                while base not in targets and base in parents and base not in seen:
                    seen.add(base)
                    base = parents[base]
                if base == row["headRefName"]:
                    deps[row["number"]].add(other["number"])
    order: list[int] = []
    remaining = sorted(rows, key=lambda row: (row["createdAt"], row["number"]))
    while remaining:
        ready = next(
            (row for row in remaining if deps[row["number"]] <= set(order)), None
        )
        if ready is None:
            raise ValueError("PR dependency cycle; no automatic ordering is safe")
        order.append(ready["number"])
        remaining.remove(ready)
    return {
        "schema": "vibecrafted.pr-closure-batch.v1",
        "repository": profile["repository"],
        "profile_sha256": _digest(profile),
        "order": order,
        "pull_requests": [
            {**row, "depends_on": sorted(deps[row["number"]])} for row in rows
        ],
    }


def dispatch_plan(
    profile: dict[str, Any], repo: Path, row: dict[str, Any], reports: Path
) -> str:
    """Compile one fresh PR into the existing dispatch format, without launching it."""
    _candidate(profile, row)
    identity = _digest(
        {
            "head": row["headRefOid"],
            "base": row["baseRefOid"],
            "reviews": row["review_digest"],
            "profile": profile,
        }
    )[:12]
    cut_id = f"pr_{row['number']}_{identity}"
    prompt = (
        f"Prepare PR #{row['number']} in {profile['repository']} for {row['baseRefName']}. "
        f"Observed head {row['headRefOid']}; base {row['baseRefOid']}. "
        "Read repository instructions and map the architecture before edits. Establish the intent, "
        "acceptance criteria and a concrete verifier for each review request. Reconcile current base "
        "by a normal merge, preserve history, repair conflicts and real defects. Do not push, merge "
        "or close GitHub PRs, resolve review threads on behalf of reviewers, publish releases, or "
        "change protection rules. Commit only owned changes. "
        "If the existing head already meets the criteria, retain it and explain why; never manufacture an empty commit. "
        "Report unresolved requests and exact verification limits. The integrator independently runs "
        "the profile gates and publishes the receipt.\n\n"
        + json.dumps(row, ensure_ascii=False, sort_keys=True)
    )

    return _native_plan(
        repo, reports, cut_id, row["headRefOid"], prompt, workflow="workflow"
    )


def _native_plan(
    repo: Path, reports: Path, cut_id: str, head: str, prompt: str, *, workflow: str
) -> str:
    """Keep worker lifecycle and execution in the existing dispatch owner."""

    def q(value: str) -> str:
        # TOML rejects JSON surrogate-pair escapes; keep Unicode scalar values.
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")

    text = (
        f'schema = "vibecrafted.dispatch.v1"\n[meta]\nname = {q(cut_id)}\n'
        f"repo = {q(str(repo))}\nreports_dir = {q(str(reports))}\n"
        f"[meta.baseline]\ncommit = {q(head)}\n"
        "[policy]\nconcurrency = 1\nrepair_rounds = 3\nrequire_commit = false\n"
        'allow_idempotent_existing = false\non_critical_fail = "break"\n'
        '[[phases]]\ntitle = "prepare"\n[[cuts]]\n'
        f'id = {q(cut_id)}\nphase = "prepare"\nagent = "codex"\nworkflow = {q(workflow)}\n'
        f"base = {q(head)}\ncritical = true\nprompt = {q(prompt)}\n"
        '[[cuts.verify]]\nrun = "git diff --check"\n[cuts.verify.expect]\nexit_code = 0\n'
        '[[cuts.verify]]\nrun = "git status --porcelain=v1 --untracked-files=all"\n[cuts.verify.expect]\nequals = ""\nexit_code = 0\n'
    )
    result = doctor_dispatch(text, base_dir=repo)
    if not result.ok:
        raise ValueError("generated dispatch rejected: " + "; ".join(result.errors))
    return text


def _owned_origin(profile: dict[str, Any], repo: Path) -> None:
    urls = _run(
        ["git", "remote", "get-url", "--push", "--all", "origin"], repo=repo
    ).splitlines()
    expected = profile["repository"].lower()
    if len(urls) != 1 or not re.fullmatch(
        r"(?:git@github\.com:|https://github\.com/)"
        + re.escape(expected)
        + r"(?:\.git)?",
        urls[0].lower(),
    ):
        raise ValueError(
            "origin must have exactly one GitHub push URL matching the profile"
        )


def publish(
    profile: dict[str, Any], repo: Path, number: int, receipt: Path
) -> dict[str, Any]:
    """Fast-forward only, after revalidating local proof and current review identity."""
    row = snapshot(profile["repository"], number)
    _candidate(profile, row)
    proof = validate_receipt(profile, repo, row, receipt)
    _owned_origin(profile, repo)
    _run(["git", "check-ref-format", f"refs/heads/{row['headRefName']}"], repo=repo)
    _run(
        [
            "git",
            "push",
            "origin",
            f"{proof['tested_head_sha']}:refs/heads/{row['headRefName']}",
        ],
        repo=repo,
    )
    after = snapshot(profile["repository"], number)
    if after["headRefOid"] != proof["tested_head_sha"]:
        raise ValueError("push completed but remote head differs; no admission claim")
    return {
        "published_sha": after["headRefOid"],
        "receipt": str(receipt.expanduser().resolve()),
        "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        "local_verification": proof["status"],
        "next": "admit with the same receipt; changed base or review requires fresh verification",
    }


def _require_server_policy(profile: dict[str, Any], branch: str) -> None:
    """Require active rulesets to preserve admission conditions after local reads.

    The effective-rules endpoint omits disabled/evaluate rulesets. Classic branch
    protection is deliberately not inferred from an empty modern-rules response.
    """
    endpoint = f"repos/{profile['repository']}/rules/branches/{quote(branch, safe='')}?per_page=100"
    pages = json.loads(_run(["gh", "api", "--paginate", "--slurp", endpoint]))
    if not isinstance(pages, list) or any(
        not isinstance(page, list)
        or any(
            not isinstance(rule, dict) or not isinstance(rule.get("type"), str)
            for rule in page
        )
        for page in pages
    ):
        raise ValueError(
            "effective branch rules response is malformed; auto-merge refused"
        )
    rules = [rule for page in pages for rule in page]
    policy_types = {"required_status_checks", "merge_queue", "pull_request"}
    if any(
        rule["type"] in policy_types and not isinstance(rule.get("parameters"), dict)
        for rule in rules
    ):
        raise ValueError(
            "effective branch rule parameters are missing; auto-merge refused"
        )
    status_rules = [
        rule["parameters"] for rule in rules if rule["type"] == "required_status_checks"
    ]
    required_checks: set[tuple[str, int]] = set()
    strict = False
    for parameters in status_rules:
        checks = parameters.get("required_status_checks")
        if not isinstance(checks, list) or any(
            not isinstance(check, dict) for check in checks
        ):
            raise ValueError(
                "effective required status checks are malformed; auto-merge refused"
            )
        for check in checks:
            context, app_id = check.get("context"), check.get("integration_id")
            if isinstance(context, str) and type(app_id) is int:
                required_checks.add((context, app_id))
        strict |= (
            bool(checks)
            and parameters.get("strict_required_status_checks_policy") is True
        )
    for check in profile["remote_checks"]:
        if (check["name"], check["app_id"]) not in required_checks:
            raise ValueError(
                f"active modern branch rulesets must require {check['name']} "
                f"from app {check['app_id']}; classic protection is not modeled"
            )
    queued = any(rule["type"] == "merge_queue" for rule in rules)
    if not strict and not queued:
        raise ValueError(
            "auto-merge requires strict required status checks or an active merge queue"
        )
    pull_request_rules = [
        rule["parameters"] for rule in rules if rule["type"] == "pull_request"
    ]
    if not any(
        parameters.get("required_review_thread_resolution") is True
        for parameters in pull_request_rules
    ):
        raise ValueError("auto-merge requires server-enforced review thread resolution")
    if profile["require_approval"] and not any(
        type(parameters.get("required_approving_review_count")) is int
        and parameters["required_approving_review_count"] >= 1
        for parameters in pull_request_rules
    ):
        raise ValueError("auto-merge requires at least one server-enforced approval")


def admission(
    profile: dict[str, Any],
    repo: Path,
    number: int,
    receipt: Path,
    *,
    enable: bool = False,
    batch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check local proof and native GitHub gates; optionally request normal auto-merge."""
    row = snapshot(profile["repository"], number)
    _candidate(profile, row)
    proof = validate_receipt(profile, repo, row, receipt)
    if row["headRefOid"] != proof["tested_head_sha"]:
        raise ValueError("candidate must be published before admission")
    if row["mergeable"] is not True:
        raise ValueError("GitHub mergeability is unknown or conflicting")
    if any(not thread["isResolved"] for thread in row["threads"]):
        raise ValueError("unresolved review threads block admission")
    latest_reviews: dict[str, str] = {}
    for review in row["reviews"]:
        if review["state"] != "COMMENTED":
            latest_reviews[review["user"]["login"]] = review["state"]
    if "CHANGES_REQUESTED" in latest_reviews.values():
        raise ValueError("requested changes block admission")
    if profile["require_approval"] and "APPROVED" not in latest_reviews.values():
        raise ValueError("an independent approval is required")
    pages = json.loads(
        _run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{profile['repository']}/commits/{row['headRefOid']}/check-runs?per_page=100",
            ]
        )
    )
    runs = [run for page in pages for run in page["check_runs"]]
    for required in profile["remote_checks"]:
        matches = [
            run
            for run in runs
            if run["name"] == required["name"]
            and run["app"]["id"] == required["app_id"]
            and run["head_sha"] == row["headRefOid"]
        ]
        newest = max(matches, key=lambda run: run["id"], default=None)
        if (
            newest is None
            or newest["status"] != "completed"
            or newest["conclusion"] != "success"
        ):
            raise ValueError(
                f"required native check is not successful: {required['name']} (app {required['app_id']})"
            )
    fresh = snapshot(profile["repository"], number)
    if _identity(fresh) != _identity(row):
        raise ValueError("PR changed during admission; reverify")
    if enable:
        if batch is None:
            raise ValueError(
                "auto-merge requires a frozen batch to enforce serial admission"
            )
        _validate_batch(profile, batch)
        if number not in batch["order"]:
            raise ValueError("PR is outside the frozen batch")
        for previous in batch["order"][: batch["order"].index(number)]:
            _confirmed_merge(profile, previous)
        _require_server_policy(profile, row["baseRefName"])
        _run(
            [
                "gh",
                "pr",
                "merge",
                str(number),
                "--repo",
                profile["repository"],
                "--auto",
                "--squash",
                "--match-head-commit",
                proof["tested_head_sha"],
            ]
        )
    return {
        "ready": True,
        "auto_merge_requested": enable,
        "head_sha": proof["tested_head_sha"],
        "merged": False,
        "note": "Snapshot readiness only; server enforcement is checked when enabling auto-merge. A request is not merge confirmation.",
    }


def _validate_batch(profile: dict[str, Any], batch: dict[str, Any]) -> None:
    if not isinstance(batch, dict):
        raise TypeError("batch must be a JSON object")
    if (
        batch.get("schema") != "vibecrafted.pr-closure-batch.v1"
        or batch.get("repository") != profile["repository"]
        or batch.get("profile_sha256") != _digest(profile)
    ):
        raise ValueError("batch belongs to another profile")
    rows = batch.get("pull_requests")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or type(row.get("number")) is not int
        or row["number"] <= 0
        for row in rows
    ):
        raise ValueError("batch pull_requests must contain positive PR numbers")
    numbers = [row["number"] for row in rows]
    order = batch.get("order", [])
    if (
        not numbers
        or not isinstance(order, list)
        or any(type(number) is not int or number <= 0 for number in order)
        or len(set(numbers)) != len(numbers)
        or len(order) != len(numbers)
        or set(order) != set(numbers)
    ):
        raise ValueError("batch needs a nonempty, complete, unique PR order")
    positions = {number: index for index, number in enumerate(order)}
    for row in rows:
        dependencies = row.get("depends_on", [])
        if not isinstance(dependencies, list) or any(
            type(parent) is not int
            or parent not in positions
            or positions[parent] >= positions[row["number"]]
            for parent in dependencies
        ):
            raise ValueError("batch order violates its declared PR dependencies")


def _confirmed_merge(profile: dict[str, Any], number: int) -> dict[str, Any]:
    row = _api(f"repos/{profile['repository']}/pulls/{number}")
    if (
        not row["merged"]
        or row["base"]["ref"] not in profile["target_branches"]
        or not row["merge_commit_sha"]
    ):
        raise ValueError(f"PR #{number} has no confirmed target merge")
    comparison = _api(
        f"repos/{profile['repository']}/compare/{row['merge_commit_sha']}...{row['base']['ref']}"
    )
    if comparison.get("status") not in {"ahead", "identical"}:
        raise ValueError(f"PR #{number} merge is absent from its current destination")
    return {
        "number": number,
        "base": row["base"]["ref"],
        "merge_sha": row["merge_commit_sha"],
    }


def confirm_batch(profile: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    """Confirm frozen merges and destination ancestry before release handoff."""
    _validate_batch(profile, batch)
    merged = []
    for frozen in batch["pull_requests"]:
        merged.append(_confirmed_merge(profile, frozen["number"]))
    return {
        "batch_complete": True,
        "merged": merged,
        "released": False,
        "next": "run the repository release contract from its canonical release source",
    }


def _write(path: Path, value: str | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(
            value
            if isinstance(value, str)
            else json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        )


def release_plan(
    profile: dict[str, Any], repo: Path, batch: dict[str, Any], reports: Path
) -> str:
    """Prepare a release worker only after proven merges and a bound source tip."""
    source = profile.get("release_source")
    if not source:
        raise ValueError(
            "release requires an explicit canonical release_source binding"
        )
    merged = confirm_batch(profile, batch)
    _owned_origin(source, repo)
    _run(["git", "check-ref-format", f"refs/heads/{source['branch']}"], repo=repo)
    ref = _api(f"repos/{source['repository']}/git/ref/heads/{source['branch']}")
    if ref["object"]["type"] != "commit":
        raise ValueError("release source must resolve to a commit")
    head = ref["object"]["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise ValueError("release source returned an invalid SHA")
    _run(["git", "cat-file", "-e", f"{head}^{{commit}}"], repo=repo)
    identity = _digest({"profile": profile, "batch": batch, "source_sha": head})[:12]
    prompt = (
        f"Release source: {source['repository']} {source['branch']} at exact {head}. "
        f"The originating PR batch belongs to {profile['repository']}. "
        "Use the canonical repository release contract and vc-release workflow. Prove the mapping "
        "from admitted changes to this source; a different repository or copied assets are not "
        "integration proof. Establish the intended version from durable Founder intent and current "
        "repository evidence; do not invent a version or permission. Prepare and verify the source, "
        "required tests, signed artifacts, installation, product acceptance and release receipts. "
        "Use existing signing and publishing owners. Never bypass branch protection, review, signing "
        "or idle guards. Execute outward release only within the current Founder's authorization; "
        "otherwise return the exact prepared artifact and remaining Founder button. Return durable "
        "source/artifact/published/installed identities and independent verification paths. A worker "
        "exit code or report alone is not proof of release.\n\nConfirmed PR batch:\n"
        + json.dumps(merged, ensure_ascii=False, sort_keys=True)
    )
    return _native_plan(
        repo, reports, f"release_{identity}", head, prompt, workflow="release"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI: JSON profile → frozen batch → dispatch → receipt → GitHub admission."""
    parser = argparse.ArgumentParser(prog="vibecrafted pr-closure", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser(
        "export", help="write a portable Rust/Make profile for editing"
    )
    export.add_argument("--repository", required=True)
    export.add_argument("--target", action="append", required=True)
    export.add_argument("--remote-check", action="append", required=True)
    export.add_argument("--require-approval", action="store_true")
    export.add_argument("--release-repository")
    export.add_argument("--release-branch")
    export.add_argument("--output", type=Path, required=True)
    commands = {
        "validate": "validate JSON bindings without executing commands",
        "plan": "freeze open PR membership and dependency order",
        "dispatch": "compile one candidate into a native dispatch TOML plan",
        "verify": "run local gates and retain a SHA-bound receipt outside the checkout",
        "publish": "revalidate the receipt and fast-forward push its exact tested commit",
        "admit": "inspect admission gates; auto-merge requires its explicit flag and batch",
        "confirm": "prove all frozen PRs merged into their current destinations",
        "release": "compile a release dispatch from a completed batch and explicit source binding",
    }
    for command, description in commands.items():
        child = sub.add_parser(command, help=description, description=description)
        child.add_argument("profile", type=Path)
        if command in {"dispatch", "verify", "publish", "admit", "release"}:
            child.add_argument("--repo", type=Path, required=True)
        if command in {"dispatch", "verify", "publish", "admit"}:
            child.add_argument("--pr", type=int, required=True)
        if command in {"plan", "dispatch", "verify", "release"}:
            child.add_argument("--output", type=Path, required=True)
        if command in {"publish", "admit"}:
            child.add_argument("--receipt", type=Path, required=True)
        if command == "admit":
            child.add_argument("--batch", type=Path)
            child.add_argument(
                "--enable-auto-merge",
                action="store_true",
                help="request normal GitHub auto-merge for this exact head; may merge immediately",
            )
        if command in {"confirm", "release"}:
            child.add_argument("--batch", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            if bool(args.release_repository) != bool(args.release_branch):
                raise ValueError(
                    "release source needs both --release-repository and --release-branch"
                )
            profile = {
                "schema": SCHEMA,
                "repository": args.repository,
                "target_branches": args.target,
                "require_approval": args.require_approval,
                "checks": [
                    {
                        "name": "repository checks",
                        "argv": ["make", "check"],
                        "timeout_seconds": 1800,
                    },
                    {
                        "name": "Rust tests",
                        "argv": ["make", "verify"],
                        "timeout_seconds": 3600,
                        "test_counts_regex": r"test result: (?:ok|FAILED)\. (?P<passed>\d+) passed; (?P<failed>\d+) failed; (?P<skipped>\d+) ignored",
                    },
                ],
                "remote_checks": [
                    {"name": name, "app_id": 15368} for name in args.remote_check
                ],
            }
            if args.release_repository:
                profile["release_source"] = {
                    "repository": args.release_repository,
                    "branch": args.release_branch,
                }
            Draft202012Validator(PROFILE_SCHEMA).validate(profile)
            _write(args.output, profile)
            result = {
                "exported": str(args.output),
                "note": "Rust/Make bindings: edit checks for other repository contracts before execution",
            }
        else:
            profile = load_profile(args.profile)
            if args.command == "validate":
                result = {"valid": True, "repository": profile["repository"]}
            elif args.command == "plan":
                result = batch_plan(profile)
                _write(args.output, result)
            elif args.command == "confirm":
                result = confirm_batch(profile, json.loads(args.batch.read_text()))
            else:
                repo = args.repo.expanduser().resolve()
                if args.command == "release":
                    text = release_plan(
                        profile,
                        repo,
                        json.loads(args.batch.read_text()),
                        args.output.resolve().parent / "release",
                    )
                    _write(args.output, text)
                    result = {
                        "dispatch": str(args.output),
                        "next": f"vibecrafted dispatch {shlex.quote(str(args.output))} --dry-run",
                        "released": False,
                    }
                elif args.command == "dispatch":
                    row = snapshot(profile["repository"], args.pr)
                    _write(
                        args.output,
                        dispatch_plan(
                            profile,
                            repo,
                            row,
                            args.output.resolve().parent / f"pr-{args.pr}",
                        ),
                    )
                    result = {
                        "dispatch": str(args.output),
                        "next": f"vibecrafted dispatch {shlex.quote(str(args.output))} --dry-run",
                    }
                elif args.command == "verify":
                    row = snapshot(profile["repository"], args.pr)
                    _candidate(profile, row)
                    result = verify_candidate(profile, repo, row, args.output)
                    if _identity(snapshot(profile["repository"], args.pr)) != _identity(
                        row
                    ):
                        raise ValueError(
                            "PR changed during verification; receipt is stale"
                        )
                    if result["status"] != "pass":
                        print(json.dumps(result, indent=2))
                        return 1
                elif args.command == "publish":
                    result = publish(profile, repo, args.pr, args.receipt)
                else:
                    result = admission(
                        profile,
                        repo,
                        args.pr,
                        args.receipt,
                        enable=args.enable_auto_merge,
                        batch=json.loads(args.batch.read_text())
                        if args.batch
                        else None,
                    )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (
        ValueError,
        TypeError,
        ValidationError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"pr-closure blocked: {exc}", file=sys.stderr)
        return 2
