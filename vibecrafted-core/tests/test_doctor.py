from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from xml.parsers.expat import ExpatError

import pytest
from vibecrafted_core import doctor


def test_installer_module_loads_source_file_without_mutating_sys_path(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "vibecrafted"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    installer = scripts / "vetcoders_install.py"
    installer.write_text("VALUE = 'loaded'\n", encoding="utf-8")

    monkeypatch.setattr(doctor, "_INSTALLER_MODULE", None)
    monkeypatch.setattr(doctor, "_repo_root_from_source", lambda: repo)
    before = list(sys.path)

    module = doctor._installer_module()

    assert module.VALUE == "loaded"
    assert sys.path == before


def test_repo_root_from_source_detects_live_checkout() -> None:
    repo_root = doctor._repo_root_from_source()

    assert repo_root is not None
    assert (repo_root / "scripts" / "vetcoders_install.py").is_file()


def test_launcher_shim_finding_flags_bash_deck(tmp_path: Path) -> None:
    deck = tmp_path / "vibecrafted"
    deck.write_text(
        "#!/usr/bin/env bash\n# \U0001d7656 command deck\nset -euo pipefail\n",
        encoding="utf-8",
    )

    findings = doctor._launcher_shim_findings(which=lambda _name: str(deck))

    assert findings, "expected a launcher finding"
    finding = findings[0]
    assert finding.level == "fail"
    assert finding.component == "launcher"
    assert "deck" in finding.message.lower()


def test_launcher_shim_finding_ok_for_uv_shim(tmp_path: Path) -> None:
    shim = tmp_path / "vibecrafted"
    shim.write_text(
        "#!/path/uv/python3\nfrom vibecrafted_core.cli import main\n",
        encoding="utf-8",
    )

    findings = doctor._launcher_shim_findings(which=lambda _name: str(shim))

    assert findings
    finding = findings[0]
    assert finding.level == "ok"
    assert finding.component == "launcher"


def test_launcher_shim_finding_ok_for_immutable_runtime_deck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(tmp_path))
    deck = (
        tmp_path
        / "tools"
        / "vibecrafted-generation-3.7.0+gabc"
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "deck"
        / "vibecrafted"
    )
    deck.parent.mkdir(parents=True)
    deck.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")

    finding = doctor._launcher_shim_findings(which=lambda _name: str(deck))[0]

    assert finding.level == "ok"
    assert finding.component == "launcher"
    assert "immutable runtime command deck" in finding.message


def _app_style_launcher(wrapper: Path, runtime_root: Path) -> Path:
    """Write the launcher shape `Vibecrafted.app` installs into `~/.local/bin`.

    Mirrors `AppDelegate.swift`: an env preamble naming the generation, then a
    single `exec` into `<runtime_root>/bin/vibecrafted`.
    """
    deck = runtime_root / "bin" / "vibecrafted"
    deck.parent.mkdir(parents=True, exist_ok=True)
    deck.write_text(
        "#!/usr/bin/env bash\n# command deck\nset -euo pipefail\n", encoding="utf-8"
    )
    deck.chmod(0o755)
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"export VIBECRAFTED_RUNTIME_ROOT={shlex.quote(str(runtime_root))}\n"
        f'exec {shlex.quote(str(deck))} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return deck


def test_launcher_shim_finding_ok_for_app_installed_release_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped macOS app is a first-class installed owner.

    `Vibecrafted.app` publishes `releases/<version>/`, not
    `tools/vibecrafted-generation-*/`, and writes a `~/.local/bin` wrapper that
    execs into it. Grading that `fail` told operators to reinstall, which
    reproduces the identical layout — the advice could never be satisfied.
    """
    runtime_home = tmp_path / "share" / "vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    runtime_root = runtime_home / "releases" / "4.1.0+g237d2814"
    wrapper = tmp_path / "bin" / "vibecrafted"
    deck = _app_style_launcher(wrapper, runtime_root)

    finding = doctor._launcher_shim_findings(which=lambda _name: str(wrapper))[0]

    assert finding.level == "ok"
    assert finding.component == "launcher"
    assert str(deck) in finding.message


def test_launcher_shim_finding_fails_when_wrapper_execs_a_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership is containment, so a wrapper cannot import a checkout by exec."""
    runtime_home = tmp_path / "share" / "vibecrafted"
    runtime_home.mkdir(parents=True)
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    checkout_root = tmp_path / "checkout"
    wrapper = tmp_path / "bin" / "vibecrafted"
    _app_style_launcher(wrapper, checkout_root)

    finding = doctor._launcher_shim_findings(which=lambda _name: str(wrapper))[0]

    assert finding.level == "fail"
    assert finding.component == "launcher"
    assert str(runtime_home) in finding.message


def test_launcher_version_warns_when_entered_generation_is_not_the_reported_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version doctor reports must be the one the PATH launcher enters.

    Two install channels can be live at once (`make install` stages
    `tools/vibecrafted-current`, the app publishes `releases/<version>`). When
    they disagree, reporting the staged stamp as `ok` hides the fact that a
    different generation is what actually runs.
    """
    import vibecrafted_core

    runtime_home = tmp_path / "share" / "vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    runtime_root = runtime_home / "releases" / "4.1.0+g237d2814"
    wrapper = tmp_path / "bin" / "vibecrafted"
    _app_style_launcher(wrapper, runtime_root)
    (runtime_root / "VERSION").write_text("4.1.0+g237d2814\n", encoding="utf-8")
    monkeypatch.setattr(vibecrafted_core, "__version__", "4.1.0+ga7f262d9")

    findings = doctor._launcher_shim_findings(which=lambda _name: str(wrapper))
    version_findings = [f for f in findings if f.component == "version"]

    assert version_findings, "expected a version finding"
    finding = version_findings[0]
    assert finding.level == "warn"
    assert "4.1.0+g237d2814" in finding.message
    assert "4.1.0+ga7f262d9" in finding.message


def test_launcher_version_stays_ok_when_the_entered_generation_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One channel live, versions agreeing — the cross-check must stay quiet."""
    import vibecrafted_core

    runtime_home = tmp_path / "share" / "vibecrafted"
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    runtime_root = runtime_home / "releases" / "4.1.0+g237d2814"
    wrapper = tmp_path / "bin" / "vibecrafted"
    _app_style_launcher(wrapper, runtime_root)
    (runtime_root / "VERSION").write_text("4.1.0+g237d2814\n", encoding="utf-8")
    monkeypatch.setattr(vibecrafted_core, "__version__", "4.1.0+g237d2814")

    version_findings = [
        f
        for f in doctor._launcher_shim_findings(which=lambda _name: str(wrapper))
        if f.component == "version"
    ]

    assert version_findings
    assert version_findings[0].level == "ok"


def test_launcher_shim_finding_fails_when_exec_target_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrapper's `exec` line is a claim; only the filesystem may confirm it."""
    runtime_home = tmp_path / "share" / "vibecrafted"
    runtime_home.mkdir(parents=True)
    monkeypatch.setenv("VIBECRAFTED_RUNTIME_HOME", str(runtime_home))
    wrapper = tmp_path / "bin" / "vibecrafted"
    wrapper.parent.mkdir(parents=True)
    ghost = runtime_home / "releases" / "9.9.9+gdeadbee" / "bin" / "vibecrafted"
    wrapper.write_text(
        "#!/bin/bash\nset -euo pipefail\n" + f'exec {shlex.quote(str(ghost))} "$@"\n',
        encoding="utf-8",
    )

    finding = doctor._launcher_shim_findings(which=lambda _name: str(wrapper))[0]

    assert finding.level == "fail"
    assert finding.component == "launcher"


def test_vc_frame_launcher_finding_flags_raw_binary(tmp_path: Path) -> None:
    binary = tmp_path / "vc-frame"
    binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 32)

    finding = doctor._vc_frame_launcher_findings(which=lambda _name: str(binary))[0]

    assert finding.level == "fail"
    assert finding.component == "vc-frame:path"
    assert "raw binary" in finding.message


def test_vc_frame_launcher_finding_ok_for_pinned_wrapper(tmp_path: Path) -> None:
    wrapper = tmp_path / "vc-frame"
    wrapper.write_text(
        "#!/usr/bin/env bash\npin_darwin_socket_dir() { :; }\n",
        encoding="utf-8",
    )

    finding = doctor._vc_frame_launcher_findings(which=lambda _name: str(wrapper))[0]

    assert finding.level == "ok"
    assert finding.component == "vc-frame:path"
    assert "product wrapper" in finding.message


def _stamped_uv_shim(tmp_path: Path) -> Path:
    """A healthy PATH winner: the uv-tool python entrypoint."""
    shim = tmp_path / "bin" / "vibecrafted"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text(
        "#!/path/uv/python3\nfrom vibecrafted_core.cli import main\n",
        encoding="utf-8",
    )
    return shim


def _living_checkout(tmp_path: Path, version: str = "3.7.1") -> Path:
    """Monorepo checkout layout carrying `.git` and a bare (unstamped) VERSION."""
    root = tmp_path / "checkout"
    package_dir = root / "vibecrafted-core" / "vibecrafted_core"
    package_dir.mkdir(parents=True)
    (package_dir / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "vetcoders_install.py").write_text("", encoding="utf-8")
    return package_dir


def _pin_loaded_package(
    monkeypatch, package_dir: Path, staged: str, tools_home: Path
) -> None:
    """Point the doctor at a chosen loaded package tree and staged stamp."""
    import vibecrafted_core
    from vibecrafted_core import runtime_paths

    monkeypatch.setattr(
        vibecrafted_core, "__file__", str(package_dir / "__init__.py"), raising=False
    )
    monkeypatch.setattr(vibecrafted_core, "__version__", staged, raising=False)
    monkeypatch.setattr(runtime_paths, "read_staged_tools_version", lambda: staged)
    monkeypatch.setattr(runtime_paths, "vibecrafted_tools_home", lambda: tools_home)


def test_launcher_shim_finding_warns_when_cwd_loads_the_living_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """cwd inside the checkout must not accuse a healthy install of shadowing."""
    package_dir = _living_checkout(tmp_path)
    _pin_loaded_package(
        monkeypatch,
        package_dir,
        staged="3.7.1+g1519cf19",
        tools_home=tmp_path / "tools",
    )
    shim = _stamped_uv_shim(tmp_path)

    findings = doctor._launcher_shim_findings(which=lambda _name: str(shim))

    launcher = [f for f in findings if f.component == "launcher"]
    assert [f.level for f in launcher] == ["ok", "warn"]
    message = launcher[-1].message
    assert str(package_dir.parent.parent) in message
    assert "re-run doctor from outside the checkout" in message.lower()
    assert "pip uninstall" not in message


def test_launcher_shim_finding_fails_for_editable_outside_any_checkout(
    tmp_path: Path, monkeypatch
) -> None:
    """A real editable shadow (no checkout, outside tools) still fails hard."""
    package_dir = tmp_path / "site-packages" / "vibecrafted_core"
    package_dir.mkdir(parents=True)
    (package_dir / "VERSION").write_text("3.7.1\n", encoding="utf-8")
    _pin_loaded_package(
        monkeypatch,
        package_dir,
        staged="3.7.1+g1519cf19",
        tools_home=tmp_path / "tools",
    )
    shim = _stamped_uv_shim(tmp_path)

    findings = doctor._launcher_shim_findings(which=lambda _name: str(shim))

    launcher = [f for f in findings if f.component == "launcher"]
    assert [f.level for f in launcher] == ["ok", "fail"]
    message = launcher[-1].message
    assert "loaded package tree is unstamped" in message
    assert "python3 -m pip uninstall vibecrafted" in message


def test_launcher_shim_finding_warns_when_absent() -> None:
    findings = doctor._launcher_shim_findings(which=lambda _name: None)

    assert findings
    assert findings[0].level == "warn"
    assert findings[0].component == "launcher"


def test_codex_mcp_config_rejects_streamable_http_on_sse_messages_endpoint(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.memex]\ntransport = "streamable_http"\n'
        'url = "http://100.73.193.98:8997/messages"\n',
        encoding="utf-8",
    )

    findings = doctor._codex_mcp_config_findings(config)

    assert len(findings) == 1
    assert findings[0].level == "fail"
    assert findings[0].component == "codex:mcp-config"
    assert "Disable this alias" in findings[0].message


def test_codex_mcp_config_keeps_stdio_and_real_mcp_endpoint_green(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.rust_memex]\ncommand = "rust-memex"\n'
        '[mcp_servers.remote]\ntransport = "streamable_http"\n'
        'url = "https://example.test/mcp"\n',
        encoding="utf-8",
    )

    findings = doctor._codex_mcp_config_findings(config)

    assert findings == [
        doctor._Finding(
            "ok", "codex:mcp-config", "no obvious HTTP/SSE transport mismatch"
        )
    ]


def test_server_supervision_finding_proves_current_managed_pair() -> None:
    status = SimpleNamespace(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=4242,
    )

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/usr/local/bin/vibecrafted",
        config_factory=lambda **kwargs: kwargs,
        status_reader=lambda _config: status,
    )

    assert findings == [
        doctor._Finding(
            "ok",
            "server-supervisor",
            "verified LaunchAgent-managed supervisor and healthy server/guardian "
            "pair (pid=4242, current build)",
        )
    ]


def test_server_supervision_uses_service_launcher_not_public_deck(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service_launcher = tmp_path / "uv-tool" / "vibecrafted"
    service_launcher.parent.mkdir(parents=True)
    service_launcher.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    captured: dict[str, Path] = {}
    status = SimpleNamespace(
        installed=True,
        loaded=True,
        supervisor_live=True,
        supervisor_verified=True,
        supervisor_service_managed=True,
        build_current=True,
        pair_healthy=True,
        supervisor_pid=4242,
    )

    def config_factory(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(doctor, "_uv_tool_shim", lambda: service_launcher)

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/runtime/generation/deck/vibecrafted",
        config_factory=config_factory,
        status_reader=lambda _config: status,
    )

    assert findings[0].level == "ok"
    assert captured["launcher"] == service_launcher


def test_server_supervision_finding_fails_closed_for_stale_pair() -> None:
    status = SimpleNamespace(
        installed=True,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/usr/local/bin/vibecrafted",
        config_factory=lambda **kwargs: kwargs,
        status_reader=lambda _config: status,
    )

    assert findings[0].level == "fail"
    assert findings[0].component == "server-supervisor"
    assert "loaded" in findings[0].message
    assert "supervisor_pid" in findings[0].message
    assert "pair_healthy" in findings[0].message


def test_server_supervision_finding_fails_when_probe_raises() -> None:
    def broken_status(_config) -> None:
        raise RuntimeError("stale pidfile")

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/usr/local/bin/vibecrafted",
        config_factory=lambda **kwargs: kwargs,
        status_reader=broken_status,
    )

    assert findings[0].level == "fail"
    assert findings[0].component == "server-supervisor"
    assert "stale pidfile" in findings[0].message


def test_server_supervision_finding_fails_when_plist_is_truncated() -> None:
    def truncated_plist(_config) -> None:
        raise ExpatError("unclosed token")

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/usr/local/bin/vibecrafted",
        config_factory=lambda **kwargs: kwargs,
        status_reader=truncated_plist,
    )

    assert findings[0].level == "fail"
    assert findings[0].component == "server-supervisor"
    assert "unclosed token" in findings[0].message


def test_server_supervision_finding_is_not_applicable_off_macos() -> None:
    findings = doctor._server_supervision_findings(
        platform="linux",
        which=lambda _name: None,
    )

    assert findings[0].level == "ok"
    assert findings[0].component == "server-supervisor"
    assert "not applicable" in findings[0].message


def test_doctor_run_includes_server_supervision_finding(monkeypatch) -> None:
    expected = doctor._Finding("fail", "server-supervisor", "not supervised")

    def missing_installer() -> None:
        raise ModuleNotFoundError

    monkeypatch.setattr(doctor, "_installer_module", missing_installer)
    monkeypatch.setattr(doctor, "_packaged_asset_findings", list)
    monkeypatch.setattr(doctor, "_launcher_shim_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_launcher_findings", list)
    monkeypatch.setattr(doctor, "_codex_mcp_config_findings", list)
    monkeypatch.setattr(doctor, "_server_supervision_findings", lambda: [expected])
    monkeypatch.setattr(doctor, "_vc_frame_delivery_findings", list)
    monkeypatch.setattr(doctor, "_vc_frame_truth_drift_findings", list)

    assert doctor.doctor_run() == [expected]


def test_packaged_asset_findings_require_release_contract_resources(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = tmp_path / "runtime/scripts/await.sh"
    skill = tmp_path / "skills/vc-justdo/SKILL.md"
    deck = tmp_path / "deck/vibecrafted"
    release_assets = (
        tmp_path / "product_contract.py",
        tmp_path / "walkaround_runner.py",
        tmp_path / "schemas/unified_product.schema.v1.json",
        tmp_path / "trust/release-policy.v1.json",
        tmp_path / "trust/vibecrafted-signing-v1.pub",
    )
    for path in (runtime, skill, deck, *release_assets):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "runtime_path", lambda: tmp_path / "runtime")
    monkeypatch.setattr(doctor, "skills_path", lambda: tmp_path / "skills")
    monkeypatch.setattr(doctor, "deck_path", lambda: deck)
    monkeypatch.setattr(doctor, "release_contract_paths", lambda: release_assets)

    findings = doctor._packaged_asset_findings()

    release_findings = [
        finding for finding in findings if finding.component == "release-contract"
    ]
    assert len(release_findings) == 5
    assert all(finding.level == "ok" for finding in release_findings)

    (tmp_path / "trust/release-policy.v1.json").unlink()
    findings = doctor._packaged_asset_findings()
    assert any(
        finding.level == "fail" and "release-policy.v1.json" in finding.message
        for finding in findings
    )


def _seed_truth(root: Path, content: str = "layout ok\n") -> None:
    (root / "layouts").mkdir(parents=True)
    (root / "config.kdl").write_text(content, encoding="utf-8")
    (root / "layouts" / "operator.kdl").write_text(content, encoding="utf-8")


def _truth_sandbox(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    """Tools home with one published generation behind vibecrafted-current."""
    from vibecrafted_core import frontier_assets

    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    def no_checkout() -> Path:
        raise FileNotFoundError

    monkeypatch.setattr(frontier_assets, "vc_frame_config_source", no_checkout)
    tools = tmp_path / "tools"
    generation = tools / "vibecrafted-generation-test"
    package = generation / "vibecrafted-core" / "vibecrafted_core"
    _seed_truth(package / "config" / "vc-frame")
    _seed_truth(package / "runtime" / "generated" / "vc-frame")
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "vibecrafted-current").symlink_to(generation)
    home = tmp_path / "home"
    home.mkdir()
    return tools, generation, home


def test_truth_drift_ok_when_generation_agrees(tmp_path: Path, monkeypatch) -> None:
    tools, _, home = _truth_sandbox(tmp_path, monkeypatch)

    findings = doctor._vc_frame_truth_drift_findings(home=home, tools_home=tools)

    assert [finding.level for finding in findings] == ["ok", "ok"]
    assert all(finding.component == "vc-frame:truth" for finding in findings)


def test_delivery_reads_package_owned_runtime_generation(
    tmp_path: Path, monkeypatch
) -> None:
    tools, generation, home = _truth_sandbox(tmp_path, monkeypatch)
    generated = (
        generation
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "generated"
        / "vc-frame"
    )
    (generated / "themes").mkdir()
    (generated / "vc-composer.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("VIBECRAFTED_PREFER_REPO_VC_FRAME", "0")

    findings = doctor._vc_frame_delivery_findings(home=home, tools_home=tools)

    runtime = [
        finding for finding in findings if finding.component == "vc-frame:runtime"
    ]
    assert len(runtime) == 1
    assert runtime[0].level == "ok"
    assert "vibecrafted-core/vibecrafted_core/runtime/generated/vc-frame" in (
        runtime[0].message
    )


def test_delivery_accepts_exact_physical_runtime_pack_config(
    tmp_path: Path, monkeypatch
) -> None:
    tools, generation, home = _truth_sandbox(tmp_path, monkeypatch)
    generated = (
        generation
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "generated"
        / "vc-frame"
    )
    (generated / "themes").mkdir()
    for name in doctor.OPERATOR_SCRIPT_NAMES:
        (generated / name).write_text(f"#!/bin/sh\n# {name}\n", encoding="utf-8")
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.parent.mkdir(parents=True)
    shutil.copytree(generated, view)

    findings = doctor._vc_frame_delivery_findings(home=home, tools_home=tools)

    relevant = [
        finding
        for finding in findings
        if finding.component == "vc-frame:view"
        or finding.component == "vc-frame:operator-scripts:view"
    ]
    assert relevant
    assert all(finding.level == "ok" for finding in relevant)
    assert any("runtime-copy" in finding.message for finding in relevant)


def test_truth_drift_fails_when_generation_disagrees_with_itself(
    tmp_path: Path, monkeypatch
) -> None:
    tools, generation, home = _truth_sandbox(tmp_path, monkeypatch)
    drifted = (
        generation
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "generated"
        / "vc-frame"
        / "config.kdl"
    )
    drifted.write_text("layout drifted\n", encoding="utf-8")

    findings = doctor._vc_frame_truth_drift_findings(home=home, tools_home=tools)

    split = [finding for finding in findings if finding.level == "fail"]
    assert len(split) == 1
    assert "disagrees with itself" in split[0].message
    assert "config.kdl" in split[0].message


def test_truth_drift_warns_when_dev_checkout_runs_ahead(
    tmp_path: Path, monkeypatch
) -> None:
    from vibecrafted_core import frontier_assets

    tools, _, home = _truth_sandbox(tmp_path, monkeypatch)
    checkout = tmp_path / "repo" / "config" / "vc-frame"
    _seed_truth(checkout, content="layout ahead\n")
    monkeypatch.setattr(frontier_assets, "vc_frame_config_source", lambda: checkout)

    findings = doctor._vc_frame_truth_drift_findings(home=home, tools_home=tools)

    ahead = [finding for finding in findings if finding.level == "warn"]
    assert len(ahead) == 1
    assert "dev checkout differs from published store" in ahead[0].message


def test_truth_drift_fails_on_projection_into_parked_generation(
    tmp_path: Path, monkeypatch
) -> None:
    tools, _, home = _truth_sandbox(tmp_path, monkeypatch)
    parked = tools / "vibecrafted-generation-parked"
    parked_generated = (
        parked
        / "vibecrafted-core"
        / "vibecrafted_core"
        / "runtime"
        / "generated"
        / "vc-frame"
    )
    _seed_truth(parked_generated)
    view = home / ".config" / "vibecrafted" / "vc-frame"
    view.mkdir(parents=True)
    (view / "config.kdl").symlink_to(parked_generated / "config.kdl")

    findings = doctor._vc_frame_truth_drift_findings(home=home, tools_home=tools)

    stale = [finding for finding in findings if finding.level == "fail"]
    assert len(stale) == 1
    assert "parked generation" in stale[0].message


def test_doctor_summary_counts_findings() -> None:
    payload = doctor.doctor_summary(
        [
            SimpleNamespace(level="ok", component="a", message="fine"),
            SimpleNamespace(level="warn", component="b", message="careful"),
            SimpleNamespace(level="fail", component="c", message="broken"),
        ]
    )

    assert payload["ok"] == 1
    assert payload["warnings"] == 1
    assert payload["failures"] == 1
    assert payload["healthy"] is False
    assert payload["authority"]["available"] is True
    assert payload["authority"]["healthy"] is False
    assert payload["authority"]["ok_count"] == 1
    assert payload["authority"]["failure_count"] == 1


def test_server_supervision_finding_is_optional_when_never_installed() -> None:
    status = SimpleNamespace(
        installed=False,
        loaded=False,
        supervisor_live=False,
        supervisor_verified=False,
        supervisor_service_managed=False,
        build_current=False,
        pair_healthy=False,
        supervisor_pid=None,
    )

    findings = doctor._server_supervision_findings(
        platform="darwin",
        which=lambda _name: "/usr/local/bin/vibecrafted",
        config_factory=lambda **kwargs: kwargs,
        status_reader=lambda _config: status,
    )

    assert findings[0].level == "warn"
    assert findings[0].component == "server-supervisor"
    assert "optional" in findings[0].message
    assert "vibecrafted server service install" in findings[0].message
