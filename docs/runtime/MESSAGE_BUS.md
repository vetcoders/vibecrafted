# Vibecrafted message bus

`vibecrafted message` addresses an existing control-plane run. It never starts
or resumes a provider process. The same command works for headless and visible
runs; both startup prompts tell the worker where to read its inbox.

## Send

```sh
vibecrafted message --session <runtime-or-provider-session-id> --file note.txt --json
vibecrafted message --run-id <run-id> --file note.txt --idempotency-key <key> --json
```

`--session` is resolved against recorded `runtime_session_id`,
`vibecrafted_session_id`, `session_id`, and `agent_session_id` in run metadata.
If several runs share a session, including a resumed provider session, the
command refuses to guess. Use `--run-id` to select the exact executor. Providing
both selectors requires them to name the same run.

Codex uses its native `queue --thread` adapter after the run's native thread ID
is recorded. Before that identity appears, it uses the same durable inbox as
the other providers. Its `provider_accepted` state proves only that the queue command
returned success. The current Codex CLI accepts `--message` text on its process
argv, so avoid putting secrets in Codex steering messages. Claude, Agy, Grok,
Junie, Kimi, and Cursor use the shared
durable inbox. Their `inbox_pending` state proves that Vibecrafted stored the
message for the selected run; it does not prove delivery to model context.

## Receive in the worker

```sh
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --ack <message-id>
```

`--receive` returns unacknowledged messages as JSON and does not consume them.
The worker acknowledges each message after reading and handling it. ACK records
`claimed_by_recipient`, not proof that the requested action succeeded. A worker
should check at useful checkpoints and before finishing. A message written
while a provider is blocked in a long tool call may wait until that next check.
The bus does not wake a stopped headless worker.

Receipts live below the existing private control-plane `messages/` directory.
`--inspect <message-id>` reads one receipt. Idempotency is scoped to body digest
and exact run. A reused key with another body or run fails. Retry can resubmit
an unresolved Codex queue operation, but cannot promise exactly-once delivery
after a timeout; an already accepted operation is not submitted again. Inbox
messages remain pending until ACK and are never submitted to a second provider
process.
