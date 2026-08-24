"""Unit tests for Delivery/Runtime Receipt (no live PATH dependency)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from vibecrafted_core import product_contract as pc
from vibecrafted_core import runtime_receipt as rr

_RUNTIME_VERSION = "3.7.0"
_RUNTIME_REVISION = "0123456789abcdef0123456789abcdef01234567"
_SOURCE_PAYLOAD = {
    "schema": pc.SOURCE_PAYLOAD_SCHEMA,
    "algorithm": "sha256",
    "tree_sha256": "b" * 64,
    "entry_count": 42,
}
_RUNTIME_FILE_BYTES = {
    "VERSION": f"{_RUNTIME_VERSION}\n".encode(),
    "scripts/distribution_manifest.py": b"MANIFEST = True\n",
    "scripts/installer_brand.py": b"BRAND = True\n",
    "scripts/vibecrafted": b"#!/usr/bin/env bash\n",
    "scripts/vetcoders_install.py": b"#!/usr/bin/env python3\n",
    pc.RUNTIME_GENERATION_CANONICAL_CONFIG: b"layout {}\n",
    pc.RUNTIME_GENERATION_ENTRYPOINT: b"#!/usr/bin/env bash\n",
    "vibecrafted-core/vibecrafted_core/product_contract.py": b"contract = True\n",
    "vibecrafted-core/vibecrafted_core/walkaround_runner.py": b"runner = True\n",
    "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json": (
        b"{}\n"
    ),
    "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json": b"{}\n",
    "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub": (
        b"fixture public key\n"
    ),
}


def _runtime_generation_fixture(
    tmp_path: Path, *, runtime_projection: str | None = None
) -> tuple[Path, Path, dict[str, object]]:
    assert set(_RUNTIME_FILE_BYTES) == pc.RUNTIME_GENERATION_REQUIRED_HASHES
    assert runtime_projection in {None, "internal", "external"}
    generation = tmp_path / "vibecrafted-generation-3.7.0+g01234567"
    for relative, raw in _RUNTIME_FILE_BYTES.items():
        if relative == "runtime/generated/vc-frame/config.kdl" and runtime_projection:
            runtime_root = (
                generation / "vibecrafted-core/vibecrafted_core/runtime"
                if runtime_projection == "internal"
                else tmp_path / "escaping-runtime"
            )
            target = runtime_root / "generated/vc-frame/config.kdl"
        else:
            target = generation / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    if runtime_projection:
        runtime_link = generation / "runtime"
        runtime_link.symlink_to(
            "vibecrafted-core/vibecrafted_core/runtime"
            if runtime_projection == "internal"
            else tmp_path / "escaping-runtime",
            target_is_directory=True,
        )
    deck = generation / pc.RUNTIME_GENERATION_ENTRYPOINT
    deck.chmod(0o755)
    manifest: dict[str, object] = {
        "schema": pc.RUNTIME_GENERATION_SCHEMA,
        "version": _RUNTIME_VERSION,
        "source_fingerprint": "a" * 64,
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": _RUNTIME_REVISION,
        "source_payload": dict(_SOURCE_PAYLOAD),
        "entrypoint": pc.RUNTIME_GENERATION_ENTRYPOINT,
        "hashes": {
            relative: hashlib.sha256(raw).hexdigest()
            for relative, raw in _RUNTIME_FILE_BYTES.items()
        },
    }
    _write_runtime_manifest(generation, manifest)
    _write_source_provenance(generation)
    return generation, deck, manifest


def _write_runtime_manifest(generation: Path, manifest: dict[str, object]) -> None:
    (generation / pc.RUNTIME_GENERATION_MANIFEST_NAME).write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_source_provenance(
    generation: Path, *, mutation: dict[str, object] | None = None
) -> None:
    provenance: dict[str, object] = {
        "schema": pc.SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": "vetcoders/vibecrafted",
        "source_revision": _RUNTIME_REVISION,
        "payload": dict(_SOURCE_PAYLOAD),
    }
    if mutation:
        provenance.update(mutation)
    (generation / pc.SOURCE_PROVENANCE_NAME).write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_schema_version_stable() -> None:
    assert rr.SCHEMA_VERSION == "vibecrafted.delivery_receipt.v1"


def test_parse_installed_provenance_cargo_style() -> None:
    p = rr.parse_installed_provenance("vc-frame 0.46.0+g5c99f72d.dirty")
    assert p["installed_sha"] == "5c99f72d"
    assert p["installed_dirty"] is True


def test_parse_installed_provenance_clean() -> None:
    p = rr.parse_installed_provenance("aicx 0.12.0+g770149ec")
    assert p["installed_sha"] == "770149ec"
    assert p["installed_dirty"] is False


def test_parse_installed_provenance_refuses_without_sha() -> None:
    p = rr.parse_installed_provenance("vibecrafted 3.6.0")
    assert isinstance(p["installed_sha"], dict)
    assert p["installed_sha"]["value"] == "unknown"


def test_parse_dirty_false_is_not_dirty_build() -> None:
    """loct banner includes ``dirty=false`` — must not trip DIRTY_BUILD."""
    line = (
        "loct 0.14.1+g8188cf0d schema=loctree.bundle.v1 "
        "bundle_id=0.14.1+g8188cf0d commit=8188cf0d dirty=false"
    )
    p = rr.parse_installed_provenance(line)
    assert p["installed_sha"] == "8188cf0d"
    assert p["installed_dirty"] is False


def test_parse_path_hint_for_bare_semver() -> None:
    p = rr.parse_installed_provenance(
        "vibecrafted 3.6.0",
        path_hint="/home/x/.local/share/vibecrafted/tools/"
        "vibecrafted-3.6.0+g560310a9/bin/vibecrafted",
    )
    assert p["installed_sha"] == "560310a9"


def test_checkout_head_sha_loose_ref(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    sha = "7fa51c66aabbccddeeff00112233445566778899"
    (git / "HEAD").write_text("ref: refs/heads/develop\n", encoding="utf-8")
    (git / "refs" / "heads" / "develop").write_text(sha + "\n", encoding="utf-8")
    assert rr.checkout_head_sha(tmp_path) == sha
    assert rr.checkout_branch(tmp_path) == "develop"


def test_checkout_head_sha_packed_refs(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    sha = "8188cf0d00112233445566778899aabbccddeeff"
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{sha} refs/heads/main\n",
        encoding="utf-8",
    )
    assert rr.checkout_head_sha(tmp_path) == sha


def test_checkout_head_sha_missing_is_none(tmp_path: Path) -> None:
    assert rr.checkout_head_sha(tmp_path) is None


def test_classify_source_ahead() -> None:
    classes = rr.classify_drift(
        on_path=True,
        checkout_sha="7fa51c66deadbeef",
        installed_sha="5c99f72dcafe",
        installed_dirty=False,
        ahead=0,
        index_stale=False,
        source_known=True,
    )
    assert rr.DRIFT_SOURCE_AHEAD in classes
    assert rr.primary_drift(classes) == rr.DRIFT_SOURCE_AHEAD


def test_classify_not_on_path_primary() -> None:
    classes = rr.classify_drift(
        on_path=False,
        checkout_sha="abc",
        installed_sha=None,
        installed_dirty=None,
        ahead=3,
        index_stale=False,
        source_known=True,
    )
    assert rr.primary_drift(classes) == rr.DRIFT_NOT_ON_PATH
    assert rr.DRIFT_UNPUSHED in classes


def test_classify_dirty_build() -> None:
    classes = rr.classify_drift(
        on_path=True,
        checkout_sha="5c99f72d",
        installed_sha="5c99f72d",
        installed_dirty=True,
        ahead=0,
        index_stale=False,
        source_known=True,
    )
    assert rr.primary_drift(classes) == rr.DRIFT_DIRTY_BUILD


def test_classify_clean() -> None:
    classes = rr.classify_drift(
        on_path=True,
        checkout_sha="abc12345",
        installed_sha="abc12345",
        installed_dirty=False,
        ahead=0,
        index_stale=False,
        source_known=True,
    )
    assert classes == [rr.DRIFT_CLEAN]


def test_never_uses_cwd_for_source(tmp_path: Path, monkeypatch) -> None:
    """Cwd is a decoy repo — receipt must not adopt it as a tool source."""
    decoy = tmp_path / "decoy"
    (decoy / ".git" / "refs" / "heads").mkdir(parents=True)
    (decoy / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (decoy / ".git" / "refs" / "heads" / "main").write_text(
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n", encoding="utf-8"
    )
    (decoy / "Cargo.toml").write_text(
        '[package]\nname = "something-else"\n', encoding="utf-8"
    )
    monkeypatch.chdir(decoy)
    # Clear env that could legitimately point at sources
    for key in (
        "VC_FRAME_SOURCE",
        "VC_FRAME_ROOT",
        "VIBECRAFTED_SOURCE",
        "VIBECRAFTED_ROOT",
        "LOCTREE_SOURCE",
        "AICX_SOURCE",
        "VIBECRAFTED_FLEET_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)

    # Force no binary and only impossible candidates
    monkeypatch.setattr(rr, "which_binary", lambda _name: None)
    monkeypatch.setattr(rr, "_default_candidate_roots", lambda _name: [])
    monkeypatch.setattr(rr, "_vibecrafted_package_repo", lambda: None)

    specs = [
        rr.ToolSpec(
            name="vc-frame",
            binaries=("vc-frame",),
            env_roots=("VC_FRAME_SOURCE",),
            markers=(("Cargo.toml", r'name\s*=\s*"vc-frame"'),),
            candidate_roots=(),
        )
    ]
    receipt = rr.build_receipt(specs)
    tool = receipt["tools"][0]
    path = tool["source"]["path"]
    assert isinstance(path, dict)
    assert path["value"] == "unknown"
    assert (
        "cwd" not in json.dumps(tool).lower() or "never uses" in receipt["cwd_policy"]
    )


def test_vibecrafted_receipt_uses_checkout_free_runtime_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    generation, deck, manifest = _runtime_generation_fixture(tmp_path)
    assert len(manifest["hashes"]) == 12
    assert (
        pc.verify_installed_runtime_generation(generation, expected_entrypoint=deck)
        == manifest
    )
    monkeypatch.setattr(
        rr, "which_binary", lambda name: str(deck) if name == "vibecrafted" else None
    )
    monkeypatch.setattr(rr, "run_version", lambda _path: "vibecrafted 3.7.0+g01234567")
    monkeypatch.setattr(rr, "_vibecrafted_tools_path_hints", list)

    [tool] = rr.build_receipt(
        [
            rr.ToolSpec(
                name="vibecrafted",
                binaries=("vibecrafted",),
                env_roots=("VIBECRAFTED_SOURCE",),
                markers=(("VERSION", r".+"),),
                candidate_roots=(),
            )
        ]
    )["tools"]

    assert tool["source"]["resolution"] == "installed_runtime_manifest"
    assert tool["source"]["owner_repo"] == "vetcoders/vibecrafted"
    assert tool["source"]["checkout_sha"] == _RUNTIME_REVISION
    assert tool["source"]["source_payload"] == _SOURCE_PAYLOAD
    assert tool["installed"]["dirty_build"] is False
    assert rr.DRIFT_DIRTY_BUILD not in tool["drift"]


@pytest.mark.parametrize(
    "mutation",
    (
        "wrong_schema",
        "version_mismatch",
        "invalid_owner",
        "uppercase_source_fingerprint",
        "uppercase_source_revision",
        "missing_field",
        "extra_field",
        "wrong_entrypoint",
        "uppercase_bound_sha",
        "legacy_four_hashes",
        "missing_source_payload",
        "open_source_payload",
        "invalid_source_payload_count",
    ),
)
def test_installed_runtime_manifest_rejects_noncanonical_manifest(
    tmp_path: Path, mutation: str
) -> None:
    generation, deck, manifest = _runtime_generation_fixture(tmp_path)
    hashes = manifest["hashes"]
    assert isinstance(hashes, dict)
    if mutation == "wrong_schema":
        manifest["schema"] = "vibecrafted.runtime-generation.v0"
    elif mutation == "version_mismatch":
        manifest["version"] = "3.7.1"
    elif mutation == "invalid_owner":
        manifest["owner_repo"] = "vetcoders"
    elif mutation == "uppercase_source_fingerprint":
        manifest["source_fingerprint"] = "A" * 64
    elif mutation == "uppercase_source_revision":
        manifest["source_revision"] = _RUNTIME_REVISION.upper()
    elif mutation == "missing_field":
        manifest.pop("source_fingerprint")
    elif mutation == "extra_field":
        manifest["unbound"] = "forbidden"
    elif mutation == "wrong_entrypoint":
        manifest["entrypoint"] = "scripts/vibecrafted"
    elif mutation == "uppercase_bound_sha":
        hashes["VERSION"] = "A" * 64
    elif mutation == "legacy_four_hashes":
        manifest["hashes"] = {
            "VERSION": hashes["VERSION"],
            "scripts/vibecrafted": hashes["scripts/vibecrafted"],
            pc.RUNTIME_GENERATION_PROJECTED_CONFIG: hashes[
                pc.RUNTIME_GENERATION_CANONICAL_CONFIG
            ],
            pc.RUNTIME_GENERATION_ENTRYPOINT: hashes[pc.RUNTIME_GENERATION_ENTRYPOINT],
        }
    elif mutation == "missing_source_payload":
        manifest.pop("source_payload")
    elif mutation == "open_source_payload":
        source_payload = manifest["source_payload"]
        assert isinstance(source_payload, dict)
        source_payload["unbound"] = True
    elif mutation == "invalid_source_payload_count":
        source_payload = manifest["source_payload"]
        assert isinstance(source_payload, dict)
        source_payload["entry_count"] = 0
    else:  # pragma: no cover - closed parametrization above.
        raise AssertionError(f"unknown mutation: {mutation}")
    _write_runtime_manifest(generation, manifest)

    assert rr._installed_runtime_manifest(str(deck)) is None


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "v1",
        "open",
        "owner_mismatch",
        "revision_mismatch",
        "payload_mismatch",
    ),
)
def test_installed_runtime_manifest_rejects_noncanonical_source_provenance(
    tmp_path: Path, mutation: str
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    provenance_path = generation / pc.SOURCE_PROVENANCE_NAME
    if mutation == "missing":
        provenance_path.unlink()
    else:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if mutation == "v1":
            provenance["schema"] = "vibecrafted.source-provenance.v1"
        elif mutation == "open":
            provenance["unbound"] = True
        elif mutation == "owner_mismatch":
            provenance["owner_repo"] = "vetcoders/other"
        elif mutation == "revision_mismatch":
            provenance["source_revision"] = "f" * 40
        elif mutation == "payload_mismatch":
            provenance["payload"]["tree_sha256"] = "f" * 64
        else:  # pragma: no cover
            raise AssertionError(mutation)
        provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    probe = rr._probe_installed_runtime_manifest(str(deck))
    assert probe.state == "rejection"
    assert probe.reason is not None
    assert probe.reason.startswith("VCPC")


def test_runtime_receipt_rejects_source_provenance_path_swap_during_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    provenance_path = generation / pc.SOURCE_PROVENANCE_NAME
    replacement = generation / "source-provenance.next.json"
    changed = json.loads(provenance_path.read_text(encoding="utf-8"))
    changed["payload"]["tree_sha256"] = "f" * 64
    replacement.write_text(
        json.dumps(changed, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    real_open = pc.os.open
    real_fstat = pc.os.fstat
    real_replace = pc.os.replace
    state: dict[str, object] = {"fd": None, "fstats": 0, "swapped": False}

    def open_then_track(path, flags, *args, **kwargs):
        descriptor = real_open(path, flags, *args, **kwargs)
        if Path(path) == provenance_path:
            state["fd"] = descriptor
        return descriptor

    def replace_path_after_second_fstat(descriptor: int):
        metadata = real_fstat(descriptor)
        if descriptor == state["fd"]:
            state["fstats"] = int(state["fstats"]) + 1
            if state["fstats"] == 2:
                real_replace(replacement, provenance_path)
                state["swapped"] = True
                state["fd"] = None
        return metadata

    monkeypatch.setattr(pc.os, "open", open_then_track)
    monkeypatch.setattr(pc.os, "fstat", replace_path_after_second_fstat)

    probe = rr._probe_installed_runtime_manifest(str(deck))

    assert state["swapped"] is True
    assert probe.state == "rejection"
    assert probe.source is None
    assert probe.reason is not None
    assert probe.reason.startswith(f"VCPC{pc.E_PROOF:03d}: ")
    assert (
        json.loads(provenance_path.read_text(encoding="utf-8"))["payload"][
            "tree_sha256"
        ]
        == "f" * 64
    )


def test_installed_runtime_manifest_rejects_bound_byte_drift(tmp_path: Path) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    (generation / "scripts/vibecrafted").write_bytes(b"drifted\n")

    assert rr._installed_runtime_manifest(str(deck)) is None


def test_receipt_fails_closed_when_installed_runtime_manifest_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    (generation / "scripts/vibecrafted").write_bytes(b"drifted\n")
    monkeypatch.setattr(
        rr, "which_binary", lambda name: str(deck) if name == "vibecrafted" else None
    )
    monkeypatch.setattr(rr, "run_version", lambda _path: "vibecrafted 3.7.0+g01234567")
    monkeypatch.setattr(rr, "_vibecrafted_tools_path_hints", list)

    manifest_probe = rr._probe_installed_runtime_manifest(str(deck))
    assert manifest_probe.state == "rejection"
    assert manifest_probe.source is None
    assert manifest_probe.reason is not None
    assert manifest_probe.reason.startswith("VCPC024: ")

    def reject_checkout_fallback(_spec: rr.ToolSpec) -> tuple[None, str]:
        raise AssertionError(
            "rejected installed runtime must not fall back to checkout"
        )

    monkeypatch.setattr(rr, "resolve_source_root", reject_checkout_fallback)
    tool = rr.inspect_tool(
        rr.ToolSpec(
            name="vibecrafted",
            binaries=("vibecrafted",),
            env_roots=("VIBECRAFTED_SOURCE",),
            markers=(("VERSION", r".+"),),
            candidate_roots=(),
        )
    )

    assert tool["source"]["resolution"] == "installed_runtime_manifest_rejected"
    for field in ("path", "owner_repo", "branch", "checkout_sha", "dirty"):
        assert tool["source"][field]["value"] == "unknown"
        assert tool["source"][field]["reason"].startswith("VCPC024: ")
    assert tool["installed"]["dirty_build"] is True
    assert tool["installed"]["dirty_build_reason"].startswith("VCPC024: ")
    assert tool["primary_drift"] == rr.DRIFT_DIRTY_BUILD
    assert rr.DRIFT_DIRTY_BUILD in tool["drift"]
    assert rr.DRIFT_CLEAN not in tool["drift"]


def test_installed_runtime_manifest_accepts_internal_runtime_projection(
    tmp_path: Path,
) -> None:
    generation, deck, manifest = _runtime_generation_fixture(
        tmp_path, runtime_projection="internal"
    )

    assert os.readlink(generation / "runtime") == (
        "vibecrafted-core/vibecrafted_core/runtime"
    )
    assert (
        pc.verify_installed_runtime_generation(generation, expected_entrypoint=deck)
        == manifest
    )
    assert rr._installed_runtime_manifest(str(deck)) is not None


@pytest.mark.parametrize("alias_component", ("generated", "config"))
def test_installed_runtime_manifest_rejects_canonical_config_alias(
    tmp_path: Path, alias_component: str
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    canonical = generation / pc.RUNTIME_GENERATION_CANONICAL_CONFIG

    if alias_component == "generated":
        generated = canonical.parents[1]
        generated.rename(generated.with_name("generated.unbound"))
        generated.symlink_to("generated.unbound", target_is_directory=True)
    else:
        backing = canonical.with_name("config.unbound.kdl")
        canonical.rename(backing)
        canonical.symlink_to(backing.name)

    with pytest.raises(pc.ProductContractError) as captured:
        pc.verify_installed_runtime_generation(generation, expected_entrypoint=deck)
    assert captured.value.code == pc.E_PATH


def test_installed_runtime_manifest_rejects_escaping_canonical_config(
    tmp_path: Path,
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    canonical = generation / pc.RUNTIME_GENERATION_CANONICAL_CONFIG
    external = tmp_path / "escaping-config.kdl"
    canonical.rename(external)
    canonical.symlink_to(external)

    with pytest.raises(pc.ProductContractError) as captured:
        pc.verify_installed_runtime_generation(generation, expected_entrypoint=deck)
    assert captured.value.code == pc.E_PATH
    assert rr._installed_runtime_manifest(str(deck)) is None


def test_installed_runtime_manifest_rejects_arbitrary_internal_parent_alias(
    tmp_path: Path,
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    core = generation / "vibecrafted-core"
    backing = generation / "internal-core"
    core.rename(backing)
    core.symlink_to(backing.name, target_is_directory=True)

    with pytest.raises(pc.ProductContractError) as captured:
        pc.verify_installed_runtime_generation(generation, expected_entrypoint=deck)
    assert captured.value.code == pc.E_PATH
    assert "is aliased" in str(captured.value)
    assert rr._installed_runtime_manifest(str(deck)) is None


def test_installed_runtime_manifest_rejects_non_entrypoint_binary(
    tmp_path: Path,
) -> None:
    generation, _deck, _manifest = _runtime_generation_fixture(tmp_path)

    assert (
        rr._installed_runtime_manifest(str(generation / "scripts/vibecrafted")) is None
    )


def test_runtime_generation_cli_has_stable_success_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)

    assert (
        pc.main(
            [
                "runtime-generation",
                str(generation),
                "--expected-entrypoint",
                str(deck),
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == f"verified runtime-generation: {generation}\n"
    assert captured.err == ""


def test_runtime_generation_cli_returns_contract_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    generation, _deck, _manifest = _runtime_generation_fixture(tmp_path)
    (generation / "scripts/vibecrafted").write_bytes(b"drifted\n")

    assert pc.main(["runtime-generation", str(generation)]) == pc.E_HASH
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith(f"VCPC{pc.E_HASH:03d}: ")


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_installed_runtime_manifest_rejects_aliased_bound_file(
    tmp_path: Path, alias_kind: str
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    target = generation / "vibecrafted-core/vibecrafted_core/product_contract.py"
    backing = target.with_name("product_contract.fixture.py")
    target.rename(backing)
    if alias_kind == "symlink":
        target.symlink_to(backing.name)
    else:
        os.link(backing, target)

    assert rr._installed_runtime_manifest(str(deck)) is None


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_installed_runtime_manifest_rejects_aliased_manifest(
    tmp_path: Path, alias_kind: str
) -> None:
    generation, deck, _manifest = _runtime_generation_fixture(tmp_path)
    target = generation / pc.RUNTIME_GENERATION_MANIFEST_NAME
    backing = generation / "runtime-manifest.fixture.json"
    target.rename(backing)
    if alias_kind == "symlink":
        target.symlink_to(backing.name)
    else:
        os.link(backing, target)

    assert rr._installed_runtime_manifest(str(deck)) is None


def test_render_contains_drift_tokens() -> None:
    receipt = {
        "schema": rr.SCHEMA_VERSION,
        "cwd_policy": "never uses process cwd",
        "tools": [
            {
                "name": "vc-frame",
                "primary_drift": rr.DRIFT_SOURCE_AHEAD,
                "drift": [rr.DRIFT_SOURCE_AHEAD, rr.DRIFT_UNPUSHED],
                "chain": {
                    "owner_repo": "vetcoders/vc-frame",
                    "branch": "develop",
                    "checkout_sha": "7fa51c66",
                    "dirty": False,
                    "installed_sha": "5c99f72d",
                    "ahead": 3,
                    "behind": 0,
                    "index_generation": {"value": "unknown", "reason": "n/a"},
                },
                "source": {
                    "path": "/tmp/vc-frame",
                    "resolution": "env:VC_FRAME_SOURCE",
                    "dirty_detail": {
                        "source_dirty_count": 0,
                        "generated_dirty_count": 0,
                    },
                },
                "installed": {
                    "path": "/usr/bin/vc-frame",
                    "sha": "5c99f72d",
                    "dirty_build": True,
                    "version_line": "vc-frame 0.46.0+g5c99f72d.dirty",
                },
                "remote": {"upstream": "origin/develop", "ahead": 3, "behind": 0},
                "index": None,
                "related": [],
            }
        ],
        "summary": {"tool_count": 1, "by_primary_drift": {rr.DRIFT_SOURCE_AHEAD: 1}},
    }
    text = rr.render_receipt_text(receipt)
    assert "SOURCE_AHEAD_OF_INSTALLED" in text
    assert "UNPUSHED" in text
    assert "7fa51c66" in text


def test_is_generated_path() -> None:
    assert rr._is_generated_path("target/release/foo")
    assert rr._is_generated_path(".loctree/context-atlas/x")
    assert not rr._is_generated_path("src/main.rs")


def test_receipt_text_says_installed_only_instead_of_eight_unknowns():
    """A plain install has no checkout; repeating the same source-root reason
    across eight fields per tool reads as breakage to a first-time user."""
    receipt = {
        "schema": "x",
        "cwd_policy": "y",
        "tools": [
            {
                "name": "loct",
                "primary_drift": "INSTALLED_NOT_ON_PATH",
                "drift": ["INSTALLED_NOT_ON_PATH"],
                "chain": {
                    "owner_repo": {
                        "value": "unknown",
                        "reason": "no verified source root",
                    },
                    "checkout_sha": {
                        "value": "unknown",
                        "reason": "no verified source root",
                    },
                },
                "source": {
                    "path": {"value": "unknown", "reason": "no verified source root"}
                },
                "installed": {"path": "/usr/bin/loct", "sha": "abc"},
                "remote": {},
            }
        ],
    }

    text = rr.render_receipt_text(receipt)

    assert "installed-only" in text
    assert text.count("no verified source root") == 0
    assert "/usr/bin/loct" in text  # what IS known stays visible


def test_receipt_text_keeps_full_drift_detail_for_a_real_checkout():
    receipt = {
        "schema": "x",
        "cwd_policy": "y",
        "tools": [
            {
                "name": "loct",
                "primary_drift": "CLEAN",
                "drift": [],
                "chain": {
                    "owner_repo": "Loctree/loctree-suite",
                    "checkout_sha": "deadbeef",
                    "branch": "main",
                    "dirty": False,
                },
                "source": {"path": "/src/loctree", "resolution": "verified_candidate"},
                "installed": {"path": "/usr/bin/loct", "sha": "deadbeef"},
                "remote": {"upstream": "origin/main", "ahead": 0, "behind": 0},
            }
        ],
    }

    text = rr.render_receipt_text(receipt)

    assert "source path:" in text
    assert "checkout SHA:" in text
    assert "upstream:" in text
    assert "installed-only" not in text
