from __future__ import annotations

import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest
from _runtime_pack_fixture import fixed_terminal_fixture, seed_runtime_pack

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("foreign_replacement", [False, True])
def test_interrupted_ancillary_install_resumes_with_local_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    foreign_replacement: bool,
) -> None:
    home = tmp_path / "home"
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(state))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)
    monkeypatch.setattr(installer, "_runtime_service_snapshot", lambda _home: None)
    monkeypatch.setattr(installer, "_darwin_process_ids", tuple)
    fixed_terminal_fixture(monkeypatch, tmp_path)

    def add_owner(payload: Path) -> None:
        package = payload / "vibecrafted-core/vibecrafted_core"
        for filename in ("local_skills.py", "portable_lock.py"):
            shutil.copy2(
                REPO_ROOT / "vibecrafted-core/vibecrafted_core" / filename,
                package / filename,
            )

    first = seed_runtime_pack(
        tmp_path / "first", version="9.9.8+g12345678", before_source_seal=add_owner
    )
    second = seed_runtime_pack(
        tmp_path / "second", version="9.9.9+g12345678", before_source_seal=add_owner
    )
    app = tmp_path / "Vibecrafted.app"
    terminal = app / "Contents/Helpers/vc-terminal"
    frame = app / "Contents/Helpers/vc-frame"
    terminal.parent.mkdir(parents=True)
    for path in (terminal, frame):
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    def args(payload: Path) -> Namespace:
        return Namespace(
            payload_root=str(payload),
            app_root=str(app),
            terminal_host=str(terminal),
            frame_helper=str(frame),
        )

    assert installer.cmd_runtime_install(args(first)) == 0
    capsys.readouterr()
    current = runtime / "tools/vibecrafted-current"
    monkeypatch.setattr(
        installer, "__file__", str(current.resolve() / "scripts/vetcoders_install.py")
    )
    owner = installer._load_local_skill_owner()
    original = owner.read_skill("vc-audit")
    saved = installer.publish_local_skill(
        "vc-audit", "# personal\n", original["revision"]
    )
    assert saved["activation"] == "published"
    selector = state / "local-skills/vc-audit/selected.json"
    selected_before = selector.read_bytes()
    views = [
        installer.runtime_skills_dir(rt) / "vc-audit"
        for rt in installer.STANDARD_VIEW_RUNTIMES
    ]
    targets_before = [view.resolve() for view in views]
    foreign = home / ".grok/skills/vc-audit"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"user-owned skill\n")
    reclaim = installer._reclaim_foreign_launcher_names

    def interrupt(*_args, **_kwargs):
        raise RuntimeError("injected ancillary interruption")

    monkeypatch.setattr(installer, "_reclaim_foreign_launcher_names", interrupt)
    with pytest.raises(RuntimeError, match="injected ancillary interruption"):
        installer.cmd_runtime_install(args(second))
    receipt = json.loads((runtime / installer.RUNTIME_INSTALL_RECEIPT).read_text())
    assert receipt["install_pending"] is True
    assert receipt["install_phase"] == "ancillary"
    assert "config_transaction" not in receipt
    with pytest.raises(RuntimeError, match="pending"):
        owner.read_skill("vc-audit")
    with pytest.raises(RuntimeError, match="pending"):
        owner.validated_local_skill("vc-audit", lease_held=True)
    with pytest.raises(RuntimeError, match="pending"):
        owner.prepare_skill("vc-audit", "cannot publish\n", saved["revision"])

    monkeypatch.setattr(installer, "_reclaim_foreign_launcher_names", reclaim)
    capsys.readouterr()
    codex = installer.runtime_skills_dir("codex") / "vc-audit"
    if foreign_replacement:
        codex.unlink()
        codex.write_bytes(b"foreign replacement\n")
    assert installer.cmd_runtime_install(args(second)) == (
        1 if foreign_replacement else 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["skill_projection_status"] == (
        "partial" if foreign_replacement else "published"
    )
    receipt = json.loads((runtime / installer.RUNTIME_INSTALL_RECEIPT).read_text())
    assert not receipt.get("install_pending")
    assert "config_transaction" not in receipt
    assert selector.read_bytes() == selected_before
    for view, target in zip(views, targets_before, strict=True):
        if view == codex and foreign_replacement:
            assert view.read_bytes() == b"foreign replacement\n"
            assert str(view) in result["skill_projection_conflicts"]
        else:
            assert view.resolve() == target
            assert (view / "SKILL.md").read_text() == "# personal\n"
    assert foreign.read_bytes() == b"user-owned skill\n"
    monkeypatch.setattr(
        installer, "__file__", str(current.resolve() / "scripts/vetcoders_install.py")
    )
    owner = installer._load_local_skill_owner()
    assert owner.read_skill("vc-audit")["text"] == "# personal\n"


@pytest.mark.parametrize(
    "pending",
    [
        {"install_pending": True, "install_phase": "preparing"},
        {"install_pending": False, "install_phase": "ancillary"},
        {
            "install_pending": True,
            "install_phase": "ancillary",
            "config_transaction": {},
        },
        {
            "install_pending": True,
            "install_phase": "ancillary",
            "preparing_previous_receipt": {},
        },
        {
            "install_pending": True,
            "install_phase": "ancillary",
            "config_pending": {"x": {}},
        },
        {"install_pending": True, "install_phase": "ancillary", "rescue_pending": True},
        {
            "install_pending": True,
            "install_phase": "ancillary",
            "uninstall_pending": True,
        },
        {
            "install_pending": True,
            "install_phase": "ancillary",
            "config_conflicts": {"x": {}},
        },
    ],
)
def test_resume_never_admits_unsealed_or_conflicting_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: dict
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path / "runtime"))
    paths = installer._runtime_install_paths()
    pending = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "roots": {key: str(value) for key, value in paths.items()},
        **pending,
    }
    with pytest.raises(RuntimeError, match="sealed ancillary phase"):
        installer._verify_local_skill_install_resume(paths, pending)


def test_resume_requires_actual_inherited_install_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path / "runtime"))
    monkeypatch.delenv("VIBECRAFTED_INSTALL_LEASE_FD", raising=False)
    paths = installer._runtime_install_paths()
    with pytest.raises(OSError, match="lease"):
        installer._verify_local_skill_install_resume(
            paths,
            {
                "schema": installer.RUNTIME_INSTALL_SCHEMA,
                "roots": {key: str(value) for key, value in paths.items()},
                "install_pending": True,
                "install_phase": "ancillary",
            },
        )


def test_resume_rejects_unproven_active_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, state, runtime = tmp_path / "home", tmp_path / "state", tmp_path / "runtime"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(state))
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / "bin"))
    monkeypatch.delenv("VIBECRAFTED_INSTALL_LEASE_FD", raising=False)
    paths = installer._runtime_install_paths()
    current = runtime / "tools/vibecrafted-current"
    outside = tmp_path / "foreign-generation"
    outside.mkdir()
    current.parent.mkdir(parents=True)
    current.symlink_to(outside)
    active = runtime / "active.json"
    active.write_bytes(b"foreign active data\n")
    previous = {
        "schema": installer.RUNTIME_INSTALL_SCHEMA,
        "roots": {key: str(value) for key, value in paths.items()},
        "install_pending": True,
        "install_phase": "ancillary",
        "version": "foreign-generation",
        "owned_symlinks": {str(current): str(outside)},
    }
    with (
        installer._tools_install_lease(current, operation="runtime-install") as fd,
        installer._inherited_tools_install_lease(fd),
        pytest.raises(RuntimeError, match="selectors disagree"),
    ):
        installer._verify_local_skill_install_resume(paths, previous)
    assert current.readlink() == outside
    assert active.read_bytes() == b"foreign active data\n"
