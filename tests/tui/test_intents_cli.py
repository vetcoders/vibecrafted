from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / "vibecrafted-core/vibecrafted_core/skills"
SKILL = SKILLS / "vc-intents"
CLI = SKILL / "scripts/intents_cli.py"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    shard = tmp_path / "work/shards/s01"
    shard.mkdir(parents=True)
    (shard / "source.txt").write_text("Third-party cooking playback: boil the pasta.\n")
    return tmp_path / "work"


def cli(work: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), str(work), *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )


def plan(work: Path, stage: str) -> dict:
    result = cli(
        work,
        "plan",
        "--stage",
        stage,
        "--repo",
        str(REPO),
        "--host",
        "test",
        "--artifacts",
        str(work / "artifacts"),
    )
    assert result.returncode == 0, result.stderr
    return tomllib.loads(
        (work / f"artifacts/plans/intents-{stage}.test.dispatch.toml").read_text()
    )


@pytest.mark.parametrize(
    "payload,valid",
    [
        ("[]", True),
        ('[{"source":"source.txt","quote":"boil the pasta"}]', True),
        ("[", False),
        ("{}", False),
        ("null", False),
        ('"text"', False),
        ("[null]", False),
        ('["text"]', False),
        ("[{}]", False),
        ('[{"source":"source.txt"}]', False),
        ('[{"quote":"boil the pasta"}]', False),
        ('[{"source":7,"quote":"boil the pasta"}]', False),
        ('[{"source":"source.txt","quote":3}]', False),
        ('[{"source":"","quote":"boil the pasta"}]', False),
    ],
)
def test_extraction_plan_verifier_checks_shape_and_accepts_empty(
    work: Path, payload: str, valid: bool
) -> None:
    generated = plan(work, "extract")
    output = work / "extractions/s01.json"
    output.parent.mkdir()
    output.write_text(payload)
    verifier = generated["cuts"][0]["verify"][0]["run"].replace("{id}", "s01")
    result = subprocess.run(
        verifier, shell=True, capture_output=True, text=True, check=False
    )
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    if valid:
        assert "bad 0" in result.stdout


def test_generated_plans_do_not_assign_source_identity_or_authority(work: Path) -> None:
    extracted = plan(work, "extract")
    (work / "lanes").mkdir()
    (work / "lanes/_lanes.json").write_text(json.dumps({"counts": {"L1": 1}}))
    confronted = plan(work, "confront")
    (work / "queue.json").write_text(
        json.dumps(
            {
                "queue": [
                    {
                        "id": "s01-001",
                        "date": "2026-11-03",
                        "strength": 5,
                        "subject": "inne",
                        "disposition": "absent",
                        "kind": "decision",
                        "quote": "boil the pasta",
                        "topic": "Third-party cooking playback",
                    }
                ]
            }
        )
    )
    implemented = plan(work, "implement")
    for generated in (extracted, confronted, implemented):
        assert "founder" not in json.dumps(generated).lower()
        assert "kandyd" in generated["common"]["text"].lower()
    common = implemented["common"]["text"]
    assert "aktywne zadanie użytkownika" in common
    assert "sam cytat" in common
    prompt = implemented["cuts"][0]["prompt"]
    assert "Cytat źródłowy: „boil the pasta”" in prompt
    assert "Modelowa klasyfikacja: decision" in prompt
    assert "powiedział" not in prompt


def test_extraction_verifier_rejects_object_with_python_optimization(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generated = plan(work, "extract")
    output = work / "extractions/s01.json"
    output.parent.mkdir()
    output.write_text("{}")
    monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    verifier = generated["cuts"][0]["verify"][0]["run"].replace("{id}", "s01")
    result = subprocess.run(
        verifier, shell=True, capture_output=True, text=True, check=False
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "bad 1" in result.stdout


@pytest.mark.parametrize(
    "quote,valid", [("boil the pasta", True), ("invented request", False)]
)
def test_strict_quote_verifier_checks_text_not_speaker_or_relevance(
    work: Path, quote: str, valid: bool
) -> None:
    extractions = work / "extractions"
    extractions.mkdir()
    (extractions / "s01.json").write_text(
        json.dumps(
            [
                {
                    "source": "source.txt",
                    "quote": quote,
                    "kind": "decision",
                    "strength": 5,
                }
            ]
        )
    )
    result = cli(work, "verify-quotes", "--extractions", str(extractions), "--strict")
    assert (result.returncode == 0) is valid
    rows = json.loads((work / "intents.json").read_text())
    assert len(rows) == int(valid)
    if valid:
        assert (
            rows[0]["kind"] == "decision"
        )  # model classification, not proven authority
    else:
        assert "QUOTE NOT VERBATIM" in (work / "verify-quotes.problems.txt").read_text()


def test_skill_surfaces_keep_candidates_and_source_evidence_distinct() -> None:
    for root in (SKILL, SKILLS / "pl/vc-intents"):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".md", ".html", ".yaml", ".py"}:
                assert not re.search(
                    r"\bfounder\w*", path.read_text(), re.IGNORECASE
                ), path
        brief = (root / "references/extraction-brief.md").read_text()
        assert "[]" in brief
        assert "odtwarzane" in brief
        assert "klasyfikacje modelu" in brief
        assert "To NIE są wypowiedzi agenta" not in brief
    html = (SKILL / "assets/intents.html").read_text()
    assert "Klasyfikacja modelu" in html
    assert "by:'manual-review'" in html
    assert "overrides.manual-review.jsonl" in html


def test_decide_default_is_neutral_and_historical_by_values_survive(work: Path) -> None:
    # No identity registry: arbitrary explicit provenance remains supported.
    (work / "LEDGER.json").write_text(
        json.dumps(
            {
                "intents": [
                    {
                        "id": "s01-001",
                        "quote": "boil the pasta",
                        "kind": "decision",
                        "hosts": {},
                        "disposition": "absent",
                    }
                ],
                "hosts": [],
                "primary": "test",
            }
        )
    )
    imported = work / "decisions.jsonl"
    imported.write_text(
        json.dumps({"id": "s01-001", "to": "drop", "by": "historical-reviewer"}) + "\n"
    )
    result = cli(work, "decide", "--import-file", str(imported))
    assert result.returncode == 0, result.stderr
    rows = [
        json.loads(line) for line in (work / "overrides.jsonl").read_text().splitlines()
    ]
    assert rows[-1]["by"] == "historical-reviewer"
    env = os.environ.copy()
    env.pop("USER", None)
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            str(work),
            "decide",
            "--id",
            "s01-001",
            "--to",
            "drop",
            "--reason",
            "unrelated playback",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        json.loads((work / "overrides.jsonl").read_text().splitlines()[-1])["by"]
        == "manual-review"
    )
