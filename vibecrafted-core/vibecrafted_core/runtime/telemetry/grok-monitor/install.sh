#!/usr/bin/env bash
# grok telemetry engine.
# The product launcher is `telemetry`. This script does not publish a PATH name.
set -euo pipefail
echo "telemetry grok line|once|sessions|daemon"
echo "Reads: ~/.grok/sessions/*/*/usage.json"
echo "Writes: \${VIBECRAFTED_HOME:-\$HOME/.vibecrafted}/telemetry/grok/quota.json"
echo "Does not modify the agent store and does not send usage off the machine."
