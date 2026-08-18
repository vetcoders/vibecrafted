"""TOML parsing, validation, and prompt rendering for ``vibecrafted.dispatch.v1`` plans."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import tomllib

from vibecrafted_core.autonomy_surface import destructive_remote_push
from vibecrafted_core.delivery.model import ContractError, ExecutionEnvelope
from vibecrafted_core.workflow import SUPPORTED_WORKFLOWS

from .model import (
    CRITICAL_FAIL_POLICIES,
    MATCHER_TYPES,
    READ_MUTATIONS,
    SCHEMA_VERSION,
    TIMEOUT_POLICIES,
    Baton,
    Common,
    Cut,
    Dispatch,
    Matcher,
    Meta,
    Phase,
    Policy,
    Recovery,
    Verify,
)

FORBIDDEN_COMMAND_NEEDLES = (
    "--no-verify",
    "git reset --hard",
    "git clean -fd",
    "git clean -xdf",
    "make release",
    "release",
    "rm -rf /",
    "vc-release",
    "vibecrafted release",
)


@dataclass(frozen=True)
class DispatchDoctorResult:
    """Outcome of doctor-validating dispatch text: pass/fail plus raw error strings."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    dispatch: Dispatch | None = None


class DispatchSchemaError(ValueError):
    """Raised when dispatch TOML fails structural or policy validation; carries all errors."""

    def __init__(self, errors: list[str] | tuple[str, ...]) -> None:
        """Store the full error list and join it into the exception message."""
        self.errors = tuple(errors)
        super().__init__("\n".join(self.errors))


def load_dispatch(path: str | Path) -> Dispatch:
    """Read and parse a dispatch TOML file from disk; raises on any validation error."""
    source = Path(path).expanduser()
    return parse_dispatch(source.read_text(encoding="utf-8"), base_dir=source.parent)


def doctor_dispatch(
    text: str, *, base_dir: str | Path | None = None
) -> DispatchDoctorResult:
    """Validate dispatch TOML text (structure + READ-cut policy) without raising."""
    try:
        dispatch = parse_dispatch(text, base_dir=base_dir)
        policy_errors = _doctor_policy_errors(dispatch)
        warnings = tuple(
            f"cuts[{index}].model: pin {cut.model!r} will be forwarded to "
            f"{cut.agent}; provider/account availability is not validated"
            for index, cut in enumerate(dispatch.cuts)
            if cut.model
        )
        if policy_errors:
            return DispatchDoctorResult(
                ok=False,
                errors=tuple(policy_errors),
                warnings=warnings,
                dispatch=dispatch,
            )
        return DispatchDoctorResult(
            ok=True,
            errors=(),
            warnings=warnings,
            dispatch=dispatch,
        )
    except DispatchSchemaError as exc:
        return DispatchDoctorResult(
            ok=False, errors=exc.errors, warnings=(), dispatch=None
        )


def parse_dispatch(text: str, *, base_dir: str | Path | None = None) -> Dispatch:
    """Parse and fully validate dispatch TOML text into a typed ``Dispatch``.

    Raises ``DispatchSchemaError`` with every collected error (not just the first).
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DispatchSchemaError([f"toml: {exc}"]) from exc

    if not isinstance(raw, dict):
        raise DispatchSchemaError(["dispatch root must be a TOML table"])

    errors: list[str] = []
    schema = _string(raw.get("schema"))
    if schema != SCHEMA_VERSION:
        errors.append(f"schema: unsupported schema {schema!r}")

    meta = _parse_meta(raw.get("meta"), errors)
    envelope = _parse_execution(raw.get("execution"), errors)
    proof = _parse_proof(raw.get("proof"), errors)
    policy = _parse_policy(raw.get("policy"), errors)
    common = _parse_common(raw.get("common"))
    workflow_map = _parse_workflow_map(
        raw.get("workflow_map") or raw.get("workflows"), errors
    )
    phases = _parse_phases(raw.get("phases"), errors)
    base = Path(base_dir).expanduser() if base_dir is not None else None
    cuts = _parse_cuts(
        raw.get("cuts"),
        phases=phases,
        workflow_map=workflow_map,
        base_dir=base,
        errors=errors,
    )

    if policy.concurrency > 1 and not policy.allow_concurrency:
        errors.append(
            "policy.concurrency: values greater than 1 require allow_concurrency = true"
        )

    _validate_recovery_targets(cuts, phases, errors)
    _validate_cut_dag(cuts, errors)

    if errors:
        raise DispatchSchemaError(errors)

    return Dispatch(
        schema=schema,
        meta=meta,
        policy=policy,
        common=common,
        phases=tuple(phases),
        cuts=tuple(cuts),
        workflow_map=workflow_map,
        envelope=envelope,
        proof=proof,
    )


def render_cell_prompt(
    dispatch: Dispatch, cut: Cut, *, baton: Baton | None = None
) -> str:
    """Assemble one cut's worker prompt: common text + brief/prompt + extra + baton JSON."""
    active_baton = baton if baton is not None else dispatch.empty_baton()
    variables = {
        "repo": cut.runtime_root or dispatch.meta.repo,
        "id": cut.id,
        "agent": cut.agent,
        "workflow": cut.workflow,
        "resolved_workflow": cut.resolved_workflow,
        "reports_dir": cut.artifact_path or dispatch.meta.reports_dir,
        "tracker": dispatch.meta.tracker,
        "baton": active_baton.to_json(),
    }
    body = _brief_or_prompt(cut)
    delivery_contract = ""
    if cut.mode != "read" and dispatch.policy.require_commit:
        slot = cut.id.split("_", 1)[0]
        delivery_contract = (
            "DELIVERY CONTRACT (supervisor-enforced): commit the verified delivery. "
            f"The commit message must contain the exact cut id '{cut.id}' or slot "
            f"marker '[{slot}]'. If no new commit is needed and "
            "allow_idempotent_existing is enabled, report exactly one proof line "
            "'Commit: <sha>' naming an ancestor commit whose message also identifies "
            "this cut."
        )
    parts = [
        dispatch.common.text,
        body,
        cut.extra,
        delivery_contract,
        active_baton.to_json(),
    ]
    rendered = [_format_known(part, variables).strip() for part in parts if part]
    return "\n\n".join(part for part in rendered if part).rstrip() + "\n"


def render_cut_verifies(dispatch: Dispatch, cut: Cut) -> Cut:
    """Render placeholders in verifier commands before execution.

    Verifier shells get the same variable set as cell prompts except the
    baton — a JSON blob never belongs inside a shell command line.
    """
    if not cut.verify:
        return cut
    variables = {
        "repo": cut.runtime_root or dispatch.meta.repo,
        "id": cut.id,
        "agent": cut.agent,
        "workflow": cut.workflow,
        "resolved_workflow": cut.resolved_workflow,
        "reports_dir": cut.artifact_path or dispatch.meta.reports_dir,
        "tracker": dispatch.meta.tracker,
    }
    rendered = tuple(
        replace(verify, run=_format_known(verify.run, variables))
        for verify in cut.verify
    )
    return replace(cut, verify=rendered)


def _brief_or_prompt(cut: Cut) -> str:
    """Read the cut's brief file if resolvable, else fall back to its inline prompt."""
    if cut.brief:
        path = Path(cut.brief).expanduser()
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return cut.prompt


def _format_known(text: str, variables: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders for known variables only; unknowns pass through."""
    rendered = text
    for name, value in variables.items():
        rendered = rendered.replace("{" + name + "}", value)
    return rendered


def _parse_meta(value: Any, errors: list[str]) -> Meta:
    """Parse the ``[meta]`` table; records an error when ``repo`` is missing."""
    if not isinstance(value, dict):
        errors.append("meta: table is required")
        return Meta(name="", repo="")
    name = _string(value.get("name"))
    repo = _string(value.get("repo"))
    if not repo:
        errors.append("meta.repo: required")
    return Meta(
        name=name,
        repo=repo,
        description=_string(value.get("description")),
        baseline=_dict(value.get("baseline")),
        reports_dir=_string(value.get("reports_dir")),
        tracker=_string(value.get("tracker")),
    )


def _parse_execution(value: Any, errors: list[str]) -> ExecutionEnvelope | None:
    """Parse the optional `[execution]` envelope block (spec §7.1).

    Absent envelope = legacy dispatch, unchanged. Present envelope is typed
    through the delivery kernel and fails closed: unknown schema versions,
    unknown fields, and missing fields all refuse the whole dispatch.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append("execution: table is required when present")
        return None
    try:
        return ExecutionEnvelope.from_payload(value)
    except ContractError as exc:
        errors.append(f"execution: {exc}")
    except KeyError as exc:
        errors.append(f"execution: missing envelope field {exc}")
    return None


def _parse_proof(value: Any, errors: list[str]) -> dict[str, Any] | None:
    """Transport the `[proof]` contract opaquely (spec §11).

    Dispatch never interprets proof semantics: the payload is preserved
    verbatim for the worker, unknown fields included.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append("proof: table is required when present")
        return None
    return value


def _parse_policy(value: Any, errors: list[str]) -> Policy:
    """Parse the ``[policy]`` table, validating enum fields and concurrency bounds."""
    raw = value if isinstance(value, dict) else {}
    on_timeout = _string(raw.get("on_timeout")) or "fail"
    if on_timeout not in TIMEOUT_POLICIES:
        errors.append(f"policy.on_timeout: unsupported value {on_timeout!r}")
    on_critical_fail = _string(raw.get("on_critical_fail")) or "break"
    if on_critical_fail not in CRITICAL_FAIL_POLICIES:
        errors.append(
            f"policy.on_critical_fail: unsupported value {on_critical_fail!r}"
        )
    concurrency = _int(raw.get("concurrency"), 1)
    if concurrency < 1:
        errors.append("policy.concurrency: must be at least 1")
    return Policy(
        repair_rounds=max(0, _int(raw.get("repair_rounds"), 0)),
        on_critical_fail=on_critical_fail,
        on_timeout=on_timeout,
        concurrency=concurrency,
        await_config=_dict(raw.get("await")),
        verify_executor=_string(raw.get("verify_executor")) or "supervisor",
        allow_concurrency=bool(
            raw.get("allow_concurrency")
            or raw.get("enable_concurrency")
            or raw.get("parallel_enabled")
        ),
        require_commit=bool(raw.get("require_commit")),
        allow_idempotent_existing=bool(raw.get("allow_idempotent_existing", True)),
    )


def _parse_common(value: Any) -> Common:
    """Parse the optional ``[common]`` table (shared prompt text); defaults to empty."""
    raw = value if isinstance(value, dict) else {}
    return Common(text=_string(raw.get("text")))


def _parse_workflow_map(value: Any, errors: list[str]) -> dict[str, str]:
    """Parse the alias -> supported-workflow mapping used to resolve ``cut.workflow``."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("workflow_map: table is required when present")
        return {}

    result: dict[str, str] = {}
    for raw_name, raw_target in value.items():
        name = _string(raw_name)
        target = _string(raw_target)
        if not name or not target:
            errors.append("workflow_map: names and targets must be non-empty strings")
            continue
        if target not in SUPPORTED_WORKFLOWS:
            errors.append(f"workflow_map.{name}: target {target!r} is not supported")
            continue
        result[name] = target
    return result


def _parse_phases(value: Any, errors: list[str]) -> list[Phase]:
    """Parse the ``[[phases]]`` array, requiring unique non-empty titles."""
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append("phases: array of tables is required")
        return []

    phases: list[Phase] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"phases[{index}]: table is required")
            continue
        title = _string(item.get("title"))
        if not title:
            errors.append(f"phases[{index}].title: required")
            continue
        if title in seen:
            errors.append(f"phases[{index}].title: duplicate phase {title!r}")
            continue
        seen.add(title)
        phases.append(Phase(title=title, detail=_string(item.get("detail"))))
    return phases


def _parse_cuts(
    value: Any,
    *,
    phases: list[Phase],
    workflow_map: dict[str, str],
    base_dir: Path | None,
    errors: list[str],
) -> list[Cut]:
    """Parse the ``[[cuts]]`` array: ids, phase refs, workflow resolution, verify/mutation rules."""
    if not isinstance(value, list) or not value:
        errors.append("cuts: at least one cut table is required")
        return []

    phase_titles = {phase.title for phase in phases}
    cuts: list[Cut] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            errors.append(f"cuts[{index}]: table is required")
            continue
        cut_id = _string(item.get("id"))
        if not cut_id:
            errors.append(f"cuts[{index}].id: required")
        elif cut_id in seen_ids:
            errors.append(f"cuts[{index}].id: duplicate cut id {cut_id!r}")
        seen_ids.add(cut_id)

        phase = _string(item.get("phase"))
        if phase and phase_titles and phase not in phase_titles:
            errors.append(f"cuts[{index}].phase: unknown phase {phase!r}")

        workflow = _string(item.get("workflow"))
        resolved_workflow = workflow_map.get(workflow, workflow)
        if resolved_workflow not in SUPPORTED_WORKFLOWS:
            errors.append(f"cuts[{index}].workflow: unsupported workflow {workflow!r}")

        prompt = _string(item.get("prompt"))
        brief = _string(item.get("brief"))
        if not prompt and not brief:
            errors.append(f"cuts[{index}]: prompt or brief is required")
        if brief:
            _validate_brief_path(brief, base_dir, index, errors)

        mode = _string(item.get("mode")) or "write"
        mutation = _string(item.get("mutation"))
        observational = bool(item.get("observational") or item.get("observe"))
        verify = _parse_verify(item.get("verify"), index, errors)
        if not verify and not (mode == "read" and observational):
            errors.append(
                f"cuts[{index}].verify: required unless observational READ is explicit"
            )
        if mode == "read" and mutation and mutation not in READ_MUTATIONS:
            errors.append(f"cuts[{index}].mutation: unsupported value {mutation!r}")

        cuts.append(
            Cut(
                id=cut_id,
                phase=phase,
                agent=_string(item.get("agent")),
                workflow=workflow,
                resolved_workflow=resolved_workflow,
                critical=bool(item.get("critical")),
                mode=mode,
                model=_string(item.get("model")),
                prompt=prompt,
                brief=_resolve_brief(brief, base_dir),
                extra=_string(item.get("extra")),
                mutation=mutation,
                observational=observational,
                verify=tuple(verify),
                recovery=_parse_recovery(item.get("recovery"), index, errors),
                depends_on=_string_tuple(
                    item.get("depends_on"), f"cuts[{index}].depends_on", errors
                ),
                integrator=bool(item.get("integrator")),
            )
        )
    return cuts


def _doctor_policy_errors(dispatch: Dispatch) -> list[str]:
    """Policy-level checks that require a fully parsed ``Dispatch`` (post-structural)."""
    errors: list[str] = []
    for index, cut in enumerate(dispatch.cuts):
        if cut.mode == "read" and not cut.mutation:
            errors.append(f"cuts[{index}].mutation: required for READ cuts")
        if cut.integrator and cut.mode == "read":
            errors.append(f"cuts[{index}].integrator: integrators must be WRITE cuts")
    for field, value in (
        ("meta.reports_dir", dispatch.meta.reports_dir),
        ("meta.tracker", dispatch.meta.tracker),
    ):
        normalized = value.replace("\\", "/")
        if value and any(
            marker in normalized
            for marker in ("/.claude/", "/.codex/", "/.gemini/", "/.vibecrafted/")
        ):
            errors.append(
                f"{field}: provider-specific or repo-local runtime roots are recovery-only; new writes use ~/.vibecrafted/artifacts"
            )
    cargo_target = str(os.environ.get("CARGO_TARGET_DIR") or "").strip()
    if dispatch.policy.concurrency > 1 and cargo_target:
        errors.append(
            "policy.concurrency: shared CARGO_TARGET_DIR is forbidden for concurrent plans; unset CARGO_TARGET_DIR — Vibecrafted assigns $PWD/target per worker"
        )
    return errors


def _parse_verify(value: Any, cut_index: int, errors: list[str]) -> list[Verify]:
    """Parse one cut's ``verify`` array, including forbidden-command checks."""
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"cuts[{cut_index}].verify: array of tables is required")
        return []

    verifiers: list[Verify] = []
    for index, item in enumerate(value):
        prefix = f"cuts[{cut_index}].verify[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: table is required")
            continue
        run = _string(item.get("run"))
        if not run:
            errors.append(f"{prefix}.run: required")
        _validate_command(run, prefix, errors)
        matchers = _parse_matchers(item.get("expect"), prefix, errors)
        verifiers.append(Verify(run=run, matchers=tuple(matchers)))
    return verifiers


def _parse_matchers(value: Any, prefix: str, errors: list[str]) -> list[Matcher]:
    """Parse one verifier's ``expect`` table into typed matchers, validating each kind."""
    if not isinstance(value, dict) or not value:
        errors.append(f"{prefix}.expect: non-empty matcher table is required")
        return []

    matchers: list[Matcher] = []
    for kind, expected in value.items():
        if kind not in MATCHER_TYPES:
            errors.append(f"{prefix}.expect.{kind}: unsupported matcher")
            continue
        if kind == "exit_code":
            if isinstance(expected, bool) or not isinstance(expected, int):
                errors.append(f"{prefix}.expect.exit_code: integer expected")
                continue
            matchers.append(Matcher(kind=kind, expected=expected))
            continue
        if not isinstance(expected, str):
            errors.append(f"{prefix}.expect.{kind}: string expected")
            continue
        if kind == "matches":
            try:
                re.compile(expected)
            except re.error as exc:
                errors.append(f"{prefix}.expect.matches: invalid regex: {exc}")
                continue
        matchers.append(Matcher(kind=kind, expected=expected))
    return matchers


def _parse_recovery(value: Any, cut_index: int, errors: list[str]) -> Recovery | None:
    """Parse a cut's optional ``[cuts.recovery]`` table."""
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"cuts[{cut_index}].recovery: table is required")
        return None
    max_loops = value.get("max_loops")
    loops = (
        max_loops
        if isinstance(max_loops, int) and not isinstance(max_loops, bool)
        else None
    )
    if max_loops is not None and loops is None:
        errors.append(f"cuts[{cut_index}].recovery.max_loops: integer expected")
    return Recovery(
        on=_string(value.get("on")),
        goto=_string(value.get("goto")),
        max_loops=loops,
    )


def _validate_recovery_targets(
    cuts: list[Cut], phases: list[Phase], errors: list[str]
) -> None:
    """Ensure every ``recovery.goto`` names an existing cut id or phase title."""
    cut_ids = {cut.id for cut in cuts if cut.id}
    phase_titles = {phase.title for phase in phases}
    for index, cut in enumerate(cuts):
        if cut.recovery is None or not cut.recovery.goto:
            continue
        target = cut.recovery.goto
        if target not in cut_ids and target not in phase_titles:
            errors.append(f"cuts[{index}].recovery.goto: unknown target {target!r}")


def _validate_cut_dag(cuts: list[Cut], errors: list[str]) -> None:
    """Validate dependency references and reject cycles before any launch."""
    positions = {cut.id: index for index, cut in enumerate(cuts)}
    for index, cut in enumerate(cuts):
        for dependency in cut.depends_on:
            if dependency == cut.id:
                errors.append(f"cuts[{index}].depends_on: cut cannot depend on itself")
            elif dependency not in positions:
                errors.append(f"cuts[{index}].depends_on: unknown cut {dependency!r}")

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {cut.id: cut for cut in cuts}

    def visit(cut_id: str) -> None:
        if cut_id in visited or cut_id not in by_id:
            return
        if cut_id in visiting:
            errors.append(f"cuts: dependency cycle includes {cut_id!r}")
            return
        visiting.add(cut_id)
        for dependency in by_id[cut_id].depends_on:
            visit(dependency)
        visiting.remove(cut_id)
        visited.add(cut_id)

    for cut in cuts:
        visit(cut.id)


def _string_tuple(value: Any, path: str, errors: list[str]) -> tuple[str, ...]:
    """Parse a TOML string array, preserving declaration order and uniqueness."""
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        errors.append(f"{path}: array of non-empty strings required")
        return ()
    result: list[str] = []
    for item in value:
        cleaned = item.strip()
        if not cleaned:
            errors.append(f"{path}: array of non-empty strings required")
        elif cleaned not in result:
            result.append(cleaned)
    return tuple(result)


def _validate_brief_path(
    brief: str, base_dir: Path | None, cut_index: int, errors: list[str]
) -> None:
    """Resolve a cut's brief path against ``base_dir`` and require it to exist."""
    path = Path(brief).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    if not path.is_file():
        errors.append(f"cuts[{cut_index}].brief: file is missing or unreadable")


def _resolve_brief(brief: str, base_dir: Path | None) -> str:
    """Resolve a brief path to an absolute string, anchored at ``base_dir`` if relative."""
    if not brief:
        return ""
    path = Path(brief).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return str(path)


def _validate_command(run: str, prefix: str, errors: list[str]) -> None:
    """Reject verifier commands containing a hard-stop needle (release/force ops)."""
    if not run:
        return
    normalized = " ".join(shlex.split(run)) if _can_shlex(run) else run
    lowered = normalized.lower()
    destructive = destructive_remote_push(normalized)
    if destructive is not None:
        errors.append(
            f"{prefix}.run: forbidden destructive remote push {destructive!r}"
        )
    for needle in FORBIDDEN_COMMAND_NEEDLES:
        if needle in lowered:
            errors.append(f"{prefix}.run: forbidden hard-stop command {needle!r}")


def _can_shlex(value: str) -> bool:
    """True when ``value`` tokenizes cleanly under POSIX shell-lexing rules."""
    try:
        shlex.split(value)
    except ValueError:
        return False
    return True


def _string(value: Any) -> str:
    """Coerce a TOML value to a trimmed string, or "" when it is not a string."""
    return value.strip() if isinstance(value, str) else ""


def _int(value: Any, default: int) -> int:
    """Coerce a TOML value to an int, rejecting bools; falls back to ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def _dict(value: Any) -> dict[str, Any]:
    """Coerce a TOML value to a plain dict, or {} when it is not a table."""
    return dict(value) if isinstance(value, dict) else {}
