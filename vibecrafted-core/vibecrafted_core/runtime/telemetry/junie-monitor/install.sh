#!/usr/bin/env bash
# junie telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry junie line|once|sessions|daemon"
echo "Reads: ~/.junie/sessions/session-*/events.jsonl"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/junie/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
