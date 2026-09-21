# vc-forensics — Schemat Dowodowy i Falsyfikacja

Ten dokument definiuje standard dowodu dla skilla `vc-forensics`. Każdy zgłoszony błąd poprawności, wyścig lub kolizja wielowładzy musi spełniać poniższy rygor, zanim agent przystąpi do cięcia i naprawy.

---

## 1. Kryteria Istotności Błędu (Severity Threshold)

`vc-forensics` bada wyłącznie wady krytyczne dla integralności i stabilności:

1. **Wielowładza i kolizja prawdy** — dwa lub więcej komponentów konkurujących o tę samą klasę decyzji (identity, reducer, konfiguracja, seal, delivery).
2. **Race conditions i brak współbieżnej atomowości** — spóźnione zapisy nadpisujące nowszy stan, wyścigi między wątkami/taskami, niechroniony stan współdzielony.
3. **Utrata, zniekształcenie lub ciche obcięcie danych** — utrata próbek audio, obcięcie tekstu w pipeline, ciche połykanie błędów.
4. **Naruszenie autoryzacji i wycieki poświadczeń** — wykonywanie akcji bez uprawnień, tokeny w logach/wyjątkach, niezgodność odbiorcy żądania.
5. **Crash, deadlock, wyciek zasobów** — zawieszenie pętli zdarzeń, niezwolnione uchwyty plików/pamięci, nieskończony wzrost zużycia zasobów.
6. **Uszkodzenie podstawowego przepływu użytkownika (core user journey)** — np. brak możliwości wklejenia transkrypcji w Codescribe, zamrożenie GUI.

Kosmetyka, styl, formatowanie i hipotetyczne scenariusze bez ścieżki wywołania są odrzucane.

---

## 2. Schemat Raportu Dowodowego (Evidence Record)

Dla każdego potwierdzonego problemu agent generuje w pamięci i journalu rekord:

```yaml
finding_id: "FORENSIC-YYYYMMDD-01"
title: "Krótki, precyzyjny tytuł wskazujący istotę błędu"
classification: "CUT_BLOCKER | CUT_COHERENT | FOLLOW_UP"
legend_mark: "🔥 | ⚠ | ◌" # 🔥 runtime collision, ⚠ phase/mode split, ◌ test/offline
affected_components:
  - path: "sciezka/do/pliku.rs"
    lines: "120-145"
    symbol: "nazwa_funkcji_lub_typu"
  - path: "sciezka/do/konkurenta.rs"
    lines: "80-110"
    symbol: "konkurencyjny_symbol"
root_cause:
  mechanism: "Opis mechanizmu błędu w kodzie (np. nieatomowy update, podwójny reducer)"
  loctree_evidence:
    slice: "Wynik loct slice wskazujący wywołania"
    occurrences: "Liczba i lokalizacja odwołań z loct occurrences"
    impact: "Wynik loct impact przed wykonaniem cięcia"
trigger_scenario:
  preconditions: "Stan wejściowy systemu"
  steps:
    - "Krok 1 wywołania"
    - "Krok 2 wywołania"
  expected_behavior: "Zachowanie poprawne zgodnie z kontraktem"
  actual_behavior: "Zachowanie wadliwe (z dowodem/logiem/błędem)"
regression_test:
  path: "tests/sciezka/test_regresji.rs"
  failing_output_before_fix: "Log z wykonania testu wykazujący FAIL"
  passing_output_after_fix: "Log z wykonania testu wykazujący PASS"
falsification_attempt:
  hypothesis_tested: "Czy to może być legalny wariant (np. replay vs runtime)?"
  rebuttal_proof: "Dowód, dlaczego to jest rzeczywisty błąd (np. wspólna ścieżka wykonania)"
resolution:
  disposition: "RADICAL_CUT | SURGICAL_FIX | QC_STOP"
  removed_competitor: "sciezka/do/usunietego_pliku (jeśli git rm)"
  commit_sha: "Pełny 40-znakowy SHA commitu po poprawce"
```

---

## 3. Falsyfikacja Braku (Absence Falsification)

Nie wolno twierdzić, że coś „nie występuje w repozytorium” na podstawie powierzchownego zapytania tekstowego.

- Zamiast luźnego `rg`, używaj `find --literal` oraz `loct occurrences <symbol>`.
- Sprawdzaj liczbę przeskanowanych plików i kompletność skanu (`scan_complete == true`).
- Pamiętaj: zliczamy **odwołania i miejsca wywołań (call sites)**, a nie same definicje typów!
