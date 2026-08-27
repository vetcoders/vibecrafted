# Compile embargo — fazowo-świadomy kontrakt recovery

Compile embargo może chronić fazę kształtowania architektury przed przeprojektowaniem sterowanym
kompilatorem. Nie jest obejściem hooków Gita, push-banem ani zgodą na pozostawienie jedynego punktu
recovery lokalnie.

## Bramka dopuszczenia

Scaffold może zadeklarować compile embargo tylko wtedy, gdy jawne są wszystkie poniższe elementy:

- decyzja foundera/operatora autoryzująca eksperyment;
- objęte fazy i dokładne bramki compile/lint/test odroczone w każdej fazie;
- asercje albo dowody strukturalne, które tymczasowo zastępują te bramki;
- atestacja kończąca embargo (np. `W2_STRUCTURALLY_CLOSED`), wymagany autor, lokalizacja journala
  i SHA commita;
- repo-owned ścieżka hooka/polityki, która rozumie ten marker.

Bramki commit-message, sekretów, bezpieczeństwa, ref-safety i destrukcyjnych komend nigdy nie są
odraczane. `--no-verify` i równoważne flagi obejścia są zabronione w każdej fazie.

Jeśli repo nie ma policy-aware mechanizmu hooków zdolnego odroczyć wyłącznie nazwane bramki,
scaffold musi dodać taki mechanizm jako osobne cięcie prerequisite albo podzielić pracę na zwykłe
hook-clean commity. Prozatorska obietnica, że hooki „powinny milczeć", nie jest implementacją i nie
dopuszcza embarga.

## Kanał recovery pod embargiem

Każda spójna granica fazy produkuje zwykły, przypisany autorowi commit przez aktywne hooki. Gdy
mandat bieżącej tury autoryzuje zdalną mutację, opublikuj ten commit na dedykowanym nietrunkowym
refie recovery `embargo/<plan-id>`. Repozytoryjny policy-aware pre-push musi zweryfikować:

1. cel to dokładnie zadeklarowany ref embarga, nigdy trunk, release branch ani tag;
2. marker fazy podaje plan ID, fazę, odroczone bramki, stan atestacji i dokładny commit;
3. commit-message, security, secrets, identity i ref-safety pozostają twarde;
4. odroczone są wyłącznie jawnie wymienione bramki compile/lint/test;
5. receipt remote checkpointu i wypchnięty SHA trafiają do journala misji.

To ref recovery, nie kandydat do merge ani drugi control plane. Plan, tracker, journal i
artefakt `.dispatch.toml` pozostają źródłami prawdy wykonania. Nigdy nie wyprowadzaj zgody na push z samego
istnienia embarga: bez autoryzacji bieżącej tury raportuj checkpoint jako local-only, a zdalną
odzyskiwalność jako blocked. Przy autoryzacji blanket push-ban jest defektem niezawodności.

## Zdjęcie embarga

Nazwana atestacja kończy embargo. Przed następnym zwykłym checkpointem feature brancha:

1. uruchom wszystkie odroczone bramki oraz normalny pełny zestaw bramek;
2. zapisz wyniki i atestację dla dokładnego SHA commita;
3. wykonaj następny commit i push przez normalną politykę feature brancha;
4. zachowaj ref embarga jako dowód recovery, dopóki polityka integracji nie pozwoli go posprzątać.

Nieudana odroczona bramka otwiera zadeklarowaną ścieżkę recovery; nigdy nie wskrzesza `--no-verify`.
Merge, tag, release, publikacja i promocja stable pozostają przyciskami `vc-release`.
