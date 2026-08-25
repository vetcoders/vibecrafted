#!/usr/bin/env bash
set -euo pipefail

die() { printf 'Linux arm64 Runtime Pack build failed: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

[[ "$(uname -s):$(uname -m)" == "Linux:aarch64" ]] \
  || die "builder must run natively on Linux/aarch64"
for tool in cargo curl make npm python3 sha256sum tar uv; do require "$tool"; done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
output="${1:-$repo_root/build/Vibecrafted_RuntimePack_linux-arm64.tar.gz}"
source_revision="${VIBECRAFTED_SOURCE_REVISION:-}"
[[ "$source_revision" =~ ^[0-9a-f]{40}$ ]] || die "VIBECRAFTED_SOURCE_REVISION must be a full Git SHA"

version="$(tr -d '[:space:]' < "$repo_root/VERSION")"
terminal_revision="d6685ead9018ad89411291d6198476666e48b0f8"
terminal_archive_sha256="3cd6670c4a80c589b945ed1b45c1f033c80745ceb34d3466e9476a1c3eeb0f71"
frame_revision="7ab84069c9b7994ce0b705ccedd708aa3a35dcb6"
frame_archive_sha256="55851e094b91d3b41712edcdc66d69f97da5859118395fee497bb104714b125c"
work="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-linux-arm64.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT INT TERM HUP
payload="$work/payload"
mkdir -p "$payload/bin" "$payload/libexec" "$payload/scripts" \
  "$payload/vibecrafted-core" "$payload/config" "$payload/server/site"

fetch_source() {
  local url="$1" expected="$2" archive="$3" destination="$4"
  curl -fL --proto '=https' --tlsv1.2 "$url" -o "$archive"
  [[ "$(sha256sum "$archive" | awk '{print $1}')" == "$expected" ]] \
    || die "source archive checksum mismatch: $url"
  mkdir -p "$destination"
  tar -xzf "$archive" --strip-components=1 -C "$destination"
}

fetch_source \
  "https://codeload.github.com/vetcoders/vc-terminal/tar.gz/$terminal_revision" \
  "$terminal_archive_sha256" "$work/vc-terminal.tar.gz" "$work/vc-terminal"
fetch_source \
  "https://codeload.github.com/vetcoders/vc-frame/tar.gz/$frame_revision" \
  "$frame_archive_sha256" "$work/vc-frame.tar.gz" "$work/vc-frame"

make -C "$work/vc-terminal" release-bins
install -m 0755 "$work/vc-terminal/target/release/alacritty" "$payload/bin/vc-terminal"
rm -rf "$work/vc-terminal" "$work/vc-terminal.tar.gz"

frame_sha="$frame_revision"
(
  cd "$work/vc-frame"
  CARGO_PROFILE_RELEASE_STRIP=false \
    RUSTFLAGS="--remap-path-prefix=$work/vc-frame=/usr/src/vc-frame" \
    VC_FRAME_GIT_SHA="$frame_sha" VC_FRAME_GIT_DIRTY=0 \
    VC_FRAME_SOURCE_MANIFEST_DIR=/usr/src/vc-frame/zellij-utils \
    cargo xtask build --release
)
install -m 0755 "$work/vc-frame/target/release/vc-frame" "$payload/libexec/vc-frame"
install -m 0755 "$repo_root/scripts/vc-frame-product-entry.sh" "$payload/bin/vc-frame"
rm -rf "$work/vc-frame" "$work/vc-frame.tar.gz"

voc_target="$work/voc-target"
CARGO_TARGET_DIR="$voc_target" cargo build --locked \
  --manifest-path "$repo_root/vibecrafted-app/Cargo.toml" \
  --release -p voc --bin voc --bin vc-start
install -m 0755 "$voc_target/release/voc" "$payload/bin/voc"
install -m 0755 "$voc_target/release/vc-start" "$payload/bin/vc-start"
rm -rf "$voc_target"

server_build="$work/server-build"
make -C "$repo_root" CARGO_BUILD_ROOT="$server_build" build-server-release
install -m 0755 "$server_build/vibecrafted-server/release/vibecrafted-server-web" \
  "$payload/bin/vc-server"
cp -R "$server_build/vibecrafted-server/site/." "$payload/server/site/"
rm -rf "$server_build"

printf '%s\n' "$version" > "$payload/VERSION"
install -m 0755 "$repo_root/vibecrafted-core/vibecrafted_core/deck/vibecrafted" \
  "$payload/bin/vibecrafted"
install -m 0755 "$repo_root/scripts/vibecrafted" "$payload/scripts/vibecrafted"
install -m 0755 "$repo_root/scripts/vetcoders_install.py" "$payload/scripts/vetcoders_install.py"
install -m 0644 "$repo_root/scripts/distribution_manifest.py" "$payload/scripts/distribution_manifest.py"
install -m 0644 "$repo_root/scripts/installer_brand.py" "$payload/scripts/installer_brand.py"
install -m 0755 "$repo_root/scripts/vc-frame-product-entry.sh" "$payload/scripts/vc-frame-product-entry.sh"
cp -R "$repo_root/bin/." "$payload/bin/"
cp -R "$repo_root/vibecrafted-core/vibecrafted_core" "$payload/vibecrafted-core/"
printf '%s+g%.8s\n' "$version" "$source_revision" \
  > "$payload/vibecrafted-core/vibecrafted_core/VERSION"
cp -R "$repo_root/config/." "$payload/config/"

python3 "$repo_root/scripts/distribution_manifest.py" carrier \
  --source "$repo_root" --output "$payload/source-provenance.json" \
  --owner-repo vetcoders/vibecrafted --source-revision "$source_revision"
"$repo_root/scripts/stage-runtime-foundations.sh" "$payload/bin"

uv python install 3.12.3 --install-dir "$work/python-seed" --no-bin
seed_python="$(find "$work/python-seed" -type f -path '*/bin/python3.12' -print -quit)"
[[ -n "$seed_python" ]] || die "uv did not produce CPython 3.12.3"
python_home="$(cd "$(dirname "$seed_python")/.." && pwd -P)"
mkdir -p "$payload/python" "$payload/python-site"
cp -RL "$python_home/." "$payload/python/"
uv pip install --python "$seed_python" --target "$payload/python-site" \
  'jsonschema>=4.23,<5' 'PyYAML>=6.0,<7' 'screenscribe==0.1.19'
rm -rf "$payload/python-site/bin"
cat > "$payload/bin/python3" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$runtime_root/vibecrafted-core:$runtime_root/python-site"
exec "$runtime_root/python/bin/python3.12" "$@"
EOF
chmod 0755 "$payload/bin/python3"
python3 "$repo_root/scripts/render-python-entrypoint-launchers.py" \
  --pyproject "$repo_root/vibecrafted-core/pyproject.toml" --bin-dir "$payload/bin"
cat > "$payload/bin/screenscribe" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
runtime_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$runtime_root/bin/python3" -c 'from screenscribe.bootstrap import main; main()' "$@"
EOF
chmod 0755 "$payload/bin/screenscribe"

find "$payload" -type f -name '*.py[co]' -delete
find "$payload" -depth -type d -name __pycache__ -exec rm -rf {} +
find "$payload" -type l -print -quit | grep -q . && die "payload contains symlinks"

PAYLOAD="$payload" SOURCE_REVISION="$source_revision" \
TERMINAL_REVISION="$terminal_revision" FRAME_REVISION="$frame_revision" python3 - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path

root = Path(os.environ["PAYLOAD"])
source_manifest_sha = hashlib.sha256((root / "source-provenance.json").read_bytes()).hexdigest()
foundation = json.loads((root / "runtime-foundations.json").read_text())
sources = {
    "vibecrafted": ("https://github.com/vetcoders/vibecrafted", os.environ["SOURCE_REVISION"], source_manifest_sha, "MIT"),
    "vc-terminal": (f"https://codeload.github.com/vetcoders/vc-terminal/tar.gz/{os.environ['TERMINAL_REVISION']}", os.environ["TERMINAL_REVISION"], "3cd6670c4a80c589b945ed1b45c1f033c80745ceb34d3466e9476a1c3eeb0f71", "Apache-2.0"),
    "vc-frame": (f"https://codeload.github.com/vetcoders/vc-frame/tar.gz/{os.environ['FRAME_REVISION']}", os.environ["FRAME_REVISION"], "55851e094b91d3b41712edcdc66d69f97da5859118395fee497bb104714b125c", "MIT"),
    "screenscribe": ("https://files.pythonhosted.org/packages/a2/8e/53e22fc84d28246c0316ab03bd26904fd80c545170466bd2cb926204f965/screenscribe-0.1.19-py3-none-any.whl", "0.1.19", "9988fe819443e2b47d949e737e1325bc755b31c18f1348a5b7b709c7cf155323", "BUSL-1.1"),
    "prview": ("https://crates.io/api/v1/crates/prview/0.6.0/download", "0.6.0", "c952a333d0c481509f30f520fd10d1016d814832e317bfd2bc2fdc34f1ecfc02", "BUSL-1.1"),
}
owners = {
    "vibecrafted": "vibecrafted", "vc-server": "vibecrafted", "voc": "vibecrafted",
    "vc-terminal": "vc-terminal", "vc-frame": "vc-frame", "screenscribe": "screenscribe",
    "loct": "loctree", "loctree": "loctree", "loctree-mcp": "loctree", "loctree-lsp": "loctree",
    "aicx": "aicx", "aicx-mcp": "aicx", "prview": "prview",
}
commands = {
    "vibecrafted": ["--version"], "vc-server": ["--version"], "voc": ["--version"],
    "vc-terminal": ["--version"], "vc-frame": ["--version"], "screenscribe": ["--version"],
    "loct": ["--version"], "loctree": ["--version"], "loctree-mcp": ["--version"],
    "loctree-lsp": ["--version"], "aicx": ["--version"], "aicx-mcp": ["--version"],
    "prview": ["--version"],
}
records = []
for name, argv in commands.items():
    path = root / "bin" / name
    output = subprocess.run([str(path), *argv], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=30, check=True).stdout.strip().splitlines()[0]
    owner = owners[name]
    if owner in sources:
        url, revision, archive_sha, license_name = sources[owner]
    else:
        revision = foundation.get("source_revisions", {}).get(owner, foundation["versions"].get(owner, "registry"))
        archive = foundation.get("source_archives", {}).get(owner, {})
        url, archive_sha = archive.get("url", "https://pypi.org/project/screenscribe/" if name == "screenscribe" else "https://crates.io/") , archive.get("sha256", "registry-integrity")
        license_name = foundation.get("licenses", {}).get(owner, "upstream-package-metadata")
    records.append({"name": name, "path": f"bin/{name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "version_argv": argv, "version_output": output, "source_url": url,
                    "source_revision": revision, "source_archive_sha256": archive_sha,
                    "target": "aarch64-unknown-linux-gnu", "license": license_name})
manifest = {"schema": "io.vetcoders.vibecrafted.runtime-inventory.v1", "platform": "linux",
            "architecture": "arm64", "executables": records}
(root / "runtime-inventory.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
PY

"$repo_root/scripts/package-runtime-pack.sh" --payload-root "$payload" --output "$output" \
  --source-revision "$source_revision" --terminal-revision "$terminal_revision" \
  --frame-revision "$frame_revision" --version "$version" \
  --platform linux --architecture arm64
