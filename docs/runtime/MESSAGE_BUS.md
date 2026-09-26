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
Junie, Kimi, Cursor, and Gemini use the shared
durable inbox. Their `inbox_pending` state proves that Vibecrafted stored the
message for the selected run; it does not prove delivery to model context.
Gemini is an inbox provider even when the run metadata has no native
`session_id` or `agent_session_id`. Address that run by `--run-id` or by a
recorded runtime session. A missing native session is not a queue attempt.

`context_injected` is a later observation on an inbox receipt. It means
`mark_context_injected` recorded that the text was attached to a tool
response, together with the injection nonce and a timestamp. It does not mean
the recipient read the text. `--receive` lists only `inbox_pending`, so an
injected receipt leaves that list and stays visible through `--inspect` until
ACK. ACK from `inbox_pending` or from `context_injected` records
`claimed_by_recipient`. That claim is still not proof the requested work ran.

## Receive in the worker

```sh
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --ack <message-id>
```

`--receive` returns `inbox_pending` messages as JSON and does not consume them.
`context_injected` receipts are not in that list; inspect or ACK them by id.
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
messages stay `inbox_pending` until ACK or until `context_injected` records
attachment. Neither transition submits the text to a second provider process.
The bus does not wake a stopped worker when a receipt changes state.
