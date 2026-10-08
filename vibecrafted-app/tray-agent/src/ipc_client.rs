use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result};
pub use mux_agent::ipc::{ClientKind, IpcEvent, MuxControlCommand, MuxControlResponse};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tracing::warn;

use crate::state::{record_spawn_entry, update_tray_status};
use crate::types::{SpawnEntry, TrayStatus, TrayUpdate};

pub fn default_socket_path() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".rmcp-mux/ipc/control.sock")
}

pub fn client_label(kind: &ClientKind) -> String {
    match kind {
        ClientKind::Claude => "Claude".to_string(),
        ClientKind::Codex => "Codex".to_string(),
        ClientKind::Gemini => "Gemini".to_string(),
        ClientKind::Junie => "Junie".to_string(),
        ClientKind::Cursor => "Cursor".to_string(),
        ClientKind::Generic { name } => name.clone(),
    }
}

pub fn from_mux_event(event: IpcEvent) -> TrayUpdate {
    match event {
        IpcEvent::StateChange { .. } => {
            // We ignore general state changes as ServerHealth tells us about restarts.
            TrayUpdate::None
        }
        IpcEvent::RouteUpdate { .. } => TrayUpdate::Status(TrayStatus::Routing),
        IpcEvent::ServerHealth {
            restarts,
            last_error,
            ..
        } => TrayUpdate::Status(tray_status_from_health(restarts, last_error.as_ref())),
        IpcEvent::ClientDrift { client, .. } => {
            TrayUpdate::Alert(format!("Drift detected for {}", client))
        }
        IpcEvent::SpawnUpdate {
            run_id,
            agent,
            skill,
            mode,
            state,
            session_id,
            exit_code,
            launcher_pid,
            transcript,
            report,
            ts,
        } => {
            let entry = SpawnEntry {
                run_id: run_id.clone(),
                agent: agent.clone(),
                skill,
                mode,
                state: state.clone(),
                session_id,
                exit_code,
                launcher_pid,
                transcript,
                report,
                ts,
            };
            let active = record_spawn_entry(entry);
            TrayUpdate::SpawnBadge {
                active,
                last_agent: agent,
                last_state: state,
                last_run_id: run_id,
            }
        }
    }
}

pub fn from_mux_response(response: MuxControlResponse) -> TrayUpdate {
    if let MuxControlResponse::Event(event) = response {
        from_mux_event(event)
    } else {
        TrayUpdate::None
    }
}

pub fn tray_status_from_health(restarts: u64, last_error: Option<&String>) -> TrayStatus {
    if restarts > 0 && last_error.is_some() {
        TrayStatus::Failed
    } else if restarts > 0 {
        TrayStatus::Restarting
    } else {
        TrayStatus::Idle
    }
}

pub async fn subscribe_loop(socket_path: PathBuf) {
    let mut attempt = 0u32;
    let mut backoff = Duration::from_secs(1);
    loop {
        match subscribe_once(&socket_path).await {
            Ok(()) => {
                attempt = 0;
                backoff = Duration::from_secs(1);
            }
            Err(error) => {
                attempt += 1;
                warn!("mux tray IPC subscribe failed attempt {attempt}/10: {error:#}");
                if attempt >= 10 {
                    let _ = update_tray_status(TrayStatus::Failed);
                    return;
                }
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(Duration::from_secs(30));
            }
        }
    }
}

async fn subscribe_once(socket_path: &Path) -> Result<()> {
    let stream = UnixStream::connect(socket_path)
        .await
        .with_context(|| format!("connect {}", socket_path.display()))?;
    let (reader, mut writer) = stream.into_split();
    write_request(&mut writer, &MuxControlCommand::Subscribe).await?;
    let mut lines = BufReader::new(reader).lines();
    while let Some(line) = lines.next_line().await? {
        let response: MuxControlResponse = serde_json::from_str(&line)?;
        match from_mux_response(response) {
            TrayUpdate::Status(status) => {
                let _ = update_tray_status(status);
            }
            TrayUpdate::SpawnBadge {
                active,
                last_agent,
                last_state,
                last_run_id,
            } => {
                let _ = update_tray_status(TrayStatus::Spawning { count: active });
                crate::handlers::notify_spawn_update(&last_agent, &last_state, &last_run_id);
            }
            TrayUpdate::Alert(msg) => {
                warn!("Tray Alert: {}", msg);
            }
            TrayUpdate::None => {} // No-op
        }
    }
    anyhow::bail!("mux IPC stream closed")
}

pub async fn send_command(
    socket_path: impl AsRef<Path>,
    command: &MuxControlCommand,
) -> Result<MuxControlResponse> {
    let mut stream = UnixStream::connect(socket_path.as_ref()).await?;
    write_request(&mut stream, command).await?;
    let mut lines = BufReader::new(stream).lines();
    let Some(line) = lines.next_line().await? else {
        anyhow::bail!("mux IPC returned no response");
    };
    serde_json::from_str(&line).context("decode mux response")
}

async fn write_request<W>(writer: &mut W, command: &MuxControlCommand) -> Result<()>
where
    W: AsyncWriteExt + Unpin,
{
    let encoded = serde_json::to_string(command)?;
    writer.write_all(encoded.as_bytes()).await?;
    writer.write_all(b"\n").await?;
    writer.flush().await?;
    Ok(())
}

pub async fn stop_mux_daemon(socket_path: &Path) -> Result<MuxControlResponse> {
    send_command(socket_path, &MuxControlCommand::Shutdown { graceful: true }).await
}

pub async fn restart_service(socket_path: &Path, name: &str) -> Result<MuxControlResponse> {
    send_command(
        socket_path,
        &MuxControlCommand::RestartService {
            name: name.to_string(),
        },
    )
    .await
}

pub async fn verify_client(socket_path: &Path, client_kind: ClientKind) -> Result<String> {
    let kind_label = client_label(&client_kind);
    match send_command(socket_path, &MuxControlCommand::Verify { client_kind }).await? {
        MuxControlResponse::VerifyResult(result) => Ok(format!(
            "{}: {} ({} non-mux servers)",
            kind_label,
            if result.ok { "ok" } else { "drift" },
            result.non_mux_servers.len()
        )),
        MuxControlResponse::Error(message) => anyhow::bail!(message),
        other => Ok(format!("{other:?}")),
    }
}

pub async fn route_snapshot(socket_path: PathBuf) -> Result<String> {
    match send_command(&socket_path, &MuxControlCommand::RouteSnapshot).await? {
        MuxControlResponse::Routes(routes) => Ok(serde_json::to_string_pretty(&routes)?),
        MuxControlResponse::Error(message) => anyhow::bail!(message),
        other => Ok(format!("{other:?}")),
    }
}

pub async fn diagnostics(socket_path: PathBuf) -> Result<String> {
    match send_command(&socket_path, &MuxControlCommand::GetStatus).await? {
        MuxControlResponse::Status(status) => Ok(serde_json::to_string_pretty(&status)?),
        MuxControlResponse::Error(message) => anyhow::bail!(message),
        other => Ok(format!("{other:?}")),
    }
}
