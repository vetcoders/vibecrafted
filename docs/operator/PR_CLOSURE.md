# Portable PR closure

`vibecrafted pr-closure` imports a versioned JSON profile and prepares ordinary
native dispatch cuts. Dispatch remains the worker lifecycle owner. Local receipt
execution requires POSIX process groups; JSON profiles can be moved between
repositories without copying a provider session or machine path.

The profile declares repository, permitted destination branches, local commands,
test evidence and required GitHub check names with their producing App IDs.
Unknown fields are rejected. Export creates a Rust/Make starting profile; edit
the commands for the destination repository before running it.

```sh
vibecrafted pr-closure export --repository OWNER/REPO --target develop \
  --target main --remote-check 'Clippy + Tests' \
  --release-repository OWNER/REPO --release-branch main \
  --output /tmp/pr-work/workflow.json
vibecrafted pr-closure validate /tmp/pr-work/workflow.json
vibecrafted pr-closure plan /tmp/pr-work/workflow.json --output /tmp/pr-work/batch.json
```

When testing an uninstalled source checkout, prefix the command with
`uv run --project vibecrafted-core`. The command is available after that source
has been integrated and installed; generating a profile does not update an
existing installation.

## Prepare one candidate

The batch freezes membership and dependency order. Feature stacks precede their
dependants; a develop-to-main promotion follows the features feeding develop.
Age breaks ties. Drafts remain visible and block completion until deliberately
settled. New arrivals belong to the next batch.

```sh
vibecrafted pr-closure dispatch /tmp/pr-work/workflow.json --repo /path/to/repo \
  --pr 123 --output /tmp/pr-work/pr-123.toml
vibecrafted dispatch /tmp/pr-work/pr-123.toml --dry-run
vibecrafted dispatch /tmp/pr-work/pr-123.toml
```

The source checkout must already contain the observed head and base objects;
fetch normally before generating a dispatch. Each generated cut uses an isolated
worktree based on the observed full PR head SHA, one worker and three bounded
repair rounds. Its brief carries the PR, every review page, issue comments and
review threads. Incomplete thread pagination refuses admission rather than
silently ignoring requests. A stacked PR whose base is another feature branch
requires explicit retargeting after its parent lands.

The worker must understand the change, reconcile the target by a normal merge,
repair and commit its owned changes. An already-ready head stays unchanged;
an empty commit is not required. Structural dispatch verifiers require
`git diff --check` and a completely clean status, including untracked files;
dispatch completion alone does not certify PR readiness.
The integrator runs the profile gates in the resulting committed candidate:

```sh
vibecrafted pr-closure verify /tmp/pr-work/workflow.json --repo /path/to/candidate \
  --pr 123 --output /tmp/pr-work/pr-123.receipt.json
```

## Count actual tests

At least one check must carry test counts. Commands are argv arrays, executed
without a shell in a sanitized environment with a fresh external
`VIBECRAFTED_HOME` for each gate. Each command must finish with exit zero and
leave the same clean commit. A timeout terminates its process group.

Rust summaries can use a regex with mandatory numeric named groups, summing
every matching suite:

```json
{
  "name": "hermetic Rust tests",
  "argv": ["make", "verify"],
  "timeout_seconds": 7200,
  "test_counts_regex": "^test result: (?:ok|FAILED)\\. (?P<passed>[0-9]+) passed; (?P<failed>[0-9]+) failed; (?P<skipped>[0-9]+) ignored;.*$"
}
```

Python gates should emit JUnit instead of guessing absent counters in pytest's
summary. Only the literal `{junit}` substring is replaced by a fresh external
report path; other braces remain data:

```json
{
  "name": "Python tests",
  "argv": ["make", "test-core", "PYTEST_ADDOPTS=--junitxml={junit}"],
  "timeout_seconds": 7200,
  "junit": true
}
```

For each counted gate, passed must be positive and failed must be zero. Skips
block by default. A repository's existing source/hermetic contract can explicitly
permit optional cases with `"allow_skips": true` and a nonblank `"skip_reason"`.
This option requires a counted gate; the receipt records the policy and reason,
and keeps skipped counts separate from executed passes. Such cases remain
**not executed**, with their names and reasons inspectable in the full log/XML.
Changing this policy invalidates the receipt through the profile digest.

Missing counts, empty reports, failures, malformed XML and changed evidence block
advancement. An opt-in never excuses zero passes, a nonzero exit, a timeout or a
changed checkout. A native GitHub check run with conclusion `SKIPPED` still blocks
admission. Required physical/product acceptance remains in the release contract;
source-gate exceptions do not certify it. Do not conceal ignored cases by
changing a regex; select and document the intended contract in the profile.

Receipts retain exact argv, full logs, report hashes, candidate/head/base SHAs,
branch names, profile digest and review digest. Files must be outside the
checkout and existing evidence is never overwritten. These are local execution
observations. Hashes detect corruption; they do not authenticate a worker or
replace required native GitHub checks. Raw local logs are not uploaded.

## Publish and request admission

```sh
vibecrafted pr-closure publish /tmp/pr-work/workflow.json --repo /path/to/candidate \
  --pr 123 --receipt /tmp/pr-work/pr-123.receipt.json
vibecrafted pr-closure admit /tmp/pr-work/workflow.json --repo /path/to/candidate \
  --pr 123 --receipt /tmp/pr-work/pr-123.receipt.json
vibecrafted pr-closure admit /tmp/pr-work/workflow.json --repo /path/to/candidate \
  --pr 123 --receipt /tmp/pr-work/pr-123.receipt.json \
  --batch /tmp/pr-work/batch.json --enable-auto-merge
```

Publish revalidates the receipt and origin identity, then pushes the exact tested
commit by fast-forward only. Its JSON result joins the published SHA to the local
receipt path and digest. A normal move from original head to tested head preserves
evidence; another head, base, review or profile requires fresh verification.

Admission is read-only by default. Explicit `--enable-auto-merge` requests normal
GitHub auto-merge/queue admission for the exact head and can merge immediately.
It requires a frozen batch, all predecessors actually merged into their current
destinations, resolved review threads, no requested changes and every configured
check successful on that exact SHA from the specified App. Skipped, neutral,
pending and failed check runs do not pass. No admin bypass is used. GitHub's
protection and queue rules remain authoritative for races after local observation.
Before requesting auto-merge, the command also verifies active server rules:
the same check names and App pins must be required, with strict base freshness
or a merge queue, resolved review threads and the configured minimum approval.
Classic branch protection is not modeled; automatic admission requires modern
rulesets. Locally green checks cannot substitute for missing server enforcement.

## Finish the batch and hand off release

```sh
vibecrafted pr-closure confirm /tmp/pr-work/workflow.json --batch /tmp/pr-work/batch.json
```

Confirmation checks actual merge records and ancestry in each current destination;
closed PRs and queued requests do not count. Its result says `batch_complete`,
not released. The profile's optional `release_source` explicitly names the
canonical repository and branch; release generation refuses an absent binding.
It may name another repository, such as a public release source for a private
suite. Editing this binding changes the profile digest, so re-plan the batch.

```sh
vibecrafted pr-closure release /tmp/pr-work/workflow.json --batch /tmp/pr-work/batch.json \
  --repo /path/to/canonical-release-source --output /tmp/pr-work/release.toml
vibecrafted dispatch /tmp/pr-work/release.toml --dry-run
vibecrafted dispatch /tmp/pr-work/release.toml
```

Release generation proves the batch merges, matches the source origin and pins
the current remote source tip, which must be locally available. The native
`release` worker then owns the repository release contract, provenance mapping,
version, signing, installation and product acceptance receipts. Missing version
intent, authorization or acceptance evidence remains a visible blocker.
Generating the task publishes nothing. A completed dispatch or worker report
alone is not proof of publication or installed-artifact acceptance; independently
inspect the release contract's resulting receipts. No new worker engine or
parallel run tracker is introduced.
