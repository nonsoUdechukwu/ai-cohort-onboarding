<#
.SYNOPSIS
  Grant the Web App's managed identity the Microsoft Graph application permissions
  User.Invite.All and GroupMember.ReadWrite.All.

.EXAMPLE
  Install-Module Microsoft.Graph.Applications -Scope CurrentUser
  ./scripts/grant-graph-permissions.ps1 -TenantId <tenant-id> -ManagedIdentityPrincipalId <principal-id>
#>
param(
    [Parameter(Mandatory)] [string] $TenantId,
    [Parameter(Mandatory)] [string] $ManagedIdentityPrincipalId,
    [string[]] $Roles = @('User.Invite.All', 'GroupMember.ReadWrite.All')
)

$ErrorActionPreference = 'Stop'
Import-Module Microsoft.Graph.Applications

Connect-MgGraph -TenantId $TenantId -Scopes 'AppRoleAssignment.ReadWrite.All', 'Application.Read.All' -NoWelcome

$graphSp = Get-MgServicePrincipal -Filter "appId eq '00000003-0000-0000-c000-000000000000'"
$existing = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $ManagedIdentityPrincipalId -All |
    Where-Object { $_.ResourceId -eq $graphSp.Id } | ForEach-Object { $_.AppRoleId }

foreach ($role in $Roles) {
    $appRole = $graphSp.AppRoles | Where-Object { $_.Value -eq $role -and $_.AllowedMemberTypes -contains 'Application' }
    if (-not $appRole) { throw "App role $role not found on Microsoft Graph" }
    if ($existing -contains $appRole.Id) {
        Write-Host "= $role already granted"
        continue
    }
    New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $ManagedIdentityPrincipalId `
        -PrincipalId $ManagedIdentityPrincipalId -ResourceId $graphSp.Id -AppRoleId $appRole.Id | Out-Null
    Write-Host "+ granted $role"
}
