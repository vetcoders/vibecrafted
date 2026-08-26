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
