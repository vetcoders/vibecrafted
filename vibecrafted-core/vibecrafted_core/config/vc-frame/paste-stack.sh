#!/usr/bin/env bash
# paste-stack.sh — shared FIFO history for Composer / copy-scrollback / scrollback-select
# Storage: ~/.cache/vc-frame/paste-stack.json (max 50 entries, newest first)
#
# Spec 1.2 §B: push on copy/yank, pick via fzf (or numbered menu) for Composer Ctrl+p.
set -euo pipefail

STACK_DIR="${HOME}/.cache/vc-frame"
STACK_FILE="${STACK_DIR}/paste-stack.json"
MAX_ENTRIES=50

usage() {
  cat <<'EOF'
Usage: paste-stack.sh <command> [args]

  push [file|-]   Append content (file or stdin) as the newest stack entry
  top [file]      Write the newest entry to file (or stdout)
  pick            Interactive picker (fzf when available); print chosen body
  list            Print a numbered one-line preview of each entry
  get <n>         Print entry n (1-based) to stdout
  clear           Empty the stack
EOF
}

ensure_dir() {
  mkdir -p "$STACK_DIR"
}

cmd_push() {
  ensure_dir
  local src="${1:--}"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/vc-paste-push.XXXXXX")"
  if [[ "$src" == "-" ]]; then
    cat >"$tmp"
  else
    cat -- "$src" >"$tmp"
  fi
  python3 - "$STACK_FILE" "$tmp" "$MAX_ENTRIES" <<'PY'
import json, sys, pathlib
stack_path = pathlib.Path(sys.argv[1])
content = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8", errors="ignore")
max_entries = int(sys.argv[3])
if not content.strip():
    sys.exit(0)
stack = []
if stack_path.exists():
    try:
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
        if not isinstance(stack, list):
            stack = []
    except Exception:
        stack = []
if not stack or stack[0] != content:
    stack.insert(0, content)
stack = stack[:max_entries]
stack_path.parent.mkdir(parents=True, exist_ok=True)
stack_path.write_text(json.dumps(stack, ensure_ascii=False, indent=2), encoding="utf-8")
PY
  rm -f -- "$tmp"
}

cmd_top() {
  local dest="${1:-}"
  python3 - "$STACK_FILE" "$dest" <<'PY'
import json, sys, pathlib
stack_path = pathlib.Path(sys.argv[1])
dest = sys.argv[2]
if not stack_path.exists():
    sys.exit(0)
try:
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)
if not stack:
    sys.exit(0)
text = stack[0] if isinstance(stack[0], str) else str(stack[0])
if dest:
    pathlib.Path(dest).write_text(text, encoding="utf-8")
else:
    sys.stdout.write(text)
PY
}

cmd_list() {
  python3 - "$STACK_FILE" <<'PY'
import json, sys, pathlib
stack_path = pathlib.Path(sys.argv[1])
if not stack_path.exists():
    sys.exit(0)
try:
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)
for i, item in enumerate(stack, 1):
    line = item.splitlines()[0] if isinstance(item, str) and item else ""
    preview = (line[:72] + "…") if len(line) > 72 else line
    print(f"{i:2}. {preview}")
PY
}

cmd_get() {
  local n="${1:-1}"
  python3 - "$STACK_FILE" "$n" <<'PY'
import json, sys, pathlib
stack_path = pathlib.Path(sys.argv[1])
n = int(sys.argv[2])
if not stack_path.exists() or n < 1:
    sys.exit(1)
try:
    stack = json.loads(stack_path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
if n > len(stack):
    sys.exit(1)
text = stack[n - 1] if isinstance(stack[n - 1], str) else str(stack[n - 1])
sys.stdout.write(text)
PY
}

cmd_pick() {
  if [[ ! -f "$STACK_FILE" ]]; then
    echo "paste-stack: empty" >&2
    return 1
  fi
  local count
  count="$(python3 -c 'import json,pathlib,sys; p=pathlib.Path(sys.argv[1]);
s=json.loads(p.read_text()) if p.exists() else []; print(len(s) if isinstance(s,list) else 0)' "$STACK_FILE")"
  if [[ "${count:-0}" -eq 0 ]]; then
    echo "paste-stack: empty" >&2
    return 1
  fi

  local choice=""
  local self
  self="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/$(basename -- "${BASH_SOURCE[0]}")"
  if command -v fzf >/dev/null 2>&1; then
    # Preview shows the full body of the selected entry via this script.
    choice="$(
      python3 - "$STACK_FILE" <<'PY' | fzf --height=40% --reverse --prompt='paste-stack> ' \
        --preview "$self get {1}" --preview-window=down:60%:wrap
import json, pathlib, sys
stack = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for i, item in enumerate(stack, 1):
    line = item.splitlines()[0] if isinstance(item, str) and item else ""
    preview = (line[:72] + "…") if len(line) > 72 else line
    print(f"{i}\t{preview}")
PY
    )" || true
    if [[ -n "$choice" ]]; then
      local idx="${choice%%$'\t'*}"
      cmd_get "$idx"
      return 0
    fi
    return 1
  fi

  # Fallback: numbered menu on stderr, choice on stdin.
  cmd_list >&2
  printf 'pick number: ' >&2
  local n
  read -r -e n || return 1
  cmd_get "$n"
}

cmd_clear() {
  ensure_dir
  printf '[]\n' >"$STACK_FILE"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "$cmd" in
    push)  cmd_push "${1:--}" ;;
    top)   cmd_top "${1:-}" ;;
    pick)  cmd_pick ;;
    list)  cmd_list ;;
    get)   cmd_get "${1:-1}" ;;
    clear) cmd_clear ;;
    -h|--help|"") usage; [[ -n "$cmd" ]] || exit 2 ;;
    *) echo "unknown command: $cmd" >&2; usage; exit 2 ;;
  esac
}

main "$@"
