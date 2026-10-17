"""Project routing through Workshop, the shipped shell and the real WES owner.

Only Frame/native execution is inert. All homes, catalogs, generation files and
engine inventory belong to the fixture; no service or provider is started.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# pytest uses importlib mode, so load the shared fixture by its physical file.
_SCENE_SPEC = importlib.util.spec_from_file_location(
    "project_destination_scene",
    Path(__file__).with_name("test_start_workspace_contract.py"),
)
assert _SCENE_SPEC is not None and _SCENE_SPEC.loader is not None
_scene_module = importlib.util.module_from_spec(_SCENE_SPEC)
_SCENE_SPEC.loader.exec_module(_scene_module)
IDENTITY_ENV = _scene_module.IDENTITY_ENV
REPO_ROOT = _scene_module.REPO_ROOT
SHELL_SH = _scene_module.SHELL_SH
SOURCE_CORE_DIR = _scene_module.SOURCE_CORE_DIR
VC_FRAME_STUB = _scene_module.VC_FRAME_STUB
Scene = _scene_module.Scene
_write = _scene_module._write

WORKSHOP_SOURCE = (
    SOURCE_CORE_DIR / "vibecrafted_core/config/vc-frame/vc-agent-workshop.py"
)

PROJECT_RECEIPT = (
    '_vetcoders_product_core_cli workspace resolve --root "$VIBECRAFTED_WORKSPACE_ROOT" '
    '--runtime-session-id "$VIBECRAFTED_OPERATOR_SESSION" '
    '--socket-dir "$(_vetcoders_vc_frame_socket_dir)" --runtime-state live --json'
)


@pytest.fixture
def public_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Import the source surface before the fixture selects its private runtime.
    for key in IDENTITY_ENV:
        monkeypatch.delenv(key, raising=False)
    fixture_core = (
        Path(os.environ.get("VC_DESTINATION_SOURCE_ROOT", str(REPO_ROOT)))
        / "vibecrafted-core"
    )
    fixture_shell = fixture_core / "vibecrafted_core/runtime/shell/vetcoders.sh"
    source = fixture_core / "vibecrafted_core/config/vc-frame/vc-agent-workshop.py"
    spec = importlib.util.spec_from_file_location(
        "project_destination_workshop", source
    )
    assert spec is not None and spec.loader is not None
    workshop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workshop)
    from vibecrafted_core import workspace_catalog as wc

    scene = Scene(tmp_path, project="selected", live=("vc-host",))
    env = scene.env(
        {
            "VIBECRAFTED_RUNTIME_ROOT": str(scene.generation),
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
            "VC_FRAME_SESSION_NAME": "vc-host",
            "VC_FRAME_PANE_ID": "terminal_1",
            "VC_FRAME": "0",
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "PATH": str(scene.generation / "bin") + os.pathsep + os.environ["PATH"],
        }
    )
    env.pop("VIBECRAFTED_PRODUCT_CORE_CLI", None)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("VIBECRAFTED_PRODUCT_CORE_CLI", raising=False)
    monkeypatch.chdir(scene.home)
    (scene.generation / "VERSION").write_text("4.4.0+g00000000\n")
    _write(
        scene.generation / "bin/python3",
        f'#!/bin/bash\nexec {shlex.quote(sys.executable)} "$@"\n',
    )
    frame = VC_FRAME_STUB.replace(
        "#!/usr/bin/env python3", f"#!{sys.executable}", 1
    ).replace(
        'if verb == "switch-session":',
        'if verb == "new-tab":\n'
        '        record({"opened": target})\n'
        "        sys.exit(0)\n"
        '    if verb == "switch-session":',
    )
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        _write(scene.generation / relative, frame)
    for relative in (
        "config.kdl",
        "pane-python",
        "vc-start-here.py",
        "vc-agent-workshop.py",
    ):
        _write(scene.config_dir / relative, "#!/bin/bash\nexit 0\n")
    facade = (
        scene.generation
        / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
    )
    _write(
        facade,
        f"export VIBECRAFTED_CORE_DIR={shlex.quote(str(fixture_core))}\n"
        f"source {shlex.quote(str(fixture_shell))} || return $?\n"
        f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(REPO_ROOT))}\n"
        "_vetcoders_owned_core_dir() { printf '%s\\n' "
        + shlex.quote(str(fixture_core))
        + "; }\n"
        "_vetcoders_owned_python_bin() { printf '%s\\n' "
        + shlex.quote(sys.executable)
        + "; }\n"
        "_vetcoders_vc_frame_config_dir() { printf '%s\\n' "
        + shlex.quote(str(scene.config_dir))
        + "; }\n"
        "_vetcoders_control_plane_eye_prepare() { :; }\n",
    )

    def bind(root: Path, name: str, *, physical: bool = True, fresh: bool = False):
        identity = wc.resolve_run_workspace_identity(root=root, env={})
        if fresh:
            identity = wc.materialize_instance(
                workspace_id=identity.workspace_id,
                root=root,
                vibecrafted_session_id=wc.new_uuid7(),
                build_id=identity.build_id,
            )
        wc.record_runtime_session_attachment(
            workspace_id=identity.workspace_id,
            vibecrafted_session_id=identity.vibecrafted_session_id,
            workspace_instance_id=identity.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id=name,
            socket_dir=env["VC_FRAME_SOCKET_DIR"],
            state="live",
        )
        if physical:
            (scene.table / "live" / name).write_text("layout { pane; }\n")
        return identity

    def open_agent(requested: str):
        admitted = workshop.ensure_live_destination(scene.root, requested, scene.live())
        command = workshop.launch_pane_argv(
            "private fixture Agent",
            scene.root,
            ["fixture-provider-must-never-execute"],
            session=admitted.session,
        )
        command[0] = str(scene.generation / "bin/vc-frame")
        result = subprocess.run(
            command,
            env=admitted.environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        return admitted

    return SimpleNamespace(
        scene=scene,
        workshop=workshop,
        wc=wc,
        bind=bind,
        open_agent=open_agent,
        socket_dir=env["VC_FRAME_SOCKET_DIR"],
        facade=facade,
    )


def _catalog_bytes(fixture) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in Path(os.environ["VIBECRAFTED_HOME"]).rglob("*.json")
    }


def _creates(fixture) -> list[dict]:
    return [call for call in fixture.scene.calls() if call.get("created")]


def _opened(fixture) -> list[dict]:
    return [call for call in fixture.scene.calls() if call.get("opened")]


def _rename_capable_frame(fixture) -> None:
    """Extend only the inert engine with Frame's public rename/incarnation contract."""
    frame = fixture.scene.generation / "bin/vc-frame"
    body = frame.read_text()
    marker = '    if verb == "new-tab":'
    extension = """    if verb == "list-tabs":
        incarnation = open(os.path.join(table, "incarnations", target)).read().strip()
        print(json.dumps([{"tab_id": 1, "session_incarnation": incarnation}]))
        sys.exit(0)
    if verb == "rename-session":
        wanted = rest[2]
        os.rename(os.path.join(table, "live", target), os.path.join(table, "live", wanted))
        os.rename(os.path.join(table, "incarnations", target), os.path.join(table, "incarnations", wanted))
        sys.exit(0)
"""
    assert marker in body
    body = body.replace(
        "    os.close(fd)",
        "    os.close(fd)\n"
        "    import uuid\n"
        "    with open(os.path.join(table, 'incarnations', name), 'w') as handle:\n"
        "        handle.write(str(uuid.uuid4()))",
    )
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        _write(
            fixture.scene.generation / relative,
            body.replace(marker, extension + marker),
        )
    (fixture.scene.table / "incarnations").mkdir()


@pytest.mark.parametrize("inside", [True, False])
def test_actual_frame_rename_reuses_original_binding(
    public_destination, monkeypatch, inside
):
    f = public_destination
    _rename_capable_frame(f)
    name = "seat-a"
    initial = f.open_agent(name)
    assert initial.session == name
    identity = f.wc.resolve_run_workspace_identity(
        root=f.scene.root,
        env={},
        runtime_session_id=name,
        socket_dir=f.socket_dir,
        runtime_state="live",
    )
    before = f.wc.read_workspace_session(identity.vibecrafted_session_id)
    if inside:
        for key, value in identity.to_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("VC_FRAME_SESSION_NAME", name)
        monkeypatch.setenv("ZELLIJ_SESSION_NAME", name)
    for renamed in ("seat-b", "seat-c"):
        subprocess.run(
            [
                str(f.scene.generation / "bin/vc-frame"),
                "--session",
                name,
                "action",
                "rename-session",
                renamed,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        requested = f.workshop.destination_session_for_workspace(f.scene.root)
        admitted = f.open_agent(requested)
        assert admitted.session == renamed
        if inside:
            assert f.workshop.current_frame_session() == renamed
            assert f.workshop.owned_current_destination(f.scene.root) == renamed
        assert [call["created"] for call in _creates(f)] == ["seat-a"]
        actual = f.wc.resolve_run_workspace_identity(
            root=f.scene.root,
            env={},
            runtime_session_id=renamed,
            socket_dir=f.socket_dir,
            runtime_state="live",
        )
        assert actual.to_env() == identity.to_env()
        assert f.wc.read_workspace_session(identity.vibecrafted_session_id) == before
        name = renamed
    assert [call["opened"] for call in _opened(f)] == ["seat-a", "seat-b", "seat-c"]


def _commit_private_project_revision(fixture, content: str) -> None:
    root = fixture.scene.root
    assert root.is_relative_to(fixture.scene.tmp_path)
    (root / "README").write_text(content)
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "project-destination-fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "project-destination-fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    for command in (
        ["git", "init", "-q", str(root)],
        ["git", "-C", str(root), "add", "README"],
        ["git", "-C", str(root), "commit", "-q", "-m", "private fixture revision"],
    ):
        subprocess.run(command, env=env, check=True, capture_output=True, timeout=10)


@pytest.mark.parametrize("project_live", [True, False])
@pytest.mark.parametrize("changed_build", [False, True])
def test_workshop_bad_host_wes_never_substitutes_for_real_project_seat(
    public_destination,
    monkeypatch: pytest.MonkeyPatch,
    project_live: bool,
    changed_build: bool,
) -> None:
    f = public_destination
    if changed_build:
        _commit_private_project_revision(f, "before\n")
    # Model the observed legacy pollution: the singleton host has a valid WES
    # pair for this same root, alongside a distinct real project incarnation.
    host = f.bind(f.scene.root, "vc-host", physical=False)
    project = f.bind(
        f.scene.root, "real-project-seat", physical=project_live, fresh=True
    )
    for key, value in host.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VIBECRAFTED_OPERATOR_SESSION", "vc-host")
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(f.scene.root))
    host_paths = (
        f.wc.workspace_session_path(host.vibecrafted_session_id),
        f.wc.instance_path(host.workspace_instance_id),
    )
    before = {path: path.read_bytes() for path in host_paths}
    frame_before = (f.scene.table / "live/vc-host").read_bytes()
    if changed_build:
        _commit_private_project_revision(f, "after\n")
    requested = f.workshop.destination_session_for_workspace(f.scene.root)
    admitted = f.open_agent(requested)
    assert admitted.session == "real-project-seat"
    assert len(_creates(f)) == (0 if project_live else 1)
    assert len(_opened(f)) == 1
    assert _opened(f)[0]["opened"] == "real-project-seat"
    assert {path: path.read_bytes() for path in host_paths} == before
    assert (f.scene.table / "live/vc-host").read_bytes() == frame_before
    if project_live:
        assert admitted.environment is None or (
            admitted.environment["VIBECRAFTED_SESSION_ID"]
            == project.vibecrafted_session_id
        )
    assert f.wc.runtime_session_owner_root(
        "real-project-seat", f.socket_dir, runtime_state="live"
    ) == str(f.scene.root)


@pytest.mark.parametrize("explicit_name", [False, True])
def test_public_start_automatic_collision_routes_but_explicit_name_refuses(
    public_destination, explicit_name: bool
) -> None:
    f = public_destination
    foreign = f.scene.tmp_path / "foreign"
    foreign.mkdir()
    owner = f.bind(foreign, "selected")
    owner_path = f.wc.workspace_session_path(owner.vibecrafted_session_id)
    before = owner_path.read_bytes()
    frame_before = (f.scene.table / "live/selected").read_bytes()
    f.scene.clients_file.write_text("vc-host\n")
    arguments = (["selected"] if explicit_name else []) + ["--repo", str(f.scene.root)]
    result = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1" || exit $?; shift; vc-start "$@" || exit $?; '
            + PROJECT_RECEIPT,
            "private-project-entry",
            str(f.facade),
            *arguments,
        ],
        cwd=f.scene.home,
        env={**os.environ, "SSH_TTY": "/private-fixture-tty"},
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert owner_path.read_bytes() == before
    assert (f.scene.table / "live/selected").read_bytes() == frame_before
    switches = [call for call in f.scene.calls() if call.get("switched")]
    if explicit_name:
        assert result.returncode == 3, result.stderr
        assert not _creates(f) and not switches
        assert f.scene.live() == ["selected", "vc-host"]
    else:
        assert result.returncode == 0, result.stderr
        assert len(_creates(f)) == len(switches) == 1
        destination = _creates(f)[0]["created"]
        assert destination not in {"selected", "vc-host"}
        assert switches[0]["switched"] == ["vc-host", destination]
        receipt = json.loads(result.stdout[result.stdout.index("{") :])
        assert receipt["VIBECRAFTED_REQUESTED_FRAME_SESSION"] == "selected"
        assert receipt["VIBECRAFTED_OPERATOR_SESSION"] == destination
        assert (
            receipt["VIBECRAFTED_FRAME_DESTINATION_REASON"] == "occupied-name-preserved"
        )
        assert "reason=" not in result.stderr and "requested=" not in result.stderr
        assert f.wc.runtime_session_owner_root(
            destination, f.socket_dir, runtime_state="live"
        ) == str(f.scene.root)


@pytest.mark.parametrize(
    "project_live", [False, True], ids=["stale-recent", "changed-live-custom"]
)
def test_public_resume_preserves_host_and_foreign_while_restoring_or_reusing_project(
    public_destination, monkeypatch: pytest.MonkeyPatch, project_live: bool
) -> None:
    f = public_destination
    if project_live:
        _commit_private_project_revision(f, "before\n")
    host = f.bind(f.scene.root, "vc-host", physical=False)
    project = f.bind(
        f.scene.root, "recent-custom-seat", physical=project_live, fresh=True
    )
    foreign = f.scene.tmp_path / "foreign"
    foreign.mkdir()
    foreign_owner = f.bind(foreign, "selected")
    for key, value in host.to_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("VIBECRAFTED_OPERATOR_SESSION", "vc-host")
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(f.scene.root))
    protected_paths = (
        f.wc.workspace_session_path(host.vibecrafted_session_id),
        f.wc.instance_path(host.workspace_instance_id),
        f.wc.workspace_session_path(foreign_owner.vibecrafted_session_id),
        f.wc.instance_path(foreign_owner.workspace_instance_id),
        f.scene.table / "live/vc-host",
        f.scene.table / "live/selected",
    )
    project_paths = (
        f.wc.workspace_session_path(project.vibecrafted_session_id),
        f.wc.instance_path(project.workspace_instance_id),
    )
    before = {path: path.read_bytes() for path in protected_paths}
    project_before = {path: path.read_bytes() for path in project_paths}
    if project_live:
        _commit_private_project_revision(f, "after\n")
    f.scene.clients_file.write_text("vc-host\n")
    result = subprocess.run(
        [
            "/bin/bash",
            "--noprofile",
            "--norc",
            "-c",
            'source "$1" || exit $?; vc-start resume --repo "$2" || exit $?; '
            + PROJECT_RECEIPT,
            "private-project-resume",
            str(f.facade),
            str(f.scene.root),
        ],
        cwd=f.scene.home,
        env={**os.environ, "SSH_TTY": "/private-fixture-tty"},
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout[result.stdout.index("{") :])
    assert receipt["VIBECRAFTED_OPERATOR_SESSION"] == "recent-custom-seat"
    assert "reason=" not in result.stderr and "requested=" not in result.stderr
    assert {path: path.read_bytes() for path in protected_paths} == before
    switches = [call for call in f.scene.calls() if call.get("switched")]
    assert len(switches) == 1
    assert switches[0]["switched"] == ["vc-host", "recent-custom-seat"]
    assert not _opened(f)
    assert len(_creates(f)) == (0 if project_live else 1)
    if project_live:
        assert {path: path.read_bytes() for path in project_paths} == project_before
    else:
        assert _creates(f)[0]["created"] == "recent-custom-seat"
        historical = f.wc.read_workspace_session(project.vibecrafted_session_id)
        assert historical.attachments[0].state == "missing"
        assert receipt["VIBECRAFTED_FRAME_DESTINATION_REASON"] == "stale-seat-restore"
        current = f.wc._runtime_workspace_identity(
            root=f.scene.root,
            runtime_session_id="recent-custom-seat",
            socket_dir=f.socket_dir,
        )
        assert current is not None
        assert current[0].session_id != project.vibecrafted_session_id
    assert f.wc.runtime_session_owner_root(
        "recent-custom-seat", f.socket_dir, runtime_state="live"
    ) == str(f.scene.root)


def test_workshop_foreign_live_basename_preserved_and_agent_opens_project_once(
    public_destination,
) -> None:
    f = public_destination
    foreign = f.scene.tmp_path / "foreign"
    foreign.mkdir()
    owner = f.bind(foreign, "selected")
    before = f.wc.workspace_session_path(owner.vibecrafted_session_id).read_bytes()
    frame_before = (f.scene.table / "live/selected").read_bytes()
    admitted = f.open_agent("selected")
    assert admitted.session not in {"vc-host", "selected"}
    assert admitted.environment["VIBECRAFTED_REQUESTED_FRAME_SESSION"] == "selected"
    assert (
        admitted.environment["VIBECRAFTED_FRAME_DESTINATION_REASON"]
        == "occupied-name-preserved"
    )
    assert admitted.environment["VIBECRAFTED_WORKSPACE_ROOT"] == str(f.scene.root)
    assert len(_creates(f)) == len(_opened(f)) == 1
    assert _opened(f)[0]["opened"] == admitted.session
    assert f.wc.runtime_session_owner_root(
        admitted.session, f.socket_dir, runtime_state="live"
    ) == str(f.scene.root)
    assert (
        f.wc.workspace_session_path(owner.vibecrafted_session_id).read_bytes() == before
    )
    assert (f.scene.table / "live/selected").read_bytes() == frame_before
    actions = [
        call
        for call in f.scene.calls()
        if "action" in call["argv"] and "list-tabs" not in call["argv"]
    ]
    assert all(call["argv"][1] != "selected" for call in actions)


def test_workshop_preserves_foreign_exited_default_and_occupied_alternate(
    public_destination,
) -> None:
    f = public_destination
    foreign = f.scene.tmp_path / "foreign"
    foreign.mkdir()
    owner = f.bind(foreign, "selected")
    (f.scene.table / "live/selected").rename(f.scene.table / "dead/selected")
    alternate = f.wc.resolve_project_destination(
        root=f.scene.root,
        requested="selected",
        live_names=["vc-host"],
        occupied_names=["selected"],
        socket_dir=f.socket_dir,
        env={},
    )["VIBECRAFTED_OPERATOR_SESSION"]
    (f.scene.table / "dead" / alternate).write_text("preserved stopped custom seat")
    foreign_before = f.wc.workspace_session_path(
        owner.vibecrafted_session_id
    ).read_bytes()
    stopped = {p: p.read_bytes() for p in (f.scene.table / "dead").iterdir()}
    admitted = f.open_agent("selected")
    assert admitted.session not in {"selected", alternate, "vc-host"}
    assert {p: p.read_bytes() for p in stopped} == stopped
    assert (
        f.wc.workspace_session_path(owner.vibecrafted_session_id).read_bytes()
        == foreign_before
    )
    assert len(_creates(f)) == len(_opened(f)) == 1
    assert not any(call.get("resurrected") for call in f.scene.calls())


def test_workshop_absent_historical_live_recent_seat_restored_and_reused_once(
    public_destination,
) -> None:
    f = public_destination
    old = f.bind(f.scene.root, "recent-project-seat", physical=False)
    admitted = f.open_agent("recent-project-seat")
    again = f.workshop.ensure_live_destination(
        f.scene.root, "recent-project-seat", f.scene.live()
    )
    assert admitted.session == again.session == "recent-project-seat"
    assert len(_creates(f)) == len(_opened(f)) == 1
    old_record = f.wc.read_workspace_session(old.vibecrafted_session_id)
    assert old_record.attachments[0].state == "missing"
    assert admitted.environment["VIBECRAFTED_SESSION_ID"] != old.vibecrafted_session_id
    assert f.wc.runtime_session_owner_root(
        admitted.session, f.socket_dir, runtime_state="live"
    ) == str(f.scene.root)


def test_workshop_reuses_exact_custom_live_project_seat_from_host(
    public_destination,
) -> None:
    f = public_destination
    owner = f.bind(f.scene.root, "my-custom-project-seat")
    before = _catalog_bytes(f)
    admitted = f.open_agent("selected")
    assert admitted.session == "my-custom-project-seat"
    assert not _creates(f)
    assert len(_opened(f)) == 1
    assert _catalog_bytes(f) == before
    assert f.wc.workspace_session_path(owner.vibecrafted_session_id).exists()


def test_workshop_failed_create_has_no_live_receipt_and_retry_recovers(
    public_destination, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = public_destination
    monkeypatch.setenv("VC_FRAME_CREATE_ERROR", "private create refused")
    with pytest.raises(ValueError, match="private create refused"):
        f.workshop.ensure_live_destination(f.scene.root, "selected", f.scene.live())
    sessions = [
        f.wc.read_workspace_session(p.stem) for p in f.wc.sessions_dir().glob("*.json")
    ]
    assert sessions
    assert all(a.state != "live" for s in sessions for a in s.attachments)
    assert any(a.state == "missing" for s in sessions for a in s.attachments)
    assert f.scene.live() == ["vc-host"]
    assert not _opened(f)
    monkeypatch.delenv("VC_FRAME_CREATE_ERROR")
    admitted = f.open_agent("selected")
    assert admitted.session == "selected"
    assert len(_creates(f)) == len(_opened(f)) == 1


def test_workshop_foreign_seat_wins_create_race_without_adoption_then_retry_routes_away(
    public_destination, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = public_destination
    for relative in ("bin/vc-frame", "libexec/vc-frame"):
        frame = f.scene.generation / relative
        body = frame.read_text().replace(
            "    if os.path.exists(dead_path):",
            '    if os.environ.get("VC_DESTINATION_CREATE_RACE") and name != "vc-host":\n'
            '        with open(live_path, "x") as handle:\n'
            '            handle.write("foreign race content")\n'
            '        record({"raced": name})\n'
            '        sys.stderr.write("Session already exists\\n")\n'
            "        sys.exit(1)\n"
            "    if os.path.exists(dead_path):",
        )
        frame.write_text(body)
    monkeypatch.setenv("VC_DESTINATION_CREATE_RACE", "1")
    with pytest.raises(ValueError, match="no consistent live WES identity"):
        f.workshop.ensure_live_destination(f.scene.root, "selected", f.scene.live())
    raced = f.scene.table / "live/selected"
    assert raced.read_text() == "foreign race content"
    assert not _opened(f) and not _creates(f)
    sessions = [
        f.wc.read_workspace_session(p.stem) for p in f.wc.sessions_dir().glob("*.json")
    ]
    assert all(a.state != "live" for s in sessions for a in s.attachments)
    monkeypatch.delenv("VC_DESTINATION_CREATE_RACE")
    admitted = f.open_agent("selected")
    assert admitted.session != "selected"
    assert raced.read_text() == "foreign race content"
    assert len(_creates(f)) == len(_opened(f)) == 1


@pytest.mark.parametrize("ownership", ["unreadable", "ambiguous"])
def test_workshop_uncertain_ownership_preserves_catalog_and_every_live_seat(
    public_destination, ownership: str
) -> None:
    f = public_destination
    owner = f.bind(f.scene.root, "custom-one")
    if ownership == "unreadable":
        f.wc.workspace_session_path(owner.vibecrafted_session_id).write_text("{broken")
    else:
        # A distinct logical incarnation, rather than another name on the same
        # receipt, makes the two physically live project seats ambiguous.
        other = f.wc.materialize_instance(
            workspace_id=owner.workspace_id,
            root=f.scene.root,
            vibecrafted_session_id=f.wc.new_uuid7(),
            build_id=owner.build_id,
        )
        f.wc.record_runtime_session_attachment(
            workspace_id=other.workspace_id,
            vibecrafted_session_id=other.vibecrafted_session_id,
            workspace_instance_id=other.workspace_instance_id,
            runtime="vc-frame",
            runtime_session_id="custom-two",
            socket_dir=f.socket_dir,
            state="live",
        )
        (f.scene.table / "live/custom-two").write_text("layout { pane; }\n")
    before = _catalog_bytes(f)
    physical = {path: path.read_bytes() for path in (f.scene.table / "live").iterdir()}
    with pytest.raises(ValueError):
        f.workshop.ensure_live_destination(f.scene.root, "selected", f.scene.live())
    assert _catalog_bytes(f) == before
    assert {
        path: path.read_bytes() for path in (f.scene.table / "live").iterdir()
    } == physical
    assert not _creates(f) and not _opened(f)
