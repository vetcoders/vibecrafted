---
name: vc-scaffold
version: 0.2.0
description: >
  Founder-first main brainstorm + planwriting — the armored lighthouse (pancerna
  latarnia) that carries a single cut, multiple cuts, or a whole project into the
  autonomous VC-ship pipeline. The WRITE entry of the read/write cadence: produces a
  measurable, self-sufficient plan a fleet executes with the operator absent mid-flight.
  This skill should be used when the user asks to "scaffold", "plan this", "architect
  this", "break this down", "I have an idea", "design the system", "vc-scaffold",
  "zaplanuj to", "rozrysuj architekturę", "mam pomysł".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-scaffold` (launcher `scaffold`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                             |
> | --------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted scaffold <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-scaffold` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                      |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-scaffold: Planowanie founder-first — Pancerna Latarnia

## Czym to jest

Scaffold to główna powierzchnia **brainstormu + planwritingu**: bierze mglisty pomysł i produkuje
zawężony, **mierzalny** plan budowy. Skaluje się przez jedną bramkę: **pojedyncze cięcie**, **wiele
cięć** albo **cały projekt**. To **wejście WRITE w cadence read/write VC-ship** —
plan, który emituje, musi być **samowystarczalny i falsyfikowalny**, bo w autonomicznym dostarczaniu
operator jest nieobecny w locie i widzi tylko artefakty pośrednie. Planuj tak, jakby nikt nie miał
odpowiedzieć na pytanie po dispatchu. Front-loaduj każdą decyzję tutaj. Zobacz `references/cadence.md`.

Latarnia orientuje, zanim flota wypłynie; pancerz to weryfikacja, którą niesie każde cięcie.

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się
na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi. Ogólne słowa w stylu
„isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do
równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli drzewo jest zbyt zatrute, by bezpiecznie kontynuować.
Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

### Dispatch

Wejdź w sesję frameworka, a potem odpalaj przez command deck (nie surowe `skills/.../*.sh`):

```bash
vibecrafted start            # or: vc-start
vibecrafted scaffold claude --prompt 'Design the payment system'
vc-scaffold agy --prompt 'Plan migration from NextAuth to custom auth'
vibecrafted scaffold codex --file /path/to/idea-brief.md
```

Preferuj `--file` dla istniejącego planu/artefaktu i `--prompt` dla intencji inline.

## Checkpoint orientacji (HARD-BLOCK — krytyczna dla bezpieczeństwa)

Przed jakąkolwiek analizą lub planowaniem specyficznym dla repo uruchom lub skonsumuj `vc-init` dla przypisanego repo.
**To nie jest krok poprawkowy — to bezpiecznik bezpieczeństwa (safety bezpiecznik).** W autonomicznym VC-ship agent, który
komponuje z pamięci, wstrzykuje cichy dryf, którego operator nie wyłapie na żywo. Dlatego: **żadnego planu,
dopóki nie istnieje prawda o repo/runtime.** Brakujące evidence z `vc-init`/Loctree to porażka procesu, nie
ostrzeżenie.

`Loctree:loctree` to domyślna percepcja strukturalna. Użyj jej przed grepem lub twierdzeniami opartymi na dokumentacji,
aby wyprodukować lub odświeżyć **Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map)**:
`repo-view`, `focus`, `slice` (przed edycją), `impact` (przed usunięciem), `find` / `find --literal`
(przed utworzeniem), `follow` (dead/cycles/twins/hotspots). Znajdź nośne węzły, twins (duplikaty), martwy kod,
dryf, entrypointy runtime'u, pułapki o dużym zasięgu zmiany. Jeśli task jest jawnie non-repo/greenfield,
zadeklaruj **wyjątek no-repo** w raporcie i nazwij użyte zamiast tego źródło orientacji.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Bramka wywiadu z founderem (HARD-BLOCK)

Scaffold jest founder-first, więc nie może wymyślać intencji foundera. Przed Shape zapisz we
frontmatterze planu i sekcji Orient jeden z dwóch rodzajów dowodu:

1. konkretne istniejące źródło wywiadu (journal operatora, sesja/ekstrakt AICX albo brief foundera),
   wraz ze ścieżką/session ID i decyzjami naprawdę z niego odzyskanymi; albo
2. odpowiedzi foundera zebrane w bieżącej rozmowie.

Jeśli nie istnieje żaden z nich, zadaj founderowi brakujące pytania produktowe przed napisaniem
planu architektury. „Wywiad nie był potrzebny", „task był jasny" i „niezapytanie nie zaszkodziło"
to zakazane samowyłączenia. Ta bramka jest intake'em discovery; nie legalizuje dwudziestu pytań
w środku scaffoldu, gdy decyzje są już uchwycone.

## Pozycja w pipelinie

```
[SCAFFOLD] → init → implement → review → workflow → followup → marbles → audit → polarize → dou → hydrate → release
^^^^^^^^^^   WRITE entry of the read/write cadence (WRITE produces an artifact, READ falsifies it)
```

Scaffold to wejście WRITE. Jeśli task jest już jasny i bounded, pomiń scaffold i zacznij od
`vc-init`. Pełen cadence oraz klasyfikacja WRITE/READ żyją w `references/cadence.md`.

## Sześć faz

Uruchamiaj je po kolei. Każda faza produkuje wejście, które konsumuje następna. Fazy 5–6 to
**mechanizm dostarczania**: każde cięcie dostaje brief (hard-gate), a artefakty są serwowane do
przeglądu przez operatora — nie narracja prozą i nie bramkowane na dobrych intencjach agenta.

### 1. Orient (research-first)

Przejdź Checkpoint orientacji powyżej. Zmapuj istniejący krajobraz: `repo-view` dla rozmiaru/zdrowia,
`focus` na podejrzanych modułach, `slice` na krytycznych plikach, `tree` dla hotspotów, `follow` dla dead/cycles.
Uchwyć **przestrzeń ograniczeń** — tech (stack/wersje/infra), zespół (kto buduje, w jakich językach),
biznes (budżet czasu, deadline), scope (MVP vs pełna wizja). Ograniczenia kształtują wszystko.

### 2. Falsify (adwersarialne sprawdzenie przesłanki)

Zanim zwiążesz się z kształtem, spróbuj **złamać** założenie fundujące. Spytaj „skąd bym wiedział, że to
kłamstwo?". Lekcja z 0-bajtów-przechodzi-exit-0: każde „to działa" musi przetrwać realną sondę, nigdy
samego zielonego ptaszka. Wyciągnij na wierzch tryby porażki, przed którymi plan musi się bronić.

### 3. Shape (skalo-adaptacyjny)

Zdecyduj o architekturze przez **granice i decyzje** (3-5 tych, które się liczą, nie tysiąc szczegółów), ustaw
**scope** (in / out / explicitly out — bądź bezwzględny) i zdefiniuj **tożsamość produktu** (metafora
materiałowa, role kolorów, typografia, ton, dark/light) — tożsamość to decyzja architektoniczna, która
karmi później DoU i Decorate. Potem wybierz **kształt wyjścia wg skali**: brief pojedynczego cięcia · wave-atlas+tracker · pipeline read/write projektu. Zobacz `references/output-shapes.md`.

### 4. Defend (bramki pierwszej klasy)

Rozbij pracę na cięcia rozmiaru agenta (30-120 min). **Każde cięcie niesie measure-core**: `Vector`
(stabilize/implement/recon/e2e), czteroczłonową deltę (`intent | baseline | claim | delivery`), marker
`state` `[ ] [~] [?] [!] [x]` oraz **delivery-verifier** — niefałszowalny test, który przerzuca
`[~]→[x]`. Cięcie bez verifiera dowozi się jako `[?]`, nigdy `[x]`. Zobacz `references/measure-core.md`.

### 5. Brief na każde cięcie (HARD-GATE — to jest mechanizm dostarczania)

Wyprodukuj plan z `references/plan-template.md` (master-dispatch: wave atlas + graf zależności

- kolumna `state`). **Potem — bez negocjacji — wyrenderuj brief dla KAŻDEGO cięcia.** Cięcie
  bez wyrenderowanego, dobrze sformułowanego briefu nie istnieje z punktu widzenia planu. To jest
  zasada, która zamienia plan z wydmuszki w coś, co flota może wykonać.

Dla każdego cięcia napisz `briefs/<wave>-<slot>_<slug>.md` z 12-sekcyjnego szablonu dispatchu
(`references/output-shapes.md`): mission · context · files · acceptance · gates · out-of-scope ·
etykieta Living Tree (verbatim) · Loctree-first · podpowiedź recovery · branch+commit · ścieżka raportu.

**Egzekwowanie (przeniesione z `/brainstorming`, flow, który prowadzi agenta za rękę):**

- **Checklist→TODOs:** utwórz jeden element TodoWrite na każdy brief cięcia; domykaj je po kolei. Scaffold
  nie jest „gotowy", dopóki jakikolwiek todo briefu cięcia jest otwarty.
- **Hard-gate:** NIE przekazuj do `vc-operator`, nie dispatchuj ani nie ogłaszaj scaffoldu za ukończony, dopóki
  KAŻDE cięcie w wave atlasie nie ma pasującego briefu z wszystkimi 12 sekcjami obecnymi.
- **Loop do zielonego:** brakujący lub źle sformułowany brief → wróć i wyrenderuj go. Jeden stan terminalny:
  wszystkie briefy wyrenderowane ORAZ bramka scaffold-doctor przechodzi.
- **Wyprzedzanie antywzorca (ZAKAZANE racjonalizacje):** „to cięcie jest za małe, żeby potrzebowało
  briefu", „jesteśmy 1:1, więc briefy zbędne", „tabela master-dispatch wystarczy". Plan bez
  briefów per cięcie to wydmuszka, nie plan. Bez wyjątków, niezależnie od postrzeganej prostoty.

### 5.5 DRIVER.md (HARD-GATE — driver przekazania operatora)

Obok briefów wyrenderuj **jeden `DRIVER.md`** współlokowany z `briefs/`. To jeden
samowystarczalny artefakt, z którego ludzki operator (albo zimna flota) prowadzi cały plan, **gdy
pętla w wątku umiera**. NIE opcjonalny, NIE re-skin atlasu — to wykonywalne przekazanie.
MUSI zawierać wszystkie pięć:

1. **Pełne ścieżki absolutne** — każdy artefakt planu, brief, evidence z orient oraz input/fixture, jako
   gotowe-do-wklejenia ścieżki absolutne.
2. **Graf zależności Z `why` na każdej krawędzi** — co-po-czym ORAZ dlaczego: dlaczego każde cięcie poprzedza
   następne; dlaczego para jest **SEQUENCE** (współdzielona domena plików → konflikt Living Tree) vs **PARALLEL**
   (rozłączne domeny → bezpieczne współbieżnie); i gdzie siedzi każdy **⛔ operator-button STOP** (push/merge,
   decyzje produktowe). Graf bez `why` to diagram, nie driver.
3. **Gotowe przekazanie** — dokładnie jeden blok walidacji poziomu planu dla kanonicznego
   `<plan-id>.dispatch.toml` (doctor + dry-run), a potem przekazanie A→Z do `/vc-ship`. `/vc-ship`
   jest właścicielem startu i resume; DAG jest właścicielem kolejności cięć i dozwolonej
   równoległości. Nie zamieniaj DRIVER-a w listę launcherów per cięcie i nie ucz ręcznego
   sekwencjonowania `vibecrafted workflow ... --prompt`.
4. **Alfabet stanów + reguła `[ ]→[x]`, odtworzone verbatim** (lustro Pomiaru):
   `[ ]` todo · `[~]` running · `[?]` done-unverified · `[!]` blocked · `[x]` verifier-green.
   **Tylko delivery-verifier przerzuca `[~]→[x]`; twierdzenie agenta NIGDY samo nie dochodzi do `[x]`.**
   Reguła żyje W DRIVER-ze celowo — po to, żeby w trakcie dispatchu nikt nie promował twierdzenia do done
   bez ponownego uruchomienia verifiera. Ta promocja-bez-dowodu to jedyny tryb porażki, który
   wykłada przebieg operatora („się zajebiemy"). Zakoduj to tam, gdzie są oczy dispatchera.
5. **Snapshot statusu na żywo** + `dou-index = |[x]| / total`.

### 5.6 manifest.json (HARD-GATE — kanoniczny inwentarz artefaktów)

Utwórz jeden root planu pod
`~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<plan_id>/` i zapisz w nim obowiązkowy
`manifest.json`. Schema version `"1"` deklaruje `plan_id`, `org`, `repo`, `day` i uporządkowaną
tablicę `artifacts`. Każdy wpis artefaktu deklaruje stabilne `id`, jawną `role`, względną `path`,
`editable` i `required`; opcjonalne `dependencies` zawierają ID artefaktów. Obsługiwane role:
`driver`, `wave-atlas`, `dispatch`, `brief`, `design-doc`, `traceability`, `tracker`, `falsification`, `report`,
`other`. Zarejestruj każdy wygenerowany artefakt przed przekazaniem. Nazwy plików nigdy nie wyznaczają
roli. Nie twórz lustra `operator/`, kopii kompatybilności, aliasu nazwy ani symlinka.

### 5.7 Frontmatter YAML na KAŻDYM artefakcie (HARD-GATE — zero gołego markdownu)

Każdy markdownowy artefakt produkowany przez ten skill — MISSION, ATLAS, DRIVER, tracker,
falsyfikacja, każdy brief i każdy design doc — zaczyna się frontmatterem YAML, bez wyjątków:

```yaml
---
plan_id: <plan_id>
run_id: <run scaffoldu, gdy działa pod lifecycle>
session_id: <session_id agenta z aicx albo nazwy surowego .jsonl>
role: driver | wave-atlas | brief | tracker | falsification | design-doc | mission | other
agent: <agent autor>
date: YYYY-MM-DD
project: <org>/<repo>
---
```

To bramka proweniencji, retrievalu i settlementu, nie dekoracja. Pakiet mieszający artefakty z
frontmatterem i bez niego odpada tak samo jak pakiet z brakującym briefem.

### 5.8 `<plan-id>.dispatch.toml` (HARD-GATE — kontrakt wykonania czytelny dla supervisora)

Każdy scaffold, także jednocięciowy, kończy się `<plan-id>.dispatch.toml` ze schematem
`vibecrafted.dispatch.v1`. Koduje pełny zbiór cięć, nazwane fazy, krawędzie `depends_on`, jawnie
dozwoloną równoległość, tożsamość agenta i workflow, ścieżki briefów per cięcie, politykę commitów
oraz delivery-verifiery. Zarejestruj go w `manifest.json` z rolą `dispatch`. ID cięć i ścieżki
briefów MUSZĄ pokrywać każde wykonywalne cięcie atlasu dokładnie raz; drugi scheduler jest zakazany.
Udowodnij artefakt przed przekazaniem:

```bash
vibecrafted dispatch <absolutny-root-planu>/<plan-id>.dispatch.toml --doctor
vibecrafted dispatch <absolutny-root-planu>/<plan-id>.dispatch.toml --dry-run --json
```

Po walidacji przekaż dokładnie ten artefakt do `/vc-ship`. `/vc-ship` jest jedyną normalną ścieżką
A→Z do startu, nadzoru, recovery i ukończenia wielocięciowego scaffoldu; bezpośrednie komendy
start/resume dispatchera są wnętrzem supervisora, nie instrukcją operatorską scaffoldu.

Nie ucz operatora ręcznego pisania TOML ani wklejania po jednej komendzie
`vibecrafted workflow <agent> --prompt ...` na task. Ręczne launchery per cięcie nie należą do
**Running This Plan** ani normalnej ścieżki DRIVER-a. Ograniczona notatka awaryjna może użyć
bezpośredniego dispatchera albo launchu per cięcie wyłącznie po nazwaniu awarii `/vc-ship`/supervisora
i powodu niedostępności normalnej drogi; musi zapisać dowód powrotu kontroli i nie może tworzyć
drugiego systemu wykonania.

### Plany z compile embargo

Jeśli plan odracza bramki compile/test podczas kształtowania architektury, stosuj fazowo-świadomy
kontrakt recovery z `references/compile-embargo.md`. Gdy Founder autoryzuje fazę embarga,
`--no-verify` jest w pełni autoryzowany dla każdego lokalnego checkpointu workera w tej fazie.
Obejmuje całe wejście bundlowanych hooków Gita, więc worker raportuje, co uruchomiono, a co
pominięto; nie jest to claim dostarczenia zweryfikowanego przez bezpieczeństwo ani compile/test.
Worker nie pushuje. Wyłącznie wyznaczony integrator może zrobić lokalny structural admission po
weryfikacji dokładnego commita/zakresu, Semgrep oraz przeglądzie sekretów/bezpieczeństwa, aby kolejne
fale budowały na złączonej architekturze, podczas gdy compile, lint, type-check i testy pozostają
odroczone. Dopiero nazwana closure oraz pełne, odpowiednie dla języka bramki tworzą verified delivery.

### 6. Serwuj i przeglądaj (edytowalne artefakty przez vibecrafted-server)

Plan + briefy to **edytowalne artefakty**, nie ściana pytań inline. Flow jest taki:
research → przedstaw findings + estymatę wysiłku → zaproponuj pierwszy kształt cięcia/fali → wyrenderuj
briefy → **zaserwuj je do przeglądu operatora przez `vibecrafted-server`** (naturalny dom tooling-u tej
fazy: czyta typowany kontrakt control-plane i renderuje wave atlas + briefy jako
**wielozakładkową, edytowalną** powierzchnię HTML — jedna zakładka na artefakt (atlas · każdy brief · każdy design doc),
edytowane w miejscu). Operator steruje przez edycję wyrenderowanego planu w przeglądarce, nie przez odpowiadanie na
dwadzieścia pytań w trakcie scaffoldu. Dopracowujesz Z operatorem na zaserwowanych artefaktach.

**Przeszczep powierzchnię — nie wymyślaj jej od nowa.** Sprawdzone źródła do zerżnięcia: `../pensieve`
(wielozakładkowy edytowalny dashboard workspace), `../unicode-puzzles-portal` (generatory portali) oraz
visual-companion z `/brainstorming` (sprawdzone generatory mockupów/diagramów HTML). Zakładka server-review
musi być wielozakładkowa + edytowalna od pierwszego dnia, nie statyczny zrzut.

**scaffold-doctor (bramka, sprawdzana maszynowo):** deterministyczny walidator w
`vibecrafted-server/control-core`, który ładuje ten sam typowany `manifest.json` co server i odmawia
przekazania batonu scaffold→implement, dopóki: tożsamość manifestu nie zgadza się z kanonicznym rootem
planu; wszystkie wymagane artefakty nie istnieją; ID i ścieżki nie są unikalne; zależności się nie
rozwiązują; edytowalne ścieżki są symlinkami lub wychodzą poza root; briefy na dysku nie są zadeklarowane;
oraz master-dispatch
nie ma wave atlasu + grafu zależności; każde cięcie nie ma `briefs/<wave>-<slot>_<slug>.md` z wszystkimi 12
sekcjami; bullety acceptance nie są atomowe + poparte verifierem; nie istnieje design doc dla każdego cięcia oznaczonego
`needs_design`; **nie istnieje `DRIVER.md` niosący wszystkie pięć (pełne ścieżki · graf z adnotacją why ·
gotowe komendy · reguła `[ ]→[x]` verbatim · snapshot statusu)**. Bramka jest **sprawdzana maszynowo, nie
obiecywana przez agenta** — to ta sama bramka artefakt-jako-prawda, której async runtime używa między każdym
przekazaniem cadence read-write.

## Pomiar (pancerz)

Każda jednostka planu jest adresowalna przez twierdzenie/wynik. **Tylko verifier przerzuca `[~]→[x]`; twierdzenie nigdy
samo nie dochodzi do `[x]`** — ten inwariant czyni plan mierzalnym zamiast optymistycznym.
`dou-index = |[x]| / total`; `delta = {[ ],[~],[?],[!]}`; trigger/stop czyta kolumnę `state`
(`[!]`/`[?]` → STOP → recovery-vector; pełna fala `[x]` → TRIGGER następną). **STOP to nigdy nie kapitulacja —
to wyzwala recovery-vector** (fallback/failover/handsoff). Pełen alfabet + markery:
`references/measure-core.md`.

## Reguły krytyczne

- **Research-first to hard-block, nie poprawka.** Żadnego planu z pamięci; wyprowadź z prawdy repo/runtime.
- **Wywiad z founderem albo dowód — bez samowyłączenia.** Wskaż journal/AICX/brief z decyzjami
  foundera albo zapytaj przed Shape.
- **Brief na każde cięcie — bez wyjątków.** Briefy per cięcie to hard-gate (Faza 5). Plan,
  którego cięcia nie mają briefów, to wydmuszka; scaffold-doctor odmawia przekazania.
- **DRIVER.md — bez wyjątków (Faza 5.5).** Driver przekazania operatora (pełne ścieżki · graf z adnotacją
  why · gotowe komendy · reguła `[ ]→[x]` verbatim · snapshot statusu) jest częścią bramki scaffold-doctor.
  Plan, którego człowiek nie poprowadzi z jednego pliku, gdy pętla umrze, nie jest gotowy do przekazania.
- **Trwałe artefakty NIGDY nie idą do `/tmp`.** `/tmp` to tylko ulotny scratch — jest wymazywany, nieśledzony
  i niewidoczny dla tooling-u i synca operatora. Każdy plan, brief, DRIVER, tracker, journal, raport
  i design doc ląduje w **kanonicznym root planu**:
  `~/.vibecrafted/artifacts/<org>/<repo>/<DATE>/plans/<plan_id>/`
  (lustro layoutu raportów). Zapis trwałego artefaktu do `/tmp` to porażka procesu, nie skrót.
- **manifest.json jest obowiązkowy.** To jedyny inwentarz artefaktów i kontrakt ról. Żadne lustro
  `operator/`, duplikat, inferencja roli z nazwy ani symlink kompatybilności nie może stać się drugą
  zapisywalną prawdą.
- **Artefakt `.dispatch.toml` jest obowiązkowy.** Zwaliduj `vibecrafted.dispatch.v1` przez dispatcher doctor i
  przekaż wielocięciowe wykonanie do `/vc-ship`; ręczne workflow per task to tylko awaryjne recovery.
- **Serwuj, nie przesłuchuj.** Renderuj edytowalne artefakty i przeglądaj je przez `vibecrafted-server`;
  operator edytuje plan, a nie odpowiada na dwadzieścia pytań w trakcie scaffoldu.
- **Mierz, nie twierdź.** Cięcie jest gotowe, gdy jego verifier jest zielony, nigdy gdy agent tak mówi.
- **Mapuj przed projektowaniem.** Szanuj ziarno istniejącego systemu; loctree przed założeniami.
- **Scope to twój najlepszy przyjaciel.** Ciasny scope + świetne wykonanie bije luźny scope za każdym razem.
- **Pisz dla nieobecnego operatora.** Artefakt mówi sam za siebie; następny READ go falsyfikuje
  bez człowieka po drugiej stronie.
- **Trzymaj zależności płytkie.** Preferuj niezależne strumienie pracy; sekwencyjne A→B→C zabija równoległość.
- **Żadnej przedwczesnej optymalizacji / żadnych wymyślonych wzorców.** Najlepsza architektura to ta, która dowozi.

## Jak wygląda sukces

- Zimna flota (albo człowiek) wykonuje plan **bez zadawania pytania** w locie.
- Każde cięcie ma `Vector` i `delivery-verifier`; kolumna `state` jest czytelna maszynowo.
- Granice scope'u są krystalicznie jasne; 3-5 decyzji architektonicznych jawnych z trade-offami.
- Plan przetrwa nieobecnego operatora: `[x]` jest zasłużone, `[?]` jest uczciwe, nic nie jest fałszowane.

## Odniesienia

- **vc-init** — bootstrapuje kontekst agenta po scaffoldowaniu (checkpoint orientacji).
- **vc-ship** — jedyny normalny wykonawca A→Z wielocięciowego artefaktu dispatch scaffoldu.
- **vc-implement** / **vc-workflow** — ograniczone komórki WRITE wybierane wewnątrz kontraktu dispatchu.
  **vc-justdo** — samodzielna postawa, nie alias implement.
- **vc-review · vc-followup · vc-audit · vc-dou** — fazy READ, które falsyfikują każdy artefakt WRITE.
- **vc-operator** — czyta kolumnę `state` planu i prowadzi dispatch (trigger/stop).
- **vc-research** — triple-agentowy research dla niewiadomych znalezionych podczas Orient/Falsify.

## Antywzorce

- Planowanie przed checkpointem orientacji (komponowanie architektury z pamięci = cichy dryf).
- Shape bez odpowiedzi foundera albo wskazanego wcześniejszego wywiadu.
- Sekcja „Running This Plan" złożona z ręcznych komend workflow per task.
- Gołe pozwolenie na `--no-verify` bez autoryzacji fazy, zadeklarowanych bramek, raportu
  checkpointu i kontraktu admission integratora.
- 50-stronicowy design doc zamiast ostrego, mierzalnego planu.
- Proza zamiast kolumny `state` — operator nie odpali trigger/stop na prozie.
- Traktowanie twierdzenia `[~]` agenta jak `[x]` bez verifiera (pułapka optymizmu).
- STOP-jako-kapitulacja (502-i-umrzyj) zamiast STOP-jako-recovery-vector.
- Rozbijanie całej pracy na sekwencyjne zależności; pomijanie tożsamości produktu.

## Dodatkowe zasoby

- **`references/measure-core.md`** — alfabet `[ ][~][?][!][x]`, inwariant, Vector→Δ, taksonomia markerów.
- **`references/cadence.md`** — cadence read/write VC-ship (kolejność, WRITE/READ, przekazanie, reguły planowania).
- **`references/output-shapes.md`** — trzy kształty skali + 12-sekcyjny szablon dispatchu + tracker.
- **`references/plan-template.md`** — format wyjścia SCAFFOLD.md (teraz z Vector + state + verifier).
- **`references/compile-embargo.md`** — kanoniczny, fazowo-świadomy kontrakt embarga: autoryzowane
  lokalne checkpointy workera, niezależny admission integratora i domknięcie odroczonych bramek.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
