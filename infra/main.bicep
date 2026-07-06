targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the the environment which is used to generate a short unique hash used in all resources.')
param environmentName string

@minLength(1)
@description('Primary location for all resources (filtered on available regions for Azure Open AI Service).')
@allowed([
  'eastus2'
  'swedencentral'
])
param location string

var abbrs = loadJsonContent('./abbreviations.json')
param useApplicationInsights bool = true
param useContainerRegistry bool = true
param appExists bool
@description('The OpenAI model name')
param modelName string = ' gpt-4o-mini'
@description('Id of the user or app to assign application roles. If ommited will be generated from the user assigned identity.')
param principalId string = ''

@description('Travel orchestrator mode injected into the container: foundry | maf')
param travelOrchestratorMode string = 'maf'

@description('AI Foundry project name used to build the MAF project endpoint (e.g. TravelAgency)')
param mafProjectName string = 'TravelAgency'

@description('Model name for native MAF agents')
param mafModel string = 'gpt-4o-mini'

// -------- Microsoft Teams bot integration (optional) --------
// Both values are supplied via `azd env set` after registering an Entra ID
// application. Leaving them empty deploys the accelerator as before and
// skips the Teams bridge entirely -- the Container App still serves the
// web UI + ACS voice paths.
@description('Entra ID application (client) id for the Teams bot. Leave empty to skip Teams integration.')
param teamsBotAppId string = ''

@secure()
@description('Entra ID application client secret for the Teams bot. Required when teamsBotAppId is set.')
param teamsBotAppPassword string = ''

@description('Display name shown to Teams users when they add the bot.')
param teamsBotDisplayName string = 'Wanderlux Trip Planner'

@description('Base name for the Azure Bot Service resource. Intentionally decoupled from environmentName so the bot keeps a stable, meaningful name even when reused across environments. Final name: bot-<teamsBotResourceName>-<uniqueSuffix>.')
param teamsBotResourceName string = 'wanderlux-tripplanner'

@description('Tenant kind for the Teams bot Entra ID app registration. Must match the sign-in audience used when the app was created.')
@allowed([ 'MultiTenant', 'SingleTenant', 'UserAssignedMSI' ])
param teamsBotAppType string = 'MultiTenant'

@description('Entra ID tenant id for the Teams bot app. Required when teamsBotAppType is SingleTenant or UserAssignedMSI. Ignored for MultiTenant.')
param teamsBotAppTenantId string = ''

@description('Foundry prompt agent that the Teams / Web Chat bot fronts. Defaults to TripPlannerAgent; set to FlightBookingAgent, OrchestratorAgent, etc. to change routing.')
param teamsBotSpecialistAgent string = 'TripPlannerAgent'

@description('Optional welcome message the bot sends on first turn. Leave empty to use the built-in default (Wanderlux Trip Planner). Override when fronting a non-TripPlanner agent.')
param teamsBotWelcomeMessage string = ''

var uniqueSuffix = substring(uniqueString(subscription().id, environmentName), 0, 5)
var tags = {'azd-env-name': environmentName }
var rgName = 'rg-${environmentName}-${uniqueSuffix}'

resource rg 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: rgName
  location: location
  tags: tags
}

// [ User Assigned Identity for App to avoid circular dependency ]
module appIdentity './modules/identity.bicep' = {
  name: 'uami'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
  }
}

// [ Virtual Network + private DNS for storage private endpoint ]
module network './modules/network.bicep' = {
  name: 'network'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var logAnalyticsName = take('log-${sanitizedEnvName}-${uniqueSuffix}', 63)
var appInsightsName = take('insights-${sanitizedEnvName}-${uniqueSuffix}', 63)
module monitoring 'modules/monitoring/monitor.bicep' = {
  name: 'monitor'
  scope: rg
  params: {
    logAnalyticsName: logAnalyticsName
    appInsightsName: appInsightsName
    tags: tags
  }
}

module registry 'modules/containerregistry.bicep' = {
  name: 'registry'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    identityName: appIdentity.outputs.name
    tags: tags
  }
  dependsOn: [ appIdentity ]
}


module aiServices 'modules/aiservices.bicep' = {
  name: 'ai-foundry-deployment'
  scope: rg
  params: {
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    identityId: appIdentity.outputs.identityId
    tags: tags
  }
  dependsOn: [ appIdentity ]
}

module acs 'modules/acs.bicep' = {
  name: 'acs-deployment'
  scope: rg
  params: {
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
  }
}

// Storage Account for transcriptions
module storage 'modules/storage.bicep' = {
  name: 'storage-deployment'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
    identityPrincipalId: appIdentity.outputs.principalId
    deployerPrincipalId: principalId
    privateEndpointSubnetId: network.outputs.privateEndpointSubnetId
    blobPrivateDnsZoneId: network.outputs.blobPrivateDnsZoneId
  }
  dependsOn: [ appIdentity, network ]
}

var keyVaultName = toLower(replace('kv-${environmentName}-${uniqueSuffix}', '_', '-'))
var sanitizedKeyVaultName = take(toLower(replace(replace(replace(replace(keyVaultName, '--', '-'), '_', '-'), '[^a-zA-Z0-9-]', ''), '-$', '')), 24)
module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault-deployment'
  scope: rg
  params: {
    location: location
    keyVaultName: sanitizedKeyVaultName
    tags: tags
    acsConnectionString: acs.outputs.acsConnectionString
  }
  dependsOn: [ appIdentity, acs ]
}

// Add role assignments 
module RoleAssignments 'modules/roleassignments.bicep' = {
  scope: rg
  name: 'role-assignments'
  params: {
    identityPrincipalId: appIdentity.outputs.principalId
    aiServicesId: aiServices.outputs.aiServicesId
    keyVaultName: sanitizedKeyVaultName
  }
  dependsOn: [ keyvault, appIdentity ] 
}

module containerapp 'modules/containerapp.bicep' = {
  name: 'containerapp-deployment'
  scope: rg
  params: {
    location: location
    environmentName: environmentName
    uniqueSuffix: uniqueSuffix
    tags: tags
    exists: appExists
    identityId: appIdentity.outputs.identityId
    identityClientId: appIdentity.outputs.clientId
    containerRegistryName: registry.outputs.name
    aiServicesEndpoint: aiServices.outputs.aiServicesEndpoint
    aiServicesResourceId: aiServices.outputs.aiServicesId
    modelDeploymentName: modelName
    acsConnectionStringSecretUri: keyvault.outputs.acsConnectionStringUri
    logAnalyticsWorkspaceName: logAnalyticsName
    imageName: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
    speechRegion: aiServices.outputs.aiServicesLocation
    chatDeploymentName: aiServices.outputs.chatDeploymentName
    storageAccountName: storage.outputs.storageAccountName
    storageBlobEndpoint: storage.outputs.primaryBlobEndpoint
    transcriptsContainerName: storage.outputs.transcriptsContainerName
    infrastructureSubnetId: network.outputs.acaSubnetId
    travelOrchestratorMode: travelOrchestratorMode
    mafNativeSdkEnabled: 'true'
    mafProjectEndpoint: '${aiServices.outputs.aiFoundryEndpoint}/api/projects/${mafProjectName}'
    mafModel: mafModel
    microsoftAppId: teamsBotAppId
    microsoftAppPassword: teamsBotAppPassword
    microsoftAppType: teamsBotAppType
    microsoftAppTenantId: teamsBotAppTenantId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    teamsBotSpecialistAgent: teamsBotSpecialistAgent
    teamsBotWelcomeMessage: teamsBotWelcomeMessage
  }
  dependsOn: [keyvault, RoleAssignments, storage, network]
}


// Azure Bot Service registration + Teams channel. Only deployed when the
// operator has already registered an Entra ID app and stashed the client id
// via `azd env set TEAMS_BOT_APP_ID`.
var botServiceName = take('${abbrs.botServiceBotServices}${teamsBotResourceName}-${uniqueSuffix}', 64)

module botservice 'modules/botservice.bicep' = if (!empty(teamsBotAppId)) {
  name: 'botservice-deployment'
  scope: rg
  params: {
    botServiceName: botServiceName
    botDisplayName: teamsBotDisplayName
    messagingEndpoint: 'https://${containerapp.outputs.containerAppFqdn}/api/messages'
    microsoftAppId: teamsBotAppId
    microsoftAppType: teamsBotAppType
    microsoftAppTenantId: teamsBotAppTenantId
    appInsightsInstrumentationKey: monitoring.outputs.appInsightsInstrumentationKey
    appInsightsAppId: monitoring.outputs.appInsightsAppId
    tags: tags
  }
  dependsOn: [ containerapp ]
}


// OUTPUTS will be saved in azd env for later use
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_USER_ASSIGNED_IDENTITY_ID string = appIdentity.outputs.identityId
output AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID string = appIdentity.outputs.clientId

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output SERVICE_API_ENDPOINTS array = ['${containerapp.outputs.containerAppFqdn}/acs/incomingcall']
output AZURE_VOICE_LIVE_ENDPOINT string = aiServices.outputs.aiServicesEndpoint
output AZURE_VOICE_LIVE_MODEL string = modelName

// Storage Account outputs for transcription storage
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.storageAccountName
output AZURE_STORAGE_BLOB_ENDPOINT string = storage.outputs.primaryBlobEndpoint
output AZURE_TRANSCRIPTS_CONTAINER string = storage.outputs.transcriptsContainerName
