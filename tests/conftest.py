import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
repo_root_str = str(REPO_ROOT)

if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)

_HELPER_DIR = str(Path(__file__).resolve().parent)
if _HELPER_DIR not in sys.path:
    sys.path.append(_HELPER_DIR)

_home_isolation = importlib.import_module("_home_isolation")

os.environ.setdefault("VIBECRAFTED_MARBLES_PROBE_NOTIFY", "0")
os.environ.setdefault("VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME", "1")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e_delivery: full config delivery channel matrix (wheel/dev × shell)",
    )


@pytest.fixture(autouse=True)
def _isolate_vibecrafted_runtime_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    """Keep root tests independent from the live Vibecrafted runtime."""
    isolated = (
        tmp_path_factory.mktemp("vc-home") / _home_isolation.ISOLATED_HOME_DIRNAME
    )
    return _home_isolation.isolate_vibecrafted_test_env(
        monkeypatch,
        isolated,
        restore={
            "VIBECRAFTED_MARBLES_PROBE_NOTIFY": "0",
            "VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME": "1",
        },
    )
