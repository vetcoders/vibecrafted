"""scripts/lib/runtime-roots.sh is the one shell definition of Vibecrafted's roots.

install.sh is the curl|bash bootstrap and cannot source the library before a
checkout exists, so it carries a verbatim copy between two markers. This test
pins that copy to the library byte for byte and proves every in-repo shell
entry point resolves the same roots from the same env.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "lib" / "runtime-roots.sh"
INSTALL_SH = REPO_ROOT / "install.sh"
MARK_IN = "# >>> scripts/lib/runtime-roots.sh"
MARK_OUT = "# <<< scripts/lib/runtime-roots.sh"
FUNCTIONS = (
    "default_vibecrafted_home",
    "default_vibecrafted_runtime_home",
    "default_vibecrafted_tools_home",
    "default_vibecrafted_launcher_bin",
    "canonical_vibecrafted_home",
    "canonical_vibecrafted_runtime_home",
    "canonical_vibecrafted_launcher_bin",
)


def _library_body() -> str:
    text = LIB.read_text(encoding="utf-8")
    return text[text.index("is_interactive_session() {") :]


def test_install_sh_carries_the_library_verbatim() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index(MARK_IN)
    start = text.index("\n", start) + 1
    end = text.index(MARK_OUT)
    assert text[start:end] == _library_body()


def test_install_sh_defines_no_second_root_function() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    for name in FUNCTIONS + (
        "enforce_runtime_root_contract",
        "pause_runtime_contract_failure",
    ):
        assert text.count(f"\n{name}() {{") == 1, name


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {},
            {
                "default_vibecrafted_home": "{home}/.vibecrafted",
                "default_vibecrafted_runtime_home": "{home}/.local/share/vibecrafted",
                "default_vibecrafted_tools_home": "{home}/.local/share/vibecrafted/tools",
                "default_vibecrafted_launcher_bin": "{home}/.local/bin",
            },
        ),
        (
            {"VIBECRAFTED_HOME": "/srv/vc", "XDG_DATA_HOME": "/srv/data"},
            {
                "default_vibecrafted_home": "/srv/vc",
                "default_vibecrafted_runtime_home": "/srv/data/vibecrafted",
                "default_vibecrafted_tools_home": "/srv/data/vibecrafted/tools",
            },
        ),
        (
            # VIBECRAFTED_ROOT is the release generation — never a home prefix.
            {"VIBECRAFTED_ROOT": "/opt/gen", "VIBECRAFTED_HOME": ""},
            {"default_vibecrafted_home": "{home}/.vibecrafted"},
        ),
        (
            {
                "VIBECRAFTED_RUNTIME_HOME": "/rt",
                "VIBECRAFTED_TOOLS_HOME": "/tools",
                "VIBECRAFTED_LAUNCHER_BIN": "/lb",
            },
            {
                "default_vibecrafted_runtime_home": "/rt",
                "default_vibecrafted_tools_home": "/tools",
                "default_vibecrafted_launcher_bin": "/lb",
            },
        ),
    ],
)
def test_every_shell_entry_resolves_the_same_roots(
    tmp_path: Path, env: dict[str, str], expected: dict[str, str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    base = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    base.update(env)
    sources = {
        "lib": f'source "{LIB}"',
        "install.sh": f'eval "$(sed -n \'/^{MARK_IN.replace("/", "\\/")}/,/^{MARK_OUT.replace("/", "\\/")}/p\' "{INSTALL_SH}")"',
    }
    results: dict[str, dict[str, str]] = {}
    for label, source in sources.items():
        script = (
            source
            + "\n"
            + "\n".join(f'printf "%s=%s\\n" {fn} "$({fn})"' for fn in expected)
        )
        proc = subprocess.run(
            ["bash", "-c", script], env=base, capture_output=True, text=True, check=True
        )
        results[label] = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    want = {k: v.format(home=home) for k, v in expected.items()}
    assert results["lib"] == want
    assert results["install.sh"] == want


def test_contract_fails_closed_on_drift(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "VIBECRAFTED_HOME": "/elsewhere",
        "VIBECRAFTED_INSTALL_NONINTERACTIVE": "1",
    }
    proc = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; enforce_runtime_root_contract'],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "store root drift" in proc.stderr
    assert "doctor --fix-legacy-bootstrap" in proc.stderr
