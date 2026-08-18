"""Contract for the payload anonymity gate.

The gate answers one question of a finished artifact: does it name the host
that built it. It exists because every compiler-side answer is partial —
measured on `Vibecrafted_4.1.0-20260817-237d2814.dmg`, 8 of 2955 files carried
the operator's account or checkout through five unrelated producers (embedded
WASM, cc-rs debug info, Swift/xcodebuild intermediates, a uv-seeded CPython's
`_sysconfigdata`, and a pip console-script shebang). `--remap-path-prefix`
reaches exactly one of them.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by VetCoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts/payload_hygiene.py"
LIBRARY = REPO_ROOT / "scripts/lib/payload-hygiene.sh"
ARTIFACT_ENTRY = REPO_ROOT / "scripts/payload-hygiene-artifact.sh"


def run_scanner(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def test_scanner_reports_a_planted_literal_and_fails(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    (payload / "nested").mkdir(parents=True)
    (payload / "clean.txt").write_text("nothing to see", encoding="utf-8")
    (payload / "nested/leaky.bin").write_bytes(
        b"\x00\x01/Users/someone/.cargo/registry/src\x00 and again /Users/someone/x"
    )

    result = run_scanner("--root", str(payload), "--forbid", "/Users/someone")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "nested/leaky.bin" in result.stderr
    assert "clean.txt" not in result.stderr
    # Every occurrence counts, not just the file: a report of "1 file" would
    # hide how much of the payload is contaminated.
    assert "2" in result.stderr


def test_scanner_passes_a_payload_that_names_nobody(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "binary").write_bytes(b"\x7fELF" + b"\x00" * 4096)
    (payload / "text.txt").write_text("/usr/src/vibecrafted/main.rs", encoding="utf-8")

    result = run_scanner("--root", str(payload), "--forbid", "/Users/someone")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 files scanned" in result.stdout


def test_scanner_finds_a_literal_that_straddles_a_read_boundary(tmp_path: Path) -> None:
    """The scanner streams; a needle split across two reads must still be seen.

    Without an overlap this is the exact leak a gate would certify as clean:
    the bigger the binary, the likelier a path lands on a chunk seam.
    """
    payload = tmp_path / "payload"
    payload.mkdir()
    needle = b"/Users/someone"
    chunk = 4 * 1024 * 1024
    # Land the needle so it begins a few bytes before the first seam.
    prefix = b"\x00" * (chunk - 4)
    (payload / "big.bin").write_bytes(prefix + needle + b"\x00" * 1024)

    result = run_scanner("--root", str(payload), "--forbid", needle.decode())

    assert result.returncode == 1, result.stdout + result.stderr
    assert "big.bin" in result.stderr


def test_scanner_refuses_literals_that_would_match_everything(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "a").write_text("x", encoding="utf-8")

    for degenerate in ("/", "", "a"):
        result = run_scanner("--root", str(payload), "--forbid", degenerate)
        assert result.returncode == 2, f"{degenerate!r} was accepted"

    # No literal at all is a gate that proves nothing; it must not pass either.
    assert run_scanner("--root", str(payload)).returncode == 2


def test_scanner_does_not_follow_symlinks_out_of_the_payload(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("/Users/someone/secret", encoding="utf-8")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "link").symlink_to(outside)

    result = run_scanner("--root", str(payload), "--forbid", "/Users/someone")

    assert result.returncode == 0, result.stdout + result.stderr


def test_both_release_channels_gate_before_they_publish() -> None:
    dmg = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    portable = (REPO_ROOT / "scripts/build-portable-release.sh").read_text(
        encoding="utf-8"
    )

    assert "scripts/lib/payload-hygiene.sh" in dmg
    assert 'assert_payload_is_anonymous "$APP" "Vibecrafted.app"' in dmg
    # The gate must run before a signature is spent on the bytes, otherwise a
    # failing release still costs a notarization round trip.
    gate_at = dmg.index('assert_payload_is_anonymous "$APP"')
    sign_at = dmg.index('log "Signing nested code and binding exact source receipts"')
    assert gate_at < sign_at, "the payload gate must run before signing"

    assert "scripts/lib/payload-hygiene.sh" in portable
    assert "assert_payload_is_anonymous" in portable


def test_the_standalone_gate_is_not_weaker_than_the_in_build_gate() -> None:
    """Measured regression: the first standalone run missed 277 leaks.

    `payload_hygiene_literals` reads `TERMINAL_DONOR` / `FRAME_DONOR` from the
    environment. The release builder sets them; a bare shell does not, so the
    standalone entry point has to resolve them itself or it silently certifies
    a payload carrying the whole vc-terminal donor path.
    """
    entry = ARTIFACT_ENTRY.read_text(encoding="utf-8")

    assert "TERMINAL_DONOR=" in entry
    assert "FRAME_DONOR=" in entry
    assert "export TERMINAL_DONOR FRAME_DONOR" in entry
    # Resolved, never concatenated — a prefix holding `..` matches nothing.
    assert "cd " in entry and "pwd" in entry


def test_the_literal_set_covers_home_checkout_donors_and_snapshots() -> None:
    library = LIBRARY.read_text(encoding="utf-8")

    for variable in (
        "${HOME:-}",
        "${TERMINAL_DONOR:-}",
        "${FRAME_DONOR:-}",
        "${TERMINAL_REPO:-}",
        "${FRAME_REPO:-}",
    ):
        assert variable in library, f"{variable} is not gated"
    assert "REPO_ROOT" in library


def test_no_tracked_file_names_this_checkout() -> None:
    """The invariant that caught five committed leaks.

    The portable channel ships a projection of the repository, so an absolute
    path pasted into a doc, a wireframe or a test fixture reaches customers
    verbatim. Measured 2026-08-18: `docs/design/agents-workshop/{Layout-2.md,
    Layout-5.md,preview.html,_render.py}` and
    `vibecrafted-app/tui-agent/src/observe.rs` each carried the operator's real
    checkout root. This assertion is host-independent: it asks the repository
    about its own location, whatever that is.
    """
    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    needle = str(REPO_ROOT).encode()

    offenders = []
    for raw in tracked:
        if not raw:
            continue
        path = REPO_ROOT / raw.decode()
        if not path.is_file() or path.is_symlink():
            continue
        if needle in path.read_bytes():
            offenders.append(raw.decode())

    assert not offenders, (
        "tracked files name the checkout root and would ship in the portable "
        f"tarball: {offenders}"
    )


def test_make_exposes_the_gate_for_an_artifact_already_on_disk() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "payload-hygiene:" in makefile
    assert "ARTIFACT" in makefile
    assert "scripts/payload-hygiene-artifact.sh" in makefile
