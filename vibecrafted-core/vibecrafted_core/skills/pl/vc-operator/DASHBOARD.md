# vc-operator — DASHBOARD: Panel Administracyjny dla Agenta-Operatora

> Agent-Operator pracuje na ślepo bez panelu administracyjnego. Wszystkie źródła danych
> istnieją; brakuje tylko warstwy widoku. Ten plik to **doktryna** —
> dlaczego dashboard ma znaczenie, jakie panele musi wyświetlać, jakie
> autorytatywne źródła stoją za każdym panelem. **Konkretny plan budowy** dowozi
> się jako siostrzany `PLAN_23` wewnątrz product workspace'u `vc-operator`.

Czytaj obok [`SKILL.md`](SKILL.md), [`AWAIT.md`](AWAIT.md). Zwiad źródłowy
dostarczony 2026-05-16.

---

## Dlaczego to ma znaczenie

Dziś Agent-Operator orkiestruje flotę z pamięci:

- „Czy Wave B-2 wylądowała na zielono?" — przeczytaj ponownie własny log sesji
- „Czy Gemini ciągnął swoją część w tym tygodniu?" — zgaduj
- „Czy `vc-partner` jest faktycznie wywoływany?" — wykonaj empiryczny zwiad za każdym razem
- „Czy dysk hosta dragon jest poniżej 80%?" — `ssh dragon df -h` i miej nadzieję
- „Który prompt jest teraz w locie?" — pamiętaj run_id

Każdy z tych punktów danych żyje na dysku. Agent-operator i operator
(człowiek) obaj pracują bez jednego widoku, który by je połączył.

Dashboard zmienia tryb operatora z **„agent pamięta"** w
**„operator i agent obaj widzą"**.

---

## Siedem paneli

### 1. Aktywne dispatchy (live)

Co działa _właśnie teraz_. Każdy wiersz:

- run_id
- agent (claude / codex / gemini)
- skill (vc-implement / vc-ownership / vc-marbles / itd.)
- wave + pozycja w planie
- elapsed wall-clock + ETA
- live link do viewera transkryptu/stanu; nigdy do karty hostującej proces

**Autorytatywne źródła**:

- `/tmp/<runtime>/<encoded-cwd>/<session-uuid>/tasks/<task-id>.output`
  dla stanu live (strumień JSONL)
- `~/.vibecrafted/artifacts/<...>/<workflow>/tmp/vc-spawn-cmd.<…>.LOCK`
  pidfile'e do wykrywania aktywnego spawnu
- Klucz złączenia: trójka `(cwd, session_id, prompt_id)`

### 2. Wave atlas (bieżący plan)

Siatka statusu aktywnego planu master-dispatch. Każdy prompt:

- litera wave + pozycja
- prompt_id
- status: `pending` / `firing` / `await` / `green` / `failed` / `recovered`
- SHA gdy green
- przypisany agent
- branch
- strzałki zależności (renderowane jako kolumny fal)

**Autorytatywne źródło**: sekcja trackera atlasu master dispatch,
aktualizowana przez agenta-operatora po każdym zamknięciu fali. Odwzorowuje
strukturę fal z [`GUIDE.md`](GUIDE.md) 1:1.

### 3. Statystyki per agent (ostatnie N dni)

Tabela per agent:

- liczba wywołań
- success rate (% z `status: completed` i `gate: pass`)
- średni wall-clock per dispatch
- zgodność peer-tier (% z `model: opus`, gdy rodzic był Opus)
- rollup tokenów / kosztu, jeśli dostępny
- niedawny rekap awarii

**Autorytatywne źródło**: `~/.vibecrafted/artifacts/<...>/<workflow>/reports/*.meta.json`
agregowane po polu `agent`. Sprawdzenie krzyżowe przez
`aicx steer --json --agent <agent>` dla potwierdzenia z sesji.

**Znana luka w danych**: `meta.json` często ma `model: unknown` i
`duration_s: null`. Napraw w momencie zapisu — jednoliniowy patch dispatchera.
Dashboard wyświetla lukę (liczbę dispatchów z nieznanym modelem), żeby
fix wylądował szybko.

### 4. Statystyki per skill

Skille to powierzchnie wywołań (vc-ownership, vc-marbles, vc-decorate,
vc-partner, vc-init, itd.). Statystyki per skill:

- wywołania w ostatnich 7 / 30 / 90 dniach
- timestamp ostatniego wywołania
- średni success rate
- skille z **zero** wywołań w 30 dni → ostrzeżenie „quiet" (np.
  obawa o vc-partner uczyniona widoczną)

**Autorytatywne źródło**: pole `skill` z `meta.json` agregowane. Aktywność
shardów sesji (`~/.aicx/store/<org>/<repo>/<date>/`) dla potwierdzenia
kadencji wywołań.

### 5. Zdrowie floty

Panel po stronie systemu:

- użycie dysku per host
- świeżość indeksu sesji (`semantic lag` z `aicx health --json`)
- zdrowie korpusu sesji (liczba brakujących sidecarów, anomalne nazwy bucketów)
- endpoint vc-agents up/down
- żywotność serwerów MCP (`loctree-mcp`, `aicx-mcp`, MCP specyficzne dla projektu)
- zdrowie linku Tailscale między hostami

**Autorytatywne źródła**:

- `aicx health --json` (wbudowane, emit JSON, otagowane severity)
- `df -h` po Tailscale ssh dla dysku
- endpointy MCP `/health`, gdzie wystawione

### 6. Tablica awarii (ostatnie 24 / 48 / 168 godzin)

Każdy dispatch z `status != completed` lub `exit_code != 0` lub
`gate: fail` lub pustym `/tmp/.../tasks/<id>.output`. Każdy wiersz:

- timestamp
- agent + skill + prompt_id
- modalność awarii (substrate / scope / implementation / hang / notify-lost)
- link do dispatchu odzyskiwania, jeśli jakiś odpalił (złączony przez `recovers: <id>` we
  frontmatterze)
- jednoliniowy fragment powodu awarii podanego przez workera

**Autorytatywne źródło**: ten sam `meta.json` przefiltrowany. Przejście krzyżowe do
chunka sesji przez `(session_id, project, agent)`.

### 7. Kolejka działań operatora

Inbox „wystarczy wcisnąć guzik". Każdy wpis:

- jednoliniowy opis tego, co wymaga przycisku (push branch X / review
  treści promptu Y / merge wave Z do trunka / zatwierdź kształt dispatchu odzyskiwania)
- która sesja agenta-operatora czeka
- timestamp zakolejkowania
- akcja one-click, gdzie operator może spełnić z terminala

**Autorytatywne źródło**: własne pliki handoffu w punkcie stopu agenta-operatora
(kształt z Sekcji 5 [`AUTONOMY.md`](AUTONOMY.md)). Dashboard śledzi
pliki `reports/<ts>_stop-point_operator.md` i wyświetla je jako
pozycje kolejki.

---

## Wylądowane vs zaplanowane (audyt zamknięcia, 2026-06-10 — PLAN_23 Wave D-2)

Siedem paneli powyżej to doktryna; ta tabela to prawda runtime'u na stan
zamknięcia PLAN_23. „Landed" znaczy, że panel renderuje się w tab Mission
Control `voc` ORAZ w samodzielnym rendererze `vc-admin` (`b534103`, scope fix
`75bc7f5`, binary rename `65c5072`), z pancerzem snapshotów Insta w
`vibecrafted-app/tui-agent/tests/mission_control_snapshots.rs`.

| Panel                 | Status  | Wylądowany kształt                                                       | Wciąż planowane (niepodpięte)                                                 |
| --------------------- | ------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------- |
| Active dispatches     | LANDED  | live runy control-plane, wszystkie roots + etykiety roots, age/ETA, wave | złączenie pidfile + JSONL `tasks/*.output`; link do viewera transkryptu/stanu |
| Wave atlas            | LANDED  | grupowanie po `prompt_id` z `meta.json` + live runy, glify stanu         | parsowanie tracker.md; SHA-on-green; branch; strzałki zależności              |
| Per-agent stats       | LANDED  | agregacja `meta.json` z 30d: runy/✓/✗/⌀dur/rate model-known              | zgodność peer-tier; rollup token/koszt; cross-check `aicx steer`              |
| Per-skill stats       | LANDED  | wywołania z 30d/✓/✗/⌀dur + flaga ⚠ quiet-skill                           | okna 7/90d; timestamp ostatniego wywołania                                    |
| Fleet health          | PARTIAL | control-plane, artifact-root, meta-scan, parytet model/duration          | dysk per host; `aicx health`; żywotność MCP; link Tailscale                   |
| Failure board         | LANDED  | okno 24h z `meta.json` + live runy failed, powód + age                   | klasy modalności awarii; linki odzyskiwania `recovers:`                       |
| Operator action queue | LANDED  | wyprowadzone: zacięte runy + awarie + intencje polarize + świeże raporty | śledzenie `.md` z punktu stopu; akcje spełnienia one-click                    |

Noty zamknięcia o charakterze przekrojowym:

- Powierzchnia CLI wylądowała jako **`vc-admin`** (`vco` przemianowane w `65c5072`):
  `status` / `wave` / `agent` / `skill` / `failures` / `button` /
  `health` / `watch`.
- Receipt DataQuality (scanned/capped/missing-model/missing-duration/
  parse-failures) renderuje się pod panelami — reguła „surface the gap"
  z noty Znana luka w danych jest na żywo. Fix telemetrii Wave 0 wylądował w
  `40935d5` (źle oznaczony subject; `runtime/scripts/lib/meta.sh`).
- Prawda motywu: nie ma w aplikacji palety light/dark — dashboard
  emituje wyłącznie nazwane kolory ANSI, a motywy mesh vc_frame rozwiązują je
  po stronie terminala. Suite snapshotów zamraża treść + rozmieszczenie kolorów
  i pilnuje inwariantu named-ANSI.

---

## Kształt danych (mapowanie panel → plik)

| Panel                 | Główny plik                                                 | Drugorzędny                         | Noty                                  |
| --------------------- | ----------------------------------------------------------- | ----------------------------------- | ------------------------------------- |
| Active dispatches     | `/tmp/<runtime>/<cwd>/<session>/tasks/*.output` + pidfile'e | `meta.json` po wylądowaniu          | złączenie live + niedawne zamknięcie  |
| Wave atlas            | master-dispatch `tracker.md`                                | prefiksy git log `[agent/workflow]` | jeden tracker na aktywny plan         |
| Per-agent stats       | `~/.vibecrafted/artifacts/*/reports/*.meta.json`            | `aicx steer --json`                 | agregacja po zakresie dat             |
| Per-skill stats       | pole `skill` w `meta.json`                                  | aktywność shardów sesji             | skille o niskiej kadencji flagowane   |
| Fleet health          | `aicx health --json`                                        | `df -h` + MCP `/health`             | odświeżanie co N minut                |
| Failure board         | `meta.json` przefiltrowany                                  | chunki sesji przez złączenie sesji  | odzyskania linkowane przez `recovers` |
| Operator action queue | `reports/<ts>_stop-point_operator.md`                       | brak                                | sterowane śledzeniem (tail)           |

**Pojedynczy najbardziej autorytatywny plik w poprzek paneli**: sidecar `meta.json` w
`~/.vibecrafted/artifacts/`. To jedyny plik, który łączy (run_id,
prompt_id, agent, skill, project, branch, commit, status, gate, exit_code,
timestamps, transcript-path) w jednym miejscu. Stan live musi pochodzić z
`/tmp/<runtime>/`; cała reszta to potwierdzenie.

---

## Koperta implementacji

Dashboard **nie jest** napisany tutaj. Dwaj mocni kandydaci na cel budowy:

1. **Rozszerz workspace Rust `vc-operator/`** (istniejące siostrzane repo z
   kokpitem `tui-agent`, agentami mux + tray + shell). Dodaj nowy crate lub
   panel do `tui-agent`, który konsumuje powyższe źródła danych. Najbardziej
   naturalne dopasowanie — kokpit już istnieje, dashboard staje się tabem.
2. **Samodzielne binarium TUI** (nowy crate `vc-admin` (dawniej `vco`) lub
   app Pythona `textual`) czytające te same pliki. Lżejszy footprint, bez
   zależności od roadmapy istniejącego kokpitu.

Plan dispatchu samej budowy żyje jako `PLAN_23` (siostrzany wobec
`PLAN_22_NEXT_OPERATOR_MISSION_CONTROL`) wewnątrz
`vc-operator/docs/plans/`. Ten plan ma kształt fal i jest dispatchowany
przez ten właśnie skill — dogfooding: dashboard Agenta-Operatora jest
budowany przez Agenta-Operatora używającego doktryny vc-operator.

---

## Dlaczego CLI / TUI przed web

Operator pracuje w terminalu (Zellij + ssh + git + CLI AICX). Dashboard web
oznacza przełączenie kontekstu na tab przeglądarki. TUI trzyma operatora
w jego istniejącym flow:

```bash
vc-admin status                  # static snapshot, all panels
vc-admin watch                   # live-refresh, full panel set
vc-admin wave --plan <id>        # focus on wave atlas
vc-admin agent claude            # per-agent panel deep dive
vc-admin failures --since 24h    # failure board
vc-admin button                  # operator action queue
```

Widok web przychodzi jako drugi, jako rozszerzenie w stylu
`aicx dashboard --serve`, jeśli zespół urośnie ponad jednego operatora.

---

## Antywzorce

- Budowanie dashboardu jako ładnej wizualizacji bez rozwiązania
  luk `model: unknown` + `duration_s: null` w momencie zapisu → garbage in,
  garbage out.
- Czytanie `/tmp/.../tasks/<id>.output` dla stanu live przez `cat` /
  `tail -f` z procesu dashboardu → ryzyko przepełnienia kontekstu na dużych
  workerach; użyj czytnika świadomego strumieniowania.
- Pollowanie każdego panelu co sekundę → ten sam antywzorzec, co w doktrynie
  await. Tailowe file watchery + tryb `aicx tail --follow`.
- Wyświetlanie wszystkich skilli jednolicie bez flagowania cichych → cały
  powód istnienia tego to uczynienie „żywy, ale cichy" widocznym (przypadek
  `vc-partner`).
- Auto-odpalanie akcji z dashboardu (push, merge, itd.) → dashboard
  _wyświetla_ kolejkę przycisków, operator _wciska_ przyciski.

---

## Handoff planu budowy

Konkretny plan budowy dowozi się jako:

- `<vc-operator-repo>/docs/plans/PLAN_23_AGENT_OPERATOR_DASHBOARD.md`
  (kształt forward-plan per [`vc-init/backlog/HOWTO.md`](../vc-init/backlog/HOWTO.md))

Dashboard dowozi się z workspace'u Rust `vc-operator/` po jednym lub
więcej dispatchach w kształcie Wave przez ten skill.

---

## Wezwanie do Działania

Przeczytaj PLAN_23 przed napisaniem jakiejkolwiek treści dispatchu związanej
z dashboardem. Odpal Wave 0 (fix luki meta.json dispatchera) przed hydracją
jakiegokolwiek panelu danych — w przeciwnym razie statystyki per agent kłamią.
Potem szkielet Wave A, potem sekwencyjne podpinanie danych Wave B, potem
równoległe panele Wave C, potem sekwencyjne zamknięcie Wave D. Łańcuch
dispatchu to dogfood.

---

## Klamra końcowa

```text
=======================
Dashboard, który nie wyświetla niczego, czego operator i tak by nie zapamiętał,
nie jest dashboardem — jest tapetą. Buduj pod pytanie, którego operator jeszcze
nie zadał, ale za chwilę zada, i wyświetlaj dane, które czynią następną
decyzję trywialnie bezpieczną. (งಠ_ಠ)ง
=======================

Suchar: Dlaczego kolejka działań operatora to najczęściej obserwowany panel?
Bo jedyne, co trudniejsze niż odpalenie fali, to pamiętanie, że jesteś winien
pusha. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
