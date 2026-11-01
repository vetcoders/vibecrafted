use std::env;

use tray_agent::menu::{current_service_count, refresh_service_count, update_service_count_label};

const SERVICES: &str = "VIBECRAFTED_MUX_SERVICES";

struct EnvGuard {
    previous: Option<String>,
}

impl EnvGuard {
    fn set(value: &str) -> Self {
        let previous = env::var(SERVICES).ok();
        // SAFETY: this integration test is the only writer and runs with
        // `--test-threads=1`, so no other thread reads the variable.
        unsafe { env::set_var(SERVICES, value) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- single test owns the process environment
        Self { previous }
    }

    fn unset() -> Self {
        let previous = env::var(SERVICES).ok();
        // SAFETY: same single-threaded test contract as `set`.
        unsafe { env::remove_var(SERVICES) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- single test owns the process environment
        Self { previous }
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        match self.previous.take() {
            // SAFETY: same single-threaded test contract as `set`.
            Some(value) => unsafe { env::set_var(SERVICES, value) }, // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- single test owns the process environment
            None => unsafe { env::remove_var(SERVICES) }, // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage -- single test owns the process environment
        }
    }
}

#[test]
fn menu_refresh_publishes_configured_service_count() {
    let _guard = EnvGuard::set("alpha, beta,,gamma");
    refresh_service_count(9);
    assert_eq!(current_service_count(), 9);

    update_service_count_label();

    assert_eq!(current_service_count(), 3);
}

#[test]
fn menu_refresh_counts_the_default_mux_service() {
    let _guard = EnvGuard::unset();
    refresh_service_count(0);

    update_service_count_label();

    assert_eq!(current_service_count(), 1);
}
