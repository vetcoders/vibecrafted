from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping

import pytest

# Frame/session targeting env leaks from a live operator shell (vc-frame is a
# zellij fork, so both spellings appear). With any of these set, launcher paths
# see "inside a live frame" and switch sessions instead of launching layouts —
# tests then fail only on operator machines while CI stays green. Tests that
# simulate an in-frame caller still work: they set their own values after
# copying os.environ.
_AMBIENT_FRAME_ENV = (
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "VC_FRAME_CONFIG_DIR",
    "VIBECRAFTED_PREPARED_VC_FRAME_SESSION",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_WORKER_SESSION",
)

# Root env exported by every installed launcher. With any of these set, a test
# that builds its own HOME still resolves the operator's live runtime (tools,
# generation, interpreter) and either touches it or silently skips the step it
# meant to prove. Tests that need a root set it explicitly.
_AMBIENT_ROOT_ENV = (
    "VIBECRAFTED_HOME",
    "VIBECRAFTED_RUNTIME_HOME",
    "VIBECRAFTED_TOOLS_HOME",
    "VIBECRAFTED_RUNTIME_BIN",
    "VIBECRAFTED_ROOT",
    "VIBECRAFTED_RUNTIME_ROOT",
    "VIBECRAFTED_PYTHON",
    "VIBECRAFTED_SOURCE",
)

_LOGIN_SHELL_ROOT_ENV = (
    "HOME",
    "PATH",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
) + _AMBIENT_ROOT_ENV


@pytest.fixture(autouse=True)
def _disable_live_perception_side_effects(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """TUI/launcher tests must not reach live operator runtime surfaces."""

    home = tmp_path_factory.mktemp("tui-home").resolve()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VC_FRAME_SOCKET_DIR", str(home / "frame-sockets"))
    monkeypatch.setenv("VIBECRAFTED_PERCEPTION_WATCH", "0")
    monkeypatch.setenv("VIBECRAFTED_TEST_MODE", "1")
    for name in _AMBIENT_FRAME_ENV + _AMBIENT_ROOT_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def hermetic_login_shell() -> Callable[[Mapping[str, str], str], str]:
    """Re-assert synthetic roots after login startup has read host profiles."""

    def command(environment: Mapping[str, str], script: str) -> str:
        root_contract = []
        for name in _LOGIN_SHELL_ROOT_ENV:
            if name in environment:
                root_contract.append(
                    f"export {name}={shlex.quote(str(environment[name]))}"
                )
            else:
                root_contract.append(f"unset {name}")
        return "\n".join((*root_contract, script))

    return command
