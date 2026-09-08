"""Contract for how the App reads the configuration owner.

Repair Runtime used to mean one thing: republish this App's bundled Runtime
Pack. Configuration drift therefore had no proportionate remedy, and when the
installer refused, the App rendered whatever the installer had printed —
including a Python traceback — into a modal.

These tests pin the replacement. The App decodes one typed envelope
(``vibecrafted.config-repair.v1``) from the installed generation's own
installer, renders counts, file names, reasons and backup locations, and turns
anything it cannot understand into a short bounded sentence. It never renders
a traceback, and it never reads a failure as permission to rewrite anything.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY = (
    REPO_ROOT
    / "vibecrafted-app"
    / "shell-agent"
    / "app"
    / "Vibecrafted"
    / "ServerMenuPolicy.swift"
)

MAIN_SWIFT = r'''
import Foundation

func emit(_ text: String) { print(text.replacingOccurrences(of: "\n", with: " ⏎ ")) }

func envelope(_ json: String) -> Data { json.data(using: .utf8)! }

let healthy = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "healthy", "mode": "apply",
 "reason": "", "generation": "4.3.1+g381c6b8b", "repaired": 0, "conflicts": 0,
 "rolled_back": false, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/o/.config/vibecrafted/vc-frame/config.kdl",
            "action": "unchanged", "reason": "", "backup": ""}]}
"""#)

let repairable = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "repairable", "mode": "plan",
 "reason": "", "generation": "4.3.1+g381c6b8b", "repaired": 0, "conflicts": 0,
 "rolled_back": false, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/o/.config/vibecrafted/vc-frame/config.kdl",
            "action": "repair",
            "reason": "shipped defaults for the selected generation were never merged into this preference",
            "backup": ""},
           {"path": "/Users/o/.config/vibecrafted/terminal-policy.toml",
            "action": "unchanged", "reason": "", "backup": ""}]}
"""#)

let repaired = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "repaired", "mode": "apply",
 "reason": "", "generation": "4.3.1+g381c6b8b", "repaired": 2, "conflicts": 0,
 "rolled_back": true, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/o/.config/vibecrafted/vc-frame/config.kdl",
            "action": "repaired", "reason": "shipped defaults changed", "backup": ""},
           {"path": "/Users/o/.config/vibecrafted/terminal-policy.toml",
            "action": "seeded", "reason": "product preference is missing", "backup": ""}]}
"""#)

let conflict = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "conflict", "mode": "apply",
 "reason": "product configuration needs an explicit choice",
 "generation": "4.3.1+g381c6b8b", "repaired": 0, "conflicts": 1,
 "rolled_back": false, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/o/.config/vibecrafted/vc-frame/config.kdl",
            "action": "conflict", "reason": "KDL structure is unbalanced",
            "backup": "/Users/o/.local/share/vibecrafted/.installer-backups/drift-1/config.kdl"}]}
"""#)

let wrongSchema = envelope(#"{"schema": "vibecrafted.config-repair.v9", "status": "healthy"}"#)

let traceback = """
Traceback (most recent call last):
  File "/Users/o/.local/share/vibecrafted/releases/4.3.1/scripts/vetcoders_install.py", line 15677, in _assert
    raise ValueError("KDL edit changes nested or structural content")
ValueError: KDL edit changes nested or structural content
""".data(using: .utf8)!

func label(_ outcome: ConfigRepairOutcome) -> String {
  switch outcome {
  case .healthy: return "healthy"
  case .repairable: return "repairable"
  case .repaired: return "repaired"
  case .conflict: return "conflict"
  case .absent: return "absent"
  case .unusable: return "unusable"
  }
}

func detail(_ outcome: ConfigRepairOutcome) -> String {
  switch outcome {
  case .healthy(let e), .repairable(let e), .repaired(let e), .conflict(let e):
    return configRepairSummary(e)
  case .absent(let reason), .unusable(let reason):
    return reason
  }
}

func advisory(_ outcome: ConfigRepairOutcome) -> String {
  switch outcome {
  case .healthy(let e), .repairable(let e), .repaired(let e), .conflict(let e):
    return configRepairAdvisory(e) ?? "<none>"
  default: return "<none>"
  }
}

let scenario = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
switch scenario {
case "healthy", "repairable", "repaired", "conflict":
  let payload = ["healthy": healthy, "repairable": repairable,
                 "repaired": repaired, "conflict": conflict][scenario]!
  let status: Int32 = scenario == "conflict" ? 2 : 0
  let outcome = decodeConfigRepair(
    stdout: payload, stderr: Data(), terminationStatus: status, clean: true)
  emit(label(outcome))
  emit(detail(outcome))
  emit(advisory(outcome))
case "schema":
  let outcome = decodeConfigRepair(
    stdout: wrongSchema, stderr: Data(), terminationStatus: 0, clean: true)
  emit(label(outcome))
  emit(detail(outcome))
case "traceback":
  let outcome = decodeConfigRepair(
    stdout: Data(), stderr: traceback, terminationStatus: 1, clean: true)
  emit(label(outcome))
  emit(detail(outcome))
  emit("length=\(detail(outcome).count)")
case "legacy":
  // A generation installed before `runtime-repair` existed answers with
  // argparse usage on stderr and exit 2 — the real shape of the upgrade path.
  let usage = """
usage: vetcoders_install.py [-h] {install,doctor,list,layout,uninstall,restore,runtime-install,runtime-resolve,runtime-uninstall} ...
vetcoders_install.py: error: argument command: invalid choice: 'runtime-repair'
""".data(using: .utf8)!
  let outcome = decodeConfigRepair(
    stdout: Data(), stderr: usage, terminationStatus: 2, clean: true)
  emit(label(outcome))
  emit(detail(outcome))
case "killed":
  let outcome = decodeConfigRepair(
    stdout: Data(), stderr: Data(), terminationStatus: 0, clean: false)
  emit(label(outcome))
  emit(detail(outcome))
case "arguments":
  let installer = URL(fileURLWithPath: "/gen/scripts/vetcoders_install.py")
  let home = URL(fileURLWithPath: "/Users/o/.local/share/vibecrafted", isDirectory: true)
  emit(runtimeRepairArguments(installer: installer, runtimeHome: home, plan: true)
    .joined(separator: " "))
  emit(runtimeRepairArguments(installer: installer, runtimeHome: home, plan: false)
    .joined(separator: " "))
default:
  emit("unknown scenario")
}
'''


@pytest.fixture(scope="module")
def policy_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    swiftc = shutil.which("swiftc")
    if swiftc is None:
        pytest.skip("swiftc is required for the native config repair contract")
    build = tmp_path_factory.mktemp("config-repair-policy")
    main = build / "main.swift"
    main.write_text(MAIN_SWIFT, encoding="utf-8")
    binary = build / "config-repair-policy"
    subprocess.run(
        [swiftc, str(POLICY), str(main), "-o", str(binary)],
        check=True,
        cwd=REPO_ROOT,
    )
    return binary


def _run(binary: Path, scenario: str) -> list[str]:
    return subprocess.run(
        [str(binary), scenario], check=True, capture_output=True, text=True
    ).stdout.splitlines()


def test_healthy_configuration_offers_nothing_to_do(policy_binary: Path) -> None:
    """A valid configuration must not invite the operator to change anything."""
    label, summary, advisory = _run(policy_binary, "healthy")
    assert label == "healthy"
    assert "already matches generation 4.3.1+g381c6b8b" in summary
    assert "Nothing was changed" in summary
    assert advisory == "<none>"


def test_a_plan_reads_as_an_invitation_not_as_a_completed_change(
    policy_binary: Path,
) -> None:
    label, summary, advisory = _run(policy_binary, "repairable")
    assert label == "repairable"
    assert "Nothing has been changed yet" in summary
    # The one drifted file is named; the unchanged one is not noise.
    assert "config.kdl — repair" in summary
    assert "terminal-policy.toml" not in summary
    assert advisory == "Configuration drift in 1 file(s) — use Repair Runtime…"


def test_a_repair_reports_counts_and_the_rollback_it_performed(
    policy_binary: Path,
) -> None:
    label, summary, _ = _run(policy_binary, "repaired")
    assert label == "repaired"
    assert "Repaired 2 configuration file(s)" in summary
    assert "interrupted configuration publication was rolled back" in summary
    assert "config.kdl — repaired" in summary
    assert "terminal-policy.toml — seeded" in summary


def test_a_conflict_names_the_preserved_copy_and_publishes_nothing(
    policy_binary: Path,
) -> None:
    label, summary, advisory = _run(policy_binary, "conflict")
    assert label == "conflict"
    assert "1 configuration file(s) need your choice" in summary
    assert "nothing was published" in summary
    assert "KDL structure is unbalanced" in summary
    assert ".installer-backups/drift-1/config.kdl" in summary
    assert advisory == "1 configuration file(s) need your choice — use Repair Runtime…"


def test_an_installer_traceback_never_reaches_the_dialog(policy_binary: Path) -> None:
    """The incident in one assertion: no raw Python traceback in a normal dialog."""
    label, summary, length = _run(policy_binary, "traceback")
    assert label == "unusable"
    assert "Traceback (most recent call last)" not in summary
    assert summary.startswith("the configuration owner exited 1")
    assert int(length.removeprefix("length=")) <= 300


def test_an_unknown_schema_is_refused_rather_than_guessed(policy_binary: Path) -> None:
    label, summary = _run(policy_binary, "schema")
    assert label == "unusable"
    assert "vibecrafted.config-repair.v9" in summary


def test_a_killed_owner_is_unusable_not_absent(policy_binary: Path) -> None:
    """`unusable` must never degrade into "nothing is installed": that reading
    is what turns a bad read into an automatic overwrite."""
    label, summary = _run(policy_binary, "killed")
    assert label == "unusable"
    assert "did not exit cleanly" in summary


def test_a_generation_without_the_verb_degrades_to_unusable(
    policy_binary: Path,
) -> None:
    """The upgrade path: an older installation cannot answer, and says so.

    `unusable` is the only safe reading — it sends the operator to the Runtime
    Pack reinstall, which is exactly the remedy an installation predating the
    verb actually needs. Reading it as `absent` would authorise an overwrite.
    """
    label, summary = _run(policy_binary, "legacy")
    assert label == "unusable"
    assert "no readable result" in summary
    assert "invalid choice: 'runtime-repair'" in summary


def test_the_plan_invocation_is_read_only_by_construction(policy_binary: Path) -> None:
    plan, apply = _run(policy_binary, "arguments")
    assert plan == (
        "-B /gen/scripts/vetcoders_install.py runtime-repair "
        "--runtime-home /Users/o/.local/share/vibecrafted --json --plan"
    )
    assert apply == (
        "-B /gen/scripts/vetcoders_install.py runtime-repair "
        "--runtime-home /Users/o/.local/share/vibecrafted --json"
    )
