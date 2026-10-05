# vc-agents — szablon planu

```markdown
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|agy|junie|grok|cursor|kimi|copilot>
skill: vc-agents
project: <repo-name>
status: <pending|in-progress|completed|failed>
loops_completed: <number>
---

# Zadanie: <krótki tytuł>

Cel:

- <1–3 punkty>

Zakres:

- W zakresie: <pliki/obszary> jako wskazówki wysokiego poziomu
- Poza zakresem: <jawne wyłączenia>

Ograniczenia:

- Bez `--no-verify` poza zadeklarowanym, autoryzowanym przez Foundera
  lokalnym checkpointem compile-embargo; workerzy nigdy nie pushują z tym obejściem
- Stosuj konwencje repo

Akceptacja:

- [ ] <obiektywny wynik>
- [ ] <obiektywny wynik>

Bramka testowa:

- <komendy>

Kontekst:

- <bardzo krótki opis>

Uwaga o Living Tree:

- Pracujesz w żywym drzewie metodą 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙; równoległe zmiany są oczekiwane.
- Dostosowuj się i kontynuuj. To nie jest zgoda na pomijanie bramek jakości, bezpieczeństwa ani testów.
- Uruchom wymagane kontrole. Przy blokadzie podaj dokładną przyczynę i wykonaj najbliższy bezpieczny odpowiednik.
- Tryb koordynacji: <solo na tym etapie / równolegle z innymi agentami>
- Nie musisz czytać planów innych agentów, chyba że ten plan tego wymaga.
- **Commit jest obowiązkiem: JEDEN commit na rundę** (marbles: jedna runda = jeden commit), poprawny według hooka commit-msg, na bieżącej gałęzi. Nie zostawiaj dostarczonej pracy bez commitu. Niedestrukcyjny push bieżącego brancha feature (`git push -u origin HEAD`, bez force i bez trunk) następuje po commicie zgodnie z autoryzacją bieżącej misji. Force-push, trunk, merge i deploy pozostają guzikami Foundera. Misja obejmująca wiele rund/jednostek może wymagać wielu commitów.
- Jesteś jednostką wykonawczą, nie władzą orkiestrującą: nie wywołuj `vc-agents`, nie otwieraj ponownie wyboru frontier i nie reinterpretuj `vc-why-matrix`. **Własne natywne subagenty są wyjątkiem**: rozdzielaj pracę wewnątrz procesu (Task / swarm / natywne podsesje), gdy plan zawiera rozłączne zadania; dobieraj tier według ekonomii podzadania (`vc-delegate` → Native Delegation Policy).
- Jeśli misja ujawni szerszy nierozstrzygnięty obszar, opisz granicę w raporcie i pozostaw zmiany orkiestracji Operatorowi.
```
