#!/bin/bash
# Banc de test ScanSubtitlesFR hors Resolve (voir README > Tester sans Resolve).
# Usage : bash tests/run.sh [scenario ...]   (sans argument : tous les scenarios)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/Library/Frameworks/Python.framework/Versions/Current/bin/python3
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  ANTHROPIC_API_KEY="$(cat "$HOME/.anthropic/api_key")" || { echo "Cle API introuvable"; exit 1; }
  export ANTHROPIC_API_KEY
fi
SCENARIOS=("$@")
[ ${#SCENARIOS[@]} -eq 0 ] && SCENARIOS=($("$PY" -c "import json,sys; print(' '.join(json.load(open(sys.argv[1]))))" "$HERE/scenarios.json"))
for sc in "${SCENARIOS[@]}"; do
  FAKE_HOME="$(mktemp -d)"; mkdir -p "$FAKE_HOME/Desktop"
  echo "######## $sc   (rapports dans $FAKE_HOME/Desktop)"
  HOME="$FAKE_HOME" PYTHONDONTWRITEBYTECODE=1 "$PY" "$HERE/harness.py" "$sc" "$HERE/../ScanSubtitlesFR.py"
  echo "exit=$?"
done
