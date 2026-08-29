# Research Synthesis Document Template

> Operator's expert interpretation of the three source reports. Each non-trivial
> claim cites file:line in the source reports. The reports themselves remain
> as immutable expert testimony in separate files — read them directly when
> you want the full unfiltered evidence.

## File location

Write the synthesis to:
`$VIBECRAFTED_HOME/artifacts/<org>/<repo>/<YYYY_MMDD>/research/<run_id>/synthesis.md`

The three source reports remain as individual files in the same directory. **Do not inline them.**

## Frontmatter

```yaml
---
run_id: <generated-unique-id>
skill: vc-research
project: <repo-name>
status: completed
operator_synthesis_by: <claude|codex|gemini|cursor|operator>
source_reports:
  - claude_<run_id>.md
  - codex_<run_id>.md
  - gemini_<run_id>.md
---
```

## Section structure

### 0. Coverage statement

Each source report below was read in full via layered slicing before synthesis was written:

- `claude_<run_id>.md`: <N> lines / <K>KB, 100% read in <M> spans
- `codex_<run_id>.md`: <N> lines / <K>KB, 100% read in <M> spans
- `gemini_<run_id>.md`: <N> lines / <K>KB, 100% read in <M> spans

If any report could not be read in full, this synthesis MUST halt at section 0 with an explicit boundary statement. Citing line ranges from an unread report is forbidden.

### 1. Problem

<from Step 1 — the actual question, not the symptom>

### 2A. Convergent findings (deduplicated — one statement per finding)

> Findings where two or three experts agreed. Reduced to a single statement per finding. Source refs cite the agreeing reports.

#### F1: <finding statement, in operator's voice>

**Sources**: `claude_<run_id>.md:L42-58`, `codex_<run_id>.md:L101-115`
**Not addressed by**: Gemini (or "all three"; or omit if all addressed)
**Operator note**: <one-line nuance if it sharpens, otherwise omit>

#### F2: ...

### 2B. Signals (single-agent findings — POTENTIAL key insights)

> Findings surfaced by only one of the three agents. NOT lower-priority than convergent — often these are the actual direction the work needed to take. Each signal gets named, cited, and adjudicated individually.

#### S1: <signal statement, in the agent's voice>

**Source**: `gemini_<run_id>.md:L78-92`
**Why others missed it**: <claude did not address Q3; codex addressed it but gave wrong answer because…; cursor timed out; etc.>
**Operator's verdict**: **amplify** | **flag for follow-up** | **acknowledge & reject**
**Reasoning**: <if amplify: why the signal is right and convergent view incomplete. If flag: what experiment / further research would resolve it. If reject: what specifically in the signal's reasoning fails, with reference to repo evidence or named external knowledge — never handwave.>

#### S2: ...

### 4. Architecture Decision

- **Chosen approach**: <operator's decision>
- **Why**: <reasoning citing specific findings via file:line>
- **Alternatives rejected**:
  - <alternative> — rejected per `<file>:Lxx-yy` because <reasoning>

### 5. Implementation Notes

- <concrete guidance — cite source for each non-trivial item>
- <API signature: see `codex_<run_id>.md:L130-145` for verified syntax>

### 6. Remaining Gaps

- <questions none of the three could answer — cite where each agent gave up>
- <areas needing hands-on experimentation>

### 7. How to Read This

- This synthesis is **operator's expertise**. The three reports it cites remain as standalone artifacts in this directory — open them when you want the full unfiltered text from each agent.
- File:line refs are absolute to the report file (e.g. `claude_<run_id>.md:L42-58` means lines 42-58 inclusive in that file).
- If you disagree with operator's judgment, the source reports are right there — read them and form your own view. That's what they are for.

## Operator imperatives (kategoryczny)

1. **Synthesis NIE zawiera verbatim treści raportów** — tylko cytaty file:line do nich. Synthesis to **opinia eksperta** (operatora), nie copy-paste.
2. **Reports zostają jako osobne pliki** w run directory. Są immutable expert testimony — pełna treść, pełen frontmatter, oryginalny styl.
3. **Każda nietrywialna teza w synthesis MUSI mieć file:line ref** do co najmniej jednego raportu. Brak refa = anti-pattern (operator zmyśla).
4. **Dissent jest cytowany z file:line do obu/wszystkich stron** + reasoned judgment operatora dlaczego jedna strona przeważa.
5. Synthesis jest krótki (zwykle 3-8KB). Wartość = jakość interpretacji + precyzja cytowania, nie objętość.

## Anti-patterns (explicit)

- ❌ "patchwork meta-artifact" — verbatim concatenation of 3 reports glued together. Becomes 30-50KB monolith with duplicate frontmatter, hard to read, hard to ingest, no real synthesis.
- ❌ "compressed view" — operator reads 3 reports, paraphrases to one short synthesis, publishes only that. Individual findings get crushed; reader loses the option to verify a specific claim against its source.

## Mandatory pattern (operator decision 2026-05-01)

- ✓ Synthesis is **a separate, concise document** that interprets the three reports and points to **exact lines** in them for every non-trivial claim.
- ✓ Reports stay as **individual artifacts**. They are immutable expert testimony — full content, full frontmatter, original style.
- ✓ Synthesis cites them with file:line refs (e.g. `claude_<run_id>.md:L42-58`). Reader who wants to verify clicks the ref and reads the full source paragraph. Operator does not paraphrase the expert; operator points at the expert.
- ✓ When reports disagree, synthesis notes the dissent **with file:line refs to each side** and gives the operator's reasoned judgment.

## Voting / majority rules — explicitly rejected

Two distinct sections: **A. Convergent findings (deduplicated)** and **B. Signals (single-agent findings, potentially key insights)**. A finding surfaced by only one agent is **not less important** than a convergent one — it is a **signal** that one expert saw something the others missed. In our experience these single-agent findings are often the actual direction the work needed to take.

For every signal, the operator writes:

- **what the signal says** (cited by file:line)
- **why the other two missed it** (didn't address the question, gave a wrong answer, ran a weaker search strategy, etc.)
- **operator's signal verdict**: **amplify** / **flag** / **acknowledge & reject** (with reasoning)

Signals never get hidden in "consensus" or "minority" framing. They get a **dedicated section** in the synthesis where each one is named, cited, and adjudicated by the operator individually.
