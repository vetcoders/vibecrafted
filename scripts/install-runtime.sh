#!/usr/bin/env bash
set -euo pipefail

# Runtime horse installer for the lab catalog exposed by install.sh --runtime.
# The helper is intentionally substrate-honest: unsupported platform combos
# fail before work begins, missing build toolchains write a substrate report.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="${VIBECRAFTED_SOURCE:-$(cd "$SCRIPT_DIR/.." && pwd)}"

RUNTIME="none"
CHECK_ONLY=0
PLATFORM_OVERRIDE=""
PREFIX="${VIBECRAFTED_BIN:-${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/bin}"

usage() {
  cat <<'EOF_USAGE'
Usage: install-runtime.sh --runtime <horse> [--yes] [--check] [--prefix DIR]

Runtime horses:
  none          No runtime install (default)
  wezterm       Cross-platform WezTerm binary + vibecrafted Lua config
  vc-apprt      vc_ Ghostty fork with apprt.vibecrafted (macOS/Linux)
  locterm       locterm.app + iTerm2 Python AutoLaunch plugin (macOS only)
  microsandbox  libkrun/krunvm-backed execution runtime (macOS/Linux)

Environment overrides:
  VIBECRAFTED_RUNTIME_<HORSE>_SOURCE  Source checkout for a horse
  VIBECRAFTED_SOURCE                  Vibecrafted checkout root
  VIBECRAFTED_HOME                    Runtime home (default: $HOME/.vibecrafted)
EOF_USAGE
}

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
info() { printf '%s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }

# Storytelling discipline (same pattern as install.sh): this script may add at
# most its ≤2-line section contribution to the default view. Everything else
# is detail behind VERBOSE=1; warnings and errors stay visible.
vinfo() {
  if [[ "${VERBOSE:-0}" == "1" ]]; then
    printf '%s\n' "$*"
  fi
}

has_cmd() { command -v "$1" >/dev/null 2>&1; }

# shellcheck source=scripts/lib/runtime-roots.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/runtime-roots.sh"

detect_platform() {
  if [[ -n "$PLATFORM_OVERRIDE" ]]; then
    printf '%s\n' "$PLATFORM_OVERRIDE"
    return
  fi
  case "$(uname -s)" in
    Darwin*) printf 'macos\n' ;;
    Linux*)
      if grep -qiE 'microsoft|wsl' /proc/sys/kernel/osrelease 2>/dev/null \
        || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        printf 'wsl\n'
      else
        printf 'linux\n'
      fi
      ;;
    *) printf 'unsupported\n' ;;
  esac
}

workspace_root() {
  local parent default_root legacy_root
  parent="$(cd "$SOURCE_DIR/.." && pwd)"
  if [[ -d "$parent/labs" ]]; then
    printf '%s\n' "$parent"
    return
  fi
  default_root="$(default_vibecrafted_home)/vc-runtime"
  legacy_root="$HOME/Libraxis/vc-runtime"
  # Migration/fallback: honor a pre-existing legacy checkout so upgrades keep
  # resolving, but prefer the new default once it actually holds runtime
  # content. "Populated" means the root carries a real runtime workspace — the
  # labs/ tree or one of the known runtime source checkouts (wezterm, vc_,
  # locterm, experimental). An absent path, a stray file, an empty directory
  # (e.g. one pre-created by the VM compose mount), or a root with only
  # unrelated content (.DS_Store, a README, a helper dir) all fall back to the
  # legacy checkout so the runtime sources stay resolvable. Idempotent and
  # non-destructive: reads paths only, never moves them (the warning goes to
  # stderr so the captured stdout stays a clean path).
  local default_populated=0
  if [[ -d "$default_root/labs" || -d "$default_root/wezterm" \
    || -d "$default_root/vc_" || -d "$default_root/locterm" \
    || -d "$default_root/experimental" ]]; then
    default_populated=1
  fi
  if (( ! default_populated )) && [[ -d "$legacy_root" ]]; then
    warn "Using legacy runtime workspace $legacy_root; new default is $default_root."
    printf '%s\n' "$legacy_root"
    return
  fi
  printf '%s\n' "$default_root"
}

runtime_source() {
  local runtime="$1" root
  root="$(workspace_root)"
  case "$runtime" in
    wezterm)
      printf '%s\n' "${VIBECRAFTED_RUNTIME_WEZTERM_SOURCE:-$root/wezterm}"
      ;;
    vc-apprt)
      printf '%s\n' "${VIBECRAFTED_RUNTIME_VC_APPRT_SOURCE:-$root/vc_}"
      ;;
    locterm)
      printf '%s\n' "${VIBECRAFTED_RUNTIME_LOCTERM_SOURCE:-$root/locterm}"
      ;;
    microsandbox)
      printf '%s\n' "${VIBECRAFTED_RUNTIME_MICROSANDBOX_SOURCE:-$root/experimental/microsandbox}"
      ;;
    *)
      return 1
      ;;
  esac
}

runtime_lab_docs() {
  local runtime="$1" root
  root="$(workspace_root)"
  case "$runtime" in
    wezterm) printf '%s\n' "$root/labs/wezterm-spine" ;;
    vc-apprt) printf '%s\n' "$root/labs/vc-apprt-spine" ;;
    locterm) printf '%s\n' "$root/labs/locterm-spine" ;;
    microsandbox) printf '%s\n' "$root/labs/microsandbox-execution" ;;
    *) return 1 ;;
  esac
}

runtime_status_file() {
  printf '%s/runtime/runtime.json\n' "$(default_vibecrafted_home)"
}

write_status() {
  local runtime="$1" status="$2" path="$3" message="$4" platform="$5"
  local status_file
  status_file="$(runtime_status_file)"
  mkdir -p "$(dirname "$status_file")"
  python3 - "$status_file" "$runtime" "$status" "$path" "$message" "$platform" <<'PY'
import json
import sys
from datetime import datetime, timezone

status_file, runtime, status, path, message, platform = sys.argv[1:]
payload = {
    "runtime": runtime,
    "status": status,
    "path": path,
    "message": message,
    "platform": platform,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(status_file, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
PY
}

substrate_report() {
  local runtime="$1" message="$2" hint="$3"
  local lab_dir report
  lab_dir="$(runtime_lab_docs "$runtime" 2>/dev/null || true)"
  if [[ -n "$lab_dir" && -d "$lab_dir" ]]; then
    mkdir -p "$lab_dir/reports"
    report="$lab_dir/reports/substrate-failure-installer.md"
  else
    report="$(default_vibecrafted_home)/runtime/substrate-failure-${runtime}.md"
    mkdir -p "$(dirname "$report")"
  fi
  {
    printf '# %s runtime substrate failure\n\n' "$runtime"
    printf 'Current state: %s\n\n' "$message"
    printf 'Recovery:\n\n'
    printf '%s\n' '```bash'
    printf '%s\n' "$hint"
    printf '%s\n' '```'
  } > "$report"
  warn "Wrote substrate report: $report"
}

validate_runtime_platform() {
  local runtime="$1" platform="$2"
  case "$runtime" in
    none)
      return 0
      ;;
    wezterm)
      case "$platform" in macos|linux|wsl) return 0 ;; esac
      ;;
    vc-apprt)
      case "$platform" in macos|linux) return 0 ;; esac
      die "vc-apprt supports macOS and Linux only, try --runtime wezterm"
      ;;
    locterm)
      [[ "$platform" == "macos" ]] && return 0
      die "locterm is macOS-only, try --runtime wezterm or --runtime microsandbox"
      ;;
    microsandbox)
      case "$platform" in macos|linux) return 0 ;; esac
      die "microsandbox requires macOS HVF or Linux KVM, try --runtime wezterm"
      ;;
    *)
      die "Unknown runtime horse: $runtime (expected wezterm, vc-apprt, locterm, microsandbox, none)"
      ;;
  esac
  die "Unsupported platform '$platform' for runtime '$runtime'"
}

ensure_prefix() {
  (( CHECK_ONLY )) && return 0
  mkdir -p "$PREFIX"
}

copy_executable() {
  local src="$1" dest="$2"
  ensure_prefix
  cp "$src" "$dest"
  chmod +x "$dest"
}

install_wezterm() {
  local platform="$1" src lua_src config_dir dest bin_path
  src="$(runtime_source wezterm)"
  lua_src=""
  for candidate in \
    "$src/config/wezterm" \
    "$SOURCE_DIR/config/wezterm"; do
    if [[ -d "$candidate" ]]; then
      lua_src="$candidate"
      break
    fi
  done

  if (( CHECK_ONLY )); then
    info "Would install wezterm runtime from ${src:-<missing>}"
    return 0
  fi

  config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/wezterm"
  dest="$config_dir/vibecrafted"
  mkdir -p "$config_dir"
  if [[ -n "$lua_src" ]]; then
    if [[ -L "$dest" || ! -e "$dest" ]]; then
      rm -f "$dest"
      ln -s "$lua_src" "$dest"
    elif [[ -d "$dest" ]]; then
      cp -R "$lua_src"/. "$dest"/
    else
      die "Cannot stage wezterm config: $dest exists and is not a directory"
    fi
    vinfo "Activated wezterm Lua config: $dest -> $lua_src"
  else
    warn "wezterm Lua config not found; expected $src/config/wezterm"
  fi

  if has_cmd wezterm && wezterm --version >/dev/null 2>&1; then
    bin_path="$(command -v wezterm)"
    write_status "wezterm" "ok" "$bin_path" "wezterm command available; config staged at $dest" "$platform"
    return 0
  fi

  for candidate in "$src/target/release/wezterm" "$src/target/debug/wezterm"; do
    if [[ -x "$candidate" ]]; then
      copy_executable "$candidate" "$PREFIX/wezterm"
      write_status "wezterm" "ok" "$PREFIX/wezterm" "installed from source checkout; config staged at $dest" "$platform"
      return 0
    fi
  done

  case "$platform" in
    macos)
      if has_cmd brew; then
        brew install --cask wezterm
        write_status "wezterm" "ok" "$(command -v wezterm || printf /Applications/WezTerm.app)" "installed via Homebrew cask; config staged at $dest" "$platform"
        return 0
      fi
      substrate_report "wezterm" "wezterm is not installed and Homebrew is unavailable." "brew install --cask wezterm"
      ;;
    linux|wsl)
      substrate_report "wezterm" "wezterm is not installed and no built binary was found in $src." "Install WezTerm for your distro, then rerun: bash install.sh --runtime wezterm --yes"
      ;;
  esac
  write_status "wezterm" "failed" "" "wezterm binary unavailable" "$platform"
  return 1
}

install_vc_apprt() {
  local platform="$1" src bin_path
  src="$(runtime_source vc-apprt)"
  if (( CHECK_ONLY )); then
    info "Would install vc-apprt runtime from $src"
    return 0
  fi
  [[ -d "$src" ]] || {
    substrate_report "vc-apprt" "vc_ source checkout not found: $src" "Set VIBECRAFTED_RUNTIME_VC_APPRT_SOURCE=/path/to/vc_ and rerun."
    write_status "vc-apprt" "failed" "" "source checkout missing" "$platform"
    return 1
  }
  if [[ ! -x "$src/zig-out/bin/vc_" ]]; then
    if ! has_cmd zig; then
      substrate_report "vc-apprt" "Zig 0.16 is required to build vc_." "brew install zig || install Zig 0.16 from https://ziglang.org/download/"
      write_status "vc-apprt" "failed" "" "zig missing" "$platform"
      return 1
    fi
    (cd "$src" && zig build)
  fi
  bin_path="$src/zig-out/bin/vc_"
  [[ -x "$bin_path" ]] || die "vc_ build did not produce $bin_path"
  copy_executable "$bin_path" "$PREFIX/vc_"
  if "$PREFIX/vc_" --help >/dev/null 2>&1; then
    write_status "vc-apprt" "ok" "$PREFIX/vc_" "vc_ binary installed" "$platform"
    return 0
  fi
  write_status "vc-apprt" "failed" "$PREFIX/vc_" "vc_ --help failed" "$platform"
  return 1
}

install_locterm() {
  local platform="$1" src app_src app_dest plugin_result
  src="$(runtime_source locterm)"
  app_dest="/Applications/locterm.app"
  if (( CHECK_ONLY )); then
    info "Would install locterm from $src and AutoLaunch plugin"
    return 0
  fi

  app_src=""
  for candidate in \
    "$src/build/Release/locterm.app" \
    "$src/build/Release/iTerm2.app" \
    "$src/locterm.app" \
    "/Applications/locterm.app"; do
    if [[ -d "$candidate" ]]; then
      app_src="$candidate"
      break
    fi
  done
  [[ -n "$app_src" ]] || {
    substrate_report "locterm" "locterm.app was not found in $src or /Applications." "Build locterm, then rerun: bash install.sh --runtime locterm --yes"
    write_status "locterm" "failed" "" "locterm.app missing" "$platform"
    return 1
  }
  if [[ "$app_src" != "$app_dest" && -w "/Applications" ]]; then
    rm -rf "$app_dest"
    cp -R "$app_src" "$app_dest"
    app_src="$app_dest"
  fi

  # Prefer the installed uv-tool entry point; bare system python3 lacks
  # vibecrafted_iterm2 and always failed here.
  plugin_result="not-run"
  if command -v vibecrafted-iterm2-autolaunch >/dev/null 2>&1 \
       && vibecrafted-iterm2-autolaunch --force >/dev/null 2>&1; then
    plugin_result="installed"
  elif python3 -m vibecrafted_iterm2.install_autolaunch --force >/dev/null 2>&1; then
    plugin_result="installed"
  else
    warn "locterm AutoLaunch plugin install failed; rerun: vibecrafted-iterm2-autolaunch --force"
    plugin_result="failed"
  fi
  write_status "locterm" "ok" "$app_src" "locterm app present; plugin=$plugin_result" "$platform"
}

install_microsandbox() {
  local platform="$1" src msb_path
  src="$(runtime_source microsandbox)"
  if (( CHECK_ONLY )); then
    info "Would install microsandbox runtime from $src"
    return 0
  fi
  if has_cmd msb && msb --version >/dev/null 2>&1; then
    write_status "microsandbox" "ok" "$(command -v msb)" "msb command available" "$platform"
    return 0
  fi
  [[ -d "$src" ]] || {
    substrate_report "microsandbox" "microsandbox checkout not found: $src" "Set VIBECRAFTED_RUNTIME_MICROSANDBOX_SOURCE=/path/to/microsandbox and rerun."
    write_status "microsandbox" "failed" "" "source checkout missing" "$platform"
    return 1
  }
  if ! has_cmd krunvm; then
    substrate_report "microsandbox" "krunvm is required for libkrun-backed microsandbox builds." "brew tap slp/krun && brew install krunvm"
    write_status "microsandbox" "failed" "" "krunvm missing" "$platform"
    return 1
  fi
  (cd "$src" && make build)
  for candidate in \
    "$src/target/release/msb" \
    "$src/cli/target/release/msb" \
    "$src/msb/target/release/msb"; do
    if [[ -x "$candidate" ]]; then
      msb_path="$candidate"
      copy_executable "$msb_path" "$PREFIX/msb"
      write_status "microsandbox" "ok" "$PREFIX/msb" "msb built and installed" "$platform"
      return 0
    fi
  done
  write_status "microsandbox" "failed" "" "make build completed but msb binary was not found" "$platform"
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --runtime"
      RUNTIME="$1"
      ;;
    --yes|-y)
      ;;
    --check)
      CHECK_ONLY=1
      ;;
    --prefix)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --prefix"
      PREFIX="$1"
      ;;
    --platform)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --platform"
      PLATFORM_OVERRIDE="$1"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
  shift
done

case "$RUNTIME" in
  vc_apprt|vc-) RUNTIME="vc-apprt" ;;
esac

PLATFORM="$(detect_platform)"
validate_runtime_platform "$RUNTIME" "$PLATFORM"

if [[ "$RUNTIME" == "none" ]]; then
  # No runtime requested is the common case — stay silent unless VERBOSE.
  vinfo "Runtime horse: none (no runtime install requested)"
  exit 0
fi

printf '\nRuntime horse: %s (%s)\n' "$RUNTIME" "$PLATFORM"
case "$RUNTIME" in
  wezterm) install_wezterm "$PLATFORM" ;;
  vc-apprt) install_vc_apprt "$PLATFORM" ;;
  locterm) install_locterm "$PLATFORM" ;;
  microsandbox) install_microsandbox "$PLATFORM" ;;
esac
