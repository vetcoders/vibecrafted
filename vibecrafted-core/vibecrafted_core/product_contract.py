"""Fail-closed contracts for the unified Vibecrafted macOS product.

The module payloads, assembled app, activation transaction, and release
walk-around each have a versioned manifest.  This verifier intentionally takes
only paths supplied by the caller: it never searches sibling checkouts, PATH,
or ``/Applications`` for product inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from xml.parsers.expat import ExpatError

MODULE_SCHEMA = "io.vetcoders.vibecrafted.module.v1"
ASSEMBLY_SCHEMA = "io.vetcoders.vibecrafted.module-assembly.v1"
PRODUCT_SCHEMA = "io.vetcoders.vibecrafted.product.v1"
TRANSACTION_SCHEMA = "io.vetcoders.vibecrafted.transaction.v1"
WALKAROUND_SCHEMA = "io.vetcoders.vibecrafted.walkaround.v1"
LAUNCH_SCHEMA = "io.vetcoders.vibecrafted.launch.v1"
RELEASE_OUTPUT_SCHEMA = "io.vetcoders.vibecrafted.release-output.v1"
RELEASE_POLICY_SCHEMA = "io.vetcoders.vibecrafted.release-policy.v1"
TRUST_PROBE_SCHEMA = "io.vetcoders.vibecrafted.trust-probe.v1"
TRUST_PROBE_DOMAIN = "io.vetcoders.vibecrafted.release-trust-probe.v1"

PRODUCT_NAME = "Vibecrafted"
PRODUCT_BUNDLE_ID = "io.vetcoders.vibecrafted"
PRODUCT_EXECUTABLE = "Vibecrafted"
PRODUCT_ICON_FILE = "Vibecrafted.icns"
SUPPORTED_MODULES = frozenset({"vc-terminal", "vc-frame"})
SUPPORTED_ARCHITECTURES = frozenset({"arm64"})
MINIMUM_MACOS = (14, 0)
WALKAROUND_RUNNER_ID = "io.vetcoders.vibecrafted.walkaround-runner.v1"
WALKAROUND_RUNNER_EXECUTABLE = "verify-vibecrafted-walkaround"
OUTER_BUNDLE_CODE_IDENTITY = "outer-bundle-codesign-v1"
MACHO_CODE_IDENTITY = "macho-code-v1"
TRUSTED_RUNNER_PUBLIC_KEY_NAME = "vibecrafted-signing-v1.pub"
RELEASE_POLICY_NAME = "release-policy.v1.json"
RELEASE_DMG_PATTERN = (
    r"^Vibecrafted_[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?-"
    r"[0-9]{8}-[0-9a-f]{8}\.dmg$"
)
RELEASE_KEY_SPKI_SHA256 = (
    "521ed59d3c446c540afe1557c2dbc39c9c190775f99896b2b65206c32814b25b"
)


def canonical_release_dmg_name(
    *, version: str, release_date: str, source_revision: str
) -> str:
    """Return the immutable public DMG name bound to version, date and source."""

    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError("release version must be canonical SemVer")
    if not re.fullmatch(r"[0-9]{8}", release_date):
        raise ValueError("release date must be YYYYMMDD")
    revision = source_revision.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise ValueError("release source revision must be a full Git object id")
    return f"Vibecrafted_{version}-{release_date}-{revision[:8]}.dmg"


def is_canonical_release_dmg_name(
    name: str, *, version: str, source_revision: str
) -> bool:
    """Validate the public name and its bindings without trusting its date."""

    if re.fullmatch(RELEASE_DMG_PATTERN, name) is None:
        return False
    revision = source_revision.strip().lower()
    if re.fullmatch(r"[0-9a-f]{40,64}", revision) is None:
        return False
    prefix = f"Vibecrafted_{version}-"
    suffix = f"-{revision[:8]}.dmg"
    return name.startswith(prefix) and name.endswith(suffix)


RUNTIME_GENERATION_SCHEMA = "vibecrafted.runtime-generation.v2"
RUNTIME_GENERATION_MANIFEST_NAME = "runtime-manifest.json"
SOURCE_PROVENANCE_NAME = "source-provenance.json"
SOURCE_PROVENANCE_SCHEMA = "vibecrafted.source-provenance.v2"
SOURCE_PAYLOAD_SCHEMA = "vibecrafted.distribution-tree.v1"
RUNTIME_GENERATION_ENTRYPOINT = "bin/vibecrafted"
RUNTIME_GENERATION_PROJECTED_CONFIG = "runtime/generated/vc-frame/config.kdl"
RUNTIME_GENERATION_CANONICAL_CONFIG = (
    "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
)
RUNTIME_GENERATION_REQUIRED_HASHES = frozenset(
    {
        "VERSION",
        "scripts/distribution_manifest.py",
        "scripts/installer_brand.py",
        "scripts/vibecrafted",
        "scripts/vetcoders_install.py",
        RUNTIME_GENERATION_CANONICAL_CONFIG,
        RUNTIME_GENERATION_ENTRYPOINT,
        "vibecrafted-core/vibecrafted_core/product_contract.py",
        "vibecrafted-core/vibecrafted_core/walkaround_runner.py",
        "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json",
        "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json",
        "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub",
    }
)

_MACHO_CODE_DOMAIN = b"io.vetcoders.vibecrafted.macho-code-v1\0"
_MACHO_MAX_BYTES = 512 * 1024 * 1024
_MACHO_MAX_COMMANDS = 4096
_MACHO_MAX_COMMAND_BYTES = 16 * 1024 * 1024
_MH_MAGIC_64 = 0xFEEDFACF
_CPU_TYPE_ARM64 = 0x0100000C
_MH_EXECUTE = 2
_LC_SEGMENT_64 = 0x19
_LC_CODE_SIGNATURE = 0x1D

E_JSON = 20
E_SCHEMA = 21
E_MISSING = 22
E_PATH = 23
E_HASH = 24
E_INVENTORY = 25
E_BUNDLE = 26
E_DEPENDENCY = 27
E_TRANSACTION = 28
E_PLATFORM = 29
E_MODE = 30
E_SIZE = 31
E_ENTRYPOINT = 32
E_PROOF = 33

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_MODE_RE = re.compile(r"0[0-7]{3}")
_MACOS_RE = re.compile(r"([0-9]+)\.([0-9]+)")
_BUILD_HOST_PATH_RE = re.compile(
    rb"(?:^|[\s\"'=:(])(?<![A-Za-z]:)(?P<path>/(?:Volumes|Users|opt/homebrew|usr/local)/[^\s\"'\x00]{0,512})"
)
_EMBEDDED_DOCUMENTATION_PATHS = {
    "Contents/Resources/runtime/python/lib/libpython3.12.dylib": frozenset(
        {
            "/usr/local/lib/python2.5/site-packages",
            "/usr/local/lib/python2.5/site-packages/bar",
            "/usr/local/lib/python2.5/site-packages/foo",
        }
    ),
}
_FILE_KINDS = frozenset({"executable", "dylib", "resource", "config"})
_APP_ENTRYPOINTS = frozenset({"app", "terminal", "frame"})
_WALKAROUND_CHECKS = frozenset(
    {
        "one_app",
        "app_codesign",
        "dmg_codesign",
        "app_notarization",
        "dmg_notarization",
        "app_gatekeeper",
        "dmg_gatekeeper",
        "sanitized_launch",
        "mission_control",
        "bundled_console",
        "start_here",
        "update",
        "rollback",
        "reattach",
        "one_outer_writer",
    }
)
_LAUNCH_INHERITED_ENV = (
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "COLORTERM",
    "TMPDIR",
    "SHELL",
)
_LAUNCH_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_WALKAROUND_TEMP_PARENT = Path("/tmp")
_LAUNCH_TERMINAL = "Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
_LAUNCH_FRAME = "Contents/Helpers/vc-frame"
_LAUNCH_CONFIG = "Contents/Resources/terminal/vibecrafted.toml"
_LAUNCH_SHELL = "Contents/Resources/runtime/bin/vc-start"
_LAUNCH_PRIMARY_SHELL = (
    "Contents/Resources/runtime/config/alacritty/launch-primary-shell.zsh"
)
_TERMINAL_HELPER_APP = "Contents/Helpers/vc-terminal.app"
_TERMINAL_HELPER_BUNDLE_ID = "io.vetcoders.vc-terminal"
_TERMINAL_HELPER_ICON = "alacritty.icns"
PRODUCT_MANIFEST_REFERENT = "manifests/product-manifest.json"
RUNTIME_MANIFEST_REFERENT = "manifests/runtime-manifest.json"
_MAX_SIGNED_PAYLOAD_BYTES = 64 * 1024 * 1024
_RELEASE_SIGNATURE_BYTES = 256  # Pinned 2048-bit RSA trust root.


@dataclass(frozen=True)
class _MachOInfo:
    relative: str
    dependencies: tuple[str, ...]
    rpaths: tuple[str, ...]
    architectures: frozenset[str]
    minimum_macos: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _ValidatedFiles:
    entries: dict[str, Mapping[str, Any]]
    machos: dict[str, _MachOInfo]


@dataclass(frozen=True)
class _ModuleManifest:
    module: str
    version: str
    git_sha: str
    dirty: bool
    architecture: str
    minimum_macos: str
    files: dict[str, Mapping[str, Any]]
    entrypoints: dict[str, str]


@dataclass(frozen=True)
class _CapturedProofArtifact:
    """One no-follow, bounded snapshot used for every later trust decision."""

    path: Path
    raw: bytes
    sha256: str
    size: int


@dataclass(frozen=True)
class ProbeSpec:
    """Closed definition of one canonical release walk-around proof."""

    name: str
    executor: str
    owner_stage: str
    operation_id: str
    assertions: tuple[str, ...]
    argv: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.executor not in {"builtin", "argv", "scenario", "pipeline_gate"}:
            raise ValueError(f"unsupported walk-around executor: {self.executor}")
        if not self.name or not self.owner_stage or not self.operation_id:
            raise ValueError("walk-around probe metadata must be non-empty")
        if not self.assertions or len(set(self.assertions)) != len(self.assertions):
            raise ValueError(
                "walk-around probe assertions must be non-empty and unique"
            )
        if (self.executor == "argv") != (self.argv is not None):
            raise ValueError("only argv probes may carry command argv")


class ProductContractError(ValueError):
    """A stable, machine-classifiable unified-product contract violation."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: int, message: str) -> NoReturn:
    raise ProductContractError(code, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_unique_keys(
    _validator: Any,
    keys: Any,
    instance: Any,
    _schema: Any,
) -> Sequence[Any]:
    errors: list[Any] = []
    if not isinstance(keys, list) or not isinstance(instance, list):
        return errors
    for key in keys:
        if not isinstance(key, str):
            continue
        seen: dict[Any, int] = {}
        for index, item in enumerate(instance):
            if not isinstance(item, dict) or key not in item:
                continue
            value = item[key]
            if value in seen:
                _draft, validation_error, _validators = _jsonschema_components()
                errors.append(
                    validation_error(
                        f"duplicate {key} at indexes {seen[value]} and {index}"
                    )
                )
            else:
                seen[value] = index
    return errors


def _validate_canonical_relative_path(
    _validator: Any,
    enabled: Any,
    instance: Any,
    _schema: Any,
) -> Sequence[Any]:
    if enabled is not True or not isinstance(instance, str):
        return ()
    pure = PurePosixPath(instance)
    if (
        pure.is_absolute()
        or pure in {PurePosixPath("."), PurePosixPath("..")}
        or ".." in pure.parts
        or "\\" in instance
        or pure.as_posix() != instance
    ):
        _draft, validation_error, _validators = _jsonschema_components()
        return (validation_error("path is not canonical relative POSIX spelling"),)
    return ()


_JSONSCHEMA_COMPONENTS: tuple[type[Any], type[Exception], Any] | None = None
_UNIFIED_PRODUCT_VALIDATOR: type[Any] | None = None


def _jsonschema_components() -> tuple[type[Any], type[Exception], Any]:
    """Load the optional heavy schema engine only for commands that validate product JSON."""
    global _JSONSCHEMA_COMPONENTS
    if _JSONSCHEMA_COMPONENTS is not None:
        return _JSONSCHEMA_COMPONENTS
    try:
        from jsonschema import Draft202012Validator, ValidationError, validators
    except ImportError as exc:
        _fail(E_DEPENDENCY, f"jsonschema dependency is unavailable: {exc}")
    _JSONSCHEMA_COMPONENTS = (Draft202012Validator, ValidationError, validators)
    return _JSONSCHEMA_COMPONENTS


def _unified_product_validator_class() -> type[Any]:
    """Build and cache the extended validator after its dependency is proven available."""
    global _UNIFIED_PRODUCT_VALIDATOR
    if _UNIFIED_PRODUCT_VALIDATOR is None:
        draft, _validation_error, validators = _jsonschema_components()
        _UNIFIED_PRODUCT_VALIDATOR = validators.extend(
            draft,
            {
                "x-vibecrafted-uniqueKeys": _validate_unique_keys,
                "x-vibecrafted-canonicalRelativePath": (
                    _validate_canonical_relative_path
                ),
            },
        )
    return _UNIFIED_PRODUCT_VALIDATOR


class UnifiedProductValidator:
    """Compatibility constructor for the lazily loaded extended JSON Schema validator."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        return _unified_product_validator_class()(*args, **kwargs)


def validate_schema_document(payload: Mapping[str, Any]) -> None:
    """Validate one public product-contract document with supported semantics."""
    schema_path = Path(__file__).parent / "schemas/unified_product.schema.v1.json"
    schema = _load_json(schema_path)
    errors = sorted(
        UnifiedProductValidator(schema).iter_errors(payload),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        _fail(E_SCHEMA, f"schema validation failed at {location}: {error.message}")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(E_MISSING, f"missing manifest or receipt: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        _fail(E_JSON, f"invalid JSON at {path}: {exc}")
    return _load_json_bytes(raw, source=path)


def _load_json_bytes(raw: bytes, *, source: Path) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(E_JSON, f"invalid JSON at {source}: {exc}")
    if not isinstance(payload, dict):
        _fail(E_SCHEMA, f"top-level JSON must be an object: {source}")
    return payload


def _expect_keys(
    payload: Mapping[str, Any],
    *,
    required: set[str] | frozenset[str],
    context: str,
) -> None:
    missing = sorted(set(required) - set(payload))
    extra = sorted(set(payload) - set(required))
    if missing:
        _fail(E_MISSING, f"{context} missing keys: {', '.join(missing)}")
    if extra:
        _fail(E_SCHEMA, f"{context} has unknown keys: {', '.join(extra)}")


def _expect_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(E_SCHEMA, f"{field} must be a non-empty string")
    return value


def _expect_sha256(value: Any, *, field: str) -> str:
    text = _expect_string(value, field=field)
    if not _SHA256_RE.fullmatch(text):
        _fail(E_SCHEMA, f"{field} must be a lowercase SHA-256")
    return text


def _expect_git_sha(value: Any, *, field: str) -> str:
    text = _expect_string(value, field=field)
    if not _GIT_SHA_RE.fullmatch(text):
        _fail(E_SCHEMA, f"{field} must be a full lowercase Git SHA")
    return text


def _relative_path(value: Any, *, field: str) -> Path:
    text = _expect_string(value, field=field)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or pure in {PurePosixPath("."), PurePosixPath("..")}
        or ".." in pure.parts
        or "\\" in text
        or pure.as_posix() != text
    ):
        _fail(E_PATH, f"{field} must be a normalized relative POSIX path: {text}")
    return Path(*pure.parts)


def _minimum_macos(value: Any, *, field: str) -> str:
    text = _expect_string(value, field=field)
    match = _MACOS_RE.fullmatch(text)
    if not match:
        _fail(E_PLATFORM, f"{field} must be major.minor")
    version = (int(match.group(1)), int(match.group(2)))
    if version < MINIMUM_MACOS:
        _fail(E_PLATFORM, f"{field} must be macOS 14.0 or newer")
    return text


def _validate_platform(payload: Mapping[str, Any], *, context: str) -> tuple[str, str]:
    architecture = _expect_string(
        payload.get("architecture"), field=f"{context}.architecture"
    )
    if architecture not in SUPPORTED_ARCHITECTURES:
        _fail(E_PLATFORM, f"{context} unsupported architecture: {architecture!r}")
    minimum_macos = _minimum_macos(
        payload.get("minimum_macos"), field=f"{context}.minimum_macos"
    )
    return architecture, minimum_macos


def _is_excluded(relative: Path, exclusions: Sequence[Path]) -> bool:
    return any(relative == item or item in relative.parents for item in exclusions)


def _payload_files(root: Path, *, exclusions: Sequence[Path]) -> set[str]:
    files: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _is_excluded(relative, exclusions):
            continue
        if path.is_symlink():
            _fail(E_PATH, f"payload symlinks are forbidden: {relative.as_posix()}")
        if path.is_file():
            files.add(relative.as_posix())
    return files


def _required_tool(name: str, *, failure_code: int) -> str:
    executable = shutil.which(name)
    if executable is None:
        _fail(failure_code, f"{name} is required for product verification")
    return executable


def _run_tool(command: Sequence[str], *, failure_code: int, context: str) -> str:
    result = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        _fail(failure_code, f"{context}: {detail}")
    return result.stdout


def _verify_assembler_signed_macho(path: Path, *, relative: str) -> None:
    """Require a post-link code signature, not the linker-generated ad-hoc placeholder."""
    codesign = _required_tool("codesign", failure_code=E_PROOF)
    _run_tool(
        [codesign, "--verify", "--strict", "--verbose=2", str(path)],
        failure_code=E_PROOF,
        context=f"signed transformation has an invalid code signature: {relative}",
    )
    result = subprocess.run(
        [codesign, "--display", "--verbose=4", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    metadata = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0:
        _fail(E_PROOF, f"signed transformation has no valid code signature: {relative}")
    if "linker-signed" in metadata:
        _fail(
            E_PROOF,
            f"signed transformation retained only a linker signature: {relative}",
        )


def _codesign_identifier(path: Path) -> str:
    codesign = _required_tool("codesign", failure_code=E_PROOF)
    result = subprocess.run(
        [codesign, "--display", "--verbose=4", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(E_PROOF, f"bundle has no readable code signature: {path}")
    metadata = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"^Identifier=(.+)$", metadata, flags=re.MULTILINE)
    if match is None:
        _fail(E_PROOF, f"bundle signature has no Identifier: {path}")
    return match.group(1).strip()


def _macho_code_sha256(path: Path) -> str:
    """Hash the immutable code image while excluding only signature bookkeeping."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        _fail(E_MISSING, f"outer Mach-O is unavailable: {exc}")
    if size > _MACHO_MAX_BYTES:
        _fail(E_SIZE, "outer Mach-O exceeds the 512 MiB identity limit")
    try:
        data = path.read_bytes()
    except OSError as exc:
        _fail(E_MISSING, f"outer Mach-O cannot be read: {exc}")
    if len(data) < 32:
        _fail(E_PROOF, "outer Mach-O header is truncated")
    try:
        magic, cpu_type, _, file_type, ncmds, sizeofcmds, _, _ = struct.unpack_from(
            "<IiiIIIII", data, 0
        )
    except struct.error as exc:  # pragma: no cover - guarded by the size check.
        _fail(E_PROOF, f"outer Mach-O header is malformed: {exc}")
    if magic != _MH_MAGIC_64 or cpu_type != _CPU_TYPE_ARM64 or file_type != _MH_EXECUTE:
        _fail(
            E_PLATFORM,
            "outer code identity requires thin little-endian arm64 MH_EXECUTE",
        )
    if ncmds > _MACHO_MAX_COMMANDS or sizeofcmds > _MACHO_MAX_COMMAND_BYTES:
        _fail(E_SIZE, "outer Mach-O load-command limits exceeded")
    commands_end = 32 + sizeofcmds
    if commands_end > len(data):
        _fail(E_PROOF, "outer Mach-O load commands are out of bounds")

    segments: list[tuple[str, int, int, int, int, int, int]] = []
    code_signatures: list[tuple[int, int, int]] = []
    offset = 32
    for index in range(ncmds):
        if offset % 8 or offset + 8 > commands_end:
            _fail(E_PROOF, "outer Mach-O has an unaligned or truncated load command")
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmdsize < 8 or cmdsize % 8 or offset + cmdsize > commands_end:
            _fail(E_PROOF, "outer Mach-O has a malformed load command")
        if cmd == _LC_SEGMENT_64:
            if cmdsize < 72:
                _fail(E_PROOF, "outer Mach-O has a truncated LC_SEGMENT_64")
            raw_name = data[offset + 8 : offset + 24].split(b"\0", 1)[0]
            try:
                name = raw_name.decode("ascii")
            except UnicodeDecodeError:
                _fail(E_PROOF, "outer Mach-O has a non-ASCII segment name")
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from(
                "<QQQQ", data, offset + 24
            )
            nsects = struct.unpack_from("<I", data, offset + 64)[0]
            if cmdsize != 72 + nsects * 80:
                _fail(E_PROOF, f"outer Mach-O segment {name!r} has malformed sections")
            segments.append((name, offset, vmaddr, vmsize, fileoff, filesize, nsects))
        elif cmd == _LC_CODE_SIGNATURE:
            if cmdsize != 16:
                _fail(E_PROOF, "outer Mach-O has a malformed LC_CODE_SIGNATURE")
            dataoff, datasize = struct.unpack_from("<II", data, offset + 8)
            code_signatures.append((offset, dataoff, datasize))
            if index != ncmds - 1:
                _fail(E_PROOF, "outer Mach-O LC_CODE_SIGNATURE is not terminal")
        offset += cmdsize
    if offset != commands_end:
        _fail(E_PROOF, "outer Mach-O load-command size does not match its header")

    names = [segment[0] for segment in segments]
    if len(names) != len(set(names)):
        _fail(E_PROOF, "outer Mach-O contains duplicate segments")
    linkedit = [segment for segment in segments if segment[0] == "__LINKEDIT"]
    if len(linkedit) != 1 or not segments or segments[-1][0] != "__LINKEDIT":
        _fail(E_PROOF, "outer Mach-O requires one final __LINKEDIT segment")
    if len(code_signatures) != 1:
        _fail(E_PROOF, "outer Mach-O requires one LC_CODE_SIGNATURE")

    file_ranges: list[tuple[int, int, str]] = []
    vm_ranges: list[tuple[int, int, str]] = []
    for name, _, vmaddr, vmsize, fileoff, filesize, _ in segments:
        if fileoff + filesize > len(data):
            _fail(E_PROOF, f"outer Mach-O segment {name!r} is out of file bounds")
        if filesize:
            file_ranges.append((fileoff, fileoff + filesize, name))
        if vmsize:
            vm_ranges.append((vmaddr, vmaddr + vmsize, name))
    for ranges, label in ((file_ranges, "file"), (vm_ranges, "virtual-memory")):
        ordered = sorted(ranges)
        for previous, current in pairwise(ordered):
            if current[0] < previous[1]:
                _fail(E_PROOF, f"outer Mach-O has overlapping {label} segments")

    _, linkedit_command, _, _, linkedit_fileoff, linkedit_filesize, nsects = linkedit[0]
    if nsects != 0:
        _fail(E_PROOF, "outer Mach-O __LINKEDIT must not contain sections")
    for name, _, _, _, fileoff, filesize, _ in segments:
        if name == "__LINKEDIT":
            continue
        if fileoff + filesize > linkedit_fileoff or (
            filesize == 0 and fileoff >= linkedit_fileoff
        ):
            _fail(
                E_PROOF,
                f"outer Mach-O segment {name!r} reaches into __LINKEDIT",
            )
    signature_command, dataoff, datasize = code_signatures[0]
    if dataoff % 16 or datasize == 0:
        _fail(E_PROOF, "outer Mach-O code signature is missing or unaligned")
    if dataoff < commands_end:
        _fail(E_PROOF, "outer Mach-O code signature overlaps the load-command table")
    expected_end = dataoff + datasize
    if (
        expected_end != len(data)
        or linkedit_fileoff + linkedit_filesize != len(data)
        or dataoff < linkedit_fileoff
    ):
        _fail(E_PROOF, "outer Mach-O code signature or __LINKEDIT is non-terminal")

    canonical = bytearray(data[:dataoff])
    canonical[linkedit_command + 32 : linkedit_command + 40] = b"\0" * 8
    canonical[linkedit_command + 48 : linkedit_command + 56] = b"\0" * 8
    canonical[signature_command + 8 : signature_command + 16] = b"\0" * 8
    return hashlib.sha256(_MACHO_CODE_DOMAIN + canonical).hexdigest()


def _trusted_runner_public_key() -> Path:
    """Resolve the package-owned public trust root, never receipt or caller input."""
    return Path(__file__).with_name("trust") / TRUSTED_RUNNER_PUBLIC_KEY_NAME


def _release_policy() -> Mapping[str, Any]:
    policy = _load_json(Path(__file__).with_name("trust") / RELEASE_POLICY_NAME)
    required = {
        "schema",
        "algorithm",
        "public_key",
        "public_key_spki_sha256",
        "bundle_id",
        "team_id",
        "designated_requirement",
        "hardened_runtime",
        "entitlements",
    }
    _expect_keys(policy, required=required, context="release policy")
    expected = {
        "schema": RELEASE_POLICY_SCHEMA,
        "algorithm": "rsa-pkcs1v15-sha256",
        "public_key": TRUSTED_RUNNER_PUBLIC_KEY_NAME,
        "public_key_spki_sha256": RELEASE_KEY_SPKI_SHA256,
        "bundle_id": PRODUCT_BUNDLE_ID,
        "team_id": "MW223P3NPX",
        "designated_requirement": (
            'identifier "io.vetcoders.vibecrafted" and anchor apple generic and '
            "certificate 1[field.1.2.840.113635.100.6.2.6] exists and "
            "certificate leaf[field.1.2.840.113635.100.6.1.13] exists and "
            'certificate leaf[subject.OU] = "MW223P3NPX"'
        ),
        "hardened_runtime": True,
        "entitlements": {},
    }
    if policy != expected:
        _fail(E_PROOF, "packaged release policy does not match the v1 trust contract")
    return policy


def _public_key_spki_sha256(public_key: Path) -> str:
    openssl = _release_openssl()
    result = subprocess.run(
        [openssl, "pkey", "-pubin", "-in", str(public_key), "-outform", "DER"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        _fail(E_PROOF, "packaged release public key is unreadable")
    return hashlib.sha256(result.stdout).hexdigest()


def _capture_proof_artifact(path: Path, *, context: str) -> _CapturedProofArtifact:
    absolute_path = Path(os.path.abspath(path))
    resolved = os.path.realpath(absolute_path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute_path, flags)
    except OSError as exc:
        _fail(E_MISSING, f"{context} is unreadable: {exc}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(E_MISSING, f"{context} is not a regular file")
        if before.st_nlink != 1:
            _fail(E_PATH, f"{context} must not be a hard-linked alias")
        if before.st_size > _MAX_SIGNED_PAYLOAD_BYTES:
            _fail(E_SIZE, f"{context} exceeds the 64 MiB signed-payload limit")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(_MAX_SIGNED_PAYLOAD_BYTES + 1)
        after = os.fstat(descriptor)
        path_after = os.lstat(absolute_path)
        resolved_after = os.path.realpath(absolute_path)
        if len(payload) > _MAX_SIGNED_PAYLOAD_BYTES:
            _fail(E_SIZE, f"{context} exceeds the 64 MiB signed-payload limit")
        if (
            (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_nlink,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            or resolved_after != resolved
            or (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            != (
                path_after.st_dev,
                path_after.st_ino,
                path_after.st_mode,
                path_after.st_nlink,
                path_after.st_size,
                path_after.st_mtime_ns,
                path_after.st_ctime_ns,
            )
        ):
            _fail(E_PROOF, f"{context} changed while it was captured")
        return _CapturedProofArtifact(
            path=absolute_path,
            raw=payload,
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
        )
    except OSError as exc:
        _fail(E_MISSING, f"{context} is unreadable: {exc}")
    finally:
        os.close(descriptor)


def _read_regular_file_once(path: Path, *, context: str) -> bytes:
    """Compatibility wrapper for callers that need only captured bytes."""
    return _capture_proof_artifact(path, context=context).raw


def _verify_release_signature(
    payload: Path, signature: Path
) -> tuple[_CapturedProofArtifact, _CapturedProofArtifact]:
    policy = _release_policy()
    public_key = _trusted_runner_public_key()
    if not public_key.is_file() or public_key.is_symlink():
        _fail(E_MISSING, "packaged release public key is missing")
    if _public_key_spki_sha256(public_key) != policy["public_key_spki_sha256"]:
        _fail(E_PROOF, "packaged release public key fingerprint is not policy-pinned")
    captured_payload = _capture_proof_artifact(
        payload, context="signed release payload"
    )
    captured_signature = _capture_proof_artifact(
        signature, context="detached release signature"
    )
    if captured_signature.size != _RELEASE_SIGNATURE_BYTES:
        _fail(E_PROOF, "detached release signature has an invalid size")
    openssl = _release_openssl()
    with tempfile.TemporaryDirectory(prefix="vibecrafted-signature-") as directory:
        signature_snapshot = Path(directory) / "release-output.json.sig"
        signature_snapshot.write_bytes(captured_signature.raw)
        signature_snapshot.chmod(0o400)
        result = subprocess.run(
            [
                openssl,
                "dgst",
                "-sha256",
                "-verify",
                str(public_key),
                "-signature",
                str(signature_snapshot),
            ],
            check=False,
            capture_output=True,
            input=captured_payload.raw,
        )
    if result.returncode != 0:
        _fail(E_PROOF, "detached release signature is invalid")
    return captured_payload, captured_signature


def _release_openssl() -> str:
    openssl = Path("/usr/bin/openssl")
    if not openssl.is_file() or openssl.is_symlink() or not os.access(openssl, os.X_OK):
        _fail(E_PROOF, "fixed system OpenSSL verifier is unavailable")
    return str(openssl)


def verify_trust_probe(
    challenge_path: str | Path, signature_path: str | Path
) -> dict[str, Any]:
    """Verify a narrowly scoped challenge through the installed production trust root."""
    challenge = Path(challenge_path)
    signature = Path(signature_path)
    captured_challenge, _ = _verify_release_signature(challenge, signature)
    payload = _load_json_bytes(captured_challenge.raw, source=challenge)
    _expect_keys(payload, required={"schema", "domain", "nonce"}, context="trust probe")
    if (
        payload["schema"] != TRUST_PROBE_SCHEMA
        or payload["domain"] != TRUST_PROBE_DOMAIN
    ):
        _fail(E_PROOF, "trust probe is outside the release trust domain")
    _expect_string(payload["nonce"], field="trust probe nonce")
    return payload


def _version_tuple(value: str) -> tuple[int, int]:
    match = _MACOS_RE.fullmatch(value)
    if match is None:
        _fail(E_PLATFORM, f"invalid measured macOS version: {value!r}")
    return int(match.group(1)), int(match.group(2))


def _observed_macho(path: Path, *, relative: str, kind: str) -> _MachOInfo | None:
    file_tool = shutil.which("file")
    if file_tool is None:
        _fail(E_DEPENDENCY, "file(1) is required to inspect executable payloads")
    probe = subprocess.run(
        [file_tool, "-b", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        _fail(E_DEPENDENCY, f"file(1) could not inspect {path}")
    if "Mach-O" not in probe.stdout:
        if kind == "dylib":
            _fail(E_DEPENDENCY, f"declared dylib is not Mach-O: {path}")
        return None

    lipo = _required_tool("lipo", failure_code=E_PLATFORM)
    architecture_output = _run_tool(
        [lipo, "-archs", str(path)],
        failure_code=E_PLATFORM,
        context=f"lipo could not inspect {relative}",
    )
    architectures = frozenset(architecture_output.split())
    if not architectures:
        _fail(E_PLATFORM, f"no Mach-O architecture slices found for {relative}")

    otool = _required_tool("otool", failure_code=E_DEPENDENCY)
    dependency_output = _run_tool(
        [otool, "-L", str(path)],
        failure_code=E_DEPENDENCY,
        context=f"otool -L could not inspect {relative}",
    )
    dependencies: list[str] = []
    for line in dependency_output.splitlines()[1:]:
        stripped = line.strip()
        if stripped:
            dependencies.append(stripped.split(" (", 1)[0])

    load_output = _run_tool(
        [otool, "-l", str(path)],
        failure_code=E_DEPENDENCY,
        context=f"otool -l could not inspect {relative}",
    )
    command = ""
    rpaths: list[str] = []
    minimum_versions: list[tuple[int, int]] = []
    for line in load_output.splitlines():
        stripped = line.strip()
        if stripped.startswith("cmd "):
            command = stripped.split(maxsplit=1)[1]
            continue
        if command == "LC_RPATH" and stripped.startswith("path "):
            match = re.match(r"path (.+?) \(offset [0-9]+\)$", stripped)
            if match is None:
                _fail(E_DEPENDENCY, f"unparseable LC_RPATH for {relative}: {stripped}")
            rpaths.append(match.group(1))
        elif (
            command == "LC_BUILD_VERSION"
            and stripped.startswith("minos ")
            or command == "LC_VERSION_MIN_MACOSX"
            and stripped.startswith("version ")
        ):
            minimum_versions.append(_version_tuple(stripped.split()[1]))

    absolute_rpaths = sorted(rpath for rpath in rpaths if rpath.startswith("/"))
    if absolute_rpaths:
        _fail(
            E_DEPENDENCY,
            f"absolute LC_RPATH is forbidden for {relative}: {absolute_rpaths[0]}",
        )
    if not minimum_versions:
        _fail(E_PLATFORM, f"Mach-O has no macOS deployment target: {relative}")
    return _MachOInfo(
        relative=relative,
        dependencies=tuple(dependencies),
        rpaths=tuple(rpaths),
        architectures=architectures,
        minimum_macos=tuple(minimum_versions),
    )


def _inside_payload(root: Path, candidate: Path, *, context: str) -> str:
    try:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        _fail(E_PATH, f"{context} cannot be resolved inside payload: {exc}")
    try:
        relative = candidate_resolved.relative_to(root_resolved)
    except ValueError:
        _fail(E_DEPENDENCY, f"{context} escapes payload: {candidate}")
    return relative.as_posix()


def _expanded_dyld_base(
    value: str,
    *,
    loader: Path,
    executable: Path,
    context: str,
) -> Path:
    if value == "@loader_path":
        return loader.parent
    if value.startswith("@loader_path/"):
        return loader.parent / value.removeprefix("@loader_path/")
    if value == "@executable_path":
        return executable.parent
    if value.startswith("@executable_path/"):
        return executable.parent / value.removeprefix("@executable_path/")
    if value.startswith("/"):
        _fail(E_DEPENDENCY, f"absolute LC_RPATH is forbidden for {context}: {value}")
    _fail(E_DEPENDENCY, f"unsupported LC_RPATH for {context}: {value}")


def _resolve_dependency(
    dependency: str,
    *,
    root: Path,
    loader: Path,
    executable: Path,
    rpaths: Sequence[str],
    declared_paths: set[str],
) -> str | None:
    if dependency.startswith(("/usr/lib/", "/System/Library/")):
        return None
    if dependency.startswith("@loader_path/"):
        candidate = loader.parent / dependency.removeprefix("@loader_path/")
        relative = _inside_payload(root, candidate, context=dependency)
    elif dependency.startswith("@executable_path/"):
        candidate = executable.parent / dependency.removeprefix("@executable_path/")
        relative = _inside_payload(root, candidate, context=dependency)
    elif dependency.startswith("@rpath/"):
        suffix = dependency.removeprefix("@rpath/")
        relative = ""
        for rpath in rpaths:
            base = _expanded_dyld_base(
                rpath,
                loader=loader,
                executable=executable,
                context=loader.relative_to(root).as_posix(),
            )
            candidate = base / suffix
            candidate_relative = _inside_payload(root, candidate, context=dependency)
            if candidate_relative in declared_paths and candidate.is_file():
                relative = candidate_relative
                break
        if not relative:
            _fail(
                E_DEPENDENCY,
                f"unresolved @rpath dependency for {loader.relative_to(root)}: {dependency}",
            )
    else:
        _fail(
            E_DEPENDENCY, f"external dylib for {loader.relative_to(root)}: {dependency}"
        )
    if relative not in declared_paths:
        _fail(
            E_DEPENDENCY,
            f"dependency is not manifest-bound for {loader.relative_to(root)}: {dependency}",
        )
    return relative


def _verify_macho_platform(
    machos: Mapping[str, _MachOInfo],
    *,
    architecture: str,
    minimum_macos: str,
) -> None:
    if not machos:
        _fail(E_PLATFORM, "payload contains no measurable Mach-O code")
    expected_architectures = frozenset({architecture})
    for relative, info in machos.items():
        if info.architectures != expected_architectures:
            observed = ",".join(sorted(info.architectures))
            _fail(
                E_PLATFORM,
                f"Mach-O architecture mismatch for {relative}: {observed} != {architecture}",
            )
    observed_floor = max(
        version for info in machos.values() for version in info.minimum_macos
    )
    expected_floor = _version_tuple(minimum_macos)
    if observed_floor != expected_floor:
        _fail(
            E_PLATFORM,
            "Mach-O minimum macOS mismatch: "
            f"{observed_floor[0]}.{observed_floor[1]} != {minimum_macos}",
        )


def _verify_macho_closure(
    root: Path,
    files: Mapping[str, Mapping[str, Any]],
    machos: Mapping[str, _MachOInfo],
    *,
    entrypoints: Mapping[str, str],
) -> None:
    declared_paths = set(files)
    visited: set[tuple[str, str, tuple[str, ...]]] = set()
    reached: set[str] = set()

    def visit(
        relative: str,
        *,
        executable_relative: str,
        inherited_rpaths: tuple[str, ...],
    ) -> None:
        info = machos[relative]
        effective_rpaths = tuple(dict.fromkeys((*info.rpaths, *inherited_rpaths)))
        key = (relative, executable_relative, effective_rpaths)
        if key in visited:
            return
        visited.add(key)
        reached.add(relative)
        loader = root / relative
        executable = root / executable_relative
        for dependency in info.dependencies:
            target = _resolve_dependency(
                dependency,
                root=root,
                loader=loader,
                executable=executable,
                rpaths=effective_rpaths,
                declared_paths=declared_paths,
            )
            if target is None:
                continue
            if target not in machos:
                _fail(E_DEPENDENCY, f"Mach-O dependency is not code: {target}")
            visit(
                target,
                executable_relative=executable_relative,
                inherited_rpaths=effective_rpaths,
            )

    executable_roots = sorted(
        relative for relative, entry in files.items() if entry["kind"] == "executable"
    )
    if not executable_roots:
        _fail(E_PLATFORM, "payload contains no declared executable roots")
    for relative in entrypoints.values():
        if relative not in executable_roots:
            _fail(E_ENTRYPOINT, f"entrypoint is not a declared executable: {relative}")
    for relative in executable_roots:
        if relative not in machos:
            _fail(E_PLATFORM, f"declared executable is not Mach-O code: {relative}")
        visit(relative, executable_relative=relative, inherited_rpaths=())

    unreachable = sorted(
        relative
        for relative, entry in files.items()
        if entry["kind"] == "dylib" and relative not in reached
    )
    if unreachable:
        _fail(E_DEPENDENCY, f"unreachable declared dylibs: {', '.join(unreachable)}")


def _reject_host_bound_paths(path: Path, *, relative: str, kind: str) -> None:
    """Reject payload bytes that silently bind a module to the build host."""
    if kind == "resource":
        return
    try:
        content = path.read_bytes()
    except OSError:
        return
    documentation_paths = _EMBEDDED_DOCUMENTATION_PATHS.get(relative, frozenset())
    for match in _BUILD_HOST_PATH_RE.finditer(content):
        host_path = match.group("path").decode("utf-8", errors="replace")
        if host_path in documentation_paths:
            continue
        _fail(
            E_PATH,
            f"host-bound absolute path in {relative}: {host_path}",
        )


def _validate_file_entry_shape(
    raw: Any, *, index: int
) -> tuple[str, str, str, str, int, list[str]]:
    context = f"files[{index}]"
    if not isinstance(raw, dict):
        _fail(E_SCHEMA, f"{context} must be an object")
    required = {"path", "sha256", "mode", "kind", "size", "dylibs"}
    _expect_keys(raw, required=required, context=context)
    relative = _relative_path(raw["path"], field=f"{context}.path")
    expected_hash = _expect_sha256(raw["sha256"], field=f"{context}.sha256")
    mode = _expect_string(raw["mode"], field=f"{context}.mode")
    if not _MODE_RE.fullmatch(mode):
        _fail(E_SCHEMA, f"{context}.mode must be a four-digit octal string")
    kind = _expect_string(raw["kind"], field=f"{context}.kind")
    if kind not in _FILE_KINDS:
        _fail(E_SCHEMA, f"{context}.kind is unsupported: {kind!r}")
    size = raw["size"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        _fail(E_SCHEMA, f"{context}.size must be a non-negative integer")
    dylibs = raw["dylibs"]
    if not isinstance(dylibs, list) or not all(
        isinstance(item, str) and item for item in dylibs
    ):
        _fail(E_SCHEMA, f"{context}.dylibs must be an array of strings")
    if len(dylibs) != len(set(dylibs)):
        _fail(E_SCHEMA, f"{context}.dylibs must not contain duplicates")
    return relative.as_posix(), expected_hash, mode, kind, size, dylibs


def _validate_files(
    root: Path,
    raw_files: Any,
    *,
    manifest_relative: Path,
    architecture: str,
    minimum_macos: str,
    exclusions: Sequence[Path] = (),
) -> _ValidatedFiles:
    if not isinstance(raw_files, list) or not raw_files:
        _fail(E_SCHEMA, "files must be a non-empty array")
    entries: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_files):
        relative_text, expected_hash, mode, kind, size, _ = _validate_file_entry_shape(
            raw, index=index
        )
        relative = Path(*PurePosixPath(relative_text).parts)
        if relative_text in entries:
            _fail(E_INVENTORY, f"duplicate manifest path: {relative_text}")
        entries[relative_text] = raw

        candidate = root / relative
        if not candidate.is_file() or candidate.is_symlink():
            _fail(E_MISSING, f"manifest-bound file is missing: {relative_text}")
        _reject_host_bound_paths(candidate, relative=relative_text, kind=kind)
        actual_mode = f"{stat.S_IMODE(candidate.stat().st_mode):04o}"
        if actual_mode != mode:
            _fail(
                E_MODE,
                f"mode mismatch for {relative_text}: expected {mode}, got {actual_mode}",
            )
        actual_size = candidate.stat().st_size
        if actual_size != size:
            _fail(
                E_SIZE,
                f"size mismatch for {relative_text}: expected {size}, got {actual_size}",
            )
        actual_hash = _sha256(candidate)
        if actual_hash != expected_hash:
            _fail(E_HASH, f"SHA-256 mismatch for {relative_text}")

    machos: dict[str, _MachOInfo] = {}
    for relative_text, raw in entries.items():
        declared_dylibs = list(raw["dylibs"])
        kind = str(raw["kind"])
        if kind not in {"executable", "dylib"}:
            if declared_dylibs:
                _fail(E_DEPENDENCY, f"non-code file declares dylibs: {relative_text}")
            continue
        observed = _observed_macho(
            root / relative_text,
            relative=relative_text,
            kind=kind,
        )
        if observed is None:
            continue
        machos[relative_text] = observed
        if sorted(observed.dependencies) != sorted(declared_dylibs):
            _fail(
                E_DEPENDENCY,
                f"declared dylibs do not match otool for {relative_text}",
            )

    _verify_macho_platform(
        machos,
        architecture=architecture,
        minimum_macos=minimum_macos,
    )

    declared_paths = set(entries)
    actual_paths = _payload_files(
        root,
        exclusions=(manifest_relative, *exclusions),
    )
    missing = sorted(declared_paths - actual_paths)
    extra = sorted(actual_paths - declared_paths)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if extra:
            details.append(f"undeclared={','.join(extra)}")
        _fail(E_INVENTORY, "payload inventory mismatch: " + " ".join(details))
    return _ValidatedFiles(entries=entries, machos=machos)


def _validate_outer_bundle_code(
    app: Path,
    raw: Any,
    *,
    architecture: str,
    minimum_macos: str,
    plist_path: Path,
) -> tuple[str, Mapping[str, Any], _MachOInfo]:
    context = "outer_bundle_code"
    if not isinstance(raw, dict):
        _fail(E_SCHEMA, f"{context} must be an object")
    required = {
        "identity",
        "path",
        "mode",
        "kind",
        "architecture",
        "minimum_macos",
        "dylibs",
        "code_identity",
        "code_sha256",
        "info_plist_sha256",
        "codesign_identifier",
    }
    _expect_keys(raw, required=required, context=context)
    if raw["identity"] != OUTER_BUNDLE_CODE_IDENTITY:
        _fail(E_SCHEMA, f"{context}.identity must be {OUTER_BUNDLE_CODE_IDENTITY}")
    relative = _relative_path(raw["path"], field=f"{context}.path").as_posix()
    expected_relative = f"Contents/MacOS/{PRODUCT_EXECUTABLE}"
    if relative != expected_relative or raw["kind"] != "executable":
        _fail(E_ENTRYPOINT, f"{context} must describe {expected_relative}")
    mode = _expect_string(raw["mode"], field=f"{context}.mode")
    if not _MODE_RE.fullmatch(mode):
        _fail(E_SCHEMA, f"{context}.mode must be a four-digit octal string")
    if raw["architecture"] != architecture or raw["minimum_macos"] != minimum_macos:
        _fail(E_PLATFORM, f"{context} platform does not match product")
    dylibs = raw["dylibs"]
    if not isinstance(dylibs, list) or not all(
        isinstance(item, str) and item for item in dylibs
    ):
        _fail(E_SCHEMA, f"{context}.dylibs must be an array of strings")
    executable = app / relative
    if not executable.is_file() or executable.is_symlink():
        _fail(E_MISSING, f"outer bundle executable is missing: {relative}")
    actual_mode = f"{stat.S_IMODE(executable.stat().st_mode):04o}"
    if actual_mode != mode:
        _fail(E_MODE, f"outer bundle executable mode mismatch: {relative}")
    observed = _observed_macho(executable, relative=relative, kind="executable")
    if observed is None:
        _fail(E_PLATFORM, "outer bundle executable is not Mach-O code")
    if sorted(observed.dependencies) != sorted(dylibs):
        _fail(E_DEPENDENCY, "outer bundle dylibs do not match measured load commands")
    if raw["code_identity"] != MACHO_CODE_IDENTITY:
        _fail(E_SCHEMA, f"{context}.code_identity must be {MACHO_CODE_IDENTITY}")
    _verify_macho_platform(
        {relative: observed},
        architecture=architecture,
        minimum_macos=minimum_macos,
    )
    expected_code_hash = _expect_sha256(
        raw["code_sha256"], field=f"{context}.code_sha256"
    )
    if _macho_code_sha256(executable) != expected_code_hash:
        _fail(E_HASH, "outer bundle Mach-O code identity does not match manifest")
    if _expect_sha256(
        raw["info_plist_sha256"], field=f"{context}.info_plist_sha256"
    ) != _sha256(plist_path):
        _fail(E_HASH, "outer bundle identity does not bind Info.plist")
    identifier = _expect_string(
        raw["codesign_identifier"], field=f"{context}.codesign_identifier"
    )
    if identifier != PRODUCT_BUNDLE_ID:
        _fail(E_BUNDLE, "outer bundle codesign Identifier is not canonical")
    return relative, raw, observed


def _verify_outer_bundle_signature(app: Path, *, expected_identifier: str) -> None:
    codesign = _required_tool("codesign", failure_code=E_PROOF)
    _run_tool(
        [codesign, "--verify", "--deep", "--strict", "--verbose=2", str(app)],
        failure_code=E_PROOF,
        context="outer bundle strict code signature is invalid",
    )
    if _codesign_identifier(app) != expected_identifier:
        _fail(E_PROOF, "outer bundle signature Identifier does not match manifest")


def _validate_entrypoints(
    root: Path,
    raw_entrypoints: Any,
    files: Mapping[str, Mapping[str, Any]],
    *,
    required_names: frozenset[str],
) -> dict[str, str]:
    if not isinstance(raw_entrypoints, dict):
        _fail(E_SCHEMA, "entrypoints must be an object")
    _expect_keys(
        raw_entrypoints,
        required=required_names,
        context="entrypoints",
    )
    entrypoints: dict[str, str] = {}
    for name in sorted(required_names):
        relative = _relative_path(raw_entrypoints[name], field=f"entrypoints.{name}")
        relative_text = relative.as_posix()
        entry = files.get(relative_text)
        if entry is None:
            _fail(E_ENTRYPOINT, f"entrypoint is not manifest-bound: {relative_text}")
        if entry["kind"] != "executable":
            _fail(E_ENTRYPOINT, f"entrypoint is not executable-kind: {relative_text}")
        if not os.access(root / relative, os.X_OK):
            _fail(E_ENTRYPOINT, f"entrypoint lacks executable mode: {relative_text}")
        entrypoints[name] = relative_text
    return entrypoints


def _canonical_launch_contract() -> dict[str, Any]:
    return {
        "schema": LAUNCH_SCHEMA,
        "program": _LAUNCH_TERMINAL,
        "argv": [
            "--config-file",
            _LAUNCH_CONFIG,
            "-e",
            _LAUNCH_PRIMARY_SHELL,
            _LAUNCH_SHELL,
            "operator",
        ],
        "config_path": _LAUNCH_CONFIG,
        "primary_shell": {
            "program": _LAUNCH_PRIMARY_SHELL,
            "argv": [_LAUNCH_SHELL, "operator"],
        },
        "shell": {"program": _LAUNCH_SHELL, "argv": ["operator"]},
        "environment": {
            "resolver_inputs": ["VIBECRAFTED_RUNTIME_HOME", "XDG_DATA_HOME"],
            "inherit_exact": list(_LAUNCH_INHERITED_ENV),
            "inject_literal": {"PATH": _LAUNCH_SYSTEM_PATH},
            "inject_bundle_paths": {
                "VIBECRAFTED_APP_ROOT": ".",
                "VIBECRAFTED_VC_FRAME_BIN": _LAUNCH_FRAME,
            },
            "resolve_writable": {
                "VIBECRAFTED_RUNTIME_HOME": {
                    "source_order": [
                        "env:VIBECRAFTED_RUNTIME_HOME",
                        "env:XDG_DATA_HOME+rel:vibecrafted",
                        "env:HOME+rel:.local/share/vibecrafted",
                    ],
                    "must_be_absolute": True,
                    "must_be_writable_or_creatable": True,
                    "must_not_descend_from": "$APP_BUNDLE",
                    "publish_to_env": "VIBECRAFTED_RUNTIME_HOME",
                }
            },
        },
    }


def _validate_launch_contract(
    raw: Any,
    *,
    files: Mapping[str, Mapping[str, Any]],
    entrypoints: Mapping[str, str],
) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        _fail(E_SCHEMA, "launch_contract must be an object")
    _expect_keys(
        raw,
        required={
            "schema",
            "program",
            "argv",
            "config_path",
            "primary_shell",
            "shell",
            "environment",
        },
        context="launch_contract",
    )
    if raw["schema"] != LAUNCH_SCHEMA:
        _fail(E_SCHEMA, f"launch_contract.schema must be {LAUNCH_SCHEMA}")
    shell = raw["shell"]
    if not isinstance(shell, dict):
        _fail(E_SCHEMA, "launch_contract.shell must be an object")
    _expect_keys(shell, required={"program", "argv"}, context="launch_contract.shell")
    primary_shell = raw["primary_shell"]
    if not isinstance(primary_shell, dict):
        _fail(E_SCHEMA, "launch_contract.primary_shell must be an object")
    _expect_keys(
        primary_shell,
        required={"program", "argv"},
        context="launch_contract.primary_shell",
    )
    canonical = _canonical_launch_contract()
    if (
        raw["program"] != _LAUNCH_TERMINAL
        or raw["config_path"] != _LAUNCH_CONFIG
        or raw["argv"] != canonical["argv"]
        or primary_shell
        != {"program": _LAUNCH_PRIMARY_SHELL, "argv": [_LAUNCH_SHELL, "operator"]}
        or shell != {"program": _LAUNCH_SHELL, "argv": ["operator"]}
    ):
        _fail(
            E_ENTRYPOINT,
            "launch_contract does not encode the canonical Start here entry",
        )
    environment = raw["environment"]
    if not isinstance(environment, dict):
        _fail(E_SCHEMA, "launch_contract.environment must be an object")
    _expect_keys(
        environment,
        required={
            "resolver_inputs",
            "inherit_exact",
            "inject_literal",
            "inject_bundle_paths",
            "resolve_writable",
        },
        context="launch_contract.environment",
    )
    expected_environment = canonical["environment"]
    if environment != expected_environment:
        _fail(E_SCHEMA, "launch_contract.environment is not the closed v1 environment")
    required_files = {
        _LAUNCH_TERMINAL: "executable",
        _LAUNCH_FRAME: "executable",
        _LAUNCH_CONFIG: "config",
        _LAUNCH_PRIMARY_SHELL: "resource",
        _LAUNCH_SHELL: "executable",
    }
    for relative, kind in required_files.items():
        entry = files.get(relative)
        if entry is None or entry.get("kind") != kind:
            _fail(
                E_ENTRYPOINT,
                f"launch_contract path is not exact inventory {kind}: {relative}",
            )
    primary_entry = files[_LAUNCH_PRIMARY_SHELL]
    if int(str(primary_entry["mode"]), 8) & 0o111 == 0:
        _fail(E_ENTRYPOINT, "primary-shell launcher is not executable")
    if (
        entrypoints["terminal"] != _LAUNCH_TERMINAL
        or entrypoints["frame"] != _LAUNCH_FRAME
    ):
        _fail(E_ENTRYPOINT, "launch_contract disagrees with product entrypoints")
    return raw


def build_launch_environment(
    app_path: str | Path,
    *,
    host_environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Verify the product and build its closed child environment."""
    raw_app = Path(app_path)
    if raw_app.is_symlink():
        _fail(E_PATH, "app bundle root must not be a symlink")
    try:
        app = raw_app.resolve(strict=True)
    except (OSError, ValueError) as exc:
        _fail(E_PATH, f"app bundle path cannot be resolved: {exc}")
    payload = verify_app(app)
    host = dict(os.environ if host_environment is None else host_environment)
    explicit = host.get("VIBECRAFTED_RUNTIME_HOME")
    xdg = host.get("XDG_DATA_HOME")
    home = host.get("HOME")
    if explicit:
        runtime_home = Path(explicit)
    elif xdg:
        runtime_home = Path(xdg) / "vibecrafted"
    elif home:
        runtime_home = Path(home) / ".local/share/vibecrafted"
    else:
        _fail(E_PATH, "launch environment cannot resolve VIBECRAFTED_RUNTIME_HOME")
    if not runtime_home.is_absolute():
        _fail(E_PATH, "VIBECRAFTED_RUNTIME_HOME must resolve to an absolute path")
    try:
        runtime_home = runtime_home.resolve(strict=False)
    except (OSError, ValueError) as exc:
        _fail(E_PATH, f"VIBECRAFTED_RUNTIME_HOME cannot be resolved: {exc}")
    if runtime_home == app or app in runtime_home.parents:
        _fail(E_PATH, "VIBECRAFTED_RUNTIME_HOME must not descend from the app bundle")
    try:
        runtime_home.mkdir(parents=True, exist_ok=True)
        if not runtime_home.is_dir() or runtime_home.is_symlink():
            _fail(E_PATH, "VIBECRAFTED_RUNTIME_HOME must be an exact directory")
        descriptor, probe = tempfile.mkstemp(
            prefix=".vibecrafted-write-probe-", dir=runtime_home
        )
        os.close(descriptor)
        Path(probe).unlink()
    except ProductContractError:
        raise
    except (OSError, ValueError) as exc:
        _fail(E_PATH, f"VIBECRAFTED_RUNTIME_HOME cannot be created or written: {exc}")
    child = {name: host[name] for name in _LAUNCH_INHERITED_ENV if host.get(name)}
    child.update(
        {
            "PATH": _LAUNCH_SYSTEM_PATH,
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_APP_ROOT": str(app),
            "VIBECRAFTED_VC_FRAME_BIN": str(app / _LAUNCH_FRAME),
        }
    )
    # Accessing the field after verification keeps this builder bound to the manifest.
    _validate_launch_contract(
        payload["launch_contract"],
        files={item["path"]: item for item in payload["files"]},
        entrypoints=payload["entrypoints"],
    )
    return child


def _validate_module_manifest(
    payload: Mapping[str, Any], *, context: str
) -> _ModuleManifest:
    required = {
        "schema",
        "module",
        "version",
        "git_sha",
        "dirty",
        "architecture",
        "minimum_macos",
        "files",
        "entrypoints",
    }
    _expect_keys(payload, required=required, context=context)
    if payload["schema"] != MODULE_SCHEMA:
        _fail(E_SCHEMA, f"module schema must be {MODULE_SCHEMA}")
    module_name = _expect_string(payload["module"], field="module")
    if module_name not in SUPPORTED_MODULES:
        _fail(E_SCHEMA, f"unsupported module: {module_name!r}")
    version = _expect_string(payload["version"], field="version")
    git_sha = _expect_git_sha(payload["git_sha"], field="git_sha")
    if not isinstance(payload["dirty"], bool):
        _fail(E_SCHEMA, "dirty must be boolean")
    architecture, minimum_macos = _validate_platform(payload, context="module")
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files:
        _fail(E_SCHEMA, "files must be a non-empty array")
    files: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_files):
        relative, *_ = _validate_file_entry_shape(raw, index=index)
        if relative in files:
            _fail(E_INVENTORY, f"duplicate manifest path: {relative}")
        files[relative] = raw
    required_entrypoints = frozenset(
        {"terminal"} if module_name == "vc-terminal" else {"frame"}
    )
    raw_entrypoints = payload["entrypoints"]
    if not isinstance(raw_entrypoints, dict):
        _fail(E_SCHEMA, "entrypoints must be an object")
    _expect_keys(
        raw_entrypoints,
        required=required_entrypoints,
        context="entrypoints",
    )
    entrypoints: dict[str, str] = {}
    for name in required_entrypoints:
        relative = _relative_path(
            raw_entrypoints[name], field=f"entrypoints.{name}"
        ).as_posix()
        entry = files.get(relative)
        if entry is None or entry["kind"] != "executable":
            _fail(E_ENTRYPOINT, f"module entrypoint is not executable: {relative}")
        entrypoints[name] = relative
    return _ModuleManifest(
        module=module_name,
        version=version,
        git_sha=git_sha,
        dirty=payload["dirty"],
        architecture=architecture,
        minimum_macos=minimum_macos,
        files=files,
        entrypoints=entrypoints,
    )


def verify_module(root: str | Path, *, require_clean: bool = False) -> dict[str, Any]:
    """Verify one explicit unsigned vc-terminal or vc-frame module directory."""
    module_root = Path(root)
    if not module_root.is_dir() or module_root.is_symlink():
        _fail(E_MISSING, f"module root is not a directory: {module_root}")
    manifest_relative = Path("module-manifest.json")
    payload = _load_json(module_root / manifest_relative)
    manifest = _validate_module_manifest(payload, context="module manifest")
    if require_clean and manifest.dirty:
        _fail(E_PROOF, "release policy rejects a dirty module receipt")
    validated = _validate_files(
        module_root,
        payload["files"],
        manifest_relative=manifest_relative,
        architecture=manifest.architecture,
        minimum_macos=manifest.minimum_macos,
    )
    entrypoints = _validate_entrypoints(
        module_root,
        payload["entrypoints"],
        validated.entries,
        required_names=frozenset(manifest.entrypoints),
    )
    _verify_macho_closure(
        module_root,
        validated.entries,
        validated.machos,
        entrypoints=entrypoints,
    )
    return payload


def _validate_product_modules(raw_modules: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw_modules, list) or len(raw_modules) != 2:
        _fail(E_SCHEMA, "product modules must contain terminal and frame receipts")
    modules: dict[str, Mapping[str, Any]] = {}
    required = {
        "module",
        "manifest_path",
        "manifest_sha256",
        "assembly_receipt_path",
        "assembly_receipt_sha256",
        "git_sha",
    }
    for index, item in enumerate(raw_modules):
        if not isinstance(item, dict):
            _fail(E_SCHEMA, f"modules[{index}] must be an object")
        _expect_keys(item, required=required, context=f"modules[{index}]")
        name = _expect_string(item["module"], field=f"modules[{index}].module")
        if name not in SUPPORTED_MODULES or name in modules:
            _fail(E_SCHEMA, f"invalid or duplicate product module: {name!r}")
        _relative_path(item["manifest_path"], field=f"modules[{index}].manifest_path")
        _expect_sha256(
            item["manifest_sha256"], field=f"modules[{index}].manifest_sha256"
        )
        _relative_path(
            item["assembly_receipt_path"],
            field=f"modules[{index}].assembly_receipt_path",
        )
        _expect_sha256(
            item["assembly_receipt_sha256"],
            field=f"modules[{index}].assembly_receipt_sha256",
        )
        _expect_git_sha(item["git_sha"], field="git_sha")
        modules[name] = item
    if set(modules) != SUPPORTED_MODULES:
        _fail(E_SCHEMA, "product must bind vc-terminal and vc-frame")
    return modules


def _validate_assembly_receipt(
    payload: Mapping[str, Any], *, module: str, manifest_sha256: str
) -> list[Mapping[str, Any]]:
    required = {"schema", "module", "module_manifest_sha256", "files"}
    _expect_keys(payload, required=required, context=f"{module} assembly receipt")
    if payload["schema"] != ASSEMBLY_SCHEMA:
        _fail(E_SCHEMA, f"assembly schema must be {ASSEMBLY_SCHEMA}")
    if payload["module"] != module:
        _fail(E_PROOF, f"assembly receipt identity mismatch: {module}")
    if (
        _expect_sha256(
            payload["module_manifest_sha256"],
            field=f"{module}.assembly.module_manifest_sha256",
        )
        != manifest_sha256
    ):
        _fail(E_PROOF, f"assembly receipt does not bind module manifest: {module}")
    raw_files = payload["files"]
    if not isinstance(raw_files, list) or not raw_files:
        _fail(E_SCHEMA, f"{module} assembly files must be a non-empty array")
    files: list[Mapping[str, Any]] = []
    module_paths: set[str] = set()
    product_paths: set[str] = set()
    for index, mapping in enumerate(raw_files):
        context = f"{module}.assembly.files[{index}]"
        if not isinstance(mapping, dict):
            _fail(E_SCHEMA, f"{context} must be an object")
        _expect_keys(
            mapping,
            required={
                "module_path",
                "product_path",
                "unsigned_sha256",
                "product_sha256",
                "transformation",
            },
            context=context,
        )
        module_path = _relative_path(
            mapping["module_path"], field=f"{context}.module_path"
        ).as_posix()
        product_path = _relative_path(
            mapping["product_path"], field=f"{context}.product_path"
        ).as_posix()
        _expect_sha256(mapping["unsigned_sha256"], field=f"{context}.unsigned_sha256")
        _expect_sha256(mapping["product_sha256"], field=f"{context}.product_sha256")
        transformation = _expect_string(
            mapping["transformation"], field=f"{context}.transformation"
        )
        if transformation not in {"identity", "codesign"}:
            _fail(E_SCHEMA, f"{context}.transformation is unsupported")
        if module_path in module_paths or product_path in product_paths:
            _fail(E_INVENTORY, f"duplicate assembly file mapping in {module}")
        module_paths.add(module_path)
        product_paths.add(product_path)
        files.append(mapping)
    return files


def _verify_product_module_receipts(
    app: Path,
    modules: Mapping[str, Mapping[str, Any]],
    product_files: Mapping[str, Mapping[str, Any]],
    product_entrypoints: Mapping[str, str],
    *,
    architecture: str,
    minimum_macos: str,
    require_clean: bool,
) -> None:
    claimed_product_paths: set[str] = set()
    for name, binding in modules.items():
        manifest_path = _relative_path(
            binding["manifest_path"], field=f"modules.{name}.manifest_path"
        ).as_posix()
        manifest_entry = product_files.get(manifest_path)
        if manifest_entry is None or manifest_entry["kind"] not in {
            "config",
            "resource",
        }:
            _fail(
                E_PROOF,
                f"module receipt is not product-manifest-bound: {manifest_path}",
            )
        receipt_path = app / manifest_path
        expected_hash = _expect_sha256(
            binding["manifest_sha256"], field=f"modules.{name}.manifest_sha256"
        )
        captured_receipt = _capture_proof_artifact(
            receipt_path, context=f"embedded {name} module manifest"
        )
        if captured_receipt.sha256 != expected_hash:
            _fail(E_PROOF, f"embedded module receipt hash mismatch: {name}")
        receipt_payload = _load_json_bytes(captured_receipt.raw, source=receipt_path)
        receipt = _validate_module_manifest(
            receipt_payload, context=f"embedded {name} module manifest"
        )
        if receipt.module != name:
            _fail(E_PROOF, f"embedded module receipt identity mismatch: {name}")
        if receipt.git_sha != binding["git_sha"]:
            _fail(E_PROOF, f"embedded module Git SHA mismatch: {name}")
        if (
            receipt.architecture != architecture
            or receipt.minimum_macos != minimum_macos
        ):
            _fail(E_PLATFORM, f"embedded module platform mismatch: {name}")
        if require_clean and receipt.dirty:
            _fail(E_PROOF, f"release policy rejects dirty embedded module: {name}")

        assembly_path = _relative_path(
            binding["assembly_receipt_path"],
            field=f"modules.{name}.assembly_receipt_path",
        ).as_posix()
        assembly_entry = product_files.get(assembly_path)
        if assembly_entry is None or assembly_entry["kind"] not in {
            "config",
            "resource",
        }:
            _fail(
                E_PROOF,
                f"assembly receipt is not product-manifest-bound: {assembly_path}",
            )
        assembly_receipt_path = app / assembly_path
        expected_assembly_hash = _expect_sha256(
            binding["assembly_receipt_sha256"],
            field=f"modules.{name}.assembly_receipt_sha256",
        )
        captured_assembly = _capture_proof_artifact(
            assembly_receipt_path,
            context=f"embedded {name} assembly receipt",
        )
        if captured_assembly.sha256 != expected_assembly_hash:
            _fail(E_PROOF, f"embedded assembly receipt hash mismatch: {name}")
        assembly_payload = _load_json_bytes(
            captured_assembly.raw, source=assembly_receipt_path
        )
        mappings = _validate_assembly_receipt(
            assembly_payload,
            module=name,
            manifest_sha256=expected_hash,
        )
        mapped_module_paths: set[str] = set()
        entrypoint_name = "terminal" if name == "vc-terminal" else "frame"
        mapped_entrypoint = ""
        for mapping in mappings:
            module_path = str(mapping["module_path"])
            product_path = str(mapping["product_path"])
            module_entry = receipt.files.get(module_path)
            product_entry = product_files.get(product_path)
            if module_entry is None or product_entry is None:
                _fail(E_PROOF, f"unbound copied module file: {name}:{module_path}")
            if product_path in claimed_product_paths:
                _fail(
                    E_INVENTORY,
                    f"product file claimed by multiple modules: {product_path}",
                )
            claimed_product_paths.add(product_path)
            mapped_module_paths.add(module_path)
            if mapping["unsigned_sha256"] != module_entry["sha256"]:
                _fail(E_PROOF, f"unsigned module hash mismatch: {name}:{module_path}")
            if mapping["product_sha256"] != product_entry["sha256"]:
                _fail(E_PROOF, f"signed product hash mismatch: {name}:{module_path}")
            transformation = mapping["transformation"]
            if transformation == "identity":
                if module_entry["kind"] in {"executable", "dylib"}:
                    _fail(
                        E_PROOF,
                        f"Mach-O module file requires codesign transform: {name}:{module_path}",
                    )
                for field in ("sha256", "mode", "kind", "size", "dylibs"):
                    if module_entry[field] != product_entry[field]:
                        _fail(
                            E_PROOF,
                            f"identity transform changed {field}: {name}:{module_path}",
                        )
            else:
                if module_entry["kind"] not in {"executable", "dylib"}:
                    _fail(
                        E_PROOF,
                        f"codesign transform applied to non-code file: {name}:{module_path}",
                    )
                for field in ("mode", "kind", "dylibs"):
                    if module_entry[field] != product_entry[field]:
                        _fail(
                            E_PROOF,
                            f"codesign transform changed {field}: {name}:{module_path}",
                        )
                if module_entry["sha256"] == product_entry["sha256"]:
                    _fail(
                        E_PROOF,
                        f"codesign transform did not change bytes: {name}:{module_path}",
                    )
                _verify_assembler_signed_macho(
                    app / product_path,
                    relative=product_path,
                )
            if module_path == receipt.entrypoints[entrypoint_name]:
                mapped_entrypoint = product_path
        if mapped_module_paths != set(receipt.files):
            _fail(E_PROOF, f"module receipt inventory is not fully copied: {name}")
        if mapped_entrypoint != product_entrypoints[entrypoint_name]:
            _fail(E_ENTRYPOINT, f"product entrypoint is not bound to {name} receipt")


def verify_app(app_path: str | Path, *, require_clean: bool = False) -> dict[str, Any]:
    """Verify one explicit assembled Vibecrafted.app and its product manifest."""
    app = Path(app_path)
    if not app.is_dir() or app.is_symlink() or app.suffix != ".app":
        _fail(E_MISSING, f"app path is not an explicit .app directory: {app}")
    manifest_relative = Path("Contents/Resources/product-manifest.json")
    payload = _load_json(app / manifest_relative)
    required = {
        "schema",
        "product",
        "bundle_id",
        "bundle_executable",
        "version",
        "build",
        "git_sha",
        "dirty",
        "architecture",
        "minimum_macos",
        "modules",
        "outer_bundle_code",
        "files",
        "entrypoints",
        "launch_contract",
    }
    _expect_keys(payload, required=required, context="product manifest")
    if payload["schema"] != PRODUCT_SCHEMA:
        _fail(E_SCHEMA, f"product schema must be {PRODUCT_SCHEMA}")
    if payload["product"] != PRODUCT_NAME:
        _fail(E_BUNDLE, f"product must be {PRODUCT_NAME}")
    if payload["bundle_id"] != PRODUCT_BUNDLE_ID:
        _fail(E_BUNDLE, f"bundle_id must be {PRODUCT_BUNDLE_ID}")
    if payload["bundle_executable"] != PRODUCT_EXECUTABLE:
        _fail(E_BUNDLE, f"bundle_executable must be {PRODUCT_EXECUTABLE}")
    version = _expect_string(payload["version"], field="version")
    build = _expect_string(payload["build"], field="build")
    _expect_git_sha(payload["git_sha"], field="git_sha")
    if not isinstance(payload["dirty"], bool):
        _fail(E_SCHEMA, "dirty must be boolean")
    if require_clean and payload["dirty"]:
        _fail(E_PROOF, "release policy rejects a dirty product receipt")
    architecture, minimum_macos = _validate_platform(payload, context="product")
    modules = _validate_product_modules(payload["modules"])
    plist_path = app / "Contents/Info.plist"
    outer_relative, outer_entry, outer_macho = _validate_outer_bundle_code(
        app,
        payload["outer_bundle_code"],
        architecture=architecture,
        minimum_macos=minimum_macos,
        plist_path=plist_path,
    )
    validated = _validate_files(
        app,
        payload["files"],
        manifest_relative=manifest_relative,
        architecture=architecture,
        minimum_macos=minimum_macos,
        exclusions=(
            Path("Contents/_CodeSignature"),
            Path("Contents/CodeResources"),
            Path("Contents/Helpers/vc-terminal.app/Contents/_CodeSignature"),
            Path(outer_relative),
        ),
    )
    validated = _ValidatedFiles(
        entries={**validated.entries, outer_relative: outer_entry},
        machos={**validated.machos, outer_relative: outer_macho},
    )
    entrypoints = _validate_entrypoints(
        app,
        payload["entrypoints"],
        validated.entries,
        required_names=_APP_ENTRYPOINTS,
    )
    if entrypoints["app"] != f"Contents/MacOS/{PRODUCT_EXECUTABLE}":
        _fail(E_ENTRYPOINT, "app entrypoint must be Contents/MacOS/Vibecrafted")
    _validate_launch_contract(
        payload["launch_contract"],
        files=validated.entries,
        entrypoints=entrypoints,
    )

    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        _fail(E_BUNDLE, f"invalid Info.plist: {exc}")
    if not isinstance(plist, dict):
        _fail(E_BUNDLE, "Info.plist top level must be a dictionary")
    if plist.get("CFBundleIdentifier") != PRODUCT_BUNDLE_ID:
        _fail(E_BUNDLE, f"Info.plist bundle id must be {PRODUCT_BUNDLE_ID}")
    if plist.get("CFBundleExecutable") != PRODUCT_EXECUTABLE:
        _fail(E_BUNDLE, f"Info.plist executable must be {PRODUCT_EXECUTABLE}")
    if plist.get("CFBundleIconFile") != PRODUCT_ICON_FILE:
        _fail(E_BUNDLE, f"Info.plist icon must be {PRODUCT_ICON_FILE}")
    icon_path = app / "Contents/Resources" / PRODUCT_ICON_FILE
    if not icon_path.is_file() or icon_path.is_symlink():
        _fail(E_BUNDLE, f"application icon is missing: {PRODUCT_ICON_FILE}")
    if f"Contents/Resources/{PRODUCT_ICON_FILE}" not in validated.entries:
        _fail(E_INVENTORY, "application icon is absent from the signed inventory")
    unexpected_icons = sorted(
        path.name
        for path in (app / "Contents/Resources").glob("*.icns")
        if path.name != PRODUCT_ICON_FILE
    )
    if unexpected_icons:
        _fail(
            E_BUNDLE,
            "application contains non-canonical icon resources: "
            + ", ".join(unexpected_icons),
        )
    if plist.get("CFBundleShortVersionString") != version:
        _fail(E_BUNDLE, "Info.plist marketing version does not match product manifest")
    if plist.get("CFBundleVersion") != build:
        _fail(E_BUNDLE, "Info.plist build version does not match product manifest")
    nested_apps = sorted(
        path.relative_to(app).as_posix() for path in app.rglob("*.app") if path.is_dir()
    )
    if nested_apps != [_TERMINAL_HELPER_APP]:
        _fail(E_BUNDLE, f"nested customer app bundles are forbidden: {nested_apps}")
    terminal_helper = app / _TERMINAL_HELPER_APP
    helper_plist_path = terminal_helper / "Contents/Info.plist"
    try:
        with helper_plist_path.open("rb") as handle:
            helper_plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException) as exc:
        _fail(E_BUNDLE, f"terminal helper Info.plist is invalid: {exc}")
    if helper_plist.get("CFBundleIdentifier") != _TERMINAL_HELPER_BUNDLE_ID:
        _fail(E_BUNDLE, "terminal helper bundle identifier is not canonical")
    if helper_plist.get("CFBundleExecutable") != "alacritty":
        _fail(E_BUNDLE, "terminal helper executable is not canonical")
    if helper_plist.get("CFBundleIconFile") != _TERMINAL_HELPER_ICON:
        _fail(E_BUNDLE, "terminal helper icon is not canonical")
    helper_icon_relative = (
        f"{_TERMINAL_HELPER_APP}/Contents/Resources/{_TERMINAL_HELPER_ICON}"
    )
    if helper_icon_relative not in validated.entries:
        _fail(E_INVENTORY, "terminal helper icon is absent from signed inventory")
    _verify_assembler_signed_macho(terminal_helper, relative=_TERMINAL_HELPER_APP)
    if _codesign_identifier(terminal_helper) != _TERMINAL_HELPER_BUNDLE_ID:
        _fail(E_PROOF, "terminal helper signature Identifier is not canonical")
    _verify_product_module_receipts(
        app,
        modules,
        validated.entries,
        entrypoints,
        architecture=architecture,
        minimum_macos=minimum_macos,
        require_clean=require_clean,
    )
    _verify_macho_closure(
        app,
        validated.entries,
        validated.machos,
        entrypoints=entrypoints,
    )
    _verify_outer_bundle_signature(
        app,
        expected_identifier=str(payload["outer_bundle_code"]["codesign_identifier"]),
    )
    return payload


def _codesign_release_evidence(app: Path) -> dict[str, Any]:
    codesign = _required_tool("codesign", failure_code=E_PROOF)
    display = subprocess.run(
        [codesign, "--display", "--verbose=4", str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    if display.returncode != 0:
        _fail(E_PROOF, "final app signature metadata is unreadable")
    metadata = f"{display.stdout}\n{display.stderr}"

    def field(pattern: str, name: str) -> str:
        match = re.search(pattern, metadata, flags=re.MULTILINE | re.IGNORECASE)
        if match is None:
            _fail(E_PROOF, f"final app signature has no {name}")
        return match.group(1).strip()

    requirement_result = subprocess.run(
        [codesign, "--display", "--requirements", "-", str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    if requirement_result.returncode != 0:
        _fail(E_PROOF, "final app designated requirement is unreadable")
    requirement_text = f"{requirement_result.stdout}\n{requirement_result.stderr}"
    requirement_match = re.search(
        r"designated\s*=>\s*(.+)$", requirement_text, re.MULTILINE
    )
    if requirement_match is None:
        _fail(E_PROOF, "final app has no designated requirement")
    policy_requirement = _release_policy()["designated_requirement"]
    requirement_test = subprocess.run(
        [
            codesign,
            "--verify",
            "--strict",
            "--test-requirement",
            f"={policy_requirement}",
            str(app),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if requirement_test.returncode != 0:
        _fail(E_PROOF, "final app does not satisfy the designated requirement policy")
    entitlements_result = subprocess.run(
        [codesign, "--display", "--entitlements", ":-", str(app)],
        check=False,
        capture_output=True,
    )
    if entitlements_result.returncode != 0:
        _fail(E_PROOF, "final app entitlements are unreadable")
    entitlement_bytes = entitlements_result.stdout.strip()
    if entitlement_bytes:
        try:
            entitlements = plistlib.loads(entitlement_bytes)
        except (plistlib.InvalidFileException, ExpatError, ValueError) as exc:
            _fail(E_PROOF, f"final app entitlements are malformed: {exc}")
    else:
        # codesign exits successfully with an empty stdout when the signature
        # carries no entitlement blob. That is the required release policy,
        # distinct from an unreadable query (non-zero exit above).
        entitlements = {}
    if not isinstance(entitlements, dict):
        _fail(E_PROOF, "final app entitlements are not a dictionary")
    return {
        "cdhash": field(r"^CDHash=([0-9a-f]+)$", "CDHash"),
        "team_id": field(r"^TeamIdentifier=(.+)$", "TeamIdentifier"),
        "designated_requirement": policy_requirement,
        "hardened_runtime": re.search(
            r"^CodeDirectory .*flags=.*\(runtime\)", metadata, re.MULTILINE
        )
        is not None,
        "entitlements": entitlements,
    }


def _release_relative_path(root: Path, value: Any, *, field: str) -> Path:
    relative = _relative_path(value, field=field)
    candidate = root / relative
    if _inside_payload(root, candidate, context=field) != relative.as_posix():
        _fail(E_PATH, f"{field} does not resolve to its signed relative path")
    if candidate.is_symlink():
        _fail(E_PATH, f"{field} must not be a symlink")
    return candidate


def _verify_release_signer_policy(
    app: Path, outer: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = _release_policy()
    observed_signer = _codesign_release_evidence(app)
    expected_signer = {
        "team_id": policy["team_id"],
        "designated_requirement": policy["designated_requirement"],
        "hardened_runtime": policy["hardened_runtime"],
        "entitlements": policy["entitlements"],
    }
    if outer["signer_policy"] != expected_signer:
        _fail(E_PROOF, "release output signer policy violates packaged policy")
    if observed_signer != {"cdhash": outer["cdhash"], **expected_signer}:
        _fail(E_PROOF, "final app signer evidence does not match release output")
    return observed_signer, expected_signer


@contextmanager
def _captured_release_dmg(dmg: Path, *, expected_size: int, expected_sha256: str):
    """Capture one immutable, verified DMG snapshot from one no-follow descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(dmg, flags)
    except OSError as exc:
        _fail(E_MISSING, f"release DMG cannot be opened safely: {exc}")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail(E_PATH, "release DMG must be a regular file")
        if metadata.st_size != expected_size:
            _fail(E_SIZE, "release output DMG size mismatch")
        with tempfile.TemporaryDirectory(
            prefix="vibecrafted-release-dmg-"
        ) as directory:
            snapshot = Path(directory) / "Vibecrafted.dmg"
            digest = hashlib.sha256()
            copied = 0
            with (
                os.fdopen(descriptor, "rb", closefd=False) as source,
                snapshot.open("xb") as target,
            ):
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > expected_size:
                        _fail(E_SIZE, "release DMG changed while being captured")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if copied != expected_size:
                _fail(E_SIZE, "release DMG changed while being captured")
            if digest.hexdigest() != expected_sha256:
                _fail(E_HASH, "release output DMG hash mismatch")
            snapshot.chmod(0o400)
            yield snapshot
    finally:
        os.close(descriptor)


@contextmanager
def _mounted_release_dmg(dmg: Path):
    """Mount exactly one signed DMG read-only and yield its sole top-level app."""
    hdiutil = _required_tool("hdiutil", failure_code=E_PROOF)
    with tempfile.TemporaryDirectory(prefix="vibecrafted-release-mount-") as directory:
        mount = Path(directory)
        _run_tool(
            [
                hdiutil,
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mount),
                str(dmg),
            ],
            failure_code=E_PROOF,
            context="signed release DMG could not be mounted",
        )
        try:
            attached = _attached_image_mounts()
            if (dmg.resolve(), mount.resolve()) not in attached:
                _fail(E_PROOF, "mounted image is not the exact signed DMG")
            apps = sorted(
                path
                for path in mount.iterdir()
                if path.is_dir() and path.suffix == ".app"
            )
            if [path.name for path in apps] != ["Vibecrafted.app"]:
                _fail(E_BUNDLE, "signed DMG must contain one top-level Vibecrafted.app")
            app = apps[0]
            if app.is_symlink():
                _fail(E_BUNDLE, "mounted Vibecrafted.app must not be a symlink")
            yield app
        finally:
            subprocess.run(
                [hdiutil, "detach", str(mount)],
                check=False,
                capture_output=True,
                text=True,
            )


def _verify_release_artifacts(
    payload: Mapping[str, Any],
    *,
    receipt_root: Path,
    dmg: Path,
    app: Path,
    require_walkaround: bool = False,
) -> dict[str, Any]:
    product = verify_app(app, require_clean=True)
    product_receipt = payload["product"]
    product_manifest_relative = _relative_path(
        product_receipt["manifest"]["path"], field="product.manifest.path"
    ).as_posix()
    if product_manifest_relative != "Contents/Resources/product-manifest.json":
        _fail(E_PROOF, "release output product manifest path is not canonical")
    product_manifest = app / product_manifest_relative
    if product_receipt != {
        "version": product["version"],
        "build": product["build"],
        "architecture": product["architecture"],
        "minimum_macos": product["minimum_macos"],
        "manifest": {
            "path": product_manifest_relative,
            "sha256": _sha256(product_manifest),
        },
    }:
        _fail(E_PROOF, "release output product identity does not match the final app")

    outer = payload["outer_executable"]
    executable_relative = _relative_path(
        outer["path"], field="outer_executable.path"
    ).as_posix()
    if executable_relative != product["outer_bundle_code"]["path"]:
        _fail(E_PROOF, "release output outer executable path mismatch")
    executable = app / executable_relative
    observed_signer, expected_signer = _verify_release_signer_policy(app, outer)
    if outer != {
        "path": executable_relative,
        "sha256": _sha256(executable),
        "code_identity": MACHO_CODE_IDENTITY,
        "code_sha256": _macho_code_sha256(executable),
        "cdhash": observed_signer["cdhash"],
        "signer_policy": expected_signer,
    }:
        _fail(E_HASH, "release output outer executable identity mismatch")

    resources = payload["code_resources"]
    resources_relative = _relative_path(
        resources["path"], field="code_resources.path"
    ).as_posix()
    if resources_relative != "Contents/_CodeSignature/CodeResources":
        _fail(E_PROOF, "release output CodeResources path is not canonical")
    resources_path = app / resources_relative
    if not resources_path.is_file() or resources_path.is_symlink():
        _fail(E_MISSING, "final app CodeResources is missing")
    if resources["sha256"] != _sha256(resources_path):
        _fail(E_HASH, "release output CodeResources hash mismatch")

    product_modules = {item["module"]: item for item in product["modules"]}
    for name in sorted(SUPPORTED_MODULES):
        binding = product_modules[name]
        expected = {
            "manifest": {
                "path": binding["manifest_path"],
                "sha256": binding["manifest_sha256"],
            },
            "assembly_receipt": {
                "path": binding["assembly_receipt_path"],
                "sha256": binding["assembly_receipt_sha256"],
            },
            "git_sha": binding["git_sha"],
        }
        if payload["modules"][name] != expected:
            _fail(E_PROOF, f"release output module identity mismatch: {name}")
        for field_name in ("manifest", "assembly_receipt"):
            referent = expected[field_name]
            artifact = app / referent["path"]
            if _sha256(artifact) != referent["sha256"]:
                _fail(E_HASH, f"release output module referent mismatch: {name}")

    expected_revisions = {
        "vibecrafted": product["git_sha"],
        "vc-terminal": product_modules["vc-terminal"]["git_sha"],
        "vc-frame": product_modules["vc-frame"]["git_sha"],
    }
    if payload["source_revisions"] != expected_revisions:
        _fail(E_PROOF, "release output source revisions do not match the product")
    if require_walkaround:
        return _run_walkaround_probes(app, dmg)
    return _run_live_release_checks(app, dmg)


def _verify_release_output(
    receipt_path: str | Path,
    signature_path: str | Path,
    *,
    require_walkaround: bool = False,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    tuple[_CapturedProofArtifact, _CapturedProofArtifact],
]:
    """Verify W4's signed receipt as the sole selector of the DMG and app."""
    receipt = Path(receipt_path)
    signature = Path(signature_path)
    if (
        receipt.name != "release-output.json"
        or signature.name != "release-output.json.sig"
        or receipt.parent.absolute() != signature.parent.absolute()
    ):
        _fail(E_PROOF, "release verification requires the canonical signed tuple")
    captured_receipt, captured_signature = _verify_release_signature(receipt, signature)
    payload = _load_json_bytes(captured_receipt.raw, source=receipt)
    canonical = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    if captured_receipt.raw != canonical:
        _fail(E_PROOF, "release output must be canonical JSON")
    # Public schema owns the closed shape.  Do not index even one receipt field
    # before it has accepted the exact immutable bytes verified above.
    validate_schema_document(payload)
    policy = _release_policy()
    if payload["schema"] != RELEASE_OUTPUT_SCHEMA:
        _fail(E_SCHEMA, f"release output schema must be {RELEASE_OUTPUT_SCHEMA}")
    expected_signature_policy = {
        "algorithm": policy["algorithm"],
        "key_id": "vibecrafted-signing-v1",
        "spki_sha256": policy["public_key_spki_sha256"],
    }
    if payload["signature_policy"] != expected_signature_policy:
        _fail(E_PROOF, "release output signature policy violates packaged policy")
    raw_dmg = payload["dmg"]
    if not is_canonical_release_dmg_name(
        raw_dmg["path"],
        version=payload["product"]["version"],
        source_revision=payload["source_revisions"]["vibecrafted"],
    ):
        _fail(E_PROOF, "release DMG path must bind version, date and source revision")
    dmg = _release_relative_path(receipt.parent, raw_dmg["path"], field="dmg.path")
    if not dmg.is_file() or dmg.is_symlink():
        _fail(E_MISSING, "release DMG is missing")
    dmg_size = raw_dmg["size"]
    if (
        isinstance(dmg_size, bool)
        or not isinstance(dmg_size, int)
        or dmg_size <= 0
        or dmg_size != dmg.stat().st_size
    ):
        _fail(E_SIZE, "release output DMG size mismatch")
    expected_dmg_sha256 = _expect_sha256(raw_dmg["sha256"], field="dmg.sha256")
    with (
        _captured_release_dmg(
            dmg, expected_size=dmg_size, expected_sha256=expected_dmg_sha256
        ) as captured_dmg,
        _mounted_release_dmg(captured_dmg) as app,
    ):
        observations = _verify_release_artifacts(
            payload,
            receipt_root=receipt.parent,
            dmg=captured_dmg,
            app=app,
            require_walkaround=require_walkaround,
        )
    return payload, observations, (captured_receipt, captured_signature)


def verify_release_output(
    receipt_path: str | Path,
    signature_path: str | Path,
) -> dict[str, Any]:
    """Verify W4's signed receipt as the sole selector of the DMG and app."""
    payload, _, _ = _verify_release_output(receipt_path, signature_path)
    return payload


def _validate_identity(
    value: Any,
    *,
    field: str,
    receipt_root: Path,
    artifact: str,
) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(E_SCHEMA, f"{field} must be an object")
    digest_field = f"{artifact}_manifest_sha256"
    required = {"version", "manifest_path", digest_field, "source_revision"}
    _expect_keys(value, required=required, context=field)
    relative = _relative_path(
        value["manifest_path"], field=f"{field}.manifest_path"
    ).as_posix()
    expected_relative = (
        PRODUCT_MANIFEST_REFERENT
        if artifact == "product"
        else RUNTIME_MANIFEST_REFERENT
    )
    if relative != expected_relative:
        _fail(
            E_TRANSACTION,
            f"{field} manifest referent must use canonical path {expected_relative}",
        )
    manifest_path = receipt_root / relative
    _inside_payload(receipt_root, manifest_path, context=f"{field}.manifest_path")
    if not manifest_path.is_file() or manifest_path.is_symlink():
        _fail(E_MISSING, f"{field} manifest referent is missing: {relative}")
    expected_hash = _expect_sha256(value[digest_field], field=f"{field}.{digest_field}")
    captured_manifest = _capture_proof_artifact(
        manifest_path, context=f"{field} manifest referent"
    )
    if captured_manifest.sha256 != expected_hash:
        _fail(E_HASH, f"{field} manifest referent hash mismatch")
    manifest = _load_json_bytes(captured_manifest.raw, source=manifest_path)
    expected_schema = (
        PRODUCT_SCHEMA if artifact == "product" else RUNTIME_GENERATION_SCHEMA
    )
    if manifest.get("schema") != expected_schema:
        _fail(E_TRANSACTION, f"{field} manifest referent has the wrong schema")
    if artifact == "product":
        try:
            validate_schema_document(manifest)
            files = {
                item["path"]: item
                for item in manifest.get("files", [])
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            }
            entrypoints = manifest.get("entrypoints")
            if not isinstance(entrypoints, dict):
                _fail(E_ENTRYPOINT, "product referent has no complete entrypoints")
            _validate_launch_contract(
                manifest.get("launch_contract"),
                files=files,
                entrypoints=entrypoints,
            )
        except ProductContractError as exc:
            _fail(E_TRANSACTION, f"{field} product manifest is incomplete: {exc}")
    else:
        try:
            _validate_runtime_generation_manifest(manifest, field=field)
        except ProductContractError as exc:
            _fail(E_TRANSACTION, f"{field} runtime manifest is incomplete: {exc}")
    version = _expect_string(value["version"], field=f"{field}.version")
    if manifest.get("version") != version:
        _fail(E_TRANSACTION, f"{field} version does not match manifest referent")
    source_revision = _expect_git_sha(
        value["source_revision"], field=f"{field}.source_revision"
    )
    revision_field = "git_sha" if artifact == "product" else "source_revision"
    if manifest.get(revision_field) != source_revision:
        _fail(
            E_TRANSACTION, f"{field} source revision does not match manifest referent"
        )
    return {
        "version": version,
        "manifest_path": relative,
        digest_field: expected_hash,
        "source_revision": source_revision,
    }


def _validate_runtime_generation_manifest(
    manifest: Mapping[str, Any], *, field: str
) -> None:
    required = {
        "schema",
        "version",
        "source_fingerprint",
        "owner_repo",
        "source_revision",
        "source_payload",
        "entrypoint",
        "hashes",
    }
    _expect_keys(manifest, required=required, context=f"{field} runtime manifest")
    if manifest["schema"] != RUNTIME_GENERATION_SCHEMA:
        _fail(E_TRANSACTION, f"{field} runtime schema is not canonical")
    version = _expect_string(manifest["version"], field=f"{field}.version")
    if version != version.strip():
        _fail(E_TRANSACTION, f"{field} runtime version is not canonical")
    fingerprint = _expect_sha256(
        manifest["source_fingerprint"], field=f"{field}.source_fingerprint"
    )
    if not fingerprint:  # pragma: no cover - _expect_sha256 either returns or fails.
        _fail(E_TRANSACTION, f"{field} runtime source fingerprint is missing")
    owner_repo = _expect_string(manifest["owner_repo"], field=f"{field}.owner_repo")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", owner_repo) is None:
        _fail(E_TRANSACTION, f"{field} runtime owner_repo is invalid")
    _expect_git_sha(manifest["source_revision"], field=f"{field}.source_revision")
    _validate_source_payload_identity(
        manifest["source_payload"], field=f"{field}.source_payload"
    )
    entrypoint = _relative_path(
        manifest["entrypoint"], field=f"{field}.entrypoint"
    ).as_posix()
    if entrypoint != RUNTIME_GENERATION_ENTRYPOINT:
        _fail(E_TRANSACTION, f"{field} runtime entrypoint is not canonical")
    hashes = manifest["hashes"]
    if (
        not isinstance(hashes, dict)
        or set(hashes) != RUNTIME_GENERATION_REQUIRED_HASHES
    ):
        _fail(E_TRANSACTION, f"{field} runtime hash inventory is incomplete")
    for relative, digest in hashes.items():
        _relative_path(relative, field=f"{field}.hashes.path")
        _expect_sha256(digest, field=f"{field}.hashes.{relative}")


def _validate_source_payload_identity(value: Any, *, field: str) -> dict[str, Any]:
    """Validate the closed digest identity of the distribution input tree."""
    if not isinstance(value, dict):
        _fail(E_SCHEMA, f"{field} must be an object")
    _expect_keys(
        value,
        required={"schema", "algorithm", "tree_sha256", "entry_count"},
        context=field,
    )
    if value["schema"] != SOURCE_PAYLOAD_SCHEMA:
        _fail(E_TRANSACTION, f"{field}.schema is not canonical")
    if value["algorithm"] != "sha256":
        _fail(E_TRANSACTION, f"{field}.algorithm is not canonical")
    tree_sha256 = _expect_sha256(value["tree_sha256"], field=f"{field}.tree_sha256")
    entry_count = value["entry_count"]
    if (
        isinstance(entry_count, bool)
        or not isinstance(entry_count, int)
        or entry_count < 1
    ):
        _fail(E_TRANSACTION, f"{field}.entry_count must be a positive integer")
    return {
        "schema": SOURCE_PAYLOAD_SCHEMA,
        "algorithm": "sha256",
        "tree_sha256": tree_sha256,
        "entry_count": entry_count,
    }


def _validate_source_provenance(value: Any, *, field: str) -> dict[str, Any]:
    """Validate the exact carrier that transported the distribution input identity."""
    if not isinstance(value, dict):
        _fail(E_SCHEMA, f"{field} must be an object")
    _expect_keys(
        value,
        required={"schema", "owner_repo", "source_revision", "payload"},
        context=field,
    )
    if value["schema"] != SOURCE_PROVENANCE_SCHEMA:
        _fail(E_TRANSACTION, f"{field}.schema is not canonical")
    owner_repo = _expect_string(value["owner_repo"], field=f"{field}.owner_repo")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", owner_repo) is None:
        _fail(E_TRANSACTION, f"{field}.owner_repo is invalid")
    source_revision = _expect_git_sha(
        value["source_revision"], field=f"{field}.source_revision"
    )
    return {
        "schema": SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": _validate_source_payload_identity(
            value["payload"], field=f"{field}.payload"
        ),
    }


def _validate_runtime_projection_topology(
    root: Path, projected: Path, resolved: Path
) -> None:
    """Allow config projection only through the exact top-level runtime link."""
    if resolved == projected:
        return
    runtime_alias = root / "runtime"
    canonical_runtime_relative = PurePosixPath(
        RUNTIME_GENERATION_CANONICAL_CONFIG
    ).parents[2]
    canonical_runtime = root / canonical_runtime_relative
    canonical_config = root / RUNTIME_GENERATION_CANONICAL_CONFIG
    if not runtime_alias.is_symlink():
        _fail(E_PATH, "runtime projection topology is not canonical")
    try:
        raw_target = os.readlink(runtime_alias)
        resolved_runtime = runtime_alias.resolve(strict=True)
        resolved_canonical_runtime = canonical_runtime.resolve(strict=True)
        resolved_canonical_config = canonical_config.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(E_PATH, f"runtime projection topology is invalid: {exc}")
    if (
        raw_target != canonical_runtime_relative.as_posix()
        or resolved_runtime != canonical_runtime
        or resolved_canonical_runtime != canonical_runtime
        or resolved_canonical_config != canonical_config
        or resolved != canonical_config
    ):
        _fail(E_PATH, "runtime projection topology is not canonical")


def verify_installed_runtime_generation(
    generation_root: str | Path,
    *,
    expected_entrypoint: str | Path | None = None,
) -> dict[str, Any]:
    """Verify one installed generation from its closed manifest and bound bytes."""
    raw_root = Path(generation_root)
    if raw_root.is_symlink():
        _fail(E_PATH, "installed runtime generation root must not be a symlink")
    try:
        root = raw_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        _fail(E_PATH, f"installed runtime generation cannot be resolved: {exc}")
    if not root.is_dir():
        _fail(E_PATH, "installed runtime generation root must be a directory")

    manifest_path = root / RUNTIME_GENERATION_MANIFEST_NAME
    captured_manifest = _capture_proof_artifact(
        manifest_path, context="installed runtime generation manifest"
    )
    manifest = _load_json_bytes(captured_manifest.raw, source=manifest_path)
    _validate_runtime_generation_manifest(manifest, field="installed generation")

    provenance_path = root / SOURCE_PROVENANCE_NAME
    captured_provenance = _capture_proof_artifact(
        provenance_path, context="installed runtime source provenance"
    )
    provenance_document = _load_json_bytes(
        captured_provenance.raw, source=provenance_path
    )
    canonical_provenance = (
        json.dumps(
            provenance_document,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if captured_provenance.raw != canonical_provenance:
        _fail(E_PROOF, "installed runtime source provenance is not canonical JSON")
    provenance = _validate_source_provenance(
        provenance_document,
        field="installed generation source provenance",
    )
    if (
        provenance["owner_repo"] != manifest["owner_repo"]
        or provenance["source_revision"] != manifest["source_revision"]
        or provenance["payload"] != manifest["source_payload"]
    ):
        _fail(
            E_TRANSACTION,
            "installed generation source provenance disagrees with runtime manifest",
        )

    version_raw: bytes | None = None
    hashes = manifest["hashes"]
    for relative in sorted(RUNTIME_GENERATION_REQUIRED_HASHES):
        target = root / _relative_path(
            relative, field="installed generation.hashes.path"
        )
        context = f"installed generation bound file {relative}"
        try:
            resolved_target = target.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            _fail(E_PATH, f"{context} cannot be resolved: {exc}")
        allowed_targets = {target}
        if relative == RUNTIME_GENERATION_PROJECTED_CONFIG:
            canonical_config = root / RUNTIME_GENERATION_CANONICAL_CONFIG
            _validate_runtime_projection_topology(root, target, resolved_target)
            if resolved_target != target:
                allowed_targets.add(canonical_config)
        if resolved_target not in allowed_targets:
            _fail(E_PATH, f"{context} is aliased")
        captured = _capture_proof_artifact(target, context=context)
        if captured.sha256 != hashes[relative]:
            _fail(E_HASH, f"installed generation bound file drifted: {relative}")
        if relative == "VERSION":
            version_raw = captured.raw

    if version_raw is None:  # pragma: no cover - guaranteed by the closed inventory.
        _fail(E_MISSING, "installed generation VERSION is missing")
    try:
        installed_version = version_raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        _fail(E_TRANSACTION, f"installed generation VERSION is not UTF-8: {exc}")
    if installed_version != manifest["version"]:
        _fail(E_TRANSACTION, "installed generation VERSION disagrees with manifest")

    if expected_entrypoint is not None:
        try:
            resolved_expected = Path(expected_entrypoint).resolve(strict=True)
            resolved_canonical = (root / RUNTIME_GENERATION_ENTRYPOINT).resolve(
                strict=True
            )
        except (OSError, RuntimeError, ValueError) as exc:
            _fail(
                E_ENTRYPOINT, f"installed runtime entrypoint cannot be resolved: {exc}"
            )
        if resolved_expected != resolved_canonical:
            _fail(E_ENTRYPOINT, "installed binary is not the manifest entrypoint")

    return dict(manifest)


def _validate_release_state(
    value: Any, *, field: str, receipt_root: Path
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(E_SCHEMA, f"{field} must be an object")
    state = value.get("state")
    if state == "absent":
        _expect_keys(value, required={"state"}, context=field)
        return {"state": "absent"}
    if state != "present":
        _fail(E_SCHEMA, f"{field}.state must be present or absent")
    _expect_keys(value, required={"state", "app", "runtime"}, context=field)
    return {
        "state": "present",
        "app": _validate_identity(
            value["app"],
            field=f"{field}.app",
            receipt_root=receipt_root,
            artifact="product",
        ),
        "runtime": _validate_identity(
            value["runtime"],
            field=f"{field}.runtime",
            receipt_root=receipt_root,
            artifact="runtime",
        ),
    }


def verify_transaction(receipt_path: str | Path) -> dict[str, Any]:
    """Verify that one activation receipt describes an atomic app/runtime pair."""
    path = Path(receipt_path)
    payload = _load_json(path)
    required = {"schema", "transaction_id", "previous", "new", "active", "outcome"}
    _expect_keys(payload, required=required, context="transaction receipt")
    if payload["schema"] != TRANSACTION_SCHEMA:
        _fail(E_SCHEMA, f"transaction schema must be {TRANSACTION_SCHEMA}")
    _expect_string(payload["transaction_id"], field="transaction_id")
    previous = _validate_release_state(
        payload["previous"], field="previous", receipt_root=path.parent
    )
    new = _validate_release_state(payload["new"], field="new", receipt_root=path.parent)
    active = _validate_release_state(
        payload["active"], field="active", receipt_root=path.parent
    )
    if new["state"] != "present":
        _fail(E_TRANSACTION, "transaction new release must be present")
    if previous == new:
        _fail(E_TRANSACTION, "transaction previous and new releases are identical")
    outcome = payload["outcome"]
    if outcome == "activated":
        expected = new
    elif outcome == "rolled_back":
        expected = previous
    else:
        _fail(E_TRANSACTION, f"unsupported transaction outcome: {outcome!r}")
    if active != expected:
        _fail(E_TRANSACTION, f"split activation: {outcome} pair is not active")
    return payload


def _walkaround_probe_id(name: str) -> str:
    return f"io.vetcoders.vibecrafted.walkaround.{name}.v1"


def _attached_image_mounts() -> set[tuple[Path, Path]]:
    hdiutil = _required_tool("hdiutil", failure_code=E_PROOF)
    result = subprocess.run(
        [hdiutil, "info", "-plist"],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        _fail(E_PROOF, "hdiutil could not prove the mounted DMG identity")
    try:
        payload = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as exc:
        _fail(E_PROOF, f"hdiutil returned invalid mount evidence: {exc}")
    if not isinstance(payload, dict):
        _fail(E_PROOF, "hdiutil mount evidence must be a dictionary")
    mounts: set[tuple[Path, Path]] = set()
    images = payload.get("images", [])
    if not isinstance(images, list):
        _fail(E_PROOF, "hdiutil mount evidence has invalid images")
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("image-path"), str):
            continue
        image_path = Path(image["image-path"]).resolve(strict=False)
        entities = image.get("system-entities", [])
        if not isinstance(entities, list):
            continue
        for entity in entities:
            if isinstance(entity, dict) and isinstance(entity.get("mount-point"), str):
                mounts.add(
                    (image_path, Path(entity["mount-point"]).resolve(strict=False))
                )
    return mounts


def _live_release_commands(app: Path, dmg: Path) -> dict[str, list[str]]:
    return {
        "app_codesign": [
            "codesign",
            "--verify",
            "--strict",
            "--verbose=2",
            str(app),
        ],
        "dmg_codesign": [
            "codesign",
            "--verify",
            "--strict",
            "--verbose=2",
            str(dmg),
        ],
        "app_notarization": ["xcrun", "stapler", "validate", str(app)],
        "dmg_notarization": ["xcrun", "stapler", "validate", str(dmg)],
        "app_gatekeeper": [
            "spctl",
            "--assess",
            "--type",
            "execute",
            "--verbose=4",
            str(app),
        ],
        "dmg_gatekeeper": [
            "spctl",
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            "--verbose=4",
            str(dmg),
        ],
    }


def _probe_registry() -> tuple[ProbeSpec, ...]:
    """The frozen 15-proof registry; W0 defines shape, later stages own providers."""
    app, dmg = "{APP}", "{DMG}"
    argv = _live_release_commands(Path(app), Path(dmg))
    specs = [
        ProbeSpec(
            name=name,
            executor="argv",
            owner_stage="W0",
            operation_id=f"platform.{name}.v1",
            assertions=("exit_zero",),
            argv=tuple(command),
        )
        for name, command in argv.items()
    ]
    specs.append(
        ProbeSpec(
            name="one_app",
            executor="builtin",
            owner_stage="W0",
            operation_id="mounted_dmg.single_top_level_app.v1",
            assertions=("sole_top_level_app", "canonical_bundle_name"),
        )
    )
    for name, stage, assertions in (
        (
            "sanitized_launch",
            "W2",
            ("closed_environment", "launch_succeeds", "vc_git_reachable"),
        ),
        ("mission_control", "W2", ("mission_control_reachable",)),
        ("bundled_console", "W2", ("bundled_console_reachable",)),
        ("start_here", "W2", ("onboarding_reachable",)),
        ("update", "W3", ("update_changes_active_identity",)),
        ("rollback", "W3", ("rollback_restores_previous_identity",)),
        ("reattach", "W3", ("session_reattaches",)),
    ):
        specs.append(
            ProbeSpec(
                name=name,
                executor="scenario",
                owner_stage=stage,
                operation_id=f"scenario.{name}.v1",
                assertions=assertions,
            )
        )
    specs.append(
        ProbeSpec(
            name="one_outer_writer",
            executor="pipeline_gate",
            owner_stage="W4",
            operation_id="release.single_outer_writer.v1",
            assertions=("single_app_builder", "single_dmg_builder"),
        )
    )
    registry = tuple(sorted(specs, key=lambda item: item.name))
    if {item.name for item in registry} != _WALKAROUND_CHECKS:
        raise RuntimeError("walk-around registry does not match the frozen proof set")
    return registry


def _probe_specs() -> dict[str, ProbeSpec]:
    return {item.name: item for item in _probe_registry()}


def _canonical_runner_commands() -> dict[str, list[str]]:
    """Compatibility view containing only the six genuine platform argv probes."""
    return {
        spec.name: list(spec.argv or ())
        for spec in _probe_registry()
        if spec.executor == "argv"
    }


def _expected_runner_commands(app: Path, dmg: Path) -> dict[str, list[str]]:
    return {
        name: [
            item.replace("{APP}", str(app)).replace("{DMG}", str(dmg))
            for item in command
        ]
        for name, command in _canonical_runner_commands().items()
    }


def _walkaround_provider_registry(
    scenario: _WalkaroundScenario,
) -> Mapping[str, Callable[[Path, Path], Mapping[str, bytes | str]]]:
    """Bind every W2/W3/W4 proof to one shared real product scenario."""
    providers = {
        "sanitized_launch": _scenario_sanitized_launch,
        "mission_control": _scenario_mission_control,
        "bundled_console": _scenario_bundled_console,
        "start_here": _scenario_start_here,
        "update": _scenario_update,
        "rollback": _scenario_rollback,
        "reattach": _scenario_reattach,
        "one_outer_writer": _scenario_one_outer_writer,
    }
    return {
        name: (lambda _app, _dmg, provider=provider: provider(scenario))
        for name, provider in providers.items()
    }


@dataclass(frozen=True)
class _WalkaroundScenario:
    root: Path
    app: Path
    dmg: Path
    environment: Mapping[str, str]
    bundled_version: str
    previous_active: bytes
    current_active: bytes
    active: Mapping[str, Any]
    bootstrap_stdout: bytes
    bootstrap_stderr: bytes
    session_socket: Path
    session_pid: int

    @property
    def runtime_root(self) -> Path:
        return Path(self.active["runtime_root"])

    @property
    def launchers(self) -> Path:
        return self.root / "launchers"

    @property
    def product_config(self) -> Path:
        return self.root / "config/vibecrafted"


def _scenario_command(
    scenario: _WalkaroundScenario,
    command: Sequence[str | Path],
    *,
    timeout: float = 20,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        [str(item) for item in command],
        cwd=scenario.root,
        env=dict(scenario.environment),
        check=False,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"no diagnostic")[:4096]
        raise RuntimeError(
            f"scenario command failed (exit={result.returncode}; "
            f"{' '.join(str(item) for item in command)}): "
            f"{detail.decode('utf-8', errors='replace').strip()}"
        )
    return result


def _socket_owner_pid(socket_path: Path, frame: Path) -> int:
    lsof = _required_tool("lsof", failure_code=E_PROOF)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = subprocess.run(
            [lsof, "-t", str(socket_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        candidates = {
            int(line) for line in result.stdout.splitlines() if line.strip().isdigit()
        }
        for pid in sorted(candidates):
            process = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
            command = process.stdout.strip()
            if str(frame) in command and str(socket_path) in command:
                return pid
        time.sleep(0.05)
    raise RuntimeError("vc-frame session socket has no exact owning process")


def _connect_session_socket(socket_path: Path) -> bytes:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2)
    try:
        client.connect(str(socket_path))
    finally:
        client.close()
    return f"connectable={socket_path.name}".encode()


def _stop_scenario_session(pid: int, socket_path: Path) -> None:
    try:
        process = subprocess.run(
            ["/bin/ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
        if str(socket_path) not in process.stdout:
            return
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _active_runtime_identity_matches(
    active: Mapping[str, Any],
    *,
    app: Path,
    runtime_root: Path,
    version: str,
) -> bool:
    """Match one exact identity while tolerating canonical parent aliases."""
    expected_identity = {
        "schema": "vibecrafted.active-runtime.v1",
        "version": version,
    }
    if set(active) != {"app_root", "runtime_root", *expected_identity}:
        return False
    if any(active.get(key) != value for key, value in expected_identity.items()):
        return False
    if not isinstance(active.get("app_root"), str) or not isinstance(
        active.get("runtime_root"), str
    ):
        return False
    try:
        return Path(active["app_root"]).resolve(strict=True) == app.resolve(
            strict=True
        ) and Path(active["runtime_root"]).resolve(strict=True) == runtime_root.resolve(
            strict=True
        )
    except (OSError, RuntimeError):
        return False


@contextmanager
def _walkaround_scenario(app: Path, dmg: Path):
    """Run one real, isolated install/update while a vc-frame server stays live."""
    # Darwin AF_UNIX paths are bounded to 104 bytes.  Keep both the temporary
    # directory prefix and socket basename deliberately short.
    with tempfile.TemporaryDirectory(
        prefix="vc-wa-", dir=_WALKAROUND_TEMP_PARENT
    ) as raw:
        root = Path(raw)
        home = root / "home"
        runtime_home = root / "runtime"
        launchers = root / "launchers"
        config_home = root / "config"
        crafted_home = home / ".vibecrafted"
        temporary = root / "tmp"
        for directory in (home, runtime_home, launchers, config_home, temporary):
            directory.mkdir(parents=True, exist_ok=True)

        bundled_version = (
            app.joinpath("Contents/Resources/runtime/VERSION")
            .read_text(encoding="utf-8")
            .strip()
        )
        if not bundled_version or "+g" not in bundled_version:
            raise RuntimeError("bundled runtime VERSION is not source-stamped")
        previous_version = "0.0.0+g00000000"
        previous_root = runtime_home / "releases" / previous_version
        previous_root.mkdir(parents=True)
        sentinel = previous_root / "walkaround.previous-generation"
        sentinel.write_text("retained-for-rollback\n", encoding="utf-8")
        previous_document = {
            "app_root": str(root / "Previous-Vibecrafted.app"),
            "runtime_root": str(previous_root),
            "schema": "vibecrafted.active-runtime.v1",
            "version": previous_version,
        }
        previous_active = (
            json.dumps(previous_document, indent=2, sort_keys=True) + "\n"
        ).encode()
        active_path = runtime_home / "active.json"
        active_path.write_bytes(previous_active)

        environment = {
            "HOME": str(home),
            "USER": os.environ.get("USER", "operator"),
            "LOGNAME": os.environ.get("LOGNAME", "operator"),
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TMPDIR": str(temporary),
            "SHELL": "/bin/zsh",
            "PATH": _LAUNCH_SYSTEM_PATH,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(config_home),
            "VIBECRAFTED_HOME": str(crafted_home),
            "VIBECRAFTED_RUNTIME_HOME": str(runtime_home),
            "VIBECRAFTED_LAUNCHER_BIN": str(launchers),
            "VC_FRAME_CONFIG_DIR": str(
                app / "Contents/Resources/runtime/config/vc-frame"
            ),
        }

        frame = app / "Contents/Helpers/vc-frame"
        session_socket = temporary / "s.sock"
        server = subprocess.run(
            [str(frame), "--server", str(session_socket)],
            cwd=root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=10,
        )
        if server.returncode != 0:
            raise RuntimeError("vc-frame could not start the preserved session server")
        session_pid = _socket_owner_pid(session_socket, frame)
        _connect_session_socket(session_socket)

        try:
            bootstrap = subprocess.run(
                [str(app / "Contents/MacOS/Vibecrafted"), "--bootstrap-only"],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                timeout=60,
            )
            if bootstrap.returncode != 0:
                detail = (bootstrap.stderr or bootstrap.stdout)[:4096]
                raise RuntimeError(
                    "outer app bootstrap failed: "
                    + detail.decode("utf-8", errors="replace").strip()
                )
            current_active = active_path.read_bytes()
            active = _load_json_bytes(current_active, source=active_path)
            expected_root = runtime_home / "releases" / bundled_version
            if not _active_runtime_identity_matches(
                active,
                app=app,
                runtime_root=expected_root,
                version=bundled_version,
            ):
                raise RuntimeError(
                    "outer app did not publish the exact bundled identity"
                )
            if sentinel.read_bytes() != b"retained-for-rollback\n":
                raise RuntimeError("update mutated the previous runtime generation")
            observed_pid = _socket_owner_pid(session_socket, frame)
            if observed_pid != session_pid:
                raise RuntimeError("update replaced the live vc-frame session process")
            _connect_session_socket(session_socket)
            yield _WalkaroundScenario(
                root=root,
                app=app,
                dmg=dmg,
                environment=environment,
                bundled_version=bundled_version,
                previous_active=previous_active,
                current_active=current_active,
                active=active,
                bootstrap_stdout=bootstrap.stdout,
                bootstrap_stderr=bootstrap.stderr,
                session_socket=session_socket,
                session_pid=session_pid,
            )
        finally:
            _stop_scenario_session(session_pid, session_socket)


def _scenario_sanitized_launch(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    expected_keys = sorted(scenario.environment)
    launcher = scenario.launchers / "vibecrafted"
    version = _scenario_command(scenario, [launcher, "version"]).stdout.strip()
    help_output = _scenario_command(scenario, [launcher, "--help"]).stdout
    vc_git_help = _scenario_command(
        scenario, [scenario.launchers / "vc-git", "--help"]
    ).stdout
    if scenario.bundled_version.encode() not in version:
        raise RuntimeError("installed CLI version is not the bundled identity")
    if scenario.bundled_version.encode() not in help_output:
        raise RuntimeError("installed CLI help is not source-stamped")
    if b"Show full Git context, including every worktree." not in vc_git_help:
        raise RuntimeError("installed vc-git launcher is unavailable")
    for candidate in scenario.launchers.iterdir():
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError(
                f"launcher is not an exact regular file: {candidate.name}"
            )
        payload = candidate.read_bytes()
        if b"/Volumes/" in payload or b"vc-frame.real" in payload:
            raise RuntimeError(
                f"launcher escaped the packaged runtime: {candidate.name}"
            )
    return {
        "closed_environment": "keys=" + ",".join(expected_keys),
        "launch_succeeds": version,
        "vc_git_reachable": hashlib.sha256(vc_git_help).hexdigest(),
    }


def _scenario_mission_control(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    outer = scenario.app / "Contents/MacOS/Vibecrafted"
    ffi = scenario.app / "Contents/Frameworks/libvibecrafted_shell_ffi.dylib"
    strings = _scenario_command(scenario, ["/usr/bin/strings", "-a", outer]).stdout
    symbols = _scenario_command(scenario, ["/usr/bin/nm", "-gU", ffi]).stdout
    if b"Mission Control" not in strings:
        raise RuntimeError("outer app has no Mission Control surface")
    if b"load_mission_control_snapshot" not in symbols:
        raise RuntimeError("Mission Control FFI entry is not linked")
    return {"mission_control_reachable": hashlib.sha256(strings + symbols).hexdigest()}


def _scenario_bundled_console(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    result = _scenario_command(
        scenario, [scenario.launchers / "vc-terminal", "--version"]
    )
    if b"alacritty" not in result.stdout.lower():
        raise RuntimeError("bundled console did not identify its terminal host")
    return {"bundled_console_reachable": result.stdout.strip()}


def _scenario_start_here(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    layout = scenario.product_config / "vc-frame/layouts/operator.kdl"
    payload = layout.read_bytes()
    if b'tab name="Start here"' not in payload:
        raise RuntimeError("installed operator layout has no Start here tab")
    help_output = _scenario_command(
        scenario, [scenario.launchers / "vc-start", "--help"]
    ).stdout
    if b"Start the operator vc-frame session" not in help_output:
        raise RuntimeError("installed vc-start onboarding help is unavailable")
    return {"onboarding_reachable": hashlib.sha256(payload + help_output).hexdigest()}


def _scenario_update(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    if scenario.current_active == scenario.previous_active:
        raise RuntimeError("update did not change the active runtime identity")
    if not scenario.runtime_root.is_dir():
        raise RuntimeError("updated runtime generation is missing")
    return {
        "update_changes_active_identity": (
            hashlib.sha256(scenario.previous_active).hexdigest()
            + "->"
            + hashlib.sha256(scenario.current_active).hexdigest()
        )
    }


def _scenario_rollback(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    active = scenario.root / "runtime/active.json"
    temporary = active.with_name(".active.json.rollback")
    temporary.write_bytes(scenario.previous_active)
    os.replace(temporary, active)
    if active.read_bytes() != scenario.previous_active:
        raise RuntimeError("rollback could not restore the previous identity")
    temporary.write_bytes(scenario.current_active)
    os.replace(temporary, active)
    if active.read_bytes() != scenario.current_active:
        raise RuntimeError("walk-around could not restore the candidate identity")
    return {
        "rollback_restores_previous_identity": hashlib.sha256(
            scenario.previous_active
        ).hexdigest()
    }


def _scenario_reattach(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    frame = scenario.app / "Contents/Helpers/vc-frame"
    if _socket_owner_pid(scenario.session_socket, frame) != scenario.session_pid:
        raise RuntimeError("preserved session PID changed after update")
    connected = _connect_session_socket(scenario.session_socket)
    return {"session_reattaches": f"pid={scenario.session_pid};".encode() + connected}


def _scenario_one_outer_writer(
    scenario: _WalkaroundScenario,
) -> Mapping[str, bytes | str]:
    manifest_path = scenario.app / "Contents/Resources/product-manifest.json"
    manifest = _load_json(manifest_path)
    modules = [item["module"] for item in manifest["modules"]]
    if (
        manifest["product"] != PRODUCT_NAME
        or manifest["bundle_id"] != PRODUCT_BUNDLE_ID
    ):
        raise RuntimeError("outer product identity is not canonical")
    if modules != ["vc-terminal", "vc-frame"]:
        raise RuntimeError("module ownership tuple is not canonical")
    return {
        "single_app_builder": f"{manifest['product']}:{manifest['git_sha']}",
        "single_dmg_builder": f"{scenario.dmg.name}:{_sha256(scenario.dmg)}",
    }


@contextmanager
def _walkaround_providers(app: Path, dmg: Path):
    """Bind every W2/W3/W4 proof to one shared real product scenario."""
    with _walkaround_scenario(app, dmg) as scenario:
        yield _walkaround_provider_registry(scenario)


def _assertion_observations(
    spec: ProbeSpec, evidence: Mapping[str, bytes | str]
) -> list[dict[str, Any]]:
    if set(evidence) != set(spec.assertions):
        _fail(E_PROOF, f"walk-around provider assertions are incomplete: {spec.name}")
    observations: list[dict[str, Any]] = []
    for name in spec.assertions:
        raw = evidence[name]
        if isinstance(raw, str):
            encoded = raw.encode("utf-8")
        elif isinstance(raw, bytes):
            encoded = raw
        else:
            _fail(E_PROOF, f"walk-around provider evidence is invalid: {spec.name}")
        observations.append(
            {
                "name": name,
                "passed": True,
                "evidence_sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    return observations


def _run_live_release_checks(app: Path, dmg: Path) -> dict[str, Any]:
    observations: dict[str, Any] = {}
    for name, command in _expected_runner_commands(app, dmg).items():
        executable = _required_tool(command[0], failure_code=E_PROOF)
        result = subprocess.run(
            [executable, *command[1:]],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"no diagnostic")[:4096]
            _fail(
                E_PROOF,
                f"live walk-around check failed ({name}): "
                f"{detail.decode('utf-8', errors='replace').strip()}",
            )
        observations[name] = {
            "probe_id": _walkaround_probe_id(name),
            "executor": "argv",
            "owner_stage": "W0",
            "operation_id": f"platform.{name}.v1",
            "command": _canonical_runner_commands()[name],
            "exit_code": 0,
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            "assertions": _assertion_observations(
                _probe_specs()[name], {"exit_zero": b"exit_code=0"}
            ),
        }
    return observations


def _run_walkaround_probes(app: Path, dmg: Path) -> dict[str, Any]:
    observations = _run_live_release_checks(app, dmg)
    with _walkaround_providers(app, dmg) as providers:
        for spec in _probe_registry():
            if spec.executor == "argv":
                continue
            if spec.executor == "builtin":
                evidence = {
                    "sole_top_level_app": b"count=1",
                    "canonical_bundle_name": app.name,
                }
            else:
                provider = providers.get(spec.name)
                if provider is None:
                    _fail(
                        E_PROOF,
                        f"walk-around {spec.executor} provider is missing: {spec.name} "
                        f"(owner {spec.owner_stage})",
                    )
                try:
                    evidence = provider(app, dmg)
                except ProductContractError:
                    raise
                # Scenario providers are an explicit W2/W3 extension boundary.
                # Normalize expected operational/data failures, while allowing
                # programming errors outside that contract to remain visible.
                except (OSError, RuntimeError, TypeError, ValueError) as exc:
                    _fail(E_PROOF, f"walk-around provider failed ({spec.name}): {exc}")
            observations[spec.name] = {
                "probe_id": _walkaround_probe_id(spec.name),
                "executor": spec.executor,
                "owner_stage": spec.owner_stage,
                "operation_id": spec.operation_id,
                "assertions": _assertion_observations(spec, evidence),
            }
    return observations


def _release_reference(root: Path, captured: _CapturedProofArtifact) -> dict[str, Any]:
    root_absolute = root.absolute()
    try:
        relative = captured.path.relative_to(root_absolute).as_posix()
    except ValueError:
        _fail(E_PATH, "walk-around release artifacts must share the output directory")
    return {
        "path": relative,
        "sha256": captured.sha256,
        "size": captured.size,
    }


def produce_walkaround(
    release_output_path: str | Path,
    release_signature_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Run the canonical probes and atomically create a verifier-owned receipt."""
    output = Path(output_path)
    release_output = Path(release_output_path)
    release_signature = Path(release_signature_path)
    if output.exists() or output.is_symlink():
        _fail(E_PATH, "walk-around output must be a new explicit path")
    if output.parent.absolute() != release_output.parent.absolute():
        _fail(E_PATH, "walk-around output and release receipt must share a directory")
    if (
        release_output.name != "release-output.json"
        or release_signature.name != "release-output.json.sig"
    ):
        _fail(E_PROOF, "walk-around requires canonical release-output artifact names")
    _, observations, captures = _verify_release_output(
        release_output,
        release_signature,
        require_walkaround=True,
    )
    captured_release, captured_signature = captures
    payload = {
        "schema": WALKAROUND_SCHEMA,
        "release_output": _release_reference(output.parent, captured_release),
        "release_signature": _release_reference(output.parent, captured_signature),
        "observations": {
            "issuer": WALKAROUND_RUNNER_ID,
            "probes": observations,
        },
    }
    validate_schema_document(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return payload


def _verify_runner_receipt(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    _expect_keys(
        payload,
        required={"schema", "release_output", "release_signature", "observations"},
        context="walk-around receipt",
    )
    release_relative = _relative_path(
        payload["release_output"]["path"], field="release_output.path"
    ).as_posix()
    signature_relative = _relative_path(
        payload["release_signature"]["path"], field="release_signature.path"
    ).as_posix()
    if (
        release_relative != "release-output.json"
        or signature_relative != "release-output.json.sig"
    ):
        _fail(E_PROOF, "walk-around must reference canonical release-output artifacts")
    _, expected_probes, captures = _verify_release_output(
        path.parent / release_relative,
        path.parent / signature_relative,
        require_walkaround=True,
    )
    expected_release, expected_signature = captures
    if payload["release_output"] != _release_reference(path.parent, expected_release):
        _fail(E_PROOF, "walk-around release-output reference changed")
    if payload["release_signature"] != _release_reference(
        path.parent, expected_signature
    ):
        _fail(E_PROOF, "walk-around release-signature reference changed")
    observations = payload["observations"]
    if observations != {"issuer": WALKAROUND_RUNNER_ID, "probes": expected_probes}:
        _fail(E_PROOF, "walk-around observations do not match live canonical probes")
    return dict(payload)


def _verify_walkaround(
    receipt_path: Path,
) -> dict[str, Any]:
    path = receipt_path
    payload = _load_json(path)
    validate_schema_document(payload)
    if payload["schema"] != WALKAROUND_SCHEMA:
        _fail(E_SCHEMA, f"walk-around schema must be {WALKAROUND_SCHEMA}")
    return _verify_runner_receipt(path, payload)


def verify_walkaround(
    receipt_path: str | Path,
) -> dict[str, Any]:
    """Verify evidence against the release-policy trust root and mounted DMG."""
    return _verify_walkaround(Path(receipt_path))


def _fixture_entry(
    root: Path,
    relative: str,
    *,
    kind: str,
    dylibs: Sequence[str] | None = None,
) -> dict[str, Any]:
    path = root / relative
    if dylibs is None and kind in {"executable", "dylib"}:
        observed = _observed_macho(path, relative=relative, kind=kind)
        dylibs = () if observed is None else observed.dependencies
    return {
        "path": relative,
        "sha256": _sha256(path),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "kind": kind,
        "size": path.stat().st_size,
        "dylibs": list(dylibs or ()),
    }


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_fixture_macho(path: Path) -> None:
    xcrun = _required_tool("xcrun", failure_code=E_PROOF)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            xcrun,
            "--sdk",
            "macosx",
            "clang",
            "-arch",
            "arm64",
            "-mmacosx-version-min=14.0",
            "-x",
            "c",
            "-",
            "-o",
            str(path),
        ],
        input="int main(void) { return 0; }\n",
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _fail(E_PROOF, f"self-test could not compile Mach-O fixture: {result.stderr}")
    path.chmod(0o755)


def _self_test() -> int:
    expected_failures: list[int] = []
    with tempfile.TemporaryDirectory(prefix="vibecrafted-product-contract-") as tmp:
        root = Path(tmp)
        module = root / "vc-terminal-module"
        executable = module / "bin/vc-terminal"
        _write_fixture_macho(executable)
        module_manifest: dict[str, Any] = {
            "schema": MODULE_SCHEMA,
            "module": "vc-terminal",
            "version": "1.0.0",
            "git_sha": "1" * 40,
            "dirty": False,
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "files": [_fixture_entry(module, "bin/vc-terminal", kind="executable")],
            "entrypoints": {"terminal": "bin/vc-terminal"},
        }
        manifest_path = module / "module-manifest.json"
        _write_json(manifest_path, module_manifest)
        verify_module(module)

        module_manifest["files"][0]["sha256"] = "f" * 64
        _write_json(manifest_path, module_manifest)
        try:
            verify_module(module)
        except ProductContractError as exc:
            expected_failures.append(exc.code)
        module_manifest["files"] = [
            _fixture_entry(module, "bin/vc-terminal", kind="executable")
        ]
        module_manifest["files"][0]["dylibs"].append(
            "/opt/homebrew/lib/libescape.dylib"
        )
        _write_json(manifest_path, module_manifest)
        try:
            verify_module(module)
        except ProductContractError as exc:
            expected_failures.append(exc.code)
        module_manifest["files"] = [
            _fixture_entry(module, "bin/vc-terminal", kind="executable")
        ]
        _write_json(manifest_path, module_manifest)
        verify_module(module)

        mount = root / "mounted"
        app = mount / "Vibecrafted.app"
        app_executable = app / "Contents/MacOS/Vibecrafted"
        terminal = app / _LAUNCH_TERMINAL
        frame = app / "Contents/Helpers/vc-frame"
        product_shell = app / _LAUNCH_SHELL
        for target in (app_executable, terminal, frame, product_shell):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(executable, target)
        codesign = _required_tool("codesign", failure_code=E_PROOF)
        for target in (terminal, frame, product_shell):
            _run_tool(
                [codesign, "--force", "--sign", "-", str(target)],
                failure_code=E_PROOF,
                context=f"self-test could not sign {target.name}",
            )
        primary_shell = app / _LAUNCH_PRIMARY_SHELL
        primary_shell.parent.mkdir(parents=True, exist_ok=True)
        primary_shell.write_text('#!/bin/zsh\nexec vc-start "$@"\n', encoding="utf-8")
        primary_shell.chmod(0o755)
        terminal_app = app / "Contents/Helpers/vc-terminal.app"
        terminal_icon = terminal_app / "Contents/Resources/alacritty.icns"
        terminal_icon.parent.mkdir(parents=True, exist_ok=True)
        terminal_icon.write_bytes(b"terminal-icns-fixture")
        with (terminal_app / "Contents/Info.plist").open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleIdentifier": "io.vetcoders.vc-terminal",
                    "CFBundleExecutable": "alacritty",
                    "CFBundleIconFile": "alacritty.icns",
                    "CFBundlePackageType": "APPL",
                },
                handle,
            )
        _run_tool(
            [codesign, "--force", "--sign", "-", str(terminal_app)],
            failure_code=E_PROOF,
            context="self-test could not sign terminal helper app",
        )
        terminal_config = app / _LAUNCH_CONFIG
        terminal_config.parent.mkdir(parents=True, exist_ok=True)
        terminal_config.write_text("[shell]\nprogram = 'vc-start'\n", encoding="utf-8")
        icon_path = app / "Contents/Resources" / PRODUCT_ICON_FILE
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        icon_path.write_bytes(b"icns-fixture")
        plist_path = app / "Contents/Info.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with plist_path.open("wb") as handle:
            plistlib.dump(
                {
                    "CFBundleIdentifier": PRODUCT_BUNDLE_ID,
                    "CFBundleExecutable": PRODUCT_EXECUTABLE,
                    "CFBundleIconFile": PRODUCT_ICON_FILE,
                    "CFBundleShortVersionString": "1.0.0",
                    "CFBundleVersion": "1",
                },
                handle,
            )
        terminal_product_entry = _fixture_entry(
            app, _LAUNCH_TERMINAL, kind="executable"
        )
        frame_product_entry = _fixture_entry(
            app, "Contents/Helpers/vc-frame", kind="executable"
        )

        def module_binding(
            *,
            module_name: str,
            git_sha: str,
            entrypoint: str,
            product_entry: Mapping[str, Any],
        ) -> dict[str, Any]:
            source_path = f"bin/{module_name}"
            source_entry = dict(module_manifest["files"][0])
            source_entry["path"] = source_path
            receipt = {
                "schema": MODULE_SCHEMA,
                "module": module_name,
                "version": "1.0.0",
                "git_sha": git_sha,
                "dirty": False,
                "architecture": "arm64",
                "minimum_macos": "14.0",
                "files": [source_entry],
                "entrypoints": {entrypoint: source_path},
            }
            receipt_relative = (
                f"Contents/Resources/module-receipts/{module_name}/module-manifest.json"
            )
            _write_json(app / receipt_relative, receipt)
            receipt_hash = _sha256(app / receipt_relative)
            assembly = {
                "schema": ASSEMBLY_SCHEMA,
                "module": module_name,
                "module_manifest_sha256": receipt_hash,
                "files": [
                    {
                        "module_path": source_path,
                        "product_path": product_entry["path"],
                        "unsigned_sha256": source_entry["sha256"],
                        "product_sha256": product_entry["sha256"],
                        "transformation": "codesign",
                    }
                ],
            }
            assembly_relative = f"Contents/Resources/module-receipts/{module_name}/assembly-receipt.json"
            _write_json(app / assembly_relative, assembly)
            return {
                "module": module_name,
                "manifest_path": receipt_relative,
                "manifest_sha256": receipt_hash,
                "assembly_receipt_path": assembly_relative,
                "assembly_receipt_sha256": _sha256(app / assembly_relative),
                "git_sha": git_sha,
            }

        terminal_binding = module_binding(
            module_name="vc-terminal",
            git_sha="4" * 40,
            entrypoint="terminal",
            product_entry=terminal_product_entry,
        )
        frame_binding = module_binding(
            module_name="vc-frame",
            git_sha="6" * 40,
            entrypoint="frame",
            product_entry=frame_product_entry,
        )
        observed_outer = _observed_macho(
            app_executable,
            relative="Contents/MacOS/Vibecrafted",
            kind="executable",
        )
        if observed_outer is None:
            _fail(E_PLATFORM, "self-test outer executable is not Mach-O")
        product_manifest: dict[str, Any] = {
            "schema": PRODUCT_SCHEMA,
            "product": PRODUCT_NAME,
            "bundle_id": PRODUCT_BUNDLE_ID,
            "bundle_executable": PRODUCT_EXECUTABLE,
            "version": "1.0.0",
            "build": "1",
            "git_sha": "2" * 40,
            "dirty": False,
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "modules": [terminal_binding, frame_binding],
            "outer_bundle_code": {
                "identity": OUTER_BUNDLE_CODE_IDENTITY,
                "path": "Contents/MacOS/Vibecrafted",
                "mode": "0755",
                "kind": "executable",
                "architecture": "arm64",
                "minimum_macos": "14.0",
                "dylibs": list(observed_outer.dependencies),
                "code_identity": MACHO_CODE_IDENTITY,
                "code_sha256": _macho_code_sha256(app_executable),
                "info_plist_sha256": _sha256(plist_path),
                "codesign_identifier": PRODUCT_BUNDLE_ID,
            },
            "files": [
                _fixture_entry(app, "Contents/Info.plist", kind="config"),
                _fixture_entry(
                    app,
                    f"Contents/Resources/{PRODUCT_ICON_FILE}",
                    kind="resource",
                ),
                terminal_product_entry,
                frame_product_entry,
                _fixture_entry(
                    app,
                    "Contents/Helpers/vc-terminal.app/Contents/Info.plist",
                    kind="config",
                ),
                _fixture_entry(
                    app,
                    "Contents/Helpers/vc-terminal.app/Contents/Resources/alacritty.icns",
                    kind="resource",
                ),
                _fixture_entry(app, terminal_binding["manifest_path"], kind="config"),
                _fixture_entry(app, frame_binding["manifest_path"], kind="config"),
                _fixture_entry(
                    app, terminal_binding["assembly_receipt_path"], kind="config"
                ),
                _fixture_entry(
                    app, frame_binding["assembly_receipt_path"], kind="config"
                ),
                _fixture_entry(app, _LAUNCH_CONFIG, kind="config"),
                _fixture_entry(app, _LAUNCH_SHELL, kind="executable"),
                _fixture_entry(app, _LAUNCH_PRIMARY_SHELL, kind="resource"),
            ],
            "entrypoints": {
                "app": "Contents/MacOS/Vibecrafted",
                "terminal": _LAUNCH_TERMINAL,
                "frame": "Contents/Helpers/vc-frame",
            },
            "launch_contract": _canonical_launch_contract(),
        }
        _write_json(app / "Contents/Resources/product-manifest.json", product_manifest)
        _run_tool(
            [codesign, "--force", "--sign", "-", str(app)],
            failure_code=E_PROOF,
            context="self-test could not sign outer app bundle",
        )
        verify_app(app)

        transaction_product = root / "manifests/product-manifest.json"
        transaction_runtime = root / "manifests/runtime-manifest.json"
        transaction_product_payload = dict(product_manifest)
        transaction_product_payload["git_sha"] = "c" * 40
        _write_json(transaction_product, transaction_product_payload)
        _write_json(
            transaction_runtime,
            {
                "schema": RUNTIME_GENERATION_SCHEMA,
                "version": "1.0.0",
                "source_fingerprint": "d" * 64,
                "owner_repo": "vetcoders/vibecrafted",
                "source_revision": "e" * 40,
                "source_payload": {
                    "schema": SOURCE_PAYLOAD_SCHEMA,
                    "algorithm": "sha256",
                    "tree_sha256": "f" * 64,
                    "entry_count": 42,
                },
                "entrypoint": RUNTIME_GENERATION_ENTRYPOINT,
                "hashes": {
                    relative: f"{index:x}" * 64
                    for index, relative in enumerate(
                        sorted(RUNTIME_GENERATION_REQUIRED_HASHES), start=1
                    )
                },
            },
        )
        new = {
            "state": "present",
            "app": {
                "version": "1.0.0",
                "manifest_path": "manifests/product-manifest.json",
                "product_manifest_sha256": _sha256(transaction_product),
                "source_revision": "c" * 40,
            },
            "runtime": {
                "version": "1.0.0",
                "manifest_path": "manifests/runtime-manifest.json",
                "runtime_manifest_sha256": _sha256(transaction_runtime),
                "source_revision": "e" * 40,
            },
        }
        transaction = root / "transaction.json"
        _write_json(
            transaction,
            {
                "schema": TRANSACTION_SCHEMA,
                "transaction_id": "self-test",
                "previous": {"state": "absent"},
                "new": new,
                "active": new,
                "outcome": "activated",
            },
        )
        verify_transaction(transaction)

        registry = _probe_registry()
        if (
            len(registry) != 15
            or {item.name for item in registry} != _WALKAROUND_CHECKS
        ):
            _fail(E_PROOF, "self-test walk-around registry is incomplete")
        if sum(item.executor == "argv" for item in registry) != 6:
            _fail(E_PROOF, "self-test walk-around argv registry is incomplete")
        if any(item.executor != "argv" and item.argv is not None for item in registry):
            _fail(E_PROOF, "self-test semantic probes carry fabricated argv")

    if expected_failures != [E_HASH, E_DEPENDENCY]:
        _fail(
            E_PROOF,
            f"self-test negative controls returned {expected_failures}, expected "
            f"{[E_HASH, E_DEPENDENCY]}",
        )
    print(f"self-test: PASS valid=4 negative=2 error_codes={E_HASH},{E_DEPENDENCY}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    module = commands.add_parser("module", help="verify an unsigned module")
    module.add_argument("path", type=Path)
    module.add_argument("--require-clean", action="store_true")
    app = commands.add_parser("app", help="verify an assembled app bundle")
    app.add_argument("path", type=Path)
    app.add_argument("--require-clean", action="store_true")
    transaction = commands.add_parser(
        "transaction", help="verify an app/runtime activation receipt"
    )
    transaction.add_argument("path", type=Path)
    schema = commands.add_parser("schema", help="validate one public contract JSON")
    schema.add_argument("path", type=Path)
    walkaround = commands.add_parser(
        "walkaround", help="verify a mounted-DMG walk-around receipt"
    )
    walkaround.add_argument("path", type=Path)
    release_output = commands.add_parser(
        "release-output", help="verify the externally signed final release identity"
    )
    release_output.add_argument("path", type=Path)
    release_output.add_argument("signature", type=Path)
    runtime_generation = commands.add_parser(
        "runtime-generation", help="verify an installed runtime generation"
    )
    runtime_generation.add_argument("path", type=Path)
    runtime_generation.add_argument("--expected-entrypoint", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint with stable non-zero exit statuses per failure class."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments == ["--self-test"]:
        try:
            return _self_test()
        except ProductContractError as exc:
            print(f"VCPC{exc.code:03d}: {exc}", file=sys.stderr)
            return exc.code
    args = _parser().parse_args(arguments)
    try:
        if args.command == "module":
            verify_module(args.path, require_clean=args.require_clean)
        elif args.command == "app":
            verify_app(args.path, require_clean=args.require_clean)
        elif args.command == "transaction":
            verify_transaction(args.path)
        elif args.command == "schema":
            validate_schema_document(_load_json(args.path))
        elif args.command == "walkaround":
            verify_walkaround(args.path)
        elif args.command == "release-output":
            verify_release_output(args.path, args.signature)
        elif args.command == "runtime-generation":
            verify_installed_runtime_generation(
                args.path, expected_entrypoint=args.expected_entrypoint
            )
        else:  # pragma: no cover - argparse owns the command set.
            _fail(E_SCHEMA, f"unsupported command: {args.command}")
    except ProductContractError as exc:
        print(f"VCPC{exc.code:03d}: {exc}", file=sys.stderr)
        return exc.code
    print(f"verified {args.command}: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
