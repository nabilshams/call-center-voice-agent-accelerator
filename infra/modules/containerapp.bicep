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

@description('Travel orchestrator mode: maf-local | foundry | maf | local')
param travelOrchestratorMode string = 'maf-local'

@description('Enable Native MAF SDK for local orchestrator')
param mafNativeSdkEnabled string = 'true'

@description('MAF / AI Foundry project endpoint for native SDK')
param mafProjectEndpoint string = ''

@description('Model name used by native MAF agents')
param mafModel string = 'gpt-4o-mini'

@description('Resource ID of the subnet to use for Container Apps VNet integration. When empty, the environment is created without VNet integration.')
param infrastructureSubnetId string = ''

// Helper to sanitize environmentName for valid container app name
var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var containerAppName = take('ca-${sanitizedEnvName}-${uniqueSuffix}', 32)
var containerEnvName = take('cae-${sanitizedEnvName}-${uniqueSuffix}', 32)

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
      secrets: [
        {
          name: 'acs-connection-string'
          keyVaultUrl: acsConnectionStringSecretUri
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'main'
          image: !empty(imageName) ? imageName : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: [
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
          ]
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
        rules: [
          {
            name: 'http-scaler'
            http: {
              metadata: {
                concurrentRequests: '100'
              }
            }
          }
        ]
      }
    }
  }
}

output containerAppFqdn string = containerApp.properties.configuration.ingress.fqdn
output containerAppId string = containerApp.id
