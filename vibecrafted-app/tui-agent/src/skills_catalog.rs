use crate::launch::LaunchCommand;
use std::collections::BTreeMap;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillAgent {
    Claude,
    Codex,
    Cursor,
    Gemini,
    Any,
}

impl SkillAgent {
    pub fn label(self) -> &'static str {
        match self {
            SkillAgent::Claude => "claude",
            SkillAgent::Codex => "codex",
            SkillAgent::Cursor => "cursor",
            SkillAgent::Gemini => "gemini",
            SkillAgent::Any => "any",
        }
    }

    pub fn from_cli_token(raw: &str) -> Self {
        match raw {
            "claude" => SkillAgent::Claude,
            "gemini" => SkillAgent::Gemini,
            "codex" => SkillAgent::Codex,
            "cursor" => SkillAgent::Cursor,
            _ => SkillAgent::Any,
        }
    }

    pub fn resolved_cli_token(self, fallback: SkillAgent) -> &'static str {
        match self {
            SkillAgent::Claude => "claude",
            SkillAgent::Codex => "codex",
            SkillAgent::Cursor => "cursor",
            SkillAgent::Gemini => "gemini",
            SkillAgent::Any => match fallback {
                SkillAgent::Claude => "claude",
                SkillAgent::Cursor => "cursor",
                SkillAgent::Gemini => "gemini",
                _ => "codex",
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SkillPayload {
    Prompt(String),
    File(PathBuf),
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SkillPayloadKind {
    Optional,
    PromptOrFile,
    None,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SkillEntry {
    pub slug: &'static str,
    pub display: &'static str,
    pub one_line: &'static str,
    pub default_agent: SkillAgent,
    pub accepts: SkillPayloadKind,
}

impl SkillEntry {
    pub fn command_token(self) -> &'static str {
        self.slug
            .strip_prefix("vc-")
            .expect("catalog slugs must be vc-*")
    }

    pub fn emphasized(self) -> bool {
        self.slug == "vc-polarize"
    }
}

pub const CATALOG: &[SkillEntry] = &[
    SkillEntry {
        slug: "vc-init",
        display: "Init",
        one_line: "Technical due diligence",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::Optional,
    },
    SkillEntry {
        slug: "vc-workflow",
        display: "Workflow",
        one_line: "Examine, research, implement",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-research",
        display: "Research",
        one_line: "Standalone research pass",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-review",
        display: "Review",
        one_line: "Bounded review pipeline",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-marbles",
        display: "Marbles",
        one_line: "Truth convergence loop",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-polarize",
        display: "Polarize",
        one_line: "One sharp truth after marbles",
        default_agent: SkillAgent::Codex,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-dou",
        display: "DoU",
        one_line: "Definition of Undone audit",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-hydrate",
        display: "Hydrate",
        one_line: "Package and GTM hydration",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-decorate",
        display: "Decorate",
        one_line: "Late-stage visual finish",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-release",
        display: "Release",
        one_line: "Outward ship path",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-ownership",
        display: "Ownership",
        one_line: "Full-spectrum ownership mode",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-partner",
        display: "Partner",
        one_line: "Executive debugging partner",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-followup",
        display: "Followup",
        one_line: "Post-implementation audit",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-audit",
        display: "Audit",
        one_line: "Spec falsification of a plan",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-prune",
        display: "Prune",
        one_line: "Repository curation",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-intents",
        display: "Intents",
        one_line: "Intent-to-runtime truth audit",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-scaffold",
        display: "Scaffold",
        one_line: "Founder-first architecture plan",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-implement",
        display: "Implement",
        one_line: "End-to-end implementation",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-justdo",
        display: "JustDo",
        one_line: "Implement alias",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-delegate",
        display: "Delegate",
        one_line: "Bounded native delegation",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-agents",
        display: "Agents",
        one_line: "External agent fleet entry",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-dispatch",
        display: "Dispatch",
        one_line: "External fleet line conductor",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-trust",
        display: "Trust",
        one_line: "Post-hoc claim falsification",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-guard",
        display: "Guard",
        one_line: "In-flight process and gate enforcer",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-skillaunch",
        display: "Skillaunch",
        one_line: "Distill a workflow into a skill",
        default_agent: SkillAgent::Any,
        accepts: SkillPayloadKind::PromptOrFile,
    },
];

pub fn catalog_entry(slug: &str) -> Option<&'static SkillEntry> {
    CATALOG.iter().find(|entry| entry.slug == slug)
}

pub fn build_skill_launch_command(
    deck: impl AsRef<Path>,
    skill: &str,
    agent: SkillAgent,
    fallback_agent: SkillAgent,
    payload: &SkillPayload,
    env: BTreeMap<String, OsString>,
) -> LaunchCommand {
    let command_skill = skill.strip_prefix("vc-").unwrap_or(skill);
    let mut args: Vec<OsString> = vec![
        command_skill.into(),
        agent.resolved_cli_token(fallback_agent).into(),
    ];
    match payload {
        SkillPayload::Prompt(prompt) if !prompt.trim().is_empty() => {
            args.push("--prompt".into());
            args.push(prompt.clone().into());
        }
        SkillPayload::File(path) => {
            args.push("--file".into());
            args.push(path.as_os_str().to_os_string());
        }
        SkillPayload::Prompt(_) | SkillPayload::None => {}
    }
    LaunchCommand {
        program: deck.as_ref().to_path_buf(),
        args,
        env,
    }
}
