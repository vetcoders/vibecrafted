//! Process environment that points the `ccusage` provider adapters at the
//! scrubbed session fixtures. Shared by the control-core, web and TUI tests.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// Fresh directory for one test, created atomically and private to this user.
pub fn private_dir(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let nonce = NEXT.fetch_add(1, Ordering::Relaxed);
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    // nosemgrep: rust.lang.security.temp-dir.temp-dir -- atomic private creation below rejects existing paths and symlinks before anything is written.
    let path = std::env::temp_dir().join(format!(
        "vc-ccusage-{label}-{}-{nanos}-{nonce}",
        std::process::id()
    ));
    let mut builder = fs::DirBuilder::new();
    #[cfg(unix)]
    {
        use std::os::unix::fs::DirBuilderExt;
        builder.mode(0o700);
    }
    builder.create(&path).expect("exclusive private directory");
    path
}

/// Provider stores resolve to `fixtures`, everything else under `host` is empty,
/// and any attempt to reach the network hits a closed local port.
///
/// # Safety
///
/// Mutates the process environment. The caller guarantees that nothing else
/// reads or writes it concurrently, normally by calling this inside a `Once`
/// that every test passes through before doing anything else.
pub unsafe fn point_provider_stores_at(fixtures: &Path, host: &Path) {
    // SAFETY: forwarded to the caller, see above.
    // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- fixture environment is established before any collector or projection starts, under the caller's Once.
    unsafe {
        std::env::set_var("HOME", host);
        std::env::set_var("XDG_CONFIG_HOME", host.join(".config"));
        std::env::set_var("XDG_DATA_HOME", host.join(".local/share"));
        std::env::set_var("CLAUDE_CONFIG_DIR", fixtures.join("claude"));
        std::env::set_var("CODEX_HOME", fixtures.join("codex"));
        std::env::set_var("GROK_HOME", fixtures.join("grok"));
        std::env::set_var("KIMI_DATA_DIR", fixtures.join("kimi"));
        std::env::set_var("COPILOT_HOME", fixtures.join("copilot"));
        std::env::set_var("ANTIGRAVITY_DATA_DIR", host.join("no-antigravity-store"));
        for proxy in [
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ] {
            std::env::set_var(proxy, "http://127.0.0.1:9");
        }
        std::env::remove_var("NO_PROXY");
        std::env::remove_var("no_proxy");
    }
}
