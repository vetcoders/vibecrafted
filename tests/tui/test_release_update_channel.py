"""Exercise release channel packaging without compiling or signing a product."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "scripts/unified_product_manifest.py"
FEED = "https://updates.example.org/stable/release-output.json?channel=stable&build=4"
APPROVED_FEED = "https://github.com/vetcoders/vibecrafted/releases/latest/download/release-output.json"


def channel(*args: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "vibecrafted-core"))
    return subprocess.run(
        [sys.executable, str(PRODUCER), "update-channel", *args],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


@pytest.fixture
def app(tmp_path: Path) -> Path:
    bundle = tmp_path / "Vibecrafted.app"
    contents = bundle / "Contents"
    contents.mkdir(parents=True)
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {"CFBundleIdentifier": "io.vetcoders.vibecrafted", "LSUIElement": True}
        )
    )
    trust = contents / "Resources/runtime-pack/vibecrafted-signing-v1.pub"
    trust.parent.mkdir(parents=True)
    trust.write_bytes(b"test sentinel for unchanged trust bytes")
    return bundle


def test_release_channel_exact_input_is_packaged_and_verified_read_only(
    app: Path,
) -> None:
    key = app / "Contents/Resources/runtime-pack/vibecrafted-signing-v1.pub"
    before_key = key.read_bytes()
    configured = channel("--app", str(app), "--feed-url", FEED)
    assert configured.returncode == 0, configured.stderr
    plist = app / "Contents/Info.plist"
    assert plistlib.loads(plist.read_bytes())["VCUpdateFeedURL"] == FEED
    digest = hashlib.sha256(plist.read_bytes()).hexdigest()
    receipt = json.loads(configured.stdout)
    assert receipt["feed_url"] == FEED
    assert receipt["info_plist_sha256"] == digest
    # Simulate the signer boundary: verification must work with the seal present.
    (app / "Contents/_CodeSignature").mkdir()
    verified = channel("--app", str(app), "--feed-url", FEED, "--verify-only")
    assert verified.returncode == 0, verified.stderr
    assert hashlib.sha256(plist.read_bytes()).hexdigest() == digest
    assert key.read_bytes() == before_key
    mismatch = channel(
        "--app",
        str(app),
        "--feed-url",
        "https://updates.example.org/other.json",
        "--verify-only",
    )
    assert mismatch.returncode != 0
    assert hashlib.sha256(plist.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(
    "feed",
    [
        "",
        "http://updates.example.org/release-output.json",
        "file:///tmp/release-output.json",
        "/tmp/release-output.json",
        "https:///release-output.json",
        "https://localhost/feed.json",
        "https://127.0.0.1/feed.json",
        "https://[::1]/feed.json",
        "https://192.168.1.2/feed.json",
        "https://updates.local/feed.json",
        "https://user:password@updates.example.org/feed.json",
        "https://updates.example.org:bad/feed.json",
        "https://updates.example.org/%zz",
        " https://updates.example.org/feed.json",
        "https://updates.example.org/feed.json#fragment",
        "https://bad host/feed.json",
        "https://updates.example.org\\feed.json",
    ],
)
def test_release_channel_rejects_invalid_input_before_plist_mutation(
    app: Path, feed: str
) -> None:
    plist = app / "Contents/Info.plist"
    before = plist.read_bytes()
    result = channel("--app", str(app), "--feed-url", feed)
    assert result.returncode != 0
    assert "explicit well-formed public HTTPS URL" in result.stderr
    assert plist.read_bytes() == before


def test_release_channel_requires_explicit_choice_and_refuses_signed_mutation(
    app: Path,
) -> None:
    assert channel().returncode != 0
    assert channel("--unprovisioned").returncode == 0
    assert channel("--feed-url", FEED, "--unprovisioned").returncode != 0
    assert channel("--feed-url", FEED).returncode == 0
    plist = app / "Contents/Info.plist"
    before = plist.read_bytes()
    (app / "Contents/_CodeSignature").mkdir()
    result = channel("--app", str(app), "--feed-url", FEED)
    assert result.returncode != 0
    assert plist.read_bytes() == before


def test_developer_channel_removes_stale_feed_and_verifier_refuses_it(
    app: Path,
) -> None:
    assert channel("--app", str(app), "--feed-url", FEED).returncode == 0
    assert (
        channel("--app", str(app), "--unprovisioned", "--verify-only").returncode != 0
    )
    assert channel("--app", str(app), "--unprovisioned").returncode == 0
    assert "VCUpdateFeedURL" not in plistlib.loads(
        (app / "Contents/Info.plist").read_bytes()
    )
    assert (
        channel("--app", str(app), "--unprovisioned", "--verify-only").returncode == 0
    )


def test_release_builder_configures_before_signing_and_notarize_only_never_writes() -> (
    None
):
    builder = (ROOT / "scripts/build-vibecrafted-release.sh").read_text()
    body = builder[builder.index("build_product() {") : builder.index("create_dmg() {")]
    assert body.index("configure_update_channel") < body.index("sign_macho_tree")
    assert body.index("configure_update_channel") < body.index(
        'unified_product_manifest.py" app'
    )
    assert body.index("verify_update_channel") > body.index(
        '"${CODESIGN_KEYCHAIN_ARGS[@]}" "$APP"'
    )
    notarize = builder[builder.rindex('if [[ "$MODE" == "notarize" ]]; then') :]
    assert "configure_update_channel" not in notarize
    assert notarize.index("verify_update_channel") < notarize.index("notarize_product")
    assert builder.index("validate_update_channel") < builder.index("TERMINAL_DONOR=")


@pytest.mark.parametrize(
    ("mode", "unprovisioned", "feed", "success"),
    [
        ("release", False, None, True),
        ("release", False, "", False),
        ("release", True, None, False),
        ("notarize", True, None, False),
        ("notarize", False, FEED, True),
        ("app", True, None, True),
        ("dmg", True, None, True),
        ("app", True, FEED, False),
        ("release", False, FEED, True),
        ("release", False, "http://updates.example.org/feed.json", False),
        ("runtime-pack", False, None, True),
    ],
)
def test_release_channel_mode_preflight(
    tmp_path: Path, mode: str, unprovisioned: bool, feed: str | None, success: bool
) -> None:
    builder = (ROOT / "scripts/build-vibecrafted-release.sh").read_text()
    functions = builder[
        builder.index("UPDATE_CHANNEL_ARGS=()") : builder.index(
            "validate_update_channel\n"
        )
    ]
    environment = dict(os.environ, PYTHON=sys.executable)
    environment.pop("VIBECRAFTED_UPDATE_FEED_URL", None)
    if feed is not None:
        environment["VIBECRAFTED_UPDATE_FEED_URL"] = feed
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'die() { echo "$*" >&2; exit 1; }\n'
            'REPO_ROOT="$1"; MODE="$2"; UNPROVISIONED_UPDATE_CHANNEL="$3"\n'
            + functions
            + "\nvalidate_update_channel\n",
            "channel-test",
            str(ROOT),
            mode,
            str(int(unprovisioned)),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert (result.returncode == 0) is success, result.stderr


def test_release_default_is_exact_approved_feed_before_signing(
    app: Path, tmp_path: Path
) -> None:
    builder = (ROOT / "scripts/build-vibecrafted-release.sh").read_text()
    functions = builder[
        builder.index("UPDATE_CHANNEL_ARGS=()") : builder.index(
            "validate_update_channel\n"
        )
    ]
    environment = dict(os.environ, PYTHON=sys.executable)
    environment.pop("VIBECRAFTED_UPDATE_FEED_URL", None)
    result = subprocess.run(
        [
            "bash",
            "-c",
            "set -euo pipefail\n"
            'die() { echo "$*" >&2; exit 1; }\n'
            'REPO_ROOT="$1"; SOURCE_ROOT="$1"; APP="$2"; BUILD_DIR="$3"\n'
            "MODE=release; UNPROVISIONED_UPDATE_CHANNEL=0\n"
            + functions
            + "\nvalidate_update_channel\nconfigure_update_channel\n"
            'mkdir -p "$APP/Contents/_CodeSignature"\nverify_update_channel\n',
            "channel-test",
            str(ROOT),
            str(app),
            str(tmp_path / "build"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    plist = app / "Contents/Info.plist"
    assert plistlib.loads(plist.read_bytes())["VCUpdateFeedURL"] == APPROVED_FEED
    receipt = json.loads((tmp_path / "build/update-channel-receipt.json").read_text())
    assert receipt["feed_url"] == APPROVED_FEED
    assert (
        receipt["info_plist_sha256"] == hashlib.sha256(plist.read_bytes()).hexdigest()
    )


def test_packaged_feed_is_admitted_by_existing_product_update_parser(
    tmp_path: Path, app: Path
) -> None:
    swiftc = shutil.which("swiftc")
    if sys.platform != "darwin" or swiftc is None:
        pytest.skip("existing macOS ProductUpdatePolicy parser requires Swift")
    assert channel("--app", str(app), "--feed-url", APPROVED_FEED).returncode == 0
    main = tmp_path / "main.swift"
    main.write_text(
        "import Foundation\n"
        "let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))\n"
        "let plist = try PropertyListSerialization.propertyList(from: data, format: nil) as! [String: Any]\n"
        'let raw = plist["VCUpdateFeedURL"] as! String\n'
        "let url = resolveProductUpdateFeedURL(feedURLString: raw, environment: [:])\n"
        "precondition(url?.absoluteString == CommandLine.arguments[2])\n"
        'precondition(resolveProductUpdateSignatureURL(for: url!).path.hasSuffix(".json.sig"))\n'
    )
    source = ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted"
    binary = tmp_path / "channel-parser"
    subprocess.run(
        [
            swiftc,
            str(source / "RuntimePackMenuPolicy.swift"),
            str(source / "ServerMenuPolicy.swift"),
            str(source / "ProductUpdatePolicy.swift"),
            str(main),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    subprocess.run(
        [str(binary), str(app / "Contents/Info.plist"), APPROVED_FEED],
        check=True,
        timeout=10,
    )
