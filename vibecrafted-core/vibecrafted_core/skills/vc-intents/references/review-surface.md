# Review surface — the page the user reviews

`intents_cli.py <workdir> render --html` fills `assets/intents.html` with the
ledger and writes `<workdir>/intents.html`. It is a single file: data inline,
no server, opens from disk. The page is a view of `LEDGER.json`, never a second
source of truth. Rows and their kinds are model classifications; a quote alone
does not establish its author, target project or permission to implement it.

## What the page shows

- Every intent: id, date, topic, kind, subject, strength; the **verbatim quote**;
  the wider transcript excerpt (`--transcripts <dir>`, repeatable, the second
  host's files fetched first) with the quote highlighted.
- Both hosts' verdicts side by side, evidence as links to the pinned commit
  (`--blob-base https://github.com/<o>/<r>/blob/<sha>/`), the fleet's `gap`.
- Mechanical overrides (rule, reason) and the human decision if any.
- Filters: decision status, effective disposition, host agreement, strength,
  kind, subject, free text; sort by date, strength, id. Keyboard: ↑/↓, Esc.
- Header counts by effective disposition; "przejrzane N/total".

## Manual review

Per intent: **✓ agrees** · **↺ reclassifies** (pick a disposition) · **✕ drops**
(false lead, model voice, not this project) · comment. Review one filter at a
time: `landed-contested` first (check the runtime path and source context), then
`partial` with strength 5. Manual review changes the ledger; implementation
authorization comes from the active user task or plan.

## How decisions come back

The file is the canon; the page has two transports:

1. **Local file** — "Eksportuj decyzje (JSONL)" shows the lines; the user
   saves them as `decisions.jsonl` and runs
   `intents_cli.py <workdir> decide --import-file decisions.jsonl --by manual-review`.
   Nothing depends on a vendor.
2. **claude.ai artifact** — published with `capabilities: {db: {}, downloads:
true, user: {}}`, the same page saves each decision to the collection
   `decisions/<id>` live, for every viewer of the page. The agent reads them
   back with the artifact data tool and feeds `decide --import-file` a JSON dump
   of the collection. Same command, same override rows.

Either way the result is a row in `overrides.jsonl`:

```json
{
  "id": "s2-052",
  "host": "*",
  "from": "landed",
  "to": "absent",
  "verdict": "wrong",
  "by": "manual-review",
  "reason": "double control nie działa na drugiej maszynie",
  "at": "2026-09-19"
}
```

`host: "*"` means the decision overrides every fleet's verdict. A human row
always wins over a rule row; neither overwrites the fleet's original, which the
page keeps showing beside the decision.

## Regenerate, do not edit

After `decide`, run `render --html` again. Hand-editing `intents.html` is lost
on the next render and leaves the ledger untouched — the exact failure the
override layer exists to prevent.
