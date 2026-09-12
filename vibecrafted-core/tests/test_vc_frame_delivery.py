"""W2-A / W2-C: stage + wire + pane-shell substitution."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from vibecrafted_core.frontier_assets import vc_frame_config_source
from vibecrafted_core.vc_frame_delivery import (
    classify_view_path,
    stage_vc_frame_config,
    substitute_pane_shell,
    wire_vc_frame_config,
)
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
)


def _runtime_payload(runtime: Path) -> Path:
    return runtime / "vibecrafted-core" / "vibecrafted_core" / "runtime"


def _seed_complete_runtime(
    tools: Path,
    *,
    path_env: str | None = None,
    with_config: bool = True,
    publish: bool = True,
) -> Path:
    runtime = tools / "vibecrafted-full"
    (runtime / "vibecrafted-core").mkdir(parents=True)
    (_runtime_payload(runtime) / "scripts").mkdir(parents=True)
    (runtime / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (_runtime_payload(runtime) / "scripts" / "codex_spawn.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    if with_config:
        materialize_vc_frame_config(
            vc_frame_config_source(),
            _runtime_payload(runtime) / "generated" / "vc-frame",
            pane_shell=resolve_pane_shell(path_env),
            clipboard_command=resolve_clipboard_command(path_env),
        )
    if publish:
        current = tools / "vibecrafted-current"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.symlink_to(runtime)
    return runtime


def test_substitute_pane_shell_only_exact_command_zsh() -> None:
    text = 'pane command="zsh"\npane command="zsh-lookalike"\n'
    out = substitute_pane_shell(text, "bash")
    assert 'command="bash"' in out
    assert 'command="zsh-lookalike"' in out
    assert 'command="zsh"' not in out.replace("zsh-lookalike", "")


def test_substitute_noop_when_shell_is_zsh() -> None:
    text = 'command="zsh"'
    assert substitute_pane_shell(text, "zsh") == text


def test_stage_wires_view_through_current(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools)
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    # ensure zsh present so canonical retained (or force path)
    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="3.6.0-test",
        dry_run=False,
        prefer_repo=False,
        path_env=os.environ.get("PATH", ""),
    )
    assert plan.channel == "store-current"
    view = home / ".config" / "vibecrafted" / "vc-frame"
    cfg = view / "config.kdl"
    assert cfg.is_symlink() or cfg.is_file()
    resolved = cfg.resolve()
    assert resolved.is_file()
    text = resolved.read_text(encoding="utf-8")
    assert "theme" in text
    # through current
    current = tools / "vibecrafted-current"
    assert current.is_symlink()
    assert current.resolve() == runtime.resolve()
    assert (current / "Makefile").is_file()
    assert (current / "vibecrafted-core").is_dir()
    assert (_runtime_payload(current) / "scripts" / "codex_spawn.sh").is_file()
    assert (
        _runtime_payload(current) / "generated" / "vc-frame" / "config.kdl"
    ).exists()
    # Operator scripts share the one product-owned XDG projection.
    generated = _runtime_payload(current) / "generated" / "vc-frame"
    assert (generated / "vc-composer.sh").is_file()
    composer_view = view / "vc-composer.sh"
    assert composer_view.is_symlink()
    assert composer_view.resolve() == (generated / "vc-composer.sh").resolve()
    frontier = home / ".config" / "vetcoders" / "frontier" / "vc-frame"
    assert not frontier.exists()
    assert 'bind "Super e"' in text or 'bind "Super e"' in text
    assert "support_kitty_keyboard_protocol true" in text


def test_stage_does_not_republish_retired_frontier_projection(
    tmp_path: Path, monkeypatch
) -> None:
    """The retired frontier path is not a second live vc-frame authority."""
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    frontier = home / ".config" / "vetcoders" / "frontier" / "vc-frame"
    frontier.mkdir(parents=True)
    stale = frontier / "vc-composer.sh"
    stale.write_text("#!/bin/sh\n# ancient\n", encoding="utf-8")
    stale.chmod(0o755)
    stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="3.6.0-test",
        dry_run=False,
        prefer_repo=False,
        path_env=os.environ.get("PATH", ""),
    )
    assert stale.is_file()
    assert not stale.is_symlink()
    assert "ancient" in stale.read_text(encoding="utf-8")
    assert (
        home / ".config" / "vibecrafted" / "vc-frame" / "vc-composer.sh"
    ).is_symlink()


def test_wire_force_frontier_compat_flag_still_wires_only_canonical_view(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    foreign = tmp_path / "checkout"
    (foreign / "layouts").mkdir(parents=True)
    (foreign / "layouts" / "operator.kdl").write_text(
        "foreign layout\n", encoding="utf-8"
    )
    user_view = home / ".config" / "vibecrafted" / "vc-frame"
    frontier = home / ".config" / "vetcoders" / "frontier" / "vc-frame"
    user_view.mkdir(parents=True)
    frontier.mkdir(parents=True)
    (user_view / "layouts").symlink_to(foreign / "layouts")
    (frontier / "layouts").symlink_to(foreign / "layouts")

    wire_vc_frame_config(
        home=home,
        tools_home=tools,
        prefer_repo=False,
        force=True,
        force_frontier=True,
    )

    assert (user_view / "layouts").resolve() == (
        _runtime_payload(runtime) / "generated" / "vc-frame" / "layouts"
    ).resolve()
    assert (frontier / "layouts").resolve() == (foreign / "layouts").resolve()
    assert (foreign / "layouts" / "operator.kdl").read_text(
        encoding="utf-8"
    ) == "foreign layout\n"


def test_wire_only_requires_pre_materialized_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools, with_config=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    with pytest.raises(RuntimeError, match="pre-materialized"):
        wire_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)

    assert not (home / ".config" / "vibecrafted" / "vc-frame").exists()


def test_wire_only_never_mutates_published_generation(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)
    generated = _runtime_payload(runtime) / "generated" / "vc-frame"
    before = {
        path.relative_to(generated): (
            "link"
            if path.is_symlink()
            else "dir"
            if path.is_dir()
            else path.read_bytes()
        )
        for path in sorted(generated.rglob("*"))
    }
    replace_observations: list[bool] = []
    original_replace = os.replace

    def observed_replace(source, destination) -> None:
        destination_path = Path(destination)
        if destination_path.parent == home / ".config" / "vibecrafted" / "vc-frame":
            replace_observations.append(
                destination_path.exists() or destination_path.is_symlink()
            )
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", observed_replace)
    wire_vc_frame_config(
        home=home,
        tools_home=tools,
        prefer_repo=False,
        force=True,
    )

    after = {
        path.relative_to(generated): (
            "link"
            if path.is_symlink()
            else "dir"
            if path.is_dir()
            else path.read_bytes()
        )
        for path in sorted(generated.rglob("*"))
    }
    assert after == before
    assert replace_observations
    assert all(replace_observations)


def test_wire_failure_restores_displaced_user_view(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)
    view = home / ".config" / "vibecrafted" / "vc-frame" / "config.kdl"
    view.unlink()
    view.write_text("operator config\n", encoding="utf-8")
    original_replace = os.replace

    def fail_view_publish(source, destination) -> None:
        if Path(destination) == view:
            raise OSError("injected view publish failure")
        original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_view_publish)

    with pytest.raises(OSError, match="injected view publish failure"):
        wire_vc_frame_config(
            home=home,
            tools_home=tools,
            prefer_repo=False,
            force=True,
        )

    assert view.is_file()
    assert not view.is_symlink()
    assert view.read_text(encoding="utf-8") == "operator config\n"


def test_stage_keeps_mirrored_runtime_source_distinct_from_generated_view(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools, with_config=False, publish=False)
    source = runtime / "config" / "vc-frame"
    (source / "layouts").mkdir(parents=True)
    (source / "themes").mkdir()
    (source / "config.kdl").write_text(
        'theme "monochrome"\ndefault_shell "zsh"\n', encoding="utf-8"
    )
    (source / "layouts" / "operator.kdl").write_text(
        'pane command="zsh"\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        "vibecrafted_core.vc_frame_delivery.vc_frame_config_source",
        lambda: source,
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    materialize_vc_frame_config(
        source,
        _runtime_payload(runtime) / "generated" / "vc-frame",
        pane_shell="sh",
        clipboard_command=None,
    )
    current = tools / "vibecrafted-current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(runtime)

    stage_vc_frame_config(
        home=home,
        tools_home=tools,
        prefer_repo=False,
        path_env=str(tmp_path / "empty-path"),
    )

    assert (source / "config.kdl").read_text(encoding="utf-8").startswith("theme")
    generated = _runtime_payload(runtime) / "generated" / "vc-frame"
    assert (generated / "config.kdl").is_file()
    assert (home / ".config" / "vibecrafted" / "vc-frame" / "config.kdl").resolve() == (
        generated / "config.kdl"
    ).resolve()


def test_dry_run_mutates_nothing(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="dry",
        dry_run=True,
        prefer_repo=False,
    )
    assert plan.dry_run is True
    assert not (home / ".config" / "vibecrafted" / "vc-frame").exists()
    assert not tools.exists() or not list(tools.iterdir())


def test_stage_refuses_to_create_a_config_only_runtime_pointer(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    with pytest.raises(RuntimeError, match="stage the full distribution"):
        stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)

    assert not (tools / "vibecrafted-current").exists()


def test_regular_file_collision_gets_stale_backup(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    stale = view / "config.kdl"
    stale.write_text('theme "choinka"\n', encoding="utf-8")
    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="bump1",
        prefer_repo=False,
    )
    backups = list(view.glob("config.kdl.stale.*"))
    assert backups, plan.render()
    assert "choinka" in backups[0].read_text(encoding="utf-8")
    assert "choinka" not in (view / "config.kdl").resolve().read_text(encoding="utf-8")


def test_same_second_rewires_never_overwrite_operator_backups(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setattr(
        "vibecrafted_core.vc_frame_delivery._timestamp",
        lambda: "20260726_120000",
    )
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    config = view / "config.kdl"

    config.write_text("first operator config\n", encoding="utf-8")
    stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)
    config.unlink()
    config.write_text("second operator config\n", encoding="utf-8")
    stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)

    backups = sorted(view.glob("config.kdl.stale.20260726_120000-*"))
    assert len(backups) == 2
    assert {path.read_text(encoding="utf-8") for path in backups} == {
        "first operator config\n",
        "second operator config\n",
    }


def test_foreign_symlink_is_preserved_without_force(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    operator_config = tmp_path / "operator-config.kdl"
    operator_config.write_text('theme "operator-custom"\n', encoding="utf-8")
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    config_link = view / "config.kdl"
    config_link.symlink_to(operator_config)

    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="preserve-foreign",
        prefer_repo=False,
    )

    assert config_link.is_symlink()
    assert config_link.resolve() == operator_config.resolve()
    assert operator_config.read_text(encoding="utf-8") == 'theme "operator-custom"\n'
    assert any(
        action.kind == "skip"
        and action.path == str(config_link)
        and "foreign" in action.detail
        for action in plan.actions
    )


def test_foreign_symlink_is_replaced_with_force(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    operator_config = tmp_path / "operator-config.kdl"
    operator_config.write_text('theme "operator-custom"\n', encoding="utf-8")
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    config_link = view / "config.kdl"
    config_link.symlink_to(operator_config)

    stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="replace-foreign",
        prefer_repo=False,
        force=True,
    )

    assert config_link.is_symlink()
    assert config_link.resolve() != operator_config.resolve()
    assert operator_config.read_text(encoding="utf-8") == 'theme "operator-custom"\n'


def test_config_refresh_preserves_runtime_pointer_and_view_paths(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    stage_vc_frame_config(home=home, tools_home=tools, version="vA", prefer_repo=False)
    view_cfg = home / ".config" / "vibecrafted" / "vc-frame" / "config.kdl"
    path_a = str(view_cfg)
    stage_vc_frame_config(
        home=home, tools_home=tools, version="vB", prefer_repo=False, force=True
    )
    assert str(view_cfg) == path_a
    assert (tools / "vibecrafted-current").resolve() == runtime.resolve()
    assert (runtime / "Makefile").is_file()
    assert (runtime / "vibecrafted-core").is_dir()
    assert (_runtime_payload(runtime) / "scripts" / "codex_spawn.sh").is_file()
    assert view_cfg.resolve().is_file()


def test_stage_rewires_legacy_store_view_to_generated_assets(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    runtime = _seed_complete_runtime(tools)
    legacy = runtime / "config" / "vc-frame"
    legacy.mkdir(parents=True)
    (legacy / "config.kdl").write_text('theme "legacy"\n', encoding="utf-8")
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    (view / "config.kdl").symlink_to(legacy / "config.kdl")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))

    stage_vc_frame_config(home=home, tools_home=tools, prefer_repo=False)

    generated = _runtime_payload(runtime) / "generated" / "vc-frame" / "config.kdl"
    assert (view / "config.kdl").resolve() == generated.resolve()


def test_dev_mode_targets_checkout(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="dev",
        prefer_repo=True,
    )
    assert plan.channel == "dev-checkout"
    view_cfg = (home / ".config" / "vibecrafted" / "vc-frame" / "config.kdl").resolve()
    # should land under repo config/vc-frame
    assert view_cfg.is_file()
    assert "config/vc-frame" in str(view_cfg).replace("\\", "/")


def test_pane_shell_substitution_on_stage_without_zsh(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    # PATH without zsh or a clipboard helper: expose only bash.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (fake_bin / "bash").symlink_to(bash)
    runtime = _seed_complete_runtime(tools, path_env=str(fake_bin))
    plan = stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="noshell",
        prefer_repo=False,
        path_env=str(fake_bin),
    )
    staged_root = _runtime_payload(runtime) / "generated" / "vc-frame"
    staged = staged_root / "layouts"
    assert plan.pane_shell == "bash"
    research = (staged / "research.kdl").read_text(encoding="utf-8")
    assert 'command="zsh"' not in research
    assert f'command="{plan.pane_shell}"' in research
    all_kdl = "\n".join(
        path.read_text(encoding="utf-8") for path in staged_root.rglob("*.kdl")
    )
    assert 'default_shell "zsh"' not in all_kdl
    assert "exec zsh -l" not in all_kdl
    assert "exec /bin/zsh -l" not in all_kdl
    if plan.clipboard_command is None:
        assert 'copy_command "pbcopy"' not in all_kdl
        assert "pbcopy <" not in all_kdl


def test_classify_dangling(tmp_path: Path) -> None:
    link = tmp_path / "x"
    link.symlink_to(tmp_path / "missing-target")
    ch = classify_view_path(link, store_current=tmp_path / "store", checkout=None)
    assert ch == "DANGLING"
