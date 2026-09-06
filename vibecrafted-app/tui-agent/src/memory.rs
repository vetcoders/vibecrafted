//! AICX memory pane for `voc`.
//!
//! `aicx wizard` stays the interactive search surface. This pane is the
//! always-on continuity strip so observation and intent live in one console.

#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

/// PATH for the `aicx` subprocess: absolute inherited entries, minus the
/// generation `bin/` that would shadow the Founder's own `aicx` (Homebrew /
/// upstream). Vibecrafted is a guest — the user's PATH install wins.
pub(crate) fn user_tool_path(inherited: &str, runtime_root: Option<&str>) -> String {
    let skipped_bins: Vec<String> = runtime_root
        .into_iter()
        .flat_map(|root| [format!("{root}/bin"), format!("{root}/libexec")])
        .collect();
    let mut entries: Vec<&str> = Vec::new();
    for entry in inherited
        .split(':')
        .filter(|entry| !entry.is_empty() && entry.starts_with('/'))
        .filter(|entry| {
            skipped_bins
                .iter()
                .all(|skip| Path::new(skip) != Path::new(entry))
        })
    {
        if !entries.contains(&entry) {
            entries.push(entry);
        }
    }
    if entries.is_empty() {
        return "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin".to_string();
    }
    entries.join(":")
}

fn sane_tool_path() -> String {
    user_tool_path(
        &std::env::var("PATH").unwrap_or_default(),
        std::env::var("VIBECRAFTED_RUNTIME_ROOT")
            .ok()
            .or_else(|| std::env::var("VIBECRAFTED_ROOT").ok())
            .as_deref(),
    )
}

fn resolve_on_path(name: &str, path: &str) -> PathBuf {
    for dir in path.split(':') {
        let candidate = Path::new(dir).join(name);
        if is_executable_file(&candidate) {
            return candidate;
        }
    }
    PathBuf::from(name)
}

fn is_executable_file(path: &Path) -> bool {
    let Ok(metadata) = path.metadata() else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    #[cfg(unix)]
    return metadata.permissions().mode() & 0o111 != 0;
    #[cfg(not(unix))]
    true
}

fn user_command(name: &str) -> Command {
    let path = sane_tool_path();
    let mut command = Command::new(resolve_on_path(name, &path));
    command.env("PATH", path);
    command
}

fn aicx() -> Command {
    user_command("aicx")
}

pub(crate) fn parse_owner_repo(url: &str) -> Option<String> {
    let trimmed = url.trim().trim_end_matches('/').trim_end_matches(".git");
    let stripped = trimmed
        .strip_prefix("ssh://")
        .or_else(|| trimmed.strip_prefix("git+ssh://"))
        .or_else(|| trimmed.strip_prefix("https://"))
        .or_else(|| trimmed.strip_prefix("http://"))
        .or_else(|| trimmed.strip_prefix("git@"))
        .unwrap_or(trimmed);
    let normalized = stripped.replace(':', "/");
    let mut parts = normalized.rsplit('/');
    let repo = parts.next()?.trim();
    let owner = parts.next()?.trim();
    if repo.is_empty() || owner.is_empty() || owner.contains('.') {
        return None;
    }
    Some(format!("{}/{}", owner.to_lowercase(), repo.to_lowercase()))
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryState {
    pub project: String,
    pub lines: Vec<String>,
    pub error: Option<String>,
}

fn git_owner_repo(launch_root: &Path) -> Option<String> {
    let output = user_command("git")
        .args(["-C", launch_root.to_str()?, "remote", "get-url", "origin"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    parse_owner_repo(&String::from_utf8_lossy(&output.stdout))
}

pub fn default_project(launch_root: &Path) -> String {
    if let Some(slug) = git_owner_repo(launch_root) {
        return slug;
    }
    launch_root
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| format!("/{name}"))
        .unwrap_or_else(|| "/vibecrafted".to_string())
}

pub fn load_continuity(project: &str) -> MemoryState {
    let output = aicx()
        .args(["continuity", "show", "-p", project, "-H", "24"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output();

    match output {
        Ok(result) if result.status.success() => {
            let text = String::from_utf8_lossy(&result.stdout);
            let lines: Vec<String> = text
                .lines()
                .filter(|line| !line.trim().is_empty())
                .take(80)
                .map(ToOwned::to_owned)
                .collect();
            MemoryState {
                project: project.to_string(),
                lines: if lines.is_empty() {
                    vec!["no continuity in the last 24h".to_string()]
                } else {
                    lines
                },
                error: None,
            }
        }
        Ok(result) => MemoryState {
            project: project.to_string(),
            lines: Vec::new(),
            error: Some(
                String::from_utf8_lossy(&result.stderr)
                    .lines()
                    .next()
                    .unwrap_or("aicx continuity failed")
                    .to_string(),
            ),
        },
        Err(error) => MemoryState {
            project: project.to_string(),
            lines: Vec::new(),
            error: Some(format!("aicx not available: {error}")),
        },
    }
}

pub fn launch_wizard(project: &str, launch_root: &Path) -> anyhow::Result<()> {
    let mut command = aicx();
    command
        .args(["wizard", "--view", "search", "-p", project])
        .current_dir(launch_root)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::piped());
    #[cfg(unix)]
    {
        if let (Ok(input), Ok(output)) = (
            std::fs::OpenOptions::new().read(true).open("/dev/tty"),
            std::fs::OpenOptions::new().write(true).open("/dev/tty"),
        ) {
            command.stdin(input).stdout(output);
        }
    }
    wait_for_wizard(&mut command)
}

fn wait_for_wizard(command: &mut Command) -> anyhow::Result<()> {
    let output = command
        .output()
        .map_err(|error| anyhow::anyhow!("aicx wizard failed to spawn ({error})"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let detail = stderr
            .lines()
            .rev()
            .find(|line| !line.trim().is_empty())
            .unwrap_or("no stderr");
        anyhow::bail!("aicx wizard exited {}: {detail}", output.status);
    }
    Ok(())
}

pub fn wizard_hint() -> &'static str {
    "w  aicx wizard   ·   m  refresh memory   ·   server is the donor"
}

#[allow(dead_code)]
const _REFRESH_HINT: Duration = Duration::from_secs(30);

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn user_tool_path_skips_generation_bin() {
        let path = user_tool_path("/gen/bin:/opt/homebrew/bin:/usr/bin", Some("/gen"));
        assert_eq!(path, "/opt/homebrew/bin:/usr/bin");
        assert!(!path.split(':').any(|entry| entry == "/gen/bin"));
    }

    #[test]
    fn resolve_on_path_picks_first_real_file() {
        let dir = tempdir().unwrap();
        let shadow = dir.path().join("shadow");
        let real = dir.path().join("real");
        fs::create_dir_all(&shadow).unwrap();
        fs::create_dir_all(&real).unwrap();
        let shadow_bin = shadow.join("aicx");
        let real_bin = real.join("aicx");
        fs::write(&shadow_bin, "#!/bin/sh\n").unwrap();
        fs::write(&real_bin, "#!/bin/sh\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&real_bin, fs::Permissions::from_mode(0o755)).unwrap();
        }
        let joined = format!("{}:{}", shadow.display(), real.display());
        assert_eq!(resolve_on_path("aicx", &joined), real_bin);
        assert!(!is_executable_file(&shadow_bin));
    }

    #[test]
    fn parse_owner_repo_from_https_and_ssh() {
        assert_eq!(
            parse_owner_repo("https://github.com/vetcoders/vibecrafted.git"),
            Some("vetcoders/vibecrafted".into())
        );
        assert_eq!(
            parse_owner_repo("git@github.com:Vetcoders/vibecrafted.git"),
            Some("vetcoders/vibecrafted".into())
        );
        assert_eq!(parse_owner_repo("https://github.com/vibecrafted"), None);
    }

    #[test]
    fn default_project_falls_back_to_slash_repo_name() {
        let dir = tempdir().unwrap();
        let root = dir.path().join("vibecrafted");
        fs::create_dir(&root).unwrap();
        assert_eq!(default_project(&root), "/vibecrafted");
    }

    #[cfg(unix)]
    #[test]
    fn wizard_wait_drains_large_stderr_before_reporting_failure() {
        let mut command = Command::new("/bin/sh");
        command
            .args([
                "-c",
                "i=0; while [ $i -lt 20000 ]; do printf 'diagnostic payload %05d................................\\n' \"$i\" >&2; i=$((i + 1)); done; printf 'final diagnostic\\n' >&2; exit 23",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        let error = wait_for_wizard(&mut command).unwrap_err().to_string();
        assert!(error.contains("exit status: 23"), "{error}");
        assert!(error.contains("final diagnostic"), "{error}");
    }
}
