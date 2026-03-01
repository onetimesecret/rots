#!/usr/bin/env bash
#
# Phase 2: Rename ots_containers → rots
#
# This script performs the bulk rename of the Python package from
# ots_containers to rots. It handles:
#   1. git mv of the source directory
#   2. Import rewrites in source and test files
#   3. String literal rewrites (mock patch targets, etc.)
#
# Run from the ots-containers repo root (deployments/containers/).
# Review the diff before committing.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== Phase 2: Rename ots_containers → rots ==="
echo "Working in: $REPO_ROOT"
echo ""

# --- Step 1: git mv the source directory ---
echo "[1/4] Moving src/ots_containers → src/rots"
git mv src/ots_containers src/rots

# --- Step 2: Rewrite imports in source files ---
echo "[2/4] Rewriting imports in src/rots/**/*.py"
find src/rots -name '*.py' -print0 | xargs -0 sed -i '' \
    -e 's/from ots_containers/from rots/g' \
    -e 's/import ots_containers/import rots/g'

# --- Step 3: Rewrite imports and mock targets in test files ---
echo "[3/4] Rewriting imports and mock targets in tests/**/*.py"
find tests -name '*.py' -print0 | xargs -0 sed -i '' \
    -e 's/from ots_containers/from rots/g' \
    -e 's/import ots_containers/import rots/g' \
    -e 's/"ots_containers\./"rots\./g' \
    -e "s/'ots_containers\./'rots\./g"

# --- Step 4: Rewrite string literals in source files (logging, module refs) ---
echo "[4/4] Rewriting string literals in src/rots/**/*.py"
find src/rots -name '*.py' -print0 | xargs -0 sed -i '' \
    -e 's/"ots_containers\./"rots\./g' \
    -e "s/'ots_containers\./'rots\./g" \
    -e 's/"ots_containers"/"rots"/g' \
    -e "s/'ots_containers'/'rots'/g"

echo ""
echo "Done. Next manual steps:"
echo "  1. Update src/rots/__init__.py: version('ots-containers') → version('rots')"
echo "  2. Update src/rots/cli.py: name='ots-containers' → name='rots', docstring, version print"
echo "  3. Update src/rots/__main__.py docstring"
echo "  4. Update pyproject.toml (name, entry point, build target, coverage)"
echo "  5. Update .github/workflows/ci.yml"
echo "  6. Update .pre-commit-config.yaml"
echo "  7. Update CLAUDE.md and README.md"
echo "  8. Verify: grep -r 'ots_containers' src/ tests/"
