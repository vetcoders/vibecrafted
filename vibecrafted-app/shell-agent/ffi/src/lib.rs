uniffi::setup_scaffolding!();

use std::path::PathBuf;
use std::sync::OnceLock;
use tray_agent::ipc_client::{ClientKind, MuxControlCommand, MuxControlResponse, send_command};
use voc::config::{default_state_root, default_vibecrafted_home};
use voc::mission_control::{self as mc, MissionControlState};
use voc::run_detail::{self, RunDetail};
use voc::state::ControlPlaneState;

static SOCKET_PATH: OnceLock<PathBuf> = OnceLock::new();

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum MuxError {
    #[error("{msg}")]
    Core { msg: String },
}

impl From<anyhow::Error> for MuxError {
    fn from(e: anyhow::Error) -> Self {
        MuxError::Core {
            msg: format!("{e:#}"),
        }
    }
}

#[derive(uniffi::Enum)]
pub enum FfiClientKind {
    Claude,
    Codex,
    Gemini,
    Junie,
    Cursor,
    Generic { name: String },
}

impl From<ClientKind> for FfiClientKind {
    fn from(k: ClientKind) -> Self {
        match k {
            ClientKind::Claude => FfiClientKind::Claude,
            ClientKind::Codex => FfiClientKind::Codex,
            ClientKind::Gemini => FfiClientKind::Gemini,
            ClientKind::Junie => FfiClientKind::Junie,
            ClientKind::Cursor => FfiClientKind::Cursor,
            ClientKind::Generic { name } => FfiClientKind::Generic { name },
        }
    }
}

impl From<FfiClientKind> for ClientKind {
    fn from(k: FfiClientKind) -> Self {
        match k {
            FfiClientKind::Claude => ClientKind::Claude,
            FfiClientKind::Codex => ClientKind::Codex,
            FfiClientKind::Gemini => ClientKind::Gemini,
            FfiClientKind::Junie => ClientKind::Junie,
            FfiClientKind::Cursor => ClientKind::Cursor,
            FfiClientKind::Generic { name } => ClientKind::Generic { name },
        }
    }
}

#[derive(uniffi::Record)]
pub struct FfiServerStatus {
    pub name: String,
    pub status: String,
    pub queue_depth: u32,
    pub queue_capacity: u32,
    pub restart_count: u64,
}

#[derive(uniffi::Record)]
pub struct FfiRoute {
    pub client: FfiClientKind,
    pub service: String,
    pub state: String,
}

#[derive(uniffi::Record)]
pub struct FfiClientConfig {
    pub kind: FfiClientKind,
    pub config: String,
}

#[derive(uniffi::Record)]
pub struct FfiVerifyResult {
    pub kind: FfiClientKind,
    pub ok: bool,
    pub detail: String,
}

#[derive(uniffi::Record)]
pub struct FfiNonMuxEntry {
    pub name: String,
}

#[derive(uniffi::Enum)]
pub enum FfiSubscriberState {
    Connected,
    Disconnected,
}

// ═══════════════════════════════════════════════════════════
// PLAN_22 Operator Snapshot — Mission Control parity
//
// Cross-surface contract that lets shell-side (Swift) read the same
// dashboard truth the TUI Mission Control tab renders. Records mirror
// `voc::mission_control::*` without duplicating the
// aggregation logic — see `load_mission_control_snapshot()` for the
// single typed boundary.
// ═══════════════════════════════════════════════════════════

#[derive(uniffi::Enum)]
pub enum FfiWaveState {
    Pending,
    InProgress,
    Completed,
    Failed,
}

#[derive(uniffi::Enum)]
pub enum FfiFleetHealthStatus {
    Ok,
    Warn,
    Blocked,
    Unknown,
}

#[derive(uniffi::Enum)]
pub enum FfiActionQueueKind {
    StalledRun,
    Failure,
    Polarize,
    ReportReady,
}

#[derive(uniffi::Enum)]
pub enum FfiActionPriority {
    Critical,
    High,
    Normal,
}

#[derive(uniffi::Record)]
pub struct FfiActiveDispatch {
    pub run_id: String,
    pub agent: String,
    pub skill: String,
    pub wave: Option<String>,
    pub started_at: Option<String>,
    pub age_label: String,
    pub eta_label: String,
}

#[derive(uniffi::Record)]
pub struct FfiWaveSegment {
    pub wave_id: String,
    pub total: u32,
    pub completed: u32,
    pub failed: u32,
    pub active: u32,
    pub latest_state: FfiWaveState,
}

#[derive(uniffi::Record)]
pub struct FfiAgentStatsRow {
    pub agent: String,
    pub total_runs: u32,
    pub completed: u32,
    pub failed: u32,
    pub success_rate: f32,
    /// Average duration in seconds. None when no upstream meta.json
    /// carried `duration_s`; the operator surface should render an
    /// explicit `—` rather than zero.
    pub avg_duration_s: Option<f64>,
    pub model_known_rate: f32,
}

#[derive(uniffi::Record)]
pub struct FfiSkillStatsRow {
    pub skill: String,
    pub invocations: u32,
    pub completed: u32,
    pub failed: u32,
    pub avg_duration_s: Option<f64>,
}

#[derive(uniffi::Record)]
pub struct FfiFleetHealthSignal {
    pub label: String,
    pub status: FfiFleetHealthStatus,
    pub detail: String,
}

#[derive(uniffi::Record)]
pub struct FfiFailureEntry {
    pub run_id: String,
    pub agent: String,
    pub skill: String,
    pub reason: String,
    pub occurred_at: Option<String>,
    pub age_label: String,
    /// Absolute filesystem path to the source artifact (meta.json or
    /// report.md) when known. Shell-side surfaces should treat absence
    /// as "no permalink available" rather than synthesizing one.
    pub source_path: Option<String>,
}

#[derive(uniffi::Record)]
pub struct FfiActionQueueItem {
    pub kind: FfiActionQueueKind,
    pub summary: String,
    pub source_path: Option<String>,
    pub priority: FfiActionPriority,
}

#[derive(uniffi::Record)]
pub struct FfiDataQuality {
    pub scanned_meta_files: u32,
    pub capped: bool,
    pub missing_model: u32,
    pub missing_duration: u32,
    pub parse_failures: u32,
    pub artifact_root: Option<String>,
    pub artifact_root_present: bool,
}

#[derive(uniffi::Record)]
pub struct FfiSettlementBoard {
    pub f: u64,
    pub x: u64,
    pub n: u64,
    pub invalid: u64,
    pub unclassified: u64,
    pub active: u64,
    pub stalled: u64,
    pub orphans: u64,
    pub total_settled: u64,
    pub scope: String,
}

#[derive(uniffi::Record)]
pub struct FfiMissionControlSnapshot {
    pub generated_at: String,
    pub active_dispatches: Vec<FfiActiveDispatch>,
    pub wave_atlas: Vec<FfiWaveSegment>,
    pub agent_stats: Vec<FfiAgentStatsRow>,
    pub skill_stats: Vec<FfiSkillStatsRow>,
    pub settlement: FfiSettlementBoard,
    pub fleet_health: Vec<FfiFleetHealthSignal>,
    pub failures: Vec<FfiFailureEntry>,
    pub action_queue: Vec<FfiActionQueueItem>,
    pub data_quality: FfiDataQuality,
}

impl From<mc::WaveState> for FfiWaveState {
    fn from(value: mc::WaveState) -> Self {
        match value {
            mc::WaveState::Pending => FfiWaveState::Pending,
            mc::WaveState::InProgress => FfiWaveState::InProgress,
            mc::WaveState::Completed => FfiWaveState::Completed,
            mc::WaveState::Failed => FfiWaveState::Failed,
        }
    }
}

impl From<mc::FleetHealthStatus> for FfiFleetHealthStatus {
    fn from(value: mc::FleetHealthStatus) -> Self {
        match value {
            mc::FleetHealthStatus::Ok => FfiFleetHealthStatus::Ok,
            mc::FleetHealthStatus::Warn => FfiFleetHealthStatus::Warn,
            mc::FleetHealthStatus::Blocked => FfiFleetHealthStatus::Blocked,
            mc::FleetHealthStatus::Unknown => FfiFleetHealthStatus::Unknown,
        }
    }
}

impl From<mc::ActionQueueKind> for FfiActionQueueKind {
    fn from(value: mc::ActionQueueKind) -> Self {
        match value {
            mc::ActionQueueKind::StalledRun => FfiActionQueueKind::StalledRun,
            mc::ActionQueueKind::Failure => FfiActionQueueKind::Failure,
            mc::ActionQueueKind::Polarize => FfiActionQueueKind::Polarize,
            mc::ActionQueueKind::ReportReady => FfiActionQueueKind::ReportReady,
        }
    }
}

impl From<mc::ActionPriority> for FfiActionPriority {
    fn from(value: mc::ActionPriority) -> Self {
        match value {
            mc::ActionPriority::Critical => FfiActionPriority::Critical,
            mc::ActionPriority::High => FfiActionPriority::High,
            mc::ActionPriority::Normal => FfiActionPriority::Normal,
        }
    }
}

impl From<mc::SettlementBoardCounts> for FfiSettlementBoard {
    fn from(value: mc::SettlementBoardCounts) -> Self {
        Self {
            f: value.f as u64,
            x: value.x as u64,
            n: value.n as u64,
            invalid: value.invalid as u64,
            unclassified: value.unclassified as u64,
            active: value.active as u64,
            stalled: value.stalled as u64,
            orphans: value.orphans as u64,
            total_settled: value.total_settled as u64,
            scope: value.scope,
        }
    }
}

fn convert_snapshot(state: MissionControlState) -> FfiMissionControlSnapshot {
    FfiMissionControlSnapshot {
        generated_at: state.generated_at,
        active_dispatches: state
            .active_dispatches
            .into_iter()
            .map(|dispatch| FfiActiveDispatch {
                run_id: dispatch.run_id,
                agent: dispatch.agent,
                skill: dispatch.skill,
                wave: dispatch.wave,
                started_at: dispatch.started_at,
                age_label: dispatch.age_label,
                eta_label: dispatch.eta_label,
            })
            .collect(),
        wave_atlas: state
            .wave_atlas
            .into_iter()
            .map(|segment| FfiWaveSegment {
                wave_id: segment.wave_id,
                total: segment.total as u32,
                completed: segment.completed as u32,
                failed: segment.failed as u32,
                active: segment.active as u32,
                latest_state: segment.latest_state.into(),
            })
            .collect(),
        agent_stats: state
            .agent_stats
            .into_iter()
            .map(|row| FfiAgentStatsRow {
                agent: row.agent,
                total_runs: row.total_runs as u32,
                completed: row.completed as u32,
                failed: row.failed as u32,
                success_rate: row.success_rate,
                avg_duration_s: row.avg_duration_s,
                model_known_rate: row.model_known_rate,
            })
            .collect(),
        skill_stats: state
            .skill_stats
            .into_iter()
            .map(|row| FfiSkillStatsRow {
                skill: row.skill,
                invocations: row.invocations as u32,
                completed: row.completed as u32,
                failed: row.failed as u32,
                avg_duration_s: row.avg_duration_s,
            })
            .collect(),
        settlement: state.settlement.into(),
        fleet_health: state
            .fleet_health
            .into_iter()
            .map(|signal| FfiFleetHealthSignal {
                label: signal.label,
                status: signal.status.into(),
                detail: signal.detail,
            })
            .collect(),
        failures: state
            .failures
            .into_iter()
            .map(|entry| FfiFailureEntry {
                run_id: entry.run_id,
                agent: entry.agent,
                skill: entry.skill,
                reason: entry.reason,
                occurred_at: entry.occurred_at,
                age_label: entry.age_label,
                source_path: entry
                    .source_path
                    .map(|path| path.to_string_lossy().into_owned()),
            })
            .collect(),
        action_queue: state
            .action_queue
            .into_iter()
            .map(|item| FfiActionQueueItem {
                kind: item.kind.into(),
                summary: item.summary,
                source_path: item
                    .source_path
                    .map(|path| path.to_string_lossy().into_owned()),
                priority: item.priority.into(),
            })
            .collect(),
        data_quality: FfiDataQuality {
            scanned_meta_files: state.data_quality.scanned_meta_files as u32,
            capped: state.data_quality.capped,
            missing_model: state.data_quality.missing_model as u32,
            missing_duration: state.data_quality.missing_duration as u32,
            parse_failures: state.data_quality.parse_failures as u32,
            artifact_root: state
                .data_quality
                .artifact_root
                .map(|path| path.to_string_lossy().into_owned()),
            artifact_root_present: state.data_quality.artifact_root_present,
        },
    }
}

/// Load the operator's Mission Control snapshot using the default
/// resolution: control-plane root under `VIBECRAFTED_HOME` and artifact
/// root at `$VIBECRAFTED_HOME/artifacts/`.
///
/// Shell surfaces (Swift/macOS app) should call this on focus / refresh
/// and bind directly to the returned record. Missing inputs surface as
/// typed empty states, never as panics.
#[uniffi::export]
pub fn load_mission_control_snapshot() -> Result<FfiMissionControlSnapshot, MuxError> {
    let state_root = default_state_root();
    let artifact_root = default_vibecrafted_home().join("artifacts");
    let control_plane = ControlPlaneState::load(&state_root)
        .unwrap_or_else(|_| ControlPlaneState::empty(&state_root));
    let mission = MissionControlState::build(&control_plane, &artifact_root);
    Ok(convert_snapshot(mission))
}

/// Variant for surfaces that already resolved their own roots (tests,
/// agents that run in vendored workspaces, multi-tenant probing).
#[uniffi::export]
pub fn load_mission_control_snapshot_at(
    state_root: String,
    artifact_root: String,
) -> Result<FfiMissionControlSnapshot, MuxError> {
    let state_path = PathBuf::from(state_root);
    let artifact_path = PathBuf::from(artifact_root);
    let control_plane = ControlPlaneState::load(&state_path)
        .unwrap_or_else(|_| ControlPlaneState::empty(&state_path));
    let mission = MissionControlState::build(&control_plane, &artifact_path);
    Ok(convert_snapshot(mission))
}

// ═══════════════════════════════════════════════════════════
// MC2 Run Inspector — drill into one run's real artifacts
//
// `load_mission_control_snapshot` shows the fleet poster; this is the
// drill-down. Given a `run_id` (e.g. the `impl-…` ids the snapshot's
// dispatches/failures carry), read that run's real `report.md`, the tail of
// its `transcript.log`, and its `meta.json` header straight off disk under
// `runtime_runs/<run_id>/`. Reading stays in `voc::run_detail`; this layer
// only converts the record. Missing inputs surface as typed-empty.
// ═══════════════════════════════════════════════════════════

#[derive(uniffi::Record)]
pub struct FfiRunDetail {
    pub run_id: String,
    pub agent: Option<String>,
    pub skill: Option<String>,
    pub state: Option<String>,
    pub report_md: Option<String>,
    pub transcript_tail: String,
    pub report_path: Option<String>,
    pub transcript_path: Option<String>,
}

impl From<RunDetail> for FfiRunDetail {
    fn from(detail: RunDetail) -> Self {
        FfiRunDetail {
            run_id: detail.run_id,
            agent: detail.agent,
            skill: detail.skill,
            state: detail.state,
            report_md: detail.report_md,
            transcript_tail: detail.transcript_tail,
            report_path: detail.report_path,
            transcript_path: detail.transcript_path,
        }
    }
}

/// Load one run's drill-down artifacts using the default control-plane root
/// (`default_state_root()` => `~/.vibecrafted/control_plane`). The inspector
/// calls this when a run is selected anywhere in Mission Control. Missing
/// inputs (no meta, no report, no transcript) surface as typed-empty, never a
/// panic.
#[uniffi::export]
pub fn load_run_detail(run_id: String) -> Result<FfiRunDetail, MuxError> {
    let state_root = default_state_root();
    Ok(run_detail::load_run_detail(&state_root, &run_id).into())
}

/// Variant for surfaces that already resolved their own control-plane root
/// (tests, vendored workspaces). Mirrors `load_mission_control_snapshot_at`.
#[uniffi::export]
pub fn load_run_detail_at(state_root: String, run_id: String) -> Result<FfiRunDetail, MuxError> {
    Ok(run_detail::load_run_detail(&PathBuf::from(state_root), &run_id).into())
}

// ═══════════════════════════════════════════════════════════
// Engine
// ═══════════════════════════════════════════════════════════

#[uniffi::export]
pub fn init_runtime(socket_path: String) -> Result<(), MuxError> {
    SOCKET_PATH
        .set(PathBuf::from(socket_path))
        .map_err(|_| MuxError::Core {
            msg: "Already initialized".to_string(),
        })?;
    Ok(())
}

fn get_socket_path() -> Result<PathBuf, MuxError> {
    SOCKET_PATH.get().cloned().ok_or_else(|| MuxError::Core {
        msg: "Runtime not initialized".to_string(),
    })
}

#[uniffi::export(callback_interface)]
pub trait EventCallback: Send + Sync {
    fn on_event(&self, event_json: String);
    fn on_error(&self, err: String);
}

#[uniffi::export]
pub async fn subscribe_events(callback: Box<dyn EventCallback>) -> Result<(), MuxError> {
    let socket = get_socket_path()?;
    std::thread::Builder::new()
        .name("vibecrafted-event-subscription".to_string())
        .spawn(move || {
            let runtime = match tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
            {
                Ok(runtime) => runtime,
                Err(e) => {
                    callback.on_error(format!("Failed to start event runtime: {e}"));
                    return;
                }
            };

            runtime.block_on(async move {
                use tokio::io::{AsyncBufReadExt, AsyncWriteExt};

                let stream = match tokio::net::UnixStream::connect(&socket).await {
                    Ok(stream) => stream,
                    Err(e) => {
                        callback.on_error(e.to_string());
                        return;
                    }
                };
                let (reader, mut writer) = stream.into_split();
                let command = MuxControlCommand::Subscribe;
                let encoded = match serde_json::to_string(&command) {
                    Ok(encoded) => encoded + "\n",
                    Err(e) => {
                        callback.on_error(format!("Failed to encode subscription command: {e}"));
                        return;
                    }
                };

                if let Err(e) = writer.write_all(encoded.as_bytes()).await {
                    callback.on_error(e.to_string());
                    return;
                }

                let mut lines = tokio::io::BufReader::new(reader).lines();
                loop {
                    match lines.next_line().await {
                        Ok(Some(line)) => callback.on_event(line),
                        Ok(None) => {
                            callback.on_error("Stream closed".to_string());
                            return;
                        }
                        Err(e) => {
                            callback.on_error(e.to_string());
                            return;
                        }
                    }
                }
            });
        })
        .map_err(|e| MuxError::Core {
            msg: format!("Failed to start event subscription thread: {e}"),
        })?;
    Ok(())
}

#[uniffi::export]
pub async fn get_server_status() -> Result<Vec<FfiServerStatus>, MuxError> {
    let socket = get_socket_path()?;
    let res = send_command(&socket, &MuxControlCommand::GetStatus).await?;
    if let MuxControlResponse::Status(snapshot) = res {
        Ok(vec![FfiServerStatus {
            name: snapshot.service_name.clone(),
            status: format!("{:?}", snapshot.server_status),
            queue_depth: snapshot.pending_requests as u32,
            queue_capacity: snapshot.max_active_clients as u32,
            restart_count: snapshot.restarts,
        }])
    } else {
        Err(MuxError::Core {
            msg: "Unexpected response".into(),
        })
    }
}

#[uniffi::export]
pub async fn get_routes() -> Result<Vec<FfiRoute>, MuxError> {
    let socket = get_socket_path()?;
    let res = send_command(&socket, &MuxControlCommand::RouteSnapshot).await?;
    if let MuxControlResponse::Routes(routes) = res {
        Ok(routes
            .into_iter()
            .map(|r| FfiRoute {
                client: FfiClientKind::Generic { name: r.client }, // Or map based on name
                service: r.server,
                state: r.status,
            })
            .collect())
    } else {
        Err(MuxError::Core {
            msg: "Unexpected response".into(),
        })
    }
}

#[uniffi::export]
pub async fn verify_client(kind: FfiClientKind) -> Result<FfiVerifyResult, MuxError> {
    let socket = get_socket_path()?;
    let ckind: ClientKind = kind.into();
    let res = send_command(
        &socket,
        &MuxControlCommand::Verify {
            client_kind: ckind.clone(),
        },
    )
    .await?;
    if let MuxControlResponse::VerifyResult(result) = res {
        Ok(FfiVerifyResult {
            kind: ckind.into(),
            ok: result.ok,
            detail: format!("{} non-mux", result.non_mux_servers.len()),
        })
    } else {
        Err(MuxError::Core {
            msg: "Unexpected response".into(),
        })
    }
}

#[uniffi::export]
pub async fn restart_service(name: String) -> Result<(), MuxError> {
    let socket = get_socket_path()?;
    let _res = send_command(&socket, &MuxControlCommand::RestartService { name }).await?;
    Ok(())
}

#[uniffi::export]
pub async fn get_recent_logs(service: String, _lines: u32) -> Result<Vec<String>, MuxError> {
    Ok(vec![format!(
        "Logs for {} not implemented in backend",
        service
    )])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_client_kind_roundtrip() {
        let original = ClientKind::Claude;
        let ffi: FfiClientKind = original.clone().into();
        let back: ClientKind = ffi.into();
        assert_eq!(original, back);

        let original = ClientKind::Generic {
            name: "test".to_string(),
        };
        let ffi: FfiClientKind = original.clone().into();
        let back: ClientKind = ffi.into();
        assert_eq!(original, back);
    }

    #[test]
    fn settlement_board_conversion_carries_every_field() {
        let board = mc::SettlementBoardCounts {
            f: 1,
            x: 2,
            n: 3,
            invalid: 4,
            unclassified: 5,
            active: 6,
            stalled: 7,
            orphans: 8,
            total_settled: 9,
            scope: "retained snapshots".to_string(),
        };

        let ffi: FfiSettlementBoard = board.into();

        assert_eq!(ffi.f, 1);
        assert_eq!(ffi.x, 2);
        assert_eq!(ffi.n, 3);
        assert_eq!(ffi.invalid, 4);
        assert_eq!(ffi.unclassified, 5);
        assert_eq!(ffi.active, 6);
        assert_eq!(ffi.stalled, 7);
        assert_eq!(ffi.orphans, 8);
        assert_eq!(ffi.total_settled, 9);
        assert_eq!(ffi.scope, "retained snapshots");
    }
}
