from __future__ import annotations

import io
import itertools
import shlex
import sys

import pytest
from vibecrafted_core.spawn import (
    PERMISSION_POLICIES,
    POLICY_MODES,
    POLICY_PROVIDERS,
    RUNTIME_POLICIES,
    interactive_policy_command,
    main,
    resolve_provider_policy,
)


def test_every_runtime_permission_provider_mode_cell_is_explicit() -> None:
    cells = [
        resolve_provider_policy(provider, runtime, permissions, mode)
        for provider, runtime, permissions, mode in itertools.product(
            POLICY_PROVIDERS, RUNTIME_POLICIES, PERMISSION_POLICIES, POLICY_MODES
        )
    ]

    assert len(cells) == 5 * 4 * 4 * 2
    assert all(cell.behavior or cell.reason for cell in cells)
    assert all(cell.supported != bool(cell.reason) for cell in cells)


@pytest.mark.parametrize("provider", POLICY_PROVIDERS)
def test_non_native_runtimes_are_honestly_unsupported(provider: str) -> None:
    assert (
        "worktree cut contract"
        in resolve_provider_policy(
            provider, "local-worktrees", "bypass", "interactive"
        ).reason
    )
    assert (
        "VM entrypoint"
        in resolve_provider_policy(provider, "local-vm", "bypass", "interactive").reason
    )
    assert (
        "coming soon"
        in resolve_provider_policy(
            provider, "cloud-soon", "bypass", "interactive"
        ).reason
    )


def test_accept_edits_is_native_or_unsupported_never_approximated() -> None:
    for provider in ("claude", "agy", "grok"):
        decision = resolve_provider_policy(
            provider, "local-native", "accept-edits", "headless"
        )
        assert decision.supported
        assert "edits pass" in decision.behavior
        assert "fail closed" in decision.behavior

    for provider in ("codex", "junie"):
        decision = resolve_provider_policy(
            provider, "local-native", "accept-edits", "interactive"
        )
        assert not decision.supported
        assert "no native accept-edits" in decision.reason


def test_junie_interactive_only_policies_fail_closed_headless() -> None:
    assert resolve_provider_policy(
        "junie", "local-native", "bypass", "interactive"
    ).supported
    assert not resolve_provider_policy(
        "junie", "local-native", "bypass", "headless"
    ).supported
    assert not resolve_provider_policy(
        "junie", "local-native", "read-only", "headless"
    ).supported


def test_interactive_command_uses_contract_flags() -> None:
    command = interactive_policy_command(
        "claude", "/vc-init", "local-native", "accept-edits"
    )
    assert command == [
        "claude",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "/vc-init",
    ]

    with pytest.raises(ValueError, match="no native accept-edits"):
        interactive_policy_command("codex", "/vc-init", "local-native", "accept-edits")


def test_policy_cli_reads_the_same_contract(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("/vc-init"))

    assert (
        main(
            [
                "policy-command",
                "grok",
                "--runtime",
                "local-native",
                "--permissions",
                "read-only",
            ]
        )
        == 0
    )
    assert shlex.split(capsys.readouterr().out) == [
        "grok",
        "--cwd",
        ".",
        "--permission-mode",
        "plan",
        "--no-alt-screen",
        "/vc-init",
    ]
