#!/usr/bin/env bash
# install-app.sh — fast iteration loop for Vibecrafted.app: build (make app)
# already happened; this script is the CLI driver over the sole UI-bundle
# mutation owner (scripts/vc-app-update.sh). Unlike the pensieve dance
# (pkill + ditto), the swap goes through the journaled transaction helper:
# preflight signature check, prior.app backup, wait-for-parent, receipt,
# relaunch. Frame sessions, PTYs and workers survive by design — the helper
# never touches them.
#
# Quits a running Vibecrafted.app first: invoking `make install-app` is the
# Founder's gate for replacing the bundle under a live workspace.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_APP="$REPO_ROOT/dist/Vibecrafted.app"
DESTINATION="${INSTALL_APP_DESTINATION:-/Applications/Vibecrafted.app}"
STATE_DIR="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/vibecrafted-product-update"
HELPER="$REPO_ROOT/scripts/vc-app-update.sh"

[[ -d "$SOURCE_APP" ]] || {
  echo "install-app: no built app at $SOURCE_APP — run \`make app\` first (or use \`make install-app\`)" >&2
  exit 1
}
[[ -f "$HELPER" ]] || { echo "install-app: missing $HELPER" >&2; exit 1; }
mkdir -p "$STATE_DIR"

TRANSACTION="install-app-$(/usr/bin/uuidgen | /usr/bin/tr '[:upper:]' '[:lower:]')"
TRANSACTION_DIR="$STATE_DIR/transactions/$TRANSACTION"
mkdir -p "$TRANSACTION_DIR"
ARGS=(
  --source "$SOURCE_APP"
  --destination "$DESTINATION"
  --receipt "$TRANSACTION_DIR/receipt.json"
  --admission "$TRANSACTION_DIR/admission.json"
  --journal "$TRANSACTION_DIR/journal.json"
  --transaction "$TRANSACTION"
  --mode replace
  --complete
  --relaunch
)

if PID="$(pgrep -x Vibecrafted | head -1)" && [[ -n "$PID" ]]; then
  LSTART="$(ps -o lstart= -p "$PID" | sed 's/^ *//')"
  echo "[install-app] quitting running Vibecrafted (pid $PID) — frame sessions survive"
  osascript -e 'tell application id "io.vetcoders.vibecrafted" to quit' >/dev/null 2>&1 || true
  ARGS+=(--wait-pid "$PID" --wait-start "$LSTART" --wait-timeout 60)
fi

bash "$HELPER" "${ARGS[@]}"
# Latest is an atomic projection; the unique transaction records are authority.
/usr/bin/python3 - "$STATE_DIR/install-app-latest.json" "$TRANSACTION_DIR/receipt.json" "$TRANSACTION" <<'PY'
import json, os, sys
from pathlib import Path
pointer, receipt, transaction = sys.argv[1:]
path = Path(pointer)
for item in [*reversed(path.parents), path]:
    if item.is_symlink():
        raise SystemExit("install-app: refusing symlink latest projection")
temporary = pointer + ".tmp." + str(os.getpid())
with open(temporary, "x") as handle:
    json.dump({"transaction": transaction, "receipt": receipt, "settlement": receipt + ".settlement.json"}, handle)
    handle.flush()
    os.fsync(handle.fileno())
os.replace(temporary, pointer)
PY
echo "[install-app] settled receipt: $TRANSACTION_DIR/receipt.json.settlement.json"
