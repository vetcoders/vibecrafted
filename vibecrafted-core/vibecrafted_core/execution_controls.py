"""Public execution controls for skill launchers: ``--permissions`` / ``--sandbox``.

One owner for three things:

* the public words (``bypass|auto|accept-edits|read-only`` and ``true|false``),
  parsed identically by the core launcher and the shell contract;
* the per-provider sandbox contract, written from the installed provider CLIs
  (read-only ``--help`` and binary strings captured on 2026-09-08 — see
  ``SANDBOX_EVIDENCE``), never from an assumption that every agent sandboxes;
* the combined resolver that turns (provider, permissions, sandbox) into the
  exact provider argv, or refuses before any process launch with a supported
  alternative. A requested restriction is never downgraded silently:
  ``--permissions auto`` never becomes ``bypassPermissions`` and
  ``--sandbox true`` never becomes an unsandboxed run.

Permission semantics stay with :func:`vibecrafted_core.spawn.resolve_provider_policy`
(the ``_PERMISSION_CONTRACT`` owner); this module only composes the sandbox
axis on top of that decision and reports both requested and effective values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PERMISSION_POLICIES = ("bypass", "auto", "accept-edits", "read-only")
SANDBOX_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
SANDBOX_FALSE_WORDS = frozenset({"0", "false", "no", "off"})

# Supervised runtimes wrap their own provider launches; the public controls are
# not threaded through them in this cut and must be refused, not ignored.
SUPERVISED_RUNTIME_KINDS = frozenset({"supervised_research", "supervised_marbles"})

# Evidence for every sandbox cell below. Kept next to the contract so a receipt
# can name where a mapping came from; re-probe when a provider is upgraded.
SANDBOX_EVIDENCE: dict[str, str] = {
    "claude": (
        "claude 2.1.263 --help: no --sandbox flag; --settings <file-or-json>; "
        "binary carries settings keys sandbox.enabled, autoAllowBashIfSandboxed, "
        "excludedCommands, allowUnsandboxedCommands and seatbelt/bubblewrap runtime"
    ),
    "codex": (
        "codex-cli 0.154.0-alpha.3 `codex exec --help`: -s/--sandbox "
        "read-only|workspace-write|danger-full-access, --approve-for-me "
        "(automatic review inside the workspace-write sandbox), "
        "--dangerously-bypass-approvals-and-sandbox; exec has no --ask-for-approval"
    ),
    "grok": (
        "grok 1.0.21 --help: --sandbox <PROFILE> [env: GROK_SANDBOX]; embedded docs: "
        "built-in profiles workspace|devbox|read-only|strict, 'off' disables"
    ),
    "cursor": "cursor-agent 2026.09.08 --help: --sandbox <mode> enabled|disabled",
    "agy": (
        "agy 1.1.27 --help: --sandbox is a boolean opt-in "
        "('Run in a sandbox with terminal restrictions enabled'); no opt-out flag"
    ),
    "junie": "junie 26.8.31 --help: no sandbox surface",
}

_CLAUDE_SANDBOX_ON = json.dumps({"sandbox": {"enabled": True}}, separators=(",", ":"))
_CLAUDE_SANDBOX_OFF = json.dumps({"sandbox": {"enabled": False}}, separators=(",", ":"))


class ExecutionControlsError(ValueError):
    """A requested control cannot be enforced by the provider; refused pre-launch."""


def parse_permissions_word(value: Any, *, label: str = "--permissions") -> str:
    """Return one of :data:`PERMISSION_POLICIES` or ``""`` when omitted."""
    if value is None:
        return ""
    word = str(value).strip().lower()
    if not word:
        return ""
    if word not in PERMISSION_POLICIES:
        raise ExecutionControlsError(
            f"{label} expects {'|'.join(PERMISSION_POLICIES)}, got: {value}"
        )
    return word


def parse_sandbox_word(value: Any, *, label: str = "--sandbox") -> bool | None:
    """Return ``True``/``False`` for a sandbox word, ``None`` when omitted."""
    if value is None or isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if not word:
        return None
    if word in SANDBOX_TRUE_WORDS:
        return True
    if word in SANDBOX_FALSE_WORDS:
        return False
    raise ExecutionControlsError(f"{label} expects true or false, got: {value}")


def default_permissions(provider: str) -> str:
    """The policy a launcher applies when ``--permissions`` is omitted."""
    return "auto" if provider == "junie" else "bypass"


def sandbox_word(value: bool | None) -> str:
    """Public spelling of a parsed sandbox value (``""`` when omitted)."""
    if value is None:
        return ""
    return "true" if value else "false"


@dataclass(frozen=True)
class ExecutionControls:
    """Requested and effective execution controls for one provider launch."""

    provider: str
    permissions_requested: str
    permissions_effective: str
    sandbox_requested: bool | None
    sandbox_effective: str
    provider_flags: tuple[str, ...]
    behavior: str
    evidence: str

    @property
    def requested(self) -> bool:
        """True when the caller passed at least one control explicitly."""
        return bool(self.permissions_requested) or self.sandbox_requested is not None

    def receipt(self) -> dict[str, Any]:
        """Launch-receipt / meta projection: requested next to effective."""
        return {
            "schema": "vibecrafted.execution_controls.v1",
            "provider": self.provider,
            "permissions_requested": self.permissions_requested,
            "permissions_effective": self.permissions_effective,
            "sandbox_requested": sandbox_word(self.sandbox_requested),
            "sandbox_effective": self.sandbox_effective,
            "provider_flags": list(self.provider_flags),
            "behavior": self.behavior,
            "evidence": self.evidence,
        }


def _refuse(provider: str, message: str) -> ExecutionControlsError:
    return ExecutionControlsError(f"{provider}: {message}")


def _apply_sandbox(
    provider: str,
    permissions: str,
    sandbox: bool | None,
    base_flags: tuple[str, ...],
) -> tuple[tuple[str, ...], str, str]:
    """Compose the sandbox axis onto the permission flags for one provider.

    Returns ``(flags, sandbox_effective, note)``. Raises
    :class:`ExecutionControlsError` with an exact alternative when the installed
    provider cannot enforce the requested value.
    """
    if provider == "claude":
        if sandbox is None:
            return base_flags, "provider-default", "sandbox left to Claude settings"
        setting = _CLAUDE_SANDBOX_ON if sandbox else _CLAUDE_SANDBOX_OFF
        state = "enabled" if sandbox else "disabled"
        return (
            (*base_flags, "--settings", setting),
            state,
            f"sandbox {state} through --settings (sandbox.enabled)",
        )

    if provider == "codex":
        if permissions == "bypass":
            if sandbox is True:
                # The bypass flag disables the sandbox; the only way to keep
                # approvals off AND the sandbox on is the plain sandbox policy
                # (`codex exec` never prompts).
                return (
                    ("--sandbox", "workspace-write"),
                    "enabled",
                    (
                        "codex exec runs non-interactively (no approval prompts); "
                        "workspace-write sandbox kept"
                    ),
                )
            return (
                base_flags,
                "disabled",
                "--dangerously-bypass-approvals-and-sandbox runs without a sandbox",
            )
        if permissions == "auto":
            if sandbox is False:
                raise _refuse(
                    provider,
                    "--permissions auto runs under --approve-for-me, which is bound "
                    "to the workspace-write sandbox, so --sandbox false cannot be "
                    "enforced. Use `--permissions bypass --sandbox false` or omit "
                    "--sandbox.",
                )
            return (
                base_flags,
                "enabled",
                "--approve-for-me reviews approvals inside the workspace-write sandbox",
            )
        if permissions == "read-only":
            if sandbox is False:
                raise _refuse(
                    provider,
                    "--permissions read-only is enforced by the read-only sandbox, "
                    "so --sandbox false cannot be enforced. Omit --sandbox or pass "
                    "--sandbox true.",
                )
            return base_flags, "enabled", "read-only sandbox enforces the policy"
        return base_flags, "provider-default", ""

    if provider == "grok":
        if sandbox is None:
            return base_flags, "provider-default", "sandbox left to grok config"
        if sandbox:
            profile = "read-only" if permissions == "read-only" else "workspace"
            return (
                (*base_flags, "--sandbox", profile),
                "enabled",
                f"grok built-in sandbox profile {profile}",
            )
        return (*base_flags, "--sandbox", "off"), "disabled", "grok --sandbox off"

    if provider == "cursor":
        if sandbox is None:
            return base_flags, "provider-default", "sandbox left to cursor config"
        mode = "enabled" if sandbox else "disabled"
        return (
            (*base_flags, "--sandbox", mode),
            mode,
            f"cursor-agent --sandbox {mode} (verified against the installed --help)",
        )

    if provider == "agy":
        if sandbox is None:
            return base_flags, "provider-default", "sandbox left to agy config"
        if sandbox:
            return (
                (*base_flags, "--sandbox"),
                "enabled",
                "agy --sandbox (terminal restrictions)",
            )
        return (
            base_flags,
            "disabled",
            "agy sandbox is opt-in only; no flag emitted",
        )

    if provider == "junie":
        if sandbox is None:
            return base_flags, "provider-default", "junie exposes no sandbox control"
        raise _refuse(
            provider,
            "junie 26.8.31 exposes no sandbox control, so --sandbox "
            f"{sandbox_word(sandbox)} cannot be enforced. Omit --sandbox.",
        )

    if sandbox is None:
        return base_flags, "provider-default", ""
    raise _refuse(
        provider,
        "no sandbox contract is recorded for this provider; omit --sandbox",
    )


def resolve_execution_controls(
    provider: str,
    *,
    permissions: str = "",
    sandbox: bool | None = None,
    mode: str = "headless",
) -> ExecutionControls:
    """Resolve the exact provider argv for the requested controls, or refuse.

    ``permissions`` is one of :data:`PERMISSION_POLICIES` or ``""`` (provider
    default: ``bypass``, ``auto`` for junie). ``sandbox`` is ``True``/``False``
    or ``None`` (provider default). Raises :class:`ExecutionControlsError`
    naming the provider and a supported alternative whenever the installed CLI
    cannot enforce the combination.
    """
    from .spawn import resolve_provider_policy

    requested = parse_permissions_word(permissions)
    effective = requested or default_permissions(provider)
    try:
        policy = resolve_provider_policy(provider, "local-native", effective, mode)
    except ValueError as exc:
        raise _refuse(
            provider, f"--permissions {effective} is not resolvable ({exc})"
        ) from exc
    if not policy.supported:
        supported = [
            word
            for word in PERMISSION_POLICIES
            if resolve_provider_policy(provider, "local-native", word, mode).supported
        ]
        raise _refuse(
            provider,
            f"cannot enforce --permissions {effective} ({policy.reason}); "
            f"supported: {', '.join(supported) or 'none'}",
        )
    flags, sandbox_effective, note = _apply_sandbox(
        provider, effective, sandbox, tuple(policy.flags)
    )
    behavior = policy.behavior
    if note:
        behavior = f"{behavior}; {note}" if behavior else note
    return ExecutionControls(
        provider=provider,
        permissions_requested=requested,
        permissions_effective=effective,
        sandbox_requested=sandbox,
        sandbox_effective=sandbox_effective,
        provider_flags=tuple(flags),
        behavior=behavior,
        evidence=SANDBOX_EVIDENCE.get(provider, ""),
    )
