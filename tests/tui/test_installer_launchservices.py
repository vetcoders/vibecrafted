"""LaunchServices cleanup uses receipt paths; no test invokes the host registry."""

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest
from _runtime_pack_fixture import seed_runtime_pack

from scripts import vetcoders_install as installer
from tests.tui import test_runtime_pack_retirement as retirement_tests
from tests.tui.test_runtime_pack_rescue import _ns, _seal_runtime_pack_for_admission

LSREGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    return retirement_tests.roots.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def quiet_census(monkeypatch):
    retirement_tests.quiet_census.__wrapped__(monkeypatch)


def _proof(*bundles):
    return {"entries": {name: ["directory"] for name in bundles}}


def test_targets_use_receipt_inventory_and_exclude_active_and_foreign(tmp_path):
    home = tmp_path / "runtime"
    old = home / "releases/old"
    active = home / "releases/active"
    foreign = tmp_path / "foreign/releases/old"
    unowned = home / "releases/unowned"
    receipt = {"owned_dirs": [str(old), str(active), str(foreign)]}
    proofs = {
        str(old): _proof("libexec/renamed studio.app", "libexec/vc-terminal.app"),
        str(active): _proof("libexec/vc-terminal.app"),
        str(foreign): _proof("libexec/vc-terminal.app"),
        str(unowned): _proof("libexec/vc-terminal.app"),
        "/Applications/Vibecrafted.app": _proof("Other.app"),
    }
    # An added app does not become owned merely by residing in a receipted root.
    (old / "Unreceipted.app").mkdir(parents=True)
    assert installer._runtime_launchservices_targets(
        home, receipt, proofs, active_generation=active
    ) == [old / "libexec/renamed studio.app", old / "libexec/vc-terminal.app"]
    assert installer._runtime_launchservices_targets(home, receipt, proofs) == [
        active / "libexec/vc-terminal.app",
        old / "libexec/renamed studio.app",
        old / "libexec/vc-terminal.app",
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "../outside.app",
        "/Applications/Vibecrafted.app",
        "libexec/../Bad.app",
        "libexec//Bad.app",
        "libexec/./Bad.app",
    ],
)
def test_targets_reject_inventory_escape_and_noncanonical_paths(tmp_path, relative):
    old = tmp_path / "runtime/releases/old"
    assert (
        installer._runtime_launchservices_targets(
            tmp_path / "runtime",
            {"owned_dirs": [str(old)]},
            {str(old): _proof(relative)},
        )
        == []
    )


def test_targets_reject_symlinks_and_file_records(tmp_path):
    home = tmp_path / "runtime"
    old = home / "releases/old"
    old.mkdir(parents=True)
    (old / "libexec").symlink_to(tmp_path)
    proof = _proof("libexec/Foreign.app")
    proof["entries"]["Fake.app"] = ["file"]
    assert (
        installer._runtime_launchservices_targets(
            home, {"owned_dirs": [str(old)]}, {str(old): proof}
        )
        == []
    )


@pytest.fixture
def registry_stub(monkeypatch):
    calls = []
    real_is_file = Path.is_file
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_file", lambda p: p == LSREGISTER or real_is_file(p))

    def run(command, **kwargs):
        assert command[:2] == [str(LSREGISTER), "-u"]
        assert kwargs == {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": 15,
        }
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    return calls, run


def test_runner_receives_exact_paths_including_spaces(tmp_path, registry_stub):
    calls, run = registry_stub
    paths = [tmp_path / "release with spaces/libexec/Studio.app"]
    result = installer._unregister_runtime_bundles(paths, runner=run)
    assert calls == [[str(LSREGISTER), "-u", str(paths[0])]]
    assert result["status"] == "unregistered"
    assert result["unregistered"] == [str(paths[0])]


@pytest.mark.parametrize("dry_run,available", [(True, True), (False, False)])
def test_missing_tool_and_dry_run_do_not_invoke_runner(
    tmp_path, monkeypatch, dry_run, available
):
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_file", lambda _p: available)

    def forbidden(*args, **kwargs):
        pytest.fail("registry side effect")

    result = installer._unregister_runtime_bundles(
        [tmp_path / "Studio.app"], dry_run=dry_run, runner=forbidden
    )
    assert result["status"] == ("dry-run" if dry_run else "skipped")
    if not available:
        assert "unavailable" in result["reason"]


@pytest.mark.parametrize(
    "failure",
    [7, FileNotFoundError("missing"), subprocess.TimeoutExpired("lsregister", 15)],
)
def test_runner_failure_is_a_residual(tmp_path, registry_stub, failure):
    def run(command, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return subprocess.CompletedProcess(command, failure, "", "")

    result = installer._unregister_runtime_bundles(
        [tmp_path / "Studio.app"], runner=run
    )
    assert result["status"] == "residual"
    assert len(result["residuals"]) == 1


def test_runner_rechecks_symlink_before_side_effect(tmp_path, registry_stub):
    calls, run = registry_stub
    target = tmp_path / "Studio.app"
    target.symlink_to(tmp_path / "Foreign.app")
    result = installer._unregister_runtime_bundles([target], runner=run)
    assert result["status"] == "residual" and calls == []


@pytest.mark.parametrize("fail_once", [False, True])
def test_three_upgrades_retire_only_old_bundles_and_uninstall_clears_retained(
    tmp_path, roots, quiet_census, monkeypatch, capsys, fail_once
):
    calls = []
    real_run = installer.subprocess.run
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: p == LSREGISTER or real_is_file(p))

    def run(command, *args, **kwargs):
        nonlocal fail_once
        if command[0] == str(LSREGISTER):
            target = Path(command[2])
            assert target.is_dir(), "unregister must precede deletion"
            active = json.loads((roots["runtime_home"] / "active.json").read_text())
            if not uninstalling:
                assert not str(target).startswith(active["runtime_root"] + "/")
            if fail_once:
                fail_once = False
                return subprocess.CompletedProcess(command, 7, "", "")
            calls.append(target)
            return subprocess.CompletedProcess(command, 0, "", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(installer.subprocess, "run", run)
    # Production sys.platform stays native; this integration seam only emulates
    # LaunchServices availability on platforms that do not provide the registry.
    real_unregister = installer._unregister_runtime_bundles

    def unregister(targets, **kwargs):
        with monkeypatch.context() as context:
            context.setattr(installer.sys, "platform", "darwin")
            return real_unregister(targets, **kwargs)

    monkeypatch.setattr(installer, "_unregister_runtime_bundles", unregister)
    uninstalling = False
    generations = []

    def add_bundle(payload):
        info = payload / "libexec/vc-terminal.app/Contents/Info.plist"
        info.parent.mkdir(parents=True)
        info.write_text("synthetic bundle, never launched")

    # Initial install followed by three upgrades, with one healthy rollback kept.
    for number in range(4):
        payload = seed_runtime_pack(
            tmp_path / f"pack-{number}",
            version=f"9.9.{number}+r4",
        )
        add_bundle(payload)
        _seal_runtime_pack_for_admission(payload)
        assert installer.cmd_runtime_install(_ns(payload)) == 0
        result = json.loads(capsys.readouterr().out.splitlines()[-1])
        generations.append(Path(result["root"]))
        assert json.loads((roots["runtime_home"] / "active.json").read_text())[
            "runtime_root"
        ] == str(generations[-1])
        if number == 2 and result["retirement"]["status"] == "residual":
            assert generations[0].exists(), "failed unregister must remain retryable"
            assert result["retirement"]["launchservices"][0]["status"] == "residual"
    assert calls == [g / "libexec/vc-terminal.app" for g in generations[:2]]
    assert not generations[0].exists() and not generations[1].exists()
    assert generations[2].exists() and generations[3].exists()
    uninstalling = True
    before = list(calls)
    assert installer.cmd_runtime_uninstall(Namespace(dry_run=True, json=True)) == 0
    capsys.readouterr()
    assert calls == before
    assert installer.cmd_runtime_uninstall(Namespace(dry_run=False, json=True)) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert result["launchservices"]["status"] == "unregistered"
    assert calls == [g / "libexec/vc-terminal.app" for g in generations]
