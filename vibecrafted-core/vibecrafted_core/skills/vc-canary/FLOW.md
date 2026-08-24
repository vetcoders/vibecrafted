# `vc-canary` Flow

```mermaid
flowchart TD
  A[$PWD] --> B[Phase 0: HEAD + dirty + worktrees + atlas --refresh]
  B --> C{coverage pass?}
  C -->|no| D[STOP + loctree-fail hak]
  C -->|yes| E[Phase I: repo-view / focus / twins / crowd / hotspots]
  E --> F[candidate decision axes]
  F --> G[Phase II: per axis — find --discover → occurrences → body → slice → follow]
  G --> H[pair verdicts: SAME / VARIANT / DRIFTED / BYPASS / FALSE]
  H --> I[Phase III: prism + writer/arbiter/observer/projection]
  I --> J[findings: CUT_BLOCKER / CUT_COHERENT / FOLLOW_UP / OBSERVATION]
  J --> K[append .loctree/canary/JOURNAL.md]
  K --> L[report → discuss → decide — no code mutation]
```

## Phase contract

| Phase | Question                                            | Output                                |
| ----- | --------------------------------------------------- | ------------------------------------- |
| 0     | Is the tree fresh and fully covered, with receipts? | HEAD/dirty/worktree record + atlas    |
| I     | Which classes of truth might have >1 author?        | candidate decision axes               |
| II    | Where does each decision actually live — proven?    | pair verdicts + absence falsification |
| III   | Who is writer / arbiter / observer / projection?    | classified findings + prism score     |
| QC    | What does the operator decide?                      | appended journal entry + report       |
