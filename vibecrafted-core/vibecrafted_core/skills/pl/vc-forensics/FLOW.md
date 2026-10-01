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

[SKILL.md](SKILL.md) jest właścicielem protokołu. Użyj
[references/forensics-evidence-spec.md](references/forensics-evidence-spec.md)
dla formatu dowodów oraz
[references/forensics-journal-template.md](references/forensics-journal-template.md)
dla wpisów append-only w `./.loctree/forensics/JOURNAL.md`.

- Przypnij baseline i przeczytaj `slice` / `impact` przed zmianą właściciela źródłowego.
- Ta sama regresja musi być czerwona przed naprawą i zielona po niej.
- Niejednoznaczny właściciel wymaga decyzji Foundera; nie dodawaj kolejnego.
- Bramki repo nie dowodzą runtime, instalacji ani dostarczenia. Każdą powierzchnię
  akceptacji opisz na rzeczywiście zweryfikowanym poziomie.
- Stage obejmuje tylko ograniczony cut. Integracja, instalacja i release podlegają
  upoważnieniu bieżącego runu i granicom kanonicznego skilla.
