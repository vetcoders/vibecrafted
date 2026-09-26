# bus-e2e-probe — jak czytać i powtórzyć pomiar

Sonda E2E message busa (plan `vc-message-bus-hub`, cut W4-01). Mierzy mechanikę
serwera na żywych procesach, w pełni izolowana (tymczasowy `VIBECRAFTED_HOME`
i `XDG_CONFIG_HOME`, własny port, sprzątanie po sobie). Nie dotyka prawdziwego
control-plane ani konfiguracji hostów.

## Uruchomienie

```sh
cargo build --manifest-path vibecrafted-server/Cargo.toml \
  -p vibecrafted-server-web --features ssr -p vc-mcp-shim
bash scripts/bus-e2e-probe.sh --json > wynik.json
```

Exit 0 = komplet: agregacja, wysyłka, wstrzyknięcie (dokładnie raz,
`context_injected`), shim, tabela 8 CLI. JSON na stdout
(`vibecrafted.bus-e2e-probe.v1`), tabela poziomów na stderr.

## Co dokładnie jest mierzone

1. **aggregation** — `tools/list` po `/mcp` z bearerem: narzędzia `vc_*`
   (piloty + most do pythonowego vibecrafted-mcp) i `loctree_*` z upstreamu
   `[mcp.upstream.loctree]` (żywy loctree-mcp na `:5174`, jeśli działa).
2. **overhead** — p50/p95 [ms] `tools/list`: bezpośrednio do `:5174`
   (z sesją MCP) vs przez agregator. Debug build; release będzie niższy.
3. **send** — `POST /api/bus/messages` z kopertą `fleet.envelope/0.1`
   (wymagane: `source` = rola floty, poprawny `traceparent`, `recipient`
   = rola floty) → receipt `inbox_pending` w magazynie `message_control`.
4. **injection** — `tools/call` z nagłówkiem `x-vibecrafted-run-id` runu,
   którego meta niesie `context_injection_nonce` → koperta w bloku
   `[vibecrafted-bus …]` w odpowiedzi, stan `context_injected`, drugi call
   bez dubla. Run bez nonce w meta = brak wstrzyknięcia (kotwica zaufania).
5. **shim** — `vc-mcp-shim` (stdio↔HTTP, `VIBECRAFTED_RUN_ID` z env) dostaje
   tę samą kopertę w wyniku narzędzia.
6. **fleet_table** — deklarowane poziomy dostarczenia per provider
   (`monitor_lane.capability_rows()`).

## Czego sonda NIE mierzy (jawna luka)

- Reakcji żywych CLI na doklejony blok (czy agent wykona polecenie z koperty)
  — wymaga runów produktowych; poziom `live` dla Claude wymaga otwartej rury
  `--input-format stream-json` po stronie supervisora (punkt zaczepienia
  opisany w raporcie W3-02).
- Pięciu CLI bez flag MCP per uruchomienie (gemini/grok/agy/cursor/kimi)
  na pasie injected-on-call — pomiar możliwy dopiero po przepięciu globalnych
  konfiguracji hostów na shim (guzik Foundera).
