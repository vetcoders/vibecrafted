# vc-workflow — szablony wyjść

### Output: CONTEXT.md

Zapisz do `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<ts>_<slug>_CONTEXT.md`:

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

- <3-5 punktów z repo-view>

## Scope

- Target dirs: <lista>
- Why: <uzasadnienie>

## Critical Files

| File | Consumers | Risk | Notes |

## Symbols Found

- <istniejące symbole istotne dla zadania>

## Risk Map

- <pliki o dużym wpływie + mitygacja>

## Decision

- [ ] Potrzebny research (nieznane API/wzorce)
- [ ] Przejdź do Implement (dobrze znana domena)
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

1. <pytanie>

## Findings

### Q1: <pytanie>

- **Source**: <URL lub biblioteka Context7>
- **Answer**: <zwięźle>
- **Code example**: <jeśli dotyczy>

## Architectural Decision

- Chosen: <decyzja>
- Why: <na podstawie ustaleń>
- Alternatives rejected: <powody>

## Implementation Notes

- <konkretne wskazówki dla agentów>
```
