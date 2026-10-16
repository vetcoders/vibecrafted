"""Detached Runtime Pack fixtures using the real materializer and manifest validators.

Native host bytes are type probes only; tests never execute them. Python verifier
entrypoints are copied from the repository and run through the test interpreter.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Callable
from pathlib import Path

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[1]


def framework_python_delegate() -> Path | None:
    """The exact Framework app a Homebrew ``python3.x`` trampoline posix_spawns.

    ``bin/python3.14`` admitted alone is not enough under a process-exec fence:
    it immediately spawns this one executable. Nothing broader is implied.
    """
    if not sysconfig.get_config_var("PYTHONFRAMEWORK"):
        return None
    delegate = Path(sys.base_prefix) / "Resources/Python.app/Contents/MacOS/Python"
    assert delegate.is_file()
    return delegate.resolve()


def private_native_copy(
    source: Path,
    destination: Path,
    probe_args: tuple[str, ...],
    *,
    sign_command: Callable[[list[str]], list[str]] = lambda command: command,
) -> Path:
    """Copy one named system executable to a private path where it really runs.

    The copy is ad-hoc signed locally, then accepted only after an actual exec
    with ``probe_args`` returns 0 and prints. Candidates: the whole file, then
    the host-native thin slice when the file carries one; never an emulated
    architecture. Every candidate is built in a private directory this call
    creates and removes; ``destination`` is linked in only if it does not
    already exist (symlinks included), so a refusal never touches it.

    Hosted macOS 15 killed the whole-file copy of /bin/ps and /bin/bash
    (SIGKILL before main). Working hypothesis, unproven until that runner
    executes a successor: macOS 15 ships these as x86_64 + arm64e only and
    refuses a non-Apple arm64e slice. The failure message therefore records
    host, architectures and each attempt's return code for that falsification.
    """
    assert sys.platform == "darwin"
    if os.path.lexists(destination):
        raise AssertionError(f"refusing to replace pre-existing {destination}")
    archs = subprocess.run(
        ["/usr/bin/lipo", "-archs", str(source)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    candidates: list[str | None] = [
        None,
        *(arch for arch in (platform.machine(),) if arch in archs),
    ]
    attempts: list[str] = []
    work = Path(tempfile.mkdtemp(prefix=".native-copy-", dir=destination.parent))
    try:
        for arch in candidates:
            candidate = work / f"{arch or 'whole'}-{source.name}"
            if arch is None:
                shutil.copyfile(source, candidate)
                assert candidate.read_bytes() == source.read_bytes()
            else:
                subprocess.run(
                    [
                        "/usr/bin/lipo",
                        str(source),
                        "-thin",
                        arch,
                        "-output",
                        str(candidate),
                    ],
                    check=True,
                    capture_output=True,
                )
            candidate.chmod(0o755)
            subprocess.run(
                sign_command(
                    ["/usr/bin/codesign", "--force", "--sign", "-", str(candidate)]
                ),
                check=True,
                capture_output=True,
            )
            probe = subprocess.run(
                [str(candidate), *probe_args],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                os.link(candidate, destination)
                return destination
            attempts.append(
                f"{arch or 'whole-file'}: rc={probe.returncode} "
                f"{probe.stderr.strip()[:120]!r}"
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    raise AssertionError(
        f"no runnable private copy of {source} (archs={archs}, "
        f"host={platform.machine()} macOS {platform.mac_ver()[0]}): "
        + "; ".join(attempts)
    )


def private_child_command(
    command: list[str],
    *,
    allowed: tuple[Path, ...] = (),
    fixture_root: Path | None = None,
    same_sandbox_signals: bool = False,
) -> list[str]:
    """Fence only a disposable fixture child, including its absolute execs.

    On Darwin missing OS enforcement is a refusal, never a PATH-only approximation.
    Non-Darwin preserves the original argv; no portable fence is claimed.
    The literals are read-only tools, the test interpreter and explicit private
    witnesses. GUI/service executables and outgoing network are never admitted.
    Signals are hard-denied unless ``same_sandbox_signals`` opts a lifecycle
    test in: the child may then signal only its own fenced descendants.
    """
    # Parent-authorized preservation of the existing portable subprocess seam.
    # This branch provides no Linux OS fence and is not verified by this worker.
    if sys.platform != "darwin":
        return command
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("private child requires a working Seatbelt execution fence")
    tools = (
        "/bin/bash",
        "/bin/sh",
        "/bin/zsh",
        "/bin/mkdir",
        "/bin/chmod",
        "/bin/rm",
        "/bin/mv",
        "/bin/cp",
        "/bin/cat",
        "/bin/sleep",
        "/bin/date",
        "/usr/bin/env",
        "/usr/bin/dirname",
        "/usr/bin/basename",
        "/usr/bin/stat",
        "/usr/bin/id",
        "/usr/bin/uname",
        "/usr/bin/sed",
        "/usr/bin/awk",
        "/usr/bin/grep",
        "/usr/bin/head",
        "/usr/bin/tail",
        "/usr/bin/sort",
        "/usr/bin/find",
        "/usr/bin/tar",
        "/usr/bin/mktemp",
        "/usr/bin/openssl",
        "/usr/bin/shasum",
        "/usr/bin/perl",
        "/usr/bin/python3",
        "/usr/bin/wc",
        "/usr/bin/tr",
        "/usr/bin/ditto",
        "/usr/bin/plutil",
        "/usr/bin/uuidgen",
    )
    paths = {
        p
        for tool in tools
        if Path(tool).is_file()
        for p in (Path(tool), Path(tool).resolve())
    }
    paths.update((Path(sys.executable).resolve(), *(p.resolve() for p in allowed)))
    if framework_python := framework_python_delegate():
        paths.add(framework_python)
    for python in (
        Path("/Library/Developer/CommandLineTools/usr/bin/python3"),
        Path(
            "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/Current/Resources/Python.app/Contents/MacOS/Python"
        ),
    ):
        if python.is_file():
            paths.add(python.resolve())
    # Hosted macOS selects Homebrew perl/shasum before their system twins.
    # Admit these exact selected executables, retaining the exec deny for all
    # other paths (and the network/Mach/signal/Applications boundaries).
    for name in ("python3", "openssl", "bash", "perl", "shasum"):
        selected = shutil.which(name)
        if selected:
            paths.update((Path(selected), Path(selected).resolve()))
    literals = " ".join(f"(literal {json.dumps(str(p))})" for p in sorted(paths))
    if fixture_root is not None:
        root = fixture_root.resolve()
        assert root != Path("/") and root != Path("/Applications")
        # The real materializer chooses a random staging name. Its only
        # executable fixture Python shim is source-sealed before this child.
        verbs = "|".join(
            re.escape(name) for name in sorted(installer._RUNTIME_WRAPPER_VERBS)
        )
        verbs = "|".join(
            re.escape(name) for name in sorted(installer._RUNTIME_WRAPPER_VERBS)
        )
        pattern = (
            "^"
            + re.escape(str(root)).replace(r"\-", "-")
            + r"/([^/]+/)*(bin/(python3|vibecrafted|vibecrafted-mcp|vc-frame|vc-server|vc-server-supervisor|vc-start|vc-workflow|vc-terminal|"
            + verbs
            + "|"
            + re.escape(installer.SECURE_WALKAROUND_LAUNCHER)
            + r")|libexec/(vc-frame|vc-terminal)|(config/alacritty|vc-terminal)/launch-primary-shell[.]zsh)$"
        )
        literals += f" (regex {json.dumps(pattern)})"
    signals = "(allow signal (target same-sandbox))" if same_sandbox_signals else ""
    profile = (
        "(version 1)(allow default)(deny network*)(deny signal)(deny mach-lookup)"
        f"{signals}"
        '(deny file-read* file-write* (subpath "/Applications"))'
        f"(deny process-exec)(allow process-exec {literals})"
    )
    return ["/usr/bin/sandbox-exec", "-p", profile, *command]


def sign_private_terminal_pair(*paths: Path) -> None:
    """One identical private ad-hoc signature for byte-identical terminal fixtures.

    Admission pairs the bundle host with the generation's own terminal by
    verified code identity; recording scripts carry no signature of their own.
    """
    for path in paths:
        assert not path.resolve().is_relative_to(Path("/Applications"))
        subprocess.run(
            [
                "/usr/bin/codesign",
                "--force",
                "-s",
                "-",
                "-i",
                "io.vetcoders.vibecrafted.vc-terminal-fixture",
                str(path),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )


def bind_private_terminal_wrapper(wrapper: Path, native: Path) -> Path:
    """Bind only a copied wrapper to a private Main bundle, retaining its checks.

    The argv/environment tests use a recording executable, not the GUI host.
    Production paths and signer policy remain untouched.
    """
    assert wrapper != REPO_ROOT / "scripts/vc-terminal-product-entry.sh"
    bundle = wrapper.parents[2] / "Applications/Vibecrafted.app"
    assert bundle.is_absolute() and bundle != Path("/Applications/Vibecrafted.app")
    host = bundle / "Contents/MacOS/vc-terminal"
    host.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native, host)
    host.chmod(0o755)
    if sys.platform == "darwin":
        sign_private_terminal_pair(native, host)
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.vetcoders.vibecrafted",
                "CFBundleExecutable": "Vibecrafted",
            }
        )
    )
    text = wrapper.read_text()
    for old, replacement in (
        (
            "bundle=/Applications/Vibecrafted.app",
            "bundle=" + installer.shlex_quote(str(bundle)),
        ),
        (
            "candidate=/Applications/Vibecrafted.app/Contents/MacOS/vc-terminal",
            "candidate=" + installer.shlex_quote(str(host)),
        ),
    ):
        assert text.count(old) == 1
        text = text.replace(old, replacement, 1)
    wrapper.write_text(text)
    # The private bundle host is admitted like the fixed one: the generation
    # carries its admission helper and interpreter. Admission state is keyed
    # by this private host path, never the installed host's namespace.
    generation = wrapper.parents[1]
    helper = generation / "vibecrafted-core/vibecrafted_core/terminal_host.py"
    if not helper.exists():
        helper.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            REPO_ROOT / "vibecrafted-core/vibecrafted_core/terminal_host.py", helper
        )
    python = generation / "bin/python3"
    if not python.exists():
        python.symlink_to(sys.executable)
    return host if sys.platform == "darwin" else native


def bind_private_child_installer(
    payload: Path, host: Path, *, fixture_root: Path | None = None
) -> None:
    """Bind a copied owner before its source/crypto seal, never the real owner.

    Reuse the publication-hold fixture's pre-main instrumentation seam. Only
    this private app and private native terminal namespace receive the signer
    boundary double; all real admission, provenance and publication code runs.
    """
    root = (fixture_root or host.parents[4]).resolve()
    assert host.is_relative_to(root) and root != Path("/")
    assert host != Path("/Applications/Vibecrafted.app/Contents/MacOS/vc-terminal")
    target = payload / "scripts/vetcoders_install.py"
    source = target.read_text(encoding="utf-8")
    guard = 'if __name__ == "__main__":\n    sys.exit(main())\n'
    assert source.count(guard) == 1
    binding = f"""
# Test-private binding, included in source provenance before publication.
_FIXED_TERMINAL_HOST = Path({str(host)!r})
_VC_FIXTURE_ROOT = Path({str(root)!r})
def _vc_fixture_signed_terminal_identity(path):
    path = Path(path)
    if path == _FIXED_TERMINAL_HOST.parents[2]:
        path = _FIXED_TERMINAL_HOST
    elif (path.is_symlink() or not path.resolve().is_relative_to(_VC_FIXTURE_ROOT)
          or path.name != "vc-terminal" or path.parent.name not in ("MacOS", "bin", "libexec")):
        raise RuntimeInstallRefusal("fixture signer refuses foreign terminal identity")
    if not _is_native_executable(path):
        raise RuntimeInstallRefusal("fixture signer requires real native terminal bytes")
    return hashlib.sha256(path.read_bytes()).hexdigest(), "FIXTURE_TEAM"
_signed_terminal_identity = _vc_fixture_signed_terminal_identity

"""
    target.write_text(source.replace(guard, binding + guard, 1), encoding="utf-8")


def private_runtime_pack_seed(host: Path, *, fixture_root: Path | None = None):
    """Compose a module's explicit child binding with its existing seal hook."""

    def seed(payload: Path, *, before_source_seal=None, **options):
        def before(root: Path):
            if before_source_seal is not None:
                before_source_seal(root)
            bind_private_child_installer(root, host, fixture_root=fixture_root)

        return seed_runtime_pack(payload, before_source_seal=before, **options)

    return seed


def fixed_terminal_fixture(monkeypatch, tmp_path: Path) -> None:
    """Pair installer fixtures with a private main app, never /Applications.

    The signer's CodeDirectory/Team probe is a test boundary double; native
    signature/process acceptance is separately tested and remains parent-owned.
    """
    host = tmp_path / "Applications/Vibecrafted.app/Contents/MacOS/vc-terminal"
    host.parent.mkdir(parents=True)
    shutil.copyfile("/usr/bin/true", host)
    host.chmod(0o755)
    contents = host.parent.parent
    (contents / "Resources").mkdir()
    (contents / "Resources/Vibecrafted.icns").write_bytes(b"fixture-icon")
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.vetcoders.vibecrafted",
                "CFBundleExecutable": "Vibecrafted",
                "CFBundleName": "Vibecrafted",
                "CFBundleDisplayName": "Vibecrafted",
                "CFBundleIconFile": "Vibecrafted.icns",
            }
        )
    )
    monkeypatch.setattr(installer, "_FIXED_TERMINAL_HOST", host)

    def code_identity(path):
        if path.is_dir():
            return hashlib.sha256(host.read_bytes()).hexdigest(), "FIXTURE_TEAM"
        return hashlib.sha256(path.read_bytes()).hexdigest(), "FIXTURE_TEAM"

    monkeypatch.setattr(installer, "_signed_terminal_identity", code_identity)


def _write_test_source_provenance(
    root: Path,
    *,
    owner_repo: str = "vetcoders/vibecrafted",
    source_revision: str = "b" * 40,
) -> dict[str, object]:
    """Mint a test-only carrier for a detached fixture's current input tree."""
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


_RUNTIME_GENERATION_FIXTURE_SOURCES = {
    Path("VERSION"): Path("VERSION"),
    Path("scripts/distribution_manifest.py"): Path("scripts/distribution_manifest.py"),
    Path("scripts/installer_brand.py"): Path("scripts/installer_brand.py"),
    Path("scripts/vetcoders_install.py"): Path("scripts/vetcoders_install.py"),
    Path("scripts/vibecrafted"): Path("scripts/vibecrafted"),
    Path(
        "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    ): Path("vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"),
    installer._RUNTIME_GENERATION_ENTRYPOINT: Path(
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
    ),
    Path("vibecrafted-core/vibecrafted_core/product_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/product_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/walkaround_runner.py"): Path(
        "vibecrafted-core/vibecrafted_core/walkaround_runner.py"
    ),
    Path(
        "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"
    ): Path("vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"),
    Path("vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"): Path(
        "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"
    ),
    Path("vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"): Path(
        "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
    ),
}


SYNTHETIC_FOUNDATIONS_INSTALLER_BODY = """#!/bin/sh
# Synthetic test-only foundations installer (offline fixture).
# Note: this is synthetic testing, not real foundation acceptance.
if [ -n "$VIBECRAFTED_TEST_FOUNDATIONS_FAIL" ]; then
    echo "synthetic foundations installer failure: $VIBECRAFTED_TEST_FOUNDATIONS_FAIL" >&2
    exit "${VIBECRAFTED_TEST_FOUNDATIONS_EXIT_CODE:-1}"
fi
if [ -n "$VIBECRAFTED_TEST_FOUNDATIONS_OBSERVE" ]; then
    echo "observation: $VIBECRAFTED_TEST_FOUNDATIONS_OBSERVE"
fi
exit_code="${VIBECRAFTED_TEST_FOUNDATIONS_EXIT_CODE:-0}"
if [ "$exit_code" -ne 0 ]; then
    echo "synthetic foundations installer intentional exit: $exit_code" >&2
    exit "$exit_code"
fi
echo "synthetic foundations installed (test-only)"
exit 0
"""


def seed_runtime_pack(
    payload: Path,
    *,
    version: str = "9.9.9+g12345678",
    frame_config: str | None = None,
    terminal_policy: str | None = None,
    before_source_seal: Callable[[Path], None] | None = None,
    include_foundations_installer: bool = True,
    foundations_installer_content: str | None = None,
) -> Path:
    """Supply real manifest inputs; publication performs its own sealing.

    ``before_source_seal`` mutates source inputs before provenance bind.
    Native donors are added after that bind and must not be resealed as source.
    """
    for destination, source in _RUNTIME_GENERATION_FIXTURE_SOURCES.items():
        if (
            "generated" in destination.parts
            or destination == installer._RUNTIME_GENERATION_ENTRYPOINT
        ):
            continue
        target = payload / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source, target)
    for relative in (
        "vibecrafted-core/vibecrafted_core/vc_frame_staging.py",
        "vibecrafted-core/vibecrafted_core/runtime_paths.py",
        "vibecrafted-core/vibecrafted_core/terminal_host.py",
        "scripts/vc-terminal-product-entry.sh",
        "scripts/vc-frame-product-entry.sh",
        "scripts/lib/runtime-roots.sh",
        "config/starship.toml",
        "config/atuin/config.toml",
    ):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    for relative in (
        "vibecrafted-core/vibecrafted_core/config/vc-frame",
        "config/vc-terminal",
        "config/runtime-pin",
        "vibecrafted-core/vibecrafted_core/runtime/shell/aliases",
    ):
        shutil.copytree(
            REPO_ROOT / relative,
            payload / relative,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.py[co]", ".DS_Store"),
        )
    for relative in (
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted",
        "vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh",
        "config/alacritty/launch-primary-shell.zsh",
    ):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    for name in (
        "vibecrafted",
        "vibecrafted-mcp",
        "vc-server",
        "vc-server-supervisor",
        "vc-start",
        "vc-workflow",
    ):
        target = payload / "bin" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
    python = payload / "bin/python3"
    # Match the shipped Python door: verification must not add bytecode to the
    # signed payload it is checking, even when the caller scrubs PYTHON* env.
    python.write_text(
        f'#!/bin/sh\nexec {installer.shlex_quote(sys.executable)} -B "$@"\n'
    )
    python.chmod(0o755)
    skills = payload / "vibecrafted-core/vibecrafted_core/skills"
    for name in ("vc-audit", "vc-implement"):
        target = skills / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n")
    (skills / "VERIFICATION_RULE.md").write_text("# Verification rule\n")
    (payload / "VERSION").write_text(version + "\n")
    for relative, content in (
        ("vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl", frame_config),
        ("config/vc-terminal/vibecrafted.toml", terminal_policy),
    ):
        if content is not None:
            (payload / relative).write_text(content, encoding="utf-8")
    foundations_target = payload / "scripts/install-foundations.sh"
    if include_foundations_installer:
        foundations_target.parent.mkdir(parents=True, exist_ok=True)
        foundations_target.write_text(
            foundations_installer_content
            if foundations_installer_content is not None
            else SYNTHETIC_FOUNDATIONS_INSTALLER_BODY,
            encoding="utf-8",
        )
        foundations_target.chmod(0o755)
    elif foundations_target.exists():
        foundations_target.unlink()
    if before_source_seal is not None:
        before_source_seal(payload)
    # Bind the distribution input before adding native donor payload.
    _write_test_source_provenance(payload)
    for relative in ("bin/vc-terminal", "libexec/vc-frame"):
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # A real native file, never launched; avoids accepting a shell as Frame.
        shutil.copyfile("/usr/bin/true", target)
        target.chmod(0o755)
    return payload


def stage_build_lease_fixture(repo: Path) -> None:
    """Stage the real lease entrypoint and its eager import closure."""
    for name in ("vc-build-lease", "project-python"):
        shutil.copy2(REPO_ROOT / "scripts" / name, repo / "scripts" / name)
    package = repo / "vibecrafted-core/vibecrafted_core"
    package.mkdir(parents=True, exist_ok=True)
    # Avoid unrelated runtime bootstrap; supervisor still gets the real version.
    version = (REPO_ROOT / "VERSION").read_text().strip()
    (package / "__init__.py").write_text(f"__version__ = {version!r}\n")
    for name in (
        "cache_lifecycle",
        "clock",
        "portable_lock",
        "report_contract",
        "run_mutation",
        "run_triage",
        "runtime_transcript",
        "server_config",
        "server_supervisor",
        "settlement",
    ):
        shutil.copy2(
            REPO_ROOT / "vibecrafted-core/vibecrafted_core" / f"{name}.py", package
        )


def seed_private_git_fixture(repo: Path, *tracked: str) -> str:
    """Create a private repository without inheriting Git routing or policy."""
    import subprocess

    env = private_git_fixture_environment()
    for args in (
        ("init", "-q"),
        ("config", "user.email", "agents@vetcoders.io"),
        ("config", "user.name", "fixture"),
        ("add", "--", *tracked),
        ("commit", "-q", "-m", "fixture"),
    ):
        subprocess.run(
            ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True
        )
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], env=env, text=True
    ).strip()


def private_git_fixture_environment() -> dict[str, str]:
    """Preserve ordinary child environment, fence inherited Git routing/policy."""
    import os

    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env
