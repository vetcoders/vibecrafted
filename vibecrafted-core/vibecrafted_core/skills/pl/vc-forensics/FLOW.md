# `vc-forensics` Flow

```mermaid
flowchart TD
    A[Consume fresh vc-init map, intent and Git baseline] --> B[Trace input through committed result and delivery]
    B --> C[Falsify hypotheses and record causal evidence]
    C --> D{Repair authorized and authority resolved?}
    D -->|diagnosis only| E[Report findings and open decisions]
    D -->|yes| F[Integrator designs independent acceptance; bounded worker writes source]
    F --> G[Integrator checks baseline RED, candidate GREEN and positive controls]
    G --> H[Verify admission; separately verify installed and live behavior]
    E --> I[Canonical journal, HTML notebook and durable handoff]
    H --> I
```

- `/vc-forensics`: interaktywne śledztwo w tej sesji.
- `vibecrafted workflow <agent> --file <brief.md>`: bounded external workflow;
  brief explicitly loads this skill, respecting current role and compile embargo.
- Brak dedykowanego `vibecrafted forensics` launcher.

[SKILL.md](SKILL.md) owns the protocol. Read the
[evidence contract](references/forensics-evidence-spec.md) for causal proof,
[journal template](references/forensics-journal-template.md) for append-only
`<repo-root>/.vibecrafted/THE_JOURNAL.md`, and
[notebook contract](references/report-notebook.md) for offline HTML imports.
Końcowy raport workera, zielona bramka ani eksport notebooka nie dowodzą odbioru na żywo.
