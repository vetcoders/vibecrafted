#!/usr/bin/env bash
# copy-scrollback.sh — Dump focused pane scrollback, copy to clipboard (pbcopy) and push to Paste Stack
set -euo pipefail

tmp=$(mktemp -t vc-pane-dump.XXXXXX)
trap 'rm -f "$tmp"' EXIT

current="${VC_FRAME_PANE_ID:-}"

pane_id=$(vc-frame action list-panes --json --all --state 2>/dev/null | python3 -c '
import json, os, sys
current = os.environ.get("VC_FRAME_PANE_ID", "")
try:
    panes = json.load(sys.stdin)
    # 1. Look for focused pane that is not the floating command script pane
    match = next((p for p in panes if p.get("is_focused") and str(p.get("id")) != current), None)
    # 2. Fallback to first non-plugin active terminal pane if floating pane stole focus
    if not match:
        match = next((p for p in panes if not p.get("is_plugin") and str(p.get("id")) != current), None)
    if match:
        prefix = "plugin_" if match.get("is_plugin") else ""
        print(f"{prefix}{match.get(\"id\")}")
except Exception:
    pass
' || true)

if [[ -n "$pane_id" ]]; then
    vc-frame action dump-screen --full --pane-id "$pane_id" --path "$tmp"
    pbcopy < "$tmp"
else
    vc-frame action dump-screen --full --path "$tmp"
    pbcopy < "$tmp"
fi

# Append to Paste Stack history (FIFO max 50) — shared with Composer seed/push.
if [[ -s "$tmp" ]]; then
    paste_stack=""
    for candidate in \
        "${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/vc-frame/paste-stack.sh" \
        "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/paste-stack.sh"
    do
        if [[ -x "$candidate" ]]; then
            paste_stack="$candidate"
            break
        fi
    done
    if [[ -n "$paste_stack" ]]; then
        "$paste_stack" push "$tmp" || true
    else
        cache_dir="${HOME}/.cache/vc-frame"
        mkdir -p "$cache_dir"
        stack_file="${cache_dir}/paste-stack.json"
        python3 -c '
import json, os, sys
tmp_file, stack_file = sys.argv[1], sys.argv[2]
try:
    content = open(tmp_file, "r", encoding="utf-8", errors="ignore").read()
    if not content.strip():
        raise SystemExit(0)
    stack = []
    if os.path.exists(stack_file):
        try:
            stack = json.load(open(stack_file, "r", encoding="utf-8"))
        except Exception:
            stack = []
    if not stack or stack[0] != content:
        stack.insert(0, content)
    stack = stack[:50]
    json.dump(stack, open(stack_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
except Exception:
    pass
' "$tmp" "$stack_file" || true
    fi
fi
