from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

_HELPER_DIR = str(Path(__file__).resolve().parents[2] / "tests")
if _HELPER_DIR not in sys.path:
    sys.path.append(_HELPER_DIR)

_home_isolation = importlib.import_module("_home_isolation")


@pytest.fixture(autouse=True)
def _isolate_vibecrafted_runtime_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Strip ambient ``VIBECRAFTED_*`` vars leaked by a live dispatched runtime.

    The suite must behave identically whether it is launched from a bare shell
    or from inside a Vibecrafted-supervised run. A live runtime exports run and
    identity context (``VIBECRAFTED_AGENT``, ``VIBECRAFTED_REPORT_PATH``,
    session/run ids, ...). Those leak into the test process and override
    in-process inference — turning green tests red only when an agent runs the
    suite from within the product's own runtime (the failure that hid two
    supervisor tests). Clearing them here makes the local environment match a
    clean CI checkout, so a test green in CI cannot be reddened by ambient
    state. Tests that need a specific value set it explicitly via monkeypatch
    after this fixture runs.

    After the strip, pin ``VIBECRAFTED_HOME`` to a pytest tmp dir. Leaving it
    unset falls through to the operator ``~/.vibecrafted`` and tests write a
    real workspace (HAK-31 / C10).
    """
    isolated = (
        tmp_path_factory.mktemp("vc-home") / _home_isolation.ISOLATED_HOME_DIRNAME
    )
    return _home_isolation.isolate_vibecrafted_test_env(
        monkeypatch,
        isolated,
        strip_prefixes=("VIBECRAFTED_", "VC_FRAME", "ZELLIJ"),
        restore={"VIBECRAFTED_LIVE_VIEWER": "0"},
    )
