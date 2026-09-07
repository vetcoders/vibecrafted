# Składanie promptu — odwrotna checklista

vc-dispatch nie niesie **żadnego kanonicznego szablonu**. To skill wykonawczy:
wyczuwa kontekst osadzenia i weryfikuje, że złożony prompt POKRYWA wymagane
pola. Plany nadrzędnego flow, repozytoryjne CLAUDE.md/AGENTS.md oraz evidence
z vc-init są materiałem źródłowym; checklista poniżej jest bramką.

## Wyczuwanie kontekstu (przed złożeniem czegokolwiek)

1. Jaki jest nadrzędny flow? (faza vc-workflow, linia vc-ship, ad-hocowe
   zlecenie operatora) — jego artefakty dyktują kształt briefu i cele raportów.
2. Gdzie mieszkają artefakty tej linii?
   (`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports}` —
   uwaga: case-insensitive APFS może pokazać dwie pisownie jednego katalogu).
3. Czego wymaga kontrakt repo? (CLAUDE.md: format commita, hooki, nietykalne
   ścieżki, precedencja configu, footguny języka/edycji.)
4. Co ruszyło się na Living Tree, odkąd napisano briefy? (`git log` od baseline'u
   — to staje się treścią EXTRA i BATON.)

## Cztery warstwy (jeden plik .md, w tej kolejności)

### 1. COMMON — kontrakt środowiskowy

Musi pokryć (złożone Z kontekstu, nie skopiowane z szablonu):

- [ ] ścieżka repo + gałąź; tryb drzewa wprost: Living Tree (zero worktree,
      zero przełączania gałęzi, re-read przed edycją, nigdy stash/discard
      cudzej pracy) ALBO formacja Fleet Worktrees (Tryb B kanonu: verifiery
      przed dispatchem, rozłączne domeny, `.claude/worktrees/<cut-id>` na
      `cut/<cut-id>`, workerzy bez push/merge, integruje koordynator)
- [ ] kolejność narzędzi prawdy strukturalnej (loctree-first; ścieżka raportu
      fallbacku dla miss)
- [ ] inwarianty architektury (np. prezentacja w app/ nigdy core/) oraz
      NIETYKALNE ścieżki/wartości, precedencja configu
- [ ] footguny języka/toolchaina istotne dla repo (np. Rust 2024 if-let temp
      scope: snapshot-into-let przed `if let` na lockach)
- [ ] twarde zakazy: ZERO pushu workera/PR/release; `--no-verify` wyłącznie dla
      lokalnego checkpointu compile embargo jawnie autoryzowanego przez
      Foundera, z receiptem pominiętych bramek; push z nim jest Founder-only;
      tabu lintera
      repo (no unwrap(), no sleep() in tests, …)
- [ ] kontrakt commita: format + trailery, które egzekwuje hook (własna
      tożsamość agenta/runtime'u workera, prawdziwy session id, komenda date)
- [ ] linijka gates-before-commit (worker je uruchamia; dyspozytor nie)
- [ ] furtka ucieczkowa SUBSTRATE_FAILURE: zatrute drzewo → żadnego
      half-commita, zamiast tego zaraportuj linię awarii
- [ ] ścieżka REPORT: `<reports_dir>/<cut_id>_report.md` + wymagane sekcje
      (pliki, evidence bramek, acceptance [x]/[?]/[!], unverified, next step,
      SHA commita + 3 fakty)

### 2. BRIEF — pełny brief cięcia

- [ ] wklejony W CAŁOŚCI, nigdy streszczony (brief jest spec)
- [ ] kotwice (file:line) rozumiane jako wskazówki — żywe drzewo jest prawdą

### 3. EXTRA — korekty względem HEAD briefu

- [ ] „brief napisany przy <SHA>, drzewo ruszyło — ufaj żywemu drzewu" z
      konkretnymi deltami, które dotykają plików tego cięcia
- [ ] hardening bramek z pre-flight (≥1 nowy nietrywialny test tam, gdzie
      baseline był 0; podmienione flaky verify)
- [ ] śruby bezpieczeństwa: DIVERGED-STOP, ogrodzenia scope'u („nie wchodź
      w pliki cięcia X"), klauzula idempotencji dla refire („jeśli już dowiezione
      na drzewie: zweryfikuj acceptance i zatrzymaj się — nie duplikuj")
- [ ] fazowanie dla wielkich cięć: zacommituj działający podzbiór + uczciwy
      raport zamiast półproduktu rozsmarowanego po N plikach

### 4. BATON — stan linii od dyspozytora

- [ ] które cięcia są [x], ich SHA commitów, które pliki dotknęły
- [ ] wprost „HEAD może iść do przodu, gdy pracujesz; operator testuje żywą
      aplikację równolegle — re-read przed edycją"
- [ ] pre-handoff baseline dla workera przejmującego: branch, SHA HEAD,
      `git status --short`, zmienione pliki, bramki już uruchomione, znane
      awarie, niezweryfikowane powierzchnie, bieżąca intencja, ogrodzenie
      scope'u i dokładna następna instrukcja/ścieżka raportu
- [ ] dla recovery-dispatch: co poprzedni run zostawił / czego nie zostawił
      („nie dziedziczysz nic" albo dokładny opis WIP), z evidence
- [ ] co przychodzi po tym cięciu (żeby worker ogrodził swój scope)

## Mechaniczne bramki przed launchem

```bash
grep -c '{repo}\|{id}\|{reports_dir}\|{[a-z_]*}' prompt.md   # MUST be 0
wc -l prompt.md                                              # sanity: full brief present
```

- **Pin modelu obecny i zgodny z klasą cuta**: cut niesie pin `model` (tańszy,
  szybszy tier dla mechanicznego, w pełni rozpisanego cuta; mocniejszy tier
  dla chirurgicznego lub niosącego decyzje). Brak pinu = default konta, czyli
  NIE-decyzja — rozwiąż przed startem.

Launch tylko przez plik:

```bash
bash -c 'ulimit -f unlimited; vibecrafted <skill> <agent> --file <prompt.md>'
```

## Reguła idempotencji (gotowość na refire)

Każdy prompt musi pozostać bezpieczny do re-fire'a verbatim: kryteria
acceptance są sprawdzalne względem drzewa, EXTRA zawiera klauzulę
„verify-and-stop if done", a deklaracja dziedziczenia z BATON zostaje prawdziwa
po częściowej rundzie (refire czyta drzewo, nie twoją pamięć). Jeśli promptu nie
da się bezpiecznie re-fire'ować, nie jest skończony.

## Reguła evidence checkpointów

Nie pozwalaj, żeby prompty workerów traktowały baseline, bramki, raporty albo
handoff notes jak ceremonię. To granice atrybucji regresji. Pominięcie ich to
regression laundering: późniejsza awaria traci właściciela, czas i segment
lifecycle.
