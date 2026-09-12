import plistlib
import stat
from types import SimpleNamespace

import pytest

from scripts import vetcoders_install as installer


@pytest.fixture
def service(tmp_path, monkeypatch):
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    runtime = home / "runtime"
    runtime.mkdir()
    program = runtime / "loctree-mcp"
    program.write_text("old")
    external = home / "external/loctree-mcp"
    external.parent.mkdir()
    external.write_text("external")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.shutil, "which", lambda name: str(external))
    monkeypatch.setattr(
        installer, "_checkpoint_runtime_install_receipt", lambda *args: None
    )
    return agents, runtime, program, external


@pytest.mark.parametrize(
    "label",
    ["com.loctree.loctree.mcp", "io.vetcoders.loctree.mcp", "io.vetcoders.aicx.mcp"],
)
@pytest.mark.parametrize("program_form", [False, True])
def test_foundation_repoint_preserves_private_mode_arguments_and_environment(
    service, monkeypatch, label, program_form
):
    agents, runtime, program, external = service
    payload = {"Label": label, "EnvironmentVariables": {"FIXTURE": "preserve"}}
    if program_form:
        payload.update(
            Program=str(program),
            ProgramArguments=["custom-argv-zero", "--host", "127.0.0.1"],
        )
    else:
        payload["ProgramArguments"] = [str(program), "--host", "127.0.0.1"]
    path = agents / (label + ".plist")
    path.write_bytes(plistlib.dumps(payload))
    path.chmod(0o600)
    calls = []
    monkeypatch.setattr(installer, "_launchctl_quiet", lambda *args: calls.append(args))
    installer._repoint_foundation_service_dependents(
        [], launcher_home=agents.parent / "bin", runtime_home=runtime
    )
    actual = plistlib.loads(path.read_bytes())
    expected = dict(payload)
    if program_form:
        expected["Program"] = str(external)
    else:
        expected["ProgramArguments"] = [str(external), "--host", "127.0.0.1"]
    assert actual == expected
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert all(call[0] == "print" for call in calls)


def test_failed_reload_retries_exact_receipted_plist_but_preserves_user_drift(
    service, monkeypatch
):
    agents, runtime, program, _external = service
    path = agents / "com.loctree.loctree.mcp.plist"
    path.write_bytes(
        plistlib.dumps(
            {"Label": "com.loctree.loctree.mcp", "ProgramArguments": [str(program)]}
        )
    )
    receipt = {}
    monkeypatch.setattr(
        installer,
        "_launchctl_quiet",
        lambda *args: SimpleNamespace(returncode=int(args[0] == "bootstrap")),
    )
    actions = installer._repoint_foundation_service_dependents(
        [], launcher_home=agents.parent / "bin", runtime_home=runtime, receipt=receipt
    )
    assert "reload failed at bootstrap" in actions[0]
    assert receipt["foundation_service_pending"]
    published = path.read_bytes()
    changed = plistlib.loads(published)
    changed["EnvironmentVariables"] = {"FOUNDER_EDIT": "preserve"}
    path.write_bytes(plistlib.dumps(changed))
    changed_bytes = path.read_bytes()
    calls = []
    monkeypatch.setattr(installer, "_launchctl_quiet", lambda *args: calls.append(args))
    actions = installer._repoint_foundation_service_dependents(
        [], launcher_home=agents.parent / "bin", runtime_home=runtime, receipt=receipt
    )
    assert "configuration changed" in actions[0]
    assert not calls
    assert path.read_bytes() == changed_bytes
    path.write_bytes(published)

    def recover(*args):
        calls.append(args)
        return SimpleNamespace(returncode=int(len(calls) == 1))

    monkeypatch.setattr(installer, "_launchctl_quiet", recover)
    actions = installer._repoint_foundation_service_dependents(
        [], launcher_home=agents.parent / "bin", runtime_home=runtime, receipt=receipt
    )
    assert any(call[0] == "bootstrap" for call in calls)
    assert "reloaded; service health unverified" in actions[0]
    assert not receipt["foundation_service_pending"]
