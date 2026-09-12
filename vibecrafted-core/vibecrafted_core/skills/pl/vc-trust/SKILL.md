---
name: vc-trust
version: 1.0.0
description: >
  Post-hoc falsification of commit claims on a Living Tree, including agent
  fairness (Authored-By matches subject agent; no vendor footers; no foreign
  envelope lies) and completeness claims. Produces pass, pass-with-gaps, or
  block verdicts, appends evidence to the trust journal, and projects explicit
  verdicts onto the canonical f/x/n settlement axis. Trust observes and judges;
  it never blocks dispatch or mutates code. Enforcement is vc-guard.
loctree_value: "commit scope, consumers, blast radius, and runtime paths"
aicx_value: "why the commit exists and which prior attempts shaped it"
dogfooding: "required"
---

# vc-trust — sędzia po fakcie

`vc-trust` to spokojny sędzia po fakcie dla commitów zrobionych na wspólnym
Living Tree. Wiadomości commitów są hipotezami. Trust falsyfikuje ich claimy
wobec diffu, konsumentów, testów, runtime'u i historycznej intencji, zanim
wyda:

- `pass` — każdy materialny claim przetrwał falsyfikację **z mocnym evidence**
  (sama legalność formatu/trailerów nigdy nie wystarcza).
- `pass-with-gaps` — rdzeń claimu przetrwał, ale zostają nazwane luki w evidence
  albo w pokryciu.
- `block` — materialny claim jest fałszywy, zaprzeczony, niebezpieczny albo nie
  jest w stanie sprostać wymaganemu progowi evidence (łącznie z naruszeniami
  agent fairness).

### Agent fairness (pierwszorzędna oś claimów)

Przy każdym commicie traktuj co najmniej te rzeczy jako materialne claimy:

1. Subject pasuje do `[<agent>/<runtime>] <type>: …`.
2. `Authored-By: <agent> <agents@vetcoders.io>` jest obecne i **równa się**
   agentowi z subjectu (Agent Fairness — autentyczność wykonawcy).
3. Brak vendorowych `Co-Authored-By` / `noreply@` / vendorowych maili.
4. Przed trailerami istnieje wyjaśniające body (kształt jest konieczny, nie
   wystarczający).
5. Koperta commita wymienia prawdziwe pliki; niezapracowane „done/fixed" bez
   nazwanych testów/bramek to luka, nie pass.

Ekstraktory mechaniczne:

```bash
python -m vibecrafted_core.trust inspect <sha>
```

Inspect nigdy nie notuje automatycznie i nigdy nie sugeruje passa. Journal i
settlement zapisuje wyłącznie jawne `note`.

Mapowanie na settlement jest zamknięte i kanoniczne (nie otwieraj liter na
nowo):

| Werdykt trustu   | Settlement      | TUI |
| ---------------- | --------------- | --- |
| `pass`           | Finalized       | `f` |
| `pass-with-gaps` | Needs attention | `n` |
| `block`          | Failed          | `x` |

## Checkpoint orientacji

Zanim zbadasz, osądzisz albo zanotujesz commit, uruchom lub skonsumuj procedurę
`vc-init` dla przypisanego repozytorium. Ustal repozytorium, branch, baseline,
zakres review, stan dirty i ścieżkę trust journala. `trust inspect` to
mechaniczny ekstraktor, nie zamiennik tej orientacji, i nigdy nie jest
wystarczającym evidence pod werdykt.

Użyj `Loctree:loctree`, żeby wyprodukować lub odświeżyć Code-Derived Application
Map dla recenzowanego zakresu: zmienione pliki, konsumenci, entrypointy
runtime'u, testy, twinsy i zasięg zmiany. Sięgaj po `slice`, `impact`, `find` i
`follow` tam, gdzie wymaga tego dany claim, a potem niezależnie obejrzyj diff i
prawdziwą ścieżkę runtime'u. Mapa ogranicza falsyfikację; nie przyznaje zaufania.
Trust pozostaje read-only wobec kodu i pisze wyłącznie po jawnych powierzchniach
journala/settlementu wymienionych niżej.

## Wywołanie

- Worker: `vibecrafted trust <claude|codex|agy|junie|grok|cursor> --prompt ...`
- Interactive: `/vc-trust`
- Linia operatora: `vibecrafted trust <agent> --file <brief.md>`

Ustrukturyzowany helper to:

```bash
python -m vibecrafted_core.trust --help
```

## Twarda granica

Trust jest READ-only wobec repozytorium. Może pisać wyłącznie:

- append-only trust journal;
- jawny trust settlement na istniejącym runie;
- zawężoną projekcję control plane'u dla tego samego runu;
- swój raport i transcript.

Trust nigdy nie edytuje kodu, nie amenduje ani nie rewertuje commitów, nie
blokuje dispatchu, nie pushuje i nie merguje. Egzekucja należy do `vc-guard`
(inwentarz bramek + odmowa przy trust `block`). Nie implementuj tu zachowania
guarda.

Po pauzy, stopy, operator buttons i granice autonomii idź do
[`vc-operator/AUTONOMY.md`](../vc-operator/AUTONOMY.md); nie forkuj tego
kontraktu.

## Protokół

### 1. Zorientuj się i ogranicz strumień

Przejdź bramkę `vc-init`. Przeczytaj cały atlas Loctree i historię intencji
AICX. Uchwyć branch, HEAD, stan dirty i dokładny zakres commitów. Na Living Tree
nigdy nie przypisuj autorstwa brudnego pliku ani równoległego commita wyłącznie
na podstawie czasu.

Wylistuj nieosądzonych kandydatów:

```bash
python -m vibecrafted_core.trust enumerate <author> --since <sha-or-ISO-time>
```

### 2. Zamień prozę w falsyfikowalne claimy

Dla każdego commita wyciągnij każdy materialny claim z jego subjectu/body i ze
zmienionej powierzchni. Przepisz mgliste zdania na sprawdzenia w rodzaju:

- nazwany test albo bramka istnieje i pada, gdy zachowanie jest zepsute;
- ścieżka runtime'u dociera do zmienionego kodu;
- deklarowane zachowanie fail-closed nie ma obejścia ani silencera;
- docsy i powierzchnia launchera opisują zachowanie, które faktycznie jedzie;
- zakres diffu zgadza się z wiadomością i nie zawiera obcych, niezadeklarowanych
  plików.

Brak claimu w wiadomości nie ukrywa materialnej regresji w diffie.

### 3. Oceń evidence per claim

- `strong` — bezpośrednia reprodukcja w runtimie, test adwersarialny, dokładna
  inspekcja artefaktu albo niezależnie padająca-a-potem-przechodząca bramka.
- `medium` — skupiony test jednostkowy/integracyjny plus strukturalny dowód po
  stronie konsumentów.
- `weak` — statyczna proza, wywnioskowana intencja, evidence wyłącznie z
  happy path albo raport upstream, którego sędzia sam nie powtórzył.

`pass` wymaga, żeby każdy materialny claim miał wystarczające bezpośrednie
evidence. Słabe evidence może podpierać kontekst, ale samo z siebie nigdy nie
daje materialnego passa.

### 4. Falsyfikuj, nie odgrywaj ceremonii

Użyj Loctree `slice` dla zmienionych plików, `impact` dla zmian o dużym zasięgu,
literalnego find/body dla dokładnych claimów oraz `follow` dla istotnych
sygnałów dead/cycle/twin. Przeczytaj diff commita i jego rodziców. Uruchom
najbliższe testy i prawdziwą ścieżkę użytkownika. Sprawdź, czy sama komenda
weryfikująca w ogóle potrafi paść.

`vc-review` i `vc-audit` pozostają czymś innym:

- `vc-review` osądza jakość ograniczonego diffu/PR-a.
- `vc-audit` falsyfikuje ukończony plan albo wielozadaniową implementację.
- `vc-trust` osądza strumień commitów na żywym, wspólnym drzewie i zapisuje
  trwały werdykt per commit.

### 5. Zapisz dokładnie jeden jawny werdykt

Każdy `--claim` musi mieć jeden pasujący `--grade` i `--evidence`:

```bash
python -m vibecrafted_core.trust note <sha> pass \
  --claim "the blocking lane rejects insecure code" \
  --grade strong \
  --evidence "negative fixture failed before the fix and passed after it"
```

Osądzając commit(y) wyprodukowane przez run, dodaj `--run-id <id>`. Kanoniczny
settlement zapisuje wyłącznie to jawne `note`. Kod wyjścia, obecność raportu ani
zakończony await nigdy nie oznaczają trust passa.

Journal domyślnie leży w
`$VIBECRAFTED_HOME/trust/journal.jsonl` i używa
`vibecrafted.trust-journal.v1`. Nadpiszesz to przez
`VIBECRAFTED_TRUST_JOURNAL` albo `--journal`.

### 6. Czekaj na granicy runu

Podstawowy tryb cyklu życia nazywa się `await-primary`, nie `guard=await`:

```bash
python -m vibecrafted_core.trust await-primary <run-id> \
  --author <agent-author> \
  --since <baseline-sha>
```

Czeka synchronicznie przez kanoniczny control plane, a potem listuje nieosądzone
commity kandydujące. Nie auto-passuje, nie auto-notuje, nie odpytuje w tle i nie
działa jak stały monitor. Stałe monitorowanie jest wyłącznie wygodą w trybie
interactive i nie jest trwałym mechanizmem wybudzania.

### 7. Zbierz do kupy

```bash
python -m vibecrafted_core.trust triage [--run-id <id>]
```

Triage bierze najnowszy wpis append-only per repo+commit i raportuje kanoniczne
liczniki `f/x/n`. Nie przelicza settlementu z Gita ani z kodów wyjścia.

## Kontrakt raportu

Raport końcowy musi zawierać:

- branch/HEAD baseline'u i recenzowany zakres commitów;
- macierz claimów z oceną evidence i dokładnymi komendami/artefaktami;
- jeden werdykt per commit oraz zbiorczy wynik na poziomie runu;
- ścieżkę journala i rezultat zapisu settlementu;
- weryfikacje wykonane i niewykonane;
- luki resztkowe i następny bezpieczny ruch.

Nigdy nie mów „zaufane", nie pokazując, który claim został zaatakowany i co
przetrwało.
