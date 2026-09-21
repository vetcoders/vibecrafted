---
name: vc-intents
version: 2.0.0
description: >
  Hunt the Founder's intents for one repository inside a chosen window, settle
  every one of them against the live code with two independent fleets, and hand
  back a stable, dependency-ordered plan of cuts for vc-dispatch. Use whenever
  the team asks "zbierz intencje", "rozlicz intencje", "czego chciał Founder",
  "co obiecaliśmy i nie dowieźliśmy", "co z planu siedzi w kodzie", "what did we
  agree to build", "what is still owed", "intent coverage", "planned vs code",
  "highest truth", "checklist from intents" — or when scattered decisions from
  transcripts and sessions must become an implementation queue that survives
  compactions and machines. Reach for this instead of a bare `aicx search`
  whenever the Founder's own words must be separated from agent proposals and
  agent completion claims, and the answer must end in a plan, not a catalogue.
loctree_value: "structural questions decide whether a promise has a runtime shape"
aicx_value: "indexed intents, session context; one source among several"
dogfooding: "required — this skill is the join between corpus, aicx, Loctree and dispatch"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-intents` (launcher `intents`)** — zobacz
> [Matrycę Delegacji](../DELEGATION_MATRIX.md).
>
> | Ścieżka           | Literał tego skilla                                                                                    |
> | ----------------- | ------------------------------------------------------------------------------------------------------ |
> | 1. Worker z ręki  | `vibecrafted intents <agent>` — jeden tor ekstrakcji albo konfrontacji, READ, zapisuje tylko swój JSON |
> | 2. Interaktywnie  | `/vc-intents` — samo polowanie, **w tej sesji**: sweep, tory, plany, merge, kolejka, render            |
> | 3. Agent-operator | wysyła formę workera przez `vc-dispatch` z planem napisanym przez `scripts/intents_cli.py plan`        |
>
> Kadencja pozostaje **read**: ten skill nigdy nie edytuje repo. Dostarczenie to
> osobny plan `implement`, który pisze dla floty.

<!-- /fleet-imperative -->

# vc-intents — od tego, co powiedział Founder, do planu cięć

Długo żyjący projekt gromadzi intencje szybciej niż kod. Founder mówi coś w
marcu, poprawia w maju, agent proponuje inny kształt w czerwcu i twierdzi w
lipcu, że dowiózł. We wrześniu nikt nie umie powiedzieć, które z tego jeszcze
obowiązuje — a jedyna osoba, która by mogła, płaci za odpowiedź.

Ten skill jest właścicielem całej ścieżki: **zebrać → rozdzielić głosy →
katalog z dosłownymi cytatami → konfrontacja dwiema flotami → reklasyfikacja
tego, co ukrywa „landed" → stabilna kolejka → plan dispatchu, który flota może
odpalić**. `vc-canary` znajduje kolizje prawdy i nigdy nie refaktoruje;
`vc-implement` tnie kod, ale nie odtwarza obietnicy; `vc-audit` falsyfikuje plan
wobec kodu. `vc-intents` odtwarza obietnicę, dowodzi, co z niej zostało, i
przekazuje resztę jako cięcia.

## Cel

`vc-intents` produkuje ledger, w którym każda intencja Foundera z wybranego okna
ma jedną jawną dyspozycję, wynik zgodności między dwiema niezależnymi flotami i
warstwę korekt edytowalną przez człowieka — i jest gotowy, gdy
`intents_cli.py <workdir> status` nazywa, co jest jeszcze otwarte, a
`plan --stage implement` zapisał dla tego plan dispatchu. Katalog nie zamyka
celu. Audyt nie zamyka celu. Plan cięć zamyka.

## Canonical Orientation Gate

Przed każdym krokiem dotyczącym repo — sweep wobec repo, konfrontacja, plan —
uruchom albo skonsumuj procedurę `vc-init` dla przydzielonego repo.
`Loctree:loctree` jest domyślnym skillem percepcji strukturalnej w tym
przebiegu i musi wytworzyć albo odświeżyć Code-Derived Application Map
(`context`, `repo-view`, `focus`, `slice`, `impact`, `find`, `follow`). Tory
konfrontacji to dziedziczą: brief czyni loctree-mcp pierwszym ruchem, a
`landed` bez strukturalnej ścieżki za sobą nie jest werdyktem. Jeśli brak
świeżych dowodów `vc-init`, wykonaj najpierw init i traktuj polowanie jako
zablokowane, dopóki nie istnieje prawda repo. Sweep korpusu transkrypcji bez
podpiętego repo deklaruje w raporcie wyjątek no-repo.

## Zakres to okno, nie historia

Ogranicz polowanie przed sweepem: `--since/--until` albo temat. Pokrycie
raportujesz wobec tego okna. Cała historia to jedno z legalnych okien — nie
warunek zamknięcia. Czego okno nie zawiera, zapisujesz jako _poza zakresem_,
nigdy jako _absent_.

## Narzędzie

```bash
CLI=~/.claude/skills/vc-intents/scripts/intents_cli.py   # albo ścieżka w store
W=~/.vibecrafted/artifacts/<owner>/<repo>/intents         # jeden workdir na polowanie
uv run "$CLI" "$W" <komenda> …
```

Jeden katalog roboczy trzyma wszystko, co musi przeżyć kompakcję albo drugi
host: `sweep.json`, `shards/`, `intents.json`, `lanes/`, `LEDGER.json`,
`overrides.jsonl`, `queue.json`, `STABLE-QUEUE.md`, `LEDGER.md`,
`intents.html`. Agenci czytają i orzekają; skrypt prowadzi księgi, robi
falsyfikacje, które nie wymagają osądu, i pisze plany oddające osąd flocie.

## Procedura

### 0 · Orientacja

Uruchom albo skonsumuj `vc-init` dla repo. Ustal tożsamość katalogu przez
`aicx intents -p /<repo>` (wiodący slash — każdy historyczny właściciel).
Przeczytaj linię `project:` w nagłówku: to Twój mianownik dla aicx. Potem
kontrola negatywna: data najnowszej intencji vs data najnowszego commitu. Luka
to brakująca tożsamość, nie cichy kwartał.

### 1 · Sweep — rozlicz każde źródło w oknie

```bash
uv run "$CLI" "$W" sweep --since 2026-06-01 --until 2026-09-18 \
  --source dir:~/.codescribe/transcriptions --source aicx --repo <repo> --shard-size 120
```

Źródła są pierwszej klasy i nierówne. Wynik z pola (Codescribe, IX 2026): aicx
miał **~10 %** głosu Foundera i **0 %** korpusu dyktowanego; katalog transkrypcji
miał 2 842 pliki na dwu hostach. Katalog żyjący na innej maszynie ściągasz
(`rsync` z tego hosta) przed sweepem, albo jest `unavailable` w pokryciu —
znany nieznany, który trzyma cel otwarty. Obcięcie korpusu aicx raportuje
**tylko na stderr**; skrypt łapie to do pliku i flaguje `truncated`.

`sweep` pisze chronologiczne shardy do ekstrakcji. Nic jeszcze nie zostało
przeczytane.

### 2 · Ekstrakcja — flota czyta, dosłownie

```bash
uv run "$CLI" "$W" plan --stage extract --repo <repo> --agent '*=junie:gemini-3.8-flash'
vibecrafted dispatch <plan> --doctor --json && vibecrafted dispatch <plan> --json
uv run "$CLI" "$W" verify-quotes --extractions "$W/extractions" --transcripts <dir> --strict
```

Brief to `references/extraction-brief.md`. Jedna twarda zasada: **`quote` jest
dosłownym podciągiem pliku źródłowego.** `verify-quotes` odrzuca wszystko inne
przez zawieranie ciągu — parafraza podana jako głos Foundera to najgorszy błąd
tej roboty, bo przypisuje człowiekowi decyzję, której nie podjął. Przekręty
Whispera zostają; normalizacja żyje w `topic`.

Dobór agenta to **przepustowość**, nie ranking: masowe czytanie krótkich plików
to robota na 375 tps (`junie`/`gemini-3.8-flash`), nie dla modelu z czołówki.
Osiem natywnych Opusów spaliło raz tygodniowy budżet na to, co flota flash robi
w minuty. Zobacz `references/replication.md`.

### 3 · Rozdziel głosy

Wiążą tylko własne słowa Foundera. Propozycje agentów to kandydaci; claimy
wykonania to cele audytu; dokumenty pisane przez agentów (CHANGELOG, ADR,
roadmapa, raport) to claimy w przebraniu dokumentu. Korpus transkrypcji też nie
jest z automatu czysty: agenci wklejali odpowiedzi modelu do plików dyktowania w
~30 % plików jednego miesiąca. Brief ekstrakcji wylicza, co pomijać; pole
`kind` zapisuje, co Founder robił (`complaint`, `task`, `constraint`,
`decision`, `preference`, `question`).

### 4 · Chronologia

Późniejsze słowa Foundera unieważniają wcześniejsze **tylko z nazwanym
następcą** (`superseded_by`). Dwie wypowiedzi, które się wykluczają bez
późniejszego rozstrzygnięcia, to `contradiction` — instrumentem jest
`aicx clarify`, potem Founder. Deduplikuj wypowiedzi, nigdy proweniencję: jedna
intencja, wiele cytatów.

### 5 · Konfrontacja — dwie floty, dwa hosty

```bash
uv run "$CLI" "$W" lanes                     # po obszarze; domyślnie sześć torów Codescribe
uv run "$CLI" "$W" plan --stage confront --repo <repo> --host div0 \
  --agent 'L1-overlay-ui=kimi:kimi-code/k3' --agent 'L4-quality-lexicon=junie:gemini-3.8-flash' …
```

Potem ten sam plan na drugiej maszynie, z tymi samymi pinami i modelami, z
katalogami werdyktów wyłączonymi z rsync workdira (druga flota nie może widzieć
odpowiedzi pierwszej). Brief to `references/confrontation-brief.md`: loctree-mcp
najpierw, `landed` wymaga `plik:linia`, `absent` wymaga pytania, które zwróciło
zero, zero edycji, wszystkie id zachowane.

Weryfikacja jest strukturalna: „X startuje automatycznie" to _krawędź od
entrypointu do X_ (`loct follow trace`), nie trafienie grepa. Tabela obietnica →
pytanie → komenda: `references/loctree-questions.md`.

```bash
uv run "$CLI" "$W" merge --verdicts div0=<dir> --verdicts dragon=<dir> --primary div0 --project <owner>/<repo>
```

`merge` pisze `LEDGER.json` i `replication-report.json`. Zgodność per id przy
tym samym agencie i modelu wyniosła **72,8 %**; największy rozjazd to
`partial ↔ landed`. Czytaj to tak: żaden pojedynczy werdykt nie jest dowodem —
zgodna para jest. Do kolejki wchodzą tylko stabilne pary.

### 6 · Reklasyfikacja — `landed` to dolna granica

```bash
uv run "$CLI" "$W" reclassify
```

Dwie reguły bez osądu, stosowane przed liczeniem czegokolwiek jako zamknięte:
`kind = complaint ∧ landed → landed-contested` (Founder mówi, że nie działa;
flota znalazła kod; runtime niezweryfikowany — skarga wygrywa do czasu sondy) i
`dowód bez pliku kodu → landed-by-doc` (dokument dowodzi, że kontrakt spisano,
nie że go dotrzymano). Wynik z pola: **43–45 % `landed` spadło** na obu hostach.
Obie pochodne dyspozycje liczą się jako otwarte. Reguły dopisują do
`overrides.jsonl`; werdykt floty nigdy nie jest nadpisywany. Schemat i treść
reguł: `references/ledger-schema.md`.

### 7 · Warstwa człowieka

```bash
uv run "$CLI" "$W" render --html --transcripts <dir> --blob-base https://github.com/<o>/<r>/blob/<sha>/
open "$W/intents.html"
uv run "$CLI" "$W" decide --import-file decisions.jsonl --by founder      # albo --id … --to … --reason …
```

Strona pokazuje każdą intencję z dosłownym cytatem, szerszym wycinkiem
transkrypcji, werdyktami obu hostów z dowodami podlinkowanymi do przypiętego
commitu, korektami mechanicznymi i formularzem decyzji: zgadza się,
reklasyfikuj, usuń jako fałszywy trop, komentarz. Decyzje eksportują się jako
JSONL i wracają przez `decide`. To jest powierzchnia edycji Foundera — linia w
`overrides.jsonl`, nigdy ręczna edycja pliku werdyktów. Opublikowana jako
artefakt claude.ai z capability `db`, ta sama strona zapisuje decyzje na żywo;
plik jest kanonem w obu przypadkach. Zobacz `references/review-surface.md`.

### 8 · Kolejka i plan cięć

```bash
uv run "$CLI" "$W" queue --min-strength 4
uv run "$CLI" "$W" plan --stage implement --repo <repo> --limit 6 --gate 'cd {repo} && make check'
```

Kolejka trzyma intencje **otwarte, stabilne między hostami albo rozstrzygnięte
przez człowieka i wystarczająco mocne**, uporządkowane po zależności (`after`),
potem ryzyku, potem wpływie na główny przepływ, potem sile i wieku. Plan
implement daje każdej cięcie: cytat Foundera jako brief, dowód i lukę floty,
bramkę repo jako verifier, `mode = write`, `require_commit = true`. `codex` jest
domyślnym workerem. Wszystko, co wymaga guzika Foundera (merge, deploy, sekrety,
kasowanie danych), jest w cucie opisane i nigdy nie naciskane.

### 9 · Zamknięcie

`status` zwraca 0 tylko, gdy nie zostaje żadna otwarta dyspozycja, żadne źródło
nie jest niedostępne ani obcięte, a każde `landed` ma `runtime_verified = yes`.
Do tego czasu cel jest otwarty — zgodnie z prawdą. Po powrocie z kompakcji:
przeczytaj `LEDGER.md`, odpal `status`, potem `queue`; **nie powtarzaj sweepu**,
wynik jest na dysku.

## Kontrakt wyjścia

1. **Pokrycie** — okno, źródła ze stanem (`read`/`empty`/`unavailable`/
   `truncated`), pliki i wiersze per źródło.
2. **Katalog** — zweryfikowane intencje, odrzucone cytaty z powodami.
3. **Ledger** — werdykty per host, zgodność per para, dyspozycja efektywna,
   korekty (reguła i człowiek), `runtime_verified`.
4. **Kolejka** — stabilne otwarte intencje z uzasadnieniem porządku.
5. **Plan** — `intents-implement.<host>.dispatch.toml`, czysty po doktorze.
6. **Najwyższa prawda** — jedna nierozwiązana rzeczywistość, która powinna
   trochę zaboleć. „43 % tego, co dwie floty nazwały landed, kwestionują własne
   słowa Foundera" to najwyższa prawda; „część pozycji jest partial" nie.

## Antywzorce

Traktowanie rankingowego trafienia aicx jak przeczytanego źródła · parafraza w
`quote` · werdykt jednej floty jako dowód · `landed` z `docs/*.md` ·
`superseded` bez następcy · powtarzanie sweepu po powrocie · modele z czołówki
do masowego czytania · verifier `run` z literałem toru (`release` w
`L6-build-release` wyzwala hard-stop parsera dispatchu; skrypt pisze ścieżki
przez `{id}`) · naciskanie hard-stopu, bo kolejka tak kazała · raportowanie
zielonego audytu jako zamknięcia, gdy kolejka jest niepusta.

## Skille pokrewne

`vc-init` przed wszystkim · `vc-canary`, gdy dwa moduły konkurują o prawdę,
której intencja potrzebuje · `vc-dispatch` odpala każdy plan, który ten skill
pisze · `vc-implement` / `vc-ownership` to cięcia · `vc-trust` falsyfikuje
potem claim ukończenia workera · `vc-audit` dla spisanego planu zamiast korpusu.

## Zweryfikuj przed przekazaniem

Obejdź ciężarówkę — [Reguła Weryfikacji](../VERIFICATION_RULE.md): ledger
istnieje i `status` mówi OPEN z powodami, które umiesz nazwać; plan przeszedł
`vibecrafted dispatch --doctor`; HTML otwiera się z właściwymi nazwami hostów i
działającymi linkami do kodu; każda liczba w raporcie pochodzi z pliku w
workdirze, nie z pamięci.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
