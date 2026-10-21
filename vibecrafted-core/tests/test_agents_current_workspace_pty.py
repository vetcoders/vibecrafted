"""Disposable Frame IPC/PTY proofs; never connect to a desktop or user seat.

Set VC_TEST_FRAME_ENGINE to a real, absolute Frame engine. The shell
preparation, WES ledger, incarnation observations and tab launch are real;
the workload is a harmless Python process recording its terminal identity.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_frame_workday_project_launcher_entry import CONFIG, _load
from vibecrafted_core import workspace_catalog as catalog

REPO = CONFIG.parents[3]
FACADE = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
WES_KEYS = (
    "VIBECRAFTED_WORKSPACE_ROOT",
    "VIBECRAFTED_WORKSPACE_ID",
    "VIBECRAFTED_SESSION_ID",
    "VIBECRAFTED_WORKSPACE_INSTANCE_ID",
    "VIBECRAFTED_BUILD_ID",
)


class IsolatedFrame:
    def __init__(self, directory: Path, engine: Path):
        self.directory = directory
        self.engine = engine
        self.sessions: set[str] = set()
        self.actions: list[dict] = []
        self.workloads: list[dict] = []
        self.workshop = _load("vc-agent-workshop")

    def cli(self, *args: str, check: bool = True):
        result = subprocess.run(
            [str(self.engine), *args],
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.actions.append(
            {
                "argv": [str(self.engine), *args],
                "status": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def live(self) -> list[str]:
        result = self.cli("list-sessions", "--short", "--no-formatting", check=False)
        if result.returncode:
            assert "No active" in result.stderr or "No active" in result.stdout
            return []
        return result.stdout.splitlines()

    def tabs(self, session: str):
        return json.loads(
            self.cli(
                "--session", session, "action", "list-tabs", "--json", "--all"
            ).stdout
        )

    def panes(self, session: str):
        return json.loads(
            self.cli(
                "--session", session, "action", "list-panes", "--json", "--all"
            ).stdout
        )

    def prepare(self, project: Path, requested: str) -> dict[str, str]:
        # Exercise the actual source-owned lifecycle, without reconciling a
        # host LaunchAgent or crossing the public GUI/terminal entrypoint.
        script = (
            'source "$1" || exit $?; '
            '_vetcoders_prepare_project_destination "$2" "$3" || exit $?; '
            'target="$VIBECRAFTED_OPERATOR_SESSION"; '
            '_vetcoders_start_read_inventory_state "$target"; '
            'state="$_vetcoders_start_inventory_state"; '
            'if [[ "$state" == live || "$state" == dead ]]; then '
            '_vetcoders_start_check_existing_owner "$target" "$state" "$2" || exit $?; fi; '
            '_vetcoders_product_workspace_prepare "$2" "$target" "$state" || exit $?; '
            '_vetcoders_start_launch_workspace "$target" "$2" background >&2 || exit $?; '
            '_vetcoders_product_core_cli workspace resolve --root "$2" --json '
            '--runtime-session-id "$target" --socket-dir "$VC_FRAME_SOCKET_DIR" --runtime-state live'
        )
        result = subprocess.run(
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-c",
                script,
                "agents-pty",
                str(FACADE),
                str(project),
                requested,
            ],
            cwd=project,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        self.actions.append(
            {
                "owner": "source shell preparation",
                "root": str(project),
                "requested": requested,
                "status": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        self.sessions.update(self.live())
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    def naked(self, project: Path, name: str):
        layout = self.directory / f"{name}.kdl"
        layout.write_text(
            'layout {\n tab name="Existing workload" {\n'
            ' pane command="/bin/bash" {\n args "--noprofile" "--norc"\n }\n }\n}\n'
        )
        self.cli(
            "--new-session-with-layout",
            str(layout),
            "attach",
            "--create-background",
            name,
        )
        self.sessions.add(name)

    def workload(self, project: Path, session: str, label: str):
        receipt = self.directory / f"{label}.json"
        script = self.directory / f"{label}.py"
        script.write_text(
            "import json,os,pathlib,sys,time\n"
            f"pathlib.Path({str(receipt)!r}).write_text(json.dumps(dict("
            "pid=os.getpid(),ppid=os.getppid(),cwd=os.getcwd(),"
            "tty=os.ttyname(0),isatty=os.isatty(0),"
            "session=os.environ.get('VC_FRAME_SESSION_NAME'),"
            "marker=sys.argv[1])))\n"
            "while True: time.sleep(0.1)\n"
        )
        argv = self.workshop.launch_pane_argv(
            label, project, [sys.executable, str(script), label], session=session
        )
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=15, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
        deadline = time.monotonic() + 10
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert receipt.exists(), self.panes(session)
        data = json.loads(receipt.read_text())
        self.workloads.append(data)
        assert data["isatty"] and data["cwd"] == str(project)
        assert data["session"] == session and data["marker"] == label
        return data

    def close(self):
        for session in sorted(self.sessions):
            self.cli("kill-session", session, check=False)


@pytest.fixture
def real_frame(monkeypatch: pytest.MonkeyPatch, request):
    # Outside the production namespace stripped by suite home isolation.
    configured = os.environ.get("VC_TEST_FRAME_ENGINE", "")
    if not configured:
        pytest.skip("VC_TEST_FRAME_ENGINE must select a real Frame engine")
    engine = Path(configured).resolve(strict=True)
    assert engine.is_absolute() and os.access(engine, os.X_OK)
    with tempfile.TemporaryDirectory(prefix="vc-agents-", dir="/tmp") as short:
        directory = Path(short).resolve()
        home = directory / "home"
        home.mkdir()
        binary = directory / "bin"
        binary.mkdir()
        (binary / "vc-frame").symlink_to(engine)
        values = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(directory / "config"),
            "XDG_CACHE_HOME": str(directory / "cache"),
            "XDG_DATA_HOME": str(directory / "data"),
            "VIBECRAFTED_HOME": str(directory / "vc-home"),
            "VC_FRAME_SOCKET_DIR": str(directory / "sockets"),
            "ZELLIJ_SOCKET_DIR": str(directory / "sockets"),
            "VC_FRAME_CONFIG_DIR": str(CONFIG),
            "VIBECRAFTED_VC_FRAME_BIN": str(engine),
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_PYTHON": sys.executable,
            "VIBECRAFTED_CORE_DIR": str(REPO / "vibecrafted-core"),
            "PATH": f"{binary}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "TERM": "xterm-256color",
            "SHELL": "/bin/bash",
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        for key in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIBECRAFTED_RUNTIME_ROOT",
            "VC_FRAME_SESSION_NAME",
            "ZELLIJ_SESSION_NAME",
            *WES_KEYS,
        ):
            monkeypatch.delenv(key, raising=False)
        runtime = IsolatedFrame(directory, engine)
        try:
            yield runtime
        finally:
            receipt_dir = os.environ.get("VC_TEST_FRAME_RECEIPT_DIR")
            if receipt_dir:
                destination = Path(receipt_dir)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / f"{request.node.name}.json").write_text(
                    json.dumps(
                        {
                            "engine": str(engine),
                            "actions": runtime.actions,
                            "workloads": runtime.workloads,
                        },
                        indent=2,
                    )
                    + "\n"
                )
            runtime.close()


def _project(runtime: IsolatedFrame, name: str) -> Path:
    project = runtime.directory / name
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    return project


def test_noninteractive_prepare_preserves_selected_layout_and_live_wes(
    real_frame, monkeypatch
):
    r = real_frame
    project = _project(r, "rail project")
    # Absolute namespace aliases are valid (macOS /tmp and /var are aliases).
    # Keep the same explicit spelling across the caller and workspace child.
    socket_root = Path(os.environ["VC_FRAME_SOCKET_DIR"])
    socket_root.mkdir(exist_ok=True)
    socket_alias = r.directory / "socket-alias"
    socket_alias.symlink_to(socket_root, target_is_directory=True)
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(socket_alias))
    monkeypatch.setenv("ZELLIJ_SOCKET_DIR", str(socket_alias))
    generation = r.directory / "selected-generation"
    binary = generation / "bin"
    binary.mkdir(parents=True)
    (generation / "VERSION").write_text("4.5.0+g00000000\n")
    (binary / "python3").symlink_to(sys.executable)
    (binary / "vc-frame").symlink_to(r.engine)
    facade = generation / FACADE.relative_to(REPO)
    facade.parent.mkdir(parents=True)
    service_called = r.directory / "forbidden-host-service-call"
    facade.write_text(
        f"source {shlex.quote(str(FACADE))} || return $?\n"
        f"export VIBECRAFTED_CORE_DIR={shlex.quote(str(REPO / 'vibecrafted-core'))}\n"
        "_vetcoders_owned_core_dir() { printf '%s\\n' "
        + shlex.quote(str(REPO / "vibecrafted-core"))
        + "; }\n"
        "_vetcoders_owned_python_bin() { printf '%s\\n' "
        + shlex.quote(sys.executable)
        + "; }\n"
        "_vetcoders_control_plane_eye_prepare() { touch "
        + shlex.quote(str(service_called))
        + "; printf 'unexpected host service reconciliation\\n' >&2; return 97; }\n"
    )
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_ROOT", str(generation))
    initial = r.directory / "selected-pane.json"
    workload = r.directory / "selected-pane.py"
    workload.write_text(
        "import json,os,pathlib,time\n"
        f"pathlib.Path({str(initial)!r}).write_text(json.dumps(dict("
        "pid=os.getpid(),cwd=os.getcwd(),isatty=os.isatty(0),"
        "environment=dict(os.environ))))\n"
        "while True: time.sleep(0.1)\n"
    )
    layout = r.directory / "selected layout.kdl"
    layout.write_text(
        'layout {\n tab name="Selected canvas" {\n pane command='
        + json.dumps(sys.executable)
        + " {\n args "
        + json.dumps(str(workload))
        + "\n }\n }\n }\n"
    )

    def invoke(*args, extra_env=None, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                str(CONFIG / "vc-agent-workshop.py"),
                "prepare",
                "--root",
                str(project),
                *args,
            ],
            cwd=r.directory,
            env={**os.environ, **(extra_env or {})},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        r.actions.append(
            {
                "owner": "noninteractive prepare API",
                "status": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        r.sessions.update(r.live())
        assert result.returncode == expected, result.stdout + result.stderr
        if expected:
            assert not result.stdout
            return result.stderr
        return json.loads(result.stdout)

    # Preconditions fail before the catalog or the physical inventory changes.
    before = r.live()
    snapshot = {
        p: p.read_bytes() for p in Path(os.environ["VIBECRAFTED_HOME"]).rglob("*.json")
    }
    assert "socket namespace" in invoke(
        extra_env={"VC_FRAME_SOCKET_DIR": "", "ZELLIJ_SOCKET_DIR": ""}, expected=4
    )
    assert "Operator host" in invoke("--session", "vc-host", expected=4)
    assert "socket namespace" in invoke(
        extra_env={"VC_FRAME_SOCKET_DIR": "relative-sockets"}, expected=4
    )
    assert "absolute physical" in invoke(
        extra_env={"VIBECRAFTED_RUNTIME_ROOT": "~/../selected-generation"}, expected=4
    )
    runtime_alias = r.directory / "runtime-alias"
    runtime_alias.symlink_to(generation)
    assert "absolute physical" in invoke(
        extra_env={"VIBECRAFTED_RUNTIME_ROOT": str(runtime_alias)}, expected=4
    )
    assert "absolute physical" in invoke("--layout", "relative.kdl", expected=4)
    assert r.live() == before
    assert {
        p: p.read_bytes() for p in Path(os.environ["VIBECRAFTED_HOME"]).rglob("*.json")
    } == snapshot

    r.naked(project, "unknown-custom-seat")
    unknown_before = r.live()
    assert "no verified project binding" in invoke(
        extra_env={
            "VC_FRAME_SESSION_NAME": "unknown-custom-seat",
            "VIBECRAFTED_WORKSPACE_ROOT": str(project),
        },
        expected=4,
    )
    assert r.live() == unknown_before
    assert {
        p: p.read_bytes() for p in Path(os.environ["VIBECRAFTED_HOME"]).rglob("*.json")
    } == snapshot
    r.cli("kill-session", "unknown-custom-seat")

    prepared = invoke("--session", "rail-seat", "--layout", str(layout))
    assert prepared["native_session_name"] == "rail-seat"
    assert prepared["VIBECRAFTED_OPERATOR_SESSION"] == "rail-seat"
    assert prepared["socket_dir"] == os.environ["VC_FRAME_SOCKET_DIR"]
    assert prepared["VIBECRAFTED_WORKSPACE_ROOT"] == str(project)
    assert (
        prepared["session_incarnation"] == r.tabs("rail-seat")[0]["session_incarnation"]
    )
    deadline = time.monotonic() + 10
    while not initial.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert initial.exists(), r.panes("rail-seat")
    started = json.loads(initial.read_text())
    assert started["isatty"] and started["cwd"] == str(project)
    for key in WES_KEYS:
        assert started["environment"][key] == prepared[key]
    before_tabs, before_panes = r.tabs("rail-seat"), r.panes("rail-seat")
    other_layout = r.directory / "other.kdl"
    other_layout.write_text(
        'layout {\n tab name="Must not replace" {\n pane;\n }\n }\n'
    )
    # Omitted native name resolves the actual admitted seat. A newly selected
    # layout is input for creation, never a command to reset existing work.
    reused = invoke("--layout", str(other_layout))
    assert reused == prepared
    foreign = _project(r, "foreign")
    foreign_identity = catalog.resolve_run_workspace_identity(root=foreign, env={})
    assert invoke(extra_env=foreign_identity.to_env()) == prepared
    assert "conflicts" in invoke(
        extra_env={
            **foreign_identity.to_env(),
            "VC_FRAME_SESSION_NAME": "rail-seat",
        },
        expected=4,
    )
    assert r.tabs("rail-seat") == before_tabs
    assert r.panes("rail-seat") == before_panes
    os.kill(started["pid"], 0)
    assert not service_called.exists(), (
        "internal preparation called the host service owner"
    )
    r.actions.append(
        {
            "host_service_fence": "fail loudly on any eye/reconcile call",
            "service_called": service_called.exists(),
        }
    )


def test_bound_seat_recovers_lost_pane_context_and_preserves_real_workload(
    real_frame, monkeypatch
):
    r = real_frame
    project, foreign = _project(r, "project"), _project(r, "foreign")
    identity = r.prepare(project, "custom-seat")
    seat = identity["VIBECRAFTED_OPERATOR_SESSION"]
    assert seat == "custom-seat"
    original = r.workload(project, seat, "kept-conversation")
    before_tabs = r.tabs(seat)
    before_panes = r.panes(seat)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", seat)
    monkeypatch.chdir(foreign)
    for key in WES_KEYS:
        monkeypatch.delenv(key, raising=False)
    form = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert form.path == str(project)
    assert str(project) + "/" in form.path_suggestions
    assert os.environ["VIBECRAFTED_SESSION_ID"] == identity["VIBECRAFTED_SESSION_ID"]
    for label in ("second-agent", "third-agent"):
        destination = r.workshop.destination_session_for_workspace(project)
        admitted = r.workshop.ensure_live_destination(project, destination, r.live())
        assert admitted.session == seat
        r.workload(project, admitted.session, label)
    assert set(r.live()) == {"vc-host", seat}
    assert (
        r.tabs(seat)[0]["session_incarnation"] == before_tabs[0]["session_incarnation"]
    )
    assert r.tabs(seat)[0]["tab_instance_id"] == before_tabs[0]["tab_instance_id"]
    after = {(p["is_plugin"], p["id"]): p for p in r.panes(seat)}
    for pane in before_panes:
        current = after[pane["is_plugin"], pane["id"]]
        assert current["tab_id"] == pane["tab_id"]
        assert current.get("pane_command") == pane.get("pane_command")
        assert current["exited"] == pane["exited"]
    os.kill(original["pid"], 0)
    os.kill(original["ppid"], 0)

    # Correctly declared context remains authoritative over an inherited cwd;
    # the user can then explicitly edit the Project field without a refresh
    # silently replacing it with either the seat's root or the foreign cwd.
    declared = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert declared.path == str(project)
    declared.path = str(foreign)
    declared._trigger_path_autocomplete()
    assert Path(declared.path).resolve() == foreign


@pytest.mark.parametrize("declared", [False, True])
def test_naked_current_repository_suggests_path_and_refuses_duplicate(
    real_frame, monkeypatch, declared
):
    r = real_frame
    project = _project(r, "legacy")
    monkeypatch.chdir(project)
    r.naked(project, "legacy-custom")
    workload = r.workload(project, "legacy-custom", "preserved-legacy")
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "legacy-custom")
    if declared:
        monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(project))
    before = r.tabs("legacy-custom")
    form = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert form.path == str(project)
    assert (
        catalog.runtime_session_owner_root(
            "legacy-custom", os.environ["VC_FRAME_SOCKET_DIR"], runtime_state="live"
        )
        is None
    )
    with pytest.raises(
        ValueError, match="(?i)(existing|current|saved).*(workspace|session|binding)"
    ):
        destination = r.workshop.destination_session_for_workspace(project)
        r.workshop.ensure_live_destination(
            project,
            destination,
            r.live(),
            current_project_hint=form.current_project_hint,
        )
    assert r.live() == ["legacy-custom"]
    assert r.tabs("legacy-custom") == before
    os.kill(workload["pid"], 0)


def test_explicit_other_project_preparation_is_idempotent(real_frame, monkeypatch):
    r = real_frame
    project, other = _project(r, "first"), _project(r, "second")
    identity = r.prepare(project, "first-seat")
    for key, value in identity.items():
        if isinstance(value, str):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "first-seat")
    monkeypatch.chdir(project)
    form = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    form.path = str(other)
    assert form.path == str(other)
    second = r.prepare(other, "second-seat")
    assert second["VIBECRAFTED_WORKSPACE_ROOT"] == str(other)
    before = r.tabs("second-seat")
    repeated = r.prepare(other, "second-seat")
    assert repeated["VIBECRAFTED_SESSION_ID"] == second["VIBECRAFTED_SESSION_ID"]
    assert r.tabs("second-seat") == before
    assert set(r.live()) == {"vc-host", "first-seat", "second-seat"}
    r.workload(other, "second-seat", "other-agent")


def test_foreign_exports_and_operator_host_are_not_project_authority(
    real_frame, monkeypatch
):
    r = real_frame
    project, foreign = _project(r, "owned"), _project(r, "foreign")
    identity = r.prepare(project, "owned-seat")
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "owned-seat")
    monkeypatch.setenv("VIBECRAFTED_WORKSPACE_ROOT", str(foreign))
    with pytest.raises(catalog.WorkspaceCatalogError, match="conflicts"):
        catalog.current_workspace_context(
            runtime_session_id="owned-seat",
            socket_dir=os.environ["VC_FRAME_SOCKET_DIR"],
            live_names=r.live(),
        )
    conflicted = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert conflicted.path == "" and "conflicts" in conflicted.error
    assert conflicted.path_suggestions == []
    monkeypatch.delenv("VIBECRAFTED_WORKSPACE_ROOT")
    monkeypatch.setenv("VIBECRAFTED_SESSION_ID", "00000000-0000-4000-8000-000000000000")
    with pytest.raises(ValueError, match="conflicts"):
        r.workshop.ensure_live_destination(project, "owned-seat", r.live())
    for key, value in identity.items():
        if isinstance(value, str):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "vc-host")
    monkeypatch.chdir(project)
    host = r.workshop.Workshop(SimpleNamespace(), mode="launcher")
    assert host.path == "" and host.path_suggestions == []
    assert set(r.live()) == {"vc-host", "owned-seat"}


def test_stale_incarnation_cannot_adopt_replacement_seat(real_frame, monkeypatch):
    r = real_frame
    project = _project(r, "stale")
    r.prepare(project, "reused-name")
    original = r.tabs("reused-name")[0]["session_incarnation"]
    r.cli("--session", "reused-name", "action", "save-session")
    r.cli("kill-session", "reused-name")
    removed = r.cli("delete-session", "reused-name", check=False)
    assert removed.returncode == 0 or (
        removed.returncode == 2 and "not found" in removed.stderr
    )
    monkeypatch.chdir(project)
    r.naked(project, "reused-name")
    replacement = r.tabs("reused-name")[0]["session_incarnation"]
    assert original != replacement
    monkeypatch.setenv("VC_FRAME_SESSION_NAME", "reused-name")
    assert (
        catalog.current_workspace_context(
            runtime_session_id="reused-name",
            socket_dir=os.environ["VC_FRAME_SOCKET_DIR"],
            live_names=r.live(),
        )
        == {}
    )
    with pytest.raises(ValueError, match="no verified project binding"):
        r.workshop.ensure_live_destination(project, "reused-name", r.live())
    assert set(r.live()) == {"vc-host", "reused-name"}
    assert r.tabs("reused-name")[0]["session_incarnation"] == replacement
