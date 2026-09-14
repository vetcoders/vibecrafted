use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use tempfile::tempdir;
use voc::polarize::{PolarizeBand, current_intents_from_home, read_intent};
use voc::skills_catalog::CATALOG;

mod support;
use support::fixture_catalog;

#[cfg(unix)]
use std::os::unix::fs::symlink;

enum SkillRootResolution {
    Found(PathBuf),
    Missing(Vec<PathBuf>),
}

fn skill_root_candidates(manifest_dir: &Path) -> Vec<PathBuf> {
    std::env::var_os("VIBECRAFTED_SKILLS_ROOT")
        .map(PathBuf::from)
        .into_iter()
        .chain(manifest_dir.parent().map(|root| {
            root.parent()
                .map(|workspace| workspace.join("vibecrafted/skills"))
                .unwrap_or_else(|| root.join("vibecrafted/skills"))
        }))
        .chain(
            manifest_dir
                .parent()
                .and_then(Path::parent)
                .map(|root| root.join("skills")),
        )
        .collect()
}

fn has_skill_entries(path: &Path) -> bool {
    path.is_dir()
        && fs::read_dir(path)
            .map(|entries| {
                entries.filter_map(Result::ok).any(|entry| {
                    entry
                        .file_name()
                        .to_str()
                        .is_some_and(|name| name.starts_with("vc-"))
                        && entry.path().join("SKILL.md").is_file()
                })
            })
            .unwrap_or(false)
}

fn resolve_skill_root(manifest_dir: &Path) -> SkillRootResolution {
    let candidates = skill_root_candidates(manifest_dir);
    if let Some(path) = candidates.iter().find(|path| has_skill_entries(path)) {
        SkillRootResolution::Found(path.clone())
    } else {
        SkillRootResolution::Missing(candidates)
    }
}

#[test]
fn catalog_covers_existing_vibecrafted_skill_directories() {
    // The operator workspace is a standalone extraction; first-class skills
    // live in the vibecrafted skill kit, which is not bundled with this
    // repo. Resolve the skill source via, in order: VIBECRAFTED_SKILLS_ROOT
    // env override, a sibling `vibecrafted/skills/` next to the workspace,
    // or a colocated `skills/` for legacy monorepo checkouts. When none
    // exist, the integrity contract is verified by checking emphasis only —
    // CATALOG-vs-filesystem parity becomes a no-op rather than a false
    // failure in extracted environments.
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    match resolve_skill_root(manifest_dir) {
        SkillRootResolution::Found(skill_root) => {
            let mut existing = fs::read_dir(&skill_root)
                .unwrap_or_else(|err| panic!("failed to read {}: {err}", skill_root.display()))
                .filter_map(Result::ok)
                .filter_map(|entry| {
                    let path = entry.path();
                    if path.join("SKILL.md").is_file() {
                        entry.file_name().to_str().map(ToOwned::to_owned)
                    } else {
                        None
                    }
                })
                .filter(|name| name.starts_with("vc-"))
                .collect::<BTreeSet<_>>();
            existing.remove("foundations");
            // `vc-operator` is the orchestrator doctrine charter that this
            // workspace itself implements; it is intentionally not launchable
            // from the operator UI (recursion / category error) and so does
            // not appear in CATALOG.
            existing.remove("vc-operator");
            // `vc-ship` is the lifecycle umbrella executed by the manifest
            // runner, not a standalone legacy skill launch. Listing it here
            // would route around lifecycle stage/transition controls.
            existing.remove("vc-ship");
            // Foundation and tool-wrapper skills load with the framework or
            // wrap a CLI; they are not standalone launchable workflows and so
            // intentionally do not appear in CATALOG.
            existing.remove("vc-aicx");
            existing.remove("vc-loctree");
            existing.remove("vc-prview");
            existing.remove("vc-screenscribe");

            let catalog = CATALOG
                .iter()
                .map(|entry| entry.slug.to_string())
                .collect::<BTreeSet<_>>();

            assert_eq!(
                catalog,
                existing,
                "CATALOG drift vs {}",
                skill_root.display()
            );
        }
        SkillRootResolution::Missing(candidates) => {
            assert!(
                !candidates.is_empty(),
                "skills-root candidate list is empty"
            );
            eprintln!(
                "standalone skills topology: no skill root found; checked {}",
                candidates
                    .iter()
                    .map(|path| path.display().to_string())
                    .collect::<Vec<_>>()
                    .join(", ")
            );
        }
    }

    assert!(
        CATALOG
            .iter()
            .any(|entry| entry.slug == "vc-polarize" && entry.emphasized()),
        "vc-polarize must be an emphasized operator entrypoint"
    );
}

#[test]
fn every_skill_default_agent_is_a_live_catalog_agent() {
    // VOC no longer carries an agent enum of its own, so a skill can only
    // prefer an agent the canonical launcher still offers. This is what keeps a
    // retired launcher (gemini) from re-entering through the skills deck.
    let catalog = fixture_catalog();
    for entry in CATALOG {
        if entry.default_agent.is_empty() {
            // Empty means "whatever the operator selected" — the common case.
            continue;
        }
        assert!(
            catalog
                .agents
                .iter()
                .any(|agent| agent == entry.default_agent),
            "skill {} prefers {:?}, which the launcher catalog does not offer: {:?}",
            entry.slug,
            entry.default_agent,
            catalog.agents
        );
        assert_ne!(
            entry.default_agent, "gemini",
            "the gemini launcher is retired and must not be reachable from {}",
            entry.slug
        );
    }
}

#[test]
fn polarize_intent_ingests_prism_payload_and_renders_band_action() {
    let home = tempdir().unwrap();
    let prism = home
        .path()
        .join("artifacts/vetcoders/vc-tui/2026_0508/polarize/polr-123/prism.json");
    fs::create_dir_all(prism.parent().unwrap()).unwrap();
    fs::write(
        &prism,
        r#"{"schema_version":"loctree.prism.v1.1","total_score":13,"band_action":"doctrine"}"#,
    )
    .unwrap();

    let intent = read_intent(&prism).unwrap();
    assert_eq!(intent.band, PolarizeBand::Doctrine);
    assert_eq!(intent.score, 13);
    assert_eq!(intent.run_id, "polr-123");
    assert!(
        intent
            .summary_line()
            .contains("doctrine pass with regression contract")
    );

    let intents = current_intents_from_home(home.path(), Path::new("/tmp/repo"));
    assert_eq!(intents, vec![intent]);
}

#[test]
fn polarize_intent_prefers_canonical_band_action_over_score_fallback() {
    let home = tempdir().unwrap();
    let prism = home
        .path()
        .join("artifacts/vetcoders/vc-tui/2026_0508/polarize/polr-action/prism.json");
    fs::create_dir_all(prism.parent().unwrap()).unwrap();
    fs::write(
        &prism,
        r#"{"schema_version":"loctree.prism.v1.1","total_score":13,"band_action":"pass"}"#,
    )
    .unwrap();

    let intent = read_intent(&prism).unwrap();

    assert_eq!(intent.band, PolarizeBand::Pass);
    assert_eq!(intent.score, 13);
}

#[test]
fn polarize_intent_discovery_skips_malformed_prisms_without_hiding_valid_intents() {
    let home = tempdir().unwrap();
    let valid_prism = home
        .path()
        .join("artifacts/vetcoders/vc-tui/2026_0508/polarize/polr-valid/prism.json");
    let malformed_prism = home
        .path()
        .join("artifacts/vetcoders/vc-tui/2026_0508/polarize/polr-bad/prism.json");
    fs::create_dir_all(valid_prism.parent().unwrap()).unwrap();
    fs::create_dir_all(malformed_prism.parent().unwrap()).unwrap();
    fs::write(
        &valid_prism,
        r#"{"schema_version":"loctree.prism.v1.1","total_score":9,"band_action":"pass","run_id":"polr-valid"}"#,
    )
    .unwrap();
    fs::write(
        &malformed_prism,
        r#"{"schema_version":"loctree.prism.v1","total_score":"bad"}"#,
    )
    .unwrap();

    assert!(read_intent(&malformed_prism).is_err());
    let intents = current_intents_from_home(home.path(), Path::new("/tmp/repo"));

    assert_eq!(intents.len(), 1);
    assert_eq!(intents[0].run_id, "polr-valid");
    assert_eq!(intents[0].band, PolarizeBand::Pass);
}

#[cfg(unix)]
#[test]
fn polarize_intent_discovery_does_not_follow_symlinked_directories() {
    let home = tempdir().unwrap();
    let escaped = tempdir().unwrap();
    let valid_prism = home
        .path()
        .join("artifacts/vetcoders/vc-tui/2026_0508/polarize/polr-valid/prism.json");
    let escaped_prism = escaped
        .path()
        .join("vetcoders/vc-tui/2026_0508/polarize/polr-escaped/prism.json");
    fs::create_dir_all(valid_prism.parent().unwrap()).unwrap();
    fs::create_dir_all(escaped_prism.parent().unwrap()).unwrap();
    fs::write(
        &valid_prism,
        r#"{"schema_version":"loctree.prism.v1.1","total_score":9,"band_action":"pass","run_id":"polr-valid"}"#,
    )
    .unwrap();
    fs::write(
        &escaped_prism,
        r#"{"schema_version":"loctree.prism.v1.1","total_score":14,"band_action":"doctrine","run_id":"polr-escaped"}"#,
    )
    .unwrap();
    symlink(escaped.path(), home.path().join("artifacts/escaped-link")).unwrap();

    let intents = current_intents_from_home(home.path(), Path::new("/tmp/repo"));

    assert_eq!(intents.len(), 1);
    assert_eq!(intents[0].run_id, "polr-valid");
}
