#!/usr/bin/env bash
# Build one zip per plugin from a committed tree, never from the working
# directory, so nothing ignored (.env, caches, local notes) can ship. Each zip
# is then checked by the readiness scanner's archive-hygiene rule. Output goes
# to dist/ (gitignored). Usage: scripts/dist.sh [tree-ish]   (default HEAD)
set -euo pipefail
cd "$(dirname "$0")/.."
ref="${1:-HEAD}"
mkdir -p dist
scanner=plugins/prod-readiness/skills/readiness-review/scripts/readiness.py
for p in plugins/*/; do
  name=$(basename "$p")
  out="dist/$name.zip"
  git archive --worktree-attributes --format=zip -o "$out" "$ref:plugins/$name"
  if [ -f "$scanner" ]; then
    status=$(python3 "$scanner" --only archive-hygiene --archive "$out" --json 2>/dev/null \
      | python3 -c 'import json,sys; c=json.load(sys.stdin)["checks"][0]; print(c["status"], "" if c["status"]!="fail" else [f["note"] for f in c["findings"]][:3])')
    case "$status" in fail*) echo "$out: archive-hygiene FAILED: $status" >&2; exit 1;; esac
  fi
  printf '%s  %s  %s\n' "$(shasum -a 256 "$out" | cut -c1-16)" "$(du -h "$out" | cut -f1)" "$out"
done
echo "install on any machine, no git needed:  claude --plugin-dir dist/supervisor.zip --plugin-dir dist/py-testing.zip --plugin-dir dist/prod-readiness.zip"
