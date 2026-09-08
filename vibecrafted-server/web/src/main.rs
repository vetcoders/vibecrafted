#![recursion_limit = "512"]

// Vibecrafted server console - Leptos 0.8 SSR bin.

#[cfg(feature = "ssr")]
#[tokio::main]
async fn main() {
    use std::net::SocketAddr;
    use std::path::PathBuf;

    use axum::Router;
    use axum::http::StatusCode;
    use axum::routing::get;
    use leptos::config::{Env, LeptosOptions};
    use leptos::logging::log;
    use leptos_axum::{LeptosRoutes, generate_route_list};
    use vibecrafted_server_web::app::{App, shell};
    use vibecrafted_server_web::control::api::control_routes;
    use vibecrafted_server_web::scaffold::api::scaffold_routes;
    use vibecrafted_server_web::tools::api::{
        aicx_reference, aicx_search, loctree_report, loctree_report_asset,
    };

    /// Canonical default bind — matches Makefile `SERVER_ADDR` and
    /// `Cargo.toml` leptos site-addr. Bare `vc-server` must not invent a
    /// second product port (was 3000 for weeks while make server used 3024).
    const DEFAULT_ADDR: &str = "127.0.0.1:3024";

    fn print_help() {
        println!(
            "vc-server — Vibecrafted control-plane viewer (SSR)

Usage:
  vc-server [--addr <host:port>]

Options:
  --addr <host:port>   Bind address (default: {DEFAULT_ADDR})
  --help, -h           Show this help and exit
  --version, -V        Show version and exit

Environment:
  VC_SERVER_ADDR       Same as --addr
  VC_SERVER_SITE_ROOT  Static site root override
  LEPTOS_SITE_ADDR     Legacy bind address fallback
  VIBECRAFTED_HOME     Control-plane root (~/.vibecrafted)

Examples:
  vc-server
  vc-server --addr 127.0.0.1:3024
  make server                  # foreground dev run on {DEFAULT_ADDR}
  make install-server          # install real file into ~/.local/bin/vc-server
"
        );
    }

    fn print_version() {
        // VC_SERVER_VERSION is the build.rs product stamp (VERSION + git sha),
        // the same identity shape `vibecrafted --version` reports.
        println!(
            "vc-server {} ({})",
            env!("VC_SERVER_VERSION"),
            env!("CARGO_PKG_NAME")
        );
    }

    /// Fail-closed CLI surface: product binaries must not treat --help as
    /// "start a listener and hang" (runtime contract for install-all bins).
    fn valid_lifecycle_nonce(value: &str) -> bool {
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    }

    fn consume_cli_meta() -> Option<i32> {
        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--help" | "-h" | "help" => {
                    print_help();
                    return Some(0);
                }
                "--version" | "-V" | "version" => {
                    print_version();
                    return Some(0);
                }
                "--addr" => {
                    // value consumed in configured_addr; skip here
                    let _ = args.next();
                }
                other if other.starts_with("--addr=") => {}
                "--lifecycle-nonce" => {
                    let Some(value) = args.next() else {
                        eprintln!("vc-server: --lifecycle-nonce requires a value");
                        return Some(2);
                    };
                    if !valid_lifecycle_nonce(&value) {
                        eprintln!("vc-server: invalid lifecycle nonce");
                        return Some(2);
                    }
                }
                other if other.starts_with("--lifecycle-nonce=") => {
                    let value = other
                        .strip_prefix("--lifecycle-nonce=")
                        .expect("guarded lifecycle nonce prefix");
                    if !valid_lifecycle_nonce(value) {
                        eprintln!("vc-server: invalid lifecycle nonce");
                        return Some(2);
                    }
                }
                other if other.starts_with('-') => {
                    eprintln!("vc-server: unknown option `{other}`");
                    eprintln!("Try `vc-server --help` for usage.");
                    return Some(2);
                }
                other => {
                    eprintln!("vc-server: unexpected argument `{other}`");
                    eprintln!("Try `vc-server --help` for usage.");
                    return Some(2);
                }
            }
        }
        None
    }

    if let Some(code) = consume_cli_meta() {
        std::process::exit(code);
    }

    fn configured_addr() -> SocketAddr {
        let cli_addr = std::env::args()
            .skip(1)
            .find_map(|arg| arg.strip_prefix("--addr=").map(ToOwned::to_owned))
            .or_else(|| {
                let mut args = std::env::args().skip(1);
                while let Some(arg) = args.next() {
                    if arg == "--addr" {
                        return args.next();
                    }
                }
                None
            });
        let configured = cli_addr
            .or_else(|| std::env::var("VC_SERVER_ADDR").ok())
            .or_else(|| std::env::var("LEPTOS_SITE_ADDR").ok())
            .unwrap_or_else(|| DEFAULT_ADDR.to_string());

        configured.parse().unwrap_or_else(|err| {
            eprintln!(
                "vc-server: invalid address `{configured}` ({err}); falling back to {DEFAULT_ADDR}"
            );
            DEFAULT_ADDR
                .parse()
                .expect("default vc-server address parses")
        })
    }

    fn home_dir() -> Option<PathBuf> {
        std::env::var_os("HOME").map(PathBuf::from)
    }

    fn configured_site_root() -> String {
        if let Some(root) =
            std::env::var_os("VC_SERVER_SITE_ROOT").filter(|value| !value.is_empty())
        {
            return PathBuf::from(root).to_string_lossy().into_owned();
        }

        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(PathBuf::from));
        let mut candidates = Vec::new();

        if let Some(dir) = exe_dir {
            candidates.push(dir.join("vc-server-site"));
            candidates.push(dir.join("site"));
        }
        if let Some(home) = home_dir() {
            candidates.push(home.join(".local/share/vibecrafted/server/site"));
        }

        candidates
            .iter()
            .find(|path| path.exists())
            .cloned()
            .or_else(|| candidates.into_iter().next())
            .unwrap_or_else(|| PathBuf::from("target/site"))
            .to_string_lossy()
            .into_owned()
    }

    async fn favicon() -> StatusCode {
        StatusCode::NO_CONTENT
    }

    let leptos_options = LeptosOptions::builder()
        .output_name("vibecrafted-server-web")
        .site_root(configured_site_root())
        .site_pkg_dir("pkg")
        .env(Env::PROD)
        .site_addr(configured_addr())
        .reload_port(3025)
        .build();
    let addr = leptos_options.site_addr;
    let routes = generate_route_list(App);

    let app: Router = Router::new()
        // Owner-backed tool surfaces carry their own boundaries (see `tools`):
        // the report runs sandboxed on this origin, the AICX corpus is served
        // to a verified local peer only.
        .route("/structure/report", get(loctree_report))
        .route("/structure/report/{asset}", get(loctree_report_asset))
        .route("/api/aicx/search", get(aicx_search))
        .route("/api/aicx/reference", get(aicx_reference))
        .leptos_routes(&leptos_options, routes, {
            let opts = leptos_options.clone();
            move || shell(opts.clone())
        })
        .route("/favicon.ico", get(favicon))
        .merge(scaffold_routes())
        .merge(control_routes())
        .fallback(leptos_axum::file_and_error_handler(shell))
        .with_state(leptos_options);

    log!("listening on http://{addr}");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("bind site_addr");
    // Peer addresses reach handlers as `ConnectInfo`; the AICX boundary fails
    // closed without them.
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await
    .expect("axum::serve");
}

#[cfg(not(feature = "ssr"))]
fn main() {
    // Hydrate path lives in lib.rs::hydrate(); this stub keeps plain cargo build valid.
}
