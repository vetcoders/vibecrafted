#!/usr/bin/env bash

# Shared inside-out signing and strict verification for macOS release payloads.
# Callers provide SIGNING_IDENTITY and, when needed, CODESIGN_KEYCHAIN_ARGS.

macho_signing_die() {
  printf 'Mach-O signing failed: %s\n' "$*" >&2
  return 1
}

is_macho_file() {
  LC_ALL=C /usr/bin/file -b "$1" | grep -q 'Mach-O'
}

is_macho_executable() {
  LC_ALL=C /usr/bin/file -b "$1" | grep -q 'Mach-O.*executable'
}

# macho_signature_kind <file> — prints `unsigned`, `adhoc` or `signed`.
#
# Linker-signed and `codesign -s -` seals are `adhoc`: dyld demands one on
# arm64, `strip` re-seals a linker-signed file after rewriting it (measured on
# voc, aicx and prview: `codesign --verify --strict` passes afterwards), and
# the packager re-signs every Mach-O with the release identity anyway, so no
# ad-hoc seal ever reaches the product. Anything else is a real signature and
# marks somebody's finished, signed artifact. `-dv` is the
# verbosity that prints the `Signature=` line; `--verbose=0` stops after
# `Executable=` and would classify every file as signed (measured 2026-09-08).
macho_signature_kind() {
  local report
  report="$(codesign -dv "$1" 2>&1 || true)"
  case "$report" in
    *"not signed at all"*) printf 'unsigned\n' ;;
    *"Signature=adhoc"*) printf 'adhoc\n' ;;
    *) printf 'signed\n' ;;
  esac
}

# strip_macho_debug_tree <root>...
#
# Remove debugging records — and only those — from every Mach-O EXECUTABLE
# under the given roots. Runs on a staged, still unsigned payload, before the
# hygiene gate reads it and before any signature is spent.
#
# WHY. rustc's --remap-path-prefix and clang's -ffile-prefix-map rewrite
# SOURCE paths; the linker still records every object file it consumed as an
# N_OSO stab, verbatim: `$HOME/.rustup/toolchains/.../libstd-*.rlib(...)` and
# the Cargo target directory under the checkout. Cargo's own `strip` profile
# is not a boundary: the 2026-09-08 f131b81b release compiled clean while
# rustc's `rust-objcopy` aborted with "Library not loaded: @rpath/libLLVM.dylib"
# — a warning, not an error — so voc, vc-start, scaffold-doctor, aicx, aicx-mcp
# and prview reached the Runtime Pack payload carrying 1–28 such stabs each and
# the payload gate refused the build. `strip -S` removes debugging symbol table
# entries only; code, exports and the indirect symbol table are untouched.
#
# WHY EXECUTABLES ONLY. MEASURED on that payload (31 Mach-O files): every host
# path sat in an executable under bin/. The 24 dylibs and bundles — the uv-seeded
# CPython's libpython and the wheels' extension modules — carry N_OSO stabs of
# their own CI builders, none naming this host. They were not linked here, the
# host `strip` has a recorded history of writing dylibs dyld refuses (see the
# Xcode-beta guard in scripts/build-vibecrafted-release.sh), and the hygiene
# scan still reads every byte of them afterwards. Nothing here narrows the gate.
#
# Files carrying a real (non ad-hoc) signature are left exactly as delivered:
# MEASURED on the same payload, the four Loctree binaries from npm arrive with
# a Developer ID seal and zero debugging records; `strip -S` made them 64–80
# bytes larger with an invalidated signature, for nothing. The gate decides
# whether such a file may ship.
#
# `find` does not follow symlinks and `-type f` excludes them, so nothing outside
# the roots is ever rewritten. Any strip failure fails the whole step.
strip_macho_debug_tree() {
  local root candidate kind output stripped=0 signed=0
  local -a roots=()
  [[ $# -gt 0 ]] \
    || macho_signing_die "strip_macho_debug_tree needs at least one root" || return 1
  for root in "$@"; do
    [[ -d "$root" ]] || macho_signing_die "missing tree: $root" || return 1
    roots+=("$(cd "$root" && pwd)")
  done
  while IFS= read -r -d '' candidate; do
    is_macho_executable "$candidate" || continue
    kind="$(macho_signature_kind "$candidate")"
    if [[ "$kind" == "signed" ]]; then
      signed=$((signed + 1))
      continue
    fi
    if ! output="$(/usr/bin/strip -S "$candidate" 2>&1)"; then
      printf '%s\n' "$output" >&2
      macho_signing_die "strip -S failed on $candidate" || return 1
    fi
    stripped=$((stripped + 1))
  done < <(find "${roots[@]}" -type f -print0)
  printf 'macho-strip: %d executable(s) stripped of debugging records, %d signed left as delivered\n' \
    "$stripped" "$signed"
}

sign_macho_tree() {
  local root="$1" excluded="${2:-}" candidate
  [[ -d "$root" ]] || macho_signing_die "missing tree: $root" || return 1
  [[ -n "${SIGNING_IDENTITY:-}" ]] \
    || macho_signing_die "SIGNING_IDENTITY is empty" || return 1
  while IFS= read -r -d '' candidate; do
    [[ -z "$excluded" || "$candidate" != "$excluded" ]] || continue
    if is_macho_file "$candidate"; then
      codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" \
        "${CODESIGN_KEYCHAIN_ARGS[@]}" "$candidate" \
        || macho_signing_die "could not sign ${candidate#"$root"/}" || return 1
    fi
  done < <(find "$root" -type f -print0)
}

verify_macho_tree() {
  local root="$1" require_macho="${2:-0}" candidate found=0
  [[ -d "$root" ]] || macho_signing_die "missing verification tree: $root" || return 1
  while IFS= read -r -d '' candidate; do
    if is_macho_file "$candidate"; then
      found=1
      codesign --verify --strict --verbose=2 "$candidate" \
        || macho_signing_die "invalid signature: ${candidate#"$root"/}" || return 1
    fi
  done < <(find "$root" -type f -print0)
  if [[ "$require_macho" == "1" && "$found" == "0" ]]; then
    macho_signing_die "tree contains no Mach-O payload: $root" || return 1
  fi
}

extract_runtime_pack_for_signature_verification() {
  local archive="$1" destination="$2"
  /usr/bin/python3 - "$archive" "$destination" <<'PY'
import os
import pathlib
import shutil
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
with tarfile.open(archive, "r:gz") as bundle:
    members = bundle.getmembers()
    if not members:
        raise SystemExit("Runtime Pack archive is empty")
    for member in members:
        relative = pathlib.PurePosixPath(member.name)
        if (
            relative.is_absolute()
            or not relative.parts
            or relative.parts[0] != "VibecraftedRuntime"
            or ".." in relative.parts
            or member.issym()
            or member.islnk()
            or not (member.isdir() or member.isfile())
        ):
            raise SystemExit(f"unsafe Runtime Pack member: {member.name}")
        target = destination.joinpath(*relative.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = bundle.extractfile(member)
        if source is None:
            raise SystemExit(f"cannot read Runtime Pack member: {member.name}")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output)
        os.chmod(target, member.mode & 0o777)
PY
}

cleanup_runtime_pack_macho_preflight_tree() {
  local work="$1" temporary_root="${TMPDIR:-/tmp}"
  temporary_root="${temporary_root%/}"
  # This directory is created by this helper under the OS temp root. Finder or
  # another metadata service may recreate a file while rm is finishing; that
  # must not replace the already-established verification result.
  case "$work" in
    "$temporary_root"/vibecrafted-macho-preflight.*) ;;
    *) macho_signing_die "refusing to clean non-preflight tree: $work" || return 1 ;;
  esac
  rm -rf -- "$work" >/dev/null 2>&1 || :
}

verify_runtime_pack_macho_signatures() {
  local archive="$1" work root temporary_root="${TMPDIR:-/tmp}"
  [[ -f "$archive" ]] \
    || macho_signing_die "missing Runtime Pack archive: $archive" || return 1
  temporary_root="${temporary_root%/}"
  work="$(mktemp -d "$temporary_root/vibecrafted-macho-preflight.XXXXXX")" \
    || macho_signing_die "could not create Runtime Pack preflight directory" || return 1
  root="$work/VibecraftedRuntime"
  if ! extract_runtime_pack_for_signature_verification "$archive" "$work"; then
    cleanup_runtime_pack_macho_preflight_tree "$work" || :
    macho_signing_die "could not safely extract $(basename "$archive")" || return 1
  fi
  if ! verify_macho_tree "$root" 1; then
    cleanup_runtime_pack_macho_preflight_tree "$work" || :
    return 1
  fi
  cleanup_runtime_pack_macho_preflight_tree "$work" || return 1
  return 0
}
