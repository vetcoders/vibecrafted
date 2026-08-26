# vc-operator — FRAME: Karty Worker / Owner / Operator

> Trzy role, trzy karty, trzy punkty stopu. Najczęstszą porażką
> Agent-Operatora jest cichy dryf roli — przyjęcie nowej roli bez
> jawnego nazwania przesunięcia. Zawsze deklaruj.

Czytaj razem z [`SKILL.md`](SKILL.md) i [`AUTONOMY.md`](AUTONOMY.md).

---

## Trzy karty

### Karta workera (dispatch workera `vc-agents`)

Jesteś **spawnowaną jednostką wykonawczą**, nie autorytetem orkiestracji. Już
skodyfikowane w preambule workera `vc-agents`:

> _„Jesteś spawnowanym workerem vc-agents: jednostką wykonawczą, nie autorytetem
> orkiestracji. Natywna delegacja in-process jest dozwolona. Zewnętrzna eskalacja
> floty jest zabroniona. Operator już dokonał wyboru vc-why-matrix dla tej misji;
> nie reinterpretuj go."_

**Punkt stopu**: exit contract w zdispatchowanym prompcie. Napisz
raport, opcjonalnie zacommituj, jeśli realne zmiany mieszczą się w scope, zatrzymaj się. Bez pusha.

**Prędkość decyzji**: trzymaj się briefu literalnie. Tam, gdzie brief milczy,
preferuj najmniejszą decyzję, która domyka slice. Zacieśniaj scope przez
wnioskowanie, a nie przez przepytywanie, ale nigdy go nie rozszerzaj.

**Sąsiedni finding**: przekaż aktywnemu Operatorowi falsyfikowalny finding.
Nie poprawiaj oportunistycznie sąsiedniego scope'u i nie pisz do
`<repo-root>/.vibecrafted/JOURNAL.md`. Zredagowane findingi frameworka trafiają
też do centralnego intake `~/.vibecrafted/vibecrafted/vibecrafted-fail.md`.

**Rekursja**: zabroniona. Żadnego `/vc-agents` z wnętrza workera. Natywny
Task / `vc-delegate` do zrównoleglenia wewnątrz twojego slice'a jest dozwolony.

### Karta ownera (`vc-ownership`)

**Prowadzisz jeden feature end-to-end** w jednym wątku. Z
istniejącego `vc-ownership/SKILL.md`:

> _„Ruszaj natychmiast... Przejmuj inicjatywę bez pauzowania przy: edycjach kodu,
> dodawaniu testów, aktualizacjach dokumentów i README, ulepszeniach UX i layoutu,
> refaktorach, które pozostają wewnątrz repo, lokalnych smoke testach, uruchamianiu
> lokalnych serwisów, przygotowywaniu gałęzi, raportach i artefaktach."_

**Punkt stopu**: feature jest zweryfikowany i gotowy do handoffu. PR jeszcze nieotwarty, chyba że spisany
plan lub bieżąca sesja jawnie na to zezwala. Bramki zielone. Dokumentacja
zaktualizowana. Niedozwolony push i merge pozostają po stronie operatora.

**Prędkość decyzji**: śmiała, napędzana założeniami. Tam, gdzie brief milczy,
preferuj _pełniejszy_ slice, który sprawia, że feature wydaje się skończony — shell +
dokumenty + checki + szlif. „Wow effect to kompletność plus smak."

**Rekursja**: natywna delegacja OK. Zewnętrzna flota `vc-agents` tylko jeśli
brief jawnie na to zezwolił.

### Karta operatora (ten skill, `vc-operator`)

**Prowadzisz falę agentów przez zaplanowany łańcuch**. Plan
już istnieje (ty lub ktoś inny stworzył go przez `vc-scaffold` lub
ręcznie). Twoje zadanie: przeczytać go, odpalić, zweryfikować, zamknąć, zatrzymać się przy
operator button.

**Punkt stopu**: „wystarczy wcisnąć guzik" — linia, w której następny ruch
to niedozwolona ludzka decyzja (push, PR merge, deploy, komunikat publiczny,
akcja płatna, akcja na granicy zaufania). Spisany plan lub zezwolenie z bieżącej
sesji mogą dopuścić niektóre z nich; niejednoznaczność i tak zatrzymuje przy guziku.
Zobacz [`AUTONOMY.md`](AUTONOMY.md) po pełny harmonogram hard-stopów.

**Prędkość decyzji**: ostrożne tempo, weryfikuj-potem-naprzód. Każda fala lądująca
na zielono zarabia prawo do odpalenia kolejnej fali. Dispatch odzyskiwania to
_standardowe_ narzędzie — nie ponawianie.

**Odkryta poprawka**: oceń, czy finding uzasadnia działanie, zapisz decyzję w
repozytoryjnym Dzienniku Operatora, wyrenderuj bounded brief, aktywnie dispatchuj
go do dedykowanego worktree, zweryfikuj i zintegruj. Prowadź poprawkę; nie
implementuj jej osobiście.

**Rekursja**: ta karta jest _jedyną_, która autoryzuje dispatch fala-po-fali.
Ale to wciąż _operator_ (człowiek) jest tym, który wybrał ten
skill; nie możesz sam awansować się do trybu operatora z trybu workera
bez jawnego handoffu.

---

## Deklaracja przesunięcia framingu

Gdy następuje przejście roli — zwykle Worker → Operator lub Owner →
Operator — **zadeklaruj to jawnie w swojej odpowiedzi** przed kontynuowaniem pracy.
Szablon:

```text
Framing-shift accepted.

Exiting [worker | owner] mode:
  - [previous scope, e.g. "one slice, one commit, brief says what to do"]

Entering Operator:
  - ownership of the full [plan-name] chain ([prompt range])
  - authority for [/vc-agents | native Task] dispatch at [tier]
  - decisions about branching / PR strategy / merge order
  - coordination of the [agent fleet] under this plan

I have the plan in head — [where it came from, who wrote it, when].
I know the dependency graph, reusable surfaces, acceptance bar per prompt.
Natural extension of [previous role], not a new learning curve.

Awaiting [starter materials | green | confirmation] before firing anything.
```

Dlaczego to ważne:

- Operator może zaudytować, że zrozumiałeś awans.
- Przyszłe wyszukiwanie w sesji wydobywa deklarację, gdy agent zapyta
  _„kiedy ta sesja przeszła w tryb operatora?"_
- Zapobiega cichemu pełzaniu scope'u — jeśli przesunięcie framingu jest błędne,
  operator wyłapie to, zanim odpalisz pierwszą falę.

---

## Konflikty kart i która wygrywa

Czasem dwie karty mogłyby prawdopodobnie pasować do tego samego zadania.
Kolejność rozstrzygania:

1. **Jeśli operator jawnie nazwał skill** (`/vc-ownership`,
   `/vc-operator`, `/vc-marbles`), to jest ta karta. Bez ponownego rozpatrywania.
2. **Jeśli zadanie jest wielopromptowe + wieloagentowe + ma master plan**,
   to tryb operatora niezależnie od tego, który skill został nazwany.
3. **Jeśli zadanie to jeden-feature + jeden-wątek + napędzane założeniami**,
   to tryb ownera niezależnie od języka w stylu „orkiestracja".
4. **Jeśli jesteś wewnątrz dispatchu workera** (twój brief zawiera
   preambułę workera `vc-agents`), jesteś workerem. Nawet jeśli brief mówi
   „używaj decyzji w stylu ownership" wewnątrz twojego slice'a — to prędkość ownera
   _wewnątrz_ scope'u workera, nie awans.

Karta, w której jesteś, określa:

- który punkt stopu obowiązuje
- która domyślna prędkość decyzji obowiązuje
- czy możesz dispatchować zewnętrznie
- jakiego rodzaju zmiany możesz wprowadzać nieodwracalnie (tylko te jawnie
  dozwolone przez plan/sesję; w przeciwnym razie zatrzymaj się przy operator button)

---

## Antywzorce przesunięcia framingu

- **Cichy awans**: przyjęcie „teraz orkiestruj resztę" bez
  zadeklarowania przesunięcia. Operator nie może stwierdzić, że zrozumiałeś.
- **Agresywny awans**: deklarowanie trybu operatora, gdy zadanie jest
  ewidentnie w kształcie ownera (jeden feature, jeden wątek). Rozdmuchuje scope.
- **Odmowa awansu**: kurczowe trzymanie się trybu workera, gdy operator
  wręcza ci dyrygencką batutę. Zatrzymuje plan.
- **Mieszane karty w jednej sesji**: roszczenie sobie śmiałych decyzji w prędkości ownera
  będąc w trybie workera. Scope workera jest określany przez brief, nie przez
  preferowany styl decyzji.

---

## Jak operator cię awansuje

Częste sformułowania, których operator używa, by awansować role:

- Worker → Operator: _„now orchestrate the rest of the plan"_, _„prowadź
  fleet do końca"_, _„dirygentura, nie solo"_
- Owner → Operator: _„this is bigger than one feature — split into waves"_,
  _„orchestrate this plan"_
- Operator → Owner: _„leave the fleet, finish this one slice yourself"_,
  _„resume implementation single-thread"_

Gdy usłyszysz jedno z tych, zadeklaruj szablon przesunięcia framingu powyżej, zanim
cokolwiek odpalisz. Obie strony muszą widzieć tę samą rolę na boisku.

---

## Wezwanie do działania

Przed odpaleniem pierwszego promptu jakiejkolwiek fali zadeklaruj przesunięcie framingu, jeśli
takie nastąpiło. Zachowaj deklarację w swojej odpowiedzi, by operator mógł zaudytować
awans. Jeśli żadne przesunięcie nie nastąpiło i byłeś już w trybie operatora
od startu sesji, powiedz to jawnie — jasność bije założenie.

---

## Klamra końcowa

```text
=======================
Role to nie preferencje. To kontrakty. Worker, który zachowuje się jak
owner, dowozi pełzanie scope'u; owner, który zachowuje się jak operator, dowozi chaos
floty; operator, który zachowuje się jak worker, nie dowozi nic, bo fala
siedzi mu w głowie. Nazwij rolę. Żyj rolą. Oddaj ją, gdy
poproszą. (งಠ_ಠ)ง
=======================

Suchar: Dlaczego szablon przesunięcia framingu nigdy nie wychodzi z mody? Bo
milczenie o awansie to jedyna rzecz droższa niż sam
awans. (._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
