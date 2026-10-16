#!/usr/bin/env bash
# omp telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry omp line|once|sessions|archive"
echo "Reads native OMP_PROFILE/PI_PROFILE and PI_CODING_AGENT_DIR sessions (default: ~/.omp/agent/sessions/**/*.jsonl)"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/omp/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
