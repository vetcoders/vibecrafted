#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'Runtime Pack bundle parity failed: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    'Usage: scripts/verify-runtime-pack-bundle-parity.sh [--pack <archive>]' \
    '' \
    'Without --pack, build the current clean commit with make runtime-pack.' \
    'Install the signed carrier under isolated roots and probe installed bytes.'
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
pack=""
while (($#)); do
  case "$1" in
    --pack)
      (($# >= 2)) || die '--pack requires an archive path'
      pack="$2"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ -z "$pack" ]]; then
  release_flags="${RELEASE_FLAGS:---snapshot-donors}"
  make -C "$repo_root" runtime-pack RELEASE_FLAGS="$release_flags"
  # shellcheck source=/dev/null
  . "$repo_root/scripts/lib/runtime-pack-selection.sh"
  runtime_pack_selection_read "$repo_root" "" "" \
    || die "${RUNTIME_PACK_SELECTION_ERROR:-release builder produced no Runtime Pack}"
  pack="$RUNTIME_PACK_SELECTION_PACK"
fi

pack_name="${pack##*/}"
pack_dir="$(cd "$(dirname "$pack")" 2>/dev/null && pwd -P)" \
  || die "cannot resolve Runtime Pack directory: $pack"
pack="$pack_dir/$pack_name"
[[ -f "$pack" ]] || die "Runtime Pack is missing: $pack"
[[ -f "$pack.sha256" ]] || die "Runtime Pack checksum is missing: $pack.sha256"
[[ -f "$pack.sig" ]] || die "Runtime Pack signature is missing: $pack.sig"

work="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-bundle-parity.XXXXXX")"
cleanup() {
  [[ ! -L "$work" && -d "$work" ]] && find "$work" -depth -delete
}
trap cleanup EXIT INT TERM HUP
isolated_home="$work/home"
runtime_home="$work/runtime-home"
crafted_home="$work/vibecrafted-home"
launcher_bin="$work/launcher-bin"
cache_home="$work/cache"
data_home="$work/data"
frame_socket_dir="$work/frame-sockets"
probe_bin="$work/probe-bin"
probe_log="$work/vc-frame-invoked"
config_file="$isolated_home/.config/vibecrafted/config.toml"
mkdir -p "$(dirname "$config_file")" "$runtime_home" "$crafted_home" \
  "$launcher_bin" "$cache_home" "$data_home" "$frame_socket_dir" "$probe_bin"
chmod 0700 "$runtime_home" "$crafted_home" "$cache_home" "$frame_socket_dir"
printf '%s\n' \
  '[server]' \
  'host = "127.0.0.1"' \
  'port = 19417' \
  '# bundle-parity sentinel: installer must preserve these bytes' \
  > "$config_file"
config_sha_before="$(shasum -a 256 "$config_file" | awk '{print $1}')"

# A PATH sentinel turns any attempted `vc-frame` restart into a hard failure.
# VC_FRAME_SOCKET_DIR also prevents an isolated test from ever addressing the
# Founder's uid-wide live socket namespace.
# shellcheck disable=SC2016  # expansion belongs to the generated sentinel
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'printf "%s\n" "invoked" > "${VC_FRAME_PROBE_LOG:?}"' \
  'exit 97' \
  > "$probe_bin/vc-frame"
chmod 0755 "$probe_bin/vc-frame"

expected_binaries=(voc vc-o vc-admin vc-procs control-observe)
command -v loct >/dev/null 2>&1 || die 'loct is required to map binary consumers'
: > "$work/consumer-map.txt"
for binary in "${expected_binaries[@]}"; do
  binary_map="$work/consumer-map-$binary.txt"
  (
    cd "$repo_root"
    loct find --literal --whole-token --compact --all "$binary"
  ) > "$binary_map"
  [[ -s "$binary_map" ]] \
    || die "Loctree returned no code contract for $binary"
  printf '=== %s ===\n' "$binary" >> "$work/consumer-map.txt"
  cat "$binary_map" >> "$work/consumer-map.txt"
done

tar -tzf "$pack" > "$work/archive.txt"
for binary in "${expected_binaries[@]}"; do
  expected="VibecraftedRuntime/bin/$binary"
  while IFS= read -r archived; do
    [[ "$archived" == "$expected" ]] && continue 2
  done < "$work/archive.txt"
  die "archive is missing $expected"
done

env \
  HOME="$isolated_home" \
  XDG_CACHE_HOME="$cache_home" \
  XDG_DATA_HOME="$data_home" \
  VIBECRAFTED_HOME="$crafted_home" \
  VIBECRAFTED_RUNTIME_HOME="$runtime_home" \
  VIBECRAFTED_LAUNCHER_BIN="$launcher_bin" \
  VC_FRAME_SOCKET_DIR="$frame_socket_dir" \
  VC_FRAME_PROBE_LOG="$probe_log" \
  PATH="$probe_bin:$PATH" \
  bash "$repo_root/scripts/install-runtime-pack.sh" --pack "$pack"

current="$runtime_home/tools/vibecrafted-current"
[[ -L "$current" ]] || die "installer did not publish $current"
generation="$(cd "$current" && pwd -P)"
case "$generation" in
  "$runtime_home"/releases/*) ;;
  *) die "installed generation escaped isolated runtime home: $generation" ;;
esac

for binary in "${expected_binaries[@]}"; do
  [[ -x "$generation/bin/$binary" ]] \
    || die "installed generation is missing executable bin/$binary"
done
for binary in vc-o vc-admin vc-procs; do
  [[ -x "$launcher_bin/$binary" ]] \
    || die "fresh install did not publish launcher $binary"
done
[[ ! -L "$generation/bin/vc-o" ]] || die 'installed vc-o is a symlink'
cmp -s "$generation/bin/voc" "$generation/bin/vc-o" \
  || die 'installed vc-o bytes differ from voc'
vc_o_version="$("$launcher_bin/vc-o" --version)"
[[ "$vc_o_version" == voc\ * ]] \
  || die "vc-o did not identify as Voc: $vc_o_version"
"$launcher_bin/vc-admin" --version >/dev/null
"$launcher_bin/vc-procs" --version >/dev/null

env \
  -u VIBECRAFTED_CONTROL_OBSERVE \
  -u VIBECRAFTED_ROOT \
  PATH="$generation/bin:$probe_bin:$PATH" \
  "$generation/bin/python3" - "$generation/bin/control-observe" <<'PY'
import sys
from pathlib import Path

from vibecrafted_core.server_observation import _control_observe_bin

expected = Path(sys.argv[1]).resolve()
resolved = _control_observe_bin()
if resolved is None or resolved.resolve() != expected:
    raise SystemExit(f"control-observe resolver mismatch: {resolved!s} != {expected}")
print(f"control-observe-resolved={resolved}")
PY

"$generation/bin/control-observe" \
  --home "$crafted_home" --run-id bundle-parity-missing --json \
  > "$work/control-observe.json"
"$generation/bin/python3" - "$work/control-observe.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert payload["schema"] == "vibecrafted.run-observation.v1"
assert payload["run_id"] == "bundle-parity-missing"
assert payload["source"] == "control_core_compute_view"
assert payload["found"] is False
print("control-observe-executed=true")
PY

config_sha_after="$(shasum -a 256 "$config_file" | awk '{print $1}')"
[[ "$config_sha_after" == "$config_sha_before" ]] \
  || die "installer changed existing user config: $config_file"
[[ ! -e "$probe_log" ]] || die 'installer invoked vc-frame during fresh install'

printf 'bundle-parity=ok pack=%s vc-o=%s installed-binaries=%s\n' \
  "$pack" "$vc_o_version" "${expected_binaries[*]}"
