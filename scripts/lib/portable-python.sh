#!/usr/bin/env bash
# Pin and materialize the Runtime Pack CPython from python-build-standalone.
# Published prebuilt install_only archives only. Never compile CPython.
#
# Usage: install_portable_python DEST_DIR
# Extracts into DEST_DIR and prints the path to bin/pythonX.Y for the pinned
# feature series. Also exports PORTABLE_PYTHON_SERIES, PORTABLE_PYTHON_BIN,
# and PORTABLE_PYTHON_DYLIB so launchers/relocation follow the pin.

if ! declare -F die >/dev/null 2>&1; then
  die() { printf 'FATAL: %s\n' "$*" >&2; exit 1; }
fi

_PORTABLE_PYTHON_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTABLE_PYTHON_ARTIFACT_JSON="${PORTABLE_PYTHON_ARTIFACT_JSON:-$_PORTABLE_PYTHON_LIB_DIR/portable-python-artifact.json}"

_portable_python_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

_portable_python_platform() {
  case "$(uname -s):$(uname -m)" in
    Darwin:arm64) printf '%s\n' darwin-arm64 ;;
    Darwin:x86_64) printf '%s\n' darwin-x86_64 ;;
    Linux:aarch64 | Linux:arm64) printf '%s\n' linux-arm64 ;;
    Linux:x86_64) printf '%s\n' linux-x64 ;;
    *) die "no pinned portable CPython for $(uname -s)/$(uname -m)" ;;
  esac
}

portable_python_load_pin() {
  local platform="${1:-}"
  [[ -f "$PORTABLE_PYTHON_ARTIFACT_JSON" ]] \
    || die "portable Python pin missing: $PORTABLE_PYTHON_ARTIFACT_JSON"
  if [[ -z "$platform" ]]; then
    platform="$(_portable_python_platform)"
  fi
  # Host python is only used to read the pin. The seed interpreter is the archive.
  eval "$(
    python3 - "$PORTABLE_PYTHON_ARTIFACT_JSON" "$platform" <<'PY'
import json, pathlib, shlex, sys

pin = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
platform = sys.argv[2]
wanted = pin.get("cpython")
if not wanted:
    raise SystemExit("portable Python pin is missing cpython")
parts = str(wanted).split(".")
if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
    raise SystemExit(f"portable Python pin cpython is not X.Y[.Z]: {wanted}")
series = f"{parts[0]}.{parts[1]}"
for item in pin.get("artifacts") or []:
    if item.get("platform") == platform:
        print(f"url={shlex.quote(item['url'])}")
        print(f"archive={shlex.quote(item['archive'])}")
        print(f"expected={shlex.quote(item['sha256'])}")
        print(f"cpython={shlex.quote(wanted)}")
        print(f"series={shlex.quote(series)}")
        raise SystemExit(0)
raise SystemExit(f"portable Python pin has no artifact for {platform}")
PY
  )"
  [[ -n "${url:-}" && -n "${archive:-}" && -n "${expected:-}" && -n "${cpython:-}" && -n "${series:-}" ]] \
    || die "portable Python pin did not resolve $platform"
  PORTABLE_PYTHON_CPYTHON="$cpython"
  PORTABLE_PYTHON_SERIES="$series"
  PORTABLE_PYTHON_BIN="python${series}"
  PORTABLE_PYTHON_DYLIB="libpython${series}.dylib"
  PORTABLE_PYTHON_URL="$url"
  PORTABLE_PYTHON_ARCHIVE="$archive"
  PORTABLE_PYTHON_SHA256="$expected"
  export PORTABLE_PYTHON_CPYTHON PORTABLE_PYTHON_SERIES PORTABLE_PYTHON_BIN \
    PORTABLE_PYTHON_DYLIB PORTABLE_PYTHON_URL PORTABLE_PYTHON_ARCHIVE \
    PORTABLE_PYTHON_SHA256
}

install_portable_python() {
  local dest="$1"
  [[ -n "$dest" ]] || die "install_portable_python requires a destination directory"
  mkdir -p "$dest"
  portable_python_load_pin
  local tarball="$dest/$PORTABLE_PYTHON_ARCHIVE"
  local digest=""
  if [[ -f "$tarball" ]]; then
    digest="$(_portable_python_sha256 "$tarball")"
  fi
  if [[ "$digest" != "$PORTABLE_PYTHON_SHA256" ]]; then
    local attempt
    for attempt in 1 2 3; do
      if curl -fL --retry 3 --retry-delay "$attempt" -o "$tarball" "$PORTABLE_PYTHON_URL"; then
        digest="$(_portable_python_sha256 "$tarball")"
        if [[ "$digest" == "$PORTABLE_PYTHON_SHA256" ]]; then
          break
        fi
        rm -f "$tarball"
      fi
      [[ "$attempt" -eq 3 ]] && die "portable CPython download failed after retries"
      sleep "$attempt"
    done
  fi
  tar -xzf "$tarball" -C "$dest"
  local seed_python
  seed_python="$(find "$dest" -type f -path "*/bin/${PORTABLE_PYTHON_BIN}" -print -quit)"
  [[ -n "$seed_python" && -x "$seed_python" ]] \
    || die "portable CPython archive did not contain bin/${PORTABLE_PYTHON_BIN}"
  "$seed_python" -c "import sys; raise SystemExit(0 if sys.version_info[:3]==tuple(int(p) for p in '$PORTABLE_PYTHON_CPYTHON'.split('.')) else 1)" \
    || die "portable CPython is not $PORTABLE_PYTHON_CPYTHON"
  printf '%s\n' "$seed_python"
}
