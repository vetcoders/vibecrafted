# Runtime acceptance canon

Jedna komenda łączy trzy zmysły odbioru runtime'u:

1. **Tożsamość generacji** — `diagnose-which` + `bin-provenance` dla pełnej
   rodziny launcherów (vibecrafted, vc-frame, vc-start, vc-server,
   vc-terminal, vc-o, vc-guardian, vc-server-supervisor, vibecrafted-mcp)
   oraz `launchctl` per faza.
2. **Dynamika procesów** — sampler co 2 s przez cały przebieg, co 1 s przez
   pierwsze 30 s po starcie aplikacji; SUMMARY rysuje oś czasu
   „ile procesów której generacji".
3. **Sceny na żywym kliencie** (`frame_scenes.py`, PTY + pyte, syntetyczne
   kliki SGR i chordy CSI-u): chrome z konstrukcji, peer-switch
   Super+↑/↓, Super+→, Super+N, Super+Shift+., dwufazowe ✕,
   strażnik organów.

## Użycie

```bash
make runtime-acceptance            # snapshot + sceny na zainstalowanym runtime
scripts/runtime-acceptance/vc-runtime-acceptance.zsh --upgrade
                                   # pełny cykl: BEFORE → install → launch → sceny
scripts/runtime-acceptance/vc-runtime-acceptance.zsh --frame-bin=path/to/vc-frame
```

## Reguły kanonu (ustalone 2026-10-04, dragon)

- **Prawo zasiedlonego świata**: sceny nawigacyjne biegną z ≥2 sesjami.
  Werdykt „martwe" ze świata jednoelementowego nie jest werdyktem —
  Super+↓ bez drugiej sesji nie ma czego przełączyć.
- **Izolacja**: sceny używają własnego `VC_FRAME_SOCKET_DIR`; świat
  użytkownika pozostaje nietknięty.
- **Uczciwy exit**: żadnych `make … | tail` nad bramką — pipeline połyka
  status; orkiestrator loguje do plików i zwraca własny kod.
- **S5 organ-guard** domyślnie WARN (starsze generacje nie mają strażnika
  OPERATOR_ORGAN_TAB_NAMES); `--strict-organs` czyni z niego bramkę.
- Dowód kliknięcia > dowód testu jednostkowego: sceny czytają realny render
  (pyte) i realny `dump-layout`, nie wewnętrzne API.
