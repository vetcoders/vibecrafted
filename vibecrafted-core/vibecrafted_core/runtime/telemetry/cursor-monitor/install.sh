#!/usr/bin/env bash
# cursor telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry cursor line|once|sessions|daemon"
echo "Reads: none — usage lives in Cursor cloud"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/cursor/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
