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

# Task: <krótki tytuł>

Goal:

- <1-3 punkty>

Scope:

- In scope: <pliki/obszary> jako ogólne sugestie
- Out of scope: <jawnie>

Constraints:

- Żadnego `--no-verify` poza zadeklarowanym, autoryzowanym przez Foundera
  lokalnym checkpointem compile-embargo; workerzy nigdy nie pushują z nim
- Trzymaj się konwencji repo

Acceptance:

- [ ] <obiektywny rezultat>
- [ ] <obiektywny rezultat>

Test gate:

- <komenda/komendy>

Context:

- <bardzo krótkie podsumowanie>

Living tree note:

- Pracujesz na żywym drzewie metodyką 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚜𝚖𝚊𝚗𝚜𝚑𝚒𝚙, więc równoległe zmiany są spodziewane.
- Adaptuj się proaktywnie i kontynuuj, ale to nigdy nie jest przyzwolenie na pomijanie bramek jakości, bezpieczeństwa i testów.
- Uruchom wymagane sprawdzenia. Jeśli coś jest zablokowane, zgłoś dokładny bloker i uruchom najbliższy bezpieczny odpowiednik.
- Tryb koordynacji: <solo na tym etapie / równolegle z innymi agentami na tym etapie>
- Nie musisz czytać planów innych agentów, chyba że ten plan wprost tak mówi.
- **Commit to obowiązek, nie opcjonalny checkpoint: JEDEN commit na rundę** (marbles — jedna runda = jeden commit), poprawny wg hooka commit-msg, na bieżącej gałęzi. NIE zostawiaj dostarczonej pracy bez commita. Niedestrukcyjny push bieżącej gałęzi feature (`git push -u origin HEAD`, bez force, bez trunka) jest obowiązkiem po tym commicie. Force-push, push na trunk, merge i deploy pozostają guzikami operatora. Gdy misja obejmuje wiele rund/jednostek, wiele commitów na dispatch jest oczekiwane.
- Jesteś jednostką wykonawczą, nie władzą orkiestracji: nie wywołuj `vc-agents`, nie otwieraj ponownie wyboru frontier i nie reinterpretuj `vc-why-matrix`. **Twoje własne natywne subagenty są wyłączone z tego zakazu**: rozgałęziaj in-process (Task tool / swarm / natywne pod-sesje), gdy plan zawiera rozłączne podcięcia, dobierając tiery wg ekonomiki podzadania (`vc-delegate` → Native Delegation Policy).
- Jeśli misja odsłania szerszą nierozwiązaną powierzchnię, zgłoś tę granicę wprost i zostaw zmiany w orkiestracji operatorowi.
```
