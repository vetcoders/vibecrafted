#![recursion_limit = "512"]

pub mod app;
pub mod chrome;

#[cfg(all(test, feature = "ssr"))]
#[test]
fn skills_one_list_then_one_editor() {
    app::skills_one_list_then_one_editor();
}
pub mod control;
pub mod run_detail;
pub mod scaffold;
pub mod theme;
pub mod tools;

// Delivery gates filter with `--exact` on these bare names. A test nested
// under `app::tests` is `app::tests::…` and that filter runs nothing.
#[cfg(all(test, feature = "ssr"))]
#[test]
fn overview_welcome_status_and_miniatures() {
    app::overview_welcome_status_and_miniatures();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn runs_five_buckets_use_settlement() {
    app::tests::runs_five_buckets_use_settlement();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn projects_filter_deduped_shelf() {
    app::projects_filter_deduped_shelf();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn code_intelligence_is_not_a_plan_library() {
    // `--lib code_intelligence_is_not_a_plan_library -- --exact` matches this
    // crate-root name, not `app::tests::…`.
    app::code_intelligence_is_not_a_plan_library();
}

#[cfg(all(test, feature = "ssr"))]
#[test]
fn history_is_aicx_not_plans() {
    crate::app::history_is_aicx_not_plans_proof();
}

#[cfg(feature = "hydrate")]
#[wasm_bindgen::prelude::wasm_bindgen]
pub fn hydrate() {
    use crate::app::App;

    console_error_panic_hook::set_once();
    leptos::mount::hydrate_body(App);
}

// The delivery gate filters with `--exact sidebar_groups_work_trace_machine`.
// Cargo's exact match is the full test path, so this has to live at the crate
// root. The assertions stay next to the chrome they describe.
#[cfg(all(test, feature = "ssr"))]
#[test]
fn sidebar_groups_work_trace_machine() {
    chrome::assert_sidebar_groups_work_trace_machine();
}
