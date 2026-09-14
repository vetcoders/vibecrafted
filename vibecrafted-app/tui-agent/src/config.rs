use crate::launch::Presentation;
use crate::observe::{self, ConsoleView};
use std::env;
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CliOptions {
    pub state_root: Option<PathBuf>,
    pub command_deck: Option<PathBuf>,
    pub repo: Option<PathBuf>,
    pub presentation: Option<Presentation>,
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
            repo: None,
            presentation: None,
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
    /// The user's repository — the work context every launch declares through
    /// `--repo`. Never the runtime root, the install directory, or the source
    /// tree the binary was compiled from.
    pub repo: PathBuf,
    pub presentation: Presentation,
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
                select_repo(&mut repo_selection, flag, PathBuf::from(value))?;
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
                select_repo(&mut repo_selection, flag, PathBuf::from(value))?;
            }
            "--runtime" => {
                let value = args
                    .next()
                    .ok_or_else(|| anyhow::anyhow!("--runtime requires a value"))?;
                options.presentation = Some(value.parse::<Presentation>()?);
            }
            _ if arg.starts_with("--runtime=") => {
                let value = arg
                    .split_once('=')
                    .map(|(_, value)| value)
                    .unwrap_or_default();
                options.presentation = Some(value.parse::<Presentation>()?);
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
    options.repo = repo_selection.map(|(_, path)| path);
    Ok(options)
}

/// One repository selector for `--repo` (standard) and `--root` (legacy).
///
/// Mirrors `vibecrafted_core.repo_selection.select_repository`: the first
/// selection wins only when every later one names the same path; a different
/// path is a hard conflict naming both flags, never a silent pick.
fn select_repo(
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
        repo: options.repo.unwrap_or_else(default_repo),
        presentation: options.presentation.unwrap_or_default(),
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

/// The user's repository when `--repo` / `--root` were not passed.
///
/// Rust mirror of `vibecrafted_core.runtime_paths.resolve_operator_launch_root`:
/// the working directory wins whenever it sits inside a Git work tree; the
/// selected workspace (`VIBECRAFTED_WORKSPACE_ROOT`) covers the two cases where
/// the working directory answers nothing — the operator's home directory, and a
/// directory outside any repository.
///
/// Deliberately NOT consulted: `VIBECRAFTED_ROOT` (the installed runtime
/// generation), the location of this binary, and the source tree it was built
/// from. All three name Vibecrafted's own install, never the user's work.
pub fn default_repo() -> PathBuf {
    let cwd = env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let home = env::var_os("HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    let workspace = env::var_os("VIBECRAFTED_WORKSPACE_ROOT")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from);
    resolve_repo_from(&cwd, home.as_deref(), workspace.as_deref())
}

/// Pure core of [`default_repo`], so the precedence is testable without
/// mutating process-wide environment state.
pub fn resolve_repo_from(cwd: &Path, home: Option<&Path>, workspace: Option<&Path>) -> PathBuf {
    let here = cwd.canonicalize().unwrap_or_else(|_| cwd.to_path_buf());
    let at_home = home
        .map(|home| home.canonicalize().unwrap_or_else(|_| home.to_path_buf()))
        .is_some_and(|home| home == here);
    if !at_home && git_toplevel(&here).is_some() {
        return here;
    }
    if let Some(workspace) = workspace.filter(|path| path.is_dir()) {
        return workspace
            .canonicalize()
            .unwrap_or_else(|_| workspace.to_path_buf());
    }
    here
}

/// Nearest ancestor holding `.git` (a directory for a normal checkout, a file
/// for a linked worktree). Pure filesystem walk: no subprocess, so a missing
/// `git` binary can never turn repository independence into a crash.
pub fn git_toplevel(start: &Path) -> Option<PathBuf> {
    let mut cursor = Some(start);
    while let Some(path) = cursor {
        if path.join(".git").exists() {
            return Some(path.to_path_buf());
        }
        cursor = path.parent();
    }
    None
}

/// True when `repo` is the operator's home directory — never a useful launch
/// context. Mirrors `runtime_paths.is_operator_home_root`, so VOC refuses the
/// same declaration the launcher would refuse.
pub fn is_operator_home_root(repo: &Path) -> bool {
    let Some(home) = env::var_os("HOME").filter(|value| !value.is_empty()) else {
        return false;
    };
    let home = PathBuf::from(home);
    let home = home.canonicalize().unwrap_or(home);
    let repo = repo.canonicalize().unwrap_or_else(|_| repo.to_path_buf());
    repo == home
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
    println!("  --deck <path>        Canonical vibecrafted launcher used for every launch");
    println!(
        "  --repo <path>        Repository (workspace root) declared to the launcher as --repo"
    );
    println!(
        "  --root <path>        Legacy spelling of --repo; both with different paths is an error"
    );
    println!("  --runtime <kind>     Presentation of launched workers (headless|terminal)");
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
        assert_eq!(repo.repo, Some(PathBuf::from("/tmp/app")));
        let root = parse(&["--root", "/tmp/app"]).unwrap();
        assert_eq!(root.repo, Some(PathBuf::from("/tmp/app")));
        let inline = parse(&["--repo=/tmp/app with space"]).unwrap();
        assert_eq!(inline.repo, Some(PathBuf::from("/tmp/app with space")));
        assert_eq!(parse(&[]).unwrap().repo, None);
    }

    #[test]
    fn same_path_spelled_twice_is_accepted() {
        let both = parse(&["--repo", "/tmp/app", "--root", "/tmp/app"]).unwrap();
        assert_eq!(both.repo, Some(PathBuf::from("/tmp/app")));
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
        assert_eq!(mixed.repo, Some(dir));
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
        // The private vc-frame selector is gone with VOC's own session
        // creation: sessions belong to the canonical launcher.
        assert!(
            parse(&["--terminal-binary", "/opt/vc-frame"])
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
        assert_eq!(options.repo, Some(PathBuf::from("/tmp/app")));
        assert_eq!(options.tick_ms, 500);
        assert!(options.no_verify_gate);
        assert_eq!(options.presentation, Some(Presentation::Headless));
    }

    #[test]
    fn repo_resolution_prefers_the_git_checkout_over_the_selected_workspace() {
        let temp = tempfile::tempdir().unwrap();
        let repo = temp.path().join("repo");
        let nested = repo.join("crate/src");
        let workspace = temp.path().join("workspace");
        let home = temp.path().join("home");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::create_dir_all(repo.join(".git")).unwrap();
        std::fs::create_dir_all(&workspace).unwrap();
        std::fs::create_dir_all(&home).unwrap();

        // From a subdirectory of a checkout the working tree still wins.
        assert_eq!(
            resolve_repo_from(&nested, Some(&home), Some(&workspace)),
            nested.canonicalize().unwrap()
        );
        // Outside any repository the selected workspace answers.
        let outside = temp.path().join("elsewhere");
        std::fs::create_dir_all(&outside).unwrap();
        assert_eq!(
            resolve_repo_from(&outside, Some(&home), Some(&workspace)),
            workspace.canonicalize().unwrap()
        );
        // At home the workspace wins even if home happens to be a checkout.
        std::fs::create_dir_all(home.join(".git")).unwrap();
        assert_eq!(
            resolve_repo_from(&home, Some(&home), Some(&workspace)),
            workspace.canonicalize().unwrap()
        );
        // With no workspace to fall back on, the honest answer is the cwd.
        assert_eq!(
            resolve_repo_from(&outside, Some(&home), None),
            outside.canonicalize().unwrap()
        );
    }

    #[test]
    fn repo_resolution_ignores_the_installed_runtime_root() {
        // VIBECRAFTED_ROOT names the installed generation, never the user's
        // work; a resolver that consulted it would launch agents against
        // Vibecrafted's own install directory.
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path().join("releases/4.3.1");
        let cwd = temp.path().join("cwd");
        std::fs::create_dir_all(&runtime).unwrap();
        std::fs::create_dir_all(&cwd).unwrap();
        let resolved = resolve_repo_from(&cwd, None, None);
        assert_ne!(resolved, runtime);
        assert_eq!(resolved, cwd.canonicalize().unwrap());
    }

    #[test]
    fn git_toplevel_finds_plain_checkouts_and_linked_worktrees() {
        let temp = tempfile::tempdir().unwrap();
        let checkout = temp.path().join("checkout");
        let deep = checkout.join("a/b/c");
        std::fs::create_dir_all(&deep).unwrap();
        std::fs::create_dir_all(checkout.join(".git")).unwrap();
        assert_eq!(git_toplevel(&deep), Some(checkout.clone()));

        // A linked worktree carries `.git` as a FILE, not a directory.
        let linked = temp.path().join("linked");
        std::fs::create_dir_all(linked.join("src")).unwrap();
        std::fs::write(linked.join(".git"), "gitdir: /elsewhere\n").unwrap();
        assert_eq!(git_toplevel(&linked.join("src")), Some(linked));

        let bare = temp.path().join("bare");
        std::fs::create_dir_all(&bare).unwrap();
        assert_eq!(git_toplevel(&bare), None);
    }
}
