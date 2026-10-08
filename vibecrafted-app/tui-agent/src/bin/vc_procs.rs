//! vc-procs — fleet process monitor for Vibecrafted / vc-frame panes.

use voc::procs::ProcsApp;

fn main() -> anyhow::Result<()> {
    // The Runtime Pack inventory records every executable's `--version`, and
    // it runs without a terminal: answer before the TUI claims one.
    if std::env::args()
        .skip(1)
        .any(|arg| arg == "--version" || arg == "-V")
    {
        println!("vc-procs {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    ProcsApp::new().run()
}
