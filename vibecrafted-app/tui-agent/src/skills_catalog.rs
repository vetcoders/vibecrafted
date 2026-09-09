//! First-class Vibecrafted skills VOC can declare.
//!
//! This catalog names skills only. Agents, models, permission/sandbox cells
//! and environments come from the launcher's own catalog
//! (`crate::catalog::LauncherCatalog`), never from a list carried here — that
//! is how the retired `gemini` launcher used to survive in the UI after the
//! launcher had already dropped it.

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
    /// Agent this skill prefers; empty means "whatever the operator selected".
    pub default_agent: &'static str,
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
        default_agent: "",
        accepts: SkillPayloadKind::Optional,
    },
    SkillEntry {
        slug: "vc-workflow",
        display: "Workflow",
        one_line: "Examine, research, implement",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-research",
        display: "Research",
        one_line: "Standalone research pass",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-review",
        display: "Review",
        one_line: "Bounded review pipeline",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-marbles",
        display: "Marbles",
        one_line: "Truth convergence loop",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-polarize",
        display: "Polarize",
        one_line: "One sharp truth after marbles",
        default_agent: "codex",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-dou",
        display: "DoU",
        one_line: "Definition of Undone audit",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-hydrate",
        display: "Hydrate",
        one_line: "Package and GTM hydration",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-decorate",
        display: "Decorate",
        one_line: "Late-stage visual finish",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-release",
        display: "Release",
        one_line: "Outward ship path",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-ownership",
        display: "Ownership",
        one_line: "Full-spectrum ownership mode",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-partner",
        display: "Partner",
        one_line: "Shared steering at the operator's side",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-followup",
        display: "Followup",
        one_line: "Post-implementation audit",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-audit",
        display: "Audit",
        one_line: "Spec falsification of a plan",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-prune",
        display: "Prune",
        one_line: "Repository curation",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-intents",
        display: "Intents",
        one_line: "Intent-to-runtime truth audit",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-scaffold",
        display: "Scaffold",
        one_line: "Founder-first architecture plan",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-implement",
        display: "Implement",
        one_line: "End-to-end implementation",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-justdo",
        display: "JustDo",
        one_line: "Implement alias",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-delegate",
        display: "Delegate",
        one_line: "Bounded native delegation",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-agents",
        display: "Agents",
        one_line: "External agent fleet entry",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-dispatch",
        display: "Dispatch",
        one_line: "External fleet line conductor",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-trust",
        display: "Trust",
        one_line: "Post-hoc claim falsification",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-guard",
        display: "Guard",
        one_line: "In-flight process and gate enforcer",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
    SkillEntry {
        slug: "vc-skillaunch",
        display: "Skillaunch",
        one_line: "Distill a workflow into a skill",
        default_agent: "",
        accepts: SkillPayloadKind::PromptOrFile,
    },
];

pub fn catalog_entry(slug: &str) -> Option<&'static SkillEntry> {
    CATALOG.iter().find(|entry| entry.slug == slug)
}
