---
status: accepted
kind: design-doc
layout_id: Layout-0
title: Prawo siatki i dwóch drzwi
owner: grok
authored_by: grok <agents@vetcoders.io>
session_id: 01a00bfd-5efc-7bf0-883f-a5d096f5235d
date: 2026-08-17
grid: 120x36
source_cheat: 120x36-operator-vertical
studio_type: 12px/1.28
scope: agents-workshop
product: vibecrafted
next: layout-factory
---

# Agents workshop — prawo, korekty, Layout-1..n

Te pliki są po to, żeby **człowiek** zobaczył warsztat zanim ktokolwiek ruszy plugin.
Nie są kalką bałaganu z żywej ściągi. Są spakowanym celem na siatce ze ściągi.

## Frontmatter — jak mnie znaleźć

Każdy Layout ma:

```
owner: grok
authored_by: grok <agents@vetcoders.io>
session_id: 01a00bfd-5efc-7bf0-883f-a5d096f5235d
date: 2026-08-17
grid: 120x36
```

Szukaj `session_id: 01a00bfd-5efc-7bf0-883f-a5d096f5235d` albo `authored_by: grok`.

## Siatka

Komórka TUI to nie piksel. Canva projektowa bierze **proporcje okna operatora w VERTICAL**, nie pełnego muxa 182.

|           | pełny mux | Twoja ściąga VERTICAL           | makieta |
| --------- | --------- | ------------------------------- | ------- |
| szerokość | ~182      | ~126                            | **120** |
| wysokość  | ~40       | ~30 w zrzucie, za płasko na 120 | **36**  |

```
wiersz   1     compact-bar
wiersze  2–35  ciało   (rail 20 + canvas 100)
wiersz  36     status-bar
```

**LOCKED 2026-08-17 (operator: JEAH — teraz idealnie).**
Studio: `12px / 1.28`. Nie wracamy do 1.7 ani do 130 wierszy.

120 zostaje. 36 daje powietrze jak pusta dziura shella na ściądze.
Pasek talii: `[‹2/4›] [Voc] [Nowy]`. Karta Nowy agent: 6 wierszy.
Nie kopiujemy Tab #6 / Tab #7 — tylko proporcje canvy.

## Korekty

Grok nie powinien był rysować 130 wierszy. Miał poprawić: „to piksele, nie komórki”.

**Korekty 2026-08-19 (operator, na żywym railu):**

1. Nagłówek railu i tytuły mówią projektem — `vibecrafted · Agents (3)` —
   nigdy surową nazwą sesji (`w-cdfc-r035…`). Prawdziwą nazwę zna
   control_plane, vc-server i vc-frame; UI pokazuje projekcję dla człowieka.
   (Runtime: cut `b0c6c624` wprowadził miejsca i twarze; rail Layoutów
   modeluje ten stan.)
2. OTWARTE: oś „rytuał" karty to launchery vc-skill „jeśli w ogóle" —
   operator zaczął korektę, nie domknął. Chipy `[init] [resume] [operator]
[partner]` stoją do jego decyzji; fabryka nie utrwala ich bez tego.

Poza tym z recenzji użytkownika zostaje:

1. Karta nie przecina słów w tle.
2. Ścieżka w całości.
3. Na pasku NOW nie ma `[New dispatch]`.
4. W celu tab nazywa się `voc`, nie `Tab #6`.
5. Chrome jak żywy (SESSIONS/LOCK), karta po polsku.

Wektor z naszych klatek:

float+PANE+strzałki → talia hosta → brakowało `[New agent]` → rytuał 3 osi, nie KDL → headless to inne drzwi.

## Dwa launchery

| drzwi   | przycisk                                | narodziny                                      |
| ------- | --------------------------------------- | ---------------------------------------------- |
| teraz   | `[New agent]`                           | interaktywny TTY na **tym** tabie              |
| później | `[New dispatch]`                        | worker, najlepiej headless, tylko przez serwer |
| zawsze  | z wewnątrz żywego interaktywnego agenta | dispatch jak dziś                              |
| zawsze  | Quick cmd                               | osobny widok, nie atrapa paska                 |

## Nawigacja karty

| klawisz        | skutek                          |
| -------------- | ------------------------------- |
| `↑` `↓`        | wiersz agent → rytuał → ścieżka |
| `←` `→`        | chip (w ścieżce: kursor)        |
| `spacja`       | zaznacz chip                    |
| `enter`        | launch                          |
| `esc` / Anuluj | nic się nie rodzi               |

## Layout factory (za tydzień)

CUT-1..n były briefami do cięcia kodu.
**Layout-1..n** są rysunkami do cięcia powierzchni.

Nie generujemy jeszcze KDL. Te pliki są ziarnem fabryki.

## Pliki

| id       | plik          | co widzisz               |
| -------- | ------------- | ------------------------ |
| Layout-0 | ten dokument  | prawo                    |
| Layout-1 | `Layout-1.md` | warsztat, jedna twarz    |
| Layout-2 | `Layout-2.md` | Nowy agent (karta)       |
| Layout-3 | `Layout-3.md` | po Enter                 |
| Layout-4 | `Layout-4.md` | voc jako drzwi tego taba |
| Layout-5 | `Layout-5.md` | Nowy dispatch, później   |

HTML studio: `preview.html` (lewe taby Layout-1..n, jeden canvas). Analog `/scaffold/editor`.
