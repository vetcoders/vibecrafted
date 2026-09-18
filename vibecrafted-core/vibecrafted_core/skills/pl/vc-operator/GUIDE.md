# vc-operator — GUIDE: framework fal A/B/C/D

> Przewodnik kompozycji do układania wieloprompowego planu w fale dispatchu.
> Źródłowy playbook: dostawa 2026-05-05 II Polar.sh (8 dispatchów w 4 falach);
> dopracowany orkiestracją TextForge 2026-05-16 (10 promptów, 4 fale, pełny
> autonomiczny łańcuch). Czytaj razem z [`SKILL.md`](SKILL.md), [`DISPATCH.md`](DISPATCH.md)
> oraz [`EMIL.md`](EMIL.md).

---

## Dlaczego fale, a nie płaski dispatch

Plan z 10 promptami odpalony płasko (wszystkie 10 jednocześnie) przegrywa na trzech osiach:

1. **Kolizje współdzielonego stanu**: prompty dotykające tego samego providera /
   kontekstu / shella mogą nadpisać nawzajem swoją pracę albo wytworzyć dryf living-tree,
   którego żaden agent nie jest właścicielem.
2. **Powierzchnia odzyskiwania**: pojedyncze zacięcie w środku płaskiego odpalenia zatruwa
   każdy prompt poniżej nieaktualnymi założeniami baseline'u.
3. **Czytelność dla operatora**: operator nie może zaudytować „czy to idzie zgodnie z planem",
   gdy 10 agentów jest w locie; potrzebuje narracji postępu w kształcie fal.

Framework fal rozwiązuje wszystkie trzy, czyniąc **topologię zależności jawną**: każdy prompt
deklaruje `depends_on` i `parallel_with`, a agent operatora grupuje prompty w fale, gdzie każdy
członek fali może bezpiecznie działać razem (albo w ścisłej sekwencji).

---

## Cztery kształty fal

### Fala A — Fundament

**Wzorzec**: jeden prompt, jeden agent, sekwencyjnie. Slice, który odblokowuje całą resztę.
Zwykle szkielet shella, schema, rozszerzenie kontekstu providera albo bazowy kontrakt.

**Reguły**:

- Fala A jest **zawsze** sekwencyjna (rozmiar 1).
- Startuje od bieżącego baseline'u/head operatora i nazywa ten baseline w briefie.
- Jej poprzeczka akceptacji to to, czy następna fala może bezpiecznie użyć jej raportu
  i powstałego head jako baseline'u.
- Jeśli Fala A zawiedzie, cały plan się zacina. Cel odzyskiwania = świeży agent
  z ostrzejszymi kryteriami akceptacji, a _nie_ ten sam prompt do tego samego agenta.

**Przykład** (TextForge): `textforge-shell` — `TextForgeShell.tsx` +
`TextForgeProvider.tsx` + pięć placeholderów regionów. Akceptacja: kingdom
pojawia się w sidebarze, regiony renderują się w obu motywach, provider eksportuje
kontrakty, w które wepną się Fale B+.

### Fala B — Łańcuch sekwencyjny

**Wzorzec**: N promptów, każdy dotyka współdzielonego stanu z poprzedniego. Połącz agentów
w łańcuch — claude → gemini → codex → claude — by uhonorować rotację AGENT
FAIRNESS, utrzymując baseline każdego kroku aktualnym.

**Reguły**:

- Każdy prompt startuje ze zweryfikowanego raportu/head poprzedniego promptu, a nie
  z nieaktualnego założenia planu.
- Ostrzeżenie Living Tree **VERBATIM** w każdym briefie: _„re-read shared file
  IMMEDIATELY before edit; append-only fields; don't delete other agents'
  lines."_
- Weryfikuj zielony commit + raporty między każdym krokiem. Jeśli krok się zatnie,
  dispatch odzyskiwania na _tym_ kroku, zanim ruszysz dalej.
- Czekaj na zielone, zanim odpalisz następny. Żadnej płaskiej równoległości wewnątrz Fali B.

**Przykład** (TextForge): B-1 editor-core (claude) → B-2 tool-rail (gemini)
→ B-3 stylize (codex) → B-4 inspectors (claude). Wszystkie łączą się w łańcuch przez
rozszerzenia `TextForgeProvider.tsx` + mechanikę `TextForgeCanvas.tsx`.

### Fala C — Rozłączna równoległość

**Wzorzec**: 2–3 prompty, których scope'y plików są dowodliwie rozłączne. Odpal
jednocześnie, czekaj na wszystkie, zsyntetyzuj razem.

**Reguły**:

- Rozłączność scope'ów plików to odpowiedzialność agenta operatora do zweryfikowania
  przed grupowaniem. Jeśli dwa prompty oba mutują `TextForgeProvider.tsx`, należą
  do łańcucha Fali B, a nie do równoległej Fali C.
- Startuj każdy prompt z tego **samego zweryfikowanego baseline'u/head**. Integracja
  po stronie operatora dzieje się między falami, nie wewnątrz.
- Używaj dispatchu floty popartego telemetrią dla workerów-dostawców. Natywne
  subagenty są do bounded zwiadu/sidecarów review, nie jako zamienniki dispatchu fal.
  Czekaj na wszystkie ukończenia, zanim odpalisz następną falę.
- Dla promptów z podwójną mutacją, których nie da się rozdzielić, preferuj
  **append-only + ręczny merge** jawnie w obu briefach, z błogosławieństwem operatora,
  że konflikty zostaną rozwiązane po stronie operatora.

**Przykład** (TextForge): Fala C = topbar (gemini) ‖ statusbar (gemini) ‖
diacritics-audit (codex). Topbar + statusbar oba dotykają pól
`TextForgeProvider.tsx` _workspaces_ vs _lastAppliedStyle_ — jawny append-only
w obu briefach. Diacritics jest tylko-backendowy (`src/tools/*.js`),
zerowe ryzyko kolizji.

### Fala D — Finalne zamknięcie

**Wzorzec**: sekwencyjne prompty wymagające najpierw merge'u Fali B+C na trunku.
Zwykle testy integracyjne, dokumentacja, harness e2e, pakowanie.

**Reguły**:

- **Integracja na trunku po stronie operatora dzieje się przed odpaleniem Fali D**,
  nie wewnątrz niej. Agent operatora wystawia punkt stopu „wystarczy wcisnąć guzik",
  prosząc o merge Fali B+C.
- Po merge'u Fala D odpala sekwencyjnie (rozmiar 1–3, prawie zawsze
  sekwencyjnie).
- Ostatni prompt w Fali D pisze finalny handoff w punkcie stopu i wpis
  do backlogu zamknięcia.

**Przykład** (TextForge): D-1 input-parity (codex) — podpina Falę A przez
Falę C w przepływy klawiatury / prawego kliknięcia. D-2 e2e-docs (claude) — smoke
Playwright w obu motywach + update README/GUIDELINES + wpis do backlogu zamknięcia.

---

## Budowa wave atlas — konkretne kroki

1. **Wylistuj każdy prompt** z jego zadeklarowanym `depends_on` i powierzchniami
   współdzielonych plików. Użyj `master-dispatch.md` planu jako prawdy bazowej.
2. **Grupuj po zależności**: prompty bez zależności idą do Fali A; prompty,
   które zależą tylko od Fali A i nie współdzielą stanu z rodzeństwem → kandydaci
   do Fali C; prompty, które łączą się w łańcuch przez współdzielony stan → Fala B.
3. **Zweryfikuj rozłączność Fali C**: dla każdej grupy równoległej wylistuj pliki,
   których dotyka każdy prompt. Jakiekolwiek nakładanie → degraduj do sekwencyjnego
   łańcucha Fali B (albo dodaj jawne notatki koordynacyjne append-only).
4. **Przydziel agentów**: rotuj Claude/Codex/Gemini między falami dla AGENT
   FAIRNESS. Wewnątrz fali wybór agenta jest per-prompt (zobacz pole
   `recommended_agent` w `DISPATCH.md`).
5. **Wybierz punkty startu gałęzi**: Fala A z trunka; łańcuch Fali B z poprzedniego
   zielonego; Fala C z trunka (po merge'u Fali B); Fala D z trunka
   (po merge'u Fali B+C).
6. **Napisz szkielet trackera**: lista checkboxów pogrupowana po fali (wg
   [`EMIL.md`](EMIL.md) Reguła 1). Każdy prompt to jeden bullet, który przechodzi
   `- [ ]` → `- [x]` na zielonym commicie. Dopisz SHA + gałąź, gdy są znane.

   ```markdown
   ## Wave A (foundation)

   - [x] A-1 textforge-shell (claude) — `f6b02744` on `feat/text-context-menu`

   ## Wave B (sequential, shared canvas/provider)

   - [x] B-1 editor-core (claude) — `304791be` on `feat/textforge-editor-core`
   - [x] B-2 tool-rail (gemini) — `ba60ef66` on `feat/textforge-tool-rail`
   - [x] B-3 stylize (codex) — `ab32a848` on `feat/textforge-stylize`
   - [ ] B-4 inspectors (claude) — firing now, await `bc2zb970r`

   ## Wave C (parallel, file-scope disjoint)

   - [ ] C-1 topbar (gemini)
   - [ ] C-2 statusbar (gemini)
   - [ ] C-3 diacritics-audit (codex)

   ## Wave D (final, sequential)

   - [ ] D-1 input-parity (codex) — requires Wave B+C merge
   - [ ] D-2 e2e-docs (claude) — requires D-1
   ```

   Statusy trackera poza `[ ]` / `[x]`: poprzedzaj adnotacjami, gdy trzeba.

   - `- [ ] 🔄 ...` — aktualnie odpalane / await w locie
   - `- [ ] ⚠ ...` — dispatch odzyskiwania odpalony (sparowany z id odzyskiwanego promptu)
   - `- [x] ↻ ...` — wylądowało przez dispatch odzyskiwania, nie oryginalny

---

## Drzewo decyzyjne: do której fali należy ten prompt?

```text
                     Czy ten prompt zależy od
                     zielonego commitu innego promptu?
                              │
                ┌────Nie──────┴────Tak─────┐
                ▼                          ▼
   Czy współdzieli scope plików        Czy zależy od >1 promptu
   z jakimkolwiek promptem rodzeństwa? (wymagany multi-merge)?
        │                                  │
   ┌Tak─┴─Nie┐                  ┌─Nie─────┴────Tak──┐
   ▼          ▼                  ▼                   ▼
 Fala B    Fala A           Łańcuch Fali B        Fala D
 (albo     (jeśli pierwszy; (sekwencyjnie z       (sekwencyjnie z
 jawny     promuj           poprzedniego          trunka po merge'u)
 append-   jeden prompt)    zielonego)
 only;
 Fala C
 z
 ostrzeżeniem)

                                  Fala C
                          (równolegle z trunka)
                          dla każdego promptu, który jest
                          rozłączny w scope plików
                          od rodzeństwa ORAZ
                          zależy tylko od
                          ukończonych fal
```

---

## Doktryna odzyskiwania wewnątrz fali

Gdy członek fali zatnie się lub nie przejdzie bramki:

1. **Przeczytaj raport zaciętego workera** w całości (warstwowo, jeśli trzeba).
   Nie streszczaj z ostrzeżenia o ucięciu.
2. **Zdiagnozuj**: awaria podłoża (zatrute Living Tree, brak ciągłości sesji,
   złamana zależność) vs awaria scope'u (prompt był przeskalowany lub
   niedospecyfikowany) vs awaria implementacji (worker wziął złe cięcie).
3. **Wybierz kształt odzyskiwania**:
   - Podłoże → najpierw fix po stronie operatora, potem re-dispatch.
   - Scope → napisz _ciaśniejszy_ brief ze zredukowaną akceptacją, dispatchuj
     świeżego agenta (NIE tego, który zawiódł — peer-tier, ale inna rotacja).
   - Implementacja → skupiony agent integracyjny: ten sam scope, ostrzejsze podpowiedzi
     o złym cięciu, którego należy unikać.
4. **Odzyskiwanie to dispatch pierwszej klasy**, a nie ponowienie. Ma własne
   ciało promptu, własny run_id, własny raport. Tracker pokazuje oryginalny
   prompt jako `failed`, a odzyskiwanie jako nowy wiersz z
   `recovers <original-id>`.
5. **Dwie porażki na tym samym prompcie → zatrzymaj falę**, napisz handoff
   w punkcie stopu, prosząc operatora o triage. Trzy porażki to zacięcie floty —
   wystaw uczciwy komunikat „potrzebuję wskazówek po stronie operatora".

---

## Antywzorce

- Grupowanie nierozłącznych promptów w Falę C, bo „wyglądają wystarczająco małe"
  → koszt ręcznego merge'u > przyspieszenie z równoległości.
- Odpalanie Fali D przed merge'em Fali B+C po stronie operatora → workerzy widzą nieaktualny trunk.
- Pomijanie update'u trackera między promptami → operator nie może zaudytować postępu.
- Przynoszenie własnego playbooka zamiast czytania struktury fal z planu →
  autor planu już podjął te decyzje; uszanuj je.
- Traktowanie dispatchu odzyskiwania jako „po prostu odpal jeszcze raz" → to inny brief.

---

## Wezwanie do działania

Zmapuj każdy prompt w swoim planie na dokładnie jedną falę, używając drzewa
decyzyjnego powyżej. Odmów odpalenia Fali A, dopóki nie przydzielisz każdego promptu —
plany zmapowane w połowie produkują fale ukształtowane w połowie. Potem napisz szkielet
trackera i wystaw go operatorowi przed odpaleniem. Tracker to kontrakt.

---

## Klamra końcowa

```text
=======================
Fale to nie sztuczka z kalendarza. To sposób, w jaki operator pozostaje zdolny czytać
plan, gdy agenci są wciąż w połowie kroku. Uhonoruj grupowanie, uhonoruj granice
merge'u, uhonoruj doktrynę odzyskiwania. Plan nie wybacza skrótów. (งಠ_ಠ)ง
=======================

Suchar: Dlaczego Fala C nigdy nie dotarła na trunk? Bo ktoś zapomniał
najpierw zmerge'ować Falę B, a agenci dostali napadu złości w swoich pull requestach.
(._.)
```

---

_Vibecrafted. with AI Agents (c)2024–2026_
