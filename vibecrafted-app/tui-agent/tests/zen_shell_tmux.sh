#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
VOC_BIN="${VOC_BIN:-$ROOT/target/debug/voc}"
STATE_ROOT="$ROOT/vibecrafted-app/tui-agent/tests/fixtures/zen_control_plane"
SOCKET="voc-zen-$PPID-$$"
SESSION="zen-shell"

cleanup() {
  tmux -L "$SOCKET" kill-server >/dev/null 2>&1 || true
}
trap cleanup EXIT

contains() {
  local screen="$1"
  local needle="$2"
  if [[ "$screen" != *"$needle"* ]]; then
    echo "ZEN PTY assertion failed: missing '$needle'" >&2
    echo "$screen" >&2
    exit 1
  fi
}

rejects() {
  local screen="$1"
  local needle="$2"
  if [[ "$screen" == *"$needle"* ]]; then
    echo "ZEN PTY assertion failed: unexpected '$needle'" >&2
    echo "$screen" >&2
    exit 1
  fi
}

tmux -L "$SOCKET" new-session -d -x 120 -y 32 -s "$SESSION" \
  "env PATH=/usr/bin:/bin VIBECRAFTED_HOME='$ROOT/vibecrafted-app/tui-agent/tests/fixtures' '$VOC_BIN' --state-root '$STATE_ROOT' --repo /tmp/voc-zen-fixture --deck /usr/bin/true --attention-working-rule --tick-ms 100"

for _ in 1 2 3 4 5 6 7 8 9 10; do
  screen="$(tmux -L "$SOCKET" capture-pane -p -t "$SESSION":0.0)"
  [[ "$screen" == *"live-codex"* ]] && break
  sleep 0.2
done
contains "$screen" "live-codex"
contains "$screen" "blocked-kimi"
contains "$screen" "failed-claude"
contains "$screen" "attention working rule on"

tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 -l '/kimi'
sleep 0.2
screen="$(tmux -L "$SOCKET" capture-pane -p -t "$SESSION":0.0)"
contains "$screen" "blocked-kimi"
rejects "$screen" "live-codex"
rejects "$screen" "failed-claude"

tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Escape
tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 -l '!observe blocked-kimi'
tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Enter
sleep 0.2
screen="$(tmux -L "$SOCKET" capture-pane -p -t "$SESSION":0.0)"
contains "$screen" "Conversation"
contains "$screen" "observe blocked-kimi"

tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Escape
tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Down Enter
sleep 0.2
screen="$(tmux -L "$SOCKET" capture-pane -p -t "$SESSION":0.0)"
contains "$screen" "Conversation"
contains "$screen" "observe failed-claude"

tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Escape
tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 -l '!resume failed-claude'
tmux -L "$SOCKET" send-keys -t "$SESSION":0.0 Enter
sleep 0.4
screen="$(tmux -L "$SOCKET" capture-pane -p -t "$SESSION":0.0)"
contains "$screen" "ran: /usr/bin/true resume claude --session session-failed-claude"

echo "ZEN_TMUX_OK query observe cursor-enter resume"
