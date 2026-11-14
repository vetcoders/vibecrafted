"""Execute donor build orchestration with tiny Makefile-produced artifacts."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "scripts/build-vibecrafted-release.sh"


def _run_donor_builds(tmp_path: Path, *, omit_terminal: bool = False):
    physical = tmp_path / "physical donors"
    snapshots = tmp_path / "snapshot sources"
    for area in (physical, snapshots):
        for donor in ("terminal", "frame"):
            root = area / donor
            root.mkdir(parents=True)
            (root / "source.txt").write_text(
                "pinned source" if area == snapshots else "uncommitted physical source"
            )
    terminal = snapshots / "terminal"
    frame = snapshots / "frame"
    # A stale snapshot-local binary must neither be built nor consumed.
    for donor, name in ((terminal, "alacritty"), (frame, "vc-frame")):
        stale = donor / "target/release" / name
        stale.parent.mkdir(parents=True)
        stale.write_text("#!/bin/sh\necho stale snapshot binary\n")
        stale.chmod(0o755)
    (terminal / "Makefile").write_text(
        "release-bins:\n"
        '\t@test "$(CARGO_BUILD_BUILD_DIR)" = "$(CARGO_TARGET_DIR)"\n'
        '\t@mkdir -p "$(CARGO_TARGET_DIR)/release"\n'
        + (
            "\t@true\n"
            if omit_terminal
            else '\t@printf \'#!/bin/sh\\necho "terminal: %s"\\n\' "$$(cat source.txt)" > "$(CARGO_TARGET_DIR)/release/alacritty"\n'
            '\t@chmod +x "$(CARGO_TARGET_DIR)/release/alacritty"\n'
        )
    )
    (frame / "Makefile").write_text(
        "plugins-assets:\n"
        '\t@test "$(CARGO_BUILD_BUILD_DIR)" = "$(CARGO_TARGET_DIR)"\n'
        '\t@mkdir -p "$(CARGO_TARGET_DIR)/vc-frame-plugins/wasm32-wasip1/release"\n'
        '\t@cat source.txt > "$(CARGO_TARGET_DIR)/vc-frame-plugins/wasm32-wasip1/release/plugin.wasm"\n'
        "release-binary:\n"
        '\t@test "$(CARGO_BUILD_BUILD_DIR)" = "$(CARGO_TARGET_DIR)"\n'
        '\t@mkdir -p "$(CARGO_TARGET_DIR)/release"\n'
        '\t@printf \'#!/bin/sh\\necho "frame: %s"\\n\' "$$(cat "$(CARGO_TARGET_DIR)/vc-frame-plugins/wasm32-wasip1/release/plugin.wasm")" > "$(CARGO_TARGET_DIR)/release/vc-frame"\n'
        '\t@chmod +x "$(CARGO_TARGET_DIR)/release/vc-frame"\n'
    )
    builder = BUILDER.read_text()
    start = builder.index("build_product() {\n")
    end = builder.index("\n}\n", start) + 3
    driver = tmp_path / "driver.sh"
    driver.write_text(
        "set -euo pipefail\n"
        "log() { :; }\n"
        'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
        "materialize_donor_snapshots() { :; }\n"
        "require_clean_repo() { :; }\n"
        "require_bound_revision() { :; }\n"
        "git_sha() { printf pinned-revision; }\n"
        "build_native_voc() {\n"
        '  "$terminal_source" > "$RECEIPTS/terminal.txt"\n'
        '  "$frame_source" > "$RECEIPTS/frame.txt"\n'
        "  exit 0\n"
        "}\n" + builder[start:end] + "\nbuild_product\n"
    )
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    environment.update(
        SOURCE_ROOT=str(snapshots),
        ROOT_SHA="pinned-revision",
        TERMINAL_REPO=str(terminal),
        FRAME_REPO=str(frame),
        TERMINAL_DONOR=str(physical / "terminal"),
        FRAME_DONOR=str(physical / "frame"),
        SNAPSHOT_DONORS="1",
        RECEIPTS=str(receipts),
        CARGO_TARGET_DIR=str(tmp_path / "foreign-target"),
        CARGO_BUILD_BUILD_DIR=str(tmp_path / "foreign-intermediates"),
    )
    result = subprocess.run(
        ["bash", str(driver)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    return result, physical, snapshots, receipts


def test_snapshot_sources_build_and_consume_physical_donor_targets(tmp_path: Path):
    result, physical, snapshots, receipts = _run_donor_builds(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (receipts / "terminal.txt").read_text() == "terminal: pinned source\n"
    assert (receipts / "frame.txt").read_text() == "frame: pinned source\n"
    assert (
        physical / "frame/target/vc-frame-plugins/wasm32-wasip1/release/plugin.wasm"
    ).read_text() == "pinned source"
    for donor, name in (("terminal", "alacritty"), ("frame", "vc-frame")):
        assert (physical / donor / "target/release" / name).is_file()
        assert (snapshots / donor / "target/release" / name).read_text() == (
            "#!/bin/sh\necho stale snapshot binary\n"
        )
        assert (physical / donor / "source.txt").read_text() == (
            "uncommitted physical source"
        )
    assert not (snapshots / "frame/target/vc-frame-plugins").exists()
    assert not (tmp_path / "foreign-target").exists()
    assert not (tmp_path / "foreign-intermediates").exists()


def test_missing_physical_output_refuses_stale_snapshot_binary(tmp_path: Path):
    result, _, _, receipts = _run_donor_builds(tmp_path, omit_terminal=True)
    assert result.returncode != 0
    assert "vc-terminal release binary is missing" in result.stderr
    assert not (receipts / "terminal.txt").exists()
