# Onboarding Stack — new human or agent into Vibecrafted

Przekaż ten plik (ze skillem) nowemu członkowi zespołu — człowiekowi lub agentowi.
Kolejność ma znaczenie: najpierw postawa, potem narzędzia, na końcu parity.

## 1. Posture before tools

Przeczytaj charter: [vibecraftsmanship](../../vibecraftsmanship/SKILL.md)
(piąty charter — jak _myśleć o działaniu_). Nowy członek musi umieć powtórzyć:

- Twardy wymóg runtime'u wygrywa z teoretyczną poprawnością; prawda produktu z lokalną elegancją.
- Loctree to mapa strukturalna; AICX to pamięć intencji; Vibecrafted to dyscyplina dowodu.
- Done jest warunkiem rynkowym (DoU), nie zieloną bramką.
- Jeden tron dla każdej prawdy — bez równoległych systemów i wrapperów konkurentów.

## 2. Canonical upstream installers

Zgodnie z "Own Only Your Namespace" fundamenty pochodzą z **własnych**
kanonicznych release'ów, nigdy z vendored copies ani wrapperów na PATH.

| Tool           | Canonical install                                                             | Verify                  |
| -------------- | ----------------------------------------------------------------------------- | ----------------------- |
| loct / loctree | `curl -fsSL https://loct.io/install.sh \| sh`                                 | `loct --version`        |
| vibecrafted    | clone `vetcoders/vibecrafted`, `make install` (lub release DMG dla aplikacji) | `vibecrafted --version` |
| aicx           | dostarczany ze stosem vibecrafted (`aicx-mcp` na PATH)                        | `aicx-mcp --help`       |
| agent CLIs     | instalator każdego vendora (Claude Code, Codex, Grok, Kimi Code)              | `<cli> --version`       |

Sekrety żyją w macOS Keychain lub magazynie poświadczeń danego CLI — nigdy
we wspólnych plikach konfiguracji, nigdy kopiowane między nimi "dla parity".

## 3. Config parity (this skill's core)

Gdy CLI już działa, wyrównaj jego postawę z flotą:

```bash
uv run tools/fleet_scan.py scan --output ./posture.json
uv run tools/fleet_scan.py diff --target <cli> --output ./drift.json
```

Następnie procedura z SKILL.md: dokumentacja → mapa → smoke-test →
zweryfikowany apply → raport parity. Znane równoważności są w
[fleet-config-map.md](fleet-config-map.md).

## 4. First-run checklist for the new member

- [ ] `loct --version`, `vibecrafted --version`, `aicx-mcp --help` odpowiadają
- [ ] CLI agenta startuje, a konfiguracja przechodzi natywny walidator
- [ ] `diff --target <cli>` nie pokazuje osi `divergent` (lub każda ma zapisany powód)
- [ ] hooki doktrynalne działają: uruchom samodzielne `rg` w repo i zobacz blokadę `loctree-first-guard`
- [ ] członek zna postawę floty: uprawnienia full-auto, najwyższy effort, ciemny motyw, odkrywanie po polsku, wspólne MCP (aicx / loctree / playwright / context7)

## Roadmap

Pełna instalacja stosu bez nadzoru na nowej maszynie jest zadeklarowanym
następnym cutem `vc-hello` — dziś ten plik jest ręczną ścieżką zgodną z doktryną.
