#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack packaging failed: %s\n' "$*" >&2; exit 1; }

app=""
output=""
while (($#)); do
  case "$1" in
    --app)
      (($# >= 2)) || die "--app requires a path"
      app="$2"
      shift 2
      ;;
    --output)
      (($# >= 2)) || die "--output requires a path"
      output="$2"
      shift 2
      ;;
    --help|-h)
      printf 'usage: %s --app <Vibecrafted.app> --output <RuntimePack.tar.gz>\n' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ -n "$app" && -n "$output" ]] || die "--app and --output are required"
app_name="${app##*/}"
app_parent="$(cd "$(dirname "$app")" 2>/dev/null && pwd)" \
  || die "cannot resolve app path: $app"
app="$app_parent/$app_name"
runtime="$app/Contents/Resources/runtime"
terminal="$app/Contents/Helpers/vc-terminal.app/Contents/MacOS/alacritty"
frame="$app/Contents/Helpers/vc-frame"
[[ -d "$runtime" ]] || die "app has no Runtime Pack payload: $runtime"
[[ -x "$terminal" ]] || die "app has no terminal host: $terminal"
[[ -x "$frame" ]] || die "app has no vc-frame helper: $frame"

work="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-runtime-pack-build.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT INT TERM HUP
root="$work/VibecraftedRuntime"
mkdir -p "$root"
if command -v ditto >/dev/null 2>&1; then
  /usr/bin/ditto "$runtime" "$root"
else
  cp -R "$runtime/." "$root/"
fi
install -m 0755 "$terminal" "$root/bin/vc-terminal"
mkdir -p "$root/libexec"
install -m 0755 "$frame" "$root/libexec/vc-frame"
install -m 0755 "$root/scripts/vc-frame-product-entry.sh" "$root/bin/vc-frame"

if find "$root" -type l -print -quit | grep -q .; then
  die "standalone Runtime Pack contains symlinks"
fi
for required in \
  VERSION bin/python3 bin/vibecrafted bin/vc-terminal bin/vc-frame \
  libexec/vc-frame scripts/vetcoders_install.py; do
  [[ -e "$root/$required" ]] || die "standalone Runtime Pack is missing $required"
done

mkdir -p "$(dirname "$output")"
candidate="$work/$(basename "$output")"
COPYFILE_DISABLE=1 tar -czf "$candidate" -C "$work" VibecraftedRuntime
mv "$candidate" "$output"
(
  cd "$(dirname "$output")"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$(basename "$output")" > "$(basename "$output").sha256"
  else
    sha256sum "$(basename "$output")" > "$(basename "$output").sha256"
  fi
)
printf '%s\n' "$output"
