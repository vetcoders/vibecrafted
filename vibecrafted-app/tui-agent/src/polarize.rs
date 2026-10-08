use anyhow::Context;
use serde::Deserialize;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::SystemTime;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PolarizeBand {
    Abort,
    Memo,
    Pass,
    Doctrine,
}

impl PolarizeBand {
    pub fn from_score(score: u8) -> Self {
        match score {
            0..=4 => PolarizeBand::Abort,
            5..=8 => PolarizeBand::Memo,
            9..=12 => PolarizeBand::Pass,
            _ => PolarizeBand::Doctrine,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            PolarizeBand::Abort => "abort",
            PolarizeBand::Memo => "memo",
            PolarizeBand::Pass => "pass",
            PolarizeBand::Doctrine => "doctrine",
        }
    }

    pub fn recommended_action(self) -> &'static str {
        match self {
            PolarizeBand::Abort => "abort before agent dispatch",
            PolarizeBand::Memo => "memo only",
            PolarizeBand::Pass => "full polarize pass",
            PolarizeBand::Doctrine => "doctrine pass with regression contract",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolarizeIntent {
    pub band: PolarizeBand,
    pub score: u8,
    pub run_id: String,
    pub prism_path: PathBuf,
}

impl PolarizeIntent {
    pub fn summary_line(&self) -> String {
        format!(
            "{} score {} run {} -> {} ({})",
            self.band.label(),
            self.score,
            self.run_id,
            self.band.recommended_action(),
            self.prism_path.to_string_lossy()
        )
    }
}

#[derive(Debug, Deserialize)]
struct PrismPayload {
    #[serde(default)]
    total_score: Option<u64>,
    #[serde(default)]
    score: Option<u64>,
    #[serde(default)]
    run_id: Option<String>,
    #[serde(default)]
    band: Option<String>,
    #[serde(default)]
    band_action: Option<String>,
}

pub fn current_intents(launch_root: &Path) -> Vec<PolarizeIntent> {
    let mut intents = current_intents_from_home(&vibecrafted_home(), launch_root);
    intents.truncate(8);
    intents
}

pub fn current_intents_from_home(home: &Path, _launch_root: &Path) -> Vec<PolarizeIntent> {
    let mut rows = discover_prism_files(home)
        .into_iter()
        .filter_map(|path| read_intent(&path).ok().map(|intent| (mtime(&path), intent)))
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| right.1.run_id.cmp(&left.1.run_id))
    });
    rows.into_iter().map(|(_, intent)| intent).collect()
}

pub fn read_intent(path: &Path) -> anyhow::Result<PolarizeIntent> {
    let path = safe_prism_file(path)?;
    let text = fs::read_to_string(&path)
        .with_context(|| format!("failed to read prism payload {}", path.display()))?;
    let payload: PrismPayload = serde_json::from_str(&text)
        .with_context(|| format!("failed to parse prism payload {}", path.display()))?;
    let score = payload.total_score.or(payload.score).unwrap_or(0).min(15) as u8;
    let band = payload
        .band_action
        .as_deref()
        .and_then(parse_band)
        .or_else(|| payload.band.as_deref().and_then(parse_band))
        .unwrap_or_else(|| PolarizeBand::from_score(score));
    let run_id = payload
        .run_id
        .filter(|value| !value.trim().is_empty())
        .or_else(|| {
            path.parent()
                .and_then(Path::file_name)
                .and_then(|value| value.to_str())
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| "unknown".to_string());
    Ok(PolarizeIntent {
        band,
        score,
        run_id,
        prism_path: path,
    })
}

pub fn prism_preview_lines(path: &Path) -> anyhow::Result<Vec<String>> {
    let path = safe_prism_file(path)?;
    let text = fs::read_to_string(&path)
        .with_context(|| format!("failed to read prism payload {}", path.display()))?;
    let mut lines = text
        .lines()
        .take(400)
        .map(ToOwned::to_owned)
        .collect::<Vec<_>>();
    if text.lines().count() > 400 {
        lines.push("[truncated after 400 lines]".to_string());
    }
    Ok(lines)
}

fn discover_prism_files(home: &Path) -> Vec<PathBuf> {
    let artifacts = home.join("artifacts");
    let mut files = Vec::new();
    collect_prisms(&artifacts, 0, &mut files);
    files.sort();
    files
}

fn collect_prisms(path: &Path, depth: usize, files: &mut Vec<PathBuf>) {
    if depth > 6 || !is_plain_dir(path) {
        return;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return;
    };
    for entry in entries.flatten() {
        let entry_path = entry.path();
        if entry_path.file_name().and_then(|value| value.to_str()) == Some("prism.json") {
            if let Ok(path) = safe_prism_file(&entry_path) {
                files.push(path);
            }
            continue;
        }
        if is_plain_dir(&entry_path) {
            collect_prisms(&entry_path, depth + 1, files);
        }
    }
}

fn is_plain_dir(path: &Path) -> bool {
    fs::symlink_metadata(path)
        .map(|meta| meta.is_dir() && !meta.file_type().is_symlink())
        .unwrap_or(false)
}

fn safe_prism_file(path: &Path) -> anyhow::Result<PathBuf> {
    if path.file_name().and_then(|value| value.to_str()) != Some("prism.json") {
        anyhow::bail!("refusing non-prism payload {}", path.display());
    }
    let meta = fs::symlink_metadata(path)
        .with_context(|| format!("failed to inspect prism payload {}", path.display()))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        anyhow::bail!("refusing unsafe prism payload {}", path.display());
    }
    let canonical = fs::canonicalize(path)
        .with_context(|| format!("failed to canonicalize prism payload {}", path.display()))?;
    if !is_polarize_prism(&canonical) {
        anyhow::bail!(
            "refusing non-polarize prism payload {}",
            canonical.display()
        );
    }
    Ok(canonical)
}

fn is_polarize_prism(path: &Path) -> bool {
    path.parent()
        .and_then(Path::parent)
        .and_then(Path::file_name)
        .and_then(|value| value.to_str())
        == Some("polarize")
}

fn mtime(path: &Path) -> SystemTime {
    fs::metadata(path)
        .and_then(|meta| meta.modified())
        .unwrap_or(SystemTime::UNIX_EPOCH)
}

fn parse_band(raw: &str) -> Option<PolarizeBand> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "abort" => Some(PolarizeBand::Abort),
        "memo" => Some(PolarizeBand::Memo),
        "pass" => Some(PolarizeBand::Pass),
        "doctrine" => Some(PolarizeBand::Doctrine),
        _ => None,
    }
}

pub fn vibecrafted_home() -> PathBuf {
    if let Some(home) = env::var_os("VIBECRAFTED_HOME").filter(|value| !value.is_empty()) {
        return PathBuf::from(home);
    }
    env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".vibecrafted")
}
