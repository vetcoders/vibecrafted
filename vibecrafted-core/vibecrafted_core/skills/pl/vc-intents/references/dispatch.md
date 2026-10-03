# Dispatch — the hunt is a fan-out shape

Przeczytaj przed fazą 3, 5 lub 9, gdy jednostka pracy przekracza jedną sesję lub cut.

## Why this phase exists

Trzy fazy można zrównoleglić, a jedna z nich jest tutaj najwolniejsza:

| Phase                       | The parallel unit    | Why serial hurts                                           |
| --------------------------- | -------------------- | ---------------------------------------------------------- |
| 3 — czytaj źródła           | N niezależnych sesji | czytanie wyznacza dolną granicę czasu całego polowania     |
| 5 — weryfikuj strukturalnie | M niezależnych pytań | każde to ograniczone wywołanie Loctree bez wspólnego stanu |
| 9 — dostarcz                | K cutów              | rozłączne domeny, jeśli kolejkę uporządkowano uczciwie     |

## The surface is Vibecrafted

Sięganie poza framework po gołego native subagenta jest awarią, której framework
ma zapobiegać: brak rekordu runu, raportu i transkryptu pod
`~/.vibecrafted/control_plane/runtime_runs/`, a kolejny agent nie może wznowić
ani sprawdzić pracy. Polowanie bez możliwości wznowienia przeczy własnemu ledgerowi.

```bash
# Cold worker — a self-contained unit of work, started without this hunt's context
vibecrafted <skill> <agent> --prompt '<brief>'
vibecrafted <skill> <agent> --file  '/path/to/brief.md'

# Fork — branches THIS session into a new one; the source session stays untouched
vibecrafted fork claude --session current -p '<what this branch should chase>'
vibecrafted fork codex  --session current --runtime headless --file <plan.md>
vc-fork grok --session <provider-session-id> --placement floating -p '<...>'
```

Istotne flagi (referencją jest `vibecrafted fork --help`):

- `--session <id|current|last>` — sesja providera, od której odgałęziasz.
  **Nigdy run id `work-*`**; od tego jest `--run-id`.
  `"$(aicx sessions current)"` rozwiązuje się do tego samego co `current`.
- `--runtime visible|terminal` dla gołego interaktywnego forka; `headless`
  dla task forka z `--prompt`/`--file` (headless jest domyślny po podaniu wejścia).
- `--permissions bypass|auto|accept-edits|read-only` — fan-out analizy powinien
  mieć `read-only`; codex nie ma `accept-edits`.
- `--repo <path|org/name>`, `--base <ref>`, `--model <name>`,
  `--execution-runtime living-tree|local-worktrees`, `--worktree [true|false]`.

Natywny fork wspierają **claude, codex i grok**. Dla cursor, agy i junie nie ma
zweryfikowanego native fork adaptera: `vibecrafted resume <agent> --session
<id>` kontynuuje **oryginalną** sesję. Raportuj resume, nigdy fork.

## Fork or cold worker — the decision that matters

**Fork, gdy worker potrzebuje tego, co polowanie już wie**: shortlisty,
rozstrzygniętych korekt, stanu ledgera, powodu klasyfikacji `claim-only`.
Fork dziedziczy rozmowę, więc nie trzeba odtwarzać godziny rekonstrukcji.
Dostarczenie (faza 9) zwykle ma ten kształt.

**Cold-dispatch, gdy jednostka pracy opisuje się sama**: „przeczytaj sesję X,
raportuj każdą wypowiedź Foundera o Y z cytowaniami”. Jest tańszy i nie
może odziedziczyć twojego uprzedzenia co do wyniku.

Drugi argument dotyczy dowodu, nie kosztu. Przy pytaniu „czy Founder naprawdę
to powiedział” odziedziczony kontekst jest **wadą**: worker dostaje hipotezę
wraz z zadaniem i gotową drogę jej potwierdzenia. Fazy 2–3 uruchamiaj więc
**cold**, a fazę 9 **forked** — odwrotnie niż intuicja „ważne potrzebuje więcej kontekstu”.

## Escalation

`vc-delegate` określa, kiedy cut przestaje być ograniczony i należy do zewnętrznej
floty zamiast delegacji w procesie. Obowiązuje jego reguła modelu: ten sam
frontier co parent; dla Claude `opus[1m]` na długich polowaniach i `sonnet[1m]`
na lekkich. Etykieta roli subagenta nie dowodzi modelu ani reasoning effort —
gdy to ma znaczenie, potwierdź je w metadanych runtime.

## The ledger has one writer

Workerzy zwracają ustalenia; integrator składa je jednowątkowo. Równoległe
zapisy ledgera gubią wpisy tak jak równoległe commity gubią pliki: oba runy
przepisują z własnego odczytu, a późniejszy zapis po cichu wygrywa.

Fan-out analizy pozostaje read-only. Fan-out implementacji to formacja Fleet
Worktrees: zapisany plan, verifiers w commitach przed startem, rozłączne domeny,
jeden integrator. Mniej niż ta formacja nie upoważnia równoległych zapisów.

## Benchmark note

Gdy fan-out ma _ocenić_ skill, zamiast go używać, jawnie ogranicz workerów:
read-only, bez checkoutu i instalatorów, zapis tylko do katalogu wynikowego,
stop przed fazą 9. Sześć agentów implementujących równolegle w żywych repo
to incydent, nie benchmark.
