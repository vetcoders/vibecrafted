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

# Keep the first refusal durable. Recovery below never re-enters replacement.
if bash "$HELPER" "${ARGS[@]}" 2>"$TRANSACTION_DIR/complete.stderr"; then
  cat "$TRANSACTION_DIR/complete.stderr" >&2
else
  helper_status=$?
  cat "$TRANSACTION_DIR/complete.stderr" >&2
  [[ "$helper_status" -eq 20 ]] || exit "$helper_status"
  /usr/bin/python3 - "$SOURCE_APP" "$DESTINATION" "$HELPER" "$TRANSACTION_DIR" "$TRANSACTION" <<'SETTLEMENT_RECOVERY_PY'
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

source, destination, helper, transaction_dir, transaction = sys.argv[1:]
source, destination, helper, transaction_dir = map(
    lambda value: Path(os.path.abspath(value)), (source, destination, helper, transaction_dir)
)
deadline = time.monotonic() + 60
bindings = {}
descriptor = None


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def physical(path):
    for component in [*reversed(path.parents), path]:
        require(not component.is_symlink(), "symlink in recovery binding")
    return path


def read_bound(path):
    physical(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1, "unowned recovery evidence")
        with os.fdopen(os.dup(fd), "rb") as stream:
            data = stream.read(128 * 1024 * 1024 + 1)
        require(len(data) <= 128 * 1024 * 1024, "oversized recovery evidence")
        after = os.fstat(fd)
        named = path.lstat()
        stamp = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns,
                              item.st_ctime_ns, item.st_mode, item.st_uid, item.st_nlink)
        require(stamp(before) == stamp(after) == stamp(named), "recovery evidence changed")
        observed = (stamp(after), data)
        require(path not in bindings or bindings[path] == observed, "recovery binding drift")
        bindings[path] = observed
        return data
    finally:
        os.close(fd)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def decode(data):
    value = json.loads(data, object_pairs_hook=unique_object)
    require(isinstance(value, dict), "invalid recovery JSON object")
    return value


def remaining():
    seconds = deadline - time.monotonic()
    require(seconds > 0, "settlement recovery deadline expired")
    return seconds


def run(arguments):
    return subprocess.run(arguments, env=environment, capture_output=True, text=True,
                          timeout=remaining(), check=False)


def signed_identity(app):
    require(run(["/usr/bin/codesign", "--verify", "--strict", str(app)]).returncode == 0,
            "signed recovery App refused")
    shown = run(["/usr/bin/codesign", "--display", "--verbose=4", str(app)])
    require(shown.returncode == 0, "missing signed App identity")
    fields = dict(line.split("=", 1) for line in (shown.stdout + shown.stderr).splitlines() if "=" in line)
    require(fields.get("Identifier") == receipt.get("identifier")
            and fields.get("TeamIdentifier") == receipt.get("team_id")
            and re.fullmatch(r"[0-9a-f]+", fields.get("CDHash", "")), "wrong signed App owner")
    return "cdhash:" + fields["CDHash"]


try:
    in_progress = {"schema": "vibecrafted.runtime-resolution.v1", "status": "unusable",
                   "reason": "runtime installation is in progress", "runtime": None}
    prefix = "capture settlement incomplete (retry with the same transaction): canonical destination verifier refused: "
    lines = read_bound(transaction_dir / "complete.stderr").decode().strip().splitlines()
    require(lines and lines[-1].startswith(prefix)
            and decode(lines[-1][len(prefix):]) == in_progress, "not the canonical in-progress refusal")
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONPYCACHEPREFIX", "VIBECRAFTED_INSTALL_LEASE_FD"):
        environment.pop(key, None)
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    receipt_path, admission_path, journal_path = [transaction_dir / name for name in
                                               ("receipt.json", "admission.json", "journal.json")]
    receipt, admission, journal = [decode(read_bound(path)) for path in
                                   (receipt_path, admission_path, journal_path)]
    for document, schema in (
        (receipt, "io.vetcoders.vibecrafted.app-replacement.v1"),
        (admission, "io.vetcoders.vibecrafted.app-replacement-admission.v1"),
        (journal, "io.vetcoders.vibecrafted.app-update-journal.v1"),
    ):
        require(document.get("schema") == schema and document.get("transaction") == transaction
                and document.get("destination") == str(destination)
                and document.get("mode") == document.get("operation") == "replace"
                and document.get("source_identity") == receipt.get("source_identity")
                and document.get("identifier") == receipt.get("identifier")
                and document.get("team_id") == receipt.get("team_id")
                and document.get("capture") == str(destination.parent / (".vc-update-capture-" + transaction)),
                "wrong recovery transaction")
    require(receipt.get("replaced") is True and receipt.get("relaunched") is True
            and receipt.get("detail") == "replaced" and admission.get("status") == "ready"
            and journal.get("phase") == "relaunched", "replacement is not already relaunched")
    require(receipt.get("journal") == admission.get("journal") == str(journal_path)
            and admission.get("receipt") == journal.get("receipt") == str(receipt_path)
            and admission.get("source") == journal.get("source") == str(source)
            and journal.get("candidate_identity") == receipt.get("source_identity")
            and journal.get("parent") == str(destination.parent), "wrong recovery owner paths")
    app_inodes = {app: (physical(app).stat().st_dev, app.stat().st_ino) for app in (source, destination)}
    require(signed_identity(source) == signed_identity(destination) == receipt.get("source_identity"),
            "source or destination signed identity drift")
    product = decode(read_bound(source / "Contents/Resources/product-manifest.json"))
    require(read_bound(destination / "Contents/Resources/product-manifest.json")
            == read_bound(source / "Contents/Resources/product-manifest.json"), "source product identity differs")
    runtime = Path(os.path.abspath(environment.get("VIBECRAFTED_RUNTIME_HOME") or
                   str(Path(environment.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")) / "vibecrafted")))
    pointer = decode(read_bound(runtime / "active.json"))
    installed = decode(read_bound(runtime / "install-receipt.json"))
    version = pointer.get("version", "")
    require(isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9.+_-]+", version), "invalid publication version")
    generation = physical(runtime / "releases" / version)
    require(pointer.get("schema") == "vibecrafted.active-runtime.v1"
            and installed.get("schema") == "vibecrafted.runtime-install.v1"
            and installed.get("version") == version and pointer.get("runtime_root") == str(generation)
            and pointer.get("app_root") == installed.get("app_root") == str(destination), "wrong published App")
    manifest = decode(read_bound(generation / "runtime-manifest.json"))
    require(manifest.get("source_revision") == product.get("git_sha")
            and manifest.get("version") == version and re.fullmatch(r"[0-9a-f]{40}", product.get("git_sha", "")),
            "published source does not match replacement")
    current = runtime / "tools/vibecrafted-current"
    physical(current.parent)
    selector = (current.lstat().st_dev, current.lstat().st_ino, os.readlink(current))
    python = generation / "bin/python3"
    verifier = destination / "Contents/Resources/runtime/scripts/vetcoders_install.py"
    for path in (python, verifier, helper):
        read_bound(path)

    def unchanged():
        remaining()
        for path in list(bindings):
            read_bound(path)
        for app, identity in app_inodes.items():
            require((physical(app).stat().st_dev, app.stat().st_ino) == identity, "App directory replaced")
        require((current.lstat().st_dev, current.lstat().st_ino, os.readlink(current)) == selector
                and current.resolve(strict=True) == generation, "runtime selector drift")

    def resolve():
        checked = run([str(python), "-B", str(verifier), "runtime-resolve", "--runtime-home", str(runtime), "--json"])
        require(not checked.stderr, "canonical resolver diagnostic")
        value = decode(checked.stdout)
        if checked.returncode == 2 and value == in_progress:
            return False
        resolved = value.get("runtime")
        require(checked.returncode == 0 and value.get("schema") == "vibecrafted.runtime-resolution.v1"
                and value.get("status") == "ready" and value.get("reason") == ""
                and isinstance(resolved, dict) and resolved.get("root") == str(generation)
                and resolved.get("app_root") == str(destination), "canonical destination refused recovery")
        return True

    unchanged()
    lock = physical(runtime / "tools/.vibecrafted-install.lock")
    descriptor = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    lock_inode = (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino)

    def validate_lock():
        opened, named = os.fstat(descriptor), physical(lock).lstat()
        require(stat.S_ISREG(opened.st_mode) and opened.st_uid == os.geteuid()
                and opened.st_nlink == 1 and (opened.st_dev, opened.st_ino)
                == (named.st_dev, named.st_ino) == lock_inode, "runtime lock identity drift")

    validate_lock()
    resolve()  # The original contention may already have ended; both answers are bound.
    while True:
        validate_lock()
        unchanged()
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            time.sleep(min(0.05, remaining()))
    validate_lock()
    unchanged()
    require(signed_identity(source) == signed_identity(destination) == receipt.get("source_identity"),
            "signed identity changed while waiting")
    require(resolve(), "canonical runtime is still in progress under shared lease")
    unchanged()
    result = run(["/bin/bash", str(helper), "--mode", "settle", "--source", str(source),
                  "--destination", str(destination), "--receipt", str(receipt_path),
                  "--admission", str(admission_path), "--journal", str(journal_path), "--transaction", transaction])
    with (transaction_dir / "settlement-retry.stderr").open("x") as stream:
        stream.write(result.stderr)
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    require(result.returncode == 0, "canonical settlement-only retry refused")
    validate_lock()
    unchanged()
except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
    print("install-app: settlement recovery refused: " + str(error), file=sys.stderr)
    sys.exit(20)
finally:
    if descriptor is not None:
        os.close(descriptor)
SETTLEMENT_RECOVERY_PY
fi
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
