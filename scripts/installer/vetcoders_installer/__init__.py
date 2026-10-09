"""Sequential trust-building installer runner.

Reads an ``install.toml`` manifest describing a sequence of install phases,
and replays each one with:

    • a per-phase reason block (trust-building)
    • an explicit consent prompt (pauses the Live region)
    • a sticky-bottom progress bar that stays visible across phases
    • cargo-style streaming of subprocess output above the bar
    • a curated summary at the end

Universal: works for Python/Rust/any repo because it only orchestrates
subprocess commands declared in the manifest. It never knows what the
commands do.

Distribution:
    uv tool install ~/Libraxis/vetcoders-tools/installer --force

Usage:
    vetcoders-installer install.toml
    vetcoders-installer install.toml --yes
    vetcoders-installer install.toml --dry-run
    vetcoders-installer install.toml --quiet
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import timezone as timezone
from pathlib import Path
from typing import Any

try:
    import termios
    import tty

    _HAS_TERMIOS = True
except ImportError:  # Windows
    _HAS_TERMIOS = False

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    HAS_RICH = True
except ModuleNotFoundError:  # soft fallback path
    HAS_RICH = False

try:
    from vetcoders_installer.tui import InstallerIntroApp

    _HAS_TEXTUAL = True
except ImportError:  # textual not installed
    _HAS_TEXTUAL = False


__version__ = "0.1.0"
TOOL_NAME = "vetcoders-installer"


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass
class Phase:
    """One install.toml `[[phase]]` entry: a consented, labeled subprocess step."""

    key: str
    label: str
    reason: str
    cmd: list[str]
    cwd: Path
    optional: bool = False

    @property
    def reason_lines(self) -> list[str]:
        """Return the reason block as trailing-whitespace-stripped lines."""
        return [ln.rstrip() for ln in self.reason.strip().splitlines()]

    def matches(self, name: str) -> bool:
        """Case-insensitive match against this phase's key or label."""
        name = name.lower()
        return name == self.key.lower() or name == self.label.lower()


@dataclass
class Manifest:
    """Parsed install.toml: title, version, phases, branding, and intro screens."""

    title: str
    version: str
    log_pattern: str | None
    persist: bool
    phases: list[Phase]
    path: Path
    branding: dict[str, Any] = field(default_factory=dict)
    intro_screens: list[str] = field(default_factory=list)
    textual_screens: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        """Parse an install.toml file at path into a Manifest instance."""
        raw = path.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        repo_root = path.parent.resolve()

        title = str(data.get("title", "Installer"))
        version = _resolve_version(data, repo_root)
        log_pattern = data.get("log")
        persist = bool(data.get("persist", False))

        # Branding
        branding = data.get("branding", {})

        # Intro screens
        intro_data = data.get("intro", {})
        intro_screens = intro_data.get("screens", [])
        textual_screens = intro_data.get("textual_screens", [])

        # Diagnostics
        diagnostics_data = data.get("diagnostics", {})

        phases_data = data.get("phase", [])
        if not isinstance(phases_data, list):
            raise TypeError("install.toml: [[phase]] must be an array of tables")

        phases: list[Phase] = []
        for i, pd in enumerate(phases_data):
            label = str(pd.get("label", f"Phase {i + 1}"))
            key = str(
                pd.get("key") or label.lower().replace(" ", "_").replace("&", "and")
            )
            reason = str(pd.get("reason", ""))
            cmd = pd.get("cmd")
            if not cmd or not isinstance(cmd, list):
                raise ValueError(f"install.toml: phase '{label}' has no 'cmd' (list)")
            cwd_raw = pd.get("cwd")
            cwd = (repo_root / cwd_raw).resolve() if cwd_raw else repo_root
            phases.append(
                Phase(
                    key=key,
                    label=label,
                    reason=reason,
                    cmd=[str(x) for x in cmd],
                    cwd=cwd,
                    optional=bool(pd.get("optional", False)),
                )
            )

        return cls(
            title=title,
            version=version,
            log_pattern=log_pattern,
            persist=persist,
            phases=phases,
            path=path.resolve(),
            branding=branding,
            intro_screens=intro_screens,
            textual_screens=textual_screens,
            diagnostics=diagnostics_data,
        )


def _resolve_version(data: dict[str, Any], repo_root: Path) -> str:
    """Extract version from explicit value, VERSION file, or regex over a file."""
    if "version" in data:
        return str(data["version"])
    version_file = data.get("version_file")
    if not version_file:
        return ""
    vf_path = (repo_root / version_file).resolve()
    if not vf_path.exists():
        return ""
    content = vf_path.read_text(encoding="utf-8")
    pattern = data.get("version_pattern")
    if pattern:
        match = re.search(pattern, content, re.MULTILINE)
        if match:
            return match.group(1)
        return ""
    # No pattern: take the first non-empty line.
    for line in content.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


# ---------------------------------------------------------------------------
# Console abstraction — Rich when available, plain fallback otherwise
# ---------------------------------------------------------------------------


class _PlainConsole:
    """Minimal drop-in for rich.console.Console when rich is unavailable."""

    def print(self, *args: Any, **_kwargs: Any) -> None:
        """Print args space-joined, stripping Rich-style [tag] markup first."""
        msg = " ".join(str(a) for a in args)
        msg = re.sub(r"\[/?[a-zA-Z0-9 #_]+\]", "", msg)
        print(msg)

    def rule(self, title: str = "", **_kwargs: Any) -> None:
        """Print a plain-text horizontal rule with an optional centered title."""
        cleaned = re.sub(r"\[/?[a-zA-Z0-9 #_]+\]", "", title)
        bar = "─" * max(4, 60 - len(cleaned) - 2)
        print(f"\n── {cleaned} {bar}" if cleaned else "\n" + ("─" * 60))


def _make_console() -> Any:
    """Return a rich.Console when rich is installed, else the plain fallback."""
    return Console() if HAS_RICH else _PlainConsole()


def _line_style(line: str) -> str:
    """Heuristic dim colour for streamed subprocess output lines."""
    stripped = line.lstrip()
    low = stripped.lower()
    if stripped.startswith(("✓", "✔", "[ok]")) or low.startswith(("ok ", "ok:")):
        return "dim green"
    if stripped.startswith(("✗", "✘", "[fail]", "[error]")) or low.startswith(
        ("error", "fail", "fatal")
    ):
        return "dim red"
    if stripped.startswith(("[warn]", "[warning]")) or low.startswith(
        ("warn", "warning")
    ):
        return "dim yellow"
    return "dim"


# ---------------------------------------------------------------------------
# Consent prompt
# ---------------------------------------------------------------------------


def consent(console: Any, label: str, optional: bool, auto_yes: bool) -> str:
    """Return one of 'yes', 'skip', 'quit'.

    The caller is responsible for pausing any Live/Progress display before
    calling this — the prompt prints as ordinary stdout and must own the
    screen while the user reads it. See ``run_installer`` where the
    sticky bar is stopped around each consent cycle.
    """
    if auto_yes:
        return "yes"

    # Visible call-to-action rule + hint block. Users were mistaking the
    # spinning progress bar for work-in-progress, so the prompt now owns
    # its own space with a jumbo ▶ marker and explicit key list.
    if HAS_RICH:
        console.print()
        console.rule(f"[bold yellow]▶ Ready to run: {label}[/]", style="yellow")
        console.print("    [bold]ENTER[/]=run   [bold]n[/]/[bold]q[/]=cancel")
        console.rule(style="yellow")
        prompt_rich = "  [bold]❯[/] "
    else:
        print()
        hdr = f"▶ Ready to run: {label}"
        print(hdr)
        print("─" * len(hdr))
        print("    ENTER=run   n/q=cancel")

    prompt_plain = "  ❯ "

    try:
        if HAS_RICH and hasattr(console, "input"):
            raw = console.input(prompt_rich).strip().lower()
        else:
            raw = input(prompt_plain).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "quit"

    if raw in ("", "y", "yes"):
        return "yes"
    if raw in ("n", "no", "q", "quit"):
        return "quit"
    # Unknown → treat as yes (default) to stay out of the user's way
    return "yes"


# ---------------------------------------------------------------------------
# Phase runner
# ---------------------------------------------------------------------------


def run_phase(
    console: Any,
    phase: Phase,
    progress: Any,
    task_id: Any,
    log_handle: Any | None,
    quiet: bool,
    *,
    non_interactive: bool = False,
) -> int:
    """Execute one phase; stream subprocess output above the sticky bar.

    Rendering model (cargo-style):

      • the Progress bar is pinned to the bottom of the Live region
      • each non-empty stdout line is printed *through* the progress.console,
        so Rich's Live machinery lifts the bar above the new line and keeps
        it at the bottom of the viewport
      • markup=False / highlight=False stops subprocess strings like
        ``[error]`` from being interpreted as Rich markup

    When ``quiet=True`` the stream is suppressed and only the progress bar's
    ``cur`` field mirrors the last subprocess line.

    Returns the subprocess exit code.
    """
    if HAS_RICH and progress is not None:
        progress.update(task_id, cur="starting…")

    try:
        phase_env = os.environ.copy()
        if non_interactive:
            phase_env["VIBECRAFTED_INSTALL_NONINTERACTIVE"] = "1"
        proc = subprocess.Popen(
            phase.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(phase.cwd),
            env=phase_env,
        )
    except FileNotFoundError as exc:
        console.print(f"  [red]✗ command not found: {exc}[/]")
        return 127

    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue

        if log_handle is not None:
            log_handle.write(raw)
            log_handle.flush()

        if HAS_RICH and progress is not None:
            progress.update(task_id, cur=line[-72:])

        if not quiet:
            style = _line_style(line)
            if HAS_RICH:
                # print via the shared console so Live keeps the bar sticky;
                # markup=False / highlight=False protect against subprocess
                # strings that accidentally look like Rich markup.
                console.print(
                    f"  {line}",
                    style=style,
                    markup=False,
                    highlight=False,
                )
            else:
                console.print(f"  {line}")

    proc.wait()
    return proc.returncode


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------


def _read_key() -> str:
    """Read a single keypress from stdin. Returns a semantic key name.

    Handles arrow-key escape sequences, common navigation keys, and single
    characters.  Falls back to ``"enter"`` when stdin is not a TTY (piped
    input, CI) so the intro flow auto-advances without blocking.
    """
    if not sys.stdin.isatty() or not _HAS_TERMIOS:
        return "enter"
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # escape sequence
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "up"
                if ch3 == "B":
                    return "down"
            return "escape"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch in ("\x7f", "\x08"):
            return "backspace"
        if ch == "\t":
            return "tab"
        if ch == "q":
            return "quit"
        if ch == "b":
            return "back"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _parse_mock_layers(body: str) -> tuple[str, str, str]:
    """Split a mockup body into (header, content, footer) layers.

    Detection relies on separator lines containing at least 10 consecutive
    ``─`` (U+2500 box-drawing horizontal).  With four or more separators the
    layout is:

        header  = start .. second separator   (inclusive)
        footer  = second-to-last separator .. end   (inclusive)
        content = everything in between

    When fewer than four separators are found the body is returned as pure
    content with empty header/footer so the caller can still render it.
    """
    lines = body.splitlines()
    sep_indices = [i for i, line in enumerate(lines) if "\u2500" * 10 in line]

    if len(sep_indices) < 4:
        # Not enough separators -- treat entire body as content.
        return ("", body, "")

    # Header: lines 0 through second separator (inclusive).
    header_end = sep_indices[1]
    header = "\n".join(lines[: header_end + 1])

    # Footer: from second-to-last separator to end (inclusive).
    footer_start = sep_indices[-2]
    footer = "\n".join(lines[footer_start:])

    # Content: between header and footer.
    content = "\n".join(lines[header_end + 1 : footer_start])

    return (header, content, footer)


def _interpolate_mock(text: str, manifest: Manifest) -> str:
    """Replace placeholder tokens in mockup text with runtime values.

    Supported placeholders:
        ``{version}``          — manifest version (or ``"dev"``).
        ``$VIBECRAFTED_ROOT``  — env var or ``$HOME`` fallback.
        ``$HOME``              — user home directory.
        ``\U0001d167X.Y.Z``    — monospace unicode version in banner headers.
    """
    home = str(Path.home())

    # Store path from manifest or default to old behavior
    vibecrafted_root = os.environ.get("VIBECRAFTED_ROOT", home)
    if (
        manifest
        and manifest.diagnostics
        and manifest.diagnostics.get("paths", {}).get("store_dir")
    ):
        store_dir = manifest.diagnostics["paths"]["store_dir"]
        # Replace $VIBECRAFTED_ROOT inside store_dir if present
        store_dir = store_dir.replace("$VIBECRAFTED_ROOT", vibecrafted_root).replace(
            "$HOME", home
        )
        # But wait, the mock says "in $VIBECRAFTED_ROOT/.vibecrafted/". Let's just replace $VIBECRAFTED_ROOT for now.

    ver = manifest.version or "dev"
    text = text.replace("{version}", ver)
    # Replace hardcoded monospace-unicode version strings (𝚟X.Y.Z) in banners
    text = re.sub(r"𝚟\d+\.\d+\.\d+", f"𝚟{ver}", text)
    text = text.replace("$VIBECRAFTED_ROOT", vibecrafted_root)
    text = text.replace("$HOME", home)

    if manifest and manifest.branding:
        if "header" in manifest.branding:
            text = text.replace("⚒ V A P O R C R A F T ⚒", manifest.branding["header"])
            text = text.replace("⚒ Vibecrafted. ⚒", manifest.branding["header"])
        if "name" in manifest.branding:
            branded_name = manifest.branding["name"].rstrip(".")
            text = re.sub(r"\bVibecrafted\b", branded_name, text)
        if "unicode_wordmark" in manifest.branding:
            text = text.replace("⚒ Vibecrafted.", manifest.branding["unicode_wordmark"])
        if "footer_tagline" in manifest.branding:
            text = text.replace("FRAMEWORK", manifest.branding["footer_tagline"])

    return text


def _load_mock_screen(docs_dir: Path, name: str, *, manifest: Manifest) -> str | None:
    """Return the body of a mock screen from ``docs/installer/<name>``.

    Strips only the ``` shell fence — the banner, content, footer hint,
    navigation bar and FRAMEWORK tag authored by the designer are preserved
    exactly as written. The nav bar advertises keys (⇅ Nav, ␣ Sel, ⇥ View)
    that belong to the advanced interactive flow reached via
    ``make setup-dev`` (``vetcoders-installer install.toml --advanced``); it
    is a consistent reference across all screens, not a per-screen promise.

    ``{version}`` placeholders inside the mockup body are interpolated with
    the manifest version (or ``"dev"`` when unset).
    """
    path = docs_dir / name
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    body = "\n".join(lines)
    return _interpolate_mock(body, manifest)


def _print_line(console: Any, line: str) -> None:
    """Print a single line through Rich (markup-safe) or plain fallback."""
    if HAS_RICH:
        console.print(line, markup=False, highlight=False)
    else:
        print(line)


def _show_mock_screen(console: Any, body: str, *, can_back: bool = False) -> str:
    """Render a mockup with sticky header/footer and wait for a keypress.

    Parses the mockup body into three layers (header, content, footer)
    using separator-line detection.  The header stays at the top, content
    fills the middle, and the footer is pushed to the bottom of the
    terminal via blank-line padding.

    Falls back to line-buffered ``input()`` when stdin is not a TTY so
    CI / piped invocations still work.

    Returns one of:
      - ``"next"``  -- Enter / Down arrow / Space.
      - ``"back"``  -- Backspace / Up arrow (only meaningful when *can_back*).
      - ``"quit"``  -- Escape / ``q``.
    """
    header, content, footer = _parse_mock_layers(body)

    # Clear screen and move cursor to top-left.
    print("\033[2J\033[H", end="", flush=True)

    _cols, rows = shutil.get_terminal_size((80, 24))

    # -- Sticky header at top --
    if header:
        for line in header.splitlines():
            _print_line(console, line)

    # -- Scrollable content in the middle --
    content_lines = content.splitlines() if content else []
    for line in content_lines:
        _print_line(console, line)

    # -- Sticky footer pushed to bottom --
    header_count = len(header.splitlines()) if header else 0
    content_count = len(content_lines)
    footer_lines = footer.splitlines() if footer else []
    footer_count = len(footer_lines)
    # +1 for the status/prompt line that follows the footer.
    used = header_count + content_count + footer_count + 1
    padding = max(0, rows - used)
    if padding:
        print("\n" * padding, end="")

    if footer:
        for line in footer_lines:
            _print_line(console, line)

    # Status bar -- reflects real available actions for this position.
    if can_back:
        status = "  \u23ce proceed  \u232b back  \u238b quit"
    else:
        status = "  \u23ce proceed  \u238b quit"

    if not sys.stdin.isatty() or not _HAS_TERMIOS:
        # Non-interactive fallback: use input() so piped/CI runs advance.
        console.print()
        try:
            raw = input(f"  {status}  \u276f ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if raw in ("q", "quit", "n", "no", "esc", "escape"):
            return "quit"
        if raw in ("b", "back", "p", "prev", "-"):
            return "back"
        return "next"

    # Interactive path -- single keypress, no ENTER required.
    if HAS_RICH:
        console.print(f"[dim]{status}[/]")
    else:
        print(status)

    while True:
        try:
            key = _read_key()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"

        if key in ("escape", "quit"):
            return "quit"
        if key in ("backspace", "up", "back"):
            if can_back:
                return "back"
            continue  # ignore back keys on first screen
        if key in ("enter", "down", "space"):
            return "next"
        # any other key — ignore, wait for valid input
        continue


def _show_intro_flow(
    console: Any, manifest: Manifest, auto_yes: bool, advanced: bool = False
) -> str:
    """Show welcome / explain / listing screens before the phase loop.

    When ``textual`` is available the intro renders as a proper TUI with
    sticky header/footer and scrollable content.  Falls back to the manual
    ANSI loop (``_show_mock_screen``) when textual is missing, and to
    ``input()`` for non-TTY environments.

    Returns "cancelled" when the user cancels, "completed" if Textual ran the full wizard,
    and "fallback" to proceed with the rich progress loop.
    """
    if auto_yes:
        return "fallback"
    docs_dir = manifest.path.parent / "docs" / "installer"
    if not docs_dir.is_dir():
        return "fallback"
    version = manifest.version or "dev"
    screen_names: list[str] = manifest.intro_screens or [
        "0_welcome_step.zsh.md",
        "1_Explain_step.zsh.md",
        "2_listing.zsh.md",
    ]
    textual_screen_names = manifest.textual_screens or screen_names + [
        "3_examine.zsh.md",
        "4_result.zsh.md",
        "5_installation.zsh.md",
    ]

    # Non-TTY fallback (CI, piped) -- auto-advance.
    if not sys.stdin.isatty():
        return "fallback"

    # -- Textual TUI path (preferred) --
    if _HAS_TEXTUAL:
        # Build (header, content, footer) tuples for all 6 screens.
        screens: list[tuple[str, str, str]] = []
        for name in textual_screen_names:
            body = _load_mock_screen(docs_dir, name, manifest=manifest)
            if body is not None:
                header, content, footer = _parse_mock_layers(body)
                screens.append((header, content, footer))

        if screens:
            try:
                app = InstallerIntroApp(
                    screens, version, manifest.path.parent, advanced, manifest
                )
                app.run()
                if app.result == "quit":
                    console.print("\n  [yellow]Cancelled — no changes were made.[/]\n")
                    return "cancelled"
                return "completed"
            # The optional Textual renderer may fail inside third-party widgets; this preview must
            # fall back to ANSI rendering without performing an installation twice.
            except Exception as _textual_exc:  # noqa: BLE001
                # Textual crashed -- fall through to the manual path.
                _ = _textual_exc

    # -- Manual ANSI path --
    # Re-assemble bodies from layers for _show_mock_screen.
    bodies: list[str] = []
    for name in screen_names:
        body = _load_mock_screen(docs_dir, name, manifest=manifest)
        if body is not None:
            bodies.append(body)

    if not bodies:
        return "fallback"

    idx = 0
    while idx < len(bodies):
        can_back = idx > 0
        action = _show_mock_screen(console, bodies[idx], can_back=can_back)
        if action == "quit":
            console.print("\n  [yellow]Cancelled — no changes were made.[/]\n")
            return "cancelled"
        if action == "back" and can_back:
            idx -= 1
            continue
        # "back" on the first screen is a no-op -- fall through to "next".
        idx += 1
    return "fallback"


def _print_title(console: Any, manifest: Manifest) -> None:
    """Print the manifest title, appending the version when one is set."""
    if manifest.version:
        title = f"{manifest.title} v{manifest.version}"
    else:
        title = manifest.title
    console.print()
    console.print(f"[bold yellow]{title}[/]")
    console.print()


def _print_reason_block(console: Any, phase: Phase) -> None:
    """Print the phase label followed by its indented, dimmed reason lines."""
    console.print(f"[bold cyan]{phase.label}[/]")
    console.print()
    for line in phase.reason_lines:
        console.print(f"  [dim]{line}[/]")
    console.print()


def _print_summary(
    console: Any,
    manifest: Manifest,
    results: list[tuple[str, str, int]],
    log_path: Path | None,
    *,
    compact: bool = False,
) -> None:
    """Print the final run summary: verdict headline, per-phase results, next steps.

    ``compact`` renders a single status line (used for quiet+auto-yes runs)
    instead of the full landing-page-style report.
    """
    if not results:
        return

    # Compute verdict so the headline can say something real instead of
    # just "summary". Users are landing with 3-4 screens of scrollback
    # above this block, so the footer has to read like a landing page.
    has_fail = any(s.startswith(("failed", "error")) for _, s, _ in results)
    has_warn = any(s.startswith("warn") for _, s, _ in results)
    has_cancel = any(s in ("cancelled", "skipped") for _, s, _ in results)
    all_ok = not has_fail and not has_warn and not has_cancel
    # Pure cancel: user said no, nothing installed, nothing to audit.
    # We do NOT suggest commands that assume the install succeeded.
    pure_cancel = has_cancel and not has_fail and not has_warn

    product = (
        manifest.branding.get("name", "Installer") if manifest.branding else "Installer"
    )
    header_ready = (
        manifest.branding.get("unicode_wordmark", f"⚒ {product}")
        if manifest.branding
        else f"⚒ {product}"
    )

    if compact:
        if all_ok:
            console.print(f"[green]✓[/] {header_ready} ready")
        elif has_fail:
            failed = next(
                (
                    (label, state)
                    for label, state, _rc in results
                    if state.startswith(("failed", "error"))
                ),
                ("Install", "failed"),
            )
            console.print(f"[red]✗[/] {failed[0]} {failed[1]}")
        elif pure_cancel:
            console.print("[yellow]·[/] Install cancelled — nothing changed")
        else:
            console.print(f"[yellow]![/] {header_ready} finished with warnings")
        if log_path:
            console.print(f"Log: {log_path}")
        return

    console.print()
    if HAS_RICH:
        if all_ok:
            console.rule(f"[bold green]{header_ready} is ready[/]", style="green")
        elif has_fail:
            console.rule("[bold red]⚒ Install stopped with errors[/]", style="red")
        elif pure_cancel:
            console.rule(
                "[bold yellow]⚒ Install cancelled — nothing changed[/]", style="yellow"
            )
        else:
            console.rule(
                "[bold yellow]⚒ Install finished with warnings[/]", style="yellow"
            )
    else:
        if all_ok:
            console.print(f"=== {header_ready} is ready ===")
        elif has_fail:
            console.print("=== Install stopped with errors ===")
        elif pure_cancel:
            console.print("=== Install cancelled — nothing changed ===")
        else:
            console.print("=== Install finished with warnings ===")

    console.print()
    widest = max(len(label) for label, _, _ in results)
    for label, state, _rc in results:
        if state == "ok":
            icon = "[green]✓[/]"
        elif state.startswith("warn"):
            icon = "[yellow]![/]"
        elif state in ("skipped", "cancelled"):
            icon = "[yellow]·[/]"
        else:
            icon = "[red]✗[/]"
        console.print(f"  {icon} {label:<{widest}}  [dim]{state}[/]")
    console.print()

    # Next-step block. We only suggest commands that actually assume the
    # install happened on paths where the install actually happened.
    raw_next_steps = (
        manifest.branding.get("next_steps", []) if manifest.branding else []
    )
    next_steps: list[dict[str, Any]] = [
        step for step in raw_next_steps if isinstance(step, dict)
    ]

    installer_cmd = (
        str(manifest.branding.get("installer_cmd", "make install"))
        if manifest.branding
        else "make install"
    )

    if all_ok:
        console.print("  [bold]Next steps[/]")
        if next_steps:
            for step in next_steps:
                console.print(
                    f"    [cyan]▸[/] [bold]{step.get('cmd', '')}[/bold]     [dim]{step.get('desc', '')}[/dim]"
                )
        else:
            console.print(
                "    [cyan]▸[/] [bold]See documentation for next steps.[/bold]"
            )
        console.print()
    elif pure_cancel:
        # User explicitly said no.
        console.print("  Nothing was installed. Re-run when you are ready:")
        console.print(
            f"    [cyan]▸[/] [bold]{installer_cmd}[/bold]        [dim]run the guided installer again[/]"
        )
        console.print()
    elif has_fail:
        console.print("  [bold]Recovery[/]")
        console.print("    [cyan]▸[/] Read the log (below) to find the failing step")
        console.print(
            f"    [cyan]▸[/] [bold]{installer_cmd}[/bold]        [dim]re-run the installer[/]"
        )
        console.print()
    else:
        console.print("  [bold]Finished with warnings[/]")
        if next_steps:
            console.print(
                f"    [cyan]▸[/] [bold]{next_steps[1].get('cmd', '') if len(next_steps) > 1 else 'doctor'}[/bold]     [dim]audit what succeeded[/]"
            )
        console.print(
            f"    [cyan]▸[/] [bold]{installer_cmd}[/bold]        [dim]re-run the installer if needed[/]"
        )
        console.print()

    if log_path:
        console.print(f"  [dim]Log:[/] {log_path}")
    docs_url = (
        manifest.branding.get("docs_url", "https://vibecrafted.io")
        if manifest.branding
        else "https://vibecrafted.io"
    )
    console.print(f"  [dim]Docs:[/] [bold]{docs_url}[/bold]")
    console.print()


def _print_cleanup_notice(console: Any, manifest: Manifest, cleanup_flag: bool) -> None:
    """Tell the user whether the tool will persist or how to remove it."""
    if manifest.persist:
        console.print(
            "  [dim]Installer tool stays installed (manifest: persist=true).[/]"
        )
        return
    if cleanup_flag:
        return  # handled via execvp in main()
    console.print()
    console.print(
        "  [dim]This manifest does not require the installer tool to persist.[/]"
    )
    console.print(f"  [dim]Remove it with:[/] [bold]uv tool uninstall {TOOL_NAME}[/]")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def _is_interactive() -> bool:
    """True when both stdin and stdout are attached to a TTY."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def _open_log(manifest: Manifest) -> tuple[Path | None, Any | None]:
    """Open a timestamped log file per manifest.log_pattern, or (None, None) if unset."""
    if not manifest.log_pattern:
        return None, None
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    expanded = os.path.expanduser(manifest.log_pattern.format(ts=ts))
    log_path = Path(expanded)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path, open(log_path, "w", encoding="utf-8", buffering=1)


def _filter_phases(
    phases: list[Phase],
    only: list[str],
    skip: list[str],
) -> list[Phase]:
    """Apply --only/--skip name filters (key or label match) to the phase list."""
    result = list(phases)
    if only:
        result = [p for p in result if any(p.matches(n) for n in only)]
    if skip:
        result = [p for p in result if not any(p.matches(n) for n in skip)]
    return result


def run(
    manifest: Manifest,
    *,
    auto_yes: bool,
    advanced: bool = False,
    dry_run: bool,
    quiet: bool,
    only: list[str],
    skip: list[str],
) -> int:
    """Drive the full install run: intro flow, per-phase consent+execution, summary.

    Returns the process exit code (0 on success, the failing phase's return
    code otherwise).
    """
    console = _make_console()
    compact_stdout = quiet and auto_yes and not dry_run

    phases = _filter_phases(manifest.phases, only, skip)
    if not phases:
        _print_title(console, manifest)
        console.print("[yellow]No phases selected.[/]")
        return 0

    # Intro flow owns its own branding (0_welcome_step.zsh.md has the banner).
    # We skip the redundant [_print_title] header when it runs so the screen
    # stays faithful to the mocks. Dry-run and auto_yes still want the title
    # because the intro flow is skipped in those cases.
    intro_will_run = not (auto_yes or dry_run)
    if intro_will_run:
        tui_status = _show_intro_flow(console, manifest, auto_yes, advanced)
        if tui_status == "cancelled" or tui_status == "completed":
            return 0
    elif not compact_stdout:
        _print_title(console, manifest)

    if dry_run:
        console.print("[dim]Dry run — no commands will execute.[/]")
        console.print()
        for phase in phases:
            _print_reason_block(console, phase)
            # phase.cmd may contain literal '[warn]' etc. — print without
            # markup so Rich does not eat those bracket-notation tokens.
            cmd_str = " ".join(phase.cmd)
            if HAS_RICH:
                console.print(
                    f"  → {cmd_str}",
                    style="dim",
                    markup=False,
                    highlight=False,
                )
                console.print(
                    f"    cwd: {phase.cwd}",
                    style="dim",
                    markup=False,
                    highlight=False,
                )
            else:
                console.print(f"  → {cmd_str}")
                console.print(f"    cwd: {phase.cwd}")
            console.print()
        return 0

    # Log is opened silently and surfaced only in the final summary — we do
    # not announce it up front, the wizard greeting stays clean.
    log_path, log_handle = _open_log(manifest)

    results: list[tuple[str, str, int]] = []
    exit_code = 0

    try:
        if HAS_RICH and not compact_stdout:
            # Cargo-style sticky bar: no cur column (truncates on narrow
            # terminals, fights with console.input). Subprocess lines scroll
            # above the bar and carry the real progress narrative.
            progress = Progress(
                SpinnerColumn(style="yellow"),
                TextColumn("[bold]{task.description}[/]"),
                BarColumn(bar_width=None),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
                transient=False,
                expand=True,
                refresh_per_second=10,
            )
            task_id: Any = None
        else:
            progress = None
            task_id = None

        # Progress is started manually around working phases so the consent
        # prompt owns the screen without fighting Rich's Live renderer.
        if HAS_RICH and not compact_stdout:
            task_id = progress.add_task("pending", total=len(phases))

        for idx, phase in enumerate(phases):
            if log_handle is not None:
                log_handle.write(f"\n===== {phase.label} =====\n")
                log_handle.flush()
            if not compact_stdout:
                _print_reason_block(console, phase)

            # Consent is gathered WITHOUT the sticky bar — a spinning bar
            # next to a still prompt reads as "working", not "waiting for you".
            verdict = consent(console, phase.label, phase.optional, auto_yes)
            if verdict == "quit":
                if not compact_stdout:
                    console.print("\n  [yellow]Cancelled — no further changes.[/]\n")
                results.append((phase.label, "cancelled", 0))
                break
            if verdict == "skip":
                if not compact_stdout:
                    console.print("  [yellow]· skipped[/]\n")
                results.append((phase.label, "skipped", 0))
                if HAS_RICH and not compact_stdout:
                    progress.update(task_id, advance=1, description=phase.label)
                continue

            # User said yes — start the sticky bar so subprocess output
            # scrolls cleanly above it during the working phase.
            if HAS_RICH and not compact_stdout:
                progress.update(task_id, description=phase.label)
                progress.start()

            rc = run_phase(
                console,
                phase,
                progress,
                task_id,
                log_handle,
                quiet,
                non_interactive=auto_yes or not _is_interactive(),
            )

            if HAS_RICH and not compact_stdout:
                progress.update(task_id, advance=1)
                progress.stop()

            if rc == 0:
                results.append((phase.label, "ok", 0))
                if not compact_stdout:
                    console.print(f"  [green]✓[/] {phase.label}\n")
            elif phase.optional:
                # Non-fatal step: flag the warning and keep moving.
                results.append((phase.label, f"warn (exit {rc})", rc))
                if not compact_stdout:
                    console.print(
                        f"  [yellow]![/] {phase.label} [yellow]finished with exit {rc} — continuing[/]\n"
                    )
            else:
                results.append((phase.label, f"failed (exit {rc})", rc))
                if not compact_stdout:
                    console.print(f"  [red]✗ {phase.label} failed (exit {rc})[/]")
                if log_path and not compact_stdout:
                    console.print(f"  [dim]See log: {log_path}[/]")
                exit_code = rc
                break
    finally:
        if log_handle is not None:
            log_handle.close()

    _print_summary(console, manifest, results, log_path, compact=compact_stdout)
    return exit_code


class _NullContext:
    """No-op context manager placeholder (never raises, never suppresses)."""

    def __enter__(self):
        """Return self; enter is a no-op."""
        return self

    def __exit__(self, *_args):
        """Never suppress an exception."""
        return False


# ---------------------------------------------------------------------------
# Self-removal
# ---------------------------------------------------------------------------


def _self_uninstall_and_exit(rc: int) -> None:
    """Replace the current process with ``uv tool uninstall vetcoders-installer``.

    Using execvp means the Python interpreter (which is *this* tool's runtime)
    terminates cleanly, and the replacement shell kills the package files.
    No mid-run self-corruption.
    """
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        print(
            f"\n  Could not locate `uv` on PATH — remove the tool manually:\n"
            f"    uv tool uninstall {TOOL_NAME}",
            file=sys.stderr,
        )
        sys.exit(rc)
    # Fire-and-forget: the child shell runs after exec, current process is replaced.
    # rc is lost in execvp; we rely on prior `run()` to have signalled errors already.
    os.execvp(uv_bin, [uv_bin, "tool", "uninstall", TOOL_NAME])


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the vetcoders-installer CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Sequential trust-building installer runner (manifest-driven).",
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to an install.toml manifest",
    )
    parser.add_argument(
        "--advanced",
        action="store_true",
        help="Advanced interactive mode: allow deselecting items during the checklist step.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Auto-approve every phase (automation mode).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned phases and reasons without running any command.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress streaming of subprocess output above the bar. "
        "The bar still shows the latest line via its 'cur' field.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="Only run phases whose key or label matches NAME (repeatable).",
    )
    parser.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="NAME",
        help="Skip phases whose key or label matches NAME (repeatable).",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="After a successful run, self-uninstall via `uv tool uninstall` "
        "(only if the manifest does not set persist=true).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{TOOL_NAME} {__version__}",
    )
    return parser


def main() -> int:
    """CLI entrypoint: parse args, load the manifest, run it, then self-uninstall/cleanup."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(line_buffering=True)

    parser = _build_parser()
    args = parser.parse_args()

    manifest_path: Path = args.manifest.expanduser().resolve()
    if not manifest_path.exists():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        manifest = Manifest.load(manifest_path)
    # Manifest loading includes version-dependent parsers and validation; every parse exception must
    # produce the CLI error and exit 2 before installer execution.
    except Exception as exc:  # noqa: BLE001
        print(f"error: failed to parse {manifest_path}: {exc}", file=sys.stderr)
        return 2

    # Automation fallback: if not a TTY, force --yes so the tool can be used
    # in cron / curl | sh / CI without hanging on input().
    auto_yes = args.yes or not _is_interactive()

    try:
        rc = run(
            manifest,
            auto_yes=auto_yes,
            advanced=args.advanced,
            dry_run=args.dry_run,
            quiet=args.quiet,
            only=args.only,
            skip=args.skip,
        )
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        return 130

    # Decide whether to self-uninstall.
    if rc == 0 and not args.dry_run and not manifest.persist and args.cleanup:
        _self_uninstall_and_exit(rc)

    # Otherwise print cleanup guidance (if applicable).
    if rc == 0 and not args.dry_run and not args.quiet:
        console = _make_console()
        _print_cleanup_notice(console, manifest, args.cleanup)

    return rc


if __name__ == "__main__":
    sys.exit(main())
