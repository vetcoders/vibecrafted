"""Final-byte signing probes on disposable ad-hoc native fixtures, never releases."""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core import product_contract as contract
from vibecrafted_core import runtime_pack_contract

from scripts import vetcoders_install as installer
from tests._runtime_pack_fixture import private_native_copy, seed_runtime_pack

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="real macOS codesign")


def _run(argv, *, env=None):
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _identity(path):
    _run(["/usr/bin/codesign", "--verify", "--strict", str(path)])
    result = _run(["/usr/bin/codesign", "-d", "--verbose=4", str(path)])
    fields = dict(
        line.split("=", 1) for line in result.stderr.splitlines() if "=" in line
    )
    return fields["CDHash"], fields["Identifier"]


def _permissions(path):
    result = subprocess.run(
        ["/usr/bin/codesign", "-d", "--entitlements", ":-", str(path)],
        capture_output=True,
        check=True,
    )
    return plistlib.loads(result.stdout) if result.stdout else {}


@pytest.fixture
def signed_pair(tmp_path):
    try:
        yield _signed_pair(tmp_path)
    finally:
        shutil.rmtree(tmp_path)


def _signed_pair(tmp_path):
    native = private_native_copy(
        Path("/bin/echo"), tmp_path / "native", ("native-smoke",)
    )
    # All actual signatures are ad-hoc. Only timestamps are disabled: tests
    # neither contact Apple's timestamp service nor touch signing credentials.
    tools = tmp_path / "tools"
    tools.mkdir()
    signer = tools / "codesign"
    signer.write_text("""#!/bin/bash
args=()
for arg in "$@"; do
  if [[ "$arg" == --timestamp ]]; then arg=--timestamp=none; fi
  args+=("$arg")
done
exec /usr/bin/codesign "${args[@]}"
""")
    signer.chmod(0o755)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{tools}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(ROOT / "vibecrafted-core"),
        "PYTHON": sys.executable,
    }
    payload = seed_runtime_pack(tmp_path / "payload")
    mcp = payload / "vibecrafted-mcp/vibecrafted_mcp"
    mcp.mkdir(parents=True)
    (mcp / "__init__.py").write_text("")
    shutil.copy2(
        ROOT / "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py",
        payload / "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py",
    )
    terminal = payload / "libexec/vc-terminal"
    shutil.copy2(native, terminal)
    for name in runtime_pack_contract.REQUIRED_FOUNDATION_EXECUTABLES | {
        "vc-frame",
        "vc-terminal",
        "voc",
        "vc-o",
        "vc-admin",
        "vc-procs",
        "scaffold-doctor",
        "control-observe",
    }:
        (payload / "bin" / name).write_text(
            (ROOT / "scripts/vc-terminal-product-entry.sh").read_text()
            if name == "vc-terminal"
            else "#!/bin/sh\nexit 0\n"
        )
        (payload / "bin" / name).chmod(0o755)
    # A real unrelated nested bundle proves sealing cannot spread permission.
    nested = payload / "libexec/Helper.app"
    exe = nested / "Contents/MacOS/helper"
    exe.parent.mkdir(parents=True)
    shutil.copy2(native, exe)
    (nested / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.vetcoders.fixture-helper",
                "CFBundleExecutable": "helper",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    runtime_pack_contract.write_runtime_foundations(payload)
    archive = tmp_path / "pack.tar.gz"
    _run(
        [
            "/bin/bash",
            str(ROOT / "scripts/package-runtime-pack.sh"),
            "--payload-root",
            str(payload),
            "--output",
            str(archive),
            "--source-revision",
            json.loads((payload / "source-provenance.json").read_text())[
                "source_revision"
            ],
            "--terminal-revision",
            "b" * 40,
            "--frame-revision",
            "c" * 40,
            "--version",
            "9.9.9+g12345678",
            "--platform",
            "darwin-arm64",
            "--architecture",
            "arm64",
            "--codesign-identity",
            "-",
        ],
        env=env,
    )
    extracted = tmp_path / "extracted"
    _run(
        [
            "/bin/bash",
            "-c",
            '. "$1/scripts/lib/macho-signing.sh"; extract_runtime_pack_for_signature_verification "$2" "$3"',
            "fixture",
            str(ROOT),
            str(archive),
            str(extracted),
        ],
        env=env,
    )
    runtime = extracted / "VibecraftedRuntime"
    app = tmp_path / "Vibecrafted.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    for name in ("Vibecrafted", "vc-terminal", "unrelated"):
        shutil.copy2(native, macos / name)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": contract.PRODUCT_BUNDLE_ID,
                "CFBundleExecutable": "Vibecrafted",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    embedded = app / "Contents/Resources/runtime-pack/pack.tar.gz"
    embedded.parent.mkdir(parents=True)
    shutil.copy2(archive, embedded)
    app_runtime = app / "Contents/Resources/runtime"
    shutil.copytree(payload, app_runtime)
    entitlements = tmp_path / "host-entitlements.plist"
    # Run the actual release signing block (before script-signing/carrier
    # embedding) against the disposable assembled App, then its final seal.
    builder = (ROOT / "scripts/build-vibecrafted-release.sh").read_text()
    block = builder.split(
        '  local host_entitlements="$BUILD_DIR/host-entitlements.plist"', 1
    )[1]
    block = block.split("  sign_helper_scripts", 1)[0]
    bundle_signer = (
        "sign_nested_app_bundles() {"
        + builder.split("sign_nested_app_bundles() {", 1)[1].split("\n# Helpers/", 1)[0]
    )
    _run(
        [
            "/bin/bash",
            "-c",
            """set -euo pipefail
. "$1/scripts/lib/macho-signing.sh"
SOURCE_ROOT="$1"; APP="$2"; BUILD_DIR="$3"; runtime="$APP/Contents/Resources/runtime"
SIGNING_IDENTITY=-; CODESIGN_KEYCHAIN_ARGS=()
log() { :; }; die() { echo "$*" >&2; exit 1; }
"""
            + bundle_signer
            + """
sign_fixture() {
local host_entitlements="$BUILD_DIR/host-entitlements.plist"
"""
            + block
            + """
}
sign_fixture
codesign --force --options runtime --timestamp --sign - --entitlements "$BUILD_DIR/host-entitlements.plist" "$APP"
verify_macho_tree "$APP/Contents" 1
""",
            "fixture",
            str(ROOT),
            str(app),
            str(tmp_path),
        ],
        env=env,
    )
    return app, runtime, native, entitlements, env, archive


def test_final_app_and_pack_share_native_code_identity_and_exact_permissions(
    signed_pair,
):
    app, runtime, _, _, env, archive = signed_pair
    host = app / "Contents/MacOS/vc-terminal"
    candidate = runtime / "libexec/vc-terminal"
    embedded = app / "Contents/Resources/runtime-pack/pack.tar.gz"
    assert embedded.read_bytes() == archive.read_bytes()
    embedded_root = runtime.parent / "embedded"
    _run(
        [
            "/bin/bash",
            "-c",
            '. "$1/scripts/lib/macho-signing.sh"; extract_runtime_pack_for_signature_verification "$2" "$3"',
            "fixture",
            str(ROOT),
            str(embedded),
            str(embedded_root),
        ],
        env=env,
    )
    embedded_terminal = embedded_root / "VibecraftedRuntime/libexec/vc-terminal"
    assert _identity(embedded_terminal) == _identity(host)
    assert _permissions(embedded_terminal) == contract.HOST_ENTITLEMENTS
    assert _identity(host) == _identity(candidate)
    assert _identity(host) == _identity(
        app / "Contents/Resources/runtime/libexec/vc-terminal"
    )
    for path in (host, candidate):
        assert _permissions(path) == contract.HOST_ENTITLEMENTS
        assert _run([str(path), "native-smoke"]).stdout.strip() == "native-smoke"
    contract._verify_host_permission_signatures(app)
    _run(
        [
            sys.executable,
            str(ROOT / "scripts/unified_product_manifest.py"),
            "host-permissions",
            "--runtime-root",
            str(runtime),
            "--verify-only",
            "--verify-signatures",
        ],
        env=env,
    )
    for root in (runtime, app / "Contents/Resources/runtime"):
        assert _permissions(root / "libexec/vc-frame") == {}
        assert _permissions(root / "libexec/Helper.app") == {}
    assert _permissions(app / "Contents/MacOS/unrelated") == {}
    print(
        json.dumps(
            {
                "host": _identity(host),
                "pack": _identity(candidate),
                "entitlements": _permissions(candidate),
                "native_exec": "native-smoke",
            }
        )
    )


@pytest.mark.parametrize("drift", ["missing", "extra", "code"])
def test_final_pair_refuses_permission_or_code_drift(signed_pair, drift):
    app, runtime, _, entitlement_file, _env, _archive = signed_pair
    candidate = runtime / "libexec/vc-terminal"
    if drift == "code":
        data = bytearray(candidate.read_bytes())
        data[4096] ^= 1
        candidate.write_bytes(data)
        verification = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(candidate)],
            capture_output=True,
            check=False,
        )
        assert verification.returncode != 0
        with pytest.raises(installer.RuntimeInstallRefusal, match="unproven"):
            installer._signed_terminal_identity(candidate)
        return
    args = []
    if drift == "extra":
        entitlement_file.write_bytes(
            plistlib.dumps(
                {
                    **contract.HOST_ENTITLEMENTS,
                    "com.apple.security.cs.disable-library-validation": True,
                }
            )
        )
        args = ["--entitlements", str(entitlement_file)]
    _run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--timestamp=none",
            "--options",
            "runtime",
            "--identifier",
            _identity(candidate)[1],
            *args,
            str(candidate),
        ]
    )
    assert _identity(app / "Contents/MacOS/vc-terminal") != _identity(candidate)
    with pytest.raises(contract.ProductContractError):
        contract._verify_host_permission_signatures(runtime, targets=(candidate,))
    # Ad-hoc fixtures can never pass production owner admission.
    with pytest.raises(installer.RuntimeInstallRefusal, match="unproven"):
        installer._signed_terminal_identity(candidate)


def test_bundle_seal_preserves_explicit_executable_permission_policy(signed_pair):
    _, runtime, _, entitlements, env, _archive = signed_pair
    bundle = runtime / "libexec/Helper.app"
    executable = bundle / "Contents/MacOS/helper"
    _run(
        [
            "/bin/bash",
            "-c",
            """set -euo pipefail
. "$1/scripts/lib/macho-signing.sh"
SIGNING_IDENTITY=-; CODESIGN_KEYCHAIN_ARGS=()
sign_macho_tree "$2/Contents" "" "$2/Contents/MacOS/helper" "$3"
sign_macho_app_bundles "$2"
verify_macho_tree "$2" 1
""",
            "fixture",
            str(ROOT),
            str(bundle),
            str(entitlements),
        ],
        env=env,
    )
    assert _permissions(executable) == contract.HOST_ENTITLEMENTS
    assert _permissions(bundle) == contract.HOST_ENTITLEMENTS
    assert _identity(executable)[1] == "io.vetcoders.fixture-helper"
