"""Exercise the packaged offline report without requiring live ASR or a browser."""

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "vibecrafted-core/vibecrafted_core/skills"
SKILL = SKILLS / "vc-forensics"
SCRIPT = SKILL / "scripts/render_report.py"


@pytest.fixture
def renderer():
    spec = importlib.util.spec_from_file_location("forensics_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_reports_retain_provenance_and_do_not_certify_tool_signals(renderer, tmp_path):
    loct = write_json(
        tmp_path / "loct.json",
        {
            "git_ref": "a" * 8,
            "generated_at": "yesterday",
            "dead_parrots": [
                {"file": "src/live.rs", "symbol": "entry", "confidence": "high"}
            ],
        },
    )
    prview = write_json(
        tmp_path / "report.json",
        {
            "schema_version": "1",
            "meta": {"range": {"head": "b" * 40}},
            "gate": {"verdict": "PASS", "allow_merge": True},
            "checks": [],
        },
    )
    report = renderer.build_report("repo", "a" * 40, None, loct, prview, None)
    assert (
        report["sources"]["loctree"]["sha256"]
        == hashlib.sha256(loct.read_bytes()).hexdigest()
    )
    assert report["sources"]["prview"]["data"]["gate"]["allow_merge"] is True
    assert report["sources"]["prview"]["revision_match"] is False
    assert report["sources"]["loctree"]["revision_match"] is True
    assert report["findings"] == []  # A tool's PASS/high confidence is not admission.
    assert report["sources"]["loctree"]["data"]["dead_parrots"][0]["symbol"] == "entry"


def test_manual_round_trip_keeps_unknown_evidence_and_missing_report_is_explicit(
    renderer, tmp_path
):
    findings = [
        {
            "id": "B46",
            "title": "Repeated shaping",
            "state": "proven",
            "evidence": ["L280.json"],
            "verification": {"runtime": "NOT_ASSESSED"},
            "extra_receipt": {"source_pin": 123},
        }
    ]
    path = write_json(
        tmp_path / "findings.json",
        {"schema": "vc-forensics.findings.v1", "findings": findings},
    )
    report = renderer.build_report("repo", "a" * 40, "b" * 40, None, None, path)
    assert report["findings"] == findings
    assert report["sources"]["prview"]["status"] == "NOT_ASSESSED"
    assert report["sources"]["loctree"]["status"] == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "data",
    [
        None,
        5,
        {"findings": "not a list"},
        [{"id": "X", "title": "X", "state": "done"}],
        [{"id": "X", "title": "X"}, {"id": "X", "title": "again"}],
    ],
)
def test_invalid_manual_import_cannot_replace_retained_findings(renderer, data):
    with pytest.raises((ValueError, TypeError)):
        renderer.validate_findings(data)


def test_html_treats_script_closing_and_markup_as_data(renderer, tmp_path):
    payload = "</script><script>globalThis.compromised=true</script><img src=x onerror=evil()>"
    path = write_json(tmp_path / "loct.json", {"note": payload})
    report = renderer.build_report(payload, "a" * 40, None, path, None, None)
    html = renderer.render_html(report)
    embedded = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    ).group(1)
    assert json.loads(embedded)["sources"]["loctree"]["data"]["note"] == payload
    assert payload not in html
    assert html.count("<script>globalThis.compromised") == 0


def test_cli_is_portable_and_refuses_overwriting_an_input(tmp_path):
    loct = write_json(tmp_path / "loct.json", {"summary": {"files": 8}, "cycles": []})
    original = loct.read_bytes()
    command = [
        sys.executable,
        str(SCRIPT),
        "--repo",
        "repo",
        "--head",
        "a" * 40,
        "--loctree",
        str(loct),
        "--out",
        str(loct),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert loct.read_bytes() == original
    destination = tmp_path / "report.html"
    result = subprocess.run(
        command[:-1] + [str(destination)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert destination.is_file()


def test_en_pl_helpers_are_identical_and_self_contained():
    for relative in ("scripts/render_report.py", "assets/report.html"):
        assert (SKILL / relative).read_bytes() == (
            SKILLS / "pl/vc-forensics" / relative
        ).read_bytes()


def test_manual_import_cannot_rebind_repository(renderer, tmp_path):
    path = write_json(
        tmp_path / "manual.json",
        {"schema": "vc-forensics.findings.v1", "head": "b" * 40, "findings": []},
    )
    with pytest.raises(ValueError, match="different head"):
        renderer.build_report("repo", "a" * 40, None, None, None, path)


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node required for offline viewer behavior"
)
def test_viewer_edits_exports_and_rejects_invalid_import_without_data_loss(
    renderer, tmp_path
):
    report = renderer.build_report("repo", "a" * 40, None, None, None, None)
    html = renderer.render_html(report)
    script_start = html.index("<script>") + len("<script>")
    script = html[script_start : html.index("</script>", script_start)]
    # Run the shipped script against a minimal DOM, exercising its event handlers.
    # No browser, live app, external scripts or page capture is needed.
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.value = ''; this.dataset = {}; this.classList = { toggle() {} }; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute() {}
  click() { if (this.events.click) this.events.click(); }
}
const elements = Object.fromEntries(['canvas','message','provenance','stats','export','import','import-file','report-data'].map(id => [id, new Element('div')]));
elements['report-data'].textContent = JSON.stringify(REPORT);
const tabs = ['manual','loctree','prview'].map(tab => { const el = new Element('button'); el.dataset.tab = tab; return el; });
let downloaded;
const context = vm.createContext({ document: { getElementById: id => elements[id], createElement: tag => new Element(tag), querySelectorAll: () => tabs },
  structuredClone, Blob, URL: { createObjectURL(blob) { downloaded = blob; return 'blob:local'; }, revokeObjectURL() {} },
  setTimeout: fn => fn(), window: { confirm: () => true, addEventListener() {} } });
vm.runInContext(SCRIPT, context);
vm.runInContext('editor(null)', context);
vm.runInContext(`
const form = canvas.children[1];
const fields = Object.fromEntries(form.children.filter(el => el.tag === 'label').map(label => [label.textContent, label.children[0]]));
fields.id.value = 'F-1'; fields.title.value = '<img src=x onerror=evil()> tail loss';
fields.mechanism.value = 'Observed delivery omits retained words';
form.children.find(el => el.textContent === 'Save in notebook').click();
`, context);
assert.equal(vm.runInContext('findings.length', context), 1);
assert.equal(vm.runInContext('findings[0].state', context), 'hypothesis');
elements.export.click();
(async () => {
  const saved = JSON.parse(await downloaded.text());
  assert.equal(saved.head, REPORT.head); assert.equal(saved.findings[0].id, 'F-1');
  await elements['import-file'].events.change({ target: { files: [{ text: async () => '{"schema":"wrong","findings":[]}' }], value: 'x' } });
  assert.equal(vm.runInContext('findings.length', context), 1);
  assert.match(elements.message.textContent, /Expected/);
  await elements['import-file'].events.change({ target: { files: [{ text: async () => JSON.stringify({ ...saved, head: 'b'.repeat(40) }) }], value: 'x' } });
  assert.equal(vm.runInContext('findings.length', context), 1);
  assert.match(elements.message.textContent, /different head/);
  await elements['import-file'].events.change({ target: { files: [{ text: async () => JSON.stringify(saved) }], value: 'x' } });
  assert.equal(vm.runInContext('findings[0].title', context), saved.findings[0].title);
  assert.equal(vm.runInContext('typeof evil', context), 'undefined');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    path = tmp_path / "viewer-test.cjs"
    path.write_text(
        "const REPORT = "
        + json.dumps(report)
        + ";\nconst SCRIPT = "
        + json.dumps(script)
        + ";\n"
        + harness
    )
    result = subprocess.run(
        ["node", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
