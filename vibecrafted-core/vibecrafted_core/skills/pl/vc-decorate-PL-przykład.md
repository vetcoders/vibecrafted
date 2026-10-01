---
name: vc-decorate
version: 2.1.0
description: >
  Umiejętność późnego wykańczania wizualnego i spójności doświadczenia. Wykrywa
  istniejący język wizualny użytkownika, audytuje spójność systemu, oddziela
  tożsamość od dryfu, ulepsza słabe wzorce i proponuje gustowny szlif działający
  WEWNĄTRZ systemu użytkownika. Nigdy nie narzuca gustu agenta. Nigdy nie
  dekoruje chaosu. Najpierw spraw, by system był spójny. Potem spraw, by
  sprawiał wrażenie premium.
  Frazy wyzwalające: "decorate", "make it look good", "add polish", "smaczki",
  "micro-interactions", "udekoruj", "dopracuj wizualnie", "curb appeal",
  "premium pass", "finish the experience", "make it feel intentional",
  "coherence audit", "design system cleanup", "interactive demo", "animate",
  "add hover effects", "make it feel nice", "visual polish".
---

# vc-decorate — Najpierw spójność. Premium na drugim miejscu.

## Wejście dla operatora (człowieka)

### Zasada Żywego Drzewa / worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na
niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w
stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do
równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie
kontynuować.

Zobacz [Zasada Żywego Drzewa](../LIVING_TREE_RULE.md).

## Kanoniczna bramka orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, przegląd, release lub delegowanie,
MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brakuje świeżych dowodów z `vc-init`,
najpierw wykonaj przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki prawda o repo nie
zaistnieje.

`Loctree:loctree` to domyślna umiejętność percepcji strukturalnej dla tego przebiegu. Używaj Loctree przed grepem lub
twierdzeniami opartymi na dokumentacji, aby wygenerować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived
Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i
kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refactorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: nośnych hubów, bliźniaków, martwego kodu, dryfu, punktów wejścia runtime'u oraz pułapek o
dużym promieniu rażenia. Jeśli zadanie jawnie nie dotyczy repo lub nie dotyczy kodu, odnotuj w raporcie wyjątek „bez
repo". W przeciwnym razie brak dowodów z `vc-init`/Loctree to błąd procesu.

Standardowy launcher (`vibecrafted start` / `vc-start`, następnie `vc-<workflow> <agent> [--prompt|--file ...]`).

```bash
vibecrafted decorate agy --prompt 'Polish the landing page'
vc-decorate claude --prompt 'Coherence audit on the CLI output surface'
vibecrafted decorate codex --file /path/to/decorate-plan.md
```

> „Nie dekoruj chaosu. Najpierw spraw, by system był spójny. Dopiero potem spraw, by sprawiał wrażenie premium."

Decorate to **nie** jest umiejętność typu „zrób ładnie". To umiejętność **późnego wykańczania produktu**. Jej zadanie:
wziąć działający produkt i przekształcić go w **spójne, zamierzone, premium doświadczenie**.

Oznacza to:

- Wykrycie rzeczywistego języka wizualnego użytkownika (kolory, fonty, motyw, spacing, rytm interakcji)
- Oddzielenie tożsamości od dryfu
- Zachowanie tego, co wyróżniające
- Ulepszenie tego, co słabe, przestarzałe lub niespójne
- Weryfikację wrażenia end-to-end
- **Dopiero potem** dodanie gustownego szlifu wizualnego i mikrointerakcji

Decorate **nie** narzuca gustu agenta, nie nadpisuje marki użytkownika ani nie dodaje przypadkowego rozmycia, poświaty,
parallaksy czy „ładności rodem z AI". Jej zadaniem jest sprawić, by istniejący system sprawiał wrażenie: bardziej
przemyślanego, nowocześniejszego, stabilniejszego, precyzyjniejszego, pełniejszego.

**Premium to nie ornament. Premium to spójność.**

---

## Główna zasada: wykrywaj, nie narzucaj

Zanim cokolwiek udekorujesz, uruchom wykrywanie stylu i audyt systemu:

```text
1. SKANUJ istniejące zmienne CSS, pliki motywów, kolory marki, fonty, spacing, komponenty
2. ZIDENTYFIKUJ paletę, stos fontów, tryb motywu, logikę powierzchni, rytm interakcji
3. ZAUDYTUJ pod kątem dryfu wizualnego, słabych wzorców, niespójnych stanów, pozostałości prototypu
4. ODDZIEL tożsamość od dryfu:
   - zachowaj to, co wyróżniające
   - popraw to, co słabe, przestarzałe lub niespójne
   - usuń rozproszenie stylu, konkurujące prawdy
   - wyeliminuj obszary konfliktu przez twarde przycięcie duplikatów lub wyścigów
5. ZAPROPONUJ ulepszenia używając ICH tokenów, ICH języka, ICH stosu
6. ZAPYTAJ, które zmiany wdrożyć
7. WDRÓŻ tylko zatwierdzone zmiany
8. ZWERYFIKUJ doświadczenie end-to-end
```

Jeśli nie wykryto istniejącego stylu, zaproponuj zbudowanie (scaffold) odpowiedniego systemu projektowego — przedstaw
opcje, nie zakładaj gustu, nie narzucaj tożsamości wizualnej.

---

## CLI to też interfejs

Terminal to nie wysypisko. Wyjście CLI to UI. Zasługuje na tę samą spójność, rytm i celowość co strona internetowa.
Paskudne, surowe, niesformatowane wyjście terminala nie jest „przyjazne dla developera" — jest obraźliwe dla operatora.

Decorate stosuje się również do powierzchni CLI. Jeśli produkt ma interfejs terminalowy, ten interfejs jest częścią
powierzchni produktu. Udekoruj go. Oczywiste obszary to:

- wyjście instalatora (wyrównanie, kolory, sygnały postępu)
- główny runtime (z brandingiem, zwięzły, informatywny)
- po wykonaniu (spinnery, paski postępu, podsumowania per krok)
- wyjścia help/--help (uporządkowane sekcje, kolory, kolumny)
- doctor/health checks (czytelne podsumowania pass/fail, czytelne minimalne logi)
- komunikaty błędów (rozdzielenie wersji dla maszyny i dla człowieka)

Jeśli dostępny jest `screenscribe`, vc-decorate może skonsumować narrację z nagrania ekranu UI (screencast), aby wykryć
dryf, niezgrabne przejścia i przerwy w spójności w obrębie rzeczywistego flow — przydatne, gdy statyczne zrzuty ekranu
są zbyt ubogie.

### Zestaw narzędzi Unicode dla CLI

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. dostarcza bazę danych Unicode (2601 znaków, 13 kategorii) oraz serwer `unicode-puzzles-mcp`. Używaj ich do
dekoracji CLI zamiast zgadywać code pointy czy hardkodować sekwencje ucieczki ANSI.

**Elementy dekoracji CLI** (baza Unicode):

| Potrzeba    | Znaki                | Źródło                   |
| ----------- | -------------------- | ------------------------ |
| Ramki       | `╭─╮│╰─╯`            | Box Drawing              |
| Separatory  | `·` `─` `━` `┄`      | Box Drawing, Punctuation |
| Znaczniki   | `✓` `✗` `⚠`          | Dingbats                 |
| Punktory    | `▸` `▪` `◆` `›`      | Geometric Shapes         |
| Postęp      | `⣿⣶⣤⣀` `█▓▒░`        | Braille, Block Elements  |
| Sparkline'y | `⣀⣤⣶⣿` (8px/komórkę) | Braille (256 kombinacji) |
| Strzałki    | `→` `←` `↑` `↓` `⟶`  | Arrows                   |
| Status      | `⚒` `⚙` `⟳`          | Misc Symbols             |
| Marki       | `🄵·🅁·🄰·🄼·🄴·🅆·🄾·🅁·🄺`  | Enclosed Alphanumerics   |

**Sparkline'y Braille'a** zasługują na uwagę. Pojedynczy znak Braille'a koduje 8 kropek w siatce 2×4 (256 kombinacji) —
40 znaków = krzywa zbieżności o 320 punktach w terminalu, bez biblioteki graficznej. Używaj do: zużycia tokenów w
czasie · znalezisk P0/P1/P2 w pętlach marbles · osi czasu aktywności agentów · dowolnych danych trendowych.

**Zasady:**

- Zero kodów ucieczki ANSI do stylowania tekstu — czysty unicode renderuje się wszędzie.
- Nigdy nie mieszaj bloków Unicode w obrębie jednej etykiety (kwadratowe F obok negatywowego kwadratowego R wygląda jak
  bug, nie jak wybór — chyba że to celowy znak firmowy w stylu `🅵·🅁·🄰·🄼·🄴·🅆·🄾·🅁·🄺`).
- Testuj renderowanie na co najmniej dwóch terminalach (macOS Terminal + domyślny terminal Linuksa).
- Użyj `search_unicode`, aby znaleźć konkretny symbol — nie zgaduj.

---

## Kiedy używać

- Produkt działa, ale sprawia wrażenie płaskiego, prototypowego, niedokończonego
- Użytkownik prosi o szlif wizualny, smaczki, „curb appeal", wrażenie premium
- UI jest funkcjonalnie poprawne, ale brakuje mu spójności między powierzchniami
- Dobre składniki, słabe wrażenie systemowe
- Niespójne karty, przyciski, spacing, stany focus, czasy animacji
- Strona pokazowa, demo, landing page lub aplikacja potrzebuje przebiegu wykańczającego
- Zespół chce, by produkt sprawiał wrażenie zamierzonego, nie tylko udekorowanego
- **Wyjście CLI jest funkcjonalne, ale brzydkie, bez brandingu, trudne do skanowania wzrokiem**

---

## Pozycja w pipelinie

```text
scaffold → init → workflow → followup → marbles → dou → [DECORATE] → hydrate → release
```

Decorate znajduje się po `dou`, zapewniając, że już kompletna powierzchnia produktu jest spójna wizualnie przed finalnym
pakowaniem (`hydrate`) i wysyłką (`release`).

---

## Tożsamość vs Dryf

Jedno z najważniejszych zadań decorate:

- **Tożsamość** — rzeczywisty język wizualny użytkownika: wybrana paleta, typografia, rytm spacingu, formy komponentów,
  styl interakcji.
- **Dryf** — rzeczy, które po prostu się nazbierały: niespójne promienie zaokrągleń, niedopasowany spacing, sprzeczne
  style przycisków, przypadkowe zachowania hover, artefakty prototypu.

Zachowuj tożsamość. Redukuj dryf.

---

## Wzorzec implementacji

```text
1. Wykryj    — skanuj tokeny, arkusze stylów, konfigurację frameworka, wzorce komponentów
2. Audytuj   — zidentyfikuj tożsamość vs dryf oraz słabe wzorce
3. Zaproponuj — przedstaw poprawki spójności, ulepszenia premium, smaczki
4. Wdróż     — zastosuj zatwierdzone zmiany, używając tokenów i struktury użytkownika
5. Zweryfikuj — przejrzyj „przed/po" pod kątem integralności doświadczenia
```

---

## Antywzorce

- Dekorowanie zepsutej struktury
- Utrzymywanie złych wzorców, bo „użytkownik już je miał"
- Zastępowanie ich stylu naszym
- Dodawanie ruchu bez celu interakcyjnego
- Dodawanie rozmycia/poświaty, bo „premium"

---

_Faza 3 — Ship (dou → decorate → hydrate → release)_

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
