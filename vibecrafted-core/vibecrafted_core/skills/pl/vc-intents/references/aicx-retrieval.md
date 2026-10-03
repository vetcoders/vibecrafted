# AICX retrieval grammar for intent hunting

Przeczytaj przed fazą 1. Od tego zależy, czy sweep obejmie historię repo, czy jedną trzecią.

## Contents

- [Project identity — the highest-leverage filter](#project-identity)
- [The two entry commands](#the-two-entry-commands)
- [Filters that matter, and what each one is for](#filters)
- [Voice separation and the lane pipeline](#voice-separation)
- [Reading sources](#reading-sources)
- [Continuity and the Loctree bridge](#continuity-and-the-loctree-bridge)
- [Failure modes](#failure-modes)

## Project identity

`-p` przyjmuje cztery formy, bez rozróżniania wielkości liter, powtarzalnie.
Wiele flag `-p` lub lista z przecinkami tworzą sumę zbiorów.

| Form            | Meaning                                      | Use for                               |
| --------------- | -------------------------------------------- | ------------------------------------- |
| `-p owner/repo` | ścisły slug                                  | jedna znana tożsamość                 |
| `-p owner/`     | każde repo właściciela                       | sweep organizacji                     |
| `-p /repo`      | **ta sama nazwa repo u każdego właściciela** | **domyślnie w polowaniu na intencje** |
| `-p name`       | jednoznaczna dokładna nazwa owner lub repo   | tylko bez niejednoznaczności          |

Substring celowo nie jest wspierany: `-p vista` nie pasuje do `vista-portal`.
Niejednoznaczność daje odmowę z listą kandydatów, nie ciszę — ten błąd odpowiada
na pytanie „jakie tożsamości istnieją”.

`--project-fuzzy` włącza dopasowanie rodziny projektu. Używaj dopiero po pustych
formach exact i odnotuj poszerzenie w raporcie.

**Dlaczego slash na początku ma znaczenie.** Repo przeniesione do innego owner,
przemianowane lub obrabiane na drugim hoście ma kilka tożsamości w katalogu.
Zaobserwowano na żywo:

```
$ aicx intents -p /screenscribe --hours 8764 --score 50 --sort newest
project: 01_deployed_libraxis_vm/screenscribe, screenscribe, vetcoders/screenscribe
```

Trzy tożsamości, jedno zapytanie. `-p vetcoders/screenscribe` zwróciłoby jedną,
pozornie zdrowo, pomijając dwie. Zawsze czytaj i zapisuj nagłówek `project:` —
to mianownik pokrycia.

## The two entry commands

### `aicx intents` — the roadmap surface

Ustrukturyzowane intencje z trwałego katalogu i allowlisty źródeł sesji.
Domyślne okno 720h; **domyślnie bez limitu wyników** (pełna roadmap),
w przeciwieństwie do search z limitem 10.

```bash
aicx intents -p /<repo> --hours 8764 --score 50 --sort newest --emit json
```

`--emit json` zawiera `oracle_status`. Markdown łatwiej czytać; JSON zasila ledger.

### `aicx search` — the topic probe

Lexical-first w indeksie CURRENT, z preferencją świeżości. `--hours 0` oznacza
całą historię (w `intents` 0 nie oznacza całości — użyj dużego okna, np. 8764).

```bash
aicx search '<theme>' -p /<repo> --hours 8764 --score 50 --sort newest
```

Przydatne modyfikatory:

- `--deep` — dense re-rank (hybrid RRF). Wolniejszy, ładuje embedder;
  używaj, gdy lexical nie znajduje tematu sformułowanego inaczej.
- `--evidence` — pakiet dowodowy z sekcjami źródłowymi i diagnostyką zamiast samych trafień.
- `--session <id>` — search _wewnątrz_ sesji zamiast rankingu sesji.
  `--literal` daje dokładne granice identyfikatora, `--context N` poszerza okno.
  To właściwe narzędzie do „gdzie w tej rozmowie to powiedzieli”.
- `--dialog` — renderuje opóźnioną ludzką mowę z sealami; pomocne, gdy słowa
  Foundera z kolejki wyglądają jak wynik narzędzia.
- `--result head=N|full` — rozwija ciało trafień `tool_call`, domyślnie skróconych.
- `--kind conversations|plans|reports|other` — rodzaj indeksowanego dokumentu.

## Filters

Dotyczą obu komend, chyba że zaznaczono inaczej.

| Filter                                          | What it buys you in an intent hunt                                                                                                                                                                             |
| ----------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--frame-kind user_msg`                         | **Własne słowa Foundera.** Główny filtr głosu; uwaga niżej: na `search` zwraca zero.                                                                                                                           |
| `--frame-kind agent_reply` / `internal_thought` | Propozycje i rozumowanie agenta — kandydaci, nigdy wymagania.                                                                                                                                                  |
| `--frame-kind tool_call`                        | Ślady wykonania — cel audytu, nie dowód dostarczenia.                                                                                                                                                          |
| `--kind decision\|intent\|outcome\|task`        | (intents) Typ wpisu; w `decision` żyją zmiany zdania.                                                                                                                                                          |
| `--sort oldest`                                 | Rekonstrukcja chronologiczna ujawnia zmianę decyzji.                                                                                                                                                           |
| `--sort newest`                                 | Najpierw najnowsze obowiązujące słowo.                                                                                                                                                                         |
| `--score <0-100>`                               | Dolny próg dopasowania; 50 jest robocze, obniż dla nietypowego sformułowania.                                                                                                                                  |
| `--min-confidence 1..5`                         | (intents) Pewność ekstrakcji, osobna od score; `--strict` to zgrubny wariant.                                                                                                                                  |
| `--since` / `--until` / `-d`                    | Granice dat; działa `--since 2026-04-23..` i zakres `2026-03-20..2026-03-28`.                                                                                                                                  |
| `--agent claude\|codex\|gemini\|junie`          | Sesje danego agenta; czy obietnica jest tylko w pamięci jednego? **`--agent codescribe` nie ma producenta**: 0 wierszy, exit 0, pozornie pusta historia. Korpus dyktowany nie jest w aicx; sweep przez `dir:`. |
| `--unresolved --unresolved-mode intent`         | **Zamknięcie per intencja** — brak pasującego outcome. Tryb session jest szerszy: sesje bez żadnego outcome.                                                                                                   |
| `--collapse-session`                            | Jedna pozycja na sesję; dobre do census, złe do detalu.                                                                                                                                                        |
| `--limit`                                       | Ogranicza stronę; `intents` domyślnie jest bez limitu.                                                                                                                                                         |
| `--live` / `--no-live`                          | Okna ≤48h automatycznie skanują live sources; wymuś lub wyłącz.                                                                                                                                                |

## Voice separation

Pipeline torów istnieje, bo twierdzenia agentów o ukończeniu trafiają do tego
samego transkryptu co wymagania Foundera.

```bash
aicx sessions report <session-id> --repo "$PWD" --format markdown
```

Jeden render, pięć torów:

- **Lane 1** — intencje człowieka
- **Lanes 2–3** — twierdzenia agenta i weryfikacja ich dowodów
- **Lane 4** — pęknięcia kontraktów
- **Lane 5** — decyzje do wyjaśnienia

Tory są też adresowalne osobno:

```bash
aicx claims  extract --session <id> --format summary                 # Lane 2
aicx results collect --session <id> --repo "$PWD" --format summary   # Lane 3
aicx clarify --session <id> --repo "$PWD" --max 5                    # Lane 5
```

`claims extract` znajduje „zaimplementowałem X”. `results collect` sprawdza
artefakty repo wynikające z tych twierdzeń i nadaje statusy weryfikacji.
Twierdzenie po `results collect` nadal dowodzi tylko _istnienia artefaktu_ —
osiągalność należy do Loctree, nie aicx.

## Reading sources

Retrieval ustawia ranking, nie czyta. Otwórz źródło każdej intencji, na której zamierzasz działać.

```bash
aicx sessions list --cwd
aicx sessions show <id>
aicx extract claude --session "$SESSION" --conversation
aicx extract codex  --file <path/to/rollout.jsonl> --conversation -o <out.md>
```

Subkomendy agentów: `codex`, `claude`, `gemini`, `grok`, `junie`.
`--session` najpierw rozwiązuje katalog (ograniczone nagłówki tożsamości, bez
parsowania ciała), potem parsuje dokładnie jedno źródło. `--file` daje bezpośredni
uchwyt bez skanu katalogu. Domyślny wynik:
`~/.aicx/extracts/<agent>/<session_id>[_conversation].md`.

Shell: `SESSION=$(aicx sessions current)` w osobnym wierszu lub inline
`--session "$(aicx sessions current)"`. `SESSION=x aicx … --session
$SESSION` nie działa: shell rozwija `$SESSION` przed procesem z tym przypisaniem.

## Continuity and the Loctree bridge

```bash
aicx continuity show -p <owner>/<repo> --hours 168 --for-inject
aicx continuity write -p <owner>/<repo> --hours 168   # to a file
```

NOW / PEERS / DECISIONS / TASKS / SOURCES / INDEX HEALTH w oknie projektu.
Najpierw live parse, potem census — embedder nie blokuje. `--for-inject`
ogranicza do około 6k tokenów. Tak dowiadujesz się o zmianach innych agentów między turami.

```bash
aicx overlay --repo /path/to/repo --format json
```

Łączy typowane intencje katalogu z bieżącym `loct anchors` repo i emituje
`loctree.overlay.intent.v1` — maszynowy join wykonywany ręcznie przez ten skill.
Źródłem jest katalog ery extracts; jeśli wynik jest cienki: `aicx catalog rebuild`,
potem `aicx intents -p <owner/repo>` i ponownie overlay. `--rebuild` przelicza
każde typowane twierdzenie, zachowując trwałe intent ids.

## Failure modes

- **Luka indeksu to blocker, nie wymówka.** Ścieżka naprawy: `aicx health`,
  `aicx doctor`, `aicx index`, `aicx catalog rebuild`. Jeśli przetrwa, oznacz
  okno jako niedostępne źródło i pozostaw otwarte.
- **Pusty wynik ścisłego filtra nie dowodzi nieobecności.** Poszerz tożsamość,
  obniż `--score`, użyj `--project-fuzzy`, zanim uznasz, że tematu nie omawiano.
- **`--frame-kind` na `search` jest zepsuty (zweryfikowano 2026-09-18, aicx 0.13.0).**
  `aicx search 'review' -p /screenscribe --hours 8764 --limit 50` daje 297 wierszy;
  `--frame-kind user_msg` daje 0. Na `intents` działa (5419 vs 1236 wierszy).
  Rozdzielaj głos w search przez Lane 1 `aicx sessions report` lub odczyt źródła.
  Zapisano w `~/.vibecrafted/aicx/aicx-fail.md`.
- **`--limit` jest respektowany na `intents`** — `--limit 2000` ustawia
  `per_section_limit: 2000` w nagłówku. Raport pozornie ograniczony do 20
  pochodzi z innego wywołania, nie twardego limitu.
- **`--collapse-session` ukrywa zmiany zdania.** Dwie zmiany w sesji zwijają się do jednej pozycji.
- **Wysoki score podsumowania agenta nie jest wymaganiem Foundera.**
  Score mierzy dopasowanie, nie autorytet. Głos określa `--frame-kind`, nigdy ranking.
