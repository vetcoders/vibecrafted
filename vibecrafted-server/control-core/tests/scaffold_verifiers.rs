//! C5: scaffold-doctor must execute brief verifiers (or refuse).

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use control_core::{
    doctor_plan_root, extract_brief_verifier_commands, SCAFFOLD_MANIFEST_SCHEMA_JSON,
};
use serde_json::json;

static ENV_LOCK: Mutex<()> = Mutex::new(());

fn temp_home(name: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "control-core-verifiers-{name}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(&root).expect("temp home");
    root
}

fn plan_root(home: &Path, plan_id: &str) -> PathBuf {
    home.join("artifacts/vetcoders/vibecrafted/2026_0819/plans")
        .join(plan_id)
}

fn write_manifest(root: &Path, plan_id: &str) {
    let manifest = json!({
        "schema_version": "1",
        "plan_id": plan_id,
        "org": "vetcoders",
        "repo": "vibecrafted",
        "day": "2026_0819",
        "artifacts": [
            {"id":"driver","role":"driver","path":"DRIVER.md","editable":true,"required":true},
            {"id":"atlas","role":"wave-atlas","path":"00_ATLAS.md","editable":true,"required":true},
            {"id":"w1-01","role":"brief","path":"briefs/W1-01_cut.md","editable":true,"required":true}
        ],
    });
    fs::write(
        root.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest).expect("manifest"),
    )
    .expect("write manifest");
}

fn frontmatter(role: &str, plan_id: &str) -> String {
    format!(
        "---\nplan_id: {plan_id}\nsession_id: test-session\nrole: {role}\nagent: grok\ndate: 2026-08-19\nproject: vetcoders/vibecrafted\n---\n\n"
    )
}

fn driver_body(plan_id: &str) -> String {
    format!(
        "{}# DRIVER\n\n## 1. Pełne ścieżki\n\n| Rzecz | Ścieżka |\n|---|---|\n| Root | /Users/vetcoder/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0819/plans/{plan_id}/ |\n\n## 2. Graf zależności — why\n\n| Krawędź | Why |\n|---|---|\n| A → B | why shared domain |\n\n## 3. Gotowe komendy\n\n```bash\nvibecrafted implement claude --file /Users/vetcoder/.vibecrafted/artifacts/vetcoders/vibecrafted/2026_0819/plans/{plan_id}/briefs/W1-01_cut.md\n```\n\n## 4. Reguła `[ ]→[x]`\n\n`[ ]` todo · `[~]` running · `[?]` done-unverified · `[!]` blocked · `[x]` verifier-green\n**Only a delivery-verifier flips `[~]→[x]`.**\n\n## 5. Snapshot\n\nW1-01 [ ]\ndou-index = 0/1 = 0.00\n",
        frontmatter("driver", plan_id)
    )
}

fn atlas_body(plan_id: &str) -> String {
    format!(
        "{}# Wave Atlas\n\n## Wave atlas\n\n| Cut | Vector | Depends |\n|---|---|---|\n| W1-01 | implement | — |\n\n### Graf zależności\n\n```\nW1-01 → done\n```\n",
        frontmatter("wave-atlas", plan_id)
    )
}

fn brief_with_gates(plan_id: &str, gates: &str) -> String {
    format!(
        "{}# W1-01 — cut\n\n## 1. Mission\n\nDo the thing.\n\n## 2. Context\n\nBackground.\n\n## 3. Files\n\n- a.rs\n\n## 4. Acceptance\n\n- [ ] unit behavior holds — verifier: listed in Gates\n\n## 5. Gates\n\n{gates}\n\n## 6. Verification (walk-around)\n\nRun the real binary.\n\n## 7. Out of scope\n\nOther work.\n\n## 8. Living Tree etiquette\n\nRe-read before edit.\n\n## 9. Loctree-first\n\nloct context first.\n\n## 10. Recovery hint\n\nResume then re-dispatch.\n\n## 11. Branch + commit\n\n`[agent/implement] …`\n\n## 12. Report path\n\n`~/.vibecrafted/artifacts/.../reports/`\n",
        frontmatter("brief", plan_id)
    )
}

fn write_plan(home: &Path, plan_id: &str, gates: &str) -> PathBuf {
    let root = plan_root(home, plan_id);
    fs::create_dir_all(root.join("briefs")).expect("briefs");
    write_manifest(&root, plan_id);
    fs::write(root.join("DRIVER.md"), driver_body(plan_id)).expect("driver");
    fs::write(root.join("00_ATLAS.md"), atlas_body(plan_id)).expect("atlas");
    fs::write(
        root.join("briefs/W1-01_cut.md"),
        brief_with_gates(plan_id, gates),
    )
    .expect("brief");
    root
}

fn doctor_locked(root: &Path) -> control_core::ScaffoldDoctorReport {
    let _guard = ENV_LOCK.lock().expect("env lock");
    doctor_plan_root(root).expect("doctor_plan_root")
}

fn codes(report: &control_core::ScaffoldDoctorReport) -> Vec<&str> {
    report
        .errors
        .iter()
        .map(|error| error.code.as_str())
        .collect()
}

#[test]
fn extract_reads_gates_fence_and_acceptance_verifier_field() {
    let content = r#"
## 4. Acceptance
- [ ] cycles stay measured — verifier: `python3 -c 'print("today: 4")'`

## 5. Gates
```bash
# comment ignored
loct audit --json 2>/dev/null | python3 -c 'print("breaking 0")'
python3 -c 'print("today: 74")'
```

`echo leftover`

## 6. Verification
ignore me
"#;
    let commands = extract_brief_verifier_commands(content);
    assert!(
        commands
            .iter()
            .any(|command| command.contains("loct audit")),
        "{commands:?}"
    );
    assert!(
        commands
            .iter()
            .any(|command| command.contains("print(\"today: 74\")")),
        "{commands:?}"
    );
    assert!(
        commands
            .iter()
            .any(|command| command.contains("print(\"today: 4\")")),
        "{commands:?}"
    );
    assert!(
        !commands.iter().any(|command| command.starts_with('#')),
        "{commands:?}"
    );
}

#[test]
fn verifier_execution_does_not_invent_a_second_plan_schema() {
    assert!(SCAFFOLD_MANIFEST_SCHEMA_JSON.contains("\"falsification\""));
    assert!(
        !SCAFFOLD_MANIFEST_SCHEMA_JSON.contains("\"verifier\""),
        "C5 must read Gates/verifier: from existing briefs, not a new manifest field"
    );
}

#[test]
fn doctor_executes_probe_that_prints_today_and_an_exit_code() {
    let home = temp_home("today-ok");
    let root = write_plan(
        &home,
        "plan-today",
        "```bash\npython3 -c 'print(\"today: 74\")'\n```",
    );
    let report = doctor_locked(&root);
    assert!(
        report.valid,
        "expected PASS with executed today probe, errors={:?}",
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_refuses_brief_whose_gate_is_only_the_word_verifier() {
    let home = temp_home("word-only");
    let root = write_plan(
        &home,
        "plan-blind",
        "Run the delivery-verifier. It must stay green.",
    );
    let report = doctor_locked(&root);
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"verifier_missing"),
        "codes={:?}",
        codes(&report)
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_refuses_silent_command_with_no_today_value() {
    let home = temp_home("no-today");
    let root = write_plan(
        &home,
        "plan-silent",
        "```bash\npython3 -c 'raise SystemExit(0)'\n```",
    );
    let report = doctor_locked(&root);
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"verifier_no_today"),
        "codes={:?} errors={:?}",
        codes(&report),
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_refuses_unrunnable_command() {
    let home = temp_home("missing-bin");
    let root = write_plan(
        &home,
        "plan-missing-bin",
        "```bash\n/no/such/scaffold-c5-probe --today\n```",
    );
    let report = doctor_locked(&root);
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"verifier_unrunnable"),
        "codes={:?} errors={:?}",
        codes(&report),
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_refuses_network_verifier_without_running_it() {
    let home = temp_home("unsafe");
    let root = write_plan(
        &home,
        "plan-curl",
        "```bash\ncurl https://example.invalid/health\n```",
    );
    let report = doctor_locked(&root);
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"verifier_unsafe"),
        "codes={:?} errors={:?}",
        codes(&report),
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn doctor_refuses_when_the_probe_times_out() {
    let home = temp_home("timeout");
    let root = write_plan(
        &home,
        "plan-timeout",
        "```bash\npython3 -c 'import time; time.sleep(30); print(\"today: late\")'\n```",
    );
    let report = {
        let _guard = ENV_LOCK.lock().expect("env lock");
        // SAFETY: ENV_LOCK serializes environment mutation in this test binary.
        unsafe {
            std::env::set_var("SCAFFOLD_VERIFIER_TIMEOUT_MS", "200");
        }
        let report = doctor_plan_root(&root).expect("doctor_plan_root");
        unsafe {
            std::env::remove_var("SCAFFOLD_VERIFIER_TIMEOUT_MS");
        }
        report
    };
    assert!(!report.valid);
    assert!(
        codes(&report).contains(&"verifier_timeout"),
        "codes={:?} errors={:?}",
        codes(&report),
        report.errors
    );
    fs::remove_dir_all(home).ok();
}

#[test]
fn red_today_value_is_enough_nonzero_exit_is_not_a_doctor_failure() {
    let home = temp_home("red-today");
    let root = write_plan(
        &home,
        "plan-red",
        "```bash\npython3 -c 'print(\"today: structural=1\"); raise SystemExit(1)'\n```",
    );
    let report = doctor_locked(&root);
    assert!(
        report.valid,
        "baseline RED with a printed today value must pass doctor, errors={:?}",
        report.errors
    );
    fs::remove_dir_all(home).ok();
}
