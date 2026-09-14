---
name: "{{SKILL_NAME}}"
version: 0.1.0
description: "Template for a new Vibecrafted skill; replace before shipping."
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie (szablon)** — podmień `{{LAUNCHER}}` na prawdziwy launcher z
> [Matrycy Delegacji](../DELEGATION_MATRIX.md). Nie wklejaj literałów `workflow`,
> jeśli ten skill nie jest `vc-workflow`.
>
> | Ścieżka        | Literał                                              |
> | -------------- | ---------------------------------------------------- |
> | 1. Worker      | `vibecrafted {{LAUNCHER}} <agent>`                   |
> | 2. Interactive | `/vc-{{LAUNCHER}}` — w sesji; native gdy trzeba      |
> | 3. Operator    | ten sam worker przez `vc-dispatch` / linie operatora |

<!-- /fleet-imperative -->

# {{SKILL_NAME}} — TODO one-line tagline

> Scaffolded {{CREATED_DATE}} via `tools/vc-skill-new.sh`.
> Replace every TODO marker before opening a PR.

---

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz
worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że
operator wprost poprosi o worktree w tym prompcie. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją,
dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli
bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

Standardowy launcher:

```bash
vibecrafted {{SKILL_NAME_NO_PREFIX}} claude --prompt 'TODO concrete operator example'
vc-{{SKILL_NAME_NO_PREFIX}} codex --prompt 'TODO shell-shortcut example'
```

---

## Cel

TODO — Zastąp tę sekcję. Określ **jeden** rezultat, który ten skill wytwarza.
Skille istnieją po to, by skompresować powtarzalny ruch operatora w nazwaną, powtarzalną
powierzchnię. Jeśli ta sekcja czyta się jak lista możliwości — zawęź ją.

Poprzeczka z `CONTRIBUTING-SKILLS.md`: jedna ostra oś, nie scyzoryk szwajcarski.

---

## Goal

**Propozycja — Founder zatwierdza ostateczne brzmienie.** {{SKILL_NAME}} wytwarza TODO konkretny rezultat i jest skończony, gdy TODO weryfikowalny punkt końcowy da się sprawdzić niezależnie od tego przebiegu. Agent proponuje ten jeden akapit zaraz po Celu; Founder potwierdza albo przepisuje go po ludzku, zanim skill stanie się kanoniczny. Ten akapit nie zastępuje Kryteriów akceptacji poniżej.

---

## Kiedy używać

Warunki wyzwalające (zastąp wszystkie punkty):

- TODO — podstawowa sytuacja operatora, w której ten skill to właściwy wybór
- TODO — wtórna sytuacja, jeśli istnieje
- TODO — jawne rozgraniczenie z istniejącymi skillami vc-\*

**Kiedy NIE używać:**

- TODO — sąsiedni skill obsługujący podobną, lecz odmienną sytuację
- TODO — sytuacja, którą należy eskalować zamiast tego do `vc-implement` lub `vc-marbles`

---

## Pozycja w pipelinie

Gdzie to się wpasowuje w łańcuch workflow Vetcoders?

- Upstream: TODO (np. następuje po `vc-init`, działa po `vc-research`)
- Downstream: TODO (np. emituje handoff dla `vc-release` lub `vc-dou`)

---

## Kryteria akceptacji

Przebieg skilla jest **gotowy**, gdy:

- [ ] TODO — konkretne, falsyfikowalne sprawdzenie #1
- [ ] TODO — konkretne, falsyfikowalne sprawdzenie #2
- [ ] TODO — deliverable widoczny dla operatora (plik, raport, commit)

Jeśli któregokolwiek punktu akceptacji nie da się odhaczyć dowodem, skill nie został
ukończony — powiedz to wprost w raporcie końcowym.

---

## Antywzorce

- TODO — typowy tryb porażki #1 (np. uruchomienie tego skilla przed `vc-init`)
- TODO — typowy tryb porażki #2 (np. rozszerzanie scope poza jedną ostrą oś)
- Wklejanie raili `workflow` / ERi, gdy ten skill nie jest `vc-workflow`
- Wymyślanie fałszywego workera `vibecrafted <name> <agent>` dla skilla fundamentu
- Pominięcie ponownego odczytu Living Tree przed edycją przy współbieżnych agentach
- Ogłaszanie „gotowe" bez odhaczenia kryteriów akceptacji

---

## Przykłady

Zobacz [`examples/example-prompt.md`](examples/example-prompt.md) — minimalna para
fraza-trigger + oczekiwane zachowanie.

---

## Zweryfikuj przed przekazaniem

Zanim ogłosisz „done", obejdź ciężarówkę — zobacz
[Regułę Weryfikacji](../VERIFICATION_RULE.md): realny artefakt, nie same zielone bramki;
nigdy nie ufaj upstream verification jako dowodowi; sprawdź własny instrument.

Progressive disclosure: trzymaj SKILL.md chudy; długie procedury w `references/`
([Matryca Delegacji](../DELEGATION_MATRIX.md), CONTRIBUTING-SKILLS).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
