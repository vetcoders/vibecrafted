#!/usr/bin/env bash
# claude telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry claude line|once|sessions|daemon"
echo "Reads: ~/.claude/projects"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/claude/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
