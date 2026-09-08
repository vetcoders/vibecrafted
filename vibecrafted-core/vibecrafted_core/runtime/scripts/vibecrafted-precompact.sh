#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/util.sh
source "$script_dir/lib/util.sh"
repo_root="$(cd "$script_dir/../.." && pwd)"
core_dir="${VIBECRAFTED_CORE_DIR:-$repo_root/vibecrafted-core}"

PYTHONPATH="$core_dir${PYTHONPATH:+:$PYTHONPATH}" \
  "$(spawn_python_bin)" -m vibecrafted_core.compact_hooks precompact
