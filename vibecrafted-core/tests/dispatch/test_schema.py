from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from vibecrafted_core.dispatch.model import (
    STATE_VERIFIED,
    Baton,
    CutState,
    Matcher,
    Verdict,
    VerifierEvidence,
)
from vibecrafted_core.dispatch.schema import (
    DispatchSchemaError,
    doctor_dispatch,
    load_dispatch,
    parse_dispatch,
    render_cell_prompt,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_minimal_fixture_parses_into_typed_model() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")

    assert dispatch.schema == "vibecrafted.dispatch.v1"
    assert dispatch.meta.name == "dispatch-minimal"
    assert dispatch.policy.concurrency == 1
    assert dispatch.phases[0].title == "Foundation"
    assert dispatch.cuts[0].id == "d1-schema-parser"
    assert dispatch.cuts[0].verify[0].matchers[0].kind == "contains"


def test_stage0_maximum_fixture_is_accepted() -> None:
    dispatch = load_dispatch(FIXTURES / "stage0.dispatch.toml")

    assert dispatch.meta.name == "d1-schema-parser"
    assert dispatch.policy.await_config == {"poll_s": 90, "timeout_min": 90}
    assert dispatch.workflow_map["audit"] == "review"
    assert len(dispatch.cuts) == 7
    assert dispatch.cuts[0].brief.endswith("stage0-brief.md")
    assert dispatch.cuts[1].resolved_workflow == "review"
    assert dispatch.cuts[1].mutation == ""
    assert dispatch.cuts[1].observational is True
    assert dispatch.cuts[6].mode == "read"
    assert dispatch.cuts[6].mutation == ""


def test_cut_model_pin_parses_and_defaults_empty_when_absent() -> None:
    dispatch = load_dispatch(FIXTURES / "model-pin.dispatch.toml")

    assert dispatch.cuts[0].id == "d1-pinned"
    assert dispatch.cuts[0].model == "test-codex-model"
    # An unpinned cut carries no pin: empty string, plan still valid.
    assert dispatch.cuts[1].id == "d2-unpinned"
    assert dispatch.cuts[1].model == ""


def test_plan_without_any_model_pin_stays_valid() -> None:
    # Backward-compatibility regression: the minimal fixture predates the
    # `model` key; it must parse unchanged with an empty pin on every cut.
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")

    assert all(cut.model == "" for cut in dispatch.cuts)


def test_prompt_rendering_substitutes_common_body_extra_and_empty_baton() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    prompt = render_cell_prompt(dispatch, dispatch.cuts[0])

    assert "Repo: /tmp/vibecrafted-dispatch-fixture" in prompt
    assert "Implement parser cut d1-schema-parser" in prompt
    assert "Use workflow implement" in prompt
    assert '"last": null' in prompt
    assert '"states": []' in prompt
    assert '"verified": 0' in prompt
    assert '"total": 1' in prompt
    assert '"ratio": 0.0' in prompt


def test_prompt_rendering_substitutes_previous_verdict_baton() -> None:
    dispatch = load_dispatch(FIXTURES / "stage0.dispatch.toml")
    verdict = Verdict(
        cut_id="d1-schema-parser",
        phase="Foundation",
        state=STATE_VERIFIED,
        commit="abc1234",
        report="/tmp/d1.md",
        verifiers=(
            VerifierEvidence(
                command="make dispatch-test",
                ok=True,
                exit_code=0,
                evidence="1 passed",
                elapsed_ms=123,
                timestamp="2026-06-10T20:30:00-07:00",
            ),
        ),
    )
    baton = Baton(
        last=verdict,
        states=(CutState.from_verdict(verdict),),
        total=len(dispatch.cuts),
    )

    prompt = render_cell_prompt(dispatch, dispatch.cuts[1], baton=baton)

    assert '"cut_id": "d1-schema-parser"' in prompt
    assert '"commit": "abc1234"' in prompt
    assert '"verified": 1' in prompt
    assert '"total": 7' in prompt
    assert '"ratio": 0.14285714285714285' in prompt


def test_required_commit_contract_is_rendered_with_exact_cut_identity() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    dispatch = replace(dispatch, policy=replace(dispatch.policy, require_commit=True))

    prompt = render_cell_prompt(dispatch, dispatch.cuts[0])

    assert "DELIVERY CONTRACT (supervisor-enforced)" in prompt
    assert "exact cut id 'd1-schema-parser'" in prompt
    assert "slot marker '[d1-schema-parser]'" in prompt
    assert "Commit: <sha>" in prompt


def test_commit_contract_is_absent_when_not_required_or_cut_is_read() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    assert "DELIVERY CONTRACT" not in render_cell_prompt(dispatch, dispatch.cuts[0])

    required = replace(dispatch, policy=replace(dispatch.policy, require_commit=True))
    read_cut = replace(required.cuts[0], mode="read")
    assert "DELIVERY CONTRACT" not in render_cell_prompt(required, read_cut)


def test_claim_prompt_uses_dispatch_identity_and_runtime_report_contract() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    run_id = 'disp-test-"quoted"'
    prompt = render_cell_prompt(dispatch, dispatch.cuts[0], run_id=run_id)
    payload = json.loads(prompt.split("```json\n", 1)[1].split("\n```", 1)[0])

    assert payload["run_id"] == run_id
    assert payload["cut_id"] == dispatch.cuts[0].id
    assert payload["report_path"] == "$VIBECRAFTED_REPORT_PATH"
    assert set(payload) == {
        "run_id",
        "cut_id",
        "commit_sha",
        "report_path",
        "measurements",
    }
    assert "http://127.0.0.1:3024/api/dispatch/claim" in prompt
    assert "distinct from the worker runtime's VIBECRAFTED_RUN_ID" in prompt
    assert "canonical Python writer records [~]" in prompt
    assert "Never write tracker [x]" in prompt
    assert "Only a passed full VERIFICATION_RULE and all green matchers" in prompt
    assert "Default verifier timeout is 600s" in prompt
    assert "DEVELOPER_DIR" in prompt
    assert "claim not received, verifiers were not run" in prompt


def test_claim_closing_rail_overrides_stale_brief_checkbox_instructions() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    cut = replace(dispatch.cuts[0], source_text="Flip brief Acceptance to [x].")

    prompt = render_cell_prompt(dispatch, cut, run_id="disp-test")

    assert prompt.index("Flip brief Acceptance") < prompt.index("Never write tracker")
    assert "even if older brief text requests it" in prompt
    assert "An unflipped [ ] cannot skip verifiers or become a failed test" in prompt
    assert "--allow-red-baseline admits work and does not count as delivery" in prompt


def test_claim_preview_never_guesses_dispatch_run_identity() -> None:
    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")

    prompt = render_cell_prompt(dispatch, dispatch.cuts[0])

    assert "Do not POST an empty or guessed identity" in prompt


def _embargo_plan() -> str:
    return (
        (FIXTURES / "minimal.dispatch.toml")
        .read_text()
        .replace("critical = true", "critical = true\ncompile_embargo = true")
        + """

[[cuts]]
id = "w2-integrator"
phase = "Foundation"
agent = "codex"
workflow = "implement"
prompt = "Assemble, then run full gates."
integrator = true
depends_on = ["d1-schema-parser"]
closes_embargo = ["d1-schema-parser"]

[[cuts.verify]]
run = "python -m pytest -q"
expect = {contains = "passed", exit_code = 0}
"""
    )


def test_typed_embargo_plan_and_closure_render_phase_obligations() -> None:
    dispatch = parse_dispatch(_embargo_plan())
    checkpoint, integrator = dispatch.cuts

    assert checkpoint.compile_embargo is True
    assert checkpoint.closes_embargo == ()
    assert integrator.compile_embargo is False
    assert integrator.closes_embargo == (checkpoint.id,)
    checkpoint_prompt = render_cell_prompt(dispatch, checkpoint, run_id="disp-test")
    assert "This cut must POST an unverified checkpoint" in checkpoint_prompt
    assert "Include security and secret controls" in checkpoint_prompt
    assert "exact plan-declared verifier command" in checkpoint_prompt
    assert "writer never executes payload commands" in checkpoint_prompt
    assert "A worker cannot close embargo through this POST" in checkpoint_prompt
    closure_prompt = render_cell_prompt(dispatch, integrator, run_id="disp-test")
    assert "PLAN INTEGRATOR CLOSURE" in closure_prompt
    assert "exact assembled SHA in measurements" in closure_prompt
    assert "independently runs full gates against that SHA" in closure_prompt


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("compile_embargo = true", 'compile_embargo = "yes"', "expected a boolean"),
        ("integrator = true", "integrator = false", "only a WRITE integrator"),
        (
            "integrator = true",
            "integrator = true\ncompile_embargo = true",
            "only structural WRITE workers",
        ),
        (
            'closes_embargo = ["d1-schema-parser"]',
            'closes_embargo = ["unknown"]',
            "must name a compile_embargo cut",
        ),
        (
            "compile_embargo = true",
            "compile_embargo = false",
            "must name a compile_embargo cut",
        ),
        (
            'depends_on = ["d1-schema-parser"]',
            "depends_on = []",
            "must be declared in depends_on",
        ),
    ],
)
def test_embargo_plan_refuses_worker_closure_or_unbound_checkpoints(
    old: str, new: str, error: str
) -> None:
    with pytest.raises(DispatchSchemaError, match=error):
        parse_dispatch(_embargo_plan().replace(old, new))


def test_matchers_cover_required_set() -> None:
    output = "test result: ok. 3 passed"

    assert Matcher("contains", "3 passed").check(output, exit_code=0)
    assert Matcher("equals", output).check(output, exit_code=0)
    assert Matcher("matches", r"\d+ passed").check(output, exit_code=0)
    assert Matcher("not_contains", "FAILED").check(output, exit_code=0)
    assert Matcher("exit_code", 0).check(output, exit_code=0)
    assert not Matcher("exit_code", 1).check(output, exit_code=0)


def test_doctor_reports_schema_errors_without_throwing() -> None:
    result = doctor_dispatch("schema = 'wrong'")

    assert result.ok is False
    assert result.dispatch is None
    assert any("unsupported schema" in error for error in result.errors)


def test_parser_accepts_read_cut_without_mutation_for_doctor_policy() -> None:
    text = (FIXTURES / "stage0.dispatch.toml").read_text(encoding="utf-8")

    dispatch = parse_dispatch(text, base_dir=FIXTURES)
    result = doctor_dispatch(text, base_dir=FIXTURES)

    assert len(dispatch.cuts) == 7
    assert dispatch.cuts[6].mode == "read"
    assert dispatch.cuts[6].mutation == ""
    assert result.ok is False
    assert result.dispatch is not None
    assert "cuts[1].mutation: required for READ cuts" in result.errors
    assert "cuts[6].mutation: required for READ cuts" in result.errors


def test_non_destructive_feature_branch_push_is_a_legal_verifier() -> None:
    text = (FIXTURES / "minimal.dispatch.toml").read_text(encoding="utf-8")
    text = text.replace(
        'run = "python -m pytest vibecrafted-core/tests/dispatch -q"',
        'run = "git push origin HEAD"',
    )

    dispatch = parse_dispatch(text, base_dir=FIXTURES)

    assert dispatch.cuts[0].verify[0].run == "git push origin HEAD"


def test_rejects_unsupported_read_mutation_at_parse_time() -> None:
    text = (FIXTURES / "stage0.dispatch.toml").read_text(encoding="utf-8")
    text = text.replace(
        'mode = "read"\nobservational = true',
        'mode = "read"\nmutation = "teleport"\nobservational = true',
    )

    with pytest.raises(DispatchSchemaError) as exc:
        parse_dispatch(text, base_dir=FIXTURES)

    assert "cuts[1].mutation: unsupported value 'teleport'" in exc.value.errors


def test_rejects_duplicate_cut_ids_and_missing_verify() -> None:
    text = (FIXTURES / "minimal.dispatch.toml").read_text(encoding="utf-8")
    text += """

[[cuts]]
id = "d1-schema-parser"
phase = "Foundation"
agent = "codex"
workflow = "implement"
prompt = "duplicate"
"""

    with pytest.raises(DispatchSchemaError) as exc:
        parse_dispatch(text, base_dir=FIXTURES)

    errors = "\n".join(exc.value.errors)
    assert "duplicate cut id 'd1-schema-parser'" in errors
    assert "verify: required unless observational READ is explicit" in errors


def test_verifier_rendering_substitutes_placeholders_and_keeps_matchers() -> None:
    from vibecrafted_core.dispatch.model import Verify
    from vibecrafted_core.dispatch.schema import render_cut_verifies

    dispatch = load_dispatch(FIXTURES / "minimal.dispatch.toml")
    cut = replace(
        dispatch.cuts[0],
        verify=(
            Verify(
                run="cd {repo} && grep -c ready {reports_dir}/{id}_report.md",
                expect={"matches": "^[1-9]"},
            ),
        ),
    )

    rendered = render_cut_verifies(dispatch, cut)

    assert rendered.verify[0].run == (
        f"cd {dispatch.meta.repo} && grep -c ready"
        f" {dispatch.meta.reports_dir}/{cut.id}_report.md"
    )
    assert "{" not in rendered.verify[0].run
    assert rendered.verify[0].matchers == cut.verify[0].matchers
