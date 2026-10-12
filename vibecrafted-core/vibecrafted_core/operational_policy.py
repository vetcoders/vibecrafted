"""Launch-time Vibecrafted operational policy: one source, provider transports.

The policy text lives once, in the packaged runtime resource
``runtime/policy/OPERATIONAL_POLICY.md``. Every launcher renders it from there;
no builder, shell heredoc or test carries a copy. This module owns the single
decision of *how* one launch receives it:

* ``native`` -- an additive system-instruction option the provider CLI
  advertises (Claude ``--append-system-prompt``, Grok ``--rules``, Junie
  ``--system-prompt=``). Used only when the launch opens a fresh provider
  conversation: Claude records the first system prompt and replays it verbatim
  on every later request and resume until compaction
  (``--system-prompt-snapshot`` default), so a flag on a continuation proves
  nothing; Grok and Junie refresh semantics are unverified. The private task
  then carries a short digest reference, never a second copy.
* ``current_turn`` -- the full policy inside the private task (stdin, private
  file or stream-json turn, carried by :mod:`prompt_transport`). Every provider
  without a verified additive option and every continuation (resume, fork).
* ``unsupported`` -- the launch has no initial turn that could carry it (the
  Kimi interactive TUI; an idle bare fork). The receipt says so and the
  launcher prints a visible notice. Loss is never silent.

Options that would replace provider defaults (Claude ``--system-prompt``, Grok
``--system-prompt-override``, Kimi ``--agent-file``) are refused, never used.
Composition here proves argv/stdin shape only; whether a provider model read
the policy is native acceptance, outside this module.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .package_resources import resource_path

POLICY_SCHEMA = "vibecrafted.operational-policy.v1"
POLICY_VERSION = 1
POLICY_RESOURCE = ("runtime", "policy", "OPERATIONAL_POLICY.md")
# Compatibility guard: "0" forces every launch onto the current-turn lane
# (for example an older provider CLI that rejects the additive option).
NATIVE_ENV = "VIBECRAFTED_OPERATIONAL_POLICY_NATIVE"

NATIVE = "native"
CURRENT_TURN = "current_turn"
UNSUPPORTED = "unsupported"

FRESH = "fresh"
RESUME = "resume"
FORK = "fork"
IDLE_FORK = "idle_fork"
CONTINUATIONS = (FRESH, RESUME, FORK, IDLE_FORK)

INTERACTIVE = "interactive"
HEADLESS = "headless"
SUPERVISED_CHILD = "supervised_child"
LEGACY_SHELL = "legacy_shell"
# Lanes whose Python builder owns the final provider argv and can add the
# native option. The legacy shell spawners compose argv in shell, so the
# policy always rides their private prompt file instead.
NATIVE_ARGV_LANES = frozenset({INTERACTIVE, HEADLESS, SUPERVISED_CHILD})
# Lane -> continuations its launchers can produce (the delivery matrix).
LANE_CONTINUATIONS: Mapping[str, tuple[str, ...]] = {
    INTERACTIVE: CONTINUATIONS,
    HEADLESS: (FRESH, RESUME, FORK),
    SUPERVISED_CHILD: (FRESH, RESUME),
    LEGACY_SHELL: (FRESH,),
}

POLICY_PROVIDERS = (
    "codex",
    "claude",
    "agy",
    "grok",
    "junie",
    "cursor",
    "kimi",
    "copilot",
)
# The policy body must never contain the headless wrapper's final delimiter:
# workflow._legacy_preassembly_spec recovers the operator source after it.
FORBIDDEN_MARKERS = ("Operator prompt:",)
_ACCEPTANCE = (
    "launcher composition only; provider receipt of the policy is not verified "
    "by the launcher (native acceptance is a separate, parent-gated step)"
)


@dataclass(frozen=True)
class NativeTransport:
    """One verified additive system-instruction option of a provider CLI."""

    provider: str
    option: str
    joined: bool  # True: one ``--opt=TEXT`` token; False: ``--opt TEXT``
    replacing: tuple[str, ...]  # options that would replace provider defaults
    continuation: str  # why a continuation cannot rely on this option
    evidence: str

    def args(self, text: str) -> list[str]:
        """Render the option for *text* in the provider's accepted shape."""
        if self.joined:
            return [f"{self.option}={text}"]
        return [self.option, text]


NATIVE_TRANSPORTS: Mapping[str, NativeTransport] = {
    "claude": NativeTransport(
        provider="claude",
        option="--append-system-prompt",
        joined=False,
        replacing=("--system-prompt", "--system-prompt-file"),
        continuation=(
            "claude records the first system prompt and replays it on every "
            "later request and resume until compaction (--system-prompt-snapshot "
            "on by default)"
        ),
        evidence=(
            "claude 2.1.296 --help: 'Append a system prompt to the default "
            "system prompt'"
        ),
    ),
    "grok": NativeTransport(
        provider="grok",
        option="--rules",
        joined=False,
        replacing=("--system-prompt-override", "--system-prompt"),
        continuation="grok refresh of --rules on resume/fork is unverified",
        evidence="grok 1.0.50 --help: 'Extra rules to append to the system prompt'",
    ),
    "junie": NativeTransport(
        provider="junie",
        option="--system-prompt",
        joined=True,
        replacing=(),
        continuation="junie persistence of --system-prompt on resume is unverified",
        evidence=(
            "junie build 3760.1 --help (saved receipt): 'Additional system prompt "
            "for the main agent'"
        ),
    ),
}

# Providers without a verified additive option, with the evidence that says so.
NO_NATIVE_REASONS: Mapping[str, str] = {
    "codex": (
        "codex-cli 0.162.1 --help advertises no additive system-instruction "
        "option; a generic -c key is not evidence of one"
    ),
    "agy": "agy 1.3.3 --help advertises no system-instruction option",
    "cursor": "cursor-agent 2026.09.28 --help advertises no system-instruction option",
    "copilot": (
        "copilot 1.0.95 --help advertises no additive system option; its custom "
        "instructions are discovered from global files"
    ),
    "kimi": (
        "kimi 2.1.1 --agent-file renders the file as the whole system prompt, "
        "selects another profile and refuses --session/--continue"
    ),
}
# Options that replace a provider's own system prompt for providers without a
# native transport entry. Never emitted; their presence is refused.
REPLACING_OPTIONS: Mapping[str, tuple[str, ...]] = {
    **{name: transport.replacing for name, transport in NATIVE_TRANSPORTS.items()},
    "kimi": ("--agent-file", "--agent"),
}
# Lanes in which a provider launch has no initial user turn at all.
NO_INITIAL_TURN: Mapping[tuple[str, str], str] = {
    ("kimi", "interactive"): (
        "kimi interactive TUI accepts no initial prompt (-p is non-interactive) "
        "and has no default-preserving system option"
    ),
}


def policy_path() -> Path:
    """The packaged policy resource (single source of truth)."""
    return resource_path(*POLICY_RESOURCE)


def policy_bytes() -> bytes:
    """Raw policy bytes, validated against the wrapper delimiter contract."""
    data = policy_path().read_bytes()
    text = data.decode("utf-8")
    for marker in FORBIDDEN_MARKERS:
        if marker in text:
            raise ValueError(f"operational policy must not contain {marker!r}")
    return data


def policy_text() -> str:
    """The policy body exactly as packaged, without trailing whitespace."""
    return policy_bytes().decode("utf-8").rstrip()


def policy_digest() -> str:
    """sha256 of the packaged policy bytes (attribution in receipts and turns)."""
    return hashlib.sha256(policy_bytes()).hexdigest()


@dataclass(frozen=True)
class PolicyDelivery:
    """The resolved transport of the policy for one provider launch."""

    provider: str
    lane: str
    continuation: str
    mode: str
    reason: str
    digest: str

    @property
    def transport(self) -> NativeTransport | None:
        """The native option in use, when this delivery is native."""
        return NATIVE_TRANSPORTS.get(self.provider) if self.mode == NATIVE else None

    def native_args(self) -> list[str]:
        """Additive system-instruction argv for a native delivery, else none."""
        transport = self.transport
        return transport.args(policy_text()) if transport is not None else []

    def turn_block(self) -> str:
        """Text for the private task: a digest reference or the full policy."""
        label = f"Vibecrafted operational policy v{POLICY_VERSION} (sha256:{self.digest[:16]})"
        if self.mode == NATIVE:
            transport = self.transport
            option = transport.option if transport is not None else "system"
            return (
                f"{label} is attached to your system instructions by this launcher "
                f"({option}). Follow it together with this task."
            )
        return (
            f"{label} follows. It is delivered in this turn ({self.reason}) and "
            "supersedes any earlier copy in this conversation.\n\n"
            f"{policy_text()}"
        )

    def notice(self) -> str:
        """Visible one-line notice for an undelivered policy, else empty."""
        if self.mode != UNSUPPORTED:
            return ""
        return (
            f"vibecrafted: operational policy NOT delivered to {self.provider} "
            f"({self.reason}); ask the agent to read {policy_path()}"
        )

    def receipt(self) -> dict[str, Any]:
        """Machine receipt for run metadata."""
        transport = NATIVE_TRANSPORTS.get(self.provider)
        return {
            "schema": POLICY_SCHEMA,
            "version": POLICY_VERSION,
            "digest": self.digest,
            "source": "/".join(POLICY_RESOURCE),
            "provider": self.provider,
            "lane": self.lane,
            "continuation": self.continuation,
            "delivery": self.mode,
            "native_option": transport.option
            if self.mode == NATIVE and transport
            else "",
            "reason": self.reason,
            "acceptance": _ACCEPTANCE,
        }


def _native_enabled(env: Mapping[str, str] | None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(NATIVE_ENV, "1")).strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def plan_delivery(
    provider: str,
    *,
    lane: str,
    continuation: str = FRESH,
    env: Mapping[str, str] | None = None,
) -> PolicyDelivery:
    """Resolve how one launch receives the policy. Pure apart from the digest."""
    name = str(provider or "").strip().lower()
    if name not in POLICY_PROVIDERS:
        raise ValueError(f"operational policy: unsupported provider {provider!r}")
    if lane not in LANE_CONTINUATIONS:
        raise ValueError(f"operational policy: unknown lane {lane!r}")
    if continuation not in LANE_CONTINUATIONS[lane]:
        raise ValueError(
            f"operational policy: lane {lane!r} has no continuation {continuation!r}"
        )
    digest = policy_digest()
    missing_turn = NO_INITIAL_TURN.get((name, lane))
    if missing_turn:
        return PolicyDelivery(
            name, lane, continuation, UNSUPPORTED, missing_turn, digest
        )
    if continuation == IDLE_FORK:
        return PolicyDelivery(
            name,
            lane,
            continuation,
            UNSUPPORTED,
            "idle bare fork opens without an initial turn; the fork carries only "
            "what its parent conversation already held",
            digest,
        )
    transport = NATIVE_TRANSPORTS.get(name)
    if transport is None:
        return PolicyDelivery(
            name, lane, continuation, CURRENT_TURN, NO_NATIVE_REASONS[name], digest
        )
    if continuation != FRESH:
        return PolicyDelivery(
            name, lane, continuation, CURRENT_TURN, transport.continuation, digest
        )
    if lane not in NATIVE_ARGV_LANES:
        return PolicyDelivery(
            name,
            lane,
            continuation,
            CURRENT_TURN,
            "the legacy shell spawner composes provider argv in shell; the policy "
            "rides its private prompt file",
            digest,
        )
    if not _native_enabled(env):
        return PolicyDelivery(
            name,
            lane,
            continuation,
            CURRENT_TURN,
            f"native transport disabled by {NATIVE_ENV}",
            digest,
        )
    return PolicyDelivery(name, lane, continuation, NATIVE, transport.evidence, digest)


def assert_additive(provider: str, command: Sequence[str]) -> None:
    """Refuse an argv that would replace the provider's default system prompt."""
    for option in REPLACING_OPTIONS.get(str(provider or "").lower(), ()):
        for token in command:
            if token == option or str(token).startswith(f"{option}="):
                raise ValueError(
                    f"{provider}: {option} replaces the provider's default system "
                    "prompt; Vibecrafted only adds operational policy"
                )


def with_native_args(
    command: Sequence[str], delivery: PolicyDelivery | None
) -> list[str]:
    """Insert native policy argv right after argv[0]; identity otherwise.

    Position directly after the executable keeps every later positional
    (prompt, session id) and trailing pop/insert contract unchanged.
    """
    argv = list(command)
    if delivery is None or not argv:
        return argv
    assert_additive(delivery.provider, argv)
    extra = delivery.native_args()
    if not extra:
        return argv
    return [argv[0], *extra, *argv[1:]]


def with_turn_block(prompt: str, delivery: PolicyDelivery | None) -> str:
    """Place the policy turn block ahead of a private task body.

    Composers that own a structured prompt (interactive orientation, the
    headless runtime contract, supervised children) insert the same block
    themselves, still before the operator task.
    """
    if delivery is None:
        return prompt
    return f"{delivery.turn_block()}\n\n{prompt}"


def emit_notice(delivery: PolicyDelivery | None) -> None:
    """Print the unsupported-delivery notice to stderr (visible in the pane)."""
    message = delivery.notice() if delivery is not None else ""
    if message:
        print(message, file=sys.stderr, flush=True)


def delivery_matrix(env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Every provider × lane × continuation the launchers use, as receipts."""
    rows: list[dict[str, Any]] = []
    for provider in POLICY_PROVIDERS:
        for lane, continuations in LANE_CONTINUATIONS.items():
            for continuation in continuations:
                rows.append(
                    plan_delivery(
                        provider, lane=lane, continuation=continuation, env=env
                    ).receipt()
                )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m vibecrafted_core.operational_policy`` -- shell/inspection lane.

    ``turn AGENT [--continuation C] [--lane L]`` prints the private-task block,
    ``path`` and ``digest`` print the source identity, ``matrix`` prints JSON.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="vibecrafted_core.operational_policy")
    sub = parser.add_subparsers(dest="command", required=True)
    turn = sub.add_parser("turn", help="print the private-task policy block")
    turn.add_argument("agent", choices=POLICY_PROVIDERS)
    turn.add_argument("--lane", choices=tuple(LANE_CONTINUATIONS), default=LEGACY_SHELL)
    turn.add_argument("--continuation", choices=CONTINUATIONS, default=FRESH)
    sub.add_parser("path", help="print the packaged policy path")
    sub.add_parser("digest", help="print the policy sha256")
    sub.add_parser("matrix", help="print the transport matrix as JSON")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "turn":
        delivery = plan_delivery(
            args.agent, lane=args.lane, continuation=args.continuation
        )
        print(delivery.turn_block())
        return 0
    if args.command == "path":
        print(policy_path())
        return 0
    if args.command == "digest":
        print(policy_digest())
        return 0
    print(json.dumps(delivery_matrix(), indent=2))
    return 0


if __name__ == "__main__":  # module entry
    raise SystemExit(main())
