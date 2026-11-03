from __future__ import annotations

import json
import os
import re
import shutil
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


def plan(work: Path, stage: str, lang: str = "en") -> dict:
    result = cli(
        work,
        "--lang",
        lang,
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
@pytest.mark.parametrize("lang", ["en", "pl"])
def test_extraction_plan_verifier_checks_shape_and_accepts_empty(
    work: Path, payload: str, valid: bool, lang: str
) -> None:
    generated = plan(work, "extract", lang)
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


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_generated_plans_do_not_assign_source_identity_or_authority(
    work: Path, lang: str
) -> None:
    extracted = plan(work, "extract", lang)
    (work / "lanes").mkdir()
    (work / "lanes/_lanes.json").write_text(json.dumps({"counts": {"L1": 1}}))
    confronted = plan(work, "confront", lang)
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
    implemented = plan(work, "implement", lang)
    for generated in (extracted, confronted, implemented):
        assert "founder" not in json.dumps(generated).lower()
        assert ("candidate" if lang == "en" else "kandyd") in generated["common"][
            "text"
        ].lower()
    common = implemented["common"]["text"]
    assert (
        "active user task" if lang == "en" else "aktywne zadanie użytkownika"
    ) in common
    assert ("source quote" if lang == "en" else "sam cytat") in common
    prompt = implemented["cuts"][0]["prompt"]
    assert (
        "Source quote: “boil the pasta”"
        if lang == "en"
        else "Cytat źródłowy: „boil the pasta”"
    ) in prompt
    assert (
        "Model classification: decision"
        if lang == "en"
        else "Modelowa klasyfikacja: decision"
    ) in prompt
    assert "powiedział" not in prompt


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_extraction_verifier_rejects_object_with_python_optimization(
    work: Path, monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    generated = plan(work, "extract", lang)
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
            if path.is_file() and path.suffix in {
                ".md",
                ".html",
                ".yaml",
                ".py",
                ".json",
            }:
                assert not re.search(
                    r"\bfounder\w*", path.read_text(), re.IGNORECASE
                ), path
        brief = (root / "references/extraction-brief.md").read_text()
        assert "[]" in brief
        assert ("playback" if root == SKILL else "odtwarzane") in brief
        assert (
            "model classifications" if root == SKILL else "klasyfikacje modelu"
        ) in brief
        assert "To NIE są wypowiedzi agenta" not in brief
    html = (SKILL / "assets/intents.html").read_text()
    for lang, root in (("en", SKILL), ("pl", SKILLS / "pl/vc-intents")):
        copy = json.loads((root / "assets/copy.json").read_text())
        assert (
            "Model classification" if lang == "en" else "Klasyfikacja modelu"
        ) in copy["ui"]["candidate_note"]
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


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_locale_selects_prompts_briefs_and_preserves_source(
    work: Path, lang: str
) -> None:
    # English is the default; PL explicitly selects the matching subtree.
    for stage in ("extract", "confront", "implement"):
        if stage == "confront":
            (work / "lanes").mkdir()
            (work / "lanes/_lanes.json").write_text(json.dumps({"counts": {"L1": 1}}))
        if stage == "implement":
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
                                "quote": "Zażółć gęślą: Search __UI_search__ {brief} __HOST0_JSON__ __PROJECT__ __DATA__ Twoja decyzja.",
                                "topic": "Synthetic quoted UI text",
                            }
                        ]
                    }
                )
            )
        args = ["--lang", "pl"] if lang == "pl" else []
        result = cli(
            work,
            *args,
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
        generated = tomllib.loads(
            (work / f"artifacts/plans/intents-{stage}.test.dispatch.toml").read_text()
        )
        common = generated["common"]["text"]
        prompt = generated["cuts"][0]["prompt"]
        assert ("MODE:" if lang == "en" else "TRYB:") in common
        if stage != "implement":
            root = SKILL if lang == "en" else SKILLS / "pl/vc-intents"
            brief = (
                root
                / f"{'extraction' if stage == 'extract' else 'confrontation'}-brief.md"
            )
            brief = root / "references" / brief.name
            assert str(brief) in common
            assert ("Final report" if lang == "en" else "Raport końcowy") in prompt
            assert ("in English" if lang == "en" else "po polsku") in prompt
        else:
            assert (
                "Zażółć gęślą: Search __UI_search__ {brief} __HOST0_JSON__ __PROJECT__ __DATA__ Twoja decyzja."
                in prompt
            )


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_locale_renders_review_ledger_and_queue_without_translating_evidence(
    work: Path, lang: str
) -> None:
    quote = "Zażółć gęślą: Search __UI_search__ {brief} __HOST0_JSON__ __PROJECT__ __DATA__ Twoja decyzja."
    item = {
        "id": "s01-001",
        "date": "2026-11-03",
        "source": "source.txt",
        "quote": quote,
        "topic": "Synthetic source",
        "kind": "decision",
        "strength": 5,
        "subject": "inne",
        "disposition": "absent",
        "stable": True,
        "verdicts": {"test": {"disposition": "absent", "evidence": "core.py:1"}},
        "effective_by_host": {"test": "absent"},
        "overrides": [],
    }
    (work / "LEDGER.json").write_text(
        json.dumps(
            {
                "intents": [item],
                "hosts": ["test"],
                "primary": "test",
                "project": "synthetic/project",
                "updated": "2026-11-03",
            }
        )
    )
    args = ["--lang", "pl"] if lang == "pl" else []
    for command in ("render", "queue"):
        result = cli(work, *args, command, *(["--html"] if command == "render" else []))
        assert result.returncode == 0, result.stderr
    html = (work / "intents.html").read_text()
    assert f'<html lang="{lang}">' in html
    ledger = (work / "LEDGER.md").read_text()
    queue = (work / "STABLE-QUEUE.md").read_text()
    assert ("<h2>Search</h2>" if lang == "en" else "<h2>Szukaj</h2>") in html
    assert ("# Intent ledger" if lang == "en" else "# Rejestr intencji") in ledger
    assert (
        "# Stable open queue" if lang == "en" else "# Stabilna otwarta kolejka"
    ) in queue
    for body in (html, ledger, queue):
        assert quote in body
    data = re.search(
        r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL
    )
    assert json.loads(data.group(1))[0]["quote"] == quote
    node = shutil.which("node")
    assert node, "Node is required to validate the generated shared review script"
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    checked = subprocess.run(
        [node, "--check"], input=script, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stderr


def test_locale_resources_resolve_from_packaged_skill_layout(
    work: Path, tmp_path: Path
) -> None:
    packaged = tmp_path / "wheel/vibecrafted_core/skills"
    for relative in ("vc-intents", "pl/vc-intents"):
        shutil.copytree(SKILLS / relative, packaged / relative)
    copied_cli = packaged / "vc-intents/scripts/intents_cli.py"
    for lang, relative in (("en", "vc-intents"), ("pl", "pl/vc-intents")):
        result = subprocess.run(
            [
                sys.executable,
                str(copied_cli),
                str(work),
                "--lang",
                lang,
                "plan",
                "--stage",
                "extract",
                "--repo",
                str(REPO),
                "--host",
                lang,
                "--artifacts",
                str(work / "artifacts"),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        generated = tomllib.loads(
            (work / f"artifacts/plans/intents-extract.{lang}.dispatch.toml").read_text()
        )
        brief = packaged / relative / "references/extraction-brief.md"
        assert str(brief) in generated["common"]["text"]
        assert brief.is_file()
        copy = json.loads((packaged / relative / "assets/copy.json").read_text())
        assert copy["extract_title"] == ("Extraction" if lang == "en" else "Ekstrakcja")


def test_locale_resources_cover_shared_template() -> None:
    english = json.loads((SKILL / "assets/copy.json").read_text())
    polish = json.loads((SKILLS / "pl/vc-intents/assets/copy.json").read_text())
    assert english.keys() == polish.keys()
    assert english["ui"].keys() == polish["ui"].keys()
    template = (SKILL / "assets/intents.html").read_text()
    required = set(re.findall(r"__UI_(\w+)__|UI\.(\w+)", template))
    assert {a or b for a, b in required} <= english["ui"].keys()
    assert not (SKILLS / "pl/vc-intents/scripts/intents_cli.py").exists()


def test_language_option_rejects_unknown_locale(work: Path) -> None:
    result = cli(
        work, "--lang", "de", "plan", "--stage", "extract", "--repo", str(REPO)
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
