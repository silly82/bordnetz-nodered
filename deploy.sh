#!/bin/zsh
# Deploys the "Bordnetz API" tab to Node-RED on a Victron GX device from a Mac or Linux machine (creates it, or
# replaces it in place). The other way is importing bordnetz-flow.json in the Node-RED editor (README.md).
#
# Usage: ./deploy.sh
#   Settings come from the environment, or from local.env next to this script (not committed, see local.env.example):
#   NODE_RED_IP     address of the GX device (required)
#   NODE_RED_CA     its Node-RED certificate (PEM) to pin; without it the certificate is not checked (-k)
#   BACKUP_DIR      default ~/Documents/nodered-backups (all flows are saved there before deploying)
#   IOS_API_TOKEN, BATTERY_SERVICE, … SOC_LOW, FRIDGE_MAX: the tab's environment variables (build_flow.py, SETTINGS).
#                   A variable not given keeps its value on the GX device.
#
# Goes through POST /flows like the editor (deployment type "flows": only changed tabs restart). The per-tab API
# (PUT /flow/:id) does not store tab env variables of type "cred" (tested 2026-10-03, Node-RED 5.0.7).
# Node-RED's admin API must be reachable without login (Venus OS default) or with NODE_RED_BEARER set.
set -euo pipefail

cd "$(dirname "$0")"
[[ -f local.env ]] && { set -a; . ./local.env; set +a; }
IP=${NODE_RED_IP:?NODE_RED_IP missing (local.env or environment)}
BACKUP_DIR=${BACKUP_DIR:-$HOME/Documents/nodered-backups}
BACKUP_DIR=${BACKUP_DIR/#\~/$HOME}
BASE=https://venus.local:1881
TLS=(-k)
[[ -n "${NODE_RED_CA:-}" ]] && TLS=(--cacert "${NODE_RED_CA/#\~/$HOME}")
AUTH=()
[[ -n "${NODE_RED_BEARER:-}" ]] && AUTH=(-H "Authorization: Bearer $NODE_RED_BEARER")
curl_nr() { curl -sS --fail-with-body "${TLS[@]}" "${AUTH[@]}" --resolve "venus.local:1881:$IP" -H 'Node-RED-API-Version: v2' "$@"; }

python3 build_flow.py >/dev/null
mkdir -p "$BACKUP_DIR"
CURRENT=$BACKUP_DIR/flows-$(date +%Y%m%d-%H%M%S)-before-bordnetz-deploy.json
(umask 077; curl_nr "$BASE/flows" > "$CURRENT")
echo "Backup: $CURRENT"

BODY=$(CURRENT=$CURRENT python3 -c '
import json, os, sys
sys.path.insert(0, ".")
from build_flow import SETTINGS, TAB_LABEL
current = json.load(open(os.environ["CURRENT"]))
new = json.load(open("bordnetz-flow.json"))
tab, nodes, configs = new[0], [n for n in new[1:] if "z" in n], [n for n in new[1:] if "z" not in n]
# The tab is the entry with this label and no "z"; "iOS API" is its name before 2026-10-12.
old = [n for n in current["flows"] if n.get("label") in (TAB_LABEL, "iOS API") and "z" not in n]
kept = {e["name"]: e.get("value", "") for o in old for e in o.get("env", [])}
if old:
    tab["id"] = old[0]["id"]   # same id: Node-RED keeps the flow context
for e in tab["env"]:
    given = os.environ.get(e["name"], "")
    e["value"] = given or kept.get(e["name"]) or e["value"]
if not next(e["value"] for e in tab["env"] if e["name"] == "IOS_API_TOKEN"):
    sys.exit("No IOS_API_TOKEN set and none on the GX device.")
for n in nodes:
    n["z"] = tab["id"]
gone = {o["id"] for o in old} | {c["id"] for c in configs}
keep = [n for n in current["flows"] if n["id"] not in gone and n.get("z") not in gone]
print(json.dumps({"rev": current["rev"], "flows": keep + [tab] + nodes + configs}))')

curl_nr -X POST -H 'Content-Type: application/json' -H 'Node-RED-Deployment-Type: flows' --data "$BODY" "$BASE/flows"
echo
