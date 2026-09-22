//! Fail-closed routing from a VOC run to its existing project Frame.
//!
//! VOC never parses runtime metadata here. `control-core` owns both the typed
//! run routing projection and the canonical workspace/session registration.
//! The action creates one new tab only after every identity axis agrees, then
//! confirms that exact tab through vc-frame's typed `list-tabs --json` view.

use control_core::{ControlPlane, FrameSessionInventory, LiveFrameSession, RunRouting};
use serde::Deserialize;
use std::collections::BTreeSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GotoWorkReceipt {
    pub run_id: String,
    pub workspace_id: String,
    pub workspace_label: String,
    pub frame_session: String,
    pub socket_dir: String,
    pub tab_id: u64,
    pub tab_name: String,
}

impl GotoWorkReceipt {
    #[must_use]
    pub fn status_line(&self) -> String {
        format!(
            "goto-work confirmed · {} · Frame {} · tab {} ({})",
            self.workspace_label, self.frame_session, self.tab_id, self.tab_name
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GotoWorkError {
    Refused(String),
    Unconfirmed(String),
}

impl fmt::Display for GotoWorkError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Refused(reason) => write!(formatter, "refused: {reason}"),
            Self::Unconfirmed(reason) => write!(
                formatter,
                "unconfirmed: {reason}; the tab may exist, do not retry blindly"
            ),
        }
    }
}

impl std::error::Error for GotoWorkError {}

#[derive(Debug, PartialEq, Eq)]
struct Target {
    run_id: String,
    agent: String,
    provider_session_id: String,
    root: String,
    workspace_id: String,
    workspace_label: String,
    frame_session: String,
    socket_dir: String,
}

/// Open and confirm a new work tab for `run_id` in its exact registered Frame.
///
/// This function never starts a Frame session. Missing, stale, or ambiguous
/// routing returns [`GotoWorkError::Refused`] before invoking `vc-frame`.
pub fn goto_work(
    control_plane_root: &Path,
    run_id: &str,
    vc_frame_binary: &Path,
    command_deck: &Path,
) -> Result<GotoWorkReceipt, GotoWorkError> {
    let target = resolve_target(control_plane_root, run_id)?;
    let tab_name = request_tab_name(&target.run_id);
    let before = frame_command(vc_frame_binary, &target)
        .args(["action", "list-tabs", "--json"])
        .output()
        .map_err(|error| {
            GotoWorkError::Refused(format!(
                "could not read the destination tab registry before mutation: {error}"
            ))
        })?;
    let (_, session_incarnation) = parse_registry(&before, "preflight")
        .map_err(|reason| refuse(format!("destination registry preflight failed: {reason}")))?;
    let revalidated = resolve_target(control_plane_root, run_id)?;
    if revalidated != target {
        return Err(refuse(format!(
            "destination for run {run_id} changed after Frame preflight; refresh and try once"
        )));
    }

    let new_tab = frame_command(vc_frame_binary, &target)
        .args(["action", "new-tab", "--name"])
        .arg(&tab_name)
        .args(["--cwd", &target.root, "--"])
        .arg(command_deck)
        .args([
            "resume",
            &target.agent,
            "--session",
            &target.provider_session_id,
        ])
        .output()
        .map_err(|error| {
            GotoWorkError::Unconfirmed(format!(
                "could not complete {} new-tab: {error}",
                vc_frame_binary.display()
            ))
        })?;
    let tab_id = parse_created_tab_id(&new_tab)?;

    let listed = frame_command(vc_frame_binary, &target)
        .args(["action", "list-tabs", "--json"])
        .output()
        .map_err(|error| {
            GotoWorkError::Unconfirmed(format!(
                "could not read canonical tab registry from {}: {error}",
                vc_frame_binary.display()
            ))
        })?;
    let registered = confirm_tab(&listed, &session_incarnation, tab_id, &tab_name)?;

    Ok(GotoWorkReceipt {
        run_id: target.run_id,
        workspace_id: target.workspace_id,
        workspace_label: target.workspace_label,
        frame_session: target.frame_session,
        socket_dir: target.socket_dir,
        tab_id: registered.tab_id,
        tab_name: registered.name,
    })
}

fn resolve_target(control_plane_root: &Path, run_id: &str) -> Result<Target, GotoWorkError> {
    let run_id = run_id.trim();
    let plane = ControlPlane::from_control_plane_home(control_plane_root);
    let route = plane.load_run_routing().remove(run_id).ok_or_else(|| {
        refuse(format!(
            "run {run_id} has no canonical runtime routing receipt"
        ))
    })?;
    let agent = required(&route.agent, "agent", run_id)?;
    let root = required(&route.root, "root", run_id)?;
    if !Path::new(&root).is_absolute() {
        return Err(refuse(format!("run {run_id} has a non-absolute root axis")));
    }
    let provider_session_id = required(&route.provider_session_id, "provider_session_id", run_id)?;
    let workspace_id = required(&route.workspace_id, "workspace_id", run_id)?;
    let workspace_instance_id = required(
        &route.workspace_instance_id,
        "workspace_instance_id",
        run_id,
    )?;
    let workspace_session_id = required(
        &route.workspace_session_id,
        "vibecrafted/workspace session id",
        run_id,
    )?;
    let worker_host_session = required(&route.worker_host_session, "worker_host_session", run_id)?;

    let projection = plane
        .load_workspace_projection()
        .map_err(|error| refuse(format!("canonical workspace registry is invalid: {error}")))?;
    let catalog = projection
        .catalog
        .as_ref()
        .ok_or_else(|| refuse("canonical workspace catalog is missing"))?;
    let workspaces = catalog
        .workspaces
        .iter()
        .filter(|workspace| workspace.workspace_id == workspace_id)
        .collect::<Vec<_>>();
    let workspace = match workspaces.as_slice() {
        [workspace] => *workspace,
        [] => {
            return Err(refuse(format!(
                "workspace {workspace_id} from run {run_id} is absent from the canonical catalog"
            )));
        }
        _ => {
            return Err(refuse(format!(
                "workspace {workspace_id} is ambiguous in the canonical catalog"
            )));
        }
    };

    let logical_sessions = projection
        .sessions
        .iter()
        .filter(|session| {
            session.session_id == workspace_session_id
                && session.workspace_id == workspace_id
                && session.workspace_instance_id == workspace_instance_id
        })
        .collect::<Vec<_>>();
    if logical_sessions.is_empty() {
        return Err(refuse(format!(
            "run {run_id} routing does not match a canonical workspace session"
        )));
    }

    let inventory = FrameSessionInventory::scan(projection.frame_socket_dirs());
    let live = projection.live_frame_sessions(&inventory);
    let logical_live = live
        .iter()
        .filter(|session| {
            session.owner.as_ref().is_some_and(|owner| {
                owner.workspace_id == workspace_id && owner.session_id == workspace_session_id
            })
        })
        .collect::<Vec<_>>();
    if logical_sessions.len() != 1 || logical_live.len() > 1 {
        return Err(refuse(format!(
            "ambiguous destination for run {run_id}: {} canonical registrations share workspace session {workspace_session_id}",
            logical_sessions.len().max(logical_live.len())
        )));
    }
    let candidates = logical_live
        .into_iter()
        .filter(|session| exact_live_match(session, &route))
        .collect::<Vec<_>>();
    let selected = match candidates.as_slice() {
        [session] => *session,
        [] => {
            let live_for_workspace = live
                .iter()
                .filter_map(|session| {
                    session
                        .owner
                        .as_ref()
                        .filter(|owner| owner.workspace_id == workspace_id)
                        .map(|_| format!("{}@{}", session.runtime_session_id, session.socket_dir))
                })
                .collect::<Vec<_>>();
            if live_for_workspace.is_empty() {
                return Err(refuse(format!(
                    "workspace {} has no live Frame session; create it explicitly with `vc-start --repo {}` (goto-work did not spawn)",
                    workspace.display_label, workspace.canonical_root
                )));
            }
            return Err(refuse(format!(
                "routing drift for run {run_id}: expected Frame {worker_host_session} / session {workspace_session_id}, live workspace candidates are {}",
                live_for_workspace.join(", ")
            )));
        }
        _ => unreachable!("logical live registrations are bounded to one"),
    };

    let current_route = plane.load_run_routing().remove(run_id);
    if current_route.as_ref() != Some(&route) {
        return Err(refuse(format!(
            "routing axes changed while resolving run {run_id}; refresh and try once"
        )));
    }

    Ok(Target {
        run_id: run_id.to_string(),
        agent,
        provider_session_id,
        root,
        workspace_id,
        workspace_label: workspace.display_label.clone(),
        frame_session: selected.runtime_session_id.clone(),
        socket_dir: selected.socket_dir.clone(),
    })
}

fn exact_live_match(session: &LiveFrameSession, route: &RunRouting) -> bool {
    session.runtime_session_id == route.worker_host_session
        && session.owner.as_ref().is_some_and(|owner| {
            owner.workspace_id == route.workspace_id
                && owner.session_id == route.workspace_session_id
        })
}

fn required(value: &str, axis: &str, run_id: &str) -> Result<String, GotoWorkError> {
    let value = value.trim();
    if value.is_empty()
        || matches!(
            value.to_ascii_lowercase().as_str(),
            "none" | "null" | "unknown"
        )
    {
        Err(refuse(format!(
            "run {run_id} has no unambiguous {axis} axis"
        )))
    } else {
        Ok(value.to_string())
    }
}

fn refuse(reason: impl Into<String>) -> GotoWorkError {
    GotoWorkError::Refused(reason.into())
}

fn frame_command(binary: &Path, target: &Target) -> Command {
    let mut command = Command::new(binary);
    command
        .args(["--session", &target.frame_session])
        .env("VC_FRAME_SESSION_NAME", &target.frame_session)
        .env("VC_FRAME_SOCKET_DIR", &target.socket_dir)
        .env("ZELLIJ_SOCKET_DIR", &target.socket_dir);
    command
}

fn parse_created_tab_id(output: &Output) -> Result<u64, GotoWorkError> {
    if !output.status.success() {
        return Err(GotoWorkError::Unconfirmed(format!(
            "new-tab failed after dispatch ({})",
            output_detail(output)
        )));
    }
    let stdout = String::from_utf8(output.stdout.clone())
        .map_err(|_| GotoWorkError::Unconfirmed("new-tab returned non-UTF-8 output".into()))?;
    let raw = stdout.trim();
    if raw.is_empty() || raw.lines().count() != 1 {
        return Err(GotoWorkError::Unconfirmed(format!(
            "new-tab did not return exactly one tab id (stdout={raw:?})"
        )));
    }
    raw.parse::<u64>()
        .map_err(|_| GotoWorkError::Unconfirmed(format!("new-tab returned invalid tab id {raw:?}")))
}

#[derive(Debug, Clone, Deserialize)]
struct RegisteredTab {
    tab_id: u64,
    name: String,
    position: u64,
    active: bool,
    other_focused_clients: Vec<serde_json::Value>,
    session_incarnation: String,
    tab_instance_id: String,
}

fn confirm_tab(
    output: &Output,
    expected_incarnation: &str,
    expected_id: u64,
    expected_name: &str,
) -> Result<RegisteredTab, GotoWorkError> {
    let (tabs, incarnation) =
        parse_registry(output, "confirmation").map_err(GotoWorkError::Unconfirmed)?;
    if incarnation != expected_incarnation {
        return Err(GotoWorkError::Unconfirmed(format!(
            "Frame session incarnation changed from {expected_incarnation} to {incarnation}"
        )));
    }
    let matches = tabs
        .into_iter()
        .filter(|tab| tab.tab_id == expected_id && tab.name == expected_name)
        .collect::<Vec<_>>();
    match matches.as_slice() {
        [tab] => Ok(tab.clone()),
        [] => Err(GotoWorkError::Unconfirmed(format!(
            "canonical registry has no tab id {expected_id} named {expected_name}"
        ))),
        _ => Err(GotoWorkError::Unconfirmed(format!(
            "canonical registry has duplicate tab id {expected_id} named {expected_name}"
        ))),
    }
}

fn parse_registry(output: &Output, phase: &str) -> Result<(Vec<RegisteredTab>, String), String> {
    if !output.status.success() {
        return Err(format!(
            "{phase} list-tabs failed: {}",
            output_detail(output)
        ));
    }
    let tabs: Vec<RegisteredTab> = serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("invalid list-tabs JSON: {error}"))?;
    let mut ids = BTreeSet::new();
    let mut instances = BTreeSet::new();
    let mut incarnations = BTreeSet::new();
    for tab in &tabs {
        if tab.name.is_empty()
            || tab.session_incarnation.is_empty()
            || tab.tab_instance_id.len() != 32
            || !tab
                .tab_instance_id
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
            || !ids.insert(tab.tab_id)
            || !instances.insert(tab.tab_instance_id.clone())
        {
            return Err("list-tabs registry failed structural validation".into());
        }
        let _ = (tab.position, tab.active, tab.other_focused_clients.len());
        incarnations.insert(tab.session_incarnation.clone());
    }
    if incarnations.len() > 1 {
        return Err("list-tabs mixed multiple session incarnations".into());
    }
    let incarnation = incarnations
        .into_iter()
        .next()
        .ok_or_else(|| format!("{phase} list-tabs returned an empty registry"))?;
    Ok((tabs, incarnation))
}

fn output_detail(output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if !stderr.is_empty() {
        format!("exit {:?}: {stderr}", output.status.code())
    } else {
        format!("exit {:?}: {stdout}", output.status.code())
    }
}

fn request_tab_name(run_id: &str) -> String {
    let safe = run_id
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() {
                ch.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .take(24)
        .collect::<String>();
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("voc-{safe}-{nonce}")
}

/// Resolve the vc-frame binary without allowing a relative environment
/// override to shadow the installed/runtime-owned executable.
pub fn vc_frame_binary_from_env() -> Result<PathBuf, GotoWorkError> {
    match std::env::var_os("VIBECRAFTED_VC_FRAME_BIN") {
        Some(raw) => {
            let path = PathBuf::from(raw);
            if path.is_absolute() {
                Ok(path)
            } else {
                Err(refuse("VIBECRAFTED_VC_FRAME_BIN must be absolute"))
            }
        }
        None => Ok(PathBuf::from("vc-frame")),
    }
}
