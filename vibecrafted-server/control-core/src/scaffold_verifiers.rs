//! Execute brief delivery-verifiers during scaffold-doctor (fail point C5).
//!
//! Plan format is unchanged: commands live in each brief's Gates section and
//! in Acceptance `verifier:` suffixes — the same surfaces R8 already reads.
//! Doctor now *runs* those commands (or a bounded probe) on the baseline
//! checkout and refuses the package when a brief has no runnable probe that
//! returns an exit code and prints a today value.
//!
//! Mutation-probe is out of scope for v1.

use std::collections::BTreeSet;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use crate::scaffold::{
    ScaffoldArtifactDeclaration, ScaffoldArtifactRole, ScaffoldDoctorError, ScaffoldManifest,
};

const RULE: &str = "R8";
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(10);
const MIN_TIMEOUT: Duration = Duration::from_millis(50);
const MAX_TIMEOUT: Duration = Duration::from_secs(30);
const OUTPUT_CAP: usize = 8 * 1024;
const MAX_COMMANDS_PER_BRIEF: usize = 16;

const COMMAND_STARTERS: &[&str] = &[
    "python3",
    "python",
    "cargo",
    "make",
    "bash",
    "sh",
    "loct",
    "git",
    "uv",
    "pytest",
    "vibecrafted",
    "env",
    "find",
    "tar",
    "jq",
    "wc",
    "echo",
    "printf",
    "awk",
    "sed",
    "rg",
    "grep",
    "pre-commit",
    "ruff",
    "mypy",
    "semgrep",
    "true",
    "false",
    "test",
    "ls",
    "cat",
    "sha256sum",
    "shasum",
    "diff",
    "curl",
    "wget",
    "hdiutil",
    "npm",
    "pnpm",
    "node",
];

/// Append doctor errors for briefs whose listed verifiers are missing, unsafe,
/// unrunnable, silent, or timed out.
pub fn execute_brief_verifiers(
    plan_root: &Path,
    manifest: &ScaffoldManifest,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let mut saw_brief = false;
    for artifact in &manifest.artifacts {
        if artifact.role != ScaffoldArtifactRole::Brief {
            continue;
        }
        saw_brief = true;
        inspect_brief(plan_root, artifact, errors);
    }
    if !saw_brief {
        errors.push(doctor_error(
            "verifier_missing",
            None,
            None,
            "plan package has no brief with a delivery-verifier to execute",
        ));
    }
}

/// Pull shell commands from a brief's Gates / Acceptance `verifier:` fields.
#[must_use]
pub fn extract_brief_verifier_commands(content: &str) -> Vec<String> {
    let mut commands = Vec::new();
    if let Some(gates) = section_body(content, "gates") {
        extract_from_gates(&gates, &mut commands);
    }
    if let Some(acceptance) = section_body(content, "acceptance") {
        extract_verifier_suffixes(&acceptance, &mut commands);
    }
    dedup_preserve(commands)
}

fn inspect_brief(
    plan_root: &Path,
    artifact: &ScaffoldArtifactDeclaration,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let path = plan_root.join(&artifact.path);
    let Ok(content) = std::fs::read_to_string(&path) else {
        return;
    };
    let commands = extract_brief_verifier_commands(&content);
    if commands.is_empty() {
        errors.push(doctor_error(
            "verifier_missing",
            Some(artifact.id.as_str()),
            Some(artifact.path.as_str()),
            "brief has no runnable verifier command (Gates fence / verifier: line); word-only gates are malformed",
        ));
        return;
    }

    for command in commands.into_iter().take(MAX_COMMANDS_PER_BRIEF) {
        if let Some(reason) = unsafe_reason(&command) {
            errors.push(doctor_error(
                "verifier_unsafe",
                Some(artifact.id.as_str()),
                Some(artifact.path.as_str()),
                &format!("refusing to execute verifier ({reason}): {command}"),
            ));
            continue;
        }
        let bound = bound_command(&command);
        match run_probe(&bound, cwd_for(&bound, plan_root)) {
            ProbeOutcome::Ok => {}
            ProbeOutcome::NoToday { exit_code } => {
                errors.push(doctor_error(
                    "verifier_no_today",
                    Some(artifact.id.as_str()),
                    Some(artifact.path.as_str()),
                    &format!("verifier exited {exit_code} but printed no today value: {bound}"),
                ));
            }
            ProbeOutcome::Unrunnable { detail } => {
                errors.push(doctor_error(
                    "verifier_unrunnable",
                    Some(artifact.id.as_str()),
                    Some(artifact.path.as_str()),
                    &format!("verifier did not run ({detail}): {bound}"),
                ));
            }
            ProbeOutcome::Timeout => {
                errors.push(doctor_error(
                    "verifier_timeout",
                    Some(artifact.id.as_str()),
                    Some(artifact.path.as_str()),
                    &format!("verifier timed out: {bound}"),
                ));
            }
        }
    }
}

fn extract_from_gates(gates: &str, commands: &mut Vec<String>) {
    let mut outside = String::new();
    let mut in_fence = false;
    for line in gates.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with("```") {
            in_fence = !in_fence;
            continue;
        }
        if in_fence {
            push_command_line(trimmed, commands);
        } else {
            outside.push_str(line);
            outside.push('\n');
        }
    }
    extract_backticks(&outside, commands);
}

fn extract_verifier_suffixes(acceptance: &str, commands: &mut Vec<String>) {
    for line in acceptance.lines() {
        let lower = line.to_ascii_lowercase();
        let Some(index) = lower.find("verifier:") else {
            continue;
        };
        let rest = line[index + "verifier:".len()..].trim();
        let before = commands.len();
        extract_backticks(rest, commands);
        if commands.len() == before {
            push_command_line(rest, commands);
        }
    }
}

fn extract_backticks(text: &str, commands: &mut Vec<String>) {
    let mut rest = text;
    while let Some(start) = rest.find('`') {
        rest = &rest[start + 1..];
        let Some(end) = rest.find('`') else {
            break;
        };
        let inner = rest[..end].trim();
        push_command_line(inner, commands);
        rest = &rest[end + 1..];
    }
}

fn push_command_line(line: &str, commands: &mut Vec<String>) {
    let stripped = strip_hash_comment(line).trim();
    if stripped.is_empty() || !looks_like_command(stripped) {
        return;
    }
    commands.push(stripped.to_string());
}

fn looks_like_command(command: &str) -> bool {
    let first = first_exec_token(command);
    if first.is_empty() {
        return false;
    }
    if first.starts_with('/') || first.starts_with("./") {
        return true;
    }
    let name = first.rsplit('/').next().unwrap_or(first);
    COMMAND_STARTERS.contains(&name)
}

fn first_exec_token(command: &str) -> &str {
    for token in command.split_whitespace() {
        if token.contains('=') && !token.starts_with('-') && !token.starts_with('/') {
            continue;
        }
        return token;
    }
    ""
}

fn strip_hash_comment(line: &str) -> &str {
    if let Some(index) = line.find(" #") {
        &line[..index]
    } else {
        line
    }
}

fn bound_command(raw: &str) -> String {
    let mut command = raw.trim().to_string();
    if is_pytest(&command) && !command.contains("--collect-only") {
        command.push_str(" --collect-only -q");
    }
    if is_cargo_test(&command) && !command.contains("--no-run") && !command.contains("--list") {
        command.push_str(" --offline --no-run");
    }
    insert_make_dry_run(&command)
}

fn is_pytest(command: &str) -> bool {
    let lower = command.to_ascii_lowercase();
    lower.contains("pytest")
}

fn is_cargo_test(command: &str) -> bool {
    let lower = command.to_ascii_lowercase();
    lower.contains("cargo test")
}

fn insert_make_dry_run(command: &str) -> String {
    let tokens: Vec<&str> = command.split_whitespace().collect();
    let Some(position) = tokens.iter().position(|token| *token == "make") else {
        return command.to_string();
    };
    let next = tokens.get(position + 1).copied().unwrap_or("");
    if next == "-n" || next == "--dry-run" || next == "--just-print" {
        return command.to_string();
    }
    let mut rewritten = String::new();
    for (index, token) in tokens.iter().enumerate() {
        if index > 0 {
            rewritten.push(' ');
        }
        rewritten.push_str(token);
        if index == position {
            rewritten.push_str(" -n");
        }
    }
    // Preserve `&&` / `||` / `;` that split_whitespace dropped by falling
    // back to a targeted replace when the token stream lost operators.
    if command.contains("&&") || command.contains("||") || command.contains(';') {
        return command.replacen("make ", "make -n ", 1);
    }
    rewritten
}

fn unsafe_reason(command: &str) -> Option<&'static str> {
    for segment in command.split(['&', '|', ';']) {
        let lower = segment.to_ascii_lowercase();
        let token = first_exec_token(segment.trim());
        let name = token.rsplit('/').next().unwrap_or(token);
        if matches!(
            name,
            "curl" | "wget" | "nc" | "nmap" | "ssh" | "scp" | "sftp"
        ) {
            return Some("network");
        }
        if lower.contains("hdiutil attach") || lower.contains("hdiutil detach") {
            return Some("mount");
        }
        if lower.contains("rm -rf") || lower.contains("rm -fr") || lower.contains("mkfs") {
            return Some("destructive");
        }
        if lower.contains("sudo ") || name == "sudo" {
            return Some("privilege");
        }
        if lower.contains("git push")
            || lower.contains("git reset --hard")
            || lower.contains("git rebase")
        {
            return Some("git-write");
        }
        if lower.contains("vibecrafted implement")
            || lower.contains("vibecrafted dispatch")
            || lower.contains("vibecrafted scaffold")
        {
            return Some("launcher");
        }
    }
    None
}

fn cwd_for(command: &str, plan_root: &Path) -> PathBuf {
    if let Ok(root) = std::env::var("SCAFFOLD_VERIFIER_CWD") {
        let path = PathBuf::from(root);
        if path.is_dir() {
            return path;
        }
    }
    if needs_repo(command) {
        if let Ok(root) = std::env::var("VIBECRAFTED_REPO_ROOT") {
            let path = PathBuf::from(root);
            if path.is_dir() {
                return path;
            }
        }
        if let Some(git) = nearest_git(std::env::current_dir().ok().as_deref()) {
            return git;
        }
        if let Some(git) = nearest_git(Some(plan_root)) {
            return git;
        }
    }
    plan_root.to_path_buf()
}

fn needs_repo(command: &str) -> bool {
    let lower = command.to_ascii_lowercase();
    lower.contains("make ")
        || lower.contains("cargo ")
        || lower.contains("pytest")
        || lower.contains("loct ")
        || lower.contains("git ")
        || lower.contains("uv ")
        || lower.contains("semgrep")
        || lower.contains("mypy")
}

fn nearest_git(start: Option<&Path>) -> Option<PathBuf> {
    let mut current = start?.to_path_buf();
    loop {
        if current.join(".git").exists() {
            return Some(current);
        }
        if !current.pop() {
            return None;
        }
    }
}

enum ProbeOutcome {
    Ok,
    NoToday { exit_code: i32 },
    Unrunnable { detail: String },
    Timeout,
}

fn run_probe(command: &str, cwd: PathBuf) -> ProbeOutcome {
    let timeout = probe_timeout();
    let mut process = Command::new("sh");
    process
        .arg("-c")
        .arg(command)
        .current_dir(&cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("CARGO_NET_OFFLINE", "true")
        .env("UV_OFFLINE", "1")
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("TERM", "dumb");
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        process.process_group(0);
    }
    let mut child = match process.spawn() {
        Ok(child) => child,
        Err(error) => {
            return ProbeOutcome::Unrunnable {
                detail: error.to_string(),
            };
        }
    };
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let exit_code = status.code().unwrap_or(128);
                let stdout = read_capped(child.stdout.take());
                let stderr = read_capped(child.stderr.take());
                let output = format!("{stdout}{stderr}");
                if is_unrunnable(exit_code, &output) {
                    return ProbeOutcome::Unrunnable {
                        detail: first_line(&output)
                            .unwrap_or("command not found")
                            .to_string(),
                    };
                }
                return match today_value(&stdout, &stderr) {
                    Some(_) => ProbeOutcome::Ok,
                    None => ProbeOutcome::NoToday { exit_code },
                };
            }
            Ok(None) if started.elapsed() > timeout => {
                kill_probe(&mut child);
                return ProbeOutcome::Timeout;
            }
            Ok(None) => thread::sleep(Duration::from_millis(20)),
            Err(error) => {
                kill_probe(&mut child);
                return ProbeOutcome::Unrunnable {
                    detail: error.to_string(),
                };
            }
        }
    }
}

fn probe_timeout() -> Duration {
    std::env::var("SCAFFOLD_VERIFIER_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .map(Duration::from_millis)
        .map(|duration| duration.clamp(MIN_TIMEOUT, MAX_TIMEOUT))
        .unwrap_or(DEFAULT_TIMEOUT)
}

fn kill_probe(child: &mut std::process::Child) {
    #[cfg(unix)]
    {
        let pid = child.id() as i32;
        // SAFETY: `process_group(0)` made this pid the leader of a new group
        // that contains only the probe shell and its descendants.
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn read_capped(stream: Option<impl Read>) -> String {
    let Some(mut stream) = stream else {
        return String::new();
    };
    let mut buf = Vec::new();
    let mut chunk = [0_u8; 512];
    while buf.len() < OUTPUT_CAP {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(read) => {
                let take = read.min(OUTPUT_CAP - buf.len());
                buf.extend_from_slice(&chunk[..take]);
            }
            Err(_) => break,
        }
    }
    String::from_utf8_lossy(&buf).into_owned()
}

fn is_unrunnable(exit_code: i32, output: &str) -> bool {
    if exit_code == 127 {
        return true;
    }
    let lower = output.to_ascii_lowercase();
    lower.contains("command not found")
}

fn today_value(stdout: &str, stderr: &str) -> Option<String> {
    let combined = format!("{stdout}\n{stderr}");
    for line in combined.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let lower = trimmed.to_ascii_lowercase();
        if let Some(value) = lower
            .strip_prefix("today:")
            .or_else(|| lower.strip_prefix("today="))
            .or_else(|| lower.strip_prefix("today "))
        {
            let original = trimmed
                .get(trimmed.len() - value.len()..)
                .unwrap_or(value)
                .trim();
            if !original.is_empty() {
                return Some(original.to_owned());
            }
        }
    }
    first_line(stdout)
        .or_else(|| first_line(stderr))
        .map(str::to_string)
}

fn first_line(text: &str) -> Option<&str> {
    text.lines().map(str::trim).find(|line| !line.is_empty())
}

fn section_body(content: &str, needle: &str) -> Option<String> {
    let needle = needle.to_ascii_lowercase();
    let lines: Vec<&str> = content.lines().collect();
    let mut start = None;
    for (index, line) in lines.iter().enumerate() {
        let trimmed = line.trim().to_ascii_lowercase();
        if trimmed.starts_with('#') && trimmed.contains(&needle) {
            start = Some(index + 1);
            break;
        }
    }
    let start = start?;
    let mut body = Vec::new();
    for line in lines.iter().skip(start) {
        let trimmed = line.trim();
        if trimmed.starts_with("## ") {
            break;
        }
        body.push(*line);
    }
    Some(body.join("\n"))
}

fn dedup_preserve(commands: Vec<String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut unique = Vec::new();
    for command in commands {
        if seen.insert(command.clone()) {
            unique.push(command);
        }
    }
    unique
}

fn doctor_error(
    code: &str,
    artifact_id: Option<&str>,
    path: Option<&str>,
    message: &str,
) -> ScaffoldDoctorError {
    ScaffoldDoctorError {
        code: code.into(),
        rule: Some(RULE.into()),
        artifact_id: artifact_id.map(str::to_string),
        path: path.map(str::to_string),
        message: message.into(),
    }
}
