# Runtime Container — Operator Runbook

The full 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. runtime, containerized, on the tailnet.

> **Why this exists.** Some agents work better with a long-lived, deterministic
> environment that already has every foundation, every agent CLI, and every
> skill pre-seeded. The runtime container is that environment: one image, one
> compose stack, one `tailscale ssh` away from any of your tailnet devices.

## Quickstart (fresh host-a)

```bash
curl -fsSL https://raw.githubusercontent.com/vetcoders/vc-workspace/main/bootstrap-modal.sh | bash
```

That installer (`scripts/bootstrap-modal.sh` in this repo, mirrored to
`vetcoders/vc-workspace`) walks host-a through: docker check → clone vibecrafted
→ build images → tailscale auth wizard → compose up → smoke test.

Need non-interactive (CI)?

```bash
curl -fsSL https://raw.githubusercontent.com/vetcoders/vc-workspace/main/bootstrap-modal.sh \
  | bash -s -- \
      --branch release/v2.0.1 \
      --ts-authkey tskey-auth-XXXX-YYYY \
      --ts-hostname runtime-host-a \
      --yes
```

## Why not Modal.com / Fly / Render?

The "modal" in `bootstrap-modal.sh` refers to the **install modality** — a
single one-liner installer that handles the whole modal flow (consent, prompt,
config, bring-up). It is **not** Modal.com integration. Hosting target is
**operator-owned host-a** on a **private tailnet** because:

- `aicx` archives are private session memory — never crosses operator-owned
  network boundary
- `loctree` snapshots reveal repo structure and intent — same constraint
- agent CLIs (`claude`, `codex`, `gemini`, `junie`, `grok`, plus `agy` via manual
  install) carry operator auth tokens
- vibecrafted is **Vetcoders'** runtime; the runtime container belongs in
  Vetcoders' infrastructure boundary

If you need a serverless variant later, that's a separate skill — this one is
**self-hosted by design**.

## Topology

| Surface          | Where it lives                     | Reachable from                             |
| ---------------- | ---------------------------------- | ------------------------------------------ |
| Container        | host-a, Docker                     | n/a (tailnet only)                         |
| sshd             | `network_mode: service:tailscale`  | tailnet (port 22 on tailnet0 only)         |
| Tailscale daemon | `vibecrafted-tailscale` sidecar    | tailnet (advertised as `$TS_HOSTNAME`)     |
| Skill store      | `/workspace/.vibecrafted` (volume) | inside container; via bind-mount if needed |
| Host workspace   | `${VIBECRAFTED_HOST_WORKSPACE}`    | bind-mounted to `/workspace/host` (opt-in) |

**Nothing is exposed on the host's public network.** The runtime container has
no network stack of its own — it shares the tailscale sidecar's netns. The
tailscale sidecar in turn binds to `tailnet0` (private mesh interface), not
the public internet.

## Access patterns

### Interactive shell (from host-b)

```bash
tailscale ssh runtime-host-a
# inside:
vibecrafted doctor
loctree slice vibecrafted-core/vibecrafted_core/runtime_paths.py
aicx intents -p vibecrafted
```

### One-shot command (no shell)

```bash
tailscale ssh runtime-host-a vibecrafted doctor
tailscale ssh runtime-host-a -- "cd /workspace/host/some-repo && make test"
```

### File transfer

```bash
# Push artifact to runtime:
tailscale ssh runtime-host-a "cat > /workspace/.vibecrafted/inbox/file.txt" < ./local-file.txt

# Pull from runtime:
tailscale ssh runtime-host-a "cat /workspace/.vibecrafted/reports/latest.md" > ./report.md

# rsync (sshd inside container makes this work transparently):
rsync -av -e "tailscale ssh" runtime-host-a:/workspace/.vibecrafted/reports/ ./reports/
```

### MCP servers from a remote agent

The container ships `loctree-mcp`, `aicx-mcp`, and friends. They speak stdio.
Pattern for a remote agent (e.g. claude on host-b) that wants to use them:

```bash
# Configure ~/.claude.json (on host-b) to spawn the MCP via tailscale ssh:
{
  "mcpServers": {
    "loctree-runtime": {
      "command": "tailscale",
      "args": ["ssh", "runtime-host-a", "loctree-mcp"]
    },
    "aicx-runtime": {
      "command": "tailscale",
      "args": ["ssh", "runtime-host-a", "aicx-mcp"]
    }
  }
}
```

stdio works over ssh because tailscale ssh is a real ssh transport. No HTTP
wrapper needed.

## Day-2 ops

### Updating to a new vibecrafted release

```bash
cd ~/vibecrafted-runtime   # or wherever you cloned
git fetch --prune
git checkout release/v2.0.2   # or whichever

# Re-run bootstrap (idempotent — picks up the new branch)
bash scripts/bootstrap-modal.sh --branch release/v2.0.2 --yes
```

Or manually:

```bash
docker build -t vibecrafted-base:local \
  --build-arg INSTALL_AGENT_CLIS=true \
  --build-arg INSTALL_FOUNDATIONS=true \
  --build-arg INSTALL_RUST=true .
docker compose -f docker/runtime/docker-compose.yml up -d --build
```

### Backup the skill store

```bash
# Volume is `vibecrafted-home` (Docker-managed)
docker run --rm \
  -v vibecrafted-home:/source:ro \
  -v "$(pwd)":/dest \
  alpine tar -czf /dest/vibecrafted-home-$(date +%Y%m%d).tar.gz -C /source .
```

### Rotate tailscale auth

```bash
# Generate new auth key in Tailscale admin
$EDITOR docker/runtime/.env   # update TS_AUTHKEY
docker compose -f docker/runtime/docker-compose.yml up -d --force-recreate tailscale
```

### Full reset

```bash
docker compose -f docker/runtime/docker-compose.yml down -v
# rm -rf ~/vibecrafted-runtime   # if you want a truly clean re-bootstrap
```

## Security boundary

| Threat                         | Mitigation                                                                    |
| ------------------------------ | ----------------------------------------------------------------------------- |
| Public internet probes         | No host port exposure — listens on tailnet0 only via sidecar                  |
| Tailnet device compromise      | Tailscale ACLs gate SSH to the runtime (set `tag:runtime`)                    |
| Container escape               | Standard Docker boundary; runs as unprivileged `vibecrafted` user             |
| Foundation supply-chain        | `install-foundations.sh --check` runs at every build via `--all`              |
| Credential leak in skill store | Volume `vibecrafted-home` lives only on host-a; backup is operator-controlled |
| Stolen `TS_AUTHKEY`            | Use reusable + ephemeral + tag-restricted keys; rotate per quarter            |

## Limitations and known gaps

- **macOS runtime horses** (locterm, iTerm2 plugin) are **not** in this image —
  they're desktop runtimes. The container is headless.
- **`agy` (Antigravity)** is installed but its full IDE surface is desktop-only;
  the CLI works.
- **`junie`** (JetBrains) and **`grok`** (xAI) are included in
  `install-foundations.sh` through their official npm packages:
  `@jetbrains/junie` and `@xai-official/grok`.
- **Microsandbox** is excluded by default (`install-foundations.sh sandbox` is
  optional and needs KVM). Re-enable via custom Dockerfile if host-a has
  `/dev/kvm`.
- **State sync between multiple runtime-\* hosts** is not built in. Each runtime
  container has its own `vibecrafted-home`. Use AICX cross-machine sync (see
  `docs/AICX-SYNC.md`) for memory continuity.

## Composition with other skills

- `vc-init` — works inside the container; pulls atlas + intents using the
  bundled loctree-mcp and aicx-mcp.
- `vc-agents` — agent CLIs are pre-installed; spawn-from-container patterns
  work the same as on a workstation.
- `vc-release` — release runs from within the container as easily as from
  host-a's host shell; the container has `make`, `git`, semgrep helpers.
- `vc-marbles` / `vc-prune` / `vc-followup` — all skills work because the full
  skill store is seeded at boot via `docker/entrypoint.sh`.

## See also

- [`docker/runtime/Dockerfile`](../docker/runtime/Dockerfile)
- [`docker/runtime/docker-compose.yml`](../docker/runtime/docker-compose.yml)
- [`docker/runtime/README.md`](../docker/runtime/README.md)
- [`scripts/bootstrap-modal.sh`](../scripts/bootstrap-modal.sh)
- [`Dockerfile`](../Dockerfile) — baseline image (extended by runtime)
- [`docs/DOCKER.md`](DOCKER.md) — existing Docker reference for the baseline
- [`docs/AICX-SYNC.md`](AICX-SYNC.md) — cross-machine memory sync (relevant for multi-host setups)
