//! Manifest-backed scaffold artifact contract shared by the doctor and server.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, Stdio};

use chrono::Utc;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const SCAFFOLD_SCHEMA_VERSION: &str = "1";
pub const SCAFFOLD_EXPORT_SCHEMA_VERSION: &str = "vibecrafted.scaffold-export.v1";
pub const SCAFFOLD_MANIFEST_SCHEMA_JSON: &str =
    include_str!("../schema/scaffold-manifest-v1.schema.json");

#[derive(Debug)]
pub enum ScaffoldError {
    Io(io::Error),
    Json(serde_json::Error),
    InvalidManifest { message: String },
    SelectionRequired { plan_ids: Vec<String> },
    ArtifactNotFound { id: String },
    Conflict { expected: String, actual: String },
    ReadOnly { message: String },
    UnsafePath { message: String },
}

impl fmt::Display for ScaffoldError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(error) => write!(formatter, "{error}"),
            Self::Json(error) => write!(formatter, "{error}"),
            Self::InvalidManifest { message }
            | Self::ReadOnly { message }
            | Self::UnsafePath { message } => formatter.write_str(message),
            Self::SelectionRequired { plan_ids } => write!(
                formatter,
                "scaffold plan selection required; available plans: {}",
                plan_ids.join(", ")
            ),
            Self::ArtifactNotFound { id } => write!(formatter, "artifact id not found: {id}"),
            Self::Conflict { expected, actual } => write!(
                formatter,
                "artifact changed since load (expected {expected}, actual {actual})"
            ),
        }
    }
}

impl std::error::Error for ScaffoldError {}

impl From<io::Error> for ScaffoldError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

impl From<serde_json::Error> for ScaffoldError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

pub type ScaffoldResult<T> = Result<T, ScaffoldError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ScaffoldArtifactRole {
    Driver,
    WaveAtlas,
    Dispatch,
    Brief,
    DesignDoc,
    Traceability,
    Tracker,
    Falsification,
    Report,
    Other,
}

impl ScaffoldArtifactRole {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Driver => "driver",
            Self::WaveAtlas => "wave-atlas",
            Self::Dispatch => "dispatch",
            Self::Brief => "brief",
            Self::DesignDoc => "design-doc",
            Self::Traceability => "traceability",
            Self::Tracker => "tracker",
            Self::Falsification => "falsification",
            Self::Report => "report",
            Self::Other => "other",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldArtifactDeclaration {
    pub id: String,
    pub role: ScaffoldArtifactRole,
    pub path: String,
    pub editable: bool,
    pub required: bool,
    #[serde(default)]
    pub dependencies: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldManifest {
    pub schema_version: String,
    pub plan_id: String,
    pub org: String,
    pub repo: String,
    pub day: String,
    pub artifacts: Vec<ScaffoldArtifactDeclaration>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldPlanSummary {
    pub plan_id: String,
    pub org: String,
    pub repo: String,
    pub day: String,
    pub plan_root: String,
    pub artifact_count: usize,
    pub legacy_read_only: bool,
}

/// A `manifest.json` that looked like a scaffold plan path but failed parse/identity.
///
/// Catalog surfaces must show these — silent skip is how plans "vanish" from
/// `/scaffold` (classic failure: illegal role string e.g. `"mission"` instead of
/// `"other"` for `MISSION.md`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldCatalogSkip {
    pub plan_root: String,
    pub reason: String,
    /// Best-effort guess from path segments when manifest is unreadable.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub guessed_plan_id: Option<String>,
}

/// Full catalog for operator UIs: valid plans + skipped (invalid) roots.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ScaffoldCatalog {
    pub plans: Vec<ScaffoldPlanSummary>,
    pub skipped: Vec<ScaffoldCatalogSkip>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ScaffoldCheckpoint {
    pub artifact_id: String,
    pub approved: bool,
    pub note: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldArtifact {
    pub id: String,
    pub title: String,
    pub role: ScaffoldArtifactRole,
    pub path: String,
    pub relative_path: String,
    pub editable: bool,
    pub required: bool,
    pub content: String,
    pub content_hash: String,
    pub bytes: usize,
    pub modified_at: String,
    pub checkpoint: ScaffoldCheckpoint,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldWorkspace {
    pub org: String,
    pub repo: String,
    pub day: String,
    pub plan_id: String,
    pub plan_root: String,
    pub legacy_read_only: bool,
    pub changes_path: String,
    pub checkpoints_path: String,
    pub artifacts: Vec<ScaffoldArtifact>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldExportArtifact {
    pub id: String,
    pub role: ScaffoldArtifactRole,
    pub relative_path: String,
    pub editable: bool,
    pub required: bool,
    pub content: String,
    pub content_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldExportBundle {
    pub schema_version: String,
    pub exported_at: String,
    pub manifest: ScaffoldManifest,
    pub artifacts: Vec<ScaffoldExportArtifact>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldArtifactPatch {
    pub artifact_id: String,
    pub content: String,
    pub expected_hash: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldCheckpointPatch {
    pub artifact_id: String,
    pub approved: bool,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldStatusPatch {
    pub artifact_id: String,
    #[serde(default)]
    pub item_id: Option<String>,
    #[serde(default)]
    pub item_index: Option<usize>,
    pub status: String,
    #[serde(default)]
    pub note: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldChange {
    pub ts: String,
    pub plan_id: String,
    pub artifact_id: String,
    pub relative_path: String,
    pub role: ScaffoldArtifactRole,
    pub action: String,
    pub bytes: usize,
    pub checkpointed: bool,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldDoctorError {
    /// Stable machine code, e.g. `driver_contract`, `frontmatter_missing`.
    pub code: String,
    /// Acceptance-rule id R1..R11 when the code maps to the skill gate.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rule: Option<String>,
    pub artifact_id: Option<String>,
    /// Relative path inside the plan root when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldDoctorReport {
    pub valid: bool,
    pub plan_id: String,
    pub plan_root: String,
    pub artifact_ids: Vec<String>,
    pub errors: Vec<ScaffoldDoctorError>,
}

impl ScaffoldDoctorReport {
    /// Whether the editor can safely construct a workspace from this report.
    ///
    /// Contract-quality findings remain visible without making the artifact
    /// package unreadable. Structural/path failures fail closed.
    #[must_use]
    pub fn workspace_reviewable(&self) -> bool {
        !self.errors.iter().any(workspace_fatal_error)
    }
}

/// Delivery-verifier command inventoried from a brief Gates section.
///
/// C4 only collects these. C5 is the execution owner — do not run them here.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScaffoldVerifierProbe {
    pub artifact_id: String,
    pub path: String,
    pub command: String,
}

/// Canonical 12 brief section markers (heading-substring, case-insensitive).
/// Matches vc-scaffold Phase 5 + the fixture convention (Verification as #6).
const BRIEF_SECTIONS: &[&str] = &[
    "mission",
    "context",
    "files",
    "acceptance",
    "gates",
    "verification",
    "out of scope",
    "living tree",
    "loctree",
    "recovery",
    "branch",
    "report",
];

const FRONTMATTER_REQUIRED: &[&str] =
    &["plan_id", "session_id", "role", "agent", "date", "project"];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
struct CheckpointStore {
    #[serde(default)]
    artifacts: BTreeMap<String, ScaffoldCheckpoint>,
}

#[derive(Debug, Clone)]
pub struct ScaffoldArtifactStore {
    home: PathBuf,
}

impl ScaffoldArtifactStore {
    #[must_use]
    pub fn new(home: impl Into<PathBuf>) -> Self {
        Self { home: home.into() }
    }

    fn day_root(&self, org: &str, repo: &str, day: &str) -> ScaffoldResult<PathBuf> {
        validate_path_segment(org, "org")?;
        validate_path_segment(repo, "repo")?;
        validate_path_segment(day, "day")?;
        Ok(self.home.join("artifacts").join(org).join(repo).join(day))
    }

    fn plan_root(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
    ) -> ScaffoldResult<PathBuf> {
        validate_path_segment(plan_id, "plan_id")?;
        Ok(self.day_root(org, repo, day)?.join("plans").join(plan_id))
    }

    pub fn plans(
        &self,
        org: &str,
        repo: &str,
        day: &str,
    ) -> ScaffoldResult<Vec<ScaffoldPlanSummary>> {
        let plans_root = self.day_root(org, repo, day)?.join("plans");
        let mut plans = Vec::new();
        let Ok(entries) = fs::read_dir(plans_root) else {
            return Ok(plans);
        };
        for entry in entries.flatten() {
            let root = entry.path();
            if !entry.file_type().is_ok_and(|kind| kind.is_dir())
                || !root.join("manifest.json").is_file()
            {
                continue;
            }
            let Ok(manifest) = read_manifest(&root) else {
                continue;
            };
            if manifest.org != org || manifest.repo != repo || manifest.day != day {
                continue;
            }
            plans.push(ScaffoldPlanSummary {
                plan_id: manifest.plan_id,
                org: org.to_string(),
                repo: repo.to_string(),
                day: day.to_string(),
                plan_root: root.display().to_string(),
                artifact_count: manifest.artifacts.len(),
                legacy_read_only: false,
            });
        }
        plans.sort_by(|left, right| left.plan_id.cmp(&right.plan_id));
        Ok(plans)
    }

    /// Every valid manifest-backed scaffold plan visible to the runtime.
    ///
    /// The artifacts tree can also contain unrelated `manifest.json` files
    /// (for example Loctree context atlases). Those are deliberately ignored:
    /// this catalog is scaffold truth, not a filename census.
    ///
    /// Prefer [`Self::catalog_detailed`] when the UI must surface parse failures
    /// (silent skip is how operators lose plans).
    #[must_use]
    pub fn catalog(&self) -> Vec<ScaffoldPlanSummary> {
        self.catalog_detailed().plans
    }

    /// Valid plans plus roots that look like scaffold plans but failed to load.
    #[must_use]
    pub fn catalog_detailed(&self) -> ScaffoldCatalog {
        let mut manifest_paths = Vec::new();
        collect_scaffold_manifest_paths(&self.home.join("artifacts"), &mut manifest_paths);
        let mut plans = Vec::new();
        let mut skipped = Vec::new();
        for path in manifest_paths {
            let Some(root) = path.parent() else {
                continue;
            };
            match read_manifest(root) {
                Ok(manifest) => {
                    if let Err(message) = validate_plan_root_identity(root, &manifest) {
                        skipped.push(ScaffoldCatalogSkip {
                            plan_root: root.display().to_string(),
                            reason: message,
                            guessed_plan_id: Some(manifest.plan_id),
                        });
                        continue;
                    }
                    plans.push(ScaffoldPlanSummary {
                        plan_id: manifest.plan_id,
                        org: manifest.org,
                        repo: manifest.repo,
                        day: manifest.day,
                        plan_root: root.display().to_string(),
                        artifact_count: manifest.artifacts.len(),
                        legacy_read_only: false,
                    });
                }
                Err(error) => {
                    // `collect_scaffold_manifest_paths` only walks …/plans/<id>/manifest.json,
                    // so failures here are broken scaffold packages — never silent.
                    skipped.push(ScaffoldCatalogSkip {
                        plan_root: root.display().to_string(),
                        reason: format!(
                            "manifest unreadable: {error} — check schema_version, plan_id/org/repo/day, and role enum (driver|wave-atlas|dispatch|brief|design-doc|traceability|tracker|falsification|report|other). Note: use role \"other\" for MISSION.md — \"mission\" is not a valid manifest role."
                        ),
                        guessed_plan_id: path
                            .parent()
                            .and_then(|p| p.file_name())
                            .map(|n| n.to_string_lossy().into_owned()),
                    });
                }
            }
        }
        plans.sort_by(|left, right| {
            right
                .day
                .cmp(&left.day)
                .then_with(|| left.org.cmp(&right.org))
                .then_with(|| left.repo.cmp(&right.repo))
                .then_with(|| left.plan_id.cmp(&right.plan_id))
        });
        skipped.sort_by(|left, right| left.plan_root.cmp(&right.plan_root));
        ScaffoldCatalog { plans, skipped }
    }

    pub fn latest_workspace(&self) -> ScaffoldResult<ScaffoldWorkspace> {
        let plans = self.catalog();
        if plans.len() != 1 {
            let plan_ids = plans.into_iter().map(|plan| plan.plan_id).collect();
            return Err(ScaffoldError::SelectionRequired { plan_ids });
        }
        let plan = &plans[0];
        self.workspace(&plan.org, &plan.repo, &plan.day, Some(&plan.plan_id))
    }

    /// Cheap structural verdict used by catalog surfaces.
    ///
    /// This intentionally avoids the full doctor/content pass. The editor
    /// reads and validates content only after an operator selects one plan.
    #[must_use]
    pub fn is_plan_reviewable(&self, org: &str, repo: &str, day: &str, plan_id: &str) -> bool {
        let Ok(root) = self.plan_root(org, repo, day, plan_id) else {
            return false;
        };
        let Ok(manifest) = read_manifest(&root) else {
            return false;
        };
        if validate_identity(&manifest, org, repo, day, plan_id).is_err() {
            return false;
        }

        let mut ids = BTreeSet::new();
        let mut paths = BTreeSet::new();
        manifest.artifacts.iter().all(|artifact| {
            if !ids.insert(&artifact.id) || !paths.insert(&artifact.path) {
                return false;
            }
            let Ok(path) = declared_path(&root, artifact) else {
                return false;
            };
            path.is_file() && (!artifact.editable || !path_has_symlink(&root, &path))
        })
    }

    pub fn workspace(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: Option<&str>,
    ) -> ScaffoldResult<ScaffoldWorkspace> {
        let plans = self.plans(org, repo, day)?;
        let selected = match plan_id {
            Some(requested) => plans
                .iter()
                .find(|plan| plan.plan_id == requested)
                .cloned()
                .ok_or_else(|| ScaffoldError::InvalidManifest {
                    message: format!("manifest-backed scaffold plan not found: {requested}"),
                })?,
            None if plans.len() == 1 => plans[0].clone(),
            None if plans.is_empty() => return self.legacy_workspace(org, repo, day),
            None => {
                return Err(ScaffoldError::SelectionRequired {
                    plan_ids: plans.into_iter().map(|plan| plan.plan_id).collect(),
                });
            }
        };
        self.manifest_workspace(org, repo, day, &selected.plan_id)
    }

    /// Export a manifest-backed plan without author-host filesystem identity.
    ///
    /// Artifact paths are always relative. Known host roots inside content are
    /// replaced with stable placeholders; any remaining `/Users/...` or
    /// `/Volumes/...` token fails closed instead of shipping a deceptive bundle.
    pub fn export_bundle(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        repo_root: Option<&str>,
    ) -> ScaffoldResult<ScaffoldExportBundle> {
        let root = self.plan_root(org, repo, day, plan_id)?;
        let manifest = read_manifest(&root)?;
        validate_identity(&manifest, org, repo, day, plan_id)?;
        let workspace = self.manifest_workspace(org, repo, day, plan_id)?;
        let plan_root = root.display().to_string();
        let home = self.home.display().to_string();
        let mut artifacts = Vec::with_capacity(workspace.artifacts.len());
        for artifact in workspace.artifacts {
            let content =
                portable_scaffold_content(&artifact.content, &plan_root, &home, repo_root);
            if let Some(path) = first_private_absolute_path(&content) {
                return Err(ScaffoldError::UnsafePath {
                    message: format!(
                        "portable export still contains host path {path:?} in artifact {}; pass repo_root when exporting or replace the undeclared host path",
                        artifact.id
                    ),
                });
            }
            artifacts.push(ScaffoldExportArtifact {
                id: artifact.id,
                role: artifact.role,
                relative_path: artifact.relative_path,
                editable: artifact.editable,
                required: artifact.required,
                content_hash: content_hash(content.as_bytes()),
                content,
            });
        }
        Ok(ScaffoldExportBundle {
            schema_version: SCAFFOLD_EXPORT_SCHEMA_VERSION.to_string(),
            exported_at: Utc::now().to_rfc3339(),
            manifest,
            artifacts,
        })
    }

    fn manifest_workspace(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
    ) -> ScaffoldResult<ScaffoldWorkspace> {
        let root = self.plan_root(org, repo, day, plan_id)?;
        let manifest = read_manifest(&root)?;
        validate_identity(&manifest, org, repo, day, plan_id)?;
        let report = validate_manifest_plan(&root, &manifest);
        if report.errors.iter().any(workspace_fatal_error) {
            return Err(ScaffoldError::InvalidManifest {
                message: report
                    .errors
                    .iter()
                    .map(|error| error.message.as_str())
                    .collect::<Vec<_>>()
                    .join("; "),
            });
        }
        let checkpoints = read_checkpoints(&checkpoint_path(&root));
        let mut artifacts = Vec::with_capacity(manifest.artifacts.len());
        for declaration in manifest.artifacts {
            let path = declared_path(&root, &declaration)?;
            let content = fs::read_to_string(&path)?;
            let checkpoint = checkpoints
                .artifacts
                .get(&declaration.id)
                .cloned()
                .unwrap_or_else(|| ScaffoldCheckpoint {
                    artifact_id: declaration.id.clone(),
                    ..ScaffoldCheckpoint::default()
                });
            artifacts.push(ScaffoldArtifact {
                id: declaration.id.clone(),
                title: artifact_title(&declaration.path, declaration.role),
                role: declaration.role,
                path: path.display().to_string(),
                relative_path: declaration.path,
                editable: declaration.editable,
                required: declaration.required,
                bytes: content.len(),
                content_hash: content_hash(content.as_bytes()),
                content,
                modified_at: modified_at(&path),
                checkpoint,
            });
        }
        Ok(ScaffoldWorkspace {
            org: org.to_string(),
            repo: repo.to_string(),
            day: day.to_string(),
            plan_id: plan_id.to_string(),
            plan_root: root.display().to_string(),
            legacy_read_only: false,
            changes_path: changes_path(&root).display().to_string(),
            checkpoints_path: checkpoint_path(&root).display().to_string(),
            artifacts,
        })
    }

    fn legacy_workspace(
        &self,
        org: &str,
        repo: &str,
        day: &str,
    ) -> ScaffoldResult<ScaffoldWorkspace> {
        let root = self.day_root(org, repo, day)?.join("operator");
        if !root.is_dir() {
            return Err(ScaffoldError::InvalidManifest {
                message: "no manifest-backed scaffold plan found".into(),
            });
        }
        let checkpoints = read_checkpoints(&checkpoint_path(&root));
        let mut paths = discover_legacy_paths(&root);
        paths.sort();
        let mut artifacts = Vec::new();
        for path in paths {
            let relative = relative_string(&root, &path)?;
            let content = fs::read_to_string(&path)?;
            let id = legacy_artifact_id(&relative);
            let role = legacy_role(&relative);
            artifacts.push(ScaffoldArtifact {
                checkpoint: checkpoints.artifacts.get(&id).cloned().unwrap_or_default(),
                id,
                title: artifact_title(&relative, role),
                role,
                path: path.display().to_string(),
                relative_path: relative,
                editable: false,
                required: false,
                bytes: content.len(),
                content_hash: content_hash(content.as_bytes()),
                content,
                modified_at: modified_at(&path),
            });
        }
        Ok(ScaffoldWorkspace {
            org: org.to_string(),
            repo: repo.to_string(),
            day: day.to_string(),
            plan_id: "legacy-operator".into(),
            plan_root: root.display().to_string(),
            legacy_read_only: true,
            changes_path: changes_path(&root).display().to_string(),
            checkpoints_path: checkpoint_path(&root).display().to_string(),
            artifacts,
        })
    }

    pub fn doctor(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
    ) -> ScaffoldResult<ScaffoldDoctorReport> {
        self.doctor_with_repo(org, repo, day, plan_id, None)
    }

    /// Same as [`Self::doctor`], with an explicit git checkout for C4 geometry.
    pub fn doctor_with_repo(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        repo_root: Option<&Path>,
    ) -> ScaffoldResult<ScaffoldDoctorReport> {
        let root = self.plan_root(org, repo, day, plan_id)?;
        let manifest = read_manifest(&root)?;
        validate_identity(&manifest, org, repo, day, plan_id)?;
        let mut report = validate_manifest_plan(&root, &manifest);
        // Path identity (R1) when invoked via store coordinates.
        if let Err(message) = validate_plan_root_identity(&root, &manifest) {
            doctor_error(
                &mut report.errors,
                "identity_mismatch",
                Some("R1"),
                None,
                None,
                &message,
            );
        }
        apply_plan_geometry(&mut report, &root, &manifest, repo_root);
        report.valid = report.errors.is_empty();
        Ok(report)
    }

    /// Doctor a plan by absolute plan root (`…/plans/<plan_id>/`).
    ///
    /// Refuses cleanly when the path is not a directory or has no `manifest.json`
    /// — never panics or stack-traces for non-plan inputs.
    pub fn doctor_plan_root(plan_root: impl AsRef<Path>) -> ScaffoldResult<ScaffoldDoctorReport> {
        doctor_plan_root(plan_root)
    }

    pub fn write_artifact(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        patch: ScaffoldArtifactPatch,
    ) -> ScaffoldResult<ScaffoldArtifact> {
        let workspace = self.workspace(org, repo, day, Some(plan_id))?;
        if workspace.legacy_read_only {
            return Err(ScaffoldError::ReadOnly {
                message: "legacy scaffold workspaces are read-only".into(),
            });
        }
        let artifact = workspace
            .artifacts
            .iter()
            .find(|artifact| artifact.id == patch.artifact_id)
            .ok_or_else(|| ScaffoldError::ArtifactNotFound {
                id: patch.artifact_id.clone(),
            })?;
        if !artifact.editable {
            return Err(ScaffoldError::ReadOnly {
                message: format!("artifact is not editable: {}", artifact.id),
            });
        }
        let actual = content_hash(fs::read(&artifact.path)?.as_slice());
        if patch.expected_hash != actual {
            return Err(ScaffoldError::Conflict {
                expected: patch.expected_hash,
                actual,
            });
        }
        let root = PathBuf::from(&workspace.plan_root);
        let path = root.join(validate_relative_markdown_path(&artifact.relative_path)?);
        reject_symlink_path(&root, &path)?;
        write_atomic(&path, patch.content.as_bytes())?;
        let refreshed = self
            .workspace(org, repo, day, Some(plan_id))?
            .artifacts
            .into_iter()
            .find(|candidate| candidate.id == artifact.id)
            .ok_or_else(|| ScaffoldError::ArtifactNotFound {
                id: artifact.id.clone(),
            })?;
        append_change(
            &root,
            ScaffoldChange {
                ts: now_ts(),
                plan_id: plan_id.to_string(),
                artifact_id: refreshed.id.clone(),
                relative_path: refreshed.relative_path.clone(),
                role: refreshed.role,
                action: "edit".into(),
                bytes: refreshed.bytes,
                checkpointed: refreshed.checkpoint.approved,
                note: String::new(),
            },
        )?;
        emit_scaffold_control_event(
            &self.home,
            "scaffold.artifact.saved",
            plan_id,
            &format!("Scaffold artifact {} saved", refreshed.id),
            serde_json::json!({
                "org": org,
                "repo": repo,
                "day": day,
                "plan_id": plan_id,
                "artifact_id": refreshed.id,
                "role": refreshed.role.as_str(),
                "relative_path": refreshed.relative_path,
                "bytes": refreshed.bytes,
            }),
        );
        Ok(refreshed)
    }

    pub fn checkpoint(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        patch: ScaffoldCheckpointPatch,
    ) -> ScaffoldResult<ScaffoldCheckpoint> {
        let workspace = self.workspace(org, repo, day, Some(plan_id))?;
        let artifact = workspace
            .artifacts
            .iter()
            .find(|artifact| artifact.id == patch.artifact_id)
            .ok_or_else(|| ScaffoldError::ArtifactNotFound {
                id: patch.artifact_id.clone(),
            })?;
        if workspace.legacy_read_only {
            return Err(ScaffoldError::ReadOnly {
                message: "legacy scaffold workspaces are read-only".into(),
            });
        }
        let root = PathBuf::from(&workspace.plan_root);
        let mut store = read_checkpoints(&checkpoint_path(&root));
        let checkpoint = ScaffoldCheckpoint {
            artifact_id: artifact.id.clone(),
            approved: patch.approved,
            note: patch.note,
            updated_at: now_ts(),
        };
        store
            .artifacts
            .insert(checkpoint.artifact_id.clone(), checkpoint.clone());
        write_checkpoints(&checkpoint_path(&root), &store)?;
        append_change(
            &root,
            ScaffoldChange {
                ts: checkpoint.updated_at.clone(),
                plan_id: plan_id.to_string(),
                artifact_id: artifact.id.clone(),
                relative_path: artifact.relative_path.clone(),
                role: artifact.role,
                action: "checkpoint".into(),
                bytes: artifact.bytes,
                checkpointed: checkpoint.approved,
                note: checkpoint.note.clone(),
            },
        )?;
        emit_scaffold_control_event(
            &self.home,
            "scaffold.checkpoint.saved",
            plan_id,
            &format!(
                "Scaffold artifact {} checkpointed (approved: {})",
                artifact.id, checkpoint.approved
            ),
            serde_json::json!({
                "org": org,
                "repo": repo,
                "day": day,
                "plan_id": plan_id,
                "artifact_id": artifact.id,
                "role": artifact.role.as_str(),
                "approved": checkpoint.approved,
                "note": checkpoint.note,
            }),
        );
        Ok(checkpoint)
    }

    pub fn write_status(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
        patch: ScaffoldStatusPatch,
    ) -> ScaffoldResult<ScaffoldArtifact> {
        let workspace = self.workspace(org, repo, day, Some(plan_id))?;
        if workspace.legacy_read_only {
            return Err(ScaffoldError::ReadOnly {
                message: "legacy scaffold workspaces are read-only".into(),
            });
        }
        let artifact = workspace
            .artifacts
            .iter()
            .find(|artifact| artifact.id == patch.artifact_id)
            .ok_or_else(|| ScaffoldError::ArtifactNotFound {
                id: patch.artifact_id.clone(),
            })?;
        if !artifact.editable {
            return Err(ScaffoldError::ReadOnly {
                message: format!("artifact is not editable: {}", artifact.id),
            });
        }

        let root = PathBuf::from(&workspace.plan_root);
        let path = root.join(validate_relative_markdown_path(&artifact.relative_path)?);
        reject_symlink_path(&root, &path)?;

        let current_content = fs::read_to_string(&path)?;
        let updated_content = update_markdown_status(
            &current_content,
            patch.item_id.as_deref(),
            patch.item_index,
            &patch.status,
        );

        write_atomic(&path, updated_content.as_bytes())?;

        let refreshed = self
            .workspace(org, repo, day, Some(plan_id))?
            .artifacts
            .into_iter()
            .find(|candidate| candidate.id == artifact.id)
            .ok_or_else(|| ScaffoldError::ArtifactNotFound {
                id: artifact.id.clone(),
            })?;

        let note = patch.note.clone().unwrap_or_default();
        let change_note = if let Some(item) = &patch.item_id {
            format!("item: {item}, status: {}, note: {note}", patch.status)
        } else {
            format!("status: {}, note: {note}", patch.status)
        };

        append_change(
            &root,
            ScaffoldChange {
                ts: now_ts(),
                plan_id: plan_id.to_string(),
                artifact_id: refreshed.id.clone(),
                relative_path: refreshed.relative_path.clone(),
                role: refreshed.role,
                action: "status".into(),
                bytes: refreshed.bytes,
                checkpointed: refreshed.checkpoint.approved,
                note: change_note,
            },
        )?;

        emit_scaffold_control_event(
            &self.home,
            "scaffold.status.updated",
            plan_id,
            &format!(
                "Scaffold artifact {} status updated to {}",
                refreshed.id, patch.status
            ),
            serde_json::json!({
                "org": org,
                "repo": repo,
                "day": day,
                "plan_id": plan_id,
                "artifact_id": refreshed.id,
                "item_id": patch.item_id,
                "item_index": patch.item_index,
                "status": patch.status,
                "note": patch.note,
                "relative_path": refreshed.relative_path,
                "role": refreshed.role.as_str(),
            }),
        );

        Ok(refreshed)
    }

    pub fn changes(
        &self,
        org: &str,
        repo: &str,
        day: &str,
        plan_id: &str,
    ) -> ScaffoldResult<Vec<ScaffoldChange>> {
        let root = self.plan_root(org, repo, day, plan_id)?;
        let Ok(text) = fs::read_to_string(changes_path(&root)) else {
            return Ok(Vec::new());
        };
        Ok(text
            .lines()
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect())
    }
}

fn workspace_fatal_error(error: &ScaffoldDoctorError) -> bool {
    matches!(
        error.code.as_str(),
        "duplicate_artifact_id"
            | "duplicate_artifact_path"
            | "missing_required_artifact"
            | "missing_manifest_artifact"
            | "path_escape"
            | "writable_symlink"
    )
}

fn read_manifest(root: &Path) -> ScaffoldResult<ScaffoldManifest> {
    Ok(serde_json::from_slice(&fs::read(
        root.join("manifest.json"),
    )?)?)
}

/// Free-function entry used by the `scaffold-doctor` binary and server.
pub fn doctor_plan_root(plan_root: impl AsRef<Path>) -> ScaffoldResult<ScaffoldDoctorReport> {
    doctor_plan_root_in_repo(plan_root, None::<&Path>)
}

/// Doctor a plan against an explicit git checkout (C4 geometry).
///
/// `repo_root = None` discovers the checkout from mission/DRIVER, then cwd.
/// An explicit path that is not a git work tree is fail-closed (no cwd fallback).
pub fn doctor_plan_root_in_repo(
    plan_root: impl AsRef<Path>,
    repo_root: Option<impl AsRef<Path>>,
) -> ScaffoldResult<ScaffoldDoctorReport> {
    let root = plan_root.as_ref();
    if !root.is_dir() {
        return Err(ScaffoldError::InvalidManifest {
            message: format!(
                "refusing: not a plan directory (path does not exist or is not a directory): {}",
                root.display()
            ),
        });
    }
    if !root.join("manifest.json").is_file() {
        return Err(ScaffoldError::InvalidManifest {
            message: format!(
                "refusing: no manifest.json under {} — not a scaffold plan root",
                root.display()
            ),
        });
    }
    let manifest = read_manifest(root)?;
    let mut report = validate_manifest_plan(root, &manifest);
    if let Err(message) = validate_plan_root_identity(root, &manifest) {
        doctor_error(
            &mut report.errors,
            "identity_mismatch",
            Some("R1"),
            None,
            None,
            &message,
        );
    }
    apply_plan_geometry(
        &mut report,
        root,
        &manifest,
        repo_root.as_ref().map(AsRef::as_ref),
    );
    crate::scaffold_verifiers::execute_brief_verifiers(root, &manifest, &mut report.errors);
    report.valid = report.errors.is_empty();
    Ok(report)
}

/// C4 geometry: named git baseline must be an ancestor of HEAD, and every
/// repo path named in mission/DRIVER/brief Files must exist on HEAD.
///
/// Local `git merge-base --is-ancestor` / `git cat-file` only — no network.
/// C5 may call this after its own verifier inventory.
pub fn apply_plan_geometry(
    report: &mut ScaffoldDoctorReport,
    plan_root: &Path,
    manifest: &ScaffoldManifest,
    repo_root: Option<&Path>,
) {
    let sources = geometry_source_texts(plan_root, manifest);
    let baselines = collect_named_baseline_shas(&sources);
    let named_paths = collect_named_repo_paths(&sources);
    if baselines.is_empty() && named_paths.is_empty() {
        return;
    }

    let resolved = resolve_geometry_repo(plan_root, &sources, repo_root);
    let Some(repo) = resolved else {
        if !baselines.is_empty() {
            doctor_error(
                &mut report.errors,
                "baseline_repo_unresolved",
                Some("C4"),
                None,
                None,
                &format!(
                    "mission/DRIVER names baseline SHA(s) {} but no git checkout was found (pass --repo)",
                    baselines.join(", ")
                ),
            );
        }
        if !named_paths.is_empty() {
            doctor_error(
                &mut report.errors,
                "named_path_repo_unresolved",
                Some("C4"),
                None,
                None,
                &format!(
                    "plan/briefs name repo path(s) {} but no git checkout was found (pass --repo)",
                    named_paths
                        .iter()
                        .take(6)
                        .cloned()
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            );
        }
        return;
    };

    for sha in &baselines {
        match git_is_ancestor_of_head(&repo, sha) {
            Ok(true) => {}
            Ok(false) => doctor_error(
                &mut report.errors,
                "baseline_not_ancestor",
                Some("C4"),
                None,
                None,
                &format!(
                    "named baseline {sha} is not an ancestor of HEAD (`git merge-base --is-ancestor`)"
                ),
            ),
            Err(message) => doctor_error(
                &mut report.errors,
                "baseline_unknown",
                Some("C4"),
                None,
                None,
                &message,
            ),
        }
    }

    for rel in &named_paths {
        if !git_path_exists_on_head(&repo, rel) {
            doctor_error(
                &mut report.errors,
                "named_path_missing",
                Some("C4"),
                None,
                Some(rel),
                &format!("named path `{rel}` is missing on HEAD"),
            );
        }
    }
}

/// Collect delivery-verifier command lines from briefs. C5 executes them.
#[must_use]
pub fn collect_delivery_verifiers(
    plan_root: impl AsRef<Path>,
    manifest: &ScaffoldManifest,
) -> Vec<ScaffoldVerifierProbe> {
    let root = plan_root.as_ref();
    let mut probes = Vec::new();
    for artifact in &manifest.artifacts {
        if artifact.role != ScaffoldArtifactRole::Brief {
            continue;
        }
        let Ok(path) = declared_path(root, artifact) else {
            continue;
        };
        let Ok(content) = fs::read_to_string(&path) else {
            continue;
        };
        let Some(gates) = section_body(&content, "gates") else {
            continue;
        };
        for line in gates.lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() || trimmed.starts_with('#') || trimmed.starts_with("```") {
                continue;
            }
            if has_verifier_command(trimmed) {
                let command = trimmed.trim_start_matches('$').trim().to_string();
                probes.push(ScaffoldVerifierProbe {
                    artifact_id: artifact.id.clone(),
                    path: artifact.path.clone(),
                    command,
                });
            }
        }
    }
    probes
}

struct GeometrySources {
    mission_driver: Vec<(String, String)>,
    briefs: Vec<(String, String)>,
}

fn geometry_source_texts(plan_root: &Path, manifest: &ScaffoldManifest) -> GeometrySources {
    let mut mission_driver = Vec::new();
    let mut briefs = Vec::new();
    for artifact in &manifest.artifacts {
        let Ok(path) = declared_path(plan_root, artifact) else {
            continue;
        };
        let Ok(content) = fs::read_to_string(&path) else {
            continue;
        };
        if is_mission_or_driver(artifact, &content) {
            mission_driver.push((artifact.id.clone(), content));
        } else if artifact.role == ScaffoldArtifactRole::Brief {
            briefs.push((artifact.id.clone(), content));
        }
    }
    GeometrySources {
        mission_driver,
        briefs,
    }
}

fn is_mission_or_driver(artifact: &ScaffoldArtifactDeclaration, content: &str) -> bool {
    if artifact.role == ScaffoldArtifactRole::Driver {
        return true;
    }
    if artifact.id.eq_ignore_ascii_case("mission") {
        return true;
    }
    let file = artifact
        .path
        .rsplit('/')
        .next()
        .unwrap_or(artifact.path.as_str());
    if file.eq_ignore_ascii_case("mission.md") {
        return true;
    }
    parse_frontmatter(content).is_some_and(|frontmatter| {
        frontmatter
            .get("role")
            .is_some_and(|role| role.eq_ignore_ascii_case("mission"))
    })
}

fn collect_named_baseline_shas(sources: &GeometrySources) -> Vec<String> {
    let mut shas = BTreeSet::new();
    for (_, content) in &sources.mission_driver {
        extract_named_baseline_shas(content, &mut shas);
    }
    shas.into_iter().collect()
}

fn collect_named_repo_paths(sources: &GeometrySources) -> Vec<String> {
    let mut paths = BTreeSet::new();
    for (_, content) in &sources.mission_driver {
        extract_repo_paths_from_text(content, false, &mut paths);
    }
    for (_, content) in &sources.briefs {
        if let Some(files) = section_body(content, "files") {
            extract_repo_paths_from_text(&files, true, &mut paths);
        }
    }
    paths.into_iter().collect()
}

const BASELINE_MARKERS: &[&str] = &[
    "authoring baseline",
    "original baseline",
    "reference baseline",
    "operator_chosen_baseline",
    "orientation head",
    "handoff baseline",
    "parent_branch",
    "baseline_branch",
    "baseline_sha",
    "git baseline",
    "scaffold baseline",
];

fn extract_named_baseline_shas(content: &str, shas: &mut BTreeSet<String>) {
    if let Some(frontmatter) = parse_frontmatter(content) {
        for key in [
            "baseline_sha",
            "baseline",
            "parent_branch",
            "baseline_branch",
            "git_baseline",
        ] {
            if let Some(value) = frontmatter.get(key) {
                collect_git_shas(value, shas);
            }
        }
    }
    for line in content.lines() {
        collect_baseline_shas_from_line(line, shas);
    }
}

fn collect_baseline_shas_from_line(line: &str, shas: &mut BTreeSet<String>) {
    let lower = line.to_ascii_lowercase();
    let mut starts = Vec::new();
    for marker in BASELINE_MARKERS {
        let mut offset = 0usize;
        while let Some(idx) = lower[offset..].find(marker) {
            starts.push(offset + idx + marker.len());
            offset += idx + marker.len();
        }
    }
    if starts.is_empty() {
        if let Some(idx) = word_index(&lower, "baseline") {
            starts.push(idx + "baseline".len());
        }
    }
    for start in starts {
        if start <= line.len() {
            collect_git_shas(&line[start..], shas);
        }
    }
}

fn word_index(haystack: &str, word: &str) -> Option<usize> {
    let mut offset = 0usize;
    for token in haystack.split_inclusive(|c: char| !c.is_ascii_alphanumeric() && c != '_') {
        let trimmed = token.trim_end_matches(|c: char| !c.is_ascii_alphanumeric() && c != '_');
        if trimmed == word {
            return Some(offset);
        }
        offset += token.len();
    }
    None
}

fn collect_git_shas(text: &str, shas: &mut BTreeSet<String>) {
    let bytes = text.as_bytes();
    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index].is_ascii_hexdigit() {
            let start = index;
            while index < bytes.len() && bytes[index].is_ascii_hexdigit() {
                index += 1;
            }
            let token = &text[start..index];
            if is_git_sha(token) && !preceded_by_sha256(&text[..start]) {
                shas.insert(token.to_ascii_lowercase());
            }
        } else {
            index += 1;
        }
    }
}

fn preceded_by_sha256(prefix: &str) -> bool {
    let trimmed = prefix.trim_end_matches(|c: char| c == ':' || c.is_ascii_whitespace());
    trimmed
        .len()
        .checked_sub(6)
        .is_some_and(|start| trimmed[start..].eq_ignore_ascii_case("sha256"))
}

fn is_git_sha(token: &str) -> bool {
    let len = token.len();
    (7..=40).contains(&len)
        && token.bytes().all(|byte| byte.is_ascii_hexdigit())
        && token.bytes().any(|byte| byte.is_ascii_digit())
}

fn extract_repo_paths_from_text(text: &str, files_section: bool, paths: &mut BTreeSet<String>) {
    for line in text.lines() {
        if is_plan_store_line(line) {
            continue;
        }
        for span in backtick_spans(line) {
            if let Some(path) = normalize_named_path(&span) {
                if !files_section || !path_marked_create_only(line, &span) {
                    paths.insert(path);
                }
            }
        }
        if files_section {
            let trimmed = line.trim_start();
            let item = trimmed
                .strip_prefix('-')
                .or_else(|| trimmed.strip_prefix('*'))
                .map(str::trim);
            if let Some(item) = item {
                let token = item
                    .trim_start_matches(['[', ']', 'x', 'X', '~', '?', '!', ' '])
                    .split_whitespace()
                    .next()
                    .unwrap_or("");
                if let Some(path) = normalize_named_path(token) {
                    if !path_marked_create_only(line, token) {
                        paths.insert(path);
                    }
                }
            }
        }
    }
}

fn is_plan_store_line(line: &str) -> bool {
    line.contains("/.vibecrafted/") || line.contains("/artifacts/") && line.contains("/plans/")
}

fn backtick_spans(text: &str) -> Vec<String> {
    let mut spans = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find('`') {
        rest = &rest[start + 1..];
        let Some(end) = rest.find('`') else {
            break;
        };
        let inner = rest[..end].trim();
        if !inner.is_empty() && !inner.contains('\n') {
            spans.push(inner.to_string());
        }
        rest = &rest[end + 1..];
    }
    spans
}

fn path_marked_create_only(line: &str, path: &str) -> bool {
    let Some(index) = line.find(path) else {
        return false;
    };
    let before = line[..index].to_ascii_lowercase();
    let window = if before.len() > 56 {
        &before[before.len() - 56..]
    } else {
        before.as_str()
    };
    let create = window.contains("create")
        || window.contains("new file")
        || window.contains("or new")
        || window.contains("(new ");
    let existing = window.contains("edit")
        || window.contains("read")
        || window.contains("do not")
        || window.contains("touch");
    create && !existing
}

fn normalize_named_path(raw: &str) -> Option<String> {
    let trimmed = raw
        .trim()
        .trim_matches(|c: char| matches!(c, ',' | ';' | '.' | ':' | ')' | '(' | '"' | '\''));
    if trimmed.is_empty() || trimmed.starts_with("~/") || trimmed.starts_with('$') {
        return None;
    }
    if trimmed.contains("://") || trimmed.contains(' ') {
        return None;
    }
    if trimmed.contains('*')
        || trimmed.contains('?')
        || trimmed.contains('{')
        || trimmed.contains('}')
    {
        return None;
    }
    if trimmed.contains("..") {
        return None;
    }
    let relative = if trimmed.starts_with('/') {
        return None;
    } else {
        trimmed.trim_start_matches("./")
    };
    if !looks_like_repo_path(relative) {
        return None;
    }
    Some(relative.to_string())
}

fn looks_like_repo_path(path: &str) -> bool {
    if !path.contains('/') || path.starts_with('/') {
        return false;
    }
    let first = path.split('/').next().unwrap_or("");
    if matches!(first, "origin" | "remotes" | "refs" | "heads") {
        return false;
    }
    if path
        .split('/')
        .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return false;
    }
    Path::new(path).extension().is_some_and(|extension| {
        let ext = extension.to_string_lossy();
        !ext.is_empty() && ext.chars().all(|c| c.is_ascii_alphanumeric())
    })
}

fn resolve_geometry_repo(
    _plan_root: &Path,
    sources: &GeometrySources,
    explicit: Option<&Path>,
) -> Option<PathBuf> {
    if let Some(explicit) = explicit {
        return git_toplevel(explicit);
    }
    for (_, content) in &sources.mission_driver {
        if let Some(declared) = parse_declared_repo_root(content) {
            if let Some(top) = git_toplevel(&declared) {
                return Some(top);
            }
        }
        for absolute in extract_absolute_fs_paths(content) {
            if absolute.contains("/.vibecrafted/") {
                continue;
            }
            let candidate = PathBuf::from(&absolute);
            let start = if candidate.is_file() {
                candidate.parent().map(Path::to_path_buf)
            } else {
                Some(candidate)
            };
            if let Some(start) = start {
                if let Some(top) = git_toplevel(&start) {
                    return Some(top);
                }
            }
        }
    }
    std::env::current_dir()
        .ok()
        .and_then(|cwd| git_toplevel(&cwd))
}

fn parse_declared_repo_root(content: &str) -> Option<PathBuf> {
    for line in content.lines() {
        let trimmed = line.trim().trim_start_matches(['-', '*']).trim();
        let lower = trimmed.to_ascii_lowercase();
        const KEYS: &[&str] = &[
            "baseline_repo_root:",
            "repository root:",
            "repo target:",
            "repo:",
        ];
        let Some(key) = KEYS.iter().copied().find(|key| lower.starts_with(*key)) else {
            continue;
        };
        let value = trimmed.get(key.len()..).unwrap_or("").trim();
        let cleaned = value
            .trim_matches('`')
            .split_whitespace()
            .next()
            .unwrap_or("")
            .trim_matches(|c: char| matches!(c, ',' | ';' | '.' | '"' | '\''));
        if cleaned.starts_with('/') {
            return Some(PathBuf::from(cleaned));
        }
    }
    None
}

fn extract_absolute_fs_paths(content: &str) -> Vec<String> {
    let mut paths = Vec::new();
    for line in content.lines() {
        let mut rest = line;
        while let Some(idx) = rest.find('/') {
            if idx > 0 {
                let prev = rest.as_bytes()[idx - 1];
                if prev != b' ' && prev != b'`' && prev != b'"' && prev != b'\'' && prev != b'(' {
                    rest = &rest[idx + 1..];
                    continue;
                }
            }
            let slice = &rest[idx..];
            let end = slice
                .find(|c: char| {
                    c.is_whitespace() || matches!(c, '`' | '"' | '\'' | ')' | ']' | ',')
                })
                .unwrap_or(slice.len());
            let path = &slice[..end];
            if path.starts_with("/Users/")
                || path.starts_with("/home/")
                || path.starts_with("/Volumes/")
            {
                paths.push(path.to_string());
            }
            rest = &slice[end.max(1)..];
        }
    }
    paths
}

fn git_toplevel(path: &Path) -> Option<PathBuf> {
    if !path.exists() {
        return None;
    }
    let start = if path.is_file() { path.parent()? } else { path };
    let output = git_command(start, &["rev-parse", "--show-toplevel"])?;
    if !output.status.success() {
        return None;
    }
    let top = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if top.is_empty() {
        None
    } else {
        Some(PathBuf::from(top))
    }
}

fn git_is_ancestor_of_head(repo: &Path, sha: &str) -> Result<bool, String> {
    let object = format!("{sha}^{{commit}}");
    let exists = git_command(repo, &["cat-file", "-e", &object])
        .ok_or_else(|| format!("git cat-file failed while resolving baseline {sha}"))?;
    if !exists.status.success() {
        return Err(format!(
            "named baseline {sha} is not a commit in this repository"
        ));
    }
    let output = git_command(repo, &["merge-base", "--is-ancestor", sha, "HEAD"])
        .ok_or_else(|| format!("git merge-base failed while checking baseline {sha}"))?;
    match output.status.code() {
        Some(0) => Ok(true),
        Some(1) => Ok(false),
        _ => Err(format!(
            "git merge-base --is-ancestor {sha} HEAD failed: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        )),
    }
}

fn git_path_exists_on_head(repo: &Path, relative: &str) -> bool {
    if relative.starts_with('-') {
        return false;
    }
    let spec = format!("HEAD:{relative}");
    git_command(repo, &["cat-file", "-e", &spec]).is_some_and(|output| output.status.success())
}

fn git_command(repo: &Path, args: &[&str]) -> Option<std::process::Output> {
    Command::new("git")
        .current_dir(repo)
        .args(args)
        .env_remove("GIT_DIR")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .ok()
}

fn validate_identity(
    manifest: &ScaffoldManifest,
    org: &str,
    repo: &str,
    day: &str,
    plan_id: &str,
) -> ScaffoldResult<()> {
    if manifest.schema_version != SCAFFOLD_SCHEMA_VERSION
        || manifest.org != org
        || manifest.repo != repo
        || manifest.day != day
        || manifest.plan_id != plan_id
    {
        return Err(ScaffoldError::InvalidManifest {
            message: "manifest identity does not match its canonical plan path".into(),
        });
    }
    Ok(())
}

/// R1 — plan root must be `…/artifacts/<org>/<repo>/<day>/plans/<plan_id>` matching the manifest.
fn validate_plan_root_identity(root: &Path, manifest: &ScaffoldManifest) -> Result<(), String> {
    let canonical = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    let components: Vec<String> = canonical
        .components()
        .filter_map(|component| match component {
            Component::Normal(value) => Some(value.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect();
    let expected = [
        "artifacts",
        manifest.org.as_str(),
        manifest.repo.as_str(),
        manifest.day.as_str(),
        "plans",
        manifest.plan_id.as_str(),
    ];
    let tail_ok = components
        .windows(expected.len())
        .any(|window| window.iter().map(String::as_str).eq(expected));
    if !tail_ok {
        return Err(format!(
            "plan root identity mismatch: path {} must end with artifacts/{}/{}/{}/plans/{}",
            root.display(),
            manifest.org,
            manifest.repo,
            manifest.day,
            manifest.plan_id
        ));
    }
    if manifest.schema_version != SCAFFOLD_SCHEMA_VERSION {
        return Err(format!(
            "unsupported schema_version {:?} (expected {SCAFFOLD_SCHEMA_VERSION})",
            manifest.schema_version
        ));
    }
    Ok(())
}

fn validate_manifest_plan(root: &Path, manifest: &ScaffoldManifest) -> ScaffoldDoctorReport {
    let mut errors = Vec::new();
    let mut ids = BTreeSet::new();
    let mut paths = BTreeSet::new();
    let declared_ids: BTreeSet<&str> = manifest
        .artifacts
        .iter()
        .map(|artifact| artifact.id.as_str())
        .collect();
    let mut role_counts = BTreeMap::new();
    let mut needs_design_cuts = Vec::new();
    let mut design_doc_count = 0usize;

    for artifact in &manifest.artifacts {
        if !ids.insert(artifact.id.clone()) {
            doctor_error(
                &mut errors,
                "duplicate_artifact_id",
                Some("R3"),
                Some(&artifact.id),
                Some(&artifact.path),
                "duplicate artifact id",
            );
        }
        if !paths.insert(artifact.path.clone()) {
            doctor_error(
                &mut errors,
                "duplicate_artifact_path",
                Some("R3"),
                Some(&artifact.id),
                Some(&artifact.path),
                "duplicate artifact path",
            );
        }
        *role_counts.entry(artifact.role.as_str()).or_insert(0usize) += 1;
        if artifact.role == ScaffoldArtifactRole::DesignDoc {
            design_doc_count += 1;
        }
        for dependency in &artifact.dependencies {
            if !declared_ids.contains(dependency.as_str()) {
                doctor_error(
                    &mut errors,
                    "unknown_dependency",
                    Some("R3"),
                    Some(&artifact.id),
                    Some(&artifact.path),
                    &format!("unknown dependency: {dependency}"),
                );
            }
        }
        match declared_path(root, artifact) {
            Ok(path) => {
                if !path.is_file() {
                    let code = if artifact.required {
                        "missing_required_artifact"
                    } else {
                        "missing_manifest_artifact"
                    };
                    doctor_error(
                        &mut errors,
                        code,
                        Some("R2"),
                        Some(&artifact.id),
                        Some(&artifact.path),
                        "manifest artifact is missing from disk",
                    );
                } else {
                    if artifact.editable && path_has_symlink(root, &path) {
                        doctor_error(
                            &mut errors,
                            "writable_symlink",
                            Some("R4"),
                            Some(&artifact.id),
                            Some(&artifact.path),
                            "editable artifact path contains a symlink",
                        );
                    }
                    if fs::metadata(&path).is_ok_and(|metadata| metadata.len() == 0) {
                        doctor_error(
                            &mut errors,
                            "empty_contract",
                            Some("R2"),
                            Some(&artifact.id),
                            Some(&artifact.path),
                            "declared artifact is empty",
                        );
                    }
                    if let Ok(content) = fs::read_to_string(&path) {
                        if artifact.role != ScaffoldArtifactRole::Dispatch {
                            validate_frontmatter(artifact, &content, &mut errors);
                        }
                        validate_role_contract(artifact, &content, &mut errors);
                        if artifact.role == ScaffoldArtifactRole::Brief {
                            validate_brief_naming(artifact, &mut errors);
                            validate_brief_sections(artifact, &content, &mut errors);
                            validate_acceptance_contract(artifact, &content, &mut errors);
                            if brief_needs_design(&content) {
                                needs_design_cuts.push(artifact.id.clone());
                            }
                        }
                    }
                }
            }
            Err(error) => doctor_error(
                &mut errors,
                "path_escape",
                Some("R4"),
                Some(&artifact.id),
                Some(&artifact.path),
                &error.to_string(),
            ),
        }
    }

    // R9 — exactly one DRIVER
    if role_counts.get("driver").copied() != Some(1) {
        doctor_error(
            &mut errors,
            "driver_contract",
            Some("R9"),
            None,
            None,
            "manifest must declare exactly one driver",
        );
    }
    // R6 — exactly one wave-atlas
    if role_counts.get("wave-atlas").copied() != Some(1) {
        doctor_error(
            &mut errors,
            "atlas_contract",
            Some("R6"),
            None,
            None,
            "manifest must declare exactly one wave-atlas",
        );
    }

    // R5 — briefs on disk ↔ manifest
    let mut briefs_on_disk = Vec::new();
    collect_markdown(&root.join("briefs"), &mut briefs_on_disk);
    for path in briefs_on_disk {
        if let Ok(relative) = relative_string(root, &path) {
            if !paths.contains(&relative) {
                doctor_error(
                    &mut errors,
                    "brief_absent_from_manifest",
                    Some("R5"),
                    None,
                    Some(&relative),
                    &format!("brief on disk is not declared in manifest: {relative}"),
                );
            }
        }
    }

    // R10 — design docs for needs_design cuts
    if !needs_design_cuts.is_empty() && design_doc_count == 0 {
        doctor_error(
            &mut errors,
            "design_doc_missing",
            Some("R10"),
            None,
            None,
            &format!(
                "cuts marked needs_design require a design-doc artifact; flagged: {}",
                needs_design_cuts.join(", ")
            ),
        );
    }

    ScaffoldDoctorReport {
        valid: errors.is_empty(),
        plan_id: manifest.plan_id.clone(),
        plan_root: root.display().to_string(),
        artifact_ids: manifest
            .artifacts
            .iter()
            .map(|artifact| artifact.id.clone())
            .collect(),
        errors,
    }
}

fn doctor_error(
    errors: &mut Vec<ScaffoldDoctorError>,
    code: &str,
    rule: Option<&str>,
    artifact_id: Option<&str>,
    path: Option<&str>,
    message: &str,
) {
    errors.push(ScaffoldDoctorError {
        code: code.into(),
        rule: rule.map(str::to_string),
        artifact_id: artifact_id.map(str::to_string),
        path: path.map(str::to_string),
        message: message.into(),
    });
}

fn parse_frontmatter(content: &str) -> Option<BTreeMap<String, String>> {
    if !content.starts_with("---\n") && !content.starts_with("---\r\n") {
        return None;
    }
    let rest = content
        .strip_prefix("---\r\n")
        .or_else(|| content.strip_prefix("---\n"))?;
    let body = rest
        .split_once("\n---")
        .map(|(front, _)| front)
        .unwrap_or(rest);
    let mut map = BTreeMap::new();
    for line in body.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = trimmed.split_once(':') {
            map.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    Some(map)
}

/// R11 — every markdown artifact must open with YAML frontmatter carrying the
/// six required keys. Mixed packages (some with, some without) refuse.
fn validate_frontmatter(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let Some(frontmatter) = parse_frontmatter(content) else {
        doctor_error(
            errors,
            "frontmatter_missing",
            Some("R11"),
            Some(&artifact.id),
            Some(&artifact.path),
            "markdown artifact lacks YAML frontmatter (mixed or bare packages refuse)",
        );
        return;
    };
    let mut missing = Vec::new();
    for key in FRONTMATTER_REQUIRED {
        if frontmatter
            .get(*key)
            .map(|value| value.is_empty())
            .unwrap_or(true)
        {
            missing.push(*key);
        }
    }
    if !missing.is_empty() {
        doctor_error(
            errors,
            "frontmatter_incomplete",
            Some("R11"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!("frontmatter missing required keys: {}", missing.join(", ")),
        );
    }
    if let Some(role) = frontmatter.get("role") {
        let role = role.trim();
        let role_ok = role == artifact.role.as_str()
            || (artifact.role == ScaffoldArtifactRole::Other && role == "mission");
        if !role_ok {
            doctor_error(
                errors,
                "frontmatter_drift",
                Some("R11"),
                Some(&artifact.id),
                Some(&artifact.path),
                &format!(
                    "frontmatter role `{role}` does not match manifest role `{}`",
                    artifact.role.as_str()
                ),
            );
        }
    }
    for key in ["id", "artifact_id"] {
        if let Some(value) = frontmatter.get(key) {
            if value != &artifact.id {
                doctor_error(
                    errors,
                    "frontmatter_drift",
                    Some("R11"),
                    Some(&artifact.id),
                    Some(&artifact.path),
                    &format!("frontmatter {key} does not match manifest id"),
                );
            }
        }
    }
}

fn validate_role_contract(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    match artifact.role {
        ScaffoldArtifactRole::Driver => validate_driver_contract(artifact, content, errors),
        ScaffoldArtifactRole::WaveAtlas => validate_atlas_contract(artifact, content, errors),
        ScaffoldArtifactRole::Tracker => validate_tracker_contract(artifact, content, errors),
        _ => {}
    }
}

/// R9 — DRIVER carries all five: full paths · why-graph · ready commands ·
/// `[ ]→[x]` rule verbatim · status snapshot (dou-index).
fn validate_driver_contract(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let lower = content.to_ascii_lowercase();
    let mut missing = Vec::new();
    // 1. Full absolute paths
    let has_abs_path = content.lines().any(|line| {
        let t = line.trim();
        t.contains("/Users/")
            || t.contains("/home/")
            || t.contains("/Volumes/")
            || t.contains("~/.vibecrafted/")
            || t.contains("$HOME/")
            || (t.contains("`/") && t.contains('/'))
    }) || lower.contains("pełne ścieżki")
        || lower.contains("full absolute")
        || lower.contains("absolute path");
    if !has_abs_path {
        missing.push("full absolute paths");
    }
    // 2. Dependency graph WITH why on edges
    let has_graph = lower.contains("why")
        || lower.contains("dlaczego")
        || lower.contains("graf")
        || lower.contains("dependenc");
    if !has_graph {
        missing.push("dependency graph with why");
    }
    // 3. Ready commands
    if !lower.contains("vibecrafted ") {
        missing.push("ready vibecrafted commands");
    }
    // 4. Literal [ ]→[x] rule (unicode arrow preferred; ASCII fallback accepted only with
    // the full alphabet line nearby is still not enough — require the unicode form OR both
    // `[~]→[x]` / `[ ]→[x]` as skill quotes).
    let has_rule = content.contains("[ ]→[x]")
        || content.contains("[~]→[x]")
        || (content.contains("[ ]->[x]") && content.contains("delivery-verifier"));
    if !has_rule {
        missing.push("`[ ]→[x]` rule verbatim");
    }
    // 5. Live status snapshot + dou-index
    if !lower.contains("dou-index") {
        missing.push("status snapshot (dou-index)");
    }
    if !missing.is_empty() {
        doctor_error(
            errors,
            "driver_contract",
            Some("R9"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!("DRIVER.md missing required parts: {}", missing.join(", ")),
        );
    }
}

/// R6 — atlas has wave atlas + dependency graph.
fn validate_atlas_contract(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let lower = content.to_ascii_lowercase();
    let has_wave = lower.contains("wave")
        || lower.contains("fala")
        || lower.contains("| cut |")
        || lower.contains("| **w");
    let has_graph = lower.contains("dependenc")
        || lower.contains("graf")
        || lower.contains("zależy")
        || content.contains("→")
        || content.contains("->");
    let mut missing = Vec::new();
    if !has_wave {
        missing.push("wave atlas table");
    }
    if !has_graph {
        missing.push("dependency graph");
    }
    if !missing.is_empty() {
        doctor_error(
            errors,
            "atlas_contract",
            Some("R6"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!("wave-atlas missing: {}", missing.join(", ")),
        );
    }
}

fn validate_tracker_contract(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let lower = content.to_ascii_lowercase();
    // Tracker needs a state alphabet / checkbox column — "stan" is the PL synonym.
    let has_state = lower.contains("state")
        || lower.contains("stan")
        || content.contains("[ ]")
        || content.contains("[x]");
    if !has_state {
        doctor_error(
            errors,
            "tracker_contract",
            Some("R6"),
            Some(&artifact.id),
            Some(&artifact.path),
            "tracker lacks state markers ([ ] / state / stan column)",
        );
    }
}

/// R7 — briefs/<wave>-<slot>_<slug>.md
fn validate_brief_naming(
    artifact: &ScaffoldArtifactDeclaration,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let path = artifact.path.as_str();
    let ok = path.starts_with("briefs/")
        && path.ends_with(".md")
        && path.strip_prefix("briefs/").is_some_and(brief_filename_ok);
    if !ok {
        doctor_error(
            errors,
            "brief_naming",
            Some("R7"),
            Some(&artifact.id),
            Some(&artifact.path),
            "brief path must match briefs/<wave>-<slot>_<slug>.md",
        );
    }
}

fn brief_filename_ok(name: &str) -> bool {
    // <wave>-<slot>_<slug>.md  e.g. W1-01_enclave-root.md or 1-01_foo.md
    let Some(stem) = name.strip_suffix(".md") else {
        return false;
    };
    let Some((left, slug)) = stem.split_once('_') else {
        return false;
    };
    if slug.is_empty()
        || !slug
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return false;
    }
    let Some((wave, slot)) = left.split_once('-') else {
        return false;
    };
    if wave.is_empty() || slot.is_empty() {
        return false;
    }
    let wave_ok = wave.chars().all(|c| c.is_ascii_alphanumeric());
    let slot_ok = slot.chars().all(|c| c.is_ascii_digit());
    wave_ok && slot_ok
}

/// R7 — all 12 brief sections present as headings.
fn validate_brief_sections(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let headings: Vec<String> = content
        .lines()
        .filter_map(|line| {
            let trimmed = line.trim();
            if trimmed.starts_with('#') {
                Some(trimmed.to_ascii_lowercase())
            } else {
                None
            }
        })
        .collect();
    let mut missing = Vec::new();
    for section in BRIEF_SECTIONS {
        let found = headings.iter().any(|heading| heading.contains(section));
        if !found {
            missing.push(*section);
        }
    }
    if !missing.is_empty() {
        doctor_error(
            errors,
            "brief_sections",
            Some("R7"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!(
                "brief missing required section headings: {}",
                missing.join(", ")
            ),
        );
    }
}

/// R8 — acceptance bullets are atomic (checkbox state) and verifier-backed
/// (Gates section carries a runnable delivery-verifier, or each bullet names one).
fn validate_acceptance_contract(
    artifact: &ScaffoldArtifactDeclaration,
    content: &str,
    errors: &mut Vec<ScaffoldDoctorError>,
) {
    let acceptance = section_body(content, "acceptance");
    let gates = section_body(content, "gates");
    let Some(acceptance) = acceptance else {
        doctor_error(
            errors,
            "acceptance_contract",
            Some("R8"),
            Some(&artifact.id),
            Some(&artifact.path),
            "brief has no Acceptance section",
        );
        return;
    };
    let bullets: Vec<&str> = acceptance
        .lines()
        .map(str::trim)
        .filter(|line| line.starts_with('-'))
        .collect();
    if bullets.is_empty() {
        doctor_error(
            errors,
            "acceptance_contract",
            Some("R8"),
            Some(&artifact.id),
            Some(&artifact.path),
            "Acceptance section has no bullets",
        );
        return;
    }
    let mut unstated = 0usize;
    let mut without_verifier = 0usize;
    let gates_has_verifier = gates.as_deref().is_some_and(has_verifier_command);
    for bullet in &bullets {
        let has_state = bullet.contains("[ ]")
            || bullet.contains("[x]")
            || bullet.contains("[~]")
            || bullet.contains("[?]")
            || bullet.contains("[!]");
        if !has_state {
            unstated += 1;
        }
        let bullet_has_verifier = has_verifier_command(bullet);
        if !bullet_has_verifier && !gates_has_verifier {
            without_verifier += 1;
        }
    }
    if unstated > 0 {
        doctor_error(
            errors,
            "acceptance_contract",
            Some("R8"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!("{unstated} acceptance bullet(s) lack state markers ([ ]/[~]/[?]/[!]/[x])"),
        );
    }
    if without_verifier > 0 {
        doctor_error(
            errors,
            "acceptance_contract",
            Some("R8"),
            Some(&artifact.id),
            Some(&artifact.path),
            &format!(
                "{without_verifier} acceptance bullet(s) lack a delivery-verifier (inline or in Gates)"
            ),
        );
    }
}

fn has_verifier_command(text: &str) -> bool {
    let lower = text.to_ascii_lowercase();
    lower.contains("pytest")
        || lower.contains("cargo ")
        || lower.contains("vibecrafted ")
        || lower.contains("python3 ")
        || lower.contains("python ")
        || lower.contains("bash ")
        || lower.contains(".sh")
        || lower.contains("make ")
        || lower.contains("pre-commit")
        || lower.contains("verify")
        || lower.contains("delivery-verifier")
        || lower.contains("verifier")
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

fn brief_needs_design(content: &str) -> bool {
    if let Some(frontmatter) = parse_frontmatter(content) {
        if frontmatter
            .get("needs_design")
            .is_some_and(|value| matches!(value.as_str(), "true" | "yes" | "1"))
        {
            return true;
        }
    }
    content.lines().any(|line| {
        let t = line.trim().to_ascii_lowercase();
        t == "needs_design: true" || t.contains("**needs_design**") || t.contains("`needs_design`")
    })
}

fn declared_path(root: &Path, artifact: &ScaffoldArtifactDeclaration) -> ScaffoldResult<PathBuf> {
    Ok(root.join(validate_relative_artifact_path(
        &artifact.path,
        artifact.role,
    )?))
}

fn validate_relative_artifact_path(
    relative: &str,
    role: ScaffoldArtifactRole,
) -> ScaffoldResult<PathBuf> {
    if role == ScaffoldArtifactRole::Dispatch {
        if relative.is_empty() || Path::new(relative).is_absolute() || relative.contains('\\') {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing unsafe scaffold dispatch path".into(),
            });
        }
        let path = Path::new(relative);
        let safe_components = path
            .components()
            .all(|component| matches!(component, Component::Normal(_)));
        if !safe_components || !relative.ends_with(".dispatch.toml") {
            return Err(ScaffoldError::UnsafePath {
                message: "refusing unsafe or non-dispatch scaffold artifact path".into(),
            });
        }
        return Ok(path.to_path_buf());
    }
    validate_relative_markdown_path(relative)
}

fn validate_relative_markdown_path(relative: &str) -> ScaffoldResult<PathBuf> {
    if relative.is_empty() || Path::new(relative).is_absolute() || relative.contains('\\') {
        return Err(ScaffoldError::UnsafePath {
            message: "refusing unsafe scaffold artifact path".into(),
        });
    }
    let path = Path::new(relative);
    if path
        .components()
        .any(|component| !matches!(component, Component::Normal(_)))
        || path.extension().and_then(|extension| extension.to_str()) != Some("md")
    {
        return Err(ScaffoldError::UnsafePath {
            message: "refusing unsafe or non-Markdown scaffold artifact path".into(),
        });
    }
    Ok(path.to_path_buf())
}

fn reject_symlink_path(root: &Path, path: &Path) -> ScaffoldResult<()> {
    if path_has_symlink(root, path) {
        return Err(ScaffoldError::UnsafePath {
            message: "refusing symlinked scaffold artifact path".into(),
        });
    }
    Ok(())
}

fn path_has_symlink(root: &Path, path: &Path) -> bool {
    let Ok(relative) = path.strip_prefix(root) else {
        return true;
    };
    let mut cursor = root.to_path_buf();
    for component in relative.components() {
        cursor.push(component.as_os_str());
        if fs::symlink_metadata(&cursor).is_ok_and(|metadata| metadata.file_type().is_symlink()) {
            return true;
        }
    }
    false
}

fn validate_path_segment(value: &str, label: &str) -> ScaffoldResult<()> {
    if value.is_empty() || value == "." || value == ".." || value.contains(['/', '\\']) {
        return Err(ScaffoldError::UnsafePath {
            message: format!("invalid scaffold {label} path segment"),
        });
    }
    Ok(())
}

fn collect_scaffold_manifest_paths(artifacts_root: &Path, output: &mut Vec<PathBuf>) {
    let Ok(orgs) = fs::read_dir(artifacts_root) else {
        return;
    };
    for org in orgs.flatten().filter(|entry| entry.path().is_dir()) {
        let Ok(repos) = fs::read_dir(org.path()) else {
            continue;
        };
        for repo in repos.flatten().filter(|entry| entry.path().is_dir()) {
            let Ok(days) = fs::read_dir(repo.path()) else {
                continue;
            };
            for day in days.flatten().filter(|entry| entry.path().is_dir()) {
                let plans_root = day.path().join("plans");
                let Ok(plans) = fs::read_dir(plans_root) else {
                    continue;
                };
                for plan in plans.flatten().filter(|entry| entry.path().is_dir()) {
                    let manifest = plan.path().join("manifest.json");
                    if manifest.is_file() {
                        output.push(manifest);
                    }
                }
            }
        }
    }
}

fn discover_legacy_paths(root: &Path) -> Vec<PathBuf> {
    let mut paths = Vec::new();
    let master = root.join("master-dispatch.md");
    if master.is_file() {
        paths.push(master);
    }
    for directory in ["briefs", "designs", "design-docs"] {
        collect_markdown(&root.join(directory), &mut paths);
    }
    paths
}

fn collect_markdown(root: &Path, output: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        match entry.file_type() {
            Ok(kind) if kind.is_dir() => collect_markdown(&path, output),
            Ok(kind)
                if kind.is_file()
                    && path.extension().and_then(|extension| extension.to_str()) == Some("md") =>
            {
                output.push(path)
            }
            _ => {}
        }
    }
}

fn relative_string(root: &Path, path: &Path) -> ScaffoldResult<String> {
    Ok(path
        .strip_prefix(root)
        .map_err(|_| ScaffoldError::UnsafePath {
            message: "artifact outside scaffold root".into(),
        })?
        .to_string_lossy()
        .replace('\\', "/"))
}

fn legacy_artifact_id(relative: &str) -> String {
    relative
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || matches!(character, '.' | '-' | '_') {
                character
            } else {
                '_'
            }
        })
        .collect()
}

fn legacy_role(relative: &str) -> ScaffoldArtifactRole {
    let lower = relative.to_ascii_lowercase();
    if lower == "master-dispatch.md" {
        ScaffoldArtifactRole::WaveAtlas
    } else if lower.starts_with("briefs/") {
        ScaffoldArtifactRole::Brief
    } else if lower.contains("design") {
        ScaffoldArtifactRole::DesignDoc
    } else {
        ScaffoldArtifactRole::Other
    }
}

fn artifact_title(relative: &str, role: ScaffoldArtifactRole) -> String {
    if role == ScaffoldArtifactRole::WaveAtlas {
        return "Wave atlas".into();
    }
    let file = relative.rsplit('/').next().unwrap_or(relative);
    file.strip_suffix(".md")
        .unwrap_or(file)
        .replace(['_', '-'], " ")
}

fn content_hash(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

fn portable_scaffold_content(
    content: &str,
    plan_root: &str,
    home: &str,
    repo_root: Option<&str>,
) -> String {
    let mut portable = content.replace(plan_root, "${SCAFFOLD_ROOT}");
    if let Some(repo_root) = repo_root.filter(|value| !value.trim().is_empty()) {
        portable = portable.replace(repo_root, "${REPO_ROOT}");
    }
    portable = portable.replace(home, "${VIBECRAFTED_HOME}");
    portable
        .lines()
        .map(|line| {
            let trimmed = line.trim_start();
            if trimmed.starts_with("baseline_branch:") {
                let indent = &line[..line.len() - trimmed.len()];
                format!("{indent}baseline_branch: <living-tree>")
            } else {
                line.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
        + if content.ends_with('\n') { "\n" } else { "" }
}

fn first_private_absolute_path(content: &str) -> Option<String> {
    ["/Users/", "/Volumes/"].into_iter().find_map(|marker| {
        let start = content.find(marker)?;
        let rest = &content[start..];
        let end = rest
            .char_indices()
            .find_map(|(index, character)| {
                (index > 0
                    && (character.is_whitespace()
                        || matches!(
                            character,
                            '`' | '\'' | '"' | ')' | ']' | '}' | ',' | ';'
                        )))
                .then_some(index)
            })
            .unwrap_or(rest.len());
        Some(rest[..end].to_string())
    })
}

fn modified_at(path: &Path) -> String {
    fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .map(|modified| {
            let value: chrono::DateTime<Utc> = modified.into();
            value.to_rfc3339()
        })
        .unwrap_or_default()
}

fn checkpoint_path(root: &Path) -> PathBuf {
    root.join(".scaffold-checkpoints.json")
}
fn changes_path(root: &Path) -> PathBuf {
    root.join(".scaffold-changes.jsonl")
}

fn read_checkpoints(path: &Path) -> CheckpointStore {
    fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
}

fn write_checkpoints(path: &Path, store: &CheckpointStore) -> ScaffoldResult<()> {
    write_atomic(path, &serde_json::to_vec_pretty(store)?)
}

fn write_atomic(path: &Path, bytes: &[u8]) -> ScaffoldResult<()> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let file = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("scaffold");
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let temporary = parent.join(format!(".{file}.tmp.{}.{nonce}", std::process::id()));
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temporary)?;
    output.write_all(bytes)?;
    output.sync_all()?;
    drop(output);
    if let Err(error) = fs::rename(&temporary, path) {
        let _ = fs::remove_file(&temporary);
        return Err(error.into());
    }
    Ok(())
}

fn append_change(root: &Path, change: ScaffoldChange) -> ScaffoldResult<()> {
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(changes_path(root))?;
    writeln!(file, "{}", serde_json::to_string(&change)?)?;
    Ok(())
}

fn now_ts() -> String {
    Utc::now().to_rfc3339()
}

fn emit_scaffold_control_event(
    home: &Path,
    kind: &str,
    run_id: &str,
    message: &str,
    payload: serde_json::Value,
) {
    let cp_dir = home.join("control_plane");
    if fs::create_dir_all(&cp_dir).is_err() {
        return;
    }
    let events_path = cp_dir.join("events.jsonl");

    let event = serde_json::json!({
        "ts": now_ts(),
        "run_id": run_id,
        "kind": kind,
        "message": message,
        "payload": payload,
    });

    let Ok(line) = serde_json::to_string(&event) else {
        return;
    };

    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(events_path)
    {
        let _ = writeln!(file, "{line}");
    }
}

fn update_markdown_status(
    content: &str,
    item_id: Option<&str>,
    item_index: Option<usize>,
    status: &str,
) -> String {
    let new_symbol = match status.trim() {
        "done" | "completed" | "checked" | "x" | "X" | "[x]" | "[X]" | "true" => "x",
        "running" | "in_progress" | "in-progress" | "~" | "[~]" => "~",
        "unverified" | "done-unverified" | "?" | "[?]" => "?",
        "blocked" | "!" | "[!]" => "!",
        "todo" | "pending" | "unchecked" | " " | "[ ]" | "false" => " ",
        custom if custom.starts_with('[') && custom.ends_with(']') && custom.len() == 3 => {
            &custom[1..2]
        }
        custom if custom.len() == 1 => custom,
        _ => status.trim(),
    };

    let mut lines: Vec<String> = content.lines().map(String::from).collect();
    let trailing_newline = content.ends_with('\n');

    let mut current_checkbox_idx = 0;
    let mut updated = false;

    for line in lines.iter_mut() {
        if line.contains("[ ]")
            || line.contains("[x]")
            || line.contains("[X]")
            || line.contains("[~]")
            || line.contains("[?]")
            || line.contains("[!]")
        {
            let is_match = match (item_id, item_index) {
                (Some(id), _) => line.contains(id),
                (None, Some(idx)) => idx == current_checkbox_idx,
                (None, None) => true,
            };

            if is_match {
                if let Some(s) = line.find('[') {
                    if let Some(rel_e) = line[s..].find(']') {
                        let e = s + rel_e;
                        if e == s + 2 {
                            line.replace_range(s + 1..e, new_symbol);
                            updated = true;
                            if item_id.is_none() && item_index.is_none() {
                                break;
                            }
                        }
                    }
                }
            }
            current_checkbox_idx += 1;
        }
    }

    if !updated {
        if let Some(id) = item_id {
            lines.push(format!("- [{new_symbol}] {id}"));
        }
    }

    let mut result = lines.join("\n");
    if trailing_newline {
        result.push('\n');
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn relative_paths_reject_traversal_absolute_and_non_markdown() {
        for path in [
            "",
            "/brief.md",
            "../brief.md",
            "briefs/../brief.md",
            "brief.txt",
            "briefs\\brief.md",
        ] {
            assert!(validate_relative_markdown_path(path).is_err(), "{path}");
        }
        assert_eq!(
            validate_relative_markdown_path("briefs/cut.md").expect("safe"),
            PathBuf::from("briefs/cut.md")
        );
        assert_eq!(
            validate_relative_artifact_path(
                "plan-a.dispatch.toml",
                ScaffoldArtifactRole::Dispatch,
            )
            .expect("safe dispatch"),
            PathBuf::from("plan-a.dispatch.toml")
        );
        assert!(
            validate_relative_artifact_path("plan-a.toml", ScaffoldArtifactRole::Dispatch).is_err()
        );
    }

    #[test]
    fn workspace_health_keeps_identity_drift_visible_but_nonfatal() {
        let mut report = ScaffoldDoctorReport {
            valid: false,
            plan_id: "plan-a".into(),
            plan_root: "/tmp/plan-a".into(),
            artifact_ids: Vec::new(),
            errors: vec![ScaffoldDoctorError {
                code: "identity_mismatch".into(),
                rule: Some("R1".into()),
                artifact_id: None,
                path: None,
                message: "path casing differs".into(),
            }],
        };
        assert!(report.workspace_reviewable());

        report.errors.push(ScaffoldDoctorError {
            code: "missing_required_artifact".into(),
            rule: Some("R2".into()),
            artifact_id: Some("driver".into()),
            path: Some("DRIVER.md".into()),
            message: "missing".into(),
        });
        assert!(!report.workspace_reviewable());
    }

    #[test]
    fn named_baseline_shas_take_only_the_labeled_span() {
        let mut shas = BTreeSet::new();
        extract_named_baseline_shas(
            "- Branch: `fix/foo` @ `69101f2c` (base 27ad70e2). Original baseline `fix/bar @ e6638c68` superseded",
            &mut shas,
        );
        assert_eq!(shas.iter().cloned().collect::<Vec<_>>(), ["e6638c68"]);
    }

    #[test]
    fn authoring_baseline_pair_is_collected() {
        let mut shas = BTreeSet::new();
        extract_named_baseline_shas(
            "The authoring baseline `fix/contract-alignment-to-pr-53` @ `f55fc2d6`/`e6638c68` is **not** an ancestor",
            &mut shas,
        );
        assert_eq!(
            shas.iter().cloned().collect::<Vec<_>>(),
            ["e6638c68", "f55fc2d6"]
        );
    }

    #[test]
    fn baseline_sha_field_is_collected_and_sha256_is_ignored() {
        let mut shas = BTreeSet::new();
        extract_named_baseline_shas(
            "baseline_sha: 1d1669ecace92c4196a7f9bf6e1adc1b7eae6a1f (content sha256:abcdef1)\n",
            &mut shas,
        );
        assert_eq!(
            shas.iter().cloned().collect::<Vec<_>>(),
            ["1d1669ecace92c4196a7f9bf6e1adc1b7eae6a1f"]
        );
    }

    #[test]
    fn files_section_skips_create_only_and_unqualified_names() {
        let mut paths = BTreeSet::new();
        extract_repo_paths_from_text(
            "- Edit: `scripts/build-vibecrafted-release.sh` and a.rs\n- add (or new `tests/tui/test_repo_symlink_free.py`)\n",
            true,
            &mut paths,
        );
        assert_eq!(
            paths.iter().cloned().collect::<Vec<_>>(),
            ["scripts/build-vibecrafted-release.sh"]
        );
    }
}
