"""Installed trust boundary for Vibecrafted release and walk-around verification."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

_MAX_VERIFIER_SOURCE_BYTES = 8 * 1024 * 1024


def _capture_sibling_source(source: Path) -> bytes:
    """Read one immutable, regular, non-aliased verifier source snapshot."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise RuntimeError(f"installed product contract is unreadable: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeError(
                "installed product contract is not a unique regular file"
            )
        if before.st_size > _MAX_VERIFIER_SOURCE_BYTES:
            raise RuntimeError(
                "installed product contract exceeds the source size limit"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_VERIFIER_SOURCE_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) > _MAX_VERIFIER_SOURCE_BYTES:
            raise RuntimeError(
                "installed product contract exceeds the source size limit"
            )
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise RuntimeError("installed product contract changed during capture")
        return raw
    finally:
        os.close(descriptor)


def _load_product_contract() -> ModuleType:
    """Execute the exact sibling verifier source, bypassing adjacent bytecode caches."""
    source = Path(__file__).with_name("product_contract.py")
    raw = _capture_sibling_source(source)
    module_name = "vibecrafted_core._installed_product_contract"
    spec = spec_from_loader(module_name, loader=None, origin=str(source))
    if spec is None:  # a concrete source path always yields a spec.
        raise RuntimeError("cannot create installed product contract module")
    module = module_from_spec(spec)
    module.__file__ = str(source)
    module.__package__ = "vibecrafted_core"
    sys.modules[module_name] = module
    try:
        # `raw` is the bounded O_NOFOLLOW snapshot of the fixed, manifest-bound sibling;
        # compiling it directly is intentional so adjacent stale bytecode cannot win.
        # The fixed manifest-bound sibling is read as a bounded O_NOFOLLOW snapshot; compile those
        # approved bytes directly to prevent adjacent stale bytecode from winning.
        # This executes the bounded O_NOFOLLOW snapshot of the fixed manifest-bound sibling; direct
        # compilation prevents stale adjacent bytecode from overriding the approved source bytes.
        # nosemgrep: python.lang.security.audit.exec-detected.exec-detected
        exec(  # noqa: S102
            compile(raw, str(source), "exec"), module.__dict__
        )
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


product_contract = _load_product_contract()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser(
        "trust-probe",
        help="verify a domain-separated challenge with the packaged release key",
    )
    probe.add_argument("challenge", type=Path)
    probe.add_argument("signature", type=Path)
    verify = commands.add_parser(
        "verify-release",
        help="verify the signed release output and its selected DMG/app",
    )
    verify.add_argument("--release-output", type=Path, required=True)
    verify.add_argument("--signature", type=Path, required=True)
    walkaround = commands.add_parser(
        "walkaround",
        help="verify canonical walk-around evidence for one signed release",
    )
    walkaround.add_argument("--release-output", type=Path, required=True)
    walkaround.add_argument("--signature", type=Path, required=True)
    walkaround.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "trust-probe":
            product_contract.verify_trust_probe(args.challenge, args.signature)
        elif args.command == "verify-release":
            product_contract.verify_release_output(args.release_output, args.signature)
        else:
            product_contract.produce_walkaround(
                args.release_output,
                args.signature,
                args.output,
            )
    except product_contract.ProductContractError as exc:
        print(f"VCPC{exc.code:03d}: {exc}", file=sys.stderr)
        return exc.code
    print(f"verified {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
