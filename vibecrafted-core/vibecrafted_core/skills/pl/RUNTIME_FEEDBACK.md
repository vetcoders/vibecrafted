---
title: Runtime Feedback Ledger
kind: doctrine_feedback
version: 1.0.0
description: "Per-command correction ledger: every faulty execution becomes an actionable feedback message with the correct usage and on-disk evidence."
scope: framework
status: active
---

# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runtime Feedback Ledger

> Każde niepoprawne wykonanie to punkt danych. Ten ledger zamienia tarcie w
> per-komendowe prompty korekcyjne, żeby żaden agent nie powtórzył błędu, za
> który flota już zapłaciła. Czytaj go tak, jak czytasz
> [Matrycę Delegacji](DELEGATION_MATRIX.md): jako doktrynę, nie jako historię.

## Model wiadomości feedbackowej

Każdy wpis jest kluczowany przez **komendę lub akcję**, która została wykonana
niepoprawnie, i niesie dokładnie trzy pola:

- **❌ Zaobserwowane** — co się faktycznie stało (kontekst + wynik).
- **✅ Poprawnie** — poprawne użycie komendy albo zachowanie, gotowe do
  skopiowania.
- **Evidence** — data, run_id/commit, ścieżka artefaktu. Bez evidence nie ma
  wpisu.

Cykl życia: incydent → wpis tutaj (tego samego dnia) → gdy wzorzec się powtarza
albo fix należy do kodu, promuj go do fixa w runtimie, hooka albo klauzuli w
skillu i podlinkuj promocję. Wpis bez ścieżki promocji po 3 nawrotach to
porażka procesu. Wpisy się dopisuje, nigdy po cichu nie przepisuje; wpisy
zastąpione dostają linię `Promoted:`, nie kasowanie.

---

## Ledger

### `vibecrafted <launcher> <agent>` (re-dispatch po martwym runie)

- **❌ Zaobserwowane:** Gdy worker umarł w trakcie runu, wystawiono świeży
  dispatch `workflow` (dwukrotnie). Zimny kontekst: następca czyta repo od nowa,
  wyprowadza plan od nowa i może umrzeć na tym samym zimnym starcie. Jeden
  następca umarł w ~2 min.
- **✅ Poprawnie:** Pierwszy ruch to `vibecrafted resume <agent> --session
<agent_session_id> --prompt "<what happened + tree delta + what to finish>"`.
  Martwa sesja trzyma pełny kontekst roboczy. Świeży dispatch dopiero wtedy, gdy
  sesja jest nie do odzyskania.
- **Evidence:** 2026-07-25, repo aicx: zimne recovery `work-260725-115446-86000`
  (umarło w ~2 min) vs wznowiona sesja `019f9894…` → dostarczony commit
  `3b7d670`.

### `vibecrafted resume <agent> --session <id>` (tożsamość sesji)

- **❌ Zaobserwowane:** `session_id` wzięto z rekordu control plane
  (`~/.vibecrafted/control_plane/runs/<run_id>.json`). To jest id sesji
  **vibecrafted**, nie agenta — resume odpowiedział
  `404 Not Found, restoring from remote` i umarł na 283 bajtach streamu.
- **✅ Poprawnie:** Użyj id natywnego dla agenta: grok → `ls -t
~/.grok/sessions/<url-encoded-cwd>/` (katalogi uuidv7; dopasuj mtime do czasu
  śmierci), claude → `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`; dla
  zakończonych runów id jest we frontmatterze raportu. Zweryfikuj format per
  agent przed launchem.
- **Evidence:** 2026-07-25 11:57 stream `resume/grok-20260725-115749.stream.jsonl`
  (404) vs resume o 11:59 sesji `019f9894-c578-7800-8c5a-dd76f004dc8c`
  (zadziałał).

### `vibecrafted <agent> await --run-id …` (zaufanie zielonemu)

- **❌ Zaobserwowane:** Await zwrócił `completed / rc=0 / report_delivered` na
  **nietkniętym szablonie launchera** — launcher zasiewa plik raportu przy
  spawnie, a `_report_file_written` akceptował dowolny niepusty plik. Cicha
  fałszywa zieleń na martwych workerach.
- **✅ Poprawnie:** Nigdy nie traktuj samego `await_rc=0` jako dostawy.
  Zweryfikuj frontmatter raportu: `head -8 <report> | grep 'finalized: true'`
  plus niepuste `claim`. Naprawione w runtimie przez `068428bc` (szablon ≠
  dostawa, `finalized` poświadcza stan terminalny); check frontmattera po
  stronie supervisora zostaje jako pas i szelki.
- **Evidence:** 2026-07-25 run `work-260725-112151-88000`
  (`await_outcome: completed` na szablonie); fix `068428bc`, 52/52
  `test_control_plane.py`.

### Ogłaszanie runu martwym (trigger recovery)

- **❌ Zaobserwowane:** Martwy `launcher_pid` + milczący transcript vibecrafted
  odczytano jako „worker martwy". Trzy kontrprzykłady tego samego dnia: workerzy
  przeżyli swoje launchery, pracowali dalej na ślepo wobec control plane, a
  jeden dostarczył pełny commit _po_ ogłoszeniu jego runu zatrzymanym.
- **✅ Poprawnie:** Przed recovery sprawdź **wszystkie trzy**: (1) `worker_pid`
  przez `kill -0`, (2) mtime pliku sesji **natywnego dla agenta** (nie pipe'a
  launchera), (3) postęp niezacommitowanego diffa w repo docelowym. Śmierć
  launchera przy żywym workerze znaczy _obserwuj, nie redispatchuj_ — drugi
  worker na tym samym drzewie to kolizja Living Tree.
- **Evidence:** 2026-07-25: worker claude 59360 żywy → commit `068428bc` po
  śmierci launchera 58811; worker runu „stopped" dostarczył `3b7d670`.

### Skrypty watch supervisora (monitory wokół runów)

- **❌ Zaobserwowane:** Dwie fałszywe notyfikacje „FINALIZED" z niechlujnych
  jednolinijkowców watch: `ls | head -1` (alfabetycznie, zły plik) i `grep -q
'finalized: true'` matchujący **cytat w treści raportu** zamiast frontmattera.
- **✅ Poprawnie:** Zakotwicz checki frontmattera w `head -8`; najnowsze pliki
  wybieraj przez `ls -t`; obserwuj plik transcriptu/sesji natywny dla agenta,
  nie stream launchera; pokryj każdy stan terminalny (sukces _i_ śmierć), nie
  tylko happy path.
- **Evidence:** 2026-07-25 monitory `bgilttlr3`/`byb31gua4` (fałszywe) vs
  `b1dyhpsyk` (poprawny wzorzec).
- **Nawrót #3, tego samego dnia** (`bzouou4nf`): watch zbaselinował `git HEAD`,
  żeby wykryć commit workera, a potem **sam supervisor zacommitował** — jego
  własny commit odpalił watch jako commit workera. Kotwicz watche na commity w
  tożsamości, którą worker posiada (`git log BASE..HEAD | grep '\[<agent>/'`),
  nigdy w „HEAD się ruszył". Osiągnięto trzy nawroty: wg cyklu życia powyżej to
  jest teraz **do promocji** — wspólny, otestowany helper watch w runtimie
  zamiast jednolinijkowca improwizowanego na nowo przy każdym dispatchu. Zanim
  to wyląduje, kopiuj poprawioną formę z tego wpisu, zamiast pisać świeżą.

### Root dispatchu (`--file`/`--prompt` z niewłaściwego katalogu)

- **❌ Zaobserwowane:** Dispatch z fixem wycelowano w pusty checkout-zaślepkę
  (`vetcoders/loctree-suite`) zamiast w żywe repo (`loctree/loctree-suite`);
  workera trzeba było przekierować w trakcie runu.
- **✅ Poprawnie:** Przed dispatchem: `git -C <root> log --oneline -3` (czy
  historia zgadza się z pracą, którą cytujesz?) i launchuj z tego roota repo, z
  którego pochodzi evidence. Receipt launchu wypisuje `root:` — przeczytaj go
  przed uzbrojeniem await.
- **Evidence:** 2026-07-25 rano, mis-dispatch loctree-suite (przed
  kompaktowaniem).

### Budowanie w żywym repo z aktywnym dispatchem

- **❌ Zaobserwowane:** Supervisor odpalił `cargo build` w repo, w którym worker
  był w trakcie runu: wspólny lock na `target/` + mieszane stany drzewa ścigają
  się z workerem.
- **✅ Poprawnie:** Nigdy nie buduj ani nie testuj w drzewie, które worker
  aktywnie mutuje. Weryfikuj na odizolowanym klonie albo poczekaj na stan
  terminalny workera. Gdy worker umrze w trakcie builda, jego `target/` jest
  rozgrzany — powiedz następcy, żeby go zachował.
- **Evidence:** 2026-07-25, build ścigający się z `work-260725-103320-02000`,
  zabity; użyto zamiast tego odizolowanego klona.

### Awaria providera przy launchu (5xx z linii agenta)

- **❌ Zaobserwowane:** Linia codex zwróciła 503 (`circuit_open`) przy spawnie.
- **✅ Poprawnie:** To awaria usługi, nie promptu: natychmiast prześlij ten sam
  brief na inną linię; sygnatura commita staje się sygnaturą agenta
  **wykonującego** (`Authored-By: <actual-agent>`), zgodnie z matrycą.
- **Evidence:** 2026-07-25 codex 503 → claude wziął cięcie ze stall-detectorem →
  `068428bc`.

### Treść briefu recovery (addendum do speca)

- **❌ Zaobserwowane:** (near-miss) Następca mógł wyrzucić 563 niezacommitowane
  linie, które poprzednik napisał przed śmiercią, albo zacommitować release'ową
  pracę równoległego agenta.
- **✅ Poprawnie:** Każdy brief recovery niesie **addendum o stanie drzewa**: co
  poprzednik zostawił niezacommitowane (_adoptuj, nie wyrzucaj_), które dirty
  pliki należą do innych agentów (_omijaj_), których bramek jeszcze nie
  uruchomiono. Pierwszy ruch następcy to `git diff` na wymienionych plikach.
- **Evidence:** 2026-07-25 ADDENDUM 2 w `specs/loctree-literal-boost.md` →
  następca zaadoptował diff → `cf9d7b62` z czystymi bramkami.

### `aicx intents` w skali (budżet kontekstu supervisora)

- **❌ Zaobserwowane:** Surowy pull `intents` (133 KB / 1000+ linii) wylądował we
  własnym kontekście supervisora podczas audytu.
- **✅ Poprawnie:** Przy retrievalu w skali audytu deleguj redukcję do subagenta
  czytającego utrwalony plik wyniku; supervisor konsumuje bounded listę
  kandydatów. Najpierw użyj `--slim --collapse-session` i dokładnej tożsamości
  projektu.
- **Evidence:** 2026-07-25 audyt vc-intents na codescribe (sweep dwoma
  subagentami).

### Ogłaszanie fixa dostarczonym (dryf źródło ↔ zainstalowany artefakt)

- **❌ Zaobserwowane:** Fix się commituje, bramki idą na zielono, raport mówi
  „dostarczone" — a operator nadal widzi zepsute zachowanie, bo to, co
  uruchamia, jest **zainstalowanym artefaktem**, nie checkoutem. Trzy niezależne
  przypadki jednego dnia: liczniki statusu vc-frame (fix `5c99f72d` w źródle,
  zainstalowana binarka zbudowana z `82ff8f27`); `scaffold-doctor` (walidator
  zacommitowany, zainstalowane CLI odpowiada _„not in the command deck"_, a
  nawet in-repo wrapper decka nie potrafi zlokalizować świeżo zbudowanej
  binarki); semantyka await w control plane (fix `068428bc` w repo, workerzy
  wykonujący `~/.local/share/vibecrafted/tools/vibecrafted-3.6.0+g560310a9/`).
  Czwarty odczyt przyszedł niezależnie z audytu codex: _każde z czterech repo
  floty_ niesie dryf między żywym checkoutem a zainstalowanym artefaktem.
- **✅ Poprawnie:** „Dostarczone" znaczy osiągalne z PATH operatora, nie tylko
  zacommitowane. Zanim ogłosisz dostawę, porównaj to, co się uruchamia, z tym,
  co zbudowano — `<tool> --version` vs `git log -1`, `command -v <tool>` i to,
  gdzie faktycznie wskazuje symlink — i napisz w raporcie wprost, czy operator
  musi przeinstalować, żeby zobaczyć zmianę. Cięcie kończące się na commicie to
  `[?]`, nigdy `[x]`; tylko uruchomienie **zainstalowanej** powierzchni
  zasługuje na `[x]`.
- **Evidence:** 2026-07-25 — vc-frame `0.46.0+g82ff8f27.dirty` vs HEAD
  `5c99f72d`; `vibecrafted scaffold-doctor` nieobecny w zainstalowanym decku,
  podczas gdy `afecda98` siedzi w drzewie; await tego samego dnia zwrócił rc=0
  na 223-bajtowym szablonie launchera, bo działająca dystrybucja jest starsza
  niż `068428bc`.
- **Promoted:** `7fa51c66` (vc-frame) — `zellij-utils/src/install_freshness.rs`
  plus linia `[INSTALL FRESHNESS]` w `setup --check`, wyprowadzona niezależnie
  przez workera z kosztu tego samego dnia: _„commit, który wylądował w źródle,
  ale nie w zainstalowanej binarce, wyglądał dokładnie jak fix, który nie
  działa, i kosztował dziś pełny przebieg triage'u."_ Czyta `.git` bezpośrednio
  (loose ref, packed-refs, detached, wskaźnik worktree), więc nie może się
  zawiesić i nie potrzebuje `git` na maszynie. Wzorzec ma teraz mechanizm;
  zostaw ten wpis jako powód, dla którego istnieje.
- **Ostrożność diagnostyczna (ten sam dzień, nauka po kosztach):**
  stale-install to _uwodzicielskie_ wyjaśnienie — było prawdziwe trzy razy i
  mimo to **nie** było przyczyną źródłową liczników vc-frame. Prawdziwym
  mechanizmem był watchdog idle-exit sprzątający szuflady triage'u, z których
  liczniki czytają (`7fa51c66`). Potwierdź łańcuch przyczynowy w logach, zanim
  zamkniesz sprawę na „po prostu przeinstaluj": fix, który jest jedynie
  prawdopodobny, przenosi buga dalej pod zielonym raportem.

### `grep`/`rg` na roboczych repo (zero-fallback)

- **❌ Zaobserwowane:** Odruchowy grep na repo, w których loctree ma snapshot;
  gdy loctree faktycznie przeoczyło jakąś powierzchnię, luka pozostała
  nieodnotowana.
- **✅ Poprawnie:** `loct find --literal` / loctree-mcp `find` do wszystkiego, co
  ma kształt identyfikatora; grep tylko do literalnego tekstu spoza AST. Gdy
  loctree nie potrafi odpowiedzieć, **dopisz hak** do
  `~/.vibecrafted/loctree/loctree-fail.md` i zejdź na fallback głośno — backlog
  jest kanałem feedbacku.
- **Evidence:** 2026-07-25 audyt zero-fallback: 8/9 grepów to odruch agenta,
  2 realne defekty loctree → oba naprawione tego samego dnia (`eed8b22b`,
  `cf9d7b62`).

### Root dispatchu idzie za cwd supervisora, nie za specem

- **❌ Zaobserwowane:** Spec celujący w `vetcoders/vc-frame` został
  zdispatchowany, gdy shell supervisora nadal siedział w
  `vetcoders/vibecrafted` (cwd przeszło z poprzedniej komendy). Receipt launchu
  związał `root:` — a z nim zamknięcie workera i ścieżkę raportu — z
  **niewłaściwym repo**; worker nie wyedytowałby niczego albo umieściłby każdy
  artefakt w złym miejscu.
- **✅ Poprawnie:** Zrób `cd` do repo docelowego w tej samej komendzie co
  `vibecrafted workflow …`, a potem **przeczytaj linię `root:` z receiptu
  launchu, zanim odejdziesz** — jest drukowana właśnie po to, żeby supervisor
  złapał to w pierwszej sekundzie. Zły root złapany wcześnie jest tani:
  `vibecrafted <agent> stop --run-id <id>` i redispatch bije każdy ratunek w
  locie.
- **Evidence:** 2026-07-25 — run `work-260725-235036-48000` (root
  `vetcoders/vibecrafted`, spec `vc-frame-freshness-identity.md`) zatrzymany po
  ~15 s i przedispatchowany jako `work-260725-235130-39000` z rootem
  `vetcoders/vc-frame`.

---

## Ujawnianie — przez zmysł intencji, nie kolejny banner na starcie sesji

Ten ledger celowo **nie** jest wstrzykiwany jako sugestia na starcie sesji:
start sesji jest już gęsty (vc-init, karta kontekstu loctree, pakiet
living-tree z AICX), a każdy dodatkowy automatyczny prompt rozwadnia
poprzednie. Zamiast tego jedzie kanałem, który każdy agent i tak otwiera:
**zmysł intencji `vc-init`** wskazuje tutaj przed każdym dispatchem floty,
resume czy recovery, a `aicx search -p vetcoders/vibecrafted '<command>'`
sięga tej samej doktryny przez retrieval. Workerzy dostają go zawsze, gdy brief
cytuje wymienioną tu komendę — cytuj wpis, nie cały plik.

## Mechanika zapisu — znana luka

Wpisy powstają dziś wolicjonalnie, w momencie awarii — czyli dokładnie wtedy,
gdy agent jest najbardziej zagoniony, więc wolicjonalny zapis **będzie** gubić
najcenniejsze incydenty. Stan docelowy: zapis na poziomie hooka — nieudana
komenda runtime'u emituje szkielet wpisu (komenda, kontekst, wynik) do
uzupełnienia przez agenta, tak jak `loctree-fail.md` (sprawdzony przodek tego
ledgera) działa w trybie append-on-hak. Zanim to wyląduje, traktuj napisanie
wpisu jako część incydentu, nie jako opcjonalną higienę.

## Kanoniczne referencje

- [Matryca Delegacji](DELEGATION_MATRIX.md) — autorytet wywołania i delegacji
- [Reguła Living Tree](LIVING_TREE_RULE.md) — dyscyplina wspólnego drzewa
  stojąca za wpisami o recovery i buildach
- [`vc-dispatch`](vc-dispatch/SKILL.md) — zewnętrzne linie floty, await/observe
- [`vc-operator`](vc-operator/SKILL.md) — postawa nadzoru wielofalowego
- Backlog haków Loctree: `~/.vibecrafted/loctree/loctree-fail.md` (po stronie
  operatora, append-only)

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

### Verify dispatchu vs runtime'owy `--report` (podwójny kontrakt raportu)

- **❌ Zaobserwowane:** W linii `vibecrafted dispatch` (wave10-engine-diet,
  8 cięć) oba cięcia codex zapisały raport WYŁĄCZNIE pod ścieżką `--report`
  podaną przez runtime (`2026_0730/reports/implement/…`), ignorując
  `report_path` z frontmattera briefu (`2026_0728/reports/<cut>_report.md`).
  Cięcia claude i grok uszanowały brief. Verifier supervisora (`test -f <brief
report_path>`) padł na obu cięciach codex → każde spaliło pełną rundę repair
  (~10 min), której jedyną dostawą było opublikowanie brakującego pliku
  raportu.
- **✅ Poprawnie:** Jeden kontrakt raportu na komórkę, nie dwa. Dopóki warstwa
  dispatchu nie przekazuje `report_path` z briefu jako `--report` komórki (albo
  verifier supervisora nie akceptuje raportu runtime'owego, który i tak zna),
  traktuj to jako zagrożenie linii codex: implementacja zwykle jest dostarczona
  i zacommitowana — sprawdź `git log`, zanim zaczniesz podejrzewać workera, i
  pozwól rundzie repair opublikować raport (idempotentne briefy czynią to
  tanim).
- **Evidence:** 2026-07-30, codescribe wave10-engine-diet: B3
  `impl-260730-044817-30941` (praca `410841ca`, repair opublikował raport w
  `83ab5fa0`), C1 `impl-260730-053220-84226` (praca `68380d9f`, repair
  `impl-260730-055020-09345`). Cięcia claude/grok A2/A1/B2/C2 wszystkie
  zweryfikowane za pierwszym razem.
