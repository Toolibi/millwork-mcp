#!/usr/bin/env bash
# Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
#
# Deploy millwork-mcp to Azure App Service (Linux, Python 3.12) from Azure Cloud Shell.
# Run from the project folder:   bash deploy/azure_appservice.sh
#
# Why this path exists: Azure free-trial subscriptions block ACR Tasks
# ("TasksOperationsNotAllowed"), so `az containerapp up --source .` cannot build the image.
# App Service builds the Python code itself (Oryx) and needs no container registry.
# The F1 (free) tier has zero quota in many regions on a trial, so this script tries a
# list of regions and uses the first one that accepts an F1 plan.
#
# What it does
#   1. Finds a region with F1 quota and creates a Linux plan there.
#   2. Deploys the code; App Service installs requirements.txt during the build.
#   3. Generates (or reuses) a random API key in ~/millwork-mcp-api-key.txt (mode 600,
#      never printed) and stores it as an app setting (encrypted at rest).
#   4. HTTPS only, TLS 1.2+, FTP off, host pinned; then a smoke test.
set -euo pipefail

RG="${RG:-millwork-rg}"
REGIONS="${REGIONS:-westus3 eastus eastus2 centralus westus2 southcentralus}"
SKU="${SKU:-F1}"
KEY_FILE="$HOME/millwork-mcp-api-key.txt"
NAME_FILE="$HOME/millwork-app-name.txt"

say() { printf '\n==> %s\n' "$*"; }

az account show --query "{subscription:name}" -o table
az group create --name "$RG" --location "${REGIONS%% *}" -o none

if [[ -s "$NAME_FILE" ]]; then APP="$(cat "$NAME_FILE")"
else APP="millwork-mcp-$RANDOM$RANDOM"; printf '%s' "$APP" > "$NAME_FILE"; fi

PLAN=""
for L in $REGIONS; do
  if az appservice plan show -n "millwork-plan-$L" -g "$RG" -o none 2>/dev/null; then
    PLAN="millwork-plan-$L"; LOCATION="$L"; break
  fi
  say "Trying an $SKU plan in $L"
  if az appservice plan create -n "millwork-plan-$L" -g "$RG" -l "$L" --sku "$SKU" --is-linux -o none 2>/dev/null; then
    PLAN="millwork-plan-$L"; LOCATION="$L"; break
  fi
done
[[ -n "$PLAN" ]] || { echo "No region accepted an $SKU plan. Request App Service quota or set SKU=B1."; exit 1; }

say "Deploying $APP to $PLAN ($LOCATION) - the build takes a few minutes"
az webapp up -n "$APP" -g "$RG" -l "$LOCATION" -p "$PLAN" --runtime PYTHON:3.12 --sku "$SKU" --os-type Linux

if [[ -s "$KEY_FILE" ]]; then KEY="$(cat "$KEY_FILE")"
else
  umask 077
  KEY="$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-48)"
  printf '%s' "$KEY" > "$KEY_FILE"
fi
HOST="$APP.azurewebsites.net"

say "Configuring startup, key and hardening"
az webapp config set -n "$APP" -g "$RG" --startup-file "python -m millwork_mcp.http_server" \
  --ftps-state Disabled --min-tls-version 1.2 -o none
az webapp config appsettings set -n "$APP" -g "$RG" -o none --settings \
  "MILLWORK_API_KEY=$KEY" "MILLWORK_ALLOWED_HOSTS=$HOST" "WEBSITES_PORT=8000" "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
az webapp update -n "$APP" -g "$RG" --https-only true -o none
az webapp restart -n "$APP" -g "$RG"

say "Smoke test (first start can take a minute or two)"
URL="https://$HOST"
for i in $(seq 1 40); do
  if curl -fsS -m 15 "$URL/healthz" >/dev/null 2>&1; then break; fi
  sleep 6
done
echo "health:      $(curl -s -m 20 "$URL/healthz")"
echo "without key: HTTP $(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST "$URL/mcp" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' -d '{}') (must be 401)"
curl -s -m 60 -X POST "$URL/mcp" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -H "authorization: Bearer $KEY" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("tools:", ", ".join(t["name"] for t in d["result"]["tools"]))' \
  || echo "tools/list failed - check: az webapp log tail -n $APP -g $RG"

say "Done"
echo "MCP server URL : $URL/mcp"
echo "API key        : in $KEY_FILE (copy it into your password manager; Cloud Shell storage is not a vault)"
