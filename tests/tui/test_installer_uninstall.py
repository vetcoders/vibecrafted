from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer


@pytest.fixture(autouse=True)
def _isolate_uninstall_from_live_runtime(monkeypatch) -> None:
    """A unit-test HOME must never inspect or mutate the host's real LaunchAgent."""
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)
    monkeypatch.setattr(installer, "_runtime_service_snapshot", lambda _home: None)
    monkeypatch.setattr(installer, "_darwin_process_ids", tuple)


def _write_executable(path: Path, body: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body or "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def _setup_installed_surface(
    tmp_path: Path, monkeypatch
) -> tuple[Path, Path, Path, Path, Path]:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    codex_skills = home / ".codex" / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)

    store_path.mkdir(parents=True)
    codex_skills.mkdir(parents=True)

    skill_dir = store_path / "vc-init"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test\n", encoding="utf-8")

    state = installer.InstallState(
        framework_version="9.9.9",
        skills=["vc-init"],
        runtimes=["codex"],
        shell_helpers=True,
    )
    state.save(store_path)

    (codex_skills / "vc-init").symlink_to(skill_dir)

    helper_file = installer._helper_target_path()
    helper_file.parent.mkdir(parents=True, exist_ok=True)
    helper_file.write_text("# helper shim\n", encoding="utf-8")

    zshrc = home / ".zshrc"
    path_line = installer._launcher_path_line()
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{path_line}\n",
        encoding="utf-8",
    )

    for launcher_bin_dir in installer._launcher_bin_dirs():
        launcher_bin_dir.mkdir(parents=True, exist_ok=True)
        _write_executable(
            launcher_bin_dir / "vibecrafted",
            "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
        )
        _write_executable(
            launcher_bin_dir / "vibecraft",
            "#!/usr/bin/env bash\nprintf 'compat\\n'\n",
        )
        for wrapper_name in (
            *installer.LAUNCHER_WRAPPERS,
            *installer.LEGACY_LAUNCHER_NAMES,
        ):
            (launcher_bin_dir / wrapper_name).symlink_to("vibecrafted")
        _write_executable(
            launcher_bin_dir / "unrelated-tool",
            "#!/usr/bin/env bash\nprintf 'keep\\n'\n",
        )

    return home, crafted_home, store_path, helper_file, zshrc


def test_cmd_uninstall_removes_launchers_and_compat_pack_wrappers(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )
    retired_frame = home / ".local" / "bin" / "vc-frame.real"
    _write_executable(retired_frame)

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    assert not helper_file.exists()
    assert not retired_frame.exists()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")
    assert not (store_path / "vc-init").exists()
    assert not (home / ".codex" / "skills" / "vc-init").exists()

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    backed_paths = {Path(item["path"]).name for item in manifest["items"]}
    assert {"marble-pack", "aicx-pack"} <= backed_paths

    for launcher_bin_dir in installer._launcher_bin_dirs():
        for removed_name in (
            "vibecrafted",
            "vibecraft",
            *installer.LAUNCHER_WRAPPERS,
            *installer.LEGACY_LAUNCHER_NAMES,
        ):
            assert not (launcher_bin_dir / removed_name).exists()
            assert not (launcher_bin_dir / removed_name).is_symlink()
        assert (launcher_bin_dir / "unrelated-tool").exists()

    assert not collect_names(installer.collect_installed_launchers())


def test_cmd_uninstall_tears_down_runtime_before_removing_files(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, _helper_file, _zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )
    events: list[str] = []

    def teardown(_shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        assert not dry_run
        assert (store_path / "vc-init").exists()
        events.append("runtime")
        return ("stop owned runtime",)

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert events == ["runtime"]
    assert not (home / ".local" / "bin" / "vibecrafted").exists()


def test_cmd_uninstall_aborts_file_removal_when_runtime_teardown_fails(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, _zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )

    def teardown(_shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        assert not dry_run
        raise OSError("owned supervisor remains")

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 1
    assert (store_path / "vc-init").exists()
    assert helper_file.exists()
    assert (home / ".local" / "bin" / "vibecrafted").exists()


def test_cmd_uninstall_treats_unlinked_retired_process_as_runtime_work(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        (str(home / ".local" / "bin" / "vc-frame.real"), "--server", "/tmp/old"),
    )
    calls: list[tuple[Path, bool]] = []

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)
    monkeypatch.setattr(
        installer, "_retired_vc_frame_process_census", lambda: (record,)
    )

    def teardown(shared_home: Path, *, dry_run: bool) -> tuple[str, ...]:
        calls.append((shared_home, dry_run))
        return ("terminate 1 retired vc-frame.real process(es)",)

    monkeypatch.setattr(installer, "_teardown_owned_runtime_for_uninstall", teardown)

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert calls == [(crafted_home, False)]


def test_retired_vc_frame_census_requires_exact_stable_same_user_argv0(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    retired = home / ".local" / "bin" / "vc-frame.real"
    _write_executable(retired)
    births = {
        101: ("darwin:1:1", os.geteuid(), 8),
        102: ("darwin:2:2", os.geteuid(), 8),
        103: ("darwin:3:3", os.geteuid() + 1, 8),
    }
    arguments = {
        101: (str(retired), "--server", "/tmp/Finalized runs"),
        102: (str(retired.with_name("vc-frame")), "--server", "/tmp/live"),
        103: (str(retired), "--server", "/tmp/foreign"),
    }

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_darwin_process_ids", lambda: (101, 102, 103))
    monkeypatch.setattr(installer, "_darwin_process_birth", births.__getitem__)
    monkeypatch.setattr(
        installer,
        "_darwin_process_arguments",
        lambda pid, *, pointer_size: arguments[pid],
    )

    assert installer._retired_vc_frame_process_census() == (
        installer._RetiredVcFrameProcess(101, births[101], arguments[101]),
    )


def test_terminate_retired_vc_frame_reproves_identity_before_signal(
    monkeypatch,
) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/finalized"),
    )
    alive = {101: True}
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda observed: alive[observed.pid],
    )

    def kill(pid: int, sent_signal: int) -> None:
        signals.append((pid, sent_signal))
        alive[pid] = False

    monkeypatch.setattr(installer.os, "kill", kill)

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == [(101, installer.signal.SIGTERM)]


def test_terminate_retired_vc_frame_escalates_stubborn_exact_process(
    monkeypatch,
) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/failed"),
    )
    alive = {101: True}
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda observed: alive[observed.pid],
    )

    def kill(pid: int, sent_signal: int) -> None:
        signals.append((pid, sent_signal))
        if sent_signal == installer.signal.SIGKILL:
            alive[pid] = False

    monkeypatch.setattr(installer.os, "kill", kill)

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == [
        (101, installer.signal.SIGTERM),
        (101, installer.signal.SIGKILL),
    ]


def test_terminate_retired_vc_frame_does_not_signal_reused_pid(monkeypatch) -> None:
    record = installer._RetiredVcFrameProcess(
        101,
        ("darwin:1:1", os.geteuid(), 8),
        ("/Users/test/.local/bin/vc-frame.real", "--server", "/tmp/old"),
    )
    signals: list[tuple[int, int]] = []

    monkeypatch.setattr(
        installer,
        "_retired_vc_frame_process_still_matches",
        lambda _observed: False,
    )
    monkeypatch.setattr(
        installer.os,
        "kill",
        lambda pid, sent_signal: signals.append((pid, sent_signal)),
    )

    installer._terminate_retired_vc_frame_processes((record,), timeout_seconds=0)

    assert signals == []


def test_runtime_teardown_uninstalls_owned_service_and_proves_quiescence(
    tmp_path: Path, monkeypatch
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    launcher = tmp_path / "bin" / "vibecrafted"
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=42,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    snapshots = iter(((launcher, healthy, "running"), (launcher, quiescent, "stopped")))
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer, "_current_tools_link", lambda _home: tmp_path / "current"
    )
    monkeypatch.setattr(
        installer,
        "_tools_install_lease",
        lambda _link, *, operation: nullcontext(9),
    )
    monkeypatch.setattr(
        installer, "_inherited_tools_install_lease", lambda _descriptor: nullcontext()
    )
    monkeypatch.setattr(installer.os, "set_inheritable", lambda _fd, _value: None)
    monkeypatch.setattr(
        installer, "_assert_runtime_loaded_service_owner", lambda _home: shared_home
    )
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: True)
    monkeypatch.setattr(
        installer, "_runtime_service_snapshot", lambda _home: next(snapshots)
    )
    monkeypatch.setattr(installer, "_retired_vc_frame_process_census", tuple)

    def run_command(
        _launcher: Path, _home: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", run_command)

    actions = installer._teardown_owned_runtime_for_uninstall(
        shared_home, dry_run=False
    )

    assert actions == ("stop and uninstall owned runtime service",)
    assert commands == [("service", "uninstall")]


def test_runtime_teardown_does_not_probe_launcher_without_service_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    shared_home = tmp_path / ".vibecrafted"

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer, "_current_tools_link", lambda _home: tmp_path / "current"
    )
    monkeypatch.setattr(
        installer,
        "_tools_install_lease",
        lambda _link, *, operation: nullcontext(9),
    )
    monkeypatch.setattr(
        installer, "_inherited_tools_install_lease", lambda _descriptor: nullcontext()
    )
    monkeypatch.setattr(installer.os, "set_inheritable", lambda _fd, _value: None)
    monkeypatch.setattr(installer, "_runtime_service_has_evidence", lambda _home: False)
    monkeypatch.setattr(installer, "_retired_vc_frame_process_census", tuple)

    def forbidden_snapshot(_home: Path):
        raise AssertionError("service status must not run without service evidence")

    monkeypatch.setattr(installer, "_runtime_service_snapshot", forbidden_snapshot)

    assert (
        installer._teardown_owned_runtime_for_uninstall(shared_home, dry_run=False)
        == ()
    )


def test_cmd_uninstall_removes_release_contract_assets_with_managed_payload(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _setup_installed_surface(tmp_path, monkeypatch)

    tools_root = installer.vibecrafted_tools_home()
    managed_payload = tools_root / "vibecrafted-9.9.9"
    package_root = managed_payload / "vibecrafted-core" / "vibecrafted_core"
    contract_assets = [
        package_root / relative
        for relative in installer.RELEASE_CONTRACT_PACKAGE_ASSETS
    ]
    expected_bytes: dict[Path, bytes] = {}
    for asset in contract_assets:
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(f"fixture for {asset.name}\n", encoding="utf-8")
        expected_bytes[asset] = asset.read_bytes()

    unmanaged_sibling = tools_root / "operator-owned-tools"
    unmanaged_sibling.mkdir(parents=True)
    (unmanaged_sibling / "keep.txt").write_text("keep\n", encoding="utf-8")

    captured_inventory: list[installer.ManagedPath] = []
    build_inventory = installer._build_uninstall_inventory

    def capture_inventory(**kwargs) -> list[installer.ManagedPath]:
        inventory = build_inventory(**kwargs)
        captured_inventory[:] = inventory
        return inventory

    monkeypatch.setattr(installer, "_build_uninstall_inventory", capture_inventory)
    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0

    output = capsys.readouterr().out
    assert f"staged-payload: {managed_payload}" in output
    assert f"tools-sibling: {unmanaged_sibling}" in output
    payload_owners = [
        record
        for record in captured_inventory
        if record.kind == "staged-payload" and record.path == managed_payload
    ]
    assert len(payload_owners) == 1
    owner = payload_owners[0]
    assert owner.action == "remove"
    for asset in contract_assets:
        assert (
            asset.relative_to(owner.path)
            .as_posix()
            .startswith("vibecrafted-core/vibecrafted_core/")
        )
    assert not managed_payload.exists()
    assert all(not asset.exists() for asset in contract_assets)
    assert (unmanaged_sibling / "keep.txt").read_text(encoding="utf-8") == "keep\n"

    store_path = installer.vibecrafted_home() / "skills"
    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    restore_manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    owner_records = [
        item for item in restore_manifest["items"] if Path(item["path"]) == owner.path
    ]
    assert len(owner_records) == 1

    assert installer.cmd_restore(Namespace(dry_run=False)) == 0
    assert {asset: asset.read_bytes() for asset in contract_assets} == expected_bytes
    assert (unmanaged_sibling / "keep.txt").read_text(encoding="utf-8") == "keep\n"


def test_cmd_uninstall_prefers_manifest_tracked_launchers_and_helpers(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    local_bin = home / ".local" / "bin"
    runtime_skills = home / ".codex" / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)

    store_path.mkdir(parents=True)
    runtime_skills.mkdir(parents=True)
    local_bin.mkdir(parents=True)

    skill_dir = store_path / "vc-init"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# test\n", encoding="utf-8")
    (runtime_skills / "vc-init").symlink_to(skill_dir)

    helper_file = installer._helper_target_path()
    helper_file.parent.mkdir(parents=True, exist_ok=True)
    helper_file.write_text("# helper shim\n", encoding="utf-8")
    manual_helper = installer._helper_legacy_path()
    manual_helper.parent.mkdir(parents=True, exist_ok=True)
    manual_helper.write_text("# user helper\n", encoding="utf-8")

    for launcher in ("vibecrafted", "vc-help", "vc-workflow", "telemetry"):
        if launcher == "vibecrafted":
            _write_executable(
                local_bin / launcher,
                "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
            )
        else:
            (local_bin / launcher).symlink_to("vibecrafted")

    (local_bin / "unrelated-tool").write_text("echo keep\n", encoding="utf-8")

    zshrc = home / ".zshrc"
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{installer._launcher_path_line()}\n",
        encoding="utf-8",
    )

    manifest_state = installer.InstallState(
        framework_version="9.9.9",
        skills=["vc-init"],
        runtimes=["codex"],
        launcher_entries=[f"{installer._launcher_dir_key(local_bin)}/vibecrafted"],
        helper_files=[str(helper_file)],
        shell_helpers=True,
    )
    manifest_state.save(store_path)

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    assert not helper_file.exists()
    assert manual_helper.exists()
    assert not (local_bin / "vibecrafted").exists()
    assert (local_bin / "vc-help").is_symlink()
    assert (local_bin / "vc-workflow").is_symlink()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")
    assert not (runtime_skills / "vc-init").exists()
    assert (local_bin / "unrelated-tool").exists()

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    backed_paths = {Path(item["path"]).name for item in manifest["items"]}
    assert "vc-skills.sh" in backed_paths
    assert "vc-help" not in backed_paths


def test_restore_roundtrip_recovers_launchers_and_runtime_symlinks(
    tmp_path: Path, monkeypatch
) -> None:
    home, _crafted_home, store_path, helper_file, zshrc = _setup_installed_surface(
        tmp_path, monkeypatch
    )

    assert installer.cmd_uninstall(Namespace(dry_run=False)) == 0
    assert installer.cmd_restore(Namespace(dry_run=False)) == 0

    assert helper_file.exists()
    assert installer._launcher_path_line() in zshrc.read_text(encoding="utf-8")

    runtime_link = home / ".codex" / "skills" / "vc-init"
    assert runtime_link.is_symlink()
    assert runtime_link.readlink() == store_path / "vc-init"

    for launcher_bin_dir in installer._launcher_bin_dirs():
        assert (launcher_bin_dir / "vibecrafted").exists()
        assert (launcher_bin_dir / "vibecraft").exists()
        for restored_name in (
            "vc-help",
            "vc-workflow",
            "telemetry",
            "marble-pack",
            "aicx-pack",
        ):
            restored = launcher_bin_dir / restored_name
            assert restored.is_symlink()
            assert restored.readlink() == Path("vibecrafted")
        assert (launcher_bin_dir / "unrelated-tool").exists()

    restored_launchers = collect_names(installer.collect_installed_launchers())
    assert "marble-pack" in restored_launchers
    assert "aicx-pack" in restored_launchers
    assert "vibecrafted" in restored_launchers


def test_cmd_uninstall_cleans_launcher_only_surface_without_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(installer, "_IS_TTY", False)
    monkeypatch.setattr(installer, "_known_bundle_names", list)

    for launcher_bin_dir in installer._launcher_bin_dirs():
        launcher_bin_dir.mkdir(parents=True, exist_ok=True)
        _write_executable(
            launcher_bin_dir / "vibecrafted",
            "#!/usr/bin/env bash\nprintf 'launcher\\n'\n",
        )
        (launcher_bin_dir / "vc-help").symlink_to("vibecrafted")
        (launcher_bin_dir / "vc-workflow").symlink_to("vibecrafted")

    zshrc = home / ".zshrc"
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{installer._launcher_path_line()}\n",
        encoding="utf-8",
    )

    exit_code = installer.cmd_uninstall(Namespace(dry_run=False))

    assert exit_code == 0
    for launcher_bin_dir in installer._launcher_bin_dirs():
        assert not (launcher_bin_dir / "vibecrafted").exists()
        assert not (launcher_bin_dir / "vc-help").exists()
        assert not (launcher_bin_dir / "vc-workflow").exists()
    assert installer._launcher_path_line() not in zshrc.read_text(encoding="utf-8")

    backup_root = installer._backup_root(store_path)
    latest = (backup_root / "latest").read_text(encoding="utf-8").strip()
    manifest = json.loads(
        (backup_root / latest / "restore-manifest.json").read_text(encoding="utf-8")
    )
    assert any(Path(item["path"]).name == "vibecrafted" for item in manifest["items"])


def collect_names(entries: list[tuple[Path, Path]]) -> set[str]:
    return {entry.name for _, entry in entries}


def test_deck_launcher_forwards_uninstall_argv(tmp_path, monkeypatch) -> None:
    """`vibecrafted uninstall --dry-run` must reach the installer with the flag intact.

    The deck once called `python3 "$installer" uninstall` without `"$@"`, so a
    dry-run request executed a real, unconfirmed teardown (2026-08-19 incident).
    """
    repo_root = Path(__file__).resolve().parents[2]
    deck = repo_root / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
    capture = tmp_path / "argv.txt"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE_FILE"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CAPTURE_FILE"] = str(capture)
    result = subprocess.run(
        ["bash", str(deck), "uninstall", "--dry-run"],
        check=False,
        env=env,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    argv = capture.read_text(encoding="utf-8").splitlines()
    assert argv[-2:] == ["uninstall", "--dry-run"], argv
