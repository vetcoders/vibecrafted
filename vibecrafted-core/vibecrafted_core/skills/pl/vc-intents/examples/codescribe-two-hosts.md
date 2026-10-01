# vc-intents — example: Codescribe, dictated corpus, two hosts

## Trigger phrase

> "Rozlicz intencje z transkrypcji Codescribe od stycznia — co obiecaliśmy i nie
> dowieźliśmy? Chcę móc sam poprawiać klasyfikację."

## Expected agent behavior

1. `vc-init` w repo; `aicx intents -p /codescribe --limit 1` → nagłówek zawiera
   pięć tożsamości; najnowsza intencja 2026-09-18 i commit 2026-09-18 — zgodne.
2. `sweep --since 2026-01-01 --source dir:~/.codescribe/transcriptions --source aicx`
   → katalog transkryptów 1 900 plików na tym hoście; aicx 3 254 wiersze,
   **truncated**. Drugi host ma kolejne 900: `rsync dragon:.codescribe/transcriptions/ …`,
   powtórny sweep daje 2 842 pliki, `--shard-size 120` → 24 shardy.
3. `plan --stage extract --agent '*=junie:gemini-3.8-flash'` → doctor czysty →
   `vibecrafted dispatch … --json`. `verify-quotes --strict` → 974 dosłowne, 0 odrzuconych.
4. `lanes` (sześć według subject) → `plan --stage confront --host div0` z kimi /
   cursor / grok / junie na tor; ten sam plan na dragon z `--host dragon`, katalogi
   werdyktów wykluczone z rsync workdiru.
5. `merge --verdicts div0=… --verdicts dragon=… --primary div0` → zgodność
   550/756 = 72.8 %, największa różnica `partial→landed` (47). Jeden tor nie
   dotarł na dragon, 218 intencji bez drugiego werdyktu, raportowane na stderr.
6. `reclassify` → +410 override'ów, 43 % host-level `landed` spadło przez dwie reguły.
7. `render --html --transcripts … --blob-base https://github.com/vetcoders/codescribe/blob/<sha>/`
   → Founder filtruje „W kodzie, Founder: nie działa” (126), decyduje, eksportuje
   `decisions.jsonl` → `decide --import-file decisions.jsonl --by founder`.
8. `queue --min-strength 4` → 199 stabilnych otwartych intencji → `plan --stage implement
--limit 6 --gate 'cd {repo} && make check'` → doctor czysty → handoff.

## Acceptance evidence

- `<workdir>/LEDGER.json` z `hosts: ["div0","dragon"]`, `replication-report.json`
  z liczbą `agreement` dla każdej pary
- `<workdir>/overrides.jsonl` z wierszami `by: rule` i co najmniej jednym `by: founder`
- `intents-implement.div0.dispatch.toml` przechodzący `vibecrafted dispatch --doctor --json`
- `status` exit 1 z nazwanymi powodami (otwarte dispositions, `runtime_verified != yes`)
- Najwyższa prawda w raporcie: „43 % `landed` podważają własne słowa Foundera;
  runtime_verified jest `unknown` dla wszystkich 974 — żadna flota nie uruchomiła produktu”.

## Notes

- Agentów do czytania wybiera przepustowość (junie/flash), do konfrontacji
  stabilność (junie 88–100 % powtarzalności, kimi 54 % w tym samym przebiegu).
- Tor `L6-build-release` wyzwala hard-stop dispatch, jeśli nazwa pojawia się
  dosłownie w verifierze; skrypt zapisuje każdą ścieżkę przez `{id}`.
