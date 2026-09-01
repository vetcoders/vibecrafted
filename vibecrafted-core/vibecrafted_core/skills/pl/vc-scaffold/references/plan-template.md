# Szablon SCAFFOLD.md

Użyj tego szablonu jako wyjścia planowania. W swoim faktycznym wyjściu wytnij komentarze.

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|cursor>
skill: <vc-scaffold|vc-workflow|vc-implement>
project: <repo-name>
status: pending
vector: <stabilize|implement|recon|e2e> # selects the gate profile = what counts as delivery
created: <ISO-8601 timestamp>
founder_interview_evidence: <ścieżka journala | sesja/ekstrakt AICX | odpowiedzi z bieżącej rozmowy>
dispatch_artifact: <absolutny root planu>/<plan-id>.dispatch.toml
---

# Architecture Plan: [Project Name]

## Problem Statement

[1-2 zdania. Jaki problem rozwiązujemy? Czemu to ma znaczenie?]

Przykład: „Monolit staje się nieutrzymywalny. Musimy wyciągnąć serwis płatności do osobnego serwisu, żeby zespoły mogły dowozić niezależnie, bez koordynowania deployów."

## Key Architectural Decisions

### Decision 1: [Name]

**Choice:** [Co robimy]
**Trade-off:** [Z czego rezygnujemy]
**Why:** [Czemu to lepsze niż alternatywa]

### Decision 2: [Name]

**Choice:** [Co robimy]
**Trade-off:** [Z czego rezygnujemy]
**Why:** [Czemu to lepsze niż alternatywa]

(Trzymaj się 3-5 decyzji. Nie każdego technicznego szczegółu.)

## Scope Boundaries

### Phase 1: MVP (This Sprint/Cycle)

**In scope:**

- Ficzer/komponent A
- Ficzer/komponent B
- Infrastruktura testowa

**Out of scope:**

- Ficzer X (nice to have, dowozi się w fazie 2)
- Optymalizacja Y (nie blokuje MVP)

**Explicitly out of scope:**

- Przepisanie starego systemu (nie wydarza się)
- Migracja do języka Z (poza granicami)

## Architecture Overview

[Diagram ASCII lub krótki opis]

Przykład:
```

User → API Gateway → Auth Service → Payment Service → Stripe
↓
Cache Layer
↓
Database

```

## Task Breakdown

Każdy task jest agent-ready. Agenci wykonują równolegle, gdy pozwalają na to zależności. Każdy task niesie
marker `state` `[ ] [~] [?] [!] [x]` (zobacz references/measure-core.md); tylko delivery-verifier przerzuca
`[~]→[x]`. vc-operator czyta kolumnę `state`, żeby trigger/stop.

### Task 1: [Imperative title]   `state: [ ]`
**Vector:** [stabilize|implement|recon|e2e]
**Produces:** [Jaki kod/config/testy powstają]
**Depends on:** [Task X, gotowa infrastruktura]
**Owner:** [Skill agenta lub rola człowieka]
**Delivery-verifier:** [niefałszowalny test, który przerzuca [~]→[x]; bez niego task dowozi się jako [?]]
**Acceptance:** [intent vs baseline — co dowodzi, że delivery ≈ claim, nie tylko „agent tak powiedział"]
**Pre-handoff baseline:** [branch + HEAD + git status + zmienione pliki + bramki/znane awarie + dokładna następna instrukcja]

Przykład:
```

Task: Build authentication middleware state: [ ]
Vector: implement
Produces: /middleware/auth.ts, /tests/auth.test.ts
Depends on: Infrastructure up, database schema
Owner: Core backend agent
Delivery-verifier: `pnpm test auth` green — rejects invalid tokens, passes valid; flips [~]→[x]
Acceptance: intent (auth enforced on all routes) vs baseline (routes open); delivery proven by the verifier, not "agent said so"
Pre-handoff baseline: branch, HEAD, git status, changed files, verifier output, known failures, next instruction

````

## Dispatch Contract

Root planu zawiera `<plan-id>.dispatch.toml` z `schema = "vibecrafted.dispatch.v1"`. Mapuje każdy task powyżej
na jeden wpis `[[cuts]]` z zależnościami, agentem/workflow, promptem wskazującym brief oraz
delivery-verifierem. `vibecrafted dispatch <absolutny-root-planu>/<plan-id>.dispatch.toml --doctor`
musi przejść przed handoffem. Wielocięciowe wykonanie należy do `/vc-ship` A→Z.

Jeśli plan używa compile embargo, podaj marker fazy, listę odroczonych bramek, ścieżkę repo-owned
hooka/polityki, ref recovery oraz atestację zdjęcia embarga wymaganą przez
`references/compile-embargo.md`. Jeśli taki mechanizm polityk nie istnieje, dodaj go jako prerequisite
albo usuń embargo; nigdy nie zastępuj go `--no-verify` ani push-banem.

## Test Gates (per Vector profile)

Każda faza ma bramkę dostarczania wybraną przez jej `Vector` (zobacz references/measure-core.md) — bramka
definiuje, co liczy się jako delivery, więc różni się wg Vectora. Nie przesuwaj fazy, dopóki jej bramka nie przerzuci
każdego cięcia `[~]→[x]`.

- **implement** → ficzer działa + testy zielone na ścieżkach core
- **stabilize** → krwawienie ustaje + bramka regresji/canary zielona (busy ≠ dead)
- **recon** → mapa/odpowiedź dostarczona z referencjami do evidence
- **e2e** → pełna ścieżka przebiega end-to-end
- **always** → żadnych odsłoniętych sekretów; bramka bezpieczeństwa nie pominięta (`--no-verify` zabronione)

## Living Tree Note

Ten plan żyje. Zmienia się, gdy się uczymy. Gdy zmieniasz plan:

1. **Opatrz datą** zmianę
2. **Wyjaśnij dlaczego** (nowe ograniczenie, odkryta zależność, zmiana rynku)
3. **Przejdź task breakdown na nowo**, jeśli scope się zmienił
4. **Zaktualizuj kryteria akceptacji**, jeśli definicje się przesunęły

Udokumentuj rozumowanie. Przyszli inżynierowie ci podziękują.

---

## Running This Plan

`<plan-id>.dispatch.toml` jest jedynym kontraktem wykonania. Zweryfikuj go, a potem przekaż dokładnie
ten artefakt do `/vc-ship`; nie odpalaj cięć ręcznie:

```bash
vibecrafted dispatch <absolutny-root-planu>/<plan-id>.dispatch.toml --doctor
vibecrafted dispatch <absolutny-root-planu>/<plan-id>.dispatch.toml --dry-run --json
````

`/vc-ship` jest właścicielem startu A→Z, nadzoru, resume/recovery, bramek verifierów i ukończenia
przez deterministyczny dispatcher. Ta sekcja nie może zawierać bezpośredniej recepty start/resume
ani recepty per cięcie `vibecrafted workflow ... --prompt`.

### Awaryjny fallback ręczny

Tylko gdy `/vc-ship` lub jego supervisor są dowodnie niedostępne, zapisz dokładną awarię i powód
konieczności fallbacku przed podaniem ograniczonej komendy direct-dispatch albo recovery per cięcie.
Zapisz, jak kontrola wraca do `/vc-ship`; fallback nigdy nie może stać się drugą ścieżką wykonania.

Żadnego machania rękami. Jasna praca. Jasne kryteria. Tak dowożą founderzy.

```

```
