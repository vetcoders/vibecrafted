# Agent session recovery after a runtime update

Status: agreed product contract, 2026-10-14. This document specifies the
required behavior; it does not certify implementation or installed acceptance.

## Purpose and scope

After a newer runtime is installed, offer to resume the current interactive
agent conversations in a new Frame server and a separate VC Terminal instance.
Collect them in one unified Frame session labeled **Recovered workspaces**.
Keep each conversation's original workspace association visible and recorded.

Recover agent conversations through their exact provider-native session IDs.
Ordinary shells and other non-agent panes are not restored. This operation is
not a transfer of process memory, live PTYs, shell state, or arbitrary commands.
Saved provider conversation history is preserved; normal provider resume may
append to that history.

Independent headless workers continue their tasks. Provider-managed background
tasks are not cancelled by this operation and may also continue running.

## Ownership and reuse

The installer owns publication of the new runtime. Frame owns live panes and
client connections. The existing interactive launcher and native resume paths
own launching the replacement agents. Recovery coordinates these owners; it
must not introduce a second installer, provider session store, or spawn path.

Reuse the applicable snapshot, native-session evidence, and resurrection
components described in [Update and rollback](../public/getting-started/update.md).
The broader `reinstall --clean` stop/install/resurrect behavior is not the scope
of this action: the runtime is already installed, independent workers remain
running, and source Frame sessions remain available until retirement.

Use the identities and snapshot lineage defined in
[Workspace identity](./WORKSPACE_IDENTITY.md). A replacement has a new runtime
instance identity. Its association with an original workspace or conversation
does not authorize reusing the old live instance identity across generations.

## Separate runtime generations

- Identify both the running Frame generation and the verified installed target.
  A public launcher's version alone does not identify a running Frame server.
- Keep each live server, its clients, plugins, and runtime assets bound to its
  generation. Changing the installed selector must not redirect an old process
  into new-generation assets or an incompatible server connection.
- Open new Frame sessions under the installed generation in a separate VC
  Terminal instance. Do not replace or repurpose the existing terminal window.
- Preserve every generation still needed by live clients, servers, or workers.
  Retiring an old Frame session is not permission to delete a generation still
  used by an independent worker.
- Scope recovery to explicitly identified source sessions. A shared version,
  repository basename, terminal window, or display label is not session identity.

## Offer and consent

After successful installed-runtime publication and detection of a newer
generation, show the offer in a **floating Frame plugin** in the current
environment. Installation alone neither moves agents nor arms session cleanup.

The notice body, including its `⚠` heading, uses a calm amber/orange foreground.
The decision prompt uses the normal foreground. Respect terminal capabilities
and retain a readable plain-text fallback; color is not the only status signal.
The default answer is **No**. Closing the notice or answering `n` leaves the
current sessions running, and new sessions can still use the new runtime.

```text
⚠ A newer Vibecrafted runtime is ready.

Vibecrafted can resume your interactive agent conversations in a new Frame
server, opened in a separate VC Terminal window.

The selected agents will stop in the current Frame before resuming in the
new one. Their saved conversation history will be preserved.

Other panes will not be restored. Your current Frame will remain available
so you can finish any remaining work and close it manually. After a
successful move, the old Frame sessions will be cleared when their last
connected client disconnects, ending any work still running in those panes.

Background work will continue. This action does not cancel independent
headless workers or tasks running through your providers.

Move agent sessions now? [y/N]
```

## Snapshot and handoff

Before stopping an agent, durably capture its source Frame/session/pane
identity, provider, proven native session ID, working directory, title/order,
workspace association, and source/target generation identities. Retain the
view/layout snapshot as recovery evidence. Do not replay arbitrary commands
reconstructed from pane titles or joined process argument strings.

Resume only a conversation whose exact native session ID is proven. Unknown or
ambiguous identities remain unresolved and are shown to the user; a fresh
conversation or an inferred ID is not a successful recovery.

Prepare the destination and persist the recovery record before relinquishing
source agents. Coordinate stopping each selected interactive instance with its
current activity. Detaching a terminal client does not stop that instance.
Confirm the old instance has stopped before starting its replacement; never
run two interactive instances of the same conversation as a recovery shortcut.
Do not signal independent workers or unrelated provider processes.

Use the existing native `vc-resume` path with the recorded provider/session ID
inside the new Frame. Preserve titles, ordering, cwd, and workspace attribution
for the recovered agent panes. New pane/process IDs are expected; record their
mapping to the originals rather than claiming physical identity was preserved.

## Progress, completion, and retry

After `y`, the floating plugin immediately shows **Processing…** and concrete
progress: snapshot recorded, destination prepared, agents stopping, conversations
resumed `n/total`, and destination verified. Keep the operation visible; a
spinner or successful spawn alone is not completion.

Verify every selected conversation in its target pane against the exact native
session ID and target Frame generation. Record per-agent outcomes and the
destination entry point in a durable receipt. Repeated clicks, reconnects, or
retries must reuse the transaction and must not duplicate successful resumes.

On partial failure, show which conversations moved and which remain unresolved.
Preserve the original snapshot and remaining source sessions. Retry only the
unresolved handoffs. Do not claim all sessions moved, offer closing the old
terminal, or arm automatic retirement while the move is incomplete. A stopped
source agent cannot be described as still running merely because recovery failed.

After full verified recovery, show the following in the normal foreground;
success is no longer an amber warning:

```text
Your agent sessions have moved.

They are now available in Recovered workspaces, opened in a separate
VC Terminal window.

This VC Terminal window remains available. The old Frame sessions will
be cleared when their last connected client disconnects. Closing the last
client will end any remaining work in their other panes.

Your saved agent conversations and independent background workers are
preserved.

Close this VC Terminal window now? [y/N]
```

`n` leaves the source window usable. `y` closes only the identified source
window, never every VC Terminal instance or the application by a broad bundle-ID
quit. If that window also contains unrelated sessions, do not silently extend
the close action to them; identify the extra affected scope and obtain consent.

## Old-session retirement

Full verified recovery arms retirement only for the exact source sessions in
that transaction. They remain available while any client is connected. The
second prompt is a convenience for closing the window, not a requirement for
retirement: a later manual disconnect follows the same rule.

When the last connected client disconnects, automatically clear those old Frame
sessions and their remaining panes. This includes ordinary pane-owned processes;
the prompts disclose that remaining pane work will end. An independent headless
worker or provider-managed task is not a leftover pane to clear.

The server must reconcile the last-client transition with concurrent attachment
before teardown. A client count observed earlier is not authority to remove a
now-connected session. Do not retire unrelated sessions, an incompletely moved
source session, provider conversation history, or worker-owned state. An
interrupted retirement remains recorded and safely retryable under the same
transaction identity.

## Acceptance obligations

The implementation must demonstrate:

1. A newer installed runtime is detected from a running older Frame; declining
   changes no sessions and does not arm retirement.
2. Old and new generations coexist in separate terminal instances with assets
   and connections correctly bound to each generation.
3. Multiple source workspaces recover into one **Recovered workspaces** session
   with exact conversation IDs, original attribution, and no ordinary shells.
4. Source interactive instances stop before replacement resume; independent
   workers retain their process/run identity and can finish their tasks.
5. Missing IDs, failed resumes, interruption, and repeated requests produce honest
   partial results without duplicate conversations or premature cleanup.
6. The old environment remains usable after a successful move and a declined
   close prompt; one of several clients disconnecting does not retire it.
7. The last-client disconnect retires only the completed transaction's source
   sessions, including after later manual closure, with concurrent attach tested.
8. Closing the source window preserves the destination, unrelated terminals,
   provider history, worker state, and any generations still used by live work.

Source tests, signed installation, and native acceptance are distinct evidence.
Documenting this contract establishes none of them by itself.
