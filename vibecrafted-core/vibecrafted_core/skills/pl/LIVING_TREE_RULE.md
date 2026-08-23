# Reguła Living Tree

VetCoderzy pracują w jednym współdzielonym checkoucie repozytorium — **domyślnie**.
Od 2026-08-10 doktryna ma dwa usankcjonowane tryby, a wszystko pomiędzy nimi
pozostaje zakazane.

## Tryb A — Living Tree (domyślny, interaktywny)

Sesje interaktywne, workflowy jednoosobowe i każda praca bez wcześniej
zacommitowanego verifiera działają we współdzielonym checkoucie. Worktree to
tutaj nie jest niewinny szczegół implementacyjny: odizolowane drzewo bez
zmierzonej ścieżki wyjścia rozszczepia prawdę runtime'u, ukrywa współbieżne
edycje i zamienia szybki Vibecraftsmanship w archeologię branchy.

Twarde reguły (bez zmian):

- Pracuj w bieżącym checkoucie i na bieżącym branchu.
- Nie uruchamiaj `git worktree add`, nie twórz bocznego checkoutu ani nie przenoś wykonania na inny tor.
- Nie przełączaj branchy podczas aktywnego wykonywania workflowu.
- Nie twórz branchy, chyba że operator wprost prosi o ten ruch gitowy.
- Czytaj pliki ponownie przed edycją, gdy minął czas lub mogą działać współbieżni agenci.
- Traktuj lokalne zmiany jako pracę współdzieloną. Nigdy nie rób stash, discard, reset ani nie nadpisuj zmian, których
  sam nie wprowadziłeś.

Ogólne prośby w stylu „isolate this", „work in parallel", „make a clean
branch" czy „avoid conflicts" **nie** przełączają trybu. Jeśli bieżące podłoże
jest zbyt zatrute, by bezpiecznie kontynuować, zatrzymaj się i zgłoś awarię
podłoża (substrate failure) — nigdy nie rozwiązuj nieważności podłoża ucieczką
do worktree.

## Tryb B — Fleet Worktrees (formacja dispatchowa)

Pisany multi-agentowy dispatch MOŻE umieścić każde cięcie w osobnym worktree —
a przy 2+ równocześnie piszących workerach POWINIEN — gdy zachodzą **wszystkie
cztery** warunki:

1. **Najpierw verifiery.** Delivery-verifiery (testy RED albo równoważne
   niepodrabialne sprawdzenia) są zacommitowane na gałęzi bazowej PRZED
   dispatchem, a komendy verify supervisora je uruchamiają. Osłabienie, zmiana
   nazwy albo usunięcie zacommitowanej asercji wymaga zgody operatora.
2. **Rozłączne domeny.** Cięcia są planowane na nienachodzących domenach
   plików; tam gdzie domeny się stykają, cięcia idą sekwencyjnie, nigdy
   równolegle. Pliki-huby są strefami sekwencji z definicji.
3. **Jeden integrator.** Nazwany koordynator jest właścicielem integracji:
   merge'uje gałęzie cięć jednowątkowo po zielonych verifierach, uruchamia
   pełne bramki na zintegrowanym drzewie i journaluje każdą zmianę planu w
   locie. Workerzy Trybu B NIGDY nie pushują, NIGDY nie merge'ują, NIGDY nie
   dotykają głównego checkoutu — remotes należą do integratora. Workerzy
   Trybu A / współdzielonego checkoutu traktują niedestruktywny push gałęzi
   feature jako wolny ruch po swoich commitach (zob. `vc-operator/AUTONOMY.md`).
4. **Standardowa geometria.** Dispatcher jest właścicielem neutralnych wobec
   providera worktree pod
   `~/.vibecrafted/worktrees/<org>/<repo>/YYYY_MMDD/<cut-id>` na gałęziach
   `cut/<cut-id>`. Każde cięcie ma prawdziwy, ignorowany
   `<worktree>/target`; współdzielenie i symlinkowanie targetów Cargo jest
   zabronione. Trwałe dowody zostają pod `~/.vibecrafted/artifacts`, stan
   efemeryczny pod `~/.vibecrafted/control_plane`, a jawny cleanup może usunąć
   wyłącznie settled worktree. Zero rootów provider-specific i zero
   drzew-sierot.

Tryb B jest operator-explicit z konstrukcji: istnieje wyłącznie wewnątrz
pisanego planu (dispatch TOML + briefy), który przeszedł swoje doctory. Agent
nie może wejść w Tryb B ad hoc.

## Zasięg — co runtime dziś potrafi, a czego nie (zmierzone 2026-08-18)

Dwa tryby powyżej opisują doktrynę. Ta sekcja opisuje _mechanizm_, żeby nikt
nie czytał Trybu B jako możliwości, którą codzienny launcher już ma. Zmierzone
na tym drzewie, nie zapamiętane:

**Tryb B jest prawdziwy i istnieje wyłącznie w planie dispatchu.**
`dispatch/worktrees.py` trzyma kanoniczną geometrię od początku do końca:
`WorktreeManager.prepare` odmawia niejednoznacznego reuse, `_validate_reuse`
odmawia brudnego lub niezarejestrowanego checkoutu, `_validate_target` odmawia
symlinkowanego albo uciekającego targetu Cargo, a `cleanup` odmawia wszystkiego,
co nie jest settled. `_validate_integrator` egzekwuje kontrakt samego
integratora — główny checkout, czyste drzewo, zgodność baseline SHA.
`dispatch/supervisor.py` to napędza, `dispatch/doctor.py` robi preflight,
`scripts/smoke-dispatch-worktrees.py` ćwiczy dwóch współbieżnych workerów plus
wyłączny join na prawdziwych linked checkoutach. Ta część jest robotą skończoną.

**Codzienny plan launchera nie ma worktree w ogóle.** `workflow.py` jest
launcherem wszystkich 24 zarejestrowanych workflowów i nie zawiera ani jednego
odwołania do worktree; `WorkflowLaunchSpec` nie niesie ani cut id, ani worktree,
ani pola integratora. Zatem `vibecrafted implement claude`,
`vibecrafted marbles codex` i każdy inny `vibecrafted <skill> <agent>` działają
we współdzielonym głównym checkoucie z konstrukcji. **Tryb B jest nieosiągalny
z codziennej powierzchni komend.** To jest luka — nie doktryna.

**Skąd naprawdę bierze się współbieżność.** Wewnątrz jednego launcha runtime
jest jednopiszący: `workflow_runtime._run_loop` uruchamia iteracje
marbles/polarize `--count` _sekwencyjnie_, a research puszcza swoje tory
współbieżnie, ale w kadencji read. Współbieżni _piszący_ pojawiają się, gdy
operator odpala kilka launchów na tym samym checkoucie — czyli w zwykłym
codziennym przypadku. Nic w control plane nie czyni checkoutu wyłącznym dla
jednego piszącego runu, więc ten przypadek ląduje w Trybie A niezależnie od
tego, czy spełnia cztery warunki Trybu B.

**Konsekwencje, powiedziane wprost.**

- Agent, który „powinien" być w Trybie B wedle warunku 3, nie ma jak tam wejść
  bez pisanego planu dispatchu. Prośba o worktree z poziomu launcha workflow nie
  jest złamaniem dyscypliny; ta powierzchnia po prostu nie istnieje.
- `integrator = true` to pole dispatch-TOML. Poza planem nie ma powierzchni
  integratora: żadnej flagi launchera, żadnej roli w control plane, żadnego
  prymitywu join.
- Dopóki ten zasięg się nie domknie, „praca równoległa odbywa się w worktrees"
  opisuje wyłącznie plan dispatchu. Powiedzenie tego o całym runtimie byłoby
  deklaracją, której kod nie potwierdza.

Domknięcie zasięgu to cięcie architektoniczne, nie edycja doktryny: potrzebuje
`WorkflowLaunchSpec` świadomego worktree, ścieżki launchera przez
`WorktreeManager` i roli integratora, którą control plane potrafi nazwać. Do
tego czasu ta sekcja jest uczciwą granicą reguły.

## Dlaczego dwa tryby (zmierzone, 2026-08-10)

Pierwotna reguła jednotrybowa była protezą ery przed-pomiarowej: bez
verifierów izolacja była miejscem, gdzie niefalsyfikowalne deklaracje
ukrywały się do merge'a — doktryna wymuszała więc prawdę **przez bliskość**
(wszyscy widzą wszystko natychmiast). Trzy rzeczy dojrzały i zostały
zmierzone na dispatchu stt-live-first-v2:

1. **Prawda przez pomiar zastąpiła prawdę przez bliskość.** Zacommitowane
   wcześniej testy RED + verify supervisora uczyniły deklarację odizolowanego
   workera falsyfikowalną przed merge'em — izolacja przestała być kryjówką.
2. **Koszt współbieżnego pisania przekroczył próg.** Przy 3+ agentach
   piszących w pliki-huby narzut koordynacji Living Tree (wyścigi
   stash/restore w hookach, częściowe nadpisania, ceremonie quiet-window)
   rośnie szybciej niż liniowo i przewyższa koszt planowanej izolacji.
3. **Istnieje rola integratora.** Worktree bez właściciela gniją w cmentarz
   osieroconych gałęzi; worktree z jednowątkowym integratorem są linią
   montażową.

Vibecrafting nadal optymalizuje pod szybkie zbieganie do prawdy runtime'u.
Tryb B nie jest odwrotem od tego — to ta sama konwergencja w skali floty, z
verifierem jako granicą prawdy zamiast współdzielonego katalogu roboczego.

Domyślne nawyki z danych treningowych dotyczące worktree pozostają
podporządkowane tej doktrynie w obu kierunkach: żadnego odruchowego worktree
w Trybie A, żadnego odruchowego męczeństwa jednego drzewa tam, gdzie warunki
Trybu B są spełnione.

## Pre-handoff baseline

Koordynacja Living Tree wymaga zmierzonego przekazania pałeczki. Zanim jeden
agent przekaże pracę innemu agentowi, następnej fazie skilla albo recovery
dispatchowi, musi uchwycić pre-handoff baseline:

- branch i SHA `HEAD`
- `git status --short`
- pliki zmienione przez ten segment
- uruchomione komendy weryfikacyjne wraz z wynikiem
- znane awarie, niezweryfikowane powierzchnie i luki runtime
- bieżąca intencja, ogrodzenie scope'u oraz dokładna następna instrukcja albo
  ścieżka raportu

Agent przejmujący wykonuje handoff intake przed edycją:

1. Przeczytaj pre-handoff baseline.
2. Przeczytaj ponownie żywy stan repo.
3. Porównaj drift między baseline'em a bieżącym drzewem.
4. Kontynuuj tylko jeśli scope nadal się trzyma; inaczej zgłoś substrate failure.

No handoff without baseline. Bez tego checkpointu atrybucja regresji jest
zgadywaniem.

## Evidence checkpoints to nie ceremonia

`vc-init`, re-read-before-edit, pre-change baseline, bramki, raporty i
pre-handoff baseline to granice atrybucji regresji. Pomijanie ich nie jest
efektywnością; to regression laundering. Późniejsza awaria musi dać się
przypisać do segmentu lifecycle, a nie rozmazać po "jakiś agent coś zrobił".

## Helper ochrony przed wyścigiem (dodany 2026-05-12, Plan 07)

Living Tree dyscyplinuje pracę równoległą, ale sam z siebie nie sprawia, że
`git commit --only path1 path2` jest atomowy względem jednoczesnego commita innego agenta na tym samym branchu. Doktryna
2026-04-16/17 uchwyciła dokładny tryb awarii:
przy współbieżnej aktywności komunikat commita jednego agenta może wylądować pod kopertą drzewa innego agenta.

Plan 07 dostarcza reużywalny prymityw, który wykrywa ten wyścig po fakcie i odmawia cichego zaakceptowania
niebezpiecznego commita.

**Punkt wejścia dla operatora**:

```
make commit-safe MSG="<commit message>" FILES="path1 path2 ..."
```

**Bezpośrednie wywołanie z shella**:

```
scripts/lib/living-tree-commit.sh "<commit message>" -- path1 path2 ...
```

Helper przechwytuje przed startem `HEAD`, stage'uje tylko nazwane pliki, robi snapshot stage'owanego drzewa, a następnie
commituje. Po commicie sprawdza krzyżowo trzy inwarianty:

1. Rodzic nowego commita równa się przed-startowemu `HEAD` (żaden współbieżny commit nie wśliznął się przez aktualizację
   refa).
2. Drzewo nowego commita zgadza się z fingerprintem stage'owanego drzewa (żadna obca mutacja indeksu nie wjechała razem
   z commitem).
3. Zbiór plików zmienionych przez commit zgadza się dokładnie ze snapshotem stage'owanych plików (żadnych obcych plików
   w kopercie).

Przy wyścigu helper wypisuje oba SHA commitów plus listę obcych plików, proponuje dwie sterowane przez operatora opcje
naprawcze i kończy się kodem niezerowym. **Nie**
robi auto-amend, auto-reset ani auto-rebase. Naprawa jest celowo sterowana przez operatora, spójnie z resztą tej reguły.

Helper egzekwuje istniejącą regułę bezpieczeństwa przeciw stage'owaniu z wildcardem:
argumenty w stylu `.`, `-A`, `--all`, `-a` są odrzucane. Nazwij pliki.

Weryfikacja:

```
make test-race-protection
```

Zestaw testów w `tests/race_protection_test.sh` przerabia zarówno ścieżkę czystego commita, jak i dwa syntetyczne
wstrzyknięcia wyścigu (współbieżna aktualizacja refa oraz mutacja obcego indeksu).

## Domknięcie ograniczeń helpera z Planu 07-b (2026-05-12)

Pierwsza wersja Planu 07 dostarczyła detektor wyścigu z dwoma znanymi ograniczeniami, które potwierdzono w czterech
kolejnych rundach marbles. Plan 07-b domyka oba. Odsyłacze: raporty marbles dla Planu 04 (Cut D), Planu 03 (Cut F) i
Planu 06 (Cut H)
dokumentują false-positive'y, które wywołały tę pracę; raport Planu 07-b pod
`.vibecrafted/reports/marbles/2026_0512/plan-07b-helper-limitations-fix.md`
zawiera dowody domknięcia.

### Ograniczenie #1 — false-positive z hooka pre-commit (3 potwierdzenia)

Repozytoryjny `scripts/hooks/pre-commit` uruchamia `prettier --write`, a po nim
`git add` na plikach `.md`/`.yaml`. Dzieje się to PO snapshocie `git write-tree`
helpera, ale PRZED zapieczętowaniem finalnego drzewa commita. Pierwotny detektor oparty na hashu drzewa potykał się o
kosmetyczną zmianę treści i raportował wyścig, mimo że commit był poprawny. Operatorzy widzieli kod wyjścia 3 +
diagnostykę „RACE DETECTED" na całkowicie legalnych commitach.

**Poprawka**: sam niezgodny hash drzewa nie jest już sygnałem wyścigu. Detektor wyścigu traktuje teraz trzy prymitywy
asymetrycznie:

- **Przesunięcie HEAD** — twardy sygnał wyścigu (inny commit wylądował przez aktualizację refa).
- **Obce pliki** — twardy sygnał wyścigu (dodatkowe pliki w kopercie commita).
- **Niezgodny hash drzewa** — informacyjny. Przyczynia się do diagnozy wyścigu tylko wtedy, gdy odpala też któryś z
  twardych sygnałów. Przy czystym HEAD i zgodnym zbiorze plików helper emituje teraz `notice — pre-commit hooks
rewrote content; not a race` i kończy się kodem 0.

**Trade-off**: hipotetyczny wyścig, który mutuje WYŁĄCZNIE treść stage'owanych plików (bez dodawania/usuwania plików i
bez przesunięcia HEAD), prześliznąłby się teraz. Akceptujemy to — żaden taki wyścig nie został zaobserwowany w czterech
rundach planu, a pierwotny incydent z kroniki 2026-04-16/17 jest łapany przez detektor obcych plików (który pozostaje
rygorystyczny).

### Ograniczenie #2 — cytowanie wieloliniowego MSG (1 potwierdzenie w Planie 06)

`make commit-safe MSG="..."` zawodził na wieloliniowych treściach komunikatu z powodu ścierania się escape'owania `$$` w
Makefile z ekspansją shella. Plan 06 obszedł to, wywołując `scripts/lib/living-tree-commit.sh` bezpośrednio z
heredokiem.

**Poprawka**: helper przyjmuje teraz `--message-file <path>` jako alternatywę dla pozycyjnego argumentu z komunikatem.
Target Makefile zyskuje parametr
`MSG_FILE=<path>`, który się na to mapuje. Oba tryby wywołania działają; w obrębie jednego wywołania wykluczają się
wzajemnie.

**Użycie wieloliniowe**:

```
cat >/tmp/commit.msg <<'EOF'
plan-XX subject line

Body paragraph one with "quotes" and $shell-style references intact.

- bullet one
- bullet two
EOF

make commit-safe MSG_FILE=/tmp/commit.msg FILES="path1 path2"
```

**Bezpośrednio z shella**:

```
scripts/lib/living-tree-commit.sh --message-file /tmp/commit.msg -- path1 path2
```

Jednoliniowy `MSG="..."` działa dalej bez zmian. Ścieżki fallbackowe Planów 04/03/06, które wywoływały helper
bezpośrednio, nie są dotknięte.

### Weryfikacja

Rozszerzony `tests/race_protection_test.sh` dodaje dwa przypadki pozytywne:

- `[positive-C]` symuluje hook pre-commit, który w stylu prettiera przepisuje stage'owaną treść `.md`. Helper musi
  zakończyć się kodem 0 i wyemitować notice o modyfikacji przez hook.
- `[positive-D]` przerabia `--message-file` z treścią zawierającą osadzone znaki nowej linii, pojedyncze/podwójne
  cudzysłowy, referencje `$shell` oraz backticki. Wszystko zachowane verbatim w zacommitowanej treści.

Dotychczasowe 10 asercji (czysty commit + 2 wstrzyknięcia wyścigu) zachowane.
