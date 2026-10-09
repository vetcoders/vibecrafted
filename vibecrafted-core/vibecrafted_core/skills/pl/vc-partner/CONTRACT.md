# Kontrakt `vc-partner`

Kontrakt obecności i odpowiedzialności określa [SKILL.md](SKILL.md).
Ten dokument podaje przykłady jego zastosowania.

| Sytuacja                                                | Oczekiwane zachowanie                                                           |
| ------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Przywrócenie znanej preferencji terminala               | Poznaj stan, zmień inline, sprawdź efektywną wartość, wróć do rozmowy.          |
| Przyjęcie zweryfikowanego batonu bez konfliktów         | Sprawdź baseline i cel, zintegruj w granicach autoryzacji, zweryfikuj cel.      |
| Prośba wymaga szerszego rozpoznania lub długiego buildu | Deleguj, gdy jest to autoryzowane, zachowaj receipt i uzbrój odbiór ukończenia. |
| „Zostań tu ze mną”, gdy worker pracuje                  | Wróć uwagą do rozmowy; autoryzowane zadanie trwa.                               |
| „Zatrzymaj ten build”                                   | Zatrzymaj wskazany build i zachowaj stan pozwalający do niego wrócić.           |
| Temat się zmienia, a worker kończy                      | Odbierz i zweryfikuj wynik; krótko przekaż rezultat lub potrzebną decyzję.      |
| Worker raportuje sukces                                 | Sprawdź rzeczywisty artefakt i stan integracji przed ogłoszeniem dowiezienia.   |
| Wznowienie po compaction                                | Odzyskaj cel, otwarte runy, dowody i następne ruchy z trwałego stanu.           |

Oceniaj łącznie czas, niepewność, wpływ, weryfikację i odzyskanie stanu.
Sama liczba komend nie rozstrzyga o wykonaniu inline lub delegacji.
Dotychczasowa autoryzacja i reguły repo nadal obowiązują.
