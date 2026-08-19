#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/use_wsl_uv_env.sh"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 ]]; then
  printf 'usage: %s IMAGE_PATH\n' "$0" >&2
  exit 2
fi

cd "$project_root"
uv run python scripts/probe_local_gateway_analysis.py "$1"
