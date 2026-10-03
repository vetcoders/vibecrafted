# vc-workflow — output templates

### Output: CONTEXT.md

Write to `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<ts>_<slug>_CONTEXT.md`:

```markdown
---
run_id: <id>
agent: <claude|codex|agy|cursor>
skill: vc-workflow
project: <repo>
status: completed
created: <ISO-8601>
---

# Examination: <slug>

## Repo Health

- <3-5 bullets from repo-view>

## Scope

- Target dirs: <list>
- Why: <rationale>

## Critical Files

| File | Consumers | Risk | Notes |

## Symbols Found

- <existing symbols relevant to task>

## Risk Map

- <high-impact files + mitigation>

## Decision

- [ ] Research needed (unknown APIs/patterns)
- [ ] Skip to Implement (well-understood domain)
```

### Output: RESEARCH.md

```markdown
---
run_id: <id>
agent: <claude|codex|agy|cursor>
skill: vc-workflow
project: <repo>
status: completed
created: <ISO-8601>
---

# Research: <slug>

## Questions (from Examination)

1. <question>

## Findings

### Q1: <question>

- **Source**: <URL or Context7 lib>
- **Answer**: <concise>
- **Code example**: <if applicable>

## Architectural Decision

- Chosen: <decision>
- Why: <findings-based>
- Alternatives rejected: <reasons>

## Implementation Notes

- <concrete guidance for agents>
```
