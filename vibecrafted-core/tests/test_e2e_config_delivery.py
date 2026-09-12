"""W3-B: e2e channel matrix (wheel→venv→stage × zsh, plus dev + upgrade)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import venv
import zipfile
from pathlib import Path

import pytest
from vibecrafted_core.frontier_assets import vc_frame_config_source
from vibecrafted_core.vc_frame_delivery import stage_vc_frame_config
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
)

CORE = Path(__file__).resolve().parents[1]
REPO = CORE.parent

pytestmark = pytest.mark.e2e_delivery


def _build_wheel(dist: Path) -> Path:
    dist.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=str(CORE),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
            cwd=str(CORE),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        pytest.skip(f"wheel build failed: {(proc.stderr or proc.stdout)[-400:]}")
    return wheels[-1]


def _path_with_only_bash(tmp_path: Path) -> str:
    """PATH containing bash but not zsh (forces pane-shell substitution)."""
    bash = shutil.which("bash")
    assert bash, "host must have bash for zsh-absent matrix cell"
    fake = tmp_path / "path_bash_only"
    fake.mkdir()
    (fake / "bash").symlink_to(bash)
    # deliberately no zsh
    return str(fake)


def _path_with_zsh() -> str:
    zsh = shutil.which("zsh")
    if not zsh:
        pytest.skip("host has no zsh; cannot prove zsh-present cell")
    return os.environ.get("PATH", "/usr/bin:/bin")


def _install_wheel_venv(wheel: Path, venv_dir: Path) -> Path:
    """Create venv, install wheel --no-deps, return venv python path.

    Prefer ``uv venv`` + ``uv pip`` (stable on this host). Fall back to
    stdlib ``venv`` + ensurepip; if that SIGABRTs, record env limit and skip.
    """
    py: Path | None = None
    # 1) uv venv (no ensurepip)
    uv = shutil.which("uv")
    if uv:
        proc = subprocess.run(
            [uv, "venv", str(venv_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            py = venv_dir / "bin" / "python"
            if not py.is_file():
                py = venv_dir / "Scripts" / "python.exe"
            if py.is_file():
                inst = subprocess.run(
                    [
                        uv,
                        "pip",
                        "install",
                        "--python",
                        str(py),
                        "--no-deps",
                        str(wheel),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if inst.returncode == 0:
                    return py
                pytest.fail(
                    "uv pip install failed:\n"
                    + (inst.stdout or "")[-800:]
                    + (inst.stderr or "")[-800:]
                )
    # 2) stdlib venv
    try:
        venv.create(venv_dir, with_pip=True, clear=True)
    except Exception as exc:  # noqa: BLE001 — env limitation is a test outcome
        limit = Path(
            os.environ.get(
                "VIBECRAFTED_E2E_LIMIT_LOG",
                str(REPO / "dist" / "e2e-env-limit.txt"),
            )
        )
        # Prefer goal scratch if present via env from harness
        scratch = os.environ.get("GROK_GOAL_SCRATCH")
        if scratch:
            limit = Path(scratch) / "e2e-env-limit.txt"
        limit.parent.mkdir(parents=True, exist_ok=True)
        limit.write_text(
            f"venv.create(with_pip=True) failed: {type(exc).__name__}: {exc}\n"
            f"uv available: {bool(uv)}\n",
            encoding="utf-8",
        )
        pytest.skip(f"venv creation impossible on this host: {exc}")
    py = venv_dir / "bin" / "python"
    if not py.is_file():
        py = venv_dir / "Scripts" / "python.exe"
    assert py is not None and py.is_file(), f"venv python missing under {venv_dir}"
    proc = subprocess.run(
        [str(py), "-m", "pip", "install", "--no-deps", str(wheel)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(
            "pip install wheel failed:\n"
            + (proc.stdout or "")[-800:]
            + (proc.stderr or "")[-800:]
        )
    return py


def _run_stage_in_venv(
    *,
    venv_python: Path,
    home: Path,
    version: str,
    path_env: str,
    prefer_repo: bool = False,
) -> str:
    """Import stage_vc_frame_config from the *installed* wheel and run it."""
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    script = textwrap.dedent(
        f"""
        import os, sys, json
        from pathlib import Path
        home = Path({str(home)!r})
        tools = Path({str(tools)!r})
        os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        runtime_root = tools / "vibecrafted-full"
        runtime_payload = runtime_root / "vibecrafted-core" / "vibecrafted_core" / "runtime"
        (runtime_root / "vibecrafted-core").mkdir(parents=True)
        (runtime_payload / "scripts").mkdir(parents=True)
        (runtime_root / "Makefile").write_text("all:\\n\\t@true\\n", encoding="utf-8")
        (runtime_payload / "scripts" / "codex_spawn.sh").write_text(
            "#!/usr/bin/env bash\\n", encoding="utf-8"
        )
        if {prefer_repo!r}:
            os.environ["VIBECRAFTED_PREFER_REPO_VC_FRAME"] = "1"
        else:
            os.environ.pop("VIBECRAFTED_PREFER_REPO_VC_FRAME", None)
        import vibecrafted_core
        from vibecrafted_core.frontier_assets import vc_frame_config_source
        from vibecrafted_core.vc_frame_delivery import (
            stage_vc_frame_config,
            vc_frame_user_config_dir,
        )
        from vibecrafted_core.vc_frame_staging import (
            materialize_vc_frame_config,
            resolve_clipboard_command,
            resolve_pane_shell,
        )
        package_root = Path(vibecrafted_core.__file__).resolve()
        assert "site-packages" in str(package_root) or "dist-packages" in str(
            package_root
        ), f"expected installed wheel import, got {{package_root}}"
        src = vc_frame_config_source()
        assert (src / "config.kdl").is_file(), src
        # Channel-1 must resolve package data inside the venv site-packages
        if not {prefer_repo!r}:
            assert "site-packages" in str(src.resolve()) or "dist-packages" in str(
                src.resolve()
            ), f"expected packaged source, got {{src}}"
            materialize_vc_frame_config(
                src,
                runtime_payload / "generated" / "vc-frame",
                pane_shell=resolve_pane_shell({path_env!r}),
                clipboard_command=resolve_clipboard_command({path_env!r}),
            )
        current = tools / "vibecrafted-current"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.symlink_to(runtime_root)
        plan = stage_vc_frame_config(
            home=home,
            tools_home=tools,
            version={version!r},
            prefer_repo={prefer_repo!r},
            path_env={path_env!r},
        )
        view = vc_frame_user_config_dir(home)
        cfg = (view / "config.kdl").resolve()
        assert cfg.is_file(), cfg
        text = cfg.read_text(encoding="utf-8")
        assert "theme" in text
        layouts = (view / "layouts").resolve()
        research = (layouts / "research.kdl").read_text(encoding="utf-8")
        workflow = (layouts / "workflow.kdl").read_text(encoding="utf-8")
        all_kdl = "\\n".join(
            [text]
            + [
                path.read_text(encoding="utf-8")
                for path in sorted(layouts.glob("*.kdl"))
            ]
        )
        out = {{
            "channel": plan.channel,
            "pane_shell": plan.pane_shell,
            "source": str(src.resolve()),
            "research_zsh": research.count('command="zsh"'),
            "workflow_zsh": workflow.count('command="zsh"'),
            "research_shell": research.count(f'command="{{plan.pane_shell}}"'),
            "workflow_shell": workflow.count(f'command="{{plan.pane_shell}}"'),
            "has_layouts": (view / "layouts").exists(),
            "has_themes": (view / "themes").exists(),
            "hard_zsh_references": sum(
                all_kdl.count(token)
                for token in (
                    'command="zsh"',
                    'default_shell "zsh"',
                    "exec zsh -l",
                    "exec /bin/zsh -l",
                )
            ),
            "hard_pbcopy_references": (
                all_kdl.count('copy_command "pbcopy"')
                + all_kdl.count("pbcopy <")
            ),
            "runtime_pointer_preserved": current.resolve() == runtime_root.resolve(),
            "runtime_makefile_preserved": (current / "Makefile").is_file(),
            "runtime_core_preserved": (current / "vibecrafted-core").is_dir(),
            "runtime_launcher_preserved": (
                current / "vibecrafted-core" / "vibecrafted_core"
                / "runtime" / "scripts" / "codex_spawn.sh"
            ).is_file(),
        }}
        print(json.dumps(out))
        """
    )
    isolated_env = {**os.environ, "PATH": path_env}
    isolated_env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [str(venv_python), "-I", "-c", script],
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=isolated_env,
    )
    if proc.returncode != 0:
        pytest.fail(
            "venv stage failed:\n" + (proc.stdout or "") + "\n" + (proc.stderr or "")
        )
    return proc.stdout.strip().splitlines()[-1]


@pytest.mark.e2e_delivery
def test_wheel_members_include_vc_frame(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path / "dist")
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    assert "vibecrafted_core/config/vc-frame/config.kdl" in names
    assert any("auto-theme.sh" in n for n in names)
    assert any("operator.kdl" in n for n in names)


@pytest.mark.e2e_delivery
def test_channel1_wheel_venv_stage_zsh_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Channel-1 / zsh present: wheel→venv→pip→stage retains command=\"zsh\"."""
    monkeypatch.setenv("PYTHONPATH", str(CORE))
    wheel = _build_wheel(tmp_path / "dist")
    py = _install_wheel_venv(wheel, tmp_path / "venv")
    home = tmp_path / "home"
    home.mkdir()
    import json

    raw = _run_stage_in_venv(
        venv_python=py,
        home=home,
        version="e2e-whl-zsh",
        path_env=_path_with_zsh(),
        prefer_repo=False,
    )
    data = json.loads(raw)
    assert data["channel"] == "store-current"
    assert data["pane_shell"] == "zsh"
    assert data["research_zsh"] > 0
    assert data["workflow_zsh"] > 0
    assert data["has_layouts"] and data["has_themes"]
    assert data["runtime_pointer_preserved"]
    assert data["runtime_makefile_preserved"]
    assert data["runtime_core_preserved"]
    assert data["runtime_launcher_preserved"]
    assert "site-packages" in data["source"] or "dist-packages" in data["source"]


@pytest.mark.e2e_delivery
def test_channel1_wheel_venv_stage_zsh_absent(tmp_path: Path) -> None:
    """Channel-1 / zsh absent: staged research/workflow have 0×zsh, host shell used."""
    wheel = _build_wheel(tmp_path / "dist")
    py = _install_wheel_venv(wheel, tmp_path / "venv")
    home = tmp_path / "home"
    home.mkdir()
    import json

    path_env = _path_with_only_bash(tmp_path)
    raw = _run_stage_in_venv(
        venv_python=py,
        home=home,
        version="e2e-whl-bash",
        path_env=path_env,
        prefer_repo=False,
    )
    data = json.loads(raw)
    assert data["channel"] == "store-current"
    assert data["pane_shell"] != "zsh"
    assert data["research_zsh"] == 0
    assert data["workflow_zsh"] == 0
    assert data["hard_zsh_references"] == 0
    assert data["hard_pbcopy_references"] == 0
    assert data["research_shell"] > 0
    assert data["workflow_shell"] > 0


@pytest.mark.e2e_delivery
def test_channel_dev_checkout(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    plan = stage_vc_frame_config(
        home=home, tools_home=tools, version="e2e-dev", prefer_repo=True
    )
    assert plan.channel == "dev-checkout"
    resolved = (plan.view_root / "config.kdl").resolve()
    assert "config/vc-frame" in str(resolved)
    # canonical checkout layouts keep zsh
    research = (resolved.parent / "layouts" / "research.kdl").read_text(
        encoding="utf-8"
    )
    assert 'command="zsh"' in research


@pytest.mark.e2e_delivery
def test_upgrade_flip_atomicity(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = tools / "vibecrafted-full"
    runtime_payload = runtime / "vibecrafted-core" / "vibecrafted_core" / "runtime"
    (runtime / "vibecrafted-core").mkdir(parents=True)
    (runtime_payload / "scripts").mkdir(parents=True)
    (runtime / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (runtime_payload / "scripts" / "codex_spawn.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    materialize_vc_frame_config(
        vc_frame_config_source(),
        runtime_payload / "generated" / "vc-frame",
        pane_shell=resolve_pane_shell(),
        clipboard_command=resolve_clipboard_command(),
    )
    current = tools / "vibecrafted-current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(runtime)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    stage_vc_frame_config(
        home=home, tools_home=tools, version="e2e-A", prefer_repo=False
    )
    view = home / ".config" / "vibecrafted" / "vc-frame" / "config.kdl"
    path_before = str(view)
    stage_vc_frame_config(
        home=home, tools_home=tools, version="e2e-B", prefer_repo=False, force=True
    )
    assert str(view) == path_before
    assert current.resolve() == runtime.resolve()
    assert (current / "Makefile").is_file()
    assert (current / "vibecrafted-core").is_dir()
    assert (
        current
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "scripts"
        / "codex_spawn.sh"
    ).is_file()
    assert not list(tools.glob(".vibecrafted-current.tmp.*"))
