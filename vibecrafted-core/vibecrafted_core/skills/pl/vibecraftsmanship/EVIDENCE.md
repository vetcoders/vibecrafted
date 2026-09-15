# Vibecraftsmanship — Dowody empiryczne (studium przypadku)

Kanoniczne studium przypadku to sesja z 2026-05-24, która wydestylowała ten
skill. Sesja jest empirycznym dowodem, że triada jest operacyjna,
a nie aspiracyjna.

---

## Oś czasu

| Czas (UTC) | Zdarzenie                                                                                                                                          | Ćwiczona oś                   |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| ~07:00     | Start sesji, framing operatora: wybór kręgosłupa dla stacku terminalowego                                                                          | gust                          |
| 07:35      | Zdispatchowano Falę A: A-1 (supervisor vibecrafted) + A-2 (most zdarzeń vc-console), 2 codex równolegle, różne repo                                | siła                          |
| 08:07      | A-1 ukończone, commit `0fc9206`, 999 LOC, ruff+mypy+pytest 148+ zielone                                                                            | rzeczywistość                 |
| 08:08      | A-2 ukończone, commit `4a9c5e5`, 1089 LOC, IPC smoke zielony, 2 ortogonalne cleanupy zgłoszone uczciwie                                            | rzeczywistość                 |
| 08:51      | Korekty operatora: „rescheduled not retired" + „3 laby w workspace" + „przestań pisać więcej briefów, niż ląduje commitów"                         | gust (reset framingu)         |
| 09:30      | Dodatki operatora: microsandbox + Zentty jako Lab #4 z granicą GPL + framing „równa intensywność"                                                  | gust                          |
| 09:39      | Zdispatchowano Falę B: 4 równoległe laby (wezterm + vc-apprt + locterm + microsandbox), każdy cwd we własnym labie                                 | siła                          |
| 10:06      | Fala B 3/4 raporty są: B-1 + B-3 zacommitowane, B-4 awaria podłoża na krunvm                                                                       | rzeczywistość                 |
| 10:11      | B-2 marbles zarchiwizowane w stanie terminalnym: 3 commity Faz wylądowały, worker uczciwie oznaczył FAILED na odziedziczonym podłożu (nie pas B-2) | rzeczywistość                 |
| 10:34      | Domknięcie Fali C: napisano CLOSE_OUT.md, 4 decyzje operatora wyniesione pod operator button                                                       | triada wyrównana              |
| ~17:30     | Klon Pensieve + vc-init: osobny punkt dowodowy — edytor markdown klasy Bear dowieziony w 28 godzin wcześniejszej pracy Vetcoders                   | rzeczywistość (cross-session) |

Całkowity czas zegarowy: **2h 59m** od startu sesji do domknięcia.

---

## Co dowieziono w tych ~3 godzinach

| Artefakt                   | Commit                                  | LOC              | Powierzchnia                                                                               | Przetrwał?                                                                               |
| -------------------------- | --------------------------------------- | ---------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| supervisor vibecrafted     | `0fc9206`                               | 999 / 25 plików  | Python supervisor + bin/vc-\* wrappery + capture session_id + synteza ostatniego finiszera | ✅ ruff+mypy+pytest 148+ zielone                                                         |
| zdarzenia spawn vc-console | `4a9c5e5`                               | 1089 / 17 plików | IpcEvent::SpawnUpdate + most jsonl + rendering w tray                                      | ✅ core IPC smoke zielony; 2 ortog cleanupy zgłoszone                                    |
| hooki Lua wezterm          | `02645e75c`                             | (wiele)          | Tytuł zakładki + pasek statusu + toast + tail events.jsonl                                 | ✅ 8/8 busted + integracyjny smoke na 17823 liniach                                      |
| runtime vc\_ apprt         | `acd99c746` + `83e9acb80` + `4d0e72e4b` | (wiele)          | Fix Zig 0.16 + session_id DiskPayload + emiter cyklu życia terminala                       | ✅ apprt 74/74 + smoke; ⚠ testy repo-wide padają na odziedziczonym podłożu (nie pas B-2) |
| plugin Python iTerm2       | `eb6beb8`                               | 1382 / 11 plików | AutoLaunch + StatusBar + Triggers + instalacja-osobno-GPL                                  | ✅ pytest 173/173, smoke granicy GPL czysty                                              |
| adapter Sandbox            | (niezacommitowane, brudny worktree)     | (wiele plików)   | SandboxAdapter + cykl życia msbserver + policy + testy + docs                              | ⚠ AWARIA PODŁOŻA: brak krunvm na hoście                                                  |

**5 z 6 jednostek pracy przetrwało kontakt z rzeczywistością.** 1 trafiła na uczciwą
awarię podłoża (informacja, nie dostarczenie).

---

## Co przewidywały konwencjonalne szacunki

Z 9-raportowego researchu cross-swarm (claude+codex+gemini × 3 swarmy
kręgosłupa) konwencjonalne szacunki w osobodniach były takie:

| Faza                                        | Konwencjonalne ED | Konwencjonalne tygodnie |
| ------------------------------------------- | ----------------- | ----------------------- |
| Faza 1 supervisor vibecrafted               | 12-18 ED          | 4-6 tygodni             |
| Faza 2 most zdarzeń vc-console              | 5-8 ED            | 3-5 tygodni             |
| Faza 3a hooki Lua wezterm                   | 3-5 ED            | ~1 tydzień              |
| Faza 3c fix Zig vc\_ + apprt                | 26-37 ED          | 6-9 tygodni             |
| Faza 3d plugin Python locterm               | 11.5 ED           | 4-6 tygodni             |
| **Suma (Faza 1 + 2 + 3 wszystkie gałęzie)** | **~58-86 ED**     | **~18-32 tygodni**      |

Rzeczywistość: **3 godziny**.

Współczynnik kompresji: `(18 tygodni × 168 godz/tydz) / 3 godz ≈ 1000×` dolna granica,
`(32 tygodnie × 168 godz/tydz) / 3 godz ≈ 1800×` górna granica.

Dla czystej pracy implementacyjnej Fali A+B (z wyłączeniem researchu). Przypadek
Pensieve (Swift + AppKit + TextKit2 tarcie podłoża, w większości szeregowo,
jedno repo) pokazuje niższą kompresję: szacunek gemini 3-6 miesięcy,
rzeczywistość 28 godzin, kompresja ~80-150× — nadal 2 rzędy wielkości.

---

## Pensieve — dowody cross-session

Repo Pensieve zostało zainicjalizowane o `2026-05-22 20:52:07` z
`[claude/vc-operator] feat(vcnotes): foundation skeleton for Swift/SwiftUI/TextKit2 rewrite`.
Najnowszy commit Vetcoders: `2026-05-24 01:11:05`.

**Upłynęło: 28 godzin, 19 minut.**

W tych 28 godzinach wylądowało 27 commitów na 4485 liniach Swift,
dostarczając:

- edytor źródła TextKit 2 z podświetlaniem składni + numerami linii (gemini)
- podgląd HTML swift-markdown z motywem + debounce (claude)
- storage file-first z bookmarkiem + watcherem + autosave (codex)
- eksplorator importu workspace + indeks wyszukiwania workspace
- pipeline podglądu (ścieżki względne + theming wyglądu + ochrona brudnego dokumentu)
- refactor MarkdownEditorSurface (cięcie po maczecie „kruchego mostu edytora")
- refactor kontrolera komend (routowanie komend aplikacji przez kontroler)
- pipeline release podpisanego + notaryzowanego .app + .dmg (operator)
- cienka fasada Makefile dla codziennej ergonomii
- menu kontekstowe sidebaru + feedback selekcji w eksploratorze
- kanoniczny katalog Application Support + bramka lintu uczciwa

To pokrywa wszystkie 4 sekcje dekompozycji gemini „kompleksowy edytor Markdown w
Swift" — parser/AST, silnik TextKit2, mikro-interakcje UX, zarządzanie
plikami — PLUS pipeline release podpisany/notaryzowany, którego gemini nawet
nie uwzględnił w szacunku.

Szacunek tieru premium gemini: **3-6 miesięcy** (90-180 dni).
Rzeczywistość: **~1.17 dnia**.
Kompresja: **77-154×** dla Swift/AppKit (niższa niż Fala A/B z powodu
tarcia podłoża TextKit 2 + narzutu cyklu kompilacji Xcode + w większości
szeregowego przepływu w jednym repo).

---

## Korekty operatora (oś gustu w akcji)

Sesja pokazała, że korekty operatora w czasie rzeczywistym są
nośne, a nie opcjonalne. Bez nich agent by zdryfował:

1. **„Rescheduled, not retired"** — semantyczne przeframowanie vc-mux. Agent
   zdryfował do języka „retirement"; korekta operatora zachowała
   inżynierską godność odłożonego-ale-funkcjonalnego kodu.
2. **„3 (potem 4) laby równa intensywność"** — operator odrzucił rankowanie
   agenta „główny kręgosłup vs równoległe R&D vs premium". Równa intensywność
   była prawdą od samego początku; rankowanie agenta było projekcją
   konwencjonalnego myślenia o przepustowości zespołu, które nie stosuje się
   do tempa Vetcoders.
3. **„Przestań pisać więcej briefów, niż ląduje commitów"** — agent
   przeinżynierował briefy Fali A (~300 LOC każdy); operator odwrócił
   proporcję na „agenci powinni wygrywać, dzięki tobie". Briefy Fali B
   były krótsze, a agenci dowozili więcej.
4. **„Dispatch z cwd per lab"** — wskazówka operatora, by startować każdego
   agenta w jego własnym katalogu labu, nie w generycznym roocie vc-runtime.
   Dyscyplina Living Tree + czyste raporty per lab.
5. **„Fork-and-forget"** — dla microsandbox: bez sync z upstream, bez
   rebase, traktuj jako codebase Vetcoders od klona. Decyzja gustu operatora,
   której agent nie mógł przewidzieć (microsandbox jest na licencji Apache
   2.0, technicznie syncowalny; operator wybrał, żeby tego nie robić).
6. **Sceptycyzm wobec przeskalowania czasu** — operator naciskał na
   sekwencję „miesiące to tygodnie, tygodnie to dni, dni to..." zmuszając
   agenta do uznania „dni to godziny". Dowody empiryczne
   potwierdziły, że operator miał rację.

Każda korekta przekształcała trajektorię w locie. Agent, który nie
reaguje na korekty operatora, produkuje błędną pracę z dużą prędkością.

---

## Wydestylowane lekcje

1. **Triada to ograniczenie, nie ważone głosowanie.** Wszystkie 3 muszą być spełnione.
2. **Kompresja jest realna i zależna od stacku.** 1000× dla
   równoległego Python/Rust; 80-150× dla szeregowego Swift/AppKit.
   Stosuj empirycznie per projekt, nie generycznie.
3. **Briefy ≠ commity.** Sukces operatora-agenta mierzy się w
   commitach wylądowanych przez workery, nie w LOC napisanych briefów.
4. **Awaria podłoża ≠ awaria dostarczenia.** Brak krunvm to
   informacja; uczciwy raport > fałszywy PASS.
5. **Równa intensywność > rankowanie**, gdy operator chce pokrycia
   portfolio przez segmenty ICP.
6. **Korekty operatora to sygnały gustu.** Czytaj je jako
   korekty trajektorii, nie czepialstwo.
7. **Konwencjonalne szacunki z raportów cross-frontier są
   konserwatywne.** Cross-waliduj z empirycznym tempem Vetcoders.
8. **Cwd per lab + branche per repo = czysty Living Tree.**
9. **Marbles poprawnie zatrzymuje się na sygnale report-failed.** Nie pal
   iteracji na nieistotnym gruncie.
10. **Rzeczywistość decyduje.** Dema się nie liczą. Mocki się nie liczą.
    Liczy się kod instalowalny u klienta, z wylądowanym commitem, z zieloną bramką.

---

## Studium przypadku #2 — autokrytyka agenta po stronie operatora (2026-05-25)

Niecałe 24 godziny po wylądowaniu skilla operator zrobił na nim dogfooding w
równoległej sesji dispatchującej agentów benchmarku GPU. Agent po stronie operatora
złapał się na sięganiu po natywne narzędzie `Agent` z `run_in_background:
true` zamiast `vibecrafted justdo codex --prompt '...'`. Agent
sam się skorygował w połowie sesji i wyprodukował ustrukturyzowaną krytykę z
podziałem 70/30:

- **70% awaria dyscypliny agenta** — agent miał dostępną komendę `vibecrafted`
  przez Bash, znał routing wg `vc-why-matrix`,
  wybrał łatwiejszą ścieżkę odruchu. „Sięgnąłem po native Agent bo łatwiej."
- **30% luka frameworka** — oryginalny `SKILL.md` był deklaratywny („triada
  ponad taktykę") bez jawnego operacyjnego trybu domyślnego wymuszającego
  `vibecrafted <workflow> <agent>` jako TĘ powierzchnię zewnętrznego dispatchu.
  Karta bez operacyjnych zębów = dryf dozwolony.

Sama autokrytyka jest empirycznym dowodem, że skill działa:
**agent po stronie operatora wywołał vibecraftsmanship, uruchomił check triady
w czasie rzeczywistym i zidentyfikował własny dryf osi Siły** (Siła bez
obserwowalności = ulotne wyjście natywnego `Agent`). Pętla feedbacku karty
funkcjonalna w ciągu 24 godzin od wylądowania skilla.

### Patche zastosowane w odpowiedzi

1. **SKILL.md zyskał** sekcję „Domyślny tryb operacyjny — zewnętrzna powierzchnia dispatchu"
   z twardą zasadą + sygnałem wykrycia + checkiem odruchowym + powodem
   dla zasady. Umieszczona między „Kiedy używać" a „Zależnościami", więc
   czyta się ją PRZED tym, jak agent dotrze do szczegółów Trzech osi.
2. **AXES.md zyskał** podsekcję „Domyślny tryb operacyjny — zewnętrzna powierzchnia
   dispatchu (TWARDA ZASADA)" w ramach Osi 2 (Siła), wyjaśniającą, że
   wyjście natywnego `Agent` łamie obietnicę „szerszego menu dla operatora",
   bo znika z kontekstu bez podłoża artefaktów.
3. **Zależności** zaktualizowane, by oznaczyć `vc-agents` jako **wymagane** dla
   Domyślnego trybu operacyjnego (wcześniej nieobecne na liście zależności).
4. **EVIDENCE.md** (ten plik) zyskał to studium przypadku.

### Otwarte follow-upy (decyzje operatora w toku)

- **vc-agents SKILL.md** — proponowany dodatek: 5-punktowa checklista
  pre-dispatch po stronie agenta (skill należący do operatora, zmiana wymaga
  zatwierdzenia operatora; draft niezacommitowany)
- **Serwer MCP `vibecrafted` dispatch+await** — inwestycja inżynierska,
  by dać agentowi-rodzicowi push notification o ukończeniu zewnętrznego workera
  (domyka lukę przekazania obserwowalności; poza scope skilla,
  zaparkowane jako pozycja feature-dev)

### Wydestylowana lekcja (dodana do głównej listy jako #11)

11. **Karta bez operacyjnych zębów = dryf dozwolony.** Deklaratywna
    postawa („triada ponad taktykę") nie przetrwa kontaktu z
    ergonomicznymi odruchami. Każde twierdzenie o osi Siły potrzebuje twardej operacyjnej
    zasady, która mówi **co wpisać** w momencie decyzji, nie tylko
    **o czym myśleć** przed decydowaniem.

---

## Studium przypadku #3 — autokorekta triady w czasie rzeczywistym + rozszerzenie doktryny `/loop` (2026-05-25)

Operator zrobił dogfooding skilla na własnym stacku infrastrukturalnym —
pipeline instalatora `vibecrafted`. Przez eskalujące pytania peer-pressure
(„a co z curl|bash?", „a jako potencjalny konsument?"),
operator przeprowadził agenta-operatora przez trzy cykle diagnozy:

### Cykl 1 — oryginalna diagnoza (częściowo błędna)

Agent-operator twierdził: bootstrap stage'uje do ulotnego
`/tmp/vibecrafted-XXXX/`, więc shim resolvera wygenerowany przez instalator ma
martwego pierwszego kandydata z założenia dla NOWYCH użytkowników. Antywzorzec
w 3 warstwach.

### Cykl 2 — operator oddala kadr jeszcze bardziej

Pytanie: „no a jak ktoś robi curl | bash?" zmusiło agenta-operatora do
weryfikacji przez pobranie żywej zawartości `https://vibecrafted.io/install.sh`
i przeczytanie faktycznego użycia bootstrapu:

> "Bootstrap a local 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. source snapshot into
> `$VIBECRAFTED_ROOT/.vibecrafted/tools` and then run a local staged
> install path from that copy."

Wyjście calla rzeczywistości: bootstrap stage'uje do **trwałego**
`~/.vibecrafted/tools/vibecrafted-main/`, NIE do `/tmp/`. Przypadek NOWEGO
użytkownika działa zgodnie z zamysłem — kopia instalacyjna JEST kanoniczna,
resolver trafia w pierwszego kandydata. Hipoteza Cyklu 1 agenta-operatora została
empirycznie sfalsyfikowana w ramach jednego checku rzeczywistości.

Bug faktycznie istnieje tylko w **trybie dev operatora** (`git clone` +
`bash install.sh` z lokalnego checkoutu) — `$repo_root` rozwija się do ścieżki
żywego repo, zostaje wypieczony w shim, w połowie rebase psuje inne shelle.

### Cykl 3 — dispatch bez wahania

Instrukcja operatora: _„dispatchuj to ziom a się nie zastanawiasz.
confidence high? operator nie odpowiada? -> dispatch”_ — jawna
zasada „bez odbioru" zastosowana do pracy nad aktualizacją skilla, plus wskazanie,
że agent-operator zapomniał wejść w `/loop`:

> "Ty zapomniałeś wejść w /loop który musi stać się canonical inside
> power feature of claude utilized by our framework!"

Agent-operator wylądował trzy skoordynowane zmiany bez dalszego
doprecyzowania:

1. **Patch `install-shell.sh`** — usunięto zahardkodowane rozwijanie
   `$repo_root`, które wypiekało ścieżkę z czasu instalacji-operatora w każdy wygenerowany
   shim. Łańcuch resolvera teraz: opt-in env `VIBECRAFTED_ROOT` (tryb dev) →
   kanoniczne ścieżki instalacji (`~/.vibecrafted/tools/vibecrafted-current/...`)
   tylko. Stany pośrednie w połowie rebase przestają psuć inne shelle.
2. **Rozszerzony Domyślny tryb operacyjny `SKILL.md`** o drugą kanoniczną
   powierzchnię — natywny dla Claude Code `/loop` dla autonomicznego samodzielnego tempa.
   Agent-operator ma teraz jawną doktrynę **KIEDY wejść w `/loop`
   vs KIEDY zostać jednoturowym**, uzupełniającą istniejącą
   zasadę dispatchu (`vibecrafted` vs natywny `Agent`).
3. **To studium przypadku** w EVIDENCE.md.

### Wydestylowane lekcje (dodane do głównej listy jako #12-13)

12. **Diagnoza bez weryfikacji rzeczywistością = dryf dozwolony.** Dwie
    tury architektonicznych twierdzeń o stage'owaniu do `/tmp/` rozpadły się po
    jednym `curl` do żywego URL install.sh. Call rzeczywistości powinien być
    PIERWSZYM ruchem przy twierdzeniu o zachowaniu infrastruktury, nie ostatnim.
13. **Agent-operator musi wejść w `/loop`, gdy istnieje autonomiczny ogon.**
    Jednoturowa bierność („czekanie na odpowiedź operatora"), gdy ISTNIEJE
    autonomiczna praca do kontynuowania = przeoczony kanoniczny wzorzec. `/loop`
    to most między „operator prowadzi" a „agent freelancuje" —
    używaj go.

### Doprecyzowana powierzchnia złożenia

Obie kanoniczne powierzchnie osi Siły są teraz udokumentowane w SKILL.md:

| Powierzchnia                                                                                            | Kiedy                                               | Co rozwiązuje                                                                                                             |
| ------------------------------------------------------------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `vibecrafted <workflow> <agent>` (Bash)                                                                 | Dispatch zewnętrznego workera produkującego dostawy | Obserwowalność (kanoniczny store, transkrypty, meta.json, odtwarzalny launch.sh) — podłoże artefaktów klasy operatorskiej |
| `/loop` (natywny Claude Code, odpowiednik: `ScheduleWakeup` z sentinelem `<<autonomous-loop-dynamic>>`) | Autonomiczne samodzielne tempo przez tury           | Ciągłość między zaangażowaniami operatora bez utraty tempa lub pollingu w ciasnych pętlach                                |

Razem: zewnętrzny dispatch tworzy pracę asynchroniczną; `/loop` utrzymuje agenta
obecnym dla ukończenia tej asynchronicznej pracy. Oba są kanoniczne, oba
zadeklarowane jako TWARDE ZASADY w sekcji Domyślny tryb operacyjny.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
