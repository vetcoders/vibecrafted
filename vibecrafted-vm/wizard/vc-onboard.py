#!/usr/bin/env python3
"""Bilingual (PL + EN) step-by-step wizard for vc-workspace dev container setup."""
# ============================================================================
# vc-onboard — bilingual (PL + EN) step-by-step wizard for vc-workspace
# dev container setup.
#
# Tabula rasa creator: walks operator through host selection, connection
# setup, profile pick, mount review, tailnet join, and finally builds +
# launches the container.
#
# Usage:
#   uv run vc-onboard.py
#   # or:
#   python3 -m pip install -r requirements.txt && python3 vc-onboard.py
#
# 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
# ============================================================================

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import questionary
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print(
        "Missing deps. Run: uv pip install questionary rich  (or: pip install -r requirements.txt)"
    )
    sys.exit(1)


# ── i18n loader ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
STRINGS_PATH = SCRIPT_DIR / "strings.json"

with open(STRINGS_PATH, encoding="utf-8") as f:
    STRINGS: dict[str, Any] = json.load(f)

console = Console()


def t(path: str, lang: str = "pl") -> str | list[str]:
    """Lookup a translated string by dotted path (e.g. 'welcome.title')."""
    node: Any = STRINGS
    for key in path.split("."):
        node = node[key]
    if isinstance(node, dict) and lang in node:
        return node[lang]
    return node


# ── State ──────────────────────────────────────────────────────────────────


@dataclass
class WizardState:
    """Mutable accumulator for wizard answers, threaded through every step function."""

    lang: str = "pl"
    host_mode: str = ""  # "local" | "tailnet" | "custom"
    tailnet_hostname: str = ""
    custom_host: str = ""
    custom_user: str = ""
    custom_port: int = 22
    custom_key: str = ""
    profile: str = ""  # "minimal" | "standard" | "sota"
    mounts: dict[str, bool] = field(default_factory=dict)
    tailscale_enabled: bool = False
    tailscale_hostname: str = ""
    tailscale_tags: str = "tag:devbox"


# ── Steps ──────────────────────────────────────────────────────────────────


def step_welcome(state: WizardState) -> None:
    """Step 0 — welcome banner (PL by default, EN toggle available)."""
    console.clear()
    title_pl = t("welcome.title", "pl")
    title_en = t("welcome.title", "en")
    body_pl = t("welcome.body", "pl")
    body_en = t("welcome.body", "en")

    console.print(
        Panel.fit(
            f"[bold cyan]{title_pl}[/bold cyan]\n\n{body_pl}",
            title="vc-onboard · PL",
            border_style="cyan",
        )
    )
    console.print()
    console.print(
        Panel.fit(
            f"[bold cyan]{title_en}[/bold cyan]\n\n{body_en}",
            title="vc-onboard · EN",
            border_style="cyan",
        )
    )
    console.print()
    input(
        str(t("welcome.continue_prompt", "pl"))
        + " / "
        + str(t("welcome.continue_prompt", "en"))
    )


def step_language(state: WizardState) -> None:
    """Step 1 — pick wizard language (sticky for rest)."""
    choice = questionary.select(
        "Język / Language",
        choices=["Polski / Polish", "English"],
    ).ask()
    state.lang = "pl" if choice and "Pol" in choice else "en"


def step_host(state: WizardState) -> None:
    """Step 2 — host selection."""
    options = t("host.options", state.lang)
    if not isinstance(options, list):
        options = []

    choice = questionary.select(
        str(t("host.prompt", state.lang)),
        choices=options,
    ).ask()

    if choice is None:
        sys.exit(0)

    state.host_mode = {0: "local", 1: "tailnet", 2: "custom"}[options.index(choice)]


def step_connection(state: WizardState) -> None:
    """Step 3 — connection setup based on host mode."""
    console.print(
        Panel.fit(str(t("connection.title", state.lang)), border_style="cyan")
    )

    if state.host_mode == "local":
        console.print(f"[green]✓[/green] {t('connection.local_note', state.lang)}")
        return

    if state.host_mode == "tailnet":
        state.tailnet_hostname = (
            questionary.text(
                str(t("connection.tailnet_prompt_hostname", state.lang)),
            ).ask()
            or ""
        )

        if state.tailnet_hostname:
            console.print(f"[dim]{t('connection.tailnet_test_msg', state.lang)}[/dim]")
            ok = ping_host(state.tailnet_hostname)
            if ok:
                console.print(
                    f"[green]✓[/green] {t('connection.test_success', state.lang)}"
                )
            else:
                console.print(
                    f"[yellow]⚠[/yellow] {t('connection.test_failed', state.lang)}"
                )
        return

    if state.host_mode == "custom":
        state.custom_host = (
            questionary.text(
                str(t("connection.custom_prompt_host", state.lang)),
            ).ask()
            or ""
        )
        state.custom_user = (
            questionary.text(
                str(t("connection.custom_prompt_user", state.lang)),
                default=os.environ.get("USER", "root"),
            ).ask()
            or ""
        )
        port_str = (
            questionary.text(
                str(t("connection.custom_prompt_port", state.lang)),
                default="22",
            ).ask()
            or "22"
        )
        state.custom_port = int(port_str)
        state.custom_key = (
            questionary.text(
                str(t("connection.custom_prompt_key", state.lang)),
                default=str(Path.home() / ".ssh" / "id_ed25519"),
            ).ask()
            or ""
        )


def step_profile(state: WizardState) -> None:
    """Step 4 — toolchain profile selection."""
    options = t("profile.options", state.lang)
    if not isinstance(options, list):
        options = []

    choice = questionary.select(
        str(t("profile.prompt", state.lang)),
        choices=options,
    ).ask()

    if choice is None:
        sys.exit(0)

    state.profile = {0: "minimal", 1: "standard", 2: "sota"}[options.index(choice)]


def step_mounts(state: WizardState) -> None:
    """Step 5 — review and confirm volume mounts."""
    console.print(Panel.fit(str(t("mounts.title", state.lang)), border_style="cyan"))
    console.print(str(t("mounts.intro", state.lang)))
    console.print()

    items_node = STRINGS["mounts"]["items"]
    item_keys = list(items_node.keys())
    item_labels = [items_node[key][state.lang] for key in item_keys]

    # Default: workspace + aicx_store + vibecrafted_artifacts + vetcoders_config + agent sessions ON;
    # keys + gnupg OFF by default (operator must opt-in).
    safe_defaults = {
        "workspace": True,
        "aicx_store": True,
        "keys": False,
        "gnupg": False,
        "claude_sessions": True,
        "codex_sessions": True,
        "gemini_sessions": True,
        "cursor_sessions": True,
        "vibecrafted_artifacts": True,
        "vetcoders_config": True,
    }

    selected_labels = (
        questionary.checkbox(
            "Space toggles, Enter confirms",
            choices=[
                questionary.Choice(
                    label, value=label, checked=safe_defaults.get(key, True)
                )
                for key, label in zip(item_keys, item_labels)
            ],
        ).ask()
        or []
    )

    for key, label in zip(item_keys, item_labels):
        state.mounts[key] = label in selected_labels


def step_tailscale(state: WizardState) -> None:
    """Step 6 — opt into Tailscale without persisting its auth key."""
    console.print(Panel.fit(str(t("tailscale.title", state.lang)), border_style="cyan"))
    console.print(str(t("tailscale.intro", state.lang)))
    console.print()

    options = t("tailscale.options", state.lang)
    if not isinstance(options, list):
        options = []

    choice = questionary.select(
        str(t("tailscale.skip_or_provide", state.lang)),
        choices=options,
    ).ask()

    if choice is None or options.index(choice) == 0:
        return

    state.tailscale_enabled = True

    state.tailscale_hostname = (
        questionary.text(
            str(t("tailscale.hostname_prompt", state.lang)),
            default=f"vc-workspace-{os.environ.get('USER', 'dev')}",
        ).ask()
        or ""
    )

    state.tailscale_tags = (
        questionary.text(
            str(t("tailscale.tags_prompt", state.lang)),
            default="tag:devbox",
        ).ask()
        or "tag:devbox"
    )


def step_review(state: WizardState) -> str:
    """Step 7 — review summary + final action choice."""
    table = Table(title=str(t("review.title", state.lang)), border_style="cyan")
    table.add_column("Pole" if state.lang == "pl" else "Field", style="bold")
    table.add_column("Wartość" if state.lang == "pl" else "Value")

    table.add_row("Host mode", state.host_mode)
    if state.host_mode == "tailnet":
        table.add_row("Tailnet hostname", state.tailnet_hostname)
    elif state.host_mode == "custom":
        table.add_row(
            "Custom host",
            f"{state.custom_user}@{state.custom_host}:{state.custom_port}",
        )
        table.add_row("SSH key", state.custom_key)
    table.add_row("Profile", state.profile)

    mounts_enabled = [k for k, v in state.mounts.items() if v]
    table.add_row(
        "Mounts (enabled)",
        ", ".join(mounts_enabled) if mounts_enabled else "(none)",
    )
    table.add_row(
        "Tailscale",
        "enabled (key injected at launch)" if state.tailscale_enabled else "skipped",
    )
    if state.tailscale_enabled:
        table.add_row("Tailnet hostname", state.tailscale_hostname)

    console.print(table)
    console.print()

    options = t("review.options", state.lang)
    if not isinstance(options, list):
        options = []

    choice = questionary.select(
        str(t("review.prompt", state.lang)),
        choices=options,
    ).ask()

    if choice is None:
        return "cancel"

    idx = options.index(choice)
    return {0: "build", 1: "save", 2: "cancel"}[idx]


# ── Side effects ───────────────────────────────────────────────────────────


def ping_host(hostname: str) -> bool:
    """Quick reachability check via ping (3 packet, 3s timeout)."""
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "3", hostname],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_docker() -> bool:
    """Verify docker CLI + daemon available."""
    return shutil.which("docker") is not None


def render_env_file(state: WizardState, target: Path) -> None:
    """Write .env file from wizard state for docker compose.

    Operator opted-in to every value via wizard prompts — no sensitive
    defaults baked in repo.
    """
    lines = [
        "# vc-workspace — generated by vc-onboard wizard",
        f"# Profile: {state.profile}",
        f"# Host: {state.host_mode}",
        f"# Generated: {os.popen('date -u +%Y-%m-%dT%H:%M:%SZ').read().strip()}",
        "",
        "# TAILSCALE_AUTHKEY is secret: inject it in the process environment at launch.",
        f"TAILSCALE_HOSTNAME={state.tailscale_hostname or 'vc-workspace'}",
        f"TAILSCALE_TAGS={state.tailscale_tags}",
        "",
        "# Host path to the multiroot repo tree mounted at /workspace.",
        "# Leave blank to use the compose default (~/.vibecrafted/vc-runtime).",
        f"VC_RUNTIME_DIR={os.environ.get('VC_RUNTIME_DIR', '')}",
        "",
        "# GPG (operator-supplied via host env)",
        f"LOCTREE_GPG_KEY_ID={os.environ.get('LOCTREE_GPG_KEY_ID', '')}",
        "LOCTREE_GPG_PASSPHRASE_FILE=/root/.keys/.gpg.passphrase",
        "",
        "AICX_NO_MUTATION_WARN=0",
        "TERM=xterm-256color",
        "COLORTERM=truecolor",
    ]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Mount-path mappings used by render_compose_file.
# Operator opts in to each via step_mounts checkbox; only checked items
# land in the rendered compose file. Defaults already exclude sensitive
# paths (keys, gnupg) unless explicitly toggled on.
MOUNT_SPEC: dict[str, tuple[str, str, str]] = {
    # key → (host_path, container_path, mode)
    # vc-runtime = the multiroot repo tree (NOT this container folder).
    # Override host path with VC_RUNTIME_DIR (e.g. /Volumes/shared-vol/vc-runtime).
    "workspace": (
        "${VC_RUNTIME_DIR:-${HOME}/.vibecrafted/vc-runtime}",
        "/workspace",
        "rw",
    ),
    "aicx_store": ("${HOME}/.aicx", "/root/.aicx", "rw"),
    "keys": ("${HOME}/.keys", "/root/.keys", "ro"),
    "gnupg": ("${HOME}/.gnupg", "/root/.gnupg", "ro"),
    "claude_sessions": ("${HOME}/.claude", "/root/.claude", "rw"),
    "codex_sessions": ("${HOME}/.codex", "/root/.codex", "rw"),
    "gemini_sessions": ("${HOME}/.gemini", "/root/.gemini", "rw"),
    "cursor_sessions": ("${HOME}/.cursor", "/root/.cursor", "rw"),
    "vibecrafted_artifacts": ("${HOME}/.vibecrafted", "/root/.vibecrafted", "rw"),
    "vetcoders_config": ("${HOME}/.config/vetcoders", "/root/.config/vetcoders", "rw"),
}


def render_compose_file(state: WizardState, target: Path) -> None:
    """Write docker-compose.yml dynamically based on opt-in mounts.

    No sensitive mounts (keys, gnupg) included unless operator explicitly
    toggled them on in step_mounts. This is the 'tabula rasa' contract —
    nothing baked in repo, every persistent path operator-confirmed.
    """
    lines = [
        "# vc-workspace compose — generated by vc-onboard wizard",
        "# DO NOT commit this file. Operator-specific mounts + secrets.",
        "",
        "services:",
        "  dev:",
        "    image: vetcoders/vc-workspace:trixie",
        "    build:",
        "      context: .",
        "      dockerfile: Containerfile",
        "    container_name: vc-workspace",
        "    hostname: ${TAILSCALE_HOSTNAME:-vc-workspace}",
        "    stdin_open: true",
        "    tty: true",
    ]

    # Tailscale userspace mode needs NET_ADMIN + /dev/net/tun (lightweight,
    # NOT --privileged). The key itself is supplied only in the launch process.
    if state.tailscale_enabled:
        lines += [
            "    cap_add:",
            "      - NET_ADMIN",
            "    devices:",
            "      - /dev/net/tun:/dev/net/tun",
        ]

    lines += ["    volumes:"]
    for key, enabled in state.mounts.items():
        if not enabled or key not in MOUNT_SPEC:
            continue
        host, container, mode = MOUNT_SPEC[key]
        lines.append(f"      - {host}:{container}:{mode}")

    if state.tailscale_enabled:
        lines.append("      - tailscale-state:/var/lib/tailscale")

    lines += [
        "    environment:",
        "      - TAILSCALE_AUTHKEY=${TAILSCALE_AUTHKEY:-}",
        "      - TAILSCALE_HOSTNAME=${TAILSCALE_HOSTNAME:-vc-workspace}",
        "      - TAILSCALE_TAGS=${TAILSCALE_TAGS:-tag:devbox}",
        "      - LOCTREE_GPG_KEY_ID=${LOCTREE_GPG_KEY_ID:-}",
        "      - LOCTREE_GPG_PASSPHRASE_FILE=${LOCTREE_GPG_PASSPHRASE_FILE:-/root/.keys/.gpg.passphrase}",
        "      - AICX_NO_MUTATION_WARN=${AICX_NO_MUTATION_WARN:-0}",
        "      - TERM=${TERM:-xterm-256color}",
        "      - COLORTERM=${COLORTERM:-truecolor}",
        "    restart: unless-stopped",
    ]

    if state.tailscale_enabled:
        lines += [
            "",
            "volumes:",
            "  tailscale-state:",
            "    driver: local",
        ]

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_and_run(state: WizardState) -> int:
    """Build container + run via docker compose."""
    repo_root = SCRIPT_DIR.parent  # vc-workspace/
    if state.tailscale_enabled and not os.environ.get(
        "TAILSCALE_AUTHKEY", ""
    ).startswith("tskey-auth-"):
        console.print(
            "[bold red]✗[/bold red] Tailscale is enabled, but a valid "
            "TAILSCALE_AUTHKEY was not injected into this launch process."
        )
        return 2
    console.print(f"[bold cyan]{t('actions.building', state.lang)}[/bold cyan]")

    build_cmd = [
        "docker",
        "compose",
        "-f",
        str(repo_root / "docker-compose.yml"),
        "build",
    ]
    rc = subprocess.run(build_cmd, cwd=repo_root, check=False).returncode
    if rc != 0:
        return rc

    console.print(f"[bold cyan]{t('actions.starting', state.lang)}[/bold cyan]")
    run_cmd = [
        "docker",
        "compose",
        "-f",
        str(repo_root / "docker-compose.yml"),
        "up",
        "-d",
    ]
    rc = subprocess.run(run_cmd, cwd=repo_root, check=False).returncode
    if rc != 0:
        return rc

    console.print(f"[bold green]✓ {t('actions.ready', state.lang)}[/bold green]")
    exec_cmd = [
        "docker",
        "compose",
        "-f",
        str(repo_root / "docker-compose.yml"),
        "exec",
        "dev",
        "zsh",
    ]
    return subprocess.run(exec_cmd, cwd=repo_root, check=False).returncode


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> int:
    """Run the full wizard flow and dispatch the operator's final review action."""
    if not check_docker():
        console.print(f"[bold red]✗[/bold red] {t('errors.docker_missing', 'pl')}")
        console.print(f"[bold red]✗[/bold red] {t('errors.docker_missing', 'en')}")
        return 1

    state = WizardState()

    try:
        step_welcome(state)
        step_language(state)
        step_host(state)
        step_connection(state)
        step_profile(state)
        step_mounts(state)
        step_tailscale(state)
        action = step_review(state)
    except KeyboardInterrupt:
        console.print(f"\n[yellow]{t('actions.cancelled', state.lang)}[/yellow]")
        return 130

    repo_root = SCRIPT_DIR.parent
    env_target = repo_root / ".env"
    compose_target = repo_root / "docker-compose.yml"

    render_env_file(state, env_target)
    render_compose_file(state, compose_target)
    console.print(
        f"[dim]→ {t('actions.config_saved', state.lang)}: {env_target} + docker-compose.yml[/dim]"
    )

    if action == "save":
        return 0
    if action == "cancel":
        console.print(f"[yellow]{t('actions.cancelled', state.lang)}[/yellow]")
        return 0

    return build_and_run(state)


if __name__ == "__main__":
    sys.exit(main())
