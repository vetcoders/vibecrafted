import json
import tomllib
import zipfile
from pathlib import Path

import yaml
from vibecrafted_core.cli import LAUNCHERS

from scripts import build_marketplace_bundle as bundle
from scripts import vetcoders_install as installer


def test_parse_listing_metadata_reads_current_registry_fields() -> None:
    text = (bundle.REPO_ROOT / "docs" / "MARKETPLACE_LISTING.md").read_text(
        encoding="utf-8"
    )

    metadata = bundle.parse_listing_metadata(text)

    assert metadata.homepage == "https://vibecrafted.io/"
    assert metadata.repository == "https://github.com/vetcoders/vibecrafted"
    assert metadata.documentation == "https://vibecrafted.io/en/quickstart/"
    assert metadata.faq == "https://vibecrafted.io/en/faq/"
    assert metadata.license.startswith("Business Source License 1.1")
    assert "codex" in metadata.keywords


def test_mcp_config_uses_streamable_http_transport() -> None:
    # Canon transport for runs is streamable HTTP (one shared loctree-mcp per
    # root via `loct watch --bg`); MCP process ownership is separate.
    loctree = bundle.mcp_config()["mcpServers"]["loctree"]
    assert loctree["type"] == "http"
    assert loctree["url"] == "http://127.0.0.1:5174/mcp"
    assert "command" not in loctree


def test_discover_bundle_skills_tracks_live_skill_surface() -> None:
    skill_names = [
        skill.name for skill in bundle.discover_bundle_skills(bundle.REPO_ROOT)
    ]

    assert "vc-implement" in skill_names
    assert "vc-justdo" in skill_names  # alias kept in bundle
    assert "vc-marbles" in skill_names
    assert "vc-ship" in skill_names  # lifecycle umbrella kept in bundle
    assert "vc-ownership" in skill_names
    assert "vc-screenscribe" in skill_names


def test_top_level_skill_dirs_are_live_skills_or_foundations() -> None:
    allowed_non_skill_dirs = {"experimental", "foundations", "pl"}
    offenders: list[str] = []

    for path in sorted(
        (
            bundle.REPO_ROOT / "vibecrafted-core" / "vibecrafted_core" / "skills"
        ).iterdir()
    ):
        if not path.is_dir() or path.name in allowed_non_skill_dirs:
            continue

        # vc-grill is an interactive, single-document skill with no launch flow.
        markers = ("SKILL.md",) if path.name == "vc-grill" else ("SKILL.md", "FLOW.md")
        missing = [marker for marker in markers if not (path / marker).is_file()]
        if missing:
            offenders.append(
                f"{path.relative_to(bundle.REPO_ROOT)} missing {', '.join(missing)}"
            )

    assert not offenders, "\n".join(offenders)


def test_grill_distributes_as_an_interactive_skill_without_a_launcher(
    tmp_path: Path,
) -> None:
    skills = installer.source_skills_root(bundle.REPO_ROOT)
    canonical = skills / "vc-grill"
    assert canonical in installer.discover_skills(bundle.REPO_ROOT)
    assert canonical in bundle.discover_bundle_skills(bundle.REPO_ROOT)

    documents = [
        (root / "vc-grill" / "SKILL.md").read_text(encoding="utf-8")
        for root in (skills, skills / "pl")
    ]
    frontmatters = [text.split("---", 2)[1] for text in documents]
    assert frontmatters[0] == frontmatters[1], "PL must preserve triggering metadata"
    metadata = yaml.safe_load(frontmatters[0])
    assert metadata["name"] == "vc-grill"
    assert metadata["version"] == "1.0.0"
    assert metadata["description"].strip()
    for text in documents:
        assert "`/vc-grill`" in text
        assert "Design Contract" in text
        assert "Loctree" in text and "AICX" in text
        assert "vibecrafted grill" not in text
        assert "launcher `grill`" not in text

    # Exercise the existing distribution seams only against private artifacts.
    store = tmp_path / "store" / "vc-grill"
    installer.rsync_skill(canonical, store)
    assert (store / "SKILL.md").read_bytes() == (canonical / "SKILL.md").read_bytes()
    output = tmp_path / bundle.OUTPUT_FILENAME
    bundle.write_bundle(bundle.REPO_ROOT, output)
    with zipfile.ZipFile(output) as archive:
        assert (
            archive.read("skills/vc-grill/SKILL.md")
            == (canonical / "SKILL.md").read_bytes()
        )
        assert not any(
            "grill" in name and "/bin/" in name for name in archive.namelist()
        )

    assert "grill" not in LAUNCHERS
    project = tomllib.loads(
        (bundle.REPO_ROOT / "vibecrafted-core/pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert "vc-grill" not in project["project"]["scripts"]
    assert (
        "vibecrafted_core/skills/**" in project["tool"]["hatch"]["build"]["artifacts"]
    )
    for root in (bundle.REPO_ROOT / "bin", bundle.REPO_ROOT / "scripts"):
        assert not (root / "vc-grill").exists()


def test_forensics_documentation_routes_match_the_command_deck() -> None:
    assert "workflow" in LAUNCHERS
    assert "forensics" not in LAUNCHERS
    skills = bundle.REPO_ROOT / "vibecrafted-core/vibecrafted_core/skills"
    for root in (skills, skills / "pl"):
        for name in ("SKILL.md", "FLOW.md"):
            text = (root / "vc-forensics" / name).read_text(encoding="utf-8")
            assert "`/vc-forensics`" in text
            assert "`vibecrafted workflow <agent> --file <brief.md>`" in text
            assert "`vibecrafted forensics <agent>`" not in text


def test_write_bundle_uses_current_metadata_and_skill_inventory(tmp_path: Path) -> None:
    output_path = tmp_path / bundle.OUTPUT_FILENAME

    bundle.write_bundle(bundle.REPO_ROOT, output_path)

    with zipfile.ZipFile(output_path) as archive:
        manifest = json.loads(archive.read(".claude-plugin/plugin.json"))
        members = set(archive.namelist())

    assert manifest["version"] == bundle.read_version(bundle.REPO_ROOT)
    assert manifest["license"] != "MIT"
    assert "skills/vc-implement/SKILL.md" in members
    assert "skills/vc-justdo/SKILL.md" in members  # alias kept in bundle
    assert "skills/vc-marbles/SKILL.md" in members
    assert "skills/vc-ship/SKILL.md" in members
    assert "skills/vc-ownership/SKILL.md" in members
    assert "skills/vc-screenscribe/SKILL.md" in members
    assert "docs/RELEASE_KICKOFF.md" in members
    assert "docs/SUBMISSION_FORMS.md" in members


def test_write_bundle_includes_bundled_tool_drop_in_slot(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    bin_root = repo_root / "tools" / "bin"
    per_arch = bin_root / "linux-x86_64"
    per_arch.mkdir(parents=True)
    (repo_root / "docs").mkdir()
    (repo_root / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    (repo_root / "LICENSE").write_text("license\n", encoding="utf-8")
    (repo_root / "docs" / "MARKETPLACE_LISTING.md").write_text(
        "listing\n", encoding="utf-8"
    )
    (per_arch / "loctree-mcp").write_text("binary\n", encoding="utf-8")
    (bin_root / "prview").write_text("binary\n", encoding="utf-8")
    (bin_root / ".gitkeep").write_text("", encoding="utf-8")
    (bin_root / ".DS_Store").write_text("", encoding="utf-8")

    monkeypatch.setattr(bundle, "SUPPORT_DOC_PATHS", ())
    monkeypatch.setattr(bundle, "discover_bundle_skills", lambda _repo: [])
    monkeypatch.setattr(bundle, "discover_foundation_skills", lambda _repo: [])
    monkeypatch.setattr(
        bundle,
        "load_listing_metadata",
        lambda _repo: bundle.ListingMetadata(
            description="desc",
            keywords=("codex",),
            homepage="https://vibecrafted.io/",
            repository="https://github.com/vetcoders/vibecrafted",
            documentation="https://vibecrafted.io/en/quickstart/",
            faq="https://vibecrafted.io/en/faq/",
            license="Business Source License 1.1",
        ),
    )

    archive_bytes = bundle.build_bundle_bytes(repo_root)

    archive_path = tmp_path / "bundle.plugin"
    archive_path.write_bytes(archive_bytes)
    with zipfile.ZipFile(archive_path) as archive:
        members = set(archive.namelist())

    assert "tools/bin/linux-x86_64/loctree-mcp" in members
    assert "tools/bin/prview" in members
    assert "tools/bin/.gitkeep" not in members
    assert "tools/bin/.DS_Store" not in members


def test_framework_playground_uses_vibecrafted_command_deck() -> None:
    text = (bundle.REPO_ROOT / "docs" / "presence" / "framework.js").read_text(
        encoding="utf-8"
    )

    assert "vibecrafted scaffold claude" in text
    assert "vibecrafted partner claude" in text
    assert "vibecrafted marbles codex --count " in text
    assert "return 'vc-' + phaseDef.name;" not in text
