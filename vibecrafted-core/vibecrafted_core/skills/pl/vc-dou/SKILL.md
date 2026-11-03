---
name: vc-dou
version: 2.1.0
description: >
  Definition of Undone audit skill. Runs a systematic gap analysis across the
  ENTIRE product surface — not just code. Crawls public URLs, audits repo
  governance, verifies install paths, checks SEO/discoverability, audits
  representation surfaces for non-web products, and measures the gap between
  internal capability and external visibility.
  Trigger phrases: "definition of undone", "dou audit", "co jest niedokończone",
  "what's undone", "product surface audit", "completion audit", "plague check",
  "hydration check", "are we shippable", "czy jesteśmy gotowi", "gap analysis",
  "co brakuje do launchu", "readiness audit", "packaging gap".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-dou` (launcher `dou`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                        |
> | --------------------- | -------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted dou <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-dou` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                 |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# `vc-dou` — AUDIT-FIRST Definition of Undone

> AUDIT-FIRST, napędzana narzędziami analiza gotowości do dowiezienia. Tam gdzie
> `vc-followup` pyta **„czy trajektoria jest zdrowa?"**, a `vc-audit` pyta **„czy
> spec wylądował?"**, DoU pyta **„jak daleko jesteśmy od tego, by ktoś mógł to
> znaleźć, zaufać, wypróbować i kupić?"** — w poprzek kodu, governance,
> instalacji, SEO, dystrybucji i powierzchni reprezentacji. Produkuje raport luk
> oraz listę remediacji. Nigdy nie modyfikuje kodu ani powierzchni.

## Pozycja w pipelinie

`vc-dou` siedzi w slocie **percepcji gotowości do dowiezienia**:

```
... → polarize (WRITE: cut) → [DOU: AUDIT-FIRST] → hydrate (WRITE) → decorate (WRITE) → release (WRITE) → ...
```

DoU jest AUDIT-FIRST: produkuje inwentarz luk oraz listę remediacji, które
konsumują dalsze kroki WRITE (`vc-hydrate`, `vc-decorate`, `vc-release`).
Naprawa luk należy do tych dalszych skillów, nie do samego DoU.

## Podłoga powierzchni publicznej

Przed audytem prozą uruchom `vc-dou --floor` (albo `vc-dou --floor --product <nazwa>`).
Czerwony wynik (`floor: FAIL` albo `install_cta: no`) jest czerwonym audytem.
Kontrakt: [`../../vc-dou/references/public-surface-floor.md`](../../vc-dou/references/public-surface-floor.md).

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, review, release lub delegację, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brak świeżych dowodów z `vc-init`, wykonaj najpierw przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu. Użyj Loctree przed grepem lub twierdzeniami opartymi na dokumentacji, aby wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refaktorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli task jest jawnie nie-repo lub no-code, zadeklaruj w raporcie wyjątek no-repo. W przeciwnym razie brak dowodów z `vc-init`/Loctree to porażka procesu.

Standardowy launcher: `vibecrafted dou <agent>` / `vc-dou` (zobacz
[Delegation Matrix](../DELEGATION_MATRIX.md)). Worker domyślnie działa jako
odłączony proces headless; `vibecrafted start` / `vc-start` to opcjonalna
obserwacja i konfiguracja interaktywnej User Session, a nie wymagany host
procesu.

```bash
vibecrafted dou claude --prompt 'Audit launch readiness'
vc-dou codex --prompt 'Full product surface audit for loctree'
vibecrafted dou gemini --file /path/to/previous-dou-report.md
```

Zależności fundamentowe (ładowane wraz z frameworkiem): `vc-loctree`, `vc-aicx`.

> „Audit skille są martwe. Praca to podejmowanie inicjatywy, nie samo wytykanie wad."
> „Inżynieria jest zrobiona. Pakowanie nie."

DoU odpowiada na pytanie, którego żaden agent nie zadaje domyślnie:
**„Co pozostaje niedokończone w poprzek całej powierzchni produktu i jak naprawiamy to teraz?"**

To silnik ukończenia. Nie pasywny generator checklist — **aktywny** silnik, który
mierzy lukę między „u mnie działa" a „ktoś może to kupić", a potem natychmiast
zaczyna łatać luki: brakujące skrypty CI, brakującą warstwę reprezentacji,
brakującą dokumentację.

**Krytyczna zasada:** Produkt nie musi być aplikacją webową, żeby potrzebować
publicznej twarzy. Aplikacje desktopowe, narzędzia CLI, agenci, serwery MCP,
wewnętrzne runtime'y — wszystkie potrzebują powierzchni reprezentacji (landing
page, showcase, one-pager, explainer, screenshoty). Jeśli produkt da się
zrozumieć tylko przez otwarcie repo albo rozmowę z jego twórcami, to jest
Definition of Undone.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Pozycja w pipelinie

```
scaffold → init → workflow → followup → marbles → [DOU] → decorate → hydrate → release
```

## Kiedy używać

- Przed jakimkolwiek launchem, zgłoszeniem na marketplace lub ogłoszeniem PR-a
- Po dużych cyklach implementacji (po `vc-followup`)
- Gdy zespół pyta „are we ready?" / „co jeszcze brakuje?"
- Okresowy health check (co ~2 tygodnie)
- Gdy poczucie postępu przewyższa rzeczywistość ukończenia

Jeśli `screenscribe` jest dostępny, vc-dou może skonsumować screencast ścieżki
instalacji lub doświadczenia pierwszego uruchomienia jako evidence audytu.

## Macierz Undone

| Oś                        | Pytanie                          | Narzędzia                    |
| ------------------------- | -------------------------------- | ---------------------------- |
| Repo Health               | Czy kod działa?                  | loctree, cargo/npm, CI       |
| Presence / Representation | Czy ktoś to znajdzie i zrozumie? | WebFetch, brave-search, curl |
| Commercial Readiness      | Czy ktoś to zaadoptuje lub kupi? | Ręczna checklista + sondy    |

Scoring: `[OK]` gotowe · `[PARTIAL]` istnieje, ale niekompletne · `[MISSING]` brak.

## Sekwencja audytu

### Faza 1 — Zarządzanie repo

Wymagane pliki: `LICENSE`, `README.md` (install/usage/contributing), `CONTRIBUTING.md`,
`CHANGELOG.md`, `.github/workflows/`, `.github/ISSUE_TEMPLATE/`, `SECURITY.md`.

```bash
for f in LICENSE README.md CONTRIBUTING.md CHANGELOG.md SECURITY.md; do
  [ -f "$ROOT/$f" ] && echo "[PASS] $f" || echo "[FAIL] $f MISSING"
done
[ -d "$ROOT/.github/workflows" ] && echo "[PASS] CI" || echo "[FAIL] No CI"
```

Strukturalny check Loctree przez `repo-view(project)`: martwe eksporty (0 dla
release'u), cykle (0 lub udokumentowane), health score.

### Faza 2 — Ścieżka instalacji

Test „czy obcy potrafi tego użyć".

- **CLI:** opublikowane do rejestru, badge wersji się zgadza, `cargo install`/`npm i -g`/`pip install` działa,
  binarka uruchamia się bez dev toolchainu, `--help` i `--version` działają.
- **Desktop:** dostępne DMG/MSI/AppImage, formuła Homebrew lub odpowiednik, signed/notarized (macOS).
- **Web:** URL dostępny, ładuje się <3s, responsywność mobilna, łagodny fallback no-JS.

### Faza 3 — Obecność i odkrywalność

Dla każdego publicznego URL-a:

```
1. WebFetch(url, "title, meta description, h1, content summary, CTAs, pricing.
   Report if page appears empty or JS-only.")
2. SSR check: curl -s <url> | grep -c '<h1\|<p\|<main' (< 3 → invisible to crawlers)
3. Security headers: curl -sI <url> | grep -i 'strict-transport\|x-frame\|content-security'
4. OG/Twitter: curl -s <url> | grep -i 'og:\|twitter:card'
```

Podstawy SEO: opisowy title (nie „React App"), meta description, H1, treść no-JS,
robots.txt, sitemap.xml, brak duplicate content między domenami.

Obecność w wyszukiwarce:

```
brave-search("<product>")            # appears in top 20?
brave-search("<product> <category>") # category ranking
brave-search("site:<domain>")        # indexed page count
```

**Brak publicznej aplikacji webowej?** Zamiast tego uruchom Representation Surface Audit:
landing/showcase/one-pager · 30-sekundowy explainer · screenshoty/diagramy/dema ·
widoczna ścieżka instalacji · narracja niewymagająca dostępu do repo · zrozumiałe dla
obcego bez founderów. Wszystkie „nie" → `[MISSING]` Presence.

**Przy `[MISSING]` Presence — wygeneruj `./presence/`** (nie tylko oznacz i jedź dalej):

- `index.html` — nazwa, one-liner, co robi, komenda instalacji, 3-5 features,
  linki (GitHub, docs, registry).
- `styles.css` — ciemny motyw, monospace chrome UI, czysta typografia, paleta ze scaffoldu lub neutralna.
- `app.js` — przycisk copy, smooth scroll, observer fade-up. Nic więcej.

Zasady: minimalnie, ale nie ubogo · brak frameworków · deployowalne na GitHub Pages · meta tagi
(OG/Twitter), favicon, robots.txt, sitemap.xml · brak animacji poza fade-up,
brak particles, brak glow. DoU tworzy je, gdy nic nie istnieje; decorate poleruje;
hydrate pakuje.

### Faza 4 — Powierzchnia komercyjna

```
Discovery → Landing → Understanding → Trial → Adoption → Payment
```

Zweryfikuj każdy etap. Brakujące etapy = dziury w lejku. Dla produktów nie-webowych
„Landing" oznacza landing page, showcase, explainer w dokumentacji lub jawną warstwę
reprezentacji. Brak takiej warstwy = lejek zepsuty na Landing/Understanding, nawet jeśli
produkt działa.

### Faza 5 — Gotowość do marketplace

**Claude Skills Marketplace:** frontmatter SKILL.md (name, version, description) ·
trigger phrases (EN+PL) · `references/` · brak hardkodowanych ścieżek · łagodny
fallback opcjonalnych zależności · przetestowany clean-install.

**GitHub / crates.io / npm:** kompletne metadane pakietu (description, keywords,
homepage, repo) · kategorie/tagi · screenshoty lub demo GIF w README ·
kompatybilna licencja.

## Format wyjścia

```markdown
# Definition of Undone: <project/ecosystem>

Date: <YYYY-MM-DD>
Auditor: <agent>

## Executive Summary

<2-3 sentences: gap between code and market>

## Undone Matrix

| Project | Repo | Web | Commercial | Critical Gap |
| ------- | ---- | --- | ---------- | ------------ |

## Findings by Severity

### P0 — Ship Blockers

### P1 — Credibility Gaps

### P2 — Polish

## The Funnel Test

Discovery → Landing → Understanding → Trial → Adoption → Payment
<for each product, mark where the funnel breaks>

## Hydration Priorities

<ordered fix list with effort estimates>

## Plague Score

0 = fully shipped and discoverable | 100 = brilliant, commercially invisible
```

## Integracja z pipelinem

```
Phase 1 — Craft:    scaffold → init → workflow → followup
Phase 2 — Converge: marbles ↻ (loop until P0=P1=P2=0)
Phase 3 — Ship:     dou → decorate → hydrate → release
```

Findingi DoU zasilają `vc-decorate` (spójność) i `vc-hydrate` (pakowanie).
Po hydracji `vc-release` zajmuje się deployem i launchem.

## Antywzorce

- Uruchamianie DoU tylko na kodzie (to audyt powierzchni PRODUKTU)
- Traktowanie `[OK]` repo health jako dowodu gotowości
- Audytowanie bez crawlowania URL-i
- Zakładanie, że produkty nie-webowe nie potrzebują powierzchni reprezentacji
- Pomijanie testu ścieżki instalacji
- Raportowanie bez rankingu po severity
- Nieuruchamianie ponownie po hydracji

## Diagnostyka plagi (The Plague Diagnostic)

Wzorzec plagi:

1. Testy przechodzą `[PASS]`
2. Architektura solidna `[PASS]`
3. README istnieje `[PASS]`
4. Nikt nie znajdzie tego z Google `[FAIL]`
5. Nikt nie zainstaluje bez toolchainu `[FAIL]`
6. Nikt nie może za to zapłacić `[FAIL]`

Punkty 4-6 to Definition of Undone.

---

_„Antidotum to nie więcej narzędzi. To nie kolejny framework._
_To decyzja: wybierz, co się dowozi, i dokończ to. Całość. Nie tylko kod."_

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
