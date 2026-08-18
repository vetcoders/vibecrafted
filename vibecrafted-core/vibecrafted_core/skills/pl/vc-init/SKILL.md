---
name: vc-init
version: 5.0.0
description: >
  Technical due diligence at the start of every repo session — the entrypoint
  to all repo work, like reading CLAUDE.md. Before touching anything, see the
  asset as it IS: materialize the Loctree context atlas and READ IT TO THE END,
  recover intent (AICX), verify ground truth (git/security), and grade the risk.
  Non-pipeline; runs every session. Trigger: "init", "initialize", "bootstrap",
  "daj kontekst", "zainicjuj", "przygotuj agenta", "start fresh with context".
loctree_value: "primary repo map for structural/literal repository work"
aicx_value: "intent, session, and decision-context retrieval"
dogfooding: "required for repo-impacting work"
---

<!-- fleet-imperative: v3 -->

> **Wywołanie dla `vc-init` (launcher `init`)**
>
> Ten sam _kształt_ trzech ścieżek floty, z **literałami tego** skilla — zobacz
> kanoniczną [Matrycę Delegacji](../DELEGATION_MATRIX.md):
>
> - [Wspólne trzy ścieżki](../DELEGATION_MATRIX.md#wspólne-trzy-ścieżki)
> - [Katalog launcherów](../DELEGATION_MATRIX.md#katalog-launcherów-core-runtime)
> - [Reguła per-launcher](../DELEGATION_MATRIX.md#reguła-per-launcher-delta-semantyczna)
> - [Native vs external](../DELEGATION_MATRIX.md#natywne-subagenty-vs-zewnętrzni-workerzy)
>
> | Ścieżka               | Literał tego skilla                                                                                                         |
> | --------------------- | --------------------------------------------------------------------------------------------------------------------------- |
> | 1. Worker użytkownika | `vibecrafted init [agent]` / `vc-init`                                                                                      |
> | 2. Interactive        | `/vc-init` — wykonaj **w tej sesji**; native subagenty gdy trzeba; **nie** zewnętrzniaj tylko dlatego, że launcher istnieje |
> | 3. Agent-operator     | może odpalić formę workera powyżej przez `vc-dispatch` / linie operatora, zachowując tożsamość tego skilla                  |
>
> **Uwaga:** Checkpoint orientacji, nie pipeline WRITE. Forma workera to `vibecrafted init [agent]`.

> Swobodniejszy native na niektórych biegach ≠ porzucenie floty external. `vc-dispatch` i `vc-ship` zachowują własne tożsamości.

<!-- /fleet-imperative -->

# vc-init — Techniczne due diligence

Due diligence to ruch inwestora: zanim zaangażujesz kapitał w aktywo, badasz je na
dowodach — wyciągasz ukryte zobowiązania, weryfikujesz pitch, wyceniasz ryzyko — żeby
decyzja była wyceniona, a nie wymarzona. Tutaj pitchem jest README i wiara foundera, że
to działa; aktywem jest żywy kod. `vc-init` to właśnie to badanie na wejściu w sesję:
zobacz, czym kod JEST teraz, odzyskaj, dlaczego taki się stał, znajdź to, co sklejone
taśmą, i wyceń ryzyko — żeby każdy kolejny ruch był wyceniony. To **read-only
orientacja**, punkt wejścia do całej pracy z repo.

`vc-init` (DD na aktywie, na wejściu) i `vc-dou` (DD na produkcie, na wyjściu, z ramy
kupującego) to ta sama dyscyplina na dwóch końcach.

## [HARD GATE] — zmaterializuj atlas i PRZECZYTAJ GO DO KOŃCA

Najtańszy i najszybszy zwiad, jaki istnieje, to przeczytany w całości Loctree context
atlas — empirycznie dużo tańszy niż agent od zwiadu i dostępny natychmiast. Agenci
niedoczytują; ten gate istnieje, bo czytania nie da się pominąć.

1. **Zmaterializuj** atlas: MCP `context()` (albo `loct context`). Zapisuje
   `.loctree/context-atlas/` — `manifest.md` plus karty.
2. **Przeczytaj `manifest.md`** — to on jest ścieżką czytania (per karta `why` +
   `saves-you-from`) i regułą kompletności.
3. **Przeczytaj karty do końca, wzdłuż tej ścieżki.** Poprzeczka z samego manifestu:
   odpowiedź na poziomie repo jest NIEKOMPLETNA, dopóki nie zostaną przeczytane
   `00-core-map.md` (synteza: tożsamość, ryzyko, autorytet, bezpieczne komendy),
   `01-structural-map.md` (pliki/symbole/importy/konsumenci) i `02-runtime-map.md`
   (runtime/env/osiągalność). Przeczytaj też `05-risk-register.md`
   (hotspoty/fan-in/health). Karty są osobnymi plikami dokładnie po to, żeby żadna się
   nie przelewała — czytaj każdą w całości.

NIE zatrzymuj się na pierwszej karcie ani na pierwszym ekranie. Synteza i rejestr ryzyka
to osobne karty; częściowy odczyt to ślepy start. Nie masz initu za sobą, dopóki
poprzeczka kompletności nie jest spełniona.

Fallback na CLI, gdy MCP jest niedostępne: `loct context --full --markdown` i przeczytaj
CAŁĄ paczkę (jej synteza jest na końcu — czytaj dalej niż tabele).

## Drill-down po zakresie — ten sam budżet, skoncentrowany

Atlas całego repo jest ważony recency na stałym budżecie ~800 linii, więc stale albo
peryferyjne podsystemy wypadają. Zanim wejdziesz w konkretny podsystem X, przeczytaj
jego paczkę zawężoną do zakresu — to nie jest zwężanie, to _głębsze_ pokrycie X tym
samym kosztem:

```bash
loct context --scope 'path:<X>' --task '<what you are doing>' --markdown
# pre-baked shelf, when present:
cat .loctree/context/scopes/path/<X>/context-compact.md
```

Przeczytanie właściwej pigułki zakresowej odzyskuje dokładnie te powierzchnie, które
atlas całego repo odrzucił — w ~12 s i przy niemal zerowych tokenach, zamiast
dispatchowania agenta od zwiadu.

## Triada staranności

### Percepcja — ponad pamięć

Atlas to podstawowa percepcja. Czytaj etykiety autorytetu, zanim uwierzysz w jakieś
twierdzenie: `repo_verified` (fakt ze snapshotu) > `loctree_derived` (wnioskowanie
analizatora) > `aicx_operator` / `aicx_agent` (wcześniejsza intencja/wynik) >
`aicx_failure` (ścieżka, która już zawiodła — nie powtarzaj) > `semantic_guess`
(heurystyka — zweryfikuj) > `stale_or_unknown` (sprawdź ponownie). Drążej przez `slice`
(przed edycją), `impact` (przed usunięciem), `find --literal` / `occurrences` / `body`
(prawda o referencjach), `follow` (dead/cycles/twins/hotspots). Przekazuj `project=`
jawnie per repo — domyślna wartość MCP to nie twoje cwd.

### Intencje — pozyskiwanie, nie RAG

`aicx intents -p <project>` + `aicx_search`/`aicx_steer`: odzyskaj, _dlaczego_
architektura ma taki kształt i jaką prowizorkę nałożono późną nocą. Pozyskaj kontekst
decyzji, a potem zweryfikuj jego aktualną prawdziwość względem percepcji.

Opłacone doświadczenie floty jest częścią tego zmysłu: zanim zaczniesz dispatchować,
wznawiać albo odzyskiwać zewnętrzne runy, sprawdź
[Ledger feedbacku runtime'u](../../RUNTIME_FEEDBACK.md) pod kątem komend, których zaraz
użyjesz (`aicx search -p vetcoders/vibecrafted '<command>'` sięga tej samej doktryny
przez retrieval). Powtórzenie błędu, który ledger już wycenił, to porażka procesu, a nie
pech.

### Twarde fakty (ground truth) — ponad intuicję

- Historia gita: `vc-git` (albo `git log --graph -n 15` + `git status -sb`).
- Przeczytaj `.claude/CLAUDE.md` / `.codex/AGENTS.md` / `AGENTS.md`; jeśli konfiguracja
  jest sprzeczna z kodem, zaufaj kodowi.
- Czerwone flagi due diligence: god tables bez indeksów; auth, gdzie każdy jest
  admin/user bez zabezpieczeń na poziomie wiersza; `.env` śledzony w gicie; ciche awarie.

### Wyjście — wyceń ryzyko

Produktem initu jest wyceniony obraz: co jest nośne (huby/fan-in), co jest kruche, co
jest miną — żeby następne działanie zostało podjęte z otwartymi oczami.

## Polityka `.env`

Nigdy nie commituj `.env*` (gitignore; pre-commit to blokuje). Wycieki zgłaszaj → szybki
revoke. Pracuj z `.env` lokalnie bez lęku — wahanie przed lokalnym użyciem samo w sobie
jest przyszłą podatnością.

## Antywzorce

- Działanie, zanim atlas zostanie przeczytany do poprzeczki kompletności (ślepy start).
- Zatrzymanie się na pierwszej karcie/ekranie — rejestr ryzyka jest dalszą kartą.
- Zaufanie domyślnemu `project` z MCP zamiast przekazania go (ciche złe repo).
- Odruchowy `grep`/`find` przed `context()`/`find` — tracisz etykiety autorytetu
  i odwrotne zależności.
- Dispatchowanie agenta od zwiadu do tego, na co za darmo odpowiada `context --scope`.
- Ogłaszanie „production-ready" na podstawie zielonego testu na niezbadanej architekturze.
- Pisanie „uruchomiłem testy" bez ich uruchomienia.

## Living Tree

Działaj w bieżącym checkoucie i na bieżącej gałęzi operatora; żadnego worktree, chyba że
ktoś o to poprosi. Czytaj ponownie przed edycją; jeśli drzewo ruszyło się pod tobą
(fingerprint z `doctor()`), ponów `context(fresh: true)`. Jeśli podłoże jest zbyt zatrute,
by kontynuować, zatrzymaj się i zgłoś to.

---

_„Zobacz aktywo. Odzyskaj intencję. Zweryfikuj grunt. Wyceń ryzyko. Wtedy działaj."_

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
