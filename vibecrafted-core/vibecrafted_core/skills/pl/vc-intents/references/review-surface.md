# Review surface — the page the user reviews

`intents_cli.py <workdir> render --html` wypełnia `assets/intents.html` ledgerem
i zapisuje `<workdir>/intents.html`. Jeden plik, dane inline, bez serwera,
otwierany z dysku. Strona jest widokiem `LEDGER.json`, nigdy drugim źródłem prawdy.
Wiersze i ich rodzaje to klasyfikacje modelu; sam cytat nie potwierdza autora,
projektu ani uprawnienia do wdrożenia.

## What the page shows

- Każdą intencję: id, date, topic, kind, subject, strength; **dosłowny cytat**;
  szerszy fragment transkryptu (`--transcripts <dir>`, powtarzalne, najpierw
  pobierz pliki drugiego hosta) z wyróżnionym cytatem.
- Werdykty obu hostów obok siebie, dowody jako linki do przypiętego commita
  (`--blob-base https://github.com/<o>/<r>/blob/<sha>/`), `gap` floty.
- Override'y mechaniczne (reguła, powód) i decyzję człowieka, jeśli jest.
- Filtry: status decyzji, efektywna klasyfikacja, zgodność hostów, strength,
  kind, subject, tekst; sortowanie po date, strength, id. Klawisze: ↑/↓, Esc.
- Liczniki w nagłówku według efektywnej klasyfikacji; „przejrzane N/total”.

## Manual review

Dla intencji: **✓ zgadza się** · **↺ zmienia klasyfikację** (wybiera disposition)
· **✕ odrzuca** (fałszywy trop, głos modelu, inny projekt) · komentarz.
Przeglądaj po jednym filtrze: najpierw `landed-contested` (sprawdź ścieżkę runtime
i kontekst źródła), potem `partial` ze strength 5. Przegląd zmienia ledger;
autoryzację wdrożenia daje aktywne zadanie użytkownika lub plan.

## How decisions come back

Plik jest kanonem; strona ma dwa transporty:

1. **Plik lokalny** — „Eksportuj decyzje (JSONL)” pokazuje wiersze; użytkownik
   zapisuje `decisions.jsonl` i uruchamia
   `intents_cli.py <workdir> decide --import-file decisions.jsonl --by manual-review`.
   Nic nie zależy od vendora.
2. **Artefakt claude.ai** — publikowany z `capabilities: {db: {}, downloads:
true, user: {}}`; ta sama strona zapisuje każdą decyzję na żywo do kolekcji
   `decisions/<id>` dla każdego oglądającego. Agent odczytuje je narzędziem danych
   artefaktu i podaje JSON kolekcji do `decide --import-file`. Ta sama komenda
   i te same wiersze override.

W obu wariantach wynikiem jest wiersz w `overrides.jsonl`:

```json
{
  "id": "s2-052",
  "host": "*",
  "from": "landed",
  "to": "absent",
  "verdict": "wrong",
  "by": "manual-review",
  "reason": "double control nie działa na drugiej maszynie",
  "at": "2026-09-19"
}
```

`host: "*"` oznacza nadpisanie werdyktów każdej floty. Wiersz człowieka zawsze
wygrywa z regułą; żaden nie nadpisuje oryginału floty, który strona pokazuje obok decyzji.

## Regenerate, do not edit

Po `decide` ponownie uruchom `render --html`. Ręczna edycja `intents.html` znika
przy renderze i nie zmienia ledgera — temu dokładnie zapobiega warstwa override.
