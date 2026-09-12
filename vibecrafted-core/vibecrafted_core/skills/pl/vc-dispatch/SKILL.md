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
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
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

## Postawa

- Sesja interaktywna → **vc-partner**: narratuj operatorowi przejścia między stanami,
  wyciągaj na wierzch decyzje, przyjmuj korekty w locie.
- Sesja nieinteraktywna → **vc-ownership**: ta sama pętla, decyzje logowane do
  journala zamiast zadawane.
- W OBU postawach: stall-kill i recovery są autonomiczne, bez ceremonii (zob.
  niżej). Odpowiedzialność za dowiezienie przebija uprzejmość wobec procesu.

## Kontrakty graniczne

- **Wejście**: briefy + tracker wyprodukowane upstream (vc-scaffold / nadrzędny
  workflow) — ALBO, w **szybkiej fali** (zob. niżej), napisane przez dispatchera
  w sesji z żywych ustaleń. Oddawaj kontrolę do scaffolda tylko wtedy, gdy
  robota jest naprawdę nieukształtowana; operator zarządzający falę na
  dowodach, które już trzymasz, to nie ten przypadek.
- **Wyczuwanie kontekstu**: ten skill nie niesie kanonicznego szablonu promptu.
  Zanim ułożysz prompty, wyczuj kontekst osadzenia — skill nadrzędny,
  repo CLAUDE.md / AGENTS.md, evidence z vc-init, istniejące artefakty planu —
  i zweryfikuj pokrycie odwrotną checklistą
  (`references/prompt-checklist.md`).
- **Wyjście**: ustabilizowana linia — tracker kompletny z evidence, append-only
  journal, commity na Living Tree, backlog po linii — przekazana
  skillom audytowym (vc-followup, vc-audit, vc-dou). **Bramki jakości należą do
  warstwy audytu, nie do pętli dispatchu** (zob. Kadencja).

## Checkpoint orientacji

`vc-dispatch` wymaga aktualnego evidence z `vc-init`, zanim poprowadzi linię.
Żaden dyspozytor nie powinien odpalać workera, przekształcać fali ani flipować stanu trackera
ze stale pamięci repozytorium.

`Loctree:loctree` to domyślna warstwa percepcji strukturalnej dla tej
orientacji. Użyj jej, by wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map),
zanim zbudujesz kolejność fal, ułożysz briefy workerów, ocenisz nakładanie się plików albo
przyjmiesz baton z poprzedniego cięcia. Brak evidence z Loctree oznacza, że linia
jest ślepa, nie tylko słabo udokumentowana.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Pętla

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

1. **Pre-flight (raz na linię)**: przetestuj komendy weryfikujące z briefów,
   zanim linia ruszy — bramka, która matchuje 0 testów, jest trywialnie zielona;
   żądaj ≥1 nowego nietrywialnego testu w EXTRA. `grep -c` wychodzi z kodem 1 przy 0 trafień
   (`|| true`); licz WSZYSTKIE linie `test result:` (wiele binarek — `tail -1`
   kłamie); `cargo test` bierze JEDEN pozycyjny filtr, nie dwa.
2. **Dispatch**: jeden plik promptu (nigdy argv — `ps`-publiczne, ARG_MAX, połamane
   nowe linie), cztery warstwy wg checklisty. Launch:
   `bash -c 'ulimit -f unlimited; vibecrafted <skill> <agent> --file <p.md>'`
   (shelle mogą nieść miękki `ulimit -f` → SIGXFSZ/exit 153). Zapisz receipt
   (run_id, report, transcript, meta) w trackerze.
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
4. **Sprawdzenie** (przy wyjściu workera): SHA commita istnieje → esencja diffa zgadza się
   z briefem → odczytane wyniki bramek i acceptance z raportu workera. **NIE
   uruchamiaj ponownie lintów/testów workera** — workerzy uruchamiają własne bramki, a commit
   hooki je egzekwują; twój re-run to koszt bez informacji.
5. **Flip**: `[~]→[x]` tylko przez dyspozytora (jeden zapisujący), evidence =
   SHA + bramki zaraportowane przez workera + kto zweryfikował. Manualne/runtime'owe acceptance
   zostaje `[?]` dla operatora. Reguły ledgera: `references/ledger.md`.
6. **Baton**: prompt kolejnego cięcia niesie stan linii — które cięcia wylądowały,
   które commity, które pliki się przesunęły, co następny worker musi przeczytać ponownie.

## Szybka fala (blitz) — natychmiastowy dispatch na rozkaz operatora

Gdy operator wskazuje N zweryfikowanych ustaleń i zarządza falę („dispatchuj
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
3. **Piny operatora jadą dosłownie**: agenci i modele nazwane przez operatora
   (`gpt-5.6-sol | grok-4.6 | claude-opus-5`) idą wprost do flag `--model`
   launchera i do tabeli trackera. Żadnych cichych podmian.
4. **Wszystkie cięcia startują równolegle**, awaity uzbrojone natychmiast
   supervisor-side, zwięzły tracker (cięcie | worker@model | run_id | gałąź | stan).
5. **Dispatcher integruje**: jednowątkowo, po osadzeniu, po diffach — szybka
   fala zmienia autorstwo briefów, nigdy rygor SPRAWDZENIE/FLIP.

Szybka fala to nadal linia: tracker, evidence w ledgerze i warstwa audytu na
końcu nie są opcjonalne. Fala tnie ceremonię, nie dowód.

## Fale równoległe to obowiązek

Gdy cięcia zajmują niezależne obszary kodu, MUSISZ zaplanować fale pod maksymalną
wieloworkerową równoległość — uruchamianie jednego workera naraz ze strachu przed konfliktem
jest przeciwwskazane. Sekwencjonuj WYŁĄCZNIE twarde nakładania plików (ten sam plik/region).

**Substrat to mechanika, nie preferencja** (Living Tree Rule v3): domyślny dla
linii interaktywnej jest Living Tree, ale gdy operator zarządza worktrees dla
równoległości, ten rozkaz JEST substratem — kropka. Każdy worker tworzy wtedy
WŁASNY worktree (`~/.vibecrafted/worktrees/<org>/<repo>/<RRRR_MMDD>/<slug>`,
gałąź `<agent>/workflow/<slug>`, z przypiętego baseline SHA), pushuje gałąź,
nigdy nie merguje trunka; dispatcher jest jednowątkowym integratorem.
Skitranie równoległej floty w jednym współdzielonym checkoucie po takim
rozkazie to powtórka porażki substratu kanarka z 2026-08-20 — nie rób tego.

Strach, że „dirty tree = konflikty”, to inwersja obserwowanej rzeczywistości
DLA TORU LIVING TREE:
merge hell rodzi się w NIEZARZĄDZANEJ izolacji — worktree bez verifierów i bez
integratora, gdzie niezależne wizje workerów rozjeżdżają się i muszą zostać
pogodzone na końcu. Living Tree (zob. vc-marbles, LIVING_TREE_RULE.md) trzyma
każdą rękę nieprzerwanie wyrównaną do żywego baseline'u — ktoś zawsze
dostosowuje się na miejscu, ryzyko merge-conflict zbliża się do zera. Formacja
Fleet Worktrees (Tryb B kanonu) usuwa to ryzyko inną drogą: rozłącznymi
domenami plików i jednowątkowym integratorem — wybór trybu zapisuje plan,
nie odruch workera. Workerowe sweepy `git add -A` zachowują współbieżną pracę
(odnotuj w journalu, czyje linie pojechały na piggyback); sweep to commitment, nie
destrukcja.

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

## Kadencja Read/Write

- Odczyty (puls, artefakty, loct) są tanie i ciągłe; zapisy dzieją się na
  granicach pętli: tracker/journal po każdym przejściu, pliki promptów przed
  dispatchem, własne commity natychmiast (jedna jednostka = jeden commit, ukształtowany przez hooka,
  z prawdziwym trailerem session_id).
- W trakcie fal: ŻADNYCH uruchomień lintów/testów przez dyspozytora, ŻADNYCH przejazdowych fixów w
  scope workera. Zaufaj instrukcjom i frameworkowi; prawda zostaje ustalona przez
  skille audytowe na końcu linii.
- Własne ręce dyspozytora dotykają repo tylko po to, by: prowadzić księgowość linii, robić hotfixy,
  które operator przydziela bezpośrednio (wtedy: własny commit obowiązkowy), oraz zbierać
  evidence do recovery.

## Puls i stall (twarda reguła, obie postawy)

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

## Korekty w locie

Operator obala politykę dowiezionego cięcia w trakcie linii → napisz brief
korygujący (sufiks `b`, np. C2→C2b), zakolejkuj go z poszanowaniem nakładań plików, ponieś
decyzję operatora wiernie co do ducha w BATON. Mechanika starego
cięcia zostaje; korygowana jest tylko polityka. Findingi po linii (smoke bugi,
życzenia featurowe) idą do pliku backtrackera z kotwicami w prawdzie kodu, stają się
cięciami backlogowymi na guzik operatora.

## Wzorce awarii (nie powtarzaj)

- Prompt w argv; placeholdery nierenderowane (bramka `grep -c '{' file` = 0).
- Zabicie supervisora ścigającego własną pętlę — sprawdź dzieci `ps` i
  `git log` po fakcie; orphan często dowozi.
- Heredoc operatora wpisany w chat zamiast w shell — zweryfikuj, że plik istnieje,
  zanim się do niego odwołasz.
- Re-run bramek workera „dla pewności" — claim kontra proof zostaje ustalony przez SHA +
  hooki + warstwę audytu, nie przez twój zduplikowany build.
- Traktowanie rąk kolegi z zespołu w „twoim" pliku jako zagrożenia — na Living
  Tree zlądowany commit należy do linii, nie do ciebie; rozjazd między rundami jest
  sygnałem dla vc-polarize, nie szkodą.

## Zależności

vc-marbles (Living Tree, cache heat, jedna runda = jeden commit) ·
vc-scaffold (kształt brief/tracker) · vc-init (evidence orientacyjne) ·
vc-followup / vc-audit / vc-dou (settlement) · vc-polarize (product smear
z rozjazdu fal) · Loctree (prawda strukturalna przed wyszukiwaniem tekstowym).

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
