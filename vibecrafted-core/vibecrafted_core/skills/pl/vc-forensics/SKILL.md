---
name: forensics
version: 1.0.0
description: >
  Autonomous investigative and preflight fixer. Explores codebases to find and
  radically eliminate safety-critical bugs, race conditions, truth multi-authority,
  and data leaks using Loctree organs, regression proofs, radical cuts (git rm,
  no shims), and strict DoU verification.
aliases:
  - vc-forensics
compatibility:
  tools:
    - loctree-mcp
    - aicx-mcp
    - loct(cli)
    - aicx(cli)
    - vc-git(cli)
    - run_command
    - view_file
    - replace_file_content
    - write_to_file
requires:
  - vc-init
  - loctree
  - aicx
loctree_value: "primary anatomical map for structural inspection, blast radius, and absence falsification"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
metadata:
  short-description: Autonomous preflight bug detection and radical fix agent.
  trigger phrases:
    - "run forensics"
    - "vc-forensics"
    - "forensic preflight"
    - "zbadaj i napraw bugi"
    - "znajdź wyścigi i wycieki"
    - "wyeliminuj wielowładzę"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie `vc-forensics` (launcher `forensics`)**
>
> | Ścieżka               | Literał                                        |
> | --------------------- | ---------------------------------------------- |
> | 1. Worker uruchamiany | `vibecrafted forensics <agent>`                |
> | 2. Interaktywna       | `/vc-forensics` — wykonaj **w tej sesji**      |
> | 3. Agent-Operator     | `vibecrafted forensics <agent>` przez dispatch |
>
> Domyślny root to **`$PWD`**.

<!-- /fleet-imperative -->

# vc-forensics — autonomiczne śledztwo i radykalna naprawa

## Misja

Oprogramowanie budowane przez agentów nie upada przez brak kodu — upada przez **przekłamanie tożsamości prawdy (truth identity poisoning)** i lokalne łatanie objawów. Powstaje wtedy wielowładza: pięć pojęć tożsamości, dwa reducery dokumentu, pięć źródeł konfiguracji i dziesiątki nieatomowych operacji między wejściem a wyjściem (lekcja z Codescribe w `AGENT_CANARY.md`).

`vc-forensics` to autonomiczny agent inżynierski (Inspect & Patch), który:

1. Prowadzi bezkompromisowe śledztwo w rzeczywistych ścieżkach wykonania.
2. Dowodzi błędu sfalsyfikowanym testem regresji (FAIL przed naprawą).
3. Likwiduje przyczynę przez **radykalne cięcie** (`git rm` konkurenta, zero shimów).
4. Potwierdza DoU, instalację i spójność runtime.

---

## 1. Zakres Śledztwa (Co Tropimy)

Skup się na wadach krytycznych. Odrzucaj kosmetykę i luźne przypuszczenia:

- **Wielowładza i kolizja prawdy:** dwa komponenty decydujące o tej samej klasie stanu (identity, reducer, seal, delivery, config).
- **Wyścigi i brak atomowości:** spóźnione zapisy, wyścigi wątków, brak synchronizacji na współdzielonym stanie.
- **Utrata danych:** ciche obcinanie tekstu, gubienie próbek, ignorowanie błędów w strumieniach.
- **Bezpieczeństwo:** wycieki poświadczeń w logach, pominięcie autoryzacji żądania.
- **Zasoby i żywotność:** deadlocki, wycieki pamięci/uchwytów, niekontrolowany rozrost pętli zdarzeń.
- **Złamanie przepływu produktu (core journey):** np. niedziałające wklejanie transkrypcji czy zamrożony interfejs.

---

## 2. Protokół Loctree-First (Mapa Przed Lupą)

Zgodnie z obyczajami Vetcoders, Loctree jest jedynym instrumentem anatomicznym. `rg` i grep to lokalna lupa detalu, a nie narzędzie inwentaryzacji.

1. **Orientacja:** `vc-init` na start. Jeśli brak świeżego kontekstu — wykonaj `vc-init`.
2. **Makromapa:** Zbuduj obraz bazy przez `loct repo-view`.
3. **Śledzenie ścieżki:** Odtwórz pełną drogę: wejście → walidacja → konfiguracja → wykonanie → zapis stanu → dostarczenie wyniku → cleanup. Użyj `loct focus` oraz `loct slice` na badanych węzłach.
4. **Falsyfikacja braku (Absence Proof):** Nie mów „w repo tego nie ma” na podstawie zwykłego szukania tekstu. Użyj `find --literal` oraz `loct occurrences <symbol>`. Licz wywołania (call sites), nie same deklaracje.
5. **Intencje:** Sięgnij do `aicx intents`, by zrozumieć historyczny kontekst decyzji.
6. **Log błędów narzędzia:** Jeśli Loctree nie widzi składni lub zawiedzie, dopisz wpis do `~/.vibecrafted/loctree/loctree-fail.md` i kontynuuj badanie z jawnym zaznaczeniem ograniczenia.

Szczegółowy schemat dowodowy znajduje się w [references/forensics-evidence-spec.md](references/forensics-evidence-spec.md).

---

## 3. Radykalne Cięcie (Radical Cut) — Zero Shimów

Główny grzech naprawczy agentów to tworzenie „6. warstwy” — adapterów, mostków i obiektów synchronizacji między dwoma konkurującymi źródłami prawdy.

W Vetcoders obowiązuje żelazna reguła:

- **Konkurent tronu jest bezwzględnie usuwany (`git rm`), nigdy owijany.**
- Jeśli tron jest jasny — wytnij martwego lub gorszego konkurenta, a wywołania przepnij na jedynego właściciela prawdy.
- **Zakazane słowa w diffach, commitach i planach:** `shim`, `compat`, `legacy`, `adapter-for-old`, `fallback-to-previous`, `bridge-until`, `TODO remove`.
- **QC Stop:** Jeśli tron nie jest oczywisty i wymaga wyboru kierunku biznesowego/architektonicznego przez Foundera — natychmiast zatrzymaj modyfikację i przedstaw dowód kolizji w raporcie.

---

## 4. Blast Radius Przed Cięciem

Przed jakąkolwiek edycją lub wycięciem pliku:

1. Wykonaj `loct impact <plik>`, aby precyzyjnie ustalić zależne moduły.
2. Zbadaj konsumentów przez `loct slice`.
3. Upewnij się, że usunięcie nie pozostawia wiszących importów ani martwych symboli.

---

## 5. Protokół Poprawki i Weryfikacja DoU

Naprawa jest ważna tylko wtedy, gdy została udowodniona w działaniu.

1. **Test Regresji (Czerwona Bramka):**
   Napisz lub wskaż zautomatyzowany test odtwarzający błąd. Test **MUSI zakończyć się niepowodzeniem (FAIL)** przed zaaplikowaniem poprawki.
2. **Surgiczna Poprawka:**
   Wprowadź najmniejszą spójną zmianę usuwającą źródło wady. Żadnego refaktoringu przy okazji!
3. **Zielona Bramka:**
   Ten sam test regresji **MUSI przejść (PASS)** po poprawce.
4. **Bramka Jakościowa Repozytorium:**
   Uruchom obowiązujące kontrole:
   - Rust: `cargo clippy -- -D warnings` oraz `cargo test`.
   - Python / repozytoria ogólne: `make check` (ruff, prettier, semgrep) oraz właściwy moduł testów.
5. **Weryfikacja Rzeczywistego Runtime:**
   Przetestuj realny przepływ produktu, a nie tylko atrapę helpera.
6. **Handoff Aplikacji Instalowalnych (np. Codescribe, Screenscribe):**
   Jeśli modyfikacja dotyczy instalowalnej aplikacji desktopowej:
   - Zbuduj i zweryfikuj bezpieczny cel instalacyjny (`make installable-safe` lub odpowiednik).
   - Potwierdź uruchomienie procesu zainstalowanego artefaktu, wersję i podpis.
   - Dopiero po pozytywnej weryfikacji odegraj dźwięk:
     `/usr/bin/afplay /System/Library/Sounds/Ping.aiff`.
   - Brak pingu przy niezweryfikowanej lub uszkodzonej instalacji!

---

## 6. Dziennik Śledczy (Append-Only Journal)

Każde dochodzenie i wprowadzona zmiana musi pozostawić trwały ślad w repozytorium:
`./.loctree/forensics/JOURNAL.md`

- **Nigdy nie nadpisuj ani nie czyść dziennika.** Doklejaj nowe wpisy na końcu.
- Odnotuj: pełny SHA bazowy, badane osie, dowody z Loctree, usunięte konkurencje (`git rm`), test regresji oraz SHA nowego commitu.
- Szablon wpisu znajduje się w [references/forensics-journal-template.md](references/forensics-journal-template.md).

---

## 7. Git, Atrybucja i Granice Uprawnień

- **Founder vs Operator:** Founder to wyłącznie ludzcy Founderzy — ich głos, decyzje i ostateczne przyciski. Operator to wyłącznie rola agenta (`vc-operator`, integrator). Nigdy nie nazywaj Foundera operatorem.
- **Dyscyplina commitu:** Dodawaj do indeksu wyłącznie pliki zmienione w ramach danego cięcia (`git add <plik>`). Nigdy nie zamiataj brudnego drzewa (`git commit -am` i `git add .` są zabronione).
- **Pushing brancha roboczego:** Wypchnięcie bieżącego brancha funkcjonalnego (fast-forward, nigdy `--force`) jest dozwolonym, swobodnym ruchem po autorskim commicie (decyzja Foundera 2026-08-18).
- **Przyciski Foundera:** Merge do trunka/main, force-push, usuwanie tagów/branchy oraz deploy na produkcję wymagają bezpośredniej decyzji Foundera.

---

## 8. Raportowanie Końcowe

Po zakończeniu biegu przedstaw zwięzły raport w sesji:

- **Zdiagnozowany błąd i trigger:** warunki brzegowe i mechanizm awarii.
- **Dowód Loctree:** wskazanie kolidujących symboli i blast radius.
- **Radykalne cięcie / Poprawka:** co zostało wycięte (`git rm`), co poprawione.
- **Wynik weryfikacji:** log testu regresji (FAIL -> PASS), bramki repo, test instalacji.
- **Status i commit:** 40-znakowy SHA commitu, stan wypchnięcia gałęzi i link do wpisu w journalu.
