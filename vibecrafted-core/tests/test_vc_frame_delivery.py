"""Product paths, selected layout retention and unpublished host adaptation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from vibecrafted_core.frontier_assets import vc_frame_config_source
from vibecrafted_core.vc_frame_delivery import (
    publish_selected_project_layout,
    vc_frame_user_config_dir,
)
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
    substitute_pane_shell,
)


def test_substitute_pane_shell_only_exact_command_zsh() -> None:
    text = 'pane command="zsh"\npane command="zsh-lookalike"\n'
    out = substitute_pane_shell(text, "bash")
    assert 'command="bash"' in out
    assert 'command="zsh-lookalike"' in out
    assert 'command="zsh"' not in out.replace("zsh-lookalike", "")


def test_substitute_noop_when_shell_is_zsh() -> None:
    text = 'command="zsh"'
    assert substitute_pane_shell(text, "zsh") == text


@pytest.mark.parametrize("present", [False, True])
def test_product_path_resolution_is_read_only_and_ignores_alternate_roots(
    tmp_path: Path, monkeypatch, present: bool
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    if sys.platform == "win32":
        roaming = home / "AppData" / "Roaming"
        roaming.mkdir(parents=True)
        config = roaming / "Vibecrafted" / "vc-frame"
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("APPDATA", str(roaming))
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    else:
        config = home / ".config/vibecrafted/vc-frame"
        monkeypatch.setenv("HOME", str(home))
    if present:
        config.mkdir(parents=True)
        (config / "config.kdl").write_text("copy_on_select true\n")
    for name in ("XDG_CONFIG_HOME", "VC_FRAME_CONFIG_DIR", "VIBECRAFTED_RUNTIME_ROOT"):
        monkeypatch.setenv(name, str(tmp_path / "foreign"))
    before = {p: p.stat().st_mtime_ns for p in home.rglob("*")}
    for _ in range(3):
        assert vc_frame_user_config_dir() == config
        assert (
            vc_frame_user_config_dir(home)
            == home / ".config" / "vibecrafted" / "vc-frame"
        )
    assert {p: p.stat().st_mtime_ns for p in home.rglob("*")} == before
    assert not (tmp_path / "foreign").exists()


def test_materialize_complete_physical_tree_preserves_source(tmp_path: Path) -> None:
    source = vc_frame_config_source()
    before = {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    }
    destination = tmp_path / "candidate/generated/vc-frame"
    materialize_vc_frame_config(
        source, destination, pane_shell="zsh", clipboard_command="pbcopy"
    )
    assert {
        p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()
    } == before
    assert {
        p.relative_to(destination): p.read_bytes()
        for p in destination.rglob("*")
        if p.is_file()
    } == before
    assert not any(p.is_symlink() for p in destination.rglob("*"))
    for name in (
        "vc-composer.sh",
        "pane-python",
        "vc-agent-workshop.py",
        "vc-start-here.py",
    ):
        assert os.access(destination / name, os.X_OK)
    assert (destination / "layouts/operator.kdl").is_file()
    assert (destination / "themes").is_dir()


@pytest.fixture
def selected_project_layout(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
    view = vc_frame_user_config_dir()
    (view / "layouts").mkdir(parents=True)
    (view / "config.kdl").write_bytes(b"// existing product\n")
    (view / "layouts/operator.kdl").write_bytes(b"layout {}\n")
    layout = tmp_path / "cache/layout.kdl"
    layout.parent.mkdir()
    layout.write_bytes(b'layout {\r\n tab name="Selected" { pane; }\r\n}\r\n')
    layout.with_suffix(".swap.kdl").write_bytes(b"// exact companion\n")
    return layout, view


def test_selected_layout_is_exact_complete_idempotent_product_snapshot(
    selected_project_layout,
) -> None:
    layout, view = selected_project_layout
    original = {p: p.read_bytes() for p in view.rglob("*") if p.is_file()}
    published = publish_selected_project_layout(layout)
    assert published.parent == view / "layouts"
    assert published.read_bytes() == layout.read_bytes()
    assert (
        published.with_suffix(".swap.kdl").read_bytes()
        == layout.with_suffix(".swap.kdl").read_bytes()
    )
    inode = published.stat().st_ino
    modified = published.stat().st_mtime_ns
    assert publish_selected_project_layout(layout) == published
    assert published.stat().st_ino == inode
    assert published.stat().st_mtime_ns == modified
    assert {p: p.read_bytes() for p in original} == original
    assert publish_selected_project_layout(view / "layouts/operator.kdl") == (
        view / "layouts/operator.kdl"
    )
    layout.with_suffix(".swap.kdl").write_bytes(b"// different swap\n")
    changed = publish_selected_project_layout(layout)
    assert changed != published
    assert published.with_suffix(".swap.kdl").read_bytes() == b"// exact companion\n"


@pytest.mark.parametrize("blocked", ["layout", "swap", "layouts", "config", "sealed"])
def test_selected_layout_refuses_aliases_and_runtime_destinations(
    selected_project_layout, tmp_path: Path, blocked: str
) -> None:
    layout, view = selected_project_layout
    if blocked == "sealed":
        (view.parent / "runtime-manifest.json").write_bytes(b"{}")
    else:
        target = {
            "layout": layout,
            "swap": layout.with_suffix(".swap.kdl"),
            "layouts": view / "layouts",
            "config": view / "config.kdl",
        }[blocked]
        moved = tmp_path / f"foreign-{blocked}"
        target.rename(moved)
        target.symlink_to(moved, target_is_directory=moved.is_dir())
    before = {
        p: p.read_bytes() for p in view.rglob("*") if p.is_file() and not p.is_symlink()
    }
    with pytest.raises(OSError):
        publish_selected_project_layout(layout)
    assert {p: p.read_bytes() for p in before} == before
    assert not list((view / "layouts").glob("project-*"))


@pytest.mark.parametrize("foreign", ["content", "symlink"])
def test_selected_layout_never_overwrites_conflicting_retained_bytes(
    selected_project_layout, tmp_path: Path, foreign: str
) -> None:
    layout, _view = selected_project_layout
    published = publish_selected_project_layout(layout)
    published.unlink()
    if foreign == "symlink":
        outside = tmp_path / "foreign.kdl"
        outside.write_bytes(b"foreign workload")
        published.symlink_to(outside)
    else:
        published.write_bytes(b"foreign workload")
    with pytest.raises(OSError):
        publish_selected_project_layout(layout)
    assert published.read_bytes() == b"foreign workload"
    assert published.is_symlink() == (foreign == "symlink")


def test_parallel_selected_layout_publication_exposes_one_complete_pair(
    selected_project_layout,
) -> None:
    layout, view = selected_project_layout
    with ThreadPoolExecutor(max_workers=4) as executor:
        published = list(executor.map(publish_selected_project_layout, [layout] * 8))
    assert len(set(published)) == 1
    assert published[0].read_bytes() == layout.read_bytes()
    assert (
        published[0].with_suffix(".swap.kdl").read_bytes()
        == layout.with_suffix(".swap.kdl").read_bytes()
    )
    assert sorted(p.name for p in (view / "layouts").iterdir()) == sorted(
        ["operator.kdl", published[0].name, published[0].with_suffix(".swap.kdl").name]
    )


def test_selected_layout_does_not_repair_missing_product_configuration(
    selected_project_layout,
) -> None:
    layout, view = selected_project_layout
    (view / "config.kdl").unlink()
    with pytest.raises(OSError):
        publish_selected_project_layout(layout)
    assert not (view / "config.kdl").exists()
    assert not list((view / "layouts").glob("project-*"))


def test_absent_selected_swap_cannot_acquire_a_foreign_companion(
    selected_project_layout,
) -> None:
    layout, _view = selected_project_layout
    layout.with_suffix(".swap.kdl").unlink()
    published = publish_selected_project_layout(layout)
    published.with_suffix(".swap.kdl").write_bytes(b"foreign companion")
    before = published.stat().st_mtime_ns
    with pytest.raises(OSError):
        publish_selected_project_layout(layout)
    assert published.stat().st_mtime_ns == before
    assert published.with_suffix(".swap.kdl").read_bytes() == b"foreign companion"


def test_real_product_wrapper_consumes_retained_selected_pair(monkeypatch) -> None:
    """Real wrapper/native boundary; canonical WES owner has separate joint proof."""
    selected = os.environ.get("VC_TEST_FRAME_ENGINE")
    if not selected:
        pytest.skip("VC_TEST_FRAME_ENGINE must select a real Frame engine")
    engine = Path(selected).resolve(strict=True)
    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="vc-layout-", dir="/tmp") as temporary:
        scratch = Path(temporary).resolve()
        runtime = scratch / "runtime"
        (runtime / "bin").mkdir(parents=True)
        (runtime / "libexec").mkdir()
        native = runtime / "libexec/vc-frame"
        shutil.copy2(engine, native)
        entry = runtime / "bin/vc-frame"
        shutil.copy2(repo / "scripts/vc-frame-product-entry.sh", entry)
        entry.chmod(0o755)
        (runtime / "runtime-manifest.json").write_bytes(b"{}\n")
        generation = {p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
        home = scratch / "home"
        monkeypatch.setenv("HOME", str(home))
        view = vc_frame_user_config_dir()
        (view / "layouts").mkdir(parents=True)
        (view / "config.kdl").write_text(
            'default_shell "/bin/bash"\nsession_serialization false\n'
        )
        sockets = scratch / "s"
        sockets.mkdir()
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home),
            "SHELL": "/bin/bash",
            "TERM": "xterm-256color",
            "XDG_CACHE_HOME": str(scratch / "cache"),
            "XDG_DATA_HOME": str(scratch / "data"),
            "VC_FRAME_SOCKET_DIR": str(sockets),
            "ZELLIJ_SOCKET_DIR": str(sockets),
        }
        marker = scratch / "workload.json"
        script = scratch / "workload.py"
        script.write_text(
            "import json,os,pathlib,time\n"
            f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'pid':os.getpid()}}))\n"
            "while True: time.sleep(0.1)\n"
        )
        layout = scratch / "cache-selected/layout.kdl"
        layout.parent.mkdir()
        layout.write_text(
            'layout {\n tab name="Selected workload" {\n pane command='
            + json.dumps(sys.executable)
            + " { args "
            + json.dumps(str(script))
            + "; }\n }\n}\n"
        )
        layout.with_suffix(".swap.kdl").write_bytes(
            b'swap_tiled_layout name="exact-selected-swap" {\n tab {\n pane\n }\n}\n'
        )
        actions = []

        def cli(*args):
            proc = subprocess.run(
                [str(entry), *args],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            actions.append(
                {
                    "argv": list(args),
                    "status": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }
            )
            return proc

        try:
            refused = cli(
                "--new-session-with-layout",
                str(layout),
                "attach",
                "--create-background",
                "selected-pair",
            )
            assert refused.returncode == 2
            assert not marker.exists()
            published = publish_selected_project_layout(layout)
            assert published.read_bytes() == layout.read_bytes()
            assert published.with_suffix(".swap.kdl").read_bytes() == (
                layout.with_suffix(".swap.kdl").read_bytes()
            )
            accepted = cli(
                "--new-session-with-layout",
                str(published),
                "attach",
                "--create-background",
                "selected-pair",
            )
            assert accepted.returncode == 0, accepted.stdout + accepted.stderr
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert marker.exists()
            pid = json.loads(marker.read_text())["pid"]
            panes = cli(
                "--session", "selected-pair", "action", "list-panes", "--json", "--all"
            )
            assert panes.returncode == 0, panes.stdout + panes.stderr
            assert publish_selected_project_layout(layout) == published
            assert (
                cli(
                    "--session",
                    "selected-pair",
                    "action",
                    "list-panes",
                    "--json",
                    "--all",
                ).stdout
                == panes.stdout
            )
            names = cli("list-sessions", "--short", "--no-formatting")
            assert names.stdout.splitlines() == ["selected-pair"]
            os.kill(pid, 0)
            assert {
                p: p.read_bytes() for p in runtime.rglob("*") if p.is_file()
            } == generation
        finally:
            processes = subprocess.check_output(
                ["ps", "-ax", "-o", "pid=", "-o", "command="], text=True
            )
            servers = set()
            for line in processes.splitlines():
                fields = line.strip().split(None, 1)
                if len(fields) == 2 and fields[1].startswith(
                    str(native) + " --server "
                ):
                    servers.add(int(fields[0]))
            cli("kill-session", "selected-pair")
            deadline = time.monotonic() + 10
            while servers:
                for pid in tuple(servers):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        servers.remove(pid)
                assert not servers or time.monotonic() < deadline, servers
                if servers:
                    time.sleep(0.05)
            receipt = os.environ.get("VC_TEST_FRAME_RECEIPT_DIR")
            if receipt:
                destination = Path(receipt)
                destination.mkdir(parents=True, exist_ok=True)
                (destination / "selected-pair-wrapper.json").write_text(
                    json.dumps(actions, indent=2) + "\n"
                )


@pytest.mark.parametrize("blocked", ["existing", "sealed", "symlink"])
def test_materializer_refuses_published_or_aliased_destination(
    tmp_path: Path, blocked: str
) -> None:
    root = tmp_path / "candidate"
    root.mkdir()
    destination = root / "generated/vc-frame"
    if blocked == "existing":
        destination.mkdir(parents=True)
        (destination / "config.kdl").write_text("user bytes\n")
    elif blocked == "sealed":
        (root / "runtime-manifest.json").write_text("{}\n")
    else:
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "generated").symlink_to(outside, target_is_directory=True)
    before = {
        p: (p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    }
    with pytest.raises(OSError, match="already exists|sealed runtime|symlink"):
        materialize_vc_frame_config(
            vc_frame_config_source(),
            destination,
            pane_shell="bash",
            clipboard_command=None,
        )
    assert {
        p: (p.readlink() if p.is_symlink() else p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    } == before


@pytest.mark.parametrize("missing", ["source", "config"])
def test_materializer_missing_source_leaves_destination_absent(
    tmp_path: Path, missing: str
) -> None:
    source = tmp_path / "source"
    if missing == "config":
        source.mkdir()
    destination = tmp_path / "candidate/generated/vc-frame"
    with pytest.raises(OSError, match="source"):
        materialize_vc_frame_config(
            source, destination, pane_shell="sh", clipboard_command=None
        )
    assert not destination.exists()


def test_pane_shell_substitution_without_zsh(tmp_path: Path, monkeypatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (fake_bin / "bash").symlink_to(bash)
    monkeypatch.delenv("SHELL", raising=False)
    shell = resolve_pane_shell(str(fake_bin))
    clipboard = resolve_clipboard_command(str(fake_bin))
    assert shell == "bash"
    assert clipboard is None
    destination = tmp_path / "candidate/generated/vc-frame"
    materialize_vc_frame_config(
        vc_frame_config_source(),
        destination,
        pane_shell=shell,
        clipboard_command=clipboard,
    )
    kdl = "\n".join(p.read_text() for p in destination.rglob("*.kdl"))
    assert 'command="bash"' in kdl
    for token in (
        'command="zsh"',
        'default_shell "zsh"',
        "exec zsh -l",
        "exec /bin/zsh -l",
        'copy_command "pbcopy"',
        "pbcopy <",
    ):
        assert token not in kdl
