#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/util.sh
source "$script_dir/lib/util.sh"
core_dir="${VIBECRAFTED_CORE_DIR:-$(cd "$script_dir/../../.." && pwd)}"

PYTHONPATH="$core_dir${PYTHONPATH:+:$PYTHONPATH}" \
  "$(spawn_python_bin)" -m vibecrafted_core.loop "$@"
