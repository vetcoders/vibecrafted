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
