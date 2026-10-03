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
 "files": [{"path": "/Users/tester/.config/vibecrafted/vc-frame/config.kdl",
            "action": "unchanged", "reason": "", "backup": ""}]}
"""#)

let repairable = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "repairable", "mode": "plan",
 "reason": "", "generation": "4.3.1+g381c6b8b", "repaired": 0, "conflicts": 0,
 "rolled_back": false, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/tester/.config/vibecrafted/vc-frame/config.kdl",
            "action": "repair",
            "reason": "shipped defaults for the selected generation were never merged into this preference",
            "backup": ""},
           {"path": "/Users/tester/.config/vibecrafted/terminal-policy.toml",
            "action": "unchanged", "reason": "", "backup": ""}]}
"""#)

let repaired = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "repaired", "mode": "apply",
 "reason": "", "generation": "4.3.1+g381c6b8b", "repaired": 2, "conflicts": 0,
 "rolled_back": true, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/tester/.config/vibecrafted/vc-frame/config.kdl",
            "action": "repaired", "reason": "shipped defaults changed", "backup": ""},
           {"path": "/Users/tester/.config/vibecrafted/terminal-policy.toml",
            "action": "seeded", "reason": "product preference is missing", "backup": ""}]}
"""#)

let conflict = envelope(#"""
{"schema": "vibecrafted.config-repair.v1", "status": "conflict", "mode": "apply",
 "reason": "product configuration needs an explicit choice",
 "generation": "4.3.1+g381c6b8b", "repaired": 0, "conflicts": 1,
 "rolled_back": false, "at": "2026-09-08T11:00:00+00:00",
 "files": [{"path": "/Users/tester/.config/vibecrafted/vc-frame/config.kdl",
            "action": "conflict", "reason": "KDL structure is unbalanced",
            "backup": "/Users/tester/.local/share/vibecrafted/.installer-backups/drift-1/config.kdl"}]}
"""#)

let wrongSchema = envelope(#"{"schema": "vibecrafted.config-repair.v9", "status": "healthy"}"#)

let traceback = """
Traceback (most recent call last):
  File "/Users/tester/.local/share/vibecrafted/releases/4.3.1/scripts/vetcoders_install.py", line 15677, in _assert
    raise ValueError("KDL edit changes nested or structural content")
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
ValueError: KDL edit changes nested or structural content
""".data(using: .utf8)!

let preferenceConflict = envelope(#"""
{"schema": "vibecrafted.preference-conflict.v1", "status": "conflict",
 "message": "Your terminal-policy.toml overlaps the new defaults on terminal.shell. The previously verified runtime is still selected.",
 "previous_runtime_available": true, "previous_runtime_version": "9.9.9+a",
 "choices": ["keep-current", "use-incoming"],
 "files": [{"path": "/Users/tester/.config/vibecrafted/terminal-policy.toml",
            "reason": "settings conflict with changed shipped defaults: terminal.shell",
            "settings": ["terminal.shell"], "choices": ["keep-current", "use-incoming"],
            "current_sha256": "aa", "incoming_sha256": "bb", "backup": "/backups/policy",
            "mergeable": true}]}
"""#)

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
  let home = URL(fileURLWithPath: "/Users/tester/.local/share/vibecrafted", isDirectory: true)
  emit(runtimeRepairArguments(installer: installer, runtimeHome: home, plan: true)
    .joined(separator: " "))
  emit(runtimeRepairArguments(installer: installer, runtimeHome: home, plan: false)
    .joined(separator: " "))
case "caret":
  let excerpt = boundedResolverDiagnostic(stdout: Data(), stderr: traceback, limit: 240)
  emit(excerpt)
  emit(isTracebackCaret("          ^^^^^") ? "caret" : "kept")
case "preference-conflict":
  let decoded = decodePreferenceConflict(from: preferenceConflict)!
  emit(preferenceConflictSummary(decoded))
  emit(preferenceResolutionChoice(from: decoded, action: "keep-current")?.action ?? "none")
  emit(preferenceResolutionChoice(from: decoded, action: "use-incoming")?.action ?? "none")
  emit(decoded.previousRuntimeAvailable == true ? "previous-ready" : "no-prior")
case "reinstall-unresolved", "reinstall-absent", "reinstall-current",
     "reinstall-upgrade", "reinstall-republish", "reinstall-service",
     "reinstall-same-version-other-source":
  let resolution: RuntimeResolution<String>
  switch scenario {
  case "reinstall-unresolved": resolution = .unusable("receipt could not be read")
  case "reinstall-absent": resolution = .absent("neither identity document exists")
  case "reinstall-upgrade": resolution = .ready("4.3.1+gaaaaaaaa")
  case "reinstall-same-version-other-source": resolution = .ready("4.3.2+gbbbbbbbb")
  default: resolution = .ready("4.3.2+gaaaaaaaa")
  }
  let presentation = runtimeReinstallPresentation(
    resolution: resolution, carrierGeneration: "4.3.2+gaaaaaaaa",
    configuration: "Configuration inspection found no changes to make.",
    serviceFailure: scenario == "reinstall-service" ? "receipt admission refused" : nil,
    canUpgrade: scenario == "reinstall-upgrade",
    explicitRepublish: scenario == "reinstall-republish")
  emit(presentation.title)
  emit(presentation.actionTitle ?? "<none>")
  emit(presentation.detail)
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
        "--runtime-home /Users/tester/.local/share/vibecrafted --json --plan"
    )
    assert apply == (
        "-B /gen/scripts/vetcoders_install.py runtime-repair "
        "--runtime-home /Users/tester/.local/share/vibecrafted --json"
    )


def test_caret_tails_never_reach_the_dialog(policy_binary: Path) -> None:
    excerpt, marker = _run(policy_binary, "caret")
    assert "Traceback" not in excerpt
    assert "^^^^" not in excerpt
    assert "KDL edit changes nested or structural content" in excerpt
    assert marker == "caret"


def test_preference_conflict_names_settings_and_keeps_a_bound_choice(
    policy_binary: Path,
) -> None:
    summary, keep, incoming, availability = _run(policy_binary, "preference-conflict")
    assert "terminal.shell" in summary
    assert "still selected" in summary
    assert "^^^^" not in summary
    assert "/usr/bin/fish" not in summary
    assert keep == "keep-current"
    assert incoming == "use-incoming"
    assert availability == "previous-ready"


def test_service_repair_does_not_infer_absence_from_missing_launch_contract() -> None:
    delegate = (POLICY.parent / "AppDelegate.swift").read_text(encoding="utf-8")
    assert "No usable runtime is currently installed." not in delegate
    assert "Configuration already matches the installed generation." not in delegate
    offer = delegate.split("private func offerRuntimePackReinstall(", 1)[1].split(
        "private func presentRuntimePackReinstall(", 1
    )[0]
    assert "resolveInstalledRuntime(forceRefresh: true)" in offer
    assert "serviceFailure: detail.message" in delegate


def test_unresolved_runtime_has_a_diagnostic_without_an_upgrade(
    policy_binary: Path,
) -> None:
    title, action, detail = _run(policy_binary, "reinstall-unresolved")
    assert title == "Runtime installation could not be verified"
    assert action == "<none>"
    assert "receipt could not be read" in detail
    assert "No runtime installation was found" not in detail
    assert "Installed generation:" not in detail


def test_positive_absence_offers_installation(policy_binary: Path) -> None:
    title, action, detail = _run(policy_binary, "reinstall-absent")
    assert title == "Install the Vibecrafted runtime from this App?"
    assert action == "Install"
    assert "No runtime installation was found" in detail
    assert "neither identity document exists" in detail


def test_known_current_generation_has_no_implicit_republish(
    policy_binary: Path,
) -> None:
    title, action, detail = _run(policy_binary, "reinstall-current")
    assert title == "Vibecrafted runtime already current"
    assert action == "<none>"
    assert "Installed generation: 4.3.2+gaaaaaaaa" in detail
    assert "This App carries the same generation" in detail
    assert "Republish anyway…" in detail


def test_a_different_hash_of_the_same_version_is_not_an_upgrade(
    policy_binary: Path,
) -> None:
    title, action, detail = _run(policy_binary, "reinstall-same-version-other-source")
    assert title == "Runtime upgrade is not available"
    assert action == "<none>"
    assert "Installed generation: 4.3.2+gbbbbbbbb" in detail
    assert "not proven newer" in detail


def test_a_verified_newer_carrier_keeps_the_upgrade_choice(policy_binary: Path) -> None:
    title, action, detail = _run(policy_binary, "reinstall-upgrade")
    assert title == "Upgrade the Vibecrafted runtime from this App?"
    assert action == "Upgrade"
    assert "Installed generation: 4.3.1+gaaaaaaaa" in detail


def test_same_generation_republish_requires_the_explicit_choice(
    policy_binary: Path,
) -> None:
    title, action, detail = _run(policy_binary, "reinstall-republish")
    assert title == "Republish the Vibecrafted runtime from this App?"
    assert action == "Republish anyway"
    assert "App carrier generation: 4.3.2+gaaaaaaaa" in detail
    assert "refuses to replace a newer runtime" in detail


def test_service_admission_failure_keeps_its_reason_and_installed_identity(
    policy_binary: Path,
) -> None:
    title, action, detail = _run(policy_binary, "reinstall-service")
    assert title == "Runtime service repair failed"
    assert action == "<none>"
    assert "Installed generation: 4.3.2+gaaaaaaaa" in detail
    assert "Service repair failed: receipt admission refused" in detail
    assert "may not resolve this failure" in detail
    assert "No runtime installation was found" not in detail
