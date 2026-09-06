# Compile embargo — fazowo-świadomy kontrakt recovery

Compile embargo chroni kształtowanie architektury przed przeprojektowaniem sterowanym przez kompilator
i testy. Przy autoryzacji Foundera checkpoint commity mogą użyć `--no-verify` lub równoważnego,
selektywnego obejścia dla odroczonych bramek compile, lint, type-check i testów w dowolnym języku
projektu. Zachowuj spójną pracę w commitach; tymczasowo psujący się build nie jest powodem, by
zostawiać jedyny punkt recovery w brudnym drzewie.

## Bramka dopuszczenia

Scaffold może zadeklarować compile embargo tylko wtedy, gdy jawne są wszystkie poniższe elementy:

- decyzja Foundera autoryzująca eksperyment;
- objęte fazy i dokładne bramki compile/lint/test odroczone w każdej fazie;
- asercje albo dowody strukturalne, które tymczasowo zastępują te bramki;
- atestacja kończąca embargo (np. `W2_STRUCTURALLY_CLOSED`), wymagany autor, lokalizacja journala
  i SHA commita;
- procedura checkpointu: które hooki i bramki są odroczone lub ominięte oraz lokalizacja raportu,
  który zapisuje, co faktycznie uruchomiono, a co pominięto.

Przy lokalnym checkpoincie workera pod zadeklarowanym embargiem `--no-verify` jest w pełni
autoryzowany. Obejmuje całe wejście bundlowanych hooków Gita; nie jest mechanizmem selektywnego
wykonania i nie nakłada na workera prerequisite'u security hooka. Autoryzacja dotyczy wyłącznie
zadeklarowanego zakresu checkpointu, nie claimu, że pominięte bramki przeszły. Zachowaj poprawną
atrybucję commita i autoryzowany zakres refa/operacji. Zapisz odroczone bramki z commitem oraz
zgłoś bramki, które faktycznie uruchomiono lub pominięto.

Użyj selektywnej, policy-aware repo-owned polityki hooków, jeśli jest dostępna. Jej brak nie
blokuje lokalnego checkpointu workera ani nie wymaga najpierw budowania nowego systemu polityk:
autoryzacja fazy wystarcza dla każdego checkpointu w tej fazie. Nie osłabiaj asercji produktu,
żeby uzyskać green.

## Kanał recovery pod embargiem

Embargo ma trzy odrębne stany:

| Stan | Właściciel i dozwolona akcja | Dowód i znaczenie |
| --- | --- | --- |
| Lokalny checkpoint workera | Worker commituję w swoim Fleet Worktree i się zatrzymuje. Bez push, publikacji ani zdalnego refa `embargo/<plan-id>`. | Dokładny SHA, zakres i raport uruchomionych/pominiętych bramek. Wejście bundlowanych hooków może być pominięte; to nie jest security-clean ani verified delivery. |
| Structural admission pod embargiem | Wyznaczony integrator weryfikuje dokładny commit i zakres workera, uruchamia Semgrep oraz przegląd sekretów/bezpieczeństwa i może lokalnie zintegrować baton, aby kolejne fale workerów budowały na spójnej architekturze. | Wyłącznie strukturalnie dopuszczone. Compile, lint, type-check i testy pozostają odroczone do nazwanej closure; integrator nigdy nie nazywa pominiętej bramki bezpieczeństwa clean. |
| Verified delivery | Wyznaczony integrator po nazwanej closure. | Pełne, odpowiednie dla języka bramki odroczone i normalne przechodzą i są zapisane dla dokładnego dopuszczonego SHA. |

Plan, tracker, journal i artefakt `.dispatch.toml` pozostają źródłami prawdy wykonania.
Structural admission jest lokalnym joinem architektonicznym, nie claimem dostarczenia ani drugim control plane.

## Zdjęcie embarga

Nazwana atestacja kończy embargo. Przed zweryfikowanym dostarczeniem:

1. uruchom wszystkie odroczone bramki oraz normalny pełny zestaw bramek;
2. zapisz wyniki i atestację dla dokładnego SHA commita;
3. zleć wyznaczonemu integratorowi zapis verified delivery dla dokładnego dopuszczonego SHA;
4. zachowaj receipty lokalnego checkpointu i structural admission jako dowód recovery, dopóki
   polityka integracji nie pozwoli ich posprzątać.

### Macierz odroczonych bramek dla mieszanego repo

Do nazwanej closure structural admission nie autoryzuje uruchamiania compile, lint, type-check ani
testów tylko po to, by zazielenić embargo. Przy closure uruchom kategorie właściwe dla dopuszczonego
zakresu: Swift — build/type-check i testy; Rust — `cargo check`/Clippy i testy; Python — lint/type-check
i testy; Shell — syntax, formatter/linter i testy skryptów. Dokładne komendy należą do normalnego
kontraktu bramek repo i są zapisane z dopuszczonym SHA.

Nieudana odroczona bramka wymaga naprawy implementacji. Odnowione embargo strukturalne i jego
obejście checkpointu wymagają zapisanej decyzji fazowej; ani nieudany test, ani stary receipt
obejścia nie są dowodem zweryfikowanego dostarczenia.
Merge, tag, release, publikacja i promocja stable pozostają przyciskami `vc-release`.
