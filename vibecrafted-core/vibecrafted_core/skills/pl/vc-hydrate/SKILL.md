---
name: vc-hydrate
version: 1.0.0
description: >
  Packaging and go-to-market hydration skill. Takes DoU audit findings and
  executes the non-code work that bridges the gap between "it works" and
  "someone can buy this." Generates marketplace listings, SEO fixes,
  distribution artifacts, onboarding flows, landing page content, and
  representation surfaces for products that do not naturally have a public web UI.
  Trigger phrases: "hydrate", "package for market", "prepare for launch",
  "przygotuj do launchu", "fix the packaging gap", "marketplace listing",
  "nawodnij", "make it shippable", "go-to-market", "distribution",
  "SEO fix", "landing page", "onboarding", "completion sprint".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-hydrate` (launcher `hydrate`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                            |
> | --------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
> | 1. Worker użytkownika | `vibecrafted hydrate <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-hydrate` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                     |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-hydrate — Antidotum na Always-in-Production

> „Kod działa, ale brakuje mu warstwy, która pozwala dotrzeć do użytkowników. Hydrate oznacza: spraw, by droga od obcego do użytkownika była możliwie bez tarcia."

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, przegląd, release lub delegowanie, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brakuje świeżych dowodów z `vc-init`, najpierw wykonaj przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu. Używaj Loctree przed grepem lub twierdzeniami opartymi na dokumentacji, aby wygenerować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refactorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli zadanie jawnie nie dotyczy repo lub nie dotyczy kodu, odnotuj w raporcie wyjątek „bez repo". W przeciwnym razie brak dowodów z `vc-init`/Loctree to błąd procesu.

Zacznij od `vibecrafted start` (lub `vc-start`). Następnie uruchom z Command Deck:

```bash
vibecrafted hydrate codex --prompt 'Package for marketplace'
vc-hydrate claude --prompt 'Generate missing SEO and landing page'
vibecrafted hydrate gemini --file /path/to/dou-report.md
```

Hydrate to skill od pakowania produktu, którego wzywa DoU. Traktuje „stwórz instalator DMG" i „napisz copy przyjazne SEO" jako pełnoprawne zadania inżynieryjne, nie jako sprawy drugorzędne.

**Zasada nadrzędna:** każdy poważny produkt potrzebuje powierzchni prezentacji, nawet jeśli sam nie jest produktem webowym. Aplikacje desktopowe, narzędzia CLI, serwery MCP, lokalne runtime'y i systemy wewnętrzne nadal potrzebują zewnętrznej powierzchni prezentacji, która pozwala obcemu odkryć, zrozumieć, zobaczyć, ocenić i przyjąć produkt.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Pozycja w pipelinie

```
scaffold → init → workflow → followup → marbles → dou → decorate → [HYDRATE] → release
```

## Podłoga powierzchni publicznej

`vc-hydrate --floor` sprawdza checkout: zgodność wersji, sekcję instalacji w README i pisownię marki (`Vetcoders`, bez wielkiego C w środku). Nie odpytuje rejestrów. Kontrakt: [`../../vc-dou/references/public-surface-floor.md`](../../vc-dou/references/public-surface-floor.md).

## Kiedy używać

- Po tym, jak audyt `vc-dou` zidentyfikuje luki w pakowaniu
- Przed jakimkolwiek zgłoszeniem do marketplace lub publicznym launchem
- Gdy zespół mówi „działa, teraz spraw, by dało się je znaleźć/zainstalować/kupić"
- Okresowy sprint hydrate (zalecane: w parze z DoU co 2 tygodnie)
- Gdy Plague Score > 40

## Domeny hydrate

### Domena 1 — Hydrate repo

Domknij luki w zarządzaniu repo wykryte przez DoU. Wygeneruj treść adekwatną do kontekstu:

- **LICENSE** — wykryj intencję projektu (komercyjny / open-source / dual), wybierz MIT/Apache-2.0/proprietary
- **CONTRIBUTING.md** — wyciągnij z README, jeśli istnieje; pokryj setup, standardy kodowania, proces PR, link do code-of-conduct
- **CHANGELOG.md** — sparsuj git log pod kątem niewydanych zmian; format Keep-a-Changelog; nagłówki wersji zgodne z opublikowanymi wersjami
- **SECURITY.md** — standardowy szablon responsible disclosure; preferowane GitHub Security Advisories
- **Workflow CI** — sterowane wykrywaniem języka (Rust: cargo check/clippy/test/fmt; Node: lint/test/build; Python: ruff/pytest); zawsze dołącz audyt zależności + sprawdzenie licencji

**Sync wersji:** `grep -rn "version" Cargo.toml package.json pyproject.toml`, a następnie porównaj z opublikowanymi wersjami (`cargo search`, `npm view`) oraz badge'ami/referencjami na stronie. Niezgodność → finding P1.

### Domena 2 — Hydrate dystrybucji

Spraw, by produkt dało się zainstalować bez dev toolchaina:

- **Narzędzia CLI (Rust):** `cargo install <name>` działa; GitHub Releases z prekompilowanymi binarkami (linux-x86_64, macos-arm64, macos-x86_64); skrypt instalacyjny `curl -sSfL <url> | sh`; formuła Homebrew (tap lub core); wygenerowane i dołączone shell completions. Wygeneruj workflow release'u GitHub Actions: cele cross-compile, assety GitHub Release, auto-aktualizacja formuły Homebrew.
- **Aplikacje desktopowe (macOS):** bundle `.app` z poprawnym Info.plist; DMG z obrazem tła i symlinkiem do Applications; code signing z Developer ID; notaryzacja przez notarytool; formuła cask Homebrew; Sparkle (lub odpowiednik) do auto-aktualizacji. Użyj szablonu `create-dmg` (`--volname`, `--background dmg-background.png`, `--window-size 600 400`, `--icon-size 100`, `--app-drop-link 400 200`).
- **Aplikacje webowe:** Dockerfile, docker-compose.yml do lokalnego podglądu, dokumentacja env-var (`.env.example`), endpoint health check (`/health` lub `/api/health`), obsługa graceful shutdown.

### Domena 3 — Hydrate odkrywalności

Napraw SEO i obecność w sieci:

**SSR / pre-rendering dla stron SPA.** Problem: strony renderowane w JS są niewidoczne dla crawlerów. Rozwiązania w kolejności preferencji:

1. Statyczny pre-rendering na etapie buildu (najlepsze dla landing page'y)
2. SSR z hydration (dla treści dynamicznej)
3. Hybryda: statyczny landing + SPA dla aplikacji
4. Minimum: fallback `<noscript>` z kluczową treścią

Dla Leptos (WASM): włącz tryb SSR lub wygeneruj statyczny HTML; pre-renderuj krytyczne trasy na etapie buildu; zapewnij, że `<title>`, `<meta>`, `<h1>` istnieją w początkowym HTML.

**Szablon meta tagów** na każdą publiczną stronę: `<title>{Product} — {Tagline} | {Company}</title>`, meta description (≤155 znaków), meta keywords (5-8 trafnych), zestaw Open Graph (`og:title`, `og:description`, `og:image`, `og:type=website`), zestaw Twitter card (`twitter:card=summary_large_image`, `twitter:title`, `twitter:description`).

**Nagłówki bezpieczeństwa (konfiguracja serwera):** `Strict-Transport-Security: max-age=63072000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'self'`.

**robots.txt + sitemap.xml** — wygeneruj z rzeczywistej struktury URL; zapewnij brak zduplikowanej treści między domenami; zgłoś do Google Search Console (krok ręczny — oflaguj dla użytkownika).

### Domena 4 — Hydrate powierzchni komercyjnej

Zbuduj ścieżkę od obcego do klienta.

**Struktura landing page:**

1. Sekcja hero: tagline + 1-zdaniowy value prop + główne CTA
2. Problem: pain point słowami użytkownika
3. Rozwiązanie: jak produkt go rozwiązuje (maks. 3 punkty)
4. Social proof: statystyki, opinie, case studies
5. Jak to działa: 3-krokowy flow wizualny
6. Cennik: jasne tiery lub „contact us"
7. Powtórka CTA: to samo główne CTA

Wygeneruj jako: Markdown (statyczne generatory stron), HTML (bezpośrednie użycie), dokument copy (handoff do projektanta).

**Scaffold powierzchni reprezentacji (obowiązkowy, gdy brakuje).** Jeśli produkt nie jest aplikacją webową, Hydrate i tak tworzy scaffold powierzchni prezentacji:

- **Aplikacje desktopowe** — landing/showcase, zrzuty ekranu / product shoty, „jak to działa", ścieżka instalacji (DMG/MSI/AppImage/cask Homebrew), sygnały zaufania (bezpieczeństwo, local-first, offline, prywatność)
- **Narzędzia CLI** — landing lub one-pager w stylu docs, przykłady komend, komenda instalacji, przykładowe wyjście, dla-kogo-to-jest
- **Serwery MCP / narzędzia infra** — strona objaśniająca, diagram architektury, przykłady workflow, ścieżka instalacji i podłączenia, realne przypadki użycia
- **Produkty wewnętrzne/hybrydowe** — showcase skierowany do founderów, podsumowanie możliwości, zrzuty ekranu / diagramy / mockupy, objaśnienie runtime vs warstwa prezentacji

Hydrate nigdy nie powinno zakładać, że „nie potrzeba strony" oznacza „nie potrzeba reprezentacji".

**Listingi marketplace:**

Dla Claude Code Skills Marketplace:

```markdown
# {Skill Name}

{One-line description}

## What it does

{2-3 sentences explaining the value}

## When to use

{Bullet list of trigger scenarios}

## How it works

{Brief technical explanation}

## Requirements

- {Required tools/dependencies}
- {Optional enhancements}

## Part of

{Suite name} — {suite description}
```

Dla crates.io / npm / PyPI: description (<250 znaków, bogaty w słowa kluczowe), keywords (5 trafnych terminów), categories (zgodne z rejestrem), homepage (URL landinga), repository (URL GitHuba), documentation (URL docs), readme (ścieżka).

### Domena 5 — Hydrate onboardingu

Stwórz pierwsze 5 minut użytkownika:

- **Narzędzia CLI:** komenda instalacji (jedna linia, do skopiowania) → pierwsza komenda (natychmiastowa wartość) → „co się właśnie stało" → kolejne kroki (2-3 progresywne komendy) → gdzie szukać pomocy
- **Aplikacje webowe:** rejestracja (<3 pola) → kreator onboardingu (<5 kroków) → dane przykładowe lub tryb demo → quick win w ciągu 60s → link do docs
- **Skille/wtyczki:** komenda instalacji → fraza triggerująca do przetestowania → oczekiwane wyjście → opcje personalizacji

### Domena 6 — Hydrate warstwy reprezentacji

Dla produktów, które są realne, używalne i wartościowe, ale obecnie niewidoczne z zewnątrz. Zbuduj minimalną, zamierzoną powierzchnię widoczną z zewnątrz, niezbędną, by produkt był czytelny dla obcych.

Możliwe artefakty: landing page `docs/index.html`, jednostronicowy statyczny showcase, one-pager produktu (Markdown/HTML), feature explainer, paczka zrzutów ekranu / diagramów, obrazek social preview, zwięzłe copy pozycjonujące, warstwa CTA („install"/„try"/„request access"/„contact").

Zalecana struktura: nazwa produktu + 1-liniowy value prop → czym jest → dla kogo jest → po co istnieje → jak działa → jak wypróbować/zainstalować/uzyskać dostęp → dowód wizualny (zrzuty ekranu, diagramy, przykłady).

**To nie jest opcjonalny ornament. To publiczna powierzchnia produktu.**

## Protokół hydrate sprintu

1. **Wczytaj raport DoU.** Wyciągnij wszystkie findingi P0/P1. Posortuj wg wpływu (powierzchnia komercyjna > odkrywalność > zarządzanie repo).
2. **Triage do domen.** Zmapuj każdy finding do domeny (1-6). Niektóre findingi mapują się na wiele — wypisz wszystkie.
3. **Wygeneruj artefakty.** Per finding: brakujące pliki → utwórz je; brakująca meta → wygeneruj HTML; brakująca ścieżka instalacji → workflow CI; brakujący landing → napisz copy; brakująca reprezentacja → scaffold odpowiedni do typu produktu; brakujący listing marketplace → wygeneruj listing.
4. **Zweryfikuj przez DoU.** Uruchom ponownie na obszarach, których to dotyczy. Cel: redukcja Plague Score o ≥20 punktów.
5. **Przedstaw użytkownikowi.** Raport po hydrate z Plague Score'ami przed/po, tabelą artefaktów wygenerowanych według domen (status) oraz pozostałymi krokami ręcznymi (DNS, klucze API, przycisk submit w marketplace itd.).

## Integracja z pipeline'em

```
Phase 1 — Craft:     scaffold → init → workflow → followup
Phase 2 — Converge:  marbles ↻ (loop until P0=P1=P2=0)
Phase 3 — Ship:      dou → decorate → hydrate → release
```

Hydrate produkuje artefakty do dystrybucji i prezentacji. `vc-decorate` dopracowuje spójność wizualną przed hydrate. Po hydrate `vc-release` zajmuje się deployem i go-to-market launchem. Uruchom ponownie DoU po hydrate, aby zweryfikować, że luka się domknęła.

## Delegacja do subagentów

Przy dużych sprintach hydrate rozbij domeny między subagentów, używając `vc-agents`:

```
Agent 1: Repo Hydration (LICENSE, CONTRIBUTING, CI, CHANGELOG)
Agent 2: Distribution Hydration (release workflows, installers)
Agent 3: Discoverability Hydration (SEO, meta tags, pre-rendering)
Agent 4: Commercial Hydration (landing copy, marketplace listings)
```

Każdy agent dostaje: findingi DoU dla swojej domeny, szablony artefaktów z tego skilla, standardową preambułę Living Tree.

## Antywzorce

- Hydrate bez wcześniejszego audytu vc-dou (naprawianie tego, co zakładasz, nie tego, co zmierzone)
- Generowanie plików bez kontekstu repo (typ LICENSE musi pasować do intencji projektu)
- Pisanie copy marketingowego bez zrozumienia produktu (najpierw uruchom vc-init)
- Zakładanie, że produkty desktopowe / CLI / MCP / lokalne nie potrzebują warstwy reprezentacji
- Zakładanie, że hydrate robi się raz i temat znika. Hydrate powinien wracać cyklicznie, tak jak refactoring: po większych zmianach, przed release i po każdym DoU, które pokaże nowe luki.
- Hydrate wszystkiego naraz (priorytetyzuj: najpierw luki komercyjne P0)
- Zapominanie o ponownym uruchomieniu DoU po hydrate (zweryfikuj poprawkę)

## Definicja „zrobione-zrobione"

Projekt jest po hydrate pass, gdy:

- Obcy może go **ODKRYĆ** (wyszukiwarki, marketplace, poczta pantoflowa)
- Obcy może go **ZROZUMIEĆ** (landing, README, value prop jasny w 30s)
- Obcy może go **ZOBACZYĆ** (powierzchnia reprezentacji istnieje, nawet jeśli produkt nie jest web-native)
- Obcy może go **ZAINSTALOWAĆ** (jedna komenda, bez dev toolchaina, <5 minut)
- Obcy może go **UŻYĆ** (onboarding, quick win w ciągu 60 sekund)
- Obcy może za niego **ZAPŁACIĆ** (cennik, rejestracja, trial — jeśli komercyjny)
- Obcy może **WSPÓŁTWORZYĆ** (CONTRIBUTING.md, szablony issue, CI — jeśli open source)

Dopóki wszystkie te warunki nie są prawdą, projekt jest w stanie Always-in-Production. Hydrate jest antidotum.

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
