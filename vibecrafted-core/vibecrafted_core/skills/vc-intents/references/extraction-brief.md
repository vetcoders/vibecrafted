# Brief: ekstrakcja intencji Foundera z korpusu dyktowanego głosu

## Czym są te pliki

Katalog (shard) zawiera transkrypcje **dyktowanego głosu Foundera** z aplikacji
Codescribe lub innego korpusu wskazanego w cucie. Każdy plik to jedna wypowiedź, nagrana i przepisana przez Whisper.
Nazwa pliku ma format `<RRRR-MM-DD>_<HHMMSS>_<pierwsze-slowa>_raw.txt`.

**To NIE są wypowiedzi agenta.** W tym korpusie nie ma ani jednego słowa napisanego
przez model. Wszystko, co tu jest, powiedział człowiek. To czyni ten korpus jedynym
źródłem, w którym intencja Foundera występuje bez pośrednictwa.

## Czego szukam

Intencji dotyczących **produktu wskazanego w cucie (repo `{repo}`) i pracy nad nim**: co ma działać, co jest
zepsute, co go wkurza, czego chce, czego zabrania, co postanowił.

Pomijaj: rozmowy prywatne, dyktowanie treści do wysłania gdzie indziej, myślenie na
głos bez konkluzji, testy mikrofonu ("raz dwa trzy"), wypowiedzi o innych projektach — CHYBA że mówią coś o tym produkcie.

## Zasada nadrzędna: cytat dosłowny

Każda pozycja MUSI mieć `quote` — **dosłowny** fragment z pliku, skopiowany znak w znak.
Nie poprawiaj, nie skracaj, nie parafrazuj, nie wygładzaj. Jeśli Whisper przekręcił słowo
("słoźnika" zamiast "słownika", "uni" zamiast "Junie"), zostaw przekręcone.

Powód: ten katalog ma rozliczać, co Founder naprawdę powiedział. Parafraza agenta
podana jako głos człowieka jest najgorszym możliwym błędem tej roboty — zdejmuje
odpowiedzialność z agenta i przypisuje człowiekowi decyzję, której nie podjął.
Normalizację umieszczaj w `topic`, nigdy w `quote`.

## Schemat wyjścia

Zapisz WYŁĄCZNIE JSON (tablica obiektów) do ścieżki podanej w zadaniu:

```json
[
  {
    "id": "<shard>-001",
    "date": "2026-01-14",
    "source": "<dokładna nazwa pliku>",
    "quote": "<dosłowny fragment, 1-3 zdania>",
    "topic": "<temat, 3-8 słów, po polsku, znormalizowany>",
    "kind": "task|constraint|preference|decision|complaint|question",
    "subject": "<obszar: stt|lexicon|live|overlay|hotkey|agent|settings|build|release|quality-loop|cli|ui|inne>",
    "strength": 3
  }
]
```

- `kind`: `task` = zrób X · `constraint` = nigdy/zawsze Y · `preference` = wolę Z ·
  `decision` = postanowione · `complaint` = to jest zepsute · `question` = pytanie otwarte
- `strength` 1–5: jak mocno wyrażona (1 = mimochodem, 5 = "to jest najważniejsze", złość, powtórzenie)

## Jak pracować

1. Przeczytaj WSZYSTKIE pliki w swoim katalogu. Są krótkie (średnio ~480 znaków).
   Użyj `cat` na wielu naraz, nie po jednym.
2. Nie każdy plik daje intencję. Typowo 20–40% plików to szum. To normalne — nie
   naciągaj, żeby mieć więcej pozycji. Pusta pozycja jest tańsza niż zmyślona.
3. Jeden plik może dać kilka intencji, jeśli Founder poruszył kilka spraw.
4. Jeśli potrzebujesz sprawdzić, czy coś w repo istnieje — **użyj
   loctree-mcp** (`mcp__loctree-mcp__find`, `slice`, `context`) z
   `project={repo}` (ścieżka z cutu), nie grepa.
   Pytania strukturalne do loctree oszczędzają dziesiątki minut szukania.
   Ale to opcjonalne — twoim zadaniem jest korpus, nie kod.

## Raport końcowy

Po zapisaniu JSON napisz 5–10 zdań po polsku: ile plików przeczytałeś, ile intencji
wyciągnąłeś, jakie 3–5 tematów dominuje w twoim okresie, co Cię zaskoczyło, i czy
zauważyłeś zmianę zdania Foundera w obrębie swojego okresu (ktoś mówi X, potem nie-X).
