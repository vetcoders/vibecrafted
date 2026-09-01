//! Process snapshot model for vc-procs (no kill policy here).

use std::time::Instant;

/// Family tag for fleet display (not authorization).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FamilyTag {
    AicxMcp,
    LoctreeMcp,
    RmcpMux,
    Grok,
    Codex,
    Claude,
    Cursor,
    Agy,
    Junie,
    Mlx,
    Stt,
    Ollama,
    RustMemex,
    Vibecrafted,
    Other,
}

impl FamilyTag {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AicxMcp => "aicx-mcp",
            Self::LoctreeMcp => "loctree-mcp",
            Self::RmcpMux => "rmcp-mux",
            Self::Grok => "grok",
            Self::Codex => "codex",
            Self::Claude => "claude",
            Self::Cursor => "cursor",
            Self::Agy => "agy",
            Self::Junie => "junie",
            Self::Mlx => "mlx",
            Self::Stt => "stt",
            Self::Ollama => "ollama",
            Self::RustMemex => "rust-memex",
            Self::Vibecrafted => "vibecrafted",
            Self::Other => "other",
        }
    }

    pub fn classify(name: &str, cmd: &str) -> Self {
        let blob = format!("{} {}", name, cmd).to_lowercase();
        if blob.contains("aicx-mcp") {
            Self::AicxMcp
        } else if blob.contains("loctree-mcp") {
            Self::LoctreeMcp
        } else if blob.contains("rmcp-mux") {
            Self::RmcpMux
        } else if blob.contains("rust-memex") {
            Self::RustMemex
        } else if blob.contains("lbrx-stt") || blob.contains("stt-engine") {
            Self::Stt
        } else if blob.contains("mlx") {
            Self::Mlx
        } else if blob.contains("ollama") {
            Self::Ollama
        } else if blob.contains("codex") {
            Self::Codex
        } else if blob.contains("claude") {
            Self::Claude
        } else if blob.contains("agy") {
            Self::Agy
        } else if blob.contains("junie") {
            Self::Junie
        } else if blob.contains("grok") {
            Self::Grok
        } else if blob.contains("cursor") {
            Self::Cursor
        } else if blob.contains("vibecrafted") || blob.contains(".vibecrafted") {
            Self::Vibecrafted
        } else {
            Self::Other
        }
    }
}

#[derive(Debug, Clone)]
pub struct ProcessRow {
    pub pid: u32,
    pub ppid: u32,
    pub name: String,
    pub command: String,
    pub cpu: f32,
    pub rss: u64,
    pub family: FamilyTag,
    /// Stable selection key: pid + command prefix.
    pub identity: String,
}

impl ProcessRow {
    pub fn truncated_cmd(&self, max: usize) -> String {
        if self.command.chars().count() <= max {
            return self.command.clone();
        }
        let take = max.saturating_sub(1);
        format!("{}…", self.command.chars().take(take).collect::<String>())
    }
}

#[derive(Debug, Clone)]
pub struct FamilyAggregate {
    pub family: FamilyTag,
    pub count: usize,
    pub cpu: f32,
    pub rss: u64,
}

#[derive(Debug, Clone)]
pub struct MonitorSnapshot {
    pub system_cpu_percent: f32,
    pub system_ram_used: u64,
    pub system_ram_total: u64,
    pub self_cpu: f32,
    pub self_rss: u64,
    pub gpu_util_percent: Option<f32>,
    pub gpu_memory_used: Option<u64>,
    pub gpu_memory_total: Option<u64>,
    pub gpu_status: String,
    pub families: Vec<FamilyAggregate>,
    pub processes: Vec<ProcessRow>,
    pub sampled_at: Instant,
}

impl Default for MonitorSnapshot {
    fn default() -> Self {
        Self {
            system_cpu_percent: 0.0,
            system_ram_used: 0,
            system_ram_total: 0,
            self_cpu: 0.0,
            self_rss: 0,
            gpu_util_percent: None,
            gpu_memory_used: None,
            gpu_memory_total: None,
            gpu_status: "not sampled".into(),
            families: Vec::new(),
            processes: Vec::new(),
            sampled_at: Instant::now(),
        }
    }
}

pub fn format_bytes(bytes: u64) -> String {
    const KB: f64 = 1024.0;
    const MB: f64 = KB * 1024.0;
    const GB: f64 = MB * 1024.0;
    match bytes {
        0..=1023 => format!("{bytes} B"),
        1_024..=1_048_575 => format!("{:.0} KB", bytes as f64 / KB),
        1_048_576..=1_073_741_823 => format!("{:.0} MB", bytes as f64 / MB),
        _ => format!("{:.1} GB", bytes as f64 / GB),
    }
}

pub fn sort_by_rss(rows: &mut [ProcessRow]) {
    rows.sort_by(|a, b| {
        b.rss
            .cmp(&a.rss)
            .then_with(|| a.pid.cmp(&b.pid))
            .then_with(|| a.command.cmp(&b.command))
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn format_bytes_boundaries() {
        assert_eq!(format_bytes(0), "0 B");
        assert_eq!(format_bytes(1023), "1023 B");
        assert_eq!(format_bytes(1024), "1 KB");
        assert_eq!(format_bytes(1_048_576), "1 MB");
        assert!(format_bytes(1_073_741_824).contains("GB"));
    }

    #[test]
    fn classify_families() {
        assert_eq!(
            FamilyTag::classify("aicx-mcp", "/usr/bin/aicx-mcp serve"),
            FamilyTag::AicxMcp
        );
        assert_eq!(
            FamilyTag::classify("node", "codex exec --json"),
            FamilyTag::Codex
        );
        assert_eq!(
            FamilyTag::classify("cursor-agent", "cursor-agent -p --output-format stream-json"),
            FamilyTag::Cursor
        );
        assert_eq!(FamilyTag::classify("bash", "ls"), FamilyTag::Other);
    }

    #[test]
    fn sort_rss_desc_pid_tiebreak() {
        let mut rows = vec![
            ProcessRow {
                pid: 2,
                ppid: 1,
                name: "a".into(),
                command: "a".into(),
                cpu: 0.0,
                rss: 100,
                family: FamilyTag::Other,
                identity: "2".into(),
            },
            ProcessRow {
                pid: 1,
                ppid: 0,
                name: "b".into(),
                command: "b".into(),
                cpu: 0.0,
                rss: 100,
                family: FamilyTag::Other,
                identity: "1".into(),
            },
            ProcessRow {
                pid: 3,
                ppid: 1,
                name: "c".into(),
                command: "c".into(),
                cpu: 0.0,
                rss: 200,
                family: FamilyTag::Other,
                identity: "3".into(),
            },
        ];
        sort_by_rss(&mut rows);
        assert_eq!(rows[0].pid, 3);
        assert_eq!(rows[1].pid, 1);
        assert_eq!(rows[2].pid, 2);
    }
}
