# vc-workspace — Runbook dla teamu / Team Runbook

> **PL:** Praktyczny przewodnik dla pierwszych użytkowników. Co się spodziewać, jak używać, co kontrolować, gdzie szukać pomocy.
>
> **EN:** Practical guide for first-time users. What to expect, how to use it, what's tunable, where to get help.

---

## Co to jest / What this is

**PL:**
`vc-workspace` to bezpieczna, odizolowana przestrzeń pracy (Linux container) z pełnym
frameworkiem Vetcoders + 11 foundations + 3 agent CLIs (claude/codex/gemini) +
opcjonalnym dostępem przez tailnet do reszty Twojej infrastruktury.

To **NIE** jest JetBrains-style remote IDE (z laggiem, czekaniem na sync, broken
UX gdy network drops). To jest **terminal w prawdziwym sensie** — `zsh` w
containerze, accessed przez SSH lub `docker exec`, czuje się jak lokalna powłoka.

**EN:**
`vc-workspace` is a secure, isolated dev workspace (Linux container) with the full
Vetcoders framework + 11 foundations + 3 agent CLIs (claude/codex/gemini) +
optional tailnet access to your rest-of-infra.

This is **NOT** JetBrains-style remote IDE (laggy, sync-waiting, broken on network
hiccups). This is a **terminal in the actual sense** — `zsh` in the container,
accessed via SSH or `docker exec`, feels like a local shell.

---

## Czego się spodziewać / What to expect

### Latency / opóźnienia

| Scenariusz / Scenario                                        | Oczekiwane / Expected                                                      |
| ------------------------------------------------------------ | -------------------------------------------------------------------------- |
| **Local Docker** (127.0.0.1, container na Twoim laptopie)    | <1 ms — pomiędzy local terminal a containerem to UNIX socket, nie ma sieci |
| **Tailnet mesh** (np. host-a @ tailnet, Ty piszesz z host-d) | 1-5 ms na LAN, 20-50 ms WAN — SSH-grade, bez IDE-protokołów                |
| **JetBrains Gateway / VSCode Remote** (porównanie)           | Zaczyna ~100 ms, wskakuje 200-500 ms przy autocomplete / index sync        |

### Co działa natywnie / What works natively

- `zsh` shell z full color, starship prompt, autocomplete, history (atuin sync między sesjami)
- `vim`, `nvim`, `tmux`, `vc_frame` — pełen TUI bez problemu
- `cargo`, `rustc`, `clippy` — natywna prędkość kompilacji (containerized = no overhead beyond Linux kernel call)
- `aicx`, `loct`, `loctree-mcp` — single-binary execution, ~5ms cold-start
- Agent CLIs (`claude`, `codex`, `gemini`) — same z host, ten sam UX
- Git push/pull przez SSH — działają normalnie z mountowanego `~/.ssh/`

### Co działa inaczej / What works differently

- **GUI apps**: NIE działają w container (no display). Use lokalnego browser/editor + container do CLI work.
- **Audio capture** (np. screenscribe live record): wymaga PulseAudio bridge — domyślnie OFF.
- **GPU/Metal acceleration**: domyślnie OFF (Linux container na Apple Silicon NIE ma Metal). Dla MLX embedder workflows użyj host-side aicx, NIE container.
- **macOS-specific tools** (np. `pbcopy`, `osascript`): NIE działają. Use `xclip` (wymaga X server bridge) lub host clipboard pass-through (advanced).

---

## Pierwszy run / First run

### Krok po kroku / Step by step

```bash
# 1. Wejdź do folderu kontenera (multiroot/vc-workspace)
cd ~/.vibecrafted/vc-runtime/vc-workspace/   # na share: /Volumes/shared-vol/vc-runtime/vc-workspace/

# 2. Odpal wizard
cd wizard
uv pip install -r requirements.txt   # lub: pip install -r requirements.txt
python3 vc-onboard.py                # lub: ./vc-onboard.py

# 3. Wizard przeprowadzi Cię przez:
#    [Welcome] → [Language PL/EN] → [Host: local/tailnet/custom] →
#    [Connection setup] → [Profile: minimal/standard/SoTA] → [Mounts opt-in] →
#    [Tailscale auth key] → [Review] → [Build + Run]

# 4. Po buildzie (3-25 min zależnie od profile) wizard dropuje Cię do zsh w container
```

### Co zobaczysz / What you'll see

Po `Welcome` screen, wizard zadaje pytania w wybranym języku. Defaults są bezpieczne (np. `~/.keys` i `~/.gnupg` domyślnie **WYŁĄCZONE** — musisz świadomie zaznaczyć je w step 6 jeśli chcesz GPG signing in container).

**Pierwszy build = długo** (~15 min Standard profile, ~25 SoTA — kompiluje aicx + loctree-suite od zera). Każdy kolejny build = ~2-3 min (Docker layer cache).

---

## Daily use / Codzienne użycie

### Start / Stop

```bash
# Start (działa w tle)
cd ~/.vibecrafted/vc-runtime/vc-workspace/
docker compose up -d

# Wejdź do containera
docker compose exec dev zsh

# Lub przez SSH (gdy tailnet on)
ssh root@vc-workspace-<twojhostname>

# Stop
docker compose down
```

### Daily workflows

```bash
# Wewnątrz container (po wejściu)
cd /workspace
ll                              # eza alias dla ls -lah
vc-init                         # vibecrafted init claude
aicx all -H 4                   # build canonical corpus z 4h history
loct context --full --markdown  # repo context pack
vc_frame                          # terminal multiplexer
```

### Tailnet access

```bash
# Sprawdź czy container dołączył do tailnetu
tailscale status

# Spodziewane wyjście:
# 100.x.y.z    vc-workspace-<hostname>    -        linux   active

# Z dowolnego mesh peer (host-a, host-d, host-e) możesz teraz:
ssh root@vc-workspace-<hostname>
# albo: ssh root@100.x.y.z
```

> **Jak działa SSH bez sshd:** kontener **nie ma** openssh-server. `entry.sh`
> odpala `tailscale up --ssh`, czyli **Tailscale SSH** — to tailscaled obsługuje
> sesję SSH, gated przez tailnet ACL. Dlatego `ssh root@vc-workspace-host-a`
> wchodzi z host-d/host-b bez żadnego demona SSH w obrazie. Warunek: w panelu
> Tailscale (admin → Access controls) tag `tag:devbox` musi mieć regułę `ssh`
> dopuszczającą `root` (np. `"users": ["autogroup:nonroot", "root"]`).

---

## Zasoby / Resources

### Domyślne / Defaults

| Resource          | Default                              | Tunable?                                                                       |
| ----------------- | ------------------------------------ | ------------------------------------------------------------------------------ |
| **CPU**           | Unlimited (host's all cores)         | ✅ Yes — `cpus: '4.0'` w `docker-compose.yml`                                  |
| **RAM**           | Unlimited (host's all memory)        | ✅ Yes — `mem_limit: '8g'` w compose                                           |
| **Disk (image)**  | Standard ~2.5 GB, SoTA ~4 GB         | ✗ — rebuild changes profile                                                    |
| **Disk (mounts)** | Per-volume (operator-side host disk) | N/A — mount-driven                                                             |
| **Network**       | Bridge + optional tailnet userspace  | ✅ Yes — `--network=host` dla bare-metal                                       |
| **GPU**           | None                                 | ✅ Yes — `--gpus all` jeśli NVIDIA; Apple Metal NIE possible w Linux container |

### Jak ustawić limity / How to set limits

Edytuj `docker-compose.yml` (wygenerowane przez wizard) lub stwórz `docker-compose.override.yml`:

```yaml
services:
  dev:
    cpus: "4.0" # cap przy 4 CPU cores
    mem_limit: "8g" # cap przy 8 GB RAM
    memswap_limit: "8g" # no swap
    pids_limit: 512 # max processes
```

Następnie `docker compose down && docker compose up -d`.

### Monitoring

```bash
# Wewnątrz container
htop                                # CPU + RAM realtime
docker stats vc-workspace             # z host: per-container resources
docker system df                    # disk usage per image/container/volume
```

---

## Common workflows / Typowe użycie

### Z użytkownikiem (non-dev perspective)

Użytkownik nie potrzebuje znać Docker'a żeby z tego korzystać:

1. **Operator raz uruchamia wizard** + ustawia container na host-a (tailnet-accessible)
2. **Użytkownik z `host-d` odpali:**
   ```bash
   ssh root@vc-workspace-host-a
   # ← dropujesz do zsh w container, masz pełen framework
   ```
3. **Standard workflow:**
   - `aicx all -H 8` — pull last 8h conversation history
   - `aicx search "example-app pricing"` — semantic search po wszystkich sessions
   - `vibecrafted decorate claude --prompt "review the landing page"` — agent dispatch
   - `loct context --full` — structural map repo

### Z teamem (dev perspective)

Każdy dev ma własny container na własnym hostie + wszyscy widzą się przez tailnet:

```bash
# Twój local container na host-b
docker compose exec dev zsh

# Możesz ssh do containera kolegi (jeśli tailnet)
ssh root@vc-workspace-emilek

# Lub: shared host (host-a), wszyscy ssh tam
ssh root@vc-workspace-host-a
```

### GPG signing (release-tag etc.)

Jeśli w step 6 wizard'a opt-in'ujesz `~/.gnupg/` mount + ustawisz `LOCTREE_GPG_KEY_ID`:

```bash
# Wewnątrz container
cd /workspace/aicx
make release-tag    # GPG-signed annotated tag
```

---

## Troubleshooting / Rozwiązywanie problemów

### Tailscale nie dołącza

```bash
# Sprawdź log
docker compose exec dev cat /var/log/tailscaled.log

# Manual restart
docker compose exec dev tailscale down
docker compose exec dev tailscale up --authkey=tskey-auth-... --hostname=...
```

### Build wisi / Build hangs

Najczęściej cargo compile aicx llama-cpp-sys (~5 min). Daj spokojnie.

Jeśli >30 min, sprawdź disk:

```bash
docker system df               # czy nie wypełniłeś disk
docker builder prune -af       # cleanup build cache
```

### `aicx --version` w container ≠ na hoście

Container ma własny binary skompilowany z `main` branch przy build time. Host może mieć inny. **Zachowanie zamierzone** — container = stable snapshot, host = working tree.

### Permission denied na mounted files

Mounts są `rw` ale UID może nie pasować. Container runs jako `root` (uid=0); jeśli operator's host pliki są jego user UID (np. 501), w container widzisz `root:root`. Nie problem dla read, write może zmienić ownership na host.

**Fix:** edytuj compose, dodaj `user: "$(id -u):$(id -g)"` (lub stwórz user inside container o pasującym UID).

---

## Cleanup / Sprzątanie

```bash
# Stop + remove container (persistent volumes pozostają)
docker compose down

# Stop + remove container + persistent volumes (DESTRUCTIVE)
docker compose down -v

# Remove image (uwolni ~2.5 GB)
docker rmi vetcoders/vc-workspace:trixie

# Pełen reset — wszystko
docker compose down -v
docker rmi vetcoders/vc-workspace:trixie
docker builder prune -af
rm ~/.vibecrafted/vc-runtime/vc-workspace/docker-compose.yml
rm ~/.vibecrafted/vc-runtime/vc-workspace/.env
```

---

## FAQ

**Q: Czy moje pliki na hoście są bezpieczne?**

PL: Tak — wizard domyślnie NIE mounti `~/.keys`, `~/.gnupg`. Workspace + aicx store są mountowane rw (container może je modyfikować), ale to są Twoje repos — zamierzone.

EN: Yes — wizard does NOT mount `~/.keys`, `~/.gnupg` by default. Workspace + aicx store are mounted rw (container can modify), but those are your repos — intended.

**Q: Czy mogę mieć wiele containerów (np. per-projekt)?**

PL: Tak. Skopiuj `vc-workspace/` do `vc-workspace-project-X/`, edytuj `container_name` i `hostname` w compose, odpal osobno. Każdy container = osobny tailnet node.

EN: Yes. Copy `vc-workspace/` to `vc-workspace-project-X/`, edit `container_name` and `hostname` in compose, run separately. Each container = separate tailnet node.

**Q: Jak update'ować framework?**

PL: Rebuild containera:

```bash
docker compose down
docker compose build --no-cache    # forces fresh git clone aicx + loctree-suite
docker compose up -d
```

EN: Rebuild container:

```bash
docker compose down
docker compose build --no-cache    # forces fresh git clone aicx + loctree-suite
docker compose up -d
```

**Q: Czy to działa offline?**

PL: Po pierwszym buildzie — tak. Container ma wszystko inside. Tailnet wymaga internetu (lub tailnet relays).

EN: After first build — yes. Container has everything inside. Tailnet requires internet (or tailnet relays).

**Q: Co jeśli host host-a padnie?**

PL: Twoje persistent volumes (`~/.aicx`, `~/.vibecrafted`) są na hoście. Jeśli host padnie, dane są jako hostide. Container można rebuild na innym hoście — wystarczy mount te same volumes (rsync z backupu jeśli trzeba).

EN: Your persistent volumes are host-side. If host dies, data lives on host disk. Container can be rebuilt elsewhere — just mount same volumes (rsync from backup if needed).

---

## Kontakt / Contact

- Team: hello@vetcoders.io
- Issues / bugs: w `vetcoders/vc-workspace` repo lub Slack

---

_𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI_
