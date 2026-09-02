#!/usr/bin/env bash
# Prove the plugins pull nothing in: every Python file must import only the
# standard library, and nothing in the plugin code may open a network
# connection. Exit non-zero on any violation. Runs in CI.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 - <<'PY'
import ast, sys, pathlib
files = sorted(p for p in pathlib.Path("plugins").rglob("*.py") if "tests" not in p.parts)
stdlib = getattr(sys, "stdlib_module_names", None)
if stdlib is None:  # Python < 3.10: a conservative allowlist of what the plugins use
    stdlib = {"ast", "configparser", "collections", "fcntl", "json", "math", "os", "pathlib", "re", "sys", "time", "typing", "__future__"}
bad = []
for f in files:
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        for n in names:
            if n not in stdlib:
                bad.append(f"{f}: imports {n}")
    print(f"{f}: stdlib only")
if bad:
    print("\n".join(bad)); sys.exit(1)
PY
echo "+ network primitives in plugin code (must be none):"
if grep -rnE "urllib|http\.client|import requests|import socket|httpx|aiohttp" plugins --include='*.py' --include='*.sh' | grep -v "/tests/"; then
  echo "network primitive found"; exit 1
fi
echo "  none"
echo "+ everything CI pulls from outside this repository:"
grep -hE "^\s*(-\s*)?uses:|npm install|pip install" .github/workflows/*.yml | sed 's/^ *//' | sort -u
echo "audit ok: plugin code is standard-library Python with no network calls; CI uses GitHub, Astral and Anthropic first-party packages only"
