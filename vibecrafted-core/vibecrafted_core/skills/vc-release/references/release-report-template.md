# Release Report Template

This is the canonical shape of a `vc-release` report. It is the only honest
way to say "released" inside the 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. framework. Every release
must produce a report that follows this structure. Missing or empty
sections mean the release is **blocked**.

Copy the body below into your release artifact (under
`$VIBECRAFTED_ROOT/.vibecrafted/artifacts/<org>/<repo>/<YYYY_MMDD>/reports/`)
and fill it in. Do not delete sections. If a section does not apply,
explain in one sentence why it does not apply.

---

## Frontmatter

```yaml
---
run_id: <generated-unique-id>
agent: <claude|codex|gemini|cursor|system>
skill: vc-release
project: <repo-name>
status: <pending|in-progress|completed|failed|blocked>
created: <ISO-8601 timestamp>
release_version: <semver tag, e.g. v1.4.1>
---
```

## 1. Security gate

- Command run: `make semgrep` (or documented equivalent: `<command>`)
- Exit status: `<0 | non-zero>`
- Finding count: `<blocking>` blocking, `<info>` informational
- Findings table:

  | Rule ID | Severity | File | Lines | Boundary | Resolution |
  | ------- | -------- | ---- | ----- | -------- | ---------- |
  |         |          |      |       |          |            |

- Boundary classification taxonomy:
  - `path` — tainted path / LFI sinks
  - `regex` — ReDoS-prone parsing
  - `merge` — header / object merge unsafety
  - `shell` — command construction
  - `auth` — authn / authz seams
  - `other` — explain in the resolution column
- Gate satisfied: `<yes | no | accepted-with-reason>`
- If `accepted-with-reason`, reference the user-signed acceptance line.

## 2. Exposed surface inventory

| Surface         | Bind address        | Port | Public? | Proxy in front            | TLS terminator     | Auth boundary                          |
| --------------- | ------------------- | ---- | ------- | ------------------------- | ------------------ | -------------------------------------- |
| <app/api/admin> | 127.0.0.1 / 0.0.0.0 |      | yes/no  | Caddy / Nginx / LB / none | proxy / app / none | public / session / token / mTLS / none |

- Edge headers added/stripped: `HSTS`, `CSP`, `X-Frame-Options`,
  `Strict-Transport-Security`, `Referrer-Policy`, `Permissions-Policy`,
  CORS allowlist (one line each, with values).
- Secret materialization: how and where each secret reaches the runtime
  (env injection at start, secrets manager at fetch, none).
- Forbidden patterns checked: `0.0.0.0` without intent, `CORS: *` on
  authenticated APIs, framework debug pages reachable, `.env` or backup
  files web-accessible, stack traces or banners exposed.

## 3. Deployment mode decision

- Chosen topology: `<static | Caddy | Nginx | Docker | other>`
- Reason this is the smallest honest fit:
  - <one or two sentences>
- Topology sketch (text is fine):

  ```text
  client → DNS (canonical host) → TLS terminator → reverse proxy → app
                                                                 → worker
                                                                 → db
  ```

- Healthcheck path and expected response: `<endpoint>` → `<expected>`
- Restart and graceful shutdown behaviour:
- Rollback procedure (without manual heroics):
  - <command or runbook link>

## 4. Post-release install smoke

- Artifact source (must NOT be a working-copy path):
  - registry URL or download URL: `<url>`
  - tag / version: `<tag>`
  - digest (when available): `<sha256>`
- Cold environment used: `<fresh container | new VM | scratch venv | other>`
- Command sequence executed:

  ```bash
  # paste the exact commands run
  ```

- First-run evidence:
  - exit code: `<code>`
  - version banner: `<paste>`
  - health probe: `<curl output | command output>`
- Drift observed against the documented quickstart:
  - <none | list each drift item; open follow-ups>

## Sign-off

- Security gate: <ok | accepted | blocked>
- Surface inventory: <ok | accepted | blocked>
- Deployment mode: <ok | accepted | blocked>
- Install smoke: <ok | accepted | blocked>

Released by: `<agent>` on `<ISO-8601 timestamp>`.

---

## Why each section is mandatory

- **Security gate** turns Semgrep from a private CI step into a public
  release-time witness. The classification column makes "we ran the
  scanner" measurably different from "we read the output."
- **Exposed surface inventory** is what an external AppSec or Semgrep
  reviewer needs to assess production reality. Ports, proxies, auth, and
  edge headers describe real attack surface; secret handling describes
  the failure mode you are most likely to ship by accident.
- **Deployment mode decision** is what makes the release reproducible.
  Choosing a topology by reflex ("we always use Docker") hides the
  failure modes of that choice; writing the reason makes the trade-off
  visible to the next operator.
- **Post-release install smoke** is the only check that proves a stranger
  can actually install and run the published artifact. A green test
  matrix is not a substitute for a cold install.

If you want to skip a section, you do not want a release. You want a
deploy. Those are different things.
