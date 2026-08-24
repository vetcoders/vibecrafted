from __future__ import annotations

import json
import subprocess
from pathlib import Path

from vibecrafted_core import doctor


def _completed(argv: list[str], stdout: str, rc: int = 0, stderr: str = "") -> object:
    return subprocess.CompletedProcess(list(argv), rc, stdout, stderr)


def _write_version(tmp_path: Path, text: str = "4.1.0\n") -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "VERSION").write_text(text, encoding="utf-8")
    return root


def _runner_for(*, latest: str, conclusion: str = "success", status: str = "completed"):
    def _run(argv, **_kwargs):
        if "release" in argv and "view" in argv:
            return _completed(argv, json.dumps({"tagName": latest}) + "\n")
        if "run" in argv and "list" in argv:
            return _completed(
                argv,
                json.dumps(
                    [
                        {
                            "conclusion": conclusion,
                            "status": status,
                            "displayTitle": latest,
                            "databaseId": 42,
                            "headSha": "abc",
                        }
                    ]
                )
                + "\n",
            )
        raise AssertionError(f"unexpected gh argv: {argv}")

    return _run


def test_release_tag_from_version_strips_stamp_and_v_prefix() -> None:
    assert doctor._release_tag_from_version("4.1.0") == "v4.1.0"
    assert doctor._release_tag_from_version("v4.1.0") == "v4.1.0"
    assert doctor._release_tag_from_version("4.1.0+g237d2814") == "v4.1.0"
    assert doctor._release_tag_from_version("unknown") == ""
    assert doctor._release_tag_from_version("") == ""


def test_release_probe_warns_loudly_when_gh_is_missing(tmp_path: Path) -> None:
    called: list[list[str]] = []

    def _run(argv, **_kwargs):
        called.append(list(argv))
        raise AssertionError("gh must not be invoked when it is missing")

    findings = doctor._release_drift_findings(
        which=lambda _name: None,
        runner=_run,
        repo_root=_write_version(tmp_path),
    )

    assert called == []
    latest = [f for f in findings if f.component == "release:github-latest"]
    gate = [f for f in findings if f.component == "release:source-gate"]
    assert latest and latest[0].level == "warn"
    assert gate and gate[0].level == "warn"
    assert "not a green pass" in latest[0].message
    assert "operator button: tag/publish" in latest[0].message
    assert all(f.level != "ok" or f.component == "release:version" for f in findings)


def test_release_probe_invokes_gh_release_view_and_source_gate(
    tmp_path: Path,
) -> None:
    seen: list[list[str]] = []

    def _run(argv, **_kwargs):
        seen.append(list(argv))
        return _runner_for(latest="v4.1.0")(argv)

    doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_run,
        repo_root=_write_version(tmp_path),
        release_repo="vetcoders/vibecrafted",
    )

    assert seen[0] == [
        "/usr/bin/gh",
        "release",
        "view",
        "--repo",
        "vetcoders/vibecrafted",
        "--json",
        "tagName",
    ]
    assert seen[1][:8] == [
        "/usr/bin/gh",
        "run",
        "list",
        "--repo",
        "vetcoders/vibecrafted",
        "--workflow",
        "Release source gate",
        "--limit",
    ]
    assert "1" in seen[1]


def test_release_probe_fails_when_github_latest_drifts(tmp_path: Path) -> None:
    findings = doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_runner_for(latest="v3.5.0"),
        repo_root=_write_version(tmp_path, "4.1.0\n"),
    )

    latest = [f for f in findings if f.component == "release:github-latest"]
    assert len(latest) == 1
    assert latest[0].level == "fail"
    assert "4.1.0" in latest[0].message
    assert "v3.5.0" in latest[0].message
    assert "operator button: tag/publish" in latest[0].message


def test_release_probe_ok_when_latest_and_source_gate_agree(tmp_path: Path) -> None:
    findings = doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_runner_for(latest="v4.1.0", conclusion="success"),
        repo_root=_write_version(tmp_path, "4.1.0\n"),
    )

    by_component = {f.component: f for f in findings}
    assert by_component["release:version"].level == "ok"
    assert by_component["release:github-latest"].level == "ok"
    assert by_component["release:source-gate"].level == "ok"
    assert all(f.level != "fail" for f in findings)


def test_release_probe_fails_when_source_gate_conclusion_is_not_success(
    tmp_path: Path,
) -> None:
    findings = doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_runner_for(latest="v4.1.0", conclusion="failure"),
        repo_root=_write_version(tmp_path, "4.1.0\n"),
    )

    gate = [f for f in findings if f.component == "release:source-gate"]
    assert len(gate) == 1
    assert gate[0].level == "fail"
    assert "failure" in gate[0].message
    assert "operator button: tag/publish" in gate[0].message


def test_release_probe_fails_when_gh_release_view_errors(tmp_path: Path) -> None:
    def _run(argv, **_kwargs):
        if "release" in argv:
            return _completed(argv, "", rc=1, stderr="HTTP 401")
        if "run" in argv:
            return _completed(argv, "[]\n")
        raise AssertionError(argv)

    findings = doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_run,
        repo_root=_write_version(tmp_path),
    )

    latest = [f for f in findings if f.component == "release:github-latest"]
    assert latest[0].level == "fail"
    assert "HTTP 401" in latest[0].message
    assert latest[0].level != "ok"


def test_release_probe_never_calls_live_subprocess(tmp_path: Path, monkeypatch) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be used in this test")

    monkeypatch.setattr(doctor.subprocess, "run", _boom)
    findings = doctor._release_drift_findings(
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
        runner=_runner_for(latest="v4.1.0"),
        repo_root=_write_version(tmp_path),
    )
    assert any(f.component == "release:github-latest" for f in findings)


def test_doctor_run_omits_release_probe_by_default(monkeypatch) -> None:
    expected = doctor._Finding("ok", "runtime", "ready")
    sentinel = doctor._Finding("fail", "release:github-latest", "should not appear")

    def missing_installer() -> None:
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor, "_installer_module", missing_installer)
    monkeypatch.setattr(doctor, "_packaged_asset_findings", list)
    monkeypatch.setattr(doctor, "_launcher_shim_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_launcher_findings", list)
    monkeypatch.setattr(doctor, "_codex_mcp_config_findings", list)
    monkeypatch.setattr(doctor, "_server_supervision_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_delivery_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_truth_drift_findings", lambda: [expected])
    monkeypatch.setattr(doctor, "_release_drift_findings", lambda: [sentinel])

    assert doctor.doctor_run() == [expected]


def test_doctor_run_includes_release_probe_when_requested(monkeypatch) -> None:
    expected = doctor._Finding("fail", "release:github-latest", "VERSION ≠ Latest")

    def missing_installer() -> None:
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor, "_installer_module", missing_installer)
    monkeypatch.setattr(doctor, "_packaged_asset_findings", list)
    monkeypatch.setattr(doctor, "_launcher_shim_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_launcher_findings", list)
    monkeypatch.setattr(doctor, "_codex_mcp_config_findings", list)
    monkeypatch.setattr(doctor, "_server_supervision_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_delivery_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_truth_drift_findings", list)
    monkeypatch.setattr(doctor, "_release_drift_findings", lambda: [expected])

    assert doctor.doctor_run(release=True) == [expected]
