#!/usr/bin/env bash
# Grant the Web App's managed identity the Microsoft Graph *application* permissions it needs:
#   - User.Invite.All            (create B2B guest invitations)
#   - GroupMember.ReadWrite.All  (add guests to the cohort group; also reads the group name)
#
# Run as a Global Administrator / Privileged Role Administrator of the training tenant:
#   az login --tenant <TENANT_ID> --allow-no-subscriptions
#   ./scripts/grant-graph-permissions.sh <managed-identity-principal-id>
#
# The principal ID is the `managedIdentityPrincipalId` output of infra/main.bicep, or:
#   az webapp identity show -g <rg> -n <app> --query principalId -o tsv
set -euo pipefail

MI_PRINCIPAL_ID="${1:?managed identity principal (object) id}"
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
ROLES=("User.Invite.All" "GroupMember.ReadWrite.All")

GRAPH_SP_ID=$(az ad sp show --id "$GRAPH_APP_ID" --query id -o tsv)
echo "Microsoft Graph service principal: $GRAPH_SP_ID"

existing=$(az rest --method GET \
  --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$MI_PRINCIPAL_ID/appRoleAssignments" \
  --query "value[?resourceId=='$GRAPH_SP_ID'].appRoleId" -o tsv)

for role in "${ROLES[@]}"; do
  role_id=$(az ad sp show --id "$GRAPH_APP_ID" \
    --query "appRoles[?value=='$role' && contains(allowedMemberTypes, 'Application')].id | [0]" -o tsv)
  if [[ -z "$role_id" ]]; then
    echo "Could not find app role $role" >&2
    exit 1
  fi
  if grep -q "$role_id" <<<"$existing"; then
    echo "= $role already granted"
    continue
  fi
  az rest --method POST \
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$MI_PRINCIPAL_ID/appRoleAssignments" \
    --headers "Content-Type=application/json" \
    --body "{\"principalId\":\"$MI_PRINCIPAL_ID\",\"resourceId\":\"$GRAPH_SP_ID\",\"appRoleId\":\"$role_id\"}" \
    --output none
  echo "+ granted $role"
done

echo "Done. Token changes can take a few minutes to apply; restart the Web App if invites still fail."
