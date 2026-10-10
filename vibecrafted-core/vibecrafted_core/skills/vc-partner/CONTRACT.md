# `vc-partner` Contract

The presence and responsibility contract is defined in [SKILL.md](SKILL.md).
This reference gives acceptance examples for applying it.

| Situation                                                 | Expected behavior                                                                       |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Restore a known terminal preference                       | Inspect, change inline, verify the effective value, return to conversation.             |
| Admit a verified, conflict-free worker baton              | Check baseline and destination, integrate within authorization, verify the destination. |
| A request needs substantial investigation or a long build | Delegate when authorized, retain the receipt and arm completion delivery.               |
| “Stay here with me” while a worker runs                   | Return attention to the conversation; keep the authorized job running.                  |
| “Stop that build”                                         | Stop the named build and retain its recoverable state.                                  |
| The topic changes, then a worker finishes                 | Receive and verify the result; briefly surface the outcome or needed decision.          |
| A worker reports success                                  | Check the actual artifact and integration state before claiming delivery.               |
| Resume after compaction                                   | Recover purpose, open runs, evidence and next actions from durable state.               |

Judge duration, uncertainty, impact, verification and recovery together.
Command count alone does not decide inline versus delegation.
Existing authorization and repository rules still apply.
