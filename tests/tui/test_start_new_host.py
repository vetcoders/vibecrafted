"""Intentional parallel hosts preserve the existing session and workspace."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from tests.tui.test_start_workspace_contract import Scene, _creates, _rc, _run


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("inside", [False, True])
def test_new_host_preserves_existing_host_and_workspace(
    tmp_path: Path, shell: str, inside: bool
) -> None:
    scene = Scene(
        tmp_path, live=("vc-host", "mlx-batch-runner"), guests=("mlx-batch-runner",)
    )
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    env = (
        {"VC_FRAME": "1", "VC_FRAME_PANE_ID": "2", "VC_FRAME_SESSION_NAME": "vc-host"}
        if inside
        else {}
    )
    result = _run(scene, "vc-start --new-host", shell=shell, extra_env=env)
    assert _rc(result) == 0, result.stdout + result.stderr
    assert scene.live() == ["mlx-batch-runner", "vc-host", "vc-host@7a69d24d"]
    for name, body in before.items():
        assert (scene.table / "live" / name).read_bytes() == body
    assert len(_creates(scene.calls())) == 1
    assert "--guest-workspace" not in _creates(scene.calls())[0]["argv"]
    assert "4.3.1+g7a69d24d" in result.stdout + result.stderr
    launches = scene.terminal_launches()
    assert len(launches) == 1
    assert "--new-host" in launches[0]["argv"]
    assert all(not c["argv"][0].startswith(("kill", "delete")) for c in scene.calls())


def test_new_host_skips_live_and_exited_names(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host@7a69d24d",), dead=("vc-host@7a69d24d-2",))
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    result = _run(scene, "vc-start --new-host")
    assert _rc(result) == 0, result.stdout + result.stderr
    assert "vc-host@7a69d24d-3" in scene.live()
    assert scene.dead() == ["vc-host@7a69d24d-2"]
    assert all(len(n) <= 24 for n in scene.live())


@pytest.mark.parametrize(
    "argv", ["--new-host resume", "resume --new-host", "named --new-host"]
)
def test_new_host_conflicts_are_usage_errors(tmp_path: Path, argv: str) -> None:
    scene = Scene(tmp_path)
    result = _run(scene, "vc-start " + argv)
    assert _rc(result) == 2, result.stdout + result.stderr
    assert not scene.calls()


def test_new_host_fails_closed_on_inventory_error(tmp_path: Path) -> None:
    scene = Scene(tmp_path)
    result = _run(
        scene,
        "vc-start --new-host",
        extra_env={"VC_FRAME_INVENTORY_ERROR": "permission denied"},
    )
    assert _rc(result) == 4, result.stdout + result.stderr
    assert not _creates(scene.calls())


def test_split_noninteractive_creates_project_without_joining_old_host(
    tmp_path: Path,
) -> None:
    scene = Scene(tmp_path, live=("vc-host",), clients=("vc-host",))
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    before = (scene.table / "live/vc-host").read_bytes()
    result = _run(
        scene,
        "_vetcoders_start_host_generation() { printf '4.3.1+gf8debfd6\\n'; }; vc-start",
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert scene.live() == sorted(["vc-host", scene.root.name])
    assert (scene.table / "live/vc-host").read_bytes() == before
    assert len(_creates(scene.calls())) == 1
    assert "--guest-workspace" not in _creates(scene.calls())[0]["argv"]
    assert not any("project-workspace" in c["argv"] for c in scene.calls())
    launches = scene.terminal_launches()
    assert len(launches) == 1
    assert launches[0]["created"] == scene.root.name
    assert "--new-host" not in result.stderr


def test_created_host_terminal_child_attaches_exact_host(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host", "vc-host@7a69d24d"))
    result = _run(
        scene,
        "_vetcoders_start_is_owned_terminal_child() { return 0; }; vc-start --new-host",
        tty=True,
        extra_env={"VIBECRAFTED_START_CREATED_HOST": "vc-host@7a69d24d"},
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert not _creates(scene.calls())
    assert any(c["argv"] == ["attach", "vc-host@7a69d24d"] for c in scene.calls())


def test_new_host_deck_uses_shared_owner(tmp_path: Path) -> None:
    from tests.tui.test_start_workspace_contract import DECK

    scene = Scene(tmp_path, live=("vc-host",))
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    body = DECK.read_text().split("cmd_start() {", 1)[1].split("\n}\n", 1)[0]
    invocation = (
        "_is_help_flag() { return 1; }; "
        f'_script_owner_root() {{ printf "%s\\n" {shlex.quote(str(scene.generation))}; }}; '
        "_ensure_helpers_loaded() { return 0; }; "
        "cmd_start() {" + body + "\n}; cmd_start --new-host"
    )
    result = _run(scene, invocation)
    assert _rc(result) == 0, result.stdout + result.stderr
    assert "vc-host@7a69d24d" in scene.live()


def test_owned_child_without_tty_does_not_open_another_terminal(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host",))
    result = _run(
        scene,
        "_vetcoders_start_is_owned_terminal_child() { return 0; }; vc-start --new-host",
        extra_env={"VIBECRAFTED_START_CREATED_HOST": "vc-host"},
    )
    assert _rc(result) == 4, result.stdout + result.stderr
    assert not _creates(scene.calls())
    assert not scene.terminal_launches(wait=0)


@pytest.mark.parametrize("native", [False, True])
def test_stale_shell_reenters_verified_active_front_door(
    tmp_path: Path, native: bool
) -> None:
    import json
    import sys

    scene = Scene(tmp_path)
    old = tmp_path / "releases" / "4.3.1+gf8debfd6"
    active = tmp_path / "releases" / "4.3.1+g7a69d24d"
    for root in (old, active):
        (root / "bin").mkdir(parents=True)
    (old / "bin/python3").symlink_to(sys.executable)
    (old / "scripts").mkdir()
    envelope = {
        "schema": "vibecrafted.runtime-resolution.v1",
        "status": "ready",
        "reason": "verified",
        "runtime": {
            "schema": "vibecrafted.runtime-install-result.v1",
            "root": str(active),
        },
    }
    (old / "scripts/vetcoders_install.py").write_text(
        "print(" + repr(json.dumps(envelope)) + ")\n"
    )
    log = tmp_path / "active-entry.json"
    entry = active / "bin/vc-start"
    logger = (
        "import json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps({{'argv': sys.argv[1:], "
        "'runtime': os.environ.get('VIBECRAFTED_RUNTIME_ROOT'), "
        "'frame': os.environ.get('VIBECRAFTED_VC_FRAME_BIN'), "
        "'core': os.environ.get('VIBECRAFTED_CORE_DIR')}))\n"
    )
    if native:
        import shutil

        if _INSTALLED_PAIR is None:
            pytest.skip("installed native vc-start required")
        shutil.copy2(_INSTALLED_PAIR[1] / "bin/vc-start", entry)
        (active / "bin/python3").symlink_to(sys.executable)
        (active / "bin/vc-frame").write_text("engine presence for native entry probe")
        facade = active / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
        facade.parent.mkdir(parents=True)
        facade.write_text(
            'vc-start() { "$VIBECRAFTED_PYTHON" -c '
            + shlex.quote(logger)
            + ' "$@"; }\n'
        )
    else:
        entry.write_text(f"#!{sys.executable}\n" + logger)
        entry.chmod(0o755)
    result = _run(
        scene,
        f"_vetcoders_vc_frame_loaded_root={shlex.quote(str(old))}; vc-start --new-host",
        extra_env={
            "VIBECRAFTED_RUNTIME_ROOT": str(old),
            "VIBECRAFTED_VC_FRAME_BIN": str(old / "bin/vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    receipt = json.loads(log.read_text())
    assert receipt["argv"] == ["--new-host", "--repo", str(scene.root.resolve())]
    assert receipt["runtime"] == str(active)
    assert receipt["frame"] == str(active / "bin/vc-frame")
    assert receipt["core"] == str(active / "vibecrafted-core")
    assert not scene.calls()


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_split_tty_enters_project_without_prompting_or_changing_old_host(
    tmp_path: Path, shell: str
) -> None:
    scene = Scene(tmp_path, live=("vc-host",))
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    before = (scene.table / "live/vc-host").read_bytes()
    result = _run(
        scene,
        "_vetcoders_start_host_generation() { printf '4.3.1+gf8debfd6\\n'; }; vc-start",
        shell=shell,
        tty=True,
        developer_root=True,
        extra_env={
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert "[y/N]" not in result.stdout + result.stderr
    assert scene.live() == sorted(["vc-host", scene.root.name])
    assert (scene.table / "live/vc-host").read_bytes() == before
    assert len(_creates(scene.calls())) == 1
    assert any(c["argv"] == ["attach", scene.root.name] for c in scene.calls())
    assert not scene.terminal_launches(wait=0)


def _installed_pair() -> tuple[Path, Path] | None:
    import json

    home = Path.home() / ".local/share/vibecrafted"
    try:
        active = Path(json.loads((home / "active.json").read_text())["runtime_root"])
        older = sorted(
            (home / "releases").iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for root in older:
            if (
                root != active
                and (root / "libexec/vc-frame").is_file()
                and (active / "libexec/vc-frame").is_file()
            ):
                return root, active
    except (OSError, ValueError, KeyError):
        pass
    return None


_INSTALLED_PAIR = _installed_pair()


@pytest.mark.skipif(
    _INSTALLED_PAIR is None, reason="two installed Frame generations required"
)
def test_real_generations_parallel_host_preserves_old_pid_and_panes(
    tmp_path: Path,
) -> None:
    """Real engines, source start owner, isolated sockets; terminal admission is stubbed."""
    import json
    import shutil
    import subprocess
    import tempfile
    import time

    from tests.tui.test_start_workspace_contract import _entry_script, _shell_argv

    assert _INSTALLED_PAIR is not None
    older, active = _INSTALLED_PAIR
    sandbox = Path(tempfile.mkdtemp(prefix="vcn-", dir="/tmp"))
    scene = Scene(tmp_path)
    socket_root = sandbox / "sock"
    socket_root.mkdir()
    # Use real generation engines through the same public shell selection.
    wrapper = scene.generation / "bin/vc-frame"
    wrapper.write_text(
        "#!/bin/sh\nexec " + shlex.quote(str(active / "libexec/vc-frame")) + ' "$@"\n'
    )
    (scene.generation / "VERSION").write_text((active / "VERSION").read_text())
    layout = scene.config_dir / "layouts/host.kdl"
    layout.write_text(
        'layout {\n tab name="Sentinel" {\n pane name="preserved" command="/bin/sh" { args "-c" "printf sentinel; sleep 180"; }\n pane { plugin location="zellij:session-manager" { frame_host true; }; }\n }\n}\n'
    )
    (scene.config_dir / "config.kdl").write_text("session_serialization false\n")
    env = scene.env(
        {
            "VC_FRAME_SOCKET_DIR": str(socket_root),
            "ZELLIJ_SOCKET_DIR": str(socket_root),
            "XDG_CACHE_HOME": str(sandbox / "cache"),
            "XDG_RUNTIME_DIR": str(sandbox),
            "TMPDIR": str(sandbox),
            "VC_FRAME_CONFIG_DIR": str(scene.config_dir),
            "VC_FRAME_CONFIG_FILE": str(scene.config_dir / "config.kdl"),
        }
    )
    names = ["vc-host"]

    def frame(engine: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(engine / "libexec/vc-frame"), *args],
            env=env,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            timeout=25,
        )

    def shell(body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            _shell_argv("bash", _entry_script(scene, body)),
            env=env,
            cwd=scene.root,
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )

    def owner(name: str) -> tuple[int, str]:
        sockets = list(socket_root.glob("*/" + name)) + [socket_root / name]
        for path in sockets:
            if not path.is_socket():
                continue
            result = subprocess.run(
                ["lsof", "-t", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            for pid in result.stdout.split():
                proc = subprocess.run(
                    ["ps", "-p", pid, "-o", "comm="],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.stdout.strip().endswith("/vc-frame"):
                    return int(pid), proc.stdout.strip()
        raise AssertionError("no owning process for " + name)

    try:
        created = frame(
            older, "--layout", str(layout), "attach", "--create-background", "vc-host"
        )
        assert created.returncode == 0, created.stdout + created.stderr
        before_pid = owner("vc-host")
        before_layout = frame(older, "--session", "vc-host", "action", "dump-layout")
        assert before_layout.returncode == 0, before_layout.stderr
        assert "preserved" in before_layout.stdout
        before_panes = frame(
            older, "--session", "vc-host", "action", "list-panes", "--json", "--command"
        )
        assert before_panes.returncode == 0, before_panes.stderr
        assert "preserved" in before_panes.stdout
        # 7f9d30d6 retired the split offer (_vetcoders_start_offer_generation_host):
        # a generation split is no longer detected or prompted. Parallel hosts are
        # now only opened explicitly via `vc-start --new-host`, below.
        new_host = "vc-host@" + active.name.split("+g")[-1][:8]
        names.append(new_host)
        started = shell("vc-start --new-host")
        assert _rc(started) == 0, started.stdout + started.stderr
        listing = frame(active, "list-sessions", "--no-formatting")
        assert all(name in listing.stdout for name in names), listing.stdout
        after_pid = owner("vc-host")
        new_pid = owner(new_host)
        assert after_pid == before_pid
        assert new_pid[0] != before_pid[0]
        assert new_pid[1] == str(active / "libexec/vc-frame")
        after_layout = frame(older, "--session", "vc-host", "action", "dump-layout")
        assert after_layout.stdout == before_layout.stdout
        after_panes = frame(
            older, "--session", "vc-host", "action", "list-panes", "--json", "--command"
        )
        assert after_panes.returncode == 0, after_panes.stderr
        assert after_panes.stdout == before_panes.stdout
        roles = shell(
            f'_vetcoders_start_session_projection_role vc-host "{wrapper}"; _vetcoders_start_session_projection_role {new_host} "{wrapper}"'
        )
        assert roles.stdout.count("host\n") == 2, roles.stdout + roles.stderr
        receipt = {
            "older": older.name,
            "active": active.name,
            "old_pid_before": before_pid,
            "old_pid_after": after_pid,
            "new_pid": new_pid,
            "sessions": listing.stdout,
            "roles": roles.stdout,
            "old_layout_unchanged": True,
            "old_pane_inventory_unchanged": True,
            "start": started.stdout,
        }
        (tmp_path / "native-parallel-host-receipt.json").write_text(
            json.dumps(receipt, indent=2)
        )
        print(json.dumps(receipt))
    finally:
        # Only the exact names in this exclusive socket namespace are ours.
        for name in names:
            frame(active, "kill-session", name)
        time.sleep(0.1)
        shutil.rmtree(sandbox, ignore_errors=True)


def test_simultaneous_new_hosts_allocate_distinct_names(tmp_path: Path) -> None:
    import subprocess

    from tests.tui.test_start_workspace_contract import _entry_script, _shell_argv

    scene = Scene(tmp_path, live=("vc-host",))
    (scene.generation / "VERSION").write_text("4.3.1+g7a69d24d\n")
    argv = _shell_argv("bash", _entry_script(scene, "vc-start --new-host"))
    processes = [
        subprocess.Popen(
            argv,
            env=scene.env(),
            cwd=scene.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        results = [proc.communicate(timeout=30) for proc in processes]
        assert all("RC=[0]" in out for out, _ in results), results
        assert scene.live() == ["vc-host", "vc-host@7a69d24d", "vc-host@7a69d24d-2"]
        assert len(scene.terminal_launches(expect=2)) == 2
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def test_inside_host_with_tty_opens_separate_terminal(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host",), clients=("vc-host",))
    result = _run(
        scene,
        "vc-start --new-host",
        tty=True,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "2",
            "VC_FRAME_SESSION_NAME": "vc-host",
        },
    )
    assert _rc(result) == 0, result.stdout + result.stderr
    assert "vc-host-2" in scene.live()
    assert len(scene.terminal_launches()) == 1
    assert not any(c["argv"] == ["attach", "vc-host-2"] for c in scene.calls())


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("source_clients", [1, 2])
def test_parallel_operators_resume_project_from_calling_client(
    tmp_path: Path, shell: str, source_clients: int
) -> None:
    current = "vc-host@7a69d24d"
    scene = Scene(
        tmp_path,
        live=("vc-host", current, "existing-project"),
        clients=("vc-host",) + (current,) * source_clients + ("existing-project",) * 2,
        guests=("existing-project",),
    )
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    result = _run(
        scene,
        "_vetcoders_resume_workspace existing-project",
        shell=shell,
        developer_root=True,
        extra_env={
            "VC_FRAME": "1",
            "VC_FRAME_PANE_ID": "2",
            "VC_FRAME_SESSION_NAME": current,
            "VIBECRAFTED_PREFER_REPO_VC_FRAME": "1",
            "VIBECRAFTED_VC_FRAME_BIN": str(scene.generation / "bin/vc-frame"),
        },
    )
    assert _rc(result) == (0 if source_clients == 1 else 4), (
        result.stdout + result.stderr
    )
    switches = [c for c in scene.calls() if c.get("switched")]
    if source_clients == 1:
        assert len(switches) == 1
        assert switches[0]["argv"] == [
            "--session",
            current,
            # 1e9ae0f8: the sole observed source client id is forwarded so Frame
            # validates the exact attached frontend (UNIFIED_LAUNCH_CONTRACT.md).
            "--client-id",
            "1",
            "action",
            "switch-session",
            "existing-project",
        ]
        assert switches[0]["VC_FRAME_SESSION_NAME"] == current
        assert switches[0]["VC_FRAME_PANE_ID"] == "2"
    else:
        assert not switches
    assert not _creates(scene.calls())
    for name, body in before.items():
        assert (scene.table / "live" / name).read_bytes() == body
    assert not scene.terminal_launches(wait=0)


def test_start_refreshes_previous_shell_inventory_and_preserves_existing_project(
    tmp_path: Path,
) -> None:
    scene = Scene(
        tmp_path, live=("vc-host", "existing-project"), guests=("existing-project",)
    )
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    result = _run(
        scene,
        "_vetcoders_start_inventory_cache_valid=1; "
        '_vetcoders_start_cached_live_hosts=""; '
        "vc-start existing-project",
    )
    assert _rc(result) == 3, result.stdout + result.stderr
    assert not _creates(scene.calls())
    assert scene.live() == sorted(before)
    for name, body in before.items():
        assert (scene.table / "live" / name).read_bytes() == body
    assert not scene.terminal_launches(wait=0)


def _fault_created_host_role(scene: Scene, fault: str, failures: int) -> Path:
    """Model a live socket whose host canvas has not finished materializing."""
    count = scene.table / "created-host-role-probes"
    response = {
        "empty": "sys.exit(0)",
        "guest": 'print("layout { pane; }"); sys.exit(0)',
        "invalid": 'print("not a layout"); sys.exit(0)',
        "rpc": 'sys.stderr.write("host canvas unavailable\\n"); sys.exit(1)',
        "slow-rpc": 'time.sleep(11); sys.stderr.write("host canvas unavailable\\n"); sys.exit(1)',
    }[fault]
    injection = f"""    if verb == "dump-layout" and target.startswith("vc-host@"):
        counter = {str(count)!r}
        probes = int(open(counter).read()) if os.path.exists(counter) else 0
        with open(counter, "w") as handle:
            handle.write(str(probes + 1))
        if {failures} < 0 or probes < {failures}:
            {response}
"""
    for frame in (
        scene.generation / "bin/vc-frame",
        scene.generation / "libexec/vc-frame",
    ):
        source = frame.read_text()
        needle = '    if verb == "dump-layout":'
        assert source.count(needle) == 1
        frame.write_text(source.replace(needle, injection + needle))
    return count


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("fault", ["empty", "guest", "rpc"])
def test_new_host_waits_for_its_host_canvas(
    tmp_path: Path, shell: str, fault: str
) -> None:
    scene = Scene(tmp_path, live=("vc-host", "research"), guests=("research",))
    (scene.generation / "VERSION").write_text("4.3.2+gba7de3c9\n")
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    probes = _fault_created_host_role(scene, fault, failures=2)
    result = _run(scene, "vc-start --new-host", shell=shell)
    assert _rc(result) == 0, result.stdout + result.stderr
    assert int(probes.read_text()) == 3
    assert len(_creates(scene.calls())) == 1
    assert len(scene.terminal_launches()) == 1
    assert scene.live() == ["research", "vc-host", "vc-host@ba7de3c9"]
    for name, body in before.items():
        assert (scene.table / "live" / name).read_bytes() == body
    assert all(not c["argv"][0].startswith(("kill", "delete")) for c in scene.calls())


@pytest.mark.parametrize("shell", ["bash", "zsh"])
@pytest.mark.parametrize("fault", ["invalid", "rpc", "guest"])
def test_new_host_unready_canvas_refuses_with_detached_attach_hint(
    tmp_path: Path, shell: str, fault: str
) -> None:
    scene = Scene(tmp_path, live=("vc-host", "research"), guests=("research",))
    (scene.generation / "VERSION").write_text("4.3.2+gba7de3c9\n")
    before = {p.name: p.read_bytes() for p in (scene.table / "live").iterdir()}
    probes = _fault_created_host_role(scene, fault, failures=-1)
    result = _run(scene, "vc-start --new-host", shell=shell, timeout=35)
    assert _rc(result) == 4, result.stdout + result.stderr
    assert 1 < int(probes.read_text()) <= 40
    assert "remains detached" in result.stderr
    assert "vc-host@ba7de3c9" in result.stderr
    assert "attach vc-host@ba7de3c9" in result.stderr
    assert (
        "no frame_host true marker" if fault == "guest" else "host-role probe failed"
    ) in result.stderr
    assert len(_creates(scene.calls())) == 1
    assert not scene.terminal_launches()
    assert scene.live() == ["research", "vc-host", "vc-host@ba7de3c9"]
    for name, body in before.items():
        assert (scene.table / "live" / name).read_bytes() == body
    assert all(not c["argv"][0].startswith(("kill", "delete")) for c in scene.calls())


def test_owned_terminal_child_invalid_role_is_not_polled(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host", "vc-host@ba7de3c9"))
    probes = _fault_created_host_role(scene, "rpc", failures=-1)
    result = _run(
        scene,
        "_vetcoders_start_is_owned_terminal_child() { return 0; }; vc-start --new-host",
        tty=True,
        extra_env={"VIBECRAFTED_START_CREATED_HOST": "vc-host@ba7de3c9"},
    )
    assert _rc(result) == 4, result.stdout + result.stderr
    assert int(probes.read_text()) == 1
    assert not _creates(scene.calls())
    assert not scene.terminal_launches()


def test_new_host_stops_retrying_after_a_slow_failed_role_probe(tmp_path: Path) -> None:
    scene = Scene(tmp_path, live=("vc-host",))
    (scene.generation / "VERSION").write_text("4.3.2+gba7de3c9\n")
    probes = _fault_created_host_role(scene, "slow-rpc", failures=-1)
    result = _run(scene, "vc-start --new-host", timeout=25)
    assert _rc(result) == 4, result.stdout + result.stderr
    assert int(probes.read_text()) == 1
    assert "host-role probe failed" in result.stderr
    assert "attach vc-host@ba7de3c9" in result.stderr
    assert len(_creates(scene.calls())) == 1
    assert not scene.terminal_launches()
