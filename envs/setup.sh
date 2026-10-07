#!/usr/bin/env bash
# Create the re-run environments outside the repository.
# Usage: envs/setup.sh <target-dir>      e.g. envs/setup.sh ../envs.nosync
# Produces <target-dir>/pyenv (Python 3.11 venv via uv) and <target-dir>/Rlib (R package library).
set -euo pipefail
target="${1:?usage: envs/setup.sh <target-dir>}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$target"

if [ ! -x "$target/pyenv/bin/python" ]; then
  uv venv -p 3.11 "$target/pyenv"
fi
uv pip install -p "$target/pyenv/bin/python" -r "$here/requirements-rerun.txt"

Rscript "$here/r-packages.R" "$target/Rlib"
echo "environments ready in $target"
