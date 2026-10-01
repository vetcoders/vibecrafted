from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer


@pytest.fixture
def generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / "runtime"
    root = runtime / "releases" / "test-generation"
    root.mkdir(parents=True)
    (runtime / "tools").mkdir()
    (runtime / "tools/vibecrafted-current").symlink_to(root)
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime))
    (root / "tool.py").write_text("original\n")
    contract = installer._runtime_pack_contract_module()
    (root / contract.PROVENANCE_NAME).write_text(
        json.dumps(
            {"payload": {"algorithm": "sha256", "files": contract._payload_files(root)}}
        )
    )
    return root


def _finding() -> installer.DoctorFinding:
    return installer._doctor_generation_integrity_findings()[0]


def test_clean_generation(generation: Path) -> None:
    assert _finding().level == "ok"
    assert "test-generation" in _finding().message


@pytest.mark.parametrize(
    "mutation", ["sha256", "size", "mode", "missing", "additional"]
)
def test_generation_inventory_drift(generation: Path, mutation: str) -> None:
    path = generation / "tool.py"
    if mutation == "sha256":
        path.write_text(
            "modified\n"
        )  # Same size: metadata alone cannot prove integrity.
    elif mutation == "size":
        path.write_text("longer modified content\n")
    elif mutation == "mode":
        path.chmod(path.stat().st_mode ^ 0o100)
    elif mutation == "missing":
        path.unlink()  # Also covers a generation with every inventoried file gone.
    else:
        path = generation / "hook_bridge.py"
        path.write_text("added locally\n")

    finding = _finding()
    assert finding.level == "warn"
    assert path.name in finding.message
    assert (
        mutation.upper() if mutation in {"missing", "additional"} else "CHANGED"
    ) in finding.message
    assert "next installation" in finding.message
    assert "source" in finding.message


def test_finder_metadata_is_ignored(generation: Path) -> None:
    (generation / ".DS_Store").write_bytes(b"finder")
    assert _finding().level == "ok"


def test_bounded_list_keeps_total_and_prioritizes_source(generation: Path) -> None:
    cache = generation / "__pycache__"
    cache.mkdir()
    for index in range(30):
        (cache / f"{index:02d}.pyc").write_bytes(b"cache")
    (generation / "hook_bridge.py").write_text("local fix")
    message = _finding().message
    assert "31 additional" in message
    assert "ADDITIONAL: hook_bridge.py" in message
    assert message.count("ADDITIONAL:") == 20
    assert "11 more" in message


@pytest.mark.parametrize(
    "content", ["broken", "{}", '{"payload":{"algorithm":"sha256","files":[]}}']
)
def test_invalid_inventory_cannot_pass(generation: Path, content: str) -> None:
    (generation / "runtime-pack-provenance.json").write_text(content)
    assert _finding().level == "fail"


def test_symlink_cannot_pass(generation: Path, tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.write_text("original\n")
    (generation / "tool.py").unlink()
    (generation / "tool.py").symlink_to(external)
    finding = _finding()
    assert finding.level == "fail"
    assert "symlink" in finding.message


def test_doctor_checks_generation_even_without_install_state(generation: Path) -> None:
    (generation / "hook_bridge.py").write_text("local fix")
    findings = installer.run_doctor(
        generation / "missing-store", installer.InstallState()
    )
    matching = [f for f in findings if f.component == "runtime-generation:integrity"]
    assert len(matching) == 1
    assert "ADDITIONAL: hook_bridge.py" in matching[0].message


def test_reports_all_three_kinds_together(generation: Path) -> None:
    contract = installer._runtime_pack_contract_module()
    (generation / "removed.py").write_text("remove me")
    (generation / contract.PROVENANCE_NAME).write_text(
        json.dumps(
            {
                "payload": {
                    "algorithm": "sha256",
                    "files": contract._payload_files(generation),
                }
            }
        )
    )
    (generation / "tool.py").write_text("modified")
    (generation / "removed.py").unlink()
    (generation / "hook_bridge.py").write_text("added")
    finding = _finding()
    assert "1 changed, 1 missing, 1 additional" in finding.message
    for line in (
        "CHANGED: tool.py",
        "MISSING: removed.py",
        "ADDITIONAL: hook_bridge.py",
    ):
        assert line in finding.message


def test_empty_payload_still_refused_by_admission(tmp_path: Path) -> None:
    contract = installer._runtime_pack_contract_module()
    with pytest.raises(contract.RuntimePackContractError, match="payload is empty"):
        contract._payload_files(tmp_path)


def test_missing_generation_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path / "absent"))
    finding = _finding()
    assert finding.level == "warn"
    assert "not checked" in finding.message


@pytest.mark.parametrize(
    "field,value",
    [("path", "../escape"), ("sha256", "invalid"), ("size", True), ("mode", "invalid")],
)
def test_invalid_record_cannot_pass(
    generation: Path, field: str, value: object
) -> None:
    path = generation / "runtime-pack-provenance.json"
    data = json.loads(path.read_text())
    data["payload"]["files"][0][field] = value
    path.write_text(json.dumps(data))
    assert _finding().level == "fail"
