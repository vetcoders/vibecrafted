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
returned success. The adapter binds the literal UTF-8 body as one
`--message=<text>` argv element: leading YAML `---`, `-`, `@`, multiline
quotes, Unicode, shell-like text and paths remain data. There is no shell
interpolation. NUL is refused before persistence because process argv cannot
carry it. Codex accepts message text on its process argv, so avoid putting
secrets in Codex steering messages. Claude, Agy, Grok,
Junie, Kimi, Cursor, and Gemini use the shared
durable inbox. Their `inbox_pending` state proves that Vibecrafted stored the
message for the selected run; it does not prove delivery to model context.
Gemini is an inbox provider even when the run metadata has no native
`session_id` or `agent_session_id`. Address that run by `--run-id` or by a
recorded runtime session. A missing native session is not a queue attempt.

`context_injected` is a later observation on an inbox receipt. It means
`mark_context_injected` recorded that the text was attached to a tool
response, together with the injection nonce and a timestamp. It does not mean
the recipient read the text. To preserve the attachment replay boundary, an
injected receipt leaves that list and stays visible through `--inspect` until
ACK. ACK from any known unacknowledged receipt records
`claimed_by_recipient`. That claim is still not proof the requested work ran.

## Receive in the worker

```sh
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --receive
vibecrafted message --run-id "$VIBECRAFTED_RUN_ID" --ack <message-id>
```

`--receive` returns `inbox_pending`, `provider_accepted`, `recorded`,
`retryable_failure` and `permanent_failure` messages as JSON without consuming
them or changing their delivery state. This includes receipts written by older
runtimes: a successful native queue operation remains explicitly receivable
until recipient ACK. An accepted queue is never automatically resubmitted.
`context_injected` receipts are not in that list; inspect or ACK them by id.
The worker acknowledges each message after reading and handling it. ACK records
`claimed_by_recipient`, not proof that the requested action succeeded. A worker
should check at useful checkpoints and before finishing. A message written
while a provider is blocked in a long tool call may wait until that next check.
The bus does not wake a stopped headless worker.

Send summaries expose `receiver_command` and `delivery_notice`. Nonzero queue
receipts retain the exit code and stdout/stderr digests and add an allowlisted
diagnostic category plus a fixed action: argument refusal, unsupported queue,
unavailable session, nonqueueable session, connection failure, or other
nonzero result. They never echo native stderr, argv body, environment or tokens.
Timeouts remain ambiguous; read the durable receipt before deciding to retry.
Operator text is a correction within the worker's authorized scope, not a
Founder decision or additional authorization.

Receipts live below the existing private control-plane `messages/` directory.
`--inspect <message-id>` reads one receipt. Idempotency is scoped to body digest
and exact run. A reused key with another body or run fails. Retry can resubmit
an unresolved Codex queue operation, but cannot promise exactly-once delivery
after a timeout; an already accepted operation is not submitted again. Inbox
messages stay `inbox_pending` until ACK or until `context_injected` records
attachment. Neither transition submits the text to a second provider process.
The bus does not wake a stopped worker when a receipt changes state.

## Delivery lanes

Two lanes can carry the same receipt. They do not share `context_injected`.

| Lane            | When it moves                                  | What it records                                        |
| --------------- | ---------------------------------------------- | ------------------------------------------------------ |
| MCP             | Next tool call, if the server glue is attached | `context_injected` plus the injection nonce            |
| Monitor         | A harness push channel is actually open        | ACK after the write is flushed. Not `context_injected` |
| Checkpoint poll | The worker runs `--receive` itself             | ACK after the worker handles the row                   |

The monitor follower lives in `vibecrafted_core.monitor_lane`. Its cursor is
the set of message ids already handed to the harness. A restart does not
inject those ids again and does not drop an `inbox_pending` id that is not
in the set. Before a write it re-reads the receipt: `context_injected` or
`agent_acknowledged` is skipped.
Automatic lanes use only `inbox_pending`. Queue-accepted or failed receipts
use the explicit recipient checkpoint route; they are not automatically
attached a second time. An attachment nonce is not a model receipt or ACK.

The startup prompt (interactive and headless) names both lanes and this
run's bus nonce. The nonce is `vcbus-` plus the first 24 hex characters of
`SHA-256("vibecrafted.message-bus.v1\\n" + run_id)` over UTF-8. Trust a
pasted bus message only when it carries that nonce. MCP glue should stamp
the same value; the monitor line carries it in the injected text.

Levels are `live`, `injected-on-call`, and `checkpoint-poll`. A missing
process drops the level. It is never reported as `live`.

| Provider | Declared level     | Monitor                                                                            | When the handle is absent | Source                                                                                                                   |
| -------- | ------------------ | ---------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| claude   | `live`             | stdin `stream-json` user turn (`type=user`, text block, `parent_tool_use_id=null`) | `injected-on-call`        | Probe 2026-09-26: mid-turn stdin works. Current headless argv does not keep that pipe open (`spawn._stdin_command`).     |
| codex    | `checkpoint-poll`  | no verified active-turn monitor                                                    | `checkpoint-poll`         | Native queue acceptance remains receivable through explicit `--receive`; a thread id does not establish push capability. |
| agy      | `injected-on-call` | none                                                                               | `checkpoint-poll`         | Stream-json stdin is the initial prompt only (`prompt_transport`).                                                       |
| grok     | `injected-on-call` | none                                                                               | `checkpoint-poll`         | Durable inbox. No verified mid-turn channel.                                                                             |
| junie    | `injected-on-call` | none                                                                               | `checkpoint-poll`         | Durable inbox. No verified mid-turn channel.                                                                             |
| kimi     | `injected-on-call` | none                                                                               | `checkpoint-poll`         | Print mode has no stdin lane (`prompt_transport` argv transport).                                                        |
| cursor   | `injected-on-call` | none                                                                               | `checkpoint-poll`         | `stream-json` is output. No verified mid-turn input.                                                                     |
| gemini   | `injected-on-call` | none                                                                               | `checkpoint-poll`         | Inbox provider in the store. The gemini binary is not a launch path.                                                     |

An agent with no monitor uses attached MCP glue when available or explicit checkpoints.
Wiring Claude's open stdin is a supervisor hook (`--input-format stream-json`
and a pipe that outlives the prompt file). This module does not change that
launcher.

## Codex native capability boundary

The 2026-10-01 controlled probe used installed `codex-cli 0.160.0-alpha.2`.
The previous adapter failed with rc2 for leading `---`; the bound option reached
the native `thread/queue/add` request intact for `---`, `-` and `@` bodies.
A controlled WebSocket fixture returned three queue acceptances and one
unsupported-method failure. The recipient then read all four through the real
Vibecrafted checkpoint CLI before a separate recipient ACK. This proves the
native parser, wire payload and durable fallback; it does not prove automatic
model receipt, interactive parity, or active-turn steering.

The [native queue command](https://github.com/openai/codex/blob/d1e010213f4be3cb7751bf35249f87eb7d8e2659/codex-rs/tui/src/session_queue_commands.rs)
submits `thread/queue/add`. The [queue service](https://github.com/openai/codex/blob/d1e010213f4be3cb7751bf35249f87eb7d8e2659/codex-rs/ext/queue/src/service.rs)
starts input only when the loaded thread is idle. These source facts support
the following conservative runtime contract; no live interactive probe is claimed:

| Recipient                    | Native queue boundary                                                                                                                | Supported Vibecrafted route                                                                                       |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------- |
| Active headless `codex exec` | Acceptance does not steer the current turn; exec uses an in-process server, which can differ from the queue command's shared server. | `--receive` at useful checkpoints and before closing; explicit recipient ACK.                                     |
| Active interactive thread    | Loaded server threads may run queued input after becoming idle; this is another turn, not a guaranteed current-turn correction.      | Same explicit checkpoint/ACK route; do not label rc0 as model receipt.                                            |
| Saved, unloaded idle thread  | Storage does not load the thread or start a model turn; the native API requires a loaded thread to start queued input.               | Durable receipt remains inspectable/receivable; loading or resuming is a separate authorized lifecycle operation. |

Recipient ACK cannot cancel a message already accepted into the provider's
queue. A later native turn may therefore repeat the same text. Handle message
identity/idempotency explicitly; never execute work twice merely because a
transport repeats it. This bus does not start a competing writer to force delivery.
