# Operator Runtime Topology

The Vetcoders operator runtime orchestrates AI-built software delivery across multiple interfaces, establishing a clear product choice instead of overlapping experiments.

## Runtime Surfaces

1. **Recommended (1.4.1): vc-frame Dashboard**

   - **Command:** `vibecrafted dashboard` or `vc-start`
   - The primary, stable product experience.
   - It organizes work using the repo-owned vc-frame configuration and layout (e.g., `config/vc-frame/layouts/operator.kdl`).
   - Ensures agents do not "disappear" by providing a visible dashboard where sessions can be tracked, attached, and garbage-collected (`dashboard gc --apply`).

2. **Experimental: Rust Operator Console**

   - **Command:** `vibecrafted tui`
   - A dedicated operator terminal built on the `operator-tui` / `vc-operator` crate.
   - Designed to run over shared control-plane state (polling `~/.vibecrafted/control_plane`).
   - Marked as experimental. If not installed, the command directs users to install it via `cargo install vc-operator`.

3. **Fallback: Command Line Interface (CLI)**

   - **Command:** `vibecrafted help`, `vibecrafted <skill>`
   - Useful for headless execution and scripting, but not intended as the main interactive product experience.
   - Reverts to standard shell streams when vc-frame or the TUI is unavailable or unneeded.

4. **Future Substrate (Research): Ghostty & `rust-mux`**
   - **Ghostty:** An advanced terminal emulator intended to serve as a richer graphics and visual space canvas beyond vc-frame's cell-based UI constraints. Ghostty is scoped purely as research and does not block the 1.4.1 release.
   - **`rust-mux` / `rmcp-mux`:** A shared Model Context Protocol (MCP) server layer orchestrating many background agents. It is designed to share MCP servers _per machine_ (and eventually per Tailscale peer) to avoid launching duplicate, resource-heavy servers per workspace.

## State Locations

- **Session State (vc-frame):** Managed dynamically by vc-frame, stored temporarily within the vc-frame runtime daemon.
- **Control Plane Events:** `~/.vibecrafted/control_plane/events.jsonl` (and `runs/` for active tracking).
- **Transcripts & Reports:** Written to the workspace's `.vibecrafted/reports/` or the global `~/.vibecrafted/artifacts/` directory, serving as durable output for completed tasks.
- **Marbles State:** Stored persistently under `~/.vibecrafted/marbles/` to track loop iterations and counterexamples.
- **Operator UI State:** Repository-specific configurations live in `config/vc-frame/`; global user configurations and frontier overrides live in `~/.vibecrafted/config/vc-frame/`.
