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

# Reuse the App's Main-role boundary. This development wrapper already requires
# the Swift toolchain used by make app; no bundle-ID AppleEvent or name-based PID
# guess is safe when Main and Terminal share one signed bundle.
cat "$REPO_ROOT/vibecrafted-app/shell-agent/app/Vibecrafted/AppInstanceOwnership.swift" >"$TRANSACTION_DIR/main-quit.swift"
cat >>"$TRANSACTION_DIR/main-quit.swift" <<'MAIN_QUIT_SWIFT'
import AppKit

enum MainQuitFailure: Error {
  case unsafe(String)
}

func installMainPeer(
  _ app: NSRunningApplication, destination: URL, identifier: String
) throws -> AppInstancePeer? {
  guard !app.isTerminated else { return nil }
  guard let bundle = app.bundleURL else {
    throw MainQuitFailure.unsafe("missing running bundle identity")
  }
  guard bundle.standardizedFileURL == destination else { return nil }
  guard app.bundleIdentifier == identifier, app.processIdentifier > 0,
    let executable = app.executableURL, executable.isFileURL,
    executable.query == nil, executable.fragment == nil,
    let main = AppInstanceOwnership.signedMainExecutableURL(
      bundleURL: bundle, bundleIdentifier: identifier)
  else { throw MainQuitFailure.unsafe("missing destination executable/signature evidence") }
  var peer = AppInstancePeer(
    pid: app.processIdentifier, bundleURL: bundle, executableURL: executable,
    signedMainExecutableURL: main)
  if AppInstanceOwnership.isMainHost(peer) {
    guard let launchDate = app.launchDate else {
      throw MainQuitFailure.unsafe("missing destination Main start evidence")
    }
    peer.launchDate = launchDate
    return peer
  }
  // Known executable evidence inside the destination admits helper exclusion;
  // it is never a quit target, so it needs no launchDate. Foreign or malformed
  // evidence cannot silently turn into "no Main".
  guard executable.host == nil || executable.host == "localhost",
    executable.standardizedFileURL.deletingLastPathComponent()
      == main.standardizedFileURL.deletingLastPathComponent()
  else { throw MainQuitFailure.unsafe("foreign destination executable evidence") }
  return nil
}

func installMainSnapshot(destination: URL, identifier: String) throws -> [AppInstancePeer] {
  let peers = try NSRunningApplication.runningApplications(withBundleIdentifier: identifier)
    .compactMap { try installMainPeer($0, destination: destination, identifier: identifier) }
  guard peers.count <= 1 else { throw MainQuitFailure.unsafe("ambiguous destination Main instances") }
  return peers
}

func installMainStart(_ pid: Int32) throws -> String {
  let process = Process()
  process.executableURL = URL(fileURLWithPath: "/bin/ps")
  process.arguments = ["-o", "lstart=", "-p", String(pid)]
  process.environment = ["PATH": "/usr/bin:/bin", "LC_ALL": "C"]
  let output = Pipe()
  process.standardOutput = output
  process.standardError = FileHandle.nullDevice
  try process.run()
  let data = output.fileHandleForReading.readDataToEndOfFile()
  process.waitUntilExit()
  guard process.terminationStatus == 0,
    let raw = String(data: data, encoding: .utf8)
  else { throw MainQuitFailure.unsafe("unavailable Main process start") }
  let start = raw.trimmingCharacters(in: .whitespacesAndNewlines)
  guard start.range(
    of: #"^[A-Z][a-z]{2} [A-Z][a-z]{2} +[0-9]{1,2} [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}$"#,
    options: .regularExpression) != nil
  else {
    throw MainQuitFailure.unsafe("malformed Main process start")
  }
  return start
}

do {
  guard CommandLine.arguments.count == 2 else { throw MainQuitFailure.unsafe("missing destination") }
  let destination = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
  let identifier = AppInstanceOwnership.productBundleIdentifier
  if let selected = try installMainSnapshot(destination: destination, identifier: identifier).first {
    let start = try installMainStart(selected.pid)
    guard try installMainSnapshot(destination: destination, identifier: identifier) == [selected],
      let app = NSRunningApplication(processIdentifier: selected.pid),
      try installMainPeer(app, destination: destination, identifier: identifier) == selected,
      try installMainStart(selected.pid) == start
    else { throw MainQuitFailure.unsafe("destination Main identity drifted before quit") }
    guard app.terminate() else { throw MainQuitFailure.unsafe("Main refused graceful termination") }
    print(selected.pid)
    print(start)
  } else {
    guard try installMainSnapshot(destination: destination, identifier: identifier).isEmpty else {
      throw MainQuitFailure.unsafe("destination Main appeared during identity check")
    }
  }
} catch {
  FileHandle.standardError.write(Data("install-app: refusing Main quit: \(error)\n".utf8))
  exit(EXIT_FAILURE)
}
MAIN_QUIT_SWIFT

QUIT_IDENTITY="$(/usr/bin/swift "$TRANSACTION_DIR/main-quit.swift" "$DESTINATION")"
if [[ -n "$QUIT_IDENTITY" ]]; then
  PID="${QUIT_IDENTITY%%$'\n'*}"
  LSTART="${QUIT_IDENTITY#*$'\n'}"
  [[ "$PID" =~ ^[1-9][0-9]*$ && "$LSTART" != "$QUIT_IDENTITY" && -n "$LSTART" ]] || {
    echo "install-app: malformed Main quit receipt" >&2
    exit 1
  }
  echo "[install-app] gracefully quitting destination Main (pid $PID) — frame sessions survive"
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
