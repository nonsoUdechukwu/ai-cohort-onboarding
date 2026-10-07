// AI Cohort Onboarding Portal - Azure infrastructure
// Linux App Service plan (F1 Free), Python 3.12 Web App with system-assigned managed identity,
// Storage account + table for the submission log, and optional Easy Auth for /admin.
//
// NOTE: app settings are fully replaced on every deployment of this template. Always pass the
// secure parameters (accessCode, turnstileSecretKey, ...) again, or manage those settings
// outside Bicep (see README).

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Globally unique Web App name (becomes <name>.azurewebsites.net).')
param appName string

@description('App Service plan name.')
param planName string = '${appName}-plan'

@description('Globally unique storage account name (3-24 lowercase letters/numbers).')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('Table name for the submission log.')
param tableName string = 'submissions'

@description('Entra tenant ID of the training tenant (target of the invitations).')
param tenantId string = subscription().tenantId

@description('Object ID of the cohort security group.')
param groupId string = ''

@description('Where guests land after redeeming. Empty = https://portal.azure.com/<tenantId>.')
param inviteRedirectUrl string = ''

@description('Optional custom text in the invitation email.')
param inviteMessage string = 'Welcome to the AI training cohort!'

@description('Cohort access code students must enter.')
@secure()
param accessCode string = ''

@description('Cloudflare Turnstile site key (public).')
param turnstileSiteKey string = ''

@description('Cloudflare Turnstile secret key.')
@secure()
param turnstileSecretKey string = ''

@description('Comma-separated admin UPNs for /admin.')
param adminUpns string = ''

@description('Object ID of the admin group for /admin (requires groups claim in the Easy Auth app registration).')
param adminGroupId string = ''

@description('Maximum invitations per UTC day (0 = unlimited).')
param dailyInviteCap int = 100

@description('Optional: client ID of the Easy Auth app registration. Empty = configure Easy Auth later.')
param easyAuthClientId string = ''

@description('Optional: client secret of the Easy Auth app registration.')
@secure()
param easyAuthClientSecret string = ''

var redirectUrl = empty(inviteRedirectUrl) ? 'https://portal.azure.com/${tenantId}' : inviteRedirectUrl
var easyAuthEnabled = !empty(easyAuthClientId)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    accessTier: 'Hot'
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource table 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: tableName
}

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'F1'
    tier: 'Free'
  }
  properties: {
    reserved: true // required for Linux
  }
}

var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

var baseSettings = [
  { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
  { name: 'TENANT_ID', value: tenantId }
  { name: 'GROUP_ID', value: groupId }
  { name: 'ACCESS_CODE', value: accessCode }
  { name: 'INVITE_REDIRECT_URL', value: redirectUrl }
  { name: 'INVITE_MESSAGE', value: inviteMessage }
  { name: 'TURNSTILE_SITE_KEY', value: turnstileSiteKey }
  { name: 'TURNSTILE_SECRET_KEY', value: turnstileSecretKey }
  { name: 'STORAGE_CONNECTION_STRING', value: storageConnectionString }
  { name: 'TABLE_NAME', value: tableName }
  { name: 'ADMIN_UPNS', value: adminUpns }
  { name: 'ADMIN_GROUP_ID', value: adminGroupId }
  { name: 'DAILY_INVITE_CAP', value: string(dailyInviteCap) }
]
var authSettings = easyAuthEnabled ? [
  { name: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET', value: easyAuthClientSecret }
] : []

resource web 'Microsoft.Web/sites@2023-12-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn --config gunicorn.conf.py -k uvicorn.workers.UvicornWorker asgi:app'
      alwaysOn: false // not available on F1
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/healthz'
      appSettings: concat(baseSettings, authSettings)
    }
  }
}

// Easy Auth: allow anonymous access (the student form is public); the app itself redirects
// unauthenticated /admin requests to /.auth/login/aad and authorizes admins.
resource auth 'Microsoft.Web/sites/config@2023-12-01' = if (easyAuthEnabled) {
  parent: web
  name: 'authsettingsV2'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      requireAuthentication: false
      unauthenticatedClientAction: 'AllowAnonymous'
    }
    httpSettings: {
      requireHttps: true
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenantId}/v2.0'
          clientId: easyAuthClientId
          clientSecretSettingName: 'MICROSOFT_PROVIDER_AUTHENTICATION_SECRET'
        }
        validation: {
          allowedAudiences: [
            'api://${easyAuthClientId}'
            easyAuthClientId
          ]
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
  }
}

output webAppName string = web.name
output webAppUrl string = 'https://${web.properties.defaultHostName}'
output managedIdentityPrincipalId string = web.identity.principalId
output storageAccount string = storage.name
