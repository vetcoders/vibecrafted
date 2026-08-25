#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Runtime Pack packaging failed: %s\n' "$*" >&2; exit 1; }

app=""
output=""
source_revision=""
terminal_revision=""
frame_revision=""
version=""
platform=""
architecture=""
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
    --source-revision|--terminal-revision|--frame-revision|--version|--platform|--architecture)
      (($# >= 2)) || die "$1 requires a value"
      case "$1" in
        --source-revision) source_revision="$2" ;;
        --terminal-revision) terminal_revision="$2" ;;
        --frame-revision) frame_revision="$2" ;;
        --version) version="$2" ;;
        --platform) platform="$2" ;;
        --architecture) architecture="$2" ;;
      esac
      shift 2
      ;;
    --help|-h)
      printf 'usage: %s --app <Vibecrafted.app> --output <RuntimePack.tar.gz> --source-revision <sha> --terminal-revision <sha> --frame-revision <sha> --version <version> --platform <platform> --architecture <arch>\n' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

for required_value in app output source_revision terminal_revision frame_revision version platform architecture; do
  [[ -n "${!required_value}" ]] || die "--$required_value is required"
done
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
  libexec/vc-frame scripts/vetcoders_install.py \
  vibecrafted-core/vibecrafted_core/runtime_pack_contract.py; do
  [[ -e "$root/$required" ]] || die "standalone Runtime Pack is missing $required"
done

PYTHONPATH="$root/vibecrafted-core" "$root/bin/python3" \
  -m vibecrafted_core.runtime_pack_contract write \
  --root "$root" \
  --carrier-basename "$(basename "$output")" \
  --version "$version" \
  --platform "$platform" \
  --architecture "$architecture" \
  --source-revision "$source_revision" \
  --terminal-revision "$terminal_revision" \
  --frame-revision "$frame_revision" >/dev/null

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
