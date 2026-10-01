# Prompt assembly — the reverse checklist

vc-dispatch nie niesie **żadnego kanonicznego szablonu**. To skill wykonawczy:
wyczuwa kontekst osadzenia i weryfikuje, że złożony prompt POKRYWA wymagane
pola. Plany nadrzędnego flow, repozytoryjne CLAUDE.md/AGENTS.md oraz evidence
z vc-init są materiałem źródłowym; checklista poniżej jest bramką.

## Context sensing (before composing anything)

1. Jaki jest nadrzędny flow? (faza vc-workflow, linia vc-ship, ad-hocowe
   zlecenie operatora) — jego artefakty dyktują kształt briefu i cele raportów.
2. Gdzie mieszkają artefakty tej linii?
   (`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/{plans,reports}` —
   uwaga: case-insensitive APFS może pokazać dwie pisownie jednego katalogu).
3. Czego wymaga kontrakt repo? (CLAUDE.md: format commita, hooki, nietykalne
   ścieżki, precedencja configu, footguny języka/edycji.)
4. Co ruszyło się na Living Tree, odkąd napisano briefy? (`git log` od baseline'u
   — to staje się treścią EXTRA i BATON.)

## The four layers (one .md file, in this order)

### 1. COMMON — environment contract

Musi pokryć (złożone Z kontekstu, nie skopiowane z szablonu):

- [ ] wybrany runtime + parent/effective roots + baseline branch/full SHA;
      szanuj jawny wybór Foundera: `local-worktrees`, `local-native`/Living Tree
      albo VM. Supervisor typowanego dispatchu daje dedykowany checkout workera
      na `cut/<cut-id>`; bez drugiego worktree tworzonego przez workera,
      przełączania gałęzi, zapisów w drzewie nadrzędnym ani self-integration
- [ ] re-read przed edycją; wąski staging (`git add -- <owned-path>` albo własne
      hunki); nigdy `git add -A`, `git add .`, stash/discard lub commit cudzej pracy

- [ ] kolejność narzędzi prawdy strukturalnej (loctree-first; ścieżka raportu
      fallbacku dla miss)
- [ ] inwarianty architektury (np. prezentacja w app/ nigdy core/) oraz
      NIETYKALNE ścieżki/wartości, precedencja configu
- [ ] footguny języka/toolchaina istotne dla repo (np. Rust 2024 if-let temp
      scope: snapshot-into-let przed `if let` na lockach)
- [ ] granice push/PR/install/release bieżącej fali; Charter pozwala na
      fast-forward push własnej feature branch, chyba że fala go zabrania.
      Trunk merge, force-push, PR merge/close, kasowanie tagów/gałęzi i deploy
      to przyciski Foundera. `--no-verify` wyłącznie dla jawnie autoryzowanego
      przez Foundera lokalnego checkpointu compile embargo, z receiptem
      pominiętych bramek; push z nim jest Founder-only; tabu lintera repo
      (no unwrap(), no sleep() in tests, …)

- [ ] kontrakt commita: format + trailery, które egzekwuje hook (własna
      tożsamość agenta/runtime'u workera, prawdziwy session id, komenda date)
- [ ] skupione bramki przed commitem headless worker uruchamia synchronicznie;
      supervisor uruchamia zadeklarowane verifiery, integrator posiada szerokie
      bramki integracji. Raport i commit kończysz w tej turze; zakończenia w tle
      nie mogą wybudzić workerów
- [ ] furtka ucieczkowa SUBSTRATE_FAILURE: zatrute drzewo → żadnego
      half-commita, zamiast tego zaraportuj linię awarii
- [ ] ścieżka REPORT: `VIBECRAFTED_REPORT_PATH` od launchera pod kanonicznym
      rootem artefaktów; zachowaj maszynowe `run_id`/`session_id`, semantykę
      finalized/status/claim oraz wymagane sekcje (pliki, evidence bramek,
      acceptance [x]/[?]/[!], unverified, next step, SHA commita + 3 fakty)
- [ ] wyłącznie Operator pisze ignored `<repo-root>/.vibecrafted/THE_JOURNAL.md`;
      workerzy zwracają raporty. Pola workera to claimy, nigdy podpisy za
      Operatora/Foundera; bramki źródła nie dowodzą installed/live acceptance
- [ ] właściciel generowanego manifestu skillów (`scripts/gen_skill_provenance.py`),
      `make skills-check UPDATE=1` po zmianach bajtów i admission zależności dla
      cuta generatora; integrator regeneruje wspólną historię po admission

### 2. BRIEF — the full cut brief

- [ ] wklejony W CAŁOŚCI, nigdy streszczony (brief jest spec)
- [ ] kotwice (file:line) rozumiane jako wskazówki — żywe drzewo jest prawdą

### 3. EXTRA — corrections vs the brief's HEAD

- [ ] „brief napisany przy <SHA>, drzewo ruszyło — ufaj żywemu drzewu" z
      konkretnymi deltami, które dotykają plików tego cięcia
- [ ] hardening bramek z pre-flight (≥1 nowy nietrywialny test tam, gdzie
      baseline był 0; podmienione flaky verify)
- [ ] śruby bezpieczeństwa: DIVERGED-STOP, ogrodzenia scope'u („nie wchodź
      w pliki cięcia X"), klauzula idempotencji dla refire („jeśli już dowiezione
      na drzewie: zweryfikuj acceptance i zatrzymaj się — nie duplikuj")
- [ ] fazowanie dla wielkich cięć: zacommituj działający podzbiór + uczciwy
      raport zamiast półproduktu rozsmarowanego po N plikach

### 4. BATON — line state from the dispatcher

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

## Mechanical gates before launch

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

Przekaż zadeklarowany pin przez `--model <pin>` przy bezpośrednim launchu
workera. W typowanym planie użyj `cuts[].model`; bez cichej podmiany modelu.
Uzbrój supervisor-side `vibecrafted await <agent> --run-id <id>` zaraz po starcie.

## Idempotency rule (refire-readiness)

Każdy prompt musi pozostać bezpieczny do re-fire'a verbatim: kryteria
acceptance są sprawdzalne względem drzewa, EXTRA zawiera klauzulę
„verify-and-stop if done", a deklaracja dziedziczenia z BATON zostaje prawdziwa
po częściowej rundzie (refire czyta drzewo, nie twoją pamięć). Jeśli promptu nie
da się bezpiecznie re-fire'ować, nie jest skończony.

## Evidence checkpoint rule

Nie pozwalaj, żeby prompty workerów traktowały baseline, bramki, raporty albo
handoff notes jak ceremonię. To granice atrybucji regresji. Pominięcie ich to
regression laundering: późniejsza awaria traci właściciela, czas i segment
lifecycle.
