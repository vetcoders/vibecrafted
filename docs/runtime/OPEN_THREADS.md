# Runtime open threads

One note replacing three superseded direction documents written between June and August 2026:
`RUNTIME_INTEGRATION_ROADMAP.md`, `DELIVERY_PROOF_KERNEL_v1.md`, and `CONTRACT_v1.5.0.md`
(1710 lines total). Git history holds them in full. This file keeps only what is still open
or still binding — verified against the tree on 2026-09-14.

## Landed — do not reopen

- **Delivery proof kernel.** `vibecrafted_core/delivery/` carries the typed contracts
  (`DeliveryProofContract`, `DeliveryRecord`, `DeliverySeal`, `ExecutionEnvelope`,
  `ExecutionEvidence` in `model.py`, plus `proof.py`, `seal.py`, `scope.py`, `store.py`,
  `doctor.py`, `executor.py`), mirrored in Rust `control-core` (`DeliverySealRef`,
  `DeliveryState`) and covered by five suites under `vibecrafted-core/tests/delivery/`.
  This replaced the old "report exists and is non-empty" test for delivery truth.
- **Named, stable failure kinds.** Delivered as `DeliveryState`: failed · invalid · stale ·
  unverified · delivered · sealed · invalidated.
- **Run-state vocabulary** from the v1.5.0 draft: 11 of its 13 states live in `control_plane.py`.

## Still open

- `completed_no_report` and `orphaned` were specified as run states and never landed
  (zero occurrences in `control_plane.py`). Wire them or drop them from the vocabulary —
  a published state list that the runtime cannot produce is a lie in the contract.
- `TOPOLOGY.md` still does not mark each runtime lane live / partial / scaffold. That was
  the Phase-0 exit condition of the integration roadmap and it never closed.
- The public docs mirror (`docs/public/**` into `vibecrafted-io/site/`) has no sync path.
  Only `/docs/`, `/docs/contributing-skills/` and `/docs/docker/` are live; the rest of the
  catalog is authored and unpublished.

## Invariants that still bind

1. **Work decoupled from view** — engine work must not depend on a watched tab.
2. **Artifact-as-truth** — every run leaves report, transcript, metadata, and machine-readable status.
3. **One contract, many eyes** — Python writers, Rust readers, TUI, web, tray and shell share
   one state schema; no surface invents a second truth.
4. **Degrade, don't die** — no vc_frame, no TTY, a crash, or an interrupted shell must still
   produce an honest failure artifact.
5. **Seal then widen** — prove one path end to end, then add eyes.

Canonical runtime contracts today: [CONTRACT.md](./CONTRACT.md) · [LIFECYCLE.md](./LIFECYCLE.md) ·
[UNIFIED_LAUNCH_CONTRACT.md](./UNIFIED_LAUNCH_CONTRACT.md) · [WORKSPACE_IDENTITY.md](./WORKSPACE_IDENTITY.md).
