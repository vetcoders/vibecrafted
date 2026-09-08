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
    parse_args_from(env::args().skip(1))
}

/// Parse the public `voc` argument vector (process name already stripped).
///
/// `--repo <path>` is the standard repository selector shared with every
/// repository-aware `vibecrafted` command; `--root <path>` is the legacy
/// spelling with identical semantics. Passing both with different paths is
/// an error (never a silent pick); the same path spelled twice is accepted.
pub fn parse_args_from<I>(args: I) -> anyhow::Result<CliOptions>
where
    I: IntoIterator<Item = String>,
{
    let mut options = CliOptions::default();
    let mut args = args.into_iter();
    let mut repo_selection: Option<(&'static str, PathBuf)> = None;
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
            "--repo" | "--root" => {
                let flag: &'static str = if arg == "--repo" { "--repo" } else { "--root" };
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("{flag} requires a value"))?;
                select_launch_root(&mut repo_selection, flag, PathBuf::from(value))?;
            }
            _ if arg.starts_with("--repo=") || arg.starts_with("--root=") => {
                let (flag, value) = arg
                    .split_once('=')
                    .map(|(flag, value)| (flag.to_string(), value.to_string()))
                    .unwrap_or_default();
                let flag: &'static str = if flag == "--repo" { "--repo" } else { "--root" };
                if value.is_empty() {
                    anyhow::bail!("{flag} requires a value");
                }
                select_launch_root(&mut repo_selection, flag, PathBuf::from(value))?;
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
    options.launch_root = repo_selection.map(|(_, path)| path);
    Ok(options)
}

/// One repository selector for `--repo` (standard) and `--root` (legacy).
///
/// Mirrors `vibecrafted_core.repo_selection.select_repository`: the first
/// selection wins only when every later one names the same path; a different
/// path is a hard conflict naming both flags, never a silent pick.
fn select_launch_root(
    selection: &mut Option<(&'static str, PathBuf)>,
    flag: &'static str,
    path: PathBuf,
) -> anyhow::Result<()> {
    if let Some((previous_flag, previous)) = selection {
        if same_repository(previous, &path) {
            return Ok(());
        }
        if *previous_flag == flag {
            anyhow::bail!(
                "{flag} passed twice with different paths: {} and {}; pass one repository",
                path_display(previous),
                path_display(&path)
            );
        }
        let (repo, root) = if flag == "--repo" {
            (&path, &*previous)
        } else {
            (&*previous, &path)
        };
        anyhow::bail!(
            "conflicting --repo {} and --root {}; pass one repository",
            path_display(repo),
            path_display(root)
        );
    }
    *selection = Some((flag, path));
    Ok(())
}

/// Two spellings of one repository: lexically equal, or canonicalizing to
/// the same existing directory (`repo` vs `repo/.` vs a symlinked twin).
fn same_repository(left: &Path, right: &Path) -> bool {
    if left == right {
        return true;
    }
    match (left.canonicalize(), right.canonicalize()) {
        (Ok(left), Ok(right)) => left == right,
        _ => false,
    }
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
    println!("  voc [--view observe|full] [--server <url>] [--state-root <dir>] [--repo <path>]");
    println!();
    println!("Options:");
    println!("  --view observe|full  Default observe: server-backed live board + AICX memory");
    println!(
        "  --server <url>       Vibecrafted Server origin (default: VC_SERVER_URL or http://127.0.0.1:3024)"
    );
    println!("  --state-root <dir>   Control-plane state root under VIBECRAFTED_HOME");
    println!("  --deck <path>        Command deck binary or script to launch workflows");
    println!(
        "  --repo <path>        Repository (workspace root) passed through to launched workflows"
    );
    println!(
        "  --root <path>        Legacy spelling of --repo; both with different paths is an error"
    );
    println!("  --runtime <kind>     Launch runtime (headless|terminal|visible)");
    println!(
        "  --terminal-binary <path>  Terminal multiplexer binary (default: vc-frame, vc_frame fallback)"
    );
    println!("  --tick-ms <ms>       Refresh cadence for the TUI (default: 250)");
}

pub fn path_display(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> anyhow::Result<CliOptions> {
        parse_args_from(args.iter().map(|arg| arg.to_string()))
    }

    #[test]
    fn repo_is_the_standard_selector_and_root_the_legacy_spelling() {
        let repo = parse(&["--repo", "/tmp/app"]).unwrap();
        assert_eq!(repo.launch_root, Some(PathBuf::from("/tmp/app")));
        let root = parse(&["--root", "/tmp/app"]).unwrap();
        assert_eq!(root.launch_root, Some(PathBuf::from("/tmp/app")));
        let inline = parse(&["--repo=/tmp/app with space"]).unwrap();
        assert_eq!(
            inline.launch_root,
            Some(PathBuf::from("/tmp/app with space"))
        );
        assert_eq!(parse(&[]).unwrap().launch_root, None);
    }

    #[test]
    fn same_path_spelled_twice_is_accepted() {
        let both = parse(&["--repo", "/tmp/app", "--root", "/tmp/app"]).unwrap();
        assert_eq!(both.launch_root, Some(PathBuf::from("/tmp/app")));
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path().to_path_buf();
        let dotted = dir.join(".");
        let mixed = parse(&[
            "--repo",
            dir.to_str().unwrap(),
            "--root",
            dotted.to_str().unwrap(),
        ])
        .unwrap();
        assert_eq!(mixed.launch_root, Some(dir));
    }

    #[test]
    fn conflicting_repo_and_root_is_an_error_naming_both_flags() {
        for argv in [
            ["--repo", "/tmp/left", "--root", "/tmp/right"],
            ["--root", "/tmp/right", "--repo", "/tmp/left"],
        ] {
            let error = parse(&argv).unwrap_err().to_string();
            assert_eq!(
                error,
                "conflicting --repo /tmp/left and --root /tmp/right; pass one repository"
            );
        }
        let twice = parse(&["--repo", "/tmp/a", "--repo=/tmp/b"])
            .unwrap_err()
            .to_string();
        assert!(twice.contains("--repo passed twice"), "{twice}");
    }

    #[test]
    fn missing_values_and_unknown_flags_still_fail_closed() {
        assert_eq!(
            parse(&["--repo"]).unwrap_err().to_string(),
            "--repo requires a value"
        );
        assert_eq!(
            parse(&["--repo="]).unwrap_err().to_string(),
            "--repo requires a value"
        );
        assert_eq!(
            parse(&["--root"]).unwrap_err().to_string(),
            "--root requires a value"
        );
        assert!(
            parse(&["--repository", "/tmp/x"])
                .unwrap_err()
                .to_string()
                .starts_with("unknown argument")
        );
    }

    #[test]
    fn other_flags_keep_their_behaviour_next_to_repo() {
        let options = parse(&[
            "--repo",
            "/tmp/app",
            "--tick-ms",
            "500",
            "--no-verify-gate",
            "--runtime",
            "headless",
        ])
        .unwrap();
        assert_eq!(options.launch_root, Some(PathBuf::from("/tmp/app")));
        assert_eq!(options.tick_ms, 500);
        assert!(options.no_verify_gate);
        assert_eq!(options.launch_runtime, Some(LaunchRuntime::Headless));
    }
}
