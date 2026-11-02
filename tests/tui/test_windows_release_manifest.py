"""Release receipt boundaries; fabricated carriers never install native software."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts import windows_release_manifest as release

SOURCE = "1" * 40
TERMINAL = "2" * 40
FRAME = "3" * 40
CONTROLLER = "4" * 40
GATES = {"cold_install": "success", "msi_install": "success", "exe_install": "success"}


@pytest.fixture(scope="module")
def signing(tmp_path_factory):
    directory = tmp_path_factory.mktemp("windows-receipt-test-key")
    key, public = directory / "fixture.key", directory / "fixture.pub"
    subprocess.run(
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
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    return key, public


def checksum(path):
    Path(f"{path}.sha256").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


@pytest.fixture
def carriers(tmp_path, signing):
    pack = (
        tmp_path
        / f"Vibecrafted_RuntimePack_4.5.1-20261029-{SOURCE[:8]}-win32-x64.tar.gz"
    )
    msi = tmp_path / f"Vibecrafted_4.5.1-20261029-{SOURCE[:8]}-windows-x64.msi"
    exe = tmp_path / f"Vibecrafted_4.5.1-20261029-{SOURCE[:8]}-windows-x64.exe"
    msi.write_bytes(release._MSI_MAGIC + bytes(504))
    pe = bytearray(134)
    pe[:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 128)
    pe[128:132] = b"PE\0\0"
    struct.pack_into("<H", pe, 132, 0x014C)
    exe.write_bytes(pe)
    provenance = {
        "schema": "io.vetcoders.vibecrafted.runtime-pack-provenance.v1",
        "version": "4.5.1",
        "carrier_basename": pack.name,
        "platform": "win32-x64",
        "architecture": "x64",
        "source_revisions": {
            "vibecrafted": SOURCE,
            "vc-terminal": TERMINAL,
            "vc-frame": FRAME,
        },
    }
    body = json.dumps(provenance).encode()
    with tarfile.open(pack, "w:gz") as archive:
        entry = tarfile.TarInfo("VibecraftedRuntime/runtime-pack-provenance.json")
        entry.size = len(body)
        archive.addfile(entry, io.BytesIO(body))
    for path in (pack, msi, exe):
        checksum(path)
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(signing[0]),
            "-out",
            str(pack) + ".sig",
            str(pack),
        ],
        check=True,
        capture_output=True,
    )
    return tmp_path, signing[1], pack, msi, exe


def receipt(carriers, **overrides):
    values = {
        "source_sha": SOURCE,
        "terminal_sha": TERMINAL,
        "frame_sha": FRAME,
        "controller_sha": CONTROLLER,
        "repository": "vetcoders/vibecrafted",
        "run_id": 1234,
        "run_attempt": 1,
        "request_id": "a" * 24,
        "verification": GATES,
    }
    values.update(overrides)
    return release.build_manifest(carriers[0], carriers[1], **values)


def test_receipt_binds_actual_carriers_and_distinguishes_controller(carriers):
    result = receipt(carriers)
    assert result["source_revisions"]["vibecrafted"] == SOURCE
    assert result["workflow"]["controller_sha"] == CONTROLLER
    assert result["workflow"]["source_sha"] == SOURCE
    assert result["verification"] == GATES
    for role, path in zip(("runtime_pack", "msi", "exe"), carriers[2:], strict=True):
        assert result[role] == {
            "path": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }


@pytest.mark.parametrize("gate", list(GATES))
def test_no_receipt_for_a_failed_or_skipped_native_install(carriers, gate):
    with pytest.raises(ValueError, match="three native"):
        receipt(carriers, verification={**GATES, gate: "skipped"})


def test_receipt_requires_full_provenance_not_just_same_filename_prefix(carriers):
    with pytest.raises(ValueError, match="provenance"):
        receipt(carriers, source_sha=SOURCE[:8] + "5" * 32)


def test_receipt_rejects_wrong_donor_pin(carriers):
    with pytest.raises(ValueError, match="provenance"):
        receipt(carriers, frame_sha="6" * 40)


def test_receipt_rejects_controller_or_request_injection(carriers):
    with pytest.raises(ValueError, match="Git SHA"):
        receipt(carriers, controller_sha="main; run something")
    with pytest.raises(ValueError, match="request_id"):
        receipt(carriers, request_id="../unowned")


def test_receipt_rejects_changed_carrier_bytes(carriers):
    carriers[3].write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        receipt(carriers)


@pytest.mark.parametrize("role", ("msi", "exe"))
def test_checksum_does_not_turn_a_source_archive_into_a_native_installer(
    carriers, role
):
    path = carriers[3 if role == "msi" else 4]
    path.write_bytes(b"not a native installer" * 8)
    checksum(path)
    with pytest.raises(ValueError, match="native"):
        receipt(carriers)


def test_receipt_rejects_rehearsal_material_even_with_a_valid_signature(carriers):
    Path(str(carriers[2]) + ".rehearsal.pub").write_text("test-only key")
    with pytest.raises(ValueError, match="rehearsal"):
        receipt(carriers)


def test_receipt_rejects_invalid_product_signature(carriers):
    Path(str(carriers[2]) + ".sig").write_bytes(b"not a product signature")
    with pytest.raises(ValueError, match="product key"):
        receipt(carriers)


def test_receipt_rejects_a_symlink_carrier(carriers):
    path = carriers[4]
    target = path.with_name("preserved.exe")
    path.rename(target)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="non-regular"):
        receipt(carriers)


def test_workflow_receipt_waits_for_all_native_install_jobs():
    workflow = (
        Path(__file__).resolve().parents[2] / ".github/workflows/install-windows.yml"
    )
    data = yaml.safe_load(workflow.read_text())
    dispatch = data[True]["workflow_dispatch"]["inputs"]
    assert set(dispatch) == {
        "source_ref",
        "terminal_revision",
        "frame_revision",
        "request_id",
        "release_signing",
    }
    jobs = data["jobs"]
    assert set(jobs["windows-release"]["needs"]) == {
        "windows-runtime-pack",
        "windows-cold-install",
        "windows-installers",
        "windows-msi-install",
        "windows-exe-install",
    }
    assert "workflow_dispatch" in jobs["windows-release"]["if"]
    assert (
        jobs["windows-runtime-pack"]["steps"][1]["with"]["ref"]
        == "${{ steps.request.outputs.source_sha }}"
    )
    for job in ("windows-cold-install", "windows-installers"):
        assert (
            jobs[job]["steps"][0]["with"]["ref"]
            == "${{ needs.windows-runtime-pack.outputs.source_sha }}"
        )
    for job in ("windows-msi-install", "windows-exe-install"):
        assert (
            jobs[job]["steps"][0]["with"]["ref"]
            == "${{ needs.windows-installers.outputs.source_sha }}"
        )
