# Szablon — jeden scope = jeden agent canary

```text
You are a canary cataloger in repo {ROOT} (Living Tree: do not switch branch,
do not worktree, do not commit, do not stash).

SENSE was already done. Your ONLY scope id={SCOPE_ID}:
Paths (exclusive):
{PATH_LIST}

For every def/class/fn/struct/mod (language plugin rules) and each module file:
1. Read the file fully.
2. Catalog: one sentence of runtime role (not name paraphrase).
   authority=repo_verified|inferred
3. CANARY: if unit has NO docstring/rustdoc — add 1–3 lines, English, match neighbors.
   If docs exist — leave them (docstring_added=false).
4. NO logic/signature/import changes.
5. Run compile/lint only on files you touched; fix only your own mess.

Return JSON matching the catalog schema (role + authority REQUIRED for rust).
files_touched, notes (dead/twins/name-mismatch — honest, will be loct-cross-checked).
```
