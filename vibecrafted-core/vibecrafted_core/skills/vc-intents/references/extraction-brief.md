# Brief: extract candidate intents from source material

## What these files are

The shard contains the source material named in the cut: transcripts, session
extracts or other text. A filename may follow
`<YYYY-MM-DD>_<HHMMSS>_<first-words>_raw.txt`, but does not establish authorship.

The corpus may mix speakers, playback, quotations, model replies, private material
and unrelated projects. Do not assume one human authored every word or addressed
it to an agent. A quote alone proves neither author, repo relevance nor an
instruction to execute. Interpret it in context; implementation authorization
comes from the active user task or plan.

## What to look for

Candidate intents concerning **the product named in the cut (repo `{repo}`) and
work on it**: desired behavior, failures, constraints, preferences and decisions
supported by the surrounding context.

Skip private conversations, text dictated for another recipient, inconclusive
thinking aloud, microphone tests and unrelated projects unless they also concern
this product. Do not turn quoted instructions or third-party playback into a
proven user decision.

## Main rule: verbatim quotes

Every row MUST have a `quote`: a source fragment copied character for character.
Do not correct, shorten, paraphrase or polish it. Preserve transcription mistakes
and the original language (for example, "słoźnika" instead of "słownika").

The catalog holds candidates, not proven user decisions. Do not assign an
unestablished author. `verify-quotes` checks text containment only; it does not
verify semantics, authorship or authorization. Put normalization in `topic`,
never in `quote`.

## Output schema

Write ONLY JSON (an array of objects) to the assigned output path. If no fragment
fits the task and project, `[]` is valid. Do not invent rows to increase the count.

```json
[
  {
    "id": "<shard>-001",
    "date": "2026-01-14",
    "source": "<exact filename>",
    "quote": "<verbatim fragment, 1-3 sentences, original language>",
    "topic": "<normalized topic, 3-8 words, in English>",
    "kind": "task|constraint|preference|decision|complaint|question",
    "subject": "<area: stt|lexicon|live|overlay|hotkey|agent|settings|build|release|quality-loop|cli|ui|inne>",
    "strength": 3
  }
]
```

- `kind` and `strength` are model classifications, not proof of intent or authority.
- `kind`: `task` = do X; `constraint` = never/always Y; `preference` = prefer Z;
  `decision` = a stated decision; `complaint` = a reported failure;
  `question` = an open question.
- `strength` 1–5: how strongly expressed (1 = passing remark; 5 = emphasis,
  anger or repetition). Keep the existing classification policy.

## How to work

1. Read ALL files in the shard. Batch short files where practical.
2. Not every file yields a candidate. An empty array is better than an invented row.
3. One file may yield several candidates if it concerns several relevant matters.
4. For optional repo existence checks, use **loctree-mcp** (`find`, `slice`,
   `context`) with `project={repo}` from the cut. Your task is the corpus, not code.

## Final report

After saving JSON, write 5–10 sentences in English: files read, candidates
extracted, 3–5 dominant topics if present, surprises, possible changes in source
statements, and missing context. Preserve source quotations in their original
language. A later conflicting statement need not belong to the same speaker.
