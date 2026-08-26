from __future__ import annotations

import importlib.util
import json
import shutil
import struct
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest
from vibecrafted_core.doctor import _vc_frame_delivery_findings
from vibecrafted_core.frontier_assets import vc_frame_config_source
from vibecrafted_core.vc_frame_delivery import stage_vc_frame_config
from vibecrafted_core.vc_frame_staging import (
    materialize_vc_frame_config,
    resolve_clipboard_command,
    resolve_pane_shell,
)

from scripts import vetcoders_install as installer

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_test_source_provenance(
    root: Path,
    *,
    owner_repo: str = "vetcoders/vibecrafted",
    source_revision: str = "b" * 40,
) -> dict[str, object]:
    """Mint a test-only carrier for a detached fixture's current input tree."""
    provenance: dict[str, object] = {
        "schema": installer._SOURCE_PROVENANCE_SCHEMA,
        "owner_repo": owner_repo,
        "source_revision": source_revision,
        "payload": installer._distribution_manifest._distribution_tree_record(root),
    }
    carrier = root / "source-provenance.json"
    carrier.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    carrier.chmod(0o644)
    return provenance


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _pin_canonical_runtime_roots(monkeypatch, home: Path, crafted_home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME",
        str(home / ".local" / "share" / "vibecrafted"),
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local" / "bin"))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(crafted_home))


def test_install_foundation_from_bundle_copies_platform_payload(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "bundle"
    bin_dir = tmp_path / "bin"
    vendor_dir = repo_root / "bin" / "vendor" / "darwin-arm64"
    vendor_dir.mkdir(parents=True)
    monkeypatch.setattr(installer, "detect_vendor_platform", lambda: "darwin-arm64")

    foundations = [
        f
        for f in installer.FOUNDATIONS
        if f.name in installer.VENDORED_FOUNDATION_BINARIES
    ]
    assert {f.name for f in foundations} == {
        "aicx",
        "aicx-mcp",
        "loct",
        "loctree-mcp",
        "vc-frame",
    }

    for foundation in foundations:
        name = installer.VENDORED_FOUNDATION_BINARIES[foundation.name]
        _write_executable(
            vendor_dir / name,
            f"#!/usr/bin/env bash\nprintf '{name} 0.0.0-test\\n'\n",
        )

    installed = [
        installer.install_foundation_from_bundle(f, repo_root, bin_dir=bin_dir)
        for f in foundations
    ]

    assert {path.name for path in installed if path is not None} == {
        "aicx",
        "aicx-mcp",
        "loct",
        "loctree-mcp",
        "vc-frame",
    }
    for path in installed:
        assert path is not None
        assert path.is_file()
        assert path.stat().st_mode & 0o111
        assert subprocess.run([str(path), "--version"], check=False).returncode == 0


def test_install_foundation_from_bundle_ignores_platform_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    repo_root = tmp_path / "bundle"
    bin_dir = tmp_path / "bin"
    linux_dir = repo_root / "bin" / "vendor" / "linux-x64"
    linux_dir.mkdir(parents=True)
    _write_executable(
        linux_dir / "vc-frame",
        "#!/usr/bin/env bash\nprintf 'wrong platform\\n'\n",
    )
    monkeypatch.setattr(installer, "detect_vendor_platform", lambda: "darwin-arm64")
    monkeypatch.setattr(installer.shutil, "which", lambda _name: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    foundation = next(f for f in installer.FOUNDATIONS if f.name == "vc-frame")

    assert (
        installer.install_foundation_from_bundle(foundation, repo_root, bin_dir=bin_dir)
        is None
    )
    assert not (bin_dir / "vc-frame").exists()
    assert installer.install_or_find_foundation(foundation, repo_root) == (
        "",
        "not-installed",
    )


def test_run_doctor_smokes_helper_and_launcher_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    launcher_bin = home / ".local" / "bin"
    helper_dir = config_home / "vetcoders"

    store_path.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)
    helper_dir.mkdir(parents=True)

    helper_file = helper_dir / "vc-skills.sh"
    helper_file.write_text(
        f"# shellcheck shell=bash\n{installer.HELPER_SHIM_MARKER}\nvc-help() {{ :; }}\nvc-agents() {{ :; }}\nvc-init() {{ :; }}\nvc-intents() {{ :; }}\nvc-ownership() {{ :; }}\nvc-loop() {{ :; }}\nvc-ship() {{ :; }}\nvc-cron() {{ :; }}\nvc-marbles() {{ :; }}\ncodex-implement() {{ :; }}\ncodex-marbles() {{ :; }}\nskills-sync() {{ :; }}"
        + "\n",
        encoding="utf-8",
    )

    _write_executable(
        launcher_bin / "vibecrafted",
        "#!/usr/bin/env bash\nprintf '𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. help ok\\n'\n",
    )
    (launcher_bin / "vc-help").symlink_to("vibecrafted")

    state = installer.InstallState(
        framework_version="1.2.1",
        shell_helpers=True,
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(installer, "_slack_provider_contract_findings", list)
    _real_which = shutil.which
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: None if name == "zsh" else _real_which(name),
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["shell-helper-runtime"].level == "ok"
    assert indexed["launcher-runtime"].level == "ok"

    guide_path = installer.write_start_here_guide(store_path, state, findings)
    guide_text = guide_path.read_text(encoding="utf-8")
    assert "vibecrafted init claude" in guide_text
    assert "vibecrafted dou claude" in guide_text
    assert "vibecrafted decorate codex" in guide_text
    # 5d39e4da (backyard product spine) replaced the "Dashboard is optional"
    # paragraph with the "Optional surfaces" section — assert the new contract.
    assert "## Optional surfaces" in guide_text
    assert "vibecrafted dashboard" in guide_text


def test_run_doctor_flags_dark_standard_decks(tmp_path: Path, monkeypatch) -> None:
    """3.6.0 regression: the manifest recorded only 'agents', the installer
    pruned the claude/codex views, and doctor kept reporting ok. Doctor must
    surface dark standard decks even when the manifest never recorded them."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    skill = store_path / "vc-init"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# vc-init\n", encoding="utf-8")

    agents_view = home / ".agents" / "skills"
    agents_view.mkdir(parents=True)
    (agents_view / "vc-init").symlink_to(skill)

    state = installer.InstallState(
        framework_version="3.6.0",
        skills=["vc-init"],
        runtimes=["agents"],
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["runtime:claude"].level == "warn"
    assert indexed["runtime:codex"].level == "warn"
    assert indexed["symlink:agents/vc-init"].level == "ok"


def test_print_doctor_default_is_summary_first_and_bounded(
    capsys, tmp_path: Path
) -> None:
    """CLI_PRODUCT_SPEC §6.4: verdict in two lines, passing checks are a
    count (never lines), details live behind --verbose."""
    findings = [
        installer.DoctorFinding("ok", "store", "ready"),
        installer.DoctorFinding("ok", "launcher", "ready"),
        installer.DoctorFinding("warn", "loctree", "missing — optional foundation"),
    ]

    exit_code = installer.print_doctor(findings, guide_path=tmp_path / "START_HERE.md")

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "doctor" in output
    assert "3 checks" in output
    assert "2 ok" in output
    assert "1 warnings" in output
    assert "0 failures" in output
    # warnings are listed; passing checks are a count, not lines
    assert "loctree: missing" in output
    assert "store: ready" not in output
    assert "details: vibecrafted doctor --verbose" in output
    assert "START_HERE.md" in output


def test_print_doctor_verbose_lists_every_check_and_golden_paths(
    capsys, tmp_path: Path
) -> None:
    findings = [installer.DoctorFinding("ok", "store", "ready")]

    exit_code = installer.print_doctor(
        findings, guide_path=tmp_path / "START_HERE.md", verbose=True
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "store: ready" in output
    assert "Simple path:" in output
    assert "vibecrafted init claude" in output
    assert "Ship-ready path:" in output
    assert "vibecrafted decorate codex" in output
    assert "vibecrafted hydrate codex" in output
    assert "vibecrafted release codex" in output
    assert "START_HERE.md" in output


def test_print_doctor_failure_hint_uses_vibecrafted_not_old_brand(
    capsys, tmp_path: Path
) -> None:
    findings = [installer.DoctorFinding("fail", "store", "missing")]

    exit_code = installer.print_doctor(findings, guide_path=tmp_path / "START_HERE.md")

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "store: missing" in output
    assert "vibecrafted doctor --fix-rc --fix-launchers" in output
    assert "Vetcoders install" not in output


def test_run_doctor_includes_dashboard_smoke(tmp_path: Path, monkeypatch) -> None:
    """Doctor checks that 'vibecrafted dashboard ls' subcommand is functional."""
    home = tmp_path / "home"
    config_home = home / ".config"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    launcher_bin = home / ".local" / "bin"
    helper_dir = config_home / "vetcoders"

    store_path.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)
    helper_dir.mkdir(parents=True)

    helper_file = helper_dir / "vc-skills.sh"
    helper_file.write_text(
        f"# shellcheck shell=bash\n{installer.HELPER_SHIM_MARKER}\nvc-help() {{ :; }}\nvc-agents() {{ :; }}\nvc-init() {{ :; }}\nvc-intents() {{ :; }}\nvc-ownership() {{ :; }}\nvc-loop() {{ :; }}\nvc-ship() {{ :; }}\nvc-cron() {{ :; }}\nvc-marbles() {{ :; }}\ncodex-implement() {{ :; }}\ncodex-marbles() {{ :; }}\nskills-sync() {{ :; }}"
        + "\n",
        encoding="utf-8",
    )

    _write_executable(
        launcher_bin / "vibecrafted",
        "#!/usr/bin/env bash\nprintf '𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. help ok\\n'\n",
    )
    _write_executable(
        launcher_bin / "vc-dashboard",
        "#!/usr/bin/env bash\nprintf 'dashboard-ok\\n'\n",
    )
    (launcher_bin / "vc-help").symlink_to("vibecrafted")

    state = installer.InstallState(
        framework_version="1.2.1",
        shell_helpers=True,
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    _real_which = shutil.which
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: None if name == "zsh" else _real_which(name),
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert "dashboard-smoke" in indexed
    assert indexed["dashboard-smoke"].level == "ok"


def test_run_doctor_uses_bundled_vc_frame_when_not_on_path(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    store_path = crafted_home / "skills"
    vc_frame = runtime_home / "bin" / "vc-frame"

    store_path.mkdir(parents=True)
    vc_frame.parent.mkdir(parents=True)
    _write_executable(
        vc_frame,
        '#!/usr/bin/env bash\nif [[ "$1" == "--version" ]]; then echo \'vc-frame 0.test\'; else exit 0; fi\n',
    )

    state = installer.InstallState(framework_version="1.5.0")
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["vc-frame"].level == "ok"
    assert str(vc_frame) in indexed["vc-frame"].message


def test_run_doctor_accepts_gemini_help_when_version_flag_exits_nonzero(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    fake_bin = tmp_path / "bin"
    gemini = fake_bin / "gemini"

    store_path.mkdir(parents=True)
    fake_bin.mkdir()
    _write_executable(
        gemini,
        "#!/usr/bin/env bash\ncase \"${1:-}\" in\n  --help) echo 'gemini help'; exit 0 ;;\n  *) exit 1 ;;\nesac"
        + "\n",
    )

    state = installer.InstallState(framework_version="1.5.0")
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: str(gemini) if name == "gemini" else None,
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["agent-stream:gemini"].level == "ok"
    assert "version flag unavailable" in indexed["agent-stream:gemini"].message


def test_run_doctor_finds_launchers_outside_local_bin(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    launcher_bin = home / ".local" / "bin"
    helper_dir = config_home / "vetcoders"

    store_path.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)
    helper_dir.mkdir(parents=True)

    helper_file = helper_dir / "vc-skills.sh"
    helper_file.write_text(
        f"# shellcheck shell=bash\n{installer.HELPER_SHIM_MARKER}\nvc-help() {{ :; }}\nvc-agents() {{ :; }}\nvc-init() {{ :; }}\nvc-intents() {{ :; }}\nvc-ownership() {{ :; }}\nvc-loop() {{ :; }}\nvc-ship() {{ :; }}\nvc-cron() {{ :; }}\nvc-marbles() {{ :; }}\ncodex-implement() {{ :; }}\ncodex-marbles() {{ :; }}\nskills-sync() {{ :; }}"
        + "\n",
        encoding="utf-8",
    )

    _write_executable(
        launcher_bin / "vibecrafted",
        "#!/usr/bin/env bash\nprintf '𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. help ok\\n'\n",
    )
    (launcher_bin / "vc-help").symlink_to("vibecrafted")
    _write_executable(
        launcher_bin / "vc-dashboard",
        "#!/usr/bin/env bash\nprintf 'dashboard-ok\\n'\n",
    )
    for wrapper_name in installer.LAUNCHER_WRAPPERS:
        wrapper_path = launcher_bin / wrapper_name
        if not wrapper_path.exists():
            wrapper_path.symlink_to("vibecrafted")

    state = installer.InstallState(
        framework_version="1.2.1",
        shell_helpers=True,
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    _real_which = shutil.which
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: None if name == "zsh" else _real_which(name),
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["launcher-wrappers"].level == "ok"
    assert indexed["launcher-runtime"].level == "ok"
    assert indexed["dashboard-smoke"].level == "ok"


def test_cmd_doctor_fix_launchers_repairs_missing_wrappers(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    config_home = home / ".config"
    store_path = crafted_home / "skills"
    launcher_bin = home / ".local" / "bin"
    source_root = runtime_home / "tools" / "vibecrafted-main"
    current_link = runtime_home / "tools" / "vibecrafted-current"

    store_path.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)
    (source_root / "scripts").mkdir(parents=True)
    (source_root / "skills").mkdir(parents=True)
    current_link.parent.mkdir(parents=True, exist_ok=True)
    current_link.symlink_to(source_root)

    _write_executable(
        source_root / "scripts" / "vibecrafted",
        (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8"),
    )
    (home / ".local" / "share" / "uv" / "tools" / "vibecrafted" / "bin").mkdir(
        parents=True
    )
    _write_executable(
        home
        / ".local"
        / "share"
        / "uv"
        / "tools"
        / "vibecrafted"
        / "bin"
        / "vibecrafted",
        "#!/usr/bin/env bash\nprintf 'uv-tool vibecrafted shim\\n'\n",
    )
    (source_root / "VERSION").write_text("1.4.1-test\n", encoding="utf-8")

    _write_executable(
        launcher_bin / "vibecrafted",
        "#!/usr/bin/env bash\n# vibecrafted stale launcher\nprintf 'stale launcher\\n'\n",
    )
    (launcher_bin / "vc-help").symlink_to("vibecrafted")

    state = installer.InstallState(framework_version="1.4.1-test")
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(installer, "_slack_provider_contract_findings", list)
    detached_source = tmp_path / "detached-source"
    installer.stage_distribution_payload(REPO_ROOT, detached_source, mirror=True)
    (detached_source / "VERSION").write_text("1.4.1-test\n", encoding="utf-8")
    _write_executable(
        detached_source / "bin/python3",
        f'#!/bin/sh\nexec {installer.shlex_quote(str(Path(sys.executable).absolute()))} "$@"\n',
    )
    _write_test_source_provenance(detached_source)
    monkeypatch.setattr(
        installer, "_doctor_launcher_source_root", lambda _store: detached_source
    )

    exit_code = installer.cmd_doctor(Namespace(fix_rc=False, fix_launchers=True))

    # Launcher repair succeeds, but the fresh-state doctor remains fail-closed
    # until the mandatory packaged release verifier is actually installed.
    assert exit_code == 1
    assert (launcher_bin / "vc-init").is_symlink()
    assert (launcher_bin / "vc-start").is_symlink()
    assert not (crafted_home / "bin" / "vc-init").exists()
    assert not (crafted_home / "bin" / "vc-start").exists()

    refreshed_state = installer.InstallState.load(crafted_home)
    assert any(entry.endswith("/vc-init") for entry in refreshed_state.launcher_entries)
    findings = installer.run_doctor(store_path, refreshed_state)
    indexed = {finding.component: finding for finding in findings}
    assert indexed["launcher-wrappers"].level == "ok"


def test_product_tool_discovery_records_path_without_rehoming(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    cargo_bin = home / ".cargo" / "bin"
    launcher_bin = home / ".local" / "bin"
    store_path.mkdir(parents=True)
    cargo_bin.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)

    _write_executable(cargo_bin / "loct", "#!/usr/bin/env bash\nprintf 'loct-dev\\n'\n")
    _write_executable(
        cargo_bin / "vc-frame", "#!/usr/bin/env bash\nprintf 'vc-frame-dev\\n'\n"
    )

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("PATH", str(cargo_bin))
    monkeypatch.setattr(
        installer,
        "FOUNDATIONS",
        [
            installer.Foundation(
                name="loct",
                description="Loctree operator CLI short command",
                channels=["canonical"],
                packages={"canonical": "curl -fsSL https://loct.io/install.sh | sh"},
                verify_cmd="loct --version",
            ),
            installer.Foundation(
                name="vc-frame",
                description="VC Frame multi-agent terminal workspace surface",
                channels=["canonical"],
                packages={
                    "canonical": "curl -fsSL https://vibecrafted.io/install.sh | bash",
                },
                verify_cmd="vc-frame --version",
            ),
        ],
    )

    product_tools = installer.snapshot_product_tool_state()

    assert product_tools["loct"]["path"] == str(cargo_bin / "loct")
    assert product_tools["loct"]["managed_by"] == "external-path"
    assert product_tools["vc-frame"]["path"] == str(cargo_bin / "vc-frame")
    assert not (launcher_bin / "loct").exists()
    assert not (launcher_bin / "vc-frame").exists()

    state = installer.InstallState(product_tools=product_tools)
    state.save(store_path)
    loaded = installer.InstallState.load(store_path)

    assert loaded.product_tools["loct"]["path"] == str(cargo_bin / "loct")
    assert loaded.product_tools["vc-frame"]["path"] == str(cargo_bin / "vc-frame")


def test_product_tool_discovery_prefers_vc_frame_for_vc_frame_key(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    cargo_bin = home / ".cargo" / "bin"
    cargo_bin.mkdir(parents=True)

    _write_executable(
        cargo_bin / "vc-frame", "#!/usr/bin/env bash\nprintf 'vc-frame-dev\\n'\n"
    )
    _write_executable(
        cargo_bin / "vc-frame", "#!/usr/bin/env bash\nprintf 'vc_frame-dev\\n'\n"
    )

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("PATH", str(cargo_bin))
    monkeypatch.setattr(
        installer,
        "FOUNDATIONS",
        [
            installer.Foundation(
                name="vc-frame",
                description="VC Frame multi-agent terminal workspace surface",
                channels=["canonical"],
                packages={
                    "canonical": "curl -fsSL https://vibecrafted.io/install.sh | bash",
                },
                verify_cmd="vc-frame --version",
            ),
        ],
    )

    product_tools = installer.snapshot_product_tool_state()

    assert product_tools["vc-frame"]["path"] == str(cargo_bin / "vc-frame")


def test_layout_migrate_promotes_legacy_agents_scripts_to_current_tools(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    store_path = crafted_home / "skills"
    legacy_agents = store_path / "vc-agents"
    legacy_scripts = legacy_agents / "scripts"
    legacy_scripts.mkdir(parents=True)
    (legacy_agents / "SKILL.md").write_text("legacy skill\n", encoding="utf-8")
    _write_executable(
        legacy_scripts / "codex_spawn.sh",
        "#!/usr/bin/env bash\nprintf 'legacy codex\\n'\n",
    )

    state = installer.InstallState(framework_version="1.5.0-legacy")
    state.save(store_path)
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)

    exit_code = installer.cmd_layout(
        Namespace(action="migrate", dry_run=False, mirror=False, force=False)
    )

    current_agents = runtime_home / "tools" / "vibecrafted-current" / "agents"
    assert exit_code == 0
    assert (
        (current_agents / "scripts" / "codex_spawn.sh")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bash")
    )
    assert (current_agents / "SKILL.md").read_text(encoding="utf-8") == "legacy skill\n"

    loaded = installer.InstallState.load(store_path)
    assert loaded.layout_transfers[-1]["direction"] == "legacy-to-new"
    assert loaded.layout_transfers[-1]["status"] == "completed"
    assert loaded.layout_transfers[-1]["source"] == str(legacy_agents)
    assert loaded.layout_transfers[-1]["target"] == str(current_agents)


def test_layout_rollback_restores_new_agents_scripts_to_legacy_store(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    store_path = crafted_home / "skills"
    current_agents = runtime_home / "tools" / "vibecrafted-current" / "agents"
    current_scripts = current_agents / "scripts"
    current_scripts.mkdir(parents=True)
    (current_agents / "SKILL.md").write_text("new skill\n", encoding="utf-8")
    _write_executable(
        current_scripts / "claude_spawn.sh",
        "#!/usr/bin/env bash\nprintf 'new claude\\n'\n",
    )

    state = installer.InstallState(framework_version="2.0-new")
    state.save(store_path)
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)

    exit_code = installer.cmd_layout(
        Namespace(action="rollback", dry_run=False, mirror=False, force=False)
    )

    legacy_agents = store_path / "vc-agents"
    assert exit_code == 0
    assert (
        (legacy_agents / "scripts" / "claude_spawn.sh")
        .read_text(encoding="utf-8")
        .startswith("#!/usr/bin/env bash")
    )
    assert (legacy_agents / "SKILL.md").read_text(encoding="utf-8") == "new skill\n"

    loaded = installer.InstallState.load(store_path)
    assert loaded.layout_transfers[-1]["direction"] == "new-to-legacy"
    assert loaded.layout_transfers[-1]["status"] == "completed"
    assert loaded.layout_transfers[-1]["source"] == str(current_agents)
    assert loaded.layout_transfers[-1]["target"] == str(legacy_agents)


def test_layout_transfer_refuses_unmanaged_target_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    store_path = crafted_home / "skills"
    legacy_agents = store_path / "vc-agents"
    legacy_scripts = legacy_agents / "scripts"
    legacy_scripts.mkdir(parents=True)
    _write_executable(
        legacy_scripts / "codex_spawn.sh",
        "#!/usr/bin/env bash\nprintf 'legacy codex\\n'\n",
    )
    current_scripts = (
        runtime_home / "tools" / "vibecrafted-current" / "agents" / "scripts"
    )
    current_scripts.mkdir(parents=True)
    _write_executable(
        current_scripts / "codex_spawn.sh",
        "#!/usr/bin/env bash\nprintf 'operator custom codex\\n'\n",
    )

    state = installer.InstallState(framework_version="1.5.0-legacy")
    state.save(store_path)
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)

    exit_code = installer.cmd_layout(
        Namespace(action="migrate", dry_run=False, mirror=False, force=False)
    )

    assert exit_code == 1
    assert "operator custom codex" in (current_scripts / "codex_spawn.sh").read_text(
        encoding="utf-8"
    )
    loaded = installer.InstallState.load(store_path)
    assert loaded.layout_transfers[-1]["direction"] == "legacy-to-new"
    assert loaded.layout_transfers[-1]["status"] == "blocked"


def test_install_launcher_does_not_overwrite_unmanaged_dev_wrapper(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    launcher_bin = home / ".local" / "bin"
    source_root = runtime_home / "tools" / "vibecrafted-main"
    (source_root / "scripts").mkdir(parents=True)
    launcher_bin.mkdir(parents=True)

    _write_executable(
        source_root / "scripts" / "vibecrafted",
        (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8"),
    )
    unmanaged = launcher_bin / "vc-research"
    _write_executable(
        unmanaged,
        "#!/usr/bin/env bash\nprintf 'my dev wrapper must survive\\n'\n",
    )

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    installed_deck = (
        runtime_home
        / "tools"
        / "vibecrafted-current"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "deck"
        / "vibecrafted"
    )
    installed_deck.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(
        installed_deck,
        (REPO_ROOT / "scripts" / "vibecrafted").read_text(encoding="utf-8"),
    )
    installed_entrypoint = (
        runtime_home / "tools" / "vibecrafted-current" / "bin" / "vibecrafted"
    )
    installed_entrypoint.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(installed_entrypoint, installed_deck.read_text(encoding="utf-8"))

    installer._install_launcher(source_root, dry_run=False, update_rc=False)

    assert unmanaged.read_text(encoding="utf-8").endswith(
        "my dev wrapper must survive\\n'\n"
    )
    assert not unmanaged.is_symlink()
    assert (launcher_bin / "vc-help").is_symlink()


def test_secure_walkaround_launcher_has_one_uv_tool_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    current_tools = home / ".local/share/vibecrafted/tools/vibecrafted-current"
    uv_bin = home / ".local/share/uv/tools/vibecrafted/bin"
    python_bin = uv_bin / "python"
    wrapper = uv_bin / installer.SECURE_WALKAROUND_LAUNCHER
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("UV_TOOL_DIR", str(uv_bin.parent.parent))
    uv_bin.mkdir(parents=True)
    _write_executable(python_bin, "#!/bin/sh\nexit 0\n")

    installed = installer._install_secure_walkaround_launcher(
        current_tools,
        python_bin,
        launcher_path=wrapper,
    )

    assert installed == wrapper
    assert "/.venv/" not in installed.read_text(encoding="utf-8")


def test_installer_doctor_fails_when_walkaround_runner_launcher_is_missing() -> None:
    state = installer.InstallState(
        launcher_entries=["/managed/bin/verify-vibecrafted-walkaround"]
    )

    assert (
        installer._python_entrypoint_issue_level(
            ["verify-vibecrafted-walkaround:missing"], state=state
        )
        == "fail"
    )
    assert (
        installer._python_entrypoint_issue_level(
            ["verify-vibecrafted-walkaround:missing"], state=installer.InstallState()
        )
        == "fail"
    )


_LEGACY_RUNTIME_GENERATION_HASH_PATHS = frozenset(
    {
        "VERSION",
        "scripts/vibecrafted",
        "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl",
        installer._RUNTIME_GENERATION_ENTRYPOINT.as_posix(),
    }
)

_RUNTIME_GENERATION_FIXTURE_SOURCES = {
    Path("VERSION"): Path("VERSION"),
    Path("scripts/distribution_manifest.py"): Path("scripts/distribution_manifest.py"),
    Path("scripts/installer_brand.py"): Path("scripts/installer_brand.py"),
    Path("scripts/vetcoders_install.py"): Path("scripts/vetcoders_install.py"),
    Path("scripts/vibecrafted"): Path("scripts/vibecrafted"),
    Path(
        "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame/config.kdl"
    ): Path("vibecrafted-core/vibecrafted_core/config/vc-frame/config.kdl"),
    installer._RUNTIME_GENERATION_ENTRYPOINT: Path(
        "vibecrafted-core/vibecrafted_core/deck/vibecrafted"
    ),
    Path("vibecrafted-core/vibecrafted_core/product_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/product_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"): Path(
        "vibecrafted-core/vibecrafted_core/runtime_pack_contract.py"
    ),
    Path("vibecrafted-core/vibecrafted_core/walkaround_runner.py"): Path(
        "vibecrafted-core/vibecrafted_core/walkaround_runner.py"
    ),
    Path(
        "vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"
    ): Path("vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json"),
    Path("vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"): Path(
        "vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json"
    ),
    Path("vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"): Path(
        "vibecrafted-core/vibecrafted_core/trust/vibecrafted-signing-v1.pub"
    ),
}


def _write_release_contract_runtime_manifest(
    current_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(_RUNTIME_GENERATION_FIXTURE_SOURCES) == 13
    assert (
        frozenset(_RUNTIME_GENERATION_FIXTURE_SOURCES)
        == installer._RUNTIME_GENERATION_REQUIRED_HASHES
    )
    for target_relative, source_relative in _RUNTIME_GENERATION_FIXTURE_SOURCES.items():
        target = current_tools / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / source_relative, target)

    runtime_python = current_tools / "bin/python3"
    runtime_python.parent.mkdir(parents=True, exist_ok=True)
    _write_executable(
        runtime_python,
        f'#!/bin/sh\nexec {installer.shlex_quote(str(Path(sys.executable).absolute()))} "$@"\n',
    )

    provenance = _write_test_source_provenance(current_tools)

    monkeypatch.delenv("VIBECRAFTED_SOURCE_OWNER_REPO", raising=False)
    monkeypatch.delenv("VIBECRAFTED_SOURCE_REVISION", raising=False)
    installer._write_runtime_generation_manifest(
        current_tools,
        source_root=current_tools,
        source_provenance=provenance,
        install_version=None,
    )


def test_runtime_semantic_verifier_uses_candidate_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate_python = tmp_path / "runtime/bin/python3"
    observed: list[str] = []

    def fake_run(argv, **_kwargs):
        observed.extend(str(part) for part in argv)
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    installer._run_runtime_verifier_semantic_command(
        ["product-contract.py", "--help"],
        cache=tmp_path / "cache",
        python_executable=candidate_python,
    )

    assert observed[0] == str(candidate_python)
    assert observed[1:4] == ["-I", "-S", "-B"]
    assert str(Path(sys.executable)) not in observed[:1]


def test_runtime_verifier_python_falls_back_only_when_no_interpreter_is_carried(
    tmp_path: Path,
) -> None:
    source_staged = tmp_path / "staged"
    (source_staged / "bin").mkdir(parents=True)
    assert installer._runtime_verifier_python(source_staged) == Path(sys.executable)

    carried = tmp_path / "pack"
    carried_python = carried / "bin/python3"
    carried_python.parent.mkdir(parents=True)
    carried_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    carried_python.chmod(0o755)
    assert installer._runtime_verifier_python(carried) == carried_python

    carried_python.chmod(0o644)
    with pytest.raises(OSError, match="not executable"):
        installer._runtime_verifier_python(carried)

    dangling = tmp_path / "dangling"
    (dangling / "bin").mkdir(parents=True)
    (dangling / "bin/python3").symlink_to(tmp_path / "missing-interpreter")
    with pytest.raises(OSError, match="not executable"):
        installer._runtime_verifier_python(dangling)


def test_installer_release_contract_assets_fail_closed_for_missing_or_exact_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_tools = tmp_path / "vibecrafted-current"
    _write_release_contract_runtime_manifest(current_tools, monkeypatch)
    package = current_tools / "vibecrafted-core" / "vibecrafted_core"

    assert installer._release_contract_asset_issues(current_tools) == []

    mutations = {
        "product_contract.py": b"def verify_release_output(*_args):\n    return {}\n",
        "walkaround_runner.py": b"def main():\n    return 0\n",
        "schemas/unified_product.schema.v1.json": b'{"$defs": {}}\n',
        "trust/release-policy.v1.json": None,
        "trust/vibecrafted-signing-v1.pub": None,
    }
    for relative, replacement in mutations.items():
        target = package / relative
        original = target.read_bytes()
        target.write_bytes(replacement if replacement is not None else original + b"\n")
        issues = installer._release_contract_asset_issues(current_tools)
        assert any(item.startswith(f"{relative}:corrupt:") for item in issues), relative
        target.write_bytes(original)

        target.unlink()
        issues = installer._release_contract_asset_issues(current_tools)
        assert f"{relative}:missing" in issues

        sibling = target.with_name(f"{target.name}.real")
        sibling.write_bytes(original)
        target.symlink_to(sibling.name)
        issues = installer._release_contract_asset_issues(current_tools)
        assert f"{relative}:missing" in issues
        target.unlink()
        sibling.unlink()
        target.write_bytes(original)


def test_installer_release_contract_assets_reject_missing_or_incomplete_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_tools = tmp_path / "vibecrafted-current"
    _write_release_contract_runtime_manifest(current_tools, monkeypatch)
    manifest_path = current_tools / installer._RUNTIME_GENERATION_MANIFEST
    original = manifest_path.read_bytes()

    manifest_path.unlink()
    assert (
        f"{installer._RUNTIME_GENERATION_MANIFEST}:missing"
        in installer._release_contract_asset_issues(current_tools)
    )

    manifest_path.write_text("{}\n", encoding="utf-8")
    assert any(
        item.startswith(f"{installer._RUNTIME_GENERATION_MANIFEST}:corrupt:")
        for item in installer._release_contract_asset_issues(current_tools)
    )

    manifest_path.unlink()
    manifest_sibling = current_tools / "runtime-manifest.real.json"
    manifest_sibling.write_bytes(original)
    manifest_path.symlink_to(manifest_sibling.name)
    assert (
        f"{installer._RUNTIME_GENERATION_MANIFEST}:missing"
        in installer._release_contract_asset_issues(current_tools)
    )
    manifest_path.unlink()
    manifest_sibling.unlink()

    manifest_path.write_bytes(original)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["hashes"].pop("vibecrafted-core/vibecrafted_core/product_contract.py")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert any(
        item.startswith(f"{installer._RUNTIME_GENERATION_MANIFEST}:corrupt:")
        for item in installer._release_contract_asset_issues(current_tools)
    )


def test_runtime_manifest_retains_clean_exact_git_source_carrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_tools = tmp_path / "vibecrafted-current"
    _write_release_contract_runtime_manifest(current_tools, monkeypatch)
    (current_tools / installer._RUNTIME_GENERATION_MANIFEST).unlink()
    (current_tools / "source-provenance.json").unlink()
    source = tmp_path / "clean-source"
    source.mkdir()
    (source / "README.md").write_text("clean exact Git source\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "remote",
            "add",
            "origin",
            "https://github.com/vetcoders/vibecrafted.git",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=Vibecrafted Tests",
            "-c",
            "user.email=tests@vetcoders.io",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        check=True,
    )

    provenance = installer.resolve_source_provenance(
        source,
        owner_repo=None,
        source_revision=None,
    )
    carrier = current_tools / "source-provenance.json"
    carrier.write_text(
        json.dumps(provenance, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    carrier.chmod(0o644)
    installer._write_runtime_generation_manifest(
        current_tools,
        source_root=source,
        source_provenance=provenance,
        install_version=None,
    )

    assert not (source / "source-provenance.json").exists()
    assert (current_tools / "source-provenance.json").is_file()
    manifest, error = installer._load_runtime_generation_manifest(current_tools)
    assert error is None
    assert manifest is not None
    assert manifest["owner_repo"] == "vetcoders/vibecrafted"


def test_release_contract_inventory_names_runner_schema_policy_and_key() -> None:
    assert "verify-vibecrafted-walkaround" in installer.PYTHON_ENTRYPOINT_LAUNCHERS
    assert installer.RELEASE_CONTRACT_PACKAGE_ASSETS == (
        "product_contract.py",
        "runtime_pack_contract.py",
        "walkaround_runner.py",
        "schemas/unified_product.schema.v1.json",
        "trust/release-policy.v1.json",
        "trust/vibecrafted-signing-v1.pub",
    )


def _loaded_release_contract_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_shape: str,
) -> tuple[Path, installer.InstallState, Path | None]:
    """Materialize and load the four installer-state shapes used by doctor."""
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    tools = home / ".local/share/vibecrafted/tools"
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    if state_shape == "fresh":
        store = crafted_home / "skills"
        return store, installer._load_install_state(store), None

    generation = tools / "vibecrafted-generation-test"
    store = generation / "vibecrafted-core" / "vibecrafted_core" / "skills"
    store.mkdir(parents=True)
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "vibecrafted-current").symlink_to(generation)

    if state_shape == "migrated":
        legacy_store = crafted_home / "skills"
        installer.InstallState(framework_version="legacy").save(legacy_store)
    elif state_shape == "corrupt":
        (store / installer.STATE_FILE).write_text("{", encoding="utf-8")
    elif state_shape != "lost":
        raise AssertionError(f"unknown fixture state: {state_shape}")

    return store, installer._load_install_state(store), generation


def _seed_release_contract_assets(
    generation: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    _write_release_contract_runtime_manifest(generation, monkeypatch)
    return generation / "vibecrafted-core" / "vibecrafted_core"


@pytest.mark.parametrize(
    ("flags", "source_present", "expected_issue"),
    [
        (0, True, False),
        (3, True, False),
        (1, True, True),
        (0, False, True),
    ],
)
def test_verifier_bytecode_cache_only_fails_when_it_can_escape_bound_source(
    tmp_path: Path, flags: int, source_present: bool, expected_issue: bool
) -> None:
    package = tmp_path / "vibecrafted-core/vibecrafted_core"
    cache = package / "__pycache__"
    cache.mkdir(parents=True)
    if source_present:
        (package / "product_contract.py").write_text("VALUE = 1\n", encoding="utf-8")
    pyc = cache / "product_contract.cpython-312.pyc"
    pyc.write_bytes(importlib.util.MAGIC_NUMBER + struct.pack("<I", flags) + b"payload")

    issues = installer._verifier_bytecode_shadow_issues(tmp_path)

    assert bool(issues) is expected_issue


def test_verifier_adjacent_bytecode_always_fails(tmp_path: Path) -> None:
    package = tmp_path / "vibecrafted-core/vibecrafted_core"
    package.mkdir(parents=True)
    (package / "product_contract.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "product_contract.pyc").write_bytes(b"shadow")

    issues = installer._verifier_bytecode_shadow_issues(tmp_path)

    assert issues == ["product_contract.pyc:corrupt:verifier bytecode shadow"]


@pytest.mark.parametrize("state_shape", ["fresh", "migrated", "lost", "corrupt"])
def test_installer_doctor_checks_release_assets_before_store_state_shortcuts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_shape: str,
) -> None:
    store, state, generation = _loaded_release_contract_state(
        tmp_path, monkeypatch, state_shape
    )
    if state_shape == "migrated":
        assert state.framework_version == "legacy"
    elif state_shape in {"fresh", "lost", "corrupt"}:
        assert state.framework_version == ""
    assert (generation is None) == (state_shape == "fresh")
    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}

    release_finding = indexed["release-contract-assets"]
    assert release_finding.level == "fail"
    if state_shape == "fresh":
        assert "runtime-pointer:corrupt:" in release_finding.message
        for relative in installer.RELEASE_CONTRACT_PACKAGE_ASSETS:
            assert f"{relative}:missing" not in release_finding.message
    else:
        for relative in installer.RELEASE_CONTRACT_PACKAGE_ASSETS:
            assert f"{relative}:missing" in release_finding.message


@pytest.mark.parametrize("state_shape", ["fresh", "migrated", "lost", "corrupt"])
@pytest.mark.parametrize(
    "mutation",
    [
        "product_contract.py",
        "walkaround_runner.py",
        "schemas/unified_product.schema.v1.json",
        "trust/release-policy.v1.json",
        "trust/vibecrafted-signing-v1.pub",
        "legacy-runtime-four-hash",
    ],
)
def test_installer_doctor_release_assets_fail_closed_in_each_loaded_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_shape: str,
    mutation: str,
) -> None:
    store, state, generation = _loaded_release_contract_state(
        tmp_path, monkeypatch, state_shape
    )
    if generation is None:
        tools = installer.vibecrafted_tools_home()
        generation = tools / "vibecrafted-generation-test"
        tools.mkdir(parents=True, exist_ok=True)
        (tools / "vibecrafted-current").symlink_to(generation.name)
    package = _seed_release_contract_assets(generation, monkeypatch)
    replacements = {
        "product_contract.py": b"def verify_release_output(*_args):\n    return {}\n",
        "walkaround_runner.py": b"def main():\n    return 0\n",
        "schemas/unified_product.schema.v1.json": b'{"$defs": {}}\n',
    }
    if mutation == "legacy-runtime-four-hash":
        manifest_path = generation / installer._RUNTIME_GENERATION_MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["hashes"] = {
            relative: digest
            for relative, digest in manifest["hashes"].items()
            if relative in _LEGACY_RUNTIME_GENERATION_HASH_PATHS
        }
        assert set(manifest["hashes"]) == _LEGACY_RUNTIME_GENERATION_HASH_PATHS
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        expected_issues = [f"{installer._RUNTIME_GENERATION_MANIFEST}:corrupt:"]
    else:
        path = package / mutation
        path.write_bytes(replacements.get(mutation, path.read_bytes() + b"\n"))
        expected_issues = [f"{mutation}:corrupt"]

    findings = installer.run_doctor(store, state)
    indexed = {finding.component: finding for finding in findings}
    release_finding = indexed["release-contract-assets"]
    assert release_finding.level == "fail"
    for expected_issue in expected_issues:
        assert expected_issue in release_finding.message


@pytest.mark.parametrize("pointer_shape", ["self-loop", "two-link-loop", "dangling"])
def test_installer_doctor_rejects_invalid_runtime_pointer_without_crashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pointer_shape: str,
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    tools = home / ".local/share/vibecrafted/tools"
    current = tools / "vibecrafted-current"
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    tools.mkdir(parents=True)

    if pointer_shape == "self-loop":
        current.symlink_to(current.name)
    elif pointer_shape == "two-link-loop":
        second = tools / "vibecrafted-pointer-loop"
        current.symlink_to(second.name)
        second.symlink_to(current.name)
    elif pointer_shape == "dangling":
        current.symlink_to("vibecrafted-generation-missing")
    else:
        raise AssertionError(f"unknown pointer fixture: {pointer_shape}")

    store = crafted_home / "skills"
    findings = installer.run_doctor(store, installer._load_install_state(store))
    indexed = {finding.component: finding for finding in findings}

    release_finding = indexed["release-contract-assets"]
    assert release_finding.level == "fail"
    assert "runtime-pointer:corrupt:" in release_finding.message
    assert indexed["store"].level == "fail"


def test_doctor_executes_vibecrafted_launcher_without_bash() -> None:
    installer_text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )

    launcher_smoke = installer_text.split(
        'launcher = wrapper_locations.get("vibecrafted")', 1
    )[1].split("# 6b. Dashboard smoke", 1)[0]

    assert '["bash", str(launcher)' not in launcher_smoke
    assert '["bash", str(wrapper)' not in launcher_smoke
    assert '[str(launcher), "--help"]' in launcher_smoke
    assert "[str(wrapper)]" in launcher_smoke


def test_doctor_executes_dashboard_wrapper_without_bash() -> None:
    installer_text = (REPO_ROOT / "scripts" / "vetcoders_install.py").read_text(
        encoding="utf-8"
    )

    dashboard_smoke = installer_text.split("# 6b. Dashboard smoke", 1)[1].split(
        "# 6c. vc-frame", 1
    )[0]

    assert '["bash", str(dashboard_wrapper)' not in dashboard_smoke
    assert '[str(dashboard_wrapper), "--help"]' in dashboard_smoke


def test_cleanse_state_home_agency_moves_only_executable_payloads(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    current_tools = (
        home / ".local" / "share" / "vibecrafted" / "tools" / "vibecrafted-current"
    )
    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)

    for name in ("skills", "helpers", "config", "bin", "scripts"):
        payload = crafted_home / name
        payload.mkdir(parents=True)
        (payload / "payload.txt").write_text(name, encoding="utf-8")
    tmp_dir = crafted_home / "tmp"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "marbles.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (tmp_dir / "note.txt").write_text("state", encoding="utf-8")
    (crafted_home / "artifacts").mkdir(parents=True)

    moved = installer.cleanse_state_home_agency(current_tools)

    assert moved == 6
    for name in ("skills", "helpers", "config", "bin", "scripts"):
        assert not (crafted_home / name).exists()
        assert (
            current_tools / ".legacy-state-agency" / name / "payload.txt"
        ).read_text(encoding="utf-8") == name
    assert not (tmp_dir / "marbles.sh").exists()
    assert (tmp_dir / "note.txt").is_file()
    assert (crafted_home / "artifacts").is_dir()
    assert (current_tools / ".legacy-state-agency" / "tmp" / "marbles.sh").is_file()


def test_run_doctor_ignores_ds_store_in_stale_file_check(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    skill_name = "vc-intents"
    installed_skill = store_path / skill_name
    source_skill = (
        REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills" / skill_name
    )

    installed_skill.mkdir(parents=True)
    (installed_skill / "SKILL.md").write_text(
        (source_skill / "SKILL.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (installed_skill / ".DS_Store").write_text("junk\n", encoding="utf-8")

    state = installer.InstallState(
        framework_version="1.4.1-test",
        skills=[skill_name],
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    monkeypatch.setattr(
        installer,
        "_doctor_launcher_source_root",
        lambda _store_path: REPO_ROOT,
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["stale-files"].level == "ok"


def test_run_doctor_spawn_e2e_supplies_full_meta_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    crafted_home = home / ".vibecrafted"
    runtime_tools = home / ".local" / "share" / "vibecrafted" / "tools"
    store_path = crafted_home / "skills"
    helper_dir = config_home / "vetcoders"
    source_root = runtime_tools / "vibecrafted-main"
    current_link = runtime_tools / "vibecrafted-current"
    scripts_dir = source_root / "skills" / "vc-agents" / "scripts"

    store_path.mkdir(parents=True)
    helper_dir.mkdir(parents=True)
    scripts_dir.mkdir(parents=True)
    current_link.parent.mkdir(parents=True, exist_ok=True)
    current_link.symlink_to(source_root)

    helper_file = helper_dir / "vc-skills.sh"
    helper_file.write_text(
        f"# shellcheck shell=bash\n{installer.HELPER_SHIM_MARKER}\nvc-help() {{ :; }}\ncodex-implement() {{ :; }}\ncodex-marbles() {{ :; }}\nskills-sync() {{ :; }}"
        + "\n",
        encoding="utf-8",
    )

    (scripts_dir / "common.sh").write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\nspawn_write_meta() { local meta_path="$1"; local status="$2"; printf "%s\\n" "$status" > "$meta_path"; }\nspawn_prepare_paths() { :; }\nspawn_watch_startup() { :; }\nspawn_generate_launcher() { local launcher="$1"; local _meta="$2"; local _report="$3"; local _transcript="$4"; local common="$5"; local command="$6"; cat > "$launcher" <<EOF\n#!/usr/bin/env bash\nset -euo pipefail\nsource "$common"\n$command\nEOF\n}'
        + "\n",
        encoding="utf-8",
    )

    state = installer.InstallState(
        framework_version="1.2.1",
        shell_helpers=False,
    )
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])
    _real_which = shutil.which
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: None if name == "zsh" else _real_which(name),
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["spawn-pipeline"].level == "ok"
    assert indexed["spawn-e2e"].level == "ok"


def test_cmd_doctor_fix_rc_repairs_compat_shell_lines(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    launcher_bin = home / ".local" / "bin"
    helper_dir = config_home / "vetcoders"
    compat_helper_dir = config_home / "zsh"
    zshrc = home / ".zshrc"

    store_path.mkdir(parents=True)
    launcher_bin.mkdir(parents=True)
    helper_dir.mkdir(parents=True)
    compat_helper_dir.mkdir(parents=True)

    helper_file = helper_dir / "vc-skills.sh"
    helper_file.write_text(
        f"# shellcheck shell=bash\n{installer.HELPER_SHIM_MARKER}\nvc-help() {{ :; }}"
        + "\n",
        encoding="utf-8",
    )
    (compat_helper_dir / "vc-skills.zsh").write_text(
        "# compat helper\n", encoding="utf-8"
    )
    _write_executable(
        launcher_bin / "vibecrafted",
        "#!/usr/bin/env bash\nprintf '𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. help ok\\n'\n",
    )
    zshrc.write_text(
        f"# existing user config\n{installer._old_zshrc_source_line()}\n"
        "# >>> vibecrafted >>>\n"
        'export VETCODERS_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders"\n'
        'if [ -f "$VETCODERS_CONFIG_DIR/vc-skills.sh" ]; then\n'
        f"  {installer._shell_source_line()}\n"
        "fi\n"
        "# <<< vibecrafted <<<\n"
        'export VIBECRAFTED_HOME="$HOME/.vibecrafted"\n'
        f"{installer._launcher_path_line()}\n" + "\n",
        encoding="utf-8",
    )

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    findings = installer._doctor_fix_rc_files()

    assert any(
        finding.component == "rc-fix:.zshrc" and finding.level == "ok"
        for finding in findings
    )
    repaired = zshrc.read_text(encoding="utf-8")
    assert installer._old_zshrc_source_line() not in repaired
    assert 'export VIBECRAFTED_HOME="$HOME/.vibecrafted"' not in repaired
    assert installer._shell_source_line() not in repaired
    assert "VETCODERS_CONFIG_DIR" not in repaired
    assert "# >>> vibecrafted >>>" not in repaired
    assert "# <<< vibecrafted <<<" not in repaired
    assert repaired.count(installer._launcher_path_line()) == 1
    assert "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. shell helpers" not in repaired
    assert "# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. launcher" in repaired
    assert (home / ".zshrc.vibecrafted-rc-bak").read_text(encoding="utf-8") == (
        f"# existing user config\n{installer._old_zshrc_source_line()}\n"
        "# >>> vibecrafted >>>\n"
        'export VETCODERS_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders"\n'
        'if [ -f "$VETCODERS_CONFIG_DIR/vc-skills.sh" ]; then\n'
        f"  {installer._shell_source_line()}\n"
        "fi\n"
        "# <<< vibecrafted <<<\n"
        'export VIBECRAFTED_HOME="$HOME/.vibecrafted"\n'
        f"{installer._launcher_path_line()}\n\n"
    )


def test_cmd_doctor_fix_rc_repairs_login_startup_file(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    zprofile = home / ".zprofile"
    launcher_bin.mkdir(parents=True)
    _write_executable(launcher_bin / "vibecrafted", "#!/bin/sh\nexit 0\n")
    original = (
        f"# user login config\n{installer._shell_source_line()}\nexport KEEP_ME=1\n"
    )
    zprofile.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    findings = installer._doctor_fix_rc_files()

    repaired = zprofile.read_text(encoding="utf-8")
    assert installer._shell_source_line() not in repaired
    assert "export KEEP_ME=1" in repaired
    assert repaired.count(installer._launcher_path_line()) == 1
    assert (home / ".zprofile.vibecrafted-rc-bak").read_text(
        encoding="utf-8"
    ) == original
    assert any(
        finding.component == "rc-fix:.zprofile"
        and finding.level == "ok"
        and ".zprofile.vibecrafted-rc-bak" in finding.message
        for finding in findings
    )


def test_cmd_doctor_fix_rc_preserves_unclosed_managed_block(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    zshrc = home / ".zshrc"
    launcher_bin.mkdir(parents=True)
    _write_executable(launcher_bin / "vibecrafted", "#!/bin/sh\nexit 0\n")
    original = (
        "# user config\n"
        "# >>> vibecrafted >>>\n"
        f"{installer._shell_source_line()}\n"
        "export KEEP_ME=1\n"
        "alias keep-me=true\n"
    )
    zshrc.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    findings = installer._doctor_fix_rc_files()

    assert zshrc.read_text(encoding="utf-8") == original
    assert any(
        finding.component == "rc-fix:.zshrc"
        and finding.level == "warn"
        and "unclosed" in finding.message
        for finding in findings
    )
    assert installer._clean_legacy_rc_entries(original) == (original, 0)


def test_host_shell_contract_rejects_active_helper_sourcing(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / ".zshrc").write_text(
        f"{installer._shell_source_line()}\n", encoding="utf-8"
    )

    [finding] = installer._host_shell_contract_findings()

    assert finding.level == "fail"
    assert finding.component == "host-shell"
    assert "--fix-rc" in finding.message


def test_host_shell_contract_checks_every_login_and_interactive_startup_file(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    for rcname in installer._SHELL_STARTUP_FILES:
        rcfile = home / rcname
        rcfile.write_text(f"{installer._shell_source_line()}\n", encoding="utf-8")

        [finding] = installer._host_shell_contract_findings()

        assert finding.level == "fail"
        assert rcname in finding.message
        rcfile.unlink()


def test_frontier_contract_rejects_checkout_link(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    frontier = config_home / "vetcoders" / "frontier"
    checkout = tmp_path / "checkout"
    frontier.mkdir(parents=True)
    checkout.mkdir()
    (frontier / "legacy.bak.1").symlink_to(checkout)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    [finding] = installer._managed_frontier_contract_findings()

    assert finding.level == "fail"
    assert finding.component == "frontier-links"
    assert "legacy.bak.1" in finding.message


def test_frontier_contract_accepts_installed_generation_link(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_home = home / ".config"
    runtime_home = home / ".local" / "share" / "vibecrafted"
    frontier = config_home / "vetcoders" / "frontier"
    installed = runtime_home / "tools" / "vibecrafted-current" / "config"
    frontier.mkdir(parents=True)
    installed.mkdir(parents=True)
    (installed / "starship.toml").write_text("format = ''\n", encoding="utf-8")
    (frontier / "starship.toml").symlink_to(installed / "starship.toml")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    [finding] = installer._managed_frontier_contract_findings()

    assert finding.level == "ok"
    assert finding.component == "frontier-links"


def test_public_launcher_contract_rejects_checkout_link(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    checkout_bin = tmp_path / "checkout" / "bin"
    launcher_bin.mkdir(parents=True)
    checkout_bin.mkdir(parents=True)
    (checkout_bin.parent / ".git").mkdir()
    _write_executable(checkout_bin / "vc-slack", "#!/bin/sh\nexit 0\n")
    (launcher_bin / "vc-slack").symlink_to(checkout_bin / "vc-slack")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher_bin))

    [finding] = installer._public_launcher_contract_findings()

    assert finding.level == "fail"
    assert finding.component == "public-launchers"
    assert "vc-slack" in finding.message


def test_public_launcher_contract_accepts_packaged_provider(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    provider_bin = home / ".local" / "share" / "uv" / "tools" / "provider" / "bin"
    launcher_bin.mkdir(parents=True)
    provider_bin.mkdir(parents=True)
    _write_executable(provider_bin / "vc-slack", "#!/bin/sh\nexit 0\n")
    (launcher_bin / "vc-slack").symlink_to(provider_bin / "vc-slack")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher_bin))

    [finding] = installer._public_launcher_contract_findings()

    assert finding.level == "ok"
    assert finding.component == "public-launchers"


def test_public_launcher_contract_ignores_foreign_checkout_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    # `vc-tools` belongs to vetcoders-hooks, whose own installer publishes a
    # symlink straight into its checkout. Sharing the `vc-*` prefix and the
    # launcher bin does not make it ours to police.
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    checkout_bin = tmp_path / "vetcoders-hooks" / "tui"
    launcher_bin.mkdir(parents=True)
    checkout_bin.mkdir(parents=True)
    (checkout_bin.parent / ".git").mkdir()
    _write_executable(checkout_bin / "vc-tools", "#!/bin/sh\nexit 0\n")
    (launcher_bin / "vc-tools").symlink_to(checkout_bin / "vc-tools")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher_bin))

    assert "vc-tools" not in installer._vibecrafted_owned_launcher_names()

    [finding] = installer._public_launcher_contract_findings()

    assert finding.level == "ok"
    assert finding.component == "public-launchers"
    assert "vc-tools" not in finding.message


def test_public_launcher_contract_rejects_owned_launcher_beside_foreign_one(
    tmp_path: Path, monkeypatch
) -> None:
    # Narrowing the scope must not soften the contract: an owned launcher in a
    # checkout still fails even when a foreign checkout launcher sits next to it.
    home = tmp_path / "home"
    launcher_bin = home / ".local" / "bin"
    foreign_bin = tmp_path / "vetcoders-hooks" / "tui"
    owned_bin = tmp_path / "checkout" / "bin"
    launcher_bin.mkdir(parents=True)
    foreign_bin.mkdir(parents=True)
    owned_bin.mkdir(parents=True)
    (foreign_bin.parent / ".git").mkdir()
    (owned_bin.parent / ".git").mkdir()
    _write_executable(foreign_bin / "vc-tools", "#!/bin/sh\nexit 0\n")
    _write_executable(owned_bin / "vc-ship", "#!/bin/sh\nexit 0\n")
    (launcher_bin / "vc-tools").symlink_to(foreign_bin / "vc-tools")
    (launcher_bin / "vc-ship").symlink_to(owned_bin / "vc-ship")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(launcher_bin))

    assert "vc-ship" in installer._vibecrafted_owned_launcher_names()

    [finding] = installer._public_launcher_contract_findings()

    assert finding.level == "fail"
    assert finding.component == "public-launchers"
    assert "vc-ship" in finding.message
    assert "vc-tools" not in finding.message


def test_slack_provider_contract_defers_when_provider_was_never_published(
    tmp_path: Path, monkeypatch
) -> None:
    # vc-slack-agent is an external sibling repo: a host that never published
    # the provider (CI runners, fresh installs) is legal and must DEFER with
    # a warn. Only a broken existing publication is a failure.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".local" / "bin"))

    [finding] = installer._slack_provider_contract_findings()

    assert finding.level == "warn"
    assert finding.component == "slack-provider"
    assert "not published" in finding.message
    assert "optional" in finding.message


def test_run_doctor_fail_fast_on_runtime_root_drift(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    store_path.mkdir(parents=True)

    state = installer.InstallState(framework_version="1.6.0")
    state.save(store_path)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIBECRAFTED_HOME", str(home / ".legacy-vibecrafted"))
    monkeypatch.setenv(
        "VIBECRAFTED_RUNTIME_HOME",
        str(home / ".legacy-runtime"),
    )
    monkeypatch.setenv("VIBECRAFTED_LAUNCHER_BIN", str(home / ".legacy-bin"))
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["root:store"].level == "fail"
    assert indexed["root:runtime"].level == "fail"
    assert indexed["root:launcher-bin"].level == "fail"
    assert "manual cleanup" in indexed["root:store"].message


def test_run_doctor_accepts_external_foundation_provider(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    store_path.mkdir(parents=True)

    state = installer.InstallState(framework_version="1.6.0")
    state.save(store_path)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)

    loct_foundation = installer.Foundation(
        name="loct",
        description="Loctree operator CLI short command",
        channels=["canonical"],
        packages={"canonical": "curl -fsSL https://loct.io/install.sh | sh"},
        verify_cmd="loct --version",
    )
    monkeypatch.setattr(installer, "FOUNDATIONS", [loct_foundation])
    monkeypatch.setattr(
        installer.shutil,
        "which",
        lambda name: "/usr/local/bin/loct" if name == "loct" else None,
    )

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["foundation:loct"].level == "ok"
    assert indexed["foundation-provenance:loct"].level == "ok"
    assert (
        "external developer provider accepted"
        in indexed["foundation-provenance:loct"].message
    )


def test_install_agent_commands_makes_marbles_discoverable(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    crafted_home = home / ".vibecrafted"
    store_path = crafted_home / "skills"
    store_path.mkdir(parents=True)

    for runtime in ("codex", "claude"):
        (home / f".{runtime}" / "skills").mkdir(parents=True)

    _pin_canonical_runtime_roots(monkeypatch, home, crafted_home)
    monkeypatch.setattr(installer, "FOUNDATIONS", [])

    installer.install_agent_commands(["codex", "claude"])

    codex_commands = home / ".codex" / "commands"
    claude_commands = home / ".claude" / "commands"
    assert (codex_commands / "marbles.md").is_file()
    assert (codex_commands / "codex-marbles-loop.md").is_file()
    assert (codex_commands / "cancel-codex-marbles.md").is_file()
    assert (claude_commands / "marbles.md").is_file()
    assert (claude_commands / "cancel-marbles.md").is_file()
    assert "vibecrafted-managed-agent-command" in (
        codex_commands / "marbles.md"
    ).read_text(encoding="utf-8")

    state = installer.InstallState(
        framework_version="3.1.0",
        runtimes=["codex", "claude"],
    )
    state.save(store_path)

    findings = installer.run_doctor(store_path, state)
    indexed = {finding.component: finding for finding in findings}

    assert indexed["commands:codex"].level == "ok"
    assert indexed["commands:claude"].level == "ok"


def test_pause_for_runtime_contract_failures_prompts_interactively(monkeypatch) -> None:
    class _TTY:
        def isatty(self) -> bool:
            return True

    prompts: list[str] = []
    monkeypatch.setattr(installer.sys, "stdin", _TTY())
    monkeypatch.setattr(installer.sys, "stdout", _TTY())
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": prompts.append(prompt) or "",
    )

    installer._pause_for_runtime_contract_failures(
        [installer.DoctorFinding("fail", "root:store", "drift")]
    )

    assert prompts
    assert "Press Enter" in prompts[0]


def test_describe_dumb_terminal_noise_flags_starship_and_stdout() -> None:
    detail = installer.describe_dumb_terminal_noise(
        """
       ○ ○○ ○○○ ○○○○
        """,
        "[ERROR] - (starship::print): Under a 'dumb' terminal (TERM=dumb).",
    )

    assert "starship init still runs under TERM=dumb" in detail
    assert "stdout noise:" in detail
    assert '[[ -o interactive && "${TERM:-}" != "dumb" ]]' in detail


# --- W3-A vc-frame config delivery (plan vcframe-config-delivery) ---


def _seed_complete_vibecrafted_runtime(tools: Path) -> Path:
    runtime = tools / "vibecrafted-local"
    runtime_payload = runtime / "vibecrafted-core" / "vibecrafted_core" / "runtime"
    (runtime_payload / "scripts").mkdir(parents=True)
    (runtime / "Makefile").write_text("install:\n", encoding="utf-8")
    materialize_vc_frame_config(
        vc_frame_config_source(),
        runtime_payload / "generated" / "vc-frame",
        pane_shell=resolve_pane_shell(),
        clipboard_command=resolve_clipboard_command(),
    )
    current = tools / "vibecrafted-current"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.symlink_to(runtime.name)
    return runtime


def test_vc_frame_delivery_healthy_store_view_ok(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    _seed_complete_vibecrafted_runtime(tools)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", raising=False)
    stage_vc_frame_config(
        home=home, tools_home=tools, version="doc1", prefer_repo=False
    )
    findings = _vc_frame_delivery_findings(home=home, tools_home=tools)
    view = [f.level for f in findings if f.component == "vc-frame:view"]
    assert "fail" not in view, findings


def test_vc_frame_delivery_dev_checkout_does_not_require_runtime_generation(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", "1")
    stage_vc_frame_config(
        home=home,
        tools_home=tools,
        version="dev",
        prefer_repo=True,
    )

    findings = _vc_frame_delivery_findings(home=home, tools_home=tools)

    assert not any(
        finding.level == "fail" and finding.component == "vc-frame:runtime"
        for finding in findings
    )
    assert any(
        finding.component == "vc-frame:channel"
        and "dev-checkout preferred" in finding.message
        for finding in findings
    )


def test_vc_frame_delivery_stale_file_fails_view(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    (view / "config.kdl").write_text('theme "choinka"\n', encoding="utf-8")
    (view / "layouts").mkdir()
    (view / "themes").mkdir()
    findings = _vc_frame_delivery_findings(home=home, tools_home=tools)
    fails = [
        f for f in findings if f.component == "vc-frame:view" and f.level == "fail"
    ]
    assert fails
    assert any("vibecrafted update" in f.message for f in fails)


def test_vc_frame_delivery_dangling_frontier_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    z = home / ".config" / "vetcoders" / "frontier" / "zellij"
    z.mkdir(parents=True)
    bad = z / "x.kdl"
    bad.symlink_to("./nope.kdl")
    findings = _vc_frame_delivery_findings(home=home, tools_home=tools)
    zf = [f for f in findings if f.component == "frontier:zombies"]
    assert zf and zf[0].level == "fail"


def test_vc_frame_delivery_pane_shell_warn_when_zsh_missing_and_layouts_unsubstituted(
    tmp_path, monkeypatch
):
    """zsh-less PATH + layouts still command=\"zsh\" → vc-frame:pane-shell warn."""
    home = tmp_path / "home"
    home.mkdir()
    tools = home / ".local" / "share" / "vibecrafted" / "tools"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    # Unsubstituted layouts (dev-style view pointing at raw kdl with zsh)
    view = home / ".config" / "vibecrafted" / "vc-frame"
    layouts = view / "layouts"
    layouts.mkdir(parents=True)
    (view / "config.kdl").write_text(
        'theme "monochrome"\ndefault_shell "zsh"\ncopy_command "pbcopy"\n',
        encoding="utf-8",
    )
    (view / "themes").mkdir()
    (layouts / "research.kdl").write_text(
        'pane command="zsh"\npane command="zsh"\n', encoding="utf-8"
    )
    (layouts / "operator.kdl").write_text(
        'pane command="bash" { args "-lc" "exec /bin/zsh -l" }\n',
        encoding="utf-8",
    )
    # PATH with only bash
    bash = shutil.which("bash")
    assert bash
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "bash").symlink_to(bash)
    findings = _vc_frame_delivery_findings(
        home=home, tools_home=tools, path_env=str(fake)
    )
    pane = [f for f in findings if f.component == "vc-frame:pane-shell"]
    assert pane, findings
    assert pane[0].level == "warn"
    assert "zsh" in pane[0].message
    assert "pbcopy" in pane[0].message
