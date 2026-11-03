"""Public-surface floor: the drift the release pipeline used to let through.

Fixtures are shaped like the 2026-11-03 audit (Vibecrafted, Loctree, AICX,
Pensieve). Live HTTP is not used here; ``vc-dou --floor`` against the catalog
is the reproducible network run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecrafted_core.public_surface import (
    FetchResult,
    FloorConfigError,
    catalog_dir,
    load_product,
    normalize_version,
    render_report,
    run_floor,
)
from vibecrafted_core.wrappers import dou_main, hydrate_main, release_main

REPO_ROOT = Path(__file__).resolve().parents[2]


class MapFetch:
    def __init__(self, routes: list[tuple[str, FetchResult]]) -> None:
        self.routes = routes
        self.urls: list[str] = []

    def __call__(self, url: str, headers: object = None) -> FetchResult:
        del headers
        self.urls.append(url)
        matches = [(needle, result) for needle, result in self.routes if needle in url]
        if not matches:
            return FetchResult(0, "", f"no fixture for {url}")
        _needle, result = max(matches, key=lambda item: len(item[0]))
        return result


def _ok(body: str, status: int = 200) -> FetchResult:
    return FetchResult(status, body, "" if status == 200 else f"HTTP {status}")


def _github(tag: str) -> FetchResult:
    return _ok(json.dumps({"tag_name": tag}))


def _tags(*names: str) -> FetchResult:
    return _ok(json.dumps([{"name": name} for name in names]))


def _releases(*names: str) -> FetchResult:
    return _ok(json.dumps([{"tag_name": name} for name in names]))


def test_normalize_version_strips_prefix_and_build_metadata() -> None:
    assert normalize_version("v0.15.1") == "0.15.1"
    assert normalize_version("0.6.0+44f395c2") == "0.6.0"
    assert normalize_version("1.2.3-rc.1") == "1.2.3-rc.1"
    assert normalize_version(" 4.4.0\n") == "4.4.0"


def test_catalog_loads_every_shipped_product() -> None:
    directory = catalog_dir(REPO_ROOT)
    names = sorted(path.stem for path in directory.glob("*.toml"))
    assert names == ["aicx", "loctree", "pensieve", "screenscribe", "vibecrafted"]
    for name in names:
        loaded = load_product(directory / f"{name}.toml")
        assert loaded["product"] == name
        assert loaded["surface"]


def test_unknown_kind_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "nope.toml"
    path.write_text(
        'schema = 1\nproduct = "nope"\nexpect = "agree"\n'
        '[[surface]]\nid = "x"\nkind = "ftp"\n',
        encoding="utf-8",
    )
    with pytest.raises(FloorConfigError, match="unknown kind"):
        load_product(path)


def _vibecrafted_world() -> MapFetch:
    page = '<script type="application/ld+json">{"softwareVersion":"3.4.0"}</script>'
    page += "Co-build with VetCoders"
    return MapFetch(
        [
            ("channel/main.json", _ok(json.dumps({"version": "4.5.1"}))),
            ("/releases/latest", _github("v3.5.0")),
            ("/tags?", _tags("v4.3.3", "v4.3.1", "v3.5.0")),
            ("/releases?", _releases("v3.5.0")),
            ("vibecrafted.io/en", _ok(page)),
            ("vetcoders.io", _ok("<title>VetCoders · Code Clinic</title>")),
            ("libraxis.com", _ok("footer Vetcoders")),
        ]
    )


def _write_vibecrafted_checkout(root: Path, *, plugin: str = "4.4.0") -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text("4.4.0\n", encoding="utf-8")
    (root / "README.md").write_text(
        "# Vibecrafted\n\n"
        '<img alt="Version 4.4.0" src="https://img.shields.io/badge/version-4.4.0-informational.svg">\n\n'
        "## Install\n\n"
        "The latest public release is `v3.5.0`.\n\n"
        "```bash\n"
        "curl -fsSL https://vibecrafted.io/install.sh | bash\n"
        "```\n",
        encoding="utf-8",
    )
    (root / "plugin.json").write_text(json.dumps({"version": plugin}), encoding="utf-8")
    (root / "vibecrafted-core" / "pyproject.toml").parent.mkdir()
    (root / "vibecrafted-core" / "pyproject.toml").write_text(
        '[project]\nname = "vibecrafted"\nversion = "4.4.0"\n',
        encoding="utf-8",
    )


def test_dou_flags_the_vibecrafted_world(tmp_path: Path) -> None:
    _write_vibecrafted_checkout(tmp_path)
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    config["version_bump"] = False
    fetch = _vibecrafted_world()
    report = run_floor("dou", config, repo=tmp_path, fetch=fetch)

    assert report.failed
    assert report.reference == "4.4.0"
    assert report.install_cta == "no"
    levels = {item.surface: item.level for item in report.findings}
    assert levels["channel"] == "fail"
    assert levels["github_release"] == "fail"
    assert levels["site_version"] == "fail"
    assert levels["orphan_tags"] == "fail"
    assert levels["site_brand"] == "warn"
    assert levels["vetcoders_brand"] == "warn"
    assert levels["libraxis_brand"] == "ok"
    assert levels["readme"] == "ok"
    assert levels["readme_prose"] == "warn"
    prose = next(item for item in report.findings if item.surface == "readme_prose")
    assert "3.5.0" in prose.observed
    text = render_report(report)
    assert "floor: FAIL" in text
    assert "install_cta: no" in text
    assert fetch.urls


def test_release_treats_a_missing_github_release_as_unfinished_not_as_done(
    tmp_path: Path,
) -> None:
    _write_vibecrafted_checkout(tmp_path)
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    config["version_bump"] = False
    config["surface"] = [
        item
        for item in config["surface"]
        if item["id"] in {"github_release", "orphan_tags", "channel"}
    ]
    fetch = MapFetch(
        [
            ("channel/main.json", _ok(json.dumps({"version": "4.4.0"}))),
            ("/releases/latest", _github("v4.4.0")),
            ("/tags?", _tags("v4.4.0", "v4.3.3")),
            ("/releases?", _releases("v4.4.0")),
        ]
    )
    preflight = run_floor("release", config, repo=tmp_path, fetch=fetch)
    assert not preflight.failed
    orphan = next(item for item in preflight.findings if item.surface == "orphan_tags")
    assert orphan.level == "warn"
    assert "v4.3.3" in orphan.message

    behind = MapFetch(
        [
            ("channel/main.json", _ok(json.dumps({"version": "4.4.0"}))),
            ("/releases/latest", _github("v3.5.0")),
            ("/tags?", _tags("v4.4.0")),
            ("/releases?", _releases("v3.5.0")),
        ]
    )
    warned = run_floor("release", config, repo=tmp_path, fetch=behind)
    github = next(item for item in warned.findings if item.surface == "github_release")
    assert github.level == "warn"
    assert "does not finish" in github.message
    required = run_floor(
        "release",
        config,
        repo=tmp_path,
        fetch=behind,
        require_release=True,
    )
    github_required = next(
        item for item in required.findings if item.surface == "github_release"
    )
    assert github_required.level == "fail"
    assert required.failed


def test_acknowledge_downgrades_a_fail_without_hiding_it(tmp_path: Path) -> None:
    _write_vibecrafted_checkout(tmp_path)
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    config["version_bump"] = False
    report = run_floor(
        "dou",
        config,
        repo=tmp_path,
        fetch=_vibecrafted_world(),
        acknowledge=["channel", "github_release", "site_version", "orphan_tags"],
    )
    assert not report.failed
    channel = next(item for item in report.findings if item.surface == "channel")
    assert channel.level == "warn"
    assert channel.message.startswith("acknowledged:")
    assert report.install_cta == "no"


def test_hydrate_fails_closed_on_repo_drift_and_does_not_fetch(tmp_path: Path) -> None:
    _write_vibecrafted_checkout(tmp_path, plugin="9.9.9")
    readme = tmp_path / "README.md"
    readme.write_text(
        "# App\n\n## Install\n\n"
        "```bash\ncurl -fsSL https://example.com/v1.0.0/install.sh | bash\n```\n\n"
        "Built by VetCoders.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "INSTALL.md").write_text("hello\n", encoding="utf-8")
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    config["version_bump"] = False
    config["brand"] = {
        "canonical": "Vetcoders",
        "forbidden": ["VetCoders"],
        "repo_globs": ["README.md", "docs/INSTALL.md"],
    }
    fetch = MapFetch([("http", _ok("{}"))])
    report = run_floor("hydrate", config, repo=tmp_path, fetch=fetch)

    assert report.failed
    assert fetch.urls == []
    levels = {item.surface: item.level for item in report.findings}
    assert levels["plugin"] == "fail"
    assert levels["readme_pin"] == "fail"
    assert levels["brand_repo"] == "fail"
    assert report.install_cta == "no"


def test_hydrate_passes_a_checkout_whose_public_files_agree(tmp_path: Path) -> None:
    _write_vibecrafted_checkout(tmp_path)
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    config["version_bump"] = False
    config["brand"] = {
        "canonical": "Vetcoders",
        "forbidden": ["VetCoders"],
        "repo_globs": ["README.md"],
    }
    report = run_floor("hydrate", config, repo=tmp_path, fetch=MapFetch([]))
    assert not report.failed
    assert any(
        item.surface == "readme_prose" and item.level == "warn"
        for item in report.findings
    )


def test_loctree_shaped_world_blocks_the_install_cta() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "loctree.toml")
    fetch = MapFetch(
        [
            (
                "%40loctree%2Floctree",
                _ok(json.dumps({"dist-tags": {"latest": "0.15.1"}})),
            ),
            ("%40loctree%2Floct", _ok(json.dumps({"dist-tags": {"latest": "0.15.1"}}))),
            (
                "/crates/loctree-mcp",
                _ok(json.dumps({"crate": {"max_stable_version": "0.15.1"}})),
            ),
            (
                "/crates/loctree-ast",
                _ok(json.dumps({"crate": {"max_stable_version": "0.15.1"}})),
            ),
            (
                "/crates/loctree",
                _ok(json.dumps({"crate": {"max_stable_version": "0.15.1"}})),
            ),
            ("releases/index.json", _ok(json.dumps({"current": "0.15.1"}))),
            ("repos/Loctree/loctree/releases/latest", _github("v0.14.5")),
            ("repos/Loctree/loct/releases/latest", _github("v0.14.5")),
            ("repos/Loctree/loctree-mcp/releases/latest", _github("v0.14.5")),
            ("repos/Loctree/loctree/tags", _tags("v0.15.1", "v0.15.0", "v0.14.5")),
            ("repos/Loctree/loctree/releases?", _releases("v0.14.5")),
            ("Formula/loct.rb", _ok('version "0.14.5"\n')),
            ("Formula/loct-mcp.rb", _ok('version "0.14.5"\n')),
            ("loctree.com/", _ok('{"softwareVersion": "v0.15.1"} VetCoders')),
            (
                "Loctree/loctree/main/README.md",
                _ok("# Loctree Engine 0.15.0\n\n## Build\n\ncargo check\n"),
            ),
        ]
    )
    report = run_floor("dou", config, repo=REPO_ROOT, fetch=fetch)
    assert report.failed
    assert report.install_cta == "no"
    by_id = {item.surface: item for item in report.findings}
    assert by_id["npm_loctree"].level == "ok"
    assert by_id["npm_loctree"].observed == "0.15.1"
    assert by_id["installer"].level == "ok"
    assert by_id["github_loctree"].observed == "0.14.5"
    assert by_id["homebrew_cli"].observed == "0.14.5"
    assert by_id["readme"].level == "fail"
    assert "no Install section" in by_id["readme"].message
    assert by_id["readme_version"].observed == "0.15.0"
    assert by_id["site_brand"].level == "warn"
    assert by_id["consensus"].level == "fail"
    assert "0.14.5" in by_id["consensus"].message
    assert "0.15.1" in by_id["consensus"].message
    # The Vibecrafted checkout must not be treated as Loctree's repo.
    assert "version_bump" not in by_id


def test_aicx_shaped_pins_and_registries_disagree() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "aicx.toml")
    readme = (
        "# aicx\n\n## Installation\n\n"
        "```bash\n"
        "curl -fsSLO https://raw.githubusercontent.com/Loctree/aicx/v0.12.0/install.sh\n"
        "AICX_RELEASE_TAG=v0.14.0 bash install.sh\n"
        "```\n"
    )
    fetch = MapFetch(
        [
            (
                "/crates/aicx",
                _ok(json.dumps({"crate": {"max_stable_version": "0.10.0"}})),
            ),
            ("/releases/latest", _github("v0.14.0")),
            ("/tags?", _tags("v0.14.0")),
            ("/releases?", _releases("v0.14.0")),
            ("Cargo.toml", _ok('[package]\nname = "aicx"\nversion = "0.15.0"\n')),
            ("README.md", _ok(readme)),
        ]
    )
    report = run_floor("dou", config, repo=REPO_ROOT, fetch=fetch)
    assert report.failed
    assert report.install_cta == "no"
    consensus = next(item for item in report.findings if item.surface == "consensus")
    for version in ("0.10.0", "0.14.0", "0.15.0", "0.12.0"):
        assert version in consensus.message
    assert not any("npm" in item.surface for item in report.findings)


def test_pensieve_shaped_site_lags_the_release() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "pensieve.toml")
    fetch = MapFetch(
        [
            ("/releases/latest", _github("0.6.0+44f395c2")),
            ("pensieve.vetcoders.io", _ok('{"softwareVersion": "0.5.0"}')),
        ]
    )
    report = run_floor("dou", config, repo=REPO_ROOT, fetch=fetch)
    assert report.failed
    site = next(item for item in report.findings if item.surface == "site_version")
    assert site.observed == "0.5.0"
    github = next(item for item in report.findings if item.surface == "github_release")
    assert github.observed == "0.6.0"


def test_unproven_registry_is_not_a_green_pass() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "screenscribe.toml")
    fetch = MapFetch(
        [
            ("pypi.org", FetchResult(0, "", "URLError: offline")),
            ("/releases/latest", _github("v0.1.21")),
        ]
    )
    audited = run_floor("dou", config, fetch=fetch)
    assert not audited.failed
    pypi = next(item for item in audited.findings if item.surface == "pypi")
    assert pypi.level == "warn"
    assert "unproven" in pypi.message
    assert audited.install_cta == "no"
    released = run_floor("release", config, fetch=fetch)
    assert released.failed


def test_screenscribe_shaped_agreement_allows_the_install_cta() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "screenscribe.toml")
    fetch = MapFetch(
        [
            ("pypi.org", _ok(json.dumps({"info": {"version": "0.1.21"}}))),
            ("/releases/latest", _github("v0.1.21")),
        ]
    )
    report = run_floor("dou", config, fetch=fetch)
    assert not report.failed
    assert report.install_cta == "yes"
    assert report.to_json()["floor"] == "PASS"


def test_launcher_floor_runs_without_dispatching_an_agent(tmp_path: Path) -> None:
    readme = "## Install\n\n```bash\ncargo install demo\n```\n"
    (tmp_path / "README.md").write_text(f"# Demo\n\n{readme}", encoding="utf-8")
    config = tmp_path / "demo.toml"
    config.write_text(
        'schema = 1\nproduct = "demo"\nexpect = "literal:1.2.3"\n'
        'local_root_file = "README.md"\n[readme]\npath = "README.md"\n'
        'require_install = true\npin_commands = "fail"\npin_prose = "ignore"\n',
        encoding="utf-8",
    )
    args = ["--floor", "--config", str(config), "--repo", str(tmp_path), "--json"]
    assert dou_main(args) == 0
    assert hydrate_main(args) == 0
    assert release_main(args) == 0


def test_hydrate_of_this_checkout_matches_version_and_brand() -> None:
    config = load_product(catalog_dir(REPO_ROOT) / "vibecrafted.toml")
    report = run_floor("hydrate", config, repo=REPO_ROOT, fetch=MapFetch([]))
    assert not report.failed, render_report(report)
    assert any(
        item.surface == "version_bump" and item.level == "ok"
        for item in report.findings
    )
    assert any(
        item.surface == "brand_repo" and item.level == "ok" for item in report.findings
    )


def test_publisher_calls_the_release_floor_before_it_can_publish() -> None:
    text = (REPO_ROOT / "scripts" / "publish-vibecrafted-release.sh").read_text(
        encoding="utf-8"
    )
    assert "vibecrafted_core.public_surface" in text
    assert "release --repo" in text
    # The floor is a precondition, not a second publisher.
    floor_at = text.index("vibecrafted_core.public_surface")
    create_at = text.index("gh release create")
    assert floor_at < create_at
