---
name: vc-hello
version: 0.1.0
description: >
  Fleet onboarding and config parity for agent CLIs. This skill should be used
  when the user asks to "dodaj nowego agenta do floty", "wyrównaj config CLI z
  zespołem", "onboard a new agent CLI", "skonfiguruj swoje cli jak reszta",
  "sprawdź drift configu", or when a new human or agent joins Vibecrafted.
  Scans the fleet's configs (Claude / Codex / Grok / Kimi / …), extracts the
  shared posture, maps it onto a target CLI using documented keys only,
  validates through the target's native doctor, and reports parity and gaps.
loctree_value: "not structural — operates on CLI config files, not repo code"
aicx_value: "intent history for why a fleet posture decision was made"
dogfooding: "runs on the operator's own machine config"
---

<!-- fleet-imperative: v3 -->

> **Invocation for `vc-hello` (meta skill)**
>
> Te same trzy ścieżki co we flocie, z literałami **tego** skilla — zobacz
> kanoniczną [Delegation Matrix](../DELEGATION_MATRIX.md):
>
> - [Shared three paths](../DELEGATION_MATRIX.md#shared-three-paths)
> - [Launcher catalogue](../DELEGATION_MATRIX.md#launcher-catalogue-core-runtime)
> - [Per-launcher rule](../DELEGATION_MATRIX.md#per-launcher-rule-the-semantic-delta)
> - [Native vs external](../DELEGATION_MATRIX.md#native-subagents-vs-external-workers)
>
> | Path                    | Literal for this skill                                                                                     |
> | ----------------------- | ---------------------------------------------------------------------------------------------------------- |
> | 1. User-launched worker | brak — powierzchnia meta, bez workera `vibecrafted hello <agent>`                                          |
> | 2. Interactive          | `vc-hello` — wykonaj **w tej sesji** (Skill tool / slash); cały przebieg to lokalna praca nad konfiguracją |
> | 3. Agent-operator       | może załadować skill podczas onboardingu nowego agenta, zachowując jego tożsamość                          |
>
> **Uwaga:** bramka onboardingu, nie pipeline zapisu. Pracuje na konfiguracji
> maszyny Operatora; zmiany przechodzą przez zweryfikowany apply docelowego CLI.

<!-- /fleet-imperative -->

# vc-hello — Fleet Onboarding & Config Parity

Nowe CLI agenta dołącza do floty jak człowiek do zespołu: dostaje wspólną postawę
pierwszego dnia, bez trzech godzin archeologii. `vc-hello` wita i wyrównuje:
skanuje uzgodnioną postawę floty (uprawnienia full-auto, najwyższy effort,
ciemny motyw, wspólne serwery MCP, hooki doktrynalne), mapuje ją na udokumentowane
klucze nowego CLI, dowodzi wyniku jego natywnym walidatorem i uczciwie opisuje luki.

To także wejście do Vibecrafted dla nowego **człowieka lub agenta**: skill
przekazuje postawę ([vibecraftsmanship](../vibecraftsmanship/SKILL.md)),
kanoniczne instalatory upstream i procedurę wyrównania w jednym pakiecie — zobacz
[references/onboarding-stack.md](references/onboarding-stack.md).

## Goal

**Propozycja — Founder decyduje o ostatecznym brzmieniu.** `vc-hello` doprowadza
CLI do zgodności z flotą albo zwraca raport każdej rozbieżności. Kończy się,
gdy walidator schematu docelowego CLI akceptuje zastosowaną konfigurację,
a raport oznacza każdą oś jako match / divergent / no-key z dowodem. Founder
potwierdza lub przepisuje ten akapit, zanim skill stanie się kanoniczny.

## When To Use

- Nowe CLI agenta trafia do floty i ma odziedziczyć wspólną postawę.
- Okresowy przegląd dryfu: „sprawdź, czy mój config się nie rozjechał z flotą”.
- Onboarding człowieka lub agenta do Vibecrafted (postawa + mapa instalacji).

**Kiedy NIE używać:**

- Orientacja w repo — to `vc-init` (vc-hello dotyka konfiguracji CLI, nie kodu).
- Zmiana jednego ustawienia kimi-code — to wbudowany skill `update-config`;
  vc-hello **deleguje** mu etap apply dla kimi.

## Pipeline Position

- Przed: nic nie jest wymagane — pracuje na konfiguracji w katalogu domowym,
  nie na repo (wyjątek no-repo: bez bramki `vc-init`).
- Po: luki wymagające kodu (brak skryptu hooka, brak instalacji serwera MCP)
  trafiają do `vc-implement`; spory o postawę floty eskalują do Foundera.

## Workflow

### Mode A — Onboarding (full parity)

1. **Skanuj flotę.** `uv run tools/fleet_scan.py scan --output <dir>/posture.json`.
   Czytaj konsensus, nie surowe konfiguracje — skaner normalizuje osie
   (uprawnienia, effort, motyw, język, auto-update, serwery MCP, rodziny hooków)
   i strukturalnie redaguje sekrety.
2. **Pobierz oficjalną dokumentację konfiguracji docelowego CLI** przed zmianą
   kluczy. Dokumentacja niedostępna → użyj wiedzy modelu, oznacz każdy
   niezweryfikowany klucz w raporcie i traktuj natywny walidator jako falsyfikator.
3. **Mapuj konsensus → klucze celu.** Użyj
   [references/fleet-config-map.md](references/fleet-config-map.md) dla znanych
   równoważności (`never`/`always-approve`/`yolo` → full-auto; `xhigh` → najwyższy
   wspierany poziom celu). Każdą pozycję oceń: przenośna, specyficzna dla CLI
   (pomiń + opisz) albo zawierająca sekret (pomiń + opisz — nigdy nie kopiuj
   sekretów między plikami). Konflikty rozstrzyga większość, odstępstwo odnotuj.
4. **Smoke-test przed podłączeniem.** Każdy przenoszony hook lub skrypt uruchom
   raz z payloadem celu (exit 2 blokuje, exit 0 pozwala, czyste fail-open).
   Hook bez działania w celu (np. wymagający niewspieranego `updatedInput`)
   jest martwym ciężarem — nie podłączaj. Następnie uruchom
   `uv run tools/fleet_scan.py check` — smoke-test dowodzi **zachowania**
   osiągalnego skryptu, ale nie wykrywa ścieżki całkiem nieobecnej w generacji.
   Incydent 2026-10-01: `~/.copilot/hooks/vibecrafted-fleet.json` wskazywał bridge,
   którego nikt nie sprawdził z brakującym plikiem, i każdy hook odmawiał całą noc.
5. **Apply przez zweryfikowaną ścieżkę.** Kopia → edycja kandydata → natywna
   walidacja (`kimi doctor`, `codex --validate`, …) → backup z datą → nadpisanie.
   Dla kimi-code tym etapem **jest** skill `update-config` — użyj go, nie pisz
   drugiej implementacji. Zakończ raportem: match / divergent / no-key /
   skipped-with-reason dla każdej osi oraz instrukcja reload.

### Mode B — Drift audit (read-only)

`uv run tools/fleet_scan.py diff --target <cli> --output <dir>/drift.json`
(`--posture <file>` wykorzystuje wcześniejszy skan). Raportuj rozbieżności;
nie edytuj niczego, dopóki użytkownik nie zleci naprawy.

## Utility Scripts

`tools/fleet_scan.py` (tylko stdlib, `uv run`):

- `scan --output FILE` — JSON postawy: znormalizowane osie każdego CLI +
  `consensus` (większość z konfliktami; MCP i rodziny hooków wspólne dla ≥2
  członków). Sekrety nie trafiają do postawy z konstrukcji; dodatkowy scrub
  redaguje pozostałe ciągi podobne do sekretów. `consensus.hooks` zawiera też
  `status`/`broken`: komendy hooków są rozwiązywane do interpretera/skryptów,
  również skryptu za separatorem `--` w `hook_bridge.py`, a brak pliku jest oznaczany.
- `diff --target {claude,codex,grok,kimi,copilot} --output FILE [--posture FILE]` —
  dryf każdej osi z klasami równoważności (najwyższy effort `xhigh`≈`max`,
  full-auto w zapisach poszczególnych vendorów).
- `check [--output FILE]` — rozwiązuje hooki każdego członka do rzeczywistych
  ścieżek i kończy niezerowo, gdy którejś brakuje. Incydent 2026-10-01:
  `~/.copilot/hooks/vibecrafted-fleet.json` wskazywał skrypt bridge nieobecny
  w każdej generacji, zamieniając hooki w hard deny niezauważone przez noc.
  Uruchamiaj po podłączeniu każdego hooka, nie tylko podczas onboardingu.

Każda awaria daje exit 1 z błędem na stderr; stdout pozostaje jednym wierszem statusu.

## Dependencies

- **vibecraftsmanship** — charter postawy floty; vc-hello odsyła do niego
  jako postawy nowych członków, zamiast powtarzać go.
- **update-config** (wbudowany, cel kimi) — właściciel apply dla kimi.
- **VERIFICATION_RULE** — zielony schemat nie kończy apply; potrzebny jest smoke.

## Error Handling

| Situation                                | Policy                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------- |
| Dokumentacja celu niedostępna            | użyj wiedzy, oznacz niezweryfikowane klucze; decyduje natywny walidator |
| Element floty zawiera sekret             | pomiń + opisz; redakcja wymuszona mechanicznie w `scan`                 |
| Konflikt floty (np. effort low vs xhigh) | większość wygrywa + odstępstwo opisane                                  |
| Hook niezgodny z celem                   | najpierw smoke-test; martwych hooków nie podłączaj                      |
| Kandydat nie przechodzi walidacji        | napraw i waliduj ponownie; nigdy nie nadpisuj bez backupu z datą        |

## Acceptance Criteria

Przebieg skilla jest **done**, gdy:

- [ ] JSON postawy istnieje, a każdy obecny członek został zeskanowany lub oznaczony `absent`
- [ ] każda oś konsensusu ma w celu match albo jawne divergent / no-key / skipped-with-reason
- [ ] natywny walidator celu akceptuje zastosowaną konfigurację (Mode A)
- [ ] raport mówi, czego **nie** zweryfikowano (Verification Rule)

Brak dowodu dla dowolnego punktu oznacza nieukończony skill — powiedz to wprost w raporcie.

## Anti-Patterns

- Czytanie surowych 600-wierszowych konfiguracji (z wyciekiem API key do kontekstu) zamiast `scan`.
- Kopiowanie sekretu „dla parity” — sekrety nie podróżują między plikami.
- Podłączanie hooka, który nie może zadziałać ani wstrzyknąć kontekstu w celu — ceremonia bez efektu.
- Zgadywanie kluczy bez dokumentacji lub walidatora celu jako falsyfikatora.
- Ciche głosowanie większości — każdy konflikt floty trafia do raportu.
- Wklejanie `workflow` / ERi — vc-hello nie jest `vc-workflow`.

## Roadmap

- Pełna automatyzacja instalacji vibecrafted-stack dla nowego człowieka
  (dziś: kanoniczny upstream w `references/onboarding-stack.md`).
- Adaptery `diff` dla claude/codex/grok (v0.1 dostarcza `kimi`, sprawdzoną ścieżkę).

## Verify before the handoff

Zanim ogłosisz done, obejdź ciężarówkę — zobacz
[Verification Rule](../VERIFICATION_RULE.md): artefaktem jest zastosowana
konfiguracja zaakceptowana przez doctor celu po `/reload`. Czysty raport diff
nie dowodzi zachowania członka floty, dopóki nie potwierdzi go rzeczywisty przebieg.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
