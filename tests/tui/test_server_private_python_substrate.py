"""Server substrate runs on the generation-owned Python, never a PATH python3.

Falsifiers for the native candidate failure where the operator's own
``~/.local/bin/python3`` (a link into a foreign uv toolchain) answered the
launcher's ``origin`` probe, so its sentinel text became the server URL.
Every test executes the real installer-rendered public entry of a temporary
installed generation under a hostile PATH; nothing here reaches the operator's
runtime, and the foreign interpreter must come out byte-for-byte untouched.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "scripts" / "vibecrafted"
SENTINEL_TEXT = "external-python-preserved"
FAIL_CLOSED_EXIT = 78
DEFAULT_ORIGIN_ARGS = "origin 127.0.0.1 3024"


@dataclass(frozen=True)
class _Fingerprint:
    link_mode: int
    link_target: str
    target_mode: int
    target_bytes: bytes


@dataclass
class _Substrate:
    """A temporary installed generation plus the operator's hostile user bin."""

    home: Path
    runtime_home: Path
    generation: Path
    public_entry: Path
    sentinel_link: Path
    sentinel_target: Path
    own_witness: Path
    sentinel_witness: Path
    scratch: Path

    @property
    def packaged_python(self) -> Path:
        return self.generation / "bin/python3"

    def environment(self) -> dict[str, str]:
        """The shape a supervisor child inherits: user bin first, then the OS.

        No VIBECRAFTED_* roots are pre-set on purpose; the installed public
        entry exports its own generation roots exactly as the product does.
        """
        user = os.environ.get("USER", "tester")
        return {
            "HOME": str(self.home),
            "PATH": os.pathsep.join(
                [
                    str(self.home / ".local/bin"),
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                ]
            ),
            "TMPDIR": str(self.scratch),
            "LANG": "C",
            "TERM": "dumb",
            "SHELL": "/bin/bash",
            "USER": user,
            "LOGNAME": user,
            "VIBECRAFTED_SERVER_SUPERVISOR_CHILD": "1",
        }

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.public_entry), *args],
            check=False,
            cwd=self.scratch,
            env=self.environment(),
            capture_output=True,
            text=True,
            timeout=90,
        )

    def fingerprint(self) -> _Fingerprint:
        link_stat = self.sentinel_link.lstat()
        assert stat.S_ISLNK(link_stat.st_mode), "sentinel must stay a symlink"
        return _Fingerprint(
            link_mode=stat.S_IMODE(link_stat.st_mode),
            link_target=os.readlink(self.sentinel_link),
            target_mode=stat.S_IMODE(self.sentinel_target.stat().st_mode),
            target_bytes=self.sentinel_target.read_bytes(),
        )

    def own_invocations(self) -> list[str]:
        if not self.own_witness.is_file():
            return []
        return self.own_witness.read_text(encoding="utf-8").splitlines()

    def sentinel_invocations(self) -> list[str]:
        if not self.sentinel_witness.is_file():
            return []
        return self.sentinel_witness.read_text(encoding="utf-8").splitlines()


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _witness_script(witness: Path, *lines: str) -> str:
    return (
        "\n".join(
            [
                "#!/bin/sh",
                f"printf '%s\\n' \"$*\" >> {installer.shlex_quote(str(witness))}",
                *lines,
            ]
        )
        + "\n"
    )


@pytest.fixture
def substrate(tmp_path: Path) -> _Substrate:
    home = tmp_path / "home"
    runtime_home = home / ".local/share/vibecrafted"
    generation = runtime_home / "releases/9.9.9+gprivate"
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    own_witness = tmp_path / "own-python.witness"
    sentinel_witness = tmp_path / "sentinel-python.witness"

    # Runtime Pack shape: native server payload, its site, and its own Python.
    _write_executable(generation / "bin/vc-server", "#!/bin/sh\nexit 0\n")
    _write_executable(generation / "bin/vc-server-supervisor", "#!/bin/sh\nexit 0\n")
    (generation / "server/site").mkdir(parents=True)
    (generation / "VERSION").write_text("9.9.9+gprivate\n", encoding="utf-8")
    (generation / "runtime-manifest.json").write_text("{}\n", encoding="utf-8")
    # The deck refuses to start without the generation's own limit helper.
    ulimits = "vibecrafted-core/vibecrafted_core/runtime/scripts/lib/ulimits.sh"
    (generation / ulimits).parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / ulimits, generation / ulimits)
    _write_executable(
        generation / "bin/python3",
        _witness_script(
            own_witness,
            f'exec {installer.shlex_quote(sys.executable)} "$@"',
        ),
    )
    # The real launcher entry of the generation, as the installer stages it.
    generation_entry = generation / installer._RUNTIME_GENERATION_ENTRYPOINT
    shutil.copy2(LAUNCHER, generation_entry)
    generation_entry.chmod(0o755)

    # The real public wrapper the installer publishes into the user bin.
    public_entry = home / ".local/bin/vibecrafted"
    _write_executable(
        public_entry,
        installer._runtime_launcher_body(
            generation=generation,
            config_home=home / ".config",
            crafted_home=home / ".vibecrafted",
            runtime_home=runtime_home,
            frame_config=home / ".config/vibecrafted/vc-frame",
            executable=generation_entry,
        ),
    )

    # The operator's foreign Python: a user-bin link into an isolated uv
    # toolchain whose interpreter answers with sentinel text.
    sentinel_target = tmp_path / "uv/python/cpython-3.12-macos/bin/python3.12"
    _write_executable(
        sentinel_target,
        _witness_script(sentinel_witness, f"printf '%s\\n' {SENTINEL_TEXT}", "exit 0"),
    )
    sentinel_link = home / ".local/bin/python3"
    sentinel_link.symlink_to(sentinel_target)

    return _Substrate(
        home=home,
        runtime_home=runtime_home,
        generation=generation,
        public_entry=public_entry,
        sentinel_link=sentinel_link,
        sentinel_target=sentinel_target,
        own_witness=own_witness,
        sentinel_witness=sentinel_witness,
        scratch=scratch,
    )


def _assert_sentinel_never_ran(substrate: _Substrate, before: _Fingerprint) -> None:
    assert substrate.sentinel_invocations() == []
    assert substrate.fingerprint() == before


def test_server_private_python_status_probe_uses_packaged_interpreter(
    substrate: _Substrate,
) -> None:
    """`server status` resolves the origin on the packaged Python, not PATH."""
    before = substrate.fingerprint()

    result = substrate.run("server", "status")

    combined = result.stdout + result.stderr
    assert SENTINEL_TEXT not in combined, combined
    assert "Server: STOPPED" in result.stdout, combined
    invocations = substrate.own_invocations()
    assert any(line.endswith(DEFAULT_ORIGIN_ARGS) for line in invocations), invocations
    # Every owned invocation (verification probe included) runs isolated.
    assert invocations and all(line.startswith("-I -") for line in invocations), (
        invocations
    )
    _assert_sentinel_never_ran(substrate, before)


def test_server_private_python_pair_health_probe_uses_packaged_interpreter(
    substrate: _Substrate,
) -> None:
    """The supervisor's own probe verb runs on the packaged Python as well."""
    before = substrate.fingerprint()

    result = substrate.run("server", "supervisor-pair-health")

    combined = result.stdout + result.stderr
    assert SENTINEL_TEXT not in combined, combined
    invocations = substrate.own_invocations()
    assert any(" pair-health " in f"{line} " for line in invocations), invocations
    _assert_sentinel_never_ran(substrate, before)


@pytest.mark.parametrize("command", ["status", "supervisor-pair-health"])
@pytest.mark.parametrize("deck", ["generation", "development", "packaged-owner"])
def test_server_embedded_guardian_imports_selected_core_in_isolation(
    substrate: _Substrate, command: str, deck: str
) -> None:
    """Actual selected core must report an unready pair, never fail its import."""
    shutil.copytree(
        REPO_ROOT / "vibecrafted-core/vibecrafted_core",
        substrate.generation / "vibecrafted-core/vibecrafted_core",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )
    # An editable test environment would mask the missing -I module admission.
    _write_executable(
        substrate.packaged_python,
        _witness_script(
            substrate.own_witness,
            f'exec {installer.shlex_quote(sys._base_executable)} "$@"',
        ),
    )
    runtime_dir = substrate.home / ".vibecrafted/server"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "guardian.execution.json").write_text(
        '{"ready": false}\n', encoding="utf-8"
    )
    (runtime_dir / "status.json").write_text(
        '{"host": "127.0.0.1", "port": 3024}\n', encoding="utf-8"
    )
    hostile = substrate.scratch / "vibecrafted_core"
    hostile.mkdir()
    marker = substrate.scratch / "foreign-core-ran"
    (hostile / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "raise RuntimeError('foreign core executed')\n",
        encoding="utf-8",
    )
    environment = substrate.environment()
    _write_executable(
        substrate.home / ".local/bin/vc-server-supervisor", "#!/bin/sh\nexit 0\n"
    )
    environment.update(
        PYTHONPATH=str(substrate.scratch),
        PYTHONHOME=str(substrate.scratch / "foreign-python-home"),
        VIBECRAFTED_CORE_DIR=str(substrate.scratch),
        VIBECRAFTED_TOOLS_HOME=str(substrate.scratch),
    )
    entry = substrate.public_entry
    if deck != "generation":
        entry = (
            LAUNCHER
            if deck == "development"
            else REPO_ROOT / "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
        )
        environment.update(
            VIBECRAFTED_HOME=str(substrate.home / ".vibecrafted"),
            VIBECRAFTED_RUNTIME_HOME=str(substrate.runtime_home),
            VIBECRAFTED_PYTHON=sys._base_executable,
        )
    result = subprocess.run(
        [str(entry), "server", command],
        check=False,
        cwd=substrate.scratch,
        env=environment,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 4, result.stdout + result.stderr
    assert "server or embedded Guardian is not ready" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr
    assert not marker.exists()
    assert "RUNNING" not in result.stdout


@pytest.mark.parametrize("missing", ["__init__.py", "server_supervisor.py"])
def test_server_embedded_guardian_missing_selected_module_fails_closed(
    substrate: _Substrate, missing: str
) -> None:
    core = substrate.generation / "vibecrafted-core/vibecrafted_core"
    for name in ("dispatcher.py", "__init__.py", "server_supervisor.py"):
        (core / name).write_text("raise RuntimeError('unadmitted module')\n")
    (core / missing).unlink()
    runtime_dir = substrate.home / ".vibecrafted/server"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "guardian.execution.json").write_text("{}\n")

    result = substrate.run("server", "supervisor-pair-health")

    assert result.returncode == FAIL_CLOSED_EXIT, result.stdout + result.stderr
    assert "selected launcher core missing" in result.stderr
    assert "unadmitted module" not in result.stderr
    assert "RUNNING" not in result.stdout


@pytest.mark.parametrize("linked_directory", ["vibecrafted-core", "vibecrafted_core"])
def test_server_embedded_guardian_rejects_foreign_package_directory(
    substrate: _Substrate, linked_directory: str
) -> None:
    core = substrate.generation / "vibecrafted-core"
    foreign = substrate.scratch / "foreign-core"
    shutil.copytree(core, foreign)
    marker = substrate.scratch / "foreign-core-ran"
    package = foreign / "vibecrafted_core"
    (package / "dispatcher.py").write_text("pass\n")
    (package / "server_supervisor.py").write_text("pass\n")
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    selected = (
        core if linked_directory == "vibecrafted-core" else core / "vibecrafted_core"
    )
    target = foreign if linked_directory == "vibecrafted-core" else package
    selected.rename(selected.with_name(f"{selected.name}-original"))
    selected.symlink_to(target)
    runtime_dir = substrate.home / ".vibecrafted/server"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "guardian.execution.json").write_text("{}\n")

    result = substrate.run("server", "supervisor-pair-health")

    assert result.returncode == FAIL_CLOSED_EXIT, result.stdout + result.stderr
    assert "outside physical owner" in result.stderr
    assert not marker.exists()


def _corrupt_absent(python: Path) -> None:
    python.unlink()


def _corrupt_dangling_link(python: Path) -> None:
    python.unlink()
    python.symlink_to(python.parent / "python3.12-gone")


def _corrupt_not_executable(python: Path) -> None:
    python.chmod(0o644)


def _corrupt_not_python(python: Path) -> None:
    _write_executable(python, "#!/bin/sh\nprintf 'corrupt\\n'\nexit 0\n")


def _corrupt_directory(python: Path) -> None:
    python.unlink()
    python.mkdir()
    (python / "nested").write_text("nope\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("corrupt", "reason"),
    [
        pytest.param(_corrupt_absent, "is missing", id="absent"),
        pytest.param(_corrupt_dangling_link, "is missing", id="dangling-link"),
        pytest.param(
            _corrupt_not_executable, "is not an executable file", id="not-executable"
        ),
        pytest.param(_corrupt_directory, "is not an executable file", id="directory"),
        pytest.param(_corrupt_not_python, "does not run as Python 3", id="not-python"),
    ],
)
def test_server_private_python_corrupt_packaged_interpreter_fails_closed(
    substrate: _Substrate,
    corrupt,
    reason: str,
) -> None:
    """No packaged Python means a clear failure, never the host python3."""
    corrupt(substrate.packaged_python)
    before = substrate.fingerprint()

    result = substrate.run("server", "status")

    assert result.returncode == FAIL_CLOSED_EXIT, result.stderr
    assert f"Packaged Python interpreter {reason}: {substrate.packaged_python}" in (
        result.stderr
    ), result.stderr
    assert "reinstall Vibecrafted" in result.stderr
    assert "Server:" not in result.stdout, result.stdout
    assert SENTINEL_TEXT not in result.stdout + result.stderr
    _assert_sentinel_never_ran(substrate, before)


def test_server_private_python_leaves_foreign_interpreter_and_workspace_selection(
    substrate: _Substrate,
) -> None:
    """Server control never rewrites the operator's link or their PATH choice."""
    before = substrate.fingerprint()

    substrate.run("server", "status")
    substrate.run("server", "supervisor-pair-health")

    assert substrate.fingerprint() == before
    workspace = subprocess.run(
        ["/bin/sh", "-c", 'command -v python3; python3 -c "pass"'],
        check=False,
        cwd=substrate.scratch,
        env=substrate.environment(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = workspace.stdout.splitlines()
    assert lines == [str(substrate.sentinel_link), SENTINEL_TEXT], workspace.stdout
    assert substrate.sentinel_invocations() == ["-c pass"]
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert "export PYTHONPATH" not in launcher
    assert "export PYTHONHOME" not in launcher


def test_server_private_python_development_checkout_keeps_host_chain(
    tmp_path: Path,
) -> None:
    """A source checkout still drives the helper on the host interpreter."""
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(Path(sys.executable).resolve())
    (home / ".vibecrafted").mkdir(parents=True)

    result = subprocess.run(
        [str(LAUNCHER), "server", "status"],
        check=False,
        cwd=REPO_ROOT,
        env={
            "HOME": str(home),
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(tmp_path),
            "VIBECRAFTED_HOME": str(home / ".vibecrafted"),
            "VIBECRAFTED_RUNTIME_HOME": str(home / ".local/share/vibecrafted"),
            "VIBECRAFTED_SERVER_SUPERVISOR_CHILD": "1",
        },
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode != FAIL_CLOSED_EXIT, result.stderr
    assert "Server: STOPPED" in result.stdout, result.stdout + result.stderr
