# Brief: confront candidate intents with the repository

## Input

`lanes/<LANE>.json` contains candidate intents extracted from the plan's corpus
(transcripts and/or aicx). Every `quote` was checked as a verbatim source
substring. That lexical check establishes neither author, project nor authority.
Rows and their `kind`, including `decision`, are model classifications.
Interpret quotes in context; describe insufficient context in `gap`. The active
user task or plan supplies implementation authorization, never a quote alone.

Fields: `id, date, source, quote, topic, kind, subject, strength, _shard`.
`kind`: task / constraint / preference / decision / complaint / question.
`strength` 1–5 describes how strongly the source statement was expressed.

## Your task

Assign **one disposition** to every row against the current repo `{repo}`: the
cut's worktree pinned to the plan baseline. This path comes from the cut.

**DO NOT IMPLEMENT ANYTHING.** Do not edit repo files or commit. This is a state
review; implementation is a separate step. Write only your output JSON.

### Dispositions

- `landed`: the code does this today. **Evidence required**: `path:line` or a symbol.
- `partial`: partly implemented; describe the missing behavior in `gap`.
- `absent`: no code trace. Evidence must name the structural question and its empty result.
- `superseded`: a later intent in the lane replaces it; name `superseded_by` (id).
- `contradiction`: two intents conflict without a later resolution; name `conflicts_with` (id).
- `unclear`: source context cannot settle it; put the question for the user in `gap`.

### Output schema

Write a JSON array to the assigned path:

```json
[
  {
    "id": "s5-042",
    "disposition": "partial",
    "evidence": "core/quality/overlay_quality.rs:1004",
    "gap": "The threshold works during teach but does not filter entries before 2026-08-21."
  }
]
```

`evidence` is required for `landed`, `partial` and `absent`. Preserve every input
`id`; no row may disappear. Preserve raw source quotes in their original language.

## Structural inspection is required

**loctree-mcp is the first move, not the last.** Use `project={repo}`:

- `context`: one entry map
- `find` (symbols / where-symbol / literal): does the symbol or string exist?
- `slice <file>`: dependencies and consumers before a file verdict
- `impact <file>`: blast radius
- `follow dead`: does the code exist but have no reachable callers?

Structural questions answer who calls it, whether it is reachable and what a
change affects. `grep`/`rg` are for literal detail such as errors, paths and docs.

**A symbol existing does not prove working behavior.** A consumer may exist
without a producer. Before `landed`, inspect `follow dead` or find the caller.
Do not use evidence narrower than the claim.

## Final report

After saving JSON, write 8–12 sentences in English: disposition counts, the
three oldest `absent` candidates (date and original-language quote), the most
serious contradiction, and separately what could not be determined and why.
