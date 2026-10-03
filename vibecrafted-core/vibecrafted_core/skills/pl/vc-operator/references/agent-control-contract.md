# Agent Control Contract

Ten dokument referencyjny definiuje minimum mechaniki, jaką skill Vibecrafted
musi wystawić, gdy oczekuje się od niego sterowania agentem, dispatchowania
innego agenta albo decydowania, czy praca jest ukończona.

To nie jest przewodnik stylu. To powierzchnia kontroli.

## Wymagane bloki

Każdy skill klasy kontrolnej powinien zawierać te bloki.

### Żelazne prawo

Jedno zdanie, którego nie da się obejść.

Przykłady:

- Żadnego fixa przed evidence przyczyny źródłowej.
- Żadnego passu bez evidence task, code, test i negative-check.
- Żadnego verdictu `done` z evidence wyłącznie z pamięci.
- Żadnego dispatchu workera bez scope, bramek, artefaktów i stop buttonów.

### Funkcja bramki

Dokładna reguła decyzyjna, która pozwala agentowi kontynuować, zatrzymać się,
obniżyć rangę twierdzenia lub eskalować.

Bramka musi nazywać:

- wymagane wejścia
- wymagane evidence
- legalne statusy
- nielegalne skróty
- komendę lub artefakt, które dowodzą statusu

### Dozwolone statusy

Używaj skończonego słownika statusów. Nie pozwól prozie wymyślać nowych stanów.

Rekomendowane statusy bazowe:

```text
pending
running
reported
verified
blocked
recovery
stop
```

Statusy specyficzne dla skilla są dozwolone tylko wtedy, gdy są zdefiniowane w skillu.

### Czerwone flagi / Stop

Wymień konkretne sytuacje, które wymuszają pauzę, obniżenie rangi statusu, krok
odzyskiwania lub handoff.

Dobre red flagi są obserwowalne:

- plik docelowy nie istnieje
- launcher nie może wystartować
- awaria testu zmieniła kategorię
- snapshot Loctree jest nieświeży i użyto fallbacku
- agentowi brakuje wymaganego artefaktu
- nie da się udowodnić ścieżki instalacji dla użytkownika

Słabe red flagi to nastroje:

- wydaje się ryzykowne
- chyba źle
- może wystarczy

### Kontrakt wyjścia

Zdefiniuj wymagany finalny artefakt.

Kontrakt powinien określać:

- ścieżkę pliku lub miejsce docelowe artefaktu
- pola lub nagłówki
- dozwolone verdicty
- format evidence
- następny legalny skill lub fazę

Jeśli skill dispatchuje agentów, wyjście workera powinno być na tyle
sprawdzalne maszynowo, by operator mógł je zintegrować bez ponownego czytania
całego transkryptu.

### Kryteria akceptacji

Kryteria akceptacji muszą być poparte weryfikatorem.

Używaj:

- dokładnej komendy
- dokładnej ścieżki pliku
- dokładnej ścieżki artefaktu
- dokładnego działania runtime'owego
- dokładnego wymogu zrzutu ekranu lub transkryptu

Unikaj:

- „wygląda dobrze"
- „powinno działać"
- „chyba wystarczy"
- „best effort"

## Kształt promptu workflow

Użyj tego kształtu, gdy generujesz prompt workera lub prompt workflow.

```yaml
---
workflow_prompt_version: 1
run_id: <id>
parent_run_id: <id|null>
skill: <vc-workflow|vc-marbles|vc-audit|...>
phase: <scaffold|implement|review|workflow|followup|marbles|audit|polarize|dou|hydrate|release>
mode: <READ|WRITE|META>
agent: <codex|claude|gemini|junie|agy|grok|cursor>
project_root: <abs-path>
branch_head: <branch@sha>
artifact_root: <abs-path>
report_path: <abs-path>
upstream_artifacts: [<paths>]
downstream_consumer: <next skill/phase>
wave: <id|null>
position: <n|null>
vector: <stabilize|implement|recon|e2e|research|release>
state: <pending|running|reported|verified|blocked|recovery|stop>
depends_on: []
parallel_with: []
blocks: []
permissions:
  source_write: <true|false>
  git_commit: <true|false>
  push_pr_deploy: false
gates:
  - <exact command>
stop_buttons:
  - push
  - merge
  - deploy
  - public promise
---
```

Następnie napisz treść w tej kolejności:

1. Rola i tryb.
2. Misja.
3. Wejścia do przeczytania, po kolei.
4. Prawda bazowa.
5. Scope, out-of-scope, zmiany zabronione.
6. Powierzchnie docelowe.
7. Kryteria akceptacji.
8. Kontrakt kadencji.
9. Kolejność narzędzi.
10. Protokół wykonania.
11. Awaria i odzyskiwanie.
12. Kontrakt artefaktu.
13. Warunek ukończenia.
14. Imperatywne wezwanie do działania.

## Reguły trybów

### READ

- Może inspekcjonować kod, dokumenty, raporty, logi i wyjście runtime'u.
- Nie wolno edytować plików źródłowych.
- Nie wolno commitować.
- Domyślny verdict to `unverified`, dopóki evidence nie udowodni inaczej.
- Finalne wyjście musi rozdzielać evidence, wnioskowanie i otwarte ryzyko.

### WRITE

- Może edytować scoped powierzchnie źródłowe lub artefaktowe.
- Musi ponownie przeczytać dotknięte pliki w living tree przed edycją.
- Musi uruchomić najbliższą realną bramkę.
- Musi raportować weryfikację i brak weryfikacji.
- Nie wolno push, deploy ani publikować, chyba że skill jawnie nadaje ten
  button.

### META

- Dispatchuje lub koordynuje agentów.
- Nie wolno cicho stać się workerem.
- Musi zapisywać lub aktualizować stan trackera.
- Musi czekać na wymagane artefakty przed weryfikacją.
- Musi zatrzymać się przy buttonach push, merge, deploy i public-promise, chyba
  że operator jawnie je nada.

## Cele skill lint

Skill klasy kontrolnej powinien nie przejść review, gdy:

- nie ma Iron Law
- nie ma Funkcji bramki
- nie ma skończonego słownika statusów
- używa `maybe` jako stanu polityki
- twierdzi o ukończeniu bez Output Contract
- każe workerom być „ostrożnymi" bez nazwania evidence
- wspomina o TODO migracji w opisie manifestu pluginu
- deleguje bez kryteriów akceptacji
- dispatchuje agentów bez ścieżek artefaktów
- używa szerokiej autonomii bez stop buttonów

## Polityka języka

Mocny język kontrolny jest dobry:

- Nie ukrywaj niepewności.
- Raportuj bramkę, która padła.
- Blokuj pracę specyficzną dla repo, dopóki nie ma aktualnej prawdy repo.
- Obniż rangę twierdzenia do unverified.

Słaby lub przepraszający język kontrolny należy przepisać:

- „maybe"
- „probably"
- „best effort" bez bounded fallbacku
- „jeśli operator poprosi o evidence"
- „nie wystawia tego samego hooka", gdy politykę da się sformułować pozytywnie

Celem nie jest mdły ton. Celem jest wykonywalna prawda.
