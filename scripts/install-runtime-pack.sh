#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack install failed: %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
pack="${VIBECRAFTED_RUNTIME_PACK:-}"
temporary=""
operation="install"
dry_run="0"
verify_only="0"
app_root=""
terminal_host=""
frame_helper=""
expected_source_revision=""
expected_terminal_revision=""
expected_frame_revision=""
expected_version=""
if [[ -f "$SCRIPT_DIR/VERSION" ]]; then
  expected_version="$(tr -d '[:space:]' < "$SCRIPT_DIR/VERSION")"
fi
expected_platform=""
expected_architecture=""

cleanup() {
  local status=$?
  local _attempt
  if [[ -n "$temporary" && -d "$temporary" ]]; then
    # Finder/metadata services can recreate .DS_Store while a large extracted
    # pack is being removed.  Cleanup is best-effort bookkeeping after the
    # installer has already emitted its result; it must neither turn a healthy
    # publication into exit 2 nor give up after the first transient ENOTEMPTY.
    for _attempt in 1 2 3; do
      if rm -rf -- "$temporary" 2>/dev/null; then
        break
      fi
      sleep 0.05
    done
    if [[ -d "$temporary" ]]; then
      printf 'Runtime Pack install warning: could not remove temporary directory: %s\n' \
        "$temporary" >&2
    fi
  fi
  return "$status"
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
    --verify-only)
      verify_only="1"
      shift
      ;;
    --app-root|--terminal-host|--frame-helper|--expected-source-revision|--expected-terminal-revision|--expected-frame-revision|--expected-version|--expected-platform|--expected-architecture)
      (($# >= 2)) || die "$1 requires a path or revision"
      case "$1" in
        --app-root) app_root="$2" ;;
        --terminal-host) terminal_host="$2" ;;
        --frame-helper) frame_helper="$2" ;;
        --expected-source-revision) expected_source_revision="$2" ;;
        --expected-terminal-revision) expected_terminal_revision="$2" ;;
        --expected-frame-revision) expected_frame_revision="$2" ;;
        --expected-version) expected_version="$2" ;;
        --expected-platform) expected_platform="$2" ;;
        --expected-architecture) expected_architecture="$2" ;;
      esac
      shift 2
      ;;
    --dry-run|-n)
      dry_run="1"
      shift
      ;;
    --help|-h)
      printf 'usage: %s [--pack <RuntimePack.tar.gz>] [--verify-only] [--expected-*-revision <sha>] [--app-root <Vibecrafted.app> --terminal-host <path> --frame-helper <path>] [--uninstall [--dry-run]]\n' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ "$operation" == "install" && "$dry_run" == "1" ]]; then
  die "--dry-run is only valid with --uninstall"
fi

if [[ -z "$expected_platform" ]]; then
  case "$(uname -s)" in
    Darwin) expected_platform="darwin-$(uname -m | sed 's/^aarch64$/arm64/; s/^x86_64$/x64/')" ;;
    Linux) expected_platform="linux-$(uname -m | sed 's/^aarch64$/arm64/; s/^x86_64$/x64/')" ;;
    *) die "unsupported Runtime Pack platform: $(uname -s)" ;;
  esac
fi
if [[ -z "$expected_architecture" ]]; then
  case "$(uname -m)" in
    x86_64|amd64)
      if [[ "$expected_platform" == darwin-* ]]; then
        expected_architecture="x64"
      else
        expected_architecture="x86_64"
      fi
      ;;
    arm64|aarch64) expected_architecture="arm64" ;;
    *) die "unsupported Runtime Pack architecture: $(uname -m)" ;;
  esac
fi
if [[ "$operation" == "uninstall" && "$verify_only" == "1" ]]; then
  die "--verify-only cannot be combined with --uninstall"
fi
helper_argument_count=0
[[ -n "$app_root" ]] && ((helper_argument_count += 1))
[[ -n "$terminal_host" ]] && ((helper_argument_count += 1))
[[ -n "$frame_helper" ]] && ((helper_argument_count += 1))
if ((helper_argument_count != 0 && helper_argument_count != 3)); then
  die "--app-root, --terminal-host and --frame-helper must be supplied together"
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
  shopt -s nullglob
  # The canonical platform is already a complete <os>-<architecture> slug.
  # Architecture remains an independent provenance check below; it is not a
  # second filename component.
  candidates=("$REPO_ROOT"/dist/Vibecrafted_RuntimePack_*-"$expected_platform".tar.gz)
  shopt -u nullglob
  if ((${#candidates[@]} == 1)); then
    pack="${candidates[0]}"
  elif ((${#candidates[@]} > 1)); then
    die "multiple Runtime Packs in dist; set VIBECRAFTED_RUNTIME_PACK explicitly"
  else
    die "no ${expected_platform}/${expected_architecture} Runtime Pack found; set VIBECRAFTED_RUNTIME_PACK to the prebuilt release asset"
  fi
fi

pack_name="${pack##*/}"
pack_parent="$(cd "$(dirname "$pack")" 2>/dev/null && pwd)" \
  || die "cannot resolve Runtime Pack path: $pack"
pack="$pack_parent/$pack_name"

payload_root=""

if [[ -f "$pack" && "$pack" == *.tar.gz ]]; then
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
  # Keep the extraction root hidden. Finder can otherwise discover the
  # short-lived directory and create .DS_Store while provenance is being
  # verified or while cleanup is removing the payload.
  temporary="$(mktemp -d "${TMPDIR:-/tmp}/.vibecrafted-runtime-pack.XXXXXX")"
  tar -tzf "$pack" >/dev/null \
    || die "Runtime Pack archive cannot be listed"
  archive_root=""
  while IFS= read -r member; do
    [[ -n "$member" ]] || die "Runtime Pack archive contains an empty member"
    case "$member" in
      /*|../*|*/../*|*/..) die "unsafe Runtime Pack archive member: $member" ;;
      .DS_Store|*/.DS_Store) die "Runtime Pack archive contains mutable host metadata: $member" ;;
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
  # Provenance binds every payload mode. Ambient umask must not rewrite those
  # signed bytes' metadata before the pack verifies itself.
  tar -xpzf "$pack" -C "$temporary" \
    || die "Runtime Pack archive extraction failed"
  # The signed archive listing above is the carrier truth. Any .DS_Store that
  # appears only after extraction was injected by the host and must not turn a
  # repeat install into a provenance failure.
  find "$temporary" -type f -name .DS_Store -delete
  payload_root="$temporary/$archive_root"
  if find "$payload_root" -type l -print -quit | grep -q .; then
    die "links are forbidden in extracted Runtime Pack archives"
  fi
else
  die "Runtime Pack must be the canonical .tar.gz carrier: $pack"
fi

[[ -d "$payload_root" ]] || die "runtime payload missing: $payload_root"
payload_version_file="$payload_root/VERSION"
[[ -f "$payload_version_file" ]] || die "Runtime Pack version truth is missing: $payload_version_file"
payload_version="$(tr -d '[:space:]' < "$payload_version_file")"
[[ -n "$payload_version" ]] || die "Runtime Pack version truth is empty: $payload_version_file"
if [[ -z "$expected_version" ]]; then
  expected_version="$payload_version"
fi
pack_python="$payload_root/bin/python3"
pack_installer="$payload_root/scripts/vetcoders_install.py"
[[ -x "$pack_python" ]] || die "Runtime Pack Python missing: $pack_python"
[[ -f "$pack_installer" ]] || die "Runtime Pack installer missing: $pack_installer"
contract_arguments=(
  -m vibecrafted_core.runtime_pack_contract verify
  --root "$payload_root"
  --carrier-basename "$pack_name"
)
[[ -n "$expected_source_revision" ]] \
  && contract_arguments+=(--expected-source-revision "$expected_source_revision")
[[ -n "$expected_terminal_revision" ]] \
  && contract_arguments+=(--expected-terminal-revision "$expected_terminal_revision")
[[ -n "$expected_frame_revision" ]] \
  && contract_arguments+=(--expected-frame-revision "$expected_frame_revision")
contract_arguments+=(
  --expected-version "$expected_version"
  --expected-platform "$expected_platform"
  --expected-architecture "$expected_architecture"
)
contract_output="$(PYTHONPATH="$payload_root/vibecrafted-core" \
  "$pack_python" "${contract_arguments[@]}")" \
  || die "Runtime Pack internal provenance verification failed"
if [[ "$verify_only" == "1" ]]; then
  printf '%s\n' "$contract_output"
  exit 0
fi

if [[ "$operation" == "uninstall" ]]; then
  arguments=(runtime-uninstall)
  [[ "$dry_run" == "1" ]] && arguments+=(--dry-run)
else
  arguments=(runtime-install --payload-root "$payload_root")
fi
if [[ "$operation" == "install" && -n "$app_root" ]]; then
  app_root="$(cd "$app_root" && pwd -P)" \
    || die "cannot resolve Vibecrafted.app root: $app_root"
  terminal_host="$(cd "$(dirname "$terminal_host")" && pwd -P)/${terminal_host##*/}" \
    || die "cannot resolve bundled terminal host"
  frame_helper="$(cd "$(dirname "$frame_helper")" && pwd -P)/${frame_helper##*/}" \
    || die "cannot resolve bundled vc-frame helper"
  [[ -x "$terminal_host" ]] || die "bundled terminal host missing: $terminal_host"
  [[ -x "$frame_helper" ]] || die "bundled vc-frame helper missing: $frame_helper"
  # Same compilation product, signature-agnostic: byte equality stopped being
  # possible when the pack's binaries gained their own Developer ID signatures
  # (the App helper is bundle-signed, the pack copy bare-signed — different
  # CodeDirectories, same code), so the contract module compares LC_UUID when
  # the bytes differ. A non-Mach-O impostor has no UUID and fails closed.
  PYTHONPATH="$payload_root/vibecrafted-core" "$pack_python" \
    -m vibecrafted_core.runtime_pack_contract helpers-agree \
    --app-copy "$terminal_host" --pack-copy "$payload_root/bin/vc-terminal" \
    || die "App terminal helper disagrees with the signed Runtime Pack"
  PYTHONPATH="$payload_root/vibecrafted-core" "$pack_python" \
    -m vibecrafted_core.runtime_pack_contract helpers-agree \
    --app-copy "$frame_helper" --pack-copy "$payload_root/libexec/vc-frame" \
    || die "App vc-frame helper disagrees with the signed Runtime Pack"
  arguments+=(
    --app-root "$app_root"
  )
  if [[ -n "$terminal_host" ]]; then
    arguments+=(--terminal-host "$terminal_host")
  fi
fi

if [[ -n "$temporary" ]]; then
  "$pack_python" "$pack_installer" "${arguments[@]}"
  exit 0
fi
exec "$pack_python" "$pack_installer" "${arguments[@]}"
