# vc-review — FINDINGS: Procedura analizy Fazy 2

> Kolejność czytania, obowiązkowe skany wzorców, przebieg adwersaryjny,
> minimalne wymagania pokrycia oraz pełny szablon wyjścia. Faza 2 skilla
> vc-review.

Czytaj razem z [`SKILL.md`](SKILL.md) i [`PRVIEW.md`](PRVIEW.md).

---

## Filozofia

Tryb: **Findings-max**. Nie kończ na „kilku punktach". Jeśli widać
25 osobnych problemów, wypisz 25. Lepiej 20 celnych findingów niż 5
ogólników.

Każdy finding MUSI mieć:

- **Stopień dowodu** (STRONG / MEDIUM / WEAK / NONE) — zobacz SKILL.md
- **Evidence pozytywny** — artefakt + ścieżka (1–2 linie z patcha/logu)
- **Negatywne sprawdzenie** — „old path removed at …" lub „WEAK — not confirmed"
- **Evidence testowy** — `<test-path>::<name>` lub „WEAK — no targeted test"
- **Komentarz** — dlaczego to ważne (1 zdanie) + co grozi
- **Rekomendacja** — co zrobić / jak zweryfikować

Zasady:

- Jeden punkt = jeden problem (nie łącz tematów).
- Czego nie da się potwierdzić z artefaktów: oznacz **[VERIFY]**.
- Rozdzielaj **problem w kodzie** vs **problem narzędzia [TOOLING]**.

---

## Kolejność czytania (obowiązkowa)

1. **`AI_INDEX.md`** (jeśli istnieje) — zweryfikuj, że wskazuje na realne ścieżki.
   Kłamliwy indeks → P3 [TOOLING].
2. **`report.json`** (domyślnie) — `meta`, `gate.allow_merge` +
   `policy_mode` + reasons, `checks[]` (status / log_path / command),
   `diff.stats` + `diff.files[]` (skala / churn / hotspoty),
   `quality` (breaking / coverage / sarif / heuristics).
3. **`00_summary/MERGE_GATE.json` + `SANITY.json`** — porównaj
   krzyżowo z `report.json`. Niespójność → P2 [TOOLING]. „All checks
   passed" z WARN / INLINE_FINDINGS = mylące.
4. **`00_summary/pr-metadata.txt` + `file-status.txt` +
   `commit-list.txt`** — scope, kategorie A/M/D, progresja commitów.
   Szukaj dryfu gałęzi (pliki infra poza scope PR-a).
5. **`30_context/INLINE_FINDINGS.sarif`** — każdy wynik SARIF =
   gotowy finding. Przenieś wszystkie na listę findings.
6. **`20_quality/*`** — bramki PASS: wyciągnij ostrzeżenia z logów (cargo
   warns, tsc non-errors). WARN / ERROR / FAIL: przyczyna źródłowa +
   rekomendacja. `checks-errors.log` po przefiltrowane błędy o wysokim sygnale.
   `BREAKING_CHANGES.md`: oceń realną wagę (P?).
   `coverage-delta.txt`: oflaguj krytyczne wpisy „NO_TEST_CHANGE".
7. **`30_context/changed-tests.txt`** — skoreluj ze zmianami w
   źródłach. Nieprzetestowane pliki źródłowe → finding.
8. **Diffy (selektywnie)** — `10_diff/per-file-diffs/00-INDEX.txt` po
   najwyższy churn. Patche per-file dla hotspotów. `10_diff/per-commit-diffs/
00-SUMMARY.md` dla commitów o najwyższym wpływie.

---

## Obowiązkowe skany wzorców

Skanuj patche per-file oraz `full.patch` pod kątem:

**Rust** — `.unwrap()`, `.expect(`, `panic!`, `todo!`, `unsafe`,
`dbg!`, `println!`, `#[allow(`

**TypeScript / JavaScript** — `any`, `as unknown as` (podwójny cast),
`@ts-ignore`, `@ts-expect-error`, `eslint-disable`,
`// TODO|FIXME|HACK`, puste `catch {}` bez logu/rethrow, non-null
assertion `!` na niepewnych wartościach, `console.log|warn|error` (powinno
używać secureLogger w example-app)

**Security / PII** — logowanie tokenów / e-maili / haseł / osobistych
ID, nowa telemetria bez przeglądu prywatności, nowe endpointy / command
handlery bez sprawdzeń auth, hardcodowane URL-e / klucze / sekrety

**Data / Performance** — zapytanie w pętli (N+1), brak batchowania dla
operacji masowych, duże payloady bez paginacji, zbędne I/O w hot
pathach

Każde „trafienie" w diffie = potencjalny finding z evidence.

---

## Przebieg adwersaryjny

Po skanach wzorców, przed napisaniem raportu finalnego, uruchom jawny
przebieg adwersaryjny. Dla każdego kandydata na finding odpowiedz na
wszystkie trzy:

1. **Evidence pozytywny:** jaki kod w diffie zdaje się wprowadzać tę
   obawę?
2. **Evidence negatywny:** czy przestarzałe / zakazane zachowanie nadal
   jest obecne gdzie indziej w diffie (lub gdzie indziej w drzewie, jeśli
   diff twierdzi, że je usuwa)?
3. **Siła testu:** czy test w diffie asertuje **dokładnie** zachowanie
   implikowane przez opis PR-a? Czy kod mógłby być zepsuty, a test i tak
   by przechodził?

Findingi, które nie przejdą przebiegu adwersaryjnego, zostają zdegradowane:

- Negatywne sprawdzenie nie wypada (stara ścieżka wciąż obecna) → podnieś severity o jeden poziom P
- Test asertuje coś słabszego niż wymagane → ogranicz evidence do MEDIUM
- Brak testu dla deklarowanej zmiany zachowania → ogranicz evidence do WEAK;
  severity zostaje

---

## Minimalne wymagania pokrycia

Aby zapobiec lakonicznym raportom:

- **Wszystkie** wpisy w `INLINE_FINDINGS.sarif`
- **Top 10** plików wg churn — przeczytaj patch per-file
- **Wszystkie** pliki w kategoriach core risk: auth, payments, database,
  session, security, encryption, middleware
- **Wszystkie** krytyczne wpisy „NO_TEST_CHANGE" z `coverage-delta.txt`
- **Wszystkie** wpisy w `BREAKING_CHANGES.md` z ocenionym poziomem P
- **Progresja per-commit** dla PR-ów z >5 commitami — zidentyfikuj
  fazy, ryzykowne przejścia

---

## Format wyjścia (obowiązkowy)

Trzy obowiązkowe sekcje, w tej kolejności.

### 1) Findingi (P0/P1/P2/P3 ze stopniem dowodu)

```
- **[P?][EVIDENCE: STRONG|MEDIUM|WEAK|NONE] <Title>**
  (optionally: [VERIFY], [TOOLING], [STAGE-OK-DEFERRED],
  [STAGE-PARTIAL], [STAGE-DRIFT])
  - **Positive evidence:** `<artifact-path>` + `<file:line>` + short fragment
  - **Negative check:** "old path removed at <file:line>" OR
    "WEAK — old path not confirmed removed" OR "N/A — no removal claim"
  - **Test evidence:** `<test-path>::<test-name>` + 1 line of assertion,
    or "WEAK — no targeted test for this behavior"
  - **Comment:** 1 sentence on risk / impact
  - **Recommendation:** concrete "what to do" / "how to verify"
  - **Owner:** `author` / `reviewer` / `infra` (optional)
```

Numeruj do odsyłaczy: `P1-01`, `P1-02`, `P2-01`, itd.

Stopień dowodu jest **obowiązkowy** na każdym findingu. Finding bez
stopnia dowodu jest sam w sobie luką procesu.

### 2) Before-Merge TODO (markdownowe checkboxy)

```markdown
- [ ] **(P0)** ... (ref: P0-01)
- [ ] **(P1)** ... (ref: P1-01, P1-02)
- [ ] **(P2)** ... (ref: P2-01)
- [ ] **(P3)** ... (ref: P3-01)
```

Każdy TODO odsyła do ID findings. Dołącz komendy weryfikacyjne w
code fence'ach tam, gdzie to zasadne.

### 3) Self-Attack Pass (przebieg autoataku) + Model Check

Dla każdego findingu otagowanego `[EVIDENCE: STRONG]` oraz każdej
rekomendacji „ready to merge" odpowiedz w jednej linii każda:

- **Najsilniejszy powód, dla którego ten verdict może być błędny:** _<odpowiedź>_
- **Czego nie zweryfikowałem bezpośrednio:** _<odpowiedź>_
- **Najszybszy falsyfikator:** _<komenda lub test, który wyłapałby lukę>_

Jeśli jakiś verdict STRONG ma wiarygodny falsyfikator, zdegraduj do MEDIUM
lub WEAK i przerankuj poziom P. Nie chroń swojej pierwszej odpowiedzi.

Wyemituj jednoliniowy model check:

```
model_confidence: high | medium | low — <one-sentence why>
```

Jeśli confidence ≠ `high`, wyjście review nie może rekomendować „merge
as-is" — tylko „merge after operator verifies <X>".

---

## Sekcje opcjonalne

Dodaj, gdy wnoszą wartość:

- **Executive Summary** (max 8 punktów): verdict bramki, top 3 ryzyka,
  sygnał testów, top hotspoty, delta scope
- **Architecture Context** — diagram lub opis dotkniętego podsystemu
- **Scope / What Changed** — na podstawie `diff.stats` + top katalogi + top pliki
- **Commit Progression** — fazy dla PR-ów wielocommitowych
- **Test Coverage Matrix** — źródło → test → liczba nowych testów
- **Security & Privacy Check** — PII w logach, przepływy danych, filtrowanie zdarzeń
- **QA Plan** — 5–15 rekomendacji testów manualnych + automatycznych
- **Evidence Index** — linki do kluczowych użytych artefaktów

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
