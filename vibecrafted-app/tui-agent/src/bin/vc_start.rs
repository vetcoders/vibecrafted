//! Hermetic product entrypoint for the bundled Vibecrafted runtime.
//!
//! This executable deliberately does not read a login shell profile. It locates
//! the runtime carried inside Vibecrafted.app, composes the host-agent PATH
//! (generation bin first, then the known agent-CLI homes, then whatever PATH the
//! caller already had — sanitized, never amputated), sources the shipped shell
//! facade, and enters `vc-start`.
//!
//! PATH composes instead of replacing: AppDelegate hands this process a PATH
//! that keeps the operator's Homebrew/npm/cargo/nvm entries behind the signed
//! generation, and dropping that tail here is exactly what made
//! `#!/usr/bin/env node` agent CLIs exit 127 from the app terminal.

use std::env;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode};

const SYSTEM_PATH_ENTRIES: &[&str] = &[
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
];

fn host_agent_search_path(
    runtime_bin: &Path,
    home: Option<&Path>,
    inherited: Option<&str>,
) -> String {
    let mut entries: Vec<String> = Vec::new();
    let mut push = |path: PathBuf| {
        if path.is_dir() {
            let text = path.display().to_string();
            if !entries.iter().any(|existing| existing == &text) {
                entries.push(text);
            }
        }
    };
    push(runtime_bin.to_path_buf());
    if let Some(home) = home {
        push(home.join(".local/bin"));
        push(home.join(".cargo/bin"));
        push(home.join("tools/scripts"));
    }
    for entry in SYSTEM_PATH_ENTRIES {
        push(PathBuf::from(entry));
    }
    // The caller's PATH survives behind the canon: that is where nvm, pnpm,
    // pipx and hand-rolled ~/bin actually live. Only sane entries cross —
    // empty segments and relative paths (`.`, `bin`) would turn a long-lived
    // agent shell into an implicit-cwd lookup, so they are dropped, not kept.
    for entry in inherited.unwrap_or("").split(':') {
        if entry.is_empty() || !entry.starts_with('/') {
            continue;
        }
        push(PathBuf::from(entry));
    }
    entries.join(":")
}

fn app_root_from_executable(executable: &Path) -> Option<PathBuf> {
    let root = executable.ancestors().nth(5)?;
    (root.extension().is_some_and(|extension| extension == "app")).then(|| root.to_path_buf())
}

fn app_root() -> Result<PathBuf, String> {
    if let Some(explicit) = env::var_os("VIBECRAFTED_APP_ROOT") {
        let root = PathBuf::from(explicit);
        if root.is_absolute() && root.extension().is_some_and(|ext| ext == "app") {
            return Ok(root);
        }
        return Err("VIBECRAFTED_APP_ROOT must be an absolute .app path".into());
    }
    let executable = env::current_exe().map_err(|error| error.to_string())?;
    app_root_from_executable(&executable)
        .ok_or_else(|| "vc-start is not inside Vibecrafted.app".to_string())
}

fn runtime_root(app: Option<&Path>) -> Result<PathBuf, String> {
    if let Some(explicit) = env::var_os("VIBECRAFTED_RUNTIME_ROOT") {
        let root = PathBuf::from(explicit);
        if root.is_absolute() {
            return Ok(root);
        }
        return Err("VIBECRAFTED_RUNTIME_ROOT must be absolute".into());
    }
    let app = app.ok_or_else(|| {
        "vc-start needs VIBECRAFTED_RUNTIME_ROOT outside Vibecrafted.app".to_string()
    })?;
    Ok(app.join("Contents/Resources/runtime"))
}

fn frame_binary(app: Option<&Path>, runtime: &Path) -> Result<PathBuf, String> {
    if let Some(explicit) = env::var_os("VIBECRAFTED_VC_FRAME_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_absolute() {
            return Ok(path);
        }
        return Err("VIBECRAFTED_VC_FRAME_BIN must be absolute".into());
    }
    Ok(app
        .map(|root| root.join("Contents/Helpers/vc-frame"))
        .unwrap_or_else(|| runtime.join("bin/vc-frame")))
}

fn runtime_shell(runtime: &Path) -> PathBuf {
    runtime.join("vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh")
}

fn run() -> Result<(), String> {
    let app = app_root().ok();
    let runtime = runtime_root(app.as_deref())?;
    let shell = runtime_shell(&runtime);
    let frame = frame_binary(app.as_deref(), &runtime)?;
    if !shell.is_file() {
        return Err(format!(
            "bundled runtime shell is missing: {}",
            shell.display()
        ));
    }
    if !frame.is_file() {
        return Err(format!("bundled vc-frame is missing: {}", frame.display()));
    }

    let home = env::var_os("HOME").map(PathBuf::from);
    let inherited_path = env::var("PATH").ok();
    let runtime_path = host_agent_search_path(
        &runtime.join("bin"),
        home.as_deref(),
        inherited_path.as_deref(),
    );
    let mut command = Command::new("/bin/bash");
    command
        .args([
            "--noprofile",
            "--norc",
            "-c",
            r#"source "$1"; shift; vc-start "$@""#,
            "vc-start",
        ])
        .arg(&shell)
        .args(env::args_os().skip(1))
        .env("PATH", runtime_path)
        .env("PYTHONNOUSERSITE", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("VIBECRAFTED_PYTHON", runtime.join("bin/python3"))
        .env("VIBECRAFTED_ROOT", &runtime)
        .env("VIBECRAFTED_RUNTIME_ROOT", &runtime)
        .env("VIBECRAFTED_VC_FRAME_BIN", &frame);

    if let Some(app) = app {
        command.env("VIBECRAFTED_APP_ROOT", app);
    }

    let error = command.exec();
    Err(format!("could not enter bundled vc-start: {error}"))
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("vc-start: {error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_only_the_canonical_bundle_location() {
        let executable =
            Path::new("/Applications/Vibecrafted.app/Contents/Resources/runtime/bin/vc-start");
        assert_eq!(
            app_root_from_executable(executable),
            Some(PathBuf::from("/Applications/Vibecrafted.app"))
        );
        assert_eq!(app_root_from_executable(Path::new("/tmp/vc-start")), None);
    }

    #[test]
    fn host_path_includes_system_bins_and_existing_home_dirs() {
        let runtime = Path::new("/usr/bin");
        let path = host_agent_search_path(runtime, Some(Path::new("/nonexistent-vc-home")), None);
        assert!(path.split(':').any(|entry| entry == "/usr/bin"));
        assert!(path.split(':').any(|entry| entry == "/bin"));
        assert!(!path.contains("/nonexistent-vc-home"));
        assert!(!path.contains("/attacker"));
    }

    #[test]
    fn host_path_keeps_the_callers_tail_behind_the_canon_and_drops_unsafe_entries() {
        // The caller's PATH (nvm, pnpm, ~/bin…) survives behind the canon: that
        // tail is where the operator's agent CLIs actually live. Empty and
        // relative segments are implicit-cwd lookups and never cross.
        let runtime = Path::new("/usr/bin");
        let inherited = format!(":{}::.:bin:/usr/bin:", "/tmp");
        let path = host_agent_search_path(runtime, None, Some(&inherited));
        let entries: Vec<&str> = path.split(':').collect();
        assert!(entries.contains(&"/tmp"));
        assert!(!entries.contains(&""));
        assert!(!entries.contains(&"."));
        assert!(!entries.contains(&"bin"));
        assert_eq!(
            entries.iter().filter(|entry| **entry == "/usr/bin").count(),
            1
        );
        let canon_end = entries.iter().position(|entry| *entry == "/sbin").unwrap();
        let tail = entries.iter().position(|entry| *entry == "/tmp").unwrap();
        assert!(
            tail > canon_end,
            "inherited tail must follow the canon: {path}"
        );
    }

    #[test]
    fn accepts_an_explicit_canonical_runtime_without_an_app() {
        let runtime = Path::new("/tmp/vibecrafted/releases/3.7.1+g12345678");
        assert_eq!(
            runtime.join("bin/vc-frame"),
            PathBuf::from("/tmp/vibecrafted/releases/3.7.1+g12345678/bin/vc-frame")
        );
        assert_eq!(
            runtime_shell(runtime),
            PathBuf::from(
                "/tmp/vibecrafted/releases/3.7.1+g12345678/vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
            )
        );
    }
}
