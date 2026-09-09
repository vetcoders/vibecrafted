//! Launcher catalog: agents, model-pin support, permission/sandbox cells and
//! environment availability, read from the canonical launcher
//! (`vibecrafted capabilities --json`, schema
//! `vibecrafted.workflow_capabilities.v1`).
//!
//! VOC carries no agent or model list of its own. Until the catalog is
//! loaded, no launch is offered; if the launcher cannot describe a choice, the
//! choice is shown as unavailable with the launcher's reason.

use anyhow::Context;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::Path;
use std::process::Command;

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct ControlCell {
    #[serde(default)]
    pub permissions: String,
    #[serde(default)]
    pub sandbox: String,
    #[serde(default)]
    pub supported: bool,
    #[serde(default)]
    pub permissions_effective: String,
    #[serde(default)]
    pub sandbox_effective: String,
    #[serde(default)]
    pub provider_flags: Vec<String>,
    #[serde(default)]
    pub reason: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct ModelOverrideCapability {
    #[serde(default)]
    pub supported: bool,
    #[serde(default)]
    pub flag: String,
    #[serde(default)]
    pub reason: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct ProviderCapability {
    #[serde(default)]
    pub binary: String,
    #[serde(default)]
    pub executable: String,
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub model_override: ModelOverrideCapability,
    #[serde(default)]
    pub permissions_default: String,
    #[serde(default)]
    pub controls: Vec<ControlCell>,
}

impl ProviderCapability {
    pub fn control_cell(
        &self,
        permissions: Option<&str>,
        sandbox: Option<&str>,
    ) -> Option<&ControlCell> {
        let permissions = permissions.unwrap_or("");
        let sandbox = sandbox.unwrap_or("");
        self.controls
            .iter()
            .find(|cell| cell.permissions == permissions && cell.sandbox == sandbox)
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
pub struct EnvironmentCapability {
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub skill_launcher_supported: bool,
    #[serde(default)]
    pub skill_launcher_flags: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LauncherCatalog {
    pub schema: String,
    /// Declarable agents (the launcher's `agents` minus the research-only
    /// `swarm` execution agent), in the launcher's order.
    pub agents: Vec<String>,
    pub providers: BTreeMap<String, ProviderCapability>,
    pub environments: BTreeMap<String, EnvironmentCapability>,
}

#[derive(Debug, Deserialize)]
struct CatalogPayload {
    #[serde(default)]
    schema: String,
    #[serde(default)]
    agents: Vec<String>,
    #[serde(default)]
    providers: BTreeMap<String, ProviderCapability>,
    #[serde(default)]
    environments: BTreeMap<String, EnvironmentCapability>,
}

impl LauncherCatalog {
    pub fn parse(bytes: &[u8]) -> anyhow::Result<Self> {
        let text = String::from_utf8_lossy(bytes);
        let trimmed = text.trim();
        if trimmed.is_empty() {
            anyhow::bail!("launcher printed an empty catalog");
        }
        let start = trimmed.find('{').unwrap_or(0);
        let payload: CatalogPayload = serde_json::from_str(&trimmed[start..])
            .context("launcher catalog is not valid JSON")?;
        if payload.schema != "vibecrafted.workflow_capabilities.v1" {
            anyhow::bail!(
                "unexpected launcher catalog schema: {}",
                if payload.schema.is_empty() {
                    "<missing>"
                } else {
                    payload.schema.as_str()
                }
            );
        }
        let agents = payload
            .agents
            .into_iter()
            .filter(|agent| agent != "swarm")
            .collect::<Vec<_>>();
        if agents.is_empty() {
            anyhow::bail!("launcher catalog lists no declarable agents");
        }
        Ok(Self {
            schema: payload.schema,
            agents,
            providers: payload.providers,
            environments: payload.environments,
        })
    }

    /// Ask the canonical launcher for its catalog. Read-only on the launcher
    /// side and bounded by `CATALOG_ANSWER_DEADLINE`; blocks the calling
    /// thread, so callers run it off the UI loop.
    pub fn load(deck: &Path, env: &BTreeMap<String, OsString>) -> anyhow::Result<Self> {
        // `deck` is the operator's configured command deck (a path from --command-deck
        // or the resolved default), and every argument below is a fixed literal. No
        // shell is involved and no caller-supplied string reaches argv.
        let mut command = Command::new(deck); // nosemgrep: rust.actix.command-injection.rust-actix-command-injection.rust-actix-command-injection
        command.args(["capabilities", "--json"]).envs(env);
        let what = format!("{} capabilities --json", deck.display());
        let output = match crate::launch::run_bounded(
            command,
            None,
            crate::launch::CATALOG_ANSWER_DEADLINE,
            &what,
        )? {
            crate::launch::LauncherRun::Completed(output) => output,
            // The probe starts nothing, so an unanswered catalog is simply an
            // unavailable catalog — and every launch stays refused until one
            // arrives.
            crate::launch::LauncherRun::Undecided { waited, .. } => {
                anyhow::bail!("{what} did not answer within {}s", waited.as_secs().max(1))
            }
        };
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            anyhow::bail!(
                "{} capabilities --json exited with {}{}",
                deck.display(),
                output.status,
                if stderr.trim().is_empty() {
                    String::new()
                } else {
                    format!(": {}", stderr.trim())
                }
            );
        }
        Self::parse(&output.stdout)
    }

    pub fn provider(&self, agent: &str) -> Option<&ProviderCapability> {
        self.providers.get(agent)
    }

    pub fn environment(&self, policy_id: &str) -> Option<&EnvironmentCapability> {
        self.environments.get(policy_id)
    }

    /// Why an agent cannot be launched right now, or `Ok` when it can.
    pub fn agent_availability(&self, agent: &str) -> Result<&ProviderCapability, String> {
        if !self.agents.iter().any(|name| name == agent) {
            return Err(format!("{agent} is not in the launcher catalog"));
        }
        match self.providers.get(agent) {
            Some(provider) if provider.available => Ok(provider),
            Some(provider) => Err(if provider.reason.is_empty() {
                format!("{agent} executable not found")
            } else {
                provider.reason.clone()
            }),
            None => Err(format!(
                "launcher catalog carries no provider contract for {agent}; update vibecrafted"
            )),
        }
    }

    /// Availability of an environment for skill launchers, with the
    /// launcher's own reason when it is not offered.
    pub fn environment_availability(
        &self,
        policy_id: &str,
    ) -> Result<&EnvironmentCapability, String> {
        match self.environments.get(policy_id) {
            Some(environment) if environment.available && environment.skill_launcher_supported => {
                Ok(environment)
            }
            Some(environment) if !environment.available => Err(if environment.reason.is_empty() {
                format!("{policy_id} is unavailable on this host")
            } else {
                environment.reason.clone()
            }),
            Some(_) => Err(format!(
                "{policy_id} has no skill-launcher entrypoint (interactive Agent Workspaces only)"
            )),
            None => Err(format!(
                "launcher catalog does not describe {policy_id}; update vibecrafted"
            )),
        }
    }
}

/// Lifecycle of the catalog inside the console.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum CatalogState {
    #[default]
    Loading,
    Ready(LauncherCatalog),
    Failed(String),
}

impl CatalogState {
    pub fn ready(&self) -> Option<&LauncherCatalog> {
        match self {
            CatalogState::Ready(catalog) => Some(catalog),
            _ => None,
        }
    }

    pub fn status_line(&self) -> String {
        match self {
            CatalogState::Loading => "catalog: loading from the launcher…".to_string(),
            CatalogState::Ready(catalog) => format!(
                "catalog: {} agents from the launcher ({})",
                catalog.agents.len(),
                catalog.schema
            ),
            CatalogState::Failed(reason) => format!("catalog: unavailable — {reason} (r retries)"),
        }
    }
}

/// The frozen launcher catalog used by in-crate tests and snapshots, so the
/// console renders the same agent list on a bare machine and a provisioned
/// one. Never a probe of this host.
#[cfg(test)]
pub(crate) fn fixture_catalog() -> LauncherCatalog {
    LauncherCatalog::parse(include_str!("../tests/fixtures/capabilities.json").as_bytes())
        .expect("the bundled capabilities fixture must parse")
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIXTURE: &str = include_str!("../tests/fixtures/capabilities.json");

    #[test]
    fn parses_the_launcher_catalog_and_drops_swarm() {
        let catalog = LauncherCatalog::parse(FIXTURE.as_bytes()).unwrap();
        assert_eq!(
            catalog.agents,
            vec!["agy", "claude", "codex", "cursor", "grok", "junie"]
        );
        assert!(catalog.provider("claude").unwrap().model_override.supported);
        assert!(!catalog.provider("junie").unwrap().model_override.supported);
        assert_eq!(
            catalog.environment("cloud-soon").unwrap().reason,
            "coming soon"
        );
    }

    #[test]
    fn availability_answers_carry_launcher_reasons() {
        let catalog = LauncherCatalog::parse(FIXTURE.as_bytes()).unwrap();
        assert!(catalog.agent_availability("claude").is_ok());
        assert_eq!(
            catalog.agent_availability("grok").unwrap_err(),
            "grok executable not found"
        );
        assert!(
            catalog
                .agent_availability("gemini")
                .unwrap_err()
                .contains("not in the launcher catalog")
        );
        assert!(catalog.environment_availability("local-native").is_ok());
        assert!(catalog.environment_availability("local-worktrees").is_ok());
        assert_eq!(
            catalog.environment_availability("local-vm").unwrap_err(),
            "no canonical VM entrypoint"
        );
        let agy = catalog.provider("agy").unwrap();
        let cell = agy.control_cell(None, Some("false")).unwrap();
        assert!(!cell.supported);
        assert!(cell.reason.contains("cannot be enforced"));
        assert!(agy.control_cell(Some("read-only"), None).unwrap().supported);
    }

    #[test]
    fn rejects_foreign_or_empty_payloads() {
        assert!(LauncherCatalog::parse(b"").is_err());
        assert!(LauncherCatalog::parse(br#"{"schema":"other","agents":["claude"]}"#).is_err());
        assert!(
            LauncherCatalog::parse(
                br#"{"schema":"vibecrafted.workflow_capabilities.v1","agents":["swarm"]}"#
            )
            .is_err()
        );
        let minimal = LauncherCatalog::parse(
            br#"{"schema":"vibecrafted.workflow_capabilities.v1","agents":["claude"]}"#,
        )
        .unwrap();
        assert!(
            minimal
                .agent_availability("claude")
                .unwrap_err()
                .contains("no provider contract")
        );
        assert!(
            minimal
                .environment_availability("local-native")
                .unwrap_err()
                .contains("does not describe")
        );
    }
}
