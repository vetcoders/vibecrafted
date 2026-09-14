# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Runtime Container

Full-stack vibecrafted runtime in a container, joined to your tailnet via the
official Tailscale sidecar. Designed to run on **host-a** (or any tailnet host
with Docker), accessible from any other tailnet device via `tailscale ssh`.

## What is baked in

| Layer       | What                                                                                 |
| ----------- | ------------------------------------------------------------------------------------ |
| Base        | `node:22-bookworm-slim` + uv + python3 + git + zsh + ripgrep + jq                    |
| Foundations | `loctree-mcp`, `aicx-mcp`, `prview`, `vc_frame` (via `install-foundations.sh --all`) |
| Agent CLIs  | `claude`, `codex`, `gemini`, `agy` (npm globals)                                     |
| Toolchain   | `rustup` stable, `clang`, `cmake`, `libclang-dev` (for cargo paths)                  |
| Runtime     | `openssh-server`, `tini`, `sudo`                                                     |
| Network     | None of its own — joins the Tailscale sidecar's network namespace                    |

## Architecture

```text
       ┌──────────────────────────────────────────────┐
       │  host-a (Docker host, on the tailnet)        │
       │                                              │
       │  ┌─────────────────┐  ┌──────────────────┐  │
       │  │  tailscale      │  │  vibecrafted-    │  │
       │  │  sidecar        │◀─┤  runtime         │  │
       │  │  hostname:      │  │  network_mode:   │  │
       │  │  runtime-host-a │  │  service:tailscale  │
       │  │  --ssh enabled  │  │  sshd on :22     │  │
       │  └────────┬────────┘  └──────────────────┘  │
       │           │ tailnet0                        │
       └───────────┼──────────────────────────────────┘
                   │
                   ▼
         ╔════════════════════════════╗
         ║  tailnet (private mesh)    ║
         ║                            ║
         ║  host-b ──► tailscale ssh ──► runtime-host-a
         ║                            ║
         ╚════════════════════════════╝
```

Operator on `host-b` (or any tailnet device) runs:

```bash
tailscale ssh runtime-host-a
# now inside the container — full vibecrafted stack on PATH
vibecrafted doctor
loctree --version
aicx intents -p some-project
```

## One-time setup on host-a

Prerequisites: Docker Engine + Docker Compose v2, Tailscale auth key.

```bash
# 1. Clone vibecrafted on host-a (or use existing checkout)
git clone https://github.com/vetcoders/vibecrafted.git
cd vibecrafted
git checkout release/v2.0.1   # or whatever's current

# 2. Build the base image (one-time, ~5 min)
docker build -t vibecrafted-base:local \
  --build-arg INSTALL_AGENT_CLIS=true \
  --build-arg INSTALL_FOUNDATIONS=true \
  --build-arg INSTALL_RUST=true \
  .

# 3. Configure tailscale auth
cd docker/runtime
cp tailscale.env.example .env
$EDITOR .env   # paste your TS_AUTHKEY

# 4. Build runtime image and bring stack up
docker compose up -d --build

# 5. Check it joined the tailnet
docker compose logs tailscale | tail -20
docker compose ps
tailscale status | grep runtime-host-a
```

## Daily ops

```bash
# Status
docker compose -f docker/runtime/docker-compose.yml ps
docker compose -f docker/runtime/docker-compose.yml logs -f vibecrafted-runtime

# Restart
docker compose -f docker/runtime/docker-compose.yml restart vibecrafted-runtime

# Stop (state survives)
docker compose -f docker/runtime/docker-compose.yml down

# Stop + nuke volumes (skill store, ssh keys, tailscale state — full reset)
docker compose -f docker/runtime/docker-compose.yml down -v

# Rebuild after pulling new vibecrafted
git pull
docker build -t vibecrafted-base:local \
  --build-arg INSTALL_AGENT_CLIS=true \
  --build-arg INSTALL_FOUNDATIONS=true \
  --build-arg INSTALL_RUST=true .
docker compose -f docker/runtime/docker-compose.yml up -d --build
```

## Accessing from host-b (or any tailnet device)

The tailscale sidecar runs with `TS_EXTRA_ARGS=--ssh`, which enables Tailscale
SSH. Authentication uses your tailnet identity — no unix passwords, no manual
key distribution. Tailnet ACLs gate access (configure in the Tailscale admin
console).

```bash
# From host-b:
tailscale ssh runtime-host-a

# One-off command (no interactive shell):
tailscale ssh runtime-host-a vibecrafted doctor
tailscale ssh runtime-host-a loctree slice vibecrafted-core/vibecrafted_core/runtime_paths.py
tailscale ssh runtime-host-a aicx intents -p vibecrafted
```

To restrict who can SSH in, set a tailnet ACL similar to:

```jsonc
{
  "ssh": [
    {
      "action": "accept",
      "src": ["autogroup:member"],
      "dst": ["tag:runtime"],
      "users": ["vibecrafted"],
    },
  ],
}
```

Then tag the sidecar via `TS_EXTRA_ARGS=--ssh --advertise-tags=tag:runtime`.

## Persistent state

| Volume                        | Mount                          | Survives `down`? | Notes                                               |
| ----------------------------- | ------------------------------ | ---------------- | --------------------------------------------------- |
| `vibecrafted-tailscale-state` | `/var/lib/tailscale` (sidecar) | yes              | Re-auth needed only on full reset                   |
| `vibecrafted-home`            | `/workspace/.vibecrafted`      | yes              | Skills, logs, reports, ssh keys                     |
| host bind (optional)          | `/workspace/host`              | always           | Live host files via `${VIBECRAFTED_HOST_WORKSPACE}` |

## Health and verification

```bash
# Inside the container (via tailscale ssh):
vibecrafted doctor       # foundation + skill seed health
loctree --version
aicx --version
which claude codex gemini agy

# From the Docker host:
docker compose -f docker/runtime/docker-compose.yml exec vibecrafted-runtime vibecrafted doctor

# Health endpoint via compose:
docker compose -f docker/runtime/docker-compose.yml ps
# STATUS should show "healthy" after start_period elapses
```

## Bootstrap on a fresh host-a (one-liner)

The `bootstrap-modal.sh` script at `scripts/bootstrap-modal.sh` is the source-of-truth
for the public one-liner installer published at
`https://raw.githubusercontent.com/vetcoders/vc-workspace/main/bootstrap-modal.sh`.

```bash
curl -fsSL https://raw.githubusercontent.com/vetcoders/vc-workspace/main/bootstrap-modal.sh | bash
```

This handles: docker install check → vibecrafted clone → base image build →
tailscale .env wizard (interactive prompt for TS_AUTHKEY) → compose up → smoke
test (tailscale status + vibecrafted doctor).

To publish updates, copy the script to the `vetcoders/vc-workspace` repo:

```bash
cp scripts/bootstrap-modal.sh /path/to/vc-workspace/bootstrap-modal.sh
cd /path/to/vc-workspace
git add bootstrap-modal.sh
git commit -m "Update bootstrap-modal.sh from vibecrafted@$(cd /path/to/vibecrafted && git rev-parse --short HEAD)"
git push
```

## Troubleshooting

| Symptom                                    | Likely cause                                                    | Fix                                                                            |
| ------------------------------------------ | --------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `tailscale` healthcheck never goes ready   | bad / expired TS_AUTHKEY                                        | regenerate in tailscale admin → update `.env` → `up -d`                        |
| `runtime-host-a` not in `tailscale status` | sidecar didn't join                                             | `docker compose logs tailscale` — usually auth / DNS issues                    |
| `tailscale ssh` denied                     | tailnet ACL doesn't permit your identity                        | check Tailscale admin → SSH policy → add your user/tag                         |
| `vibecrafted doctor` fails on foundations  | `install-foundations.sh --all` had partial failure during build | Rebuild base with verbose: `docker build --progress=plain ...`                 |
| `sshd` not starting                        | host keys missing on first boot                                 | entrypoint should auto-generate; check `/workspace/.vibecrafted/logs/sshd.log` |
| `tini`-related zombies                     | entrypoint not using `tini -g`                                  | Verify Dockerfile ENTRYPOINT line is intact                                    |
| Slow rebuild                               | apt-get + cargo + npm not cached                                | Use BuildKit + a registry: tag and push base image once                        |

## Cost / footprint

- Base image: ~600 MB (node:22-bookworm-slim + everything)
- Runtime image: ~900 MB (adds clang, cmake, openssh, tini, build-essential)
- RAM idle: ~150 MB (mostly sshd + tailscale daemon)
- RAM under load: depends on which agent / mcp servers active

## See also

- [`docs/RUNTIME_CONTAINER.md`](../../docs/RUNTIME_CONTAINER.md) — operator runbook
- [`scripts/bootstrap-modal.sh`](../../scripts/bootstrap-modal.sh) — one-liner installer
- [`Dockerfile`](../../Dockerfile) — baseline image
- [`docker/entrypoint.sh`](../entrypoint.sh) — base entrypoint (chained from runtime)
