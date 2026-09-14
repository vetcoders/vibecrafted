//! C4: scaffold-doctor fails closed on a non-ancestor baseline and on
//! repo paths named in the plan that are missing from HEAD.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use control_core::{
    ScaffoldArtifactStore, collect_delivery_verifiers, doctor_plan_root, doctor_plan_root_in_repo,
};
use serde_json::json;

fn temp_home(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let path = std::env::temp_dir().join(format!(
        "control-core-c4-{name}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(&path).expect("temp home");
    path
}

fn plan_root(home: &Path, plan_id: &str) -> PathBuf {
    home.join("artifacts/vetcoders/vibecrafted/2026_0720/plans")
        .join(plan_id)
}

fn frontmatter(role: &str, plan_id: &str) -> String {
    format!(
        "---\nplan_id: {plan_id}\nsession_id: test-session\nrole: {role}\nagent: grok\ndate: 2026-07-20\nproject: vetcoders/vibecrafted\n---\n\n"
    )
}

fn driver_body(plan_id: &str, extra: &str) -> String {
    format!(
        "{}# DRIVER\n\n## 1. Pełne ścieżki\n\n| Rzecz | Ścieżka |\n|---|---|\n| Root | /Users/vetcoder/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0720/plans/{plan_id}/ |\n\n{extra}\n\n## 2. Graf zależności — why\n\n| Krawędź | Why |\n|---|---|\n| A → B | why shared domain |\n\n## 3. Gotowe komendy\n\n```bash\nvibecrafted implement claude --file /Users/vetcoder/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0720/plans/{plan_id}/briefs/W1-01_cut.md\n```\n\n## 4. Reguła `[ ]→[x]`\n\n`[ ]` todo · `[~]` running · `[?]` done-unverified · `[!]` blocked · `[x]` verifier-green\n**Only a delivery-verifier flips `[~]→[x]`.**\n\n## 5. Snapshot\n\nW1-01 [ ]\ndou-index = 0/1 = 0.00\n",
        frontmatter("driver", plan_id)
    )
}

fn atlas_body(plan_id: &str) -> String {
    format!(
        "{}# Wave Atlas\n\n## Wave atlas\n\n| Cut | Vector | Depends |\n|---|---|---|\n| W1-01 | implement | — |\n\n### Graf zależności\n\n```\nW1-01 → done\n```\n",
        frontmatter("wave-atlas", plan_id)
    )
}

fn brief_body(plan_id: &str, files: &str) -> String {
    format!(
        "{}# W1-01 — cut\n\n## 1. Mission\n\nDo the thing.\n\n## 2. Context\n\nBackground.\n\n## 3. Files\n\n{files}\n\n## 4. Acceptance\n\n- [ ] unit behavior holds\n\n## 5. Gates\n\n`python3 -m pytest vibecrafted-core/tests/ -q`\n\n## 6. Verification (walk-around)\n\nRun the real binary.\n\n## 7. Out of scope\n\nOther work.\n\n## 8. Living Tree etiquette\n\nRe-read before edit.\n\n## 9. Loctree-first\n\nloct context first.\n\n## 10. Recovery hint\n\nResume then re-dispatch.\n\n## 11. Branch + commit\n\n`[agent/implement] …`\n\n## 12. Report path\n\n`~/.vibecrafted/artifacts/.../reports/`\n",
        frontmatter("brief", plan_id)
    )
}

fn write_plan(home: &Path, plan_id: &str, driver_extra: &str, files: &str) -> PathBuf {
    let root = plan_root(home, plan_id);
    fs::create_dir_all(root.join("briefs")).expect("briefs");
    fs::create_dir_all(root.join("notes")).expect("notes");
    let manifest = json!({
        "schema_version": "1",
        "plan_id": plan_id,
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_0720",
        "artifacts": [
            {"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true},
            {"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true},
            {"id":"w1-01","role":"brief","path":"briefs/W1-01_cut.md","editable":true,"required":true},
            {"id":"architecture","role":"design-doc","path":"notes/architecture.md","editable":true,"required":true}
        ],
    });
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest).expect("manifest json"),
    )
    .expect("manifest");
    fs::write(root.join("DRIVER.md"), driver_body(plan_id, driver_extra)).expect("driver");
    fs::write(root.join("00_ATLAS.md"), atlas_body(plan_id)).expect("atlas");
    fs::write(root.join("briefs/W1-01_cut.md"), brief_body(plan_id, files)).expect("brief");
    fs::write(
        root.join("notes/architecture.md"),
        format!(
            "{}# Architecture\n\nNotes.\n",
            frontmatter("design-doc", plan_id)
        ),
    )
    .expect("design");
    root
}

fn git(repo: &Path, args: &[&str]) -> String {
    let output = Command::new("git")
        .current_dir(repo)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_TEMPLATE_DIR", "")
        .env("GIT_AUTHOR_NAME", "c4")
        .env("GIT_AUTHOR_EMAIL", "c4@example.com")
        .env("GIT_COMMITTER_NAME", "c4")
        .env("GIT_COMMITTER_EMAIL", "c4@example.com")
        .args([
            "-c",
            "init.defaultBranch=main",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.name=c4",
            "-c",
            "user.email=c4@example.com",
        ])
        .args(args)
        .stdin(Stdio::null())
        .output()
        .expect("spawn git");
    if !output.status.success() {
        panic!(
            "git {args:?} failed:\n{}\n{}",
            String::from_utf8_lossy(&output.stderr),
            String::from_utf8_lossy(&output.stdout)
        );
    }
    String::from_utf8_lossy(&output.stdout).trim().to_string()
}

struct GeometryRepo {
    root: PathBuf,
    ancestor: String,
    orphan: String,
}

fn init_geometry_repo(home: &Path) -> GeometryRepo {
    let root = home.join("repo");
    fs::create_dir_all(root.join("src")).expect("repo src");
    git(&root, &["init", "-b", "main"]);
    fs::write(root.join("README.md"), "hello\n").expect("readme");
    fs::write(root.join("src/keep.rs"), "fn keep() {}\n").expect("keep");
    git(&root, &["add", "README.md", "src/keep.rs"]);
    git(&root, &["commit", "-m", "ancestor"]);
    let ancestor = git(&root, &["rev-parse", "HEAD"]);
    fs::write(root.join("src/keep.rs"), "fn keep() { /* later */ }\n").expect("keep2");
    git(&root, &["add", "src/keep.rs"]);
    git(&root, &["commit", "-m", "head"]);
    git(&root, &["checkout", "--orphan", "orphan-c4"]);
    fs::write(root.join("only-on-orphan.md"), "secret\n").expect("orphan file");
    git(&root, &["add", "only-on-orphan.md"]);
    git(&root, &["commit", "-m", "orphan"]);
    let orphan = git(&root, &["rev-parse", "HEAD"]);
    git(&root, &["checkout", "main"]);
    GeometryRepo {
        root,
        ancestor,
        orphan,
    }
}

fn codes(report: &control_core::ScaffoldDoctorReport) -> Vec<&str> {
    report
        .errors
        .iter()
        .map(|error| error.code.as_str())
        .collect()
}

#[test]
fn ancestor_baseline_and_head_path_pass() {
    let home = temp_home("pass");
    let repo = init_geometry_repo(&home);
    let plan = write_plan(
        &home,
        "plan-ok",
        &format!("baseline_sha: {}\n", repo.ancestor),
        "- Edit: `src/keep.rs`\n",
    );
    let report = doctor_plan_root_in_repo(&plan, Some(&repo.root)).expect("doctor");
    assert!(report.valid, "expected pass, errors={:?}", report.errors);
    fs::remove_dir_all(home).ok();
}

#[test]
fn non_ancestor_baseline_is_refused() {
    let home = temp_home("not-ancestor");
    let repo = init_geometry_repo(&home);
    let plan = write_plan(
        &home,
        "plan-diverged",
        &format!(
            "The authoring baseline `cut/old` @ `{}` is not an ancestor of the checkout.\n",
            &repo.orphan[..8]
        ),
        "- a.rs\n",
    );
    let report = doctor_plan_root_in_repo(&plan, Some(&repo.root)).expect("doctor");
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"baseline_not_ancestor"),
        "codes={:?}",
        codes(&report)
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn unknown_baseline_sha_is_refused() {
    let home = temp_home("unknown-sha");
    let repo = init_geometry_repo(&home);
    let plan = write_plan(
        &home,
        "plan-unknown",
        "baseline_sha: deadbee1deadbee1deadbee1deadbee1deadbee1\n",
        "- a.rs\n",
    );
    let report = doctor_plan_root_in_repo(&plan, Some(&repo.root)).expect("doctor");
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"baseline_unknown"),
        "codes={:?}",
        codes(&report)
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn named_path_missing_on_head_is_refused() {
    let home = temp_home("missing-path");
    let repo = init_geometry_repo(&home);
    let plan = write_plan(
        &home,
        "plan-missing-path",
        "",
        "- Edit: `docs/ROADMAP_4.2.0.md`\n- Edit: `src/keep.rs`\n",
    );
    let report = doctor_plan_root_in_repo(&plan, Some(&repo.root)).expect("doctor");
    assert!(!report.valid);
    assert!(
        report.errors.iter().any(|error| {
            error.code == "named_path_missing"
                && error.path.as_deref() == Some("docs/ROADMAP_4.2.0.md")
        }),
        "errors={:?}",
        report.errors
    );
    assert!(
        !report
            .errors
            .iter()
            .any(|error| error.path.as_deref() == Some("src/keep.rs")),
        "existing path should not fail: {:?}",
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn named_baseline_without_git_repo_is_refused() {
    let home = temp_home("no-repo");
    let not_git = home.join("not-git");
    fs::create_dir_all(&not_git).expect("not git");
    let plan = write_plan(
        &home,
        "plan-no-repo",
        "baseline_sha: 1d1669ecace92c4196a7f9bf6e1adc1b7eae6a1f\n",
        "- a.rs\n",
    );
    let report = doctor_plan_root_in_repo(&plan, Some(&not_git)).expect("doctor");
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"baseline_repo_unresolved"),
        "codes={:?}",
        codes(&report)
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn store_doctor_with_repo_matches_free_function() {
    let home = temp_home("store");
    let repo = init_geometry_repo(&home);
    let _plan = write_plan(
        &home,
        "plan-store",
        &format!("baseline_sha: {}\n", repo.orphan),
        "- a.rs\n",
    );
    let store = ScaffoldArtifactStore::new(&home);
    let via_store = store
        .doctor_with_repo(
            "vetcoders",
            "vibecrafted",
            "2026_0720",
            "plan-store",
            Some(&repo.root),
        )
        .expect("store doctor");
    let via_root = doctor_plan_root_in_repo(plan_root(&home, "plan-store"), Some(&repo.root))
        .expect("root doctor");
    assert_eq!(codes(&via_store), codes(&via_root));
    assert!(codes(&via_store).contains(&"baseline_not_ancestor"));
    fs::remove_dir_all(home).ok();
}

#[test]
fn verifier_inventory_is_collected_not_executed() {
    let home = temp_home("probes");
    let plan = write_plan(&home, "plan-probes", "", "- a.rs\n");
    let manifest = serde_json::from_slice(&fs::read(plan.join("manifest.json")).expect("manifest"))
        .expect("typed manifest");
    let probes = collect_delivery_verifiers(&plan, &manifest);
    assert_eq!(probes.len(), 1);
    assert_eq!(probes[0].artifact_id, "w1-01");
    assert!(probes[0].command.contains("pytest"));
    let report = doctor_plan_root(&plan).expect("doctor without geometry");
    assert!(report.valid, "{:?}", report.errors);
    fs::remove_dir_all(home).ok();
}
