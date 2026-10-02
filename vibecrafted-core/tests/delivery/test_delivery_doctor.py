from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from vibecrafted_core.delivery.doctor import (
    diagnose_file,
    diagnose_payload,
    main,
)


def valid_envelope_payload() -> dict[str, object]:
    return {
        "schema": "vibecrafted.execution-envelope.v1",
        "agent": "agy",
        "repo": "vetcoders/vibecrafted",
        "root": "/repo",
        "branch": "feat/reduce-wrong-assumptions",
        "expected_head": "a" * 40,
        "upstream_ref": "origin/feat/reduce-wrong-assumptions",
        "upstream_relation": {"ahead": 1, "behind": 0},
        "dirty_policy": "living-tree-scoped",
        "baseline_status_digest": "sha256:baseline",
        "protected_paths": ["protected.py"],
        "owned_paths": ["delivery/doctor.py"],
        "brief_path": "/brief.md",
        "brief_sha256": "sha256:brief",
    }


def valid_proof_payload() -> dict[str, object]:
    return {
        "schema": "vibecrafted.delivery-proof.v1",
        "id": "dpk-w5a-doctor",
        "execution_envelope_sha256": "sha256:envelope",
        "subject": {
            "producer_id": "vetcoders/vibecrafted",
            "public_surface": "uv run pytest python -m pytest tests/delivery/test_delivery_doctor.py",
            "expected_exit": 0,
        },
        "witness": {
            "input": "tests/delivery/test_delivery_doctor.py",
            "sha256": "sha256:witness",
            "expected_outcome": "full rejection matrix covered",
        },
        "oracle": {
            "producer_id": "oracle.independent",
            "argv": ["oracle", "--check"],
        },
        "assertion": {
            "kind": "pytest exit 0 AND collected > 0",
        },
        "negative_controls": [
            {
                "id": "red-first",
                "mutation": "run test before doctor.py exists",
                "expected": "pytest fails",
            }
        ],
        "delivery_scope": "checkout",
        "integration_target": None,
        "runtime_probes": [],
    }


def test_delivery_doctor_happy_path_valid_pair() -> None:
    report = diagnose_payload(valid_envelope_payload(), valid_proof_payload())
    assert report.ok is True
    assert report.errors == ()
    assert report.envelope is not None
    assert report.contract is not None


@pytest.mark.parametrize(
    ("mutator", "expected_path", "expected_msg"),
    [
        # 1. missing subject/public entrypoint
        (
            lambda env, prf: prf["subject"].pop("public_surface"),
            "subject.public_surface",
            "missing subject public entrypoint",
        ),
        # 2. missing witness digest AND missing digest-computation rule
        (
            lambda env, prf: prf["witness"].pop("sha256"),
            "witness.digest",
            "missing witness digest AND missing digest-computation rule",
        ),
        # 3. missing expected outcome/assertion
        (
            lambda env, prf: prf.pop("assertion"),
            "assertion",
            "missing expected outcome/assertion",
        ),
        # 4. zero negative controls
        (
            lambda env, prf: prf.update({"negative_controls": []}),
            "negative_controls",
            "zero negative controls",
        ),
        # 5. missing delivery scope
        (
            lambda env, prf: prf.pop("delivery_scope"),
            "delivery_scope",
            "missing delivery scope",
        ),
        # 6. envelope missing agent/repo/root/branch/HEAD/brief digest
        (
            lambda env, prf: env.pop("expected_head"),
            "envelope.expected_head",
            "missing expected_head",
        ),
        # 7. missing gate-tool producer
        (
            lambda env, prf: prf["subject"].pop("producer_id"),
            "subject.producer_id",
            "missing gate-tool producer",
        ),
        # 8. missing Living Tree policy (dirty_policy)
        (
            lambda env, prf: env.pop("dirty_policy"),
            "envelope.dirty_policy",
            "missing Living Tree policy (dirty_policy)",
        ),
    ],
)
def test_delivery_doctor_rejection_matrix(
    mutator: object, expected_path: str, expected_msg: str
) -> None:
    env = valid_envelope_payload()
    prf = valid_proof_payload()
    mutator(env, prf)

    report = diagnose_payload(env, prf)
    assert report.ok is False
    assert any(
        err.path == expected_path and expected_msg in err.message
        for err in report.errors
    ), report.errors


def test_delivery_doctor_t03_oracle_subject_tautology() -> None:
    env = valid_envelope_payload()
    prf = valid_proof_payload()
    # Set subject and oracle producer_id to be identical
    prf["oracle"] = {"producer_id": prf["subject"]["producer_id"]}

    report = diagnose_payload(env, prf)
    assert report.ok is False
    assert any("oracle_subject_tautology" in err.message for err in report.errors), (
        report.errors
    )


def test_delivery_doctor_decorative_oracle_accepted_when_valid() -> None:
    env = valid_envelope_payload()
    prf = valid_proof_payload()
    prf["oracle"] = None  # null oracle

    report = diagnose_payload(env, prf)
    assert report.ok is True
    assert report.errors == ()


def test_delivery_doctor_decorative_oracle_rejected_without_negative_controls() -> None:
    env = valid_envelope_payload()
    prf = valid_proof_payload()
    prf["oracle"] = None
    prf["negative_controls"] = []

    report = diagnose_payload(env, prf)
    assert report.ok is False
    assert any(err.path == "negative_controls" for err in report.errors)


def test_delivery_doctor_cli_valid_and_gutted_brief(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    valid_brief = tmp_path / "valid_brief.md"
    valid_yaml = yaml.dump(
        {
            "execution": valid_envelope_payload(),
            "proof": valid_proof_payload(),
        }
    )
    valid_brief.write_text(
        f"""# Valid Brief

```yaml
{valid_yaml}
```
""",
        encoding="utf-8",
    )

    gutted_brief = tmp_path / "gutted_brief.md"
    gutted_brief.write_text(
        """# Gutted Brief

No code blocks here.
""",
        encoding="utf-8",
    )

    # Valid brief run
    exit_code_valid = main([str(valid_brief)])
    captured_valid = capsys.readouterr()
    assert exit_code_valid == 0
    assert "delivery-doctor: ok" in captured_valid.out

    # Gutted brief run
    exit_code_gutted = main([str(gutted_brief)])
    captured_gutted = capsys.readouterr()
    assert exit_code_gutted == 1
    assert "brief: missing execution envelope code block" in captured_gutted.out

    # Gutted brief run with --json
    exit_code_json = main([str(gutted_brief), "--json"])
    captured_json = capsys.readouterr()
    assert exit_code_json == 1
    data = json.loads(captured_json.out)
    assert data["ok"] is False
    assert len(data["errors"]) > 0


def test_delivery_doctor_diagnose_file_json(tmp_path: Path) -> None:
    json_file = tmp_path / "contract.json"
    json_file.write_text(
        json.dumps(
            {
                "execution": valid_envelope_payload(),
                "proof": valid_proof_payload(),
            }
        ),
        encoding="utf-8",
    )
    report = diagnose_file(json_file)
    assert report.ok is True
    assert report.errors == ()
