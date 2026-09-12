"""Ownership contract gate for ADR-0002 (unified operator runtime doctrine).

Validates docs/adr/ownership-matrix.json: exactly one owner per truth domain,
bidirectional owner<->component consistency, and the presence of the
resume-lineage and checkout-free-install rules. The gate must reject any
matrix that smuggles in a second owner for a domain — either as a duplicate
domain entry or as a component claiming a domain it does not own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MATRIX_PATH = REPO_ROOT / "docs" / "adr" / "ownership-matrix.json"
INVALID_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ownership" / "second_owner_invalid.json"
)
OPERATOR_RUNTIME_DOCS = (
    REPO_ROOT / "docs" / "runtime" / "OMNI_OBSERVER_SLACK_GATEWAY.md",
    REPO_ROOT / "docs" / "public" / "server" / "slack-gateway.md",
)

REQUIRED_RULE_IDS = {
    "one-owner-per-domain",
    "checkout-free-install",
    "resume-lineage",
    "real-controls",
    "artifact-gates",
    "in-file-provenance",
}


class OwnershipViolation(AssertionError):
    """Raised when the ownership matrix breaks the one-owner doctrine."""


def _fail(message: str) -> None:
    raise OwnershipViolation(message)


def validate_ownership_matrix(matrix: dict[str, Any]) -> None:
    if matrix.get("schema_version") != "vibecrafted.ownership.v1":
        _fail(f"unknown schema_version: {matrix.get('schema_version')!r}")

    domains = matrix.get("domains")
    components = matrix.get("components")
    rules = matrix.get("rules")
    if not isinstance(domains, list) or not domains:
        _fail("matrix has no domains")
    if not isinstance(components, list) or not components:
        _fail("matrix has no components")
    if not isinstance(rules, list) or not rules:
        _fail("matrix has no rules")

    owners_by_domain: dict[str, str] = {}
    for domain in domains:
        domain_id = str(domain.get("id", "")).strip().lower()
        if not domain_id:
            _fail("domain without id")
        owner = domain.get("owner")
        if isinstance(owner, list):
            _fail(f"domain {domain_id!r} declares multiple owners: {owner!r}")
        if not isinstance(owner, str) or not owner.strip():
            _fail(f"domain {domain_id!r} has no owner")
        if domain_id in owners_by_domain:
            _fail(
                f"second owner for domain {domain_id!r}: "
                f"{owners_by_domain[domain_id]!r} vs {owner!r}"
            )
        owners_by_domain[domain_id] = owner
        if not str(domain.get("write_surface", "")).strip():
            _fail(f"domain {domain_id!r} has no write surface")
        projections = domain.get("read_projections")
        if not isinstance(projections, list) or not projections:
            _fail(f"domain {domain_id!r} has no read projections")
        if owner in projections:
            _fail(
                f"domain {domain_id!r}: owner {owner!r} listed as its own read projection"
            )

    component_ids: set[str] = set()
    claims_by_domain: dict[str, list[str]] = {}
    for component in components:
        component_id = str(component.get("id", "")).strip()
        if not component_id:
            _fail("component without id")
        if component_id in component_ids:
            _fail(f"duplicate component {component_id!r}")
        component_ids.add(component_id)
        owns = component.get("owns")
        if not isinstance(owns, list):
            _fail(f"component {component_id!r} has no owns list")
        for claimed in owns:
            claimed_id = str(claimed).strip().lower()
            if claimed_id not in owners_by_domain:
                _fail(f"component {component_id!r} claims unknown domain {claimed!r}")
            if owners_by_domain[claimed_id] != component_id:
                _fail(
                    f"second owner for domain {claimed_id!r}: matrix says "
                    f"{owners_by_domain[claimed_id]!r}, component {component_id!r} claims it"
                )
            claims_by_domain.setdefault(claimed_id, []).append(component_id)

    for domain_id, owner in owners_by_domain.items():
        if owner not in component_ids:
            _fail(f"domain {domain_id!r} owner {owner!r} is not a declared component")
        if claims_by_domain.get(domain_id) != [owner]:
            _fail(
                f"domain {domain_id!r}: owner {owner!r} does not claim it exactly once "
                f"(claims: {claims_by_domain.get(domain_id)!r})"
            )

    rules_by_id = {str(rule.get("id", "")).strip(): rule for rule in rules}
    missing = REQUIRED_RULE_IDS - set(rules_by_id)
    if missing:
        _fail(f"missing required rules: {sorted(missing)}")

    checkout_rule = rules_by_id["checkout-free-install"]
    patterns = checkout_rule.get("forbidden_path_patterns")
    if not isinstance(patterns, list) or not patterns:
        _fail("checkout-free-install rule has no forbidden_path_patterns")
    if "**/vc-workspace/**" not in patterns:
        _fail(
            "checkout-free-install must forbid vc-workspace checkouts without pinning a host root"
        )

    resume_rule = rules_by_id["resume-lineage"]
    if resume_rule.get("semantics") != "lineage-preserving-attempt":
        _fail("resume-lineage semantics must be 'lineage-preserving-attempt'")
    if resume_rule.get("persona_replacement") != "forbidden":
        _fail("resume-lineage must forbid silent persona replacement")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_canonical_matrix_is_valid() -> None:
    validate_ownership_matrix(_load(MATRIX_PATH))


def test_gate_rejects_second_owner_fixture() -> None:
    with pytest.raises(
        OwnershipViolation, match="second owner for domain 'run-lifecycle'"
    ):
        validate_ownership_matrix(_load(INVALID_FIXTURE))


def test_gate_rejects_component_claiming_foreign_domain() -> None:
    matrix = _load(MATRIX_PATH)
    for component in matrix["components"]:
        if component["id"] == "vibecrafted-server":
            component["owns"].append("run-lifecycle")
    with pytest.raises(
        OwnershipViolation, match="second owner for domain 'run-lifecycle'"
    ):
        validate_ownership_matrix(matrix)


def test_gate_rejects_missing_resume_rule() -> None:
    matrix = _load(MATRIX_PATH)
    matrix["rules"] = [
        rule for rule in matrix["rules"] if rule["id"] != "resume-lineage"
    ]
    with pytest.raises(OwnershipViolation, match="missing required rules"):
        validate_ownership_matrix(matrix)


def test_operator_docs_do_not_advise_checkout_linked_install() -> None:
    forbidden_recipes = (
        "ln -sfn <repo>",
        "ln -sfn /Volumes/vc-workspace",
    )
    for path in OPERATOR_RUNTIME_DOCS:
        body = path.read_text(encoding="utf-8")
        for recipe in forbidden_recipes:
            assert recipe not in body, (
                f"{path.relative_to(REPO_ROOT)} recommends checkout-linked "
                f"installation: {recipe}"
            )
