"""Product entry choke — execute shipped shell/wrapper, not greps or reimpls.

Surfaces under test:
  - scripts/vc-frame-product-entry.sh  (bare frame refuse / pin)
  - runtime/shell vc-start path          (_vetcoders_product_entry_prepare via probe)
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "vc-frame-product-entry.sh"
HELPER = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
DASHBOARD = REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/dashboard.sh"
DISPATCH = (
    REPO
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "lib"
    / "dispatch.sh"
)
FOUNDATIONS = REPO / "scripts" / "install-foundations.sh"


def _write_fake_bin(bin_dir: Path, name: str, body: str) -> Path:
    path = bin_dir / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def test_wrapper_exists_executable() -> None:
    assert WRAPPER.is_file()
    assert WRAPPER.stat().st_mode & stat.S_IXUSR
    text = WRAPPER.read_text(encoding="utf-8")
    assert "pin_product_config" in text
    assert "pin_darwin_socket_dir" in text
    assert "is_product_session_name" in text
    assert "vc-frame.real" not in text


def test_installer_only_mentions_retired_sibling_for_one_way_cleanup() -> None:
    text = FOUNDATIONS.read_text(encoding="utf-8")
    assert text.count("vc-frame.real") == 1
    assert 'legacy_bin="$LAUNCHER_PREFIX/vc-frame.real"' in text
    assert 'rm -f "$legacy_bin"' in text
    assert 'ln -sfn "$current/bin/vc-frame" "$dest"' in text


def test_wrapper_never_executes_retired_sibling_shadow(tmp_path: Path) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    cargo_bin = home / ".cargo" / "bin"
    xdg.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    cargo_bin.mkdir(parents=True)

    _write_fake_bin(
        bin_dir,
        "vc-frame.real",
        "#!/usr/bin/env bash\necho RETIRED_SHADOW_RAN\nexit 91\n",
    )
    _write_fake_bin(
        cargo_bin,
        "vc-frame",
        "#!/usr/bin/env bash\necho CANONICAL_RAN args=$*\nexit 0\n",
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "USER": "test",
    }
    proc = subprocess.run(
        [str(wrapper), "list-sessions"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "CANONICAL_RAN args=list-sessions" in proc.stdout
    assert "RETIRED_SHADOW_RAN" not in proc.stdout


def test_product_entry_prepare_exists_in_shipped_dashboard() -> None:
    """Shell prepare is the real choke (vc-start never enters deck cmd_start)."""
    text = DASHBOARD.read_text(encoding="utf-8")
    assert "_vetcoders_product_entry_prepare()" in text
    dispatch = DISPATCH.read_text(encoding="utf-8")
    assert "_vetcoders_product_entry_prepare" in dispatch
    assert "VIBECRAFTED_PRODUCT_ENTRY_PROBE" in dispatch
    start = text.index("_vetcoders_product_entry_prepare() {")
    end = text.index("\n}", start)
    body = text[start:end]
    assert body.index("_vetcoders_path_with_bundled_bin_priority") < body.index(
        "_vetcoders_product_workspace_prepare"
    )
    assert body.index("_vetcoders_product_workspace_prepare") < body.index(
        "_vetcoders_control_plane_eye_prepare"
    )
    assert "vibecrafted workspace resolve" not in text
    assert "vibecrafted server status" not in text
    assert "_vetcoders_product_core_cli workspace resolve --env" in text
    vc_frame = (
        REPO / "vibecrafted-core/vibecrafted_core/runtime/shell/lib/vc_frame.sh"
    ).read_text(encoding="utf-8")
    assert '_vetcoders_product_core_cli "${args[@]}"' in vc_frame


def test_shipped_deck_routes_workspace_resolution_to_core(tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = tmp_path / "workspace"
    home.mkdir()
    root.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
        "VIBECRAFTED_PYTHON": sys.executable,
    }
    proc = subprocess.run(
        [
            str(REPO / "scripts/vibecrafted"),
            "workspace",
            "resolve",
            "--root",
            str(root),
            "--env",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "VIBECRAFTED_WORKSPACE_ID=" in proc.stdout
    assert "VIBECRAFTED_OPERATOR_SESSION=workspace-" in proc.stdout


def test_wrapper_refuses_product_attach_without_config(tmp_path: Path) -> None:
    """Execute shipped wrapper: product attach with no frontier/view config → exit 2."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()

    real = _write_fake_bin(
        bin_dir,
        "vc-frame-bin",
        "#!/usr/bin/env bash\necho REAL_RAN args=$*\nexit 0\n",
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_VC_FRAME_BIN": str(real),
        "USER": "test",
    }
    # No VC_FRAME_* leakage
    for key in list(os.environ):
        if key.startswith("VC_FRAME"):
            continue
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        **env,
    }

    proc = subprocess.run(
        [str(wrapper), "attach", "vibecrafted"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "Refusing bare attach to product session" in proc.stderr
    assert "Run: vc-start" in proc.stderr
    assert "REAL_RAN" not in proc.stdout


def test_wrapper_refuses_dash_s_product_session_without_config(tmp_path: Path) -> None:
    """-s / --session product names also refuse without product config."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    real = _write_fake_bin(
        bin_dir,
        "vc-frame-bin",
        "#!/usr/bin/env bash\necho REAL_RAN\nexit 0\n",
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_VC_FRAME_BIN": str(real),
        "USER": "test",
    }
    proc = subprocess.run(
        [str(wrapper), "-s", "operator", "list-sessions"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert "Refusing bare attach to product session" in proc.stderr
    assert "REAL_RAN" not in proc.stdout


def test_wrapper_pins_and_execs_when_frontier_config_present(tmp_path: Path) -> None:
    """With frontier config present, wrapper pins VC_FRAME_CONFIG_DIR and execs real bin."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    frontier = xdg / "vetcoders" / "frontier" / "vc-frame"
    frontier.mkdir(parents=True)
    (frontier / "config.kdl").write_text("// product\n", encoding="utf-8")

    real = _write_fake_bin(
        bin_dir,
        "vc-frame-bin",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf 'VC_FRAME_CONFIG_DIR=%s\\n' "${VC_FRAME_CONFIG_DIR:-}"
            printf 'args=%s\\n' "$*"
            exit 0
            """
        ),
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_VC_FRAME_BIN": str(real),
        "USER": "test",
    }
    proc = subprocess.run(
        [str(wrapper), "attach", "vibecrafted"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert f"VC_FRAME_CONFIG_DIR={frontier}" in proc.stdout
    assert "args=attach vibecrafted" in proc.stdout


def test_wrapper_pins_darwin_socket_dir_when_unset(tmp_path: Path) -> None:
    """Claude/CLI path: pin /tmp/vc-frame-$UID when neither socket env is set."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    real = _write_fake_bin(
        bin_dir,
        "vc-frame-bin",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            printf 'VC_FRAME_SOCKET_DIR=%s\\n' "${VC_FRAME_SOCKET_DIR:-}"
            printf 'ZELLIJ_SOCKET_DIR=%s\\n' "${ZELLIJ_SOCKET_DIR:-}"
            exit 0
            """
        ),
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    env = {
        **{
            k: v
            for k, v in os.environ.items()
            if not k.startswith("VC_FRAME") and k != "ZELLIJ_SOCKET_DIR"
        },
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_VC_FRAME_BIN": str(real),
        "USER": "test",
    }
    proc = subprocess.run(
        [str(wrapper), "list-sessions"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=20,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    if sys.platform == "darwin":
        expected = f"/tmp/vc-frame-{os.getuid()}"
        assert f"VC_FRAME_SOCKET_DIR={expected}" in proc.stdout
        assert f"ZELLIJ_SOCKET_DIR={expected}" in proc.stdout


def test_wrapper_allows_non_product_session_without_config(tmp_path: Path) -> None:
    """Non-product sessions may run bare without product config (scratch sessions etc.)."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    real = _write_fake_bin(
        bin_dir,
        "vc-frame-bin",
        "#!/usr/bin/env bash\necho REAL_RAN args=$*\nexit 0\n",
    )
    wrapper = bin_dir / "vc-frame"
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_VC_FRAME_BIN": str(real),
        "USER": "test",
    }
    proc = subprocess.run(
        [str(wrapper), "attach", "scratch"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "REAL_RAN args=attach scratch" in proc.stdout


def test_vc_start_probe_pins_product_config(tmp_path: Path) -> None:
    """Exercise real shell vc-start path with PROBE=1 (no TUI attach)."""
    assert HELPER.is_file(), f"missing helper facade {HELPER}"

    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    frontier = xdg / "vetcoders" / "frontier" / "vc-frame"
    layout = frontier / "layouts" / "operator.kdl"
    frontier.mkdir(parents=True)
    layout.parent.mkdir(parents=True)
    (frontier / "config.kdl").write_text("// product frontier\n", encoding="utf-8")
    layout.write_text("layout {\n}\n", encoding="utf-8")
    # starship.toml makes frontier_root resolve for sidecar path
    (xdg / "vetcoders" / "frontier" / "starship.toml").write_text(
        "#\n", encoding="utf-8"
    )

    _write_fake_bin(
        bin_dir,
        "vc-frame",
        "#!/usr/bin/env bash\necho should-not-run-in-probe\nexit 99\n",
    )
    _write_fake_bin(bin_dir, "python3", "#!/usr/bin/env bash\nexit 1\n")
    # vibecrafted server status is best-effort — fake that returns non-zero is fine
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "$*" == "workspace resolve --env" ]]; then
              echo VIBECRAFTED_WORKSPACE_ID=019ff97a-3328-7660-b6cd-f957b1b163f8
              echo VIBECRAFTED_WORKSPACE_INSTANCE_ID=019ff97a-3328-7660-b6cd-f957b1b163f9
              echo VIBECRAFTED_OPERATOR_SESSION=workspace-b1b163f8
              exit 0
            fi
            exit 1
            """
        ),
    )

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_ROOT": str(REPO),
        "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        "VIBECRAFTED_PRODUCT_ENTRY_PROBE": "1",
        "USER": "test",
    }
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VC_FRAME_SESSION_NAME", None)
    env.pop("VC_FRAME", None)
    env.pop("ZELLIJ", None)
    env.pop("ZELLIJ_SESSION_NAME", None)

    proc = subprocess.run(
        ["bash", "-lc", f'source "{HELPER}"; vc-start'],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, out
    assert "VIBECRAFTED_PRODUCT_ENTRY=1" in proc.stdout
    assert f"VC_FRAME_CONFIG_DIR={frontier}" in proc.stdout
    assert "VC_FRAME_CONFIG_KDL=present" in proc.stdout
    assert "OPERATOR_LAYOUT_PRESENT=1" in proc.stdout
    assert (
        "VIBECRAFTED_WORKSPACE_ID=019ff97a-3328-7660-b6cd-f957b1b163f8" in proc.stdout
    )
    assert (
        "VIBECRAFTED_WORKSPACE_INSTANCE_ID=019ff97a-3328-7660-b6cd-f957b1b163f9"
        in proc.stdout
    )
    assert "VIBECRAFTED_OPERATOR_SESSION=workspace-b1b163f8" in proc.stdout
    assert "should-not-run-in-probe" not in out


def test_product_entry_reconciles_the_one_macos_server_service_owner(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "server-calls"
    frontier = xdg / "vetcoders/frontier/vc-frame"
    (frontier / "layouts").mkdir(parents=True)
    (frontier / "config.kdl").write_text("// product frontier\n", encoding="utf-8")
    (frontier / "layouts/operator.kdl").write_text("layout {\n}\n", encoding="utf-8")
    home.mkdir()
    bin_dir.mkdir()

    _write_fake_bin(bin_dir, "python3", "#!/usr/bin/env bash\nexit 1\n")
    _write_fake_bin(bin_dir, "uname", "#!/usr/bin/env bash\necho Darwin\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> "{capture}"
            if [[ "$*" == "workspace resolve --env" ]]; then exit 0; fi
            if [[ "$*" == "server status" ]]; then exit 1; fi
            if [[ "$*" == "server service reconcile" ]]; then exit 0; fi
            exit 1
            """
        ),
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_ROOT": str(REPO),
        "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        "VIBECRAFTED_PRODUCT_ENTRY_PROBE": "1",
        "USER": "test",
    }
    proc = subprocess.run(
        ["bash", "-lc", f'source "{HELPER}"; vc-start'],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = capture.read_text(encoding="utf-8").splitlines()
    assert "server status" in calls
    assert "server service reconcile" in calls


def test_product_entry_does_not_invent_a_non_macos_service_owner(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "server-calls"
    bin_dir.mkdir()
    _write_fake_bin(bin_dir, "uname", "#!/usr/bin/env bash\necho Linux\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{capture}"\nexit 1\n',
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DASHBOARD}"; _vetcoders_control_plane_eye_prepare',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        },
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == ["server status"]


def test_product_entry_keeps_a_healthy_macos_server_untouched(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    capture = tmp_path / "server-calls"
    bin_dir.mkdir()
    _write_fake_bin(bin_dir, "uname", "#!/usr/bin/env bash\necho Darwin\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{capture}"\nexit 0\n',
    )

    proc = subprocess.run(
        [
            "bash",
            "-c",
            f'source "{DASHBOARD}"; _vetcoders_control_plane_eye_prepare',
        ],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "VIBECRAFTED_PRODUCT_CORE_CLI": str(bin_dir / "vibecrafted"),
        },
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == ["server status"]


def test_vc_start_probe_twice_is_stable(tmp_path: Path) -> None:
    """Verification plan: re-run twice for consistent success."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    xdg.mkdir()
    bin_dir.mkdir()
    frontier = xdg / "vetcoders" / "frontier" / "vc-frame"
    frontier.mkdir(parents=True)
    (frontier / "layouts").mkdir()
    (frontier / "config.kdl").write_text("// product\n", encoding="utf-8")
    (frontier / "layouts" / "operator.kdl").write_text("layout {}\n", encoding="utf-8")
    (xdg / "vetcoders" / "frontier" / "starship.toml").write_text(
        "#\n", encoding="utf-8"
    )
    _write_fake_bin(bin_dir, "vc-frame", "#!/usr/bin/env bash\nexit 0\n")
    _write_fake_bin(bin_dir, "python3", "#!/usr/bin/env bash\nexit 1\n")
    _write_fake_bin(
        bin_dir,
        "vibecrafted",
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [[ "$*" == "workspace resolve --env" ]]; then
              echo VIBECRAFTED_WORKSPACE_ID=019ff97a-3328-7660-b6cd-f957b1b163f8
              echo VIBECRAFTED_WORKSPACE_INSTANCE_ID=019ff97a-3328-7660-b6cd-f957b1b163f9
              echo VIBECRAFTED_OPERATOR_SESSION=workspace-b1b163f8
              exit 0
            fi
            exit 1
            """
        ),
    )

    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("VC_FRAME")},
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "VIBECRAFTED_ROOT": str(REPO),
        "VIBECRAFTED_PRODUCT_ENTRY_PROBE": "1",
        "USER": "test",
    }
    env.pop("VC_FRAME_CONFIG_DIR", None)

    outs: list[str] = []
    for _ in range(2):
        proc = subprocess.run(
            ["bash", "-lc", f'source "{HELPER}"; vc-start'],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert outs[0] == outs[1]
    assert f"VC_FRAME_CONFIG_DIR={frontier}" in outs[0]
