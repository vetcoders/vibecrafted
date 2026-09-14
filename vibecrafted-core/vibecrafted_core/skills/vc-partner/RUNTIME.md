# `vc-partner` Runtime

`vc-partner` as an interactive skill does not automatically launch runtime.

Runtime begins only from a TTY face (same family as `vc-init`):

```bash
vibecrafted partner <agent>
vibecrafted partner <agent> --runtime plain
vc-start   # then [New] with the partner ritual, then /vc-partner
```

`vc-partner` without a TTY refuses. `--prompt` / `--file` on
`vibecrafted partner` are extra seed context, never a headless worker.

## Runtime Responsibilities

The partner runtime creates durable state for shared steering:

- run metadata
- transcript
- partner report
- append-only journal
- delegated runtime links
- close-out summary

## Artifact Layout

```text
$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/partner/
  journal.md
  reports/
    <timestamp>_<slug>_partner.md
    <timestamp>_<slug>_partner.transcript.log
    <timestamp>_<slug>_partner.meta.json
```

The journal is the memory spine. Reports are snapshots.

## Runtime Lanes

| Need                                    | Runtime lane                                                      |
| --------------------------------------- | ----------------------------------------------------------------- |
| Single bounded build                    | `vibecrafted implement <agent>`                                   |
| Strict Examine -> Research -> Implement | `vibecrafted workflow <agent>`                                    |
| Multiple field teams                    | `$vc-operator` posture + `vibecrafted dispatch` or workflow lanes |
| Full takeover                           | `vibecrafted ownership <agent>`                                   |
| Implementation review                   | `vibecrafted review <agent>`                                      |
| Shape/trajectory check                  | `vibecrafted followup <agent>`                                    |
| Independent falsification               | `vibecrafted audit <agent>`                                       |
| Product-surface undone check            | `vibecrafted dou <agent>`                                         |
| Gap convergence                         | `vibecrafted marbles <agent>`                                     |
| Entropy reduction after marbles         | `vibecrafted polarize <agent>`                                    |
| Release surface                         | `vibecrafted release <agent>`                                     |

## Runtime Close-Out

Partner runtime may close only when one terminal state is true:

```yaml
terminal_state:
  shipped:
    requires:
      - original_shape preserved or intentionally changed
      - gates recorded
      - review/followup/audit/dou findings handled or deferred explicitly
      - next move named
  escalated_to_ownership:
    requires:
      - reason for takeover
      - current original_shape
      - handoff state
  blocked_with_evidence:
    requires:
      - blocker
      - attempted checks
      - nearest safe next action
```

## Non-Goals

- Do not use runtime to avoid shared decision-making.
- Do not launch field teams before the success contract exists.
- Do not let runtime workers change the original shape without a journal entry.
