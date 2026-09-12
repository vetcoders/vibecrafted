//! Per-run drill-down reader for `runtime_runs/<run_id>/`.
//!
//! The control-plane runtime writes each dispatched run into
//! `<state_root>/runtime_runs/<run_id>/` with `prompt.md`, `transcript.log`,
//! and — once the run finishes cleanly — `meta.json` + `report.md`. Mission
//! Control's snapshot folds the *fleet-wide* `*.meta.json` history; this module
//! instead reads a *single* run's real artifacts so an operator can drill into
//! what actually happened in the inspector.
//!
//! Missing inputs degrade to typed-empty (no report, empty transcript tail) —
//! never a panic. This mirrors the empty-state discipline of
//! [`crate::mission_control::MissionControlState`].

use serde::Deserialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

/// Max bytes of `report.md` folded into memory. Reports are normally a few KB;
/// the cap protects the inspector from a pathologically large artifact.
const REPORT_BYTE_CAP: u64 = 200 * 1024;

/// Max bytes read from the *tail* of a transcript before line-slicing. The
/// transcript is JSONL and can reach megabytes; we only ever surface the tail,
/// so reading the whole file would be wasteful.
const TRANSCRIPT_TAIL_BYTE_CAP: u64 = 256 * 1024;

/// Number of trailing transcript lines surfaced to the inspector.
const TRANSCRIPT_TAIL_LINES: usize = 200;

/// Real artifacts for a single dispatched run, resolved from
/// `runtime_runs/<run_id>/`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RunDetail {
    pub run_id: String,
    pub agent: Option<String>,
    pub skill: Option<String>,
    /// The run's terminal/working state. Sourced from meta.json `state` when
    /// present, else `status` (the field the dispatch writer actually emits).
    pub state: Option<String>,
    pub report_md: Option<String>,
    pub transcript_tail: String,
    pub report_path: Option<String>,
    pub transcript_path: Option<String>,
}

/// The subset of `runtime_runs/<id>/meta.json` the inspector needs. This is a
/// *different* on-disk schema from the artifact `*.meta.json` parsed by
/// `mission_control::MetaJson` (per-run dispatch meta vs fleet history), so it
/// gets its own focused deserialize shape rather than overloading that one.
#[derive(Debug, Default, Deserialize)]
struct RunMetaJson {
    #[serde(default)]
    agent: Option<String>,
    #[serde(default)]
    skill: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    state: Option<String>,
    #[serde(default)]
    report: Option<String>,
    #[serde(default)]
    transcript: Option<String>,
}

/// Resolve a run's drill-down artifacts under `<state_root>/runtime_runs/<run_id>`.
///
/// `state_root` is the control-plane root (e.g. `config::default_state_root()`
/// => `~/.vibecrafted/control_plane`). `run_id` is treated as a single path
/// component: any separator or parent reference collapses the result to
/// typed-empty so a hostile id cannot escape the `runtime_runs` tree.
#[must_use]
pub fn load_run_detail(state_root: &Path, run_id: &str) -> RunDetail {
    let mut detail = RunDetail {
        run_id: run_id.to_string(),
        ..Default::default()
    };

    if !is_safe_run_id(run_id) {
        return detail;
    }

    let run_dir = state_root.join("runtime_runs").join(run_id);

    // meta.json is optional — early dispatches and in-flight runs may carry
    // only transcript.log. When present it provides agent/skill/state plus the
    // canonical absolute artifact paths.
    let meta = read_meta(&run_dir.join("meta.json"));
    if let Some(meta) = meta.as_ref() {
        detail.agent = meta.agent.clone();
        detail.skill = meta.skill.clone();
        detail.state = meta.state.clone().or_else(|| meta.status.clone());
    }

    // report.md: prefer the meta-declared path, fall back to the conventional
    // sibling, and accept only an existing regular file.
    if let Some(path) = resolve_artifact(
        meta.as_ref().and_then(|m| m.report.as_deref()),
        &run_dir,
        "report.md",
    ) {
        detail.report_md = read_capped(&path, REPORT_BYTE_CAP);
        detail.report_path = Some(path.to_string_lossy().into_owned());
    }

    // Prefer the humanized projection written beside the raw JSONL log.
    let human = run_dir.join("transcript.human.log");
    if human.is_file() {
        detail.transcript_tail = humanize_transcript(&read_tail_lines(&human));
        detail.transcript_path = Some(human.to_string_lossy().into_owned());
    } else if let Some(path) = resolve_artifact(
        meta.as_ref().and_then(|m| m.transcript.as_deref()),
        &run_dir,
        "transcript.log",
    ) {
        detail.transcript_tail = humanize_transcript(&read_tail_lines(&path));
        detail.transcript_path = Some(path.to_string_lossy().into_owned());
    }

    detail
}

/// A run id must be a single, non-traversing path component. Reject anything
/// that could escape `runtime_runs/` or address a parent.
fn is_safe_run_id(run_id: &str) -> bool {
    !run_id.is_empty()
        && !run_id.contains('/')
        && !run_id.contains('\\')
        && !run_id.contains("..")
        && !run_id.contains('\0')
        && run_id != "."
}

/// Pick the artifact path: the meta-declared absolute path if it exists as a
/// regular file, else the conventional sibling under the run dir, else `None`.
fn resolve_artifact(declared: Option<&str>, run_dir: &Path, sibling: &str) -> Option<PathBuf> {
    if let Some(declared) = declared {
        let path = PathBuf::from(declared);
        if path.is_file() {
            return Some(path);
        }
    }
    let candidate = run_dir.join(sibling);
    candidate.is_file().then_some(candidate)
}

fn read_meta(path: &Path) -> Option<RunMetaJson> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// Read at most `cap` bytes from the start of a file as lossy UTF-8. Used for
/// `report.md`, which is small and read head-first.
fn read_capped(path: &Path, cap: u64) -> Option<String> {
    let file = File::open(path).ok()?;
    let mut buf = Vec::new();
    file.take(cap).read_to_end(&mut buf).ok()?;
    Some(String::from_utf8_lossy(&buf).into_owned())
}

/// Read the tail of a (possibly large) transcript and return the last
/// [`TRANSCRIPT_TAIL_LINES`] lines with ANSI escapes stripped. Reads at most
/// [`TRANSCRIPT_TAIL_BYTE_CAP`] bytes from the end so a megabyte JSONL log does
/// not get fully buffered. Returns an empty string on any read failure.
fn read_tail_lines(path: &Path) -> String {
    let Some(raw) = read_tail_bytes(path, TRANSCRIPT_TAIL_BYTE_CAP) else {
        return String::new();
    };
    let text = String::from_utf8_lossy(&raw);
    let mut lines: Vec<&str> = text.lines().collect();
    // When we truncated mid-file the first surviving line is likely a partial
    // record; drop it so the inspector never shows a sliced line.
    if raw.len() as u64 == TRANSCRIPT_TAIL_BYTE_CAP && lines.len() > 1 {
        lines.remove(0);
    }
    let start = lines.len().saturating_sub(TRANSCRIPT_TAIL_LINES);
    lines[start..]
        .iter()
        .map(|line| strip_ansi(line))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Read up to `cap` bytes from the end of `path`.
fn read_tail_bytes(path: &Path, cap: u64) -> Option<Vec<u8>> {
    let mut file = File::open(path).ok()?;
    let len = file.metadata().ok()?.len();
    let start = len.saturating_sub(cap);
    if start > 0 {
        file.seek(SeekFrom::Start(start)).ok()?;
    }
    let mut buf = Vec::new();
    file.read_to_end(&mut buf).ok()?;
    Some(buf)
}

/// Project a raw control-plane transcript (JSONL, escaped shell, ANSI) into
/// wrapped operator-readable lines. Empty input stays empty so callers can
/// render an honest empty state.
pub fn humanize_transcript(text: &str) -> String {
    let mut lines = Vec::new();
    for raw in text.lines() {
        let cleaned = strip_ansi(raw).trim().to_string();
        if cleaned.is_empty() {
            continue;
        }
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&cleaned)
            && let Some(human) = json_line_human(&value)
        {
            lines.extend(wrap_human_line(&human, 88));
            continue;
        }
        if let Some(unescaped) = unescape_json_string(&cleaned) {
            lines.extend(wrap_human_line(&unescaped, 88));
            continue;
        }
        lines.extend(wrap_human_line(&cleaned, 88));
    }
    lines.join("\n")
}

fn json_line_human(value: &serde_json::Value) -> Option<String> {
    for key in ["message", "text", "content", "delta", "body"] {
        if let Some(text) = value.get(key).and_then(serde_json::Value::as_str) {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                return Some(trimmed.to_string());
            }
        }
    }
    if let Some(kind) = value
        .get("kind")
        .or(value.get("type"))
        .and_then(serde_json::Value::as_str)
    {
        if let Some(message) = value.get("event").and_then(serde_json::Value::as_str) {
            return Some(format!("{kind}: {message}"));
        }
        return Some(kind.to_string());
    }
    None
}

fn unescape_json_string(raw: &str) -> Option<String> {
    if !(raw.contains("\\n") || raw.contains("\\\"")) {
        return None;
    }
    let wrapped = format!("\"{raw}\"");
    serde_json::from_str::<String>(&wrapped).ok()
}

fn wrap_human_line(value: &str, width: usize) -> Vec<String> {
    if value.chars().count() <= width {
        return vec![value.to_string()];
    }
    let mut lines = Vec::new();
    let mut current = String::new();
    for ch in value.chars() {
        current.push(ch);
        if current.chars().count() >= width {
            lines.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// Strip ANSI/VT control sequences (CSI `ESC [ … final` and OSC `ESC ] … BEL/ST`)
/// so a terminal-captured transcript renders cleanly in a plain text view.
fn strip_ansi(input: &str) -> String {
    let mut out = String::with_capacity(input.len());
    let mut chars = input.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\u{1b}' {
            out.push(c);
            continue;
        }
        match chars.peek() {
            Some('[') => {
                chars.next();
                // CSI: consume until a final byte in 0x40..=0x7E.
                while let Some(&next) = chars.peek() {
                    chars.next();
                    if ('\u{40}'..='\u{7e}').contains(&next) {
                        break;
                    }
                }
            }
            Some(']') => {
                chars.next();
                // OSC: consume until BEL or the ST terminator (ESC \).
                while let Some(&next) = chars.peek() {
                    if next == '\u{07}' {
                        chars.next();
                        break;
                    }
                    if next == '\u{1b}' {
                        chars.next();
                        if chars.peek() == Some(&'\\') {
                            chars.next();
                        }
                        break;
                    }
                    chars.next();
                }
            }
            // Lone ESC or a 2-byte escape we do not model: drop the ESC only.
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn run_dir(state_root: &Path, run_id: &str) -> PathBuf {
        let dir = state_root.join("runtime_runs").join(run_id);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn reads_meta_report_and_transcript_tail_with_meta_paths() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-1");
        let report = dir.join("report.md");
        let transcript = dir.join("transcript.log");
        fs::write(&report, "# Done\nreal report body").unwrap();
        fs::write(&transcript, "line one\n\u{1b}[32mline two\u{1b}[0m\n").unwrap();
        fs::write(
            dir.join("meta.json"),
            serde_json::json!({
                "agent": "codex",
                "skill": "implement",
                "status": "completed",
                "report": report.to_string_lossy(),
                "transcript": transcript.to_string_lossy(),
            })
            .to_string(),
        )
        .unwrap();

        let detail = load_run_detail(root.path(), "impl-1");

        assert_eq!(detail.run_id, "impl-1");
        assert_eq!(detail.agent.as_deref(), Some("codex"));
        assert_eq!(detail.skill.as_deref(), Some("implement"));
        assert_eq!(detail.state.as_deref(), Some("completed"));
        assert_eq!(
            detail.report_md.as_deref(),
            Some("# Done\nreal report body")
        );
        assert_eq!(
            detail.report_path.as_deref(),
            Some(&*report.to_string_lossy())
        );
        // ANSI stripped, both lines present.
        assert_eq!(detail.transcript_tail, "line one\nline two");
        assert_eq!(
            detail.transcript_path.as_deref(),
            Some(&*transcript.to_string_lossy())
        );
    }

    #[test]
    fn humanize_transcript_projects_jsonl_and_wraps_long_commands() {
        let raw = concat!(
            r#"{"kind":"tool","message":"cargo test -p voc"}"#,
            "\n",
            r#"{"text":"ok"}"#,
            "\n",
            r#"echo \"/very/long/path/to/a/workspace/and/then/some/more/segments/that/should/wrap\""#,
            "\n"
        );
        let human = humanize_transcript(raw);
        assert!(human.contains("cargo test -p voc"));
        assert!(human.contains("ok"));
        assert!(!human.contains("{\"kind\""));
        assert!(human.lines().any(|line| line.chars().count() <= 88));
    }

    #[test]
    fn state_prefers_explicit_state_over_status() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-2");
        fs::write(
            dir.join("meta.json"),
            serde_json::json!({"state": "stalled", "status": "running"}).to_string(),
        )
        .unwrap();

        let detail = load_run_detail(root.path(), "impl-2");
        assert_eq!(detail.state.as_deref(), Some("stalled"));
    }

    #[test]
    fn falls_back_to_sibling_files_without_meta() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-3");
        fs::write(dir.join("transcript.log"), "only a transcript\n").unwrap();
        fs::write(dir.join("report.md"), "sibling report").unwrap();

        let detail = load_run_detail(root.path(), "impl-3");
        assert_eq!(detail.agent, None);
        assert_eq!(detail.report_md.as_deref(), Some("sibling report"));
        assert!(detail.report_path.is_some());
        assert_eq!(detail.transcript_tail, "only a transcript");
    }

    #[test]
    fn missing_run_is_typed_empty_not_a_panic() {
        let root = tempdir().unwrap();
        let detail = load_run_detail(root.path(), "does-not-exist");
        assert_eq!(detail.run_id, "does-not-exist");
        assert_eq!(detail.agent, None);
        assert_eq!(detail.report_md, None);
        assert_eq!(detail.report_path, None);
        assert!(detail.transcript_tail.is_empty());
        assert_eq!(detail.transcript_path, None);
    }

    #[test]
    fn rejects_traversing_run_ids() {
        let root = tempdir().unwrap();
        // Plant a file the traversal would try to reach.
        fs::write(root.path().join("secret"), "nope").unwrap();
        for hostile in ["../secret", "..", "a/b", "", "."] {
            let detail = load_run_detail(root.path(), hostile);
            assert!(detail.report_md.is_none(), "leaked for {hostile:?}");
            assert!(detail.transcript_tail.is_empty(), "leaked for {hostile:?}");
        }
    }

    #[test]
    fn transcript_tail_caps_line_count() {
        let root = tempdir().unwrap();
        let dir = run_dir(root.path(), "impl-4");
        let body: String = (0..500).map(|i| format!("line {i}\n")).collect();
        fs::write(dir.join("transcript.log"), body).unwrap();

        let detail = load_run_detail(root.path(), "impl-4");
        let lines: Vec<&str> = detail.transcript_tail.lines().collect();
        assert_eq!(lines.len(), TRANSCRIPT_TAIL_LINES);
        assert_eq!(*lines.last().unwrap(), "line 499");
    }

    #[test]
    fn strip_ansi_removes_csi_and_osc() {
        assert_eq!(strip_ansi("\u{1b}[1;31mred\u{1b}[0m"), "red");
        assert_eq!(strip_ansi("\u{1b}]0;title\u{07}body"), "body");
        assert_eq!(strip_ansi("plain"), "plain");
    }
}
