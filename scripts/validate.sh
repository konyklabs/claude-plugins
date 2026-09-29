#!/usr/bin/env bash
# Strict validation of everything Claude Code loads from this repo: the
# marketplace manifest, each plugin manifest, and each plugin's skills and
# agents directories. Exit non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."
run() { echo "+ claude plugin validate --strict $1"; claude plugin validate --strict "$1"; }
run .
for p in plugins/*/; do
  run "$p"
  [ -d "$p/skills" ] && run "$p/skills"
  [ -d "$p/agents" ] && run "$p/agents"
done
echo "all manifests and components valid"
