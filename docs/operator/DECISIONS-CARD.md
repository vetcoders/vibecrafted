---
plan_id: voc-operator-frame-reconstruction
cut_id: decision-card-r1
session_id: f9aa5bc0
role: decision-card
author: gemini
date: 2026-09-22
project: vetcoders/vibecrafted-suite
---

# KARTA DECYZJI FOUNDERA (C-1 … C-15)

## Rekonstrukcja produktu Voc / Operator Frame

Karta rozstrzygnięć dla 15 kluczowych guzików architektonicznych i produktowych zdefiniowanych w `DECISION-MATRIX.md` §C.
Każda pozycja zawiera:

1. Precyzyjne pytanie decyzyjne
2. Kontekst źródłowy i stan faktyczny
3. Minimum 2 rozłączne opcje wraz z konsekwencjami technicznymi i operacyjnymi
4. Kotwicę źródłową (ledger/digest/commit z datą)
5. Wyraźnie oznaczoną propozycję agenta (zgodnie z Prawdą Źródła §0 Charteru)
6. Puste pole na werdykt Foundera

---

## C-1 · Kanon rodziny layoutów: wbudowane czy pakietowe?

**Pytanie decyzyjne:**
Która rodzina layoutów stanowi kanoniczny autorytet źródłowy dla Operator Frame: layouty wbudowane w silnik `vc-frame` (`zellij-utils/assets/layouts/`) czy layouty pakietowe w repozytorium `vibecrafted` (`vibecrafted-core/config/vc-frame/layouts/`)?

**Kontekst źródłowy:**
W sesji z 2026-09-21 ujawniono split-brain pięciu par layoutów (`host.kdl`, `dashboard.kdl`, `marbles.kdl`, `research.kdl`, `workflow.kdl`, `operator.kdl`). Layout wbudowany `vibecrafted-host.kdl` zawierał `frame_host` wyłącznie w pojedynczym tabie `Workspace`, podczas gdy pakietowy `host.kdl` umieszczał `frame_host true` wewnątrz współdzielonej `session_layer`, co skutkowało powieleniem raila na każdym tabie i błędem niejednoznaczności routingu (`AmbiguousHost`). Dodano bramkę porównującą hashe (`make layouts-check`), lecz nie wskazano nadrzędnego autorytetu.

**Opcje wyboru:**

- **Opcja 1:** Kanon w `vc-frame` (`zellij-utils/assets/layouts/`). Repozytorium `vibecrafted` podczas budowy lub instalacji materializuje/kopiuje layouty z wersjonowanej generacji `vc-frame`, eliminując drugą ręczną kopię.
  - _Konsekwencje:_ Silnik terminala i chrome mają jedno źródło prawdy. Zmiana layoutu wymaga edycji w `vc-frame` i przebudowy assetów silnika.
- **Opcja 2:** Kanon w `vibecrafted` (`vibecrafted-core/config/vc-frame/layouts/`). Silnik `vc-frame` traktuje wbudowane layouty wyłącznie jako awaryjny bootstrap, a w runtime ładuje layouty dostarczone przez `vibecrafted` (zgodnie z zasadą A13: „powierzchnia = vibecrafted, vc-frame to substrat”).
  - _Konsekwencje:_ Szybsza iteracja nad produktem bez konieczności rekompilacji kodu w Rust w `vc-frame`. Wymaga twardego testu walidującego zgodność struktur wstrzykiwanych layoutów z parserem `vc-frame`.

**Kotwica:** `orient/codex-div0-01a0c238-digest.md` §4.2 (2026-09-21 05:12–06:07), `DECISION-MATRIX.md` §C (C-1), commity `b380fbe8` oraz `a11e7805`.

**Propozycja agenta:** Opcja 2 (ze ścisłą bramką walidacji kontraktu w CI).

**Werdykt Foundera:** [ ]

---

## C-2 · „Chrome rule” i definicja Hosta: przyjęcie jako prawo architektoniczne

**Pytanie decyzyjne:**
Czy sformułowania agenta Kimi z 2026-09-19:

1. „Chrome rule”: _„chrome zawsze należy do hosta; host może projektować stan gościa; gość nigdy nie współwłaszczy chrome'u.”_
2. Reguła generatywna: _„Host = to, co przeżywa śmierć sesji i ma władzę nad sesjami. Guest = to, co żyje i umiera z sesją.”_
   należy przyjąć jako obowiązujące prawo architektoniczne całego systemu?

**Kontekst źródłowy:**
W sesji `bada6c18` agent Kimi oznaczył te reguły stemplem _„§1 Design law (Founder-verified, NIE otwierać)”_. Analiza źródeł (M-B w `DECISION-MATRIX.md`) wykazała, że Founder podyktował konkretny skład hosta i organów, ale reguła generatywna oraz „Chrome rule” były autorską syntezą agenta, do której Founder nie odniósł się werbalnie.

**Opcje wyboru:**

- **Opcja 1:** Przyjąć obie reguły w całości jako twarde prawo architektoniczne.
  - _Konsekwencje:_ Całkowity zakaz modyfikacji raila, pasków stanu i tabów hosta przez procesy i wtyczki gościa. Wszelkie rozszerzenia gościa muszą być rzutowane wyłącznie przez deklaratywne interfejsy hosta.
- **Opcja 2:** Odrzucić „Chrome rule”; dopuścić współwłasność lub bezpośredni wpływ aktywnej sesji gościa na wybrane elementy chrome (np. dynamiczne menu akcji sesji w nagłówku).
  - _Konsekwencje:_ Większa elastyczność widoków gościa, ale wysokie ryzyko wyścigów aktualizacji, rozjeżdżania geometrii ekranu i powrotu migania raila.
- **Opcja 3:** Przyjąć regułę generatywną (Host = to co przeżywa sesję), a w zakresie „Chrome rule” zdefiniować stałe, deklaratywne sloty projekcji (np. centralna strefa dolnego paska dedykowana dla organów gościa).
  - _Konsekwencje:_ Bezpieczna izolacja cyklu życia przy jednoczesnym zachowaniu ergonomii rzutowania stanu aktywnego zadania.

**Kotwica:** `orient/kimi-div0-bada6c18-digest.md` §3 (linie 126–127, 442–453), `DECISION-MATRIX.md` §B (M-B) (2026-09-19 15:07).

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-3 · Semantyka znacznika ◉ (fisheye): prawda serwera czy projekcja hosta?

**Pytanie decyzyjne:**
Co formalnie reprezentuje wskaźnik `◉` (fisheye) na railu sesji: fizyczną obecność podłączonego klienta (prawda serwera: „w której sesji aktualnie znajduje się fokus terminala/użytkownika”) czy stan nawigacji w Operator Frame (projekcja hosta: „którą sesję gościa host aktualnie wyświetla / ostatnio otworzył”)?

**Kontekst źródłowy:**
W sesji `f2cba272` (2026-09-21) ujawniła się sprzeczność semantyczna: przyznanie `◉` roli „odwiedzanego gościa” ukrywało przed operatorem fakt, do której sesji faktycznie podłączony jest proces klienta. Claude zaproponował rozdzielenie `is_current_session` oraz `is_visited_guest`, po czym wycofał propozycję w obawie przed naruszeniem notatki Kimiego (P1 / M-C). W efekcie semantyka tronu obecności pozostała nierozstrzygnięta.

**Opcje wyboru:**

- **Opcja 1:** `◉` oznacza wyłącznie projekcję hosta (ostatnio otwarty / rzutowany gość).
  - _Konsekwencje:_ Rail odzwierciedla stan nawigacji Operator Frame. Przy wielu podłączonych klientach (lub równoległym SSH) rail nie wskazuje lokalizacji poszczególnych terminali.
- **Opcja 2:** `◉` oznacza wyłącznie fizyczną obecność klienta (gdzie trafiają znaki z klawiatury).
  - _Konsekwencje:_ Operator natychmiast widzi, w której sesji pracuje; projekcja hosta musi otrzymać inne wyróżnienie wizualne (np. stałe podkreślenie nazwy wg A6).
- **Opcja 3:** Rozdzielenie na dwa ortogonalne wskaźniki w railu sesji: stały znacznik obecności klienta (`◉` / `○`) oraz niezależny styl fokusu rzutowania hosta (pogrubienie wiersza / inwersja).
  - _Konsekwencje:_ Pełna prawda operacyjna; likwiduje dwuznaczność bez ukrywania stanu żadnej z warstw.

**Kotwica:** `orient/aicx-decisions.md` §10 (P1), §1.10, §1.13; `orient/claude-div0-f2cba272-digest.md` (2026-09-21); `DECISION-MATRIX.md` §B (M-C).

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-4 · Predykat „needs attention”: źródło sygnału, progi i reguła kwalifikacji

**Pytanie decyzyjne:**
Jaka formalna reguła logiczna, zestaw źródeł zdarzeń i progi czasowe decydują o zakwalifikowaniu agenta lub sesji do grupy `needs attention` na liście konsoli Voc?

**Kontekst źródłowy:**
Founder w sesji `184f4a99` (2026-09-13, wpis 2.13 i 5.13) zdefiniował pożądany widok Zen dla Voc: lista pogrupowana w kategorie `live / needs attention / failed`. Jednak w kodzie i dokumentacji nie istnieje żaden sformalizowany predykat kwalifikujący to zdarzenie (L 11.7/30). Istnieją jedynie nazwy sesji i surowe kody wyjścia procesów.

**Opcje wyboru:**

- **Opcja 1:** Wąski predykat interakcji: oczekiwanie na decyzję/potwierdzenie użytkownika (np. monit o zatwierdzenie narzędzia, prompt ask_question) LUB brak postępu (brak nowych linii w transkrypcie przez > 90 s w stanie aktywnym).
  - _Konsekwencje:_ Niski poziom szumu; kategoria obejmuje wyłącznie sytuacje, w których agent realnie czeka na interwencję człowieka.
- **Opcja 2:** Rozszerzony predykat zasobowo-budżetowy: warunki z Opcji 1 plus zbliżanie się do wyczerpania kontekstu (> 85% okna modelu) lub ostrzeżenie o wyczerpaniu tygodniowego limitu providera (rate-limit warning).
  - _Konsekwencje:_ Wczesne ostrzeganie przed awariami budżetowymi, wymaga jednak stałego zasilania z modułu telemetrii.
- **Opcja 3:** Predykat trójstanowy z jawną flagą w protokole zdarzeń: `agent_blocked` (czeka na człowieka), `health_warning` (brak heartbeatu > 30 s), `gate_failure` (czerwona bramka weryfikacyjna przed finalizacją).
  - _Konsekwencje:_ Najbardziej uniwersalna architektura, deterministycznie sterowana przez runtime.

**Kotwica:** `orient/aicx-decisions.md` §2.13, §5.13 (2026-09-13), §11.7/30; `DECISION-MATRIX.md` §C (C-4).

**Propozycja agenta:** Opcja 1 dla wdrożenia fazy W1 (prosta i niezawodna), z migracją do Opcji 3 po scaleniu z telemetrią.

**Werdykt Foundera:** [ ]

---

## C-5 · Kontrakt `vc-start`: wejście do żywego workspace czy błąd kolizji?

**Pytanie decyzyjne:**
Jak ma zachować się polecenie `vc-start` w sytuacji, gdy sesja lub workspace o podanej (lub domyślnej) nazwie już istnieje w środowisku?

**Kontekst źródłowy:**
W korpusie istnieją dwie przeciwstawne wypowiedzi Foundera (para sprzeczna P6):

- 2026-08-25 (8.4): Founder:

> _„ziomuś - mój schemat jest taki. wchodzę z laptopa po ssh na dragona -> uruchamiam `vc-start` i wbijam się w main workspace aktualnie działający. Myślałem, że będzie jak zwykle, a mnie coś uniemożliwia”_

- 2026-09-09 (6.6): Founder zażądał, by przy kolizji nazw `vc-start` rzucał błąd:

> _„Use `vc-frame attach <workspace>` or `vc-dashboard switch <workspace>` or explicitly use `vc-start <new_workspace>` with different name”_
> Próba pogodzenia tego w kodzie doprowadziła do stanu z 2026-09-10 (1.7), w którym runtime odmawiał startu bezwarunkowo.

**Opcje wyboru:**

- **Opcja 1:** Domyślny attach (semantyka idempotentna): jeśli sesja istnieje, `vc-start` dołącza do niej klienta; jeśli nie istnieje — tworzy nową.
  - _Konsekwencje:_ Zgodne z naturalnym nawykiem pracy terminalowej po SSH (8.4). Likwiduje zbędne błędy i konieczność ręcznego wpisywania komendy attach.
- **Opcja 2:** Ścisły tryb create-only: `vc-start` służy wyłącznie do inicjalizacji; przy istniejącej sesji zwraca błąd z kodem wyjścia i czytelną podpowiedzią użycia `attach` / `switch`.
  - _Konsekwencje:_ Całkowita ochrona przed przypadkowym wejściem do niewłaściwego workspace'u i pomyłkową zmianą jego stanu.
- **Opcja 3:** Domyślny attach z wyraźnym komunikatem ostrzegawczym, z flagą `--create-unique` / `--new` generującą w razie kolizji instancję z przyrostkiem numerycznym (`<name>-2`).
  - _Konsekwencje:_ Łączy wygodę jednoetapowego startu z możliwością jawnego wymuszenia nowej, izolowanej instancji.

**Kotwica:** `orient/aicx-decisions.md` §10 (P6), §6.6 (2026-09-09), §8.4 (2026-08-25), §1.7 (2026-09-10).

**Propozycja agenta:** Opcja 1 (lub Opcja 3).

**Werdykt Foundera:** [ ]

---

## C-6 · DMG-first vs Runtime-first i uporządkowanie kanałów instalacji

**Pytanie decyzyjne:**
Jaka jest nadrzędna relacja architektoniczna między instalatorem DMG a paczką runtime CLI, i który kanał stanowi podstawowe źródło prawdy dla release'u?

**Kontekst źródłowy:**
Najgłębsza sprzeczność packagingowa w korpusie (P18):

- 2026-08-24 (9.5):

> _„dmg i app jest nakładką na coś co ma działać nawet jak ich nie ma”_

- 2026-08-29 (9.10):

> _„po eradykacji dmg-first podejscia (…) Server po rozpięciu dmg ma nie być z przypadku tylko ma napierdalać na pierwszych skrzypcach nadzorczych”_

- 2026-09-21 (9.14–9.16): Founder sprecyzował macierz kanałów:

> _„pamiętajmy, że ścieżka `install.sh` musi instalować też @loctree/aicx i loctree z npm oraz prview z gh releases i screenscribe z pipx (…) ale nie budować ich idiotycznie ze źródeł (…) vc-frame i vc-terminal nadal ze źródeł wiadomix (…) i chciałbym, żeby zestaw dmg notarized + tarballe budowały się na tag do main po merge (…) muszę przetestować exactly the same product jaki dostanie każdy i to przez wszystkie platformy”_
> Równolegle funkcjonują trzy kanały (DMG, tarball runtime pack, `make install`), których rozjazd powoduje powstawanie generacji-widm.

**Opcje wyboru:**

- **Opcja 1:** Runtime-first: rdzeniem produktu jest skrypt `install.sh` pobierający wersjonowane binarki i tworzący hermetyczne środowisko CLI. Aplikacja macOS (DMG) jest jedynie klientem UI i menu bar extra instalowanym opcjonalnie.
  - _Konsekwencje:_ Identyczna architektura działania na macOS, Linux i maszynach SSH bez GUI.
- **Opcja 2:** DMG-first na macOS: DMG stanowi nadrzędny nośnik dystrybucji na Macu; zawiera w sobie wszystkie komponenty i automatycznie zarządza ich instalacją oraz usługami launchd.
  - _Konsekwencje:_ Najprostsza instalacja metodą „przeciągnij do Applications”, ale konieczność utrzymywania odrębnej logiki dla Linuksa.
- **Opcja 3:** Ujednolicony pakiet dystrybucyjny wg reguły 9.14–9.16: `install.sh` instaluje zależności z oficjalnych rejestrów (npm, pipx, gh-releases), a proces release'u na tag do main generuje równocześnie spójny tarball runtime i podpisany DMG.
  - _Konsekwencje:_ Pełna spójność wersji we wszystkich kanałach; testy release'u odbywają się wyłącznie na gotowym artefakcie.

**Kotwica:** `orient/aicx-decisions.md` §10 (P18), §9.4, §9.5 (2026-08-24), §9.10 (2026-08-29), §9.14–9.16 (2026-09-21).

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-7 · Domyślny tryb pracy z kodem: Living Tree czy worktrees-default?

**Pytanie decyzyjne:**
Jaki tryb izolacji repozytorium powinien być domyślny przy interaktywnym uruchamianiu agentów przez CLI / wizard: modyfikacja bieżącego drzewa (Living Tree default) czy izolowane drzewo robocze („local-worktrees safe default”)?

**Kontekst źródłowy:**
Sprzeczność P5 w zestawieniu decyzji:

- Kanon Charteru §3 oraz `LIVING_TREE_RULE.md` v3 stanowi: Living Tree jest domyślnym trybem interaktywnym (zero worktrees), a worktrees są zastrzeżone dla równoległego dispatchu floty.
- 2026-08-18 (6.2): Founder: _„praca ma być prowadzona w worktrees”_.
- 2026-08-25 (9.7): W wizardzie Founder osobiście zatwierdził: _„local-worktrees (safe default)”_.
  W efekcie liczba niescalonych worktree wzrosła do 25, co doprowadziło do ostrej reakcji Foundera (P25):

> _„one miały być kurwa scalone i zeprunowane!”_

**Opcje wyboru:**

- **Opcja 1:** Living Tree jako domyślny tryb pracy interaktywnej; worktree tworzone wyłącznie przy jawnym żądaniu użytkownika (`--worktree`) lub przy dispatchu zautomatyzowanej floty zadań.
  - _Konsekwencje:_ Praca na żywym kodzie bez narzutu na tworzenie i sprzątanie dziesiątek kopii repozytorium.
- **Opcja 2:** Worktrees jako twardy default dla każdego agenta (zarówno w sesji interaktywnej, jak i w zadaniach w tle).
  - _Konsekwencje:_ Bezpieczeństwo przed nadpisaniem lokalnych niescommitowanych zmian użytkownika, lecz konieczność wdrożenia automatycznego prunowania i scalania.
- **Opcja 3:** Tryb adaptacyjny: Living Tree, o ile drzewo gita jest w stanie czystym (`git status --porcelain` puste); w przypadku wykrycia brudnego katalogu roboczego system pyta lub automatycznie tworzy bezpieczne worktree.
  - _Konsekwencje:_ Pełna ochrona przed zniszczeniem niescommitowanej pracy użytkownika bez generowania zbędnych worktree na czystym repo.

**Kotwica:** `orient/aicx-decisions.md` §10 (P5, P25), §6.2 (2026-08-18), §9.7 (2026-08-25), Charter §3.

**Propozycja agenta:** Opcja 1 (dla interaktywnej pracy Foundera) z Opcją 3 jako zabezpieczeniem.

**Werdykt Foundera:** [ ]

---

## C-8 · Doktryna `--no-verify` w pracy agentów i commitach

**Pytanie decyzyjne:**
Czy użycie flagi `--no-verify` w commitach gita jest bezwzględnym zakazem (hard-stop), czy dopuszczalnym standardem roboczym pod warunkiem przejścia Semgrepa?

**Kontekst źródłowy:**
Sprzeczność P23 w dokumentacji:

- 2026-09-06 Founder w sesji głosowej jednoznacznie stwierdził:

> _„no verify to jest normalny, standardowy tryb w tej pracy”_ (podkreślając, że jedynym bezwzględnym strażnikiem pozostaje Semgrep).

- Z kolei w Charterze §4 i instrukcjach dla agentów floty wpisano: `--no-verify` znajduje się na liście nienaruszalnych hard-stopów wymagających zgody człowieka.
  Powoduje to paraliż agentów w sytuacjach, gdy lokalne hooki gita zawodzą na skutek błędów środowiskowych niezwiązanych ze zmianami w kodzie.

**Opcje wyboru:**

- **Opcja 1:** Całkowity zakaz stosowania `--no-verify` przez agentów w każdych warunkach.
  - _Konsekwencje:_ Żaden niesprawdzony przez hooki commit nie trafi do repozytorium, ale agenci mogą utknąć przy defektach samej konfiguracji hooków.
- **Opcja 2:** Przyjęcie doktryny Foundera z 2026-09-06: `--no-verify` jest dopuszczalne dla commitów roboczych pod warunkiem, że Semgrep i testy jednostkowe zostały uruchomione i zaliczone w procesie.
  - _Konsekwencje:_ Zwiększenie autonomii agentów i eliminacja przestojów wywołanych fałszywymi alarmami narzędzi formatujących.
- **Opcja 3:** `--no-verify` dozwolone wyłącznie na prywatnych gałęziach roboczych worktree (`cut/*`), z bezwzględną weryfikacją fail-closed przed włączeniem zmian do gałęzi głównej (merge gate).
  - _Konsekwencje:_ Swoboda zapisu pośrednich etapów pracy w worktree przy zachowaniu sterylności gałęzi release/main.

**Kotwica:** `orient/aicx-decisions.md` §10 (P23), wypowiedź Foundera z 2026-09-06, Charter §4.

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-9 · Rewizja pinów modeli C1–C6 po decyzji „każdy claude ma 1M”

**Pytanie decyzyjne:**
Czy zaktualizować matrycę modeli per cut (ustaloną w sesji Kimiego `de1ed947`), uwzględniając późniejszą ocenę Foundera o nieopłacalności modeli zewnętrznych (Kimi k3) wobec okna kontekstowego 1M w modelach rodziny Claude?

**Kontekst źródłowy:**
W nocnej sesji 2026-09-20 (1.11) Kimi zaproponował sztywne piny: c1/c4 `junie@gemini-3.8-flash`, c2 `cursor@cursor-grok-4.6-xhigh`, c3 `claude@claude-opus-5`, c5 `codex@gpt-5.6-sol`, c6 `kimi@k3`.
Jednak wieczorem tego samego dnia Founder zauważył:

> _„Każdy claude ma już 1M, łącznie z sonnet-5”_
> i wskazał na nieopłacalność utrzymywania Kimi k3-1M. Następnego dnia (2026-09-21, wpis 6.14) Founder osobiście wydał dyspozycję:
> _„/vc-intents native /vc-delegate sonnet-5”_

**Opcje wyboru:**

- **Opcja 1:** Utrzymać heterogeniczną matrycę Kimiego (Junie, Cursor/Grok, Claude, Codex, Kimi).
  - _Konsekwencje:_ Zachowanie różnorodności silników rozumowania, lecz wysokie koszty i częste problemy z uwierzytelnianiem zewnętrznych dostawców.
- **Opcja 2:** Rewizja matrycy w oparciu o modele Claude 1M (Sonnet 5 / Opus 5) jako domyślne dla zadań architektonicznych i rekonstrukcji, z zachowaniem Codexa do specyficznych modułów systemowych.
  - _Konsekwencje:_ Spójność z rachunkiem ekonomicznym Foundera; radykalne ograniczenie tarcia auth i awarii narzędziowych.
- **Opcja 3:** Dynamiczny dobór modelu przez operatora na podstawie bieżących limitów quota i kosztu tokenów (zgodnie z zasadą routingu sterowanego budżetem z 5.9/5.10).
  - _Konsekwencje:_ Optymalizacja kosztowa, ale mniejsza powtarzalność środowiska wykonawczego.

**Kotwica:** `DECISION-MATRIX.md` §B (M-H), `orient/aicx-decisions.md` §1.11, §6.14 (2026-09-20/21).

**Propozycja agenta:** Opcja 2.

**Werdykt Foundera:** [ ]

---

## C-10 · Upgrade generacji runtime bez utraty sesji: wybór wariantu

**Pytanie decyzyjne:**
Który wariant techniczny wdrożyć, aby aktualizacja generacji `vibecrafted` i `vc-frame` odbywała się płynnie bez zabijania procesów PTY i utraty stanu działających sesji agentów?

**Kontekst źródłowy:**
W badaniu `~/.vibecrafted/artifacts/vc-frame_upgrade-without-session-lost.md` (zanalizowanym w `orient/sources-inventory.md` §3.14) wykazano, że uruchomienie `make install` nie zastępuje działających procesów serwera, co skutkuje jednoczesnym działaniem wielu generacji (ghost servers), a jedyna dostępna komenda `kill-session` niszczy pracę agentów. Zestawiono 4 warianty, odnotowując preferencję Foundera dla wariantu B, jednak ostateczna decyzja nie zapadła.

**Opcje wyboru:**

- **Opcja 1:** Wariant B: hosting sesji PTY w `tmux` pod nadzorem serwera `vc-server` (odnotowana preferencja Foundera).
  - _Konsekwencje:_ Sprawdzona, stabilna izolacja procesów powłoki; restart warstwy graficznej `vc-frame` lub aktualizacja kodu nie przerywa sesji terminalowych. Wymaga obecności binarki `tmux`.
- **Opcja 2:** Wariant A / C: natywny mechanizm w Rust — re-exec procesu serwera z dziedziczeniem deskryptorów plików (fd passing) lub dedykowany demon-holder PTY komunikujący się przez `SCM_RIGHTS`.
  - _Konsekwencje:_ Pełna niezależność od narzędzi zewnętrznych, ale wysoka złożoność implementacji i ryzyko błędów termios.
- **Opcja 3:** Wariant D: tryb pasywny — serwer wykrywa niezgodność generacji i ostrzega operatora, blokując destrukcyjne operacje i odkładając restart do momentu zakończenia aktywnych zadań.
  - _Konsekwencje:_ Najprostsza implementacja, eliminuje cichy split-brain, ale wymaga ręcznej koordynacji momentu aktualizacji.

**Kotwica:** `orient/sources-inventory.md` §3.14 (2026-09-16), `orient/aicx-decisions.md` §9.12; `DECISION-MATRIX.md` §C (C-10).

**Propozycja agenta:** Opcja 1 (zgodnie ze wskazaną preferencją Foundera).

**Werdykt Foundera:** [ ]

---

## C-11 · Miejsce renderowania telemetrii: rail vs status bar vs osobny panel

**Pytanie decyzyjne:**
W którym miejscu interfejsu Operator Frame powinny być prezentowane wskaźniki telemetrii floty (tokeny, koszty, stan limitów weekly/5h)?

**Kontekst źródłowy:**
W sesji `01a08a8a` (2026-09-10, wpis 5.12) Founder polecił:

> _„Zapropobuj system metryk w tym zakresie dla vibecrafted w formie zunifikowanego dashboardu telemetrii”_
> W korpusie brak jednak jakiejkolwiek wiążącej decyzji architektonicznej co do wyboru powierzchni UI (L 11.7/29). Z kolei 2026-08-25 (5.5) jedyną zatwierdzoną powierzchnią statusową był macOS menu bar extra.

**Opcje wyboru:**

- **Opcja 1:** Dolny pasek stanu (status bar / compact bar): zwięzłe podsumowanie kosztu i tokenów aktywnej sesji obok wskaźników organów gościa.
  - _Konsekwencje:_ Dane są stale widoczne bez przełączania widoków, ale przestrzeń pozioma na pasku jest silnie ograniczona.
- **Opcja 2:** Dedykowany panel telemetrii w konsoli Voc LUB osobny tab `Telemetry` w Dashboardzie hosta.
  - _Konsekwencje:_ Możliwość prezentacji pełnej analityki: koszt per model, udział cache read, zużycie okna > 150k (zgodnie z wymogami z 5.15). Wymaga jednak celowego otwarcia panelu.
- **Opcja 3:** Architektura dwuwarstwowa: syntetyczny wskaźnik ostrzegawczy (stan limitu) w status barze plus pełny panel analityczno-rozliczeniowy dostępny pod dedykowanym klawiszem w Voc.
  - _Konsekwencje:_ Optymalny kompromis: natychmiastowa widoczność stanów krytycznych bez zaśmiecania głównego ekranu pracy.

**Kotwica:** `orient/aicx-decisions.md` §5.12 (2026-09-10), §5.15 (2026-09-15), §11.7/29; `DECISION-MATRIX.md` §C (C-11).

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-12 · Cykl życia workspace'u: retencja i polityka GC katalogu (527 vs 4)

**Pytanie decyzyjne:**
Jaka formalna reguła retencji i czyszczenia (Garbage Collection) powinna zarządzać katalogiem workspace'ów i metadanymi historycznych runów?

**Kontekst źródłowy:**
Podczas sesji 2026-09-14 (3.6) Founder kategorycznie zażądał usunięcia starych danych z widoku głównego:

> _„527 workspaces, no błagam cię, co tu jest, wszystkie biegi testowe, jakie w ogóle żyły w życiu, czy o co chodzi? … Ja nie mam 527 workspaces, bo widzę swój terminal i w moim terminalu jest cztery workspaces”_
> Wycięto wówczas pasek „runtime truth” z Console home, ale pliki na dysku i wpisy w katalogu pozostały bez żadnej polityki retencji (L 11.7/26), co powoduje rozrost metadanych i spadek wydajności skanowania.

**Opcje wyboru:**

- **Opcja 1:** Ścisły podział: Workspace to wyłącznie żywa sesja Frame lub zadeklarowane repozytorium. Wszystkie historyczne katalogi i metadane runów starsze niż N dni (np. 14 dni) są automatycznie usuwane z dysku.
  - _Konsekwencje:_ Czysty dysk i natychmiastowa responsywność katalogu, lecz bezpowrotna utrata transkrypcji dawnych zadań.
- **Opcja 2:** Brak automatycznego usuwania z dysku: czyszczenie wyłącznie na jawne żądanie Foundera (`vibecrafted workspace prune`), z jednoczesnym odfiltrowaniem widoków UI wyłącznie do sesji aktywnych.
  - _Konsekwencje:_ Stuprocentowe bezpieczeństwo danych historycznych, ale postępujące zużycie przestrzeni dyskowej i konieczność okresowych ręcznych porządków.
- **Opcja 3:** Automatyczna archiwizacja (kompresja do pojedynczych archiwów `.tar.gz` dla zadań > 7 dni) z zachowaniem metadanych w AICX oraz automatyczne usuwanie worktree powiązanych z zamkniętymi cutami.
  - _Konsekwencje:_ Zwolnienie miejsca i porządek w repozytoriach przy zachowaniu pełnej bazy wiedzy w indeksie AICX.

**Kotwica:** `orient/aicx-decisions.md` §3.6 (2026-09-14), §11.7/26; `DECISION-MATRIX.md` §C (C-12).

**Propozycja agenta:** Opcja 3.

**Werdykt Foundera:** [ ]

---

## C-13 · Definicja operacji „detach” jako funkcji produktowej

**Pytanie decyzyjne:**
Jaką semantykę produktową i operacyjną posiada polecenie `detach` w ekosystemie Vibecrafted: czy jest to wyłącznie rozłączenie interfejsu klienta, czy stan wstrzymania sesji?

**Kontekst źródłowy:**
W całym korpusie operacja `detach` pojawia się wyłącznie jako nazwa technicznego testu lub skrót klawiszowy (`^x Disconnect`), bez żadnej definicji produktowej (L 11.7/18). Jednocześnie kanon A16 i wpisy 8.8 oraz 9.10 jednoznacznie rozstrzygają: „Quit App ≠ Stop Runtime”, a serwer jest nadrzędnym opiekunem procesów w tle.

**Opcje wyboru:**

- **Opcja 1:** Czyste rozłączenie widoku (UI-only detach): `detach` zamyka okno terminala / interfejs vc-frame, pozostawiając serwer, procesy agentów, nasłuchy i zadania w pełnym biegu w tle. Ponowny attach wznawia interfejs 1:1.
  - _Konsekwencje:_ Zgodne z filozofią serwera jako opiekuna. Pozwala zamknąć laptopa bez przerywania nocnego dispatchu floty.
- **Opcja 2:** Detach z zawieszeniem (suspend): odłączenie interfejsu wstrzymuje wykonywanie procesów agentów (SIGSTOP / pauza pętli zadań) do czasu ponownego podłączenia klienta.
  - _Konsekwencje:_ Oszczędność tokenów i baterii, ale brak możliwości autonomicznego prowadzenia prac w tle.
- **Opcja 3:** Domyślne rozłączenie UI-only, z możliwością jawnego podania flagi `--freeze` dla sesji wymagających ścisłego nadzoru człowieka.
  - _Konsekwencje:_ Bezpieczna praca w tle jako standard, z precyzyjną kontrolą na żądanie.

**Kotwica:** `orient/aicx-decisions.md` §8.8 (2026-09-07), §8.10 (2026-09-09), §11.7/18; `DECISION-MATRIX.md` §C (C-13).

**Propozycja agenta:** Opcja 1 (zgodnie z kanonem A16 / 8.8: „Quit App ≠ Stop Runtime”).

**Werdykt Foundera:** [ ]

---

## C-14 · Naprawa błędu usage-guard po komendzie `cd` (F02 / P17)

**Pytanie decyzyjne:**
W jaki sposób wyeliminować defekt w mechanizmie `usage-guard`, który ubija proces pracującego agenta (`child.terminate()`), gdy ten wykona polecenie `cd` do innego katalogu roboczego?

**Kontekst źródłowy:**
Guard wprowadzony w commicie `ee905f90` (2026-08-25) porównuje `event["cwd"]` każdego zdarzenia telemetrii ze zdefiniowanym `effective_root` sesji. W przypadku wykrycia niezgodności rzuca `RuntimeError` i natychmiast ubija proces potomny. 2026-09-15 wywołało to ostrą reakcję Foundera:

> _„Ponownie WRRR! … provider usage event belongs to a foreign workspace”_
> Tego dnia padły co najmniej dwa legalne runy po wejściu agenta do sąsiedniego katalogu `../vc-frame` (L 8.15 / P17).

**Opcje wyboru:**

- **Opcja 1:** Ignorowanie lub filtracja obcych ścieżek `cwd` w module zliczania zużycia telemetrii bez rzucania wyjątku i bez zabijania procesu agenta.
  - _Konsekwencje:_ Agent zachowuje pełną swobodę poruszania się po strukturze katalogów repozytorium suite; zużycie tokenów jest nadal rejestrowane w sesji macierzystej.
- **Opcja 2:** Rozszerzenie listy dopuszczalnych ścieżek (`allowed_roots`) o całe nadrzędne drzewo projektu (workspace suite), z blokadą wyłącznie ucieczki do ścieżek systemowych (`/tmp`, `/var`, katalog domowy).
  - _Konsekwencje:_ Ochrona przed niekontrolowanym rozproszeniem pracy przy zachowaniu swobody pracy w repozytoriach wielomodułowych.
- **Opcja 3:** Zastąpienie twardego błędu i metody `terminate()` ostrzeżeniem w logu (warning) oraz przypisaniem kosztu do kategorii `unattributed_workspace_usage`.
  - _Konsekwencje:_ Całkowite wyeliminowanie awarii wykonania przy zachowaniu stuprocentowej audytowalności anomalii ścieżek.

**Kotwica:** `orient/aicx-decisions.md` §8.15 (2026-09-15), §10 (P17); commit `ee905f90`.

**Propozycja agenta:** Opcja 1 w połączeniu z Opcją 3.

**Werdykt Foundera:** [ ]

---

## C-15 · Cenniki modeli w `telemetry.py`: uzupełnienie stawek i estymacja kosztów

**Pytanie decyzyjne:**
W jaki sposób traktować brakujące stawki modeli w pliku `telemetry.py` (obecnie 593 runy mają `cost_source=unknown`, a 528 runów posiada zliczone tokeny, lecz zerowy koszt w USD)?

**Kontekst źródłowy:**
Tabela stawek w `vibecrafted-core/vibecrafted_core/telemetry.py` obejmuje wyłącznie wybrane modele Grok i rodziny GPT-5.x (L 11.7/31, wpis 5.19). Brak stawek dla Claude (Opus/Sonnet/Haiku), Kimi i modeli lokalnych sprawia, że baza danych zlicza tokeny, ale fałszuje bilans finansowy floty. 2026-09-15 Founder zażądał pełnej wiedzy o kosztach:

> _„czyli jaki był koszt - daj kompletną tabelę”_

**Opcje wyboru:**

- **Opcja 1:** Uzupełnić tabelę stawek w kodzie o oficjalne ceny API (dla rodziny Claude 3.5/Opus/Sonnet, Kimi) i wyliczać koszt z jawnie ustawioną etykietą `cost_source: "estimated"`.
  - _Konsekwencje:_ Prawdziwy, szacunkowy obraz wydatków floty w panelu telemetrycznym bez udawania, że dane pochodzą bezpośrednio z faktury providera.
- **Opcja 2:** Rejestrować koszt w USD wyłącznie wtedy, gdy provider sam go bezpośrednio przekazał w strumieniu zdarzeń (`cost_source: "provider_reported"`), a w pozostałych przypadkach pozostawiać wartość pustą (`null` / `unknown`).
  - _Konsekwencje:_ Rygorystyczna prawda źródłowa bez założeń o cennikach, lecz większość runów w Dashboardzie pozostaje bez informacji o kosztach.
- **Opcja 3:** Wyprowadzić tabelę stawek do zewnętrznego pliku konfiguracyjnego `rates.toml` (lub pobieranego manifestu cen), co pozwoli na aktualizację cenników bez modyfikowania kodu silnika.
  - _Konsekwencje:_ Elastyczność przy zmianach taryf dostawców; możliwość definiowania własnych stawek dla modeli lokalnych i chmurowych.

**Kotwica:** `orient/aicx-decisions.md` §5.19 (2026-09-15), §11.7/31; `DECISION-MATRIX.md` §C (C-15).

**Propozycja agenta:** Opcja 1 w połączeniu z Opcją 3.

**Werdykt Foundera:** [ ]

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
