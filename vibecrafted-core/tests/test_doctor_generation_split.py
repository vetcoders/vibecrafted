from __future__ import annotations

import subprocess
from pathlib import Path

from vibecrafted_core import doctor
from vibecrafted_core.runtime_paths import GenerationResolutionError

ACTIVE = Path("/runtime/releases/4.3.1+gb9dce88f")
STALE_EXE = "/runtime/releases/4.3.1+gbd03673c/libexec/vc-frame"
ACTIVE_EXE = "/runtime/releases/4.3.1+gb9dce88f/libexec/vc-frame"


def _ps_runner(output: str, rc: int = 0):
    def runner(argv, **_kwargs):
        assert argv == ["ps", "-axo", "pid=,comm="]
        return subprocess.CompletedProcess(
            argv, rc, stdout=output, stderr="" if rc == 0 else "ps exploded"
        )

    return runner


def _findings(
    ps_output: str,
    *,
    rc: int = 0,
    resolver=ACTIVE,
    which=lambda _name: None,
):
    if isinstance(resolver, Path):
        active = resolver
        resolver = lambda: active
    return doctor._vc_frame_generation_split_findings(
        which=which,
        runner=_ps_runner(ps_output, rc),
        generation_resolver=resolver,
    )


def test_split_reports_stale_pid_and_generation() -> None:
    ps_output = f"  101 {ACTIVE_EXE}\n  202 {STALE_EXE}\n"

    findings = _findings(ps_output)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "warn"
    assert finding.component == "vc-frame:generation-split"
    assert "SPLIT" in finding.message
    assert "202" in finding.message
    assert "4.3.1+gbd03673c" in finding.message
    assert "4.3.1+gb9dce88f" in finding.message
    assert "101" not in finding.message.split("stale generations:", 1)[1]


def test_split_mentions_diagnose_which_hint_only_when_present() -> None:
    ps_output = f"  202 {STALE_EXE}\n"

    with_hint = _findings(ps_output, which=lambda name: "/usr/local/bin/diagnose-which")
    without_hint = _findings(ps_output)

    assert "diagnose-which vc-frame" in with_hint[0].message
    assert "diagnose-which" not in without_hint[0].message


def test_matches_when_all_live_servers_run_active_generation() -> None:
    ps_output = f"  101 {ACTIVE_EXE}\n  303 {ACTIVE_EXE}\n"

    findings = _findings(ps_output)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "ok"
    assert "match the active generation" in finding.message
    assert "4.3.1+gb9dce88f" in finding.message


def test_no_servers_claims_nothing() -> None:
    findings = _findings("  1 /sbin/launchd\n  42 /usr/sbin/syslogd\n")

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "ok"
    assert "nothing claimed" in finding.message


def test_inconclusive_when_process_scan_fails() -> None:
    findings = _findings("", rc=1)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "warn"
    assert "inconclusive" in finding.message
    assert "process scan unavailable" in finding.message


def test_inconclusive_when_ps_binary_missing() -> None:
    def runner(argv, **_kwargs):
        raise OSError("No such file or directory: 'ps'")

    findings = doctor._vc_frame_generation_split_findings(
        which=lambda _name: None,
        runner=runner,
        generation_resolver=lambda: ACTIVE,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "warn"
    assert "inconclusive" in finding.message


def test_inconclusive_when_active_generation_unresolvable() -> None:
    def resolver() -> Path:
        raise GenerationResolutionError("no active Runtime Pack generation")

    findings = _findings(f"  202 {STALE_EXE}\n", resolver=resolver)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "warn"
    assert "inconclusive" in finding.message
    assert "cannot resolve the active generation" in finding.message


def test_live_server_parser_ignores_non_vc_frame_rows() -> None:
    ps_output = (
        "  7 /runtime/releases/4.3.1+gb9dce88f/bin/vc-frame-wrapper\n"
        "  8 vc-frame\n"
        f"  101 {ACTIVE_EXE}\n"
        "garbage line without pid\n"
    )

    servers, unresolved = doctor._live_vc_frame_servers(ps_output)

    assert servers == [(101, ACTIVE_EXE)]
    assert unresolved == [8]


def test_inconclusive_when_server_path_unresolvable() -> None:
    ps_output = "  8 vc-frame\n"

    findings = _findings(ps_output)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.level == "warn"
    assert "inconclusive" in finding.message
    assert "8" in finding.message


def test_generation_label_unknown_outside_releases_layout(tmp_path: Path) -> None:
    orphan = tmp_path / "somewhere" / "vc-frame"

    assert doctor._vc_frame_server_generation(str(orphan)) == "unknown"


def test_generation_label_from_version_ancestor(tmp_path: Path) -> None:
    generation = tmp_path / "vibecrafted-generation-9.9.9+gtest"
    exe = generation / "libexec" / "vc-frame"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"\x7fELF")
    (generation / "VERSION").write_text("9.9.9+gtest\n", encoding="utf-8")

    assert doctor._vc_frame_server_generation(str(exe)) == generation.name
