# vc-agents — instrukcja launchera (bieżący deck)

Wejściem dla Operatora jest deck `vibecrafted` lub helper `vc-<launcher>`.
Agenci: `claude · codex · agy · junie · grok · cursor · kimi · copilot`.
Pełna powierzchnia flag obejmuje więcej niż `--prompt`:

```bash
vibecrafted <launcher> <agent> \
  --repo "$(pwd)" \
  --model <id> \
  --effort <low|medium|high|xhigh> \
  --worktree true \
  --permissions <bypass|auto|accept-edits|read-only> \
  --prompt "..."
# albo --file /path/to/plan.md
```

`--repo` wybiera checkout rodzica. Model jest guzikiem kosztowym Foundera —
nie podmieniaj go. Effort jest mapowany per provider, z receiptem pominięcia,
gdy provider go nie obsługuje. `--worktree true` wybiera izolowaną gałąź i worktree.

**Składanie prompta to idiom powłoki.** Wstrzykuj aktualne wyniki komend,
aby worker dostał prawdę bieżącą zamiast zadania jej ponownego odkrycia:

```bash
# Prefiks tekstowy przed planem: frontmatter na początku prompta uruchamia
# kontrolę konfliktu providera.
vibecrafted workflow grok --repo "$(pwd)" --model grok-4.7-build-fast \
  --worktree true \
  --prompt "Implementation plan follows. Execute it end to end.

$(cat "$PLAN")"

vibecrafted workflow codex --repo "$(pwd)" --model gpt-6-sol --worktree true \
  --prompt "$(echo 'Zaprojektuj i wdroż obsługę copilot CLI zgodnie z:' \
             && echo && copilot --help \
             && echo 'Agent ma być wszędzie wpisem kolejnym po kimi.')"
```

Przy dispatchu z sesji Claude usuń odziedziczone zmienne sesji,
aby dziecko nie przypisało pracy do rodzica:

```bash
env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION \
    -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN \
    -u CLAUDE_PID -u CLAUDE_JOB_DIR \
  zsh -lc 'vibecrafted workflow <agent> ...'
```

Komendy towarzyszące: `vibecrafted await <agent> --run-id <id>` (uzbrój od razu),
`observe` (końcówka transkryptu), `stop` (TERM grupy procesu),
`usage [--run-id ...]` (tokeny i koszt raportowany przez providera — podaj przy
rozliczeniu). Jeśli narzędzia są niedostępne, zgłoś brak konfiguracji spawnu.

## Konwencja wyjścia

- Plany: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/plans/<timestamp>_<slug>.md`
  lub inna trwała nazwa zadania.
- Raporty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.md`
- Transkrypty: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.transcript.log`
- Metadane: `$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/<timestamp>_<slug>_<agent>.meta.json`

Każdy spawn od razu pokazuje kartę uruchomienia: `run_id`, agenta/rodzinę modelu,
ścieżki planu, raportu, transkryptu i metadanych oraz dokładną komendę await.
Brak tych ścieżek oznacza niepełną obserwowalność nawet przy działającym agencie.

## Obserwacja

Kanoniczny kontrakt supervisora (`docs/runtime/AGENT_OPS.md`): po dispatchu
natychmiast uzbrój `vibecrafted await <agent> --run-id <id>` po stronie supervisora.
JSON control-plane, raporty, transkrypty, panele i zaplanowane wybudzenia są
diagnostyką, nie sygnałem wybudzenia. Doraźne pollery/watchery obok await to
naruszenie klasy 3: napraw `control_plane.await_run`.

Trzy sygnały żywotności: werdykt await, terminalne metadane runu i zakończony PID
workera; dodatkowo obecność obiecanego raportu. Dwa zgodne sygnały wystarczą do
działania, trzy do uznania zakończenia. Przy rozbieżności traktuj run jako żywy
i ponownie uzbrój await. Znany dryf: rc=0 dla żywego procesu oraz metadane
`active`/`stalled` po rzeczywistym zakończeniu.

Trwałe artefakty służą do obserwacji postępu. Czekanie i końcowe podsumowanie
należą do dedykowanego helpera runtime:

```bash
vibecrafted await codex --run-id <run_id>
vibecrafted await codex --last
```

Dla wielu workerów przekaż helperowi ich ścieżki launcherów lub metadanych.
Jeśli dostępny jest observer, używaj go do inspekcji transkryptu lub diagnozy:

```bash
vibecrafted observe codex --last
```

Observer nie jest jedyną powierzchnią statusu. `vc-agents` musi działać na
trwałych artefaktach także bez obserwowania paneli na żywo.

## Pętla integratora snap-dispatch (wzorzec Foundera, 2026-10-03)

Integrator pisze gęsty plan, uruchamia JEDEN launcher na cut i sprawdza wynik.
Przepustowość wynika z jakości planu:

1. **Plan zawiera pomiary.** Dokładne ścieżki store, formaty rekordów, liczby
   kontrolne i kotwice kodu z liniami oszczędzają workerowi ponownego odkrywania.
   Zaobserwowany efekt: telemetryczny cut sześciu silników dostarczony w jednym runie.
2. **Kanon Foundera jest wiążący.** Decyzje odzyskane z AICX wpisz pod
   „CANON/INWARIANT — implement exactly, never reinterpret”; powtórz w prefiksie dispatchu.
3. **Jeden snap, jeden cut.** Użyj `vibecrafted workflow <agent>` z modelem
   wybranym przez Foundera, `--worktree true` i tekstowym prefiksem przed
   `$(cat plan.md)`. Usuń zmienne sesji CLAUDE_* i od razu uzbrój await.
4. **Bramka integratora po powrocie.** Powtórz testy workera w jego worktree
   z czystym PATH, zbadaj produkt z tego binarium i klasyfikuj czerwone testy
   porównaniem z czystym baseline HEAD. Integruj przez `--no-ff` i push zgodnie
   z autoryzacją misji; podaj koszt providera przy rozliczeniu.
5. **Model i effort wybiera Founder.** Nie podmieniaj ich podczas runu;
   błąd providera 400 oznacza zatrzymanie i pytanie, nie zmianę modelu.
