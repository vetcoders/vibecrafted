from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from vibecrafted_core import product_contract as contract


@pytest.fixture(autouse=True)
def clear_probe_cache() -> None:
    contract._probe_login_shell_path.cache_clear()


def test_user_shell_path_reads_login_and_interactive_rc_and_caches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real shell execution: startup noise cannot masquerade as PATH."""
    if not Path("/bin/zsh").exists():
        pytest.skip("zsh unavailable")
    (tmp_path / ".zprofile").write_text('export PATH="/login/bin:$PATH"\n')
    rc = tmp_path / ".zshrc"
    rc.write_text('printf "startup noise\\n"\nexport PATH="$MY_TOOLS:$PATH"\n')
    host = {
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path),
        "SHELL": "/bin/zsh",
        "PATH": "/usr/bin:/bin",
        "MY_TOOLS": "/interactive/bin",
    }
    real_popen = contract.subprocess.Popen
    calls = []

    def counted_popen(*args: Any, **kwargs: Any) -> Any:
        calls.append(args[0])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(contract.subprocess, "Popen", counted_popen)
    path = contract.resolve_login_shell_path(host)
    assert path.startswith("/interactive/bin:/login/bin:")
    assert "startup noise" not in path
    assert contract.resolve_login_shell_path(host) == path
    assert len(calls) == 1
    rc.write_text('export PATH="/changed/bin:$PATH"\n')
    assert contract.resolve_login_shell_path(host).startswith(
        "/changed/bin:/login/bin:"
    )
    assert len(calls) == 2


def test_user_shell_timeout_reaps_its_real_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not Path("/bin/zsh").exists():
        pytest.skip("zsh unavailable")
    (tmp_path / ".zshrc").write_text("/bin/sleep 30\n")
    real_popen = contract.subprocess.Popen
    probes = []

    def bounded_fixture(*args: Any, **kwargs: Any) -> Any:
        process = real_popen(*args, **kwargs)
        communicate = process.communicate
        monkeypatch.setattr(
            process,
            "communicate",
            lambda timeout=None: communicate(timeout=0.1 if timeout == 5 else timeout),
        )
        probes.append(process)
        return process

    monkeypatch.setattr(contract.subprocess, "Popen", bounded_fixture)
    host = {
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path),
        "SHELL": "/bin/zsh",
        "PATH": "/usr/bin:/bin",
    }
    assert contract.resolve_login_shell_path(host) == host["PATH"]
    assert probes[0].returncode == -contract.signal.SIGKILL


@pytest.mark.parametrize(
    "failure",
    ["timeout", "spawn", "exit", "malformed", "empty", "relative", "encoding"],
)
def test_user_shell_path_failures_fall_back_and_are_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    host = {"HOME": str(tmp_path), "SHELL": "/bin/zsh", "PATH": "/host/bin:/usr/bin"}
    calls = []
    killed = []
    communications = []

    class Probe:
        pid = 424242
        returncode = 3 if failure == "exit" else 0

        def communicate(self, timeout: int | None = None) -> tuple[bytes, None]:
            communications.append(timeout)
            if failure == "timeout" and len(communications) == 1:
                raise subprocess.TimeoutExpired("fixture-shell", timeout)
            marker = re.search(r"vc_path_[0-9a-f]+", calls[0][-1])[0].encode()
            value = {
                "empty": b"",
                "relative": b"relative:.",
                "encoding": b"/bin:\xff",
            }.get(failure, b"/user/bin")
            return (
                b"no marker"
                if failure == "malformed"
                else marker + b"\0" + value + b"\0" + marker
            ), None

    def fake_popen(command: list[str], **kwargs: Any) -> Probe:
        calls.append(command)
        assert command[:4] == ["/bin/zsh", "-i", "-l", "-c"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["start_new_session"] is True
        if failure == "spawn":
            raise OSError("fixture shell unavailable")
        return Probe()

    monkeypatch.setattr(contract.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        contract.os, "killpg", lambda pid, sig: killed.append((pid, sig))
    )
    assert contract.resolve_login_shell_path(host) == host["PATH"]
    assert contract.resolve_login_shell_path(host) == host["PATH"]
    assert len(calls) == 1
    if failure == "timeout":
        assert communications == [5, 1]
        assert killed == [(424242, contract.signal.SIGKILL)]
    else:
        assert not killed


def test_user_shell_path_cache_is_scoped_to_shell_and_user_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def missing(command: list[str], **kwargs: Any) -> None:
        calls.append(command)
        raise OSError("missing")

    monkeypatch.setattr(contract.subprocess, "Popen", missing)
    host = {"HOME": str(tmp_path), "SHELL": "/bin/zsh", "PATH": "/usr/bin"}
    contract.resolve_login_shell_path(host)
    contract.resolve_login_shell_path({**host, "SHELL": "/bin/bash"})
    contract.resolve_login_shell_path({**host, "HOME": str(tmp_path / "other")})
    (tmp_path / ".zprofile").write_text("# new startup file\n")
    contract.resolve_login_shell_path(host)
    assert len(calls) == 4


def test_user_shell_probe_recovers_user_zdotdir_without_mutating_host(
    tmp_path: Path,
) -> None:
    host = {
        "HOME": str(tmp_path),
        "ZDOTDIR": str(tmp_path / "generation/config/runtime-pin/zsh"),
        "VIBECRAFTED_RUNTIME_ROOT": str(tmp_path / "generation"),
        "VIBECRAFTED_USER_ZDOTDIR": str(tmp_path / "user-zsh"),
        "PYTHONPATH": "/poison",
    }
    clean = contract._login_shell_probe_environment(host)
    assert clean["ZDOTDIR"] == host["VIBECRAFTED_USER_ZDOTDIR"]
    assert "PYTHONPATH" not in clean
    assert host["ZDOTDIR"].endswith("runtime-pin/zsh")
    del host["VIBECRAFTED_USER_ZDOTDIR"]
    assert "ZDOTDIR" not in contract._login_shell_probe_environment(host)
    host["ZDOTDIR"] = str(tmp_path / "custom-zsh")
    assert contract._login_shell_probe_environment(host)["ZDOTDIR"] == host["ZDOTDIR"]


def test_launch_path_keeps_pins_first_and_absolute_entries_once(tmp_path: Path) -> None:
    app = tmp_path / "Vibecrafted.app"
    pin = str(app / "Contents/Resources/runtime/bin")
    assert contract._launch_child_path(
        app, f"/user/bin:{pin}:/usr/bin:/user/bin::relative"
    ) == (f"{pin}:/user/bin:/usr/bin:/bin:/usr/sbin:/sbin")
