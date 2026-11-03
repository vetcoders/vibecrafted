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
        original = module.shutil.copy2

        def interrupted(src, dst):
            Path(dst).write_text("partial")
            raise OSError("simulated interrupted copy")

        monkeypatch.setattr(module.shutil, "copy2", interrupted)
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
        monkeypatch.setattr(module.shutil, "copy2", original)
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
    assert sweep["sources"][0]["state"] == "read"
    assert sweep["sources"][0]["coverage"] == {
        "since": "2026-11-01",
        "until": "2026-11-03",
        "state": "read",
    }


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


def test_append_refreshes_full_coverage_with_no_new_files(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "2026-11-03_note.txt").write_text("abc")
    (work / "shards/s01/2026-11-03_note.txt").write_text("abc")
    aicx = {"source": "aicx", "state": "unavailable", "reason": "offline", "rows": 0}
    sweep = {
        "window": {"since": "2026-11-01", "until": "2026-11-03"},
        "sources": [
            {
                "source": f"dir:{source}",
                "state": "unavailable",
                "reason": "offline",
                "files": 0,
            },
            aicx,
        ],
    }
    (work / "sweep.json").write_text(json.dumps(sweep))
    result = cli(work, "append", "--source", f"dir:{source}", "--until", "2026-11-03")
    assert result.returncode == 0, result.stderr
    updated = json.loads((work / "sweep.json").read_text())
    directory = updated["sources"][0]
    assert (directory["state"], directory["files"], directory["bytes"]) == (
        "read",
        1,
        3,
    )
    assert directory["coverage"] == {
        "since": "2026-11-01",
        "until": "2026-11-03",
        "state": "read",
    }
    assert updated["sources"][1] == aicx
    before = (work / "sweep.json").read_bytes()
    assert (
        cli(
            work, "append", "--source", f"dir:{source}", "--until", "2026-11-03"
        ).returncode
        == 0
    )
    assert (work / "sweep.json").read_bytes() == before


def test_append_narrow_scan_does_not_claim_full_coverage(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "2026-11-03_note.txt").write_text("new")
    sweep = {
        "window": {"since": "2026-11-01", "until": "2026-11-02"},
        "sources": [
            {
                "source": f"dir:{source}",
                "state": "unavailable",
                "reason": "offline",
                "files": 0,
            }
        ],
    }
    (work / "sweep.json").write_text(json.dumps(sweep))
    result = cli(
        work,
        "append",
        "--source",
        f"dir:{source}",
        "--since",
        "2026-11-03",
        "--until",
        "2026-11-03",
    )
    assert result.returncode == 0, result.stderr
    entry = json.loads((work / "sweep.json").read_text())["sources"][0]
    assert entry["state"] == "unavailable"
    assert entry["files"] == 1
    assert entry["coverage"] == {
        "since": "2026-11-03",
        "until": "2026-11-03",
        "state": "read",
    }
    assert "partial" in entry["reason"]


def test_incremental_merge_reports_all_retained_hosts(
    work: Path, tmp_path: Path
) -> None:
    intents = [{"id": "old"}, {"id": "new"}, {"id": "missing"}]
    (work / "intents.json").write_text(json.dumps(intents))
    old = {
        "id": "old",
        "verdicts": {
            "a": {"disposition": "partial", "evidence": "a-proof"},
            "b": {"disposition": "partial", "evidence": "b-proof"},
        },
        "runtime_verified": "yes",
    }
    (work / "LEDGER.json").write_text(
        json.dumps({"hosts": ["a", "b"], "primary": "a", "intents": [old]})
    )
    (work / "overrides.jsonl").write_text(
        json.dumps({"id": "old", "by": "human", "to": "absent"}) + "\n"
    )
    verdicts = tmp_path / "verdicts"
    verdicts.mkdir()
    (verdicts / "new.verdicts.json").write_text(
        json.dumps([{"id": "new", "disposition": "absent"}])
    )
    result = cli(work, "merge", "--verdicts", f"a={verdicts}", "--primary", "a")
    assert result.returncode == 0, result.stderr
    report = json.loads((work / "replication-report.json").read_text())
    assert report["hosts"] == ["a", "b"]
    assert report["per_host_rows"] == {"a": 2, "b": 1}
    assert report["pairs"]["a|b"]["compared"] == 1
    assert report["pairs"]["a|b"]["agree"] == 1
    assert "a: 1 intents without a verdict" in result.stderr
    assert "b: 2 intents without a verdict" in result.stderr
    ledger = json.loads((work / "LEDGER.json").read_text())
    assert ledger["intents"][0]["verdicts"] == old["verdicts"]
    assert ledger["intents"][0]["disposition"] == "absent"


def test_append_adds_explicit_empty_source_without_touching_others(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "empty-source"
    source.mkdir()
    untouched = {"source": "aicx", "state": "read", "rows": 7, "truncated": False}
    sweep = {
        "window": {"since": "2026-11-01", "until": "2026-11-03"},
        "sources": [untouched],
    }
    (work / "sweep.json").write_text(json.dumps(sweep))
    result = cli(work, "append", "--source", f"dir:{source}", "--until", "2026-11-03")
    assert result.returncode == 0, result.stderr
    sources = json.loads((work / "sweep.json").read_text())["sources"]
    assert {k: sources[0][k] for k in untouched} == untouched
    assert sources[0]["covered_ranges"] == [
        {"since": "2026-11-01", "until": "2026-11-03", "state": "read"}
    ]
    assert sources[1]["source"] == f"dir:{source}"
    assert sources[1]["state"] == "empty"
    assert sources[1]["files"] == 0 and sources[1]["first"] is None


def test_partial_coverage_keeps_status_open(work: Path, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}],
            }
        )
    )
    result = cli(work, "append", "--since", "2026-11-03", "--until", "2026-11-03")
    assert result.returncode == 0, result.stderr
    (work / "LEDGER.json").write_text(
        json.dumps(
            {"project": "example", "hosts": ["a"], "primary": "a", "intents": []}
        )
    )
    status = cli(work, "status")
    assert status.returncode == 1
    assert "partial coverage" in status.stdout


@pytest.mark.parametrize(
    "since, expected_state", [("2026-11-03", "read"), ("2026-11-04", "unavailable")]
)
def test_append_preserves_proven_legacy_prefix(
    work: Path, tmp_path: Path, since: str, expected_state: str
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / f"{since}_note.txt").write_text("new")
    entry = {"source": f"dir:{source}", "state": "read", "files": 2, "bytes": 15}
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [entry],
            }
        )
    )
    result = cli(work, "append", "--since", since, "--until", since)
    assert result.returncode == 0, result.stderr
    result_entry = json.loads((work / "sweep.json").read_text())["sources"][0]
    assert result_entry["state"] == expected_state
    assert result_entry["coverage"] == {"since": since, "until": since, "state": "read"}
    assert result_entry["files"] == 1
    assert result_entry["covered_ranges"][0]["since"] == "2026-11-01"
    assert result_entry["covered_ranges"][0]["until"] == "2026-11-02"


def test_append_does_not_infer_legacy_prefix_after_unattributed_append(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    entry = {"source": f"dir:{source}", "state": "read", "files": 1}
    sweep = {
        "window": {"since": "2026-11-01", "until": "2026-11-04"},
        "sources": [entry],
        "appends": [{"id": "legacy", "until": "2026-11-04"}],
    }
    (work / "sweep.json").write_text(json.dumps(sweep))
    result = cli(work, "append", "--since", "2026-11-05", "--until", "2026-11-05")
    assert result.returncode == 0, result.stderr
    updated = json.loads((work / "sweep.json").read_text())["sources"][0]
    assert updated["state"] == "unavailable"


def test_partial_coverage_can_be_closed_by_later_gap_scan(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-04"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}],
            }
        )
    )
    for start, end in [("2026-11-01", "2026-11-01"), ("2026-11-03", "2026-11-04")]:
        assert cli(work, "append", "--since", start, "--until", end).returncode == 0
    entry = json.loads((work / "sweep.json").read_text())["sources"][0]
    assert entry["state"] == "unavailable"
    assert (
        cli(work, "append", "--since", "2026-11-02", "--until", "2026-11-02").returncode
        == 0
    )
    entry = json.loads((work / "sweep.json").read_text())["sources"][0]
    assert entry["state"] == "empty"
    assert len(entry["covered_ranges"]) == 3


def test_append_recovers_after_coverage_checkpoint(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-03"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}],
            }
        )
    )
    spec = importlib.util.spec_from_file_location("intents_checkpoint_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    args = Namespace(
        workdir=work, source=[], since=None, until="2026-11-03", shard_size=120
    )
    original = module.write_json

    def interrupted(path, data):
        if path.name == ".append-transaction.json" and data.get("complete"):
            raise OSError("after coverage checkpoint")
        return original(path, data)

    monkeypatch.setattr(module, "write_json", interrupted)
    with pytest.raises(OSError, match="after coverage checkpoint"):
        module.cmd_append(args)
    monkeypatch.setattr(module, "write_json", original)
    module.cmd_append(args)
    sweep = json.loads((work / "sweep.json").read_text())
    assert sweep["sources"][0]["state"] == "empty"
    assert len(sweep["appends"]) == 1


@pytest.mark.parametrize("explicit", [None, "a", "b"])
def test_merge_preserves_primary_when_only_other_host_arrives(
    work: Path, tmp_path: Path, explicit: str | None
) -> None:
    (work / "intents.json").write_text(json.dumps([{"id": "one"}]))
    original = {
        "id": "one",
        "verdicts": {
            "a": {"disposition": "partial", "evidence": "a"},
            "b": {"disposition": "absent", "evidence": "b"},
        },
        "runtime_verified": "yes",
        "notes": ["retain"],
    }
    (work / "LEDGER.json").write_text(
        json.dumps({"hosts": ["a", "b"], "primary": "a", "intents": [original]})
    )
    verdicts = tmp_path / "b"
    verdicts.mkdir()
    args = ["merge", "--verdicts", f"b={verdicts}"]
    if explicit:
        args += ["--primary", explicit]
    result = cli(work, *args)
    assert result.returncode == 0, result.stderr
    ledger = json.loads((work / "LEDGER.json").read_text())
    assert ledger["primary"] == (explicit or "a")
    row = ledger["intents"][0]
    assert row["disposition"] == ("absent" if explicit == "b" else "partial")
    assert row["verdicts"] == original["verdicts"]
    assert row["runtime_verified"] == "yes" and row["notes"] == ["retain"]


@pytest.mark.parametrize("payload", ["null", '"text"', "[null]", "[1]", "{}", "["])
def test_quote_verifier_reports_malformed_shards(
    work: Path, tmp_path: Path, payload: str
) -> None:
    extract = tmp_path / "extract"
    extract.mkdir()
    (extract / "s01.json").write_text(payload)
    (work / "intents.json").write_text(
        json.dumps([{"id": "retained", "date": "2026-01-01"}])
    )
    result = cli(work, "verify-quotes", "--extractions", str(extract), "--strict")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert (work / "verify-quotes.problems.txt").read_text()
    assert json.loads((work / "intents.json").read_text())[0]["id"] == "retained"


def test_append_extension_leaves_unselected_source_unconfirmed(
    work: Path, tmp_path: Path
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    sources = [
        {"source": f"dir:{a}", "state": "read", "files": 2},
        {"source": f"dir:{b}", "state": "read", "files": 7},
    ]
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-02", "until": "2026-11-03"},
                "sources": sources,
            }
        )
    )
    assert (
        cli(
            work,
            "append",
            "--source",
            f"dir:{a}",
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-04",
        ).returncode
        == 0
    )
    sweep = json.loads((work / "sweep.json").read_text())
    assert sweep["window"] == {"since": "2026-11-01", "until": "2026-11-04"}
    assert sweep["sources"][1]["files"] == 7 and sweep["sources"][1]["state"] == "read"
    (work / "LEDGER.json").write_text(
        json.dumps({"hosts": ["a"], "primary": "a", "intents": []})
    )
    result = cli(work, "status")
    assert result.returncode == 1 and f"dir:{b}" in result.stdout


def test_append_abort_preserves_published_and_avoids_staging_contamination(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    one, two = source / "2026-11-01_one.txt", source / "2026-11-02_two.txt"
    one.write_text("published")
    two.write_text("old")
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-02"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}],
            }
        )
    )
    import hashlib

    shard = work / "shards/s02"
    shard.mkdir()
    (shard / one.name).write_text("published")
    stage = work / ".append-staging/oldtx/s03"
    stage.mkdir(parents=True)
    (stage / "unexpected.txt").write_text("intruder")
    tx = {
        "id": "oldtx",
        "since": "2026-11-01",
        "until": "2026-11-02",
        "complete": False,
        "stage_layout": "transaction",
        "shards": [
            {
                "id": "s02",
                "items": [
                    {
                        "name": one.name,
                        "path": str(one),
                        "sha256": hashlib.sha256(b"published").hexdigest(),
                    }
                ],
            },
            {
                "id": "s03",
                "items": [
                    {
                        "name": two.name,
                        "path": str(two),
                        "sha256": hashlib.sha256(b"old").hexdigest(),
                    }
                ],
            },
        ],
    }
    (work / ".append-transaction.json").write_text(json.dumps(tx))
    two.write_text("new")
    assert cli(work, "append", "--until", "2026-11-02").returncode != 0
    result = cli(work, "append", "--abort")
    assert result.returncode == 0, result.stderr
    assert (shard / one.name).read_text() == "published"
    assert cli(work, "append", "--until", "2026-11-02").returncode == 0
    assert not list((work / "shards").glob("s*/unexpected.txt"))
    assert list((work / ".append-archive").rglob("unexpected.txt"))


def test_extract_plan_orders_shards_numerically(work: Path) -> None:
    for name in ("s11", "s99", "s100", "s101"):
        (work / "shards" / name).mkdir()
    generated = plan(work, "extract")
    assert [cut["id"] for cut in generated["cuts"]] == [
        "s01",
        "s11",
        "s99",
        "s100",
        "s101",
    ]


def test_sweep_and_append_copy_metadata_and_numeric_shards(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "2026-11-01").mkdir()
    dated = source / "2026-11-01/120000_x.txt"
    dated.write_text("original")
    os.utime(dated, (1600000000, 1600000000))
    fresh_work = tmp_path / "fresh"
    result = cli(
        fresh_work,
        "sweep",
        "--source",
        f"dir:{source}",
        "--since",
        "2026-11-01",
        "--until",
        "2026-11-01",
        "--shard-size",
        "120",
    )
    assert result.returncode == 0, result.stderr
    assert (
        fresh_work / "shards/s01/2026-11-01_120000_x.txt"
    ).stat().st_mtime == dated.stat().st_mtime
    (fresh_work / "shards/s11").mkdir()
    (fresh_work / "shards/s99").mkdir()
    flat = source / "2026-11-02_new.txt"
    flat.write_text("new")
    os.utime(flat, (1600000001, 1600000001))
    assert cli(fresh_work, "append", "--until", "2026-11-02").returncode == 0
    assert (
        fresh_work / "shards/s100/2026-11-02_new.txt"
    ).stat().st_mtime == flat.stat().st_mtime
    assert json.loads((fresh_work / "sweep.json").read_text())["shards"] == [
        "s01",
        "s11",
        "s99",
        "s100",
    ]


def test_orphan_staging_is_never_published(work: Path, tmp_path: Path) -> None:
    source = tmp_path / "sources"
    source.mkdir()
    (source / "2026-11-02_new.txt").write_text("new")
    orphan = work / ".append-staging/s02"
    orphan.mkdir(parents=True)
    (orphan / "stray.txt").write_text("orphan")
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-01"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}],
            }
        )
    )
    assert cli(work, "append", "--until", "2026-11-02").returncode == 0
    assert (orphan / "stray.txt").read_text() == "orphan"
    assert not list((work / "shards").glob("s*/stray.txt"))


def test_unmanifested_staging_entries_refuse_publication(
    work: Path, tmp_path: Path
) -> None:
    import hashlib

    source = tmp_path / "2026-11-02_new.txt"
    source.write_text("new")
    stage = work / ".append-staging/tx/s02"
    stage.mkdir(parents=True)
    (stage / "stray.txt").write_text("do not publish")
    (work / "sweep.json").write_text(
        json.dumps(
            {"window": {"since": "2026-11-01", "until": "2026-11-02"}, "sources": []}
        )
    )
    tx = {
        "id": "tx",
        "stage_layout": "transaction",
        "since": "2026-11-01",
        "until": "2026-11-02",
        "complete": False,
        "shards": [
            {
                "id": "s02",
                "items": [
                    {
                        "name": source.name,
                        "path": str(source),
                        "sha256": hashlib.sha256(b"new").hexdigest(),
                    }
                ],
            }
        ],
    }
    (work / ".append-transaction.json").write_text(json.dumps(tx))
    result = cli(work, "append", "--until", "2026-11-02")
    assert result.returncode != 0
    assert "unexpected" in result.stderr
    assert not (work / "shards/s02").exists()
    assert (stage / "stray.txt").read_text() == "do not publish"


def test_quote_verifier_accepts_empty_and_continues_valid_rows(
    work: Path, tmp_path: Path
) -> None:
    extract = tmp_path / "extract"
    extract.mkdir()
    (extract / "s01.json").write_text("[]")
    (extract / "s02.json").write_text(
        json.dumps(
            [
                None,
                {
                    "id": "valid",
                    "source": "source.txt",
                    "quote": "boil the pasta",
                    "kind": "task",
                },
            ]
        )
    )
    result = cli(work, "verify-quotes", "--extractions", str(extract), "--strict")
    assert result.returncode == 1 and "Traceback" not in result.stderr
    assert [x["id"] for x in json.loads((work / "intents.json").read_text())] == [
        "valid"
    ]
    (extract / "s02.json").write_text("[]")
    assert (
        cli(work, "verify-quotes", "--extractions", str(extract), "--strict").returncode
        == 0
    )


def test_unselected_ambiguous_legacy_coverage_stays_open(
    work: Path, tmp_path: Path
) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    sources = [
        {"source": f"dir:{a}", "state": "read", "files": 1},
        {"source": f"dir:{b}", "state": "read", "files": 7},
    ]
    sweep = {
        "window": {"since": "2026-11-01", "until": "2026-11-04"},
        "sources": sources,
        "appends": [{"id": "legacy", "until": "2026-11-04"}],
    }
    (work / "sweep.json").write_text(json.dumps(sweep))
    (work / "LEDGER.json").write_text(
        json.dumps({"hosts": ["a"], "primary": "a", "intents": []})
    )
    status = cli(work, "status")
    assert status.returncode == 1 and f"dir:{b}" in status.stdout
    result = cli(
        work,
        "append",
        "--source",
        f"dir:{a}",
        "--since",
        "2026-11-01",
        "--until",
        "2026-11-04",
    )
    assert result.returncode == 0, result.stderr
    entry = json.loads((work / "sweep.json").read_text())["sources"][1]
    assert (
        entry["state"] == "read"
        and entry["files"] == 7
        and entry["covered_ranges"] == []
    )
    status = cli(work, "status")
    assert status.returncode == 1 and f"dir:{b}" in status.stdout


def test_unselected_legacy_source_retains_proven_window_without_extension(
    work: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    aicx = {"source": "aicx", "state": "read", "rows": 7}
    (work / "sweep.json").write_text(
        json.dumps(
            {
                "window": {"since": "2026-11-01", "until": "2026-11-04"},
                "sources": [{"source": f"dir:{source}", "state": "unavailable"}, aicx],
            }
        )
    )
    assert (
        cli(
            work,
            "append",
            "--source",
            f"dir:{source}",
            "--since",
            "2026-11-01",
            "--until",
            "2026-11-04",
        ).returncode
        == 0
    )
    entry = json.loads((work / "sweep.json").read_text())["sources"][1]
    assert entry["rows"] == 7 and entry["covered_ranges"] == [
        {"since": "2026-11-01", "until": "2026-11-04", "state": "read"}
    ]
    (work / "LEDGER.json").write_text(
        json.dumps({"hosts": ["a"], "primary": "a", "intents": []})
    )
    assert cli(work, "status").returncode == 0
