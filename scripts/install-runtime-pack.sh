#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack install failed: %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
pack="${VIBECRAFTED_RUNTIME_PACK:-}"
temporary=""
operation="install"
dry_run="0"

cleanup() {
  if [[ -n "$temporary" && -d "$temporary" ]]; then
    rm -rf -- "$temporary"
  fi
}
trap cleanup EXIT INT TERM HUP

while (($#)); do
  case "$1" in
    --pack)
      (($# >= 2)) || die "--pack requires a path"
      pack="$2"
      shift 2
      ;;
    --uninstall)
      operation="uninstall"
      shift
      ;;
    --dry-run|-n)
      dry_run="1"
      shift
      ;;
    --help|-h)
      printf 'usage: %s [--pack <Runtime Pack directory, Vibecrafted.app, or .tar.gz>] [--uninstall [--dry-run]]\n' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ "$operation" == "install" && "$dry_run" == "1" ]]; then
  die "--dry-run is only valid with --uninstall"
fi

if [[ "$operation" == "uninstall" ]]; then
  runtime_home="${VIBECRAFTED_RUNTIME_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/vibecrafted}"
  receipt="$runtime_home/install-receipt.json"
  if [[ ! -f "$receipt" ]]; then
    printf '{"schema":"vibecrafted.runtime-uninstall-result.v1","status":"absent"}\n'
    exit 0
  fi
  [[ -d "$runtime_home" ]] || die "receipt exists outside a runtime home: $receipt"
  runtime_home="$(cd "$runtime_home" && pwd -P)"
  current="$runtime_home/tools/vibecrafted-current"
  if [[ -d "$current" ]]; then
    generation="$(cd "$current" && pwd -P)"
    case "$generation" in
      "$runtime_home"/releases/*) ;;
      *) die "installed Runtime Pack projection escapes releases: $generation" ;;
    esac
    pack_python="$generation/bin/python3"
    pack_installer="$generation/scripts/vetcoders_install.py"
    [[ -x "$pack_python" ]] || die "installed Runtime Pack Python missing: $pack_python"
    [[ -f "$pack_installer" ]] || die "installed Runtime Pack installer missing: $pack_installer"
    arguments=(runtime-uninstall)
    [[ "$dry_run" == "1" ]] && arguments+=(--dry-run)
    exec "$pack_python" "$pack_installer" "${arguments[@]}"
  fi
  [[ -n "$pack" ]] \
    || die "installed Runtime Pack projection is missing; pass --pack to recover from the receipt"
fi

if [[ -z "$pack" ]]; then
  if [[ -d "$REPO_ROOT/dist/Vibecrafted.app/Contents/Resources/runtime" ]]; then
    pack="$REPO_ROOT/dist/Vibecrafted.app"
  else
    shopt -s nullglob
    candidates=("$REPO_ROOT"/dist/Vibecrafted_RuntimePack_*.tar.gz)
    shopt -u nullglob
    if ((${#candidates[@]} == 1)); then
      pack="${candidates[0]}"
    elif ((${#candidates[@]} > 1)); then
      die "multiple Runtime Packs in dist; set VIBECRAFTED_RUNTIME_PACK explicitly"
    else
      die "no Runtime Pack found; set VIBECRAFTED_RUNTIME_PACK or run 'make runtime-pack'"
    fi
  fi
fi

pack_name="${pack##*/}"
pack_parent="$(cd "$(dirname "$pack")" 2>/dev/null && pwd)" \
  || die "cannot resolve Runtime Pack path: $pack"
pack="$pack_parent/$pack_name"

app_root=""
terminal_host=""
frame_helper=""
payload_root=""

if [[ -d "$pack" ]]; then
  if [[ "$pack" == *.app ]]; then
    app_root="$pack"
    payload_root="$pack/Contents/Resources/runtime"
    terminal_host="$pack/Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
    frame_helper="$pack/Contents/Helpers/vc-frame"
  else
    payload_root="$pack"
  fi
elif [[ -f "$pack" && "$pack" == *.tar.gz ]]; then
  command -v tar >/dev/null 2>&1 \
    || die "tar is required to extract a Runtime Pack archive"
  checksum="$pack.sha256"
  signature="$pack.sig"
  public_key="${VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY:-$REPO_ROOT/vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub}"
  [[ -f "$checksum" ]] || die "Runtime Pack checksum is missing: $checksum"
  [[ -f "$signature" ]] || die "Runtime Pack signature is missing: $signature"
  [[ -f "$public_key" ]] || die "trusted Runtime Pack public key is missing: $public_key"
  if command -v shasum >/dev/null 2>&1; then
    (cd "$(dirname "$pack")" && shasum -a 256 -c "$(basename "$checksum")" >/dev/null) \
      || die "Runtime Pack checksum mismatch"
  elif command -v sha256sum >/dev/null 2>&1; then
    (cd "$(dirname "$pack")" && sha256sum -c "$(basename "$checksum")" >/dev/null) \
      || die "Runtime Pack checksum mismatch"
  else
    die "cannot verify Runtime Pack checksum (shasum/sha256sum missing)"
  fi
  command -v openssl >/dev/null 2>&1 \
    || die "openssl is required to verify the Runtime Pack signature"
  openssl dgst -sha256 -verify "$public_key" -signature "$signature" "$pack" >/dev/null 2>&1 \
    || die "Runtime Pack signature verification failed"
  temporary="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-runtime-pack.XXXXXX")"
  tar -tzf "$pack" >/dev/null \
    || die "Runtime Pack archive cannot be listed"
  archive_root=""
  while IFS= read -r member; do
    [[ -n "$member" ]] || die "Runtime Pack archive contains an empty member"
    case "$member" in
      /*|../*|*/../*|*/..) die "unsafe Runtime Pack archive member: $member" ;;
    esac
    member_root="${member%%/*}"
    [[ -n "$member_root" ]] || die "Runtime Pack archive has no root directory"
    if [[ -z "$archive_root" ]]; then
      archive_root="$member_root"
    elif [[ "$member_root" != "$archive_root" ]]; then
      die "Runtime Pack archive must contain one root directory"
    fi
  done < <(tar -tzf "$pack")
  [[ -n "$archive_root" ]] || die "Runtime Pack archive is empty"
  while IFS= read -r mode _rest; do
    case "${mode:0:1}" in
      -|d) ;;
      *) die "links/devices are forbidden in Runtime Pack archives" ;;
    esac
  done < <(tar -tvzf "$pack")
  tar -xzf "$pack" -C "$temporary" \
    || die "Runtime Pack archive extraction failed"
  payload_root="$temporary/$archive_root"
  if find "$payload_root" -type l -print -quit | grep -q .; then
    die "links are forbidden in extracted Runtime Pack archives"
  fi
else
  die "Runtime Pack is not a directory, app, or .tar.gz archive: $pack"
fi

[[ -d "$payload_root" ]] || die "runtime payload missing: $payload_root"
pack_python="$payload_root/bin/python3"
pack_installer="$payload_root/scripts/vetcoders_install.py"
[[ -x "$pack_python" ]] || die "Runtime Pack Python missing: $pack_python"
[[ -f "$pack_installer" ]] || die "Runtime Pack installer missing: $pack_installer"

if [[ "$operation" == "uninstall" ]]; then
  arguments=(runtime-uninstall)
  [[ "$dry_run" == "1" ]] && arguments+=(--dry-run)
else
  arguments=(runtime-install --payload-root "$payload_root")
fi
if [[ "$operation" == "install" && -n "$app_root" ]]; then
  [[ -x "$terminal_host" ]] || die "bundled terminal host missing: $terminal_host"
  [[ -x "$frame_helper" ]] || die "bundled vc-frame helper missing: $frame_helper"
  arguments+=(
    --app-root "$app_root"
    --terminal-host "$terminal_host"
    --frame-helper "$frame_helper"
  )
fi

if [[ -n "$temporary" ]]; then
  "$pack_python" "$pack_installer" "${arguments[@]}"
  exit 0
fi
exec "$pack_python" "$pack_installer" "${arguments[@]}"
