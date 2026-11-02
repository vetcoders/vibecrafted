"""One capability decision and one mount attempt at each DMG boundary."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest
from vibecrafted_core import product_contract as contract

from tests.tui.test_install_macos_cold import _cold_fixture, _python_script, _run_cold

ROOT = Path(__file__).resolve().parents[2]
HELP = "--readOnly --nobrowse --mountPoint"


def _tools(tmp_path, *, modern, refusal):
    tools = tmp_path / "tools"
    for name in ("diskutil", "hdiutil"):
        _python_script(
            tools / name,
            f"""import json,os,shutil,sys
from pathlib import Path
name={name!r}; modern={modern!r}; refusal={refusal!r}
args=sys.argv[1:]
with open(os.environ['EVENTS'],'a') as f:f.write(json.dumps([name,*args])+'\\n')
if args==['image','attach','--help']:
 print({HELP!r} if modern else 'did not recognize verb image')
 raise SystemExit(0 if modern else 1)
if 'attach' in args:
 if refusal:
  print('fixture mount refused',file=sys.stderr);raise SystemExit(41)
 option='--mountPoint' if name=='diskutil' else '-mountpoint'
 mount=Path(args[args.index(option)+1])
 if os.environ.get('DMG_APP'):
  shutil.copytree(os.environ['DMG_APP'],mount/'Vibecrafted.app',symlinks=True)
""",
        )


def _calls(env):
    return [
        json.loads(line)
        for line in Path(env["EVENTS"]).read_text().splitlines()
        if line.startswith("[")
    ]


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("refusal", [False, True])
def test_bootstrap_selects_before_mount_and_preserves_refusal(
    tmp_path, modern, refusal
):
    install, _, env = _cold_fixture(tmp_path)
    _tools(tmp_path, modern=modern, refusal=refusal)
    result = _run_cold(install, env)
    assert result.returncode == (1 if refusal else 0), result.stdout + result.stderr
    calls = _calls(env)
    mounts = [row for row in calls if "attach" in row and "--help" not in row]
    assert len(mounts) == 1
    assert mounts[0][0] == ("diskutil" if modern else "hdiutil")
    assert ("--readOnly" if modern else "-readonly") in mounts[0]
    assert ("--nobrowse" if modern else "-nobrowse") in mounts[0]
    assert ("--mountPoint" if modern else "-mountpoint") in mounts[0]
    if refusal:
        assert "Could not mount the downloaded DMG" in result.stderr
        assert (
            "fixture mount refused"
            in (Path(env["HOME"]) / ".vibecrafted/install.log").read_text()
        )
        assert not any(row[0] == "hdiutil" and row[1] == "detach" for row in calls)
    else:
        assert any(row[0] == "hdiutil" and row[1] == "detach" for row in calls)
        assert "codesign" in Path(env["EVENTS"]).read_text()


@pytest.mark.parametrize("modern", [False, True, None])
@pytest.mark.parametrize("outcome", ["success", "refusal", "wrong-image"])
def test_verifier_keeps_exact_image_proof_and_cleanup(
    tmp_path, monkeypatch, modern, outcome
):
    dmg = tmp_path / "signed.dmg"
    dmg.write_bytes(b"fixture")
    calls = []
    mount = None
    monkeypatch.setattr(
        contract.shutil,
        "which",
        lambda name: (
            None if name == "diskutil" and modern is None else "/fixture/" + name
        ),
    )

    def run(args, **kwargs):
        nonlocal mount
        calls.append(args)
        if args[1:] == ["image", "attach", "--help"]:
            return subprocess.CompletedProcess(args, 0 if modern else 1, HELP, "")
        if "attach" in args:
            if outcome == "refusal":
                return subprocess.CompletedProcess(
                    args, 41, "", "fixture mount refused"
                )
            flag = "--mountPoint" if args[0].endswith("diskutil") else "-mountpoint"
            mount = Path(args[args.index(flag) + 1])
            (mount / "Vibecrafted.app").mkdir()
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[1:] == ["info", "-plist"]:
            image = dmg if outcome != "wrong-image" else tmp_path / "foreign.dmg"
            data = {
                "images": [
                    {
                        "image-path": str(image),
                        "system-entities": [{"mount-point": str(mount)}],
                    }
                ]
            }
            return subprocess.CompletedProcess(args, 0, plistlib.dumps(data), b"")
        assert args[1] == "detach"
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(contract.subprocess, "run", run)
    if outcome == "success":
        with contract._mounted_release_dmg(dmg) as app:
            assert app.name == "Vibecrafted.app"
    else:
        with (
            pytest.raises(
                contract.ProductContractError,
                match="could not be mounted|not the exact signed DMG",
            ),
            contract._mounted_release_dmg(dmg),
        ):
            pytest.fail("a refused or foreign image must not be yielded")
    mounts = [row for row in calls if "attach" in row and "--help" not in row]
    assert len(mounts) == 1
    assert mounts[0][0] == ("/fixture/diskutil" if modern else "/fixture/hdiutil")
    assert any(row[1:] == ["info", "-plist"] for row in calls) == (outcome != "refusal")
    assert any(row[1] == "detach" for row in calls) == (outcome != "refusal")


@pytest.mark.parametrize(
    "error", [OSError("probe unavailable"), subprocess.TimeoutExpired("diskutil", 5)]
)
def test_verifier_uncertain_capability_never_starts_a_mount(
    tmp_path, monkeypatch, error
):
    calls = []
    monkeypatch.setattr(contract.shutil, "which", lambda name: "/fixture/" + name)

    def run(args, **kwargs):
        calls.append(args)
        raise error

    monkeypatch.setattr(contract.subprocess, "run", run)
    with (
        pytest.raises(
            contract.ProductContractError, match="capability could not be checked"
        ),
        contract._mounted_release_dmg(tmp_path / "fixture.dmg"),
    ):
        pytest.fail("uncertain capability must not yield an app")
    assert calls == [["/fixture/diskutil", "image", "attach", "--help"]]


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("refusal", [False, True])
def test_artifact_gate_selects_before_mount_and_cleans_up(tmp_path, modern, refusal):
    (tmp_path / "tools").mkdir()
    _tools(tmp_path, modern=modern, refusal=refusal)
    image = tmp_path / "fixture.dmg"
    image.touch()
    env = os.environ.copy()
    env.update(
        PATH=f"{tmp_path / 'tools'}:{env['PATH']}", EVENTS=str(tmp_path / "events")
    )
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "scripts/payload-hygiene-artifact.sh"), str(image)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == (41 if refusal else 0), result.stdout + result.stderr
    calls = _calls(env)
    mounts = [row for row in calls if "attach" in row and "--help" not in row]
    assert len(mounts) == 1
    assert mounts[0][0] == ("diskutil" if modern else "hdiutil")
    assert any(row[1] == "detach" for row in calls)
    if refusal:
        assert "fixture mount refused" in result.stderr
