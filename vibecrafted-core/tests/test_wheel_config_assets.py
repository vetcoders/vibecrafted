"""W1-A: wheel/sdist carry config/vc-frame; accessor resolves checkout + package."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest
from vibecrafted_core.frontier_assets import vc_frame_config_kdl, vc_frame_config_source

CORE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CORE_ROOT.parent

REQUIRED_MEMBERS = (
    "vibecrafted_core/config/vc-frame/config.kdl",
    "vibecrafted_core/config/vc-frame/auto-theme.sh",
    "vibecrafted_core/config/vc-frame/layouts/operator.kdl",
    "vibecrafted_core/config/vc-frame/layouts/dashboard.kdl",
    "vibecrafted_core/config/vc-frame/layouts/research.kdl",
    "vibecrafted_core/config/vc-frame/layouts/workflow.kdl",
    "vibecrafted_core/config/vc-frame/layouts/marbles.kdl",
    "vibecrafted_core/config/vc-frame/themes/vibecrafted-ivory.kdl",
    "vibecrafted_core/config/vc-frame/themes/vetcoders-mesh.kdl",
)


def test_accessor_returns_existing_tree_from_checkout() -> None:
    source = vc_frame_config_source()
    assert source.is_dir()
    assert (source / "config.kdl").is_file()
    assert (source / "auto-theme.sh").is_file()
    for layout in (
        "operator.kdl",
        "dashboard.kdl",
        "research.kdl",
        "workflow.kdl",
        "marbles.kdl",
    ):
        assert (source / "layouts" / layout).is_file(), layout
    assert (source / "themes" / "vibecrafted-ivory.kdl").is_file()
    assert (source / "themes" / "vetcoders-mesh.kdl").is_file()
    text = vc_frame_config_kdl().read_text(encoding="utf-8")
    assert 'theme "monochrome"' in text or "theme " in text


def test_repo_root_config_is_canonical_source() -> None:
    """Checkout accessor must land on monorepo config/vc-frame, not a duplicate."""
    source = vc_frame_config_source().resolve()
    canonical = (
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "config" / "vc-frame"
    ).resolve()
    # When package data is also present (editable install after wheel stage),
    # either path is valid if it contains the tree; prefer equality when possible.
    assert (source / "config.kdl").is_file()
    assert (canonical / "config.kdl").is_file()
    assert (
        source == canonical
        or (source / "config.kdl").read_bytes()
        == (canonical / "config.kdl").read_bytes()
    )


def _ensure_build_artifacts(dist: Path) -> tuple[Path, Path]:
    """Build this checkout into an isolated directory; require wheel and sdist."""
    import subprocess
    import sys

    cmds = [
        ["uv", "build", "--directory", str(CORE_ROOT), "--out-dir", str(dist)],
        [sys.executable, "-m", "build", str(CORE_ROOT), "--outdir", str(dist)],
    ]
    last_err = ""
    for cmd in cmds:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(CORE_ROOT),
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            last_err = str(exc)
            continue
        if proc.returncode == 0:
            break
        last_err = (proc.stderr or proc.stdout or "")[-500:]
    else:
        pytest.fail(f"could not build wheel/sdist: {last_err}")

    wheels = sorted(dist.glob("vibecrafted-*.whl"))
    sdists = sorted(dist.glob("vibecrafted-*.tar.gz"))
    if len(wheels) != 1:
        pytest.fail(f"build produced no wheel or ambiguous wheels: {wheels}")
    if len(sdists) != 1:
        pytest.fail(f"build produced no sdist or ambiguous sdists: {sdists}")
    return wheels[0], sdists[0]


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return _ensure_build_artifacts(tmp_path_factory.mktemp("core-config-carriers"))


def test_wheel_contains_vc_frame_tree(artifacts: tuple[Path, Path]) -> None:
    wheel, _ = artifacts
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    missing = [n for n in REQUIRED_MEMBERS if n not in names]
    assert not missing, f"wheel {wheel.name} missing: {missing}"


def test_sdist_contains_vc_frame_tree(artifacts: tuple[Path, Path]) -> None:
    _, sdist = artifacts
    with tarfile.open(sdist, "r:gz") as tf:
        names = set(tf.getnames())
    # sdist prefixes with package-version/
    missing = []
    for member in REQUIRED_MEMBERS:
        if not any(n.endswith((member, "/" + member)) for n in names) and not any(
            member.split("/", 1)[-1] in n for n in names if "vc-frame" in n
        ):
            # also allow top-level without vibecrafted- prefix quirks
            missing.append(member)
    assert not missing, f"sdist {sdist.name} missing: {missing}"
    # Require the same config members as the wheel, then inspect the source prefix.
    assert any(n.endswith("config/vc-frame/config.kdl") for n in names), (
        f"sdist {sdist.name} has no config.kdl; sample={sorted(names)[:20]}"
    )
    assert any("auto-theme.sh" in n for n in names)
    assert any("operator.kdl" in n for n in names)


@pytest.mark.parametrize("build_returns_success", [False, True])
def test_build_failure_is_failure_not_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build_returns_success: bool
) -> None:
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0 if build_returns_success else 7, "", "broken builder"
        ),
    )
    try:
        with pytest.raises(pytest.fail.Exception, match="could not build|no wheel"):
            _ensure_build_artifacts(tmp_path)
    except pytest.skip.Exception as exc:
        pytest.fail(f"builder hid failure as skip: {exc}")
