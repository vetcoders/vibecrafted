from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
import tomllib

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


def test_append_rejects_changed_source_and_preserves_existing(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    transcript = source / "2026-11-03_note.txt"
    transcript.write_text("original")
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [{"source": f"dir:{source}"}],
            }
        )
    )
    first = cli(work, "append", "--until", "2026-11-03")
    assert first.returncode == 0, first.stderr
    transcript.write_text("changed")
    second = cli(work, "append", "--until", "2026-11-03")
    assert second.returncode != 0
    assert (work / "shards/s02/2026-11-03_note.txt").read_text() == "original"


def test_incremental_quotes_and_merge_keep_existing_decisions(
    work: Path, tmp_path: Path
) -> None:
    extract = tmp_path / "new-extractions"
    extract.mkdir()
    (work / "intents.json").write_text(
        json.dumps(
            [
                {
                    "id": "old",
                    "date": "2026-11-01",
                    "source": "old.txt",
                    "quote": "old",
                    "kind": "task",
                    "subject": "inne",
                    "strength": 3,
                    "_shard": "s01",
                }
            ]
        )
    )
    (extract / "s02.json").write_text(
        json.dumps(
            [
                {
                    "id": "new",
                    "source": "source.txt",
                    "quote": "boil the pasta",
                    "kind": "task",
                }
            ]
        )
    )
    result = cli(work, "verify-quotes", "--extractions", str(extract), "--strict")
    assert result.returncode == 0, result.stderr
    assert {row["id"] for row in json.loads((work / "intents.json").read_text())} == {
        "old",
        "new",
    }
    old = {
        "id": "old",
        "verdicts": {"a": {"disposition": "partial", "evidence": "old proof"}},
        "runtime_verified": "yes",
    }
    (work / "LEDGER.json").write_text(
        json.dumps({"intents": [old], "hosts": ["a"], "primary": "a"})
    )
    (work / "overrides.jsonl").write_text(
        json.dumps({"id": "old", "by": "human", "to": "absent", "reason": "review"})
        + "\n"
    )
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    (verdicts / "new.verdicts.json").write_text(
        json.dumps([{"id": "new", "disposition": "absent"}])
    )
    result = cli(work, "merge", "--verdicts", f"a={verdicts}", "--primary", "a")
    assert result.returncode == 0, result.stderr
    ledger = json.loads((work / "LEDGER.json").read_text())
    item = next(row for row in ledger["intents"] if row["id"] == "old")
    assert item["verdicts"]["a"]["evidence"] == "old proof"
    assert item["runtime_verified"] == "yes"
    assert item["disposition"] == "absent"


@pytest.mark.parametrize("failure_point", ["copy", "sweep"])
def test_append_recovers_interrupted_transaction(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "2026-11-03_note.txt").write_text("complete transcript")
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [{"source": f"dir:{source}"}],
            }
        )
    )
    spec = importlib.util.spec_from_file_location("intents_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = Namespace(
        workdir=work, source=[], since=None, until="2026-11-03", shard_size=120
    )
    if failure_point == "copy":
        original = module.shutil.copyfile

        def interrupted(src, dst):
            Path(dst).write_text("partial")
            raise OSError("simulated interrupted copy")

        monkeypatch.setattr(module.shutil, "copyfile", interrupted)
    else:
        original = module.write_json

        def interrupted(path, data):
            if path.name == "sweep.json":
                raise OSError("simulated interruption after publish")
            return original(path, data)

        monkeypatch.setattr(module, "write_json", interrupted)
    with pytest.raises(OSError, match="simulated"):
        module.cmd_append(args)
    if failure_point == "copy":
        monkeypatch.setattr(module.shutil, "copyfile", original)
    else:
        monkeypatch.setattr(module, "write_json", original)
    module.cmd_append(args)
    assert (
        work / "shards/s02/2026-11-03_note.txt"
    ).read_text() == "complete transcript"
    sweep = json.loads((work / "sweep.json").read_text())
    assert sweep["window"]["until"] == "2026-11-03"
    assert sum(entry["files"] for entry in sweep["appends"]) == 1
    assert sweep["shards"].count("s02") == 1


def test_append_identical_replay_and_cross_root_collision(
    work: Path, tmp_path: Path
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "2026-11-03_note.txt").write_text("one")
    (two / "2026-11-03_note.txt").write_text("two")
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [{"source": f"dir:{one}"}],
            }
        )
    )
    assert cli(work, "append", "--until", "2026-11-03").returncode == 0
    assert cli(work, "append", "--until", "2026-11-03").returncode == 0
    assert (
        cli(
            work, "append", "--source", f"dir:{two}", "--until", "2026-11-03"
        ).returncode
        != 0
    )
    assert len(list((work / "shards").glob("s*/2026-11-03_note.txt"))) == 1


def test_verify_rejects_changed_existing_id_without_overwriting(
    work: Path, tmp_path: Path
) -> None:
    previous = [
        {
            "id": "same",
            "date": "",
            "source": "source.txt",
            "quote": "old",
            "topic": "",
            "kind": "task",
            "subject": "inne",
            "strength": 3,
            "_shard": "s01",
        }
    ]
    catalog = work / "intents.json"
    catalog.write_text(json.dumps(previous))
    before = catalog.read_bytes()
    extraction = tmp_path / "new"
    extraction.mkdir()
    (extraction / "s02.json").write_text(
        json.dumps(
            [
                {
                    "id": "same",
                    "source": "source.txt",
                    "quote": "boil the pasta",
                    "kind": "task",
                }
            ]
        )
    )
    result = cli(work, "verify-quotes", "--extractions", str(extraction))
    assert result.returncode != 0
    assert catalog.read_bytes() == before
