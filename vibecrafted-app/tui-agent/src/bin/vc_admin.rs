use anyhow::Context;
use clap::{Parser, Subcommand};
use std::fmt::Write as _;
use std::io::{self, IsTerminal};
use std::path::PathBuf;
use std::thread;
use std::time::Duration;
use voc::mission_control::default_artifact_root;
use voc::state::ControlPlaneState;
use voc::{
    ActionQueueItem, ActiveDispatch, AgentStatsRow, DataQuality, FailureEntry, FleetHealthSignal,
    MissionControlState, SettlementBoardCounts, SkillStatsRow, WaveSegment,
};

#[derive(Debug, Parser)]
#[command(
    name = "vc-admin",
    about = "Standalone Mission Control snapshot renderer",
    arg_required_else_help = false
)]
struct Cli {
    #[arg(long, value_name = "DIR", global = true)]
    state_root: Option<PathBuf>,

    #[arg(long, value_name = "DIR", global = true)]
    artifact_root: Option<PathBuf>,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Render all Mission Control panels.
    Status,
    /// Render the wave atlas panel.
    Wave,
    /// Render per-agent stats, optionally filtered by agent name.
    Agent { name: Option<String> },
    /// Render per-skill stats, optionally filtered by skill name.
    Skill { name: Option<String> },
    /// Render the failure board.
    Failures {
        #[arg(long, default_value = "24h")]
        since: String,
    },
    /// Render the operator action queue.
    Button,
    /// Render fleet health.
    Health,
    /// Re-render Mission Control on a polling cadence.
    Watch {
        #[arg(long, default_value_t = 2)]
        interval_secs: u64,
    },
}

struct MissionSnapshot {
    state_root: PathBuf,
    artifact_root: PathBuf,
    state: MissionControlState,
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command.as_ref().unwrap_or(&Command::Status) {
        Command::Watch { interval_secs } => watch(&cli, *interval_secs),
        command => {
            let snapshot = load_snapshot(&cli)?;
            print!("{}", render_command(command, &snapshot));
            Ok(())
        }
    }
}

fn watch(cli: &Cli, interval_secs: u64) -> anyhow::Result<()> {
    if !io::stdout().is_terminal() {
        let snapshot = load_snapshot(cli)?;
        print!("{}", render_status(&snapshot));
        println!("watch requested in non-TTY mode; printed one snapshot and stopped.");
        return Ok(());
    }

    let interval = Duration::from_secs(interval_secs.max(1));
    loop {
        let snapshot = load_snapshot(cli)?;
        print!("\x1b[2J\x1b[H");
        print!("{}", render_status(&snapshot));
        println!(
            "watching every {}s; press Ctrl-C to stop",
            interval.as_secs()
        );
        thread::sleep(interval);
    }
}

fn load_snapshot(cli: &Cli) -> anyhow::Result<MissionSnapshot> {
    let state_root = cli
        .state_root
        .clone()
        .unwrap_or_else(voc::config::default_state_root);
    let artifact_root = cli
        .artifact_root
        .clone()
        .unwrap_or_else(default_artifact_root);
    let control_plane = ControlPlaneState::load(&state_root).with_context(|| {
        format!(
            "failed to load control-plane state from {}",
            state_root.display()
        )
    })?;
    let state = MissionControlState::build(&control_plane, &artifact_root);

    Ok(MissionSnapshot {
        state_root,
        artifact_root,
        state,
    })
}

fn render_command(command: &Command, snapshot: &MissionSnapshot) -> String {
    match command {
        Command::Status => render_status(snapshot),
        Command::Wave => render_single(
            snapshot,
            render_wave_atlas("Wave atlas", &snapshot.state.wave_atlas),
        ),
        Command::Agent { name } => {
            let rows = filter_by(&snapshot.state.agent_stats, name.as_deref(), |row| {
                row.agent.as_str()
            });
            render_single(snapshot, render_agent_stats("Per-agent stats", &rows))
        }
        Command::Skill { name } => {
            let rows = filter_by(&snapshot.state.skill_stats, name.as_deref(), |row| {
                row.skill.as_str()
            });
            render_single(snapshot, render_skill_stats("Per-skill stats", &rows))
        }
        Command::Failures { since } => {
            let mut body = render_failure_board(
                &format!("Failure board ({since})"),
                &snapshot.state.failures,
            );
            if since != "24h" {
                body.push_str(
                    "note: MissionControlState currently exposes the canonical 24h failure window.\n\n",
                );
            }
            render_single(snapshot, body)
        }
        Command::Button => render_single(
            snapshot,
            render_action_queue("Operator action queue", &snapshot.state.action_queue),
        ),
        Command::Health => render_single(
            snapshot,
            render_fleet_health("Fleet health", &snapshot.state.fleet_health),
        ),
        Command::Watch { .. } => render_status(snapshot),
    }
}

fn render_status(snapshot: &MissionSnapshot) -> String {
    let mut out = header(snapshot);
    out.push_str(&render_settlement_board(&snapshot.state.settlement));
    out.push_str(&render_active_dispatches(
        "Active dispatches",
        &snapshot.state.active_dispatches,
    ));
    out.push_str(&render_wave_atlas("Wave atlas", &snapshot.state.wave_atlas));
    out.push_str(&render_agent_stats(
        "Per-agent stats",
        &snapshot.state.agent_stats,
    ));
    out.push_str(&render_skill_stats(
        "Per-skill stats",
        &snapshot.state.skill_stats,
    ));
    out.push_str(&render_fleet_health(
        "Fleet health",
        &snapshot.state.fleet_health,
    ));
    out.push_str(&render_failure_board(
        "Failure board 24h",
        &snapshot.state.failures,
    ));
    out.push_str(&render_action_queue(
        "Operator action queue",
        &snapshot.state.action_queue,
    ));
    out.push_str(&render_quality_footer(
        &snapshot.state.data_quality,
        &snapshot.state.generated_at,
    ));
    out
}

fn render_single(snapshot: &MissionSnapshot, body: String) -> String {
    let mut out = header(snapshot);
    out.push_str(&body);
    out.push_str(&render_quality_footer(
        &snapshot.state.data_quality,
        &snapshot.state.generated_at,
    ));
    out
}

fn header(snapshot: &MissionSnapshot) -> String {
    let mut out = String::new();
    writeln!(out, "Mission Control").unwrap();
    writeln!(out, "control plane: {}", snapshot.state_root.display()).unwrap();
    writeln!(out, "artifact root: {}", snapshot.artifact_root.display()).unwrap();
    writeln!(out).unwrap();
    out
}

fn render_settlement_board(board: &SettlementBoardCounts) -> String {
    let mut out = section_title("Settlement board", board.total_settled);
    out.push_str(&board.render_strip());
    out.push('\n');
    out.push_str(
        "note: f/x/n reads settlement_verdict on retained snapshots only.\n\n",
    );
    out
}

fn render_active_dispatches(title: &str, items: &[ActiveDispatch]) -> String {
    let mut out = section_title(title, items.len());
    if items.is_empty() {
        out.push_str("no live dispatches\n\n");
        return out;
    }
    writeln!(
        out,
        "{:<28} {:<10} {:<16} {:<18} {:<12} ETA",
        "RUN_ID", "AGENT", "SKILL", "WAVE", "AGE"
    )
    .unwrap();
    for item in items {
        writeln!(
            out,
            "{:<28} {:<10} {:<16} {:<18} {:<12} {}",
            clip(&item.run_id, 28),
            clip(&item.agent, 10),
            clip(&item.skill, 16),
            clip(item.wave.as_deref().unwrap_or("-"), 18),
            clip(&item.age_label, 12),
            item.eta_label
        )
        .unwrap();
    }
    out.push('\n');
    out
}

fn render_wave_atlas(title: &str, segments: &[WaveSegment]) -> String {
    let mut out = section_title(title, segments.len());
    if segments.is_empty() {
        out.push_str("no waves in the last 30d\n\n");
        return out;
    }
    writeln!(
        out,
        "{:<14} {:<34} {:>5} {:>9} {:>7} {:>7}",
        "STATE", "WAVE", "TOTAL", "COMPLETE", "FAILED", "ACTIVE"
    )
    .unwrap();
    for segment in segments {
        writeln!(
            out,
            "{:<14} {:<34} {:>5} {:>9} {:>7} {:>7}",
            segment.latest_state.label(),
            clip(&segment.wave_id, 34),
            segment.total,
            segment.completed,
            segment.failed,
            segment.active
        )
        .unwrap();
    }
    out.push('\n');
    out
}

fn render_agent_stats(title: &str, rows: &[AgentStatsRow]) -> String {
    let mut out = section_title(title, rows.len());
    if rows.is_empty() {
        out.push_str("no agent activity in window\n\n");
        return out;
    }
    writeln!(
        out,
        "{:<14} {:>5} {:>9} {:>7} {:>8} {:>8} AVG_DUR",
        "AGENT", "RUNS", "COMPLETE", "FAILED", "SUCCESS", "MODEL"
    )
    .unwrap();
    for row in rows {
        writeln!(
            out,
            "{:<14} {:>5} {:>9} {:>7} {:>7}% {:>7}% {}",
            clip(&row.agent, 14),
            row.total_runs,
            row.completed,
            row.failed,
            pct(row.success_rate),
            pct(row.model_known_rate),
            row.avg_duration_s
                .map(format_duration_seconds)
                .unwrap_or_else(|| "-".to_string())
        )
        .unwrap();
    }
    out.push('\n');
    out
}

fn render_skill_stats(title: &str, rows: &[SkillStatsRow]) -> String {
    let mut out = section_title(title, rows.len());
    if rows.is_empty() {
        out.push_str("no skill invocations in window\n\n");
        return out;
    }
    writeln!(
        out,
        "{:<22} {:>5} {:>9} {:>7} AVG_DUR",
        "SKILL", "INV", "COMPLETE", "FAILED"
    )
    .unwrap();
    for row in rows {
        writeln!(
            out,
            "{:<22} {:>5} {:>9} {:>7} {}",
            clip(&row.skill, 22),
            row.invocations,
            row.completed,
            row.failed,
            row.avg_duration_s
                .map(format_duration_seconds)
                .unwrap_or_else(|| "-".to_string())
        )
        .unwrap();
    }
    out.push('\n');
    out
}

fn render_fleet_health(title: &str, signals: &[FleetHealthSignal]) -> String {
    let mut out = section_title(title, signals.len());
    if signals.is_empty() {
        out.push_str("fleet not probed\n\n");
        return out;
    }
    writeln!(out, "{:<9} {:<18} DETAIL", "STATUS", "SIGNAL").unwrap();
    for signal in signals {
        writeln!(
            out,
            "{:<9} {:<18} {}",
            signal.status.marker(),
            clip(&signal.label, 18),
            signal.detail
        )
        .unwrap();
    }
    out.push('\n');
    out
}

fn render_failure_board(title: &str, entries: &[FailureEntry]) -> String {
    let mut out = section_title(title, entries.len());
    if entries.is_empty() {
        out.push_str("no failures in window\n\n");
        return out;
    }
    writeln!(
        out,
        "{:<28} {:<10} {:<18} {:<12} REASON",
        "RUN_ID", "AGENT", "SKILL", "AGE"
    )
    .unwrap();
    for entry in entries {
        writeln!(
            out,
            "{:<28} {:<10} {:<18} {:<12} {}",
            clip(&entry.run_id, 28),
            clip(&entry.agent, 10),
            clip(&entry.skill, 18),
            clip(&entry.age_label, 12),
            entry.reason
        )
        .unwrap();
        if let Some(path) = &entry.source_path
            && !path.as_os_str().is_empty()
        {
            writeln!(out, "{:<28} source: {}", "", path.display()).unwrap();
        }
    }
    out.push('\n');
    out
}

fn render_action_queue(title: &str, items: &[ActionQueueItem]) -> String {
    let mut out = section_title(title, items.len());
    if items.is_empty() {
        out.push_str("nothing to press\n\n");
        return out;
    }
    writeln!(out, "{:<8} {:<10} SUMMARY", "PRIORITY", "KIND").unwrap();
    for item in items {
        writeln!(
            out,
            "{:<8} {:<10} {}",
            item.priority.marker(),
            item.kind.label(),
            item.summary
        )
        .unwrap();
        if let Some(path) = &item.source_path
            && !path.as_os_str().is_empty()
        {
            writeln!(out, "{:<8} {:<10} source: {}", "", "", path.display()).unwrap();
        }
    }
    out.push('\n');
    out
}

fn render_quality_footer(quality: &DataQuality, generated_at: &str) -> String {
    let mut out = section_title("DataQuality", 0);
    writeln!(
        out,
        "artifact root present: {}",
        yes_no(quality.artifact_root_present)
    )
    .unwrap();
    if let Some(root) = &quality.artifact_root {
        writeln!(out, "artifact root: {}", root.display()).unwrap();
    } else {
        writeln!(out, "artifact root: unset").unwrap();
    }
    writeln!(out, "generated at: {generated_at}").unwrap();
    writeln!(out, "derived runs scanned: {}", quality.scanned_meta_files).unwrap();
    writeln!(out, "scan capped: {}", yes_no(quality.capped)).unwrap();
    writeln!(out, "missing model: {}", quality.missing_model).unwrap();
    writeln!(out, "missing duration: {}", quality.missing_duration).unwrap();
    writeln!(out, "parse failures: {}", quality.parse_failures).unwrap();
    out.push('\n');
    out
}

fn section_title(title: &str, count: usize) -> String {
    let mut out = String::new();
    if title == "DataQuality" {
        writeln!(out, "== {title} ==").unwrap();
    } else {
        writeln!(out, "== {title} ({count}) ==").unwrap();
    }
    out
}

fn filter_by<T: Clone>(rows: &[T], needle: Option<&str>, label: impl Fn(&T) -> &str) -> Vec<T> {
    let Some(needle) = needle else {
        return rows.to_vec();
    };
    let needle = needle.to_ascii_lowercase();
    rows.iter()
        .filter(|row| label(row).to_ascii_lowercase().contains(&needle))
        .cloned()
        .collect()
}

fn pct(value: f32) -> i32 {
    (value * 100.0).round() as i32
}

fn yes_no(value: bool) -> &'static str {
    if value { "yes" } else { "no" }
}

fn clip(value: &str, max: usize) -> String {
    if value.chars().count() <= max {
        return value.to_string();
    }
    let prefix_len = max.saturating_sub(3);
    let mut out: String = value.chars().take(prefix_len).collect();
    out.push_str("...");
    out
}

fn format_duration_seconds(seconds: f64) -> String {
    if seconds < 60.0 {
        format!("{seconds:.0}s")
    } else if seconds < 3600.0 {
        format!("{:.1}m", seconds / 60.0)
    } else {
        format!("{:.1}h", seconds / 3600.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clip_keeps_short_values_unchanged() {
        assert_eq!(clip("codex", 8), "codex");
    }

    #[test]
    fn clip_truncates_with_ascii_ellipsis() {
        assert_eq!(clip("mission-control", 10), "mission...");
    }
}
