# Compile Embargo — W2 Integration Responsibility

Compile embargo separates architectural construction from executable verification.
It is a phase contract, not a personal permission or an exception granted by a
particular person. Workers must not reshape a cut merely to make a partial system
pass local gates.

## W1 and W2 workers: structural work

Workers map dependencies, implement the assigned contract, inspect interfaces and
connections, and preserve coherent local checkpoint commits. They do not run
compilers, builds, formatters, linters, type checks, tests, or selectively chosen
gates during structural W1/W2 work. Tests may be authored and inspected without
being executed. Workers record unknowns, risks, missing connections and the
verification that the integrator will need.

A local checkpoint may use `git commit --no-verify` when hooks would run gates.
No selective gate prerequisite or hook-policy implementation is required first.
This bypass skips the whole Git hook entrypoint; record all controls actually
skipped, including security controls. Preserve correct attribution and stage only
owned changes. A checkpoint preserves work; it does not certify correctness or
security. It does not authorize a push, publication or release.

## Integrator W2: assemble, then verify

The designated W2 integrator owns the transition from structural work to
executable verification. The integrator:

1. checks exact worker commits, scope, dependencies and interface contracts;
2. assembles the cuts and resolves structural gaps between components;
3. records `W2_STRUCTURALLY_CLOSED` against the exact assembled SHA when the
   system is structurally complete enough for meaningful verification;
4. restores and runs the full applicable gates, including security and secret
   checks skipped by checkpoint hooks;
5. assigns bounded repairs from those results and verifies the repaired state.

Structural integration during W2 is permitted before executable gates. It is
explicitly unverified and must retain all outstanding verification obligations.
Workers neither choose a subset of gates nor close the embargo themselves.

`W2_STRUCTURALLY_CLOSED` means **assembled and ready to check**, not **working**.
A failed gate after closure calls for implementation repair, not weaker assertions
or an automatic reopening of embargo. A further structural phase must be recorded
explicitly by its integrator, with its scope and closure condition.

## Evidence and phase state

The plan and handoff identify the phase, W2 integrator, cut boundaries, structural
proof, deferred verification, and closure condition. Each checkpoint records its
SHA, owned scope, risks, and controls run or skipped. The integrator records the
assembled SHA, closure attestation, gate results and remaining product acceptance.
Use existing plan, tracker, journal and dispatch records; do not create a parallel
control plane. In Vibecrafted the Operator journal is `.vibecrafted/THE_JOURNAL.md`.

Repository hook adapters must reflect this division of responsibility. A malformed
marker is an error, never permission to widen a bypass. A marker or adapter alone
is not proof that enforcement works. Outside structural W1/W2 the normal gates
apply. Closure restores the gates; retaining the closure receipt does not keep
embargo active.

## One policy, repository-specific commands

The policy applies across languages. Deferred worker execution includes Swift
build/type-check/tests, Rust check/build/Clippy/fmt/tests, Python Ruff/type-check/
tests, Shell syntax/format/lint/tests, and frontend format/lint/type-check/tests.
The repository supplies exact commands; a historical four-ID allowlist is not the
universal policy.

Codescribe's `embargo.toml`, `embargo-guard.sh` and self-test are an existing adapter
profile, not assumed Vibecrafted implementation. Its older selective four-gate
policy and absolute checkpoint bypass prohibition do not describe this unified
contract. Updating this document does not update Codescribe's hooks or repository.

## Delivery

Before delivery, the integrator must complete every required gate and real product
acceptance on the exact delivered generation. Signing, notarization, installation,
session preservation and user interaction checks remain separate evidence where
applicable. No checkpoint, structural admission, closure marker or green unit test
alone proves verified delivery. Publication and release retain their existing
operation boundaries; embargo grants no additional authority.
