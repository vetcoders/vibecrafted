# Podłoga powierzchni publicznej

Potok wydania musi umieć odpowiedzieć, dla każdego produktu, który wydaje:

> Czy każda publiczna powierzchnia mówi tę samą wersję i czy obcy może dziś zainstalować tę rzecz z tego, co publikujemy?

`scripts/version_bump.py --check` porównuje tylko pliki w checkoutcie.
`vibecrafted doctor --release` porównuje tylko `VERSION` Vibecrafted z najnowszym GitHub Release i z bramką źródła Release. Żadne z nich nie patrzy na npm, crates.io, PyPI, tapy Homebrew, kanał instalatora, metadane strony, sekcję instalacji w README, pisownię marki ani na tag wypchnięty bez GitHub Release. Tę lukę zamyka ta podłoga. To jedna sonda z trzema właścicielami etapów, konfigurowana per produkt. Nie taguje, nie publikuje i nie deployuje.

```bash
vc-dou --floor                         # ten checkout, produkt vibecrafted
vc-dou --floor --product loctree       # żywe powierzchnie innego produktu
vc-hydrate --floor                     # tylko checkout; bez sieci
vc-release --floor                     # preflight wydania
vc-release --floor --require-release   # obiekt Release musi istnieć
```

Ten sam punkt wejścia to `python -m vibecrafted_core.public_surface {dou|hydrate|release}`.
Konfiguracja w checkoutcie źródłowym to `packaging/public-surfaces/<product>.toml`, albo `--config PATH`.
Zainstalowany wheel niesie te same pliki w `vibecrafted_core/public_surfaces` i używa tej kopii, gdy bieżące repozytorium nie ma własnego katalogu.

Kod `0` to zaliczenie. Ostrzeżenia i tak są drukowane i nie są cichą zielenią: nieudowodniony rejestr mówi `unproven, not a green pass`. Kod `1` znaczy, że co najmniej jedno sprawdzenie ma na tym etapie poziom `fail`. Kod `2` to zła konfiguracja. `--acknowledge <surface-id>` obniża fail tej powierzchni do ostrzeżenia i zostawia go w raporcie; wezwanie do instalacji zostaje zablokowane. Gdy `expect = "agree"`, każda rozjeżdżająca się powierzchnia zachowuje własną surowość i własne id, a linia `consensus` jest podsumowaniem. Potwierdzenie każdej powierzchni z fail obniża też to podsumowanie. Powierzchnia tylko z ostrzeżeniem, na przykład tap Homebrew albo tekst strony Pensieve na etapie release, sama nie oblewa etapu. 404 z `/releases/latest` podczas `vc-release` jest ostrzeżeniem tylko wtedy, gdy `--allow-missing-release` nazywa wycinaną wersję i nie ma `--require-release`. Każdy inny błąd pobrania oraz brak Release przy `--require-release` zostaje porażką.

`install_cta: yes` jest dozwolone tylko wtedy, gdy README (gdy ten checkout jest produktem) ma polecenie instalacji i każda powierzchnia oznaczona `install` się zgadza. W przeciwnym razie raport mówi `install_cta: no`. Nie mów nikomu, żeby instalował, dopóki ta linia mówi nie.

## Kto co sprawdza

| Sprawdzenie                                                               | vc-dou                                       | vc-hydrate  | vc-release                                                                                                         |
| ------------------------------------------------------------------------- | -------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| Żywe rejestry, kanał, wersja strony, GitHub Release, Homebrew             | porównanie, surowość `dou` każdej powierzchni | niepobierane | porównanie, surowość `release` każdej powierzchni                                                                  |
| Tag bez GitHub Release                                                    | `fail`, chyba że produkt mówi `warn`         | —           | domyślnie `warn`. Wydawca tworzy Release dla wycinanej wersji; starsze osierocone tagi zostają widoczne             |
| GitHub Release nie jest jeszcze wersją referencyjną                       | `fail`                                       | —           | `warn`, ze zdaniem, że sam tag nie kończy etapu. `--require-release` zamienia to w `fail`                           |
| Projekcje w repo (`version_bump`, pyproject, plugin, badge README)        | `fail`, gdy ten checkout jest produktem      | `fail`      | `fail`, gdy ten checkout jest produktem                                                                             |
| Sekcja instalacji README i piny poleceń                                   | `fail`                                       | `fail`      | `fail`                                                                                                             |
| Proza README, która nazywa inną wersję                                    | `warn`                                       | `warn`      | `warn`                                                                                                             |
| Pisownia marki w checkoutcie (wielkie C wewnątrz Vetcoders)               | `fail` na skonfigurowanych globach           | `fail`      | `fail`                                                                                                             |
| Pisownia marki na żywej stronie (pobrany HTML, nie każdy bundle skryptów) | `warn`                                       | niepobierane | `warn`                                                                                                             |
| Rejestr, do którego nie da się dojść                                      | `warn` (nieudowodnione)                      | —           | `fail`, gdy surowość release tej powierzchni to `fail`                                                             |

Plik produktu nadpisuje te surowości przez `dou = "fail"|"warn"|"ignore"` i `release = "..."`. `install = true` znaczy, że obcy może naprawdę zainstalować z tej powierzchni, więc rozjazd blokuje `install_cta` nawet gdy surowość to tylko ostrzeżenie (tapy Homebrew są zwykłym przypadkiem).

`local_root_file` decyduje, czy checkout w `--repo` należy do tego produktu. Audyt Loctree z drzewa Vibecrafted nie traktuje README Vibecrafted jak README Loctree.

## Expect

- `file:VERSION` — porównaj świat z tym checkoutem. Dobre dla produktu, którego repo jest tym, co wycinamy.
- `agree` — nie ma checkoutu albo checkout nie jest wydawcą, którego widzi obcy. Każda żywa wersja musi być tą samą liczbą. Dobre dla Loctree, AICX, Pensieve, Screenscribe, gdy nie stoisz w ich drzewach.
- `literal:1.2.3` — stała referencja.
- `surface:<id>` — jedna żywa powierzchnia jest referencją, a pozostałe są porównywane do niej.

## Dodawanie produktu

Skopiuj `packaging/public-surfaces/screenscribe.toml`. Wymień tylko rejestry, które ten produkt naprawdę publikuje. AICX nie może dostać powierzchni npm ani PyPI: te nazwy należą do innych ludzi. Ustaw `install = true` na powierzchniach, z których obcy ma instalować. Kanoniczna pisownia marki to `Vetcoders`. Zakazana forma to to samo słowo z wielkim C; wpisz tę dokładną pisownię w `brand.forbidden` i w powierzchniach `brand_html`.

## Czego czerwona podłoga nie robi

Nie podbija `VERSION`, nie tnie tagu, nie otwiera GitHub Release i nie deployuje strony. To zostają przyciskami operatora. `scripts/publish-vibecrafted-release.sh` uruchamia `vc-release --floor` i staje, gdy podłoga kończy się kodem `1`, zanim utworzy szkic. Brak Release dla publikowanego tagu jest w tym momencie dozwolony (`--allow-missing-release`); wszystko inne nie. Skrypt i `make publish-release` przyjmują `--acknowledge <surface-id>` (Make: `ACKNOWLEDGE="<surface-id>"`) i odrzucają każdy inny argument podłogi, więc wywołujący nie może przestawić `--repo` ani `--product`. `install_cta: no` mówi, żeby nie wysyłać obcego do instalacji. Samo to nie zabrania publikacji Release, które czyści tę linię, gdy podłoga już zakończyła się kodem 0.
