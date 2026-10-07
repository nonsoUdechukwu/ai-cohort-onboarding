#!/usr/bin/env bash
# Create an Entra app registration that GitHub Actions uses to deploy via OIDC (no secrets).
#
# Usage (signed in to the tenant that owns the Azure subscription):
#   ./scripts/setup-github-oidc.sh <github-owner/repo> <resource-group> [branch=main]
#
# Prints the values to store as GitHub repository *variables*:
#   AZURE_CLIENT_ID, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID
set -euo pipefail

REPO="${1:?github owner/repo}"
RG="${2:?resource group containing the Web App}"
BRANCH="${3:-main}"
NAME="gh-deploy-${REPO//\//-}"

SUB_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)

APP_ID=$(az ad app list --display-name "$NAME" --query "[0].appId" -o tsv)
if [[ -z "$APP_ID" ]]; then
  APP_ID=$(az ad app create --display-name "$NAME" --query appId -o tsv)
  az ad sp create --id "$APP_ID" --output none
fi

add_fic() {
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"$1\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$2\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" --output none 2>/dev/null || echo "(federated credential $1 already exists)"
}
add_fic "github-${BRANCH//\//-}" "repo:${REPO}:ref:refs/heads/${BRANCH}"

# GitHub may issue subjects with immutable owner/repo IDs (repo:owner@<id>/repo@<id>:...).
if command -v gh >/dev/null 2>&1 && IDS=$(gh api "repos/${REPO}" --jq '"\(.owner.id) \(.id)"' 2>/dev/null); then
  read -r OWNER_ID REPO_ID <<<"$IDS"
  add_fic "github-${BRANCH//\//-}-immutable" "repo:${REPO%%/*}@${OWNER_ID}/${REPO#*/}@${REPO_ID}:ref:refs/heads/${BRANCH}"
fi

# Website Contributor on the resource group is enough for zip deploy.
az role assignment create --assignee "$APP_ID" --role "Website Contributor" \
  --scope "/subscriptions/$SUB_ID/resourceGroups/$RG" --output none

cat <<EOF
Set these GitHub repository variables (Settings > Secrets and variables > Actions > Variables):
  AZURE_CLIENT_ID=$APP_ID
  AZURE_TENANT_ID=$TENANT_ID
  AZURE_SUBSCRIPTION_ID=$SUB_ID
  AZURE_WEBAPP_NAME=<your web app name>
EOF
