# Brief: konfrontacja intencji Foundera ze stanem repo

## Co dostajesz

`lanes/<TOR>.json` — wycinek katalogu intencji Foundera, wydobytych z korpusu
wskazanego w planie (transkrypcje dyktowanego głosu i/lub aicx).
Każda pozycja ma `quote` — **dosłowny** fragment wypowiedzi, zweryfikowany maszynowo
jako podciąg pliku źródłowego. To nie są parafrazy agenta. To słowa człowieka.

Pola: `id, date, source, quote, topic, kind, subject, strength, _shard`.
`kind`: task / constraint / preference / decision / complaint / question.
`strength` 1–5 — jak mocno wyrażona.

## Twoje zadanie

Dla każdej pozycji orzec **jedną dyspozycję** wobec dzisiejszego stanu repo
`{repo}` — worktree cutu przypięty do baseline planu; ta ścieżka pochodzi z cutu.

**NIE IMPLEMENTUJESZ NICZEGO.** Zero edycji plików repo. Zero commitów.
To jest przegląd stanu; wdrożenie jest osobnym krokiem i należy do kogo innego.
Jedyny plik, który zapisujesz, to swój wynik JSON.

### Dyspozycje

- `landed` — kod to dziś robi. **Wymaga dowodu**: `plik:linia` albo nazwa symbolu.
  Bez dowodu nie wolno użyć tej dyspozycji.
- `partial` — częściowo; napisz w `gap`, czego brakuje.
- `absent` — brak śladu w kodzie. Też wymaga dowodu — jakiego pytania zadałeś
  i co zwróciło zero.
- `superseded` — późniejsza intencja z tego samego toru ją unieważnia.
  Podaj `superseded_by` (id).
- `contradiction` — dwie intencje wykluczają się i żadna nie jest późniejsza
  co do treści. Podaj `conflicts_with` (id).
- `unclear` — nie da się orzec bez pytania do Foundera. Sformułuj to pytanie w `gap`.

### Schemat wyjścia

Zapisz tablicę JSON pod ścieżkę podaną w zadaniu:

```json
[
  {
    "id": "s5-042",
    "disposition": "partial",
    "evidence": "core/quality/overlay_quality.rs:1004",
    "gap": "próg działa przy teach, ale nie filtruje wpisów sprzed 2026-08-21"
  }
]
```

`evidence` obowiązkowe dla `landed`, `partial`, `absent`.
Zachowaj wszystkie `id` ze swojego toru — żadna pozycja nie może wypaść.

## Jak pracować — to jest warunek, nie sugestia

**loctree-mcp jest pierwszym ruchem, nie ostatnim.** Projekt:
`project={repo}`

- `context` — atlas na wejściu, raz
- `find` (mode `symbols` / `where-symbol` / `literal`) — czy symbol/ciąg istnieje
- `slice <plik>` — zależności i konsumenci przed orzeczeniem o pliku
- `impact <plik>` — promień rażenia
- `follow dead` — czy rzecz istnieje, ale jest odcięta od wywołań

Pytanie strukturalne do loctree oszczędza dziesiątki minut grepowania i odpowiada
na to, na co grep odpowiedzieć nie może: _kto to woła, czy to żyje, co pęknie_.
`grep`/`rg` są legalne wyłącznie dla literalnego tekstu (komunikaty błędów,
ścieżki w configach, markdown).

**Uwaga na pułapkę tej roboty:** „symbol istnieje" ≠ „funkcja działa". W tym repo
wielokrotnie zdarzało się, że konsument kontraktu był, a producenta nie było —
struktura obecna, zachowanie martwe. Zanim orzekniesz `landed`, sprawdź `follow dead`
albo znajdź miejsce wywołania. Sonda węższa niż twierdzenie to najczęstszy błąd tej pracy.

## Raport końcowy

Po zapisaniu JSON napisz po polsku 8–12 zdań: rozkład dyspozycji, trzy najstarsze
intencje wciąż `absent` (z datą i cytatem), najpoważniejsza sprzeczność, oraz —
osobno — czego nie dało się orzec i dlaczego.
