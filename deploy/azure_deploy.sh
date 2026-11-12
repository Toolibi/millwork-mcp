#!/usr/bin/env bash
# Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
#
# Deploy millwork-mcp to Azure Container Apps from Azure Cloud Shell (bash).
# Run from the project folder:   bash deploy/azure_deploy.sh
#
# What it does
#   1. Creates a resource group, a Container Apps environment and a container registry
#      (the image is built in Azure - no Docker needed on your machine).
#   2. Generates a strong random API key, stores it as a Container Apps SECRET, and saves
#      a copy to ~/millwork-mcp-api-key.txt (readable only by you). The key is never
#      printed to the screen.
#   3. Scales to zero when idle (0-2 replicas, 0.5 vCPU / 1 GiB) to stay inside the
#      Container Apps free monthly grant.
#   4. Pins the server to its own hostname and runs a smoke test:
#      health check, request without key (must be 401), tools/list with the key.
set -euo pipefail

RG="${RG:-millwork-rg}"
LOCATION="${LOCATION:-eastus2}"
APP="${APP:-millwork-mcp}"
ENV_NAME="${ENV_NAME:-millwork-env}"
KEY_FILE="$HOME/millwork-mcp-api-key.txt"

say() { printf '\n==> %s\n' "$*"; }

say "Checking Azure sign-in"
az account show --query "{subscription:name, id:id}" -o table

say "Installing the Container Apps CLI extension and registering providers"
az extension add --name containerapp --upgrade --only-show-errors -y >/dev/null
for p in Microsoft.App Microsoft.OperationalInsights Microsoft.ContainerRegistry; do
  az provider register --namespace "$p" --wait --only-show-errors
done

say "Creating resource group $RG in $LOCATION"
az group create --name "$RG" --location "$LOCATION" --only-show-errors -o none

if [[ -s "$KEY_FILE" ]]; then
  say "Reusing the existing API key in $KEY_FILE"
  KEY="$(cat "$KEY_FILE")"
else
  say "Generating a new API key (saved to $KEY_FILE, not shown)"
  umask 077
  KEY="$(openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-48)"
  printf '%s' "$KEY" > "$KEY_FILE"
fi

say "Building the image in Azure and deploying (takes a few minutes)"
# The first revision starts without a key and exits on purpose (the server refuses to run
# unauthenticated); the next step adds the key and it comes up healthy.
az containerapp up \
  --name "$APP" --resource-group "$RG" --location "$LOCATION" \
  --environment "$ENV_NAME" --source . \
  --ingress external --target-port 8000 --only-show-errors -o none

FQDN="$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"

say "Storing the key as a secret and locking the app down"
az containerapp secret set -n "$APP" -g "$RG" \
  --secrets "millwork-api-key=$KEY" --only-show-errors -o none
az containerapp update -n "$APP" -g "$RG" \
  --set-env-vars "MILLWORK_API_KEY=secretref:millwork-api-key" "MILLWORK_ALLOWED_HOSTS=$FQDN" \
  --min-replicas 0 --max-replicas 2 --cpu 0.5 --memory 1.0Gi \
  --only-show-errors -o none
az containerapp ingress update -n "$APP" -g "$RG" --allow-insecure false --only-show-errors -o none

say "Smoke test (the first call may take ~20 s while the app wakes from zero)"
URL="https://$FQDN"
for i in $(seq 1 30); do
  if curl -fsS -m 10 "$URL/healthz" >/dev/null 2>&1; then break; fi
  sleep 5
done
echo "health:            $(curl -s -m 20 "$URL/healthz")"
echo "without key:       HTTP $(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST "$URL/mcp" \
      -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' -d '{}') (must be 401)"
TOOLS="$(curl -s -m 30 -X POST "$URL/mcp" \
      -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
      -H "authorization: Bearer $KEY" \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
      | python3 -c 'import sys,json; print(", ".join(t["name"] for t in json.load(sys.stdin)["result"]["tools"]))')"
echo "tools with key:    $TOOLS"

cat <<DONE

Deployed.
  MCP server URL :  $URL/mcp
  API key        :  saved in $KEY_FILE   (run:  cat $KEY_FILE   - then store it in a password manager)

Next: add it to your Foundry agent as an MCP tool - see docs/AZURE_FOUNDRY.md.
To remove everything later:  az group delete -n $RG --yes
DONE
