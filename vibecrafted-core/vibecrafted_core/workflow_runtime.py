"""Supervised runtime for research/marbles/polarize workflows: spawns child
agent processes, tracks their durable artifacts, and writes the parent report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .model_overrides import _with_model_override
from .package_resources import package_root
from .research_config import (
    SUPPORTED_RESEARCH_AGENTS,
    ResearchAgentSelection,
    resolve_research_runtime_config,
)
from .runtime_paths import agent_tool_search_path
from .spawn import _resolve_agent_command, _stdin_command
from .supervisor_async import AsyncRunHandle, AsyncSupervisor


@dataclass(frozen=True)
class ChildResult:
    """Durable outcome of one supervised child agent run (research lane, marbles
    loop iteration, or synthesis), as recorded from its meta.json / handle.
    """

    label: str
    agent: str
    run_id: str
    agent_session_id: str
    agent_model: str
    model_requested: str
    model_override_supported: bool
    model_override_skipped: bool
    model_override_skip_reason: str
    report: Path
    transcript: Path
    exit_code: int | None
    artifact_ok: bool
    artifact_errors: tuple[str, ...]
    tokens_input: int = 0
    tokens_cached_input: int = 0
    tokens_cache_write: int | None = None
    tokens_output: int = 0
    cost_usd: float | None = None
    resume_command: str = ""
    completed_at: str = ""


def _parent_run_id() -> str:
    """The parent supervised run's id, from `VIBECRAFTED_RUN_ID` (falls back to
    a fixed placeholder outside a real run)."""
    return os.environ.get("VIBECRAFTED_RUN_ID", "workflow-runtime")


def _parent_report_path() -> Path:
    return Path(os.environ["VIBECRAFTED_REPORT_PATH"]).expanduser()


def _parent_meta_path() -> Path:
    return Path(os.environ["VIBECRAFTED_META_PATH"]).expanduser()


def _child_dir() -> Path:
    """Directory for this run's child artifacts, created on first access."""
    base = _parent_report_path().parent / f"{_parent_run_id()}-children"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_label(label: str) -> str:
    """Filesystem-safe form of a label: non-alnum runs become `-`, trimmed."""
    return "".join(ch if ch.isalnum() else "-" for ch in label).strip("-")


def _slug(value: str, fallback: str) -> str:
    """Lowercase, hyphenated, 64-char-capped slug of `value`; `fallback` if empty."""
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", value.lower()).strip("-")
    raw = raw[:64].strip("-")
    return raw or fallback


def _artifact_ts() -> str:
    """Artifact date stamp: `VIBECRAFTED_ARTIFACT_TS` override, else today's UTC date."""
    return os.environ.get("VIBECRAFTED_ARTIFACT_TS") or datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")


def _artifact_slug(prompt: str) -> str:
    """Artifact slug: `VIBECRAFTED_ARTIFACT_SLUG` override, else slugified prompt."""
    return os.environ.get("VIBECRAFTED_ARTIFACT_SLUG") or _slug(
        prompt, _parent_run_id()
    )


def _artifact_suffix() -> str:
    return os.environ.get("VIBECRAFTED_ARTIFACT_SUFFIX", "")


def _research_artifact_agent(label: str, agent: str) -> str:
    """Agent token used in a research artifact's canonical filename."""
    if label == "research-synthesis":
        return "synthesis"
    if agent:
        return agent
    if label.startswith("research-"):
        return label.removeprefix("research-")
    return _safe_label(label) or "research"


def _canonical_research_dir() -> Path | None:
    """Canonical research artifact directory from env, created if configured;
    `None` when unset, signaling callers to fall back to the per-run child dir.
    """
    raw = os.environ.get("VIBECRAFTED_CANONICAL_REPORT_DIR", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _research_artifact_paths(
    *, label: str, agent: str, prompt: str
) -> tuple[Path, Path, Path]:
    """Report/transcript/meta paths for a research child: canonical dated stem
    when a canonical dir is configured, else plain label-named files in the
    per-run child dir.
    """
    base = _canonical_research_dir()
    if base is None:
        child_base = _child_dir()
        safe_label = _safe_label(label)
        return (
            child_base / f"{safe_label}.md",
            child_base / f"{safe_label}.transcript.log",
            child_base / f"{safe_label}.meta.json",
        )
    stem = (
        f"{_artifact_ts()}_"
        f"{_slug(_research_artifact_agent(label, agent), 'agent')}_"
        f"{_artifact_slug(prompt)}_report"
        f"{_artifact_suffix()}"
    )
    return (
        base / f"{stem}.md",
        base / f"{stem}.transcript.log",
        base / f"{stem}.meta.json",
    )


def _child_artifact_paths(
    *, kind: str, label: str, agent: str, prompt: str
) -> tuple[Path, Path, Path, Path]:
    """Report/transcript/meta/prompt paths for any child: research kind uses the
    canonical-or-child-dir research layout, other kinds use the plain child dir.
    """
    safe_label = _safe_label(label)
    if kind == "research":
        report, transcript, meta = _research_artifact_paths(
            label=label, agent=agent, prompt=prompt
        )
        prompt_file = _child_dir() / f"{safe_label}.prompt.md"
        return report, transcript, meta, prompt_file
    base = _child_dir()
    return (
        base / f"{safe_label}.md",
        base / f"{safe_label}.transcript.log",
        base / f"{safe_label}.meta.json",
        base / f"{safe_label}.prompt.md",
    )


def _child_env(
    agent: str,
    report: Path,
    transcript: Path,
    meta: Path,
    model_requested: str = "",
) -> dict[str, str]:
    """Child process env: agent + artifact paths, plus model override if requested."""
    env = os.environ.copy()
    env["VIBECRAFTED_AGENT"] = agent
    env["VIBECRAFTED_REPORT_PATH"] = str(report)
    env["VIBECRAFTED_TRANSCRIPT_PATH"] = str(transcript)
    env["VIBECRAFTED_META_PATH"] = str(meta)
    env["PATH"] = agent_tool_search_path(env)
    if model_requested:
        env["VIBECRAFTED_MODEL_REQUESTED"] = model_requested
    return env


def _tee_enabled() -> bool:
    return os.environ.get("VIBECRAFTED_TEE_OUTPUT") == "1"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """Write `payload` as pretty-printed UTF-8 JSON, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _optional_int(value: object) -> int | None:
    """Best-effort int coercion (bool/int/whole-float/digit-string); `None` otherwise."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _optional_float(value: object) -> float | None:
    """Best-effort float coercion (int/float/numeric string); `None` for bool/junk."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _tokens_total(
    input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> int:
    """Sum usage without double-counting provider-specific cache shapes.

    Claude/Codex: ``input`` already includes cache hits (cached ≤ input).
    Junie-style: ``input`` is non-cached only and ``cached`` is additive
    (cached can exceed input). Detect by comparing magnitudes.
    """
    inp = max(0, int(input_tokens or 0))
    cached = max(0, int(cached_input_tokens or 0))
    out = max(0, int(output_tokens or 0))
    if cached and cached > inp:
        return inp + cached + out
    return inp + out


def _child_tokens_total(result: ChildResult) -> int:
    """Total token count for one child result, via `_tokens_total`."""
    return _tokens_total(
        result.tokens_input,
        result.tokens_cached_input,
        result.tokens_output,
    )


def _sum_cache_write(results: Sequence[ChildResult]) -> int | None:
    """Sum `tokens_cache_write` across results that report it; `None` if none do."""
    values = [
        item.tokens_cache_write
        for item in results
        if item.tokens_cache_write is not None
    ]
    return sum(values) if values else None


def _sum_cost_usd(results: Sequence[ChildResult]) -> float | None:
    """Sum `cost_usd` across results that report it, rounded to 6 places."""
    values = [item.cost_usd for item in results if item.cost_usd is not None]
    return round(sum(values), 6) if values else None


def _runtime_model_requested() -> str:
    """The model requested for this run, from `VIBECRAFTED_MODEL_REQUESTED`."""
    return str(os.environ.get("VIBECRAFTED_MODEL_REQUESTED") or "").strip()


def _remember_runtime_model_request(model_requested: str) -> str:
    """Stash a non-empty requested model into the process env for later readers."""
    requested = str(model_requested or "").strip()
    if requested:
        os.environ["VIBECRAFTED_MODEL_REQUESTED"] = requested
    return requested


def _result_meta(result: ChildResult) -> dict[str, object]:
    """Serialize one `ChildResult` into the JSON shape written to meta.json."""
    payload: dict[str, object] = {
        "label": result.label,
        "agent": result.agent,
        "run_id": result.run_id,
        "agent_session_id": result.agent_session_id,
        "agent_model": result.agent_model,
        "model_requested": result.model_requested,
        "model_override_supported": result.model_override_supported,
        "model_override_skipped": result.model_override_skipped,
        "report": str(result.report),
        "transcript": str(result.transcript),
        "exit_code": result.exit_code,
        "artifact_ok": result.artifact_ok,
        "artifact_errors": list(result.artifact_errors),
        "tokens_input": result.tokens_input,
        "tokens_cached_input": result.tokens_cached_input,
        "tokens_output": result.tokens_output,
        "tokens_total": _child_tokens_total(result),
        "cost_usd": result.cost_usd if result.cost_usd is not None else "unknown",
        "resume_command": result.resume_command,
        "completed_at": result.completed_at,
    }
    if result.tokens_cache_write is not None:
        payload["tokens_cache_write"] = result.tokens_cache_write
    if result.model_override_skip_reason:
        payload["model_override_skip_reason"] = result.model_override_skip_reason
    return payload


def _parent_receipt(results: Sequence[ChildResult]) -> dict[str, object]:
    """Aggregate token/cost accounting across all children into the parent receipt."""
    tokens_input = sum(result.tokens_input for result in results)
    tokens_cached_input = sum(result.tokens_cached_input for result in results)
    tokens_output = sum(result.tokens_output for result in results)
    receipt: dict[str, object] = {
        "session_id": "aggregated" if results else "",
        "tokens_input": tokens_input,
        "tokens_cached_input": tokens_cached_input,
        "tokens_output": tokens_output,
        "tokens_total": _tokens_total(tokens_input, tokens_cached_input, tokens_output),
        "cost_usd": _sum_cost_usd(results),
        "cost_source": "children_sum" if results else "none",
    }
    cache_write = _sum_cache_write(results)
    if cache_write is not None:
        receipt["tokens_cache_write"] = cache_write
    model_requested = _runtime_model_requested()
    if model_requested:
        receipt["model_requested"] = model_requested
    return receipt


def _parent_footer(run_id: str, status: str, receipt: dict[str, object]) -> list[str]:
    """Render the machine-parsed `run_closure:` YAML footer block for the parent report."""
    cost = receipt.get("cost_usd")
    lines = [
        "",
        f"<!-- vibecrafted-artifact-footer:{run_id} -->",
        "---",
        "run_closure:",
        f"  run_id: {run_id}",
        f"  session_id: {receipt.get('session_id') or 'aggregated'}",
        f"  tokens_input: {receipt.get('tokens_input', 0)}",
        f"  tokens_cached_input: {receipt.get('tokens_cached_input', 0)}",
    ]
    if receipt.get("tokens_cache_write") is not None:
        lines.append(f"  tokens_cache_write: {receipt.get('tokens_cache_write')}")
    if receipt.get("model_requested"):
        lines.append(f"  model_requested: {receipt.get('model_requested')}")
    lines.extend(
        [
            f"  tokens_output: {receipt.get('tokens_output', 0)}",
            f"  tokens_total: {receipt.get('tokens_total', 0)}",
            f"  cost_usd: {cost if cost is not None else 'unknown'}",
            f"  cost_source: {receipt.get('cost_source', 'none')}",
            f"  status: {status}",
            f"  completed_at: {datetime.now(timezone.utc).isoformat()}",
            '  resume_hint: ""',
            "---",
            "",
        ]
    )
    return lines


def _read_prompt_file(path: str) -> str:
    """Read a prompt file's text; on read failure, return a fallback instruction
    naming the path instead of raising."""
    if not path:
        return ""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"Read the requested prompt file yourself: {path}"


def _repo_root() -> Path:
    return package_root()


def _user_config_path() -> Path:
    """Per-user config.toml path under `$XDG_CONFIG_HOME` (or `~/.config`)."""
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home.expanduser() / "vibecrafted" / "config.toml"


def _manifest_config_paths() -> tuple[Path, ...]:
    """Candidate `install.toml` locations (VIBECRAFTED_ROOT, repo root, tools
    home), de-duplicated in precedence order."""
    roots = [
        Path(os.environ["VIBECRAFTED_ROOT"]).expanduser()
        if os.environ.get("VIBECRAFTED_ROOT")
        else None,
        _repo_root(),
        (
            Path(os.environ["VIBECRAFTED_TOOLS_HOME"]).expanduser()
            / "vibecrafted-current"
            if os.environ.get("VIBECRAFTED_TOOLS_HOME")
            else None
        ),
    ]
    paths: list[Path] = []
    for root in roots:
        if root is None:
            continue
        path = root / "install.toml"
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def research_agent_selection() -> ResearchAgentSelection:
    """Resolve the live research agent selection (env/config/manifest/builtin)."""
    return resolve_research_runtime_config()


# Gate-nap prevention (docs/runtime/AGENT_OPS.md, Class 1): dispatched workers
# are never re-invoked when their own background tasks complete, so a worker
# that ends its turn "waiting for the gate signal" hangs forever while its run
# reports completed. Explaining the mechanics beats a bare prohibition — the
# ban alone was broken in the wild with the affordance still visible.
WORKER_SIGNAL_DISCIPLINE = (
    "- You are a dispatched worker: background-task completions will NEVER wake\n"
    "  you or re-invoke you. Never end your turn waiting for a signal, monitor,\n"
    "  or background gate. Run quality gates (tests, builds) synchronously in\n"
    "  the foreground and finish everything — work, report, commits — within\n"
    "  this turn.\n"
)


def _child_prompt(kind: str, label: str, root: str, prompt: str) -> str:
    """Compose the full prompt handed to a supervised child worker: contract
    boilerplate, worker-signal discipline, marbles blindness note if applicable,
    then the operator prompt.
    """
    marbles_blindness = ""
    if kind == "marbles":
        marbles_blindness = (
            "- You are intentionally blind to prior marbles runs.\n"
            "- Do not read sibling child reports/transcripts unless the operator "
            "prompt explicitly names them.\n"
        )
    return f"""You are running as a supervised Vibecrafted {kind} worker.

Contract:
- Work in repository root: {root}
- Skill: vc-{kind}
- Track: {label}
- Do not launch external agent fleets.
- Write your durable report to VIBECRAFTED_REPORT_PATH.
- Let stdout/stderr form VIBECRAFTED_TRANSCRIPT_PATH.
{WORKER_SIGNAL_DISCIPLINE}{marbles_blindness}
Operator prompt:
{prompt}
"""


def _loop_prompt(kind: str, prompt: str, index: int, count: int, depth: int) -> str:
    """Append this loop iteration's instruction (polarize vs. marbles wording)
    to the base operator prompt."""
    if kind == "polarize":
        instruction = (
            f"Polarize loop: L{index}/{count}. Depth target: {depth}. "
            "Start fresh against the current workspace state, strip back marbles "
            "excess, reject competing axes, and choose one runtime truth."
        )
    else:
        instruction = (
            f"Marbles loop: L{index}/{count}. Depth target: {depth}. "
            "Start fresh against the current workspace state, find what is still wrong, "
            "over-correct deliberately, and report the next truth."
        )
    return f"{prompt}\n\n{instruction}"


def _research_synthesis_prompt(
    root: str, prompt: str, results: Sequence[ChildResult]
) -> str:
    """Compose the synthesis worker's prompt, citing each surviving lane's report path."""
    reports = "\n".join(
        f"- {result.agent}: {result.report}" for result in results if result.report
    )
    return f"""You are producing the objective vc-research synthesis from completed lanes.

Contract:
- Work in repository root: {root}
- This is not new research; synthesize only from the completed research reports.
- Read every source report fully before citing it.
- Write the synthesis report to VIBECRAFTED_REPORT_PATH.
- Use concise file:path citations to source reports; do not inline full reports.
- Surface convergent findings, single-agent signals, disagreements, and the operator-ready recommendation.

Original operator prompt:
{prompt}

Research reports:
{reports}
"""


NATIVE_RESUME_AGENTS = frozenset({"claude", "codex", "grok"})


def native_resume_argv(agent: str, agent_session_id: str) -> list[str]:
    """Build a provider-native resume argv without shell interpretation.

    Only adapters verified by the runtime contract live here.  In particular,
    a generic ``session_id`` is never accepted as provider identity and
    unsupported agents never fall back to a fresh-session command.
    """

    normalized_agent = str(agent or "").strip().lower()
    native_id = str(agent_session_id or "").strip()
    if not native_id:
        raise ValueError("missing_agent_session_id")
    if normalized_agent == "claude":
        return [
            "claude",
            "--resume",
            native_id,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
    if normalized_agent == "codex":
        return [
            "codex",
            "exec",
            "resume",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            native_id,
            "-",
        ]
    if normalized_agent == "grok":
        return [
            "grok",
            "--resume",
            native_id,
            "--cwd",
            ".",
            "--permission-mode",
            "bypassPermissions",
            "--no-alt-screen",
            "--output-format",
            "streaming-json",
            "--prompt-file",
            "/dev/stdin",
        ]
    raise ValueError(f"native_resume_unsupported:{normalized_agent or 'unknown'}")


def _resume_stdin_command(agent: str, agent_session_id: str) -> list[str]:
    """Compatibility shim for the strict provider-native resume builder.

    Callers must supply an already verified native identity.  The shim performs
    no metadata lookup and therefore cannot promote a legacy ``session_id``.
    """

    return native_resume_argv(agent, agent_session_id)


async def _run_child(
    *,
    kind: str,
    label: str,
    agent: str,
    root: str,
    prompt: str,
    model_requested: str = "",
    command: Sequence[str] | None = None,
    prompt_body: str | None = None,
) -> ChildResult:
    """Spawn and await one supervised child agent process, writing its prompt
    file, resolving its command, applying a requested model pin once, and
    returning the collected `ChildResult`.
    """
    safe_label = _safe_label(label)
    run_id = f"{_parent_run_id()}-{safe_label}"
    report, transcript, meta, prompt_file = _child_artifact_paths(
        kind=kind,
        label=label,
        agent=agent,
        prompt=prompt,
    )
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        prompt_body or _child_prompt(kind, label, root, prompt), encoding="utf-8"
    )
    child_command = _with_model_override(
        agent,
        command if command is not None else _stdin_command(agent),
        model_requested,
    )
    child_env = _child_env(agent, report, transcript, meta, model_requested)
    child_command = _resolve_agent_command(agent, child_command, child_env)
    if _tee_enabled():
        print(f"\n===== {kind}:{label}:{agent} =====", flush=True)
    handle: AsyncRunHandle = await AsyncSupervisor().run(
        run_id=run_id,
        command=child_command,
        root=root,
        env=child_env,
        meta_path=meta,
        report_path=report,
        transcript_path=transcript,
        prompt_file_path=prompt_file,
        require_report=True,
        require_transcript_output=False,
        tee_output=_tee_enabled(),
    )
    validation = handle.artifact_validation
    return ChildResult(
        label=label,
        agent=agent,
        run_id=run_id,
        agent_session_id=handle.agent_session_id,
        agent_model=handle.agent_model,
        model_requested=handle.model_requested,
        model_override_supported=handle.model_override_supported,
        model_override_skipped=handle.model_override_skipped,
        model_override_skip_reason=handle.model_override_skip_reason,
        report=report,
        transcript=transcript,
        exit_code=handle.exit_code,
        artifact_ok=bool(validation.ok if validation is not None else False),
        artifact_errors=tuple(validation.errors if validation is not None else ()),
        tokens_input=handle.tokens_input,
        tokens_cached_input=handle.tokens_cached_input,
        tokens_cache_write=handle.tokens_cache_write,
        tokens_output=handle.tokens_output,
        cost_usd=handle.cost_usd,
        resume_command=handle.resume_command,
        completed_at=handle.completed_at.isoformat() if handle.completed_at else "",
    )


def _meta_sibling_path(meta_path: Path, suffix: str) -> Path:
    """Derive a sibling artifact path (e.g. report/transcript) from a meta.json path."""
    marker = ".meta.json"
    if meta_path.name.endswith(marker):
        return meta_path.with_name(f"{meta_path.name[: -len(marker)]}{suffix}")
    return meta_path.with_suffix(suffix)


def _child_result_from_meta(label: str, meta_path: Path) -> ChildResult | None:
    """Reconstruct a `ChildResult` by reading a child's meta.json off disk.

    `None` when the file is missing/unreadable/malformed. Infers a successful
    exit code from a non-empty report when the meta omits `exit_code` and
    reports no errors (covers workers that wrote a report but no explicit code).
    """
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    report = Path(str(payload.get("report") or _meta_sibling_path(meta_path, ".md")))
    transcript = Path(
        str(
            payload.get("transcript")
            or _meta_sibling_path(meta_path, ".transcript.log")
        )
    )
    exit_code_raw = payload.get("exit_code")
    exit_code: int | None
    try:
        exit_code = int(exit_code_raw) if exit_code_raw is not None else None
    except (TypeError, ValueError):
        exit_code = None
    errors = payload.get("artifact_errors") or []
    artifact_errors = (
        tuple(str(item) for item in errors)
        if isinstance(errors, list)
        else (str(errors),)
    )
    if exit_code is None and not artifact_errors and _non_empty_file(report):
        exit_code = 0
    return ChildResult(
        label=label,
        agent=str(payload.get("agent") or ""),
        run_id=str(payload.get("run_id") or ""),
        agent_session_id=str(payload.get("agent_session_id") or ""),
        agent_model=str(payload.get("agent_model") or payload.get("model") or ""),
        model_requested=str(payload.get("model_requested") or ""),
        model_override_supported=bool(payload.get("model_override_supported")),
        model_override_skipped=bool(payload.get("model_override_skipped")),
        model_override_skip_reason=str(payload.get("model_override_skip_reason") or ""),
        report=report,
        transcript=transcript,
        exit_code=exit_code,
        artifact_ok=not artifact_errors and exit_code == 0 and report.is_file(),
        artifact_errors=artifact_errors,
        tokens_input=int(payload.get("tokens_input") or 0),
        tokens_cached_input=int(payload.get("tokens_cached_input") or 0),
        tokens_cache_write=_optional_int(payload.get("tokens_cache_write")),
        tokens_output=int(payload.get("tokens_output") or 0),
        cost_usd=_optional_float(payload.get("cost_usd")),
        resume_command=str(payload.get("resume_command") or ""),
        completed_at=str(
            payload.get("completed_at") or payload.get("updated_at") or ""
        ),
    )


def _non_empty_file(path: Path) -> bool:
    """True if `path` exists as a regular file with size > 0; false on OSError."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _lane_meta_path(agent: str) -> Path:
    """Meta.json path for a research agent's lane."""
    return _research_artifact_paths(
        label=f"research-{agent}",
        agent=agent,
        prompt="",
    )[2]


def _lane_progress_fingerprint(
    meta_path: Path, result: ChildResult | None
) -> tuple[tuple[str, int, int], ...]:
    """(path, size, mtime_ns) tuples for a lane's meta/report/transcript, used
    to detect whether a pending lane is still making progress."""
    paths = (
        meta_path,
        result.report if result is not None else _meta_sibling_path(meta_path, ".md"),
        (
            result.transcript
            if result is not None
            else _meta_sibling_path(meta_path, ".transcript.log")
        ),
    )
    fingerprint: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            fingerprint.append((str(path), -1, -1))
        else:
            fingerprint.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(fingerprint)


def _timed_out_lane_result(
    agent: str, observed: ChildResult | None, reason: str
) -> ChildResult:
    """Synthesize a failed `ChildResult` (exit 124) for a lane that never
    finished, preserving whatever was already observed plus the timeout reason.
    """
    meta_path = _lane_meta_path(agent)
    errors = tuple(
        dict.fromkeys(
            (
                *(observed.artifact_errors if observed is not None else ()),
                "worker_timeout",
                reason,
            )
        )
    )
    return ChildResult(
        label=f"research-{agent}",
        agent=(observed.agent if observed is not None else "") or agent,
        run_id=(observed.run_id if observed is not None else "")
        or f"{_parent_run_id()}-research-{_safe_label(agent)}",
        agent_session_id=(observed.agent_session_id if observed is not None else ""),
        agent_model=observed.agent_model if observed is not None else "",
        model_requested=observed.model_requested if observed is not None else "",
        model_override_supported=(
            observed.model_override_supported if observed is not None else False
        ),
        model_override_skipped=(
            observed.model_override_skipped if observed is not None else False
        ),
        model_override_skip_reason=(
            observed.model_override_skip_reason if observed is not None else ""
        ),
        report=(
            observed.report
            if observed is not None
            else _meta_sibling_path(meta_path, ".md")
        ),
        transcript=(
            observed.transcript
            if observed is not None
            else _meta_sibling_path(meta_path, ".transcript.log")
        ),
        exit_code=124,
        artifact_ok=False,
        artifact_errors=errors,
        tokens_input=observed.tokens_input if observed is not None else 0,
        tokens_cached_input=(
            observed.tokens_cached_input if observed is not None else 0
        ),
        tokens_cache_write=(
            observed.tokens_cache_write if observed is not None else None
        ),
        tokens_output=observed.tokens_output if observed is not None else 0,
        cost_usd=observed.cost_usd if observed is not None else None,
        resume_command=observed.resume_command if observed is not None else "",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


def _research_quorum(total: int) -> int:
    """Survivors needed for a research swarm to still count as a success.

    Majority of the configured lanes (``floor(N/2) + 1``): 2-of-3, 3-of-5.
    A 2-lane swarm needs both — there is no majority below the full set.
    """

    if total <= 0:
        return 0
    return total // 2 + 1


def _research_survivors(results: Sequence[ChildResult]) -> list[ChildResult]:
    """Filter to results that exited cleanly with a valid artifact."""
    return [r for r in results if r.exit_code == 0 and r.artifact_ok]


def _research_run_status(
    results: Sequence[ChildResult],
    synthesis: ChildResult | None,
    *,
    kind: str,
) -> str:
    """Three-way outcome for a supervised swarm run.

    Research degrades gracefully: a majority of surviving lanes plus a valid
    synthesis is ``partial_success`` (a green run, not a failure) instead of
    collapsing the whole swarm to ``failed`` on a single dead lane. Non-research
    kinds (marbles/polarize) keep the strict all-or-nothing contract.
    """

    total = len(results)
    if total == 0:
        return "failed"
    survivors = _research_survivors(results)
    all_ok = len(survivors) == total
    if kind != "research":
        return "completed" if all_ok else "failed"
    synthesis_ok = (
        synthesis is not None and synthesis.exit_code == 0 and synthesis.artifact_ok
    )
    if not synthesis_ok:
        return "failed"
    if all_ok:
        return "completed"
    if len(survivors) >= _research_quorum(total):
        return "partial_success"
    return "failed"


async def _wait_for_research_lanes(
    agents: Sequence[str],
    *,
    timeout_seconds: float = 3600,
    quorum_idle_seconds: float = 120,
    interval_seconds: float = 5,
) -> list[ChildResult]:
    """Poll research lane meta files until all finish, quorum becomes impossible,
    the hard timeout elapses, or quorum is reached and stays idle past
    `quorum_idle_seconds` — whichever comes first. Pending lanes at exit are
    synthesized as timed-out `ChildResult`s via `_timed_out_lane_result`.
    """
    quorum = _research_quorum(len(agents))
    loop = asyncio.get_running_loop()
    hard_deadline = loop.time() + max(timeout_seconds, 0.0)
    quorum_idle_deadline: float | None = None
    last_pending_progress: (
        tuple[tuple[str, tuple[tuple[str, int, int], ...]], ...] | None
    ) = None
    last_announced_pending: tuple[str, ...] | None = None
    while True:
        results: dict[str, ChildResult] = {}
        pending: dict[str, ChildResult | None] = {}
        for agent in agents:
            result = _child_result_from_meta(
                f"research-{agent}", _lane_meta_path(agent)
            )
            if result is None or result.exit_code is None:
                pending[agent] = result
            else:
                results[agent] = result
        if not pending:
            return [results[agent] for agent in agents if agent in results]

        pending_agents = tuple(pending)
        survivors = len(_research_survivors(tuple(results.values())))
        now = loop.time()
        pending_progress = tuple(
            (
                agent,
                _lane_progress_fingerprint(_lane_meta_path(agent), pending[agent]),
            )
            for agent in pending_agents
        )

        if survivors + len(pending) < quorum:
            print(
                "research quorum is impossible; failing pending lanes: "
                f"{', '.join(pending_agents)}",
                file=sys.stderr,
                flush=True,
            )
            return [
                results.get(agent)
                or _timed_out_lane_result(
                    agent, pending.get(agent), "lane_quorum_impossible"
                )
                for agent in agents
            ]

        if now >= hard_deadline:
            print(
                "research lane hard timeout; failing pending lanes: "
                f"{', '.join(pending_agents)}",
                file=sys.stderr,
                flush=True,
            )
            return [
                results.get(agent)
                or _timed_out_lane_result(
                    agent, pending.get(agent), "lane_hard_timeout"
                )
                for agent in agents
            ]

        if survivors >= quorum:
            if (
                quorum_idle_deadline is None
                or pending_progress != last_pending_progress
            ):
                quorum_idle_deadline = min(
                    hard_deadline, now + max(quorum_idle_seconds, 0.0)
                )
            if now >= quorum_idle_deadline:
                print(
                    "research quorum reached; failing idle pending lanes and "
                    f"proceeding with {survivors}/{len(agents)} survivors: "
                    f"{', '.join(pending_agents)}",
                    file=sys.stderr,
                    flush=True,
                )
                return [
                    results.get(agent)
                    or _timed_out_lane_result(
                        agent, pending.get(agent), "lane_quorum_idle_timeout"
                    )
                    for agent in agents
                ]
        else:
            quorum_idle_deadline = None

        if pending_agents != last_announced_pending:
            print(
                f"waiting for research lanes: {', '.join(pending_agents)}", flush=True
            )
            last_announced_pending = pending_agents
        last_pending_progress = pending_progress
        next_deadline = hard_deadline
        if quorum_idle_deadline is not None:
            next_deadline = min(next_deadline, quorum_idle_deadline)
        sleep_seconds = min(
            max(interval_seconds, 0.0),
            max(next_deadline - loop.time(), 0.0),
        )
        await asyncio.sleep(sleep_seconds)


def _failed_synthesis_result(last: ChildResult, reason: str) -> ChildResult:
    """Write and return a failed synthesis `ChildResult` (exit 1) with `reason`
    recorded in a fresh report/transcript, for when synthesis cannot proceed."""
    report, transcript, _meta, _prompt_file = _child_artifact_paths(
        kind="research",
        label="research-synthesis",
        agent=last.agent,
        prompt="",
    )
    now = datetime.now(timezone.utc).isoformat()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "---\nstatus: failed\n---\n\n"
        f"# vc-research synthesis failed\n\nReason: {reason}\n",
        encoding="utf-8",
    )
    transcript.write_text(reason + "\n", encoding="utf-8")
    return ChildResult(
        label="research-synthesis",
        agent=last.agent,
        run_id=f"{_parent_run_id()}-research-synthesis",
        agent_session_id=last.agent_session_id,
        agent_model=last.agent_model,
        model_requested=last.model_requested,
        model_override_supported=last.model_override_supported,
        model_override_skipped=last.model_override_skipped,
        model_override_skip_reason=last.model_override_skip_reason,
        report=report,
        transcript=transcript,
        exit_code=1,
        artifact_ok=False,
        artifact_errors=(reason,),
        resume_command=last.resume_command,
        completed_at=now,
    )


async def _run_research_synthesis(
    root: str,
    prompt: str,
    results: Sequence[ChildResult],
    selection: ResearchAgentSelection | None = None,
    model_requested: str = "",
) -> ChildResult | None:
    """Run research synthesis: an explicit synthesizer agent if configured,
    else a native resume of the last-finishing survivor (falling back to a
    fresh stdin command when native resume is unsupported for that agent).
    `None` when there are too few survivors to meet quorum.
    """
    survivors = _research_survivors(results)
    if not survivors or len(survivors) < _research_quorum(len(results)):
        return None
    if selection is not None and selection.synthesizer:
        return await _run_child(
            kind="research",
            label="research-synthesis",
            agent=selection.synthesizer,
            root=root,
            prompt=prompt,
            model_requested=selection.synthesis_model(model_requested),
            prompt_body=_research_synthesis_prompt(root, prompt, survivors),
        )
    last = max(survivors, key=lambda item: item.completed_at or "")
    if not last.agent_session_id:
        return _failed_synthesis_result(last, "missing_agent_session_id_for_resume")
    try:
        synthesis_command = native_resume_argv(last.agent, last.agent_session_id)
    except ValueError:
        # The tracked guardian resume boundary is intentionally stricter than
        # research synthesis.  For an unverified provider, synthesize in a fresh
        # turn from the durable survivor reports instead of pretending a native
        # resume occurred.
        synthesis_command = _stdin_command(last.agent)
    return await _run_child(
        kind="research",
        label="research-synthesis",
        agent=last.agent,
        root=root,
        prompt=prompt,
        command=synthesis_command,
        model_requested=model_requested,
        prompt_body=_research_synthesis_prompt(root, prompt, survivors),
    )


def _write_parent_report(
    kind: str,
    root: str,
    prompt: str,
    results: Sequence[ChildResult],
    *,
    synthesis: ChildResult | None = None,
    research_selection: ResearchAgentSelection | None = None,
) -> None:
    """Write the parent supervised run's Markdown report and meta.json: status,
    receipt, research lane selection, synthesis and per-child sections.
    """
    status = _research_run_status(results, synthesis, kind=kind)
    lanes_failed = [
        result.agent
        for result in results
        if result.exit_code != 0 or not result.artifact_ok
    ]
    accounting_results = tuple(results) + (
        (synthesis,) if synthesis is not None else ()
    )
    receipt = _parent_receipt(accounting_results)
    cost = receipt.get("cost_usd")
    report = _parent_report_path()
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"status: {status}",
        f"skill: vc-{kind}",
        f"run_id: {_parent_run_id()}",
        f"session_id: {receipt.get('session_id') or 'aggregated'}",
        f"root: {root}",
        f"tokens_input: {receipt['tokens_input']}",
        f"tokens_cached_input: {receipt['tokens_cached_input']}",
    ]
    if receipt.get("tokens_cache_write") is not None:
        lines.append(f"tokens_cache_write: {receipt['tokens_cache_write']}")
    if receipt.get("model_requested"):
        lines.append(f"model_requested: {receipt['model_requested']}")
    lines.extend(
        [
            f"tokens_output: {receipt['tokens_output']}",
            f"tokens_total: {receipt['tokens_total']}",
            f"cost_usd: {cost if cost is not None else 'unknown'}",
            f"cost_source: {receipt['cost_source']}",
        ]
    )
    lines.extend(
        [
            "---",
            "",
            f"# vc-{kind} supervised run",
            "",
            "## Operator Prompt",
            "",
            prompt or "(empty)",
            "",
            "## Reception Ledger",
            "",
            (
                "Child reports are supervised artifacts for the parent runtime. "
                "Research synthesis resumes the last-finishing lane so the reducer "
                "can use native agent context/cache."
            ),
            "",
        ]
    )
    if research_selection is not None:
        lines.extend(
            [
                "## Research Lane Selection",
                "",
                f"- source: {research_selection.source}",
                f"- agents: {', '.join(research_selection.agents) or 'none'}",
                f"- ignored: {', '.join(research_selection.ignored) or 'none'}",
                f"- synthesizer: {research_selection.synthesizer or 'last-survivor'}",
                f"- synthesizer_source: {research_selection.synthesizer_source or 'default'}",
                f"- synthesizer_model: {research_selection.synthesizer_model or 'none'}",
                f"- lanes_failed: {', '.join(lanes_failed) or 'none'}",
                "",
            ]
        )
    if synthesis is not None:
        synthesis_lines = [
            "## Synthesis",
            "",
            f"- {synthesis.label} ({synthesis.agent})",
            f"  - run_id: {synthesis.run_id}",
            f"  - agent_session_id: {synthesis.agent_session_id or 'unknown'}",
            f"  - agent_model: {synthesis.agent_model or 'unknown'}",
            f"  - model_requested: {synthesis.model_requested or 'none'}",
            (
                "  - model_override_supported: "
                f"{str(synthesis.model_override_supported).lower()}"
            ),
            (
                "  - model_override_skipped: "
                f"{str(synthesis.model_override_skipped).lower()}"
            ),
            f"  - exit_code: {synthesis.exit_code}",
            f"  - artifact_ok: {str(synthesis.artifact_ok).lower()}",
            f"  - resume: {synthesis.resume_command}",
            (
                "  - tokens: "
                f"{synthesis.tokens_input} in "
                f"({synthesis.tokens_cached_input} cached) / "
                f"{synthesis.tokens_output} out"
            ),
        ]
        if synthesis.tokens_cache_write is not None:
            synthesis_lines.append(
                f"  - tokens_cache_write: {synthesis.tokens_cache_write}"
            )
        synthesis_lines.extend(
            [
                (
                    "  - cost_usd: "
                    f"{synthesis.cost_usd if synthesis.cost_usd is not None else 'unknown'}"
                ),
                f"  - report: {synthesis.report}",
                f"  - transcript: {synthesis.transcript}",
                "",
            ]
        )
        lines.extend(synthesis_lines)
    elif kind == "research":
        lines.extend(["## Synthesis", "", "- skipped: child run failure", ""])
    lines.extend(["## Child Runs", ""])
    for result in results:
        errors = ", ".join(result.artifact_errors) if result.artifact_errors else "none"
        child_lines = [
            f"- {result.label} ({result.agent})",
            f"  - run_id: {result.run_id}",
            f"  - agent_session_id: {result.agent_session_id or 'unknown'}",
            f"  - agent_model: {result.agent_model or 'unknown'}",
            f"  - model_requested: {result.model_requested or 'none'}",
            (
                "  - model_override_supported: "
                f"{str(result.model_override_supported).lower()}"
            ),
            f"  - model_override_skipped: {str(result.model_override_skipped).lower()}",
            f"  - exit_code: {result.exit_code}",
            f"  - artifact_ok: {str(result.artifact_ok).lower()}",
            f"  - artifact_errors: {errors}",
            (
                "  - tokens: "
                f"{result.tokens_input} in ({result.tokens_cached_input} cached) / "
                f"{result.tokens_output} out"
            ),
        ]
        if result.tokens_cache_write is not None:
            child_lines.append(f"  - tokens_cache_write: {result.tokens_cache_write}")
        child_lines.extend(
            [
                (
                    "  - cost_usd: "
                    f"{result.cost_usd if result.cost_usd is not None else 'unknown'}"
                ),
                f"  - resume: {result.resume_command}",
                f"  - report: {result.report}",
                f"  - transcript: {result.transcript}",
            ]
        )
        lines.extend(child_lines)
    lines.extend(_parent_footer(_parent_run_id(), status, receipt))
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_json(
        _parent_meta_path(),
        {
            "run_id": _parent_run_id(),
            "skill": kind,
            "status": status,
            "report": str(report),
            "session_id": receipt.get("session_id") or "aggregated",
            "tokens_input": receipt["tokens_input"],
            "tokens_cached_input": receipt["tokens_cached_input"],
            **(
                {"tokens_cache_write": receipt["tokens_cache_write"]}
                if receipt.get("tokens_cache_write") is not None
                else {}
            ),
            "tokens_output": receipt["tokens_output"],
            "tokens_total": receipt["tokens_total"],
            "cost_usd": cost if cost is not None else "unknown",
            "cost_source": receipt["cost_source"],
            **(
                {"model_requested": receipt["model_requested"]}
                if receipt.get("model_requested")
                else {}
            ),
            "research_agent_source": research_selection.source
            if research_selection is not None
            else "",
            "research_agents": list(research_selection.agents)
            if research_selection is not None
            else [],
            "research_ignored_agents": list(research_selection.ignored)
            if research_selection is not None
            else [],
            "research_synthesizer": research_selection.synthesizer
            if research_selection is not None
            else "",
            "research_synthesizer_model": research_selection.synthesizer_model
            if research_selection is not None
            else "",
            "research_synthesizer_source": research_selection.synthesizer_source
            if research_selection is not None
            else "",
            "lanes_failed": lanes_failed,
            "synthesis": _result_meta(synthesis) if synthesis is not None else {},
            "children": [_result_meta(result) for result in results],
        },
    )


async def run_research(root: str, prompt: str, model_requested: str = "") -> int:
    """Run all research lanes concurrently, then synthesis; write the parent
    report. Returns 0 for completed/partial_success, 1 otherwise (including
    when no supported research agents are configured).
    """
    model_requested = _remember_runtime_model_request(model_requested)
    selection = research_agent_selection()
    for agent in selection.ignored:
        print(
            f"Ignoring unsupported research agent from runtime picking config: {agent}",
            file=sys.stderr,
        )
    if not selection.agents:
        print("vc-research: no supported research agents configured.", file=sys.stderr)
        return 1
    tasks = [
        _run_child(
            kind="research",
            label=f"research-{agent}",
            agent=agent,
            root=root,
            prompt=prompt,
            model_requested=selection.lane_model(agent, model_requested),
        )
        for agent in selection.agents
    ]
    results = await asyncio.gather(*tasks)
    synthesis = await _run_research_synthesis(
        root, prompt, results, selection, model_requested
    )
    _write_parent_report(
        "research",
        root,
        prompt,
        results,
        synthesis=synthesis,
        research_selection=selection,
    )
    return (
        0
        if _research_run_status(results, synthesis, kind="research")
        in {"completed", "partial_success"}
        else 1
    )


async def run_research_lane(
    root: str, prompt: str, agent: str, model_requested: str = ""
) -> int:
    """Run a single research lane for one agent. Returns 0 on clean exit with
    a valid artifact, 1 for an unsupported agent or a failed/invalid run.
    """
    model_requested = _remember_runtime_model_request(model_requested)
    if agent not in SUPPORTED_RESEARCH_AGENTS:
        print(f"vc-research: unsupported research agent: {agent}", file=sys.stderr)
        return 1
    selection = research_agent_selection()
    result = await _run_child(
        kind="research",
        label=f"research-{agent}",
        agent=agent,
        root=root,
        prompt=prompt,
        model_requested=selection.lane_model(agent, model_requested),
    )
    return 0 if result.exit_code == 0 and result.artifact_ok else 1


async def run_research_synthesis(
    root: str, prompt: str, model_requested: str = ""
) -> int:
    """Wait for already-launched research lanes to finish (via meta polling),
    then synthesize and write the parent report. Returns 0 for completed/
    partial_success, 1 otherwise.
    """
    model_requested = _remember_runtime_model_request(model_requested)
    selection = research_agent_selection()
    for agent in selection.ignored:
        print(
            f"Ignoring unsupported research agent from runtime picking config: {agent}",
            file=sys.stderr,
        )
    if not selection.agents:
        print("vc-research: no supported research agents configured.", file=sys.stderr)
        return 1
    hard_timeout = float(
        os.environ.get("VIBECRAFTED_RESEARCH_SYNTHESIS_TIMEOUT", "3600")
    )
    quorum_idle_timeout = float(
        os.environ.get("VIBECRAFTED_RESEARCH_QUORUM_IDLE_TIMEOUT", "120")
    )
    results = await _wait_for_research_lanes(
        selection.agents,
        timeout_seconds=hard_timeout,
        quorum_idle_seconds=quorum_idle_timeout,
    )
    synthesis = await _run_research_synthesis(
        root, prompt, results, selection, model_requested
    )
    _write_parent_report(
        "research",
        root,
        prompt,
        results,
        synthesis=synthesis,
        research_selection=selection,
    )
    return (
        0
        if _research_run_status(results, synthesis, kind="research")
        in {"completed", "partial_success"}
        else 1
    )


async def run_marbles(
    root: str,
    agent: str,
    prompt: str,
    count: int,
    depth: int,
    workflow: str = "marbles",
    model_requested: str = "",
) -> int:
    """Run up to `count` sequential marbles/polarize loop iterations, stopping
    early on the first failed/invalid child; writes the parent report.
    Returns 0 only if all `count` iterations completed cleanly.
    """
    model_requested = _remember_runtime_model_request(model_requested)
    kind = _safe_label(workflow) or "marbles"
    results: list[ChildResult] = []
    for index in range(1, max(count, 1) + 1):
        loop_prompt = _loop_prompt(kind, prompt, index, count, depth)
        result = await _run_child(
            kind=kind,
            label=f"{kind}-L{index}",
            agent=agent,
            root=root,
            prompt=loop_prompt,
            model_requested=model_requested,
        )
        results.append(result)
        if result.exit_code != 0 or not result.artifact_ok:
            break
    _write_parent_report(kind, root, prompt, results)
    return (
        0
        if len(results) == count
        and all(result.exit_code == 0 and result.artifact_ok for result in results)
        else 1
    )


def _parser() -> argparse.ArgumentParser:
    """Build the `workflow_runtime` CLI's argparse parser (research/research-lane/
    research-synthesis/marbles subcommands)."""
    parser = argparse.ArgumentParser(
        description="Vibecrafted supervised workflow runtimes."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    research = sub.add_parser("research")
    research.add_argument("--root", required=True)
    research.add_argument("--prompt", default="")
    research.add_argument("--prompt-file", default="")
    research.add_argument("--model", default="")
    research.add_argument("--synthesizer", default="")
    research.add_argument("--synthesizer-model", default="")
    research_lane = sub.add_parser("research-lane")
    research_lane.add_argument("--agent", required=True)
    research_lane.add_argument("--root", required=True)
    research_lane.add_argument("--prompt", default="")
    research_lane.add_argument("--prompt-file", default="")
    research_lane.add_argument("--model", default="")
    research_synthesis = sub.add_parser("research-synthesis")
    research_synthesis.add_argument("--root", required=True)
    research_synthesis.add_argument("--prompt", default="")
    research_synthesis.add_argument("--prompt-file", default="")
    research_synthesis.add_argument("--model", default="")
    research_synthesis.add_argument("--synthesizer", default="")
    research_synthesis.add_argument("--synthesizer-model", default="")
    marbles = sub.add_parser("marbles")
    marbles.add_argument("--workflow", default="marbles")
    marbles.add_argument("--agent", default="codex")
    marbles.add_argument("--root", required=True)
    marbles.add_argument("--prompt", default="")
    marbles.add_argument("--prompt-file", default="")
    marbles.add_argument("--count", type=int, default=3)
    marbles.add_argument("--depth", type=int, default=3)
    marbles.add_argument("--model", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: parse args, stash requested model/synthesizer overrides
    into env, and dispatch to the matching `run_*` coroutine via `asyncio.run`.
    """
    ns = _parser().parse_args(argv)
    model_requested = str(
        getattr(ns, "model", "") or os.environ.get("VIBECRAFTED_MODEL_REQUESTED") or ""
    ).strip()
    if model_requested:
        os.environ["VIBECRAFTED_MODEL_REQUESTED"] = model_requested
    synthesizer = str(getattr(ns, "synthesizer", "") or "").strip()
    if synthesizer:
        os.environ["VIBECRAFTED_RESEARCH_SYNTHESIZER"] = synthesizer
    synthesizer_model = str(getattr(ns, "synthesizer_model", "") or "").strip()
    if synthesizer_model:
        os.environ["VIBECRAFTED_RESEARCH_SYNTHESIZER_MODEL"] = synthesizer_model
    if ns.command == "research":
        prompt = ns.prompt or _read_prompt_file(ns.prompt_file)
        return asyncio.run(run_research(ns.root, prompt, model_requested))
    if ns.command == "research-lane":
        prompt = ns.prompt or _read_prompt_file(ns.prompt_file)
        return asyncio.run(
            run_research_lane(ns.root, prompt, ns.agent, model_requested)
        )
    if ns.command == "research-synthesis":
        prompt = ns.prompt or _read_prompt_file(ns.prompt_file)
        return asyncio.run(run_research_synthesis(ns.root, prompt, model_requested))
    if ns.command == "marbles":
        prompt = ns.prompt or _read_prompt_file(ns.prompt_file)
        return asyncio.run(
            run_marbles(
                ns.root,
                ns.agent,
                prompt,
                ns.count,
                ns.depth,
                ns.workflow,
                model_requested,
            )
        )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
