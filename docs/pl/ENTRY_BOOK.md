# Vibecrafted Entry book — Linux i Windows

Od czystego systemu do zainstalowanego `vibecrafted doctor` i pierwszej sesji
agenta. Przy każdym kroku znajdziesz źródło, wynik do sprawdzenia i naprawę
najczęstszego błędu. Dalsza praca: [Runbook](RUNBOOK.md). [English](../ENTRY_BOOK.md).

Potrzebujesz Runtime Packa z `.sha256` i `.sig` oraz pasującego checkoutu.
Jeśli nie masz paczki, wykonaj [Build from source](../public/getting-started/build-from-source.md):
Linux L1–L6 zawiera kompilację i podpis własnym kluczem deweloperskim, Windows
W1–W4 — kompilację i lokalny klucz próbny. Sam klon repo nie instaluje runtime'u.
Natywny instalator Windows to `install.ps1` z repo, nie starszy instalator WSL
ze strony. Wybierając pobranie, kieruj się rzeczywistą listą assetów release'u.

## Linux — Ubuntu 24.04 / glibc ≥ 2.39

### L1. Przygotuj system

```bash
sudo apt-get update
sudo apt-get install -y bash ca-certificates curl file git make \
  python3 python3-venv tar zsh openssl
python3 --version
```

**Wynik:** apt kończy z kodem 0, Python ma co najmniej 3.11 (`tomllib`).
**Błąd → naprawa:** Python 3.10 jest za stary. Wybierz obsługiwany interpreter
przed instalacją. Do kompilacji lokalnej dołóż pakiety i toolchainy z kroków
L1–L5 dokumentu Build from source.

Źródło: `.github/workflows/install-linux.yml` → pakiety Debiana / Python 3.11;
`Dockerfile` → lista apt; `scripts/build-linux-runtime-pack.sh` → preflight OpenSSL.

### L2. Pobierz pasujące źródła i uv

```bash
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
curl -LsSf https://astral.sh/uv/install.sh -o uv-install.sh
sh uv-install.sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
```

**Wynik:** checkout i działające `uv`. Wybierz rewizję dostarczoną z paczką.
Dla tego przekazania deweloperskiego wybierz
`git switch port/windows-setup-wizard-fix`, dopóki gałąź istnieje.
**Błąd → naprawa:** rozbieżność rewizji wymaga pasujących źródeł albo nowej
paczki. Wyłączanie weryfikacji nie naprawia pochodzenia artefaktu.

Źródło: `docs/QUICK_START.md` → „1. Install”;
`.github/workflows/install-linux.yml` → Debian „Install uv”;
`scripts/distribution_manifest.py` → pochodzenie źródeł.

### L3. Zainstaluj fundacje

```bash
bash scripts/install-foundations.sh loctree aicx prview screenscribe
export PATH="$HOME/.vibecrafted/tools/node/bin:$HOME/.local/bin:$PATH"
node --version
```

**Wynik:** działające Loctree, AICX, PRView i ScreenScribe. Jeśli brakuje Node/npm,
`ensure_node` pobiera Node 22.16.0. Istniejącego Node akceptuje bez wymiany;
używaj Node 22. Kanały: npm `@loctree/loctree`, npm `@loctree/aicx`, release'y
GitHub `vetcoders/prview-rs`, PyPI `screenscribe` przez uv/pipx.
**Błąd → naprawa:** niedostępny kanał zatrzymuje instalację. Napraw dostęp i
powtórz komendę. Domyślne `REQUIRE_FOUNDATIONS` to **1**.

**Jammy/bookworm:** binarki Loctree i PRView nie uruchamiają się na glibc
2.35/2.36. Udane npm i późniejsze `missing or broken` oznaczają problem ABI.
Przejdź na Ubuntu 24.04/glibc ≥ 2.39 albo jawnie zaakceptuj instalację bez tych
narzędzi:

```bash
REQUIRE_FOUNDATIONS=0 bash scripts/install-foundations.sh loctree aicx prview screenscribe
```

Waiver nie naprawia binarek. Loctree/PRView pozostają niedostępne.

Źródło: `scripts/install-foundations.sh` → `ensure_node`, instalatory czterech
produktów, `foundation_channel_fail`; `.github/workflows/install-linux.yml` →
jawne waivery 22.04 i Debiana, brak waivera na 24.04.

### L4. Zainstaluj Runtime Pack

Podstaw dokładną ścieżkę dostarczonej paczki; oba pliki towarzyszące zostaw obok.

```bash
make install RUNTIME_PACK=/absolute/path/to/Vibecrafted_RuntimePack_linux-x64.tar.gz
```

**Wynik:** weryfikacja sumy, podpisu i pochodzenia, potem publikacja generacji.
`make install` nie kompiluje. `install-source` jest dziś aliasem tego targetu;
kompilację opisuje osobny przepis Build from source.
**Błąd → naprawa:** brak podpisu albo zły klucz wymaga kompletnej, uwierzytelnionej
paczki. Przy własnym buildzie użyj `VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY` z kroku
L6 instrukcji deweloperskiej. Przy świadomym waiverze starej glibc przekaż
`REQUIRE_FOUNDATIONS=0` również do **tej komendy**. Odmowa instalacji podczas
aktywnego runu oznacza: zaczekaj na koniec pracy, nie obchodź strażnika.

Źródło: `Makefile` → `install`, `install-source`;
`scripts/install-runtime-pack.sh` → weryfikacja i publikacja runtime'u.

### L5. Zweryfikuj instalację poza checkoutem

```bash
export PATH="$HOME/.vibecrafted/bin:$HOME/.cargo/bin:$HOME/.local/bin:$HOME/.local/share/vibecrafted/bin:$PATH"
cd /tmp
vibecrafted doctor
vibecrafted version
```

**Wynik:** doctor kończy z kodem 0, bez błędów; version wskazuje zainstalowany
build. **Błąd → naprawa:** `command not found` wymaga PATH z katalogami powyżej.
Ostrzeżenie o niestemplowanym launcherze uruchomionym wewnątrz checkoutu sprawdź
ponownie z tego neutralnego katalogu. Żółta fundacja to brak narzędzia; kod 0
doctora nie zamienia instalacji z waiverem w pełną.

Źródło: `.github/workflows/install-linux.yml` → „Run vibecrafted doctor”.

### L6. Uruchom pierwszą sesję agenta

Wróć do repozytorium, w którym chcesz pracować:

```bash
cd /path/to/your/repo
npm install -g @openai/codex
codex
```

**Wynik:** interaktywna sesja providera, przy pierwszym użyciu z logowaniem.
Zaloguj się w tym CLI; Vibecrafted nie zarządza jego danymi uwierzytelniającymi.
**Błąd → naprawa:** npm zakończone sukcesem, ale brak `codex`, oznacza brak
katalogu globalnych binarek npm w PATH. Napraw instalację CLI przed startem
przez Vibecrafted.

Źródło: `scripts/install-foundations.sh` → `AGENT_PACKAGES` / `install_agents`;
`docs/RUNBOOK.md` → cold start, rozmowa w CLI providera.

### L7. Wejdź do zarządzanego workspace i uruchom orientację

Po zakończeniu poprzedniej sesji, w terminalu:

```bash
vibecrafted start --repo /path/to/your/repo
vibecrafted init codex
```

**Wynik:** nowy workspace repozytorium w vc-frame, potem interaktywna sesja
orientacji. `start` tworzy workspace; nie jest aliasem dashboardu.
**Błąd → naprawa:** kod 3 oznacza istniejący workspace. Wróć do niego świadomie:
`vibecrafted start resume`. Kod 4 oznacza problem z inwentarzem lub tworzeniem;
przeczytaj komunikat i uruchom `vibecrafted doctor`. Okno terminala wymaga hosta
z pulpitem. Na serwerze użyj sesji `codex` w zwykłym terminalu; tworzenie GUI
nie jest odbiorem trybu headless.

Źródło: `scripts/vibecrafted` → `cmd_start_help`, routing init;
`docs/RUNBOOK.md` → cold start.

## Windows — natywnie

### W1. Przygotuj PowerShell i paczkę

Windows x64, **PowerShell 5.1+**, System32 `tar.exe`. Zainstaluj Git for Windows,
żeby pobrać checkout. W PowerShellu:

```powershell
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
```

**Wynik:** obecne `install.ps1` i `scripts\install-runtime-pack.ps1`. Wybierz
rewizję dostarczoną z paczką (bieżące przekazanie deweloperskie:
`port/windows-setup-wizard-fix`). Gotowy pack zawiera Python; instalacja
użytkownika nie wymaga Cargo, Rusta, npm ani uv. Dla lokalnej kompilacji wykonaj
Windows W1–W4 z Build from source.
**Błąd → naprawa:** preflight wylicza brakujące `tar.exe`, pack, sumę, podpis i
zaufany klucz. Uzupełnij listę. Brak paczki nie kończy się pozornym sukcesem.

Źródło: `install.ps1` → `Invoke-Preflight`, `Get-VibecraftedVersion`;
`.github/workflows/install-windows.yml` → `windows-cold-install`.

### W2. Zainstaluj bez WSL

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Pack .\build\<exact-win32-x64-pack>.tar.gz
```

**Wynik:** czytelne podsumowanie; runtime w `%LOCALAPPDATA%\Vibecrafted`, launchery
w jego `bin`, konfiguracja w `%APPDATA%\Vibecrafted`. Instalator aktualizuje PATH
użytkownika albo wypisuje dokładny katalog do dodania.
**Błąd → naprawa:** `.sha256` i `.sig` są obowiązkowe i sprawdzane przed
rozpakowaniem. Lokalny pack z próbnym podpisem wymaga jawnie wybranego własnego
klucza publicznego (Build from source W4). Paczka produktu używa klucza z repo.
Przypadkowy pobrany klucz próbny nie uwierzytelnia release'u.

**MSI/EXE nie mają podpisu Authenticode. SmartScreen ostrzeże.** Zaufanie opiera
się na `.sha256` oraz `.sig` wewnętrznego Runtime Packa i zaufanym kluczu.
Sama suma wrappera nie potwierdza wydawcy. Instalację cichą, doctor i
odinstalowanie MSI/EXE opisuje Build from source W6.

Źródło: `install.ps1` → delegacja i podsumowanie;
`scripts/install-runtime-pack.ps1` → weryfikacja;
`.github/workflows/install-windows.yml` → niepodpisane instalatory.

### W3. Uruchom doctor z zainstalowanego launchera

```powershell
$bin = Join-Path $env:LOCALAPPDATA "Vibecrafted\bin"
$env:Path = "$bin;$env:Path"
& (Join-Path $bin "vibecrafted.cmd") doctor
```

**Wynik:** kod 0 i jawne linie `not supported on Windows`.
**Błąd → naprawa:** brak launchera oznacza niedokończoną publikację generacji;
napraw błąd wskazany przez instalator. Natywne `start`, `dashboard`, `init` i
pozostałe komendy decka POSIX kończą z kodem 2 i wskazują WSL2.
`voc`/`vc-o`, `vc-admin`, `vc-procs`, `vc-start`, rescue/flock i shelle PTY/zsh
nie należą do natywnej powierzchni produktu.

Źródło: `.github/workflows/install-windows.yml` → cold doctor;
`vibecrafted-core/vibecrafted_core/cli.py` → odmowa lifecycle na win32.

### W4. Uruchom pierwszą natywną sesję agenta

Zainstaluj **Node 22** z jego kanału i dodaj npm do PATH. W PowerShellu, w swoim
repozytorium:

```powershell
npm install -g @openai/codex
codex
```

**Wynik:** sesja providera i jego logowanie. To sesja agenta obok zainstalowanego
runtime'u Vibecrafted. Zarządzany workspace POSIX nie ma dziś działającej ścieżki
natywnej; do niego użyj WSL2 poniżej.
**Błąd → naprawa:** brak npm wymaga naprawy instalacji Node/PATH. Logowanie należy
do CLI providera. Natywny pack nie instaluje fundacji przez skrypt POSIX.

Źródło: `scripts/install-foundations.sh` → `AGENT_PACKAGES`;
`Dockerfile` → Node 22; `docs/RUNBOOK.md` → rozmowa w CLI providera;
core CLI → granica Windows.

### W5. WSL2 dla pełnej zarządzanej sesji

W PowerShellu z uprawnieniami administratora:

```powershell
wsl --install
wsl --status
```

**Wynik:** zainstalowana dystrybucja; uruchom ponownie system, jeśli instalator
tego wymaga, i sprawdź status. Otwórz tę dystrybucję i wykonaj Linux L1–L7.
Wybierz Ubuntu 24.04; granica glibc obowiązuje również w WSL. Natywne CLI samo
nie uruchamia WSL.
**Błąd → naprawa:** stara dystrybucja ma ten sam problem ABI co starszy Linux.
Wybierz nowszy baseline albo świadomy waiver z opisanym brakiem narzędzi.

Źródło: `docs/INSTALL.md` → „POSIX alternative — WSL2”;
`install.sh` → detekcja WSL; workflow Linux → waivery fundacji.
