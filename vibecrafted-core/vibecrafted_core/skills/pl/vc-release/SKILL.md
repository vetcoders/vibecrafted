---
name: vc-release
version: 0.2.1
description: >
  Final outward ship skill. Turns "done in the repo" into "safe, visible, deployable,
  discoverable, and launchable in the world." Covers release mechanics, deployment
  topology, reverse-proxy defaults, Semgrep-gated security hygiene, domain and DNS
  wiring, SEO/indexability, verification challenges, onboarding truth, and post-release
  smoke checks. Trigger phrases: "release", "ship to market", "publish",
  "deploy to production", "vc-release", "go live", "launch", "wypuść wersję",
  "deploy", "release prep", "launch path", "launch checklist", "production checklist".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-release` (launcher `release`)**
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
> | 1. Worker użytkownika | `vibecrafted release <agent>`                                                                                                  |
> | 2. Interactive        | `/vc-release` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                     |

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-release — Dowieź bez ściemy

> „Zrobione w repo" to nie „zrobione w świecie".

## Wejście operatora

### Reguła Living Tree / Worktree

Ten workflow działa w bieżącym checkoucie i na bieżącej gałęzi operatora. Nie twórz worktree gita, nie przełączaj się na niego ani nie przenoś do niego wykonania, chyba że operator wprost poprosi o worktree w tym prompcie. Ogólne słowa w stylu „isolate", „parallel" czy „clean branch" to za mało. Jedyny usankcjonowany drugi tryb to dispatch Fleet Worktrees (pisany plan, zacommitowane wcześniej verifiery, rozłączne domeny plików, jednowątkowy integrator — patrz Reguła Living Tree, Tryb B); poza tą formacją zostań we wspólnym drzewie. Czytaj pliki ponownie przed edycją, dostosowuj się do równoległych zmian i zgłoś awarię podłoża (substrate failure), jeśli bieżące drzewo jest zbyt zatrute, by bezpiecznie kontynuować.

Zobacz [Reguła Living Tree](../LIVING_TREE_RULE.md).

## Checkpoint orientacji

Zanim ten workflow wykona analizę specyficzną dla repo, planowanie, implementację, review, release lub delegację, MUSI uruchomić lub skonsumować procedurę `vc-init` dla przypisanego repo. Jeśli brak świeżego evidence z `vc-init`, wykonaj najpierw przebieg init i traktuj pracę specyficzną dla workflow jako zablokowaną, dopóki nie ma aktualnej prawdy repo.

`Loctree:loctree` to domyślny skill do mapowania struktury repo dla tego przebiegu. Użyj Loctree przed grepem lub twierdzeniami z dokumentacji, aby wyprodukować lub odświeżyć Mapę Aplikacji Wyprowadzoną z Kodu (Code-Derived Application Map): repo-view, focus, slice, impact, find i follow w odpowiednim zakresie. Szukaj istniejących symboli i kontraktów, zanim utworzysz nowe; uruchom impact przed usunięciem lub dużym refaktorem; uruchom slice przed edycją.

Chodzi o znalezienie zaczepów: węzłów nośnych, twins (duplikaty), martwego kodu, dryfu, entrypointów runtime'u oraz pułapek o dużym zasięgu zmiany. Jeśli task jest jawnie poza repo lub bez kodu, zadeklaruj wyjątek no-repo w raporcie. W przeciwnym razie brak evidence z `vc-init`/Loctree to porażka procesu.

Wejdź przez `vibecrafted start` (lub `vc-start`). Następnie odpal przez command deck:

```bash
vibecrafted release codex --prompt 'Prepare v1.2.1 release'
vc-release claude --prompt 'Ship the web surface safely behind Caddy'
vibecrafted release gemini --file /path/to/release-checklist.md
```

Preferuj `--file` dla istniejącego planu, `--prompt` dla intencji inline.

Release to nie ceremonia. Release to operacyjny kontrakt bezpieczeństwa, widoczności i adopcji.

## Doktryna pracy z repozytorium

W pracy z repozytorium zacznij od Loctree jako mapy: użyj `loct context`,
`loct occurrences`, `loct body` i `loct find --literal` przed szerokim ręcznym
przeszukiwaniem. Używaj AICX do kontekstu intencji i sesji. Używaj rg/grep jako
fallbacku lub lokalnej lupy, nie jako zamiennika mapowania strukturalnego. Jeśli Loctree
zawiedzie lub przeoczy jakąś powierzchnię, dopisz feedback do `~/.vibecrafted/loctree/loctree-fail.md`.

## Pozycja w pipelinie

```
scaffold → init → workflow → followup → marbles → dou → decorate → hydrate → [RELEASE]
```

Release uruchamia się po tym, jak `vc-dou` zweryfikuje powierzchnię produktu, `vc-decorate` zapewni spójność wizualną, a `vc-hydrate` opakuje dystrybucję/SEO/onboarding. Release urealnia artefakty po hydrate: tagi, changelogi, publikację rejestru/binarek, topologię deploya, proxy/TLS, domenę/DNS/weryfikację, indeksowalność, bramki bezpieczeństwa, post-release smoke.

## Podłoga powierzchni publicznej

`vc-release --floor` jest bramką wydania. Sam tag bez obiektu GitHub Release nie kończy etapu; `--require-release` zostaje czerwone, dopóki Release nie istnieje. Kontrakt: [`../../vc-dou/references/public-surface-floor.md`](../../vc-dou/references/public-surface-floor.md).

## Główna zasada

**Jeśli poniższy kanon release'u nie jest spełniony, release jest no-opem.** Nie myl „umiem to zdeployować" z „jest bezpieczne, widoczne i gotowe, by wyjść do obcych".

## Kanon release'u (Release Canon) — sześć płaszczyzn

1. **Prawda artefaktu** — wersje, tagi, changelog, opublikowane wyjścia
2. **Prawda deploya** — topologia, proxying, healthchecki, zachowanie przy restarcie
3. **Prawda bezpieczeństwa** — Semgrep, wyeksponowane powierzchnie, headery, auth, obsługa sekretów
4. **Prawda domeny** — DNS, domyślny host, TLS, redirecty, wyzwania weryfikacyjne
5. **Prawda widoczności** — SEO, indeksowalność, social cards, sitemap, robots, publiczne metadane
6. **Prawda onboardingu** — ścieżka instalacji, pierwsze uruchomienie, docs, screenshoty, quickstart, ścieżka kupującego

Jeśli brakuje którejkolwiek płaszczyzny, wskaż to jawnie i zablokuj release, chyba że użytkownik świadomie akceptuje ryzyko.

## Kanon artefaktu

**Git/wersjonowanie:** otaguj dokładny commit (`git tag -a v1.2.3 -m "Release 1.2.3"`), push (`git push origin v1.2.3`), obowiązkowy changelog, opublikowana wersja zgadza się z referencjami w repo/badge'ach/docs/stronie.

**Opublikowane wyjścia:** npm (`npm publish` po bumpie wersji), crates.io (`cargo publish`), PyPI (wheel + sdist), GitHub Release (dołącz dokładne artefakty z nudnymi, opisowymi nazwami plików), Docker (otaguj dokładną wersję, opcjonalnie `latest` — nigdy nie dowoź samego `latest` jako tożsamości).

**Nazewnictwo artefaktów:** `myapp-v1.2.3-linux-x86_64.tar.gz` (dobrze) vs `release.zip` (źle).

## Topologia deploya

Wybierz jedną świadomie:

- **Caddy** — solo/mały zespół, kilka prostych upstreamów, automatyczny HTTPS. Domyślne dla webowych MVP, proxy landing+app.
- **Nginx** — już go obsługujesz pewnie, zaawansowane potrzeby reverse-proxy, wiele upstreamów. Dla ustalonych stacków ops, większych posiadłości web/API.
- **Docker** — odtwarzalność, heterogeniczne środowiska, przenośny preview/staging/prod.

**Bezpieczna drabina:** najprostszy realny launch → static hosting lub Caddy. App + worker + db → Docker + reverse proxy. Dojrzała infra → Nginx lub standard platformy. Wybierz najmniejszy uczciwy stack, nie to, co brzmi imponująco.

## Domyślne ustawienia bezpieczeństwa deploya

- Domyślnie binduj usługi aplikacji do `127.0.0.1`; udokumentuj każdy wyjątek `0.0.0.0`
- Terminuj TLS na celowym proxy/ingressie; preferuj reverse proxy nad surowym wystawieniem portu
- Wewnętrzna sieć Dockera zamiast portów publikowanych na hoście dla usług prywatnych
- Wstrzykiwanie env w runtimie, nigdy sekrety wpieczone w obrazy
- Wymagaj endpointu `/health`, graceful shutdown, kontenerów non-root, `.dockerignore` bez sekretów

**Czerwone flagi:** panel admin/debug zbindowany publicznie; publiczna usługa na `:3000`/`:5173`/`:8000` bez proxy/TLS; `CORS *` na uwierzytelnionych API; wyeksponowane stacktrace'y lub bannery frameworka; pliki `.env` lub backupy dostępne z weba.

## Reverse proxy i ekspozycja

Minimalne oczekiwania wobec reverse-proxy:

- nagłówki `Host` i forwardujące zachowane celowo
- wsparcie websocket upgrade, jeśli aplikacja tego potrzebuje
- rozsądne ustawienia timeoutu i rozmiaru body
- redirect `www`/apex zgodnie z decyzją kanoniczną
- redirect 80 -> 443, gdy zamierzony jest publiczny HTTPS

Ekspozycja na publiczny Internet to decyzja, nie domyślny tryb.

## Bramka release'u Semgrepa

Semgrep jest częścią kanonu. Nieopcjonalny. Raport release'u musi nieść evidence.

Komenda kanoniczna: `make semgrep` (podpięta tak samo jak lokalne hooki pre-commit/pre-push: `semgrep scan --config auto --error --quiet --exclude-rule html.security.audit.missing-integrity.missing-integrity .`). Hooki mieszkają w `scripts/hooks/`, aktywowane przez `make init-hooks`.

Przed release'em: uruchom `make semgrep` na pełnym repo, zapisz findingi (rule id, severity, plik, zakres linii), klasyfikuj wg **granicy przepływu danych (dataflow boundary)** (nie wg lokalizacji pliku):

- tainted-path / sinki LFI → napraw przy zwalidowanym obiekcie root
- regexy podatne na ReDoS → ograniczone parsowanie lub bezpieczny kształt
- niebezpieczne merge'owanie headerów/obiektów → jawna allowlista + niemutowalna granica wejścia
- konstrukcja command/shell → sparametryzowane wywołanie, nigdy konkatenacja stringów przez niezaufany szew (untrusted seam)

Zablokuj release przy każdym nierozwiązanym blokującym findingu, chyba że użytkownik jawnie akceptuje ryzyko na piśmie wewnątrz raportu.

Minimalne klasy: obejścia auth/authz, niebezpieczna obsługa sekretów, shell/command injection, SSRF, path traversal/LFI, niebezpieczne serwowanie plików, słaba walidacja wejścia na groźnych sinkach, niebezpieczna deserializacja/eval-podobne, regexy ReDoS, niebezpieczne merge'owanie headerów/obiektów, pozostawione włączone endpointy debug/dev frameworka.

Jeśli Semgrep niedostępny, powiedz to jawnie, uruchom `uvx semgrep` (udokumentowany fallback) i zapisz w raporcie, że bramka nie została spełniona. Cisza jest nieakceptowalna.

## Kontrakt raportu release'u

Każdy raport release'u musi zawierać te obowiązkowe sekcje i linkować z powrotem do
`references/release-report-template.md`:

- **Bramka bezpieczeństwa** — evidence z `make semgrep`, findingi i nierozwiązane ryzyko.
- **Inwentarz wyeksponowanej powierzchni** — publiczne route'y, usługi, porty, domeny oraz powierzchnie admin/debug.
- **Decyzja o trybie deploya** — wybrana topologia, postawa proxy/TLS oraz ścieżka rollbacku.
- **Smoke instalacji po release'ie** — weryfikacja instalacji/uruchomienia zimną ścieżką z opublikowanego artefaktu.

## Domena, DNS, weryfikacja

Jeśli produkt ma jakąkolwiek publiczną powierzchnię, zweryfikuj: domena zarejestrowana i zamierzona, DNS na właściwy target, kanoniczny host (
`www` vs apex), redirecty zgadzają się z kanonem, TLS rozwiązuje się czysto, domeny staging vs prod nie pomieszane. Także: żadnych nieaktualnych domen preview reklamowanych jako główne, żadnego niepasującego favicona/title/og:image wyciekającego starą tożsamość, żadnych zepsutych ścieżek
`/.well-known/*`.

Dowody własności (gdy publiczne produkty ich potrzebują): Search Console, Bing Webmaster, pliki TXT/challenge domeny, endpointy `.well-known/` ekosystemu Apple/Google, wszelkie proofy challenge-response wymagane przez infrę/platformy. Jeśli dowód własności domeny jest wymagany, a ścieżka challenge jest nieobecna, release nie jest skończony.

## Kanon SEO i widoczności

Widoczność to twardy checklist, nie nice-to-have.

- **Poziom strony**: opisowy `<title>`, meta description, jeden prawdziwy `<h1>`, crawlable content w początkowym HTML lub
  prawdziwy fallback, kanoniczny URL, tagi Open Graph + Twitter card, poprawny status code, `noindex` tylko gdy
  zamierzony.
- **Poziom strony (site)**: `robots.txt`, `sitemap.xml`, strategia kanonicznego hosta, spójne linkowanie wewnętrzne, żadnych zepsutych
  linków docs/marketing, favicon + assety social preview.
- **Sprawdzenia indeksowalności**: `curl` strony i weryfikacja sensownej treści bez JS; route nieblokowany przez `robots.txt`; meta
  robots nie `noindex`, chyba że zamierzone; canonical wskazuje na zamierzony publiczny URL.
- **Sprawdzenia widoczności domeny**: docs/landing/CTA wszystkie się rozwiązują; instrukcje instalacji wskazują na realne publiczne artefakty; podgląd social share nie jest zepsuty.

Jeśli obcy nie potrafi szybko odkryć, zrozumieć i wypróbować produktu, release jest niekompletny.

## Prawda onboardingu

Zweryfikuj ścieżkę pierwszego użytkownika: instalacja z opublikowanych artefaktów (nie z repo), przejście publicznego quickstartu na zimno, screenshoty i dema zgodne z rzeczywistością, app lub CLI startuje bez założeń dev-only, błędy są czytelne dla człowieka.

## Smoke weryfikacja po release'ie

Weryfikuj z **zimnej ścieżki**. Maszyna deweloperska nie jest świadkiem.

Zainstaluj z **opublikowanego artefaktu** (npm/cargo/PyPI/GitHub Release/registry Dockera — nigdy lokalny checkout, nigdy
side-loadowany tarball, nigdy gałąź dev). Następnie zweryfikuj: publiczny URL się rozwiązuje, TLS ważny + zgodny z kanonicznym hostem, endpoint health
przechodzi, kluczowa akcja działa end to end, linki docs i CTA się rozwiązują, opublikowana wersja zgadza się z uruchomioną wersją,
screenshoty/dema onboardingu zgodne z wyjściem instalatora na zimno.

Raport musi nazwać dokładne źródło artefaktu (URL rejestru, tag, digest, URL pobierania). „Działało na moim repo" nie spełnia tej bramki.

Każdy przebieg `vc-release` musi wyprodukować raport z faktycznym evidence. Nie można uczciwie powiedzieć „done" bez czterech obowiązkowych sekcji poniżej. Jeśli którejś brakuje, release jest **zablokowany**, dopóki nie zostanie wypełniona lub użytkownik nie zaakceptuje luki na piśmie.

> Kanoniczny szablon: [`references/release-report-template.md`](references/release-report-template.md).
> Pełny checklist operatora: [`references/release-checklist.md`](references/release-checklist.md).
> Pogłębienie realiów deploya: [`references/deployment-reality.md`](references/deployment-reality.md).

## Kontrakt raportu release'u

**Obowiązkowe sekcje:**

1. **Bramka bezpieczeństwa** — uruchomiona komenda (`make semgrep` lub odpowiednik), status wyjścia i liczba findingów, klasyfikacja per finding (rule id, severity, plik, zakres linii, granica przepływu danych), rozwiązanie per finding (naprawione w commicie X / zaakceptowane z powodem / odroczone z issue trackującym), jawne stwierdzenie, gdy bramka faktycznie nie została spełniona.
2. **Inwentarz wyeksponowanej powierzchni** — nasłuchujące porty i adresy bind (domyślnie `127.0.0.1`, udokumentuj każdy `0.0.0.0`), reverse proxy z przodu (Caddy/Nginx/cloud LB/brak) i gdzie terminuje się TLS, granice uwierzytelniania per powierzchnia, nagłówki odpowiedzi dodawane/zdejmowane na brzegu (HSTS, CSP, frame options, allowlista CORS), ścieżka materializacji sekretów.
3. **Decyzja o trybie deploya** — wybrana topologia z uzasadnieniem, dlaczego to najmniejsze uczciwe dopasowanie, historia rollbacku (jak cofnąć bez ręcznych bohaterstw).
4. **Smoke instalacji po release'ie** — źródło artefaktu (URL rejestru, tag, digest, URL pobierania — nigdy `file://` z drzewa roboczego), sekwencja komend wykonana z czystego środowiska, evidence z pierwszego uruchomienia (exit code, banner wersji, health check), wszelki dryf między udokumentowanym quickstartem a zaobserwowanym zachowaniem.

**Sign-off/zatwierdzenie** tylko gdy wszystkie cztery sekcje są wypełnione i każda ma dołączone obiektywne evidence. Zielona bramka Semgrepa bez inwentarza wyeksponowanej powierzchni to nie sign-off. Decyzja o topologii bez przebiegu smoke to nie sign-off. Prawda jest kumulatywna.

## Realia finansowe / prawne

Koszty hostingu i transferu zrozumiane, limity rejestru/CDN znane, LICENSE poprawny, SECURITY.md istnieje, polityka prywatności/regulamin istnieją, jeśli w grę wchodzą dane użytkownika. Nie marketinguj własnościowego jako open source. Nie zbieraj danych bez powiedzenia o tym.

## Antywzorce

- Publikowanie bez `vc-dou`
- Pomijanie hydracji i zakładanie, że użytkownicy sami sobie poradzą
- Brak Semgrepa lub równoważnej bramki bezpieczeństwa
- Wystawianie usług na `0.0.0.0` bez celowego projektu proxy/TLS
- Zepsuta logika kanonicznej domeny lub redirectów
- Zapominanie o plikach wyzwań weryfikacyjnych / rekordach TXT
- Dowiezienie pustej skorupy tylko z JS, której crawlery nie rozumieją
- Tagowanie bez changeloga
- Deployowanie bez sprawdzeń smoke po release'ie
- Traktowanie release'u jako jednorazowej ceremonii zamiast powtarzalnej dyscypliny

## Zasada końcowa

Dowoź tylko wtedy, gdy jest wystarczająco bezpieczne, wystarczająco widoczne, wystarczająco instalowalne, wystarczająco zrozumiałe, a historia deploya jest wystarczająco nudna, by jej zaufać. Jeśli nie — uczciwym wynikiem `vc-release` nie jest „done", lecz „zablokowane, z tych dokładnie powodów".

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
