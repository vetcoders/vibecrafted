"""Cold channel integration with explicit OS/product boundary doubles.

The real bootstrap, RSA source admission, source tree checks, App updater file
transaction/journal and interpreter selection execute. The dedicated real-pack
case also runs inventory admission, publication, resolution and settlement.
Developer ID/notarization, running-App inventory, GUI launch and portable-Python
delegation are doubles. Final release-artifact acceptance belongs to the integrator.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tui.test_install_bootstrap import (
    _run_with_tty,
    _write_distribution_manifest_stub,
    _write_executable,
    _write_fake_release_curl,
    _write_v2_archive,
)

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "scripts/lib/app-main-identity.py"
# Development-only baseline falsification; never a production bootstrap switch.
BOOTSTRAP = Path(os.environ.get("VC_TEST_BOOTSTRAP", ROOT / "install.sh"))
UPDATER = Path(os.environ.get("VC_TEST_UPDATER", ROOT / "scripts/vc-app-update.sh"))


def _python_script(path: Path, body: str) -> None:
    _write_executable(path, f"#!{sys.executable}\n" + body)


def _cold_fixture(tmp_path: Path, *, damage="none"):
    home, server, tools = (tmp_path / name for name in ("home", "server", "tools"))
    for directory in (home, server, tools):
        directory.mkdir()
    destination = tmp_path / "Applications/Vibecrafted.app"
    destination.parent.mkdir()
    app = tmp_path / "dmg/Vibecrafted.app"
    runtime = app / "Contents/Resources/runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "libexec").mkdir()
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Helpers").mkdir()
    for name in ("vc-terminal", "vc-frame"):
        for path in (
            runtime / "libexec" / name,
            app
            / ("Contents/MacOS" if name == "vc-terminal" else "Contents/Helpers")
            / name,
        ):
            _write_executable(path, "#!/bin/sh\nexit 0\n")
    # Interpreter delegation is the portable-carrier double, never a PATH Python.
    _write_executable(
        runtime / "bin/python3",
        '#!/bin/bash\necho carrier-python >> "$EVENTS"\nexec '
        + shlex.quote(sys.executable)
        + ' "$@"\n',
    )
    package = runtime / "vibecrafted-core/vibecrafted_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "runtime_pack_contract.py").write_text(
        "import pathlib,sys\n"
        "a=pathlib.Path(sys.argv[sys.argv.index('--app-copy')+1])\n"
        "b=pathlib.Path(sys.argv[sys.argv.index('--pack-copy')+1])\n"
        "assert a.read_bytes() == b.read_bytes()\n"
    )
    pack_dir = app / "Contents/Resources/runtime-pack"
    pack_dir.mkdir()
    pack = server / "Vibecrafted_RuntimePack_fixture-darwin-arm64.tar.gz"
    from tests.tui.test_install_bootstrap import _write_host_pack_double

    _write_host_pack_double(pack, "darwin-arm64", "arm64")
    bundled = pack_dir / pack.name
    shutil.copy2(pack, bundled)
    if damage == "mismatch":
        bundled.write_bytes(b"different signed App generation\n")
    for suffix in (".sig", ".sha256"):
        Path(str(pack) + suffix).write_text("pack-sidecar-double\n")

    # Real RSA source authentication, with an ephemeral fixture key sealed by
    # the explicit codesign/notary double, never a production trust override.
    key = tmp_path / "fixture.key"
    public = pack_dir / "vibecrafted-signing-v1.pub"
    for command in (
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        ["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
    ):
        subprocess.run(command, check=True, capture_output=True)
    source = tmp_path / "source"
    scripts = source / "scripts"
    (scripts / "lib").mkdir(parents=True)
    helper = UPDATER.read_text()
    helper = helper.replace("/usr/bin/codesign", shlex.quote(str(tools / "codesign")))
    if sys.platform != "darwin":
        # Linux has no ditto. Double only the filesystem-copy OS boundary;
        # the real updater still owns staging, capture, displacement and restore.
        _python_script(
            tools / "ditto",
            """import os,shutil,sys
from pathlib import Path
assert len(sys.argv) == 3
source, destination = (Path(arg).resolve() for arg in sys.argv[1:])
root = Path(os.environ['DMG_APP']).resolve().parents[1]
assert source.is_relative_to(root) and destination.is_relative_to(root)
assert source.is_dir() and source != destination
shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True)
with open(os.environ['EVENTS'], 'a') as log:
    log.write('ditto-copy ' + str(source) + ' -> ' + str(destination) + '\\n')
""",
        )
        assert "/usr/bin/ditto" in helper
        helper = helper.replace("/usr/bin/ditto", shlex.quote(str(tools / "ditto")))
    helper = helper.replace(
        'OPEN_BIN="/usr/bin/open"', f"OPEN_BIN={shlex.quote(str(tools / 'open'))}"
    )
    # Keep the real update owner, capture, adopt, journal, receipt and relaunch
    # paths. Replace only product publication/settlement boundary functions.
    boundary = '\ncomplete_replacement() { echo runtime-published >> "$EVENTS"; echo new-runtime > "$RUNTIME_POINTER"; }\nsettle_capture() { echo settlement-double >> "$EVENTS"; }\n'
    helper = helper.replace(
        'validate_transaction_id "$TRANSACTION"\nif [[ "$RESUME"',
        boundary + 'validate_transaction_id "$TRANSACTION"\nif [[ "$RESUME"',
        1,
    )
    _write_executable(scripts / "vc-app-update.sh", helper)
    _write_executable(
        scripts / "install-runtime-pack.sh",
        '#!/bin/bash\nset -e\nif [[ "$*" == *--verify-only* ]]; then\necho pack-verified >> "$EVENTS"\nelse\necho runtime-published >> "$EVENTS"\necho new-runtime > "$RUNTIME_POINTER"\nfi\n',
    )
    _write_executable(
        scripts / "install-foundations.sh",
        '#!/bin/bash\necho foundations-double >> "$EVENTS"\n',
    )
    # OS running-App inventory double: production ctypes adapter is exercised
    # independently below, and identity failures must stop the real bootstrap.
    (scripts / "lib/app-main-identity.py").write_text(
        "import os,sys\n"
        "from pathlib import Path\n"
        "with open(os.environ['EVENTS'],'a') as f: f.write('main-identity\\n')\n"
        "if os.environ['DAMAGE'] == 'ambiguous': raise SystemExit('ambiguous destination Main instances')\n"
    )
    _write_distribution_manifest_stub(source)
    archive = server / "portable.tar.gz"
    _write_v2_archive(source, archive, "vibecrafted-fixture")
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(key),
            "-out",
            str(archive) + ".sig",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    if damage == "source-signature":
        Path(str(archive) + ".sig").write_bytes(b"invalid signature")
    (server / "SHA256SUMS").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    (server / "main.json").write_text(
        json.dumps(
            {
                "archive_url": "https://fixture.invalid/portable.tar.gz",
                "runtime_pack_url": "https://fixture.invalid/" + pack.name,
                "dmg_url": "https://fixture.invalid/app.dmg",
            }
        )
    )
    (server / "app.dmg").write_text("DMG transport double")
    _write_fake_release_curl(tools)
    _write_executable(
        tools / "uname",
        '#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n',
    )
    _write_executable(
        tools / "python3",
        '#!/bin/bash\necho forbidden-host-python >> "$EVENTS"\nexit 99\n',
    )
    _write_executable(
        tools / "file", '#!/bin/sh\necho "Mach-O 64-bit executable arm64"\n'
    )
    for name in ("swift", "xcrun", "xcode-select", "make", "cargo", "brew"):
        _write_executable(
            tools / name, '#!/bin/bash\necho forbidden-tool >> "$EVENTS"\nexit 99\n'
        )
    _python_script(
        tools / "codesign",
        """import os,sys
from pathlib import Path
with open(os.environ['EVENTS'],'a') as f: f.write('codesign ' + ' '.join(sys.argv[1:-1]) + '\\n')
if os.environ['DAMAGE'] == 'trust': raise SystemExit(1)
# A foreign Developer ID is otherwise valid/notarized. Only the pinned
# requirement rejects it; removing -R makes this negative control fail.
if os.environ['DAMAGE'] == 'foreign' and '-R' in sys.argv: raise SystemExit(1)
if '--display' in sys.argv:
    print('Identifier=io.vetcoders.vibecrafted\\nTeamIdentifier=MW223P3NPX\\nCDHash=fixture-identity', file=sys.stderr)
""",
    )
    _write_executable(tools / "spctl", '#!/bin/bash\necho notarized >> "$EVENTS"\n')
    _write_executable(tools / "open", '#!/bin/bash\necho opened >> "$EVENTS"\n')
    _python_script(
        tools / "hdiutil",
        """import os,shutil,sys
from pathlib import Path
if sys.argv[1] == 'attach':
    mount=Path(sys.argv[sys.argv.index('-mountpoint')+1])
    shutil.copytree(os.environ['DMG_APP'], mount/'Vibecrafted.app')
""",
    )
    # Linux can execute the same bootstrap test through a strict native-reader
    # boundary double. On macOS the actual system plutil parses the JSON.
    if sys.platform != "darwin":
        _python_script(
            tools / "plutil",
            "import json,sys\nprint(json.load(open(sys.argv[-1]))['dmg_url'])\n",
        )
    install = tmp_path / "install.sh"
    install.write_text(
        BOOTSTRAP.read_text().replace(
            'PAIRED_APP_ROOT="/Applications/Vibecrafted.app"',
            f"PAIRED_APP_ROOT={shlex.quote(str(destination))}",
        )
    )
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = dict(
        os.environ,
        TMPDIR=str(temporary),
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        VIBECRAFTED_HOME=str(home / ".vibecrafted"),
        PATH=f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin",
        EVENTS=str(tmp_path / "events"),
        CURL_CAPTURE=str(tmp_path / "curl"),
        FAKE_RELEASE_ROOT=str(server),
        DMG_APP=str(app),
        DAMAGE=damage,
        RUNTIME_POINTER=str(tmp_path / "runtime-pointer"),
    )
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "UV_PROJECT_ENVIRONMENT",
        "VIBECRAFTED_UPDATE_PYTHON",
    ):
        env.pop(name, None)
    return install, destination, env


def _fenced(command: list[str]) -> list[str]:
    if sys.platform == "darwin":
        # Host Python/Swift/CLT executables are denied even if code bypasses
        # PATH. No Foundation installation or real /Applications mutation.
        profile = '(version 1)(allow default)(deny network*)(deny file-write* (subpath "/Applications"))(deny process-exec (literal "/usr/bin/python3") (literal "/usr/bin/swift") (literal "/usr/bin/xcrun") (literal "/usr/bin/xcode-select") (literal "/usr/bin/make"))'
        command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    return command


def _run_cold(install: Path, env: dict[str, str]):
    return subprocess.run(
        _fenced(["/bin/bash", str(install), "--yes"]),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.parametrize("interactive", [False, True])
def test_no_host_python_or_swift_cold_channel_install(
    tmp_path: Path, interactive: bool
) -> None:
    install, destination, env = _cold_fixture(tmp_path)
    if interactive:
        # Only the terminal-presence boundary is doubled. Both routes must
        # reach the same compiler-free pack owner, rather than the Make wizard.
        install.write_text(
            install.read_text().replace(
                "is_interactive_session() {\n  [[ -t 0 && -t 1 ]]\n}",
                "is_interactive_session() {\n  return 0\n}",
                1,
            )
        )
    result = _run_cold(install, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "Contents/MacOS/vc-terminal").is_file()
    events = Path(env["EVENTS"]).read_text().splitlines()
    assert not any("forbidden" in event for event in events)
    assert events.index("notarized") < events.index("carrier-python")
    assert any(
        'identifier "io.vetcoders.vibecrafted"' in event and "MW223P3NPX" in event
        for event in events
    )
    assert events.index("pack-verified") < events.index("runtime-published")
    assert "opened" in events
    receipts = list(Path(env["VIBECRAFTED_HOME"]).rglob("receipt.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["replaced"] is True and receipt["relaunched"] is True
    assert (
        json.loads(receipts[0].with_name("journal.json").read_text())["phase"]
        == "relaunched"
    )
    assert Path(env["RUNTIME_POINTER"]).read_text().strip() == "new-runtime"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native payload fixture")
def test_cold_channel_runs_real_runtime_pack_publication_and_settlement(
    tmp_path: Path,
) -> None:
    """Real pack signature/inventory/install/resolve and real App settlement.

    Native signing, GUI launch, process inventory and portable-Python delegation
    remain explicit fixture boundaries; these fixtures are not release artifacts.
    """
    from _runtime_pack_fixture import private_runtime_pack_seed

    from tests.tui.test_runtime_pack_rescue import _seal_runtime_pack_for_admission

    install, destination, env = _cold_fixture(tmp_path)
    source = tmp_path / "source"
    app = Path(env["DMG_APP"])
    server = Path(env["FAKE_RELEASE_ROOT"])
    tools = Path(env["PATH"].split(":")[0])
    payload = private_runtime_pack_seed(
        destination / "Contents/MacOS/vc-terminal", fixture_root=tmp_path
    )(tmp_path / "payload", version="9.9.9+cold")
    _seal_runtime_pack_for_admission(payload)
    pack_name = "Vibecrafted_RuntimePack_rescue-fixture-darwin-arm64.tar.gz"
    pack_provenance = payload / "runtime-pack-provenance.json"
    pack_record = json.loads(pack_provenance.read_text())
    pack_record["carrier_basename"] = pack_name
    pack_provenance.write_text(
        json.dumps(pack_record, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    )
    pack = server / pack_name
    with tarfile.open(pack, "w:gz") as archive:
        archive.add(payload, arcname="VibecraftedRuntime")
    public = app / "Contents/Resources/runtime-pack/vibecrafted-signing-v1.pub"
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(tmp_path / "fixture.key"),
            "-out",
            str(pack) + ".sig",
            str(pack),
        ],
        check=True,
        capture_output=True,
    )
    Path(str(pack) + ".sha256").write_text(
        f"{hashlib.sha256(pack.read_bytes()).hexdigest()}  {pack.name}\n"
    )
    pack_dir = public.parent
    for old in pack_dir.glob("*.tar.gz"):
        old.unlink()
    for suffix in ("", ".sig", ".sha256"):
        shutil.copy2(str(pack) + suffix, str(pack_dir / pack_name) + suffix)
    for scripts in (source / "scripts", pack_dir):
        shutil.copy2(ROOT / "scripts/install-runtime-pack.sh", scripts)
        shutil.copytree(
            ROOT / "scripts/lib",
            scripts / "lib",
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if scripts != pack_dir:
            shutil.copy2(public, scripts / "vibecrafted-signing-v1.pub")
    # Restore the actual app-update owner, including its real pack publication
    # and settlement functions. Redirect only system signing/open boundaries.
    helper = UPDATER.read_text().replace(
        "/usr/bin/codesign", shlex.quote(str(tools / "codesign"))
    )
    _write_executable(tools / "ps", "#!/bin/sh\nexit 0\n")
    helper = helper.replace("/bin/ps", str(tools / "ps"))
    helper = helper.replace(
        'OPEN_BIN="/usr/bin/open"', f"OPEN_BIN={shlex.quote(str(tools / 'open'))}"
    )
    _write_executable(source / "scripts/vc-app-update.sh", helper)
    signer = tools / "codesign"
    signer.write_text(
        signer.read_text().replace("fixture-identity", "abcdef0123456789")
    )
    runtime = app / "Contents/Resources/runtime"
    shutil.rmtree(runtime)
    shutil.copytree(payload, runtime)
    for name, target in (
        ("vc-terminal", app / "Contents/MacOS/vc-terminal"),
        ("vc-frame", app / "Contents/Helpers/vc-frame"),
    ):
        shutil.copy2(payload / "libexec" / name, target)
    # Product identity and source/module bindings are consumed by the actual
    # installer and capture settlement, rather than synthesized success bits.
    import plistlib

    (app / "Contents/Info.plist").write_bytes(
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
    (app / "Contents/Resources/Vibecrafted.icns").write_bytes(b"fixture-icon")
    (app / "Contents/Resources/product-manifest.json").write_text(
        json.dumps(
            {
                "schema": "io.vetcoders.vibecrafted.product.v1",
                "git_sha": "b" * 40,
                "modules": [
                    {"module": "vc-terminal", "git_sha": "2" * 40},
                    {"module": "vc-frame", "git_sha": "3" * 40},
                ],
            }
        )
    )
    _write_distribution_manifest_stub(source)
    provenance = source / "source-provenance.json"
    record = json.loads(provenance.read_text())
    record["source_revision"] = "b" * 40
    provenance.write_text(
        json.dumps(record, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    )
    archive = server / "portable.tar.gz"
    _write_v2_archive(source, archive, "vibecrafted-fixture")
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(tmp_path / "fixture.key"),
            "-out",
            str(archive) + ".sig",
            str(archive),
        ],
        check=True,
        capture_output=True,
    )
    (server / "SHA256SUMS").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    channel = server / "main.json"
    record = json.loads(channel.read_text())
    record["runtime_pack_url"] = "https://fixture.invalid/" + pack_name
    channel.write_text(json.dumps(record))
    env["VIBECRAFTED_RUNTIME_HOME"] = str(
        Path(env["HOME"]) / ".local/share/vibecrafted"
    )
    env["VIBECRAFTED_LAUNCHER_BIN"] = str(Path(env["HOME"]) / ".local/bin")
    result = _run_cold(install, env)
    assert result.returncode == 0, result.stdout + result.stderr
    runtime_home = Path(env["VIBECRAFTED_RUNTIME_HOME"])
    pointer = json.loads((runtime_home / "active.json").read_text())
    assert pointer["version"] == "9.9.9+cold" and pointer["app_root"] == str(
        destination
    )
    receipt = json.loads((runtime_home / "install-receipt.json").read_text())
    assert receipt["schema"] == "vibecrafted.runtime-install.v1"
    assert not receipt.get("install_pending")
    settlements = list(
        Path(env["VIBECRAFTED_HOME"]).rglob("receipt.json.settlement.json")
    )
    assert len(settlements) == 1
    assert json.loads(settlements[0].read_text())["status"] == "disposed"


def test_attended_cancel_fetches_no_dmg_or_carrier_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install, destination, env = _cold_fixture(tmp_path)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    command = (
        "env PATH="
        + shlex.quote(env["PATH"])
        + " "
        + shlex.join(_fenced(["/bin/bash", str(install)]))
    )
    status, output = _run_with_tty(command, response="n", timeout=15)
    assert status == 0 and output.count("Proceed? [y/N]") == 1
    assert not destination.exists()
    assert "app.dmg" not in Path(env["CURL_CAPTURE"]).read_text()
    assert not Path(env["EVENTS"]).exists()


@pytest.mark.parametrize("old_pack", [False, True])
def test_same_helpers_only_skip_app_transaction_for_identical_generation(
    tmp_path: Path, old_pack: bool
) -> None:
    install, destination, env = _cold_fixture(tmp_path)
    shutil.copytree(env["DMG_APP"], destination)
    bundled = next((destination / "Contents/Resources/runtime-pack").glob("*.tar.gz"))
    if old_pack:
        bundled.write_bytes(b"prior-generation-with-the-same-donors")
        (destination / "prior-state").write_text("old Main and modules")
    result = _run_cold(install, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        bundled.read_bytes()
        == (Path(env["FAKE_RELEASE_ROOT"]) / bundled.name).read_bytes()
    )
    receipts = list(Path(env["VIBECRAFTED_HOME"]).rglob("receipt.json"))
    assert len(receipts) == (1 if old_pack else 0)
    assert not (destination / "prior-state").exists()
    assert Path(env["RUNTIME_POINTER"]).read_text().strip() == "new-runtime"


@pytest.mark.parametrize(
    "damage", ["trust", "foreign", "source-signature", "mismatch", "ambiguous"]
)
def test_failed_trust_or_ambiguous_owner_preserves_prior(
    tmp_path: Path, damage: str
) -> None:
    install, destination, env = _cold_fixture(tmp_path, damage=damage)
    destination.mkdir()
    sentinel = destination / "prior-state"
    sentinel.write_bytes(b"prior App")
    pointer = Path(env["RUNTIME_POINTER"])
    pointer.write_bytes(b"prior runtime")
    result = _run_cold(install, env)
    assert result.returncode != 0
    assert sentinel.read_bytes() == b"prior App"
    assert pointer.read_bytes() == b"prior runtime"
    assert list(destination.iterdir()) == [sentinel]
    assert not list(Path(env["VIBECRAFTED_HOME"]).rglob("receipt.json"))
    events = Path(env["EVENTS"]).read_text()
    assert "forbidden" not in events
    assert "runtime-published" not in events
    if damage in ("trust", "foreign"):
        assert "carrier-python" not in events


def test_app_owned_updater_without_bootstrap_env_resumes_and_restores(
    tmp_path: Path,
) -> None:
    _install, destination, env = _cold_fixture(tmp_path)
    source = Path(env["DMG_APP"])
    shutil.copytree(source, destination)
    # The signature double binds a different durable identity to the prior App.
    (destination / "prior-state").write_text("prior")
    signer = Path(env["PATH"].split(":")[0]) / "codesign"
    text = signer.read_text().replace(
        "CDHash=fixture-identity', file=sys.stderr)",
        "CDHash=' + ('prior-identity' if (Path(sys.argv[-1])/'prior-state').exists() else 'fixture-identity'), file=sys.stderr)",
    )
    signer.write_text(text)
    helper = tmp_path / "source/scripts/vc-app-update.sh"
    receipt, journal = tmp_path / "receipt.json", tmp_path / "journal.json"
    env["VIBECRAFTED_UPDATE_HELPER_HARNESS"] = "1"
    # No bootstrap-selected Python, and the fake PATH Python is unusable. The
    # owner must authenticate/copy the App interpreter on every detached entry.
    assert "VIBECRAFTED_UPDATE_PYTHON" not in env
    argv = [
        "/bin/bash",
        str(helper),
        "--source",
        str(source),
        "--destination",
        str(destination),
        "--receipt",
        str(receipt),
        "--journal",
        str(journal),
        "--transaction",
        "cold-resume-123",
    ]
    first = subprocess.run(
        _fenced([*argv, "--fail-after", "displaced"]),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert first.returncode == 40, first.stdout + first.stderr
    assert not destination.exists()
    assert json.loads(journal.read_text())["phase"] == "displaced"
    second = subprocess.run(
        _fenced([*argv, "--resume"]),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert receipt.is_file() and destination.is_dir()
    assert not (destination / "prior-state").exists()
    prior = destination.parent / ".vc-update-capture-cold-resume-123/prior.app"
    recovery_receipt = tmp_path / "restored.json"
    restored = subprocess.run(
        _fenced(
            [
                "/bin/bash",
                str(helper),
                "--source",
                str(prior),
                "--destination",
                str(destination),
                "--receipt",
                str(recovery_receipt),
                "--journal",
                str(journal),
                "--transaction",
                "cold-resume-123",
                "--mode",
                "restore",
            ]
        ),
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert (destination / "prior-state").read_text() == "prior"
    assert json.loads(recovery_receipt.read_text())["detail"] == "restored"
    assert Path(str(journal) + ".replace.json").is_file()
    # The exact App copies are verified before any portable interpreter call.
    events = Path(env["EVENTS"]).read_text().splitlines()
    if sys.platform != "darwin":
        assert any(event.startswith("ditto-copy ") for event in events)
    assert events.index("carrier-python") > next(
        i for i, event in enumerate(events) if "--deep" in event
    )


def _probe_module():
    spec = importlib.util.spec_from_file_location("installer_main_identity", PROBE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("team", ["MW223P3NPX", "FOREIGN_TEAM"])
def test_main_metadata_requires_product_publisher(team: str) -> None:
    probe = _probe_module()
    api = probe.MacOS.__new__(probe.MacOS)

    def signing_information(_code, flags, _output):
        assert flags == 1 << 1  # Security omits TeamIdentifier without this flag
        return 0

    api.security = SimpleNamespace(
        SecStaticCodeCreateWithPath=lambda *_: 0,
        SecStaticCodeCheckValidity=lambda *_: 0,
        SecCodeCopySigningInformation=signing_information,
    )
    api.cf = SimpleNamespace(CFRelease=lambda *_: None)
    metadata = {
        "kSecCodeInfoIdentifier": probe.IDENTIFIER,
        "kSecCodeInfoTeamIdentifier": team,
        "kSecCodeInfoPList": "plist",
        "CFBundleIdentifier": probe.IDENTIFIER,
        "CFBundleExecutable": "Main",
        "kSecCodeInfoMainExecutable": "/fixture.app/Contents/MacOS/Main",
    }
    api.item = lambda _dictionary, key, **_: metadata[key]
    api.text = api.path = lambda value: value
    if team == probe.TEAM:
        assert api.signed_main("/fixture.app") == "/fixture.app/Contents/MacOS/Main"
    else:
        with pytest.raises(ValueError, match="foreign destination publisher"):
            api.signed_main("/fixture.app")


@pytest.mark.parametrize(
    "drift", ["none", "multiple", "appeared", "snapshot", "pid-start", "refused"]
)
def test_main_identity_rechecks_exact_peer_before_termination(drift: str) -> None:
    probe = _probe_module()
    peer = probe.Peer(123, "/fixture.app", "/fixture.app/Contents/MacOS/Main", 123.0)

    class API:
        calls = 0
        terminated = False

        def snapshot(self, _destination):
            self.calls += 1
            if drift == "multiple":
                return [peer, peer]
            if drift == "appeared" and self.calls == 1:
                return []
            if drift == "snapshot" and self.calls > 1:
                return []
            return [peer]

        def application(self, pid):
            assert pid == peer.pid
            return peer

        def peer(self, app, _destination):
            return app

        def terminate(self, app):
            self.terminated = True
            return drift != "refused"

    api = API()
    starts = iter(["same", "different" if drift == "pid-start" else "same"])
    if drift == "none":
        assert (
            probe.inspect_main(
                api, "/fixture.app", quit_main=True, start=lambda _: next(starts)
            )
            == "123\nsame"
        )
        assert api.terminated
    else:
        with pytest.raises(ValueError):
            probe.inspect_main(
                api, "/fixture.app", quit_main=True, start=lambda _: next(starts)
            )
        assert api.terminated == (drift == "refused")


@pytest.mark.skipif(sys.platform != "darwin", reason="native AppKit/Security probe")
def test_native_main_probe_can_observe_absent_destination(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-S", str(PROBE), str(tmp_path / "absent.app")],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_intel_host_refuses_arm_only_channel_before_dmg_or_carrier_python(
    tmp_path: Path,
) -> None:
    install, _, env = _cold_fixture(tmp_path)
    tools = Path(env["PATH"].split(":")[0])
    _write_executable(
        tools / "uname",
        '#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo x86_64; fi\n',
    )
    result = _run_cold(install, env)
    assert result.returncode != 0
    assert "darwin-x64" in result.stderr
    assert "app.dmg" not in Path(env["CURL_CAPTURE"]).read_text()
    assert (
        not Path(env["EVENTS"]).exists()
        or "carrier-python" not in Path(env["EVENTS"]).read_text()
    )
