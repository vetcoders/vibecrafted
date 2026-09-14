from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER_SCRIPT = (
    REPO_ROOT
    / "vibecrafted-core"
    / "vibecrafted_core"
    / "runtime"
    / "shell"
    / "vetcoders.sh"
)

_GENERATION_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "tui_generation_fixture", Path(__file__).with_name("_generation_fixture.py")
)
assert _GENERATION_FIXTURE_SPEC is not None and _GENERATION_FIXTURE_SPEC.loader
gen = importlib.util.module_from_spec(_GENERATION_FIXTURE_SPEC)
_GENERATION_FIXTURE_SPEC.loader.exec_module(gen)

# VC_FRAME_CONFIG_DIR has one owner (3d9da4dc, runtime/shell/lib/frontier.sh
# `_vetcoders_vc_frame_config_dir` / `_vetcoders_pin_vc_frame_config_dir`):
# outside developer mode it is pinned to $HOME/.config/vibecrafted/vc-frame,
# "even when absent", together with VC_FRAME_CONFIG_FILE. The retired resolver
# looked for vc-frame/ in the frontier companion
# ($XDG_CONFIG_HOME/vetcoders/frontier) and kept a live user pin; neither is a
# candidate any more. Starship/Atuin keep "suggest, never override" for an
# explicit STARSHIP_CONFIG / ATUIN_CONFIG, but private files in the user's own
# config directory are no longer consulted (Founder, 2026-09-14: configuration
# lives only in ~/.config/vibecrafted).


def _write_fake_binary(bin_dir: Path, name: str) -> None:
    script = bin_dir / name
    script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)


def test_ensure_vc_frame_session_uses_frontier_config_not_user_vc_frame(
    tmp_path: Path,
) -> None:
    """The vc-frame launcher is isolated from stock and companion vc-frame config.

    The engine is the installed generation's (vc_frame.sh `_vetcoders_vc_frame_bin`),
    and the config it receives is the pinned product home -- never the stock
    ~/.config/vc-frame namespace, never the frontier companion's vc-frame/.
    """
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    capture = tmp_path / "vc-frame-env.log"
    frontier_vc_frame = xdg_config_home / "vetcoders" / "frontier" / "vc-frame"
    user_vc_frame = xdg_config_home / "vc-frame"

    home.mkdir()
    frontier_vc_frame.mkdir(parents=True)
    (frontier_vc_frame / "config.kdl").write_text("// frontier\n", encoding="utf-8")
    (frontier_vc_frame / "layouts").mkdir()
    (frontier_vc_frame / "layouts" / "operator.kdl").write_text(
        "layout {}\n", encoding="utf-8"
    )
    user_vc_frame.mkdir(parents=True)
    (user_vc_frame / "config.kdl").write_text(
        "// stale user vc_frame\n", encoding="utf-8"
    )
    product_config = gen.install_product_vc_frame_config(home)
    layout_file = product_config / "layouts" / "operator.kdl"

    generation = gen.fake_generation(tmp_path)
    gen.write_executable(
        generation / "bin" / "vc-frame",
        '#!/usr/bin/env bash\nif [[ "${1:-}" == "ls" ]]; then exit 0; fi\n'
        'printf "VC_FRAME_CONFIG_DIR=%s\\n" "${VC_FRAME_CONFIG_DIR:-}" >> "$CAPTURE_FILE"\n'
        'printf "VC_FRAME_CONFIG_FILE=%s\\n" "${VC_FRAME_CONFIG_FILE:-}" >> "$CAPTURE_FILE"\n'
        'printf "args=%s\\n" "$*" >> "$CAPTURE_FILE"\nexit 0\n',
    )

    env = os.environ.copy()
    env["CAPTURE_FILE"] = str(capture)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VIBECRAFTED_PREFER_REPO_VC_FRAME", None)

    subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                f"{gen.loaded_root_prelude(generation)}; "
                f'_vetcoders_ensure_vc_frame_session "operator-test" "{layout_file}"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    payload = capture.read_text(encoding="utf-8")
    assert f"VC_FRAME_CONFIG_DIR={product_config}\n" in payload
    assert f"VC_FRAME_CONFIG_FILE={product_config / 'config.kdl'}\n" in payload
    assert f"--session operator-test --new-session-with-layout {layout_file}" in payload
    assert str(user_vc_frame) not in payload
    assert str(frontier_vc_frame) not in payload


def test_sourcing_helper_keeps_user_prompt_configs_and_pins_frame_to_product_home(
    tmp_path: Path,
) -> None:
    """Existing Starship/Atuin env is never overridden -- frontier configs are
    suggestions, not mandates. vc-frame is no longer a frontier suggestion: a
    live user VC_FRAME_CONFIG_DIR pin is replaced by the product home (3d9da4dc
    removed the "keep a pin that resolves to a config.kdl" self-heal)."""
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    fake_bin = tmp_path / "bin"
    vc_frame_config = (
        xdg_config_home / "vetcoders" / "frontier" / "vc-frame" / "config.kdl"
    )

    home.mkdir()
    fake_bin.mkdir()
    vc_frame_config.parent.mkdir(parents=True)
    vc_frame_config.write_text("layout {}\n", encoding="utf-8")
    _write_fake_binary(fake_bin, "starship")
    _write_fake_binary(fake_bin, "atuin")
    _write_fake_binary(fake_bin, "vc-frame")

    user_starship = str(tmp_path / "user-starship.toml")
    user_atuin = str(tmp_path / "user-atuin.toml")
    # A live user pin: resolves to a real config.kdl. The retired self-heal kept
    # exactly this shape; the product pin must still win over it.
    user_vc_frame_dir = tmp_path / "user-vc-frame"
    user_vc_frame_dir.mkdir()
    (user_vc_frame_dir / "config.kdl").write_text("// user\n", encoding="utf-8")
    user_vc_frame = str(user_vc_frame_dir)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env["STARSHIP_CONFIG"] = user_starship
    env["ATUIN_CONFIG"] = user_atuin
    env["VC_FRAME_CONFIG_DIR"] = user_vc_frame
    env["VC_FRAME_CONFIG_FILE"] = str(user_vc_frame_dir / "config.kdl")
    env.pop("VIBECRAFTED_PREFER_REPO_VC_FRAME", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                'printf "STARSHIP_CONFIG=%s\\n" "$STARSHIP_CONFIG"; '
                'printf "ATUIN_CONFIG=%s\\n" "$ATUIN_CONFIG"; '
                'printf "VC_FRAME_CONFIG_DIR=%s\\n" "$VC_FRAME_CONFIG_DIR"; '
                'printf "VC_FRAME_CONFIG_FILE=%s\\n" "$VC_FRAME_CONFIG_FILE"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    # User's configs must be preserved — not overwritten by frontier
    assert f"STARSHIP_CONFIG={user_starship}" in result.stdout
    assert f"ATUIN_CONFIG={user_atuin}" in result.stdout
    product_config = gen.product_vc_frame_config_dir(home)
    assert f"VC_FRAME_CONFIG_DIR={product_config}\n" in result.stdout
    assert f"VC_FRAME_CONFIG_FILE={product_config / 'config.kdl'}\n" in result.stdout
    assert user_vc_frame not in result.stdout


def test_sourcing_helper_sets_frontier_when_no_user_config(
    tmp_path: Path,
) -> None:
    """With no user env, Starship gets the frontier default and vc-frame is
    pinned to the product home -- the companion's vc-frame/ is not a source."""
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    fake_bin = tmp_path / "bin"
    vc_frame_config = (
        xdg_config_home / "vetcoders" / "frontier" / "vc-frame" / "config.kdl"
    )

    home.mkdir()
    fake_bin.mkdir()
    vc_frame_config.parent.mkdir(parents=True)
    vc_frame_config.write_text("layout {}\n", encoding="utf-8")
    _write_fake_binary(fake_bin, "starship")
    _write_fake_binary(fake_bin, "atuin")
    _write_fake_binary(fake_bin, "vc-frame")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    # No STARSHIP_CONFIG, ATUIN_CONFIG, VC_FRAME_CONFIG_DIR set
    env.pop("STARSHIP_CONFIG", None)
    env.pop("ATUIN_CONFIG", None)
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VIBECRAFTED_PREFER_REPO_VC_FRAME", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                'printf "STARSHIP_CONFIG=%s\\n" "$STARSHIP_CONFIG"; '
                'printf "ATUIN_CONFIG=%s\\n" "$ATUIN_CONFIG"; '
                'printf "VC_FRAME_CONFIG_DIR=%s\\n" "$VC_FRAME_CONFIG_DIR"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    # Frontier defaults should be set
    assert "STARSHIP_CONFIG=" in result.stdout
    # Pinned even though the product home does not exist yet ("pin even when
    # absent: startup validates it, never searches or repairs it").
    product_config = gen.product_vc_frame_config_dir(home)
    assert not product_config.exists()
    assert f"VC_FRAME_CONFIG_DIR={product_config}\n" in result.stdout
    assert str(vc_frame_config.parent) not in result.stdout


def test_sourcing_helper_pins_vc_frame_despite_default_user_vc_frame_config(
    tmp_path: Path,
) -> None:
    """vc-frame uses the product config home even when stock vc_frame has user
    config and the retired frontier companion carries its own vc-frame/.
    Private Starship/Atuin files in the user's own config directory are never
    consulted: they neither suppress nor replace the product preset."""
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    fake_bin = tmp_path / "bin"
    frontier_vc_frame_config = (
        xdg_config_home / "vetcoders" / "frontier" / "vc-frame" / "config.kdl"
    )

    home.mkdir()
    fake_bin.mkdir()
    frontier_vc_frame_config.parent.mkdir(parents=True)
    frontier_vc_frame_config.write_text("layout {}\n", encoding="utf-8")
    (xdg_config_home / "atuin").mkdir(parents=True)
    (xdg_config_home / "vc-frame").mkdir(parents=True)
    (xdg_config_home / "starship.toml").write_text("# user\n", encoding="utf-8")
    (xdg_config_home / "atuin" / "config.toml").write_text("# user\n", encoding="utf-8")
    (xdg_config_home / "vc-frame" / "config.kdl").write_text(
        "// user\n", encoding="utf-8"
    )
    _write_fake_binary(fake_bin, "starship")
    _write_fake_binary(fake_bin, "atuin")
    _write_fake_binary(fake_bin, "vc-frame")

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    env["VIBECRAFTED_ROOT"] = str(REPO_ROOT)
    env.pop("STARSHIP_CONFIG", None)
    env.pop("ATUIN_CONFIG", None)
    env.pop("VC_FRAME_CONFIG_DIR", None)
    env.pop("VIBECRAFTED_PREFER_REPO_VC_FRAME", None)

    result = subprocess.run(
        [
            "bash",
            "-lc",
            (
                f'source "{HELPER_SCRIPT}"; '
                'printf "STARSHIP_CONFIG=%s\\n" "${STARSHIP_CONFIG:-}"; '
                'printf "ATUIN_CONFIG=%s\\n" "${ATUIN_CONFIG:-}"; '
                'printf "VC_FRAME_CONFIG_DIR=%s\\n" "${VC_FRAME_CONFIG_DIR:-}"'
            ),
        ],
        check=True,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert (
        f"STARSHIP_CONFIG={REPO_ROOT / 'config' / 'starship.toml'}\n" in result.stdout
    )
    assert (
        f"ATUIN_CONFIG={REPO_ROOT / 'config' / 'atuin' / 'config.toml'}\n"
        in result.stdout
    )
    assert str(xdg_config_home / "starship.toml") not in result.stdout
    assert str(xdg_config_home / "atuin") not in result.stdout
    product_config = gen.product_vc_frame_config_dir(home)
    assert f"VC_FRAME_CONFIG_DIR={product_config}\n" in result.stdout
    assert str(xdg_config_home / "vc-frame") not in result.stdout
    assert str(frontier_vc_frame_config.parent) not in result.stdout
