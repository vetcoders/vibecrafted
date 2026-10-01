# `vc-forensics` Flow

## Flow

```mermaid
flowchart TD
    A[vc-init: map, intent, Git and security baseline] --> B[Trace the real execution path with Loctree]
    B --> C[Prove the defect with a failing regression]
    C --> D{Is the authority clear?}
    D -->|no| E[Report the collision for a Founder decision]
    D -->|yes| F[Cut the root cause and rewire consumers]
    F --> G[Run the same regression, quality gates and real product path]
    G --> H[Record evidence and bounded commit in the forensics journal]
    H --> I[Report the highest verified state and remaining acceptance]
```

## Routes

| Entry                           | Args                             | Produces                                 | Exit             |
| ------------------------------- | -------------------------------- | ---------------------------------------- | ---------------- |
| `vibecrafted forensics <agent>` | `--prompt` or `--file`           | report, transcript, run metadata         | dispatch receipt |
| `/vc-forensics`                 | bounded investigation and repair | evidence, regression and authored commit | report           |

## Evidence and boundaries

[SKILL.md](SKILL.md) owns the protocol. Use
[references/forensics-evidence-spec.md](references/forensics-evidence-spec.md)
for the proof shape and
[references/forensics-journal-template.md](references/forensics-journal-template.md)
for append-only entries in `./.loctree/forensics/JOURNAL.md`.

- Pin the baseline and inspect `slice` / `impact` before changing a source owner.
- The same regression must fail before the repair and pass after it.
- An ambiguous authority requires a Founder decision; do not add another owner.
- Repository gates do not prove runtime, installation, or delivery. Report each
  acceptance surface at its actual verified level.
- Stage the bounded cut only. Integration, install and release follow the current
  run's authorization and the canonical skill's boundaries.
