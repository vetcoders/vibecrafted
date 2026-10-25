import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from vibecrafted_core.runtime_paths import read_version_file, vibecrafted_home

from scripts import installer_gui, vetcoders_install


def _write_installed_runtime_deck(home: Path) -> Path:
    deck = (
        home
        / ".local"
        / "share"
        / "vibecrafted"
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "deck"
        / "vibecrafted"
    )
    deck.parent.mkdir(parents=True, exist_ok=True)
    deck.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    deck.chmod(0o755)
    # The public launcher enters the generation through its manifest-bound
    # entrypoint, a byte-identical copy of the deck.
    entrypoint = deck.parents[3] / "bin" / "vibecrafted"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text(deck.read_text(encoding="utf-8"), encoding="utf-8")
    entrypoint.chmod(0o755)
    return entrypoint


def test_read_framework_version_reads_version_file(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")

    assert installer_gui.read_framework_version(str(tmp_path)) == "9.9.9"


def test_read_framework_version_returns_unknown_when_missing(tmp_path: Path) -> None:
    assert installer_gui.read_framework_version(str(tmp_path)) == "unknown"


def test_runtime_paths_reads_framework_version(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("3.2.1\n", encoding="utf-8")

    assert read_version_file(tmp_path) == "3.2.1"


def test_build_install_command_includes_compact_noninteractive_flags(
    tmp_path: Path,
) -> None:
    installer_path = tmp_path / "scripts" / "vetcoders_install.py"
    installer_path.parent.mkdir()
    installer_path.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    command = installer_gui.build_install_command(str(tmp_path), with_shell=True)

    assert command[0] == sys.executable
    assert command[1] == str(installer_path)
    assert command[2:] == [
        "install",
        "--source",
        str(tmp_path.resolve()),
        "--compact",
        "--non-interactive",
        "--mirror",
        "--with-shell",
    ]


def test_build_install_command_raises_when_installer_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        installer_gui.build_install_command(str(tmp_path), with_shell=True)


def test_installer_gui_runtime_paths_expand_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    portable_vc = tmp_path / "portable-vc"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(portable_vc))

    assert vibecrafted_home() == portable_vc
    assert installer_gui.framework_store_dir() == portable_vc / "skills"
    assert installer_gui.install_log_path() == portable_vc / "install.log"


def test_vetcoders_install_env_paths_expand_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    portable_vc = tmp_path / "portable-vc"
    portable_config = tmp_path / "portable-config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(portable_vc))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(portable_config))

    assert vetcoders_install.vibecrafted_home() == portable_vc
    assert (
        vetcoders_install._helper_target_path()
        == portable_config / "vibecrafted" / "shell" / "vc-skills.sh"
    )


def test_helper_surface_label_reads_only_the_product_shim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    assert vetcoders_install._helper_surface_label() == "not installed"

    # Retired helper locations are not a fallback: configuration lives only in
    # ~/.config/vibecrafted.
    for retired in (
        home / ".config" / "zsh" / "vc-skills.zsh",
        home / ".config" / "vetcoders" / "vc-skills.sh",
    ):
        retired.parent.mkdir(parents=True)
        retired.write_text("# retired\n", encoding="utf-8")
    assert vetcoders_install._helper_surface_label() == "not installed"

    canonical = home / ".config" / "vibecrafted" / "shell" / "vc-skills.sh"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# canonical\n", encoding="utf-8")
    assert vetcoders_install._helper_surface_label(zsh_available=True) == "bash + zsh"


def test_helper_surface_label_reports_bash_only_when_zsh_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    canonical = home / ".config" / "vibecrafted" / "shell" / "vc-skills.sh"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# canonical\n", encoding="utf-8")

    assert vetcoders_install._helper_surface_label(zsh_available=False) == "bash only"


def test_detect_system_deps_includes_optional_shells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "python3": "/usr/bin/python3",
        "git": "/usr/bin/git",
        "rsync": "/usr/bin/rsync",
        "zsh": "/bin/zsh",
    }

    monkeypatch.setattr(
        vetcoders_install.shutil,
        "which",
        lambda cmd: paths.get(cmd),
    )

    assert vetcoders_install.detect_system_deps() == paths


def test_strip_rc_entry_removes_duplicate_launcher_blocks() -> None:
    path_line = vetcoders_install._launcher_path_line()
    content = (
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{path_line}\n"
        f"{path_line}\n"
        'export PATH="$HOME/.cargo/bin:$PATH"\n'
    )

    cleaned, removed = vetcoders_install._strip_rc_entry(
        content, path_line, "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher"
    )

    assert removed == 3
    assert path_line not in cleaned
    assert "cargo/bin" in cleaned


def test_install_launcher_leaves_shell_rc_untouched_without_consent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    launcher_src = repo_root / "scripts" / "vibecrafted"
    zshrc = home / ".zshrc"

    launcher_src.parent.mkdir(parents=True)
    launcher_src.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    home.mkdir()

    original = "# user config\n"
    zshrc.write_text(original, encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    deck = _write_installed_runtime_deck(home)

    vetcoders_install._install_launcher(repo_root, dry_run=False)

    assert zshrc.read_text(encoding="utf-8") == original
    canonical = home / ".local" / "bin" / "vibecrafted"
    assert canonical.is_symlink()
    assert canonical.resolve() == deck.resolve()


def test_install_launcher_refuses_source_checkout_without_installed_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A partial install must not turn the development checkout into runtime."""
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    launcher_src = repo_root / "scripts" / "vibecrafted"

    launcher_src.parent.mkdir(parents=True)
    launcher_src.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    home.mkdir()

    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(OSError, match="installed runtime deck is missing"):
        vetcoders_install._install_launcher(repo_root, dry_run=False)

    canonical = home / ".local" / "bin" / "vibecrafted"
    assert not canonical.exists()


def test_install_launcher_dedupes_zshrc_path_entries_with_consent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    launcher_src = repo_root / "scripts" / "vibecrafted"
    zshrc = home / ".zshrc"
    bashrc = home / ".bashrc"

    launcher_src.parent.mkdir(parents=True)
    launcher_src.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    home.mkdir()

    path_line = vetcoders_install._launcher_path_line()
    zshrc.write_text(
        f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{path_line}\n{path_line}\n",
        encoding="utf-8",
    )
    bashrc.write_text("", encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    deck = _write_installed_runtime_deck(home)

    vetcoders_install._install_launcher(repo_root, dry_run=False, update_rc=True)

    zshrc_content = zshrc.read_text(encoding="utf-8")
    assert path_line not in zshrc_content
    assert zshrc_content.count(vetcoders_install._user_rc_guest_line()) == 1
    assert "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher" not in zshrc_content
    assert "vibecrafted/shell/rc.sh" in zshrc_content
    assert ".vibecrafted/bin" not in zshrc_content
    launcher_bin = home / ".local" / "bin"
    canonical = launcher_bin / "vibecrafted"
    assert canonical.is_symlink()
    assert canonical.resolve() == deck.resolve()
    assert not (home / ".vibecrafted" / "bin" / "vibecrafted").exists()
    for wrapper_name in (
        "vc-help",
        "vc-start",
        "vc-dashboard",
        "vc-init",
        "vc-dispatch",
        "vc-resume",
        "telemetry",
    ):
        wrapper_path = launcher_bin / wrapper_name
        assert wrapper_path.is_symlink()
        assert wrapper_path.readlink() == Path("vibecrafted")


def test_non_python_launcher_wrappers_have_explicit_deck_verbs() -> None:
    from vibecrafted_core import cli

    shell_wrappers = set(vetcoders_install.LAUNCHER_WRAPPERS) - set(
        vetcoders_install.PYTHON_ENTRYPOINT_LAUNCHERS
    )

    assert set(cli.SHELL_WRAPPER_VERBS) == shell_wrappers
    assert cli.SHELL_WRAPPER_VERBS == {
        "telemetry": "telemetry",
        "vc-await": "await",
        "vc-canary": "canary",
        "vc-capabilities": "capabilities",
        "vc-claims": "claims",
        "vc-dashboard": "dashboard",
        "vc-dispatch": "dispatch",
        "vc-doctor": "doctor",
        "vc-fork": "fork",
        "vc-help": "help",
        "vc-init": "init",
        "vc-justdo": "justdo",
        "vc-message": "message",
        "vc-observe": "observe",
        "vc-operator": "operator",
        "vc-receipt": "receipt",
        "vc-reinstall": "reinstall",
        "vc-resume": "resume",
        "vc-resume-session": "resume-session",
        "vc-scaffold-doctor": "scaffold-doctor",
        "vc-settlements": "settlements",
        "vc-start": "start",
        "vc-status": "status",
        "vc-stop": "stop",
        "vc-uninstall": "uninstall",
        "vc-update": "update",
        "vc-workspace": "workspace",
    }


def test_install_launcher_replaces_old_blind_local_bin_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    launcher_src = repo_root / "scripts" / "vibecrafted"
    zshrc = home / ".zshrc"

    launcher_src.parent.mkdir(parents=True)
    launcher_src.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    home.mkdir()
    zshrc.write_text(
        '# user\n# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\nexport PATH="$HOME/.local/bin:$PATH"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home))
    _write_installed_runtime_deck(home)

    vetcoders_install._install_launcher(repo_root, dry_run=False, update_rc=True)

    zshrc_content = zshrc.read_text(encoding="utf-8")
    assert 'export PATH="$HOME/.local/bin:$PATH"' not in zshrc_content.splitlines()
    assert vetcoders_install._launcher_path_line() not in zshrc_content
    assert zshrc_content.count(vetcoders_install._user_rc_guest_line()) == 1


def test_install_launcher_wires_the_shell_guest_line_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The managed guest line is appended exactly once, never creates or
    reorders the user's file, stages a live rc.sh, and returns the receipt of
    what changed."""

    home = tmp_path / "home"
    repo_root = tmp_path / "repo"
    launcher_src = repo_root / "scripts" / "vibecrafted"
    rc_source = (
        repo_root
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "shell"
        / "rc.sh"
    )
    zshrc = home / ".zshrc"

    launcher_src.parent.mkdir(parents=True)
    launcher_src.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    rc_source.parent.mkdir(parents=True)
    rc_source.write_text("# guest rc\n", encoding="utf-8")
    home.mkdir()
    original = '# user config\nexport PATH="$HOME/cargo/bin:$PATH"'
    zshrc.write_text(original, encoding="utf-8")  # no trailing newline

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    _write_installed_runtime_deck(home)

    line = vetcoders_install._user_rc_guest_line()
    first = vetcoders_install._install_launcher(
        repo_root, dry_run=False, update_rc=True
    )

    staged = home / ".config" / "vibecrafted" / "shell" / "rc.sh"
    assert staged.read_bytes() == rc_source.read_bytes()
    content = zshrc.read_text(encoding="utf-8")
    assert content.startswith(original + "\n")
    assert content.count(line) == 1
    assert content.count(f"# {vetcoders_install._USER_RC_GUEST_COMMENT}") == 1
    assert not (home / ".bashrc").exists()  # never create the user's rc file
    actions = {(record["path"], record["action"]) for record in first}
    assert (str(staged), "staged") in actions
    assert (str(zshrc), "appended") in actions

    second = vetcoders_install._install_launcher(
        repo_root, dry_run=False, update_rc=True
    )
    assert zshrc.read_text(encoding="utf-8") == content
    actions = {(record["path"], record["action"]) for record in second}
    assert (str(staged), "current") in actions
    assert (str(zshrc), "present") in actions


def test_pipeline_category_describes_release_not_removed_ship_skill() -> None:
    description = vetcoders_install.SKILL_CATEGORIES["pipeline"]["description"]

    assert "release" in description
    assert "ship" not in description


def test_app_installer_writes_deck_verb_wrappers() -> None:
    """The DMG installer must publish deck-verb wrappers, in lockstep with cli.py.

    ~/.local/bin shims exec the bash deck through a shebang, and the kernel
    rebuilds argv for `#!` targets, so the invoked wrapper name cannot survive
    the exec chain. The only identity that survives is the verb written into
    the shim itself. Without it `vc-resume claude --session <id>` degraded to
    `vibecrafted claude --session <id>` ("Unknown mode: --session").
    """
    from vibecrafted_core import cli

    # vc-start ships as a real runtime binary; the installer's bin guard skips
    # it dynamically, so the static verb list intentionally leaves it out.
    expected = {
        name: verb
        for name, verb in cli.SHELL_WRAPPER_VERBS.items()
        if name != "vc-start"
    }
    assert vetcoders_install._RUNTIME_WRAPPER_VERBS == expected


@pytest.mark.parametrize("lane", ["install", "doctor", "manual"])
def test_guest_entry_preserves_python_uv_winners_and_launcher_availability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys, lane: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config_home = home / ".config"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    _write_installed_runtime_deck(home)
    repo = tmp_path / "source"
    source = repo / "vibecrafted-core/vibecrafted_core/runtime/shell/rc.sh"
    source.parent.mkdir(parents=True)
    shutil.copy2(
        Path(__file__).resolve().parents[2]
        / "vibecrafted-core/vibecrafted_core/runtime/shell/rc.sh",
        source,
    )
    (repo / "scripts").mkdir()
    (repo / "scripts/vibecrafted").write_text("#!/bin/sh\nexit 0\n")
    preferred = home / "preferred"
    preferred.mkdir()
    local = home / ".local/bin"
    local.mkdir(parents=True, exist_ok=True)
    for directory, marker in [(preferred, "PREFERRED"), (local, "LOCAL")]:
        for name in ["python3", "uv"]:
            tool = directory / name
            tool.write_text(f"#!/bin/sh\necho {marker}_{name}\n")
            tool.chmod(0o755)
    vc_tool = local / "vc-fixture-tool"
    vc_tool.write_text("#!/bin/sh\necho VC_AVAILABLE\n")
    vc_tool.chmod(0o755)
    if lane == "doctor":
        wrapper = local / "vibecrafted"
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        wrapper.chmod(0o755)
    original = f'# user config\nexport PATH="{preferred}:$PATH"\n'
    rc = home / ".zshrc"
    legacy = f"# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher\n{vetcoders_install._launcher_path_line()}\n"
    rc.write_text(original + (legacy if lane != "manual" else ""))
    vetcoders_install._stage_user_rc_guest(repo, dry_run=False)

    def compose() -> None:
        if lane == "doctor":
            findings = vetcoders_install._doctor_fix_rc_files()
            assert any(
                f.component == "rc-fix:.zshrc" and f.level == "ok" for f in findings
            )
        else:
            vetcoders_install._install_launcher(
                repo, dry_run=False, update_rc=lane == "install"
            )
            output = capsys.readouterr().out
            if lane == "manual":
                instruction = (
                    output.split(
                        "Shell rc files unchanged. To expose launchers, add:\n", 1
                    )[1]
                    .splitlines()[0]
                    .strip()
                )
                assert rc.read_text() == original
                rc.write_text(original + instruction + "\n")

    compose()
    first = rc.read_text()
    if lane != "manual":
        compose()
        assert rc.read_text() == first
    result = subprocess.run(
        [
            "/bin/zsh",
            "-d",
            "-i",
            "-c",
            (
                "command -v python3; python3; command -v uv; uv; vc-fixture-tool; "
                'print -r -- "HOMEVAR=${PYTHONHOME-UNSET} PATHVAR=${PYTHONPATH-UNSET}"'
            ),
        ],
        env={
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config_home),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": os.environ["TMPDIR"],
            "TERM": "dumb",
        },
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"{preferred}/python3" in result.stdout
    assert f"{preferred}/uv" in result.stdout
    assert "PREFERRED_python3" in result.stdout
    assert "PREFERRED_uv" in result.stdout
    assert "LOCAL_" not in result.stdout
    assert "VC_AVAILABLE" in result.stdout
    assert "HOMEVAR=UNSET PATHVAR=UNSET" in result.stdout
    assert first.count(vetcoders_install._user_rc_guest_line()) == 1
    assert vetcoders_install._launcher_path_line() not in first
    cleaned = first
    for line, comment in vetcoders_install._uninstall_rc_entries():
        cleaned, _ = vetcoders_install._strip_rc_entry(cleaned, line, comment)
    assert cleaned.rstrip() == original.rstrip()
