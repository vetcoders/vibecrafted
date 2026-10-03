#!/usr/bin/env bash
# copilot telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry copilot line|once|sessions|daemon"
echo "Reads: ~/.copilot/session-state/*/events.jsonl"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/copilot/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
