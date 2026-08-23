use std::fs;
use std::path::{Path, PathBuf};

use control_core::{
    SCAFFOLD_EXPORT_SCHEMA_VERSION, SCAFFOLD_MANIFEST_SCHEMA_JSON, ScaffoldArtifactPatch,
    ScaffoldArtifactRole, ScaffoldArtifactStore, ScaffoldCheckpointPatch, ScaffoldError,
    ScaffoldManifest, doctor_plan_root,
};
use serde_json::json;

fn temp_home(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    std::env::temp_dir().join(format!("control-core-{name}-{nanos}"))
}

fn plan_root(home: &Path, plan_id: &str) -> PathBuf {
    home.join("artifacts/vetcoders/vibecrafted/2026_0720/plans")
        .join(plan_id)
}

fn write_plan(home: &Path, plan_id: &str, artifacts: serde_json::Value) -> PathBuf {
    let root = plan_root(home, plan_id);
    fs::create_dir_all(&root).expect("plan root");
    let manifest = json!({
        "schema_version": "1",
        "plan_id": plan_id,
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_0720",
        "artifacts": artifacts,
    });
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest).expect("manifest json"),
    )
    .expect("manifest");
    root
}

fn frontmatter(role: &str, plan_id: &str) -> String {
    format!(
        "---\nplan_id: {plan_id}\nsession_id: test-session\nrole: {role}\nagent: grok\ndate: 2026-07-20\nproject: vetcoders/vibecrafted\n---\n\n"
    )
}

fn driver_body(plan_id: &str) -> String {
    format!(
        "{}# DRIVER\n\n## 1. Pełne ścieżki\n\n| Rzecz | Ścieżka |\n|---|---|\n| Root | /Users/tester/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0720/plans/{plan_id}/ |\n\n## 2. Graf zależności — why\n\n| Krawędź | Why |\n|---|---|\n| A → B | why shared domain |\n\n## 3. Gotowe komendy\n\n```bash\nvibecrafted implement claude --file /Users/tester/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0720/plans/{plan_id}/briefs/W1-01_cut.md\n```\n\n## 4. Reguła `[ ]→[x]`\n\n`[ ]` todo · `[~]` running · `[?]` done-unverified · `[!]` blocked · `[x]` verifier-green\n**Only a delivery-verifier flips `[~]→[x]`.**\n\n## 5. Snapshot\n\nW1-01 [ ]\ndou-index = 0/1 = 0.00\n",
        frontmatter("driver", plan_id)
    )
}

fn atlas_body(plan_id: &str) -> String {
    format!(
        "{}# Wave Atlas\n\n## Wave atlas\n\n| Cut | Vector | Depends |\n|---|---|---|\n| W1-01 | implement | — |\n\n### Graf zależności\n\n```\nW1-01 → done\n```\n",
        frontmatter("wave-atlas", plan_id)
    )
}

fn brief_body(plan_id: &str) -> String {
    format!(
        "{}# W1-01 — cut\n\n## 1. Mission\n\nDo the thing.\n\n## 2. Context\n\nBackground.\n\n## 3. Files\n\n- a.rs\n\n## 4. Acceptance\n\n- [ ] unit behavior holds\n\n## 5. Gates\n\n`python3 -m pytest vibecrafted-core/tests/ -q`\n\n## 6. Verification (walk-around)\n\nRun the real binary.\n\n## 7. Out of scope\n\nOther work.\n\n## 8. Living Tree etiquette\n\nRe-read before edit.\n\n## 9. Loctree-first\n\nloct context first.\n\n## 10. Recovery hint\n\nResume then re-dispatch.\n\n## 11. Branch + commit\n\n`[agent/implement] …`\n\n## 12. Report path\n\n`~/.vibecrafted/artifacts/.../reports/`\n",
        frontmatter("brief", plan_id)
    )
}

fn design_body(plan_id: &str) -> String {
    format!(
        "{}# Architecture\n\nNotes.\n",
        frontmatter("design-doc", plan_id)
    )
}

fn declarations() -> serde_json::Value {
    json!([
        {"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true},
        {"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true},
        {"id":"w1-01","role":"brief","path":"briefs/W1-01_cut.md","editable":true,"required":true},
        {"id":"architecture","role":"design-doc","path":"notes/architecture.md","editable":true,"required":true}
    ])
}

fn populate(root: &Path, plan_id: &str) {
    fs::create_dir_all(root.join("briefs")).expect("briefs");
    fs::create_dir_all(root.join("notes")).expect("notes");
    fs::write(root.join("DRIVER.md"), driver_body(plan_id)).expect("driver");
    fs::write(root.join("00_ATLAS.md"), atlas_body(plan_id)).expect("atlas");
    fs::write(root.join("briefs/W1-01_cut.md"), brief_body(plan_id)).expect("brief");
    fs::write(root.join("notes/architecture.md"), design_body(plan_id)).expect("design");
}

#[test]
fn published_schema_and_fixture_match_the_typed_model() {
    let fixture = include_str!("fixtures/scaffold-manifest-v1.json");
    let manifest: ScaffoldManifest = serde_json::from_str(fixture).expect("typed fixture");
    let schema: serde_json::Value =
        serde_json::from_str(SCAFFOLD_MANIFEST_SCHEMA_JSON).expect("published schema");
    let path_pattern = schema["properties"]["artifacts"]["items"]["properties"]["path"]["pattern"]
        .as_str()
        .expect("artifact path pattern");
    assert_eq!(manifest.schema_version, "1");
    assert_eq!(manifest.artifacts[2].role, ScaffoldArtifactRole::Brief);
    assert_eq!(manifest.artifacts[2].dependencies, ["driver", "atlas"]);
    assert!(SCAFFOLD_MANIFEST_SCHEMA_JSON.contains("\"wave-atlas\""));
    assert_eq!(path_pattern, r"^[^/\\].*\.md$");
}

#[test]
fn doctor_rejects_invalid_driver_atlas_tracker_and_frontmatter_drift() {
    let home = temp_home("doctor-contracts");
    let root = write_plan(
        &home,
        "plan-a",
        json!([
            {"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true},
            {"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true},
            {"id":"tracker","role":"tracker","path":"tracker.md","editable":true,"required":true},
            {"id":"w1-01","role":"brief","path":"briefs/W1-01_cut.md","editable":true,"required":true}
        ]),
    );
    fs::create_dir_all(root.join("briefs")).expect("briefs");
    fs::write(root.join("DRIVER.md"), "# Decorative driver\n").expect("driver");
    fs::write(root.join("00_ATLAS.md"), "# Notes\n").expect("atlas");
    fs::write(root.join("tracker.md"), "# Tracker\n").expect("tracker");
    fs::write(
        root.join("briefs/W1-01_cut.md"),
        "---\nid: wrong-id\nrole: report\n---\n# Mission\n",
    )
    .expect("brief");

    let report = ScaffoldArtifactStore::new(&home)
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-a")
        .expect("doctor");
    let codes = report
        .errors
        .iter()
        .map(|error| error.code.as_str())
        .collect::<Vec<_>>();
    assert!(codes.contains(&"driver_contract"), "codes={codes:?}");
    assert!(codes.contains(&"atlas_contract"), "codes={codes:?}");
    assert!(
        codes.iter().any(|code| code.starts_with("frontmatter")),
        "codes={codes:?}"
    );
    assert!(
        codes
            .iter()
            .any(|code| *code == "brief_sections" || *code == "acceptance_contract"),
        "codes={codes:?}"
    );
    assert!(!report.valid);

    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_plan_root_refuses_non_plan_cleanly() {
    let home = temp_home("not-a-plan");
    fs::create_dir_all(&home).expect("dir");
    let err = doctor_plan_root(&home).expect_err("must refuse");
    let message = err.to_string();
    assert!(
        message.contains("manifest.json") || message.contains("not a plan"),
        "{message}"
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_negative_cases_fail_independently() {
    let home = temp_home("negatives");
    let base = write_plan(&home, "plan-ok", declarations());
    populate(&base, "plan-ok");
    let store = ScaffoldArtifactStore::new(&home);
    let ok = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-ok")
        .expect("doctor");
    assert!(ok.valid, "{:?}", ok.errors);

    // 1) remove a required brief
    let missing = write_plan(&home, "plan-missing-brief", declarations());
    populate(&missing, "plan-missing-brief");
    fs::remove_file(missing.join("briefs/W1-01_cut.md")).expect("rm brief");
    let report = store
        .doctor(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            "plan-missing-brief",
        )
        .expect("doctor");
    assert!(!report.valid);
    assert!(
        report
            .errors
            .iter()
            .any(|error| error.code == "missing_required_artifact")
    );

    // 2) break dependency
    let deps = write_plan(
        &home,
        "plan-bad-dep",
        json!([
            {"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true},
            {"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true},
            {"id":"w1-01","role":"brief","path":"briefs/W1-01_cut.md","editable":true,"required":true,"dependencies":["no-such-id"]},
            {"id":"architecture","role":"design-doc","path":"notes/architecture.md","editable":true,"required":true}
        ]),
    );
    populate(&deps, "plan-bad-dep");
    let report = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-bad-dep")
        .expect("doctor");
    assert!(
        report
            .errors
            .iter()
            .any(|error| error.code == "unknown_dependency")
    );

    // 3) strip frontmatter from DRIVER
    let bare = write_plan(&home, "plan-bare", declarations());
    populate(&bare, "plan-bare");
    let driver = fs::read_to_string(bare.join("DRIVER.md")).expect("driver");
    let stripped = driver
        .split_once("---\n")
        .and_then(|(_, rest)| rest.split_once("\n---\n").map(|(_, body)| body.to_string()))
        .unwrap_or(driver);
    fs::write(bare.join("DRIVER.md"), stripped).expect("strip");
    let report = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-bare")
        .expect("doctor");
    assert!(
        report
            .errors
            .iter()
            .any(|error| error.code == "frontmatter_missing")
    );

    // 4) cut [ ]→[x] rule from DRIVER
    let no_rule = write_plan(&home, "plan-no-rule", declarations());
    populate(&no_rule, "plan-no-rule");
    let driver = fs::read_to_string(no_rule.join("DRIVER.md")).expect("driver");
    let mutilated = driver
        .replace("[ ]→[x]", "[ ] to [x]")
        .replace("[~]→[x]", "[~] to [x]");
    fs::write(no_rule.join("DRIVER.md"), mutilated).expect("write");
    let report = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-no-rule")
        .expect("doctor");
    assert!(
        report
            .errors
            .iter()
            .any(|error| error.code == "driver_contract" && error.message.contains("[ ]→[x]")),
        "{:?}",
        report.errors
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn manifest_plan_is_discovered_without_operator_mirror_and_roles_are_explicit() {
    let home = temp_home("manifest-discovery");
    let root = write_plan(&home, "plan-a", declarations());
    populate(&root, "plan-a");

    let store = ScaffoldArtifactStore::new(&home);
    let plans = store
        .plans("vetcoders", "vibecrafted", "2026_0720")
        .expect("plans");
    assert_eq!(
        plans
            .iter()
            .map(|plan| plan.plan_id.as_str())
            .collect::<Vec<_>>(),
        ["plan-a"]
    );

    let workspace = store
        .workspace("vetcoders", "vibecrafted", "2026_0720", Some("plan-a"))
        .expect("workspace");
    assert_eq!(workspace.plan_id, "plan-a");
    assert_eq!(workspace.artifacts.len(), 4);
    assert_eq!(workspace.artifacts[3].role, ScaffoldArtifactRole::DesignDoc);
    assert_eq!(
        workspace.artifacts[3].relative_path,
        "notes/architecture.md"
    );
    assert!(!root.join("../operator").exists());

    fs::remove_dir_all(home).ok();
}

#[test]
fn portable_export_removes_host_paths_and_freezes_no_branch() {
    let home = temp_home("portable-export");
    let root = write_plan(&home, "plan-a", declarations());
    populate(&root, "plan-a");
    let driver = fs::read_to_string(root.join("DRIVER.md")).expect("driver");
    fs::write(
        root.join("DRIVER.md"),
        driver.replace(
            "/Users/tester/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0720/plans/plan-a",
            &root.display().to_string(),
        ),
    )
    .expect("portable fixture driver");
    let repo_root = "/Volumes/vc-workspace/vetcoders/vibecrafted";
    fs::write(
        root.join("notes/architecture.md"),
        format!(
            "{}baseline_branch: feat/author-host\nrepo={repo_root}/src\nplan: {}/briefs\n",
            frontmatter("design-doc", "plan-a"),
            root.display(),
        ),
    )
    .expect("portable fixture design");
    let store = ScaffoldArtifactStore::new(&home);

    assert!(
        store
            .export_bundle("vetcoders", "vibecrafted", "2026_0720", "plan-a", None)
            .is_err(),
        "undeclared repo root must fail closed"
    );
    let bundle = store
        .export_bundle(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            "plan-a",
            Some(repo_root),
        )
        .expect("portable bundle");
    let encoded = serde_json::to_string(&bundle).expect("bundle json");

    assert_eq!(bundle.schema_version, SCAFFOLD_EXPORT_SCHEMA_VERSION);
    assert!(!encoded.contains("/Users/"));
    assert!(!encoded.contains("/Volumes/"));
    assert!(encoded.contains("${SCAFFOLD_ROOT}"));
    assert!(encoded.contains("${REPO_ROOT}"));
    assert!(encoded.contains("baseline_branch: <living-tree>"));
    assert!(
        bundle
            .artifacts
            .iter()
            .all(|artifact| !artifact.relative_path.starts_with('/'))
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn plan_selection_is_explicit_when_more_than_one_manifest_exists() {
    let home = temp_home("selection");
    for plan_id in ["plan-a", "plan-b"] {
        let root = write_plan(&home, plan_id, declarations());
        populate(&root, plan_id);
    }
    let store = ScaffoldArtifactStore::new(&home);

    let error = store
        .workspace("vetcoders", "vibecrafted", "2026_0720", None)
        .expect_err("ambiguous plan selection must fail");
    assert!(matches!(error, ScaffoldError::SelectionRequired { .. }));
    assert_eq!(
        store
            .workspace("vetcoders", "vibecrafted", "2026_0720", Some("plan-b"))
            .expect("explicit plan")
            .plan_id,
        "plan-b"
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn global_catalog_lists_scaffold_truth_and_ignores_unrelated_manifests() {
    let home = temp_home("catalog");
    for plan_id in ["plan-a", "plan-b"] {
        let root = write_plan(&home, plan_id, declarations());
        populate(&root, plan_id);
    }
    let unrelated = home.join("artifacts/Loctree/context-atlas");
    fs::create_dir_all(&unrelated).expect("unrelated manifest root");
    fs::write(
        unrelated.join("manifest.json"),
        br#"{"schema":"loctree.context-atlas.v1","files":[]}"#,
    )
    .expect("unrelated manifest");
    let deep_noise = home.join("artifacts/noise/deep/noncanonical/tree");
    fs::create_dir_all(&deep_noise).expect("deep noise root");
    fs::write(
        deep_noise.join("manifest.json"),
        br#"{
          "schema_version":"1",
          "plan_id":"deep-noise",
          "org":"noise",
          "repo":"deep",
          "day":"noncanonical",
          "artifacts":[]
        }"#,
    )
    .expect("deep valid-looking noise manifest");
    let invalid_sibling = plan_root(&home, "invalid-sibling");
    fs::create_dir_all(&invalid_sibling).expect("invalid sibling root");
    fs::write(
        invalid_sibling.join("manifest.json"),
        br#"{"plan_id":"invalid-sibling"}"#,
    )
    .expect("invalid sibling manifest");

    let store = ScaffoldArtifactStore::new(&home);
    let catalog = store.catalog();
    assert_eq!(
        catalog
            .iter()
            .map(|plan| plan.plan_id.as_str())
            .collect::<Vec<_>>(),
        ["plan-a", "plan-b"]
    );
    assert!(catalog.iter().all(|plan| plan.artifact_count == 4));
    assert_eq!(
        store
            .plans("vetcoders", "vibecrafted", "2026_0720")
            .expect("valid plans survive an invalid sibling")
            .iter()
            .map(|plan| plan.plan_id.as_str())
            .collect::<Vec<_>>(),
        ["plan-a", "plan-b"]
    );
    assert_eq!(
        store
            .workspace("vetcoders", "vibecrafted", "2026_0720", Some("plan-a"))
            .expect("valid workspace survives an invalid sibling")
            .plan_id,
        "plan-a"
    );
    assert!(store.is_plan_reviewable("vetcoders", "vibecrafted", "2026_0720", "plan-a"));
    fs::remove_file(plan_root(&home, "plan-a").join("DRIVER.md")).expect("remove driver");
    assert!(!store.is_plan_reviewable("vetcoders", "vibecrafted", "2026_0720", "plan-a"));
    fs::write(
        plan_root(&home, "plan-a").join("DRIVER.md"),
        driver_body("plan-a"),
    )
    .expect("restore driver");
    assert!(matches!(
        store.latest_workspace(),
        Err(ScaffoldError::SelectionRequired { plan_ids })
            if plan_ids == ["plan-a", "plan-b"]
    ));

    fs::remove_dir_all(home).ok();
}

#[test]
fn canonical_write_requires_hash_rejects_unlisted_and_scopes_history() {
    let home = temp_home("writes");
    for plan_id in ["plan-a", "plan-b"] {
        let root = write_plan(&home, plan_id, declarations());
        populate(&root, plan_id);
        fs::write(root.join("unlisted.md"), "not editable\n").expect("unlisted");
    }
    let store = ScaffoldArtifactStore::new(&home);
    let before = store
        .workspace("vetcoders", "vibecrafted", "2026_0720", Some("plan-a"))
        .expect("workspace");
    let brief = before
        .artifacts
        .iter()
        .find(|artifact| artifact.id == "w1-01")
        .expect("brief");
    let updated = store
        .write_artifact(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            "plan-a",
            ScaffoldArtifactPatch {
                artifact_id: brief.id.clone(),
                content: format!("{}# rewritten\n", frontmatter("brief", "plan-a")),
                expected_hash: brief.content_hash.clone(),
            },
        )
        .expect("write");
    assert_ne!(updated.content_hash, brief.content_hash);
    assert!(
        store
            .write_artifact(
                "vetcoders",
                "vibecrafted",
                "2026_0720",
                "plan-a",
                ScaffoldArtifactPatch {
                    artifact_id: "missing".into(),
                    content: "x".into(),
                    expected_hash: "sha256:0".into(),
                },
            )
            .is_err()
    );
    let checkpoint = store
        .checkpoint(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            "plan-a",
            ScaffoldCheckpointPatch {
                artifact_id: "w1-01".into(),
                approved: true,
                note: "ok".into(),
            },
        )
        .expect("checkpoint");
    assert!(checkpoint.approved);
    let changes = store
        .changes("vetcoders", "vibecrafted", "2026_0720", "plan-a")
        .expect("changes");
    assert!(changes.iter().any(|change| change.action == "edit"));
    assert!(changes.iter().any(|change| change.action == "checkpoint"));
    assert!(
        !store
            .changes("vetcoders", "vibecrafted", "2026_0720", "plan-b")
            .expect("scoped")
            .iter()
            .any(|change| change.plan_id == "plan-a")
    );

    fs::remove_dir_all(home).ok();
}

#[cfg(unix)]
#[test]
fn writable_symlink_and_escaping_symlink_are_rejected() {
    use std::os::unix::fs::symlink;

    let home = temp_home("symlink");
    let root = write_plan(&home, "plan-a", declarations());
    populate(&root, "plan-a");
    let outside = home.join("outside.md");
    fs::write(&outside, "outside\n").expect("outside");
    fs::remove_file(root.join("briefs/W1-01_cut.md")).expect("remove brief");
    symlink(&outside, root.join("briefs/W1-01_cut.md")).expect("symlink");

    let store = ScaffoldArtifactStore::new(&home);
    let report = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", "plan-a")
        .expect("doctor report");
    assert!(!report.valid);
    assert!(
        report
            .errors
            .iter()
            .any(|error| error.code == "writable_symlink")
    );
    assert!(
        store
            .workspace("vetcoders", "vibecrafted", "2026_0720", Some("plan-a"))
            .is_err()
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_and_server_return_identical_manifest_order_for_twenty_four_artifacts() {
    let home = temp_home("parity");
    let mut artifacts = vec![
        json!({"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true}),
        json!({"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true}),
    ];
    for index in 1..=22 {
        artifacts.push(json!({
            "id": format!("w0-{index:02}"),
            "role": "brief",
            "path": format!("briefs/W0-{index:02}_cut.md"),
            "editable": true,
            "required": true
        }));
    }
    let plan_id = "aicx-product-convergence-v1";
    let root = write_plan(&home, plan_id, json!(artifacts));
    fs::create_dir_all(root.join("briefs")).expect("briefs");
    fs::write(root.join("DRIVER.md"), driver_body(plan_id)).expect("driver");
    fs::write(root.join("00_ATLAS.md"), atlas_body(plan_id)).expect("atlas");
    for index in 1..=22 {
        fs::write(
            root.join(format!("briefs/W0-{index:02}_cut.md")),
            brief_body(plan_id),
        )
        .expect("brief");
    }

    let store = ScaffoldArtifactStore::new(&home);
    let workspace = store
        .workspace("vetcoders", "vibecrafted", "2026_0720", Some(plan_id))
        .expect("workspace");
    let doctor = store
        .doctor("vetcoders", "vibecrafted", "2026_0720", plan_id)
        .expect("doctor");
    assert!(doctor.valid, "{:?}", doctor.errors);
    assert_eq!(workspace.artifacts.len(), 24);
    assert_eq!(
        doctor.artifact_ids,
        workspace
            .artifacts
            .iter()
            .map(|a| a.id.clone())
            .collect::<Vec<_>>()
    );

    fs::remove_dir_all(home).ok();
}

#[test]
fn legacy_operator_workspace_is_read_only() {
    let home = temp_home("legacy");
    let operator = home.join("artifacts/vetcoders/vibecrafted/2026_0720/operator");
    fs::create_dir_all(&operator).expect("operator");
    fs::write(operator.join("master-dispatch.md"), "# Legacy\n").expect("legacy");
    let store = ScaffoldArtifactStore::new(&home);
    let workspace = store
        .workspace("vetcoders", "vibecrafted", "2026_0720", None)
        .expect("legacy readable");
    assert!(workspace.legacy_read_only);
    assert!(!workspace.artifacts[0].editable);

    fs::remove_dir_all(home).ok();
}

#[test]
fn typed_status_update_and_control_event_bridge_holds() {
    use control_core::ScaffoldStatusPatch;

    let home = temp_home("status-bridge");
    let plan_id = "plan-status-test";
    let root = write_plan(
        &home,
        plan_id,
        json!([
            {"id":"tracker","role":"tracker","path":"tracker.md","editable":true,"required":true}
        ]),
    );
    fs::write(
        root.join("tracker.md"),
        "# Tracker\n\n- [ ] W1-01 first task\n- [x] W1-02 second task\n",
    )
    .expect("tracker");

    let store = ScaffoldArtifactStore::new(&home);

    // 1. Update W1-01 to done ([x])
    let updated = store
        .write_status(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            plan_id,
            ScaffoldStatusPatch {
                artifact_id: "tracker".into(),
                item_id: Some("W1-01".into()),
                item_index: None,
                status: "done".into(),
                note: Some("verified in test".into()),
            },
        )
        .expect("write status");

    assert!(updated.content.contains("- [x] W1-01 first task"));

    // Check .scaffold-changes.jsonl
    let changes = store
        .changes("vetcoders", "vibecrafted", "2026_0720", plan_id)
        .expect("changes");
    assert_eq!(changes.len(), 1);
    assert_eq!(changes[0].action, "status");
    assert!(changes[0].note.contains("W1-01"));

    // Check control_plane/events.jsonl
    let events_path = home.join("control_plane/events.jsonl");
    assert!(events_path.is_file());
    let events_text = fs::read_to_string(&events_path).expect("events read");
    assert!(events_text.contains("scaffold.status.updated"));
    assert!(events_text.contains(plan_id));

    // 2. Update W1-01 to running ([~])
    let running = store
        .write_status(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            plan_id,
            ScaffoldStatusPatch {
                artifact_id: "tracker".into(),
                item_id: Some("W1-01".into()),
                item_index: None,
                status: "running".into(),
                note: None,
            },
        )
        .expect("write running status");

    assert!(running.content.contains("- [~] W1-01 first task"));

    fs::remove_dir_all(home).ok();
}
