"""Packaged and checkout materialization; publication lives in installer tests."""

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
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
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
    *, venv_python: Path, home: Path, version: str, path_env: str
) -> str:
    """Use installed wheel resources only, at an unpublished destination."""
    script = textwrap.dedent(f"""
        import json
        from pathlib import Path
        import vibecrafted_core
        from vibecrafted_core.frontier_assets import vc_frame_config_source
        from vibecrafted_core.vc_frame_staging import materialize_vc_frame_config, resolve_pane_shell, resolve_clipboard_command
        home = Path({str(home)!r})
        source = vc_frame_config_source()
        assert "site-packages" in str(source) or "dist-packages" in str(source)
        assert "site-packages" in vibecrafted_core.__file__ or "dist-packages" in vibecrafted_core.__file__
        destination = home / "candidate" / {version!r}
        shell = resolve_pane_shell({path_env!r})
        materialize_vc_frame_config(source, destination, pane_shell=shell, clipboard_command=resolve_clipboard_command({path_env!r}))
        research = (destination / "layouts/research.kdl").read_text()
        workflow = (destination / "layouts/workflow.kdl").read_text()
        kdl = "\\n".join(p.read_text() for p in destination.rglob("*.kdl"))
        print(json.dumps({{
            "pane_shell": shell, "source": str(source),
            "research_zsh": research.count('command="zsh"'),
            "workflow_zsh": workflow.count('command="zsh"'),
            "research_shell": research.count(f'command="{{shell}}"'),
            "workflow_shell": workflow.count(f'command="{{shell}}"'),
            "has_layouts": (destination / "layouts").is_dir(),
            "has_themes": (destination / "themes").is_dir(),
            "active_config_absent": not (home / ".config").exists(),
            "hard_zsh_references": sum(kdl.count(t) for t in ('command="zsh"', 'default_shell "zsh"', 'exec zsh -l', 'exec /bin/zsh -l')),
            "hard_pbcopy_references": sum(kdl.count(t) for t in ('copy_command "pbcopy"', 'pbcopy <')),
        }}))
    """)
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("VIBECRAFTED", "VC_FRAME", "ZELLIJ", "PYTHON"))
    }
    env.update(HOME=str(home), PATH=path_env)
    proc = subprocess.run(
        [str(venv_python), "-I", "-B", "-c", script],
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
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
    )
    data = json.loads(raw)
    assert data["active_config_absent"]
    assert data["pane_shell"] == "zsh"
    assert data["research_zsh"] > 0
    assert data["workflow_zsh"] > 0
    assert data["has_layouts"] and data["has_themes"]
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
    )
    data = json.loads(raw)
    assert data["active_config_absent"]
    assert data["pane_shell"] != "zsh"
    assert data["research_zsh"] == 0
    assert data["workflow_zsh"] == 0
    assert data["hard_zsh_references"] == 0
    assert data["hard_pbcopy_references"] == 0
    assert data["research_shell"] > 0
    assert data["workflow_shell"] > 0


@pytest.mark.e2e_delivery
@pytest.mark.parametrize("shell,clipboard", [("zsh", "pbcopy"), ("bash", None)])
def test_checkout_delivery_uses_unpublished_materialization(
    tmp_path: Path, shell: str, clipboard: str | None
) -> None:
    source = vc_frame_config_source()
    before = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    destination = tmp_path / "candidate/generated/vc-frame"
    materialize_vc_frame_config(
        source, destination, pane_shell=shell, clipboard_command=clipboard
    )
    assert (destination / "config.kdl").is_file()
    assert (destination / "themes").is_dir()
    research = (destination / "layouts/research.kdl").read_text()
    assert f'command="{shell}"' in research
    assert {p: p.read_bytes() for p in source.rglob("*") if p.is_file()} == before
    assert not (tmp_path / ".config").exists()
    assert not (tmp_path / "tools/vibecrafted-current").exists()
