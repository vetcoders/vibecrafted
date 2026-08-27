//! Typed, read-only projection of the canonical workspace catalog and sessions.
//!
//! Python remains the sole writer. This module reads only the exact
//! `control_plane/workspaces/catalog.json` and `sessions/*.json` contracts; it
//! does not discover repositories or infer workspace identity from paths.

use std::collections::BTreeMap;
use std::fmt;
use std::fs;
use std::path::Path;

use serde::Deserialize;

use crate::ControlPlane;

const CATALOG_SCHEMA: &str = "vibecrafted.workspace-catalog.v1";
const WORKSPACE_SCHEMA: &str = "vibecrafted.workspace.v1";
const SESSION_SCHEMA: &str = "vibecrafted.workspace-session.v1";
const SESSION_LIMIT: usize = 200;

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
    pub state: String,
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
}
