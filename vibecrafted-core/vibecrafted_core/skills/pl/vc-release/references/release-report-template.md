# Release Report Template

To kanoniczny kształt raportu `vc-release`. To jedyny uczciwy
sposób, by powiedzieć „released" wewnątrz frameworka 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.. Każdy release
musi wyprodukować raport zgodny z tą strukturą. Brakujące lub puste
sekcje oznaczają, że release jest **zablokowany**.

Skopiuj treść poniżej do swojego artefaktu release'u (do
`$VIBECRAFTED_ROOT/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`)
i wypełnij ją. Nie usuwaj sekcji. Jeśli sekcja nie ma zastosowania,
wyjaśnij w jednym zdaniu, dlaczego nie ma zastosowania.

---

## Frontmatter

```yaml
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|cursor|system>
skill: vc-release
project: <repo-name>
status: <pending|in-progress|completed|failed|blocked>
created: <ISO-8601 timestamp>
release_version: <semver tag, e.g. v1.4.1>
---
```

## 1. Security gate

- Command run: `make semgrep` (or documented equivalent: `<command>`)
- Exit status: `<0 | non-zero>`
- Finding count: `<blocking>` blocking, `<info>` informational
- Findings table:

  | Rule ID | Severity | File | Lines | Boundary | Resolution |
  | ------- | -------- | ---- | ----- | -------- | ---------- |
  |         |          |      |       |          |            |

- Taksonomia klasyfikacji granic:
  - `path` — tainted path / sinki LFI
  - `regex` — parsowanie podatne na ReDoS
  - `merge` — niebezpieczne merge'owanie headerów / obiektów
  - `shell` — konstrukcja komendy
  - `auth` — szwy authn / authz
  - `other` — wyjaśnij w kolumnie resolution
- Gate satisfied: `<yes | no | accepted-with-reason>`
- Jeśli `accepted-with-reason`, wskaż podpisaną przez użytkownika linijkę akceptacji.

## 2. Exposed surface inventory

| Surface         | Bind address        | Port | Public? | Proxy in front            | TLS terminator     | Auth boundary                          |
| --------------- | ------------------- | ---- | ------- | ------------------------- | ------------------ | -------------------------------------- |
| <app/api/admin> | 127.0.0.1 / 0.0.0.0 |      | yes/no  | Caddy / Nginx / LB / none | proxy / app / none | public / session / token / mTLS / none |

- Nagłówki brzegowe dodawane/zdejmowane: `HSTS`, `CSP`, `X-Frame-Options`,
  `Strict-Transport-Security`, `Referrer-Policy`, `Permissions-Policy`,
  allowlista CORS (po jednej linijce każda, z wartościami).
- Materializacja sekretu: jak i gdzie każdy sekret trafia do runtime'u
  (wstrzyknięcie env przy starcie, menedżer sekretów przy pobraniu, żaden).
- Sprawdzone wzorce zabronione: `0.0.0.0` bez intencji, `CORS: *` na
  uwierzytelnionych API, osiągalne strony debug frameworka, pliki `.env` lub backupy
  dostępne z weba, wyeksponowane stacktrace'y lub bannery.

## 3. Deployment mode decision

- Chosen topology: `<static | Caddy | Nginx | Docker | other>`
- Powód, dla którego to najmniejsze uczciwe dopasowanie:
  - <jedno lub dwa zdania>
- Szkic topologii (tekst jest w porządku):

  ```text
  client → DNS (canonical host) → TLS terminator → reverse proxy → app
                                                                 → worker
                                                                 → db
  ```

- Ścieżka healthchecka i oczekiwana odpowiedź: `<endpoint>` → `<expected>`
- Zachowanie przy restarcie i graceful shutdown:
- Procedura rollbacku (bez ręcznych bohaterstw):
  - <komenda lub link do runbooka>

## 4. Post-release install smoke

- Źródło artefaktu (NIE może być ścieżką drzewa roboczego):
  - URL rejestru lub URL pobierania: `<url>`
  - tag / wersja: `<tag>`
  - digest (gdy dostępny): `<sha256>`
- Użyte zimne środowisko: `<fresh container | new VM | scratch venv | other>`
- Wykonana sekwencja komend:

  ```bash
  # paste the exact commands run
  ```

- Evidence z pierwszego uruchomienia:
  - exit code: `<code>`
  - banner wersji: `<paste>`
  - sonda health: `<curl output | command output>`
- Dryf zaobserwowany względem udokumentowanego quickstartu:
  - <none | wypisz każdy element dryfu; otwórz followupy>

## Sign-off

- Security gate: <ok | accepted | blocked>
- Surface inventory: <ok | accepted | blocked>
- Deployment mode: <ok | accepted | blocked>
- Install smoke: <ok | accepted | blocked>

Released by: `<agent>` on `<ISO-8601 timestamp>`.

---

## Dlaczego każda sekcja jest obowiązkowa

- **Security gate** zamienia Semgrep z prywatnego kroku CI w publicznego
  świadka czasu release'u. Kolumna klasyfikacji sprawia, że „odpaliliśmy
  skaner" daje się wymiernie odróżnić od „przeczytaliśmy wyjście".
- **Exposed surface inventory** to to, czego zewnętrzny recenzent AppSec lub
  Semgrepa potrzebuje, by ocenić realia produkcji. Porty, proxy, auth i
  nagłówki brzegowe opisują realną powierzchnię ataku; obsługa sekretów opisuje
  tryb awarii, który najprawdopodobniej dowieziesz przez przypadek.
- **Deployment mode decision** to to, co czyni release odtwarzalnym.
  Wybranie topologii odruchowo („zawsze używamy Dockera") ukrywa
  tryby awarii tego wyboru; spisanie powodu czyni kompromis
  widocznym dla następnego operatora.
- **Post-release install smoke** to jedyne sprawdzenie, które dowodzi, że obcy
  faktycznie potrafi zainstalować i uruchomić opublikowany artefakt. Zielona
  macierz testów nie zastępuje instalacji na zimno.

Jeśli chcesz pominąć sekcję, nie chcesz release'u. Chcesz
deploya. To są różne rzeczy.
