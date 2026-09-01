# Szablon dokumentu syntezy researchu

> Ekspercka interpretacja operatora trzech raportów źródłowych. Każda nietrywialna
> teza cytuje file:line w raportach źródłowych. Same raporty pozostają jako
> niezmienne eksperckie zeznanie w osobnych plikach — czytaj je bezpośrednio, gdy
> chcesz pełne, nieprzefiltrowane evidence.

## Lokalizacja pliku

Zapisz syntezę do:
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/synthesis.md`

Trzy raporty źródłowe pozostają jako osobne pliki w tym samym katalogu. **Nie wklejaj ich inline.**

## Frontmatter

```yaml
---
run_id: <generated-unique-id>
skill: vc-research
project: <repo-name>
status: completed
operator_synthesis_by: <claude|codex|gemini|cursor|operator>
source_reports:
  - claude_<run_id>.md
  - codex_<run_id>.md
  - gemini_<run_id>.md
---
```

## Struktura sekcji

### 0. Coverage statement

Każdy raport źródłowy poniżej został przeczytany w całości przez warstwowe slicowanie, zanim powstała synteza:

- `claude_<run_id>.md`: <N> linii / <K>KB, 100% przeczytane w <M> odcinkach
- `codex_<run_id>.md`: <N> linii / <K>KB, 100% przeczytane w <M> odcinkach
- `gemini_<run_id>.md`: <N> linii / <K>KB, 100% przeczytane w <M> odcinkach

Jeśli któregoś raportu nie dało się przeczytać w całości, ta synteza MUSI przerwać na sekcji 0 z jawnym stwierdzeniem granicy. Cytowanie zakresów linii z nieprzeczytanego raportu jest zabronione.

### 1. Problem

<z Kroku 1 — faktyczne pytanie, nie objaw>

### 2A. Convergent findings (zdeduplikowane — jedno stwierdzenie na finding)

> Findingi, w których dwóch lub trzech ekspertów się zgodziło. Zredukowane do jednego stwierdzenia na finding. Referencje źródeł cytują zgodne raporty.

#### F1: <stwierdzenie findingu, głosem operatora>

**Sources**: `claude_<run_id>.md:L42-58`, `codex_<run_id>.md:L101-115`
**Not addressed by**: Gemini (albo „all three"; albo pomiń, jeśli wszyscy poruszyli)
**Operator note**: <jednolinijkowy niuans, jeśli wyostrza; w przeciwnym razie pomiń>

#### F2: ...

### 2B. Signals (findingi pojedynczego agenta — POTENCJALNIE kluczowe insighty)

> Findingi wyniesione tylko przez jednego z trzech agentów. NIE niższego priorytetu niż convergent — często to faktyczny kierunek, którego praca potrzebowała. Każdy sygnał zostaje nazwany, ocytowany i osądzony z osobna.

#### S1: <stwierdzenie sygnału, głosem agenta>

**Source**: `gemini_<run_id>.md:L78-92`
**Why others missed it**: <claude nie poruszył Q3; codex poruszył, ale dał złą odpowiedź, ponieważ…; cursor przekroczył timeout; itd.>
**Operator's verdict**: **amplify** | **flag for follow-up** | **acknowledge & reject**
**Reasoning**: <jeśli amplify: dlaczego sygnał jest słuszny, a widok convergent niekompletny. Jeśli flag: jaki eksperyment / dalszy research to rozstrzygnie. Jeśli reject: co konkretnie w rozumowaniu sygnału zawodzi, z referencją do evidence z repo albo nazwanej wiedzy zewnętrznej — nigdy na pałę.>

#### S2: ...

### 4. Architecture Decision

- **Chosen approach**: <decyzja operatora>
- **Why**: <uzasadnienie cytujące konkretne findingi przez file:line>
- **Alternatives rejected**:
  - <alternatywa> — odrzucona wg `<file>:Lxx-yy`, ponieważ <uzasadnienie>

### 5. Implementation Notes

- <konkretne wskazówki — cytuj źródło dla każdej nietrywialnej pozycji>
- <sygnatura API: zobacz `codex_<run_id>.md:L130-145` po zweryfikowaną składnię>

### 6. Remaining Gaps

- <pytania, na które żaden z trzech nie potrafił odpowiedzieć — cytuj, gdzie każdy agent się poddał>
- <obszary wymagające praktycznego eksperymentowania>

### 7. How to Read This

- Ta synteza to **ekspertyza operatora**. Trzy raporty, które cytuje, pozostają jako samodzielne artefakty w tym katalogu — otwórz je, gdy chcesz pełny, nieprzefiltrowany tekst od każdego agenta.
- Referencje file:line są bezwzględne względem pliku raportu (np. `claude_<run_id>.md:L42-58` oznacza linie 42-58 włącznie w tym pliku).
- Jeśli nie zgadzasz się z osądem operatora, raporty źródłowe są tuż obok — przeczytaj je i wyrób własne zdanie. Po to są.

## Imperatywy operatora (kategoryczny)

1. **Synthesis NIE zawiera verbatim treści raportów** — tylko cytaty file:line do nich. Synthesis to **opinia eksperta** (operatora), nie copy-paste.
2. **Reports zostają jako osobne pliki** w run directory. Są immutable expert testimony — pełna treść, pełen frontmatter, oryginalny styl.
3. **Każda nietrywialna teza w synthesis MUSI mieć file:line ref** do co najmniej jednego raportu. Brak refa = antywzorzec (operator zmyśla).
4. **Dissent jest cytowany z file:line do obu/wszystkich stron** + reasoned judgment operatora dlaczego jedna strona przeważa.
5. Synthesis jest krótki (zwykle 3-8KB). Wartość = jakość interpretacji + precyzja cytowania, nie objętość.

## Antywzorce (jawnie)

- ❌ „patchwork meta-artifact" — konkatenacja verbatim 3 raportów sklejonych razem. Staje się monolitem 30-50KB z duplikowanym frontmatterem, trudnym do czytania, trudnym do wczytania, bez prawdziwej syntezy.
- ❌ „compressed view" — operator czyta 3 raporty, parafrazuje do jednej krótkiej syntezy, publikuje tylko ją. Pojedyncze findingi zostają zmiażdżone; czytelnik traci możliwość zweryfikowania konkretnej tezy względem jej źródła.

## Wzorzec obowiązkowy (decyzja operatora 2026-05-01)

- ✓ Synteza to **osobny, zwięzły dokument**, który interpretuje trzy raporty i wskazuje **dokładne linie** w nich dla każdej nietrywialnej tezy.
- ✓ Raporty zostają jako **osobne artefakty**. Są niezmiennym eksperckim zeznaniem — pełna treść, pełen frontmatter, oryginalny styl.
- ✓ Synteza cytuje je referencjami file:line (np. `claude_<run_id>.md:L42-58`). Czytelnik, który chce zweryfikować, klika referencję i czyta pełny akapit źródłowy. Operator nie parafrazuje eksperta; operator wskazuje na eksperta.
- ✓ Gdy raporty się nie zgadzają, synteza odnotowuje zdanie odrębne **z referencjami file:line do każdej strony** i podaje uzasadniony osąd operatora.

## Reguły głosowania / większości — jawnie odrzucone

Dwie odrębne sekcje: **A. Convergent findings (zdeduplikowane)** oraz **B. Signals (findingi pojedynczego agenta, potencjalnie kluczowe insighty)**. Finding wyniesiony tylko przez jednego agenta jest **nie mniej ważny** niż convergent — to **sygnał**, że jeden ekspert zauważył coś, co inni przeoczyli. Z naszego doświadczenia te findingi pojedynczego agenta to często faktyczny kierunek, którego praca potrzebowała.

Dla każdego sygnału operator pisze:

- **co mówi sygnał** (ocytowane przez file:line)
- **dlaczego pozostali dwaj to przeoczyli** (nie poruszyli pytania, dali złą odpowiedź, prowadzili słabszą strategię wyszukiwania itd.)
- **verdict sygnału operatora**: **amplify** / **flag** / **acknowledge & reject** (z uzasadnieniem)

Sygnały nigdy nie zostają ukryte w ramce „consensus" czy „minority". Dostają **dedykowaną sekcję** w syntezie, gdzie każdy jest nazwany, ocytowany i osądzony przez operatora z osobna.
