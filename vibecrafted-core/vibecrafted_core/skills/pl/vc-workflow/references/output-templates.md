# vc-workflow — szablony wyjścia

### Wyjście: CONTEXT.md

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

# Rozpoznanie: <slug>

## Zdrowie repo

- <3–5 punktów z repo-view>

## Zakres

- Katalogi docelowe: <lista>
- Dlaczego: <uzasadnienie>

## Pliki krytyczne

| Plik | Konsumenci | Ryzyko | Uwagi |

## Znalezione symbole

- <istniejące symbole istotne dla zadania>

## Mapa ryzyka

- <pliki o dużym zasięgu zmiany i ograniczenie ryzyka>

## Decyzja

- [ ] Potrzebny research (nieznane API/wzorce)
- [ ] Przejdź do implementacji (dobrze rozpoznana domena)
```

### Wyjście: RESEARCH.md

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

## Pytania (z rozpoznania)

1. <pytanie>

## Ustalenia

### P1: <pytanie>

- **Źródło**: <URL lub biblioteka Context7>
- **Odpowiedź**: <zwięzła>
- **Przykład kodu**: <jeśli dotyczy>

## Decyzja architektoniczna

- Wybrano: <decyzja>
- Dlaczego: <na podstawie ustaleń>
- Odrzucone alternatywy: <powody>

## Wskazówki implementacyjne

- <konkretne wskazówki dla agentów>
```
