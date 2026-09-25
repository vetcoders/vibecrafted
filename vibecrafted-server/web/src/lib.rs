#![recursion_limit = "512"]

pub mod app;
pub mod chrome;
pub mod control;
pub mod run_detail;
pub mod scaffold;
pub mod theme;
pub mod tools;

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
