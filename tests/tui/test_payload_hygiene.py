"""Contract for the payload anonymity gate.

The gate answers one question of a finished artifact: does it name the host
that built it. It exists because every compiler-side answer is partial —
measured on `Vibecrafted_4.1.0-20260817-237d2814.dmg`, 8 of 2955 files carried
the operator's account or checkout through five unrelated producers (embedded
WASM, cc-rs debug info, Swift/xcodebuild intermediates, a uv-seeded CPython's
`_sysconfigdata`, and a pip console-script shebang). `--remap-path-prefix`
reaches exactly one of them.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
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
        b"\x00\x01/Users/tester/.cargo/registry/src\x00 and again /Users/tester/x"
    )

    result = run_scanner("--root", str(payload), "--forbid", "/Users/tester")

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

    result = run_scanner("--root", str(payload), "--forbid", "/Users/tester")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 files scanned" in result.stdout


def test_scanner_finds_a_literal_that_straddles_a_read_boundary(tmp_path: Path) -> None:
    """The scanner streams; a needle split across two reads must still be seen.

    Without an overlap this is the exact leak a gate would certify as clean:
    the bigger the binary, the likelier a path lands on a chunk seam.
    """
    payload = tmp_path / "payload"
    payload.mkdir()
    needle = b"/Users/tester"
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
    outside.write_text("/Users/tester/secret", encoding="utf-8")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "link").symlink_to(outside)

    result = run_scanner("--root", str(payload), "--forbid", "/Users/tester")

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


def run_library(snippet: str, **environment: str) -> str:
    """Source the gate library and run one bash snippet against it."""
    script = f'set -euo pipefail\n. "{LIBRARY}"\n{snippet}\n'
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", **environment},
    )
    return result.stdout


def test_the_literal_set_reaches_the_workshop_above_the_checkout() -> None:
    """The gate's blind spot: a prefix of REPO_ROOT is never a substring of it.

    Matching is a byte `count()`, so forbidding the exact checkout can only ever
    catch paths at or below it. Every path naming the directory the checkout
    lives IN — the workshop — passed as clean. Measured 2026-08-18 on the 4.1.0
    portable tarball: 5 offending files with the checkout alone, 12 with the
    workshop, and the seven in between included shipped runtime code.
    """
    literals = run_library(
        "payload_hygiene_literals",
        HOME="/Users/tester",
        REPO_ROOT="/Volumes/workshop/org/suite/repo",
    ).split()

    assert "/Volumes/workshop" in literals, (
        "the workshop above the checkout is not gated; paths one level up ship clean"
    )
    assert "/Volumes/workshop/org/suite/repo" in literals


def test_the_ancestor_walk_stops_before_generic_system_roots() -> None:
    """`/Users` or `/Volumes` says nothing about who built the payload.

    Walking all the way to `/` would forbid a directory every machine has and
    flag every legitimate absolute path in the tree — a gate that fails on
    everything is a gate nobody keeps.
    """
    assert (
        run_library('payload_hygiene_topmost_host_root "/Users/tester"').strip() == ""
    )
    assert (
        run_library('payload_hygiene_topmost_host_root "/Volumes/solo"').strip() == ""
    )
    assert (
        run_library('payload_hygiene_topmost_host_root "/Volumes/ws/a/b"').strip()
        == "/Volumes/ws"
    )


# The packer refuses any path carrying one of these components, so a literal
# inside them never reaches a customer. Mirrors
# scripts/distribution_manifest.py::FORBIDDEN_COMPONENTS for the parts that
# matter to this invariant.
_UNSHIPPED_COMPONENTS = frozenset({"tests", "test", "__tests__", ".github", ".loctree"})


def test_no_shipping_file_names_the_workshop_above_the_checkout() -> None:
    """Sibling of the checkout invariant, one directory up.

    Host-independent: it asks the repository where it lives and forbids the
    workshop that contains it, so it holds on any machine.
    """
    # Ask the library itself where the workshop is. Deriving it here with a
    # guess like `REPO_ROOT.parent.parent` produced a test that silently passed:
    # it looked one level too deep, so the literal it searched for did not exist
    # in any file and the assertion could never fire.
    workshop = Path(
        run_library(f'payload_hygiene_topmost_host_root "{REPO_ROOT}"').strip()
    )
    assert str(workshop) not in {"", "/", "."}, (
        "checkout sits directly under a generic root; there is no workshop to gate"
    )

    tracked = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    needle = str(workshop).encode()

    offenders = []
    for raw in tracked:
        if not raw:
            continue
        relative = raw.decode()
        if _UNSHIPPED_COMPONENTS & set(Path(relative).parts):
            continue
        path = REPO_ROOT / relative
        if not path.is_file() or path.is_symlink():
            continue
        if needle in path.read_bytes():
            offenders.append(relative)

    assert not offenders, (
        f"tracked shipping files name the workshop {workshop} and would reach "
        f"customers in the portable tarball: {offenders}"
    )
