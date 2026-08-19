#!/usr/bin/env bash

if [[ -r /proc/sys/kernel/osrelease ]] \
  && grep -qi microsoft /proc/sys/kernel/osrelease; then
  personaweave_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${personaweave_root}/.runtime/wsl-venv}"
  unset personaweave_root
fi
