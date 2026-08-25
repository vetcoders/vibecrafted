use crate::launch::LaunchRuntime;
use crate::observe::{self, ConsoleView};
use std::env;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CliOptions {
    pub state_root: Option<PathBuf>,
    pub command_deck: Option<PathBuf>,
    pub launch_root: Option<PathBuf>,
    pub launch_runtime: Option<LaunchRuntime>,
    pub terminal_binary: Option<PathBuf>,
    pub tick_ms: u64,
    pub no_verify_gate: bool,
    pub server: Option<String>,
    pub view: ConsoleView,
}

impl Default for CliOptions {
    fn default() -> Self {
        Self {
            state_root: None,
            command_deck: None,
            launch_root: None,
            launch_runtime: None,
            terminal_binary: None,
            tick_ms: 250,
            no_verify_gate: false,
            server: None,
            view: ConsoleView::Observe,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppConfig {
    pub state_root: PathBuf,
    pub command_deck: PathBuf,
    pub launch_root: PathBuf,
    pub launch_runtime: LaunchRuntime,
    pub terminal_binary: PathBuf,
    pub tick_rate: Duration,
    pub no_verify_gate: bool,
    pub server: String,
    pub view: ConsoleView,
}

pub fn parse_args() -> anyhow::Result<CliOptions> {
    let mut options = CliOptions::default();
    let mut args = env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--help" | "-h" => {
                print_help();
                std::process::exit(0);
            }
            "--version" | "-V" => {
                println!("voc {}", env!("CARGO_PKG_VERSION"));
                std::process::exit(0);
            }
            "--state-root" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--state-root requires a value"))?;
                options.state_root = Some(PathBuf::from(value));
            }
            _ if arg.starts_with("--state-root=") => {
                options.state_root = Some(PathBuf::from(arg.trim_start_matches("--state-root=")));
            }
            "--deck" | "--command-deck" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--deck requires a value"))?;
                options.command_deck = Some(PathBuf::from(value));
            }
            _ if arg.starts_with("--deck=") || arg.starts_with("--command-deck=") => {
                let value = arg
                    .split_once('=')
                    .map(|(_, value)| value)
                    .unwrap_or_default();
                options.command_deck = Some(PathBuf::from(value));
            }
            "--root" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--root requires a value"))?;
                options.launch_root = Some(PathBuf::from(value));
            }
            _ if arg.starts_with("--root=") => {
                options.launch_root = Some(PathBuf::from(arg.trim_start_matches("--root=")));
            }
            "--runtime" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--runtime requires a value"))?;
                options.launch_runtime = Some(value.parse::<LaunchRuntime>()?);
            }
            _ if arg.starts_with("--runtime=") => {
                let value = arg
                    .split_once('=')
                    .map(|(_, value)| value)
                    .unwrap_or_default();
                options.launch_runtime = Some(value.parse::<LaunchRuntime>()?);
            }
            "--terminal-binary" | "--vc_frame" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--terminal-binary requires a value"))?;
                options.terminal_binary = Some(PathBuf::from(value));
            }
            _ if arg.starts_with("--terminal-binary=") || arg.starts_with("--vc_frame=") => {
                let value = arg
                    .split_once('=')
                    .map(|(_, value)| value)
                    .unwrap_or_default();
                options.terminal_binary = Some(PathBuf::from(value));
            }
            "--tick-ms" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--tick-ms requires a value"))?;
                options.tick_ms = value.parse::<u64>()?;
            }
            "--no-verify-gate" => {
                options.no_verify_gate = true;
            }
            "--server" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--server requires a value"))?;
                options.server = Some(value);
            }
            _ if arg.starts_with("--server=") => {
                options.server = Some(arg.trim_start_matches("--server=").to_string());
            }
            "--view" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--view requires a value"))?;
                options.view = ConsoleView::parse(&value)?;
            }
            _ if arg.starts_with("--view=") => {
                options.view = ConsoleView::parse(arg.trim_start_matches("--view="))?;
            }
            _ => {
                return Err(anyhow::anyhow!("unknown argument: {arg}"));
            }
        }
    }
    Ok(options)
}

pub fn build_config(options: CliOptions) -> AppConfig {
    let command_deck = options.command_deck.unwrap_or_else(default_command_deck);
    AppConfig {
        state_root: options.state_root.unwrap_or_else(default_state_root),
        launch_root: options
            .launch_root
            .unwrap_or_else(|| default_launch_root(&command_deck)),
        launch_runtime: options.launch_runtime.unwrap_or_default(),
        terminal_binary: options
            .terminal_binary
            .unwrap_or_else(default_terminal_binary),
        command_deck,
        tick_rate: Duration::from_millis(options.tick_ms.max(50)),
        no_verify_gate: options.no_verify_gate,
        server: observe::normalize_origin(
            &options
                .server
                .unwrap_or_else(observe::default_server_origin),
        ),
        view: options.view,
    }
}

pub fn default_vibecrafted_home() -> PathBuf {
    env::var_os("VIBECRAFTED_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(home_dir()).join(".vibecrafted"))
}

pub fn default_state_root() -> PathBuf {
    let home = default_vibecrafted_home();
    for candidate in [
        home.join("control_plane"),
        home.join("state/control-plane"),
        home.join("state"),
        home.join("control-plane"),
    ] {
        if candidate.exists() {
            return candidate;
        }
    }
    home.join("control_plane")
}

pub fn default_command_deck() -> PathBuf {
    // The development convenience below is compiled out of release builds, and
    // that is not tidiness. `env!("CARGO_MANIFEST_DIR")` is expanded by rustc
    // into an opaque string literal, so `--remap-path-prefix` cannot reach it:
    // MEASURED on the shipped Vibecrafted_4.1.0-20260817-237d2814.dmg, both
    // Contents/MacOS/voc and Contents/MacOS/vc-mux-daemon carried the builder's
    // checkout root through exactly this constant.
    //
    // The leak is the smaller half. On the build host that path EXISTS, so a
    // release binary silently prefers the developer's living checkout over the
    // deck bundled beside it — and the build host is the one machine where the
    // shipped app gets walked around before it goes out. A verification that
    // exercises a code path no customer can reach is worse than no
    // verification.
    #[cfg(debug_assertions)]
    {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_candidate = manifest_dir.join("../scripts/vibecrafted");
        if repo_candidate.exists() {
            return repo_candidate;
        }
    }
    PathBuf::from("vibecrafted")
}

pub fn default_terminal_binary() -> PathBuf {
    env::var_os("VIBECRAFTED_TERMINAL_BINARY")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("vc-frame"))
}

pub fn default_launch_root(command_deck: &Path) -> PathBuf {
    if let Some(value) = env::var_os("VIBECRAFTED_ROOT").filter(|value| !value.is_empty()) {
        return PathBuf::from(value);
    }
    if command_deck.file_name().and_then(|name| name.to_str()) == Some("vibecrafted")
        && command_deck
            .parent()
            .and_then(|parent| parent.file_name())
            .and_then(|name| name.to_str())
            == Some("scripts")
        && let Some(root) = command_deck.parent().and_then(Path::parent)
    {
        return root.to_path_buf();
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn home_dir() -> String {
    env::var("HOME").unwrap_or_else(|_| ".".to_string())
}

fn print_help() {
    println!("Voc Agent");
    println!();
    println!("Usage:");
    println!("  voc [--view observe|full] [--server <url>] [--state-root <dir>] [--root <path>]");
    println!();
    println!("Options:");
    println!("  --view observe|full  Default observe: server-backed live board + AICX memory");
    println!(
        "  --server <url>       Vibecrafted Server origin (default: VC_SERVER_URL or http://127.0.0.1:3024)"
    );
    println!("  --state-root <dir>   Control-plane state root under VIBECRAFTED_HOME");
    println!("  --deck <path>        Command deck binary or script to launch workflows");
    println!("  --root <path>        Workspace root passed through to launched workflows");
    println!("  --runtime <kind>     Launch runtime (headless|terminal|visible)");
    println!(
        "  --terminal-binary <path>  Terminal multiplexer binary (default: vc-frame, vc_frame fallback)"
    );
    println!("  --tick-ms <ms>       Refresh cadence for the TUI (default: 250)");
}

pub fn path_display(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}
