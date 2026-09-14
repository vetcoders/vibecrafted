"""Contract tests for ``examples/dispatch/parallel-worktrees.dispatch.toml``.

The example is documentation that runs: it must load through the real parser
and the real doctor, its verifiers must be gates this repository actually has,
and its parallel shape (three disjoint scopes, two pinned providers, no
integrator, no ordering, no repair rounds) must hold.

Nothing here loosens the parser or the doctor. Rules the schema deliberately
does not enforce — an explicit model pin, a per-agent pin, "no worker
integrator" — are the *example's* contract and are checked by
``parallel_contract_violations`` against the parsed plan.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core.dispatch import cli as dispatch_cli
from vibecrafted_core.dispatch.doctor import diagnose_text
from vibecrafted_core.dispatch.model import Dispatch
from vibecrafted_core.dispatch.schema import parse_dispatch

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "examples" / "dispatch" / "parallel-worktrees.dispatch.toml"
REPO_PLACEHOLDER = "/absolute/path/to/repository"

# The pin each provider is dispatched with. Both must appear, or the example
# stops demonstrating a two-provider fleet.
EXPECTED_PINS = {"claude": "claude-opus-5", "codex": "gpt-5.6-terra"}

# Repository-relative surfaces each cut owns. Disjointness of these sets is
# what makes the cuts safe to run concurrently.
EXPECTED_SCOPES = {
    "verifier-env": (
        "vibecrafted-core/vibecrafted_core/dispatch/verify.py",
        "vibecrafted-core/tests/dispatch/test_verify.py",
    ),
    "shell-dialect-gate": (
        "scripts/check_shell.py",
        "scripts/lib",
    ),
    "control-core-lint": ("vibecrafted-server/control-core/src",),
}

# A verifier whose command starts with one of these proves nothing about the
# cut; the example must never degrade into a green-by-construction check.
INERT_COMMANDS = frozenset({"echo", "true", ":", "printf", "cat", "test"})


def parallel_contract_violations(dispatch: Dispatch) -> tuple[str, ...]:
    """Return every way ``dispatch`` fails the parallel-example contract."""
    violations: list[str] = []
    cuts = dispatch.cuts

    if len(cuts) != 3:
        violations.append(f"cuts: exactly 3 parallel cuts required, found {len(cuts)}")
    if dispatch.policy.concurrency != len(cuts):
        violations.append(
            f"policy.concurrency: must equal the cut count {len(cuts)}, "
            f"found {dispatch.policy.concurrency}"
        )
    if not dispatch.policy.allow_concurrency:
        violations.append("policy.allow_concurrency: must be true")
    if not dispatch.policy.require_commit:
        violations.append("policy.require_commit: must be true")
    if dispatch.policy.repair_rounds != 0:
        violations.append(
            "policy.repair_rounds: automatic repair rounds are forbidden, "
            f"found {dispatch.policy.repair_rounds}"
        )

    for index, cut in enumerate(cuts):
        if not cut.model:
            violations.append(f"cuts[{index}].model: explicit pin required")
        elif EXPECTED_PINS.get(cut.agent) != cut.model:
            violations.append(
                f"cuts[{index}].model: {cut.model!r} is not the pin for "
                f"agent {cut.agent!r}"
            )
        if cut.integrator:
            violations.append(
                f"cuts[{index}].integrator: the root integrates, not a worker"
            )
        if cut.depends_on:
            violations.append(
                f"cuts[{index}].depends_on: parallel cuts carry no ordering, "
                f"found {list(cut.depends_on)}"
            )
        if not cut.verify:
            violations.append(f"cuts[{index}].verify: at least one verifier required")
        for position, verify in enumerate(cut.verify):
            prefix = f"cuts[{index}].verify[{position}]"
            if not verify.matchers:
                violations.append(f"{prefix}.expect: at least one matcher required")
            head = shlex.split(verify.run)[0] if verify.run.strip() else ""
            if Path(head).name in INERT_COMMANDS:
                violations.append(f"{prefix}.run: inert command {verify.run!r}")

    if {cut.agent for cut in cuts} != set(EXPECTED_PINS):
        violations.append(
            f"cuts.agent: both providers required, found "
            f"{sorted({cut.agent for cut in cuts})}"
        )
    return tuple(violations)


@pytest.fixture(autouse=True)
def _no_ambient_cargo_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop an inherited ``CARGO_TARGET_DIR``.

    A dispatched worker runs with ``CARGO_TARGET_DIR`` exported, and the doctor
    refuses any concurrent plan while it is set — the target dir must be
    per-worker. Without this the suite would pass in a bare shell and fail
    inside the product's own runtime.
    """
    monkeypatch.delenv("CARGO_TARGET_DIR", raising=False)


@pytest.fixture
def bound_checkout(tmp_path: Path) -> Path:
    """A controlled git checkout the example is bound to instead of a live repo."""
    checkout = tmp_path / "bound-checkout"
    checkout.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "parallel-baseline"],
        ["git", "config", "user.email", "agents@vetcoders.io"],
        ["git", "config", "user.name", "claude"],
    ):
        subprocess.run(args, cwd=checkout, check=True)
    (checkout / "README.md").write_text("bound\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=checkout, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=checkout, check=True)
    return checkout


def bind(text: str, checkout: Path) -> str:
    """Bind example text to ``checkout`` the way the parser's caller must."""
    assert REPO_PLACEHOLDER in text, "example lost its repo placeholder"
    return text.replace(REPO_PLACEHOLDER, str(checkout.resolve()))


def mutate(text: str, old: str, new: str) -> str:
    """Rewrite one literal of the example, failing loudly if it moved."""
    assert old in text, f"example no longer contains {old!r}"
    return text.replace(old, new, 1)


@pytest.fixture
def example_text() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


@pytest.fixture
def bound_text(example_text: str, bound_checkout: Path) -> str:
    return bind(example_text, bound_checkout)


def test_example_parses_and_doctors_clean_in_a_bound_checkout(
    bound_text: str, bound_checkout: Path
) -> None:
    report = diagnose_text(bound_text, base_dir=EXAMPLE.parent)

    assert report.errors == ()
    assert report.ok is True
    assert report.dispatch is not None
    assert report.dispatch.schema == "vibecrafted.dispatch.v1"
    assert report.dispatch.meta.repo == str(bound_checkout.resolve())


def test_example_satisfies_the_parallel_contract(bound_text: str) -> None:
    dispatch = parse_dispatch(bound_text, base_dir=EXAMPLE.parent)

    assert parallel_contract_violations(dispatch) == ()


def test_example_pins_both_providers_and_the_doctor_reports_every_pin(
    bound_text: str,
) -> None:
    report = diagnose_text(bound_text, base_dir=EXAMPLE.parent)
    assert report.dispatch is not None
    cuts = report.dispatch.cuts

    assert [cut.agent for cut in cuts].count("codex") == 2
    assert [cut.agent for cut in cuts].count("claude") == 1
    assert {cut.model for cut in cuts} == set(EXPECTED_PINS.values())
    # Every pin is unvalidated by definition, so the doctor must surface all
    # three — a silent pin is a pin nobody reviewed.
    assert [warning.path for warning in report.warnings] == [
        "cuts[0].model",
        "cuts[1].model",
        "cuts[2].model",
    ]


def test_example_scopes_are_real_and_disjoint(bound_text: str) -> None:
    dispatch = parse_dispatch(bound_text, base_dir=EXAMPLE.parent)

    assert [cut.id for cut in dispatch.cuts] == list(EXPECTED_SCOPES)
    for cut in dispatch.cuts:
        for scope in EXPECTED_SCOPES[cut.id]:
            assert (REPO_ROOT / scope).exists(), f"{cut.id} scopes a missing {scope}"
            assert scope in cut.prompt, f"{cut.id} never names its scope {scope}"

    owned = [
        (cut.id, scope) for cut in dispatch.cuts for scope in EXPECTED_SCOPES[cut.id]
    ]
    for left_id, left in owned:
        for right_id, right in owned:
            if left_id == right_id:
                continue
            assert not f"{left}/".startswith(f"{right}/"), (
                f"{left_id} scope {left} overlaps {right_id} scope {right}"
            )


def test_verifiers_are_real_repository_gates(bound_text: str) -> None:
    dispatch = parse_dispatch(bound_text, base_dir=EXAMPLE.parent)

    for cut in dispatch.cuts:
        for verify in cut.verify:
            tokens = shlex.split(verify.run)
            named_paths = [token for token in tokens if "/" in token]
            assert named_paths, f"{cut.id}: {verify.run!r} names no repository path"
            for token in named_paths:
                assert (REPO_ROOT / token).exists(), (
                    f"{cut.id}: verifier references a missing {token}"
                )
            assert any(
                matcher.kind == "exit_code" and matcher.expected == 0
                for matcher in verify.matchers
            ), f"{cut.id}: {verify.run!r} declares no success condition"


def test_dry_run_binds_baseline_and_renders_report_and_commit_duties(
    bound_text: str,
    bound_checkout: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bound_file = tmp_path / "parallel-worktrees.dispatch.toml"
    bound_file.write_text(bound_text, encoding="utf-8")

    code = dispatch_cli.main([str(bound_file), "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=bound_checkout,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # meta.repo is the whole binding: the baseline is read off the bound
    # checkout, never taken from the file.
    assert payload["baseline"]["head"] == head
    assert payload["baseline"]["branch"] == "parallel-baseline"
    assert payload["cuts"] == list(EXPECTED_SCOPES)

    for cut_id, prompt_path in payload["prompts"].items():
        prompt = Path(prompt_path).read_text(encoding="utf-8")
        assert str(bound_checkout.resolve()) in prompt, f"{cut_id} lost its repo"
        assert "VIBECRAFTED_REPORT_PATH" in prompt
        assert "DELIVERY CONTRACT" in prompt
        assert f"'{cut_id}'" in prompt


def test_doctor_rejects_the_example_without_concurrency_permission(
    bound_text: str,
) -> None:
    text = mutate(bound_text, "allow_concurrency = true\n", "")

    report = diagnose_text(text, base_dir=EXAMPLE.parent)

    assert report.ok is False
    assert any(
        error.path == "policy.concurrency" and "allow_concurrency" in error.message
        for error in report.errors
    ), report.errors


def test_doctor_rejects_a_hard_stop_verifier_in_the_example(bound_text: str) -> None:
    text = mutate(
        bound_text,
        "scripts/project-python scripts/check_shell.py",
        "make release",
    )

    report = diagnose_text(text, base_dir=EXAMPLE.parent)

    assert report.ok is False
    assert any(
        error.path.endswith(".run") and "hard-stop" in error.message
        for error in report.errors
    ), report.errors


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param(
            'model = "claude-opus-5"\n',
            "",
            "cuts[0].model: explicit pin required",
            id="missing-pin",
        ),
        pytest.param(
            'model = "claude-opus-5"',
            'model = "gpt-5.6-terra"',
            "is not the pin for agent 'claude'",
            id="pin-crossed-with-wrong-provider",
        ),
        pytest.param(
            'id = "control-core-lint"',
            'id = "control-core-lint"\nintegrator = true',
            "the root integrates, not a worker",
            id="worker-integrator",
        ),
        pytest.param(
            'id = "control-core-lint"',
            'id = "control-core-lint"\ndepends_on = ["verifier-env"]',
            "parallel cuts carry no ordering",
            id="ordering-between-cuts",
        ),
        pytest.param(
            "repair_rounds = 0",
            "repair_rounds = 2",
            "automatic repair rounds are forbidden",
            id="automatic-repair-rounds",
        ),
        pytest.param(
            "concurrency = 3",
            "concurrency = 1",
            "must equal the cut count 3",
            id="serialized-fleet",
        ),
        pytest.param(
            "bash -n scripts/lib/runtime-roots.sh",
            "echo ok",
            "inert command",
            id="echo-ok-verifier",
        ),
    ],
)
def test_contract_rejects_a_degraded_example(
    bound_text: str, old: str, new: str, expected: str
) -> None:
    dispatch = parse_dispatch(mutate(bound_text, old, new), base_dir=EXAMPLE.parent)

    violations = parallel_contract_violations(dispatch)

    assert any(expected in violation for violation in violations), violations
