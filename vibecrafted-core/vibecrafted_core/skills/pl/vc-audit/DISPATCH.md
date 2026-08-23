# vc-audit — DISPATCH: Szablon operator dispatch

> Kanoniczny kształt ciała dla dispatchu `/vc-audit`. Wyprowadzony z
> 22-taskowego flow auditu Loctree, który ten skill formalizuje. Honoruje
> ośmiofazowy kontrakt z [`SKILL.md`](SKILL.md) oraz per-fazowy trace z
> [`PHASES.md`](PHASES.md).

Czytaj razem z [`SKILL.md`](SKILL.md) i [`PHASES.md`](PHASES.md).

---

## Wejścia, które otrzyma agent

1. Loctree Context Pack (auto-generowany przez `vc-init`).
2. Wszystkie pliki tasków / planu (ścieżki dostarcza operator).
3. Checkout repozytorium (bieżąca gałąź).
4. Opcjonalnie: wcześniejsze raporty od agentów deklarujących ukończenie.
   **Traktuj jako niezaufane, dopóki nie potwierdzone w kodzie/testach.**

---

## Ciało dispatchu na poziomie operatora

Wklej to ciało do dispatchu (podstaw placeholdery):

```
You are vc-audit running against the implementation plan at
{PLAN_DIR_OR_FILES}.

Your job is NOT to implement anything.
Your job is NOT to refactor.
Your job is NOT to "improve" the plan.
Your job is to falsify the completion claim.

Read all task files in full, extract their requirements atomically,
then compare against the current repository state. Default verdict
for every requirement is UNVERIFIED. A requirement earns PASS only
with all four:

- task requirement evidence (quoted),
- code evidence (file path + line + symbol),
- test evidence or justified test-gap classification,
- negative check proving old / forbidden behavior is not still present.

Do not edit code.
Do not move files.
Do not commit.
Do not "fix while auditing".

Treat completion claims as untrusted until verified in code/tests.

Phases (see PHASES.md for full detail):
1. Context Receipt (from vc-init's Loctree context pack)
2. Task Ingestion Receipt (FULL_READ every task file)
3. Atomic Requirements Extraction (jsonl matrix)
4. Positive + Negative Code Verification (loctree-first)
5. Adversarial Pass — actively try to prove implementation incomplete
6. Stage-Aware Verdict (landed scope vs deferred scope)
7. Per-Task Verdict Table (no narrative collapse)
8. Self-Attack Pass + Model Check on every PASS / PASS_WITH_GAPS

Emit exactly three files:
- audit_report.md
- audit_requirements_matrix.jsonl
- audit_trace.log

Executive verdict at top of audit_report.md MUST include:
- task counts per verdict
- P0..P3 counts
- top 5 risks
- next 5 actions
- model_confidence rating

Hard non-trust rules:
- Do not say "implemented" without code evidence
- Do not say "tested" without test evidence
- Do not say "complete" if any task was not full-read
- Do not collapse all tasks into a general summary
- Do not trust frontmatter status, prior agent reports, commit messages,
  AICX entries, chronicle notes, or "completed" annotations unless
  independently confirmed in current code/tests
```

---

## Kontrakt trace'u

Agent MUSI zapisać do `audit_trace.log`:

```
BEGIN
READ_CONTEXT_PACK
READ_TASK task_id_01
EXTRACT_REQUIREMENTS task_id_01 count=N
INSPECT_CODE task_id_01 files=N
VERIFY_TESTS task_id_01 tests=N
NEGATIVE_CHECK task_id_01 checks=N
DEPENDENCY_CHECK task_id_01
STAGE_CHECK task_id_01
CLASSIFY task_id_01 verdict=PASS_WITH_GAPS
SELF_ATTACK task_id_01
...
WRITE_REPORT
END
```

Trace to audit-auditu w rękach operatora. Jeśli w trace brakuje jakiejś
fazy, audit jest podejrzany.

---

## Typowe cele

- `plans/<initiative>/*.md` — katalog plików tasków z acceptance
  criteria
- pojedynczy multi-task dokument planu z jawnymi acceptance criteria
- artefakt pack `prview` + pasujący napisany spec
- delta report po rundzie marbles + brief marbles, który ją napędził
- dowolna para „powiedzieliśmy, że zrobimy X, oto diff, który deklaruje X"

---

## Anty-cele (eskaluj gdzie indziej)

- „Zaudytuj to repo ogólnie" → brak ograniczonego planu, użyj `vc-followup`
- „Zreviewuj ten PR" → brak napisanego specu, użyj `vc-review`
- „Napraw luki, które znajdziesz" → audit jest READ-ONLY, eskaluj do
  `vc-marbles` po wyemitowaniu verdictu
- „Która prawda wygrywa?" → użyj `vc-polarize`, nie auditu

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
