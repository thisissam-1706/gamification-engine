#!/usr/bin/env bash
# Captures test evidence for the exact committed state. Only reads git; run on a clean tree.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

if [ -n "$(git status --porcelain)" ]; then
    echo "error: working tree is not clean; commit or remove changes before capturing evidence." >&2
    git status --short >&2
    exit 1
fi

sha="$(git rev-parse --short HEAD)"
out="docs/evidence/${sha}"
mkdir -p "${out}"

{
    git rev-parse HEAD
    git log -1 --oneline
    date -u +"%Y-%m-%dT%H:%M:%SZ"
} > "${out}/commit.txt"

{
    python --version
    python -m pip freeze
} > "${out}/env.txt" 2>&1

# Keep the script's own status separate from pytest's so the output file is always written.
set +e
python -m pytest -q > "${out}/pytest_full.txt" 2>&1
status=$?
set -e

tail -n 1 "${out}/pytest_full.txt"
echo "evidence written to ${out}/ (pytest exit code ${status})"
exit "${status}"
