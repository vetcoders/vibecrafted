//! Typed, read-only projection of the canonical workspace catalog and sessions.
//!
//! Python remains the sole writer. This module reads only the exact
//! `control_plane/workspaces/catalog.json` and `sessions/*.json` contracts; it
//! does not discover repositories or infer workspace identity from paths.
//!
//! An attachment's recorded `state` is attach-time evidence: the writer stamps
//! `live` when a vc-frame session is bound and nothing downgrades it when that
//! Frame later exits. Whether a Frame runs *now* is answered only by
//! [`FrameSessionInventory`], which reads the Frame socket files, and joined
//! back to the records by [`WorkspaceProjection::live_frame_sessions`].

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs;
use std::path::Path;

use serde::Deserialize;

use crate::ControlPlane;

const CATALOG_SCHEMA: &str = "vibecrafted.workspace-catalog.v1";
const WORKSPACE_SCHEMA: &str = "vibecrafted.workspace.v1";
const SESSION_SCHEMA: &str = "vibecrafted.workspace-session.v1";
const SESSION_LIMIT: usize = 200;
const FRAME_RUNTIME: &str = "vc-frame";
const FRAME_CONTRACT_DIR_PREFIX: &str = "contract_version_";
/// Distinct socket roots probed per read. The live host records two (the
/// short `/tmp/vc-frame-$UID` root and the legacy TMPDIR one).
const FRAME_SOCKET_DIR_LIMIT: usize = 16;
/// `contract_version_<N>` directories probed per socket root.
const FRAME_CONTRACT_DIR_LIMIT: usize = 8;
/// Socket entries read per contract directory.
const FRAME_SOCKET_ENTRY_LIMIT: usize = 512;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkspaceProjection {
    pub catalog: Option<WorkspaceCatalogProjection>,
    pub sessions: Vec<WorkspaceSession>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WorkspaceCatalogProjection {
    pub selected_workspace_id: Option<String>,
    pub updated_at: String,
    pub workspaces: Vec<WorkspaceRecord>,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct WorkspaceRecord {
    schema: String,
    pub workspace_id: String,
    pub display_label: String,
    pub canonical_root: String,
    pub status: String,
    pub updated_at: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct WorkspaceSession {
    schema: String,
    pub session_id: String,
    pub workspace_id: String,
    pub workspace_instance_id: String,
    pub updated_at: String,
    #[serde(default)]
    pub attachments: Vec<RuntimeSessionAttachment>,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct RuntimeSessionAttachment {
    pub runtime: String,
    pub runtime_session_id: String,
    /// Attach-time evidence (`live` / `dead` / `missing`), never refreshed
    /// when the runtime exits. See [`FrameSessionInventory`] for liveness.
    pub state: String,
    /// Frame socket root the session was bound under (`/tmp/vc-frame-$UID`).
    #[serde(default)]
    pub socket_dir: String,
    #[serde(default)]
    pub updated_at: String,
}

/// vc-frame sessions whose server is running right now.
///
/// A vc-frame server binds `<socket_dir>/contract_version_<N>/<session name>`
/// for as long as it runs, and `vc-frame list-sessions` enumerates exactly
/// those socket files. The inventory reads the same directories and never
/// connects: every client connection — including a short-lived
/// `vc-frame list-sessions` subprocess — is announced to all plugins of that
/// session and re-renders them, so a per-request connect probe would storm
/// every open Frame. The trade-off is that a socket file left behind by a
/// crashed server counts as running until the next `list-sessions` removes it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct FrameSessionInventory {
    running: BTreeSet<(String, String)>,
}

impl FrameSessionInventory {
    /// One bounded directory read per distinct socket root. Unreadable or
    /// missing roots contribute nothing; they are not an error, because a
    /// root with no running server is the normal state after a reboot.
    pub fn scan<I, S>(socket_dirs: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        let roots = socket_dirs
            .into_iter()
            .map(|dir| dir.as_ref().trim().to_string())
            .filter(|dir| !dir.is_empty())
            .collect::<BTreeSet<_>>();
        let mut running = BTreeSet::new();
        for socket_dir in roots.into_iter().take(FRAME_SOCKET_DIR_LIMIT) {
            let Ok(entries) = fs::read_dir(&socket_dir) else {
                continue;
            };
            let contract_dirs = entries
                .flatten()
                .filter(|entry| {
                    entry
                        .file_name()
                        .to_str()
                        .is_some_and(|name| name.starts_with(FRAME_CONTRACT_DIR_PREFIX))
                        && entry.file_type().is_ok_and(|kind| kind.is_dir())
                })
                .take(FRAME_CONTRACT_DIR_LIMIT);
            for contract_dir in contract_dirs {
                let Ok(sockets) = fs::read_dir(contract_dir.path()) else {
                    continue;
                };
                for socket in sockets.flatten().take(FRAME_SOCKET_ENTRY_LIMIT) {
                    if !socket.file_type().is_ok_and(|kind| is_socket(&kind)) {
                        continue;
                    }
                    if let Some(name) = socket.file_name().to_str() {
                        running.insert((socket_dir.clone(), name.to_string()));
                    }
                }
            }
        }
        Self { running }
    }

    /// Inventory from already-known `(socket_dir, session name)` pairs.
    pub fn from_running<I, D, N>(sessions: I) -> Self
    where
        I: IntoIterator<Item = (D, N)>,
        D: Into<String>,
        N: Into<String>,
    {
        Self {
            running: sessions
                .into_iter()
                .map(|(dir, name)| (dir.into(), name.into()))
                .collect(),
        }
    }

    #[must_use]
    pub fn is_running(&self, socket_dir: &str, session_name: &str) -> bool {
        self.running
            .contains(&(socket_dir.to_string(), session_name.to_string()))
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.running.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.running.is_empty()
    }

    /// `(socket_dir, session name)` pairs, ordered by root then name.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &str)> {
        self.running
            .iter()
            .map(|(dir, name)| (dir.as_str(), name.as_str()))
    }
}

#[cfg(unix)]
fn is_socket(kind: &fs::FileType) -> bool {
    use std::os::unix::fs::FileTypeExt;
    kind.is_socket()
}

#[cfg(not(unix))]
fn is_socket(_kind: &fs::FileType) -> bool {
    false
}

/// A running vc-frame session joined with the logical session that owns it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LiveFrameSession {
    pub socket_dir: String,
    /// The vc-frame session name, as `vc-frame list-sessions` prints it.
    pub runtime_session_id: String,
    /// `None` when the Frame runs but no session record claims it.
    pub owner: Option<FrameSessionOwner>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FrameSessionOwner {
    pub session_id: String,
    pub workspace_id: String,
}

#[derive(Debug)]
pub enum WorkspaceProjectionError {
    Read {
        path: String,
        source: std::io::Error,
    },
    Json {
        path: String,
        source: serde_json::Error,
    },
    Invalid(String),
}

impl fmt::Display for WorkspaceProjectionError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read { path, source } => write!(formatter, "cannot read {path}: {source}"),
            Self::Json { path, source } => write!(formatter, "invalid JSON in {path}: {source}"),
            Self::Invalid(message) => formatter.write_str(message),
        }
    }
}

impl std::error::Error for WorkspaceProjectionError {}

#[derive(Deserialize)]
struct CatalogWire {
    schema: String,
    selected_workspace_id: Option<String>,
    #[serde(default)]
    updated_at: String,
    workspaces: BTreeMap<String, WorkspaceRecord>,
}

impl ControlPlane {
    /// Load the canonical workspace catalog and bounded session records.
    ///
    /// A missing catalog is a valid first-install state. A malformed catalog
    /// or session fails closed so the UI cannot label partial data healthy.
    pub fn load_workspace_projection(
        &self,
    ) -> Result<WorkspaceProjection, WorkspaceProjectionError> {
        let root = self.control_plane_home().join("workspaces");
        let catalog_path = root.join("catalog.json");
        if !catalog_path.is_file() {
            let sessions_dir = root.join("sessions");
            if sessions_dir.is_dir() {
                let entries = fs::read_dir(&sessions_dir).map_err(|source| {
                    WorkspaceProjectionError::Read {
                        path: sessions_dir.display().to_string(),
                        source,
                    }
                })?;
                for entry in entries {
                    let path = entry
                        .map_err(|source| WorkspaceProjectionError::Read {
                            path: sessions_dir.display().to_string(),
                            source,
                        })?
                        .path();
                    if path.extension().is_some_and(|ext| ext == "json") {
                        return Err(WorkspaceProjectionError::Invalid(
                            "workspace sessions exist without the canonical catalog".into(),
                        ));
                    }
                }
            }
            return Ok(WorkspaceProjection {
                catalog: None,
                sessions: Vec::new(),
            });
        }

        let wire: CatalogWire = read_json(&catalog_path)?;
        validate_catalog(&wire)?;
        let mut workspaces = wire.workspaces.into_values().collect::<Vec<_>>();
        workspaces.sort_by(|left, right| {
            left.display_label
                .to_ascii_lowercase()
                .cmp(&right.display_label.to_ascii_lowercase())
                .then_with(|| left.workspace_id.cmp(&right.workspace_id))
        });

        let sessions_dir = root.join("sessions");
        let mut sessions = Vec::new();
        if sessions_dir.is_dir() {
            let entries =
                fs::read_dir(&sessions_dir).map_err(|source| WorkspaceProjectionError::Read {
                    path: sessions_dir.display().to_string(),
                    source,
                })?;
            let mut paths = Vec::new();
            for entry in entries {
                let entry = entry.map_err(|source| WorkspaceProjectionError::Read {
                    path: sessions_dir.display().to_string(),
                    source,
                })?;
                let path = entry.path();
                if path.extension().is_some_and(|ext| ext == "json") {
                    let metadata = fs::symlink_metadata(&path).map_err(|source| {
                        WorkspaceProjectionError::Read {
                            path: path.display().to_string(),
                            source,
                        }
                    })?;
                    if !metadata.file_type().is_file() {
                        return Err(WorkspaceProjectionError::Invalid(format!(
                            "workspace session is not a regular file: {}",
                            path.display()
                        )));
                    }
                    paths.push(path);
                }
            }
            if paths.len() > SESSION_LIMIT {
                return Err(WorkspaceProjectionError::Invalid(format!(
                    "workspace session count exceeds the bounded projection limit ({SESSION_LIMIT})"
                )));
            }
            paths.sort();
            for path in paths {
                let session: WorkspaceSession = read_json(&path)?;
                validate_session(&session)?;
                sessions.push(session);
            }
            sessions.sort_by(|left, right| right.updated_at.cmp(&left.updated_at));
        }

        Ok(WorkspaceProjection {
            catalog: Some(WorkspaceCatalogProjection {
                selected_workspace_id: wire.selected_workspace_id,
                updated_at: wire.updated_at,
                workspaces,
            }),
            sessions,
        })
    }
}

impl WorkspaceProjection {
    /// Socket roots recorded by vc-frame attachments — the only roots a
    /// liveness probe has to read.
    #[must_use]
    pub fn frame_socket_dirs(&self) -> BTreeSet<String> {
        self.sessions
            .iter()
            .flat_map(|session| session.attachments.iter())
            .filter(|attachment| attachment.runtime == FRAME_RUNTIME)
            .map(|attachment| attachment.socket_dir.trim().to_string())
            .filter(|dir| !dir.is_empty())
            .collect()
    }

    /// Every running Frame session in `inventory`, joined with the logical
    /// session that owns it.
    ///
    /// Ownership goes to the newest attachment (by `updated_at`) that
    /// recorded the same socket root and session name as `live`. Older live
    /// claims on that name — an earlier incarnation, or an earlier session
    /// bound to the same Frame — are superseded. `dead` and `missing`
    /// attachments are discovery evidence and never own a Frame. A running
    /// Frame nobody claims is still reported, with no owner.
    #[must_use]
    pub fn live_frame_sessions(&self, inventory: &FrameSessionInventory) -> Vec<LiveFrameSession> {
        let mut owners: BTreeMap<(String, String), (String, String, FrameSessionOwner)> =
            BTreeMap::new();
        for session in &self.sessions {
            for attachment in &session.attachments {
                if attachment.runtime != FRAME_RUNTIME
                    || attachment.state != "live"
                    || !inventory.is_running(&attachment.socket_dir, &attachment.runtime_session_id)
                {
                    continue;
                }
                let key = (
                    attachment.socket_dir.clone(),
                    attachment.runtime_session_id.clone(),
                );
                let rank = (
                    attachment.updated_at.clone(),
                    session.updated_at.clone(),
                    session.session_id.clone(),
                );
                if owners
                    .get(&key)
                    .is_some_and(|(updated_at, session_at, id)| {
                        (updated_at, session_at, &id.session_id) >= (&rank.0, &rank.1, &rank.2)
                    })
                {
                    continue;
                }
                owners.insert(
                    key,
                    (
                        rank.0,
                        rank.1,
                        FrameSessionOwner {
                            session_id: session.session_id.clone(),
                            workspace_id: session.workspace_id.clone(),
                        },
                    ),
                );
            }
        }
        inventory
            .iter()
            .map(|(socket_dir, name)| LiveFrameSession {
                socket_dir: socket_dir.to_string(),
                runtime_session_id: name.to_string(),
                owner: owners
                    .remove(&(socket_dir.to_string(), name.to_string()))
                    .map(|(_, _, owner)| owner),
            })
            .collect()
    }
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Result<T, WorkspaceProjectionError> {
    let bytes = fs::read(path).map_err(|source| WorkspaceProjectionError::Read {
        path: path.display().to_string(),
        source,
    })?;
    serde_json::from_slice(&bytes).map_err(|source| WorkspaceProjectionError::Json {
        path: path.display().to_string(),
        source,
    })
}

fn validate_catalog(wire: &CatalogWire) -> Result<(), WorkspaceProjectionError> {
    if wire.schema != CATALOG_SCHEMA {
        return Err(WorkspaceProjectionError::Invalid(format!(
            "unsupported workspace catalog schema: {}",
            wire.schema
        )));
    }
    for (key, workspace) in &wire.workspaces {
        if key != &workspace.workspace_id {
            return Err(WorkspaceProjectionError::Invalid(
                "workspace map key must equal workspace_id".into(),
            ));
        }
        if workspace.schema != WORKSPACE_SCHEMA
            || !canonical_uuid(&workspace.workspace_id)
            || workspace.display_label.trim().is_empty()
            || workspace.canonical_root.trim().is_empty()
            || !matches!(workspace.status.as_str(), "active" | "buried")
        {
            return Err(WorkspaceProjectionError::Invalid(format!(
                "invalid workspace record: {}",
                workspace.workspace_id
            )));
        }
    }
    if wire
        .selected_workspace_id
        .as_ref()
        .is_some_and(|selected| !wire.workspaces.contains_key(selected))
    {
        return Err(WorkspaceProjectionError::Invalid(
            "selected_workspace_id is not in catalog".into(),
        ));
    }
    Ok(())
}

fn validate_session(session: &WorkspaceSession) -> Result<(), WorkspaceProjectionError> {
    if session.schema != SESSION_SCHEMA
        || !canonical_uuid(&session.session_id)
        || !canonical_uuid(&session.workspace_id)
        || !canonical_uuid(&session.workspace_instance_id)
        || session.attachments.iter().any(|attachment| {
            attachment.runtime.trim().is_empty()
                || attachment.runtime_session_id.trim().is_empty()
                || !matches!(attachment.state.as_str(), "live" | "dead" | "missing")
        })
    {
        return Err(WorkspaceProjectionError::Invalid(format!(
            "invalid workspace session: {}",
            session.session_id
        )));
    }
    Ok(())
}

fn canonical_uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(index, byte)| match index {
            8 | 13 | 18 | 23 => byte == b'-',
            _ => byte.is_ascii_hexdigit(),
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home() -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        std::env::temp_dir().join(format!("vc-workspace-projection-{nonce}"))
    }

    #[test]
    fn canonical_catalog_and_sessions_are_projected_without_path_inference() {
        let home = temp_home();
        let root = home.join("control_plane/workspaces");
        fs::create_dir_all(root.join("sessions")).expect("workspace dirs");
        fs::write(
            root.join("catalog.json"),
            r#"{"schema":"vibecrafted.workspace-catalog.v1","updated_at":"2026-08-27T10:00:00Z","selected_workspace_id":"0198f84e-1234-7abc-8def-1234567890ab","workspaces":{"0198f84e-1234-7abc-8def-1234567890ab":{"schema":"vibecrafted.workspace.v1","workspace_id":"0198f84e-1234-7abc-8def-1234567890ab","display_label":"Vibecrafted","canonical_root":"/work/vibecrafted","status":"active","updated_at":"2026-08-27T10:00:00Z"}}}"#,
        )
        .expect("catalog");
        fs::write(
            root.join("sessions/0198f84e-2222-7abc-8def-1234567890ab.json"),
            r#"{"schema":"vibecrafted.workspace-session.v1","session_id":"0198f84e-2222-7abc-8def-1234567890ab","workspace_id":"0198f84e-1234-7abc-8def-1234567890ab","workspace_instance_id":"0198f84e-3333-7abc-8def-1234567890ab","updated_at":"2026-08-27T10:01:00Z","attachments":[{"runtime":"vc-frame","runtime_session_id":"frame-1","state":"live"}]}"#,
        )
        .expect("session");

        let projection = ControlPlane::new(&home)
            .load_workspace_projection()
            .expect("projection");
        let catalog = projection.catalog.expect("catalog");
        assert_eq!(catalog.workspaces[0].display_label, "Vibecrafted");
        assert_eq!(projection.sessions[0].attachments[0].state, "live");
        fs::remove_dir_all(home).ok();
    }

    #[test]
    fn malformed_catalog_fails_closed_instead_of_becoming_an_empty_healthy_view() {
        let home = temp_home();
        let root = home.join("control_plane/workspaces");
        fs::create_dir_all(&root).expect("workspace dir");
        fs::write(
            root.join("catalog.json"),
            r#"{"schema":"demo.workspace-catalog","workspaces":{}}"#,
        )
        .expect("catalog");

        let error = ControlPlane::new(&home)
            .load_workspace_projection()
            .expect_err("invalid catalog must fail");
        assert!(
            error
                .to_string()
                .contains("unsupported workspace catalog schema")
        );
        fs::remove_dir_all(home).ok();
    }

    /// Short socket root: macOS `sockaddr_un` holds 104 bytes, and the
    /// default TMPDIR plus `contract_version_N/<name>` already exhausts it.
    #[cfg(unix)]
    fn short_socket_root(tag: &str) -> std::path::PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .subsec_nanos();
        let root =
            std::path::PathBuf::from(format!("/tmp/vcfs-{tag}-{}-{nonce}", std::process::id()));
        fs::create_dir_all(root.join("contract_version_2")).expect("socket root");
        root
    }

    fn write_session_record(
        root: &Path,
        session_id: &str,
        workspace_id: &str,
        updated_at: &str,
        attachments: &str,
    ) {
        fs::write(
            root.join(format!("sessions/{session_id}.json")),
            format!(
                r#"{{"schema":"vibecrafted.workspace-session.v1","session_id":"{session_id}","workspace_id":"{workspace_id}","workspace_instance_id":"0198f84e-3333-7abc-8def-1234567890ab","updated_at":"{updated_at}","attachments":{attachments}}}"#
            ),
        )
        .expect("session");
    }

    #[cfg(unix)]
    #[test]
    fn frame_liveness_comes_from_the_socket_not_the_recorded_attachment_state() {
        use std::os::unix::net::UnixListener;

        let home = temp_home();
        let root = home.join("control_plane/workspaces");
        fs::create_dir_all(root.join("sessions")).expect("workspace dirs");
        fs::write(
            root.join("catalog.json"),
            r#"{"schema":"vibecrafted.workspace-catalog.v1","updated_at":"2026-08-27T10:00:00Z","selected_workspace_id":"0198f84e-1234-7abc-8def-1234567890ab","workspaces":{"0198f84e-1234-7abc-8def-1234567890ab":{"schema":"vibecrafted.workspace.v1","workspace_id":"0198f84e-1234-7abc-8def-1234567890ab","display_label":"Vibecrafted","canonical_root":"/work/vibecrafted","status":"active","updated_at":"2026-08-27T10:00:00Z"}}}"#,
        )
        .expect("catalog");
        let workspace = "0198f84e-1234-7abc-8def-1234567890ab";

        let sockets = short_socket_root("live");
        let socket_dir = sockets.to_str().expect("utf-8 socket root").to_string();
        let contract = sockets.join("contract_version_2");
        let _studio = UnixListener::bind(contract.join("studio")).expect("bind studio");
        let _services = UnixListener::bind(contract.join("services")).expect("bind services");
        fs::write(contract.join("plain-file"), "not a socket").expect("plain file");
        fs::create_dir_all(sockets.join("vc-frame-log")).expect("log dir");
        let _stray = UnixListener::bind(sockets.join("vc-frame-log/stray")).expect("bind stray");

        let attach = |name: &str, state: &str, updated_at: &str| {
            format!(
                r#"[{{"runtime":"vc-frame","runtime_session_id":"{name}","state":"{state}","socket_dir":"{socket_dir}","updated_at":"{updated_at}"}}]"#
            )
        };
        let older_studio = "0198f84e-aaaa-7abc-8def-000000000001";
        let newer_studio = "0198f84e-aaaa-7abc-8def-000000000002";
        write_session_record(
            &root,
            older_studio,
            workspace,
            "2026-09-12T10:00:00+00:00",
            &attach("studio", "live", "2026-09-12T10:00:00+00:00"),
        );
        write_session_record(
            &root,
            newer_studio,
            workspace,
            "2026-09-12T12:48:14+00:00",
            &attach("studio", "live", "2026-09-12T12:48:14+00:00"),
        );
        // Recorded `live`, but that Frame exited and nothing downgraded it.
        write_session_record(
            &root,
            "0198f84e-aaaa-7abc-8def-000000000003",
            workspace,
            "2026-09-13T09:00:00+00:00",
            &attach("gone", "live", "2026-09-13T09:00:00+00:00"),
        );
        // `services` runs; its only record is discovery evidence calling it dead.
        write_session_record(
            &root,
            "0198f84e-aaaa-7abc-8def-000000000004",
            workspace,
            "2026-09-13T10:00:00+00:00",
            &attach("services", "dead", "2026-09-13T10:00:00+00:00"),
        );

        let projection = ControlPlane::new(&home)
            .load_workspace_projection()
            .expect("projection");
        assert_eq!(
            projection.frame_socket_dirs(),
            BTreeSet::from([socket_dir.clone()])
        );

        let inventory = FrameSessionInventory::scan(projection.frame_socket_dirs());
        assert_eq!(
            inventory.iter().collect::<Vec<_>>(),
            vec![
                (socket_dir.as_str(), "services"),
                (socket_dir.as_str(), "studio")
            ],
            "only sockets directly under contract_version_* count"
        );
        assert!(!inventory.is_running(&socket_dir, "gone"));

        assert_eq!(
            projection.live_frame_sessions(&inventory),
            vec![
                LiveFrameSession {
                    socket_dir: socket_dir.clone(),
                    runtime_session_id: "services".into(),
                    owner: None,
                },
                LiveFrameSession {
                    socket_dir: socket_dir.clone(),
                    runtime_session_id: "studio".into(),
                    owner: Some(FrameSessionOwner {
                        session_id: newer_studio.into(),
                        workspace_id: workspace.into(),
                    }),
                },
            ]
        );

        fs::remove_dir_all(home).ok();
        fs::remove_dir_all(sockets).ok();
    }

    #[test]
    fn frame_inventory_of_missing_roots_is_empty_not_an_error() {
        let inventory = FrameSessionInventory::scan(["", "  ", "/nonexistent/vc-frame-probe"]);
        assert!(inventory.is_empty());
        assert_eq!(inventory.len(), 0);
        assert_eq!(
            FrameSessionInventory::from_running([("/tmp/vc-frame-1", "a")]).len(),
            1
        );
    }
}
