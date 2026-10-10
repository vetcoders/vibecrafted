# Runtime `vc-partner`

Skill interaktywny przyjmuje postawę w bieżącej sesji. Launcher otwiera
interaktywną sesję TTY z rodziny init:

```bash
vibecrafted partner <agent>
vibecrafted partner <agent> --runtime plain
```

`--prompt` i `--file` dostarczają kontekst początkowy. Launcher odmawia wywołania
headless; nie jest komendą dispatchu workera. Aktualną semantykę launcherów
opisuje [Matryca Delegacji](../DELEGATION_MATRIX.md).

Do autoryzowanej pracy zewnętrznej dobierz ścieżkę frameworka:
`implement` dla ograniczonej implementacji, `workflow` dla Examine → Research →
Implement lub plan dispatchu dla wielu zależnych cięć. Zachowaj wybrany runtime
i piny modeli.

Zachowaj rzeczywisty receipt startu i uzbrój odbiór ukończenia przez dostępne
powiadomienia harnessu lub await frameworka w tle. Udostępnij transkrypty,
raporty i ścieżki odzyskania stanu. Samo wypisanie komendy await nie uzbraja
monitora. Proces workera należy do jego runtime'u, a nie do widoku Frame.

Po powiadomieniu przeczytaj końcowy receipt, sprawdź rezultat i przyjmij lub
odrzuć integrację w uzgodnionym zakresie. Konkretnie pokaż pozostałe decyzje
użytkownika lub blokady. Istotne decyzje zapisuj w istniejącym dzienniku repo;
artefakty launchera nie tworzą kolejnego kanonicznego dziennika.
