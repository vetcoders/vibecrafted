"""Provider continuity capability registry — the ONE declarative authority.

Continuity Kernel spec §7 (Provider capability contract): provider behavior
must be represented once and consumed everywhere. This module is that single
representation. Every later continuity surface (kernel resolver, deck, MCP,
TUI) imports THIS table; no other module may declare provider resume/fork
capability.

Two truth layers live here, deliberately separated:

* the **declarative table** (:data:`CAPABILITIES`) — current core truth
  (``spawn._stdin_command`` / ``workflow_runtime._resume_stdin_command``) merged with the
  operator-verified installed-CLI evidence (AICX 2026-07-12/13, host probes
  2026-07-18); a claim the runtime has not proven headless stays
  ``unverified``, never optimistically ``supported``;
* the **installed-CLI probe** (:func:`probe`) — live evidence from the binary
  actually on this host. Spec §7 rule 6: a probe failure makes autonomous
  recovery unavailable — it does NOT prove the provider incapable, so
  ``probe_failed`` is its own state, distinct from ``unsupported``.

Gemini is present as ``evidence_only``: deprecated sessions may contribute
historical evidence, but nothing may ever select or execute the gemini binary
(spec §7 rule 4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from typing import Any

from ..capabilities import ProbeResult as CliProbe
from ..clock import utc_now_iso
from ..runtime_paths import agent_tool_search_path

# Capability verdicts for the declarative table. ``unverified`` means the
# surface exists in the installed CLI but the runtime has not proven the
# headless contract — fail-closed until a later cut upgrades it with evidence.
SUPPORTED = "supported"
UNSUPPORTED = "unsupported"
UNVERIFIED = "unverified"
# Native-fork adds one restricted middle state (codex: fork exists but only
# in a visible terminal runtime; ``codex exec`` has no fork command).
TERMINAL_ONLY = "terminal_only"

# Execution classes. ``evidence_only`` providers contribute historical session
# evidence but must never be selected as an execution binary.
EXECUTABLE = "executable"
EVIDENCE_ONLY = "evidence_only"

# Probe states. ``probe_failed`` (binary missing/broken/timeout) is NOT
# ``unsupported`` (binary runs but the declared contract markers are absent).
PROBE_CONFIRMED = "confirmed"
PROBE_UNSUPPORTED = "unsupported"
PROBE_FAILED = "probe_failed"
PROBE_EVIDENCE_ONLY = "evidence_only"

Runner = Callable[[Sequence[str]], CliProbe]


@dataclass(frozen=True)
class ProbeRecipe:
    """How to interrogate one installed provider CLI, read-only."""

    cli: str
    version_args: tuple[str, ...] = ("--version",)
    help_args: tuple[str, ...] = ("--help",)
    # Literal tokens that must appear on the installed CLI help surface for
    # the declared continuity contract to be considered present.
    required_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProviderCapability:
    """Spec §7 record for one provider. Frozen: the table is data, not state."""

    agent: str
    execution: str  # EXECUTABLE | EVIDENCE_ONLY
    session_id_shape: str
    session_id_sources: tuple[str, ...]
    interactive_resume: str
    noninteractive_resume: str
    native_fork: str  # SUPPORTED | TERMINAL_ONLY | UNSUPPORTED
    fork_runtime_restrictions: str
    prompt_transport: str  # "stdin" | "file" | "flag_value" | "none"
    session_identity_event: str
    cwd_safety: str
    resume_preserves_cache: bool | None  # None = unknown/not established
    forbidden_flags: tuple[str, ...]
    probe_recipe: ProbeRecipe | None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Flatten this record to a JSON-serializable dict (tuples/nested recipe unpacked)."""
        payload: dict[str, Any] = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, ProbeRecipe):
                payload[item.name] = {
                    "cli": value.cli,
                    "version_args": list(value.version_args),
                    "help_args": list(value.help_args),
                    "required_markers": list(value.required_markers),
                }
            elif isinstance(value, tuple):
                payload[item.name] = list(value)
            else:
                payload[item.name] = value
        return payload


# The session-id token shape every dispatch surface already extracts
# (spawn.SESSION_PATTERNS / agent_dispatch._SESSION_PATTERNS).
_SESSION_TOKEN = "[A-Za-z0-9][A-Za-z0-9-]* (UUID in practice)"

CAPABILITIES: Mapping[str, ProviderCapability] = {
    "claude": ProviderCapability(
        agent="claude",
        execution=EXECUTABLE,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=(
            "stream_json_init_event",
            "transcript_session_line",
            "run_meta",
        ),
        interactive_resume=SUPPORTED,
        noninteractive_resume=SUPPORTED,
        native_fork=SUPPORTED,
        fork_runtime_restrictions=(
            "none — `--fork-session` composes with `-p --resume <id>` headless"
        ),
        prompt_transport="stdin",
        session_identity_event=(
            "stream-json `system`/`init` event carrying `session_id`"
        ),
        cwd_safety="runs in invocation cwd; no checkout-mutating flags",
        resume_preserves_cache=True,
        forbidden_flags=(),
        probe_recipe=ProbeRecipe(
            cli="claude",
            required_markers=("--resume", "--fork-session", "--print"),
        ),
        notes="claude 2.1.214 verified on host 2026-07-18",
    ),
    "codex": ProviderCapability(
        agent="codex",
        execution=EXECUTABLE,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=(
            "exec_json_thread_started_event",
            "transcript_session_line",
            "run_meta",
        ),
        interactive_resume=SUPPORTED,
        noninteractive_resume=SUPPORTED,
        native_fork=TERMINAL_ONLY,
        fork_runtime_restrictions=(
            "top-level `fork` subcommand needs a visible terminal runtime; "
            "`codex exec` has no fork command — headless fork fails closed"
        ),
        prompt_transport="stdin",
        session_identity_event=(
            "`codex exec --json` JSONL `thread.started` event (thread id)"
        ),
        cwd_safety="runs in invocation cwd; no checkout-mutating flags",
        resume_preserves_cache=True,
        forbidden_flags=(),
        probe_recipe=ProbeRecipe(
            cli="codex",
            required_markers=("exec", "resume"),
        ),
        notes="codex-cli 0.144.5 verified on host 2026-07-18",
    ),
    "gemini": ProviderCapability(
        agent="gemini",
        execution=EVIDENCE_ONLY,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=("legacy_artifacts",),
        interactive_resume=UNSUPPORTED,
        noninteractive_resume=UNSUPPORTED,
        native_fork=UNSUPPORTED,
        fork_runtime_restrictions="never executable",
        prompt_transport="none",
        session_identity_event="none — historical artifacts only",
        cwd_safety="not applicable — binary must never launch",
        resume_preserves_cache=None,
        forbidden_flags=(),
        probe_recipe=None,
        notes=(
            "spec §7 rule 4: deprecated gemini sessions may contribute "
            "historical evidence but may never select the gemini binary; "
            "agy is the execution replacement"
        ),
    ),
    "agy": ProviderCapability(
        agent="agy",
        execution=EXECUTABLE,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=("transcript_session_line", "run_meta"),
        interactive_resume=SUPPORTED,
        noninteractive_resume=UNVERIFIED,
        native_fork=UNSUPPORTED,
        fork_runtime_restrictions="no fork surface in agy 1.1.x",
        prompt_transport="flag_value",
        session_identity_event=(
            "none structured; runner-captured transcript `session:` line"
        ),
        cwd_safety=("workspace pinned via `--add-dir .`; no checkout-mutating flags"),
        resume_preserves_cache=None,
        forbidden_flags=(),
        probe_recipe=ProbeRecipe(
            cli="agy",
            required_markers=("--continue", "--conversation", "--print"),
        ),
        notes=(
            "agy 1.1.3 verified on host 2026-07-18: `-c/--continue` (most "
            "recent) and `--conversation <id>` (resume by ID) exist; the "
            "headless `--conversation` + `--print` combination is the F06 "
            "contract still to be proven — core spawn fails closed today. "
            "Prompt rides the `--print` flag value (ARG_MAX-bound; stdin "
            "folded via shell shim)"
        ),
    ),
    "junie": ProviderCapability(
        agent="junie",
        execution=EXECUTABLE,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=("json_stream_receipt", "run_meta"),
        interactive_resume=SUPPORTED,
        noninteractive_resume=UNVERIFIED,
        native_fork=UNSUPPORTED,
        fork_runtime_restrictions="no fork surface in junie 26.x",
        prompt_transport="stdin",
        session_identity_event=(
            "`--output-format json-stream` telemetry receipt (session id)"
        ),
        cwd_safety=("project pinned via `--project .`; no checkout-mutating flags"),
        resume_preserves_cache=None,
        forbidden_flags=(),
        probe_recipe=ProbeRecipe(
            cli="junie",
            required_markers=("--resume", "--session-id"),
        ),
        notes=(
            "junie 26.7.13 verified on host 2026-07-18: `--resume` + "
            "`--session-id <id>` follow up a previous session; operator- "
            "verified 2026-07-12/13 that this opens an INTERACTIVE session. "
            "The headless resume combination (with `--input-format text "
            "--output-format json-stream`) is the F06 contract still to be "
            "proven — core spawn fails closed today"
        ),
    ),
    "grok": ProviderCapability(
        agent="grok",
        execution=EXECUTABLE,
        session_id_shape="UUID (grok `--session-id` requires a valid UUID)",
        session_id_sources=(
            "streaming_json_events",
            "transcript_session_line",
            "run_meta",
        ),
        interactive_resume=SUPPORTED,
        noninteractive_resume=SUPPORTED,
        native_fork=SUPPORTED,
        fork_runtime_restrictions=(
            "`--fork-session` composes with `--resume` headless; "
            "`--session-id` only names the forked session, never resumes"
        ),
        prompt_transport="file",
        session_identity_event=("`--output-format streaming-json` session events"),
        cwd_safety=(
            "cwd pinned via `--cwd .`; recovery must never restore code or "
            "leave the shared checkout"
        ),
        resume_preserves_cache=True,
        forbidden_flags=("--restore-code", "--worktree"),
        probe_recipe=ProbeRecipe(
            cli="grok",
            required_markers=("--resume", "--fork-session", "--prompt-file"),
        ),
        notes=(
            "grok 0.2.102 verified on host 2026-07-18. spec §7 rule 3: "
            "`--restore-code` checks out the original session's commit "
            "(checkout mutation) and `--worktree` moves execution out of the "
            "shared Living Tree — both forbidden for recovery"
        ),
    ),
    "cursor": ProviderCapability(
        agent="cursor",
        execution=EXECUTABLE,
        session_id_shape=_SESSION_TOKEN,
        session_id_sources=(
            "stream_json_init_event",
            "transcript_session_line",
            "run_meta",
        ),
        interactive_resume=SUPPORTED,
        noninteractive_resume=UNVERIFIED,
        native_fork=UNSUPPORTED,
        fork_runtime_restrictions="no fork surface in cursor-agent 2026.08.x",
        prompt_transport="stdin",
        session_identity_event=(
            "stream-json `system`/`init` event carrying `session_id`"
        ),
        cwd_safety=(
            "runs in invocation cwd; `--workspace` overrides; no "
            "checkout-mutating flags"
        ),
        resume_preserves_cache=None,
        forbidden_flags=(),
        probe_recipe=ProbeRecipe(
            cli="cursor-agent",
            required_markers=("--resume", "--print", "--output-format"),
        ),
        notes=(
            "cursor-agent 2026.08.25-3e8eec8 probed on host 2026-08-29: "
            "agent key is `cursor`, binary is `cursor-agent`; `-p` accepts "
            "prompt on argv or stdin; `--output-format stream-json` emits "
            "claude-shaped init/assistant/result events. Interactive "
            "`--resume [chatId]` exists; headless `-p --resume <id>` is "
            "UNVERIFIED — core native resume fails closed until proven"
        ),
    ),
}


def capability_for(agent: str) -> ProviderCapability:
    """Return the declarative capability record for one agent."""
    try:
        return CAPABILITIES[agent]
    except KeyError:
        raise ValueError(
            f"unknown agent {agent!r}; known: {', '.join(sorted(CAPABILITIES))}"
        ) from None


def capability_registry() -> dict[str, Any]:
    """Machine-diffable snapshot of the whole table (stable schema)."""
    return {
        "schema": "vibecrafted.continuity.capabilities.v1",
        "agents": {name: cap.to_dict() for name, cap in CAPABILITIES.items()},
    }


@dataclass(frozen=True)
class ProbeResult:
    """Installed-CLI evidence for one provider's continuity contract."""

    agent: str
    state: str  # confirmed | unsupported | probe_failed | evidence_only
    executable: str | None
    version: str | None
    markers: Mapping[str, bool] = field(default_factory=dict)
    detail: str = ""
    checked_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Flatten this probe result to a JSON-serializable dict."""
        return {
            "agent": self.agent,
            "state": self.state,
            "executable": self.executable,
            "version": self.version,
            "markers": dict(self.markers),
            "detail": self.detail,
            "checked_at": self.checked_at,
        }


_PROBE_CACHE: dict[str, ProbeResult] = {}


def clear_probe_cache() -> None:
    """Drop all cached per-agent probe results, forcing the next probe() to re-run."""
    _PROBE_CACHE.clear()


def _default_runner(timeout: float) -> Runner:
    """Build a subprocess-backed :data:`Runner` bound to a fixed timeout."""

    def run(cmd: Sequence[str]) -> CliProbe:
        """Execute one read-only CLI probe command and capture its result."""
        try:
            environment = dict(os.environ)
            environment["PATH"] = agent_tool_search_path(environment)
            completed = subprocess.run(
                list(cmd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except FileNotFoundError:
            return CliProbe(ok=False, returncode=None, stderr="not found")
        except subprocess.TimeoutExpired:
            return CliProbe(ok=False, returncode=None, stderr="timeout")
        except OSError as exc:  # pragma: no cover - defensive
            return CliProbe(ok=False, returncode=None, stderr=str(exc))
        return CliProbe(
            ok=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    return run


def _first_line(text: str) -> str | None:
    """First non-blank line of ``text`` (e.g. a version banner), or None."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def probe(
    agent: str,
    *,
    timeout: float = 10.0,
    runner: Runner | None = None,
    refresh: bool = False,
) -> ProbeResult:
    """Probe the installed provider CLI for its declared continuity contract.

    Read-only: only ``--version`` / ``--help`` style invocations, never a real
    session. Results are cached per agent; pass ``refresh=True`` to re-probe.

    States (spec §7 rule 6 — probe failure never proves incapability):

    * ``confirmed`` — binary runs and every declared marker is present;
    * ``unsupported`` — binary runs but declared markers are missing;
    * ``probe_failed`` — binary absent, broken, or timed out; autonomous
      recovery is unavailable, capability stays an open question;
    * ``evidence_only`` — provider must never be executed (gemini).
    """
    capability = capability_for(agent)
    if not refresh and agent in _PROBE_CACHE:
        return _PROBE_CACHE[agent]
    checked_at = utc_now_iso()

    if capability.execution == EVIDENCE_ONLY or capability.probe_recipe is None:
        result = ProbeResult(
            agent=agent,
            state=PROBE_EVIDENCE_ONLY,
            executable=None,
            version=None,
            markers={},
            detail=f"{agent} is evidence-only and is never executed",
            checked_at=checked_at,
        )
        _PROBE_CACHE[agent] = result
        return result

    recipe = capability.probe_recipe
    executable = shutil.which(recipe.cli, path=agent_tool_search_path())
    if executable is None:
        result = ProbeResult(
            agent=agent,
            state=PROBE_FAILED,
            executable=None,
            version=None,
            markers={},
            detail=(
                f"{recipe.cli} not found on $PATH — autonomous recovery "
                "unavailable; NOT proof the provider is incapable"
            ),
            checked_at=checked_at,
        )
        _PROBE_CACHE[agent] = result
        return result

    run = runner or _default_runner(timeout)
    version_probe = run([executable, *recipe.version_args])
    if not version_probe.ok:
        reason = (
            version_probe.stderr or version_probe.stdout or "non-zero exit"
        ).strip()
        result = ProbeResult(
            agent=agent,
            state=PROBE_FAILED,
            executable=executable,
            version=None,
            markers={},
            detail=(
                f"{recipe.cli} present at {executable} but failed to "
                f"execute: {reason} — NOT proof the provider is incapable"
            ),
            checked_at=checked_at,
        )
        _PROBE_CACHE[agent] = result
        return result

    version = _first_line(version_probe.stdout or version_probe.stderr)
    help_probe = run([executable, *recipe.help_args])
    surface = (help_probe.stdout or "") + "\n" + (help_probe.stderr or "")
    markers = {marker: marker in surface for marker in recipe.required_markers}
    missing = [marker for marker, found in markers.items() if not found]

    if missing:
        result = ProbeResult(
            agent=agent,
            state=PROBE_UNSUPPORTED,
            executable=executable,
            version=version,
            markers=markers,
            detail=(
                f"{recipe.cli} {version or '(unknown version)'} lacks "
                f"declared contract markers: {', '.join(missing)}"
            ),
            checked_at=checked_at,
        )
    else:
        result = ProbeResult(
            agent=agent,
            state=PROBE_CONFIRMED,
            executable=executable,
            version=version,
            markers=markers,
            detail=(
                f"{recipe.cli} {version or '(unknown version)'} exposes the "
                "full declared continuity contract"
            ),
            checked_at=checked_at,
        )
    _PROBE_CACHE[agent] = result
    return result


# Unambiguous alias for the package-root export (the foundation-tool probe is
# `probe_tool`; this one probes agent providers).
probe_provider = probe
