#!/bin/sh
# Operational startup guard; no model, application, or persistent-data changes.
# Decision: .agents/notes/platform/20261014-wait-for-tailnet-colima-forwarding.md
set -eu

: "${PERSONAWEAVE_BIND_ADDRESS:?Set the deployment bind address}"
DEPLOYMENT=${PERSONAWEAVE_DEPLOYMENT_DIR:-"$HOME/services/personaweave"}
LIMA_CONFIG=${PERSONAWEAVE_LIMA_CONFIG:-"$HOME/.colima/_lima/colima/lima.yaml"}
CONTAINER=${PERSONAWEAVE_CONTAINER:-personaweave-personaweave-1}
IFCONFIG=${PERSONAWEAVE_IFCONFIG_BIN:-/sbin/ifconfig}
COLIMA=${PERSONAWEAVE_COLIMA_BIN:-/opt/homebrew/bin/colima}
DOCKER=${PERSONAWEAVE_DOCKER_BIN:-/opt/homebrew/bin/docker}
BREW=${PERSONAWEAVE_BREW_BIN:-/opt/homebrew/bin/brew}

# launchd retries on its interval; do not race the existing Homebrew startup.
if ! "$IFCONFIG" | awk -v address="$PERSONAWEAVE_BIND_ADDRESS" '
    $1 == "inet" && $2 == address { found = 1 }
    END { exit !found }
'; then
    exit 0
fi
if ! "$COLIMA" status >/dev/null 2>&1; then
    exit 0
fi
if [ ! -r "$LIMA_CONFIG" ]; then
    exit 0
fi

# Colima snapshots host addresses into these rules before its VM is ready.
if awk -v address="$PERSONAWEAVE_BIND_ADDRESS" '
    {
        line = $0
        sub(/^[[:space:]-]+/, "", line)
        split(line, fields, /[[:space:]]+/)
        if (fields[1] == "guestIP:" && fields[2] == address) guest = 1
        if (fields[1] == "hostIP:" && fields[2] == address) host = 1
    }
    END { exit !(guest && host) }
' "$LIMA_CONFIG"; then
    exit 0
fi

# Only restart the dedicated VM, and only while this deployment is idle.
if ! running=$("$DOCKER" --context colima ps --format '{{.Names}}'); then
    exit 0
fi
if [ "$running" != "$CONTAINER" ]; then
    printf '%s\n' 'Boot guard deferred: VM has other containers or is still starting.' >&2
    exit 0
fi
if ! "$DOCKER" --context colima exec "$CONTAINER" python -c '
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
    health = json.load(response)
inference = health["local_inference"]
raise SystemExit(0 if health["pipeline_ready"] and not inference["active"] and inference["queued"] == 0 else 1)
'; then
    exit 0
fi

STATE="$DEPLOYMENT/.boot-guard"
mkdir -p "$STATE"
last=0
if [ -r "$STATE/last-restart" ]; then
    last=$(cat "$STATE/last-restart")
    case "$last" in ''|*[!0-9]*) last=0 ;; esac
fi
now=$(date +%s)
if [ "$((now - last))" -lt 600 ]; then
    printf '%s\n' 'Boot guard deferred: restart cooldown is active.' >&2
    exit 0
fi
printf '%s\n' "$now" > "$STATE/last-restart"
printf '%s\n' 'Boot guard: bind address is ready but forwarding rules are missing; restarting Colima.' >&2
"$BREW" services restart colima
