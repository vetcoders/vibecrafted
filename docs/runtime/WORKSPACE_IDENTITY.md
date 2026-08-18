# Vibecrafted Workspace Identity — Cut A wire contract

_Status: control-plane authority · Cut A landed · vc-frame Cut B consumer contract_

## Ownership

| Role                                         | Actor                                                                                     |
| -------------------------------------------- | ----------------------------------------------------------------------------------------- |
| Sole durable writer of the workspace catalog | Vibecrafted control plane (`vibecrafted_core.workspace_catalog`) under `VIBECRAFTED_HOME` |
| Projections / readers                        | Server/API, future vc-frame integrations                                                  |
| Forbidden                                    | Second catalog inside vc-frame or `session-layout.kdl`                                    |

## Identity model

| Field                          | Kind   | Meaning                                                                                                            |
| ------------------------------ | ------ | ------------------------------------------------------------------------------------------------------------------ |
| `workspace_id`                 | UUID   | Durable logical Vibecrafted Workspace. **Not** derived from root. Same root may host multiple parallel workspaces. |
| `vibecrafted_session_id`       | UUID   | Durable logical session belonging to `workspace_id`.                                                               |
| `workspace_instance_id`        | UUID   | Concrete runtime materialization of `workspace_id`, bound to an exact `build_id`.                                  |
| `run_id`                       | string | Concrete execution belonging to `workspace_id` + `vibecrafted_session_id`.                                         |
| `agent_session_id`             | string | Provider-native session (subordinate).                                                                             |
| `runtime_session_id`           | string | Runtime tracking id (subordinate).                                                                                 |
| vc-frame / Zellij session name | string | Physical pane host (subordinate). Never overload `session_id`.                                                     |

### Accepted id rule — mint v7, accept any UUID

New ids are minted as UUIDv7 (`new_uuid7`) so that lexical order is
chronological. **Acceptance is version-agnostic:** `require_uuid` admits any
canonical RFC 4122 UUID, and every reader — control plane, server/API,
vc-frame — MUST do the same.

No consumer may validate, filter, or sort on the UUID _version_. The live
catalog is mixed by construction: workspaces minted before v7 became the
default carry v4 ids, including Vibecrafted's own repository
(`bda366e0-519f-45f1-8d10-449058491a94`). A v7-only rail would drop them.

Ordering that needs chronology reads `created_at`, never the id bits.

## build_id

Schema: `vibecrafted.build-id.v1`

```json
{
  "schema": "vibecrafted.build-id.v1",
  "git_commit": "<full sha or empty>",
  "dirty": true,
  "dirty_digest": "<sha256 of canonical dirty evidence when dirty, else empty>",
  "package_version": "<VERSION file or package>",
  "root": "<resolved absolute root>",
  "rendered": "git:<12sha>[+dirty:<12digest>]@v<version>"
}
```

A live `workspace_instance_id` is bound to that `build_id`. An instance from
build B cannot claim live ownership of build A's instance
(`WorkspaceInstanceBuildMismatch`).

`dirty_digest` covers NUL-delimited porcelain status, binary tracked diffs,
and each untracked path, file type, and content. Two dirty builds at the same
commit therefore remain distinct even when `git status` prints the same path
and state. If Git identifies a checkout but cannot provide complete dirty
evidence, build identity resolution fails closed.

## Catalog location

```
$VIBECRAFTED_HOME/control_plane/workspaces/
  catalog.json                 # vibecrafted.workspace-catalog.v1
  .catalog.lock
  instances/<uuid>.json        # vibecrafted.workspace-instance.v1
  sessions/<uuid>.json        # vibecrafted.workspace-session.v1
  snapshot_manifests/<uuid>.json
  migration_report.json
```

### Lifecycle verbs

```
vibecrafted workspace create  --root PATH [--label NAME] [--workspace-id UUID] [--no-select]
vibecrafted workspace list     [--include-buried]
vibecrafted workspace show     <workspace_id>
vibecrafted workspace select   <workspace_id>
vibecrafted workspace bury     <workspace_id>     # hide without deleting history
vibecrafted workspace recover  <workspace_id> [--select]
vibecrafted workspace materialize <workspace_id> [--root PATH]
vibecrafted workspace session-attach --workspace-id UUID --session-id UUID \
  --instance-id UUID --runtime vc-frame --runtime-session-id NAME --state live|dead|missing
vibecrafted workspace migrate  [--dry-run]
vibecrafted workspace settlement-counts <workspace_id>
```

`bury` detaches live instances. `recover` reactivates the logical workspace
without pretending an incompatible live runtime can be attached.

## WES runtime attachments

A logical `vibecrafted_session_id` may own multiple physical runtime
incarnations. They are append-preserved in
`sessions/<vibecrafted_session_id>.json`; a dead `vc-frame` is evidence, not a
process to resurrect or delete. A replacement is added as another attachment
with `replaces_runtime_session_id`, while the dead attachment and its socket
namespace remain visible to WES.

On macOS, Vibecrafted.app opens new frames under the short product socket root
`/tmp/vc-frame-$UID`. Before opening the new window it reads the former
TMPDIR-based namespace and attaches every discovered live/dead session to WES.
It never kills or rewrites those legacy physical sessions.

Claude / CLI / any path that does not inherit AppDelegate must still land on
that same root. `~/.local/bin/vc-frame` is a symlink into
`vibecrafted-current/bin/vc-frame` (the product wrapper). A copied Mach-O or
an old wrapper in `~/.local/bin` is how the previous `/tmp` fix stayed
app-only. `vibecrafted doctor --json` exposes `authority.available` so host
path-doctor can stop guessing from symlink shape alone.

## Worker host routing

Rules (shell and Python are semantically identical):

1. `VIBECRAFTED_WORKER_SESSION` if set — explicit override.
2. Else workspace-bound host:
   `{sanitized_display_label}-{workspace_id_short8}-w`
3. Emergency fallback only: `{basename(root)}-w` if the catalog cannot open.

The older `{label}-{short} workers` form (space in the session name) overflowed
macOS `sockaddr_un` (104 bytes) on the default TMPDIR socket root. Claude / CLI
paths that do not inherit AppDelegate must still use `/tmp/vc-frame-$UID`.
`legacy_worker_host_session_name()` keeps the old token for WES attach.

Two workspaces rooted in directories both named `vibecrafted` never share a
worker host. The bare basename remains the human operator interactive card.

Env exports for workers:

| Env                                 | Value                    |
| ----------------------------------- | ------------------------ |
| `VIBECRAFTED_WORKSPACE_ID`          | `workspace_id`           |
| `VIBECRAFTED_SESSION_ID`            | `vibecrafted_session_id` |
| `VIBECRAFTED_WORKSPACE_INSTANCE_ID` | `workspace_instance_id`  |
| `VIBECRAFTED_BUILD_ID`              | `build_id.rendered`      |

## Run metadata fields (new, additive)

New runs stamp into `meta.json` / control-plane snapshots:

```json
{
  "workspace_id": "<uuid>",
  "vibecrafted_session_id": "<uuid>",
  "workspace_instance_id": "<uuid>",
  "build_id": { "...": "vibecrafted.build-id.v1" },
  "workspace_display_label": "vibecrafted",
  "worker_host_session": "vibecrafted-a1b2c3d4 workers",
  "worker_host_display": "vibecrafted [a1b2c3d4]"
}
```

Legacy fields (`agent_session_id`, `runtime_session_id`, `session_id`) are
unchanged.

## Settlement F/X/N scoping

- Permanent authority remains the global settlement ledger.
- Scoped projection: `vibecrafted settlements summary --workspace-id <uuid>`
  or `workspace_catalog.settlement_counts_for_workspace(workspace_id)`.
- Membership is evidence-based (run meta / snapshot carrying `workspace_id`).
- Runs without workspace evidence are **excluded**, never guessed.

## Snapshot manifest (Cut B contract)

Schema: `vibecrafted.workspace-snapshot-manifest.v1`

```json
{
  "schema": "vibecrafted.workspace-snapshot-manifest.v1",
  "snapshot_id": "<uuid>",
  "workspace_id": "<uuid>",
  "schema_version": "1",
  "build_id": { "schema": "vibecrafted.build-id.v1", "...": "..." },
  "created_at": "<iso8601>",
  "previous_snapshot_id": null,
  "sessions": [],
  "runs": [],
  "layout_snapshots": [],
  "artifacts": [],
  "checksums": { "<path>": "sha256:..." },
  "migration_lineage": [
    {
      "from_build_id": "...",
      "to_build_id": "...",
      "migrated_at": "...",
      "notes": "recover under new build"
    }
  ]
}
```

Cut A defines and can persist the manifest. **Cut A does not implement
vc-frame resurrection.** Cut B must:

1. Provide a workspace selector UI reading `catalog.json`.
2. Scope the settlement rail F/X/N via `--workspace-id` / projection API.
3. Isolate live runtime hosts by `workspace_instance_id` + `build_id`.
4. On recover-under-new-build: write a new snapshot with `migration_lineage`
   linking the previous snapshot; never attach a live instance across
   incompatible builds.

## Migration rules (fail-closed)

- Idempotent.
- Group legacy records only when canonical-root evidence is unambiguous.
- Do not invent membership for records lacking evidence.
- Preserve unclassified records; report them in `migration_report.json`.
- Never rewrite or delete original historical evidence.

## Cut B remaining risks

- Operator multi-workspace same-root requires explicit select/env pin.
- Headless workers launched before Cut A lack `workspace_id` and stay
  unassigned in scoped F/X/N until re-settled with evidence.
- Shell host resolution shells out to Python; pure-shell emergency fallback
  is basename-only (collision-prone) and must remain rare.
- vc-frame session name length limits with multi-word hosts — already used
  for `"… workers"`; short token keeps names bounded.

## Python module

`vibecrafted_core.workspace_catalog` — create/list/select/show/bury/recover,
WES runtime attachments,
`resolve_run_workspace_identity`, `resolve_worker_host_session`,
`settlement_counts_for_workspace`, snapshot manifest helpers.
