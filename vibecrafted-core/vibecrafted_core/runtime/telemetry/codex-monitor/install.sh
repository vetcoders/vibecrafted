#!/usr/bin/env bash
# codex telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry codex line|once|sessions|daemon"
echo "Reads: ~/.codex/sessions and ~/.codex/archived_sessions"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/codex/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
