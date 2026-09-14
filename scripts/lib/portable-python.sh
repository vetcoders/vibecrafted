#!/usr/bin/env bash
# Pin and materialize the Runtime Pack CPython from python-build-standalone.
# Published prebuilt install_only archives only. Never compile CPython.
#
# Usage: install_portable_python DEST_DIR
# Extracts into DEST_DIR and prints the path to bin/python3.12.

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

install_portable_python() {
  local dest="$1"
  [[ -n "$dest" ]] || die "install_portable_python requires a destination directory"
  [[ -f "$PORTABLE_PYTHON_ARTIFACT_JSON" ]] \
    || die "portable Python pin missing: $PORTABLE_PYTHON_ARTIFACT_JSON"
  mkdir -p "$dest"
  local platform
  platform="$(_portable_python_platform)"
  local url archive expected cpython
  # Host python is only used to read the pin. The seed interpreter is the archive.
  eval "$(
    python3 - "$PORTABLE_PYTHON_ARTIFACT_JSON" "$platform" <<'PY'
import json, pathlib, shlex, sys

pin = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
platform = sys.argv[2]
wanted = pin.get("cpython")
if not wanted:
    raise SystemExit("portable Python pin is missing cpython")
for item in pin.get("artifacts") or []:
    if item.get("platform") == platform:
        print(f"url={shlex.quote(item['url'])}")
        print(f"archive={shlex.quote(item['archive'])}")
        print(f"expected={shlex.quote(item['sha256'])}")
        print(f"cpython={shlex.quote(wanted)}")
        raise SystemExit(0)
raise SystemExit(f"portable Python pin has no artifact for {platform}")
PY
  )"
  [[ -n "$url" && -n "$archive" && -n "$expected" && -n "$cpython" ]] \
    || die "portable Python pin did not resolve $platform"
  local tarball="$dest/$archive"
  local attempt
  for attempt in 1 2 3; do
    if curl -fL --retry 3 --retry-delay "$attempt" -o "$tarball" "$url"; then
      local digest
      digest="$(_portable_python_sha256 "$tarball")"
      if [[ "$digest" == "$expected" ]]; then
        break
      fi
      rm -f "$tarball"
    fi
    [[ "$attempt" -eq 3 ]] && die "portable CPython download failed after retries"
    sleep "$attempt"
  done
  tar -xzf "$tarball" -C "$dest"
  local seed_python
  seed_python="$(find "$dest" -type f -path '*/bin/python3.12' -print -quit)"
  [[ -n "$seed_python" && -x "$seed_python" ]] \
    || die "portable CPython archive did not contain bin/python3.12"
  "$seed_python" -c "import sys; raise SystemExit(0 if sys.version_info[:3]==tuple(int(p) for p in '$cpython'.split('.')) else 1)" \
    || die "portable CPython is not $cpython"
  printf '%s\n' "$seed_python"
}
