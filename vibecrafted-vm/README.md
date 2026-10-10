# Linux arm64 Runtime Pack carrier

This directory owns the hardened Runtime Pack carrier image. It is **not** the
Agents launcher's `local-vm` environment: that selector runs the persistent
per-project dev container described below (a container, not a VM), and does
not create per-run carrier containers.

The image has one input: the checksum-pinned Runtime Pack produced by the
repository's canonical `package-runtime-pack.sh` contract. It does not build
from sibling repositories, install mutable releases, mount operator state, or
provide a success stub for a missing tool.

## Build the Runtime Pack

From a clean clone at the release commit:

```bash
sha="$(git rev-parse HEAD)"
stage="$(mktemp -d "${TMPDIR:-/tmp}/vibecrafted-linux-arm64.XXXXXX")"
python3 scripts/distribution_manifest.py archive \
  --source "$PWD" --output "$stage/source.tar.gz" --root-name vibecrafted
tar -xzf "$stage/source.tar.gz" -C "$stage"
docker buildx build --platform linux/arm64 \
  -f "$stage/vibecrafted/vibecrafted-vm/RuntimePack.Containerfile" \
  --build-arg VIBECRAFTED_SOURCE_REVISION="$sha" \
  --output type=local,dest=build/linux-arm64-runtime-pack \
  "$stage/vibecrafted"
```

The manifest-owned public distribution stage supplies the closed
`source-provenance.json` carrier and excludes development/secret surfaces. The
builder then downloads only public immutable source archives, verifies their
SHA-256 digests, builds the native binaries, records executable versions and
provenance in `runtime-inventory.json`, and invokes the canonical Runtime Pack
packager.

## Build the exact image

```bash
pack=build/linux-arm64-runtime-pack/Vibecrafted_RuntimePack_linux-arm64.tar.gz
pack_sha="$(sha256sum "$pack" | awk '{print $1}')"
manifest_sha="$(tar -xOzf "$pack" VibecraftedRuntime/runtime-pack-provenance.json | sha256sum | awk '{print $1}')"
sha="$(git rev-parse HEAD)"
docker build --platform linux/arm64 -f vibecrafted-vm/Containerfile \
  --build-arg RUNTIME_PACK_ARCHIVE="$pack" \
  --build-arg RUNTIME_PACK_CARRIER_BASENAME="$(basename "$pack")" \
  --build-arg RUNTIME_PACK_SHA256="$pack_sha" \
  --build-arg RUNTIME_PACK_MANIFEST_SHA256="$manifest_sha" \
  --build-arg VIBECRAFTED_SOURCE_REVISION="$sha" \
  -t vibecrafted-local-vm:"${sha:0:12}" .
```

The default process is UID/GID 10001. No `VOLUME` is declared and no provider
credential, session, home, key, XDG, or repository material is baked in.
Provider CLIs are installed at exact versions recorded in
`runtime-provider-lock.json`; no paid provider call is part of the carrier
proof.

## Personal-dev compose and wizard

`compose.yaml` and `wizard/` are retained only as an operator-controlled
personal development convenience. They require an explicitly supplied
`VC_PERSONAL_DEV_IMAGE` and may mount broad host state. They are **not** a
security boundary, do not build this carrier, and are not the Workshop
selector backend.

## Persistent, per-repo dev container (Agents "local container")

The recipe — `Dockerfile.dev`, `compose.dev.yaml`, `dev-entry.sh`, `entry.sh`,
`zshrc.template` — lives in
`vibecrafted-core/vibecrafted_core/runtime/dev-container/` so it ships inside
every installed generation. It is a batteries-included, **non-ephemeral,
per-repo** container that preserves **session-history continuity across
agents**: Debian 13 + uv + Node + Rust, the agent CLIs **claude, codex, gemini
and kimi**, and the AICX/Loctree foundations. The repo is mounted live at
`/workspace`; every agent's session state (`~/.claude`, `~/.codex`,
`~/.gemini`, `~/.kimi-code`) and the AICX corpus (`~/.aicx`) live in
per-project named volumes, so rebuilding never loses history.

The Agents launcher drives it through `vibecrafted_core.dev_container`
(runtime-policy key `local-vm`, shown as `local-container`): the image is
`vibecrafted-dev:recipe-<digest>` labelled with the recipe digest, built only
when the recipe changes, then `docker compose -p vc-<repo-slug> up -d
--no-build` and `docker exec -it -w /workspace` into the selected agent. It
never runs `down -v`, never mounts the host `HOME`, and passes provider keys by
name only. The Docker VM must share the project path (Colima shares only
`$HOME` by default); the launcher refuses an unshared path with the exact
`colima start --mount` instruction instead of mounting an empty `/workspace`.

```bash
# manual entry for the current repo (same project name and volumes)
vibecrafted-vm/dev-up.sh                 # or: dev-up.sh /path/to/repo

recipe=vibecrafted-core/vibecrafted_core/runtime/dev-container
docker compose -p vc-<repo-slug> -f "$recipe/compose.dev.yaml" exec dev zsh
docker compose -p vc-<repo-slug> -f "$recipe/compose.dev.yaml" down     # keeps history
docker compose -p vc-<repo-slug> -f "$recipe/compose.dev.yaml" down -v  # wipes history
```

Tailnet is optional — supply `TAILSCALE_AUTHKEY` to join the mesh (userspace
networking, no `NET_ADMIN`). Like `compose.yaml`, this is a local development
container, not a security boundary, and is distinct from the hardened Runtime
Pack carrier above. It shares the Debian 13 baseline with `.cursor/Dockerfile`
(the Cloud Agent env).
