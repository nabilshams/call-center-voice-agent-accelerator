param location string
param environmentName string
param uniqueSuffix string
param tags object
param exists bool
param identityId string
param identityClientId string
param containerRegistryName string
param aiServicesEndpoint string
param aiServicesResourceId string = ''
param modelDeploymentName string
@description('Name of the Azure OpenAI chat completions deployment used for server-side LLM calls (e.g. persona inference).')
param chatDeploymentName string = 'gpt-4o-mini'
param acsConnectionStringSecretUri string
param logAnalyticsWorkspaceName string
@description('The name of the container image')
param imageName string = ''
@description('The region for Azure Speech Service (same as AI Services)')
param speechRegion string = location
@description('Azure Storage Account name for transcriptions')
param storageAccountName string = ''
@description('Azure Storage Blob endpoint for transcriptions')
param storageBlobEndpoint string = ''
@description('Azure Storage container name for transcripts')
param transcriptsContainerName string = 'transcripts'

@description('Travel orchestrator mode: foundry | maf')
param travelOrchestratorMode string = 'maf'

@description('Enable Native MAF SDK for local orchestrator')
param mafNativeSdkEnabled string = 'true'

@description('MAF / AI Foundry project endpoint for native SDK')
param mafProjectEndpoint string = ''

@description('Model name used by native MAF agents')
param mafModel string = 'gpt-4o-mini'

@description('Resource ID of the subnet to use for Container Apps VNet integration. When empty, the environment is created without VNet integration.')
param infrastructureSubnetId string = ''

@description('Entra ID application (client) id for the Microsoft Teams bot. Leave empty to disable the Teams bridge.')
param microsoftAppId string = ''

@secure()
@description('Client secret for the Microsoft Teams bot Entra ID application. Required when microsoftAppId is set.')
param microsoftAppPassword string = ''

@description('Tenant kind for the Microsoft Teams bot app registration (MultiTenant recommended for Teams).')
param microsoftAppType string = 'MultiTenant'

@description('Entra ID tenant id for the Teams bot app. Required when microsoftAppType is SingleTenant or UserAssignedMSI.')
param microsoftAppTenantId string = ''

@description('Application Insights connection string. When non-empty, injected as APPLICATIONINSIGHTS_CONNECTION_STRING so the Python bot + orchestrator can push telemetry events.')
param appInsightsConnectionString string = ''

@description('Foundry prompt agent name the Teams bot fronts. Defaults to TripPlannerAgent; set to FlightBookingAgent, OrchestratorAgent, etc. to change routing without redeploying code.')
param teamsBotSpecialistAgent string = 'TripPlannerAgent'

@description('Optional welcome message the bot sends when a user first opens the chat. Leave empty to use the built-in TripPlanner default.')
param teamsBotWelcomeMessage string = ''

// Helper to sanitize environmentName for valid container app name
var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var containerAppName = take('ca-${sanitizedEnvName}-${uniqueSuffix}', 32)
var containerEnvName = take('cae-${sanitizedEnvName}-${uniqueSuffix}', 32)

// Teams bot env vars are only injected when the operator has supplied a
// bot app id; otherwise the container starts fine and /api/messages
// returns 503 with a diagnostic body.
var teamsBotEnv = empty(microsoftAppId) ? [] : concat(
  [
    {
      name: 'MICROSOFT_APP_ID'
      value: microsoftAppId
    }
    {
      name: 'MICROSOFT_APP_TYPE'
      value: microsoftAppType
    }
    {
      name: 'TEAMS_BOT_SPECIALIST_AGENT'
      value: teamsBotSpecialistAgent
    }
  ],
  empty(microsoftAppPassword) ? [] : [
    {
      name: 'MICROSOFT_APP_PASSWORD'
      secretRef: 'microsoft-app-password'
    }
  ],
  empty(microsoftAppTenantId) ? [] : [
    {
      name: 'MICROSOFT_APP_TENANT_ID'
      value: microsoftAppTenantId
    }
  ],
  empty(teamsBotWelcomeMessage) ? [] : [
    {
      name: 'TEAMS_BOT_WELCOME_MESSAGE'
      value: teamsBotWelcomeMessage
    }
  ]
)

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' existing = { name: logAnalyticsWorkspaceName }


module fetchLatestImage './fetch-container-image.bicep' = {
  name: '${containerAppName}-fetch-image'
  params: {
    exists: exists
    name: containerAppName
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: containerEnvName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsWorkspace.properties.customerId
        sharedKey: logAnalyticsWorkspace.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: empty(infrastructureSubnetId) ? null : {
      infrastructureSubnetId: infrastructureSubnetId
      internal: false
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: containerAppName
  location: location
  tags: union(tags, { 'azd-service-name': 'app' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: '${containerRegistryName}.azurecr.io'
          identity: identityId
        }
      ]
      secrets: concat(
        [
          {
            name: 'acs-connection-string'
            keyVaultUrl: acsConnectionStringSecretUri
            identity: identityId
          }
        ],
        // Teams bot secret is only mounted when both id + password are set;
        // Container Apps rejects secrets with empty values.
        empty(microsoftAppPassword) ? [] : [
          {
            name: 'microsoft-app-password'
            value: microsoftAppPassword
          }
        ]
      )
    }
    template: {
      containers: [
        {
          name: 'main'
          image: !empty(imageName) ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: concat([
            {
              name: 'AZURE_VOICE_LIVE_ENDPOINT'
              value: aiServicesEndpoint
            }
            {
              name: 'AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'AZURE_CLIENT_ID'
              value: identityClientId
            }
            {
              name: 'VOICE_LIVE_MODEL'
              value: modelDeploymentName
            }
            {
              name: 'ACS_CONNECTION_STRING'
              secretRef: 'acs-connection-string'
            }
            {
              name: 'AZURE_SPEECH_REGION'
              value: speechRegion
            }
            {
              name: 'AZURE_SPEECH_RESOURCE_ID'
              value: aiServicesResourceId
            }
            {
              name: 'DEBUG_MODE'
              value: 'true'
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_NAME'
              value: storageAccountName
            }
            {
              name: 'AZURE_STORAGE_BLOB_ENDPOINT'
              value: storageBlobEndpoint
            }
            {
              name: 'AZURE_TRANSCRIPTS_CONTAINER'
              value: transcriptsContainerName
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: aiServicesEndpoint
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: chatDeploymentName
            }
            {
              // Document Intelligence (prebuilt-read) endpoint used by the
              // attachment extractor for TripPlannerAgent. Same underlying
              // multi-service AIServices account as AZURE_OPENAI_ENDPOINT.
              name: 'AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT'
              value: aiServicesEndpoint
            }
            {
              name: 'TRAVEL_ORCHESTRATOR_MODE'
              value: travelOrchestratorMode
            }
            {
              name: 'MAF_NATIVE_SDK_ENABLED'
              value: mafNativeSdkEnabled
            }
            {
              name: 'MAF_PROJECT_ENDPOINT'
              value: mafProjectEndpoint
            }
            {
              name: 'MAF_MODEL'
              value: mafModel
            }
          ], teamsBotEnv, empty(appInsightsConnectionString) ? [] : [
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
          ])
          resources: {
            cpu: json('2.0')
            memory: '4.0Gi'
          }
        }
      ]
      // TODO add memory/cpu scaling
      scale: {
        minReplicas: 1
        maxReplicas: 10
      }
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppId string = containerApp.id
