"""W2-authored contracts. Compilation, fixture execution and pytest are W3 gates."""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "vibecrafted-app/shell-agent"
APP = SHELL / "app/Vibecrafted"


def test_single_native_host_source_contract() -> None:
    delegate = (APP / "AppDelegate.swift").read_text()
    main = (APP / "main.swift").read_text()
    window = (APP / "Views/MainWindowController.swift").read_text()
    host = (APP / "CommandDeck/WebConsoleHost.swift").read_text()
    bridge = (APP / "CommandDeck/NativeCommandBridge.swift").read_text()
    assert delegate.count("WebConsoleSession()") == 1
    assert delegate.count("MainWindowController(model:") == 1
    assert "if mainWindow == nil" in delegate
    assert "NSApp.setActivationPolicy(.regular)" in delegate
    assert main.index("NSApplication.shared") < main.index("AppDelegate()")
    coordinator = (APP / "CommandDeck/NativeTabCoordinator.swift").read_text()
    policy = (APP / "CommandDeck/WebNavigationPolicy.swift").read_text()
    scaffold = (ROOT / "vibecrafted-server/web/src/scaffold/mod.rs").read_text()
    assert "window.isReleasedWhenClosed = false" in window
    assert "presentation: model.presentation" in window
    # One chrome: the SwiftUI toolbar is bridged into the native titlebar and
    # every tab window shares one tab group; no second content row exists.
    assert "hosting.sceneBridgingOptions = [.toolbars]" in window
    assert "window.tabbingIdentifier = tabbingIdentifier" in window
    assert "window.toolbarStyle = .unified" in window
    assert (
        "CommandDeckChromeBar"
        not in (APP / "CommandDeck/CommandDeckView.swift").read_text()
    )
    # One WKWebView per tab: the coordinator adds native tabs and never a
    # WebKit-created view; the console session stays the App's single one.
    assert "addTabbedWindow" in coordinator
    assert (
        "WKScriptMessageHandler" not in coordinator
        and "userContentController.add" not in coordinator
    )
    assert "return nil" in host and "createWebViewWith" in host
    assert "case divertToReferenceTab" in policy
    assert "func decideLocalDocumentNavigation" in policy
    assert delegate.count("NativeTabCoordinator(") == 1
    assert "tabs.apply(runtimeEndpoint: endpoint)" in delegate
    # Destinations: owner-backed configuration, never a guessed host/port or a
    # permanently-unavailable stub; generated documents are isolated.
    destinations = (APP / "CommandDeck/ToolDestinations.swift").read_text()
    assert '.configuredService(key: "slack-console"' in destinations
    assert "vibecrafted/config.toml" in destinations
    assert "target: .unavailable(reason:" not in destinations
    assert (
        'target: .runtimeRoute("/structure/report"), isolatedContent: true'
        in destinations
    )
    assert "func present(service url: URL)" in host
    assert "case .runtime(let origin), .service(let origin): return origin" in host
    assert "scope.followsRuntimeEndpoint" in coordinator
    assert (
        ".available(let url, .runtime), .available(let url, .service): openExternalURL(url)"
        in delegate
    )
    # The server owns the report boundary: sandboxed CSP, explicit assets,
    # and the AICX corpus behind a verified local peer.
    tools = (ROOT / "vibecrafted-server/web/src/tools.rs").read_text()
    server_main = (ROOT / "vibecrafted-server/web/src/main.rs").read_text()
    assert "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox" in tools
    assert "sandbox allow-scripts allow-same-origin" not in tools
    assert "fn local_peer_access" in tools
    assert "into_make_service_with_connect_info::<SocketAddr>()" in server_main
    assert 'route("/structure/report/{asset}"' in server_main
    assert 'route("/api/aicx/reference"' in server_main
    app_rs = (ROOT / "vibecrafted-server/web/src/app.rs").read_text()
    assert "'file://'" not in app_rs and "href='file://" not in app_rs
    assert "item.reference" in app_rs
    # Scaffold endpoint links open outside the studio document.
    assert scaffold.count('class="api-link" href="/api/scaffold/') == 2
    assert (
        scaffold.count('target="_blank" rel="noopener noreferrer">artifact endpoint')
        == 1
    )
    assert host.count("WKWebView(frame:") == 1
    assert "websiteDataStore: WKWebsiteDataStore = .default()" in host
    assert "configuration.websiteDataStore = websiteDataStore" in host
    assert "WKScriptMessageHandler" not in bridge
    assert "func receive(" not in bridge and "func decode(" not in bridge
    assert "userContentController.add" not in host
    assert 'openRoute("/workspaces")' in delegate
    assert 'openRoute("/run/\\(runID)")' in delegate
    assert "getServerStatus()" not in delegate
    assert "NSStatusBar.system.statusItem" not in delegate
    assert "StatusItemController {" in delegate
    assert "self.confirmedStopRoot == install.root" in delegate
    assert "deriveServerMenuState(caretakerData: self.lastCaretakerData" in delegate
    assert "It does not stop terminal sessions or agents." in delegate
    for name in [
        "CanvasViewController",
        "InspectorViewController",
        "MainSplitViewController",
        "MissionControlViewController",
        "SidebarViewController",
    ]:
        assert not (APP / f"Views/{name}.swift").exists()
        assert name not in delegate + window


def test_package_has_distinct_art_and_no_competing_tray() -> None:
    project = (SHELL / "app/project.yml").read_text()
    makefile = (SHELL / "Makefile").read_text()
    builder = (ROOT / "scripts/build-vibecrafted-release.sh").read_text()
    tray = (APP / "CommandDeck/TrayGlyph.swift").read_text()
    assert "build-rust-binaries.sh" not in project
    assert "vc-mux-tray" not in makefile
    assert "libvibecrafted_shell_ffi" in project  # notifications still consume FFI
    assert "docs/presence/logo-master.png" in builder + makefile
    assert "VIBECRAFTED_ICON_SOURCE:-$TERMINAL_REPO" not in builder
    assert '"$TERMINAL_REPO/assets/icon/vc-terminal-icon.png"' in builder
    assert 'install -m 0644 "$resources/Vibecrafted.icns"' not in builder
    assert "image.isTemplate = false" not in tray


def _compile(tmp_path: Path, name: str, sources: list[Path]) -> Path:
    if sys.platform != "darwin":
        pytest.skip("macOS 14+ native harness")
    compiler = shutil.which("swiftc")
    if not compiler:
        pytest.skip("swiftc is unavailable")
    binary = tmp_path / name
    target = "arm64" if os.uname().machine == "arm64" else "x86_64"
    subprocess.run(
        [
            compiler,
            "-swift-version",
            "6",
            "-target",
            f"{target}-apple-macosx14.0",
            *map(str, sources),
            "-o",
            str(binary),
        ],
        check=True,
        timeout=180,
    )
    return binary


def test_native_typed_actions_and_terminal_contract(tmp_path: Path) -> None:
    binary = _compile(
        tmp_path,
        "native-command-contract",
        [
            APP / "CommandDeck/NativeCommandBridge.swift",
            APP / "CommandDeck/TerminalLauncher.swift",
            SHELL / "tests/NativeCommandRecoveryTests.swift",
        ],
    )
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30, check=True
    )
    assert "NativeCommandRecoveryTests passed" in result.stdout


def test_native_session_state_routes_and_reopen(tmp_path: Path) -> None:
    sources = sorted((APP / "CommandDeck").glob("*.swift"))
    sources += [
        APP / "ServerMenuPolicy.swift",
        APP / "Views/MainWindowController.swift",
        SHELL / "tests/CommandDeckIntegrationTests.swift",
    ]
    binary = _compile(tmp_path, "command-deck-contract", sources)

    class Fixture(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/download":
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header(
                    "Content-Disposition", 'attachment; filename="report.txt"'
                )
                self.end_headers()
                self.wfile.write(b"server-download")
                return
            if self.path.startswith("/api/scaffold/artifacts"):
                body = b'{"artifacts":[{"id":"fixture","approved":false}]}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/scaffold"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"""<!doctype html><title>Scaffold fixture</title>
<main><textarea id="draft">plan</textarea>
<a id="api" href="/api/scaffold/artifacts?org=o&repo=r&day=d&plan_id=p">artifact endpoint</a>
<a id="blank" href="/workspaces" target="_blank" rel="noopener noreferrer">Workspaces</a></main>""")
                return
            self.send_response(503 if self.path.startswith("/failure") else 200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Reconnect must retain the original cookie; later responses must
            # not recreate it and conceal a discarded website data store.
            if self.path == "/":
                self.send_header(
                    "Set-Cookie", "w3_session=fixture; Path=/; SameSite=Lax"
                )
            self.end_headers()
            self.wfile.write(b"""<!doctype html><title>W3 fixture</title>
<a href="/workspaces">Workspaces</a><p id="fixture">Ready</p>
<a id="download" href="/download" download="report.txt">Download</a>
<a id="blob" download="blob.txt">Blob</a>
<script>document.getElementById('blob').href = URL.createObjectURL(new Blob(['blob-download'], {type: 'application/octet-stream'}));</script>""")

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [str(binary), f"http://127.0.0.1:{server.server_port}/"],
            capture_output=True,
            text=True,
            timeout=150,
            check=True,
        )
        assert "CommandDeckIntegrationTests passed" in result.stdout
        print(result.stdout)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
