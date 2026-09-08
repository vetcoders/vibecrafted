#!/usr/bin/env bash

spawn_write_command_script() {
  local script_path="$1"
  local command_text="$2"
  local shell_bin

  shell_bin="$(spawn_preferred_shell)"
  mkdir -p "$(dirname "$script_path")"
  # shellcheck disable=SC2016
  printf '#!/usr/bin/env bash
set -euo pipefail
%s -lc %s
' \
    "$(spawn_shell_quote "$shell_bin")" \
    "$(spawn_shell_quote "$command_text")" \
    > "$script_path"
  chmod +x "$script_path"
}

spawn_frontmatter_field() {
  local source_file="$1"
  local field_name="$2"

  "$(spawn_python_bin)" - "$source_file" "$field_name" <<'PY'
import pathlib
import sys

source_file, field_name = sys.argv[1:3]
try:
    text = pathlib.Path(source_file).read_text(encoding="utf-8")
except OSError:
    raise SystemExit(0)

lines = text.splitlines()
if not lines or lines[0].strip() != "---":
    raise SystemExit(0)

for line in lines[1:]:
    if line.strip() == "---":
        break
    if ":" not in line:
        continue
    key, value = line.split(":", 1)
    if key.strip() != field_name:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(value, end="")
    raise SystemExit(0)
PY
}

# Shared status reader for marbles loop/watcher: returns the report's
# frontmatter `status` field, or nothing when the report is absent.
_report_frontmatter_status() {
  local report_path="$1"
  [[ -f "$report_path" ]] || return 0
  spawn_frontmatter_field "$report_path" "status"
}

spawn_report_template_untouched() {
  local report_path="$1"
  local marker status finalized claim

  [[ -s "$report_path" ]] || return 1
  marker="$(spawn_frontmatter_field "$report_path" "launcher_template")"
  [[ "$marker" == "true" ]] || return 1
  status="$(spawn_frontmatter_field "$report_path" "status")"
  [[ "$status" == "pending-unset" ]] || return 1
  finalized="$(spawn_frontmatter_field "$report_path" "finalized")"
  case "$finalized" in
    1|true|yes|on) return 1 ;;
  esac
  claim="$(spawn_frontmatter_field "$report_path" "claim")"
  [[ -z "$claim" ]] || return 1

  # The worker may append evidence without touching the template fields. Body
  # content still counts as a deliberate write; the Python finalizer removes
  # the marker and stamps the discovered child session after command closure.
  if awk '
    BEGIN { body=0; in_front=0 }
    NR==1 && $0=="---" { in_front=1; next }
    in_front && $0=="---" { in_front=0; next }
    in_front { next }
    NF { body=1 }
    END { exit body ? 0 : 1 }
  ' "$report_path"; then
    return 1
  fi
  return 0
}

spawn_strip_frontmatter_to_file() {
  local source_file="$1"
  local target_file="$2"

  "$(spawn_python_bin)" - "$source_file" "$target_file" <<'PY'
import pathlib
import sys

source_file, target_file = sys.argv[1:3]
text = pathlib.Path(source_file).read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)

if lines and lines[0].strip() == "---":
    body_start = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = idx + 1
            break
    if body_start is not None:
        text = "".join(lines[body_start:]).lstrip("\n")

pathlib.Path(target_file).write_text(text, encoding="utf-8")
PY
}

spawn_write_frontmatter() {
  local target_file="$1"
  local agent="${2:-${SPAWN_AGENT:-unknown}}"
  local model="${3:-${SPAWN_MODEL:-unknown}}"
  local status="${4:-pending}"

  cat > "$target_file" <<EOF_FM
---
run_id: ${SPAWN_RUN_ID:-unknown}
prompt_id: ${SPAWN_PROMPT_ID:-unknown}
agent: $agent
skill: ${SPAWN_SKILL_CODE:-unknown}
model: $model
status: $status
session_id: ${SPAWN_SESSION_ID:-pending}
finalized: false
repo_path: ${SPAWN_ROOT:-unknown}
tokens_input: 0
tokens_output: 0
tokens_total: 0
cost_usd: unknown
---

EOF_FM
}

spawn_append_prompt_body() {
  local source_file="$1"
  local target_file="$2"

  # Strip top-level metadata before handing the task body to the model. Runtime
  # metadata belongs in artifacts, not in the worker's execution prompt.
  awk \
    '\
    BEGIN { in_fm=0; fm_done=0; }
    NR==1 && /^---[ 	]*$/ { in_fm=1; next; }
    in_fm && /^---[ 	]*$/ { in_fm=0; fm_done=1; next; }
    in_fm { next; }
    { print }
  ' "$source_file" >> "$target_file"
}

spawn_build_research_runtime_prompt() {
  local source_file="$1"
  local runtime_file="$2"
  local report_path="$3"
  local agent="${4:-${SPAWN_AGENT:-agent}}"
  local model="${5:-${SPAWN_MODEL:-unknown}}"
  local run_id="${SPAWN_RUN_ID:-unknown}"
  local prompt_id="${SPAWN_PROMPT_ID:-unknown}"

  cat > "$runtime_file" <<'EOF_RESEARCH_PROMPT'
# Research Task

EOF_RESEARCH_PROMPT

  spawn_append_prompt_body "$source_file" "$runtime_file"

  cat >> "$runtime_file" <<EOF_RESEARCH_CONTRACT

---
## Execution
- Execute the research plan directly.
- Ground findings in primary sources, repository evidence, or clearly labeled inference.
- If evidence conflicts, call out the conflict explicitly.
- Write the final markdown report to the exact path below.

## Research Safety Contract
- Research mode is read-only for the source repo. Closure marker = the report
  filesystem artifact (report.md + meta.json + transcript.log under the run
  directory). No git writes are needed; operator verifies completion via
  filesystem.
- **SOURCE MUTATION**: forbidden unless the operator plan explicitly asks for source modifications. Do not edit repo source files, config, .gitignore, generated files, or cleanup stray files from this research worker.
- **GIT WRITES forbidden**: do not stage, commit, amend, tag, branch, merge,
  rebase, push, stash, clean, reset, checkout, switch. Working tree must be
  unchanged at the end of the run.
- If you discover a fix, describe it in the report instead of implementing it.

Report path: $report_path

## Report Frontmatter
Start the report with this frontmatter, changing status to failed if the research cannot be completed:

---
run_id: $run_id
prompt_id: $prompt_id
agent: $agent
skill: ${SPAWN_SKILL_CODE:-rsch}
model: $model
status: completed
session_id: pending
finalized: false
claim:
repo_path: ${SPAWN_ROOT:-unknown}
tokens_input: 0
tokens_output: 0
tokens_total: 0
cost_usd: unknown
---
EOF_RESEARCH_CONTRACT

  if [[ "$agent" == "codex" ]]; then
    cat >> "$runtime_file" <<'EOF_CODEX_RESEARCH'

## Codex Report Write Contract
- You are launched through `codex exec --output-last-message`, so your final assistant message is NOT the durable research artifact.
- Before exiting, write the COMPLETE markdown report to the exact `Report path` above using a shell command such as a heredoc (`cat > "$REPORT_PATH" <<'EOF' ... EOF`) or an equivalent filesystem write.
- The report file itself must contain the full frontmatter, findings, evidence, synthesis, and open questions. Do not rely on streamed intermediate messages or the final assistant message to carry report content.
- After writing, verify the file exists and is non-trivial with `wc -c "$REPORT_PATH"` and a short `sed -n '1,40p' "$REPORT_PATH"` check.
- Your final assistant message may be a short completion note, but it must not be the only place where the report exists.
EOF_CODEX_RESEARCH
  fi
}

spawn_build_runtime_prompt() {
  local source_file="$1"
  local runtime_file="$2"
  local report_path="$3"
  local agent="${4:-${SPAWN_AGENT:-agent}}"
  local model="${5:-${SPAWN_MODEL:-unknown}}"
  local skill_name="${SPAWN_SKILL_NAME:-${VIBECRAFTED_SKILL_NAME:-}}"

  if [[ "$skill_name" == "research" || "${SPAWN_SKILL_CODE:-}" == "rsch" || "${VIBECRAFTED_RESEARCH_MODE:-0}" == "1" ]]; then
    spawn_build_research_runtime_prompt "$source_file" "$runtime_file" "$report_path" "$agent" "$model"
    return 0
  fi

  spawn_write_frontmatter "$runtime_file" "$agent" "$model" "prompt"

  # Strip existing frontmatter (so we don't have double) and append the plan
  spawn_append_prompt_body "$source_file" "$runtime_file"

  # shellcheck disable=SC2129
  cat >> "$runtime_file" <<EOF_LABEL
---
## VC Agents Worker Charter
- You are a spawned vc-agents worker: an execution unit, not orchestration authority.
- **Native in-process delegation is allowed.** You MAY use the Task tool (and the \`vc-delegate\` skill that wraps it) to spawn local subagents inside your current process — for parallelization, self-review, bounded research, or splitting independent work streams. Those subagents run in your context window, on the same model tier, and return their results to you. They are productivity, not escalation.
- **External fleet escalation is forbidden.** Do NOT invoke vc-agents from inside the worker, do NOT launch another claude/codex/gemini fleet, and do NOT reopen frontier selection. That escalation path is operator-only.
- The operator already made the vc-why-matrix choice for this mission; do not reinterpret it.
- Implementation scope (architecture, refactor, new modules, broad cuts) is bounded by the dispatched plan, not by this charter. If the plan calls for deep architectural work, do it. If it calls for a surgical fix, stay surgical. Read the plan, not the charter, for scope.
- If the task reveals a wider unresolved surface, complete the assigned mission as far as honestly possible and record the boundary clearly in your report.

## Layered Reading Discipline (NON-NEGOTIABLE)

When any tool returns truncation warning + "see file: <path>" fallback because
its output exceeded the runtime token cap, you MUST read the full file via
layered slicing. Do not skip. Do not summarize from the warning text or the
truncated preview. The whole point of the dump file is full evidence — using
only the warning defeats the workflow.

Concrete procedure:
- Use the Read tool with offset/limit in spans of ~1500-2000 lines until you
  have covered the entire file.
- Or use Bash with python3: \`python3 -c "print(open(P).read()[A:B])"\` in spans
  of ~80,000 chars until you have covered all bytes.
- For very large files (>500KB) consider running grep / structured search
  inside the dump first to locate relevant regions, then layer-read those
  regions in full — but never substitute grep summary for actual reading
  of the regions you're going to cite or rely on.
- In your report, explicitly state how many spans you read and the total
  coverage (e.g. "Read codex_report.md in 4 spans of 1800 lines each, total
  7200 lines, 100% coverage"). This makes the operator audit trivial.

Forbidden: writing analysis based on the truncation warning text, the first
2KB preview, or the file's filename alone. If you do not have time to read
the dump file, explicitly halt and report the boundary — do NOT fabricate
coverage. Operator can re-dispatch with a tighter scope.
EOF_LABEL

  cat >> "$runtime_file" <<'EOF_IMPLEMENT'
## Exit Contract
- **REPORT**: mandatory. Write to the report path given at the end of this prompt.
  Filesystem artifact (report.md + meta.json + transcript.log) is the closure
  marker. Operator verifies completion via filesystem, not git.
- **COMMIT**: only if you produced staged changes that match the dispatched
  scope. Regular commit with detailed message.
  - NO empty commits. NO `--allow-empty`. NO chore stamps.
  - If you have nothing to stage, do not commit. Report stands alone.
  - Forbidden: `git push` to remote (operator publishes).
  - Forbidden: branch switch, worktree, stashing other agents' WIP.
- **SCOPE**: do your work, write report, optionally commit if real changes,
  stop.
EOF_IMPLEMENT

  cat >> "$runtime_file" <<EOF_PROMPT

At the end of the task, write your final human-readable report to this exact path:
Report path: $report_path

Keep streaming useful progress to stdout while you work. The launcher has
already created the report file with machine-owned run/session identity and
\`finalized: false\`. Edit that report in place: preserve its identity fields,
change status to \`completed\` or \`failed\`, and add the human-readable body.
Only when you believe the run succeeded, deliberately change
\`finalized: false\` to \`finalized: true\` and add a non-empty one-line
\`claim:\`. If you cannot write a standalone report, finish normally and let
the transcript act as the fallback artifact.
EOF_PROMPT
}
