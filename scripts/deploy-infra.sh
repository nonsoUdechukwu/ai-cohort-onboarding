#!/usr/bin/env bash
# Deploy infra/main.bicep into a resource group.
#
# Usage:
#   ACCESS_CODE=... TURNSTILE_SECRET_KEY=... ./scripts/deploy-infra.sh <resource-group> <location> <parameters.json>
#
# Secure values are read from env vars so they never land in a file or the shell history.
set -euo pipefail

RG="${1:?resource group}"
LOCATION="${2:?location, e.g. westeurope}"
PARAMS="${3:?parameters file, e.g. infra/main.parameters.local.json}"

az group create --name "$RG" --location "$LOCATION" --output none

az deployment group create \
  --resource-group "$RG" \
  --template-file "$(dirname "$0")/../infra/main.bicep" \
  --parameters "@$PARAMS" \
  --parameters accessCode="${ACCESS_CODE:-}" \
               turnstileSecretKey="${TURNSTILE_SECRET_KEY:-}" \
               easyAuthClientSecret="${EASY_AUTH_CLIENT_SECRET:-}" \
  --query "properties.outputs" --output json
