#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack install failed: %s\n' "$*" >&2; exit 1; }

_stat_uid() {
  local uid
  uid="$(stat -f %u "$1" 2>/dev/null || stat -c %u "$1")" || return 1
  printf '%s\n' "$uid"
}

_stat_mode() {
  local mode
  mode="$(stat -f %Lp "$1" 2>/dev/null || stat -c %a "$1")" || return 1
  printf '%s\n' "$mode"
}

_world_writable() {
  local mode
  mode="$(_stat_mode "$1")" || return 1
  case "$mode" in
    *[2367]) return 0 ;;
    *) return 1 ;;
  esac
}

_assert_physical_dir() {
  local path="$1"
  local label="$2"
  [[ ! -L "$path" ]] || die "$label is a symlink: $path"
  [[ -d "$path" ]] || die "$label is not a directory: $path"
}

_assert_private_owned_dir() {
  local path="$1"
  local label="$2"
  local uid mode
  _assert_physical_dir "$path" "$label"
  uid="$(_stat_uid "$path")" || die "cannot stat $label: $path"
  [[ "$uid" == "$EUID" ]] || die "$label is not privately owned: $path"
  mode="$(_stat_mode "$path")" || die "cannot read mode of $label: $path"
  [[ "$mode" == "700" || "$mode" == "0700" ]] || die "$label is not private (mode $mode): $path"
}

_ensure_rescue_cache_root() {
  local cache_home parent
  cache_home="${XDG_CACHE_HOME:-${HOME:?HOME is required for Runtime Pack rescue staging}/.cache}"
  [[ -n "$cache_home" ]] || die "rescue staging cache home is empty"
  if [[ -e "$cache_home" ]]; then
    [[ -d "$cache_home" ]] || die "rescue staging cache home is not a directory: $cache_home"
    if _world_writable "$cache_home"; then
      die "rescue staging refuses a world-writable cache home: $cache_home"
    fi
  else
    mkdir -m 700 -- "$cache_home" \
      || die "cannot create rescue staging cache home: $cache_home"
  fi
  parent="$cache_home/vibecrafted"
  if [[ -L "$parent" ]]; then
    die "rescue staging parent is a symlink: $parent"
  fi
  if [[ -e "$parent" ]]; then
    [[ -d "$parent" ]] || die "rescue staging parent is not a directory: $parent"
    [[ "$(_stat_uid "$parent")" == "$EUID" ]] \
      || die "rescue staging parent is not owned: $parent"
  else
    mkdir -m 755 -- "$parent" || die "cannot create rescue staging parent: $parent"
  fi
  rescue_cache_root="$parent/runtime-pack-rescue"
  if [[ -L "$rescue_cache_root" ]]; then
    die "rescue staging root is a symlink: $rescue_cache_root"
  fi
  if [[ -e "$rescue_cache_root" ]]; then
    _assert_private_owned_dir "$rescue_cache_root" "rescue staging root"
  else
    mkdir -m 700 -- "$rescue_cache_root" \
      || die "cannot create rescue staging root: $rescue_cache_root"
    _assert_private_owned_dir "$rescue_cache_root" "rescue staging root"
  fi
  rescue_cache_root="$(cd "$rescue_cache_root" && pwd -P)" \
    || die "cannot resolve rescue staging root"
}

_acquire_rescue_lock() {
  local lock="$1"
  if [[ -L "$lock" ]]; then
    die "rescue staging lock is a symlink: $lock"
  fi
  if ! mkdir -- "$lock" 2>/dev/null; then
    die "concurrent Runtime Pack rescue is already in progress for this archive"
  fi
  rescue_lock="$lock"
}

_write_rescue_identity() {
  local identity="$1"
  local sha="$2"
  local root_name="$3"
  printf 'schema=vibecrafted.runtime-pack-rescue-extract.v1\narchive_sha256=%s\narchive_root=%s\nuid=%s\n' \
    "$sha" "$root_name" "$EUID" > "$identity"
  chmod 600 "$identity"
}

_verify_rescue_identity() {
  local identity="$1"
  local sha="$2"
  local root_name="$3"
  local schema stored_sha stored_root stored_uid
  [[ ! -L "$identity" ]] || die "rescue extract identity is a symlink: $identity"
  [[ -f "$identity" ]] || die "retained Runtime Pack rescue extract is missing identity"
  [[ "$(_stat_uid "$identity")" == "$EUID" ]] \
    || die "rescue extract identity is not privately owned: $identity"
  schema="$(awk -F= '/^schema=/{print $2}' "$identity")"
  stored_sha="$(awk -F= '/^archive_sha256=/{print $2}' "$identity")"
  stored_root="$(awk -F= '/^archive_root=/{print $2}' "$identity")"
  stored_uid="$(awk -F= '/^uid=/{print $2}' "$identity")"
  [[ "$schema" == "vibecrafted.runtime-pack-rescue-extract.v1" ]] \
    || die "retained Runtime Pack rescue extract identity is stale or tampered"
  [[ "$stored_sha" == "$sha" ]] \
    || die "retained Runtime Pack rescue extract no longer matches the signed archive"
  [[ "$stored_root" == "$root_name" ]] \
    || die "retained Runtime Pack rescue extract root does not match the signed archive"
  [[ "$stored_uid" == "$EUID" ]] \
    || die "retained Runtime Pack rescue extract identity owner changed"
}

_write_rescue_manifest() {
  local payload="$1"
  local dest="$2"
  (
    cd "$payload" || exit 1
    if command -v shasum >/dev/null 2>&1; then
      find . -type f ! -name .DS_Store -print | LC_ALL=C sort | while IFS= read -r rel; do
        shasum -a 256 "$rel"
      done
    else
      find . -type f ! -name .DS_Store -print | LC_ALL=C sort | while IFS= read -r rel; do
        sha256sum "$rel"
      done
    fi
  ) > "$dest"
  chmod 600 "$dest"
}

_verify_rescue_manifest() {
  local payload="$1"
  local manifest="$2"
  local expected actual tmp
  [[ ! -L "$manifest" ]] || die "rescue extract manifest is a symlink: $manifest"
  [[ -f "$manifest" ]] || die "retained Runtime Pack rescue extract is missing its manifest"
  [[ "$(_stat_uid "$manifest")" == "$EUID" ]] \
    || die "rescue extract manifest is not privately owned: $manifest"
  expected="$(cat "$manifest")"
  tmp="$(mktemp "${TMPDIR:-/tmp}/.vibecrafted-rescue-manifest.XXXXXX")"
  _write_rescue_manifest "$payload" "$tmp"
  actual="$(cat "$tmp")"
  rm -f -- "$tmp"
  [[ "$expected" == "$actual" ]] \
    || die "retained Runtime Pack rescue extract is stale or tampered"
}

_extract_signed_archive_into() {
  local dest="$1"
  tar -xpzf "$pack" -C "$dest" \
    || die "Runtime Pack archive extraction failed"
  find "$dest" -type f -name .DS_Store -delete
}

_prepare_rescue_extract() {
  local sha="$1"
  local root_name="$2"
  local extract identity manifest lock
  [[ "$sha" =~ ^[0-9a-f]{64}$ ]] || die "Runtime Pack digest is not a sha256"
  _ensure_rescue_cache_root
  extract="$rescue_cache_root/$sha"
  identity="$rescue_cache_root/${sha}.identity"
  manifest="$rescue_cache_root/${sha}.manifest"
  lock="$rescue_cache_root/${sha}.lock"
  _acquire_rescue_lock "$lock"
  rescue_staging="$extract"
  rescue_identity="$identity"
  rescue_manifest="$manifest"
  if [[ -L "$extract" ]]; then
    die "retained Runtime Pack rescue extract is a symlink: $extract"
  fi
  if [[ -e "$extract" ]]; then
    _assert_private_owned_dir "$extract" "retained Runtime Pack rescue extract"
    if [[ -f "$identity" && -f "$manifest" ]]; then
      _verify_rescue_identity "$identity" "$sha" "$root_name"
      [[ -d "$extract/$root_name" && ! -L "$extract/$root_name" ]] \
        || die "retained Runtime Pack rescue payload is missing or a symlink"
      if find "$extract/$root_name" -type l -print -quit | grep -q .; then
        die "links are forbidden in retained Runtime Pack rescue extracts"
      fi
      find "$extract/$root_name" -type f -name .DS_Store -delete
      _verify_rescue_manifest "$extract/$root_name" "$manifest"
      return 0
    fi
    if [[ -f "$identity" ]]; then
      _verify_rescue_identity "$identity" "$sha" "$root_name"
    fi
    rm -rf -- "$extract"
    rm -f -- "$identity" "$manifest"
  fi
  mkdir -m 700 -- "$extract" || die "cannot create rescue extract: $extract"
  _assert_private_owned_dir "$extract" "rescue extract"
  _extract_signed_archive_into "$extract"
  [[ -d "$extract/$root_name" && ! -L "$extract/$root_name" ]] \
    || die "runtime payload missing: $extract/$root_name"
  if find "$extract/$root_name" -type l -print -quit | grep -q .; then
    die "links are forbidden in extracted Runtime Pack archives"
  fi
  _write_rescue_identity "$identity" "$sha" "$root_name"
  _write_rescue_manifest "$extract/$root_name" "$manifest"
}

_release_rescue_extract() {
  if [[ -n "${rescue_staging:-}" && -d "$rescue_staging" && ! -L "$rescue_staging" ]]; then
    rm -rf -- "$rescue_staging"
  fi
  if [[ -n "${rescue_identity:-}" && -f "$rescue_identity" && ! -L "$rescue_identity" ]]; then
    rm -f -- "$rescue_identity"
  fi
  if [[ -n "${rescue_manifest:-}" && -f "$rescue_manifest" && ! -L "$rescue_manifest" ]]; then
    rm -f -- "$rescue_manifest"
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
pack="${VIBECRAFTED_RUNTIME_PACK:-}"
temporary=""
rescue_lock=""
rescue_staging=""
rescue_identity=""
rescue_manifest=""
rescue_cache_root=""
installer_child_pid=""
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
resolve_preference=""
preference_current_sha256=""
preference_incoming_sha256=""
preference_path=""
rescue="0"
rescue_plan="0"
rescue_apply="0"
plan_digest=""

cleanup() {
  local status=$?
  local _attempt
  if [[ -n "${rescue_lock:-}" && -d "$rescue_lock" ]]; then
    rmdir -- "$rescue_lock" 2>/dev/null || true
  fi
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
terminate_installer_child() {
  local signal="$1"
  local attempt

  # The App owns this bootstrap process, while this bootstrap owns precisely
  # one Python installer.  Do not let a UI timeout reap only Bash and leave the
  # Python transaction holding its publication lease after the App has made the
  # repair control available again.  This is deliberately a PID, not a broad
  # process-group kill: helper/service processes outside this invocation are
  # never ours to signal.
  if [[ -n "$installer_child_pid" ]] && kill -0 "$installer_child_pid" 2>/dev/null; then
    kill -"$signal" "$installer_child_pid" 2>/dev/null || true
    for attempt in {1..10}; do
      kill -0 "$installer_child_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$installer_child_pid" 2>/dev/null; then
      kill -KILL "$installer_child_pid" 2>/dev/null || true
    fi
    wait "$installer_child_pid" 2>/dev/null || true
  fi
  installer_child_pid=""
  exit 143
}

trap cleanup EXIT
trap 'terminate_installer_child TERM' TERM
trap 'terminate_installer_child INT' INT
trap 'terminate_installer_child HUP' HUP

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
    --app-root|--terminal-host|--frame-helper|--expected-source-revision|--expected-terminal-revision|--expected-frame-revision|--expected-version|--expected-platform|--expected-architecture|--resolve-preference|--preference-current-sha256|--preference-incoming-sha256|--preference-path)
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
        --resolve-preference) resolve_preference="$2" ;;
        --preference-current-sha256) preference_current_sha256="$2" ;;
        --preference-incoming-sha256) preference_incoming_sha256="$2" ;;
        --preference-path) preference_path="$2" ;;
      esac
      shift 2
      ;;
    --rescue)
      rescue="1"
      shift
      ;;
    --plan)
      rescue_plan="1"
      shift
      ;;
    --apply)
      rescue_apply="1"
      shift
      ;;
    --plan-digest)
      (($# >= 2)) || die "--plan-digest requires a hex digest"
      plan_digest="$2"
      shift 2
      ;;
    --dry-run|-n)
      dry_run="1"
      shift
      ;;
    --help|-h)
      printf 'usage: %s [--pack <RuntimePack.tar.gz>] [--verify-only] [--expected-*-revision <sha>] [--app-root <Vibecrafted.app> --terminal-host <path> --frame-helper <path>] [--resolve-preference keep-current|use-incoming --preference-current-sha256 <hex> --preference-incoming-sha256 <hex>] [--rescue --plan|--apply [--plan-digest <hex>]] [--uninstall [--dry-run]]\n' "$0"
      printf 'Rescue: explicit plan/apply when historical rollback bytes are missing. Plan and apply reuse a private extract bound to the signed archive digest so the same verified pack keeps the same payload-root. If this pack installer lacks --rescue, bootstrap with a source installer that includes it against the verified --payload-root. Do not rewrite the signed payload.\n'
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
if [[ "$rescue" != "1" && ( "$rescue_plan" == "1" || "$rescue_apply" == "1" || -n "$plan_digest" ) ]]; then
  die "--plan, --apply, and --plan-digest are only valid with --rescue"
fi
if [[ "$rescue" == "1" && "$operation" == "uninstall" ]]; then
  die "--rescue cannot be combined with --uninstall"
fi
if [[ "$rescue" == "1" && "$rescue_plan" == "1" && "$rescue_apply" == "1" ]]; then
  die "--rescue requires exactly one of --plan or --apply"
fi
if [[ "$rescue" == "1" && "$rescue_plan" != "1" && "$rescue_apply" != "1" ]]; then
  die "explicit rescue requires --plan or --apply"
fi
if [[ "$rescue_apply" == "1" && -z "$plan_digest" ]]; then
  die "--rescue --apply requires --plan-digest"
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
  # An explicit --pack / VIBECRAFTED_RUNTIME_PACK stays authoritative and never
  # reaches this branch: deliberately installing another signed generation is a
  # supported act. Only the implicit case asks the producer what it built.
  #
  # The record's owner is scripts/lib/runtime-pack-selection.sh. It is absent
  # from the copy of this script that ships inside Vibecrafted.app, which always
  # passes --pack; no owner means no record, and the legacy path below stands.
  selection_library="$SCRIPT_DIR/lib/runtime-pack-selection.sh"
  if [[ -f "$selection_library" ]]; then
    # shellcheck source=/dev/null
    . "$selection_library"
    selection_status=0
    runtime_pack_selection_read "$REPO_ROOT" \
      "$expected_platform" "$expected_architecture" || selection_status=$?
    case "$selection_status" in
      0)
        pack="$RUNTIME_PACK_SELECTION_PACK"
        # Feed the producer's captured identity into the checks that already
        # exist. This does not add trust: the checksum, detached signature and
        # internal provenance gates below are unchanged, and now simply know
        # which revisions the selected bytes are required to claim.
        if [[ -z "$expected_version" ]]; then
          expected_version="$RUNTIME_PACK_SELECTION_VERSION"
        fi
        if [[ -z "$expected_source_revision" ]]; then
          expected_source_revision="$RUNTIME_PACK_SELECTION_SOURCE_REVISION"
        fi
        if [[ -z "$expected_terminal_revision" ]]; then
          expected_terminal_revision="$RUNTIME_PACK_SELECTION_TERMINAL_REVISION"
        fi
        if [[ -z "$expected_frame_revision" ]]; then
          expected_frame_revision="$RUNTIME_PACK_SELECTION_FRAME_REVISION"
        fi
        # A record produced from some other checkout, or from this one before it
        # moved, no longer describes "the pack you just built here". Say so;
        # never resolve the disagreement by picking a different archive.
        if command -v git >/dev/null 2>&1 \
          && current_source_revision="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)" \
          && [[ -n "$current_source_revision" ]] \
          && [[ "$current_source_revision" != "$RUNTIME_PACK_SELECTION_SOURCE_REVISION" ]]; then
          die "the recorded Runtime Pack was built from ${RUNTIME_PACK_SELECTION_SOURCE_REVISION:0:8}, but this source is at ${current_source_revision:0:8}; rebuild it or pass an explicit pack"
        fi
        ;;
      1) ;;
      *) die "${RUNTIME_PACK_SELECTION_ERROR:-the Runtime Pack build selection record is unusable}" ;;
    esac
  fi
fi

if [[ -z "$pack" ]]; then
  shopt -s nullglob
  # No build-selection record: the pre-handoff behaviour, retained for a repo
  # holding exactly one prebuilt release asset. The canonical platform is
  # already a complete <os>-<architecture> slug. Architecture remains an
  # independent provenance check below; it is not a second filename component.
  #
  # Ambiguity is still a refusal. Historical packs are legitimate artifacts, and
  # the answer to many of them is a producer that says which one it made — never
  # newest mtime, glob order, or deleting the others.
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
  if command -v shasum >/dev/null 2>&1; then
    archive_sha256="$(shasum -a 256 "$pack" | awk '{print $1}')"
  else
    archive_sha256="$(sha256sum "$pack" | awk '{print $1}')"
  fi
  [[ "$archive_sha256" =~ ^[0-9a-f]{64}$ ]] \
    || die "Runtime Pack digest is not a sha256"
  checksum_hex="$(awk '{print $1; exit}' "$checksum")"
  [[ "$archive_sha256" == "$checksum_hex" ]] \
    || die "Runtime Pack checksum digest mismatch"
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
  # Rescue plan/apply/resume pin payload_root in the owner digest. Ephemeral
  # mktemp paths cannot carry that identity across two wrapper invocations.
  # Non-rescue installs keep the hidden one-shot extract and still delete it.
  if [[ "$rescue" == "1" ]]; then
    _prepare_rescue_extract "$archive_sha256" "$archive_root"
    payload_root="$rescue_staging/$archive_root"
  else
    # Keep the extraction root hidden. Finder can otherwise discover the
    # short-lived directory and create .DS_Store while provenance is being
    # verified or while cleanup is removing the payload.
    temporary="$(mktemp -d "${TMPDIR:-/tmp}/.vibecrafted-runtime-pack.XXXXXX")"
    # Provenance binds every payload mode. Ambient umask must not rewrite those
    # signed bytes' metadata before the pack verifies itself.
    _extract_signed_archive_into "$temporary"
    payload_root="$temporary/$archive_root"
  fi
  if find "$payload_root" -type l -print -quit | grep -q .; then
    die "links are forbidden in extracted Runtime Pack archives"
  fi
else
  die "Runtime Pack must be the canonical .tar.gz carrier: $pack"
fi

[[ -d "$payload_root" && ! -L "$payload_root" ]] \
  || die "runtime payload missing: $payload_root"
payload_root="$(cd "$payload_root" && pwd -P)" \
  || die "cannot resolve runtime payload: $payload_root"
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

installer_entry="$pack_installer"
if [[ "$rescue" == "1" ]]; then
  if ! grep -Fq 'RUNTIME_RESCUE_PLAN_SCHEMA' "$pack_installer" \
    || ! grep -Fq -- '--rescue' "$pack_installer"; then
    source_installer="$SCRIPT_DIR/vetcoders_install.py"
    if [[ -f "$source_installer" ]] \
      && grep -Fq 'RUNTIME_RESCUE_PLAN_SCHEMA' "$source_installer" \
      && grep -Fq -- '--rescue' "$source_installer"; then
      installer_entry="$source_installer"
      printf 'Runtime Pack installer lacks --rescue; bootstrapping with source installer %s against the verified payload-root. Do not rewrite the signed payload.\n' \
        "$source_installer" >&2
    else
      die "This Runtime Pack installer does not support --rescue. Bootstrap with a source/version whose installer includes runtime-install --rescue (compatibility: tests/tui/test_runtime_pack_rescue.py) targeting this verified pack via --payload-root. Do not rewrite the signed payload."
    fi
  fi
fi
if [[ "$operation" == "uninstall" ]]; then
  arguments=(runtime-uninstall)
  [[ "$dry_run" == "1" ]] && arguments+=(--dry-run)
else
  arguments=(runtime-install --payload-root "$payload_root")
fi
if [[ "$rescue" == "1" ]]; then
  arguments+=(--rescue)
  [[ "$rescue_plan" == "1" ]] && arguments+=(--plan)
  [[ "$rescue_apply" == "1" ]] && arguments+=(--apply)
  [[ -n "$plan_digest" ]] && arguments+=(--plan-digest "$plan_digest")
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
  pack_terminal_host="$payload_root/libexec/vc-terminal"
  [[ -x "$pack_terminal_host" ]] || die "Runtime Pack native terminal host missing: $pack_terminal_host"
  # Same compilation product, signature-agnostic: byte equality stopped being
  # possible when the pack's binaries gained their own Developer ID signatures
  # (the App helper is bundle-signed, the pack copy bare-signed — different
  # CodeDirectories, same code), so the contract module compares LC_UUID when
  # the bytes differ. A non-Mach-O impostor has no UUID and fails closed.
  PYTHONPATH="$payload_root/vibecrafted-core" "$pack_python" \
    -m vibecrafted_core.runtime_pack_contract helpers-agree \
    --app-copy "$terminal_host" --pack-copy "${pack_terminal_host}" \
    || die "App terminal helper disagrees with the signed Runtime Pack"
  PYTHONPATH="$payload_root/vibecrafted-core" "$pack_python" \
    -m vibecrafted_core.runtime_pack_contract helpers-agree \
    --app-copy "$frame_helper" --pack-copy "$payload_root/libexec/vc-frame" \
    || die "App vc-frame helper disagrees with the signed Runtime Pack"
  arguments+=(
    --app-root "$app_root"
  )
  # helpers-agree already proved App copies match the signed pack. Do not
  # forward --terminal-host/--frame-helper into the installer — that would
  # replace pack Mach-O bytes with the bundle-signed helper.
fi
if [[ "$operation" == "install" && -n "$resolve_preference" ]]; then
  case "$resolve_preference" in
    keep-current|use-incoming) ;;
    *) die "unsupported --resolve-preference: $resolve_preference" ;;
  esac
  [[ -n "$preference_current_sha256" && -n "$preference_incoming_sha256" ]] \
    || die "--resolve-preference requires bound current and incoming hashes"
  arguments+=(
    --resolve-preference "$resolve_preference"
    --preference-current-sha256 "$preference_current_sha256"
    --preference-incoming-sha256 "$preference_incoming_sha256"
  )
  [[ -n "$preference_path" ]] && arguments+=(--preference-path "$preference_path")
fi

if [[ -n "$temporary" || -n "$rescue_staging" ]]; then
  "$pack_python" "$installer_entry" "${arguments[@]}" &
  installer_child_pid="$!"
  wait "$installer_child_pid"
  installer_status=$?
  installer_child_pid=""
  if [[ "$rescue_apply" == "1" && "$installer_status" -eq 0 ]]; then
    _release_rescue_extract
  fi
  exit "$installer_status"
fi
exec "$pack_python" "$installer_entry" "${arguments[@]}"
