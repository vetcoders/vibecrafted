"""Textual TUI app for the installer wizard."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, ClassVar

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Checkbox, Static

DEFAULT_RENDER_WIDTH = 57
MAX_RENDER_WIDTH = 180
INSTALL_OUTPUT_TAIL = 96
INSTALL_STATUS_TAIL = 8
INSTALL_BOX_MIN_ROWS = 12
INSTALL_BOX_FIXED_CHROME_ROWS = 14
BOX_DRAWING_RE = re.compile(r"[╔╗╚╝╠╣║═─┌┐└┘├┤│┃━┏┓┗┛┠┨┼┬┴╭╮╰╯]+")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _trim_home(path: str) -> str:
    """Replace the user's home directory prefix in path with `~`."""
    return path.replace(str(Path.home()), "~")


def run_manifest_diagnostics(manifest: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """Run the manifest's generic diagnostics.categories/commands/paths checks.

    Returns a nested {category: {name: {label, found, detail, kind}}} map,
    empty when the manifest has no `diagnostics` table.
    """
    diags: dict[str, dict[str, dict[str, Any]]] = {}
    if not manifest or not manifest.diagnostics:
        return diags

    # Generic diagnostics from manifest
    cats = manifest.diagnostics.get("categories", [])
    cmds = manifest.diagnostics.get("commands", {})
    paths = manifest.diagnostics.get("paths", {})

    for cat in cats:
        diags[cat] = {}
        for cmd in cmds.get(cat, []):
            cmd_path = shutil.which(cmd)
            diags[cat][cmd] = {
                "label": cmd,
                "found": bool(cmd_path),
                "detail": cmd_path or f"{cmd} not found on PATH",
                "kind": "command",
            }
        for name, path_str in paths.get(cat, {}).items():
            if isinstance(path_str, list):
                # Multiple paths, assume it's symlinks check
                active = []
                for p in path_str:
                    expanded = os.path.expandvars(os.path.expanduser(p))
                    if Path(expanded).is_dir() and list(Path(expanded).iterdir()):
                        active.append(expanded)
                diags[cat][name] = {
                    "label": name,
                    "found": bool(active),
                    "detail": ", ".join(active)
                    if active
                    else f"No items found in {name}",
                    "kind": "path",
                }
            else:
                expanded = os.path.expandvars(os.path.expanduser(path_str))
                diags[cat][name] = {
                    "label": name,
                    "found": Path(expanded).exists(),
                    "detail": expanded,
                    "kind": "path",
                }
    return diags


def summarize_diagnostics(
    diags: dict[str, dict[str, dict[str, Any]]], manifest: Any
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Flatten diagnostics results into (found labels, missing labels, missing-by-category)."""
    found_items: list[str] = []
    missing_items: list[str] = []
    needs_install: dict[str, list[str]] = {}

    if not manifest or not manifest.diagnostics:
        return found_items, missing_items, needs_install

    cats = manifest.diagnostics.get("categories", [])
    labels = manifest.diagnostics.get("labels", {})

    for cat in cats:
        missing_in_cat = []
        for name, entry in diags.get(cat, {}).items():
            label = entry.get("label", name)
            flat_label = f"{labels.get(cat, cat)}: {label}"
            if entry.get("found"):
                found_items.append(flat_label)
            else:
                missing_items.append(flat_label)
                missing_in_cat.append(label)
        if missing_in_cat:
            needs_install[cat] = missing_in_cat

    return found_items, missing_items, needs_install


class InstallerIntroApp(App):
    """Multi-screen installer wizard."""

    CSS = """
    #header {
        dock: top;
        height: auto;
        background: $surface;
        padding: 0 1;
    }
    #scroll-area {
        height: 1fr;
    }
    #content-container {
        padding: 0 5 1 5;
    }
    #footer {
        dock: bottom;
        height: auto;
        background: $surface;
        padding: 0 1;
    }
    .check-item {
        height: 1;
        padding: 0;
        margin: 0;
        border: none;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "next_screen", "Proceed", show=True),
        Binding("backspace", "prev_screen", "Back", show=True),
        Binding("escape", "quit_installer", "Quit", show=True),
        Binding("q", "quit_installer", "Quit", show=False),
        Binding("tab", "toggle_details", "Details", show=True),
        Binding("up", "scroll_up", show=False),
        Binding("down", "scroll_down", show=False),
    ]

    def action_scroll_up(self) -> None:
        """Scroll the content pane up one step."""
        self.query_one("#scroll-area", VerticalScroll).scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll the content pane down one step."""
        self.query_one("#scroll-area", VerticalScroll).scroll_down()

    def __init__(
        self,
        screens: list[tuple[str, str, str]],
        version: str,
        source_dir: Path,
        advanced: bool = False,
        manifest: Any = None,
    ) -> None:
        """Seed the wizard's screen deck and mutable diagnostics/install state."""
        super().__init__()
        self._screens = screens
        self._version = version
        self._source_dir = source_dir
        self._advanced = advanced
        self._current = 0
        self.result: str = "quit"

        self.manifest = manifest
        self.details_view = False

        # State
        self.diagnostics_done = False
        self.diagnostics_results: dict[str, dict[str, dict[str, Any]]] = {}
        self.found_items: list[str] = []
        self.missing_items: list[str] = []
        self.needs_install: dict[str, list[str]] = {}
        self.selected_items: set[str] = set()

        self.install_running = False
        self.install_done = False
        self.install_exit_code: int | None = None
        self.install_log: list[str] = []
        self.install_status_log: list[str] = []
        self.install_error: str | None = None
        self.install_phase_label = "Preparing"
        self.install_phase_reason = "Preparing the local installer runtime."
        self.install_phase_index = 0
        self.install_phase_total = 0
        self.install_phase_names: list[str] = []

        self._diag_msg = "Starting..."

    def compose(self) -> ComposeResult:
        """Lay out the sticky header, scrollable content area, and sticky footer."""
        yield Static(id="header", markup=False)
        with VerticalScroll(id="scroll-area"):
            yield Vertical(id="content-container")
        yield Static(id="footer", markup=False)

    async def on_mount(self) -> None:
        """Render the first screen once the app widgets are mounted."""
        await self._render_screen()

    def action_toggle_details(self) -> None:
        """Toggle expanded-detail rendering for the diagnostics/results screens."""
        self.details_view = not self.details_view
        if self._current in (3, 4):
            self.call_later(self._render_content)

    async def _render_screen(self) -> None:
        """Redraw header/footer chrome and content for the current screen index."""
        idx = min(self._current, len(self._screens) - 1)
        header, _content, footer = self._screens[idx]

        # Override headers/footers using branding if available
        if self.manifest and getattr(self.manifest, "branding", None):
            pass  # Currently keeping exactly what the screens provided

        self.query_one("#header", Static).update(self._render_chrome(header))
        self.query_one("#footer", Static).update(self._render_chrome(footer))

        await self._render_content()
        self.query_one("#scroll-area", VerticalScroll).scroll_home(animate=False)

    def _terminal_size(self) -> tuple[int, int]:
        """Return (width, height) from the live app size, falling back to shutil."""
        size = getattr(self, "size", None)
        width = int(getattr(size, "width", 0) or 0)
        height = int(getattr(size, "height", 0) or 0)
        if width > 0 and height > 0:
            return width, height
        fallback = shutil.get_terminal_size((DEFAULT_RENDER_WIDTH, 32))
        return fallback.columns, fallback.lines

    def _pane_width(self) -> int:
        """Content pane width: terminal width minus a 2-column margin, floored."""
        width, _ = self._terminal_size()
        return max(DEFAULT_RENDER_WIDTH, width - 2)

    def _render_width(self) -> int:
        """Box-drawing render width, clamped between the default and max widths."""
        return max(DEFAULT_RENDER_WIDTH, min(self._pane_width(), MAX_RENDER_WIDTH))

    def _install_box_rows(self) -> int:
        """Row budget for the scrolling install-log box given current terminal height."""
        _, height = self._terminal_size()
        return max(INSTALL_BOX_MIN_ROWS, height - INSTALL_BOX_FIXED_CHROME_ROWS)

    def _render_chrome(self, text: str) -> str:
        """Center each line of header/footer text (or expand a rule) to pane width."""
        width = self._pane_width()
        lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                lines.append("")
            elif set(line) <= {"─"}:
                lines.append("─" * width)
            else:
                lines.append(line.center(width))
        return "\n".join(lines)

    def _render_box(self, title: str, body_lines: list[str], rows: int = 0) -> str:
        """Draw a box-drawn panel with a titled header, tailing/padding to `rows` lines."""
        width = self._render_width()
        inner_width = max(24, width - 6)
        display_lines = body_lines[:]
        if rows:
            display_lines = display_lines[-rows:]
            if len(display_lines) < rows:
                display_lines.extend([""] * (rows - len(display_lines)))

        lines = [f"  ╔{'═' * inner_width}╗"]
        header = f" {title} "
        lines.append(f"  ║{header[:inner_width]:<{inner_width}}║")
        lines.append(f"  ╠{'═' * inner_width}╣")
        for raw_line in display_lines:
            line = str(raw_line)
            lines.append(f"  ║{line[-inner_width:]:<{inner_width}}║")
        lines.append(f"  ╚{'═' * inner_width}╝")
        return "\n".join(lines)

    def _wrap_panel_line(self, label: str, value: str, width: int) -> list[str]:
        """Word-wrap value under a fixed-width label, indenting continuation lines."""
        label_text = f"  {label:<11} "
        body_width = max(24, width - len(label_text))
        wrapped = textwrap.wrap(value, width=body_width) or [""]
        lines = [label_text + wrapped[0]]
        indent = " " * len(label_text)
        lines.extend(indent + part for part in wrapped[1:])
        return lines

    def _render_install_panel(self, title: str, status_lines: list[str]) -> str:
        """Render the live install status panel: phase rail, checkpoint, latest status line."""
        width = self._render_width()
        visible = [line for line in status_lines if line.strip()]
        if not visible:
            visible = ["Starting..."]

        lines = [
            "  [bold]𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. install[/bold]",
            f"  [dim]{self._render_phase_rail(width)}[/dim]",
            "",
            f"  [bold]{title}[/bold]",
            "",
        ]
        lines.extend(
            self._wrap_panel_line(
                "Checkpoint",
                f"{self.install_phase_index}/{self.install_phase_total}",
                width,
            )
        )
        lines.extend(self._wrap_panel_line("Phase", self.install_phase_label, width))
        lines.extend(self._wrap_panel_line("Reason", self.install_phase_reason, width))
        lines.extend(self._wrap_panel_line("Status", visible[-1], width))

        if self.install_running:
            lines.append(
                "  [dim]Full command output is kept in the install log, not in this preview.[/dim]"
            )
        elif self.install_done and self.install_exit_code == 0:
            lines.append(
                "  [green]Done.[/green] [dim]Detailed output stayed out of the preview.[/dim]"
            )
        elif self.install_error:
            lines.extend(self._wrap_panel_line("Error", self.install_error, width))

        return "\n".join(lines)

    def _render_phase_rail(self, width: int) -> str:
        """Render the ●/◆/○ phase-progress rail, truncated with an ellipsis at width."""
        names = self.install_phase_names or [self.install_phase_label]
        parts = []
        for index, name in enumerate(names, start=1):
            if (
                self.install_done
                and self.install_exit_code == 0
                or index < self.install_phase_index
            ):
                marker = "●"
            elif index == self.install_phase_index:
                marker = "◆"
            else:
                marker = "○"
            parts.append(f"{marker} {name}")

        rail = "  ┄  ".join(parts)
        if len(rail) > width:
            return rail[: max(0, width - 1)] + "…"
        return rail

    def _summarize_phase_reason(self, reason: str) -> str:
        """Collapse a multi-line phase reason block to one display line."""
        lines = [
            line.strip().lstrip("-").strip()
            for line in reason.splitlines()
            if line.strip()
        ]
        if not lines:
            return "Run this checkpoint with a quiet, inspectable output surface."
        if lines[0].endswith(":") and len(lines) > 1:
            return f"{lines[0]} {lines[1]}"
        return lines[0]

    async def _render_content(self) -> None:
        """Remount the content pane's widgets for the current screen.

        Also kicks off diagnostics (screen 3) or install (screen 5) as a
        side effect the first time each of those screens is shown.
        """
        container = self.query_one("#content-container", Vertical)
        await container.query("*").remove()

        if self._current in (0, 1, 2):
            await container.mount(Static(self._screens[self._current][1], markup=False))
        elif self._current == 3:
            await container.mount(Static(self._build_step_3(), markup=True))
            if not self.diagnostics_done and not getattr(self, "_diag_started", False):
                self._diag_started = True
                self.run_diagnostics()
        elif self._current == 4:
            if self._advanced:
                await self._mount_step_4_advanced(container)
            else:
                await container.mount(Static(self._build_step_4_static(), markup=True))
        elif self._current == 5:
            await container.mount(Static(self._build_step_5(), markup=True))
            if not self.install_running and not self.install_done:
                self.run_install()
        elif self._current == 6:
            await container.mount(Static(self._build_step_6(), markup=True))

    def _build_step_3(self) -> str:
        """Render the diagnostics screen: preflight box plus per-category results."""
        lines = []
        lines.append("  [bold]Diagnostics[/bold]\n")

        lines.append(self._render_box("Preflight", [self._diag_msg]))
        lines.append("")

        if self.diagnostics_done:
            cats = []
            labels = {}
            if self.manifest and getattr(self.manifest, "diagnostics", None):
                cats = self.manifest.diagnostics.get("categories", [])
                labels = self.manifest.diagnostics.get("labels", {})

            for category in cats:
                lines.append(f"  {labels.get(category, category)}:")
                entries = list(self.diagnostics_results.get(category, {}).values())
                if self.details_view:
                    for entry in entries:
                        label = entry.get("label", "?")
                        detail = _trim_home(str(entry.get("detail", "")))
                        icon = (
                            "[green]✔[/green]" if entry.get("found") else "[red]𐄂[/red]"
                        )
                        lines.append(f"    {icon} {label} — {detail}")
                else:
                    parts = []
                    for entry in entries:
                        icon = (
                            "[green]✔[/green]" if entry.get("found") else "[red]𐄂[/red]"
                        )
                        parts.append(f"{icon} {entry.get('label', '?')}")
                    if parts:
                        lines.append("    " + " · ".join(parts))
                    else:
                        lines.append("    [dim]None[/dim]")
                lines.append("")

        return "\n".join(lines)

    @work(exclusive=True, thread=True)
    def run_diagnostics(self) -> None:
        """Run manifest diagnostics on a worker thread, then hand results back to the UI."""
        self.app.call_from_thread(self._update_diag_msg, "Running diagnostics...")
        diags = run_manifest_diagnostics(self.manifest)
        self.app.call_from_thread(self._finish_diagnostics, diags)

    def _update_diag_msg(self, msg: str) -> None:
        """Update the preflight status message and re-render if screen 3 is visible."""
        self._diag_msg = msg
        if self._current == 3:
            self._update_static_content(self._build_step_3())

    def _finish_diagnostics(self, diags) -> None:
        """Store diagnostics results, seed default selections, and auto-advance."""
        self.diagnostics_results = diags
        self.found_items, self.missing_items, self.needs_install = (
            summarize_diagnostics(diags, self.manifest)
        )
        self.selected_items = set(self.missing_items)  # All selected by default
        self.diagnostics_done = True
        self._diag_msg = "Complete."
        if self._current == 3:
            self._update_static_content(self._build_step_3())
            self.action_next_screen()

    def _update_static_content(self, text: str) -> None:
        """Update the content pane's Static widget text, tolerating a mid-refresh unmount."""
        try:
            widget = self.query_one("#content-container > Static", Static)
            widget.update(text)
        # A Textual widget can unmount during an asynchronous refresh; renderer-specific exceptions
        # here must leave the installer worker and its results intact.
        except Exception as _update_exc:  # noqa: BLE001
            _ = _update_exc  # widget may be unmounted mid-refresh

    def _build_step_4_static(self) -> str:
        """Render the read-only results screen: found vs. missing items."""
        lines = ["[bold]  Results[/bold]\n"]
        lines.append("  [bold]Already have[/bold] (no action needed)")
        if not self.found_items:
            lines.append("    [dim]None[/dim]")
        for item in self.found_items:
            lines.append(f"    [green]✔[/green] {item}")

        lines.append("\n  [bold]Need to install[/bold]")
        if not self.missing_items:
            lines.append("    [dim]None[/dim]")
        for item in self.missing_items:
            lines.append(f"    [red]𐄂[/red] {item}")
        return "\n".join(lines)

    async def _mount_step_4_advanced(self, container: Vertical) -> None:
        """Mount the advanced results screen: found items plus toggleable checkboxes."""
        await container.mount(
            Static(
                "[bold]  Results (Advanced Mode)[/bold]\n\n  [bold]Already have[/bold] (no action needed)",
                markup=True,
            )
        )
        if not self.found_items:
            await container.mount(Static("    [dim]None[/dim]", markup=True))
        for item in self.found_items:
            await container.mount(Static(f"    [green]✔[/green] {item}", markup=True))

        await container.mount(
            Static("\n  [bold]Need to install[/bold] (Space to toggle)", markup=True)
        )
        if not self.missing_items:
            await container.mount(Static("    [dim]None[/dim]", markup=True))
        for item in self.missing_items:
            cb = Checkbox(item, value=item in self.selected_items, classes="check-item")
            await container.mount(cb)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Sync selected_items with an advanced-mode checkbox toggle."""
        if event.checkbox.label is None:
            return
        item = str(event.checkbox.label)
        if event.checkbox.value:
            self.selected_items.add(item)
        else:
            self.selected_items.discard(item)

    def _build_step_5(self) -> str:
        """Render the live installation screen: status panel plus per-item outcome."""
        lines = ["  [bold]Installation[/bold]\n"]

        if self.install_running:
            header = "Live progress"
        elif self.install_done and self.install_exit_code == 0:
            header = "Install log · finished cleanly"
        elif self.install_error or (
            self.install_done and self.install_exit_code not in (None, 0)
        ):
            header = "Install log · failed"
        else:
            header = "Install status"

        tail = [
            line
            for line in self.install_status_log[-INSTALL_STATUS_TAIL:]
            if line.strip()
        ]
        if not tail:
            if self.install_done:
                if self.install_exit_code == 0:
                    tail = ["Install finished cleanly.", "Ready."]
                else:
                    tail = [self.install_error or "Failed."]
            else:
                tail = ["Starting..."]

        lines.append(self._render_install_panel(header, tail))
        lines.append("")

        for item in self.missing_items:
            if item in self.selected_items:
                icon = (
                    "[green]✔[/green]"
                    if self.install_done and self.install_exit_code == 0
                    else "[dim]𐄂[/dim]"
                )
                lines.append(f"    {icon} {item}")
            else:
                lines.append(f"    [dim]- {item} (skipped)[/dim]")

        return "\n".join(lines)

    @work(exclusive=True, thread=True)
    def run_install(self) -> None:
        """Run the manifest's non-diagnostic phases sequentially on a worker thread.

        Streams subprocess output back to the UI line-by-line and stops at
        the first non-optional phase failure.
        """
        self.install_running = True

        # Loop through manifest phases
        if not self.manifest or not self.manifest.phases:
            self.app.call_from_thread(self._finish_install, 0, None)
            return

        exit_code = 0
        err = None
        runnable_phases = [
            phase
            for phase in self.manifest.phases
            if phase.key not in {"diagnostics", "introduction"}
        ]
        self.app.call_from_thread(
            self._set_install_phases,
            [phase.label for phase in runnable_phases],
        )
        for index, phase in enumerate(runnable_phases, start=1):
            # We skip "diagnostics" phase in the install loop, because
            # this TUI handles diagnostics via step 3 natively
            cmd = phase.cmd
            cwd = phase.cwd
            reason = self._summarize_phase_reason(phase.reason)
            self.app.call_from_thread(
                self._set_install_phase, index, phase.label, reason
            )

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=str(cwd),
                )
                assert process.stdout is not None
                for line in process.stdout:
                    clean = line.rstrip("\n")
                    self.app.call_from_thread(self._add_install_log, clean)

                rc = process.wait()
                if rc != 0 and not phase.optional:
                    exit_code = rc
                    err = f"Phase {phase.label} failed with exit {rc}"
                    break
            # Install phases execute external processes and UI callbacks; any phase exception must
            # reach _finish_install as exit -1 and its reason rather than strand a running UI.
            except Exception as exc:  # noqa: BLE001
                exit_code = -1
                err = str(exc)
                break

        self.app.call_from_thread(self._finish_install, exit_code, err)

    def _set_install_phases(self, names: list[str]) -> None:
        """Record the ordered runnable-phase labels and refresh the phase rail."""
        self.install_phase_names = names
        self.install_phase_total = len(names)
        if self._current == 5:
            self._update_static_content(self._build_step_5())

    def _set_install_phase(self, index: int, label: str, reason: str) -> None:
        """Advance the current-phase pointer and log a start marker for it."""
        self.install_phase_index = index
        self.install_phase_label = label
        self.install_phase_reason = reason
        self._add_install_log(f"{label} started.")

    def _status_from_install_line(self, line: str) -> str | None:
        """Distill a raw subprocess output line into a short status string, or None to drop it.

        Strips ANSI/box-drawing noise, filters boilerplate "ok/done" lines,
        and condenses long paths to "Checking <basename>".
        """
        clean = ANSI_RE.sub("", line)
        clean = BOX_DRAWING_RE.sub(" ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if not clean:
            return None

        low_value = {
            "already linked",
            "ok",
            "done",
            "complete",
        }
        if clean.lower() in low_value:
            return None

        if len(clean) > 100:
            clean = _trim_home(clean)
        if "/" in clean and (" " not in clean or len(clean) > 72):
            name = Path(clean.strip("'\"")).name
            if name:
                return f"Checking {name}"
        return clean

    def _add_install_log(self, line: str) -> None:
        """Append a raw log line and, if it yields a new status, the status tail too."""
        self.install_log.append(line)
        if len(self.install_log) > INSTALL_OUTPUT_TAIL:
            self.install_log = self.install_log[-INSTALL_OUTPUT_TAIL:]
        status = self._status_from_install_line(line)
        if status and (
            not self.install_status_log or self.install_status_log[-1] != status
        ):
            self.install_status_log.append(status)
            if len(self.install_status_log) > INSTALL_STATUS_TAIL:
                self.install_status_log = self.install_status_log[-INSTALL_STATUS_TAIL:]
        if self._current == 5:
            self._update_static_content(self._build_step_5())

    def _finish_install(self, rc: int, err: str | None) -> None:
        """Record the install outcome and auto-advance from the install screen."""
        self.install_running = False
        self.install_done = True
        self.install_exit_code = rc
        self.install_error = err
        if self._current == 5:
            self._update_static_content(self._build_step_5())
            self.action_next_screen()

    def _build_step_6(self) -> str:
        """Render the final screen: success/failure banner plus branded next steps."""
        lines = []
        if self.install_exit_code == 0:
            lines.append("  [bold green]Installation complete.[/bold green]\n")
        else:
            lines.append("  [bold red]Installation failed.[/bold red]\n")
            lines.append(f"  Exit code: {self.install_exit_code}")
            if self.install_error:
                lines.append(f"  Error: {self.install_error}")
            lines.append("")

        next_steps = []
        if self.manifest and getattr(self.manifest, "branding", None):
            next_steps = self.manifest.branding.get("next_steps", [])
            tagline = self.manifest.branding.get("footer_tagline", "")
        else:
            tagline = ""

        if next_steps:
            for step in next_steps:
                lines.append(
                    f"  {step.get('label', 'Step')}:    [bold]{step.get('cmd', '')}[/bold]  [dim]({step.get('desc', '')})[/dim]"
                )

        if tagline:
            lines.append(f"\n  {tagline}\n")

        lines.append("  Press Enter to exit installer...")
        return "\n".join(lines)

    # -- Actions bound to keys ----------------------------------------------

    def action_next_screen(self) -> None:
        """Advance to the next screen, gated on diagnostics/install completion; exits at the end."""
        if self._current == 3 and not self.diagnostics_done:
            return
        if self._current == 5 and not self.install_done:
            return

        if self._current < 6:
            self._current += 1
            self.call_later(self._render_screen)
        else:
            self.result = "complete"
            self.exit()

    def action_prev_screen(self) -> None:
        """Step back a screen, unless the current screen locks navigation (3, 5, 6)."""
        if self._current in (3, 5, 6):
            return
        if self._current > 0:
            self._current -= 1
            self.call_later(self._render_screen)

    def action_quit_installer(self) -> None:
        """Quit the wizard, unless an install is actively running (screen 5)."""
        if self._current == 5 and self.install_running:
            return
        self.result = "quit"
        self.exit()
