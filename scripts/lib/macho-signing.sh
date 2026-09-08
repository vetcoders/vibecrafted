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

# The strip that rewrites a Mach-O is the selected toolchain's, resolved once
# through xcrun so DEVELOPER_DIR decides — the release builder exports the
# verified stable Xcode there — never whichever `strip` the global
# xcode-select or the shell's PATH happens to name. MEASURED 2026-09-09 on the
# aa12980d App candidate's libvibecrafted_shell_ffi.dylib: Xcode 26.6 (17F113)
# and Xcode 27 beta (27A5228h) `strip -S` wrote byte-identical output; the
# resolution is printed so the release log can prove which one ran.
macho_strip_tool() {
  if [[ -n "${MACHO_STRIP_TOOL:-}" ]]; then
    printf '%s\n' "$MACHO_STRIP_TOOL"
    return 0
  fi
  local resolved
  resolved="$(xcrun --find strip 2>/dev/null || true)"
  [[ -n "$resolved" && -x "$resolved" ]] || resolved=/usr/bin/strip
  printf '%s\n' "$resolved"
}

is_macho_shared_library() {
  LC_ALL=C /usr/bin/file -b "$1" | grep -q 'Mach-O.*dynamically linked shared library'
}

# macho_public_shape <file> — the part of a Mach-O its consumers bind to:
# every exported symbol (name and address) and every load command with the
# names and paths it carries. `strip -S` may only shrink the symbol table's
# debugging entries; if this shape changes, the file is not the one that was
# linked and the step fails.
macho_public_shape() {
  nm -gU "$1" && otool -l "$1" | grep -E '^ *(cmd|name|path) '
}

# macho_library_loads <file> — bounded dyld acceptance for a shared library
# this build linked: dlopen it, RTLD_LOCAL, from a process of the library's
# architecture, with a hard alarm. `codesign --verify` and `dyld_info` read
# the file; only dyld itself decides whether it loads (the Xcode 27 beta strip
# once wrote chained-fixups dylibs that verified and did not load).
macho_library_loads() {
  local library="$1" output
  if ! output="$(/usr/bin/python3 - "$library" 2>&1 <<'PY'
import ctypes
import platform
import signal
import subprocess
import sys

library = sys.argv[1]
signal.alarm(20)
description = subprocess.run(
    ["/usr/bin/file", "-b", library], check=True, capture_output=True, text=True
).stdout
machine = platform.machine()
if machine not in description:
    raise SystemExit(
        f"cannot prove loadability from a {machine} process: {description.strip()}"
    )
ctypes.CDLL(library, mode=ctypes.RTLD_LOCAL)
print(f"loads in a {machine} process")
PY
  )"; then
    printf '%s\n' "$output" >&2
    return 1
  fi
}

# strip_macho_debug_tree [--shared-libraries] <root>...
#
# Remove debugging records — and only those — from every Mach-O EXECUTABLE
# under the given roots, and with --shared-libraries from every Mach-O dylib
# there as well. Runs on a staged, still unsigned payload, before the hygiene
# gate reads it and before any signature is spent.
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
# WHY EXECUTABLES BY DEFAULT. MEASURED on that payload (31 Mach-O files): every
# host path sat in an executable under bin/. The 24 dylibs and bundles — the
# uv-seeded CPython's libpython and the wheels' extension modules — carry N_OSO
# stabs of their own CI builders, none naming this host. They were not linked
# here, the host `strip` has a recorded history of writing dylibs dyld refuses
# (see the Xcode-beta guard in scripts/build-vibecrafted-release.sh), and the
# hygiene scan still reads every byte of them afterwards. Nothing here narrows
# the gate.
#
# WHY --shared-libraries FOR OWN LIBRARIES. MEASURED 2026-09-09 on the aa12980d
# App candidate: Contents/Frameworks/libvibecrafted_shell_ffi.dylib — the
# UniFFI library `cargo build -p vibecrafted-shell-ffi` links for the Swift
# host, embedded by Xcode without a strip — carried 17 N_OSO stabs naming the
# Cargo target directory (11) and the rustup sysroot (6), and nothing else
# named the host. It is a library this build linked, so this boundary owns it;
# the caller says so per root, and vendor roots never get the flag. A library
# is rewritten only if its public shape (exports, load commands) is unchanged
# afterwards and dyld actually loads it; either failure fails the step.
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
  local root candidate kind output strip_tool shape_before shape_after
  local stripped=0 signed=0 libraries=0 shared_libraries=0
  local -a roots=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --shared-libraries) shared_libraries=1; shift ;;
      --) shift; break ;;
      -*) macho_signing_die "strip_macho_debug_tree: unknown option $1" || return 1 ;;
      *) break ;;
    esac
  done
  [[ $# -gt 0 ]] \
    || macho_signing_die "strip_macho_debug_tree needs at least one root" || return 1
  for root in "$@"; do
    [[ -d "$root" ]] || macho_signing_die "missing tree: $root" || return 1
    roots+=("$(cd "$root" && pwd)")
  done
  strip_tool="$(macho_strip_tool)"
  [[ -x "$strip_tool" ]] \
    || macho_signing_die "strip is not executable: $strip_tool" || return 1
  printf 'macho-strip: tool %s\n' "$strip_tool"
  while IFS= read -r -d '' candidate; do
    if is_macho_executable "$candidate"; then
      kind=executable
    elif (( shared_libraries )) && is_macho_shared_library "$candidate"; then
      kind=library
    else
      continue
    fi
    if [[ "$(macho_signature_kind "$candidate")" == "signed" ]]; then
      signed=$((signed + 1))
      continue
    fi
    if [[ "$kind" == "library" ]]; then
      shape_before="$(macho_public_shape "$candidate")" \
        || macho_signing_die "cannot read the public shape of $candidate" || return 1
    fi
    if ! output="$("$strip_tool" -S "$candidate" 2>&1)"; then
      printf '%s\n' "$output" >&2
      macho_signing_die "strip -S failed on $candidate" || return 1
    fi
    if [[ "$kind" == "library" ]]; then
      shape_after="$(macho_public_shape "$candidate")" \
        || macho_signing_die "cannot read the public shape of $candidate after strip" || return 1
      [[ "$shape_before" == "$shape_after" ]] \
        || macho_signing_die "strip changed the exports or load commands of $candidate" || return 1
      macho_library_loads "$candidate" \
        || macho_signing_die "stripped library does not load: $candidate" || return 1
      libraries=$((libraries + 1))
    else
      stripped=$((stripped + 1))
    fi
  done < <(find "${roots[@]}" -type f -print0)
  if (( shared_libraries )); then
    printf 'macho-strip: %d executable(s) and %d shared library(ies) stripped of debugging records, %d signed left as delivered\n' \
      "$stripped" "$libraries" "$signed"
  else
    printf 'macho-strip: %d executable(s) stripped of debugging records, %d signed left as delivered\n' \
      "$stripped" "$signed"
  fi
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
