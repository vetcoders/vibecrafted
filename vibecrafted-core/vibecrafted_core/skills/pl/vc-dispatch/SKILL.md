---
name: vc-dispatch
description: "Operate external Vibecrafted fleet lines with prompt assembly, await/observe, reports, and recovery."
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-dispatch` (launcher `dispatch`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Ścieżka               | Literał tego skilla                                                                                                                                             |
> | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted dispatch …` / `vc-dispatch`                                                                                                                        |
> | 2. Interactive        | załaduj `vc-dispatch` (skill metody operatora) — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                                                      |
>
> **Uwaga:** External fleet **dyspozytura** — runs lines/plans; does not become implement/workflow.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Dispatch — the dyspozytura

**Jesteś dyspozytorem (rola vc-operator), NIE workerem.** Flota: zewnętrzni
agenci (codex, agy, …) launchowani przez launcher `vibecrafted`. Jeden mózg,
wiele rąk. Ten skill definiuje _metodę i rygor_ prowadzenia linii
cięć — nie posiada własnej fazy pipeline'u i można go wywołać z dowolnego punktu dowolnego
workflow.

`vc-ship` pozostaje normalną klamrą lifecycle. **Ograniczony dispatch zarządzony
przez Foundera** jest również wspieranym wywołaniem: prowadź ukształtowaną falę
napraw przez `vibecrafted dispatch <plan.dispatch.toml>`, z aktualną orientacją,
pełnymi briefami, doctor/dry-run, pinami modeli i verifierami. Nie wymaga to
jedenastu etapów lifecycle ani fikcyjnej wcześniejszej awarii `vc-ship`.
Przy konsumowaniu wyjścia scaffoldu zachowaj jego wymagania artefaktów,
DRIVER-a, manifestu i briefów.

## Posture

- Sesja interaktywna → **vc-partner**: opisuj Founderowi przejścia stanów,
  ujawniaj decyzje, przyjmuj korekty w locie.
- Sesja nieinteraktywna → **vc-ownership**: ta sama pętla, decyzje zapisane w
  journalu Operatora w autoryzowanym zakresie. Przyciski Foundera nadal obowiązują.
- W OBU postawach: stall-kill i recovery są autonomiczne, bez ceremonii (zobacz
  niżej). Odpowiedzialność za dostawę przebija uprzejmość wobec procesu.

## Boundary contracts

Aktualne `AGENTS.md` i `docs/public/dispatch/dispatch-schema.md` mają pierwszeństwo
przed historycznymi poradami, gdy rozchodzą się substrat, ownership, journale lub
semantyka zgód.

- **Wejście**: briefy + tracker z upstreamu (vc-scaffold / nadrzędny workflow)
  — ALBO, w **szybkiej fali** (niżej), napisane przez dispatchera w sesji z
  żywych ustaleń. Wracaj do scaffoldu tylko przy nieukształtowanej pracy;
  Founder zarządzający falę na posiadanych już dowodach to inny przypadek.
- **Wyczuwanie kontekstu**: skill nie niesie kanonicznego szablonu promptu.
  Przed układaniem promptów wyczuj skill nadrzędny, repo CLAUDE.md / AGENTS.md,
  evidence vc-init i istniejące artefakty planu; sprawdź pokrycie odwrotną
  checklistą (`references/prompt-checklist.md`).
- **Wyjście**: ograniczone commity i raporty workerów, receipty verifierów
  supervisora, stan trackera i jawny integration disposition. Izolowana dostawa
  nie jest admission do Living Tree. Wyznaczony integrator dowodzi ancestry,
  tożsamości merge-parent lub dokładnej patch equivalence i ponownie sprawdza
  drzewo docelowe. Workerzy uruchamiają skupione bramki; supervisor zadeklarowane
  verifiery. Szerokie bramki integracji należą do integratora. Formalny `vc-audit`
  uruchamia się na prośbę Foundera, nie przy każdym ograniczonym dispatchu.
- **Journal i artefakty**: tylko Operator dopisuje istotne decyzje do
  `<repo-root>/.vibecrafted/THE_JOURNAL.md` (ignored, prywatny, nigdy commitowany).
  Workerzy zwracają raporty, nie wpisy journala. Plany, briefy, trackery i raporty
  żyją pod `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/artifacts/<org>/<repo>/<YYYY_MMDD>/`.
  Używaj `VIBECRAFTED_REPORT_PATH` od launchera; zachowaj maszynowe `run_id` i
  `session_id` oraz uczciwy status raportu.

## Canonical Orientation Gate

`vc-dispatch` wymaga aktualnego evidence z `vc-init`, zanim poprowadzi linię.
Żaden dyspozytor nie powinien odpalać workera, przekształcać fali ani flipować stanu trackera
ze stale pamięci repozytorium.

`Loctree:loctree` to domyślna warstwa percepcji strukturalnej dla tej
orientacji. Użyj jej, by wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map),
zanim zbudujesz kolejność fal, ułożysz briefy workerów, ocenisz nakładanie się plików albo
przyjmiesz baton z poprzedniego cięcia. Brak evidence z Loctree oznacza, że linia
jest ślepa, nie tylko słabo udokumentowana.

## Repository Work Doctrine

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## The loop

```
pre-flight → DISPATCH → SPANKO → SPRAWDZENIE → FLIP → BATON → next cut
                ↑           |  (pulse ticks; stall → recovery-dispatch)
                └── refire ←┘  (partial delivery / convergence pressure)
```

**Pin modelu per cut (pre-flight):** każdy cut deklaruje pin `model` zgodny ze
swoją klasą — cut mechaniczny, w pełni rozpisany, jedzie na tańszym,
szybszym tierze; cut chirurgiczny albo niosący decyzje — na mocniejszym
tierze. Pin jedzie z planem do launchera (`Cut.model` →
`WorkflowLaunchSpec.model` → flaga modelu agenta: `--model` dla claude i
cursor, `-m` dla codex). Cut bez pinu leci na defaulcie
konta — a to NIE-decyzja, nie bezpieczny default: pinuj świadomie, a brak
pinu traktuj jako smell do rozwiązania przed startem.

1. **Pre-flight (raz na linię)**: testuj komendy verify z briefów przed startem;
   bramka pasująca do 0 testów jest trywialnie zielona; żądaj ≥1 nowego
   nietrywialnego testu w EXTRA. `grep -c` zwraca 1 przy 0 trafień (`|| true`);
   licz WSZYSTKIE linie `test result:` (wiele binarek — `tail -1` kłamie);
   `cargo test` bierze JEDEN filtr pozycyjny. Dla pytest dowiedź niepustej
   selekcji `-k` (`--collect-only -q` ≥1); preferuj **sondy semantyczne**, które
   dziś drukują STARĄ wartość, a NOWĄ dopiero po cięciu — uruchom je w pre-flight.
   Dla linii `.dispatch.toml`: `--doctor` → sonda/collect → `--dry-run` z bramką
   placeholderów wyrenderowanych promptów → launch. Pełne reguły z pola:
   `references/toml-plan-preflight.md`.
2. **Dispatch**: jeden plik promptu (nigdy argv — publiczne `ps`, ARG_MAX,
   połamane nowe linie), cztery warstwy checklisty. Launch:
   `bash -c 'ulimit -f unlimited; vibecrafted <skill> <agent> --model <pin> --file <p.md>'`
   (shelle mogą nieść miękki `ulimit -f` → SIGXFSZ/exit 153). **Domyślnie headless:**
   workerzy CLI i MCP działają w odłączonej sesji procesu nawet przy żywej vc-frame
   User Session. Obserwuj przez stan runu, transcript, `observe` i `await`;
   zakładka vc-frame może projektować te powierzchnie, ale nie posiada procesu
   workera. `--runtime terminal` / `runtime="visible"` wyłącznie przy jawnym
   wyjątku TTY providera, z kosztem sprzężenia workera z terminalem. Zapisz receipt
   (run_id, report, transcript, meta) w trackerze.
   Nigdy cicho nie podmieniaj pinu modelu Foundera. Headless Worker kończy bramki
   na pierwszym planie, pracę, raport i commit w bieżącej turze; zakończenia w tle
   nie mogą go wybudzić. Await po stronie supervisora to osobna rola.

3. **Spanko**: czekaj przez artefakty, nigdy przez gapienie się w pane. Użyj
   dedykowanej komendy jako standardowej pętli dyspozytora. Kanoniczny kontrakt
   supervisora (zobacz `docs/runtime/AGENT_OPS.md`): po dispatchu uzbrój
   `vibecrafted await <agent> --run-id <id>` natychmiast, po stronie supervisora.
   JSON control-plane, pliki raportów, transkrypty, karty terminala i
   zaplanowane wybudzenia są wyłącznie diagnostyczne, nie są sygnałem
   wybudzenia. Hedge'owanie await ad-hoc pollerami/watcherami to naruszenie
   Class 3; napraw `control_plane.await_run`, nie normalizuj hedge'u.
   Liveness jest zawsze 3-sygnałowy: przed "done" pogódź await verdict,
   terminalny stan w run meta i martwy worker pid; gdy raport jest obiecany,
   sprawdź obecność raportu. Dwa zgodne sygnały wystarczą do działania, trzy do
   deklaracji done; rozjazd = traktuj jako live i uzbrój await ponownie. Znany
   skew: rc=0-on-live oraz meta `active`/`stalled` po realnym zakończeniu.
   `vibecrafted loop spanko --run-id <id> --agent <a> --verify '<cmd>' --tracker <tracker.md> --cut-id <cut> --then '<next dispatch>'`
   (heartbeat cron frameworka → control-plane await → sprawdzenie → flip → baton),
   niższopoziomowego `vibecrafted loop await-run --run-id <id> --agent <a> --then-cmd '<next>'`,
   albo probe await-watch
   (`vibecrafted-await-watch.sh --meta <meta.json>` — tail-await-die) jako
   warstwy widoczności podporządkowanej kanonicznemu await. Żywy worker dostaje
   ZERO ingerencji; przerywanie mu w fazie bramki to czysta strata.
4. **Sprawdzenie** (po wyjściu workera): SHA commita istnieje → esencja diffa
   odpowiada briefowi → odczytane bramki i acceptance z raportu → uruchomione
   zadeklarowane verifiery supervisora. Unikaj przypadkowych powtórnych buildów;
   raport workera lub wynik hooka nie zastępuje wymaganej niezależnej weryfikacji.
5. **Flip**: `[~]→[x]` tylko przez dispatchera (jeden zapisujący), evidence =
   SHA + receipty verifierów + kto zweryfikował + integration disposition.
   Pola Acceptance workera są claimem; nigdy nie podpisują za Operatora ani
   Foundera. Source-green nie jest installed/live acceptance. Zasady ledgera:
   `references/ledger.md`.

6. **Baton**: prompt kolejnego cięcia niesie stan linii — które cięcia wylądowały,
   które commity, które pliki się przesunęły, co następny worker musi przeczytać ponownie.

## Fast wave (blitz) — Founder-ordered bounded dispatch

Gdy Founder wskazuje N zweryfikowanych ustaleń i zarządza falę („dispatchuj
falę na te pakiety”, „blitzkrieg, nie partyzantka”), dispatcher JEST autorem
briefów. Nie kieruj przez vc-scaffold, nie buduj DRIVERA, nie odpytuj rundami —
ustalenia z dowodami są planem.

Kształt (polowo sprawdzony, loctree-suite findings-wave-2, 2026-08-22):

1. **Pre-flight zostaje**: świeży baseline SHA (fetch — HEAD przesuwa się między
   twoją diagnozą a rozkazem), rozłączne domeny plików per cięcie, regiony
   plików współdzielonych zadeklarowane jawnie w briefach, wiszące siostrzane
   gałęzie wymienione jako do-not-touch.
2. **Zwięzły brief per cięcie**, pisany przez dispatchera w minuty, nie godziny:
   frontmatter + misja + dowody verbatim (komendy repro, kotwice linii) +
   pliki + akceptacja (≥2 nietrywialne testy) + bramki + poza-zakresem +
   blok substratu + zasady commit/trailer + ścieżka raportu + klauzula
   idempotencji. Checklist a min z pola: `references/fast-wave-brief.md`.
3. **Piny Foundera jadą dosłownie**: agenci i modele nazwane przez Foundera
   (`gpt-5.6-sol | grok-4.6 | claude-opus-5`) idą wprost do flag `--model`
   launchera i do tabeli trackera. Żadnych cichych podmian.
4. **Wszystkie cięcia startują równolegle**, awaity uzbrojone natychmiast
   supervisor-side, zwięzły tracker (cięcie | worker@model | run_id | gałąź | stan).
5. **Dispatcher integruje**: jednowątkowo, po osadzeniu, po diffach — szybka
   fala zmienia autorstwo briefów, nigdy rygor SPRAWDZENIE/FLIP.

Szybka fala to nadal linia: tracker, evidence ledgera, zadeklarowane verifiery
oraz receipty integracji pozostają wymagane. Domknij ograniczoną acceptance,
bez twierdzenia, że cały lifecycle został ukończony.

## Parallel waves are an obligation

Gdy cięcia zajmują niezależne obszary kodu, MUSISZ zaplanować fale pod maksymalną
wieloworkerową równoległość — uruchamianie jednego workera naraz ze strachu przed konfliktem
jest przeciwwskazane. Sekwencjonuj WYŁĄCZNIE twarde nakładania plików (ten sam plik/region).

**Szanuj jawny substrat.** Wybór Foundera, planu lub launchera wygrywa:
Living Tree / `local-native`, Fleet Worktrees / `local-worktrees`, Fleet VM local
oraz Fleet VM cloud to osobne runtime'y. Nie przenoś workera dla wygody.
Pojedyncza interaktywna linia domyślnie używa Living Tree, jeśli nie wybrano
innego runtime'u.

Dla typowanego `.dispatch.toml` supervisor tworzy dedykowany worktree każdego
nie-integratora pod `${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/worktrees/<org>/<repo>/<YYYY_MMDD>/<cut-id>`
na `cut/<cut-id>` z rozwiązanego baseline'u. `{repo}` jest efektywnym rootem
workera. Workerzy go dziedziczą; nie tworzą drugiej gałęzi ani worktree, nie
piszą w checkoucie nadrzędnym i nie integrują siebie. `depends_on` porządkuje
czas; `base = "cut:<cut-id>"` dostarcza ancestry poprzednika, gdy jest potrzebna.
Tylko wyznaczony integrator przyjmuje commity. Zobacz
`docs/public/dispatch/dispatch-schema.md`.

W każdym runtime czytaj ponownie przed edycją i stage'uj wyłącznie własne ścieżki
lub hunki: `git add -- <owned-path>` (albo selekcja hunków). Nigdy `git add -A`,
`git add .`, stash, discard ani commit cudzej pracy. Jeśli overlap uniemożliwia
uczciwą izolację, zachowaj diff i zgłoś substrate failure.

Zapisuj runtime class, parent/effective roots, baseline branch/full SHA, worker
branch/tip, artefakty, integration disposition i sprawdzony stan celu.
Fast-forward push feature brancha po własnym commicie jest dozwolony przez
Charter, chyba że bieżąca fala go zabrania. Trunk merge, force-push, PR merge/close,
kasowanie tagów/gałęzi i deploy pozostają przyciskami Foundera.

Zmiany bajtów skillów wymagają regeneracji `skills/SKILL_PROVENANCE.json` przez
`make skills-check UPDATE=1`; tracked owner to `scripts/gen_skill_provenance.py`.
Zachowaj historyczne wpisy. Szanuj cut właściciela generatora; konsumuj jego
przyjętą zależność bez edycji kodu. Po admission integrator regeneruje wspólny
manifest raz, zamiast brać manifest siostrzanego workera w całości.

## Refire = mini-marbles

Ponowne uruchomienie TEGO SAMEGO promptu (vc-frame: `<ENTER> re-run` na pane spawnu, albo
re-launch `--file` z tą samą ścieżką) to najtańszy prymityw zbieżności
— gorące podłoże, worker płaci mniej za archeologię i wydaje
budżet na delty (vc-marbles: „Marbles exploits cache heat").

- **Warunek wstępny**: briefy muszą być IDEMPOTENTNE — napisane tak, że re-run na drzewie,
  gdzie praca już wylądowała, weryfikuje i zatrzymuje się („nothing to do"), nigdy
  nie duplikuje.
- **Używaj refire, gdy**: task może być za wielki na jedną rundę workera; raport
  mówi, że pod-element nie został zrobiony; chcesz presji zbieżności w stylu marbles
  na kruchej powierzchni.
- Preferuj dispatch przez launcher nad pracą inline właśnie DLATEGO, że refire sprawia,
  że częściowy postęp jest kumulatywny.

## Read/Write cadence

- Odczyty (puls, artefakty, loct) są tanie i ciągłe; zapisy dzieją się na
  granicach pętli: tracker/journal po każdym przejściu, pliki promptów przed
  dispatchem, własne commity natychmiast (jedna jednostka = jeden commit, ukształtowany przez hooka,
  z prawdziwym trailerem session_id).
- W trakcie fal: bez przypadkowych duplikatów lintów/testów przez dispatchera,
  bez przygodnych fixów w zakresie workera. Zadeklarowane verifiery i admission
  integratora rozliczają dostawę; formalny audyt na prośbę Foundera.
- Ręce dispatchera dotykają repo tylko dla księgowości linii, ograniczonych
  napraw bookkeeping przydzielonych wprost przez Foundera (wtedy własny commit
  obowiązkowy) i zbierania evidence recovery.

## Pulse & stall (hard rule, both postures)

Heartbeat jest FRAMEWORK-FIRST — mechanika loop/cron jest już zautomatyzowana
w vibecrafted; nie sklecaj ręcznie timerów, gdy te istnieją:

- `vibecrafted loop spanko --run-id <id> --agent <a> --verify '<cmd>'
--tracker <tracker.md> --cut-id <cut> --then '<next dispatch>'` — komenda
  rangi dyspozytorskiej dla pętli await: SPANKO → SPRAWDZENIE → FLIP → BATON;
- `vibecrafted loop start|next|status|complete` — maszyna stanów linii z
  `--max-iterations` i `--completion-promise`;
- `vibecrafted cron line --root <repo> --every-minutes 10 --then-cmd
'vibecrafted loop next'` — heartbeat na prawdziwym crontabie, który łapie kontekst Loctree +
  AICX na każdy tick;
- `vibecrafted cron tick --after-idle-minutes 10 --then-cmd <cmd>` — wznawia
  zatwierdzoną następną komendę po oknie bezczynności.

Prowadź await dedykowaną komendą (NASZ vc-loop / cron) jako STANDARD nawet z
sesji interaktywnej — dispatchowany run MA CLI. Harness `/loop` to prawdziwy
last-resort, tylko gdy CLI vibecrafted jest faktycznie niedostępne.

Na każdym ticku oceniaj liveness po trzech niezależnych sygnałach wg
`references/pulse-and-stall.md`: status control-plane, mtime+rozmiar pliku sesji
agenta, delty `git status`. **≥10 min ciszy na wszystkich trzech → zabij
drzewo launchera, sprawdź proces osierocony (orphan) (orphan często DOWOZI), potem
recovery-dispatch z evidence wpisanym w aktualizację BATON** —
możliwie inny agent. Nigdy ślepy restart; nigdy kill na jednym sygnale
(sygnał matchujący znaną awarię ≠ ta awaria).

## In-flight corrections

Founder obala politykę dowiezionego cięcia w trakcie linii → napisz brief
korygujący (sufiks `b`, np. C2→C2b), zakolejkuj go z poszanowaniem nakładań plików, ponieś
decyzję Foundera wiernie co do ducha w BATON. Mechanika starego
cięcia zostaje; korygowana jest tylko polityka. Findingi po linii (smoke bugi,
życzenia featurowe) idą do pliku backtrackera z kotwicami w prawdzie kodu, stają się
cięciami backlogowymi na guzik Foundera.

## Failure patterns (do not repeat)

- Prompt w argv; nierenderowane placeholdery. Bramkuj **znane tokeny placeholderów**
  (`grep -E '\{(repo|id|agent|workflow|resolved_workflow|reports_dir|tracker|baton)\}'`
  na promptach dry-run, oczekuj pustego wyniku). Naiwne `grep -c '{'` daje false
  positive na wyrenderowanym JSON `{baton}`, który legalnie niesie klamry
  (zobacz `references/toml-plan-preflight.md`).
- Zabicie supervisora ścigającego własną pętlę — sprawdź dzieci `ps` i
  `git log` po fakcie; orphan często dowozi.
- Heredoc Foundera wpisany w chat zamiast w shell — zweryfikuj, że plik istnieje,
  zanim się do niego odwołasz.
- Re-run bramek workera „dla pewności" — claim kontra proof zostaje ustalony przez SHA +
  hooki + warstwę audytu, nie przez twój zduplikowany build.
- Traktowanie rąk kolegi z zespołu w „twoim" pliku jako zagrożenia — na Living
  Tree zlądowany commit należy do linii, nie do ciebie; rozjazd między rundami jest
  sygnałem dla vc-polarize, nie szkodą.

## Dependencies

Semantyka delivery-proof żyje w `vibecrafted_core.delivery`; zobacz
`docs/runtime/DELIVERY_PROOF_KERNEL_v1.md`.

vc-marbles (Living Tree, cache heat, jedna runda = jeden commit) ·
vc-scaffold (kształt brief/tracker) · vc-init (evidence orientacyjne) ·
vc-followup / vc-audit / vc-dou (settlement) · vc-polarize (product smear
z rozjazdu fal) · Loctree (prawda strukturalna przed wyszukiwaniem tekstowym).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
