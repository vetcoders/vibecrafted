from __future__ import annotations

import hashlib
import io
import json
import multiprocessing
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import threading
import time
from argparse import Namespace
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from vibecrafted_core import product_contract

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_LOADED_SERVICE_HOME = installer._runtime_loaded_service_home


@pytest.fixture(autouse=True)
def _isolate_fixed_runtime_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transaction tests never inspect or mutate the operator's live label."""

    monkeypatch.setattr(
        installer,
        "_canonical_operator_home",
        lambda: Path.home().resolve(strict=False),
    )
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _wait_for_text(path: Path, expected: str, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and expected in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.02)
    raise AssertionError(f"{expected!r} did not appear in {path}")


def _write_complete_source(
    root: Path,
    *,
    helper: str,
    launcher: str,
    service_lock_contract: bool = False,
) -> None:
    installer.stage_distribution_payload(
        REPO_ROOT,
        root,
        mirror=True,
    )
    (
        root
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "vetcoders.sh"
    ).write_text(helper, encoding="utf-8")
    if service_lock_contract:
        launcher = launcher.replace(
            "#!/usr/bin/env bash\n",
            (
                "#!/usr/bin/env bash\n"
                "readonly VIBECRAFTED_SERVICE_LIFECYCLE_LOCK_CONTRACT=1\n"
            ),
            1,
        )
    _write_executable(root / "scripts" / "vibecrafted", launcher)
    _write_executable(
        root / "bin" / "python3",
        f'#!/bin/sh\nexec {installer.shlex_quote(str(Path(sys.executable).absolute()))} "$@"\n',
    )
    _write_source_provenance_fixture(root)


def _write_source_provenance_fixture(
    root: Path,
    *,
    owner_repo: str = "vetcoders/vibecrafted",
    source_revision: str = "b" * 40,
) -> dict[str, object]:
    """Mint a test-only v2 carrier for the fixture's final detached tree."""
    provenance: dict[str, object] = {
        "schema": installer._SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": installer._distribution_manifest._distribution_tree_record(root),
    }
    carrier = root / "source-provenance.json"
    carrier.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    carrier.chmod(0o644)
    return provenance


def _write_walkaround_generation(
    tools_root: Path,
    name: str,
    runner_body: str,
) -> Path:
    generation = tools_root / name
    captured: dict[str, bytes] = {}
    for relative in installer._RUNTIME_GENERATION_REQUIRED_HASHES:
        target = generation / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative == Path("VERSION"):
            raw = b"9.9.9+gtest\n"
        elif relative == installer._RUNTIME_VERIFIER_RUNNER:
            raw = runner_body.encode("utf-8")
        else:
            raw = f"test fixture: {relative.as_posix()}\n".encode()
        target.write_bytes(raw)
        captured[relative.as_posix()] = raw
    manifest = {
        "schema": installer._RUNTIME_GENERATION_MANIFEST_SCHEMA,
        "version": "9.9.9+gtest",
        "source_fingerprint": "a" * 64,
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": "b" * 40,
        "source_payload": {
            "schema": installer._SOURCE_PAYLOAD_SCHEMA,
            "algorithm": "sha256",
            "tree_sha256": "c" * 64,
            "entry_count": 42,
        },
        "entrypoint": installer._RUNTIME_GENERATION_ENTRYPOINT.as_posix(),
        "hashes": {
            relative: hashlib.sha256(raw).hexdigest()
            for relative, raw in captured.items()
        },
    }
    (generation / installer._RUNTIME_GENERATION_MANIFEST).write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    carrier = generation / "source-provenance.json"
    carrier.write_text(
        json.dumps(
            {
                "schema": installer._SOURCE_PROVENANCE_SCHEMA,
                "owner_repo": manifest["owner_repo"],
                "source_revision": manifest["source_revision"],
                "payload": manifest["source_payload"],
            },
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    carrier.chmod(0o644)
    return generation


def _rewrite_walkaround_manifest(generation: Path) -> None:
    manifest_path = generation / installer._RUNTIME_GENERATION_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hashes"] = {
        relative.as_posix(): hashlib.sha256(
            (generation / relative).read_bytes()
        ).hexdigest()
        for relative in installer._RUNTIME_GENERATION_REQUIRED_HASHES
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


_SEMANTICALLY_FAKE_PRODUCT_CONTRACT = b"""from __future__ import annotations
import argparse
from pathlib import Path


class ProductContractError(ValueError):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message)
        self.code = code


def verify_installed_runtime_generation(generation_root, *, expected_entrypoint=None):
    return {}


def _parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("module").add_argument("path", type=Path)
    commands.add_parser("app").add_argument("path", type=Path)
    commands.add_parser("transaction").add_argument("path", type=Path)
    commands.add_parser("schema").add_argument("path", type=Path)
    commands.add_parser("walkaround").add_argument("path", type=Path)
    commands.add_parser("release-output").add_argument("path", type=Path)
    runtime = commands.add_parser("runtime-generation")
    runtime.add_argument("path", type=Path)
    return parser


def main(argv=None):
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "runtime-generation":
        print(f"verified runtime-generation: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


_SEMANTICALLY_FAKE_WALKAROUND_RUNNER = b"""from __future__ import annotations
import argparse
import sys
from pathlib import Path


def _parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("trust-probe")
    probe.add_argument("challenge", type=Path)
    probe.add_argument("signature", type=Path)
    verify = commands.add_parser("verify-release")
    verify.add_argument("--release-output", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    walkaround = commands.add_parser("walkaround")
    walkaround.add_argument("--release-output", type=Path, required=True)
    walkaround.add_argument("--signature", type=Path, required=True)
    walkaround.add_argument("--output", type=Path, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command in {"trust-probe", "verify-release"}:
        print("VCPC022: synthetic missing input", file=sys.stderr)
        return 22
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


_FINITE_QUIZ_FAKE_WALKAROUND_RUNNER = b"""from __future__ import annotations
import argparse
import sys
from pathlib import Path


def _parser():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("trust-probe")
    probe.add_argument("challenge", type=Path)
    probe.add_argument("signature", type=Path)
    verify = commands.add_parser("verify-release")
    verify.add_argument("--release-output", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    walkaround = commands.add_parser("walkaround")
    walkaround.add_argument("--release-output", type=Path, required=True)
    walkaround.add_argument("--signature", type=Path, required=True)
    walkaround.add_argument("--output", type=Path, required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(list(argv) if argv is not None else None)
    inputs = (
        (args.challenge, args.signature)
        if args.command == "trust-probe"
        else (args.release_output, args.signature)
    )
    if any(not path.is_file() for path in inputs):
        print("VCPC022: synthetic missing input", file=sys.stderr)
        return 22
    print("VCPC033: synthetic invalid proof", file=sys.stderr)
    return 33


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _install_test_walkaround_launcher(
    tmp_path: Path,
    current: Path,
) -> tuple[Path, Path]:
    tool_bin = tmp_path / "uv-tools" / "vibecrafted" / "bin"
    python_bin = tool_bin / "python"
    interpreter = Path(sys.executable).absolute()
    _write_executable(
        python_bin,
        f'#!/bin/sh\nexec {installer.shlex_quote(str(interpreter))} "$@"\n',
    )
    managed = installer._install_secure_walkaround_launcher(
        current,
        python_bin,
        launcher_path=tool_bin / installer.SECURE_WALKAROUND_LAUNCHER,
    )
    public = tmp_path / "public-bin" / installer.SECURE_WALKAROUND_LAUNCHER
    public.parent.mkdir(parents=True)
    public.symlink_to(managed)
    return managed, public


def _write_valid_runtime_generation(root: Path) -> None:
    package = root / "vibecrafted-core" / "vibecrafted_core"
    (package / "skills").mkdir(parents=True)
    (package / "runtime").mkdir()
    (root / "VERSION").write_text("9.9.8+gold\n", encoding="utf-8")
    deck = root / "scripts" / "vibecrafted"
    deck.parent.mkdir(parents=True)
    deck.write_bytes((REPO_ROOT / "scripts" / "vibecrafted").read_bytes())
    deck.chmod(0o755)


def _write_runtime_launch_agent(
    home: Path,
    shared_home: Path,
    launcher: Path,
) -> Path:
    supervisor = home / ".local" / "bin" / "vc-server-supervisor"
    path = (
        home / "Library" / "LaunchAgents" / f"{installer._RUNTIME_SERVICE_LABEL}.plist"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": installer._RUNTIME_SERVICE_LABEL,
        "ProgramArguments": [
            str(supervisor),
            "run",
            "--home",
            str(shared_home),
            "--runtime-home",
            str(home / ".local" / "share" / "vibecrafted"),
            "--operator-home",
            str(home),
            "--launcher",
            str(launcher),
            "--host",
            "127.0.0.1",
            "--port",
            "3024",
        ],
        "EnvironmentVariables": {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(shared_home),
            "VIBECRAFTED_RUNTIME_HOME": str(home / ".local" / "share" / "vibecrafted"),
            "VIBECRAFTED_SERVER_SUPERVISOR_PATH": str(supervisor),
        },
    }
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))
    path.chmod(0o600)
    return path


def _mock_runtime_launchd_gate(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str] | None = None,
) -> dict[str, bool]:
    state = {"disabled": False}

    def set_disabled(disabled: bool) -> None:
        state["disabled"] = disabled
        if events is not None:
            events.append(f"launchd-disabled:{disabled}")

    monkeypatch.setattr(
        installer,
        "_runtime_launchd_disabled_state",
        lambda: state["disabled"],
    )
    monkeypatch.setattr(installer, "_set_runtime_launchd_disabled", set_disabled)
    return state


def _tools_lease_worker(
    current_link: str,
    label: str,
    hold_seconds: float,
    timeout_seconds: float,
    ready,
    events,
) -> None:
    try:
        with installer._tools_install_lease(
            Path(current_link),
            timeout_seconds=timeout_seconds,
            operation=label,
        ):
            events.put((label, "acquired", time.monotonic()))
            ready.set()
            time.sleep(hold_seconds)
            events.put((label, "leaving", time.monotonic()))
    except (OSError, ValueError) as exc:  # pragma: no cover - asserted by parent
        events.put((label, "error", repr(exc)))
        ready.set()


class _TtyBuffer:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return True

    @property
    def text(self) -> str:
        return "".join(self.parts)


def test_compact_status_updates_one_tty_row() -> None:
    out = _TtyBuffer()

    installer._compact_line(out, "✓", "Skills", "27 installed")
    installer._compact_line(out, "✓", "Store", "~/.vibecrafted/skills")
    installer._clear_compact_status(out)

    assert "\n" not in out.text
    assert out.text.count("\r\033[K") == 3
    assert "Skills" in out.text
    assert "Store" in out.text
    assert out.text.endswith("\r\033[K")


def test_compact_status_appends_lines_for_non_tty_logs() -> None:
    out = io.StringIO()

    installer._compact_line(out, "✓", "Skills", "27 installed")
    installer._compact_line(out, "✓", "Store", "~/.vibecrafted/skills")
    installer._clear_compact_status(out)

    assert out.getvalue().splitlines() == [
        "  ✓ Skills        27 installed",
        "  ✓ Store         ~/.vibecrafted/skills",
    ]


def test_compact_checkpoint_prints_title_and_bounded_details_without_reason() -> None:
    """CLI_PRODUCT_SPEC §4: installers don't explain their own typography —
    the REASON narration line is retired from compact checkpoints."""
    out = io.StringIO()

    installer._compact_checkpoint(
        out,
        2,
        "Diagnostics and Plan",
        ("Skills   27 -> ~/.vibecrafted/skills", "Shell    enabled"),
    )

    assert out.getvalue().splitlines() == [
        "",
        "  [2/4] Diagnostics and Plan",
        "      Skills   27 -> ~/.vibecrafted/skills",
        "      Shell    enabled",
    ]


def test_refresh_current_tools_mirrors_shadowing_files(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source"
    crafted_home = tmp_path / "home" / ".vibecrafted"
    runtime_tools = tmp_path / "home" / ".local" / "share" / "vibecrafted" / "tools"
    old_target = runtime_tools / "vibecrafted-main"
    current_link = runtime_tools / "vibecrafted-current"

    _write_complete_source(
        source,
        helper='printf "fresh helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "fresh launcher\\n"\n',
    )
    (old_target / "runtime" / "shell").mkdir(parents=True)
    (old_target / "scripts").mkdir(parents=True)
    (old_target / "runtime" / "shell" / "vetcoders.sh").write_text(
        'printf "stale helper\\n"\n', encoding="utf-8"
    )
    (old_target / "scripts" / "vibecrafted").write_text(
        'printf "stale launcher\\n"\n', encoding="utf-8"
    )
    (old_target / "obsolete.txt").write_text("delete me\n", encoding="utf-8")
    stale_cache = old_target / "vibecrafted-core" / "vibecrafted_core" / "__pycache__"
    stale_cache.mkdir(parents=True)
    (stale_cache / "dispatcher.cpython-314.pyc").write_bytes(b"stale")
    current_link.parent.mkdir(parents=True, exist_ok=True)
    current_link.symlink_to(old_target)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))

    refreshed = installer.refresh_current_tools(
        source, crafted_home, dry_run=False, mirror=True
    )

    assert refreshed == current_link
    assert current_link.is_symlink()
    new_target = current_link.resolve()
    assert new_target != old_target
    assert (
        new_target / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
    ).read_text(encoding="utf-8") == 'printf "fresh helper\\n"\n'
    assert (new_target / "scripts" / "vibecrafted").read_text(
        encoding="utf-8"
    ) == '#!/usr/bin/env bash\nprintf "fresh launcher\\n"\n'
    assert not (new_target / "obsolete.txt").exists()
    assert not (
        new_target / "vibecrafted-core" / "vibecrafted_core" / "__pycache__"
    ).exists()
    # Rollback truth remains immutable until the tool/service handoff is sealed.
    assert (old_target / "obsolete.txt").read_text(encoding="utf-8") == "delete me\n"
    assert (old_target / "scripts" / "vibecrafted").read_text(
        encoding="utf-8"
    ) == 'printf "stale launcher\\n"\n'


def _runtime_pointer_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    old_target = tmp_path / "tools" / "vibecrafted-generation-old"
    current = tmp_path / "tools" / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    (old_target / "proof.txt").write_text("old runtime\n", encoding="utf-8")
    current.symlink_to(old_target.name)
    return source, old_target, current


def test_atomic_runtime_pointer_survives_symlinked_parent_path(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    tools = real_root / "tools"
    generation = tools / "vibecrafted-generation-test"
    generation.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    current = alias / "tools" / "vibecrafted-current"

    installer._atomic_symlink(alias / "tools" / generation.name, current)

    assert current.is_symlink()
    assert current.resolve(strict=True) == generation.resolve(strict=True)


def test_live_legacy_service_cutover_publishes_native_identity_without_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    server_dir = shared_home / "server"
    events: list[str] = []
    processes: list[subprocess.Popen[str]] = []
    active_processes: list[subprocess.Popen[str]] = []
    mode = "old-active"

    _write_complete_source(
        source,
        helper='printf "native helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "native launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    server_dir.mkdir(parents=True)

    def spawn_group(label: str) -> list[subprocess.Popen[str]]:
        group = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(120)",
                    f"{label}-{role}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for role in ("server", "guardian", "supervisor")
        ]
        processes.extend(group)
        return group

    def stop_group(group: list[subprocess.Popen[str]]) -> None:
        for process in group:
            if process.poll() is None:
                process.terminate()
        for process in group:
            if process.poll() is None:
                process.wait(timeout=10)

    def write_pair_identity(prefix: str, group: list[subprocess.Popen[str]]) -> None:
        for role, process in zip(("server", "guardian"), group[:2], strict=True):
            (server_dir / f"{role}.identity.json").write_text(
                json.dumps(
                    {
                        "pid": process.pid,
                        "role": role,
                        "start_token": f"{prefix}:{process.pid}",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

    active_processes = spawn_group("legacy")
    old_processes = list(active_processes)
    write_pair_identity("ps", active_processes)

    def service_payload() -> dict[str, object]:
        active = mode in {"old-active", "native-active"}
        return {
            "installed": True,
            "loaded": active,
            "supervisor_live": active,
            "supervisor_verified": active,
            "supervisor_service_managed": active,
            "build_current": active,
            "pair_healthy": active,
            "supervisor_pid": active_processes[2].pid if active else None,
        }

    def fake_service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal active_processes, mode
        pointer = current.resolve().name
        if arguments == ("service", "status", "--json"):
            payload = service_payload()
            return subprocess.CompletedProcess(
                list(arguments),
                0 if payload["loaded"] else 1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            output = (
                "Server: RUNNING\nGuardian: RUNNING\n"
                if mode in {"old-active", "native-active"}
                else "Server: STOPPED\nGuardian: STOPPED\n"
            )
            return subprocess.CompletedProcess(list(arguments), 0, output, "")
        if arguments == ("service", "stop"):
            events.append(f"stop:{mode}:{pointer}")
            stop_group(active_processes)
            active_processes = []
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        if arguments[:2] == ("service", "install"):
            assert mode == "stopped"
            assert (server_dir / "lifecycle.lock").is_dir()
            events.append(f"install-native:{pointer}")
            active_processes = spawn_group("native")
            write_pair_identity("darwin", active_processes)
            mode = "native-active"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    _mock_runtime_launchd_gate(monkeypatch, events)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        fake_service_command,
    )

    try:
        supervisor_fence_probe = tmp_path / "supervisor-fence-probe"
        lifecycle_fence_probe = tmp_path / "lifecycle-fence-probe"
        publish_code = (
            "import fcntl, os, sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import vetcoders_install as v\n"
            "shared = Path(sys.argv[3])\n"
            "try:\n"
            "    (shared / 'server' / 'lifecycle.lock').mkdir()\n"
            "except FileExistsError:\n"
            "    Path(sys.argv[5]).write_text('blocked\\n', encoding='utf-8')\n"
            "else:\n"
            "    Path(sys.argv[5]).write_text('acquired\\n', encoding='utf-8')\n"
            "    raise SystemExit(92)\n"
            "descriptor = os.open(shared / 'server' / 'supervisor.lock', os.O_RDWR)\n"
            "try:\n"
            "    try:\n"
            "        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    except BlockingIOError:\n"
            "        Path(sys.argv[4]).write_text('blocked\\n', encoding='utf-8')\n"
            "    else:\n"
            "        Path(sys.argv[4]).write_text('acquired\\n', encoding='utf-8')\n"
            "        raise SystemExit(91)\n"
            "finally:\n"
            "    os.close(descriptor)\n"
            "v.refresh_current_tools(Path(sys.argv[2]), shared, mirror=True)\n"
        )
        assert (
            installer.run_with_tools_install_lease(
                shared_home,
                [
                    sys.executable,
                    "-c",
                    publish_code,
                    str(REPO_ROOT / "scripts"),
                    str(source),
                    str(shared_home),
                    str(supervisor_fence_probe),
                    str(lifecycle_fence_probe),
                ],
            )
            == 0
        )

        assert supervisor_fence_probe.read_text(encoding="utf-8") == "blocked\n"
        assert lifecycle_fence_probe.read_text(encoding="utf-8") == "blocked\n"
        assert not (shared_home / "server" / "lifecycle.lock").exists()
        assert current.resolve() != old_target.resolve()
        assert events == [
            "launchd-disabled:True",
            f"stop:old-active:{old_target.name}",
            "launchd-disabled:False",
            f"install-native:{current.resolve().name}",
        ]
        assert len(active_processes) == 3
        assert all(process.poll() is None for process in active_processes)
        assert all(process.poll() is not None for process in old_processes)
        for role in ("server", "guardian"):
            payload = json.loads(
                (server_dir / f"{role}.identity.json").read_text(encoding="utf-8")
            )
            assert payload["start_token"].startswith("darwin:")
    finally:
        stop_group(processes)


def test_runtime_cutover_refuses_uncertain_old_identity_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    calls: list[tuple[str, ...]] = []

    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")

    def uncertain_status(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        payload = {
            "installed": True,
            "loaded": True,
            "supervisor_live": True,
            "supervisor_verified": False,
            "supervisor_service_managed": True,
            "build_current": False,
            "pair_healthy": False,
            "supervisor_pid": 4242,
        }
        return subprocess.CompletedProcess(
            list(arguments),
            1,
            json.dumps(payload) + "\n",
            "",
        )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        uncertain_status,
    )

    with installer._tools_install_lease(
        current,
        operation="test-uncertain-cutover",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        with pytest.raises(OSError, match="identity is uncertain"):
            installer.runtime_service_active_for_install(shared_home)

    assert calls == [("service", "status", "--json")]
    assert current.resolve() == old_target.resolve()


def test_runtime_cutover_refuses_foreign_fixed_label_launchd_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "plist": tmp_path / "owned.plist",
        "program": tmp_path / "owned-supervisor",
        "supervisor": tmp_path / "owned-supervisor",
        "home": tmp_path / "owned-home",
        "runtime_home": tmp_path / "owned-runtime",
        "operator_home": tmp_path / "operator",
        "launcher": tmp_path / "owned-launcher",
    }
    monkeypatch.setattr(
        installer,
        "_runtime_launch_agent_contract",
        lambda _shared_home: expected,
    )
    launchctl_payload = f"""
path = {expected["plist"]}
program = {expected["program"]}
environment = {{
    VIBECRAFTED_SERVER_SUPERVISOR_PATH => {expected["supervisor"]}
    VIBECRAFTED_HOME => {tmp_path / "foreign-home"}
    VIBECRAFTED_RUNTIME_HOME => {expected["runtime_home"]}
    HOME => {expected["operator_home"]}
}}
"""

    with pytest.raises(OSError, match="foreign runtime paths"):
        installer._assert_runtime_launchd_job_owned(
            expected["home"],
            result=subprocess.CompletedProcess(
                ["launchctl", "print"],
                0,
                launchctl_payload,
                "",
            ),
        )


def test_loaded_runtime_home_is_attributed_from_launchd_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / "owned" / ".vibecrafted"
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            0,
            (f"environment = {{\n    VIBECRAFTED_HOME => {shared_home}\n}}\n"),
            "",
        ),
    )

    assert _RUNTIME_LOADED_SERVICE_HOME() == shared_home.resolve(strict=False)


def test_loaded_runtime_home_without_attribution_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            0,
            "environment = {\n    HOME => /tmp/operator\n}\n",
            "",
        ),
    )

    with pytest.raises(OSError, match="no attributable VIBECRAFTED_HOME"):
        _RUNTIME_LOADED_SERVICE_HOME()


def test_loaded_runtime_home_accepts_exact_launchctl_missing_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            113,
            "",
            (
                "Bad request.\n"
                f'Could not find service "{installer._RUNTIME_SERVICE_LABEL}" '
                "in domain for user gui: 501\n"
            ),
        ),
    )

    assert _RUNTIME_LOADED_SERVICE_HOME() is None


def test_loaded_runtime_home_rejects_non_absence_launchctl_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            5,
            "",
            "permission denied",
        ),
    )

    with pytest.raises(OSError, match="ownership query failed.*permission denied"):
        _RUNTIME_LOADED_SERVICE_HOME()


def test_bootout_never_hides_loaded_job_when_owned_plist_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            0,
            "loaded job\n",
            "",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("loaded runtime service has no readable owned LaunchAgent plist")
        ),
    )

    with pytest.raises(OSError, match="no readable owned LaunchAgent plist"):
        installer._bootout_owned_runtime_launchd_job(tmp_path / ".vibecrafted")


def test_bootout_rejects_ambiguous_launchctl_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print"],
            5,
            "",
            "permission denied",
        ),
    )

    with pytest.raises(OSError, match="ownership query failed.*permission denied"):
        installer._bootout_owned_runtime_launchd_job(tmp_path / ".vibecrafted")


def test_bootout_rejects_ambiguous_postcondition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = iter(
        (
            subprocess.CompletedProcess(["launchctl", "print"], 0, "loaded\n", ""),
            subprocess.CompletedProcess(
                ["launchctl", "print"],
                5,
                "",
                "permission denied",
            ),
        )
    )

    def launchctl(*arguments: str) -> subprocess.CompletedProcess[str]:
        if arguments[0] == "bootout":
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        return next(observations)

    monkeypatch.setattr(installer, "_runtime_launchctl", launchctl)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(OSError, match="ownership query failed.*permission denied"):
        installer._bootout_owned_runtime_launchd_job(tmp_path / ".vibecrafted")


def test_same_home_loaded_label_counts_as_runtime_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    monkeypatch.setattr(
        installer,
        "_runtime_loaded_service_home",
        lambda: shared_home.resolve(strict=False),
    )

    assert installer._runtime_service_has_evidence(shared_home) is True


def test_foreign_loaded_label_refuses_managed_policy_before_gate_or_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "alternate-home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    child_marker = tmp_path / "child-ran"
    launchd_mutations: list[bool] = []
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_loaded_service_home",
        lambda: tmp_path / "operator-home" / ".vibecrafted",
    )
    monkeypatch.setattr(
        installer,
        "_set_runtime_launchd_disabled",
        lambda disabled: launchd_mutations.append(disabled),
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(child_marker),
        ],
        service_policy="ensure",
    )
    captured = capfd.readouterr()

    assert result == 126
    assert "fixed-label runtime service belongs to foreign home" in captured.err
    assert launchd_mutations == []
    assert not child_marker.exists()
    assert current.resolve() == old_target.resolve()


def test_alternate_home_requires_explicit_isolated_service_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "alternate-home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    child_marker = tmp_path / "child-ran"
    launchd_mutations: list[bool] = []
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_canonical_operator_home",
        lambda: tmp_path / "canonical-operator-home",
    )
    monkeypatch.setattr(
        installer,
        "_runtime_loaded_service_home",
        lambda: (_ for _ in ()).throw(
            AssertionError("alternate HOME reached the global runtime label")
        ),
    )
    monkeypatch.setattr(
        installer,
        "_set_runtime_launchd_disabled",
        lambda disabled: launchd_mutations.append(disabled),
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(child_marker),
        ],
        service_policy="preserve",
        require_tools_handoff=False,
    )
    captured = capfd.readouterr()

    assert result == 126
    assert "managed runtime service requires the canonical operator HOME" in (
        captured.err
    )
    assert "Use service policy 'isolated'" in captured.err
    assert launchd_mutations == []
    assert not child_marker.exists()


def test_preserve_policy_always_closes_managed_darwin_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    child_marker = tmp_path / "child-ran"
    disabled_events: list[bool] = []
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer, "_runtime_loaded_service_home", lambda: None)
    monkeypatch.setattr(installer, "_runtime_service_snapshot", lambda _home: None)
    monkeypatch.setattr(
        installer,
        "_runtime_launchd_disabled_state",
        lambda: False,
    )
    monkeypatch.setattr(
        installer,
        "_set_runtime_launchd_disabled",
        lambda disabled: disabled_events.append(disabled),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _home: False,
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(child_marker),
        ],
        service_policy="preserve",
        require_tools_handoff=False,
    )

    assert result == 0
    assert child_marker.is_file()
    assert disabled_events == [True, False]


def test_isolated_policy_publishes_without_runtime_service_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "sandbox-home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "isolated helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "isolated launcher\\n"\n',
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("isolated policy reached runtime service state")

    for name in (
        "_canonical_operator_home",
        "_runtime_loaded_service_home",
        "_runtime_service_snapshot",
        "_capture_runtime_launch_agent_backup",
        "_bootout_owned_runtime_launchd_job",
        "_runtime_launchd_disabled_state",
        "_set_runtime_launchd_disabled",
    ):
        monkeypatch.setattr(installer, name, unexpected)

    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )
    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="isolated",
    )

    assert result == 0
    assert current.is_symlink()
    assert (
        current / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
    ).read_text(encoding="utf-8") == 'printf "isolated helper\\n"\n'


def test_isolated_policy_rolls_back_without_runtime_service_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "sandbox-home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "replacement helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "replacement launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    (old_target / "proof.txt").write_text("old runtime\n", encoding="utf-8")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("isolated rollback reached runtime service state")

    for name in (
        "_canonical_operator_home",
        "_runtime_loaded_service_home",
        "_runtime_service_snapshot",
        "_capture_runtime_launch_agent_backup",
        "_bootout_owned_runtime_launchd_job",
        "_runtime_launchd_disabled_state",
        "_set_runtime_launchd_disabled",
    ):
        monkeypatch.setattr(installer, name, unexpected)
    monkeypatch.setattr(
        installer,
        "_complete_current_tools_handoff_locked",
        lambda _shared_home: (_ for _ in ()).throw(OSError("seal failed")),
    )

    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )
    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="isolated",
    )
    captured = capfd.readouterr()

    assert result == 126
    assert "seal failed" in captured.err
    assert current.resolve() == old_target.resolve()
    assert (current / "proof.txt").read_text(encoding="utf-8") == "old runtime\n"


@pytest.mark.parametrize(
    ("rendered", "expected"),
    [
        ('"io.vetcoders.vibecrafted.server" => enabled\n', False),
        ('"io.vetcoders.vibecrafted.server" => disabled\n', True),
        ('"io.vetcoders.vibecrafted.server" => false\n', False),
        ('"io.vetcoders.vibecrafted.server" => true\n', True),
        ('"another.service" => disabled\n', False),
    ],
)
def test_runtime_launchd_disabled_state_accepts_native_and_legacy_rendering(
    monkeypatch: pytest.MonkeyPatch,
    rendered: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        installer,
        "_runtime_launchctl",
        lambda *_arguments: subprocess.CompletedProcess(
            ["launchctl", "print-disabled"],
            0,
            f"disabled services = {{\n{rendered}}}\n",
            "",
        ),
    )

    assert installer._runtime_launchd_disabled_state() is expected


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (
            (
                "/bin/bash",
                "/tmp/vibecrafted",
                "server",
                "service",
                "install",
            ),
            True,
        ),
        (
            (
                sys.executable,
                "-m",
                "vibecrafted_core.server_supervisor",
                "service",
                "restart",
            ),
            True,
        ),
        (("/tmp/vibecrafted", "server", "start"), True),
        (("/tmp/vibecrafted", "server", "stop"), True),
        (("/tmp/vc-server-supervisor", "manual-stop"), True),
        (
            (
                sys.executable,
                "-m",
                "vibecrafted_core.server_supervisor",
                "manual-stop",
            ),
            True,
        ),
        (("/tmp/vibecrafted", "server", "service", "status"), False),
        (("/tmp/vibecrafted", "server", "status"), False),
        (("/tmp/unrelated", "server", "service", "install"), False),
        (("/tmp/unrelated", "server", "stop"), False),
        ((sys.executable, "-m", "unrelated", "service", "install"), False),
        ((sys.executable, "-m", "unrelated", "manual-stop"), False),
    ],
)
def test_legacy_service_mutator_argv_classifier(
    argv: tuple[str, ...],
    expected: bool,
) -> None:
    assert installer._argv_is_service_mutator(argv) is expected


def test_legacy_mutator_wait_ignores_post_publication_contender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published_at = datetime.now(timezone.utc)
    old = installer._LegacyServiceMutator(
        pid=41,
        start_token="darwin:1:1",
        started_at=published_at - timedelta(seconds=1),
        argv=("/tmp/vibecrafted", "server", "service", "install"),
    )
    new = installer._LegacyServiceMutator(
        pid=42,
        start_token="darwin:2:2",
        started_at=published_at + timedelta(seconds=1),
        argv=("/tmp/vibecrafted", "server", "service", "install"),
    )
    observations = iter(((old, new), (new,), (new,)))
    monkeypatch.setattr(
        installer,
        "_legacy_service_mutator_census",
        lambda: next(observations),
    )
    monkeypatch.setattr(installer.time, "sleep", lambda _seconds: None)

    installer._wait_for_legacy_service_mutator_quiescence(
        published_at=published_at,
        timeout_seconds=1,
    )


def test_runtime_launch_agent_backup_restores_exact_bytes_and_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    launcher = home / ".local" / "bin" / "vibecrafted"
    monkeypatch.setenv("HOME", str(home))
    path = _write_runtime_launch_agent(home, shared_home, launcher)
    payload = plistlib.loads(path.read_bytes())
    arguments = payload["ProgramArguments"]
    arguments[arguments.index("--host") + 1] = "127.0.0.7"
    arguments[arguments.index("--port") + 1] = "43024"
    arguments.extend(("--interval", "2.75"))
    path.write_bytes(plistlib.dumps(payload, sort_keys=False))
    exact = path.read_bytes()

    backup = installer._capture_runtime_launch_agent_backup(shared_home)
    assert backup.contents == exact
    assert backup.service_arguments == (
        "--host",
        "127.0.0.7",
        "--port",
        "43024",
        "--interval",
        "2.75",
    )
    path.write_bytes(plistlib.dumps({"Label": "foreign"}))
    with pytest.raises(OSError, match="foreign runtime paths|foreign label"):
        installer._restore_runtime_launch_agent_backup(shared_home, backup)

    path.unlink()
    installer._restore_runtime_launch_agent_backup(shared_home, backup)
    assert path.read_bytes() == exact

    path.unlink()
    absent = installer._capture_runtime_launch_agent_backup(shared_home)
    _write_runtime_launch_agent(home, shared_home, launcher)
    installer._restore_runtime_launch_agent_backup(shared_home, absent)
    assert not path.exists()


def test_installer_seeds_server_config_from_verified_custom_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_home = tmp_path / "operator"
    monkeypatch.setattr(installer, "_canonical_operator_home", lambda: operator_home)
    backup = installer._RuntimeLaunchAgentBackup(
        operator_home / "Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist",
        b"verified",
        0o600,
        (
            "--host",
            "100.82.232.70",
            "--port",
            "3025",
            "--interval",
            "2.75",
        ),
    )

    arguments = installer._runtime_service_arguments_from_config(backup)
    config_path = operator_home / ".config/vibecrafted/config.toml"

    assert arguments == (
        "--host",
        "100.82.232.70",
        "--port",
        "3025",
        "--interval",
        "2.75",
    )
    assert 'bind_host = "100.82.232.70"' in config_path.read_text(encoding="utf-8")
    assert "port = 3025" in config_path.read_text(encoding="utf-8")


def test_installer_existing_server_config_overrides_legacy_plist_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator_home = tmp_path / "operator"
    config_path = operator_home / ".config/vibecrafted/config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[server]\n"
        'bind_host = "100.82.232.70"\n'
        "port = 3025\n"
        'public_url = "http://100.82.232.70:3025"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "_canonical_operator_home", lambda: operator_home)
    backup = installer._RuntimeLaunchAgentBackup(
        operator_home / "Library/LaunchAgents/io.vetcoders.vibecrafted.server.plist",
        b"verified",
        0o600,
        ("--host", "127.0.0.1", "--port", "3024"),
    )

    arguments = installer._runtime_service_arguments_from_config(backup)

    assert arguments == (
        "--host",
        "100.82.232.70",
        "--port",
        "3025",
    )


def test_runtime_cutover_failed_legacy_stop_recovers_previous_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    launcher = tmp_path / "old-vibecrafted"
    current.parent.mkdir(parents=True)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    events: list[str] = []
    mode = "active"
    recovery_transition_observed = False
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    old_plist = plist.read_bytes()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    def snapshot(_shared_home: Path):
        nonlocal recovery_transition_observed
        if mode == "active":
            return launcher, healthy, "running"
        if not recovery_transition_observed:
            recovery_transition_observed = True
            raise installer._RuntimeServiceTransition(
                "runtime service identity is uncertain while transition is in progress"
            )
        return launcher, quiescent, "stopped"

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        events.append(" ".join(arguments))
        if arguments == ("service", "stop"):
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 42, "", "stop failed")
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", command)

    def restore_exact(
        _shared_home: Path,
        backup: installer._RuntimeLaunchAgentBackup,
    ) -> None:
        nonlocal mode
        assert backup.contents == old_plist
        events.append("restore exact LaunchAgent")
        mode = "active"

    monkeypatch.setattr(
        installer,
        "_activate_runtime_service_from_backup",
        restore_exact,
    )
    with installer._tools_install_lease(
        current,
        operation="test-failed-drain-compensation",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        with pytest.raises(
            OSError,
            match=r"legacy runtime drain failed \(.*stop failed.*\); previous",
        ):
            installer.prepare_runtime_service_for_install(shared_home)

    assert events == ["service stop", "restore exact LaunchAgent"]
    assert recovery_transition_observed


def test_runtime_drain_waits_for_launchd_transition_to_become_quiescent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An owned post-stop transition is convergence, not lost identity."""
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    launcher = tmp_path / "old-vibecrafted"
    current.parent.mkdir(parents=True)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    degraded = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=False,
        supervisor_pid=8181,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    phase = "degraded"
    transition_observed = False
    plist = _write_runtime_launch_agent(home, shared_home, launcher)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(installer.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def snapshot(_shared_home: Path):
        nonlocal transition_observed
        if phase == "degraded":
            return launcher, degraded, "orphaned"
        if not transition_observed:
            transition_observed = True
            raise installer._RuntimeServiceTransition(
                "runtime service identity is uncertain while transition is in progress"
            )
        return launcher, quiescent, "stopped"

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)

    def command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal phase
        assert arguments == ("service", "stop")
        phase = "stopping"
        return subprocess.CompletedProcess(list(arguments), 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", command)
    with installer._tools_install_lease(
        current,
        operation="test-transitioning-drain",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        assert installer.prepare_runtime_service_for_install(
            shared_home,
            launch_agent_backup=installer._RuntimeLaunchAgentBackup(
                plist,
                plist.read_bytes(),
                0o600,
                (),
            ),
        )

    assert transition_observed


def test_runtime_service_settlement_timeout_reports_last_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (_ for _ in ()).throw(
            installer._RuntimeServiceTransition("launchd is still converging")
        ),
    )

    with pytest.raises(OSError, match="last observation: launchd is still converging"):
        installer._wait_for_runtime_service_settlement(
            tmp_path,
            allow_healthy=False,
            timeout_seconds=0,
        )


def test_failed_legacy_stop_replaces_raced_plist_with_exact_previous_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    launcher = tmp_path / "old-vibecrafted"
    current.parent.mkdir(parents=True)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    mode = "active"
    stop_calls = 0
    events: list[str] = []
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    payload = plistlib.loads(plist.read_bytes())
    arguments = payload["ProgramArguments"]
    arguments[arguments.index("--port") + 1] = "41017"
    plist.write_bytes(plistlib.dumps(payload, sort_keys=False))
    exact = plist.read_bytes()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            launcher,
            healthy if mode == "active" else quiescent,
            "running" if mode == "active" else "stopped",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode, stop_calls
        assert arguments == ("service", "stop")
        stop_calls += 1
        events.append(f"stop:{stop_calls}")
        if stop_calls == 1:
            raced = plistlib.loads(exact)
            raced_arguments = raced["ProgramArguments"]
            raced_arguments[raced_arguments.index("--port") + 1] = "3024"
            plist.write_bytes(plistlib.dumps(raced, sort_keys=True))
            return subprocess.CompletedProcess(
                list(arguments),
                42,
                "",
                "raced stop failed",
            )
        mode = "stopped"
        return subprocess.CompletedProcess(list(arguments), 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", command)
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: events.append("bootout") or True,
    )

    def restore_exact(
        _shared_home: Path,
        backup: installer._RuntimeLaunchAgentBackup,
    ) -> None:
        nonlocal mode
        assert backup.contents == exact
        plist.write_bytes(backup.contents)
        mode = "active"
        events.append("restore exact LaunchAgent")

    monkeypatch.setattr(
        installer,
        "_activate_runtime_service_from_backup",
        restore_exact,
    )
    with installer._tools_install_lease(
        current,
        operation="test-raced-drain-compensation",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        with pytest.raises(OSError, match="previous service ownership was recovered"):
            installer.prepare_runtime_service_for_install(shared_home)

    assert plist.read_bytes() == exact
    assert events == [
        "stop:1",
        "stop:2",
        "bootout",
        "restore exact LaunchAgent",
    ]


def test_uncertain_drain_failure_keeps_launchd_label_disabled_before_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    marker = tmp_path / "child-ran"
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (launcher, healthy, "running"),
    )
    monkeypatch.setattr(
        installer,
        "prepare_runtime_service_for_install",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("drain recovery is uncertain")
        ),
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        require_tools_handoff=False,
    )

    assert result == 126
    assert gate_state["disabled"] is True
    assert not marker.exists()
    assert current.resolve() == old_target.resolve()


def test_runtime_service_commands_pin_xdg_tools_lock_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "xdg-data"
    launcher = home / ".local" / "bin" / "vibecrafted"
    shared_home = home / ".vibecrafted"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.delenv("VIBECRAFTED_TOOLS_HOME", raising=False)

    environment = installer._runtime_service_environment(launcher, shared_home)

    assert environment["VIBECRAFTED_HOME"] == str(shared_home)
    assert environment["VIBECRAFTED_TOOLS_HOME"] == str(
        data_home / "vibecrafted" / "tools"
    )


def test_service_install_executes_exact_staged_supervisor_from_repo_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    tools = runtime_home / "tools"
    current = tools / "vibecrafted-current"
    bin_dir = home / ".local" / "bin"
    checkout = tmp_path / "checkout"
    staged_core = current / "staged-core"
    staged_package = staged_core / "vibecrafted_core"
    source_package = checkout / "vibecrafted-core" / "vibecrafted_core"
    deck = current / "scripts" / "vibecrafted"
    launcher = bin_dir / "vibecrafted"
    supervisor_binary = bin_dir / "vc-server-supervisor"
    launch_agent = (
        home / "Library" / "LaunchAgents" / "io.vetcoders.vibecrafted.server.plist"
    )
    record = tmp_path / "service-install-record.json"
    source_version = "1.0.0+gcheckout"
    staged_version = "9.9.9+gstaged"

    for directory in (
        checkout / "scripts",
        checkout / "skills",
        checkout / "runtime",
        source_package,
        staged_package,
        deck.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (checkout / "VERSION").write_text(source_version + "\n", encoding="utf-8")
    (checkout / "scripts" / "vibecrafted").write_text(
        "#!/bin/sh\nexit 86\n",
        encoding="utf-8",
    )
    (source_package / "__init__.py").write_text(
        f"__version__ = {source_version!r}\n",
        encoding="utf-8",
    )
    (source_package / "dispatcher.py").write_text("", encoding="utf-8")
    (source_package / "server_supervisor.py").write_text(
        "import sys\n"
        "print('checkout supervisor executed', file=sys.stderr)\n"
        "raise SystemExit(86)\n",
        encoding="utf-8",
    )

    deck.write_bytes((REPO_ROOT / "scripts" / "vibecrafted").read_bytes())
    deck.chmod(0o755)
    (staged_package / "__init__.py").write_text(
        f"__version__ = {staged_version!r}\n",
        encoding="utf-8",
    )
    shutil.copy2(
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "server_supervisor.py",
        staged_package / "server_supervisor.py",
    )
    shutil.copy2(
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "server_config.py",
        staged_package / "server_config.py",
    )
    _write_executable(
        launcher,
        (
            f"#!{Path(sys.executable).resolve()}\n"
            "import sys\n"
            "raise SystemExit(1 if sys.argv[1:3] == ['server', 'start'] else 0)\n"
        ),
    )
    _write_executable(
        supervisor_binary,
        f"""#!{Path(sys.executable).resolve()}
from __future__ import annotations

import contextlib
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, {str(staged_core)!r})
from vibecrafted_core import server_supervisor as runtime

LAUNCH_AGENT = Path({str(launch_agent)!r})
RECORD = Path({str(record)!r})


def service_main() -> int:
    loaded = False
    child = None
    payload = None

    def start_child() -> None:
        nonlocal child, payload
        payload = plistlib.loads(LAUNCH_AGENT.read_bytes())
        environment = os.environ.copy()
        environment.update(payload["EnvironmentVariables"])
        child = subprocess.Popen(
            payload["ProgramArguments"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def launchctl(arguments):
        nonlocal loaded
        action = arguments[0]
        if action == "bootstrap":
            loaded = True
            start_child()
        elif action == "kickstart" and (
            child is None or child.poll() is not None
        ):
            start_child()
        return subprocess.CompletedProcess(arguments, 0, "", "")

    runtime.sys.platform = "darwin"
    runtime._launchctl = launchctl
    runtime._launchctl_loaded = lambda: loaded
    runtime._launchctl_job_owns_paths = lambda _paths: loaded
    try:
        result = runtime.main()
        if result != 0:
            return result
        payload = plistlib.loads(LAUNCH_AGENT.read_bytes())
        arguments = payload["ProgramArguments"]
        paths = runtime.SupervisorPaths.create(
            home=Path(os.environ["VIBECRAFTED_HOME"]),
            runtime_home=Path(os.environ["VIBECRAFTED_RUNTIME_HOME"]),
            operator_home=Path(os.environ["HOME"]),
        )
        probe = runtime.probe_supervisor(paths)
        RECORD.write_text(
            json.dumps(
                {{
                    "renderer_version": runtime.PACKAGE_VERSION,
                    "expected_version": arguments[
                        arguments.index("--expected-build-version") + 1
                    ],
                    "environment_version": payload["EnvironmentVariables"][
                        "VIBECRAFTED_SERVER_SUPERVISOR_VERSION"
                    ],
                    "program": arguments[0],
                    "probe_live": probe.live,
                    "probe_verified": probe.verified,
                    "probe_version": probe.build_version,
                    "probe_executable": probe.executable,
                }},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return 0
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    child.kill()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    child.wait(timeout=1)


if sys.argv[1:2] == ["service"]:
    raise SystemExit(service_main())
raise SystemExit(runtime.main())
""",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "PATH": (
                f"{bin_dir}:{Path(sys.executable).resolve().parent}:/usr/bin:/bin"
            ),
            "VIBECRAFTED_HOME": str(shared_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_TOOLS_HOME": str(tools),
        }
    )
    environment.pop("VIBECRAFTED_INSTALL_LEASE_FD", None)
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [str(deck), "server", "service", "install"],
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "checkout supervisor executed" not in result.stderr
    observed = json.loads(record.read_text(encoding="utf-8"))
    assert {
        observed["renderer_version"],
        observed["expected_version"],
        observed["environment_version"],
        observed["probe_version"],
    } == {staged_version}
    assert Path(observed["program"]).samefile(supervisor_binary)
    assert Path(observed["probe_executable"]).samefile(supervisor_binary)
    assert observed["probe_live"] is True
    assert observed["probe_verified"] is True


def test_runtime_cutover_refuses_legacy_restart_race_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    lock_path = shared_home / "server" / "supervisor.lock"
    child_marker = tmp_path / "publication-ran"
    mode = "active"
    contender: subprocess.Popen[str] | None = None

    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    lock_path.parent.mkdir(parents=True)
    lock_path.touch(mode=0o600)

    def start_contender() -> subprocess.Popen[str]:
        code = (
            "import fcntl, os, sys\n"
            "descriptor = os.open(sys.argv[1], os.O_RDWR)\n"
            "fcntl.flock(descriptor, fcntl.LOCK_EX)\n"
            "print('ready', flush=True)\n"
            "sys.stdin.readline()\n"
            "fcntl.flock(descriptor, fcntl.LOCK_UN)\n"
            "os.close(descriptor)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", code, str(lock_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline() == "ready\n"
        return process

    def fake_service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode, contender
        active = mode == "active"
        if arguments == ("service", "status", "--json"):
            payload = {
                "installed": True,
                "loaded": active,
                "supervisor_live": active,
                "supervisor_verified": active,
                "supervisor_service_managed": active,
                "build_current": active,
                "pair_healthy": active,
                "supervisor_pid": 7070 if active else None,
            }
            return subprocess.CompletedProcess(
                list(arguments),
                0 if active else 1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            if not active and contender is None:
                contender = start_contender()
            output = (
                "Server: RUNNING\nGuardian: RUNNING\n"
                if active
                else "Server: STOPPED\nGuardian: STOPPED\n"
            )
            return subprocess.CompletedProcess(list(arguments), 0, output, "")
        if arguments == ("service", "stop"):
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        fake_service_command,
    )

    try:
        result = installer.run_with_tools_install_lease(
            shared_home,
            [
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_text('published\\n')"
                ),
                str(child_marker),
            ],
        )
        assert result == 126
        assert not child_marker.exists()
        assert current.resolve() == old_target.resolve()
    finally:
        if contender is not None:
            assert contender.stdin is not None
            contender.stdin.write("\n")
            contender.stdin.flush()
            contender.wait(timeout=10)


def test_foreign_far_edge_launchd_job_keeps_new_pointer_and_label_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    mode = "active"
    bootouts = 0

    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        active = mode == "active"
        if arguments == ("service", "status", "--json"):
            payload = {
                "installed": True,
                "loaded": active,
                "supervisor_live": active,
                "supervisor_verified": active,
                "supervisor_service_managed": active,
                "build_current": active,
                "pair_healthy": active,
                "supervisor_pid": 5050 if active else None,
            }
            return subprocess.CompletedProcess(
                list(arguments),
                0 if active else 1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            rendered = (
                "Server: RUNNING\nGuardian: RUNNING\n"
                if active
                else "Server: STOPPED\nGuardian: STOPPED\n"
            )
            return subprocess.CompletedProcess(list(arguments), 0, rendered, "")
        if arguments == ("service", "stop"):
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    def bootout(_shared_home: Path) -> bool:
        nonlocal bootouts
        bootouts += 1
        if bootouts == 2:
            raise OSError("loaded fixed-label launchd job belongs to foreign paths")
        return False

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    monkeypatch.setattr(installer, "_bootout_owned_runtime_launchd_job", bootout)
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
    )

    assert result == 126
    assert bootouts == 2
    assert current.resolve() != old_target.resolve()
    handoff = installer._read_tools_handoff(shared_home)
    assert handoff is not None and handoff["state"] == "prepared"
    assert gate_state["disabled"] is True


def test_runtime_cutover_rollback_drains_new_before_restoring_old_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    mode = "native-active"
    events: list[str] = []

    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gcutover",
    )
    new_target = current.resolve()

    def fake_service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        active = mode.endswith("active")
        if arguments == ("service", "status", "--json"):
            payload = {
                "installed": True,
                "loaded": active,
                "supervisor_live": active,
                "supervisor_verified": active,
                "supervisor_service_managed": active,
                "build_current": active,
                "pair_healthy": active,
                "supervisor_pid": 9001 if active else None,
            }
            return subprocess.CompletedProcess(
                list(arguments),
                0 if active else 1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            output = (
                "Server: RUNNING\nGuardian: RUNNING\n"
                if active
                else "Server: STOPPED\nGuardian: STOPPED\n"
            )
            return subprocess.CompletedProcess(list(arguments), 0, output, "")
        if arguments == ("service", "stop"):
            events.append(f"stop:{current.resolve().name}")
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(installer.sys, "platform", "darwin")
    _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        fake_service_command,
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )

    backup = installer._capture_runtime_launch_agent_backup(shared_home)

    def restore_old(
        _shared_home: Path,
        observed_backup: installer._RuntimeLaunchAgentBackup,
    ) -> None:
        nonlocal mode
        assert observed_backup == backup
        events.append(f"install:{current.resolve().name}")
        mode = "old-active"

    monkeypatch.setattr(
        installer,
        "_activate_runtime_service_from_backup",
        restore_old,
    )

    def prove_rollback_fences(
        _backup: installer._RuntimePayloadBackup | None,
    ) -> None:
        assert (shared_home / "server" / "lifecycle.lock").is_dir()
        descriptor = os.open(shared_home / "server" / "supervisor.lock", os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                installer.fcntl.flock(
                    descriptor,
                    installer.fcntl.LOCK_EX | installer.fcntl.LOCK_NB,
                )
        finally:
            os.close(descriptor)
        events.append("rollback-fenced")

    monkeypatch.setattr(
        installer,
        "_restore_runtime_payload_backup",
        prove_rollback_fences,
    )

    with installer._tools_install_lease(
        current,
        operation="test-cutover-rollback",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        assert installer.rollback_runtime_install(
            shared_home,
            service_was_active=True,
            service_activation_attempted=True,
            launch_agent_backup=backup,
        )

    assert current.resolve() == old_target.resolve()
    assert events == [
        f"stop:{new_target.name}",
        "rollback-fenced",
        f"install:{old_target.name}",
    ]
    receipt = installer._read_tools_handoff(shared_home)
    assert receipt is not None
    assert receipt["state"] == "rolled-back"


def test_inactive_service_activation_failure_restores_exact_dormant_plist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    old_payload = plistlib.loads(plist.read_bytes())
    old_arguments = old_payload["ProgramArguments"]
    old_arguments[old_arguments.index("--port") + 1] = "41017"
    plist.write_bytes(plistlib.dumps(old_payload, sort_keys=False))
    old_bytes = plist.read_bytes()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        if arguments == ("service", "status", "--json"):
            payload = {
                "installed": True,
                "loaded": False,
                "supervisor_live": False,
                "supervisor_verified": False,
                "supervisor_service_managed": False,
                "build_current": False,
                "pair_healthy": False,
                "supervisor_pid": None,
            }
            return subprocess.CompletedProcess(
                list(arguments),
                1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            return subprocess.CompletedProcess(
                list(arguments),
                0,
                "Server: STOPPED\nGuardian: STOPPED\n",
                "",
            )
        if arguments[:2] == ("service", "install"):
            replacement = plistlib.loads(old_bytes)
            replacement_arguments = replacement["ProgramArguments"]
            replacement_arguments[replacement_arguments.index("--port") + 1] = "3024"
            plist.write_bytes(plistlib.dumps(replacement, sort_keys=True))
            return subprocess.CompletedProcess(
                list(arguments),
                44,
                "",
                "activation failed",
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="ensure",
    )

    assert result == 126
    assert current.resolve() == old_target.resolve()
    assert plist.read_bytes() == old_bytes
    assert gate_state["disabled"] is False


def test_successful_explicit_service_install_repairs_retained_disabled_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    active = False
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    gate_state["disabled"] = True
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            launcher,
            healthy if active else stopped,
            "running" if active else "stopped",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal active
        assert arguments == (
            "service",
            "install",
            "--host",
            "127.0.0.1",
            "--port",
            "3024",
        )
        assert gate_state["disabled"] is False
        active = True
        return subprocess.CompletedProcess(list(arguments), 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="ensure",
    )

    assert result == 0
    assert active is True
    assert current.resolve() != old_target.resolve()
    handoff = installer._read_tools_handoff(shared_home)
    assert handoff is not None and handoff["state"] == "complete"
    assert gate_state["disabled"] is False


def test_reclaimable_degraded_service_status_is_known_not_transition() -> None:
    """Supervisor live + pair down is drainable, not an in-progress race."""
    degraded = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=False,
        supervisor_pid=4326,
    )
    mid_start = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )

    assert not degraded.healthy
    assert not degraded.quiescent
    assert degraded.reclaimable
    assert degraded.needs_drain

    payload = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": False,
        "supervisor_pid": 4326,
    }
    decoded = installer._decode_runtime_service_status(
        subprocess.CompletedProcess(
            ["service", "status", "--json"],
            1,
            json.dumps(payload) + "\n",
            "",
        )
    )
    assert decoded.reclaimable
    assert decoded.needs_drain

    with pytest.raises(installer._RuntimeServiceTransition):
        installer._decode_runtime_service_status(
            subprocess.CompletedProcess(
                ["service", "status", "--json"],
                1,
                json.dumps(
                    {
                        "installed": True,
                        "loaded": True,
                        "supervisor_live": False,
                        "supervisor_verified": False,
                        "supervisor_service_managed": False,
                        "build_current": False,
                        "pair_healthy": False,
                        "supervisor_pid": None,
                    }
                )
                + "\n",
                "",
            )
        )
    assert not mid_start.reclaimable
    assert not mid_start.needs_drain


def test_reclaimable_orphaned_guardian_is_drainable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server down + orphan guardian is a stable degraded pair, not an unknown identity."""
    launcher = tmp_path / "vibecrafted"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    payload = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": False,
        "supervisor_pid": 949,
    }
    calls = 0

    monkeypatch.setattr(
        installer,
        "_runtime_service_launcher",
        lambda _shared_home: launcher,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if arguments == ("service", "status", "--json"):
            return subprocess.CompletedProcess(
                list(arguments),
                1,
                json.dumps(payload) + "\n",
                "",
            )
        if arguments == ("status",):
            return subprocess.CompletedProcess(
                list(arguments),
                1,
                "Supervision: LAUNCHD (installed=yes, loaded=yes, supervisor PID 949)\n"
                "Server: STOPPED\n"
                "Guardian: ORPHANED (PID 95934 is live without a healthy managed server)\n",
                "",
            )
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)

    snapshot = installer._runtime_service_snapshot(tmp_path)

    assert snapshot is not None
    assert snapshot[1].reclaimable
    assert snapshot[2] == "orphaned"
    assert calls == 2


def test_install_drains_reclaimable_degraded_supervisor_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Next install heals supervisor-live/pair-down without manual isolated ops."""
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)

    degraded = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=False,
        supervisor_pid=4326,
    )
    quiescent = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=9191,
    )
    mode = "degraded"
    events: list[str] = []

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: events.append("bootout") or True,
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def snapshot(_shared_home: Path):
        if mode == "degraded":
            return launcher, degraded, "orphaned"
        if mode == "stopped":
            return launcher, quiescent, "stopped"
        return launcher, healthy, "running"

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        if arguments == ("service", "stop"):
            events.append("service stop")
            mode = "stopped"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        if arguments[:2] == ("service", "install"):
            events.append("service install")
            mode = "healthy"
            return subprocess.CompletedProcess(list(arguments), 0, "", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="ensure",
    )

    assert result == 0
    assert "service stop" in events
    assert "service install" in events
    assert current.resolve() != old_target.resolve()
    handoff = installer._read_tools_handoff(shared_home)
    assert handoff is not None and handoff["state"] == "complete"
    assert gate_state["disabled"] is False


def test_service_activation_waits_for_exact_managed_pair_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current.parent.mkdir(parents=True)
    _write_runtime_launch_agent(home, shared_home, launcher)
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    observations: list[tuple[Path, installer._RuntimeServiceStatus, str] | OSError] = [
        (launcher, stopped, "stopped"),
        installer._RuntimeServiceTransition("supervisor is starting"),
        (launcher, stopped, "stopped"),
        (launcher, healthy, "running"),
    ]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    def snapshot(_shared_home: Path):
        observation = observations.pop(0)
        if isinstance(observation, OSError):
            raise observation
        return observation

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        lambda _launcher, _shared_home, *arguments: subprocess.CompletedProcess(
            list(arguments),
            0,
            "",
            "",
        ),
    )
    monkeypatch.setattr(installer.time, "sleep", lambda _seconds: None)

    with (
        installer._tools_install_lease(
            current,
            operation="test-bounded-service-activation",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
    ):
        installer.activate_runtime_service_after_install(shared_home)

    assert observations == []


def test_service_activation_does_not_retry_invalid_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current.parent.mkdir(parents=True)
    _write_runtime_launch_agent(home, shared_home, launcher)
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    observations: list[tuple[Path, installer._RuntimeServiceStatus, str] | OSError] = [
        (launcher, stopped, "stopped"),
        OSError("invalid service identity"),
        (launcher, stopped, "stopped"),
    ]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    def snapshot(_shared_home: Path):
        observation = observations.pop(0)
        if isinstance(observation, OSError):
            raise observation
        return observation

    monkeypatch.setattr(installer, "_runtime_service_snapshot", snapshot)
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        lambda _launcher, _shared_home, *arguments: subprocess.CompletedProcess(
            list(arguments),
            0,
            "",
            "",
        ),
    )

    with (
        installer._tools_install_lease(
            current,
            operation="test-invalid-service-activation",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
        pytest.raises(
            OSError,
            match="invalid service identity",
        ),
    ):
        installer.activate_runtime_service_after_install(shared_home)

    assert len(observations) == 1


def test_healthy_runtime_snapshot_uses_one_correlated_service_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = tmp_path / "vibecrafted"
    calls: list[tuple[str, ...]] = []
    payload = {
        "installed": True,
        "loaded": True,
        "supervisor_live": True,
        "supervisor_verified": True,
        "supervisor_service_managed": True,
        "build_current": True,
        "pair_healthy": True,
        "supervisor_pid": 8181,
    }
    monkeypatch.setattr(
        installer,
        "_runtime_service_launcher",
        lambda _shared_home: launcher,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess(
            list(arguments),
            0,
            json.dumps(payload) + "\n",
            "",
        )

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)

    snapshot = installer._runtime_service_snapshot(tmp_path / ".vibecrafted")

    assert snapshot is not None
    assert snapshot[1].healthy is True
    assert snapshot[2] == "running"
    assert calls == [("service", "status", "--json")]


def test_runtime_service_probe_honors_transaction_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    launcher = tmp_path / "slow-launcher"
    _write_executable(
        launcher,
        f"#!{sys.executable}\nimport time\ntime.sleep(5)\n",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))

    with (
        installer._tools_install_lease(
            current,
            operation="test-service-probe-deadline",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
    ):
        token = installer._RUNTIME_SERVICE_COMMAND_DEADLINE.set(time.monotonic() + 0.1)
        started = time.monotonic()
        try:
            with pytest.raises(subprocess.TimeoutExpired):
                installer._run_runtime_service_command(
                    launcher,
                    shared_home,
                    "service",
                    "status",
                    "--json",
                )
        finally:
            installer._RUNTIME_SERVICE_COMMAND_DEADLINE.reset(token)

    assert time.monotonic() - started < 1


@pytest.mark.parametrize(
    "pair_lines",
    (
        (
            "Server: PID-MISMATCH (43426 is live but identity is unverified)\n"
            "Guardian: STOPPED\n"
        ),
        (
            "Server: RUNNING (PID 92141, listening on http://100.82.232.70:3025)\n"
            "Guardian: STOPPED\n"
        ),
        (
            "Server: RUNNING (PID 45721, listening on http://100.82.232.70:3025)\n"
            "Guardian: PID-MISMATCH (46050 is live but identity is unverified)\n"
        ),
    ),
)
def test_partial_runtime_pair_retries_only_during_bounded_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair_lines: str,
) -> None:
    output = (
        "Supervision: LAUNCHD (installed=yes, loaded=yes, supervisor PID 43242)\n"
        + pair_lines
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        lambda _launcher, _shared_home, *arguments: subprocess.CompletedProcess(
            list(arguments),
            0,
            output,
            "",
        ),
    )

    with pytest.raises(OSError, match="refusing install handoff"):
        installer._runtime_service_pair_state(tmp_path / "launcher", tmp_path)

    token = installer._RUNTIME_SERVICE_COMMAND_DEADLINE.set(time.monotonic() + 1)
    try:
        with pytest.raises(
            installer._RuntimeServiceTransition,
            match="still converging during bounded activation",
        ):
            installer._runtime_service_pair_state(tmp_path / "launcher", tmp_path)
    finally:
        installer._RUNTIME_SERVICE_COMMAND_DEADLINE.reset(token)


def test_activation_rejects_healthy_service_with_stale_endpoint_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current.parent.mkdir(parents=True)
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    payload = plistlib.loads(plist.read_bytes())
    arguments = payload["ProgramArguments"]
    arguments[arguments.index("--port") + 1] = "41017"
    plist.write_bytes(plistlib.dumps(payload, sort_keys=False))
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (launcher, healthy, "running"),
    )

    with (
        installer._tools_install_lease(
            current,
            operation="test-stale-activation-config",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
        pytest.raises(OSError, match="stale endpoint"),
    ):
        installer.activate_runtime_service_after_install(shared_home)


def test_activation_accepts_production_plist_without_legacy_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    current.parent.mkdir(parents=True)
    _write_runtime_launch_agent(home, shared_home, launcher)
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (launcher, healthy, "running"),
    )
    monkeypatch.setattr(
        installer,
        "_run_runtime_service_command",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("healthy production service must not be reinstalled")
        ),
    )

    with (
        installer._tools_install_lease(
            current,
            operation="test-production-activation-config",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
    ):
        installer.activate_runtime_service_after_install(
            shared_home,
            service_arguments=(
                "--host",
                "127.0.0.1",
                "--port",
                "3024",
                "--interval",
                "2.75",
            ),
        )


def test_operator_interrupt_during_activation_rolls_back_full_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    server_bin = home / ".local" / "bin" / "vc-server"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    old_deck = old_target / "scripts" / "vibecrafted"
    old_deck.write_bytes(
        old_deck.read_bytes().replace(
            installer._SERVICE_LIFECYCLE_LOCK_MARKER + b"\n",
            b"",
        )
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    original_plist = plist.read_bytes()
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (launcher, stopped, "stopped"),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    lifecycle_fence_depth = 0
    real_lifecycle_fence = installer._runtime_lifecycle_handoff_fence

    @contextmanager
    def tracked_lifecycle_fence(
        _shared_home: Path,
        *,
        deck: Path | None,
    ):
        nonlocal lifecycle_fence_depth
        with real_lifecycle_fence(_shared_home, deck=deck) as guard:
            lifecycle_fence_depth += 1
            try:
                yield guard
            finally:
                lifecycle_fence_depth -= 1

    monkeypatch.setattr(
        installer,
        "_runtime_lifecycle_handoff_fence",
        tracked_lifecycle_fence,
    )
    publication_waits: list[tuple[datetime, object, int]] = []

    def observe_legacy_wait(
        *,
        published_at: datetime,
        classifier: object,
    ) -> None:
        if lifecycle_fence_depth == 0:
            assert classifier is installer._argv_is_service_mutator
            assert classifier(
                (
                    "/bin/bash",
                    "/tmp/vibecrafted",
                    "server",
                    "service",
                    "stop",
                )
            )
        publication_waits.append((published_at, classifier, lifecycle_fence_depth))

    monkeypatch.setattr(
        installer,
        "_wait_for_legacy_service_mutator_quiescence",
        observe_legacy_wait,
    )

    def interrupt_activation(
        _shared_home: Path,
        *,
        service_arguments: tuple[str, ...] = (),
    ) -> None:
        assert service_arguments == (
            "--host",
            "127.0.0.1",
            "--port",
            "3024",
        )
        replacement = plistlib.loads(plist.read_bytes())
        arguments = replacement["ProgramArguments"]
        arguments[arguments.index("--port") + 1] = "59999"
        plist.write_bytes(plistlib.dumps(replacement, sort_keys=False))
        raise KeyboardInterrupt

    monkeypatch.setattr(
        installer,
        "activate_runtime_service_after_install",
        interrupt_activation,
    )
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "source, shared, server_bin = map(Path, sys.argv[2:])\n"
        "v.refresh_current_tools(source, shared, mirror=True)\n"
        "server_bin.write_text('new server\\n', encoding='utf-8')\n"
    )

    with pytest.raises(KeyboardInterrupt):
        installer.run_with_tools_install_lease(
            shared_home,
            [
                sys.executable,
                "-c",
                child,
                str(REPO_ROOT / "scripts"),
                str(source),
                str(shared_home),
                str(server_bin),
            ],
            service_policy="ensure",
            runtime_payload_paths=(server_bin,),
        )

    assert current.resolve() == old_target.resolve()
    assert server_bin.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old-server\n"
    assert plist.read_bytes() == original_plist
    assert gate_state["disabled"] is False
    assert len(publication_waits) == 2
    assert all(boundary.tzinfo is not None for boundary, _, _ in publication_waits)
    assert publication_waits[0][1:] == (
        installer._argv_is_service_mutator,
        0,
    )
    assert publication_waits[1][1:] == (
        installer._argv_is_legacy_service_action_mutator,
        1,
    )
    assert not list((shared_home / "install-transactions").glob("runtime-payload-*"))


def test_runtime_cutover_uncertain_activation_keeps_new_pointer_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"

    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+guncertain",
    )
    new_target = current.resolve()
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (_ for _ in ()).throw(
            OSError("active service identity is uncertain")
        ),
    )

    with installer._tools_install_lease(
        current,
        operation="test-uncertain-activation-rollback",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        with pytest.raises(OSError, match="identity is uncertain"):
            installer.rollback_runtime_install(
                shared_home,
                service_was_active=True,
                service_activation_attempted=True,
            )

    assert current.resolve() == new_target
    assert current.resolve() != old_target.resolve()
    receipt = installer._read_tools_handoff(shared_home)
    assert receipt is not None
    assert receipt["state"] == "prepared"
    assert gate_state["disabled"] is True


def test_failed_install_child_restores_server_payload_and_runtime_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    source = tmp_path / "source"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    server_bin = home / ".local" / "bin" / "vc-server"
    compat_bin = home / ".local" / "bin" / "vibecrafted-server-web"
    site = home / ".local" / "share" / "vibecrafted" / "server" / "site"

    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    site.mkdir(parents=True)
    (site / "index.html").write_text("old site\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "linux")

    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "source, shared, server_bin, compat_bin, site = map(Path, sys.argv[2:])\n"
        "v.refresh_current_tools(source, shared, mirror=True)\n"
        "server_bin.write_text('new server\\n', encoding='utf-8')\n"
        "compat_bin.write_text('new compat\\n', encoding='utf-8')\n"
        "(site / 'index.html').write_text('new site\\n', encoding='utf-8')\n"
        "(site / 'new.js').write_text('new asset\\n', encoding='utf-8')\n"
        "raise SystemExit(42)\n"
    )
    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
            str(server_bin),
            str(compat_bin),
            str(site),
        ],
        runtime_payload_paths=(server_bin, compat_bin, site),
    )

    assert result == 42
    assert current.resolve() == old_target.resolve()
    assert server_bin.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old-server\n"
    assert not compat_bin.exists()
    assert (site / "index.html").read_text(encoding="utf-8") == "old site\n"
    assert not (site / "new.js").exists()
    transaction_root = shared_home / "install-transactions"
    assert not list(transaction_root.glob("runtime-payload-*"))


def test_failed_payload_only_child_restores_server_payload_without_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    server_bin = home / ".local" / "bin" / "vc-server"
    site = home / ".local" / "share" / "vibecrafted" / "server" / "site"
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    site.mkdir(parents=True)
    (site / "index.html").write_text("old site\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "linux")
    child = (
        "from pathlib import Path; import sys\n"
        "server_bin, site = map(Path, sys.argv[1:])\n"
        "server_bin.write_text('new server\\n', encoding='utf-8')\n"
        "(site / 'index.html').write_text('new site\\n', encoding='utf-8')\n"
        "(site / 'new.js').write_text('new asset\\n', encoding='utf-8')\n"
        "raise SystemExit(42)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [sys.executable, "-c", child, str(server_bin), str(site)],
        runtime_payload_paths=(server_bin, site),
        require_tools_handoff=False,
    )

    assert result == 42
    assert current.resolve() == old_target.resolve()
    assert installer._read_tools_handoff(shared_home) is None
    assert server_bin.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old-server\n"
    assert (site / "index.html").read_text(encoding="utf-8") == "old site\n"
    assert not (site / "new.js").exists()


def test_operator_interrupt_after_child_mutation_rolls_back_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    server_bin = home / ".local" / "bin" / "vc-server"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "linux")

    def interrupt_after_child(
        argv: list[str],
        *,
        descriptor: int,
        environment: dict[str, str],
        lifecycle_guard: installer._RuntimeLifecycleFenceGuard,
    ) -> int:
        result = subprocess.run(
            argv,
            check=False,
            pass_fds=(descriptor,),
            env=environment,
        )
        assert result.returncode == 0
        lifecycle_guard.assert_owned()
        raise KeyboardInterrupt

    monkeypatch.setattr(
        installer,
        "_run_install_child_with_lifecycle_guard",
        interrupt_after_child,
    )
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "source, shared, server_bin = map(Path, sys.argv[2:])\n"
        "v.refresh_current_tools(source, shared, mirror=True)\n"
        "server_bin.write_text('new server\\n', encoding='utf-8')\n"
    )

    with pytest.raises(KeyboardInterrupt):
        installer.run_with_tools_install_lease(
            shared_home,
            [
                sys.executable,
                "-c",
                child,
                str(REPO_ROOT / "scripts"),
                str(source),
                str(shared_home),
                str(server_bin),
            ],
            runtime_payload_paths=(server_bin,),
        )

    assert current.resolve() == old_target.resolve()
    assert server_bin.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old-server\n"
    assert not list((shared_home / "install-transactions").glob("runtime-payload-*"))


@pytest.mark.parametrize("failure_stage", ["publication", "manual-drain"])
def test_prelock_cutover_failure_rolls_back_but_keeps_service_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    server_bin = home / ".local" / "bin" / "vc-server"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    old_deck = old_target / "scripts" / "vibecrafted"
    old_deck.write_bytes(
        old_deck.read_bytes().replace(
            installer._SERVICE_LIFECYCLE_LOCK_MARKER + b"\n",
            b"",
        )
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    plist = _write_runtime_launch_agent(home, shared_home, launcher)
    original_plist = plist.read_bytes()
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    mode = "active"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            launcher,
            healthy if mode == "active" else stopped,
            "running" if mode == "active" else "stopped",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        assert arguments == ("service", "stop")
        mode = "stopped"
        return subprocess.CompletedProcess(list(arguments), 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    monkeypatch.setattr(
        installer,
        "_activate_runtime_service_from_backup",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("pre-lock rollback must not reactivate the old service")
        ),
    )

    def interrupt_after_child(
        argv: list[str],
        *,
        descriptor: int,
        environment: dict[str, str],
        lifecycle_guard: installer._RuntimeLifecycleFenceGuard,
    ) -> int:
        result = subprocess.run(
            argv,
            check=False,
            pass_fds=(descriptor,),
            env=environment,
        )
        assert result.returncode == 0
        lifecycle_guard.assert_owned()
        raise KeyboardInterrupt

    if failure_stage == "publication":
        monkeypatch.setattr(
            installer,
            "_run_install_child_with_lifecycle_guard",
            interrupt_after_child,
        )
    else:

        def fail_pre_fence_drain(
            *,
            published_at: datetime,
            classifier: object,
        ) -> None:
            assert published_at.tzinfo is not None
            assert classifier is installer._argv_is_service_mutator
            raise KeyboardInterrupt

        monkeypatch.setattr(
            installer,
            "_wait_for_legacy_service_mutator_quiescence",
            fail_pre_fence_drain,
        )
    child = (
        "from pathlib import Path; import plistlib, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "source, shared, server_bin, plist = map(Path, sys.argv[2:])\n"
        "v.refresh_current_tools(source, shared, mirror=True)\n"
        "server_bin.write_text('new server\\n', encoding='utf-8')\n"
        "payload = plistlib.loads(plist.read_bytes())\n"
        "arguments = payload['ProgramArguments']\n"
        "arguments[arguments.index('--port') + 1] = '59999'\n"
        "plist.write_bytes(plistlib.dumps(payload, sort_keys=False))\n"
    )

    with pytest.raises(KeyboardInterrupt):
        installer.run_with_tools_install_lease(
            shared_home,
            [
                sys.executable,
                "-c",
                child,
                str(REPO_ROOT / "scripts"),
                str(source),
                str(shared_home),
                str(server_bin),
                str(plist),
            ],
            runtime_payload_paths=(server_bin,),
        )

    assert current.resolve() == old_target.resolve()
    assert server_bin.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old-server\n"
    assert plist.read_bytes() == original_plist
    assert mode == "stopped"
    assert gate_state["disabled"] is True


def test_operator_interrupt_during_failed_child_service_recovery_keeps_gate_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    _write_runtime_launch_agent(home, shared_home, launcher)
    healthy = installer._RuntimeServiceStatus(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=8181,
    )
    stopped = installer._RuntimeServiceStatus(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    mode = "active"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            launcher,
            healthy if mode == "active" else stopped,
            "running" if mode == "active" else "stopped",
        ),
    )

    def service_command(
        _launcher: Path,
        _shared_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal mode
        assert arguments == ("service", "stop")
        mode = "stopped"
        return subprocess.CompletedProcess(list(arguments), 0, "", "")

    monkeypatch.setattr(installer, "_run_runtime_service_command", service_command)
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    monkeypatch.setattr(
        installer,
        "_assert_runtime_launchd_job_owned",
        lambda _shared_home: True,
    )
    monkeypatch.setattr(
        installer,
        "_activate_runtime_service_from_backup",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    with pytest.raises(KeyboardInterrupt):
        installer.run_with_tools_install_lease(
            shared_home,
            [sys.executable, "-c", "raise SystemExit(42)"],
            require_tools_handoff=False,
        )

    assert mode == "stopped"
    assert gate_state["disabled"] is True


def test_runtime_payload_backup_refuses_symlinked_install_target(
    tmp_path: Path,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    target = tmp_path / "real-server"
    target.write_text("do not overwrite\n", encoding="utf-8")
    linked = tmp_path / "vc-server"
    linked.symlink_to(target)

    with pytest.raises(OSError, match="traverses a symlink"):
        installer._capture_runtime_payload_backup(shared_home, (linked,))

    assert target.read_text(encoding="utf-8") == "do not overwrite\n"


def test_runtime_payload_restore_refuses_replaced_ancestor_symlink(
    tmp_path: Path,
) -> None:
    shared_home = tmp_path / "state" / ".vibecrafted"
    payload_root = tmp_path / "payload"
    payload = payload_root / "bin" / "vc-server"
    external_root = tmp_path / "external"
    external_payload = external_root / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    _write_executable(external_payload, "#!/bin/sh\nprintf external-new\n")
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    payload.write_text("new\n", encoding="utf-8")
    displaced_root = tmp_path / "payload-before-symlink"
    payload_root.rename(displaced_root)
    payload_root.symlink_to(external_root, target_is_directory=True)

    with pytest.raises(OSError, match="traverses a symlink"):
        installer._restore_runtime_payload_backup(backup)

    assert external_payload.read_text(encoding="utf-8") == (
        "#!/bin/sh\nprintf external-new\n"
    )
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_handles_file_directory_type_drift(
    tmp_path: Path,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload_root = tmp_path / "payload"
    original_file = payload_root / "vc-server"
    original_directory = payload_root / "site"
    _write_executable(original_file, "#!/bin/sh\nprintf old\n")
    original_directory.mkdir(parents=True)
    (original_directory / "index.html").write_text("old site\n", encoding="utf-8")
    backup = installer._capture_runtime_payload_backup(
        shared_home,
        (original_file, original_directory),
    )
    assert backup is not None
    original_file.unlink()
    original_file.mkdir()
    (original_file / "new").write_text("new directory\n", encoding="utf-8")
    shutil.rmtree(original_directory)
    original_directory.write_text("new file\n", encoding="utf-8")

    installer._restore_runtime_payload_backup(backup)

    assert original_file.is_file()
    assert original_file.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old\n"
    assert original_directory.is_dir()
    assert (original_directory / "index.html").read_text(
        encoding="utf-8"
    ) == "old site\n"
    assert not list(payload_root.glob(".*.restore-*"))
    assert not list(payload_root.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_reverses_partial_multi_entry_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first-old\n")
    _write_executable(second, "#!/bin/sh\nprintf second-old\n")
    backup = installer._capture_runtime_payload_backup(
        shared_home,
        (first, second),
    )
    assert backup is not None
    first.write_text("first-new\n", encoding="utf-8")
    second.write_text("second-new\n", encoding="utf-8")
    real_replace = installer.os.replace
    injected = False

    def fail_second_publish(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal injected
        if not injected and destination == second.name and ".restore-" in source:
            injected = True
            raise OSError("injected second payload publish failure")
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(installer.os, "replace", fail_second_publish)

    with pytest.raises(OSError, match="injected second payload publish failure"):
        installer._restore_runtime_payload_backup(backup)

    assert injected is True
    assert first.read_text(encoding="utf-8") == "first-new\n"
    assert second.read_text(encoding="utf-8") == "second-new\n"
    assert not list(first.parent.glob(".*.restore-*"))
    assert not list(first.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_preserves_regular_file_mtime(
    tmp_path: Path,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload = tmp_path / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    os.utime(payload, ns=(1_700_000_000_123_456_789, 1_700_000_000_987_654_321))
    original_mtime = payload.stat().st_mtime_ns
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    payload.write_text("new\n", encoding="utf-8")

    installer._restore_runtime_payload_backup(backup)

    assert payload.read_text(encoding="utf-8") == "#!/bin/sh\nprintf old\n"
    assert payload.stat().st_mtime_ns == original_mtime
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_capture_interrupt_removes_partial_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first\n")
    _write_executable(second, "#!/bin/sh\nprintf second\n")
    real_copy_node = installer._runtime_payload_copy_node
    calls = 0

    def interrupt_second_copy(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return real_copy_node(*args, **kwargs)

    monkeypatch.setattr(
        installer,
        "_runtime_payload_copy_node",
        interrupt_second_copy,
    )

    with pytest.raises(KeyboardInterrupt):
        installer._capture_runtime_payload_backup(
            shared_home,
            (first, second),
        )

    assert not list((shared_home / "install-transactions").glob("runtime-payload-*"))


def test_runtime_payload_restore_anchors_parent_against_mid_replace_symlink_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / "state" / ".vibecrafted"
    payload_root = tmp_path / "payload"
    payload = payload_root / "bin" / "vc-server"
    external_root = tmp_path / "external"
    external_payload = external_root / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    _write_executable(external_payload, "#!/bin/sh\nprintf external\n")
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    payload.write_text("new\n", encoding="utf-8")
    detached_root = tmp_path / "payload-detached"
    real_replace = installer.os.replace
    attacked = False

    def swap_parent_before_replace(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal attacked
        if not attacked and source == payload.name and ".displaced-" in destination:
            payload_root.rename(detached_root)
            payload_root.symlink_to(external_root, target_is_directory=True)
            attacked = True
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(installer.os, "replace", swap_parent_before_replace)

    with pytest.raises(OSError, match="parent identity changed"):
        installer._restore_runtime_payload_backup(backup)

    assert attacked is True
    assert external_payload.read_text(encoding="utf-8") == (
        "#!/bin/sh\nprintf external\n"
    )
    detached_payload = detached_root / "bin" / "vc-server"
    assert detached_payload.read_text(encoding="utf-8") == "new\n"
    assert not list(detached_payload.parent.glob(".*.restore-*"))
    assert not list(detached_payload.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_rejects_backup_content_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload = tmp_path / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    entry = backup.entries[0]
    assert entry.backup is not None
    backup_contents = entry.backup.read_bytes()
    backup_metadata = entry.backup.stat()
    payload.write_text("new\n", encoding="utf-8")
    real_copy_node = installer._runtime_payload_copy_node
    attacked = False

    def substitute_backup_before_stage(
        source_fd: int,
        kind: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal attacked
        if not attacked and ".restore-" in destination_name:
            entry.backup.write_text("substituted-not-old\n", encoding="utf-8")
            attacked = True
        real_copy_node(
            source_fd,
            kind,
            destination_parent_fd,
            destination_name,
        )

    monkeypatch.setattr(
        installer,
        "_runtime_payload_copy_node",
        substitute_backup_before_stage,
    )

    with pytest.raises(OSError, match="staged digest changed"):
        installer._restore_runtime_payload_backup(backup)

    assert attacked is True
    assert payload.read_text(encoding="utf-8") == "new\n"
    assert not list(payload.parent.glob(".*.restore-*"))
    assert not list(payload.parent.glob(".*.displaced-*"))
    entry.backup.write_bytes(backup_contents)
    entry.backup.chmod(stat.S_IMODE(backup_metadata.st_mode))
    os.utime(
        entry.backup,
        ns=(backup_metadata.st_atime_ns, backup_metadata.st_mtime_ns),
    )
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_capture_rejects_write_after_entry_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload = tmp_path / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf before\n")
    real_digest = installer._runtime_payload_digest_fd
    injected = False

    def write_before_first_digest(descriptor: int, kind: str) -> str:
        nonlocal injected
        if not injected:
            payload.write_text("during-capture\n", encoding="utf-8")
            injected = True
        return real_digest(descriptor, kind)

    monkeypatch.setattr(
        installer,
        "_runtime_payload_digest_fd",
        write_before_first_digest,
    )

    with pytest.raises(OSError, match="changed before capture"):
        installer._capture_runtime_payload_backup(shared_home, (payload,))

    assert injected is True
    assert not list((shared_home / "install-transactions").glob("runtime-payload-*"))


def test_runtime_payload_capture_collectively_seals_all_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first-precall\n")
    _write_executable(second, "#!/bin/sh\nprintf second-precall\n")
    real_copy = installer._runtime_payload_copy_node
    second_changed = False

    def change_second_after_first_backup_copy(
        source_fd: int,
        kind: str,
        destination_parent_fd: int,
        destination_name: str,
    ) -> None:
        nonlocal second_changed
        real_copy(
            source_fd,
            kind,
            destination_parent_fd,
            destination_name,
        )
        if not second_changed and destination_name == f"0-{first.name}":
            second.write_text("second-during-capture\n", encoding="utf-8")
            second_changed = True

    monkeypatch.setattr(
        installer,
        "_runtime_payload_copy_node",
        change_second_after_first_backup_copy,
    )

    with pytest.raises(OSError, match="changed during capture"):
        installer._capture_runtime_payload_backup(
            shared_home,
            (first, second),
        )

    assert second_changed is True
    assert not list((shared_home / "install-transactions").glob("runtime-payload-*"))


def test_runtime_payload_restore_rejects_staged_change_at_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload = tmp_path / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    payload.write_text("new\n", encoding="utf-8")
    real_replace = installer.os.replace
    injected = False

    def change_stage_inside_publish(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal injected
        if not injected and ".restore-" in source and destination == payload.name:
            (payload.parent / source).write_text(
                "changed-after-digest\n",
                encoding="utf-8",
            )
            injected = True
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(installer.os, "replace", change_stage_inside_publish)

    with pytest.raises(OSError, match="retained runtime payload digest changed"):
        installer._restore_runtime_payload_backup(backup)

    assert injected is True
    assert payload.read_text(encoding="utf-8") == "new\n"
    assert not list(payload.parent.glob(".*.restore-*"))
    assert not list(payload.parent.glob(".*.precall-*"))
    assert not list(payload.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_uses_snapshot_after_displaced_open_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first-old\n")
    _write_executable(second, "#!/bin/sh\nprintf second-old\n")
    backup = installer._capture_runtime_payload_backup(
        shared_home,
        (first, second),
    )
    assert backup is not None
    first.write_text("first-new\n", encoding="utf-8")
    second.write_text("second-new\n", encoding="utf-8")
    held_writer = os.open(first, os.O_RDWR)
    real_replace = installer.os.replace
    first_changed = False
    second_failed = False

    def change_displaced_then_fail_second(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal first_changed, second_failed
        if not first_changed and ".restore-" in source and destination == first.name:
            os.lseek(held_writer, 0, os.SEEK_SET)
            os.write(held_writer, b"changed-through-open-writer\n")
            os.ftruncate(held_writer, len(b"changed-through-open-writer\n"))
            first_changed = True
        if not second_failed and ".restore-" in source and destination == second.name:
            second_failed = True
            raise OSError("injected second publish failure")
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        installer.os,
        "replace",
        change_displaced_then_fail_second,
    )
    try:
        with pytest.raises(OSError, match="injected second publish failure"):
            installer._restore_runtime_payload_backup(backup)
    finally:
        os.close(held_writer)

    assert first_changed is True
    assert second_failed is True
    assert first.read_text(encoding="utf-8") == "first-new\n"
    assert second.read_text(encoding="utf-8") == "second-new\n"
    assert not list(first.parent.glob(".*.restore-*"))
    assert not list(first.parent.glob(".*.precall-*"))
    assert not list(first.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_snapshots_all_entries_before_first_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first-old\n")
    _write_executable(second, "#!/bin/sh\nprintf second-old\n")
    backup = installer._capture_runtime_payload_backup(
        shared_home,
        (first, second),
    )
    assert backup is not None
    first.write_text("first-precall\n", encoding="utf-8")
    second.write_text("second-precall\n", encoding="utf-8")
    real_replace = installer.os.replace
    second_changed = False

    def change_second_after_first_publish(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal second_changed
        real_replace(source, destination, *args, **kwargs)
        if not second_changed and ".restore-" in source and destination == first.name:
            second.write_text("second-concurrent\n", encoding="utf-8")
            second_changed = True

    monkeypatch.setattr(
        installer.os,
        "replace",
        change_second_after_first_publish,
    )

    with pytest.raises(OSError, match="changed during publication"):
        installer._restore_runtime_payload_backup(backup)

    assert second_changed is True
    assert first.read_text(encoding="utf-8") == "first-precall\n"
    assert second.read_text(encoding="utf-8") == "second-precall\n"
    assert not list(first.parent.glob(".*.restore-*"))
    assert not list(first.parent.glob(".*.precall-*"))
    assert not list(first.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_restore_collectively_seals_all_published_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    first = tmp_path / "bin" / "first"
    second = tmp_path / "bin" / "second"
    _write_executable(first, "#!/bin/sh\nprintf first-old\n")
    _write_executable(second, "#!/bin/sh\nprintf second-old\n")
    backup = installer._capture_runtime_payload_backup(
        shared_home,
        (first, second),
    )
    assert backup is not None
    first.write_text("first-precall\n", encoding="utf-8")
    second.write_text("second-precall\n", encoding="utf-8")
    real_assert = installer._runtime_payload_assert_retained_entry
    first_changed = False

    def change_first_after_its_post_publish_check(
        parent_fd: int,
        name: str,
        retained_fd: int,
        kind: str,
        expected_digest: str,
    ) -> None:
        nonlocal first_changed
        real_assert(
            parent_fd,
            name,
            retained_fd,
            kind,
            expected_digest,
        )
        if not first_changed and name == first.name:
            first.write_text("changed-after-entry-seal\n", encoding="utf-8")
            first_changed = True

    monkeypatch.setattr(
        installer,
        "_runtime_payload_assert_retained_entry",
        change_first_after_its_post_publish_check,
    )

    with pytest.raises(OSError, match="retained runtime payload digest changed"):
        installer._restore_runtime_payload_backup(backup)

    assert first_changed is True
    assert first.read_text(encoding="utf-8") == "first-precall\n"
    assert second.read_text(encoding="utf-8") == "second-precall\n"
    assert not list(first.parent.glob(".*.restore-*"))
    assert not list(first.parent.glob(".*.precall-*"))
    assert not list(first.parent.glob(".*.displaced-*"))
    installer._discard_runtime_payload_backup(backup)


def test_runtime_payload_discard_preserves_replacement_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_home = tmp_path / ".vibecrafted"
    payload = tmp_path / "bin" / "vc-server"
    _write_executable(payload, "#!/bin/sh\nprintf old\n")
    backup = installer._capture_runtime_payload_backup(shared_home, (payload,))
    assert backup is not None
    detached = backup.root.parent / f"{backup.root.name}-detached"
    real_replace = installer.os.replace
    injected = False

    def replace_root_before_quarantine(
        source: str,
        destination: str,
        *args,
        **kwargs,
    ) -> None:
        nonlocal injected
        if not injected and source == backup.root.name and ".discard-" in destination:
            backup.root.rename(detached)
            backup.root.mkdir()
            (backup.root / "replacement.txt").write_text(
                "preserve me\n",
                encoding="utf-8",
            )
            injected = True
        real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(
        installer.os,
        "replace",
        replace_root_before_quarantine,
    )

    with pytest.raises(OSError, match="changed during discard"):
        installer._discard_runtime_payload_backup(backup)

    assert injected is True
    assert (backup.root / "replacement.txt").read_text(encoding="utf-8") == (
        "preserve me\n"
    )
    assert detached.is_dir()


def test_non_darwin_ensure_service_rolls_back_without_macos_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    source = tmp_path / "source"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(
        installer,
        "_complete_current_tools_handoff_locked",
        lambda _shared_home: (_ for _ in ()).throw(OSError("seal failed")),
    )
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (_ for _ in ()).throw(
            AssertionError("non-Darwin rollback must not inspect launchd")
        ),
    )
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
        service_policy="ensure",
    )

    assert result == 126
    assert current.resolve() == old_target.resolve()


def test_stale_complete_handoff_cannot_suppress_payload_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    server_bin = home / ".local" / "bin" / "vc-server"
    # Installer path helpers resolve the canonical tools root from the
    # environment, so fence it before creating the synthetic receipt.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(server_bin, "#!/bin/sh\nprintf old-server\n")
    installer._atomic_json_file(
        installer._tools_handoff_file(shared_home),
        {
            "schema": installer._TOOLS_HANDOFF_SCHEMA,
            "state": "complete",
            "old_target": "",
            "new_target": str(old_target),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(installer.sys, "platform", "linux")
    child = (
        "from pathlib import Path; import sys\n"
        "Path(sys.argv[1]).write_text('new server\\n', encoding='utf-8')\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [sys.executable, "-c", child, str(server_bin)],
        runtime_payload_paths=(server_bin,),
    )

    assert result == 126
    assert current.resolve() == old_target.resolve()
    assert server_bin.read_text(encoding="utf-8") == ("#!/bin/sh\nprintf old-server\n")


def test_lifecycle_fence_helper_exit_after_ready_is_fatal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    deck = tmp_path / "legacy-deck"
    _write_executable(
        deck,
        "#!/usr/bin/env bash\n"
        "_acquire_server_lifecycle_lock() {\n"
        "  _SERVER_LIFECYCLE_LOCK_PID=$$\n"
        "  _SERVER_LIFECYCLE_LOCK_NONCE="
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "}\n"
        "_release_server_lifecycle_lock() { :; }\n",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tmp_path / "tools"))
    monkeypatch.setattr(installer.sys, "platform", "darwin")

    with (
        pytest.raises(OSError, match="before explicit release"),
        installer._runtime_lifecycle_handoff_fence(
            shared_home,
            deck=deck,
        ) as guard,
    ):
        assert guard.process is not None
        assert guard.process.stdin is not None
        guard.process.stdin.close()
        guard.process.wait(timeout=10)
        guard.assert_owned()


@pytest.mark.parametrize("receipt_probe_failure", [False, True])
def test_fence_release_failure_after_seal_keeps_committed_generation_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    receipt_probe_failure: bool,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    launcher = home / ".local" / "bin" / "vibecrafted"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    _write_valid_runtime_generation(old_target)
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    _write_executable(launcher, "#!/usr/bin/env bash\nexit 0\n")
    stopped = installer._RuntimeServiceStatus(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher.parent))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    gate_state = _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (launcher, stopped, "stopped"),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    fence_entries = 0

    @contextmanager
    def fail_second_fence_release(
        _shared_home: Path,
        *,
        deck: Path | None,
    ):
        nonlocal fence_entries
        assert deck == old_target / "scripts" / "vibecrafted"
        fence_entries += 1
        yield installer._RuntimeLifecycleFenceGuard(None)
        if fence_entries == 2:
            raise OSError("lifecycle fence release failed after seal")

    monkeypatch.setattr(
        installer,
        "_runtime_lifecycle_handoff_fence",
        fail_second_fence_release,
    )
    if receipt_probe_failure:
        monkeypatch.setattr(
            installer,
            "_tools_handoff_is_complete_current",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("receipt probe failed")
            ),
        )
    child = (
        "from pathlib import Path; import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import vetcoders_install as v\n"
        "v.refresh_current_tools(Path(sys.argv[2]), Path(sys.argv[3]), mirror=True)\n"
    )

    result = installer.run_with_tools_install_lease(
        shared_home,
        [
            sys.executable,
            "-c",
            child,
            str(REPO_ROOT / "scripts"),
            str(source),
            str(shared_home),
        ],
    )

    assert result == 126
    assert current.resolve() != old_target.resolve()
    handoff = installer._read_tools_handoff(shared_home)
    assert handoff is not None and handoff["state"] == "complete"
    assert gate_state["disabled"] is True


def test_server_service_mutations_serialize_through_lifecycle_lock(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    log = tmp_path / "service-mutations.log"
    deck = tmp_path / "vibecrafted"
    source = (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8")
    harness = r"""
_server_supervisor_cli() {
  printf 'enter %s\n' "$*" >> "$VIBECRAFTED_TEST_SERVICE_LOG"
  sleep 0.4
  printf 'exit %s\n' "$*" >> "$VIBECRAFTED_TEST_SERVICE_LOG"
}
main "$@"
"""
    assert source.endswith('main "$@"\n')
    _write_executable(deck, source.removesuffix('main "$@"\n') + harness)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "VIBECRAFTED_HOME": str(shared_home),
            "VIBECRAFTED_RUNTIME_HOME": str(home / ".local" / "share" / "vibecrafted"),
            "VIBECRAFTED_TOOLS_HOME": str(
                home / ".local" / "share" / "vibecrafted" / "tools"
            ),
            "VIBECRAFTED_TEST_SERVICE_LOG": str(log),
        }
    )
    first = subprocess.Popen(
        [str(deck), "server", "service", "install"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_text(log, "enter service install")
    second = subprocess.Popen(
        [str(deck), "server", "service", "restart"],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(0.15)
    assert log.read_text(encoding="utf-8").splitlines() == ["enter service install"]
    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode == 0, (second_stdout, second_stderr)
    assert log.read_text(encoding="utf-8").splitlines() == [
        "enter service install",
        "exit service install",
        "enter service restart",
        "exit service restart",
    ]


def test_lifecycle_fence_loss_terminates_owned_installer_child_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    started = tmp_path / "child-started"
    completed = tmp_path / "child-completed"
    _write_valid_runtime_generation(old_target)
    _write_executable(
        old_target / "scripts" / "vibecrafted",
        "#!/usr/bin/env bash\n"
        "_acquire_server_lifecycle_lock() {\n"
        "  _SERVER_LIFECYCLE_LOCK_PID=$$\n"
        "  _SERVER_LIFECYCLE_LOCK_NONCE="
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "}\n"
        "_release_server_lifecycle_lock() { :; }\n",
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(old_target.name)
    quiescent = installer._RuntimeServiceStatus(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            old_target / "scripts" / "vibecrafted",
            quiescent,
            "stopped",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    real_fence = installer._runtime_lifecycle_handoff_fence

    @contextmanager
    def close_fence_after_child_starts(
        observed_home: Path,
        *,
        deck: Path | None,
    ):
        with real_fence(observed_home, deck=deck) as guard:
            assert guard.process is not None
            assert guard.process.stdin is not None

            def close_helper_stdin() -> None:
                deadline = time.monotonic() + 5
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                guard.process.stdin.close()

            closer = threading.Thread(target=close_helper_stdin, daemon=True)
            closer.start()
            try:
                yield guard
            finally:
                closer.join(timeout=5)

    monkeypatch.setattr(
        installer,
        "_runtime_lifecycle_handoff_fence",
        close_fence_after_child_starts,
    )
    child = (
        "from pathlib import Path; import sys, time\n"
        "started, completed = map(Path, sys.argv[1:])\n"
        "started.write_text('started\\n', encoding='utf-8')\n"
        "time.sleep(5)\n"
        "completed.write_text('should not exist\\n', encoding='utf-8')\n"
    )

    began = time.monotonic()
    result = installer.run_with_tools_install_lease(
        shared_home,
        [sys.executable, "-c", child, str(started), str(completed)],
        require_tools_handoff=False,
    )

    assert result == 126
    assert time.monotonic() - began < 3
    assert started.is_file()
    assert not completed.exists()


def test_operator_interrupt_contains_owned_installer_child_process_group(
    tmp_path: Path,
) -> None:
    started = tmp_path / "child-started"
    completed = tmp_path / "child-completed"
    child = (
        "from pathlib import Path; import sys, time\n"
        "started, completed = map(Path, sys.argv[1:])\n"
        "started.write_text('started\\n', encoding='utf-8')\n"
        "time.sleep(5)\n"
        "completed.write_text('should not exist\\n', encoding='utf-8')\n"
    )

    class InterruptAfterStart:
        def assert_owned(self) -> None:
            if started.exists():
                raise KeyboardInterrupt

    descriptor = os.open("/dev/null", os.O_RDONLY)
    try:
        with pytest.raises(KeyboardInterrupt):
            installer._run_install_child_with_lifecycle_guard(
                [sys.executable, "-c", child, str(started), str(completed)],
                descriptor=descriptor,
                environment=os.environ.copy(),
                lifecycle_guard=InterruptAfterStart(),
            )
    finally:
        os.close(descriptor)

    assert started.is_file()
    assert not completed.exists()


def test_install_child_allows_bounded_natural_descendant_drain(
    tmp_path: Path,
) -> None:
    grandchild_ready = tmp_path / "grandchild-ready"
    child = (
        "from pathlib import Path; import subprocess, sys, time\n"
        "ready = Path(sys.argv[1])\n"
        "code = (\n"
        "    'from pathlib import Path; import sys, time\\n'\n"
        '    \'Path(sys.argv[1]).write_text("ready\\\\n", encoding="utf-8")\\n\'\n'
        "    'time.sleep(0.25)\\n'\n"
        ")\n"
        "subprocess.Popen([sys.executable, '-c', code, str(ready)])\n"
        "deadline = time.monotonic() + 2\n"
        "while not ready.exists():\n"
        "    if time.monotonic() >= deadline:\n"
        "        raise SystemExit(70)\n"
        "    time.sleep(0.01)\n"
    )

    class OwnedFence:
        def assert_owned(self) -> None:
            return None

    descriptor = os.open("/dev/null", os.O_RDONLY)
    try:
        result = installer._run_install_child_with_lifecycle_guard(
            [sys.executable, "-c", child, str(grandchild_ready)],
            descriptor=descriptor,
            environment=os.environ.copy(),
            lifecycle_guard=OwnedFence(),
        )
    finally:
        os.close(descriptor)

    assert result == 0
    assert grandchild_ready.is_file()


def test_fence_loss_kills_sigterm_ignoring_installer_grandchild(
    tmp_path: Path,
) -> None:
    grandchild_ready = tmp_path / "grandchild-ready"
    child = (
        "from pathlib import Path; import subprocess, sys, time\n"
        "ready = Path(sys.argv[1])\n"
        "code = (\n"
        "    'import os, signal, sys, time\\n'\n"
        "    'from pathlib import Path\\n'\n"
        "    'signal.signal(signal.SIGTERM, signal.SIG_IGN)\\n'\n"
        "    'Path(sys.argv[1]).write_text(str(os.getpgrp()), encoding=\"utf-8\")\\n'\n"
        "    'time.sleep(30)\\n'\n"
        ")\n"
        "subprocess.Popen([sys.executable, '-c', code, str(ready)])\n"
        "time.sleep(30)\n"
    )

    class LoseFenceAfterGrandchildStarts:
        def assert_owned(self) -> None:
            if grandchild_ready.exists():
                raise OSError("fence lost")

    descriptor = os.open("/dev/null", os.O_RDONLY)
    try:
        with pytest.raises(OSError, match=r"fence (?:was )?lost"):
            installer._run_install_child_with_lifecycle_guard(
                [sys.executable, "-c", child, str(grandchild_ready)],
                descriptor=descriptor,
                environment=os.environ.copy(),
                lifecycle_guard=LoseFenceAfterGrandchildStarts(),
            )
    finally:
        os.close(descriptor)

    process_group = int(grandchild_ready.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.killpg(process_group, 0)


def test_tools_install_lease_serializes_processes_and_times_out_clearly(
    tmp_path: Path,
) -> None:
    current = tmp_path / "tools" / "vibecrafted-current"
    current.parent.mkdir(parents=True)
    context = multiprocessing.get_context("fork")
    events = context.Queue()
    first_ready = context.Event()
    second_ready = context.Event()
    first = context.Process(
        target=_tools_lease_worker,
        args=(str(current), "first", 0.8, 5.0, first_ready, events),
    )
    second = context.Process(
        target=_tools_lease_worker,
        args=(str(current), "second", 0.0, 5.0, second_ready, events),
    )

    first.start()
    try:
        assert first_ready.wait(10)
        first_event = events.get(timeout=10)
        assert first_event[:2] == ("first", "acquired")

        with (
            pytest.raises(TimeoutError, match="operation=first"),
            installer._tools_install_lease(
                current,
                timeout_seconds=0.05,
                operation="timeout-probe",
            ),
        ):
            raise AssertionError("contending process must not acquire the lease")

        second.start()
        assert not second_ready.wait(0.15)
        first.join(timeout=10)
        second.join(timeout=10)
        assert first.exitcode == 0
        assert second.exitcode == 0

        remaining = [events.get(timeout=10) for _ in range(3)]
        assert not [event for event in remaining if event[1] == "error"]
        first_leaving = next(
            event[2] for event in remaining if event[:2] == ("first", "leaving")
        )
        second_acquired = next(
            event[2] for event in remaining if event[:2] == ("second", "acquired")
        )
        assert second_acquired >= first_leaving
        assert (
            installer._tools_install_lease_path(current).read_text(encoding="utf-8")
            == ""
        )
    finally:
        for process in (first, second):
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        events.close()
        events.join_thread()


def test_generation_gc_preserves_live_recovery_and_interrupted_receipt_targets(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    generation_names = (
        "vibecrafted-generation-recent-a",
        "vibecrafted-generation-recent-b",
        "vibecrafted-generation-stale-a",
        "vibecrafted-generation-stale-b",
        "vibecrafted-generation-current",
        "vibecrafted-generation-rollback",
        "vibecrafted-generation-prepared",
    )
    generations: dict[str, Path] = {}
    now = time.time_ns()
    for age, name in enumerate(generation_names):
        generation = tools / name
        _write_valid_runtime_generation(generation)
        os.utime(generation, ns=(now - age * 1_000_000, now - age * 1_000_000))
        generations[name] = generation
    current.symlink_to(generations["vibecrafted-generation-current"].name)
    installer._atomic_json_file(
        installer._tools_handoff_path(current),
        {
            "schema": installer._TOOLS_HANDOFF_SCHEMA,
            "state": "prepared",
            "old_target": str(generations["vibecrafted-generation-rollback"].resolve()),
            "new_target": str(generations["vibecrafted-generation-prepared"].resolve()),
        },
    )
    invalid = tools / "vibecrafted-generation-unmanaged"
    invalid.mkdir()
    (invalid / "do-not-delete.txt").write_text("not ours\n", encoding="utf-8")

    removed = installer.prune_tools_generations(home, keep=2)

    protected = {
        generations["vibecrafted-generation-current"],
        generations["vibecrafted-generation-rollback"],
        generations["vibecrafted-generation-prepared"],
        generations["vibecrafted-generation-recent-a"],
        generations["vibecrafted-generation-recent-b"],
    }
    assert all(path.is_dir() for path in protected)
    assert set(removed) == {
        generations["vibecrafted-generation-stale-a"].resolve(),
        generations["vibecrafted-generation-stale-b"].resolve(),
    }
    assert invalid.is_dir()


@pytest.mark.parametrize(
    "failure_point",
    ["stage", "stamp", "vc-frame", "rename", "publish"],
)
def test_runtime_generation_failure_keeps_old_pointer_live(
    tmp_path: Path, monkeypatch, failure_point: str
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)

    if failure_point == "stage":
        monkeypatch.setattr(
            installer,
            "stage_distribution_payload",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stage failed")),
        )
    elif failure_point == "stamp":
        monkeypatch.setattr(
            installer,
            "stamp_install_version",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stamp failed")),
        )
    elif failure_point == "vc-frame":
        monkeypatch.setattr(
            installer,
            "_materialize_vc_frame_generation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("vc-frame materialization failed")
            ),
        )
    elif failure_point == "rename":
        original_rename = Path.rename

        def fail_generation_rename(path: Path, target: Path) -> Path:
            if path.name.startswith(".vibecrafted-current.staging-"):
                raise OSError("generation publish failed")
            return original_rename(path, target)

        monkeypatch.setattr(Path, "rename", fail_generation_rename)
    else:
        monkeypatch.setattr(
            installer,
            "_atomic_symlink",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("pointer publish failed")
            ),
        )

    with pytest.raises(OSError):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gtest",
        )

    assert current.is_symlink()
    assert current.resolve() == old_target.resolve()
    assert (current / "proof.txt").read_text(encoding="utf-8") == "old runtime\n"
    assert not list(current.parent.glob(".vibecrafted-current.staging-*"))
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gtest-*"))


def test_tarball_install_version_uses_explicit_source_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "vibecrafted-tarball"
    source.mkdir()
    (source / "VERSION").write_text("3.7.0\n", encoding="utf-8")
    revision = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("VIBECRAFTED_SOURCE_OWNER_REPO", "vetcoders/vibecrafted")
    monkeypatch.setenv("VIBECRAFTED_SOURCE_REVISION", revision)
    _write_source_provenance_fixture(source, source_revision=revision)

    assert installer.get_repo_commit(source) == "01234567"
    assert installer.get_repo_full_commit(source) == revision
    assert installer.get_repo_owner(source) == "vetcoders/vibecrafted"
    assert installer.get_install_version(source) == "3.7.0+g01234567"


def test_secure_walkaround_launcher_accepts_public_symlink_and_rejects_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools,
        "vibecrafted-generation-a",
        'print("generation-a")\n',
    )
    current.symlink_to(generation.name)
    managed, public = _install_test_walkaround_launcher(tmp_path, current)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "uv-tools"))

    assert public.is_symlink()
    assert installer._secure_walkaround_launcher_issues(current, public) == []

    managed.write_bytes(managed.read_bytes() + b"# benign drift\n")
    assert installer._secure_walkaround_launcher_issues(current, public) == [
        f"{installer.SECURE_WALKAROUND_LAUNCHER}:corrupt:wrapper bytes drifted"
    ]


def test_secure_walkaround_launcher_rejects_noncanonical_carrier_bytes(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-a", 'print("must-not-run")\n'
    )
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)
    carrier = generation / "source-provenance.json"
    carrier.write_text(
        json.dumps(json.loads(carrier.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 70
    assert result.stdout == ""
    assert "source provenance disagrees with runtime manifest" in result.stderr


@pytest.mark.parametrize(
    "mutation",
    ("missing", "v1", "open", "owner", "revision", "payload"),
)
def test_secure_walkaround_launcher_rejects_carrier_manifest_lineage_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-a", 'print("must-not-run")\n'
    )
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)
    carrier_path = generation / "source-provenance.json"
    if mutation == "missing":
        carrier_path.unlink()
    else:
        carrier = json.loads(carrier_path.read_text(encoding="utf-8"))
        if mutation == "v1":
            carrier["schema"] = "vibecrafted.source-provenance.v1"
        elif mutation == "open":
            carrier["unbound"] = True
        elif mutation == "owner":
            carrier["owner_repo"] = "vetcoders/other"
        elif mutation == "revision":
            carrier["source_revision"] = "f" * 40
        elif mutation == "payload":
            carrier["payload"]["tree_sha256"] = "f" * 64
        else:  # pragma: no cover
            raise AssertionError(mutation)
        carrier_path.write_text(
            json.dumps(carrier, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 70
    assert result.stdout == ""
    assert "must-not-run" not in result.stdout
    assert "invalid Vibecrafted verifier runtime" in result.stderr


def test_secure_walkaround_launcher_rejects_wrapper_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-a", 'print("must-not-run")\n'
    )
    current.symlink_to(generation.name)
    managed, public = _install_test_walkaround_launcher(tmp_path, current)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "uv-tools"))
    os.link(managed, managed.with_name("wrapper-hardlink"))

    issues = installer._secure_walkaround_launcher_issues(current, public)
    assert any(
        "wrapper" in issue and "unique regular file" in issue for issue in issues
    )
    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)
    assert result.returncode == 70
    assert "managed wrapper is not a unique regular file" in result.stderr


def test_secure_walkaround_launcher_canonicalizes_parent_aliases_not_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = tmp_path / "real"
    alias = tmp_path / "alias"
    real.mkdir()
    alias.symlink_to(real, target_is_directory=True)
    tools = real / "runtime-tools"
    current_alias = alias / "runtime-tools/vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-a", 'print("alias-ok")\n'
    )
    current_alias.symlink_to(generation.name)
    uv_alias = alias / "uv-tools"
    uv_real = real / "uv-tools"
    python_alias = uv_alias / "vibecrafted/bin/python"
    python_alias.parent.mkdir(parents=True)
    interpreter = Path(sys.executable).resolve(strict=True)
    python_alias.symlink_to(interpreter)
    wrapper_alias = python_alias.parent / installer.SECURE_WALKAROUND_LAUNCHER
    installed = installer._install_secure_walkaround_launcher(
        current_alias,
        python_alias,
        launcher_path=wrapper_alias,
    )
    public = tmp_path / "public" / installer.SECURE_WALKAROUND_LAUNCHER
    public.parent.mkdir()
    public.symlink_to(installed.resolve(strict=True))
    monkeypatch.setenv("UV_TOOL_DIR", str(uv_real))

    assert (
        installer._secure_walkaround_launcher_issues(
            real / "runtime-tools/vibecrafted-current", public
        )
        == []
    )
    rendered = installed.read_text(encoding="utf-8")
    assert f"python={python_alias.parent.resolve(strict=True) / 'python'}" in rendered
    assert str(interpreter) not in rendered.splitlines()[1]


def test_secure_walkaround_launcher_follows_each_atomic_pointer_swap(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation_a = _write_walkaround_generation(
        tools,
        "vibecrafted-generation-a",
        'print("generation-a")\n',
    )
    generation_b = _write_walkaround_generation(
        tools,
        "vibecrafted-generation-b",
        'print("generation-b")\n',
    )
    installer._atomic_symlink(generation_a, current)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    observed: list[str] = []
    for generation in (generation_a, generation_b, generation_a):
        installer._atomic_symlink(generation, current)
        result = subprocess.run(
            [str(public)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        observed.append(result.stdout.strip())

    assert observed == ["generation-a", "generation-b", "generation-a"]


@pytest.mark.parametrize(
    "relative",
    [
        Path("vibecrafted-core/vibecrafted_core/product_contract.py"),
        installer._RUNTIME_VERIFIER_RUNNER,
        installer._RUNTIME_VERIFIER_SCHEMA,
        Path("vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"),
        Path("vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"),
    ],
)
def test_secure_walkaround_launcher_never_executes_post_manifest_mutation(
    tmp_path: Path,
    relative: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    marker = tmp_path / "executed.txt"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-trusted", 'print("trusted")\n'
    )
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)
    target = generation / relative
    if relative == installer._RUNTIME_VERIFIER_RUNNER:
        target.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
    else:
        target.write_bytes(target.read_bytes() + b"\npost-manifest mutation\n")

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 70
    assert "manifest-bound file drifted" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    "relative",
    [
        installer._RUNTIME_VERIFIER_RUNNER,
        installer._RUNTIME_VERIFIER_SCHEMA,
    ],
)
def test_secure_walkaround_launcher_rejects_bound_file_hardlinks(
    tmp_path: Path,
    relative: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-trusted", 'print("must-not-run")\n'
    )
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)
    os.link(generation / relative, tmp_path / f"hardlink-{relative.name}")

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 70
    assert "not a unique regular file" in result.stderr


def test_secure_walkaround_launcher_uses_canonical_runtime_config_without_alias(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-projection", 'print("projection-ok")\n'
    )
    assert not (generation / "runtime").exists()
    assert (
        generation
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    ).is_file()
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "projection-ok\n"


def test_secure_walkaround_launcher_rejects_canonical_config_parent_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-nested-alias", 'print("must-not-run")\n'
    )
    canonical = (
        generation
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    generated = canonical.parents[1]
    generated.rename(generated.with_name("generated.unbound"))
    generated.symlink_to("generated.unbound", target_is_directory=True)
    _rewrite_walkaround_manifest(generation)
    current.symlink_to(generation.name)
    managed, public = _install_test_walkaround_launcher(tmp_path, current)
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "uv-tools"))

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)
    issues = installer._secure_walkaround_launcher_issues(current, managed)

    assert result.returncode == 70
    assert result.stdout == ""
    assert "manifest-bound file" in result.stderr
    assert "is aliased" in result.stderr
    assert any("manifest-bound file is aliased" in item for item in issues)


def test_secure_walkaround_launcher_rejects_escaping_canonical_config(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools, "vibecrafted-generation-escape", 'print("must-not-run")\n'
    )
    canonical = (
        generation
        / "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    )
    external_config = tmp_path / "escaping-config.kdl"
    canonical.rename(external_config)
    canonical.symlink_to(external_config)
    current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    result = subprocess.run([str(public)], check=False, capture_output=True, text=True)

    assert result.returncode == 70
    assert "manifest-bound file" in result.stderr
    assert "is aliased" in result.stderr


def test_secure_walkaround_launcher_resists_hostile_python_and_path_environment(
    tmp_path: Path,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools,
        "vibecrafted-generation-trusted",
        (
            "import os\n"
            'print("trusted-generation")\n'
            'print(os.environ.get("PYTHONPATH", "unset"))\n'
            'print(os.environ.get("PYTHONHOME", "unset"))\n'
        ),
    )
    installer._atomic_symlink(generation, current)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    hostile_bin = tmp_path / "hostile-bin"
    for name in ("mktemp", "python", "python3", "readlink", "rm"):
        _write_executable(
            hostile_bin / name,
            f"#!/bin/sh\nprintf 'diverted-by-{name}\\n'\nexit 97\n",
        )
    hostile_python = tmp_path / "hostile-python"
    hostile_python.mkdir()
    (hostile_python / "sitecustomize.py").write_text(
        'print("diverted-by-pythonpath")\n',
        encoding="utf-8",
    )
    hostile_home = tmp_path / "hostile-python-home"
    hostile_home.mkdir()
    cache_root = tmp_path / "wrapper-cache"
    cache_root.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": str(hostile_bin),
            "PYTHONHOME": str(hostile_home),
            "PYTHONPATH": str(hostile_python),
            "PYTHONPYCACHEPREFIX": str(tmp_path / "hostile-bytecode"),
            "TMPDIR": str(cache_root),
        }
    )

    result = subprocess.run(
        [str(public)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["trusted-generation", "unset", "unset"]
    assert result.stderr == ""
    assert not list(cache_root.iterdir())
    assert not (tmp_path / "hostile-bytecode").exists()


@pytest.mark.parametrize("alias_kind", ["generation", "runner", "internal-parent"])
def test_secure_walkaround_launcher_rejects_aliased_runtime_surface(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    tools = tmp_path / "tools"
    current = tools / "vibecrafted-current"
    generation = _write_walkaround_generation(
        tools,
        "vibecrafted-generation-trusted",
        'print("trusted-generation")\n',
    )
    if alias_kind == "generation":
        aliased_generation = tools / "vibecrafted-generation-alias"
        aliased_generation.symlink_to(generation.name, target_is_directory=True)
        current.symlink_to(aliased_generation.name)
    elif alias_kind == "runner":
        runner = (
            generation
            / "vibecrafted-core"
            / "vibecrafted_core"
            / "walkaround_runner.py"
        )
        real_runner = runner.with_name("real_walkaround_runner.py")
        runner.rename(real_runner)
        runner.symlink_to(real_runner.name)
        current.symlink_to(generation.name)
    else:
        core = generation / "vibecrafted-core"
        real_core = generation / "internal-core"
        core.rename(real_core)
        core.symlink_to(real_core.name, target_is_directory=True)
        current.symlink_to(generation.name)
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    result = subprocess.run(
        [str(public)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 70
    assert result.stdout == ""
    assert "invalid Vibecrafted" in result.stderr


def test_secure_walkaround_launcher_runs_real_generation_contract(
    tmp_path: Path,
) -> None:
    source, _old_target, current = _runtime_pointer_fixture(tmp_path)
    generation = installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gwalkaround",
    )
    assert current.resolve() == generation.resolve()
    _managed, public = _install_test_walkaround_launcher(tmp_path, current)

    help_result = subprocess.run(
        [str(public), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0, help_result.stderr
    assert "trust-probe" in help_result.stdout
    assert "verify-release" in help_result.stdout

    missing_result = subprocess.run(
        [
            str(public),
            "trust-probe",
            str(tmp_path / "missing-challenge.json"),
            str(tmp_path / "missing-signature.sig"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert missing_result.returncode == product_contract.E_MISSING
    assert "VCPC022:" in missing_result.stderr


def test_runtime_generation_pointer_swap_never_removes_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    source_provenance = installer.load_source_provenance(source)
    assert source_provenance is not None
    assert not (source / ".git").exists()
    original_replace = installer.os.replace
    observations: list[tuple[bool, bool, bool]] = []

    def observed_replace(source_path, destination_path) -> None:
        destination = Path(destination_path)
        if destination == current:
            before = current.is_symlink() and current.resolve() == old_target.resolve()
            raw_target = Path(os.readlink(source_path))
            if not raw_target.is_absolute():
                raw_target = Path(source_path).parent / raw_target
            materialized_before_publish = (
                raw_target.resolve()
                / "vibecrafted-core"
                / "vibecrafted_core"
                / "runtime"
                / "generated"
                / "vc-frame"
                / "config.kdl"
            ).is_file()
            original_replace(source_path, destination_path)
            observations.append(
                (
                    before,
                    current.is_symlink() and current.exists(),
                    materialized_before_publish,
                )
            )
            return
        original_replace(source_path, destination_path)

    monkeypatch.setattr(installer.os, "replace", observed_replace)

    generation = installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gtest",
    )

    assert observations == [(True, True, True)]
    assert current.resolve() == generation.resolve()
    assert current.resolve() != old_target.resolve()
    assert (current / "scripts" / "vibecrafted").is_file()
    manifest = json.loads(
        (generation / installer._RUNTIME_GENERATION_MANIFEST).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["schema"] == installer._RUNTIME_GENERATION_MANIFEST_SCHEMA
    assert manifest["version"] == "9.9.9+gtest"
    assert manifest["entrypoint"] == (
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
    )
    assert (manifest["owner_repo"], manifest["source_revision"]) == (
        source_provenance["owner_repo"],
        source_provenance["source_revision"],
    )
    expected_hashes = {
        relative.as_posix()
        for relative in installer._RUNTIME_GENERATION_REQUIRED_HASHES
    }
    assert expected_hashes == product_contract.RUNTIME_GENERATION_REQUIRED_HASHES
    assert set(manifest["hashes"]) == expected_hashes
    for relative, digest in manifest["hashes"].items():
        assert (
            digest == hashlib.sha256((generation / relative).read_bytes()).hexdigest()
        )


def test_runtime_generation_rejects_final_bound_path_swap_before_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    replacement = tmp_path / "replacement-vibecrafted"
    replacement.write_bytes(b"#!/bin/sh\nprintf replaced\n")
    replacement.chmod(0o755)
    real_open = installer.os.open
    real_fstat = installer.os.fstat
    real_replace = installer.os.replace
    state: dict[str, object] = {
        "opens": 0,
        "fd": None,
        "path": None,
        "fstats": 0,
        "swapped": False,
    }

    def open_then_track(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        try:
            candidate = Path(path)
        except TypeError:
            return descriptor
        if (
            candidate.name == "vibecrafted"
            and candidate.parent.name == "scripts"
            and any(
                part.startswith(".vibecrafted-current.staging-")
                for part in candidate.parts
            )
        ):
            state["opens"] = int(state["opens"]) + 1
            # The third capture is the final payload validation immediately
            # before the generation and its stable pointer can be published.
            if state["opens"] == 3:
                state["fd"] = descriptor
                state["path"] = candidate
        return descriptor

    def replace_path_after_second_fstat(descriptor: int):
        metadata = real_fstat(descriptor)
        if descriptor == state["fd"]:
            state["fstats"] = int(state["fstats"]) + 1
            if state["fstats"] == 2:
                target = state["path"]
                assert isinstance(target, Path)
                real_replace(replacement, target)
                state["swapped"] = True
                state["fd"] = None
        return metadata

    monkeypatch.setattr(installer.os, "open", open_then_track)
    monkeypatch.setattr(installer.os, "fstat", replace_path_after_second_fstat)

    with pytest.raises(OSError, match="file changed while it was captured"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gpath-swap",
        )

    assert state["swapped"] is True
    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gpath-swap-*"))


def test_runtime_generation_rejects_active_source_checkout_reference(
    tmp_path: Path,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    launcher = source / "scripts" / "vibecrafted"
    launcher.write_text(
        launcher.read_text(encoding="utf-8")
        + f"\nreadonly LEAKED_CHECKOUT={source!s}\n",
        encoding="utf-8",
    )
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match="references source checkout"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gleak",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gleak-*"))


@pytest.mark.parametrize(
    ("relative", "replacement", "message"),
    [
        (
            Path("vibecrafted-core/vibecrafted_core/product_contract.py"),
            b"{}\n",
            "missing entrypoints",
        ),
        (
            installer._RUNTIME_VERIFIER_RUNNER,
            b"def main():\n    return 0\n",
            "command grammar drifted",
        ),
        (
            installer._RUNTIME_VERIFIER_SCHEMA,
            b"{}\n",
            "top level is not closed",
        ),
    ],
)
def test_runtime_generation_rejects_semantically_inert_verifier_before_pointer(
    tmp_path: Path,
    relative: Path,
    replacement: bytes,
    message: str,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    (source / relative).write_bytes(replacement)
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match=message):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gmalformed",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gmalformed-*"))


@pytest.mark.parametrize(
    ("relative", "replacement"),
    [
        (installer._RUNTIME_VERIFIER_PRODUCT, _SEMANTICALLY_FAKE_PRODUCT_CONTRACT),
        (installer._RUNTIME_VERIFIER_RUNNER, _SEMANTICALLY_FAKE_WALKAROUND_RUNNER),
    ],
)
def test_runtime_generation_rejects_semantic_verifier_stub_before_pointer(
    tmp_path: Path,
    relative: Path,
    replacement: bytes,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    (source / relative).write_bytes(replacement)
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match="semantic negative control"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gsemantic-fake",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(
        current.parent.glob("vibecrafted-generation-9.9.9+gsemantic-fake-*")
    )


def test_runtime_generation_rejects_finite_quiz_runner_by_retained_source_payload(
    tmp_path: Path,
) -> None:
    """A quiz-shaped runner cannot replace bytes bound by the unchanged v2 carrier."""
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    runner = source / installer._RUNTIME_VERIFIER_RUNNER
    runner.write_bytes(_FINITE_QUIZ_FAKE_WALKAROUND_RUNNER)
    missing = tmp_path / "missing"
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    challenge = invalid / "challenge.json"
    challenge.write_text("not a challenge\n", encoding="utf-8")
    challenge_signature = invalid / "challenge.sig"
    challenge_signature.write_bytes(b"\0" * 256)
    release = invalid / "release-output.json"
    release.write_text("not a release\n", encoding="utf-8")
    release_signature = invalid / "release-output.json.sig"
    release_signature.write_bytes(b"\0" * 256)
    output = invalid / "walkaround.json"
    quiz = (
        (
            "trust-probe",
            [str(missing / "challenge.json"), str(missing / "challenge.sig")],
            [str(challenge), str(challenge_signature)],
            None,
        ),
        (
            "verify-release",
            [
                "--release-output",
                str(missing / "release-output.json"),
                "--signature",
                str(missing / "release-output.json.sig"),
            ],
            [
                "--release-output",
                str(release),
                "--signature",
                str(release_signature),
            ],
            None,
        ),
        (
            "walkaround",
            [
                "--release-output",
                str(missing / "release-output.json"),
                "--signature",
                str(missing / "release-output.json.sig"),
                "--output",
                str(missing / "walkaround.json"),
            ],
            [
                "--release-output",
                str(release),
                "--signature",
                str(release_signature),
                "--output",
                str(output),
            ],
            output,
        ),
    )
    for command, missing_arguments, invalid_arguments, forbidden_output in quiz:
        for arguments, expected_code in (
            (missing_arguments, product_contract.E_MISSING),
            (invalid_arguments, product_contract.E_PROOF),
        ):
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(runner), command, *arguments],
                check=False,
                capture_output=True,
                text=True,
            )
            assert result.returncode == expected_code
            assert result.stdout == ""
            assert result.stderr.startswith(f"VCPC{expected_code:03d}:")
        if forbidden_output is not None:
            assert not forbidden_output.exists()

    with pytest.raises(
        installer.DistributionManifestError,
        match="payload digest does not match the source tree",
    ):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gfinite-quiz",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gfinite-quiz-*"))


@pytest.mark.parametrize(
    "relative",
    [installer._RUNTIME_VERIFIER_PRODUCT, installer._RUNTIME_VERIFIER_RUNNER],
)
def test_runtime_generation_rejects_syntax_invalid_verifier_before_pointer(
    tmp_path: Path,
    relative: Path,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    (source / relative).write_text("def broken(:\n", encoding="utf-8")
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match="not compilable"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gsyntax",
        )

    assert current.resolve() == old_target.resolve()


def test_runtime_generation_rejects_nested_open_schema_before_pointer(
    tmp_path: Path,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    schema_path = source / installer._RUNTIME_VERIFIER_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["$defs"]["fileEntry"]["additionalProperties"] = True
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match="leaves an object open"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gopen-schema",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gopen-schema-*"))


@pytest.mark.parametrize(
    ("removed_field", "message"),
    [
        ("type", "object path inventory drifted"),
        ("additionalProperties", "leaves an object open"),
    ],
)
def test_runtime_generation_rejects_weakened_nested_object_schema_before_pointer(
    tmp_path: Path,
    removed_field: str,
    message: str,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    schema_path = source / installer._RUNTIME_VERIFIER_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    nested_object = schema["$defs"]["releaseOutput"]["properties"]["dmg"]
    assert nested_object.pop(removed_field) is not None
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    _write_source_provenance_fixture(source)

    with pytest.raises(OSError, match=message):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version=f"9.9.9+gweakened-{removed_field.lower()}",
        )

    assert current.resolve() == old_target.resolve()
    assert not list(current.parent.glob("vibecrafted-generation-9.9.9+gweakened-*"))


def test_runtime_generation_doctor_verifies_manifest_and_launcher(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    _write_valid_runtime_generation(old_target)
    current.symlink_to(old_target.name)
    monkeypatch.setenv("HOME", str(home))

    generation = installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gdoctor",
    )
    launcher = home / ".local" / "bin" / "vibecrafted"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(
        current / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
    )

    findings = installer._runtime_generation_contract_findings()
    assert findings == [
        installer.DoctorFinding(
            "ok",
            "runtime-generation",
            f"{generation.name} is manifest-bound and checkout-free",
        )
    ]

    runtime_launcher = generation / "scripts" / "vibecrafted"
    runtime_launcher_original = runtime_launcher.read_bytes()
    runtime_launcher.write_text(
        "#!/usr/bin/env bash\nexit 42\n",
        encoding="utf-8",
    )
    [drift] = installer._runtime_generation_contract_findings()
    assert drift.level == "fail"
    assert "manifest-bound file drifted: scripts/vibecrafted" in drift.message

    runtime_launcher.write_bytes(runtime_launcher_original)
    verifier = generation / "vibecrafted-core/vibecrafted_core/product_contract.py"
    verifier.write_text(
        "def verify_release_output(*_args):\n    return {}\n",
        encoding="utf-8",
    )
    [verifier_drift] = installer._runtime_generation_contract_findings()
    assert verifier_drift.level == "fail"
    assert (
        "manifest-bound file drifted: "
        "vibecrafted-core/vibecrafted_core/product_contract.py"
        in verifier_drift.message
    )


def test_runtime_generation_doctor_rejects_deck_drift_and_incomplete_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "launcher\\n"\n',
    )
    monkeypatch.setenv("HOME", str(home))
    generation = installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gdeck",
    )
    launcher = home / ".local" / "bin" / "vibecrafted"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(
        current / "vibecrafted-core" / "vibecrafted_core" / "deck" / "vibecrafted"
    )
    deck = generation / installer._RUNTIME_GENERATION_ENTRYPOINT
    original = deck.read_bytes()
    deck.write_bytes(original + b"\nexit 99\n")
    [drift] = installer._runtime_generation_contract_findings()
    assert drift.level == "fail"
    assert (
        f"manifest-bound file drifted: {installer._RUNTIME_GENERATION_ENTRYPOINT}"
        in drift.message
    )

    deck.write_bytes(original)
    manifest_path = generation / installer._RUNTIME_GENERATION_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hashes"].pop("vibecrafted-core/vibecrafted_core/product_contract.py")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    [invalid] = installer._runtime_generation_contract_findings()
    assert invalid.level == "fail"
    assert "does not satisfy the runtime schema" in invalid.message


def test_runtime_generation_doctor_rejects_launcher_from_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    source = tmp_path / "source"
    current = tools / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "launcher\\n"\n',
    )
    monkeypatch.setenv("HOME", str(home))
    generation = installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gcurrent",
    )
    shadow = tools / "vibecrafted-generation-shadow"
    shadow_entrypoint = shadow / installer._RUNTIME_GENERATION_ENTRYPOINT
    shadow_entrypoint.parent.mkdir(parents=True)
    shadow_entrypoint.write_bytes(
        (generation / installer._RUNTIME_GENERATION_ENTRYPOINT).read_bytes()
    )
    shadow_entrypoint.chmod(0o755)
    launcher = home / ".local" / "bin" / "vibecrafted"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(shadow_entrypoint)

    [finding] = installer._runtime_generation_contract_findings()
    assert finding.level == "fail"
    assert "neither resolves to nor wraps" in finding.message


def test_chained_prepared_publish_keeps_last_verified_rollback_target(
    tmp_path: Path, monkeypatch
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    tools.parent.mkdir(parents=True)
    current.parent.rename(tools)
    current = tools / "vibecrafted-current"
    old_target = tools / old_target.name
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gfirst",
    )
    unverified = current.resolve()
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gsecond",
    )
    assert current.resolve() != unverified
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert Path(receipt["old_target"]).resolve() == old_target.resolve()

    assert installer.rollback_current_tools(home) is True
    assert current.resolve() == old_target.resolve()


def test_runtime_generation_handoff_rolls_back_and_completes(
    tmp_path: Path, monkeypatch
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    home = tmp_path / "home"
    runtime_tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime_tools.parent.mkdir(parents=True)
    current.parent.rename(runtime_tools)
    current = runtime_tools / "vibecrafted-current"
    old_target = runtime_tools / old_target.name
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gtest",
    )
    new_target = current.resolve()

    assert installer.rollback_current_tools(home) is True
    assert current.resolve() == old_target.resolve()
    assert installer.rollback_current_tools(home) is False

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gtest2",
    )
    assert current.resolve() != new_target
    assert installer.complete_current_tools_handoff(home) is True
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["state"] == "complete"
    completed_target = current.resolve()
    assert installer.rollback_current_tools(home) is False
    assert current.resolve() == completed_target


def test_first_runtime_generation_handoff_rolls_back_to_absent_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    source = tmp_path / "source"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gfirst",
    )
    published = current.resolve()
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["old_target"] == ""

    assert installer.rollback_current_tools(home) is True

    assert not current.exists()
    assert not current.is_symlink()
    assert published.is_dir()
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["state"] == "rolled-back"


def test_existing_target_rollback_retry_completes_prepared_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, old_target, current = _runtime_pointer_fixture(tmp_path)
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    tools.parent.mkdir(parents=True)
    current.parent.rename(tools)
    current = tools / "vibecrafted-current"
    old_target = tools / old_target.name
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gretry",
    )
    installer._atomic_symlink(old_target, current)
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["state"] == "prepared"

    assert installer.rollback_current_tools(home) is False

    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["state"] == "rolled-back"
    assert current.resolve() == old_target.resolve()


def test_first_install_receipt_failure_never_republishes_failed_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    source = tmp_path / "source"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    monkeypatch.setenv("HOME", str(home))
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+greceipt-fault",
    )
    published = current.resolve()
    real_atomic_json = installer._atomic_json_file

    def fail_after_receipt_replace(path: Path, payload: dict[str, object]) -> None:
        real_atomic_json(path, payload)
        if payload.get("state") == "rolled-back":
            raise OSError("injected post-receipt fsync ambiguity")

    monkeypatch.setattr(
        installer,
        "_atomic_json_file",
        fail_after_receipt_replace,
    )

    with pytest.raises(OSError, match="injected post-receipt"):
        installer.rollback_current_tools(home)

    assert not current.exists()
    assert not current.is_symlink()
    assert published.is_dir()
    receipt = installer._read_tools_handoff(home)
    assert receipt is not None
    assert receipt["state"] == "rolled-back"


def test_darwin_first_install_rollback_fences_with_new_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    shared_home = home / ".vibecrafted"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    source = tmp_path / "source"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
        service_lock_contract=True,
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(shared_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gfirst-darwin",
    )
    published = current.resolve()
    quiescent = installer._RuntimeServiceStatus(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )
    monkeypatch.setattr(installer.sys, "platform", "darwin")
    _mock_runtime_launchd_gate(monkeypatch)
    monkeypatch.setattr(
        installer,
        "_runtime_service_snapshot",
        lambda _shared_home: (
            published / "scripts" / "vibecrafted",
            quiescent,
            "stopped",
        ),
    )
    monkeypatch.setattr(
        installer,
        "_bootout_owned_runtime_launchd_job",
        lambda _shared_home: False,
    )
    fenced_generations: list[Path] = []

    class FenceGuard:
        def assert_owned(self) -> None:
            return None

    @contextmanager
    def lifecycle_fence(_shared_home: Path, *, deck: Path | None = None):
        assert deck is not None
        fenced_generations.append(deck)
        yield FenceGuard()

    @contextmanager
    def supervisor_fence(_shared_home: Path, *, required: bool):
        assert required is True
        yield

    monkeypatch.setattr(
        installer,
        "_runtime_lifecycle_handoff_fence",
        lifecycle_fence,
    )
    monkeypatch.setattr(
        installer,
        "_runtime_supervisor_handoff_fence",
        supervisor_fence,
    )

    with installer._tools_install_lease(
        current,
        operation="test-first-install-rollback",
    ) as descriptor:
        monkeypatch.setenv(installer._TOOLS_INSTALL_LEASE_ENV, str(descriptor))
        assert installer.rollback_runtime_install(
            shared_home,
            service_was_active=False,
            service_activation_attempted=True,
        )

    assert fenced_generations == [
        published / "scripts" / "vibecrafted",
    ]
    assert not current.exists()
    assert not current.is_symlink()
    receipt = installer._read_tools_handoff(shared_home)
    assert receipt is not None
    assert receipt["state"] == "rolled-back"


def test_completed_handoff_is_not_rolled_back_after_next_stage_failure(
    tmp_path: Path, monkeypatch
) -> None:
    source, _, current = _runtime_pointer_fixture(tmp_path)
    home = tmp_path / "home"
    runtime_tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime_tools.parent.mkdir(parents=True)
    current.parent.rename(runtime_tools)
    current = runtime_tools / "vibecrafted-current"
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gcomplete",
    )
    completed_target = current.resolve()
    assert installer.complete_current_tools_handoff(home) is True
    monkeypatch.setattr(
        installer,
        "stage_distribution_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("stage failed")),
    )

    with pytest.raises(OSError, match="stage failed"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gnext",
        )

    assert installer.rollback_current_tools(home) is False
    assert current.resolve() == completed_target


def test_portable_runtime_pointer_discards_stale_generation_handoff(
    tmp_path: Path, monkeypatch
) -> None:
    source, _, current = _runtime_pointer_fixture(tmp_path)
    home = tmp_path / "home"
    runtime_tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime_tools.parent.mkdir(parents=True)
    current.parent.rename(runtime_tools)
    current = runtime_tools / "vibecrafted-current"
    monkeypatch.setenv("HOME", str(home))

    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gstale",
    )
    assert installer.complete_current_tools_handoff(home) is True
    installer._atomic_symlink(source, current)
    assert installer._tools_handoff_file(home).is_file()

    refreshed = installer.refresh_current_tools(source, home, mirror=True)

    assert refreshed == current
    assert current.resolve() == source.resolve()
    assert not installer._tools_handoff_file(home).exists()
    assert installer.complete_current_tools_handoff(home) is False
    assert installer.rollback_current_tools(home) is False


def test_inherited_install_publishes_generation_when_current_is_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    current = tools / "vibecrafted-current"
    source = tmp_path / "extracted-source"
    _write_complete_source(
        source,
        helper='printf "portable helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "portable launcher\\n"\n',
    )
    current.parent.mkdir(parents=True)
    current.symlink_to(source)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))

    with (
        installer._tools_install_lease(
            current,
            operation="test-root-bootstrap",
        ) as descriptor,
        installer._inherited_tools_install_lease(descriptor),
    ):
        refreshed = installer.refresh_current_tools(
            source,
            home / ".vibecrafted",
            mirror=True,
        )

    assert refreshed == current
    generation = current.resolve()
    assert generation != source.resolve()
    assert generation.name.startswith("vibecrafted-generation-")
    receipt = installer._read_tools_handoff(home / ".vibecrafted")
    assert receipt is not None
    assert receipt["state"] == "prepared"
    assert Path(receipt["old_target"]).resolve() == source.resolve()
    assert Path(receipt["new_target"]).resolve() == generation


def test_runtime_generation_refuses_legacy_real_directory_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    current = tmp_path / "tools" / "vibecrafted-current"
    _write_complete_source(
        source,
        helper='printf "new helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "new launcher\\n"\n',
    )
    current.mkdir(parents=True)
    (current / "proof.txt").write_text("legacy runtime\n", encoding="utf-8")

    with pytest.raises(OSError, match="must be a symlink pointer"):
        installer.sync_control_plane_tree(
            source,
            current,
            mirror=True,
            install_version="9.9.9+gtest",
        )

    assert (current / "proof.txt").read_text(encoding="utf-8") == "legacy runtime\n"


@pytest.mark.parametrize("failed_uv_install", [1, 2, 3, 0])
def test_install_tools_child_failure_rolls_runtime_pointer_back(
    tmp_path: Path,
    failed_uv_install: int,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    data_home = tmp_path / "xdg-data"
    tools = data_home / "vibecrafted" / "tools"
    source = tmp_path / "source"
    old_target = tools / "vibecrafted-generation-old"
    current = tools / "vibecrafted-current"
    _write_valid_runtime_generation(old_target)
    _write_complete_source(
        source,
        helper='printf "runtime helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "runtime launcher\\n"\n',
    )
    (old_target / "proof.txt").write_text("old runtime\n", encoding="utf-8")
    current.symlink_to(old_target.name)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("VIBECRAFTED_TOOLS_HOME", str(tools))
    installer.sync_control_plane_tree(
        source,
        current,
        mirror=True,
        install_version="9.9.9+gtest",
    )
    assert current.resolve() != old_target.resolve()

    fake_bin = tmp_path / "fake-bin"
    state_file = tmp_path / "uv-install-count"
    fake_tool_dir = tmp_path / "uv-tools"
    fake_uv = fake_bin / "uv"
    fake_launcher = fake_bin / "vibecrafted"
    _write_executable(
        fake_launcher,
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'if [[ "${1:-} ${2:-} ${3:-}" == "server service status" ]]; then\n'
        "  printf '%s\\n' "
        '\'{"installed":false,"loaded":false,"supervisor_live":false,'
        '"supervisor_verified":false,"supervisor_service_managed":false,'
        '"build_current":false,"pair_healthy":false,"supervisor_pid":null}\'\n'
        "  exit 1\n"
        "fi\n"
        'if [[ "${1:-} ${2:-}" == "server status" ]]; then\n'
        "  printf 'Server: STOPPED\\nGuardian: STOPPED\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
    )
    _write_executable(
        fake_uv,
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'if [[ "${1:-} ${2:-}" == "tool install" ]]; then\n'
        f'  count="$(cat "{state_file}" 2>/dev/null || printf 0)"\n'
        '  count="$((count + 1))"\n'
        f'  printf "%s\\n" "$count" > "{state_file}"\n'
        f'  if [[ "{failed_uv_install}" -gt 0 && "$count" -eq "{failed_uv_install}" ]]; then exit 42; fi\n'
        "  exit 0\n"
        "fi\n"
        'if [[ "${1:-} ${2:-}" == "tool dir" ]]; then\n'
        f'  printf "%s\\n" "{fake_tool_dir}"\n'
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
    )
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ['PATH']}")
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(fake_bin))
    # The public Make target is covered structurally. Exercise its internal
    # child through the in-process outer transaction so this test never mutates
    # the real per-user launchd label on a Darwin test host.
    monkeypatch.setattr(installer.sys, "platform", "linux")

    result = installer.run_with_tools_install_lease(
        home / ".vibecrafted",
        ["make", "--no-print-directory", "install-tools-held"],
    )
    captured = capfd.readouterr()

    assert result != 0
    assert "restored previous runtime generation" in captured.err
    assert "service ownership" in captured.err
    assert current.resolve() == old_target.resolve()
    assert (current / "proof.txt").read_text(encoding="utf-8") == "old runtime\n"
    assert (
        installer._tools_install_lease_path(current).read_text(encoding="utf-8") == ""
    )


def test_compact_install_refreshes_current_tools_from_local_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    source = tmp_path / "checkout"
    crafted_home = home / ".vibecrafted"
    runtime_tools = home / ".local" / "share" / "vibecrafted" / "tools"
    old_target = runtime_tools / "vibecrafted-main"
    current_link = runtime_tools / "vibecrafted-current"

    _write_complete_source(
        source,
        helper='printf "fresh installed helper\\n"\n',
        launcher='#!/usr/bin/env bash\nprintf "fresh installed launcher\\n"\n',
    )
    (old_target / "runtime" / "shell").mkdir(parents=True)
    (old_target / "scripts").mkdir(parents=True)
    (old_target / "runtime" / "shell" / "vetcoders.sh").write_text(
        'printf "stale staged helper\\n"\n', encoding="utf-8"
    )
    (old_target / "scripts" / "vibecrafted").write_text(
        'printf "stale staged launcher\\n"\n', encoding="utf-8"
    )
    current_link.parent.mkdir(parents=True, exist_ok=True)
    current_link.symlink_to(old_target)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))
    monkeypatch.setattr(
        installer,
        "detect_system_deps",
        lambda: {"python3": "/usr/bin/python3", "git": "/usr/bin/git", "rsync": None},
    )
    monkeypatch.setattr(
        installer,
        "detect_agent_runtimes",
        lambda: {"claude": None, "codex": None, "gemini": None},
    )
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(installer, "run_doctor", lambda _store, _state: [])
    monkeypatch.setattr(
        installer,
        "write_start_here_guide",
        lambda _store, _state, _findings: crafted_home / "START_HERE.md",
    )

    exit_code = installer._cmd_install_compact(
        Namespace(dry_run=False, mirror=True, with_shell=False),
        source,
    )

    assert exit_code == 0
    assert (
        current_link / "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
    ).read_text(encoding="utf-8") == 'printf "fresh installed helper\\n"\n'
    assert (current_link / "scripts" / "vibecrafted").read_text(
        encoding="utf-8"
    ) == '#!/usr/bin/env bash\nprintf "fresh installed launcher\\n"\n'
    package_skills = current_link / "vibecrafted-core" / "vibecrafted_core" / "skills"
    assert (package_skills / "vc-init" / "SKILL.md").is_file()
    assert not (current_link / "skills").exists()
    assert (crafted_home / installer.STATE_FILE).is_file()
    assert not (package_skills / installer.STATE_FILE).exists()


def _build_symlinked_skill_store(tmp_path: Path) -> tuple[Path, Path]:
    """Wire vibecrafted-current -> vibecrafted-main so the skill store and the
    install source resolve to the same inode (portable-CI staging shape)."""
    main = tmp_path / "vibecrafted-main"
    skills = main / "skills"
    skills.mkdir(parents=True)
    for filename in installer.SKILL_ROOT_RULE_FILES:
        (skills / filename).write_text(f"{filename}\n", encoding="utf-8")
    for localized in installer.LOCALIZED_SKILL_RULE_DIRS:
        (skills / localized).mkdir(parents=True, exist_ok=True)
        for filename in installer.SKILL_ROOT_RULE_FILES:
            (skills / localized / filename).write_text(
                f"{localized}/{filename}\n", encoding="utf-8"
            )
    current = tmp_path / "vibecrafted-current"
    current.symlink_to(main)
    # source_skills_root(...).resolve() is what the installer passes as the
    # source; the store comes from the unresolved current-link path -> same
    # inode via two different string paths.
    source = (current / "skills").resolve()
    store = current / "skills"
    return source, store


def test_sync_skill_root_rules_skips_same_inode_store(tmp_path: Path) -> None:
    """Regression: a symlinked store (vibecrafted-current -> vibecrafted-main)
    made copy2 raise shutil.SameFileError during the portable "skills and
    launchers" phase. The sync must treat the self-copy as a no-op."""
    source, store = _build_symlinked_skill_store(tmp_path)

    copied = installer.sync_skill_root_rules(source, store, dry_run=False)

    # All rule files are still reported as synced (they already exist in place).
    expected = {p for _src, p in installer.iter_skill_root_rule_files(source)}
    assert set(copied) == expected
    for filename in installer.SKILL_ROOT_RULE_FILES:
        assert (store / filename).read_text(encoding="utf-8") == f"{filename}\n"


def test_rsync_skill_skips_same_inode_dir(tmp_path: Path, monkeypatch) -> None:
    """Regression: the shutil fallback copied a skill dir onto itself (and under
    --mirror rmtree'd the source) when the store symlinked back to the source."""
    source, store = _build_symlinked_skill_store(tmp_path)
    # A skill living inside the skills dir: source/vc-demo (real) and
    # store/vc-demo (via the current-link symlink) are the same inode.
    src_skill = source / "vc-demo"
    src_skill.mkdir()
    (src_skill / "SKILL.md").write_text("demo\n", encoding="utf-8")
    dst_skill = store / "vc-demo"

    # Force the pure-Python fallback path (no rsync) with mirror=True — the most
    # destructive shape (rmtree of dst == the source) — and assert the source
    # survives untouched.
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    installer.rsync_skill(src_skill, dst_skill, dry_run=False, mirror=True)

    assert (src_skill / "SKILL.md").read_text(encoding="utf-8") == "demo\n"
