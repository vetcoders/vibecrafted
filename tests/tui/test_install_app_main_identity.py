"""Install wrapper Main selection, using fenced private apps and process doubles."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from _runtime_pack_fixture import private_child_command

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="macOS NSRunningApplication wrapper contract"
)

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts/install-app.sh"
OWNERSHIP = (
    ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppInstanceOwnership.swift"
)


def _executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _child(command: list[str], *, allowed: tuple[Path, ...] = ()) -> list[str]:
    if sys.platform != "darwin":
        pytest.fail("Main installer tests require the child-only Seatbelt fence")
    return private_child_command(command, allowed=allowed)


@pytest.fixture
def fence(tmp_path: Path) -> None:
    # Verify absolute escapes before a wrapper invocation; PATH alone is not
    # an envelope. No denied program reaches execution, including AppleEvents.
    for command in (
        ["/usr/bin/osascript", "-e", "return 1"],
        ["/usr/bin/open", "-h"],
        ["/bin/ps", "-p", "1"],
        ["/usr/bin/swift", "--version"],
        ["/bin/cat", "/Applications/Vibecrafted.app/Contents/Info.plist"],
    ):
        result = subprocess.run(
            _child(command), capture_output=True, text=True, timeout=10, check=False
        )
        assert result.returncode != 0, (command, result.stdout, result.stderr)
    private = _executable(
        tmp_path / "allowed", "#!/bin/sh\nprintf 'private-child\\n'\n"
    )
    result = subprocess.run(
        _child([str(private)], allowed=(private,)),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "private-child\n"


def _logs(path: Path) -> list[list[str]]:
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


FAKE_APPKIT = r"""
final class NSRunningApplication {
  nonisolated(unsafe) private static var censusRead = 0
  private static func snapshot() -> [[String: Any]] {
    let data = ProcessInfo.processInfo.environment["MAIN_FAKE_CENSUS"]!.data(using: .utf8)!
    let snapshots = try! JSONSerialization.jsonObject(with: data) as! [[[String: Any]]]
    let index = min(censusRead, snapshots.count - 1)
    censusRead += 1
    return snapshots[index]
  }
  static func runningApplications(withBundleIdentifier identifier: String) -> [NSRunningApplication] {
    snapshot().map { NSRunningApplication($0) }
  }
  convenience init?(processIdentifier pid: Int32) {
    guard let row = Self.snapshot().first(where: { ($0["pid"] as! NSNumber).int32Value == pid })
    else { return nil }
    self.init(row)
  }
  private let row: [String: Any]
  init(_ row: [String: Any]) { self.row = row }
  var processIdentifier: Int32 { (row["pid"] as! NSNumber).int32Value }
  var bundleIdentifier: String? { row["identifier"] as? String }
  private func url(_ field: String) -> URL? {
    guard let value = row[field] as? String else { return nil }
    return value.hasPrefix("/") ? URL(fileURLWithPath: value) : URL(string: value)
  }
  var bundleURL: URL? { url("bundle") }
  var executableURL: URL? { url("executable") }
  var launchDate: Date? {
    (row["start"] as? NSNumber).map { Date(timeIntervalSince1970: $0.doubleValue) }
  }
  var isTerminated: Bool { row["terminated"] as? Bool ?? false }
  func terminate() -> Bool {
    let success = row["graceful"] as? Bool ?? true
    let event = [success ? "terminate" : "terminate-refused", String(processIdentifier)]
    let path = ProcessInfo.processInfo.environment["MAIN_TEST_LOG"]!
    if !FileManager.default.fileExists(atPath: path) {
      FileManager.default.createFile(atPath: path, contents: nil)
    }
    let handle = FileHandle(forWritingAtPath: path)!
    handle.seekToEndOfFile()
    handle.write(try! JSONSerialization.data(withJSONObject: event))
    handle.write(Data("\n".utf8))
    try! handle.close()
    return success
  }
}
"""


def _replace_signed_probe(source: str) -> str:
    # The only substituted ownership method is OS signature evidence. Role
    # admission, peer selection and identity comparison remain product code.
    start = source.index("  static func signedMainExecutableURL(")
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    signature = source[start:opening]
    body = r"""{
    let data = ProcessInfo.processInfo.environment["MAIN_FAKE_SIGNATURE"]!.data(using: .utf8)!
    let signatures = try! JSONSerialization.jsonObject(with: data) as! [String: String]
    guard let path = signatures[bundleURL.standardizedFileURL.path] else { return nil }
    return path.hasPrefix("/") ? URL(fileURLWithPath: path) : URL(string: path)
  }"""
    return (source[:start] + signature + body + source[end:]).replace(
        "import Security\n", ""
    )


@dataclass
class Harness:
    model: str
    wrapper: str
    source: str
    binary: Path
    ps: Path
    ps_config: Path


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Harness:
    swiftc = shutil.which("swiftc")
    assert swiftc is not None, "scoped Swift compiler is required"
    directory = tmp_path_factory.mktemp("install-main-harness")
    ps_config = directory / "ps-config.json"
    ps = _executable(
        directory / "private-ps",
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        f"path = Path({str(ps_config)!r})\n"
        "config = json.loads(path.read_text())\n"
        "assert sys.argv[1:3] == ['-o', 'lstart='] and sys.argv[3] == '-p'\n"
        "count = config['count']\n"
        "config['count'] += 1\n"
        "path.write_text(json.dumps(config))\n"
        "with open(config['log'], 'a') as stream:\n"
        "    stream.write(json.dumps(['ps', sys.argv[4], str(count)]) + '\\n')\n"
        "print(config['starts'][min(count, len(config['starts']) - 1)])\n"
        "raise SystemExit(config['exit'])\n",
    )
    wrapper = WRAPPER.read_text(encoding="utf-8")
    marker = "<<'MAIN_QUIT_SWIFT'\n"
    assert wrapper.count(marker) == 1
    inline = wrapper.split(marker, 1)[1].split("\nMAIN_QUIT_SWIFT\n", 1)[0] + "\n"
    assert inline.count("import AppKit\n") == 1
    assert inline.count('"/bin/ps"') == 1
    model = _replace_signed_probe(OWNERSHIP.read_text(encoding="utf-8"))
    wrapper = wrapper.replace("import AppKit\n", FAKE_APPKIT + "\n").replace(
        '"/bin/ps"', json.dumps(str(ps))
    )
    inline = inline.replace("import AppKit\n", FAKE_APPKIT + "\n").replace(
        '"/bin/ps"', json.dumps(str(ps))
    )
    source = model + inline
    swift = directory / "main.swift"
    swift.write_text(source, encoding="utf-8")
    binary = directory / "private-main-selection"
    target = "arm64" if os.uname().machine == "arm64" else "x86_64"
    subprocess.run(
        [
            swiftc,
            "-swift-version",
            "6",
            "-target",
            f"{target}-apple-macosx14.0",
            str(swift),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=180,
    )
    return Harness(model, wrapper, source, binary, ps, ps_config)


def _private_wrapper(
    tmp_path: Path, harness: Harness
) -> tuple[Path, dict[str, str], tuple[Path, ...]]:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    (repository / "dist/Vibecrafted.app").mkdir(parents=True)
    wrapper = scripts / "install-app.sh"
    private_model = repository / OWNERSHIP.relative_to(ROOT)
    private_model.parent.mkdir(parents=True)
    private_model.write_text(harness.model, encoding="utf-8")
    expected = tmp_path / "expected-main.swift"
    expected.write_text(harness.source, encoding="utf-8")
    runner = _executable(
        tmp_path / "private-swift",
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        f"assert Path(sys.argv[1]).read_bytes() == Path({str(expected)!r}).read_bytes()\n"
        f"os.execv({str(harness.binary)!r}, [{str(harness.binary)!r}, *sys.argv[2:]])\n",
    )
    assert harness.wrapper.count("/usr/bin/swift ") == 1
    wrapper.write_text(
        harness.wrapper.replace("/usr/bin/swift ", f"{shlex.quote(str(runner))} "),
        encoding="utf-8",
    )
    environment = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "VIBECRAFTED_HOME": str(tmp_path / "state"),
        "INSTALL_APP_DESTINATION": str(tmp_path / "Applications/Vibecrafted.app"),
        "MAIN_TEST_LOG": str(tmp_path / "events.jsonl"),
    }
    helper = _executable(
        scripts / "vc-app-update.sh",
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with open(os.environ['MAIN_TEST_LOG'], 'a') as stream:\n"
        "    stream.write(json.dumps(['helper', *sys.argv[1:]]) + '\\n')\n"
        "for option in ('--receipt', '--admission', '--journal'):\n"
        "    target = Path(sys.argv[sys.argv.index(option) + 1])\n"
        "    target.write_text(json.dumps({'historical': option}))\n"
        "raise SystemExit(int(os.environ.get('MAIN_HELPER_EXIT', '0')))\n",
    )
    # vc-app-update.sh is invoked with bash; retain that interface while the
    # stub records argv and historical receipts through this private witness.
    helper_python = helper.with_suffix(".py")
    helper.rename(helper_python)
    _executable(
        helper,
        f'#!/bin/bash\nexec {shlex.quote(sys.executable)} {shlex.quote(str(helper_python))} "$@"\n',
    )
    return wrapper, environment, (helper_python, runner, harness.binary, harness.ps)


def _peer(destination: str, pid: int, executable: str = "Vibecrafted") -> dict:
    return {
        "pid": pid,
        "bundle": destination,
        "identifier": "io.vetcoders.vibecrafted",
        "executable": f"{destination}/Contents/MacOS/{executable}",
        "start": 1_760_397_618,
    }


def _run(
    tmp_path: Path,
    harness: Harness,
    peers: list[dict],
    *,
    snapshots: list[list[dict]] | None = None,
    signatures: dict[str, str] | None = None,
    starts: list[str] | None = None,
    ps_exit: int = 0,
    helper_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    wrapper, environment, allowed = _private_wrapper(tmp_path, harness)
    destination = environment["INSTALL_APP_DESTINATION"]
    environment["MAIN_FAKE_CENSUS"] = json.dumps(snapshots or [peers, peers, peers])
    environment["MAIN_FAKE_SIGNATURE"] = json.dumps(
        signatures
        if signatures is not None
        else {destination: f"{destination}/Contents/MacOS/Vibecrafted"}
    )
    environment["MAIN_HELPER_EXIT"] = str(helper_exit)
    harness.ps_config.write_text(
        json.dumps(
            {
                "count": 0,
                "starts": starts or ["Tue Oct 13 23:26:58 2026"],
                "log": environment["MAIN_TEST_LOG"],
                "exit": ps_exit,
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        _child(["/bin/bash", str(wrapper)], allowed=allowed),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return (
        result,
        _logs(Path(environment["MAIN_TEST_LOG"])),
        Path(environment["VIBECRAFTED_HOME"]),
    )


def _destination(tmp_path: Path) -> str:
    return str(tmp_path / "Applications/Vibecrafted.app")


def _helper_args(events: list[list[str]]) -> list[str]:
    helpers = [event[1:] for event in events if event[0] == "helper"]
    assert len(helpers) == 1
    return helpers[0]


def _assert_refused(
    result: subprocess.CompletedProcess[str], events: list[list[str]]
) -> None:
    assert result.returncode != 0
    assert "refusing Main quit" in result.stderr
    assert not any(event[0] in {"helper", "terminate"} for event in events)


def test_child_fence_refuses_absolute_gui_and_live_process_escape(fence: None) -> None:
    assert fence is None


def test_shared_bundle_terminal_is_not_the_quit_target(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    destination = _destination(tmp_path)
    result, events, state = _run(
        tmp_path,
        harness,
        [
            _peer(destination, 57391),
            _peer(destination, 14649, "vc-terminal"),
            _peer(destination, 97410, "vc-terminal"),
        ],
    )
    assert result.returncode == 0, result.stderr
    assert ["terminate", "57391"] in events
    assert not any(
        event[:2] in (["terminate", "14649"], ["terminate", "97410"])
        for event in events
    )
    assert events.index(["terminate", "57391"]) < next(
        i for i, event in enumerate(events) if event[0] == "helper"
    )
    args = _helper_args(events)
    assert args[args.index("--wait-pid") + 1] == "57391"
    assert args[args.index("--wait-start") + 1] == "Tue Oct 13 23:26:58 2026"
    assert args[args.index("--wait-timeout") + 1] == "60"
    assert "--complete" in args and "--relaunch" in args
    assert args[args.index("--destination") + 1] == destination
    latest = json.loads(
        (state / "vibecrafted-product-update/install-app-latest.json").read_text()
    )
    assert latest["receipt"] == args[args.index("--receipt") + 1]
    assert latest["settlement"] == latest["receipt"] + ".settlement.json"


@pytest.mark.parametrize(
    "peer_kind", ["terminal-only", "no-peer", "foreign-destination", "terminated"]
)
def test_without_destination_main_updates_without_quitting(
    tmp_path: Path, fence: None, harness: Harness, peer_kind: str
) -> None:
    destination = _destination(tmp_path)
    peers = {
        "terminal-only": [_peer(destination, 14649, "vc-terminal")],
        "no-peer": [],
        "foreign-destination": [_peer(str(tmp_path / "other/Vibecrafted.app"), 57391)],
        "terminated": [{**_peer(destination, 57391), "terminated": True}],
    }[peer_kind]
    result, events, _ = _run(tmp_path, harness, peers)
    assert result.returncode == 0, result.stderr
    assert not any(event[0] in {"terminate", "ps"} for event in events)
    assert "--wait-pid" not in _helper_args(events)


def test_legitimate_main_is_gracefully_quit(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    result, events, _ = _run(tmp_path, harness, [_peer(_destination(tmp_path), 57391)])
    assert result.returncode == 0, result.stderr
    assert [event for event in events if event[0] == "terminate"] == [
        ["terminate", "57391"]
    ]
    assert [event for event in events if event[0] == "ps"] == [
        ["ps", "57391", "0"],
        ["ps", "57391", "1"],
    ]
    _helper_args(events)


@pytest.mark.parametrize("with_main", [False, True])
def test_verified_terminal_without_launch_date_is_excluded(
    tmp_path: Path, fence: None, harness: Harness, with_main: bool
) -> None:
    destination = _destination(tmp_path)
    terminal = {**_peer(destination, 14649, "vc-terminal"), "start": None}
    peers = [terminal]
    if with_main:
        peers.append(_peer(destination, 57391))
    result, events, _ = _run(tmp_path, harness, peers)
    assert result.returncode == 0, result.stderr
    terminations = [event for event in events if event[0] == "terminate"]
    assert terminations == ([["terminate", "57391"]] if with_main else [])
    assert not any(event[:2] == ["ps", "14649"] for event in events)
    args = _helper_args(events)
    if with_main:
        assert args[args.index("--wait-pid") + 1] == "57391"
        assert args[args.index("--wait-start") + 1] == "Tue Oct 13 23:26:58 2026"
    else:
        assert "--wait-pid" not in args


@pytest.mark.parametrize("missing", ["bundle", "identifier", "executable"])
@pytest.mark.parametrize("role", ["Vibecrafted", "vc-terminal"])
def test_missing_running_identity_refuses_before_quit(
    tmp_path: Path, fence: None, harness: Harness, missing: str, role: str
) -> None:
    peer = _peer(_destination(tmp_path), 57391, role)
    del peer[missing]
    result, events, _ = _run(tmp_path, harness, [peer])
    _assert_refused(result, events)


@pytest.mark.parametrize("missing", [True, False])
def test_selected_main_without_launch_date_still_refuses(
    tmp_path: Path, fence: None, harness: Harness, missing: bool
) -> None:
    destination = _destination(tmp_path)
    main = _peer(destination, 57391)
    if missing:
        del main["start"]
    else:
        main["start"] = None
    terminal = {**_peer(destination, 14649, "vc-terminal"), "start": None}
    result, events, _ = _run(tmp_path, harness, [terminal, main])
    _assert_refused(result, events)


@pytest.mark.parametrize(
    "evidence",
    [
        "missing-signature",
        "foreign-signature",
        "malformed-signature",
        "foreign-executable",
        "nonfile-executable",
        "malformed-file-executable",
        "foreign-identifier",
        "invalid-pid",
    ],
)
def test_terminal_without_launch_date_still_requires_provenance(
    tmp_path: Path, fence: None, harness: Harness, evidence: str
) -> None:
    destination = _destination(tmp_path)
    terminal = {**_peer(destination, 14649, "vc-terminal"), "start": None}
    signatures = None
    if evidence == "missing-signature":
        signatures = {}
    elif evidence == "foreign-signature":
        signatures = {destination: str(tmp_path / "foreign/Vibecrafted")}
    elif evidence == "malformed-signature":
        signatures = {destination: "https://example.invalid/Vibecrafted"}
    elif evidence == "foreign-executable":
        terminal["executable"] = str(tmp_path / "foreign/vc-terminal")
    elif evidence == "nonfile-executable":
        terminal["executable"] = "https://example.invalid/vc-terminal"
    elif evidence == "malformed-file-executable":
        terminal["executable"] = (
            f"file://{destination}/Contents/MacOS/vc-terminal?role=Terminal"
        )
    elif evidence == "foreign-identifier":
        terminal["identifier"] = "io.foreign.vibecrafted"
    else:
        terminal["pid"] = 0
    result, events, _ = _run(tmp_path, harness, [terminal], signatures=signatures)
    _assert_refused(result, events)


@pytest.mark.parametrize(
    "evidence",
    [
        "missing-signature",
        "foreign-signature",
        "foreign-executable",
        "nonfile-executable",
        "malformed-file-executable",
        "foreign-identifier",
        "invalid-pid",
    ],
)
def test_foreign_or_missing_role_evidence_refuses(
    tmp_path: Path, fence: None, harness: Harness, evidence: str
) -> None:
    destination = _destination(tmp_path)
    peer = _peer(destination, 57391)
    signatures = None
    if evidence == "missing-signature":
        signatures = {}
    elif evidence == "foreign-signature":
        signatures = {destination: str(tmp_path / "foreign/Vibecrafted")}
    elif evidence == "foreign-executable":
        peer["executable"] = str(tmp_path / "foreign/Vibecrafted")
    elif evidence == "nonfile-executable":
        peer["executable"] = "https://example.invalid/Vibecrafted"
    elif evidence == "malformed-file-executable":
        peer["executable"] = (
            f"file://{destination}/Contents/MacOS/Vibecrafted?role=Main"
        )
    elif evidence == "foreign-identifier":
        peer["identifier"] = "io.foreign.vibecrafted"
    else:
        peer["pid"] = 0
    result, events, _ = _run(tmp_path, harness, [peer], signatures=signatures)
    _assert_refused(result, events)


def test_ambiguous_destination_mains_refuse(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    destination = _destination(tmp_path)
    result, events, _ = _run(
        tmp_path, harness, [_peer(destination, 57391), _peer(destination, 57392)]
    )
    _assert_refused(result, events)


def test_main_appearing_during_empty_census_refuses(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    peer = _peer(_destination(tmp_path), 57391)
    result, events, _ = _run(tmp_path, harness, [], snapshots=[[], [peer]])
    _assert_refused(result, events)


@pytest.mark.parametrize(
    "drift",
    [
        "pid",
        "executable",
        "start",
        "lookup-executable",
        "vanished",
        "new-main",
        "ps-start",
    ],
)
def test_revalidated_identity_drift_refuses_before_quit(
    tmp_path: Path, fence: None, harness: Harness, drift: str
) -> None:
    destination = _destination(tmp_path)
    peer = _peer(destination, 57391)
    changed = dict(peer)
    snapshots = [[peer], [peer], [peer]]
    starts = None
    if drift == "pid":
        changed["pid"] = 57392
        snapshots[1] = [changed]
    elif drift in {"executable", "lookup-executable"}:
        changed["executable"] = f"{destination}/Contents/MacOS/vc-terminal"
        snapshots[2 if drift == "lookup-executable" else 1] = [changed]
    elif drift == "start":
        changed["start"] += 1
        snapshots[1] = [changed]
    elif drift == "vanished":
        snapshots[1] = []
    elif drift == "new-main":
        snapshots[1] = [peer, _peer(destination, 57392)]
    else:
        starts = ["Tue Oct 13 23:26:58 2026", "Tue Oct 13 23:26:59 2026"]
    result, events, _ = _run(
        tmp_path, harness, [peer], snapshots=snapshots, starts=starts
    )
    _assert_refused(result, events)


@pytest.mark.parametrize(
    "start,ps_exit",
    [("", 0), ("line-one\nline-two", 0), ("not-a-start", 0), ("valid", 1)],
)
def test_missing_or_malformed_process_start_refuses(
    tmp_path: Path, fence: None, harness: Harness, start: str, ps_exit: int
) -> None:
    result, events, _ = _run(
        tmp_path,
        harness,
        [_peer(_destination(tmp_path), 57391)],
        starts=[start],
        ps_exit=ps_exit,
    )
    _assert_refused(result, events)


def test_graceful_termination_refusal_stops_helper(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    peer = {**_peer(_destination(tmp_path), 57391), "graceful": False}
    result, events, _ = _run(tmp_path, harness, [peer])
    _assert_refused(result, events)
    assert ["terminate-refused", "57391"] in events


def test_cleanup20_preserves_historical_receipts_and_does_not_repeat_install(
    tmp_path: Path, fence: None, harness: Harness
) -> None:
    result, events, state = _run(
        tmp_path,
        harness,
        [_peer(_destination(tmp_path), 14649, "vc-terminal")],
        helper_exit=20,
    )
    assert result.returncode == 20
    args = _helper_args(events)
    for option in ("--receipt", "--admission", "--journal"):
        path = Path(args[args.index(option) + 1])
        assert path.read_text() == json.dumps({"historical": option})
    assert not (state / "vibecrafted-product-update/install-app-latest.json").exists()
    assert len(list((state / "vibecrafted-product-update/transactions").iterdir())) == 1
    assert not any(event[0] == "terminate" for event in events)


@pytest.mark.parametrize(
    "scenario",
    [
        "released",
        "receipt-drift",
        "journal-drift",
        "publication-drift",
        "source-drift",
        "lock-replaced",
        "wrong-transaction",
        "wrong-owner",
        "wrong-capture",
        "not-relaunched",
        "other-refusal",
        "malformed-resolver",
        "deadline",
        "settle-refuses",
    ],
)
def test_settlement_only_recovery_of_startup_service_lease(
    tmp_path: Path, fence: None, harness: Harness, scenario: str
) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    from vibecrafted_core import server_supervisor as supervisor

    budget = "0.2" if scenario == "deadline" else "5"
    private_harness = replace(
        harness,
        wrapper=harness.wrapper.replace(
            "deadline = time.monotonic() + 60",
            f"deadline = time.monotonic() + {budget}",
        ),
    )
    wrapper, environment, allowed = _private_wrapper(tmp_path, private_harness)
    source = wrapper.parents[1] / "dist/Vibecrafted.app"
    destination = Path(environment["INSTALL_APP_DESTINATION"])
    runtime = tmp_path / "runtime"
    generation = runtime / "releases/9.0.0"
    environment["VIBECRAFTED_RUNTIME_HOME"] = str(runtime)
    environment["MAIN_FAKE_CENSUS"] = json.dumps([[], [], []])
    environment["MAIN_FAKE_SIGNATURE"] = json.dumps({})
    environment["RACE_SCENARIO"] = scenario
    marker = tmp_path / "resolver-held"
    environment["RACE_MARKER"] = str(marker)
    product = {"schema": "io.vetcoders.vibecrafted.product.v1", "git_sha": "a" * 40}
    for app in (source, destination):
        (app / "Contents/Resources").mkdir(parents=True, exist_ok=True)
        (app / "Contents/Resources/product-manifest.json").write_text(
            json.dumps(product)
        )
    generation.mkdir(parents=True)
    (generation / "runtime-manifest.json").write_text(
        json.dumps({"source_revision": "a" * 40, "version": "9.0.0"})
    )
    (runtime / "tools").mkdir()
    (runtime / "tools/vibecrafted-current").symlink_to(generation)
    pointer = {
        "schema": "vibecrafted.active-runtime.v1",
        "version": "9.0.0",
        "runtime_root": str(generation),
        "app_root": str(destination),
    }
    (runtime / "active.json").write_text(json.dumps(pointer))
    (runtime / "install-receipt.json").write_text(
        json.dumps({**pointer, "schema": "vibecrafted.runtime-install.v1"})
    )
    python = _executable(
        generation / "bin/python3",
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
    )
    verifier = destination / "Contents/Resources/runtime/scripts/vetcoders_install.py"
    verifier.parent.mkdir(parents=True)
    verifier.write_text(
        "import fcntl,json,os,sys\nfrom pathlib import Path\n"
        "runtime=Path(sys.argv[sys.argv.index('--runtime-home')+1])\n"
        "if os.environ['RACE_SCENARIO']=='malformed-resolver':\n"
        "    print('{broken'); raise SystemExit(2)\n"
        "fd=os.open(runtime/'tools/.vibecrafted-install.lock',os.O_RDONLY)\n"
        "try:\n"
        "    fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    Path(os.environ['RACE_MARKER']).write_text('held')\n"
        "    print(json.dumps({'schema':'vibecrafted.runtime-resolution.v1',"
        "'status':'unusable','reason':'runtime installation is in progress','runtime':None}))\n"
        "    raise SystemExit(2)\n"
        "print(json.dumps({'schema':'vibecrafted.runtime-resolution.v1','status':'ready',"
        "'reason':'','runtime':{'root':str(runtime/'releases/9.0.0'),"
        "'app_root':os.environ['INSTALL_APP_DESTINATION']}}))\n"
    )
    codesign = _executable(
        tmp_path / "private-codesign",
        "#!/bin/sh\n"
        'if [ "$1" = --display ]; then\n'
        "printf 'Identifier=io.vetcoders.vibecrafted\\nTeamIdentifier=TEAM\\nCDHash=aaaa\\n'\n"
        "fi\n",
    )
    wrapper.write_text(
        wrapper.read_text().replace('"/usr/bin/codesign"', json.dumps(str(codesign)))
    )
    helper_python = wrapper.parent / "vc-app-update.py"
    helper_python.write_text(
        f"#!{sys.executable}\n"
        "import fcntl,json,os,sys\nfrom pathlib import Path\n"
        "args=sys.argv[1:]\n"
        "def arg(name): return args[args.index(name)+1]\n"
        "mode=arg('--mode')\n"
        "with open(os.environ['MAIN_TEST_LOG'],'a') as out:\n"
        "    out.write(json.dumps(['helper',*args])+'\\n')\n"
        "if mode=='settle':\n"
        "    assert not any(x in args for x in ('--complete','--relaunch','--resume'))\n"
        "    fd=os.open(Path(os.environ['VIBECRAFTED_RUNTIME_HOME'])/'tools/.vibecrafted-install.lock',os.O_RDONLY)\n"
        "    try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "    except BlockingIOError: pass\n"
        "    else: raise SystemExit('shared runtime lease was not retained through settlement')\n"
        "    if os.environ['RACE_SCENARIO']=='settle-refuses': raise SystemExit(20)\n"
        "    Path(arg('--receipt')+'.settlement.json').write_text(json.dumps({'status':'disposed'}))\n"
        "    raise SystemExit(0)\n"
        "assert '--complete' in args and '--relaunch' in args\n"
        "base={'transaction':arg('--transaction'),'destination':arg('--destination'),"
        "'source':arg('--source'),'source_identity':'cdhash:aaaa','identifier':'io.vetcoders.vibecrafted',"
        "'team_id':'TEAM','mode':'replace','operation':'replace','journal':arg('--journal'),"
        "'receipt':arg('--receipt'),'candidate_identity':'cdhash:aaaa',"
        "'parent':str(Path(arg('--destination')).parent),"
        "'capture':str(Path(arg('--destination')).parent/('.vc-update-capture-'+arg('--transaction')))}\n"
        "for option,schema,extra in ("
        "('--receipt','io.vetcoders.vibecrafted.app-replacement.v1',{'replaced':True,'relaunched':True,'detail':'replaced'}),"
        "('--admission','io.vetcoders.vibecrafted.app-replacement-admission.v1',{'status':'ready'}),"
        "('--journal','io.vetcoders.vibecrafted.app-update-journal.v1',{'phase':'relaunched'})):\n"
        "    value={**base,'schema':schema,**extra}\n"
        "    if option=='--journal' and os.environ['RACE_SCENARIO']=='wrong-transaction': value['transaction']='foreign'\n"
        "    if option=='--receipt' and os.environ['RACE_SCENARIO']=='not-relaunched': value['relaunched']=False\n"
        "    if option=='--journal' and os.environ['RACE_SCENARIO']=='wrong-owner': value['team_id']='FOREIGN'\n"
        "    if option=='--journal' and os.environ['RACE_SCENARIO']=='wrong-capture': value['capture']='foreign'\n"
        "    Path(arg(option)).write_text(json.dumps(value))\n"
        "reason='runtime installation is in progress'\n"
        "if os.environ['RACE_SCENARIO']=='other-refusal': reason='runtime identity versions disagree'\n"
        "print('capture settlement incomplete (retry with the same transaction): canonical destination verifier refused: '+"
        "json.dumps({'schema':'vibecrafted.runtime-resolution.v1','status':'unusable','reason':reason,'runtime':None}),file=sys.stderr)\n"
        "raise SystemExit(20)\n"
    )
    paths = supervisor.SupervisorPaths.create(
        home=tmp_path / "state", runtime_home=runtime, operator_home=tmp_path / "home"
    )
    command = _child(["/bin/bash", str(wrapper)], allowed=(*allowed, codesign, python))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with supervisor._ToolsInstallMutationLease(paths):
            future = pool.submit(
                subprocess.run,
                command,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            deadline = time.monotonic() + 10
            while not marker.exists() and not future.done():
                assert time.monotonic() < deadline, "canonical resolver was not reached"
                time.sleep(0.01)
            if scenario == "deadline":
                result = future.result(timeout=10)
            else:
                drift = {
                    "receipt-drift": "receipt.json",
                    "journal-drift": "journal.json",
                }.get(scenario)
                if drift:
                    target = next((tmp_path / "state").rglob(drift))
                    target.write_bytes(target.read_bytes() + b" ")
                if scenario == "publication-drift":
                    target = runtime / "active.json"
                    target.write_bytes(target.read_bytes() + b" ")
                if scenario == "source-drift":
                    target = source / "Contents/Resources/product-manifest.json"
                    target.write_bytes(target.read_bytes() + b" ")
                if scenario == "lock-replaced":
                    target = runtime / "tools/.vibecrafted-install.lock"
                    target.unlink()
                    target.write_text("")
        result = future.result(timeout=10)
    events = _logs(Path(environment["MAIN_TEST_LOG"]))
    calls = [event[1:] for event in events if event[0] == "helper"]
    assert len([args for args in calls if "--complete" in args]) == 1
    settlements = [args for args in calls if args[args.index("--mode") + 1] == "settle"]
    if scenario == "released":
        assert result.returncode == 0, result.stderr
        assert len(settlements) == 1
        assert (
            settlements[0][settlements[0].index("--transaction") + 1]
            == calls[0][calls[0].index("--transaction") + 1]
        )
        assert (
            tmp_path / "state/vibecrafted-product-update/install-app-latest.json"
        ).is_file()
    else:
        assert result.returncode == 20, result.stderr
        assert len(settlements) == (1 if scenario == "settle-refuses" else 0)
        assert not (
            tmp_path / "state/vibecrafted-product-update/install-app-latest.json"
        ).exists()
    stderr = next((tmp_path / "state").rglob("complete.stderr"))
    assert "canonical destination verifier refused:" in stderr.read_text()
