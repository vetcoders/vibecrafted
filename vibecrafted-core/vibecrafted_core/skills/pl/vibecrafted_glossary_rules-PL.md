Vibecrafted. glossary PL

Słownik żargonu, konceptów i nazw, których nie tłumaczymy na siłę.

Ten glossariusz służy do tłumaczenia skillów Vibecrafted z EN na PL bez tworzenia pato-kalek językowych.

Zasada ogólna:

Nazwy własne, nazwy skillów, nazwy mechanik runtime’u i pojęcia operacyjne zostają verbatim, jeśli polskie tłumaczenie
brzmiałoby sztucznie, zmieniałoby sens albo psuło rozpoznawalność systemu.

W polskim tekście wolno je obudowywać naturalnym opisem, np.:

- “uruchom vc-followup”
- “zrób followup pass”
- “to jest operator run”
- “marbles powinny dostać bounded target”
- “worker nie podejmuje decyzji operatora”

Nie robimy tłumaczeń typu:

- “kulki”
- “nawadnianie”
- “przycinanie”
- “dekorowanie”
- “przepływ pracy” jako sztywna kalka wszędzie
- “czas wykonawczy” zamiast runtime
- “przegląd następczy” zamiast followup

⸻

1. Nazwa systemu i formy bazowe

Vibecrafted.

Zostaje: Vibecrafted

Nie tłumaczyć. Nie spolszczać. Nie pisać "Vibecrafted",“Widecrafted”, “Vibecraft”, “Wajbkrafted”.

Dopuszczalne skróty w roboczych notatkach:

- VC
- vibecrafted
- vibecrafted tylko jako nazwa CLI / komendy

Przykłady:

- “Vibecrafted prowadzi pracę przez operatora.”
- “Odpal vibecrafted init claude.”
- “To jest test VC jako systemu, nie pojedynczego promptu.”

⸻

Vibecrafted

Zostaje: Vibecrafted

To nazwa CLI, więc zawsze zapis techniczny, najlepiej w backtickach.

Przykłady:

- vibecrafted init claude
- vibecrafted operator claude --file dispatch.md
- vibecrafted loop status

Nie tłumaczyć jako “wibrorzemiosło”, “vibestwórstwo”, “rzemieślnik nastroju” itd.

⸻

VC

Zostaje: VC

Roboczy skrót od Vibecrafted. Naturalny w rozmowach i dokumentach wewnętrznych.

Przykład:

- “VC bez operatora-przewodnika jest frameworkiem dla power userów.”

⸻

2. Skille i nazwy komend

Nazwy skillów zostają verbatim. Nie tłumaczymy ich na polskie rzeczowniki. Można dodać polskie objaśnienie.

skill

Zostaje: skill

Nie tłumaczyć jako “umiejętność”, jeśli mowa o jednostce systemu VC. “Umiejętność” brzmi jak capability modelu, a nie
artefakt runtime’u.

Przykłady:

- “Ten skill jest read-only.”
- “Nie każ userowi wybierać skillów.”
- “Skill contract powinien wejść do kontekstu agenta.”

⸻

vc-init

Zostaje: vc-init

Znaczenie: start orientacyjny w repo, ustalenie gdzie jesteśmy, jaki jest branch, stan, ryzyka, pamięć / loctree /
kontekst.

Nie tłumaczyć jako “inicjalizacja”, chyba że opisowo.

Dobre:

- “Najpierw robimy vc-init.”
- “vc-init ustawia kontekst repo.”

Złe:

- “uruchom inicjalizatora”
- “zrób inicjację repo”

⸻

vc-followup

Zostaje: vc-followup albo followup

Znaczenie: diagnoza rzeczywistości po wejściu w repo / po zmianach / po etapie pracy. Patrzy, co jest zdrowe, co nie,
jaki jest next move.

Nie tłumaczyć jako “następstwo”, “kontynuacja”, “przegląd następczy”.

Dobre:

- “Zrób followup pass.”
- “vc-followup to diagnoza rzeczywistości, nie walidacja kontraktu.”
- “Po commicie robimy followup.”

Złe:

- “następczy audyt”
- “pouzupełnieniowy przegląd”
- “śledzenie dalsze”

⸻

vc-audit

Zostaje: vc-audit albo audit

Znaczenie w VC: walidacja względem konkretnego kontraktu/specyfikacji, nie ogólny “przegląd repo”.

Dobre:

- “Audit ma sens, gdy mamy spec.”
- “To jest audit kontraktu README vs kod.”
- “vc-audit falsyfikuje wymagania.”

Uwaga UX: dla normalnego usera “audit” brzmi jak ogólny przegląd kodu. W dokumentacji PL warto wyjaśnić, że w VC audit
oznacza “sprawdzenie względem konkretnego kontraktu”.

Nie tłumaczyć jako “rewizja”, “kontrola”, “audytowanie” w nazwie skilla.

⸻

vc-review

Zostaje: vc-review albo review

Znaczenie: review diffu, PR, zmian, patcha. Bardziej code review niż ogólna diagnoza.

Dobre:

- “Review sprawdza konkretną zmianę.”
- “Po PR robimy vc-review.”
- “Review nie powinien naprawiać kodu.”

Nie tłumaczyć jako “recenzja”, jeśli mowa o skillu.

⸻

vc-implement

Zostaje: vc-implement albo implement

Znaczenie: wykonanie ograniczonego zadania. Nie jest pełnym operatorem ani systemowym prowadzeniem pracy.

Dobre:

- “Implement jest dobry na bounded fix.”
- “Nie zaczynaj od implement, jeśli nie wiemy jeszcze co naprawiać.”

Złe:

- “wdrażacz”
- “implementator”
- “zrób implementację skillem wdrożeniowym”

⸻

vc-justdo

Zostaje: vc-justdo albo justdo

Status: **samodzielny** skill + launcher postawy Just Do (ADR-0001). **Nie** alias
`vc-implement`. Typ zadania definiuje prompt; non-pipeline (nie faza ship).

Nie tłumaczyć nazwy skilla jako “po prostu zrób” — w prozie można opisać postawę:
“bez gadania, zrób”.

Dobre:

- “justdo to osobna postawa, nie alias implement.”
- “Ship WRITE → implement. Daily rescue / typ z promptu → justdo.”

⸻

vc-workflow

Zostaje: vc-workflow albo workflow

Znaczenie: uporządkowany flow pracy typu examine → research → implement albo podobny. To nie jest “przepływ pracy” w
każdym zdaniu.

Dobre:

- “Workflow prowadzi pracę przez fazy.”
- “To wygląda na workflow, nie pojedynczy implement.”

Złe:

- “przepływ roboczy”
- “proces pracy przepływowej”

⸻

vc-scaffold

Zostaje: vc-scaffold albo scaffold

Znaczenie: przejście od pomysłu do pierwszego planu / konstrukcji projektu / szkieletu.

Nie tłumaczyć jako “rusztowanie” w nazwie.

Dobre:

- “Dla pomysłu bez repo zaczynamy od scaffold.”
- “Scaffold ma stworzyć plan, nie od razu pełny runtime.”

⸻

vc-marbles

Zostaje: vc-marbles albo marbles

To nazwa mechaniki. Nie tłumaczyć jako “kulki”, “marmurki”, “koraliki”.

Znaczenie: bounded convergence / równoległe lub wieloagentowe sondy na ograniczonym problemie, z raportami i recepcją
wyników.

Dobre:

- “Marbles potrzebują bounded target.”
- “Nie dawaj marbles zadania ‘ulepsz repo’.”
- “To jest marbles pass na trzy hipotezy.”

Złe:

- “kulki naprawcze”
- “marmurki konwergencji”
- “puśćmy kuleczki po repo”

⸻

marble

Zostaje: marble

Pojedynczy worker / pojedyncza sonda / jednostka pracy w mechanice marbles.

Dobre:

- “Jeden marble powinien dostać jeden bounded target.”
- “Marble nie zna pełnej narracji, tylko zadanie.”

Nie tłumaczyć jako “kulka”.

⸻

vc-polarize

Zostaje: vc-polarize albo polarize

Znaczenie: rozcięcie sprzecznych prawd produktowych / decyzyjnych, wyostrzenie kierunku.

Nie tłumaczyć jako “polaryzuj”, jeśli brzmi niezręcznie. W zdaniu można pisać “zrobić polarize pass”.

Dobre:

- “Gdy są dwie konkurencyjne prawdy, robimy polarize.”
- “Polarize nie jest fixem, tylko cięciem decyzyjnym.”

⸻

vc-prune

Zostaje: vc-prune albo prune

Znaczenie: sprzątanie repo / usuwanie martwych części / repo curation, ale nie masowa wycinka.

Nie tłumaczyć jako “przycinanie”, “obcinanie”, “prunek”.

Dobre:

- “Prune zaczyna od inventory.”
- “Prune nie może być clear-cuttingiem.”
- “To jest prune pass, nie refactor.”

⸻

vc-hydrate

Zostaje: vc-hydrate albo hydrate

Znaczenie: uzupełnienie powierzchni adopcji, docs, onboarding, install path, brakujących elementów, które pozwalają
produktowi żyć poza kodem.

Nie tłumaczyć jako “nawadniać”.

Dobre:

- “Po DoU może wejść hydrate.”
- “Hydrate uzupełnia adopcję i docs.”

Złe:

- “nawodnij dokumentację”
- “hydratacja powierzchni”
- “uwodnienie onboardingowe”

⸻

vc-decorate

Zostaje: vc-decorate albo decorate

Znaczenie: polish, coherence pass, visual/UX finish, dopięcie powierzchni produktu bez zmiany jego kierunku.

Nie tłumaczyć jako “dekorowanie”, bo brzmi jak ozdabianie choinki.

Dobre:

- “Decorate ma dopiąć istniejący język wizualny.”
- “To jest decorate pass, nie redesign.”

⸻

vc-dou

Zostaje: vc-dou albo DoU

Rozwinięcie zostaje: Definition of Undone

Można po polsku opisać jako: “lista rzeczy, przez które produkt nadal nie jest domknięty”.

Nie tłumaczyć oficjalnie jako “Definicja Niezrobionego”, chyba że bardzo roboczo w nawiasie.

Dobre:

- “DoU mówi, co jeszcze blokuje gotowość.”
- “Po DoU wiemy, czy iść w hydrate, decorate czy release.”

⸻

vc-release

Zostaje: vc-release albo release

Znaczenie: release gate, końcowy etap przed wypuszczeniem. W VC release zawiera twarde operator buttons.

Nie tłumaczyć jako “wydanie” w nazwie skilla. W opisach można pisać “wydanie”, ale komenda/skill zostaje release.

Dobre:

- “Release nigdy nie powinien sam przekraczać operator button.”
- “vc-release może przygotować, ale nie pushować bez zgody.”

⸻

vc-agents

Zostaje: vc-agents albo agents

Znaczenie: mechanika pracy z flotą zewnętrznych agentów / workerów.

Dobre:

- “Agents to execution fleet, nie operator.”
- “Workerzy dostają małe kontrakty.”

⸻

vc-delegate

Zostaje: vc-delegate albo delegate

Znaczenie: delegacja do pomocniczego modelu/agenta, zwykle nie jako pełna flota.

Dobre:

- “Delegate jest pomocnikiem, nie pełnym workflow.”
- “Nie używaj delegate, jeśli potrzebny jest operator run.”

⸻

vc-research

Zostaje: vc-research albo research

Znaczenie: research swarm / wielomodelowe sprawdzenie wiedzy, nie implementacja.

Dobre:

- “Research jest dobry, gdy nie wiemy czegoś poza repo.”
- “Research kończy się syntezą, nie patchem.”

⸻

vc-partner

Zostaje: vc-partner albo partner

Znaczenie: strategiczny / systemowy partner do myślenia o stanie projektu, runtime, kierunku, decyzjach.

Dobre:

- “Partner nie jest workerem od kodu.”
- “Użyj partner, gdy nie wiadomo, jaki systemowy ruch wykonać.”

⸻

vc-ownership

Zostaje: vc-ownership albo ownership

Znaczenie: przejęcie odpowiedzialności za product slice / obszar, a nie zwykły fix.

Nie tłumaczyć jako “własność” w nazwie.

Dobre:

- “Ownership oznacza odpowiedzialność za slice.”
- “To jest ownership pass, nie jednorazowy implement.”

⸻

vc-prview

Zostaje: vc-prview albo prview

Znaczenie: generowanie / przygotowanie artefaktów PR-view / przeglądu zmian. Nie mylić z review.

Nie tłumaczyć jako “podgląd PR” w nazwie, ale można tak objaśniać.

⸻

vc-screenscribe

Zostaje: vc-screenscribe albo screenscribe

Znaczenie: skill / mechanika pracy z nagraniem ekranu, transcriptami, findingami, screenshotami, paczką dla agenta.

Nie pisać Screenscribe, jeśli decyzja produktu mówi lowercase screenscribe.

⸻

vibecraftsmanship

Zostaje: vibecraftsmanship

To nazwa meta-postawy / jakości pracy. Nie tłumaczyć jako “wibratorzemiosło”, “rzemiosło vibe’owe” ani “vibe-rzemiosło”.

Dobre:

- “vibecraftsmanship opisuje posture pracy.”
- “To nie jest skill od kodu, tylko rama jakościowa.”

⸻

3. Role w systemie

operator

Zostaje: operator

To może być człowiek albo agent-operator, ale ich uprawnienia są różne.

Znaczenie: warstwa decyzyjna i prowadząca. Operator wybiera kierunek, stany, kolejne ruchy, stop pointy, boundaries.

Dobre:

- “Człowiek-operator może zrobić override.”
- “Agent-operator nie powinien przekraczać operator button.”
- “Operator prowadzi usera za rękę.”

Nie tłumaczyć jako “obsługujący”, “operator systemu wykonawczego” itd.

⸻

operator-przewodnik

Preferowana polska forma opisowa dla brakującej warstwy UX.

Znaczenie: agent / warstwa, która prowadzi usera przez VC bez zmuszania go do wyboru skillów.

Dobre:

- “VC potrzebuje operatora-przewodnika.”
- “Operator-przewodnik rozpoznaje intencję i wybiera next move.”

⸻

worker

Zostaje: worker

Nie tłumaczyć jako “pracownik”, jeśli mowa o agencie wykonawczym.

Znaczenie: jednostka wykonawcza, mały agent od bounded tasku, bez prawa do szerokich decyzji.

Dobre:

- “Worker wykonuje, operator decyduje.”
- “Worker nie powinien mieć pełnej historii.”
- “Worker dostaje minimalny kontrakt.”

⸻

agent

Zostaje: agent

W polskim naturalne. Może oznaczać Claude/Codex/Gemini albo innego wykonawcę.

Dobre:

- “Agent ma przeczytać kontrakt skilla.”
- “Agent nie powinien sam pushować.”

⸻

agent-operator

Zostaje: agent-operator

Znaczenie: agent pełniący rolę prowadzącą, ale nadal bez praw człowieka-operatora do przekraczania twardych granic.

Dobre:

- “Agent-operator może prowadzić flow, ale nie pushuje bez zgody.”
- “Człowiek ma zawsze wyższy override niż agent-operator.”

⸻

human operator

Można pisać: człowiek-operator albo human operator

Znaczenie: człowiek ma najwyższą decyzyjność.

Dobre:

- “Human operator decyduje o push/PR/release.”
- “Człowiek-operator może świadomie przekroczyć systemowe zalecenie.”

⸻

execution fleet

Zostaje: execution fleet albo opisowo “flota workerów”.

Nie robić dziwnej kalki “flota egzekucyjna”.

Dobre:

- “vc-agents zarządza execution fleet.”
- “Flota workerów dostaje małe zadania.”

⸻

4. Runtime i mechaniki wykonawcze

runtime

Zostaje: runtime

Nie tłumaczyć jako “czas wykonania”, gdy mowa o systemowej warstwie VC.

Znaczenie: warstwa uruchamiania, kontynuacji, loopów, agentów, stanu, stop/retry/recover.

Dobre:

- “Runtime musi mieć stop/retry/recover.”
- “To jest luka runtime’u, nie błąd promptu.”

⸻

control plane

Zostaje: control plane

Można objaśnić: “warstwa kontroli / koordynacji”.

Nie tłumaczyć sztywno jako “płaszczyzna kontroli”, jeśli brzmi jak chmura w spodniach.

Dobre:

- “VC ma shell/Zellij i control plane, które trzeba spiąć.”
- “Control plane powinien wiedzieć, jaki jest stan runu.”

⸻

loop

Zostaje: loop

Znaczenie: pętla operatorowa / kontynuacja pracy przez kolejne kroki.

Dobre:

- “Operator loop powinien wiedzieć, kiedy kontynuować.”
- “Sprawdź Vibecrafted loop status.”

Nie tłumaczyć jako “pętla”, jeśli mowa o nazwie mechaniki, ale w opisach “pętla” jest OK.

⸻

operator loop

Zostaje: operator loop

Znaczenie: pętla prowadząca pracę operatora.

Dobre:

- “Operator loop to nie pojedynczy skill.”
- “Loop wymaga stop condition.”

⸻

run

Zostaje: run

Znaczenie: uruchomiony przebieg pracy, np. operator run, marbles run, research run.

Dobre:

- “To był dobry operator run.”
- “Run kończy się final report.”

Nie tłumaczyć zawsze jako “przebieg”, “wykonanie”, “uruchomienie”, bo traci operacyjny smak.

⸻

pass

Zostaje: pass

Znaczenie: przejście przez wybrany typ pracy, np. followup pass, decorate pass, audit pass.

Dobre:

- “Zróbmy audit pass.”
- “To jest mały polish pass.”

Nie tłumaczyć jako “przejście” za każdym razem.

⸻

flow

Zostaje: flow

Znaczenie: ścieżka działania / sekwencja pracy.

Dobre:

- “Domyślny flow dla nowego repo to init → followup → decyzja.”
- “Ten flow jest za trudny dla usera.”

“Przepływ” można użyć opisowo, ale nie zmuszać wszędzie.

⸻

entrypoint

Zostaje: entrypoint

Znaczenie: pierwszy punkt wejścia do systemu / repo / flow.

Dobre:

- “VC ma za cienki entrypoint dla nowych userów.”
- “Potrzebujemy ludzkiego entrypointu, nie listy skillów.”

⸻

golden path

Zostaje: golden path

Znaczenie: domyślna, najprostsza, oficjalna ścieżka użycia.

Nie tłumaczyć jako “złota ścieżka” w dokumentach technicznych, chyba że jako luźny opis.

Dobre:

- “Brakuje golden path dla usera z repo.”
- “README powinien pokazać golden path.”

⸻

state machine

Zostaje: state machine albo opisowo “maszyna stanów”.

Dobre:

- “Operator powinien działać jak state machine.”
- “Stany robocze: PLAN / BUILD / CHECK / FIX / FINISH.”

⸻

PLAN / BUILD / CHECK / FIX / FINISH

Zostają verbatim jako nazwy stanów.

Nie tłumaczyć na “PLANUJ / BUDUJ / SPRAWDŹ / NAPRAW / KOŃCZ” w kontrakcie systemowym.

Dobre:

- “Repo jest teraz w CHECK.”
- “Po followup przechodzimy do FIX albo FINISH.”

⸻

INIT

Zostaje: INIT

Stan początkowej orientacji.

Dobre:

- “INIT ma ustalić stan repo.”
- “Nie pomijaj INIT przy nieznanym repo.”

⸻

dispatch

Zostaje: dispatch

Znaczenie: pakiet zlecenia / master prompt / dokument sterujący dla operatora lub agenta.

Nie tłumaczyć jako “wysyłka”, “dyspozycja”, “rozkaz”.

Dobre:

- “Przygotuj master dispatch.”
- “Operator startuje z dispatch file.”

⸻

master dispatch

Zostaje: master dispatch

Znaczenie: nadrzędny dokument sterujący całym runem.

Dobre:

- “To jest master dispatch dla operator runu.”
- “Master dispatch zawiera mission, boundaries, waves i stop conditions.”

⸻

handoff

Zostaje: handoff

Znaczenie: przekazanie kontekstu między agentami/sesjami.

Nie tłumaczyć jako “przekazanie pałeczki” w docs.

Dobre:

- “Napisz handoff dla nowej Mikserki.”
- “Handoff musi zawierać stan, decyzje i next move.”

⸻

prompt

Zostaje: prompt

W polskim roboczym kontekście AI to normalne słowo.

Dobre:

- “Daj prompt do aktywnej sesji.”
- “Nie pisz idealnego promptu za usera, jeśli operator powinien to zrobić.”

⸻

skill contract

Zostaje: skill contract

Można opisać: “kontrakt skilla”.

Znaczenie: zasady, granice, output, write/read permissions danego skilla.

Dobre:

- “Skill contract powinien być automatycznie injectowany.”
- “Agent nie ma zgadywać zasad skilla z promptu usera.”

⸻

contract injection

Zostaje: contract injection

Można opisać jako: “wstrzyknięcie kontraktu skilla do kontekstu”.

Nie robić “iniekcji kontraktu” w user-facing copy, bo brzmi medycznie i dziwnie.

Dobre:

- “Brakuje contract injection.”
- “Runtime powinien wstrzykiwać skill contract.”

⸻

runner

Zostaje: runner

Znaczenie: mechanizm uruchamiający flow / agenta / skill.

Dobre:

- “Runner musi rozróżniać terminal i aktywną sesję.”
- “RUNNER.md opisuje wykonanie.”

⸻

await-run

Zostaje: await-run

Znaczenie: mechanika oczekiwania na zakończenie / kontynuację runu.

Dobre:

- “await-run nie powinien udawać autonomii.”
- “Operator może użyć await-run --then-cmd.”

⸻

--then-cmd

Zostaje: --then-cmd

To flaga techniczna. Nie tłumaczyć.

W dokumentacji PL opisać jako: komenda wykonywana po spełnieniu warunku / zakończeniu runu, jeśli operator ją
zatwierdził.

⸻

5. Granice, decyzje i bezpieczeństwo

boundary

Zostaje: boundary w kontekście systemowym, albo “granica” w naturalnym polskim opisie.

Dobre:

- “Push to operator boundary.”
- “To przekracza granicę operatora.”

⸻

hard boundary

Zostaje: hard boundary albo “twarda granica”.

Dobre:

- “Release jest hard boundary.”
- “Agent nie może przekroczyć hard boundary bez człowieka.”

⸻

hard stop

Zostaje: hard stop

Znaczenie: bezwzględne zatrzymanie pracy.

Nie tłumaczyć jako “twardy stop” w oficjalnych docs, choć w rozmowie może przejść.

Dobre:

- “Hard stop przed push.”
- “Hard stop, jeśli potrzebna jest migracja danych.”

⸻

stop condition

Zostaje: stop condition albo “warunek stopu”.

Dobre:

- “Każdy operator run musi mieć stop conditions.”
- “Stop condition: auth/billing/deploy.”

⸻

operator button

Zostaje: operator button

Znaczenie: czynność, której agent nie może wykonać sam, nawet jeśli technicznie potrafi.

Typowe operator buttons:

- force-push / trunk push
- PR merge
- merge do trunka
- release
- deploy
- public repo toggle
- tag
- notarization
- install global
- deleting branches
- deleting locks
- broad cleanup

Dobre:

- “Force-push i push na trunk są operator button.”
- “Agent może przygotować PR body, ale nie otwiera PR bez zgody.”

Nie tłumaczyć jako “przycisk operatora” w głównym żargonie, chyba że objaśniająco.

⸻

override

Zostaje: override

Znaczenie: świadoma decyzja człowieka ponad regułami systemu.

Dobre:

- “Człowiek-operator ma prawo override.”
- “Agent-operator nie ma prawa override wobec hard boundaries.”

⸻

gate

Zostaje: gate

Znaczenie: checkpoint / walidacja / warunek przejścia.

Dobre:

- “Verification gate musi przejść.”
- “Release gate blokuje wyjście.”

“Bramka” może być OK w luźnym języku, ale gate lepiej zostawić w systemowym żargonie.

⸻

verification gate

Zostaje: verification gate

Znaczenie: sprawdzenie, że zmiana jest poprawna.

Dobre:

- “Po fixie uruchom verification gates.”
- “Gate może być manualny, jeśli repo nie ma testów.”

⸻

smoke test

Zostaje: smoke test albo smoke

Nie tłumaczyć jako “test dymny”.

Dobre:

- “Zrób smoke po install.”
- “To repo jest idealne na realny smoke VC.”

⸻

dogfood

Zostaje: dogfood

Znaczenie: używanie własnego systemu do własnej pracy.

Nie tłumaczyć jako “jedzenie karmy dla psów”.

Dobre:

- “VC musi przejść dogfood na realnych repo.”
- “To jest dogfood operator flow.”

⸻

safety

Zostaje: safety albo “bezpieczeństwo”, zależnie od kontekstu.

W mechanice agentowej safety często zostaje.

Dobre:

- “To jest safety boundary.”
- “Bezpieczeństwo danych jest out of scope dla workera.”

⸻

6. Zakres, autonomia i styl pracy

bounded

Zostaje: bounded

Znaczenie: ograniczony zakresem, hipotezą, powierzchnią, targetem.

Nie tłumaczyć wszędzie jako “ograniczony”, bo traci rolę techniczną.

Dobre:

- “Marbles muszą mieć bounded target.”
- “To jest bounded fix, nie system rewrite.”

⸻

scope

Zostaje: scope albo “zakres”.

Obie formy są OK. W technicznym żargonie VC scope zostaje.

Dobre:

- “Scope to tylko transcript/audio.”
- “Nie wychodź poza scope.”

⸻

target

Zostaje: target

Znaczenie: konkretny cel pracy / hipoteza / obszar dla workera.

Dobre:

- “Daj marble’owi jeden target.”
- “Target A: repo vs installed copy boundary.”

⸻

slice

Zostaje: slice

Znaczenie: wycinek produktu/systemu, za który można wziąć odpowiedzialność.

Nie tłumaczyć jako “plaster”.

Dobre:

- “Ownership obejmuje product slice.”
- “To jest backend slice, nie całe repo.”

⸻

lane

Zostaje: lane

Znaczenie: pas pracy / typ operacji, np. read lane, write lane.

Dobre:

- “Followup jest read lane.”
- “Implement przechodzi w write lane.”

Nie tłumaczyć jako “pas ruchu” w dokumentacji.

⸻

read-only

Zostaje: read-only

Znaczenie: agent może czytać i raportować, ale nie pisać zmian.

Dobre:

- “Audit jest read-only.”
- “Read-only pass kończy się raportem.”

⸻

write lane

Zostaje: write lane

Znaczenie: tryb pracy, w którym agent może modyfikować pliki.

Dobre:

- “Do write lane przechodzimy dopiero po diagnozie.”
- “Write lane wymaga boundaries.”

⸻

full autonomy

Zostaje: full autonomy

Można opisać: “pełna autonomia w obrębie granic”.

Dobre:

- “Full autonomy: non-destructive feature-branch push after your commit; no force/trunk/merge.”
- “Pełna autonomia nie obejmuje operator buttons.”

⸻

semi-autonomous

Zostaje: semi-autonomous

Znaczenie: agent prowadzi większość pracy, ale częściej zatrzymuje się na decyzje.

Dobre:

- “Ten run jest semi-autonomous.”
- “Semi-autonomous jest dobre dla pierwszego smoke’u.”

⸻

unattended

Zostaje: unattended

Znaczenie: praca bez ciągłego pilnowania przez człowieka, ale niekoniecznie z prawem do decyzji.

Dobre:

- “Unattended nie znaczy unlimited.”
- “Run może być unattended, ale stop przed PR.”

⸻

convergence

Zostaje: convergence

Znaczenie: domykanie rozbieżnych wyników / dojście do stabilnej prawdy / stabilnego fixa.

Nie tłumaczyć jako “konwergencja” wszędzie, choć technicznie jest poprawne. W VC convergence brzmi jak koncept
systemowy.

Dobre:

- “Marbles robią convergence na bounded problem.”
- “Brakuje convergence report.”

⸻

reception

Zostaje: reception

Znaczenie: warstwa odbioru wyników z workerów/marbles, która pamięta, porównuje i decyduje, co dalej.

Nie tłumaczyć jako “recepcja”, bo brzmi jak hotel.

Dobre:

- “Marbles bez reception robią chaos.”
- “Reception musi rozstrzygnąć sprzeczne raporty.”

⸻

falsify / falsification

Preferowane: falsyfikować, falsyfikacja

To jedno z pojęć, które można naturalnie spolszczyć.

Dobre:

- “Read lane ma falsyfikować, nie dopieszczać.”
- “Audit falsyfikuje spec.”

⸻

evidence

Zostaje: evidence albo “dowód”, zależnie od stylu.

W raportach VC często lepiej brzmi evidence.

Dobre:

- “Podaj evidence anchors.”
- “Bez evidence to tylko guess.”

⸻

evidence anchor

Zostaje: evidence anchor

Znaczenie: konkretny plik/linia/komenda/log, który podpiera finding.

Nie tłumaczyć jako “kotwica dowodowa” w normalnym tekście, chyba że żartem.

Dobre:

- “Każdy P1 musi mieć evidence anchor.”
- “Finding bez evidence anchor jest weak.”

⸻

verdict

Zostaje: verdict

Znaczenie: końcowy osąd typu PASS / CONCERNS / FAIL / HEALTHY WITH GAPS.

Dobre:

- “Followup daje verdict.”
- “Verdict: healthy with gaps.”

Nie tłumaczyć jako “werdykt” w nazwach pól, ale w zdaniu “werdykt” jest OK.

⸻

finding

Zostaje: finding

Znaczenie: konkretna obserwacja/problem z dowodem.

Dobre:

- “To jest P1 finding.”
- “Nie rób findingu bez evidence.”

Nie tłumaczyć jako “znalezisko” w docs.

⸻

severity

Zostaje: severity

Znaczenie: poziom ważności findingu.

Dobre:

- “Severity: P0/P1/P2.”
- “P2 nie blokuje release.”

⸻

P0 / P1 / P2 / P3

Zostają: P0, P1, P2, P3

Nie tłumaczyć. To poziomy priorytetu/severity.

Ogólnie:

- P0: krytyczne, blokujące, system broken
- P1: ważne, powinno być naprawione przed merge/release
- P2: istotne, ale nie blokujące
- P3: polish / backlog / nice-to-have

⸻

source of truth

Zostaje: source of truth albo “źródło prawdy”.

Obie formy są OK. W architekturze technicznej często zostawić source of truth.

Dobre:

- “Nie twórz drugiego source of truth.”
- “Kanoniczne źródło prawdy jest w runtime.”

⸻

canonical

Uwaga: jeśli istnieje alergia copy na “canonical”, nie nadużywać w user-facing PL.

Technicznie można zostawić canonical w kodowym/kontraktowym kontekście, ale w copy preferować:

- “default”
- “główna ścieżka”
- “jedna ścieżka prawdy”
- “właściwa ścieżka”
- “kontraktowa ścieżka”

Nie robić “kanoniczny” wszędzie jak zaklęcia.

⸻

stale

Zostaje: stale

Znaczenie: nieświeży, przestarzały względem aktualnego stanu repo.

Dobre:

- “Snapshot jest stale.”
- “Nie pracuj na stale context.”

⸻

dirty

Zostaje: dirty

Znaczenie: repo ma lokalne zmiany.

Dobre:

- “Dirty worktree wymaga ostrożności.”
- “Nie zaczynaj operator runu bez statusu.”

⸻

7. Artefakty i raporty

artifact

Zostaje: artifact albo “artefakt”.

Obie formy są OK. “Artefakt” jest naturalny po polsku.

Dobre:

- “Run powinien zostawić artifact.”
- “Artefakty idą do .artifacts/.”

⸻

report

Zostaje: report albo “raport”.

Obie formy OK.

Dobre:

- “Worker kończy reportem.”
- “Final report ma zawierać commity i gates.”

⸻

delta report

Zostaje: delta report

Znaczenie: raport tylko z tego, co się zmieniło / co worker ustalił względem punktu startu.

Dobre:

- “Marble kończy delta report.”
- “Nie pisz narracji, daj delta report.”

⸻

closeout

Zostaje: closeout

Znaczenie: końcowe domknięcie runu / etapu.

Nie tłumaczyć jako “zamknięciówka” w docs, choć w rozmowie może być urocze.

Dobre:

- “Zrób closeout po commicie.”
- “Closeout powinien powiedzieć, czy branch jest gotowy do PR.”

⸻

summary

Zostaje: summary albo “podsumowanie”.

Obie formy OK.

⸻

handoff note

Zostaje: handoff note

Znaczenie: notatka przekazująca kontekst nowej sesji/agentowi.

Dobre:

- “Napisz handoff note dla nowej Mikserki.”
- “Handoff note musi zawierać decyzje, nie tylko streszczenie.”

⸻

dispatch file

Zostaje: dispatch file

Znaczenie: plik z master dispatch.

Dobre:

- “Odpal operatora z dispatch file.”
- “Nie wklejaj 200 linii w voice mode, użyj dispatch file.”

⸻

PR body

Zostaje: PR body

Nie tłumaczyć jako “ciało PR”.

Dobre:

- “Przygotuj PR body, ale nie otwieraj PR.”

⸻

8. Git / repo / runtime lokalny

Te terminy zostają verbatim, bo są naturalnym żargonem pracy.

branch

Zostaje: branch

Nie tłumaczyć jako “gałąź” w operacyjnym promptowaniu, chyba że w luźnej rozmowie.

Dobre:

- “Nowy branch z develop.”
- “Branch jest ahead 2.”

⸻

commit

Zostaje: commit

Dobre:

- “Małe regularne commity.”
- “Commit message po angielsku.”

⸻

PR

Zostaje: PR

Nie tłumaczyć jako “żądanie pociągnięcia”.

⸻

push

Zostaje: push

Nie tłumaczyć jako “wypchnięcie” w instrukcjach.

⸻

merge

Zostaje: merge

Nie tłumaczyć jako “scalenie” w komendach/kontraktach, choć opisowo można.

⸻

worktree

Zostaje: worktree

Nie tłumaczyć jako “drzewo pracy”.

⸻

stash

Zostaje: stash

Nie tłumaczyć jako “schowek” w kontekście git.

⸻

dirty worktree

Zostaje: dirty worktree

Znaczenie: repo ma lokalne zmiany.

Dobre:

- “Dirty worktree blokuje bezpieczny start.”
- “Najpierw sprawdź git status.”

⸻

ahead / behind

Zostają: ahead / behind

Dobre:

- “Branch jest ahead 2, behind 0.”
- “Nie force-pushuj i nie pushuj trunka bez operator decision.”

⸻

local-only

Zostaje: local-only

Dobre:

- “Ten branch jest local-only.”
- “Local-only commit wymaga decyzji przed cleanupem.”

⸻

remote

Zostaje: remote

Dobre:

- “Sprawdź, czy to jest na remote.”
- “Remote truth może być inna niż lokalna.”

⸻

9. Dokumenty i reguły systemowe

Living Tree Rule

Zostaje: Living Tree Rule

Można pisać: “reguła Living Tree”.

Znaczenie: repo jest żywe; przed edycją re-read; nie zakładamy, że stan sprzed chwili nadal obowiązuje.

Dobre:

- “Living Tree Rule wymaga re-read przed edycją.”
- “Repo nie jest martwym snapshotem.”

Nie tłumaczyć jako “Reguła Żywego Drzewa” w nazwie własnej.

⸻

Source Files

Zostaje: Source Files

Znaczenie: pliki źródłowe / dokumenty, które model może dostać jako wiedzę.

Dobre:

- “Dodaj notatkę do Source Files.”
- “Nowa Mikserka ma dostać Source Files o VC.”

⸻

README

Zostaje: README

Nie tłumaczyć.

⸻

RUNNER.md / AWAIT.md / SYSTEM.md / GUIDE.md

Nazwy plików zostają verbatim.

Nie tłumaczyć tytułów plików.

⸻

contract

Zostaje: contract albo “kontrakt”.

Obie formy OK.

Dobre:

- “Ten contract musi być egzekwowany przez runtime.”
- “Kontrakt skilla mówi, czy wolno pisać.”

⸻

doctrine

Zostaje: doctrine albo “doktryna”.

Obie formy OK. W VC “doctrine” ma sens jako styl zasad dla agentów.

Dobre:

- “To jest agent doctrine.”
- “Doktryna nie może zastąpić runtime’u.”

⸻

playbook

Zostaje: playbook

Znaczenie: praktyczny zestaw ruchów / procedura.

Nie tłumaczyć jako “księga zagrań”, chyba że żartem.

Dobre:

- “Potrzebujemy playbook dla usera z repo.”
- “To jest operator playbook.”

⸻

runbook

Zostaje: runbook

Znaczenie: instrukcja operacyjna krok po kroku.

Dobre:

- “Runbook ma być dla człowieka, nie tylko dla agentów.”

⸻

10. UI / produkt / adopcja

surface

Zostaje: surface albo “powierzchnia”.

Obie formy OK, ale uważać na nadmiar “powierzchni”, jeśli brzmi jak geologia.

Dobre:

- “Product surface”
- “UI surface”
- “powierzchnia produktu”

Złe:

- “nawierzchnia adopcyjna”
- “powłoka użytkowa”

⸻

product surface

Zostaje: product surface albo “powierzchnia produktu”.

Znaczenie: część produktu widoczna/doświadczana przez usera.

⸻

adoption surface

Zostaje: adoption surface albo opisowo “miejsce, przez które user zaczyna używać produktu”.

Nie tłumaczyć jako “powierzchnia adopcji” w tekście dla ludzi, jeśli brzmi zbyt technicznie.

⸻

onboarding

Zostaje: onboarding

Nie tłumaczyć jako “wdrażanie użytkownika” w żargonie.

Dobre:

- “Hydrate ma poprawić onboarding.”
- “Onboarding nie może zakładać znajomości skillów.”

⸻

polish

Zostaje: polish

Znaczenie: dopieszczenie.

Nie mylić z Polish = polski.

Dobre:

- “To jest polish pass.”
- “Decorate robi visual polish.”

⸻

premium

Zostaje: premium

Dobre:

- “Premium product surface.”
- “To ma wyglądać premium, nie generic.”

⸻

UX

Zostaje: UX

Nie rozwijać na siłę.

⸻

UI

Zostaje: UI

Nie tłumaczyć jako “interfejs użytkownika” za każdym razem.

⸻

11. Modele, sesje, terminal

Claude

Zostaje: Claude

Nie tłumaczyć.

⸻

Codex

Zostaje: Codex

Nie tłumaczyć.

⸻

Gemini

Zostaje: Gemini

Nie tłumaczyć.

⸻

Claude Code

Zostaje: Claude Code

Nie tłumaczyć jako “Kod Klaudii”.

⸻

Zellij

Zostaje: Zellij

Nie tłumaczyć.

⸻

iTerm / iTerm2

Zostaje: iTerm / iTerm2

⸻

terminal

Można pisać: terminal

Naturalne po polsku.

⸻

active session

Zostaje: active session albo “aktywna sesja”.

Dobre:

- “W aktywnej sesji Claude użyj /vc-followup.”
- “Nie odpalaj drugiego launchera.”

⸻

slash command

Zostaje: slash command

Znaczenie: komenda w aktywnej sesji, np. /vc-followup.

Dobre:

- “Claude używa slash command.”
- “W Zellij nie uruchamiaj nowego procesu, tylko użyj slash command.”

⸻

$vc-\*

Zostaje technicznie: $vc-\*

Znaczenie: prefix dla Codex skill invocation, jeśli taki jest aktualny kontrakt.

Dobre:

- “W aktywnej sesji Codex użyj $vc-followup.”
- “To nie jest shell prompt, tylko prefix Codexa.”

⸻

/vc-\*

Zostaje technicznie: /vc-\*

Znaczenie: slash command w aktywnej sesji Claude.

Dobre:

- “W Claude piszesz /vc-followup.”
- “Nie myl /vc-\* z CLI Vibecrafted ....”

⸻

12. Czego absolutnie nie tłumaczyć dosłownie

Poniżej lista zakazanych albo mocno niezalecanych kalek.

marbles

Nie:

- kulki
- marmurki
- kuleczki
- kamyczki konwergencji

Tak:

- marbles
- marbles pass
- marble worker
- bounded marbles run

⸻

hydrate

Nie:

- nawodnić
- hydratyzować
- uwodnić
- hydratacja adopcji

Tak:

- hydrate
- hydrate pass
- uzupełnić docs/onboarding/adoption surfaces

⸻

decorate

Nie:

- udekorować
- dekorowanie produktu
- ozdabianie UI

Tak:

- decorate
- decorate pass
- polish pass
- dopięcie wizualne / coherence pass

⸻

prune

Nie:

- przycinać
- obcinać
- prunkować
- wykarczować

Tak:

- prune
- prune pass
- repo curation
- cleanup martwych elementów

⸻

followup

Nie:

- kontynuacyjka
- następnik
- przegląd następczy
- ponowny przegląd, jeśli to zaciemnia sens

Tak:

- followup
- followup pass
- diagnoza rzeczywistości

⸻

audit

Nie:

- audycik
- kontrola zgodności wszystkiego ze wszystkim
- ogólny code audit, jeśli w VC chodzi o spec

Tak:

- audit
- audit pass
- walidacja względem kontraktu/specyfikacji

⸻

runtime

Nie:

- czas wykonania
- środowisko uruchomieniowe, jeśli chodzi o system VC
- warstwa czasu biegu

Tak:

- runtime
- warstwa runtime
- runtime VC

⸻

control plane

Nie:

- płaszczyzna kontroli, jeśli brzmi nienaturalnie
- tablica dowodzenia

Tak:

- control plane
- warstwa kontroli
- warstwa koordynacji

⸻

dispatch

Nie:

- wysyłka
- dyspozycja
- rozkaz
- depesza

Tak:

- dispatch
- master dispatch
- dispatch file

⸻

handoff

Nie:

- przekazanie pałeczki
- zdawka
- przejęcie zmiany

Tak:

- handoff
- handoff note
- handoff dla nowej sesji

⸻

worker

Nie:

- pracownik
- robotnik
- wykonawca, jeśli mowa o nazwie roli systemowej

Tak:

- worker
- worker agent
- wykonawczy agent, opisowo

⸻

operator button

Nie:

- przycisk operatora, jako główna nazwa
- guzik człowieka

Tak:

- operator button
- decyzja operatora
- twarda granica operatora

⸻

smoke test

Nie:

- test dymny

Tak:

- smoke
- smoke test
- manual smoke
- smoke checklist

⸻

dogfood

Nie:

- karmienie psem
- jedzenie karmy
- psi test

Tak:

- dogfood
- dogfooding
- realny dogfood na repo

⸻

13. Preferowany styl polskich zdań

Najlepszy styl to mieszany polsko-techniczny, naturalny dla operatora AI/dev:

Dobre:

- “Zrób vc-init, potem followup pass i wrzuć mi verdict.”
- “To wygląda na CHECK, nie FIX.”
- “Nie odpalaj marbles bez bounded targetu.”
- “Force-push i push na trunk są operator button.”
- “Worker ma wykonać mały slice i oddać delta report.”
- “Audit ma falsyfikować kontrakt, nie robić ogólnego przeglądu.”
- “Hydrate uzupełnia onboarding i docs, ale nie robi release.”

Złe:

- “Uruchom umiejętność następczą.”
- “Wykonaj nawodnienie powierzchni adopcyjnych.”
- “Przytnij repozytorium według gałęzi dowodów.”
- “Robotnik powinien dostarczyć raport różnicowy po kulce.”
- “Czasowykonanie ma poprowadzić przepływ roboczy.”

⸻

14. Zasada tłumaczenia skillów EN → PL

W tłumaczonych skillach:

1. Nazwy skillów zostają po angielsku.
   - vc-followup
   - vc-audit
   - vc-marbles
   - vc-operator
2. Nagłówki można tłumaczyć, ale nie trzeba tłumaczyć terminów systemowych. EN:
   “When to use this skill” PL:
   “Kiedy używać tego skilla”
3. Opis celu tłumaczymy naturalnie po polsku. EN:
   “Use this skill to inspect whether the current trajectory is healthy.” PL:
   “Użyj tego skilla, gdy chcesz sprawdzić, czy aktualny kierunek pracy jest zdrowy i co powinno wydarzyć się dalej.”
4. Mechaniki zostają verbatim, jeśli są nazwami wewnętrznymi.
   - operator
   - worker
   - runtime
   - dispatch
   - marbles
   - bounded target
   - followup pass
   - audit pass
   - release gate
   - operator button
5. Unikać fałszywej elegancji. Lepiej napisać:
   “Zrób followup pass i oddaj verdict.” Niż:
   “Przeprowadź następczą ocenę i zwróć werdykt operacyjny.”
6. Polski ma być roboczy, operatorowy i jasny. Nie akademicki. Nie marketingowy. Nie korporacyjny. Nie fantasy.

⸻

15. Minimalny zestaw terminów, które prawie zawsze zostają verbatim

- Vibecrafted
- VC
- Vibecrafted
- skill
- operator
- worker
- agent
- runtime
- control plane
- loop
- operator loop
- run
- pass
- flow
- entrypoint
- golden path
- dispatch
- master dispatch
- handoff
- prompt
- skill contract
- contract injection
- boundary
- hard stop
- operator button
- override
- gate
- verification gate
- smoke test
- dogfood
- bounded
- scope
- target
- slice
- lane
- read-only
- write lane
- full autonomy
- convergence
- reception
- evidence
- evidence anchor
- verdict
- finding
- severity
- source of truth
- stale
- dirty worktree
- artifact
- report
- delta report
- closeout
- branch
- commit
- PR
- push
- merge
- worktree
- stash
- Living Tree Rule
- Source Files
- playbook
- runbook
- surface
- onboarding
- polish

⸻

16. Minimalny zestaw terminów, które można tłumaczyć naturalnie

Te terminy można spokojnie tłumaczyć, jeśli zdanie brzmi lepiej:

- purpose → cel
- mission → misja
- goal → cel
- context → kontekst
- current state → aktualny stan
- next move → kolejny ruch
- risks → ryzyka
- assumptions → założenia
- constraints → ograniczenia / constraints
- output → wynik / output
- result → wynik
- report → raport / report
- summary → podsumowanie
- decision → decyzja
- recommendation → rekomendacja
- evidence → dowód / evidence
- issue → problem / issue
- bug → bug
- fix → fix / poprawka
- cleanup → cleanup / sprzątanie
- plan → plan
- checklist → checklist
- manual verification → ręczna weryfikacja
- local commits → lokalne commity

⸻

17. Jednozdaniowa reguła dla tłumacza

Jeśli termin jest nazwą mechaniki VC, roli systemowej, trybu pracy, artefaktu runtime’u albo komendy, zostaw go verbatim
i wyjaśnij po polsku.

Jeśli termin jest zwykłym opisem czynności, tłumacz naturalnie.

Nie walcz o czystość języka kosztem zrozumiałości systemu.

Vibecrafted PL Glossary - decyzje po fali I

0. Decyzje policy

| #   | Sprawa                                       | Decyzja                                                                                                                                                                                                                                                                                 |
| --- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1  | blast radius                                 | Używać zasięg zmiany jako default techniczny. Promień rażenia tylko w miejscach wyraźnie metaforycznych, ostrzegawczych albo żartobliwych. W skillach i kontraktach: zasięg zmiany.                                                                                                     |
| P2  | Bloki ```text z pseudokodem procesu          | Tłumaczyć, jeśli to proza procesowa w przebraniu kodu. Realny bash, json, literalne CLI, nazwy skillów i pipeline typu scaffold → … → release zostają verbatim. Procesowe kroki typu “Detect → Audit → …” można tłumaczyć.                                                              |
| P3  | Przykładowe outputy agenta i cytaty w prozie | Tłumaczyć. To lokalizacja głosu agenta, nie literalny kod. Wyjątek: jeśli cytat jest nazwą artefaktu, komendą, formatem outputu albo literalem testowym.                                                                                                                                |
| P4  | Closing rail / Closing Rail oraz żarty       | W polskich skillach robić cały rail po polsku. Ujednolicić żart jako Suchar:. Jeśli rail zawiera komendy, nazwy pól albo literalne formaty, zostawić je verbatim w środku. Nie mieszać pół raila EN, pół PL.                                                                            |
| P5  | Nazwy-koncepty opisowe                       | Default: tłumaczyć nazwę opisową na PL, przy pierwszym użyciu dać EN w nawiasie, jeśli termin ma znaczenie systemowe. Opaque coinage i nazwy narzędzi zostają EN. Przykład: Atlas Kontekstu (Context Atlas), ale marbles zostaje marbles.                                               |
| P6  | Rodzina “X truth”                            | Ujednolicić jako rodzinę prawda X: prawda repo, prawda runtime’u, prawda strukturalna, prawda produktu, prawda kodu. ground truth tłumaczyć jako twarde fakty, a przy pierwszym użyciu można dodać (ground truth). Nie używać “twardy wymóg runtime’u” jako odpowiednika runtime truth. |
| P7  | ship / shipping                              | W żargonie VC: dowieźć / dowiezienie. Przy formalnym release: wydać / wypuścić. Pipeline release zostaje release. “Shipping surface” raczej “powierzchnia dowiezienia / gotowości do dowiezienia” zależnie od zdania, nie sztywno.                                                      |
| P8  | Pliki już w większości po polsku             | Zostawić PL jako bazę i zachować wtrącenia-coinage EN. Nie tłumaczyć na siłę DoU, measure-core, best-of-n, marbles, runtime itd. Jeśli w źródle PL jest celowo stylizowany, nie wygładzać do korpomowy.                                                                                 |

⸻

1. Rdzeń doktryny - terminy powtarzalne

| Termin                        | Decyzja                                                                                                                                                                                                                                                                                 |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| drift                         | dryf. Potwierdzone.                                                                                                                                                                                                                                                                     |
| blast radius                  | zasięg zmiany jako default. Promień rażenia tylko metaforycznie.                                                                                                                                                                                                                        |
| surface                       | Default: powierzchnia, ale elastycznie. product surface = powierzchnia produktu, operator surface = powierzchnia operatora / warstwa operatora zależnie od zdania, representation surface = powierzchnia reprezentacji. Nie wymuszać “powierzchnia”, jeśli zdanie robi się geologiczne. |
| gate / quality gate           | W tłumaczonych skillach: bramka. W literalnych nazwach, flagach, plikach i utartych konstrukcjach typu release gate można zostawić gate. Default PL: bramka jakości, bramka orientacji, bramka release’u.                                                                               |
| posture                       | postawa. “Postura” tylko jeśli źródło wyraźnie stylizuje żart/metaforę.                                                                                                                                                                                                                 |
| ship / shipping               | dowieźć / dowiezienie w roboczym VC. wydać / wypuścić przy publicznym release.                                                                                                                                                                                                          |
| hook(s)                       | Dwa sensy: git hook i techniczny hook zostają hook. Retoryczne/product hooks: zaczepy. “Find the hooks” = “znajdź zaczepy”.                                                                                                                                                             |
| slice                         | Komenda Loctree i literalne slice zostają verbatim. Jako jednostka pracy: wycinek albo slice. Rekomendacja: w doktrynie VC używać slice, a przy pierwszym użyciu objaśnić “wycinek pracy/produktu”.                                                                                     |
| cut / line of cuts            | cięcie. To centralna metafora i po polsku działa. “line of cuts” = linia cięć. cut zostaje tylko jako literalna etykieta, jeśli występuje w formacie/komendzie.                                                                                                                         |
| scaffold                      | Skill/stage scaffold zostaje verbatim. W prozie: rusztowanie. “dead scaffolding” = martwe rusztowanie. “to scaffold” = zbudować scaffold / naszkicować rusztowanie, zależnie od zdania.                                                                                                 |
| dispatch                      | Jako mechanika VC: dispatch. “Dyspozytura” zostaje jako metafora/objaśnienie, nie jako defaultowy termin. Można pisać: “dispatch, czyli dyspozytura pracy”.                                                                                                                             |
| wave / Wave B                 | fala. “Wave B” = Fala B.                                                                                                                                                                                                                                                                |
| fleet                         | flota.                                                                                                                                                                                                                                                                                  |
| store                         | Uważać. Jeśli chodzi o katalog/registry/listę: katalog. Jeśli chodzi o storage/state store: zostawić store albo magazyn stanu zależnie od kodu. “canonical store” bez kontekstu: główny katalog lub główny store, nie automatycznie “magazyn”.                                          |
| substrate / substrate failure | podłoże / awaria podłoża. Nie “substrat”, bo brzmi laboratoryjnie i sztywno.                                                                                                                                                                                                            |
| twins                         | Jeśli to termin Loctree: twins verbatim z objaśnieniem “bliźniaki”. W luźnej prozie można pisać bliźniaki. Pierwsze użycie: twins (bliźniaki).                                                                                                                                          |
| load-bearing                  | nośny. load-bearing walls = ściany nośne, load-bearing hubs = węzły nośne.                                                                                                                                                                                                              |
| hydrate / hydration           | Skill/stage hydrate zostaje verbatim. Nie używać “nawadniać” jako normalnego czasownika, poza istniejącym triggerem nawowodnij, jeśli jest celowo źródłowy. W prozie: uzupełnić onboarding/docs/adoption surfaces, zrobić hydrate pass.                                                 |
| decorate                      | Skill/stage decorate zostaje verbatim. W prozie preferować: polish, dopięcie wizualne, coherence pass, dopięcie powierzchni produktu. Unikać “udekoruj”, chyba że źródło celowo żartuje.                                                                                                |
| prune / pruning / hard prune  | Skill vc-prune zostaje. W prozie: prune pass, porządkowanie, cięcie martwych elementów. “hard prune” = twardy prune / twarde cięcie, nie “twarde przycięcie” jako default.                                                                                                              |
| polarize                      | Skill vc-polarize zostaje. Czasownikowo: zrobić polarize, a opisowo wyostrzyć / rozstrzygnąć sprzeczne prawdy. Nie forsować “polaryzuj” wszędzie.                                                                                                                                       |
| convergence                   | Default PL: zbieżność. Przy pierwszym użyciu można dać zbieżność (convergence). W nazwach mechanik marbles można zostawić convergence, jeśli jest częścią hasła.                                                                                                                        |
| swarm                         | Default: swarm jako mechanika, z objaśnieniem rój agentów. W prozie może być rój. “research swarm” = research swarm / rój researchowy.                                                                                                                                                  |
| harness                       | Zostawić harness. Nie ma dobrego PL. Można objaśnić jako “uprząż/test harness/warstwa uruchomieniowa”.                                                                                                                                                                                  |
| shape                         | kształt. Potwierdzone. “preserve the shape” = zachować kształt.                                                                                                                                                                                                                         |
| ground truth                  | twarde fakty. Przy pierwszym użyciu: twarde fakty (ground truth). W rodzinie truth zachować wzorzec “prawda X” dla repo/runtime/code/product truth.                                                                                                                                     |

⸻

2. Coinage / neologizmy / nazwy własne

Blok potwierdzony jako verbatim

| Termin                                              | Decyzja                                                                                                                                                                                                                            |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DoU / dou / Definition of Undone                    | Zostaje. Koncept: DoU. Komenda/slug: vc-dou, dou. Rozwinięcie: Definition of Undone. Można objaśnić po polsku, ale nie tłumaczyć jako oficjalna nazwa.                                                                             |
| mylik / myliki                                      | Zostaje. To polski neologizm i jest dobry.                                                                                                                                                                                         |
| marbles                                             | Zostaje. Nigdy “kulki/marmurki” w oficjalnym glosariuszu.                                                                                                                                                                          |
| screenscribe / Screenscribe                         | Ujednolicić do screenscribe jako public/product-facing nazwa, jeśli tłumaczenie nie cytuje literalnego starego wystąpienia. Screenscribe tylko w cytatach/historycznych fragmentach albo jeśli źródło literalne wymaga zachowania. |
| skillaunch / Distiller                              | Zostają verbatim.                                                                                                                                                                                                                  |
| prview / prview-rs                                  | Zostają verbatim.                                                                                                                                                                                                                  |
| scaffold-doctor, DRIVER.md, Vector                  | Zostają verbatim jako nazwy artefaktów/bramek.                                                                                                                                                                                     |
| dou-index, FLIP, BATON, SPANKO, SPRAWDZENIE, Refire | Zostają verbatim. To etykiety systemowe/pętli. Nie normalizować na siłę.                                                                                                                                                           |
| ERi                                                 | Zostaje verbatim. Pierwsze użycie może mieć objaśnienie: examine → research → implement.                                                                                                                                           |
| measure-core, best-of-n / best-offer                | Zostają verbatim. Jeśli “best-offer” okaże się literówką od “best-of-n”, nie poprawiać bez sprawdzenia źródła.                                                                                                                     |

Nazwy opisowe vs opaque coinage

| Kandydat                         | Decyzja                                                                                                                                                                   |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Plague Score / Plague Diagnostic | Traktować jako nazwę własną/coinage. Zostawić Plague Score, Plague Diagnostic. Przy pierwszym użyciu można dodać “wskaźnik plagi” / “diagnostyka plagi” jako objaśnienie. |
| Prism Score / prism bands        | Prism Score jako nazwa zostaje. prism bands można tłumaczyć jako pasma pryzmatu, jeśli to opis, albo zostawić prism bands, jeśli to etykieta formatu.                     |
| Code Smear / conceptual smear    | Tłumaczyć: rozmaz kodu, rozmaz koncepcyjny. Jeśli capitalized jako nazwa sekcji, pierwsze użycie: Rozmaz kodu (Code Smear).                                               |
| Funnel Test / funnel holes       | Tłumaczyć: Test lejka, dziury w lejku. “Lejek” jest standardowy po polsku.                                                                                                |
| Forgotten Gems / gem hunter      | Tłumaczyć: zapomniane perełki, łowca perełek. To dobrze siedzi po polsku.                                                                                                 |
| Silencer Strip                   | Jako nazwa mechaniki: Silencer Strip. Objaśnienie: zdejmowanie wyciszeń / zdejmowanie silencerów. Nie tłumaczyć oficjalnie jako “Pas wyciszaczy”.                         |
| Release Canon / Artifact Canon   | Pierwsze użycie: Kanon release’u (Release Canon), Kanon artefaktów (Artifact Canon). Potem można używać PL.                                                               |
| Stabilization Lenses             | Tłumaczyć: soczewki stabilizacji. Jeśli to tytuł sekcji: Soczewki stabilizacji (Stabilization Lenses).                                                                    |
| Command Deck                     | Zostawić Command Deck jako nazwę powierzchni/artefaktu. Objaśnienie: mostek dowodzenia. Nie zamieniać wszędzie na “mostek”, bo brzmi mocno stylizowanie.                  |
| Living Tree                      | Żywe Drzewo (Living Tree). Przyjęte.                                                                                                                                      |
| Context Atlas                    | Atlas Kontekstu (Context Atlas). Przyjęte.                                                                                                                                |
| Code-Derived Application Map     | Mapa Aplikacji Wyprowadzona z Kodu (Code-Derived Application Map). Przyjęte.                                                                                              |
| Authority labels                 | etykiety autorytetu. Przyjęte.                                                                                                                                            |
| Triad / Trinity                  | Default: triada. Jeśli Trinity jest stylizowaną nazwą własną, pierwsze użycie: Trinity (triada).                                                                          |
| Prime Directive                  | Dyrektywa Naczelna. Aluzja do Star Treka działa po polsku.                                                                                                                |

⸻

3. Idiomy, metafory, gra słów

| Termin / fraza                                        | Decyzja                                                                                                                                                                                   |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| curb appeal                                           | pierwsze wrażenie albo atrakcyjność na wejściu, zależnie od zdania. Nie zostawiać EN.                                                                                                     |
| Swiss-army knife                                      | scyzoryk szwajcarski.                                                                                                                                                                     |
| duct-tape                                             | prowizorka. Potwierdzone.                                                                                                                                                                 |
| Scar Tissue                                           | Utrzymać metaforę. tkanka bliznowata w tytule/kontraście, blizny w kodzie w lżejszej prozie.                                                                                              |
| Done Done                                             | zrobione-zrobione.                                                                                                                                                                        |
| It runs on my machine                                 | u mnie działa. Potwierdzone.                                                                                                                                                              |
| locker-room rule                                      | Jeśli to żart/zasada z puentą sportową: locker-room rule zostawić przy pierwszym użyciu z objaśnieniem zasada z szatni. Jeśli tekst ma być w pełni PL i lekki, można dać zasada z szatni. |
| court / witnesses / prosecution                       | Utrzymać całą metaforę po polsku: sąd / świadkowie / oskarżenie / materiał dowodowy. Nie mieszać w jednym fragmencie pół EN, pół PL.                                                      |
| cache heat / hot loop / cold start                    | rozgrzany cache / gorąca pętla / zimny start. Przy pierwszym użyciu można dodać EN, ale PL jest wystarczająco czytelny.                                                                   |
| poisoned tree                                         | zatrute drzewo. Potwierdzone.                                                                                                                                                             |
| plaster every crack in excess                         | tynkować każdą rysę na zapas. Bardzo dobre, zostawić.                                                                                                                                     |
| convergence cosplay                                   | cosplay zbieżności albo udawanie convergence, zależnie od tonu. W mocniej żartobliwych sekcjach: cosplay zbieżności.                                                                      |
| Polishing theater / confidence theater / test theater | teatr polishu / teatr pewności / teatr testów. Jeśli brzmi za hybrydowo, użyć teatr dopieszczania dla Polishing theater.                                                                  |
| seam / untrusted seam                                 | szew / niezaufany szew.                                                                                                                                                                   |
| ladder / safe ladder                                  | drabina / bezpieczna drabina.                                                                                                                                                             |
| meet strangers                                        | wyjść do obcych. Działa i ma dobry rytm.                                                                                                                                                  |
| AI exhaust                                            | spaliny AI.                                                                                                                                                                               |
| runtime cone                                          | stożek runtime’u.                                                                                                                                                                         |

⸻

4. Slogany / mottos / podtytuły

| Oryginał                                               | Decyzja                                                                                         |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Coherence First. Premium Second.                       | Najpierw spójność. Premium na drugim miejscu. Przyjęte.                                         |
| Detect, Don’t Dictate                                  | Wykrywaj, nie dyktuj. Bliżej oryginału i lepszy rytm niż “nie narzucaj”.                        |
| One brain, many hands.                                 | Jeden mózg, wiele rąk.                                                                          |
| We do not outsource thought.                           | Nie outsourcujemy myślenia.                                                                     |
| Ship It Without Lying                                  | W internal/VC: Dowieź bez ściemy. W public/release docs: Wydaj bez kłamstwa.                    |
| Curation, Not Clear-Cutting                            | Kuratorstwo, nie wycinka. Jeśli brzmi zbyt muzealnie w danym pliku: Porządkowanie, nie wycinka. |
| Strip and Listen                                       | Zdejmij i słuchaj.                                                                              |
| Perception. Intentions. Ground truth. Then… stabilize. | Percepcja. Intencje. Twarde fakty. Wtedy… stabilizuj. Przyjęte.                                 |

⸻

5. Marketing / web / UI

| Termin                              | Decyzja                                                                                                                                            |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| funnel                              | lejek.                                                                                                                                             |
| go-to-market                        | Zostawić go-to-market.                                                                                                                             |
| one-pager                           | Zostawić one-pager. “Jednostronicówka” tylko żartobliwie albo bardzo luźno.                                                                        |
| hero                                | sekcja hero.                                                                                                                                       |
| value prop / value proposition      | Internal: value prop. Bardziej formalnie/public: propozycja wartości.                                                                              |
| CTA                                 | Zostaje CTA.                                                                                                                                       |
| SEO / indexability / crawlable      | SEO zostaje. indexability = indeksowalność. crawlable = crawlable / możliwe do crawlowania albo “dostępne dla crawlerów” w formalniejszym tekście. |
| churn                               | Internal: churn. Proza produktowa: odpływ / rezygnacje.                                                                                            |
| hotspot                             | Zostaje hotspot.                                                                                                                                   |
| smoke / post-release smoke          | smoke, smoke test, post-release smoke. Nigdy “test dymny”.                                                                                         |
| spinner / sparkline / fade-up       | Zostają EN.                                                                                                                                        |
| chrome                              | Uważać na false friend. Default: chrome UI albo obudowa UI. Nie pisać samego “Chrome”, jeśli może brzmieć jak przeglądarka.                        |
| pane                                | Zostaje pane.                                                                                                                                      |
| board                               | Jeśli to UI pattern/nazwa: board. Jeśli zwykła rzecz: tablica.                                                                                     |
| markdown pill                       | Zostawić markdown pill, dopóki komponent nie ma ustalonej polskiej nazwy.                                                                          |
| dashboard / launch card / drilldown | Zostają EN.                                                                                                                                        |

⸻

6. Git / inżynierskie loanwords

Zbiorczo potwierdzone jako EN:

checkout, worktree, hook w sensie git, commit, merge, rebase, deploy, build, runtime, endpoint, fallback, prompt, token,
cache, baseline, no-op, greenfield, headless, lint, linter, dogfooding, split-brain, handoff, recon, vibe coding,
vibe-coded.

Dopuszczalne PL tylko opisowo, jeśli zdanie tego potrzebuje, ale terminy operacyjne zostają EN.

⸻

7. Już po polsku w źródle

| Słowo/fraza                                   | Decyzja                                                                                                                                                                 |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| smaczki                                       | Zostawić PL.                                                                                                                                                            |
| dyspozytura                                   | Zostawić, jeśli jest w źródle jako metafora dispatch. Nie robić z tego defaultowego tłumaczenia dispatch.                                                               |
| kronika                                       | Zostawić PL.                                                                                                                                                            |
| wydmuszka                                     | Zostawić PL. Działa dobrze.                                                                                                                                             |
| bezpiecznik                                   | Zostawić PL.                                                                                                                                                            |
| pancerna latarnia                             | Zostawić PL, jeśli źródło ma taką stylizację.                                                                                                                           |
| dirygentura / conductor                       | Zostawić dirygentura tam, gdzie źródło gra metaforą. conductor można objaśnić jako dyrygent/operator prowadzący.                                                        |
| Ludzki gust / Agentyczna siła / Rzeczywistość | Zostawić PL. To dobre nazwy osi.                                                                                                                                        |
| bez odbioru                                   | Zostawić PL.                                                                                                                                                            |
| się zajebiemy                                 | W internal skill docs można zostawić, jeśli styl źródła jest celowo ostry i roboczy. W public-facing docs złagodzić do rozjedziemy się, polegniemy, wjedziemy w ścianę. |
| nawowodnij                                    | Zostawić jako trigger/źródłowy żart. Nie generalizować na hydrate. Normalnie używać hydrate albo “uzupełnić onboarding/docs”.                                           |

⸻

8. Spójność nazewnictwa

Casing

Nie trzymać ślepo casing per-wystąpienie, jeśli istnieje decyzja produktowa.

Decyzja:

- screenscribe: normalizować do lowercase screenscribe, chyba że cytujesz historyczne/literalne wystąpienie.
- loctree: trzymać casing zgodny z aktualną decyzją repo/produktu. Jeśli brak decyzji, w komendach/slugach lowercase
  loctree, w nazwie produktu można zostawić Loctree tylko jeśli tak występuje oficjalnie.
- DoU: koncept = DoU. Komenda/slug = vc-dou, dou.
- Closing rail: w PL ujednolicić jako Closing rail albo przetłumaczyć nagłówek całego bloku, jeśli cały rail jest po
  polsku. Nie mieszać Closing Rail i Closing rail w obrębie jednego pliku bez powodu.

Suchar / Dad’s joke

W polskich tłumaczeniach ujednolicić na:

Suchar:

Nie zostawiać “Dad’s joke:” w PL, chyba że fragment jest celowo cytowany verbatim.

⸻

9. Dodatkowe reguły jakości tłumaczenia

1. Nie tłumaczyć nazw mechanik, jeśli PL brzmi jak parodyjny onboarding do RPG. Przykład: marbles, hydrate, dispatch,
   runtime, operator button.
1. Nie robić fałszywej czystości językowej. VC po polsku ma brzmieć jak narzędzie operatora AI/dev, nie jak instrukcja
   urzędowa.
1. Jeśli termin jest user-facing i semantycznie mylący, dodać jednozdaniowe objaśnienie. Najważniejszy przykład: audit w
   VC nie oznacza ogólnego code audit, tylko walidację względem kontraktu/specyfikacji.
1. Terminy stage/skill/command zostają verbatim. Czasowniki wokół nich można naturalizować. Dobre: “zrób followup pass”,
   “odpal marbles”, “przejdź do FIX”, “zrób hydrate dla docs”.
1. Metafory tłumaczyć jako całe rodziny, nie punktowo. Jeśli sąd, to sąd/świadkowie/oskarżenie/dowody. Jeśli drzewo, to
   drzewo/zatrute drzewo/Żywe Drzewo. Nie robić hybryd, które gubią obraz.
1. Wątpliwe coinage rozstrzygać tak:
   - jeśli jest capitalized, powtarzalne i działa jak mechanika: zostaw EN + PL objaśnienie,
   - jeśli jest zwykłą metaforą opisową: tłumacz,
   - jeśli jest komendą/artefaktem/labelem: verbatim.

⸻

10. Krótka wersja decyzji dla tłumacza

- skill, runtime, operator, worker, dispatch, marbles, hydrate, decorate, prune, followup, audit, review, release, loop,
  run, pass, flow zostają jako żargon VC.
- gate w tłumaczonych skillach najczęściej jako bramka, ale literalne gate zostaje w nazwach/flagach.
- truth jako rodzina: prawda repo/runtime’u/produktu/kodu, a ground truth = twarde fakty.
- ship = dowieźć, chyba że mowa o formalnym release, wtedy wydać/wypuścić.
- blast radius = zasięg zmiany.
- surface = powierzchnia, ale z elastycznością.
- convergence = zbieżność, z EN przy pierwszym użyciu, jeśli trzeba.
- Opaque coinage zostaje EN.
- Żarty i outputy agentów lokalizujemy na PL.
- Realny kod, CLI, JSON, nazwy plików, pipeline i literały zostają verbatim.

⸻

11. Dopiski po review `vc-marbles` — kwiatki językowe i decyzje praktyczne

Te reguły wynikają z review tłumaczenia `vc-marbles`. Traktować je jako doprecyzowanie glosariusza, nie jako ogólną
krytykę stylu.

Checkpoint

Preferować **checkpoint** tam, gdzie chodzi o obowiązkowy punkt zatrzymania / orientacji przed dalszą pracą.

Dobre:

- “Checkpoint orientacji”
- “Checkpoint startowy”
- “Checkpoint orientacji przed edycją”

Unikać:

- “kanoniczna bramka orientacji” — brzmi za ciężko i sakralnie
- “bramka orientacji” — może zostać, ale checkpoint jest naturalniejszy w operatorowym flow

Najlepszy wariant dla `vc-marbles`:

- “Checkpoint orientacji”

⸻

reception / warstwa odbioru wyników

Nie używać **recepcja**. Brzmi hotelowo i psuje sens mechaniki.

Preferowany termin PL:

- **warstwa odbioru wyników**

Dobre:

- “Warstwa odbioru wyników trzyma pamięć swarmu.”
- “Warstwa odbioru wyników porównuje kandydatów między równoległymi rundami.”
- “Warstwa odbioru wyników decyduje, czy uznać zbieżność, czy odpalić kolejną falę.”

Dopuszczalne skróty:

- “warstwa odbioru”
- “operator / orchestrator” jako dopowiedzenie roli

Złe:

- “warstwa recepcji”
- “recepcja wyników”
- “reception”, jeśli polski tekst ma być czytelny dla zespołu bez czytania EN kontraktu

Przykład docelowy:

- “Warstwa odbioru wyników (operator / orchestrator) trzyma rejestr otwartych findingów, porównuje kandydatów między
  równoległymi rundami i decyduje, czy uznać zbieżność, czy odpalić kolejną falę.”

⸻

Kontekst pracy zamiast “rozgrzanego kontekstu”

Nie używać **rozgrzany kontekst**. Brzmi nienaturalnie po polsku.

Zostawić można:

- “zimny start”
- “gorąca pętla”
- “rozgrzany cache”

Ale sam “kontekst” lepiej opisywać jako:

- **ustalony kontekst pracy**
- **ten sam kontekst pracy**
- **nie zaczyna od zera**
- **mniej czasu traci na archeologię repo**

Dobre:

- “Marbles nie zaczyna od zera. Kolejne runy pracują na już ustalonym kontekście, więc mniej czasu tracą na archeologię
  repo, a więcej na domykanie luk.”
- “Marbles nie przepala kolejnych runów na ponowne odkrywanie repo. Trzyma ten sam kontekst pracy i każe workerom szukać
  tego, co jeszcze pęka.”
- “Krótko: marbles nie zaczyna od zera przy każdym runie. Trzyma ten sam kontekst pracy, tę samą powierzchnię problemu i
  te same bramki, dzięki czemu kolejne workery mniej czasu tracą na archeologię repo, a więcej na brakujące luki,
  fałszywe fixy i kruche założenia.”

Unikać:

- “marbles wydaje rozgrzany kontekst”
- “marbles inwestuje rozgrzany kontekst”
- “marbles zużywa rozgrzany kontekst”
- “kupuje kompletność”

⸻

Żywe narzędzia zamiast “percypuj przez instrumenty”

Nie używać konstrukcji typu:

- “Percypuj przez żywe instrumenty”

Brzmi sztucznie i zaciemnia prosty sens.

Dobre:

- “Zanim dotkniesz kodu, zbierz aktualny obraz repo przez żywe narzędzia.”
- “Zanim dotkniesz kodu, sprawdź repo przez żywe narzędzia.”

⸻

Nadmiarowe fixy zamiast “over-aplikuje”

Nie używać:

- “over-aplikuje”
- “over-aplikacja”

Dobre:

- “swarm celowo przesadza z fixami”
- “swarm aplikuje fixy nadmiarowo”
- “nadmiarowe fixy są częścią mechaniki marbles”

Najbardziej naturalne:

- “swarm celowo przesadza z fixami”

⸻

bounded target / bounded round

Unikać hybryd typu:

- “bounded zbiór celów”
- “bounded runda”

Lepsze warianty:

- **bounded target**
- **bounded round**
- “ograniczony zestaw celów” — jeśli zdanie ma być mniej żargonowe
- “ograniczona runda” — jeśli nie chcemy EN terminu

W `vc-marbles` preferować:

- “jeden bounded target”
- “jedno wywołanie = jedna bounded round”

⸻

Powierzchnia problemu

Unikać:

- “powierzchnia taska”
- “powierzchnia o dużym wpływie”

Dobre:

- “ta sama powierzchnia problemu”
- “ten sam obszar zadania”
- “najmniejsza powierzchnia, której naprawa realnie zmieni wynik”

W `vc-marbles` najlepiej działa:

- “ta sama powierzchnia problemu”
- “Wybierz najmniejszą powierzchnię, której naprawa realnie zmieni wynik.”

⸻

actionable

Nie robić hybryd typu:

- “actionable obsługa”

Dobre:

- “obsługa, po której da się działać”
- “obsługa z konkretną akcją”
- “komunikat z konkretną akcją”

Przykład:

- “zamień połknięte wyjątki na obsługę, po której da się działać”

⸻

Metafora sądowa / oskarżanie drzewa

Metaforę sądową utrzymywać po polsku, ale bez kalkowych dubletów.

Unikać:

- “oskarżenie: oskarżanie drzewa dowodami”

Dobre:

- “oskarżasz drzewo dowodami, nie przeczuciem”
- “Oskarż obecne drzewo.”
- “Bez evidence nie ma celu.”

⸻

Testy najpierw

Unikać:

- “Testy-najpierw zwija pole widzenia”

Dobre:

- “Start od testów zawęża pole widzenia do ‘co pada’, zamiast pokazać ‘co jest kruche’.”
- “Podejście ‘testy najpierw’ zawęża pole widzenia do ‘co pada’.”

⸻

Regresje

Unikać:

- “policz regresję”

Dobre:

- “podaj liczbę regresji”
- “nazwij regresje”
- “podaj liczbę i zakres regresji”

Przykład:

- “Jeśli bramka pada: raportuj wprost, podaj liczbę regresji i nie zagrzebuj ich pod narracją.”

⸻

verdict / audit

Preferować spójny żargon VC.

Dobre:

- “verdict po audicie”
- “po zalewie marbles i verdict po audicie”

Dopuszczalne:

- “werdykt auditu”

Unikać mieszanki:

- “verdykt auditu”

⸻

release candidate

Unikać:

- “ostrość klasy release candidate”

Dobre:

- “klarowność na poziomie release candidate”
- “kształt oparty na jednej prawdzie”

Przykład:

- “by uzyskać klarowność na poziomie release candidate i kształt oparty na jednej prawdzie”

⸻

“niesie detal”

Nie używać:

- “niesie detal protokołu”

Dobre:

- “zawiera szczegóły protokołu”
- “opisuje szczegóły protokołu”

⸻

Klamra końcowa `vc-marbles`

W closing rail unikać konstrukcji:

- “pozwolenie na napisanie małego lub szerokiego prawdziwego fixa”
- “chyba że jest to wyraźnie podane jako opis taska”

Lepszy wariant:

- “tryb marbles to pozwolenie na napisanie małego albo szerzej zakrojonego, ale prawdziwego fixa, nie pozwolenie na
  refactor, chyba że wynika to wprost z opisu taska.”

⸻

Audit jako warstwa falsyfikacji

W `vc-marbles` lepiej mówić, że audit falsyfikuje, a nie tylko percypuje.

Lepszy wariant:

- “Audit siedzi pomiędzy jako READ-ONLY warstwa falsyfikacji.”

Zamiast:

- “Audit siedzi pomiędzy jako READ-ONLY percepcja.”

⸻

12. Terminy i decyzje z realizacji tłumaczenia (Fala 1 + Fala 2)

Skonsolidowane z roboczych plików (Fala1_glosariusz, GLOSARIUSZ_watpliwosci — usunięte po scaleniu). Sekcja 11 pokrywa
review `vc-marbles`; tu reszta. Gdyby coś kolidowało, wygrywa nowsza decyzja (sekcje 11–12).

⸻

12a. Decyzje korpusowe (uzupełnienie sekcji 11)

- Checkpoint strukturalny — odpowiednik „Canonical Structural Gate" (vc-init, vc-loctree). Analogicznie do „Checkpoint
  orientacji" z sekcji 11.
- twins → (duplikaty). Gloss: „twins (duplikaty)”, NIE „bliźniaki”. Literalny scope Loctree (`twins`, `'twins'`,
  `follow twins`) zostaje verbatim.
- „domyślny skill percepcji strukturalnej" → „domyślny skill do mapowania struktury repo" (zdanie o Loctree w
  checkpoincie orientacji).
- Klauzula checkpointu: „…dopóki [prawda repo nie zaistnieje / nie istnieje / nie powstanie / nie pojawi się]" →
  „…dopóki nie ma aktualnej prawdy repo". NIE ruszać innych użyć: „produkuje prawdę repo", „otwiera prawdę
  repo/runtime'u", „mamy świeżą/bieżącą prawdę repo" (tabele FLOW), „znajomości / wyprowadź z / sprawdzenia prawdy
  repo".
- charter → karta (Karta meta-doktryny, karta taktyczna). Default — baza tego nie rozstrzygała.
- Closing Rail → Klamra końcowa; Call to Action → Wezwanie do działania; Non-Goals → Nie-cele (ujednolicone korpusowo).
- „Detect, Don't Dictate" → „Wykrywaj, nie dyktuj".
- „Flow" jako nagłówek/tytuł FLOW.md zostaje EN (mechanika); reszta nagłówków po PL (Trasy, Krawędzie eskalacji,
  Artefakty sesji).

⸻

12b. Nowe terminy — Fala 1 (EN → PL)

- representation surface → powierzchnia reprezentacji
- adoption surface → powierzchnia adopcji / adoption surface (elastycznie)
- discoverability → odkrywalność · indexability → indeksowalność
- evidence anchor → evidence anchor (EN) · evidence grade → stopień dowodu · evidence taxonomy → taksonomia dowodów
- value prop → value prop (formalnie: propozycja wartości) · social proof → social proof (EN)
- command deck → Command Deck (mostek dowodzenia)
- launch card → karta uruchomienia / launch card
- delta report / closeout / handoff note → EN (mechaniki)
- mandate → mandat · shape / shape fidelity → kształt / wierność kształtu
- cut / line of cuts → cięcie / linia cięć · baton → baton (pałeczka)
- swarm / research swarm → swarm (rój) / research swarm
- Code Smear / conceptual smear → rozmaz kodu / rozmaz koncepcyjny
- Plague Score → Plague Score (wskaźnik plagi) · Prism Score → Prism Score (wynik pryzmatu)
- Stabilization Lenses → soczewki stabilizacji · Forgotten Gems / gem hunter → zapomniane perełki / łowca perełek
- Silencer Strip → Silencer Strip (zdejmowanie wyciszeń) · runtime cone → stożek runtime'u · AI exhaust → spaliny AI
- test theater / confidence theater / convergence cosplay → teatr testów / teatr pewności / cosplay zbieżności
- court / witnesses / prosecution → sąd / świadkowie / oskarżenie (cała metafora spójnie)
- seam / untrusted seam → szew / niezaufany szew · ladder → drabina · cold path → zimna ścieżka · meet strangers → wyjść
  do obcych
- Release Canon → Kanon release'u · six planes → sześć płaszczyzn · buyer path → ścieżka kupującego · dataflow
  boundary → granica przepływu danych · verification challenge → wyzwanie weryfikacyjne
- Self-Attack Pass → Self-Attack Pass (przebieg autoataku) · Stage-Aware Verdicts → werdykty świadome etapu · Prime
  Directive → Dyrektywa Naczelna
- holographic → holograficzny · type laundering → pranie typów · trajectory → trajektoria
- cadence → cadence / kadencja · lane → lane (tor) · POI → POI (points of interest)
- exponential backoff → wykładniczy backoff · rate limiting → rate limiting / limit zapytań
- screencast / transcript / screenshot → EN · smell / code smell → code smell (zapaszek) · churn / hotspot → EN (proza:
  odpływ)
- Always-in-Production → Always-in-Production (nazwa stanu) · „Done Done" → zrobione-zrobione

⸻

12c. Nowe terminy — Fala 2 (EN → PL)

- transmission belt → pas transmisyjny · forward plan → plan przyszłościowy · baseline → commit bazowy (baseline)
- failure modality / failure board → modalność awarii / tablica awarii · fleet health → zdrowie floty
- implementation envelope → koperta implementacji · safety net → siatka bezpieczeństwa · control surface → powierzchnia
  kontroli
- stampede → stampeda · liveness → liveness (EN)
- wave shapes → kształty fal · Foundation / Sequential chain / Parallel disjoint → Fundament / Łańcuch sekwencyjny /
  Rozłączna równoległość · final close-out → finalne zamknięcie
- gate clamp → docisk bramki (gate clamp) · cascade effect / agent blindness → efekt kaskady / ślepota agenta ·
  remediation → remediacja
- dual-source truth → prawda z dwóch źródeł · memory spine → kręgosłup pamięci · snapshots → migawki
- field teams → zespoły polowe · takeover → przejęcie / pełne przejęcie · mission diary → dziennik misji
- adjacent postures → postawy sąsiadujące · runtime lane → pas runtime'u · terminal state → stan terminalny · binding
  artifacts → wiążące artefakty
- compat bridge → mostek kompatybilności · pattern scans → skany wzorców
- Iron Law → Żelazne prawo · Red Flags / Stop → Czerwone flagi / Stop · Output Contract → Kontrakt wyjścia
- Plan-shape Style Guide → Przewodnik stylu kształtu planu (plik EMIL.md) · market polish → polish rynkowy ·
  battle-tested → sprawdzony w boju
- graceful shutdown / static hosting / executive debugging → EN (terminy techniczne)

⸻

12d. Review vc-hydrate (decyzje)

- „Kod jest suchy… płynu… bezfrykcyjna" → „Kod działa, ale brakuje mu warstwy, która pozwala dotrzeć do użytkowników…
  możliwie bez tarcia"
- bezfrykcyjna → bez tarcia · agent pakujący → skill od pakowania produktu · Reguła kanoniczna → Zasada nadrzędna
- zewnętrzna/publiczna twarz → zewnętrzna powierzchnia prezentacji / publiczna powierzchnia produktu
- Wejdź przez / uruchom przez command deck → Zacznij od / uruchom z Command Deck
- Załataj luki → Domknij luki · relewantnych → trafnych · runtime-vs-prezentacja → runtime vs warstwa prezentacji
- Scaffolding… / buduje (scaffold) → Scaffold… / tworzy scaffold · „pierwszych 5 minut" → pierwsze 5 minut użytkownika ·
  szybki win → quick win
- Protokół sprintu hydrate → Protokół hydrate sprintu · raport hydrate / per domena → raport po hydrate / według domen
- Integracja z pipelinem → Integracja z pipeline'em · artefakty pakujące → artefakty do dystrybucji i prezentacji
- deploymentem i launchem go-to-market → deployem i go-to-market launchem · preambuł living-tree → preambuła Living Tree
- Hydrate repozytorium → Hydrate repo (pozostałe domeny: „Hydrate X" bez zmian) · „wszystkie sześć" (lista ma 7) →
  „wszystkie te warunki"

⸻

12e. Już po polsku w źródle (zostają PL)

smaczki · dyspozytura · kronika · wydmuszka · bezpiecznik · pancerna latarnia · dirygentura · „bez odbioru" · „się
zajebiemy" (internal; w public-facing złagodzić) · Ludzki gust / Agentyczna siła / Rzeczywistość · nawodnij (trigger) ·
mylik / myliki

⸻

12f. Świadome decyzje / quirki źródła (zachowane 1:1, nie tłumaczeniowe)

- Etykiety szablonów zostają EN — 12-sekcyjny brief (Mission/Context/Gates/Acceptance/Out of scope…) w
  vc-operator/DISPATCH (\_TEMPLATE), vc-init/backlog/HOWTO, vc-scaffold/plans/HOWTO + plan-template; nazwy faz trace'u w
  vc-audit/PHASES (Context Receipt, Adversarial Pass…); pola raportów (Executive Summary, Evidence Index…). To literalne
  pola wyjścia.
- Bank sucharów (vc-operator/DISPATCH) — 7 dowcipów zostało EN (źródło: „port-ready EN dad-jokes"; etykieta „Suchar:"
  ujednolicona).
- EMIL.md — nazwa pliku/dokumentu („Plan-shape Style Guide"), NIE persona; nazwa verbatim, treść PL.
- Stopki niestandardowe — vc-prview / vc-research: „ _Created by Vetcoders (c)2026_" (verbatim); część plików
  (operator/partner/ownership/screenscribe/skillaunch) bez stopki — 1:1.
- Quirki źródła zachowane 1:1: „braad" (vc-marbles), „handsoff" (vc-scaffold), `jp` zamiast `jq`? (vc-skillaunch),
  „dziurawy" fence szablonu w vc-workflow/references/phase-research, podwójny ukośnik `agents//…spawn.sh` w
  phase-implement. Do ewentualnego zgłoszenia autorom oryginału.
- vc-skillaunch — w źródle ślady ogólnego skill-creatora (science bundle, reference papers); przetłumaczone dosłownie.
  Ewentualny rebranding = decyzja redakcyjna, nie tłumaczeniowa.

⸻

12g. Rozwiązane resztki

- vc-screenscribe: „narrowane wideo" → „wideo z narracją" (zastosowane).
- vc-release H1: mantra „Done in the repo" / „done in the world" → „Zrobione w repo" / „zrobione w świecie"
  (zastosowane).
