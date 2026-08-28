#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
sandbox_root="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-uninstall-roundtrip.XXXXXX")"
sandbox_home="$sandbox_root/home"
sandbox_tmp="$sandbox_root/tmp"
mkdir -p "$sandbox_home" "$sandbox_tmp"

cleanup() {
  rm -rf -- "$sandbox_root"
}
trap cleanup EXIT

sandbox_env=(
  env -i
  "HOME=$sandbox_home"
  "USER=${USER:-vibecrafted-test}"
  "LOGNAME=${LOGNAME:-${USER:-vibecrafted-test}}"
  "SHELL=/bin/zsh"
  "PATH=$sandbox_home/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  "TMPDIR=$sandbox_tmp"
  "XDG_CACHE_HOME=$sandbox_home/.cache"
  "XDG_CONFIG_HOME=$sandbox_home/.config"
  "XDG_DATA_HOME=$sandbox_home/.local/share"
  "UV_CACHE_DIR=$sandbox_home/.cache/uv"
  "UV_TOOL_DIR=$sandbox_home/.local/share/uv/tools"
  "UV_TOOL_BIN_DIR=$sandbox_home/.local/bin"
  "VIBECRAFTED_HOME=$sandbox_home/.vibecrafted"
  "VIBECRAFTED_RUNTIME_HOME=$sandbox_home/.local/share/vibecrafted"
  "VIBECRAFTED_TOOLS_HOME=$sandbox_home/.local/share/vibecrafted/tools"
  "VIBECRAFTED_LAUNCHER_BIN=$sandbox_home/.local/bin"
  "VC_FRAME_SOCKET_DIR=$sandbox_tmp/vc-frame-$(id -u)"
  "VIBECRAFTED_INSTALL_NONINTERACTIVE=1"
  "INSTALL_SERVER_SERVICE_POLICY=isolated"
  "CARGO_TARGET_DIR=$repo_root/target/codex-runtime-uninstall-roundtrip"
  "PYTHONDONTWRITEBYTECODE=1"
)

runtime_children=(
  control_plane server foundation locks runtime install-transactions recovery
  tmp logs .vc-install.json START_HERE.md install.log .DS_Store
)

seed_founder_data() {
  mkdir -p \
    "$sandbox_home/.vibecrafted/artifacts" \
    "$sandbox_home/.vibecrafted/inbox" \
    "$sandbox_home/.vibecrafted/charter"
  printf 'artifact\n' >"$sandbox_home/.vibecrafted/artifacts/x.md"
  printf 'inbox\n' >"$sandbox_home/.vibecrafted/inbox/y.md"
  printf 'charter\n' >"$sandbox_home/.vibecrafted/charter/z.md"
}

seed_runtime_state() {
  local child
  for child in "${runtime_children[@]}"; do
    case "$child" in
      .vc-install.json)
        printf '{"version":"1.0","framework_version":"legacy","skills":[],"runtimes":[]}\n' \
          >"$sandbox_home/.vibecrafted/$child"
        ;;
      START_HERE.md | install.log | .DS_Store)
        printf 'runtime\n' >"$sandbox_home/.vibecrafted/$child"
        ;;
      *)
        mkdir -p "$sandbox_home/.vibecrafted/$child"
        printf 'runtime\n' >"$sandbox_home/.vibecrafted/$child/probe"
        ;;
    esac
  done
}

assert_founder_data() {
  test "$(<"$sandbox_home/.vibecrafted/artifacts/x.md")" = artifact
  test "$(<"$sandbox_home/.vibecrafted/inbox/y.md")" = inbox
  test "$(<"$sandbox_home/.vibecrafted/charter/z.md")" = charter
}

assert_runtime_removed() {
  local child
  for child in "${runtime_children[@]}"; do
    if [[ -e "$sandbox_home/.vibecrafted/$child" || -L "$sandbox_home/.vibecrafted/$child" ]]; then
      printf 'runtime state survived uninstall: %s\n' "$sandbox_home/.vibecrafted/$child" >&2
      return 1
    fi
  done
  test ! -e "$sandbox_home/.local/bin/vibecrafted"
  test ! -e "$sandbox_home/.local/share/vibecrafted/install-receipt.json"
}

install_and_doctor() {
  "${sandbox_env[@]}" make --no-print-directory -C "$repo_root" install-source
  "${sandbox_env[@]}" "$sandbox_home/.local/bin/vibecrafted" doctor
}

printf '== receipted install ==\n'
install_and_doctor
seed_founder_data
seed_runtime_state

printf '== receipted uninstall ==\n'
"${sandbox_env[@]}" "$sandbox_home/.local/bin/vibecrafted" uninstall
assert_runtime_removed
assert_founder_data

printf '== reinstall after receipted uninstall ==\n'
install_and_doctor

printf '== legacy discovery uninstall ==\n'
rm -f -- "$sandbox_home/.local/share/vibecrafted/install-receipt.json"
seed_runtime_state
"${sandbox_env[@]}" "$sandbox_home/.local/bin/vibecrafted" uninstall
assert_runtime_removed
assert_founder_data

printf 'runtime uninstall round-trip: PASS\n'
