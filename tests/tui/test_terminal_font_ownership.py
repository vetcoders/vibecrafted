"""Who owns the terminal font, proved by running macOS font resolution.

The parent app used to call `CTFontManagerRegisterFontsForURL(..., .session,
...)` just before spawning the terminal. `.session` is the login session, not
the process: measured on this stack, an unrelated process saw the app's copy of
SpotMono.ttc and CoreText picked it over the owner's own `~/Library/Fonts` file.

The replacement is the route Apple documents for a consuming app:
`ATSApplicationFontsPath` in the bundle's Info.plist, naming a directory under
Resources. `vc-terminal-product-entry.sh` execs
`vc-terminal.app/Contents/MacOS/alacritty`, so that bundle is the main bundle of
the process that draws the glyphs.

  https://developer.apple.com/documentation/bundleresources/information-property-list/atsapplicationfontspath

A string in a plist is not evidence, so the macOS test below builds throwaway
app bundles in a temporary directory and asks CoreText, in a real consuming
process, what it can see. No installed app, user font store, or live process is
touched -- which also bounds what these tests may claim: the precedence case
draws its donor from `/System/Library/Fonts` and `/Library/Fonts` only, so
"the installed copy still wins" is proved for the system stores and is NOT a
universal statement about `~/Library/Fonts`.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
"""

from __future__ import annotations

import json
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_SOURCE = Path(__file__).resolve().parent / "fixtures" / "font_probe.m"

# Font files inside another application's bundle are app-private by the very
# contract under test, so they are the natural donor for "a family this host
# does not have installed". The control probe verifies that claim rather than
# assuming it.
_DONOR_ROOTS = (Path("/Applications"), Path("/System/Applications"))


def _probe_binary(tmp_path: Path) -> Path:
    binary = tmp_path / "font_probe"
    subprocess.run(
        [
            "/usr/bin/clang",
            "-fobjc-arc",
            "-framework",
            "Foundation",
            "-framework",
            "CoreText",
            "-o",
            str(binary),
            str(PROBE_SOURCE),
        ],
        check=True,
        capture_output=True,
    )
    return binary


def _make_bundle(
    root: Path, name: str, probe: Path, font: Path, *, declare_fonts: bool
) -> Path:
    """A minimal .app whose executable is the probe, with or without the key."""
    app = root / f"{name}.app"
    macos = app / "Contents" / "MacOS"
    fonts = app / "Contents" / "Resources" / "fonts"
    macos.mkdir(parents=True)
    fonts.mkdir(parents=True)
    # copyfile, not copy2: a system font carries SIP flags that cannot be
    # reproduced, and the bytes are the only thing under test.
    shutil.copyfile(probe, macos / "probe")
    (macos / "probe").chmod(0o755)
    shutil.copyfile(font, fonts / font.name)
    (fonts / font.name).chmod(0o644)
    info = {
        "CFBundleIdentifier": f"io.vetcoders.vibecrafted.fontprobe.{name}",
        "CFBundleExecutable": "probe",
        "CFBundlePackageType": "APPL",
    }
    if declare_fonts:
        info["ATSApplicationFontsPath"] = "fonts"
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return macos / "probe"


def _same_file(reported: str | None, expected: Path) -> bool:
    """CoreText reports the path it was handed; /var vs /private/var is noise."""
    return reported is not None and Path(reported).resolve() == expected.resolve()


def _bundled_font(probe_executable: Path, font: Path) -> Path:
    return Path(probe_executable).parents[1] / "Resources" / "fonts" / font.name


def _ask(executable: Path, *arguments: str) -> dict:
    result = subprocess.run(
        [str(executable), *arguments], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def _absent_family_donor(probe: Path) -> tuple[Path, str] | None:
    """A font file whose family this host cannot resolve without the key."""
    for root in _DONOR_ROOTS:
        if not root.is_dir():
            continue
        for candidate in sorted(root.glob("*/Contents/Resources/*")):
            if candidate.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            families = _ask(probe, "--families", str(candidate)).get("families", [])
            for family in families:
                if not _ask(probe, family)["matching_urls"]:
                    return candidate, family
    return None


def _installed_family_donor(probe: Path) -> tuple[Path, str] | None:
    """A font this host already has registered, taken from the system store.

    Deliberately not `~/Library/Fonts`: the precedence question is about an
    installed family, not about the owner's private collection, and a test has
    no business reading it.
    """
    for root in (Path("/System/Library/Fonts"), Path("/Library/Fonts")):
        if not root.is_dir():
            continue
        for candidate in sorted(root.iterdir()):
            if candidate.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                continue
            if candidate.is_symlink() or not candidate.is_file():
                continue
            for family in _ask(probe, "--families", str(candidate)).get("families", []):
                matching = _ask(probe, family)["matching_urls"]
                if any(_same_file(url, candidate) for url in matching):
                    return candidate, family
    return None


def test_parent_app_does_not_register_fonts_for_the_login_session() -> None:
    """The app must not own a family on behalf of processes it does not own."""
    delegate = (
        REPO_ROOT / "vibecrafted-app/shell-agent/app/Vibecrafted/AppDelegate.swift"
    ).read_text(encoding="utf-8")
    assert "CTFontManagerRegisterFontsForURL" not in delegate
    assert "registerBundledFonts" not in delegate
    assert "Contents/Resources/fonts/SpotMono.ttc" not in delegate


def test_builder_binds_the_font_to_the_bundle_that_draws_it() -> None:
    """The fallback moved, it was not dropped: same licensed input, new home."""
    builder = (REPO_ROOT / "scripts/build-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    assert "embed_terminal_font_resources() {" in builder
    assert (
        'install -m 0644 "$SPOT_MONO_FONT" "$resources/fonts/SpotMono.ttc"' in builder
    )
    assert "Add :ATSApplicationFontsPath string fonts" in builder
    assert 'embed_terminal_font_resources "$terminal_app"' in builder
    # The font is bound before any signature is spent on the helper bundle.
    assert builder.index(
        'embed_terminal_font_resources "$terminal_app"'
    ) < builder.index("sign_nested_app_bundles\n")


def test_product_terminal_config_asks_for_the_bundled_family() -> None:
    terminal = (REPO_ROOT / "config/vc-terminal/vibecrafted.toml").read_text(
        encoding="utf-8"
    )
    assert terminal.count('family = "Spot Mono"') == 3


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreText is macOS-only")
@pytest.mark.skipif(
    not Path("/usr/bin/clang").exists(), reason="probe needs the system clang"
)
def test_bundle_private_font_resolves_only_inside_the_declaring_process(tmp_path):
    """The load-bearing claim: the key works, and it costs nobody else."""
    probe = _probe_binary(tmp_path)
    donor = _absent_family_donor(probe)
    if donor is None:
        pytest.skip("no font file on this host carries an unregistered family")
    font, family = donor

    control = _make_bundle(tmp_path, "Control", probe, font, declare_fonts=False)
    declaring = _make_bundle(tmp_path, "Declaring", probe, font, declare_fonts=True)

    # Same bytes in Resources; only the plist key differs.
    silent = _ask(control, family)
    assert silent["declared_fonts_path"] is None
    assert silent["matching_urls"] == []
    assert silent["usable_family"] != family, "CoreText substituted, as expected"

    private = _ask(declaring, family)
    assert private["declared_fonts_path"] == "fonts"
    assert private["usable_family"] == family
    assert _same_file(private["resolved_url"], _bundled_font(declaring, font))

    # The declaring process ran first for nothing if the family leaked: ask the
    # control again, after the fact, in a fresh process.
    assert _ask(control, family)["matching_urls"] == []


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreText is macOS-only")
@pytest.mark.skipif(
    not Path("/usr/bin/clang").exists(), reason="probe needs the system clang"
)
def test_bundled_font_never_shadows_a_system_installed_family(tmp_path):
    """A fallback, not a takeover, for the stores this test is allowed to read.

    Donor comes from `/System/Library/Fonts` or `/Library/Fonts`. The owner's
    `~/Library/Fonts` is deliberately never opened, so nothing here licenses a
    claim about precedence over the owner's private collection.
    """
    probe = _probe_binary(tmp_path)
    donor = _installed_family_donor(probe)
    if donor is None:
        pytest.skip("no readable registered font file on this host")
    font, family = donor

    declaring = _make_bundle(tmp_path, "Declaring", probe, font, declare_fonts=True)
    answer = _ask(declaring, family)
    bundled = _bundled_font(declaring, font)

    assert any(_same_file(url, bundled) for url in answer["matching_urls"]), (
        "the bundled copy is registered in this process"
    )
    assert answer["usable_family"] == family
    assert _same_file(answer["resolved_url"], font), "the installed copy still wins"


BUILDER = REPO_ROOT / "scripts/build-vibecrafted-release.sh"


def _builder_function(name: str) -> str:
    """The shipped source of one builder function, lifted verbatim.

    The release builder is a single top-level script: sourcing it would run a
    build. Lifting the function bodies keeps the assertion on the bytes that
    actually ship instead of on a paraphrase maintained beside them.
    """
    builder = BUILDER.read_text(encoding="utf-8")
    start = builder.index(f"\n{name}() {{\n") + 1
    end = builder.index("\n}\n", start) + len("\n}\n")
    return builder[start:end]


def _licensed_font_preflight_condition() -> str:
    """The `[[ ... ]]` test that decides whether the font input is required."""
    lines = BUILDER.read_text(encoding="utf-8").splitlines()
    failure = next(
        index
        for index, line in enumerate(lines)
        if "missing licensed Spot Mono input" in line
    )
    guard = next(
        lines[index]
        for index in range(failure, -1, -1)
        if lines[index].startswith("if [[")
    )
    return guard[len("if [[") : guard.rindex("]]; then")].strip()


@pytest.mark.parametrize(
    ("mode", "platform_", "required"),
    [
        ("app", "darwin-arm64", True),
        ("runtime-pack", "darwin-arm64", True),
        ("runtime-pack", "linux-arm64", False),
    ],
)
def test_licensed_font_preflight_covers_every_darwin_payload(
    mode: str, platform_: str, required: bool
) -> None:
    """A Runtime Pack that materializes a .app consumes the licensed family.

    Evaluated, not read: the shipped condition is handed to bash under each
    payload shape. While it was `MODE != runtime-pack` the Darwin pack built
    its whole native payload and only then died inside the bundle walk.
    """
    condition = _licensed_font_preflight_condition()
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"set -eu\nif [[ {condition} ]]; then echo require; else echo skip; fi",
        ],
        env={"MODE": mode, "RUNTIME_PACK_PLATFORM": platform_, "PATH": "/usr/bin:/bin"},
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == ("require" if required else "skip")


@pytest.mark.skipif(sys.platform != "darwin", reason="PlistBuddy and ditto are macOS")
def test_both_materializer_roles_receive_the_font_before_any_signature(
    tmp_path: Path,
) -> None:
    """Run the real materializer twice; both payloads come out font-owning.

    One function assembles the App helper and the Runtime Pack's own bundle, so
    the proof has to be that the shipped body — not a second copy of it — puts
    SpotMono.ttc and ATSApplicationFontsPath into whichever bundle it is given.
    Nothing is signed here: the materializer runs before any signature is
    spent, and the absence of Contents/_CodeSignature is part of the claim.
    """
    terminal_repo = tmp_path / "vc-terminal"
    donor = terminal_repo / "extra/osx/vc-terminal.app"
    (donor / "Contents/MacOS").mkdir(parents=True)
    (donor / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "org.alacritty",
                "CFBundleExecutable": "alacritty",
                "CFBundleIconFile": "alacritty.icns",
                "CFBundleName": "Alacritty",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    icons = terminal_repo / "assets/icon"
    icons.mkdir(parents=True)
    (icons / "vc-terminal-icon.png").write_bytes(b"png-fixture")
    (icons / "terminal.png").write_bytes(b"png-reference-fixture")

    source_root = tmp_path / "vibecrafted"
    (source_root / "scripts").mkdir(parents=True)
    icon_builder = source_root / "scripts/build-vibecrafted-icon.sh"
    icon_builder.write_text(
        '#!/bin/sh\nprintf "icns-fixture\\n" > "$2"\n', encoding="utf-8"
    )
    icon_builder.chmod(0o755)

    binary = tmp_path / "alacritty"
    binary.write_bytes(b"terminal-binary-fixture")
    binary.chmod(0o755)
    font = tmp_path / "SpotMono.ttc"
    font.write_bytes(b"spot-mono-fixture")

    driver = "".join(
        (
            "set -euo pipefail\n",
            _builder_function("die"),
            _builder_function("embed_terminal_font_resources"),
            _builder_function("materialize_vc_terminal_app_bundle"),
            'materialize_vc_terminal_app_bundle "$1" "$2" "$3"\n',
        )
    )

    for role, relative in (
        ("helper", "Vibecrafted.app/Contents/Helpers/vc-terminal.app"),
        ("Runtime Pack", "VibecraftedRuntime/libexec/vc-terminal.app"),
    ):
        bundle = tmp_path / "payloads" / relative
        bundle.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["bash", "-c", driver, "materializer", str(bundle), str(binary), role],
            env={
                "PATH": "/usr/bin:/bin",
                "TERMINAL_REPO": str(terminal_repo),
                "SOURCE_ROOT": str(source_root),
                "SPOT_MONO_FONT": str(font),
            },
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{role}: {result.stdout}{result.stderr}"

        bundled = bundle / "Contents/Resources/fonts/SpotMono.ttc"
        assert bundled.read_bytes() == font.read_bytes(), role
        with (bundle / "Contents/Info.plist").open("rb") as handle:
            info = plistlib.load(handle)
        assert info["ATSApplicationFontsPath"] == "fonts", role
        assert info["CFBundleName"] == "VC Terminal", role
        assert info["CFBundleDisplayName"] == "VC Terminal", role
        assert not (bundle / "Contents/_CodeSignature").exists(), role
