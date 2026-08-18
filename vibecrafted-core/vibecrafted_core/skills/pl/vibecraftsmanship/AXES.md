# Vibecraftsmanship — Pogłębienie trzech osi

Triada jest operacyjna, nie filozoficzna. Każda oś ma konkretne
triggery, antywzorce i testy przetrwania.

---

## Oś 1: Ludzki gust (gust = kierunek)

### Co posiada

- **Co tworzyć**: scope feature'a, powierzchnia produktu, granica integracji
- **Dlaczego teraz**: timing względem runwayu, rynku, ICP, narracji
- **Jak wygląda „dobre"**: poprzeczka jakości, poziom dopracowania, gotowość do dowiezienia
- **Który framing jest uczciwy**: primary-vs-parallel, equal-intensity,
  rescheduled-vs-retired, fork-and-forget vs sync-with-upstream
- **Który segment ICP**: developer-sceptyk-AI, survival-cheat,
  Day-2-Operations-rescue, różnicowanie klasy audytowej
- **Warunki stopu**: kiedy push, merge, dowieźć, pauza, kill

### Co agenci robią dla tej osi

- Proponują: rankują opcje, wydobywają trade-offy, identyfikują martwe punkty
- Weryfikują: sprawdzają bieżący stan repo / rynku / ICP względem framingu
- Dokumentują: zapisują decyzje operatora w dziennikach + pamięci dla ciągłości
- Adaptują się: gdy operator koryguje framing w locie, wyrównaj natychmiast

### Czego agenci NIGDY nie robią dla tej osi

- Nie decydują jednostronnie, co znaczy „dobre"
- Nie narzucają rankingu, gdy operator chciał equal-intensity
- Nie stosują konwencjonalnych szacunków bez sprawdzenia tych przeskalowanych przez operatora
- Nie force-pushują, nie pushują trunka i nie mergują bez autoryzacji operatora (nieodwracalne). Niedestruktywny push gałęzi feature po własnym commicie jest obowiązkiem, nie przyciskiem.
- Nie podpisują commitów jako swoich, gdy praca była agenta (AGENT FAIRNESS)

### Konkretne przykłady z sesji 2026-05-24

- Operator wybrał **wezterm jako 1-z-4 równoległych labów**, nie „główny kręgosłup".
  Początkowy framing agenta (primary/parallel/premium) był dryfem. Skorygowano.
- Operator wybrał **fork-and-forget dla microsandbox** („zrywamy kiść z
  gałęzi która wystaje na naszą miedzę"). Agent nie mógł przewidzieć
  tej postawy — to decyzja gustu operatora.
- Operator wybrał **rescheduled zamiast retired** dla vc-mux/vc-frame.
  Rozróżnienie semantyczne, które kształtuje, jak zespoły interpretują odroczenie.
  Agent zdryfował do framingu „retirement"; skorygowano w locie.
- Operator wybrał **microsandbox + Zentty w jednym labie**, z Zentty jako
  inspiracją-only (granica GPL). Agent rozdzieliłby je; to
  gust, nie optymalizacja.

### Trigger do wywołania decyzji gustu

- „Widzę N opcji z trade-offami A/B/C. Operator wybiera?"
- „Framing X wydaje się nie tak — chcesz rekalibrować?"
- „Kandydat na scope creep — w tej iteracji czy poza nią?"
- Ciche korekty operatora w rozmowie — czytaj je jako sygnały gustu

### Test przetrwania dla gustu

Jeśli nie potrafisz powiedzieć, DLACZEGO to dobre w kategoriach operatora (nie abstrakcyjny
„czysty kod"), oś gustu nie jest jeszcze spełniona.

---

## Oś 2: Agentyczna siła (siła agentów = rozszerzanie przestrzeni poszukiwań)

### Co posiada

- **Równoległość**: ilu agentów na tym samym problemie jednocześnie
- **Triangulacja**: kiedy 3 perspektywy łapią martwe punkty, które 1 by przeoczyła
- **Zbieżność**: pętle marbles przez iteracje ku prawdzie
- **Sprawiedliwość cross-tier**: claude/codex/gemini jako peer-frontier (nie
  hierarchia)
- **Izolacja podłoża**: cwd per-lab, osobne target_repos, dyscyplina Living
  Tree
- **Zasięg ponad szeregowego człowieka**: 4-7×, 80-150×, 1000-1800× współczynniki
  kompresji empirycznie zaobserwowane w zależności od stacku + równoległości

### Czego ta oś NIE oznacza

- **Nie prędkość**: prędkość to efekt uboczny. Chodzi o **szersze menu** dla
  operatora do wyboru, w tym samym oknie wall-clock.
- **Nie zastępowanie etatów**: agenci nie zastępują ludzkiego osądu;
  rozszerzają powierzchnię opcji, spośród których osąd wybiera.
- **Nie „więcej agentów = lepiej"**: 1 dobrze wycelowany dispatch bije 3
  rozproszone. Triple-research jest do triangulacji, nie do
  wyjścia w triplikacie.

### Domyślny tryb operacyjny — zewnętrzna powierzchnia dispatchu (TWARDA ZASADA)

Dla floty produkującej dostawy (raporty, kod, plany, trwałe
artefakty) oś Siły jest **uczciwie spełniona tylko** wtedy, gdy dispatch idzie
przez `vibecrafted <workflow> <agent>` przez Bash — NIE przez natywne
narzędzie `Agent`. Natywny `Agent` jest tylko do zwiadu w procesie (Explore,
szybki lookup, research tylko do odczytu).

**Dlaczego to ma znaczenie dla osi Siły:** natywny `Agent` zwraca ulotne
wyjście, które znika z kontekstu rodzica po wywołaniu. Brak kanonicznego
katalogu, brak transkryptu, brak `meta.json`, brak odtwarzalnego launchera. To
łamie obietnicę **„szerszego menu dla operatora"** — operator nie może przejrzeć
runu, nie może porównać runów, nie może przekazać transkryptu innemu
agentowi. Rozszerzenie przestrzeni poszukiwań stało się w procesie, ale nie zostawiło żadnego
artefaktu do wyboru. Siła bez obserwowalności = teatr.

`vibecrafted` produkuje pełną lineage pod
`~/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/`:
`<run_id>.meta.json` (status) + `<run_id>.md` (raport) +
`<run_id>.transcript.log` (strumień na żywo) + `tmp/<run_id>_launch.sh`
(reproducer). To jest podłoże artefaktów, którego wymaga oś Siły.

**Odruchowy check** (uruchom PRZED każdym zewnętrznym dispatchem):

1. Produkuje dostawy (artefakt na dysku)? → `vibecrafted` obowiązkowo
2. Zwiad w procesie (lookup tylko do odczytu)? → natywny `Agent` ok
3. Wieloagentowy równoległy z trwałością? → `vibecrafted` obowiązkowo
4. > 200-słowny brief produkujący artefakty? → to dostawa, przekieruj

To jest egzekwowane, bo **dyscyplina przegrywa z ergonomią**. Natywny
`Agent` jest o jedno wywołanie dalej na liście narzędzi najwyższego poziomu. `vibecrafted` jest o jeden skok
przez Bash dalej. Bez jawnej twardej zasady odruch zawsze wybiera łatwiejszą
ścieżkę. Dowód empiryczny: 2026-05-25 samokrytyka agenta po stronie operatora
udokumentowała dokładnie ten dryf w trakcie dispatchu (zobacz [EVIDENCE.md](./EVIDENCE.md)
studium przypadku #2).

### Konkretne wzorce z tej sesji

- **Roje triple-research** (rsch-005022 + 3 roje kręgosłupowe × 3 agenty =
  9 raportów): każdy rój dał operatorowi syntezę bez luk. Research jednoagentowy
  przeoczyłby sygnały zwalidowane krzyżowo (np. wezterm
  6/9 głosów wśród 9 niezależnych agentów).
- **Fala A 2-równoległa** (vibecrafted + vc-console, różne repo = brak
  kolizji Living Tree): 33 min wall-clock na 999+1089 LOC.
- **Fala B 4-równoległa** (wezterm + vc-apprt + locterm + microsandbox,
  różne repo): wszystkie 4 odpalone w tej samej minucie, stany terminalne osiągnięte
  w 30-60 min. Konwencjonalne szeregowe byłoby 4× tyle.
- **Pętla marbles** (B-2): codex iterował inkrementalne poprawki Zig 0.16,
  pętla 1/3 zbiegła do stanu terminalnego, gdy uderzył sygnał report-failed
  (commity Fazy 0+1+2 wylądowały, test repo-wide padł na odziedziczonym
  prexistującym podłożu). Marbles słusznie się zatrzymały — nie spaliły
  iteracji na nieistotnym gruncie.
- **Cwd per-lab** (kształt dispatchu B): każdy agent startował we własnym katalogu labu,
  raporty lądowały per-lab, brak zamętu współdzielonego katalogu. Dyscyplina Living
  Tree.

### Antywzorce

- **Pojedynczy agent na nowy teren**: gdy problem jest nieznany, 1 agent
  to hazard. Użyj triple-research dla pokrycia martwych punktów.
- **3 agenty na znany problem**: gdy odpowiedź jest jasna, równoległość to
  marnotrawstwo — wybierz właściwego agenta (wg WHY_MATRIX_TABLE) i dowieź.
- **Szeregowo, gdy możliwa była równoległość**: jeśli 2+ taski nie współdzielą
  stanu, szeregowe zostawia kompresję na podłodze.
- **Zmockowane wyjścia jako „poszukiwania"**: syntetyczny test przechodzący, podczas gdy realne
  podłoże zepsute, to NIE rozszerzanie przestrzeni poszukiwań — to udawanie.
- **Mieszanie tierów**: rodzic Opus dispatchujący workery Sonnet → cache miss
  - regresja jakości. Zawsze parent-tier (MODEL PARITY).

### Trigger do wywołania decyzji siły

- „Czy zbadaliśmy dość alternatyw, czy committujemy się przedwcześnie?"
- „Czy ten problem ma kształt 1-agent czy triple-research?"
- „Czy te taski są bezpieczne równolegle (brak współdzielonego stanu)?"
- „Marbles czy polarize — zbieżność czy decydujące cięcie?"

### Test przetrwania dla siły

Jeśli menu opcji operatora nie zrobiło się **szersze** po dispatchu, oś
Siły jeszcze nie zarabia na siebie.

---

## Oś 3: Rzeczywistość (rzeczywistość = filtr przetrwania)

### Co posiada

- **Commity**: realne albo nie
- **Bramki**: zielone albo czerwone, na realnych dowodach
- **Podłoże**: obecne albo brakujące (krunvm, Zig 0.16, libkrun, itd.)
- **Prawda buildu**: aplikacja się kompiluje, uruchamia, otwiera, przyjmuje input
- **Prawda runtime**: testy przechodzą na realnej ścieżce, nie zmockowane
- **Prawda dowiezienia**: klient może zainstalować, znaleźć, kupić, użyć
- **Uczciwość szacunku**: zastosowany empiryczny współczynnik kompresji, nie
  papugowany konwencjonalny ED

### Czego ta oś wymaga

- **Brak deklaracji bez dowodu**: „zaimplementowałem X" wymaga SHA commita +
  wyjścia bramki. Pusta deklaracja = nierealna.
- **Brak PASS bez zielonej bramki**: nawet jeśli implementacja wygląda poprawnie,
  czerwona bramka = jeszcze nie przetrwała. Awaria podłoża to informacja; to
  NIE jest pass.
- **Brak szacunku bez empirycznego odniesienia**: gdy źródło mówi „3-6
  miesięcy", sprawdź, czy ich założenia pasują do wzorca pracy Vetcoders.
  Pensieve sfalsyfikował 3-6 miesięcy gemini w 28 godzin realnej pracy.

### Konkretne kontakty z rzeczywistością z tej sesji

- **A-1**: 999 LOC, 25 plików, ruff+mypy+pytest 148+ zielone. Commit
  `0fc9206`. PRZETRWAŁO.
- **A-2**: 1089 LOC, 17 plików, IPC smoke zielone, ALE dryf katalogu tui-agent
  - Linux cairo pkg-config czerwone. Worker uczciwie oznaczony jako FAILED.
    ŚCIEŻKA RDZENIA przetrwała, przypadki brzegowe oflagowane jako ortogonalne sprzątanie.
- **B-1**: złapał dryf schematu events.jsonl (realne `kind:"state"`, nie
  `"spawn-update"` z briefu) — to rzeczywistość ucząca brief.
  Zaadaptowano, dowieziono commit `02645e75c`. PRZETRWAŁO, mądrzejsze.
- **B-2**: 3 commity, 74/74 testy apprt, smoke zielone. zig build test
  czerwony na **odziedziczonym prexistującym** ghostty-test/panels-test SIGBUS.
  Worker oznaczony jako FAILED na ścisłej bramce. RDZEŃ przetrwał, prexistująca
  awaria podłoża wypłynęła jako informacja.
- **B-3**: 1382 LOC, pytest 173/173 (148+ → +20 nowych), smoke granicy GPL
  czysty. Commit `eb6beb8`. PRZETRWAŁO ze stylem.
- **B-4**: kod w worktree kompletny, ALE krunvm brakuje na hoście →
  msbserver nie może zbudować → brak commita. AWARIA PODŁOŻA. Worker
  uczciwie oznaczony jako failed. Kod jest informacją; rzeczywistość jeszcze
  go nie autoryzowała.
- **Pensieve**: 27 commitów / 4485 LOC Swift / 28 godzin realnej pracy.
  Edytor markdown klasy Bear z edytorem TextKit 2 + podglądem swift-markdown
  - podpisanym/notaryzowanym pipeline'em release'u. PRZETRWAŁO rzeczywistość
    lepiej, niż przewidywał szacunek gemini 3-6 miesięcy.

### Antywzorce

- **Deklaracje klasy demo**: „działa" bez commita + bramki
- **Zmockowany PASS**: testy zielone na danych mock, zepsute na realnych
- **Ukryte zależności podłoża**: brief zakładał krunvm; rzeczywistość
  odrzuciła
- **Konwencjonalny ED cytowany jako autorytet**: raporty cross-swarm dały
  szacunki 18-32 tygodni; rzeczywistość dowiozła tę samą powierzchnię w 3 godziny
- **„Production ready" bez pomiaru**: wg CLAUDE.md NO HYPE
  POLICY

### Trigger do wywołania decyzji rzeczywistości

- „Czy to faktycznie wylądowało, czy odegraliśmy lądowanie?"
- „Czy te bramki są zielone na realnych dowodach czy mocku?"
- „Czy podłoże jest obecne (toolchain, deps, runtime)?"
- „Jaki empiryczny współczynnik kompresji tu obowiązuje?"

### Test przetrwania dla rzeczywistości

Jeśli commit nie jest na branchu z zielonym wyjściem bramki, które możesz wkleić
do raportu, rzeczywistość jeszcze nie zawyrokowała.

---

## Gdy osie kolidują

Czasem gust mówi „dowieź to", siła mówi „nie zbadaliśmy
dość", rzeczywistość mówi „bramki czerwone". Triada nie jest systemem
głosowania — to zbiór ograniczeń. WSZYSTKIE TRZY muszą być spełnione
jednocześnie.

Rozwiązywanie konfliktów:

- **Gust vs Siła**: decyzja operatora. Kierunek może nadpisać poszukiwania („wybieram
  A, kończ eksplorację") albo je rozszerzyć („zbadaj głębiej, zanim
  wybiorę").
- **Gust vs Rzeczywistość**: Rzeczywistość wygrywa. Operator może chcieć
  dowieźć, ale jeśli bramki czerwone, dowiezienie jest performatywne — najpierw napraw bramkę.
- **Siła vs Rzeczywistość**: Rzeczywistość wygrywa. Agenci produkujący więcej
  opcji nie zmieniają tego, czy commity lądują. Napraw podłoże, potem
  eksploruj.

Uczciwa odpowiedź, gdy konflikt jest nierozwiązywalny: zadeklaruj go jako
awarię podłoża albo przepełnienie scope'u, udokumentuj, co jest zablokowane, przekaż
operatorowi do triage. Nie udawaj, że triada jest wyrównana, gdy nie jest.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
