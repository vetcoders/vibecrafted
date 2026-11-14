"""Host assembly contracts: no signing, device access, app launch, or TCC mutation."""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import struct
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from vibecrafted_core import product_contract as contract

from scripts import unified_product_manifest

ROOT = Path(__file__).resolve().parents[2]
HOST_SOURCE = ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted"
GENERATOR = ROOT / "scripts/unified_product_manifest.py"


def _bundle(tmp_path: Path) -> Path:
    app = tmp_path / "Vibecrafted.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": contract.PRODUCT_BUNDLE_ID,
                "CFBundleExecutable": "Vibecrafted",
                "CFBundleVersion": "42",
                "VCUpdateFeedURL": "https://example.com/release-output.json",
                "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
            }
        )
    )
    return app


def _assemble(app: Path, output: Path, *extra: str, generator: Path = GENERATOR):
    return subprocess.run(
        [
            sys.executable,
            str(generator),
            "host-permissions",
            "--app",
            str(app),
            "--entitlements-output",
            str(output),
            *extra,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "vibecrafted-core")},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def test_actual_assembly_propagates_privacy_and_exact_signing_plan(
    tmp_path: Path,
) -> None:
    app = _bundle(tmp_path)
    original = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    entitlements = tmp_path / "host-entitlements.plist"
    result = _assemble(app, entitlements)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    built = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    canonical = plistlib.loads((HOST_SOURCE / "Info.plist").read_bytes())
    assert {key: built[key] for key in original} == original
    for key in contract.HOST_USAGE_DESCRIPTION_KEYS:
        assert built[key] == canonical[key] and built[key].strip()
    assert plistlib.loads(entitlements.read_bytes()) == contract.HOST_ENTITLEMENTS
    assert plan["signing_targets"] == ["Vibecrafted.app", "Contents/MacOS/vc-terminal"]
    assert plan["entitlements"] == {"com.apple.security.device.audio-input": True}
    assert plan["hardened_runtime"] is True
    assert plan["signatures_verified"] is plan["live_consent_verified"] is False
    before = (app / "Contents/Info.plist").read_bytes()
    verified = _assemble(app, entitlements, "--verify-only")
    assert verified.returncode == 0, verified.stderr
    assert (app / "Contents/Info.plist").read_bytes() == before


@pytest.mark.parametrize("key", contract.HOST_USAGE_DESCRIPTION_KEYS)
@pytest.mark.parametrize("value", [None, "", "   ", "$(UNRESOLVED)", False])
def test_generated_bundle_refuses_missing_or_invalid_usage(
    tmp_path: Path,
    key: str,
    value: object,
) -> None:
    app = _bundle(tmp_path)
    output = tmp_path / "entitlements.plist"
    assert _assemble(app, output).returncode == 0
    path = app / "Contents/Info.plist"
    info = plistlib.loads(path.read_bytes())
    if value is None:
        info.pop(key)
    else:
        info[key] = value
    path.write_bytes(plistlib.dumps(info))
    before = path.read_bytes()
    result = _assemble(app, output, "--verify-only")
    assert result.returncode != 0 and key in result.stderr
    assert path.read_bytes() == before


def test_assembly_refuses_signed_bundle_and_foreign_identity(tmp_path: Path) -> None:
    app = _bundle(tmp_path)
    signature = app / "Contents/_CodeSignature"
    signature.mkdir()
    result = _assemble(app, tmp_path / "entitlements.plist")
    assert result.returncode != 0 and "signed App" in result.stderr
    signature.rmdir()
    path = app / "Contents/Info.plist"
    path.write_bytes(plistlib.dumps({"CFBundleIdentifier": "foreign.app"}))
    result = _assemble(app, tmp_path / "entitlements.plist")
    assert result.returncode != 0 and "canonical app identity" in result.stderr


def test_assembly_refuses_entitlement_drift_at_actual_source_input(
    tmp_path: Path,
) -> None:
    generator = tmp_path / "source/scripts/unified_product_manifest.py"
    generator.parent.mkdir(parents=True)
    shutil.copy2(GENERATOR, generator)
    source = generator.parent.parent / HOST_SOURCE.relative_to(ROOT)
    source.mkdir(parents=True)
    shutil.copy2(HOST_SOURCE / "Info.plist", source / "Info.plist")
    (source / "Vibecrafted.entitlements").write_bytes(
        plistlib.dumps(
            {
                **contract.HOST_ENTITLEMENTS,
                "com.apple.security.app-sandbox": True,
            }
        )
    )
    app = _bundle(tmp_path)
    result = _assemble(app, tmp_path / "entitlements.plist", generator=generator)
    assert result.returncode != 0 and "entitlement policy" in result.stderr


def test_signing_selector_grants_audio_only_to_embedded_terminal(
    tmp_path: Path,
) -> None:
    app = _bundle(tmp_path)
    output = tmp_path / "entitlements.plist"
    assert _assemble(app, output).returncode == 0
    # The selector must inspect Mach-O bytes even on a Linux test host.
    # A header-only executable is an OS file-type boundary double, never run.
    native_bytes = (
        Path("/usr/bin/true").read_bytes()
        if sys.platform == "darwin"
        else struct.pack("<8I", 0xFEEDFACF, 0x01000007, 3, 2, 0, 0, 0, 0)
    )
    for name in ("Vibecrafted", "vc-terminal", "unrelated-tool"):
        (app / "Contents/MacOS" / name).write_bytes(native_bytes)
    (app / "Contents/MacOS/not-code.txt").write_text("Must not be signed")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    signer = fake_bin / "codesign"
    signer.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SIGNING_LOG"\n',
        encoding="utf-8",
    )
    signer.chmod(0o755)
    log = tmp_path / "signing.log"
    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            """set -euo pipefail
. "$1/scripts/lib/macho-signing.sh"
SIGNING_IDENTITY=FAKE-NOT-A-SIGNER
CODESIGN_KEYCHAIN_ARGS=()
sign_macho_tree "$2/Contents" "$2/Contents/MacOS/Vibecrafted" "$2/Contents/MacOS/vc-terminal" "$3"
""",
            "test",
            str(ROOT),
            str(app),
            str(output),
        ],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "SIGNING_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    terminal = next(line for line in calls if line.endswith("/vc-terminal"))
    unrelated = next(line for line in calls if line.endswith("/unrelated-tool"))
    assert f"--entitlements {output}" in terminal
    assert "--entitlements" not in unrelated
    assert all("--options runtime" in line for line in calls)


@pytest.mark.parametrize(
    "drift", ["missing", "extra", "false", "integer", "unreadable", "unhardened"]
)
@pytest.mark.parametrize("target", ["Vibecrafted.app", "vc-terminal"])
def test_final_signature_verifier_refuses_each_host_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    target: str,
) -> None:
    app = _bundle(tmp_path)

    def codesign(argv, **kwargs):
        assert argv[0] == "/usr/bin/codesign" and "--sign" not in argv
        selected = Path(argv[-1]).name == target
        if "--entitlements" in argv:
            payload = dict(contract.HOST_ENTITLEMENTS)
            if selected:
                if drift == "missing":
                    return subprocess.CompletedProcess(argv, 0, b"", b"")
                if drift == "unreadable":
                    return subprocess.CompletedProcess(argv, 1, b"", b"error")
                if drift == "extra":
                    payload["com.apple.security.cs.disable-library-validation"] = True
                if drift == "false":
                    payload["com.apple.security.device.audio-input"] = False
                if drift == "integer":
                    payload["com.apple.security.device.audio-input"] = 1
            return subprocess.CompletedProcess(argv, 0, plistlib.dumps(payload), b"")
        text = "CodeDirectory v=20500 flags=0x10000(runtime)\n"
        if selected and drift == "unhardened":
            text = "CodeDirectory v=20500 flags=0x0(none)\n"
        return subprocess.CompletedProcess(argv, 0, "", text)

    monkeypatch.setattr(contract.subprocess, "run", codesign)
    with pytest.raises(contract.ProductContractError):
        contract._verify_host_permission_signatures(app)


def test_final_product_manifest_generation_preserves_host_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _bundle(tmp_path)
    assert _assemble(app, tmp_path / "entitlements.plist").returncode == 0
    (app / "Contents/MacOS/Vibecrafted").write_bytes(b"native-inspection-double")
    monkeypatch.setattr(unified_product_manifest, "_module", lambda *a, **kw: {})
    monkeypatch.setattr(unified_product_manifest, "_entry", lambda *a, **kw: {})
    monkeypatch.setattr(
        contract, "_observed_macho", lambda *a, **kw: SimpleNamespace(dependencies=[])
    )
    monkeypatch.setattr(contract, "_macho_code_sha256", lambda *a: "a" * 64)
    unified_product_manifest.produce_app(
        Namespace(
            app=app,
            version="4.4.0",
            build="42",
            terminal_source=tmp_path / "terminal",
            frame_source=tmp_path / "frame",
            terminal_sha="a" * 40,
            frame_sha="b" * 40,
            vibecrafted_sha="c" * 40,
        )
    )
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    contract._validate_host_usage_descriptions(info)
    assert info["CFBundleIdentifier"] == contract.PRODUCT_BUNDLE_ID
    assert info["CFBundleVersion"] == "42"


@pytest.mark.parametrize("flags", ["runtime", "adhoc,runtime"])
def test_final_signature_verifier_accepts_exact_audio_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: str,
) -> None:
    seen = []

    def codesign(argv, **kwargs):
        assert "--sign" not in argv
        seen.append(argv)
        if "--entitlements" in argv:
            return subprocess.CompletedProcess(
                argv, 0, plistlib.dumps(contract.HOST_ENTITLEMENTS), b""
            )
        return subprocess.CompletedProcess(
            argv, 0, "", f"CodeDirectory v=20500 flags=0x10000({flags})\n"
        )

    monkeypatch.setattr(contract.subprocess, "run", codesign)
    contract._verify_host_permission_signatures(_bundle(tmp_path))
    assert len(seen) == 4
    assert {Path(argv[-1]).name for argv in seen} == {"Vibecrafted.app", "vc-terminal"}
