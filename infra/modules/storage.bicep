@description('Location for all resources')
param location string

@description('Environment name used for naming')
param environmentName string

@description('Unique suffix for resource naming')
param uniqueSuffix string

@description('Tags for all resources')
param tags object

@description('Principal ID of the managed identity for role assignment')
param identityPrincipalId string

@description('Principal ID of the deploying user for data access')
param deployerPrincipalId string = ''

@description('Subnet resource ID where the storage private endpoint will be created. When provided, public network access is disabled.')
param privateEndpointSubnetId string = ''

@description('Resource ID of the private DNS zone for blob endpoints (privatelink.blob.<suffix>).')
param blobPrivateDnsZoneId string = ''

var usePrivateEndpoint = !empty(privateEndpointSubnetId) && !empty(blobPrivateDnsZoneId)

// Storage account name must be 3-24 characters, lowercase letters and numbers only
var sanitizedEnvName = toLower(replace(replace(replace(environmentName, '-', ''), '_', ''), ' ', ''))
var baseStorageName = 'st${sanitizedEnvName}${uniqueSuffix}'
var storageAccountName = length(baseStorageName) < 3 ? 'st${uniqueSuffix}' : take(baseStorageName, 24)

// Storage Account
resource storageAccount 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: usePrivateEndpoint ? 'Disabled' : 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: usePrivateEndpoint ? 'Deny' : 'Allow'
    }
  }
}

// Blob Services
resource blobServices 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

// Transcripts Container
resource transcriptsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobServices
  name: 'transcripts'
  properties: {
    publicAccess: 'None'
    metadata: {
      description: 'Container for storing call transcriptions'
    }
  }
}

// Storage Blob Data Contributor role definition ID
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// Role assignment for the managed identity to access blob storage
resource storageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, identityPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignment for the deploying user to access blob storage data
// Only create if deployerPrincipalId is provided and different from managed identity
resource deployerStorageRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId) && deployerPrincipalId != identityPrincipalId) {
  name: guid(storageAccount.id, deployerPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// Outputs
output storageAccountName string = storageAccount.name
output storageAccountId string = storageAccount.id
output primaryBlobEndpoint string = storageAccount.properties.primaryEndpoints.blob
output transcriptsContainerName string = transcriptsContainer.name

// ---------------------------------------------------------------------------
// Private endpoint for Blob service (only created when subnet + DNS provided)
// ---------------------------------------------------------------------------
var blobPrivateEndpointName = take('pe-${storageAccountName}-blob', 80)

resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-01-01' = if (usePrivateEndpoint) {
  name: blobPrivateEndpointName
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${blobPrivateEndpointName}-conn'
        properties: {
          privateLinkServiceId: storageAccount.id
          groupIds: [
            'blob'
          ]
        }
      }
    ]
  }
}

resource blobPrivateEndpointDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = if (usePrivateEndpoint) {
  parent: blobPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: blobPrivateDnsZoneId
        }
      }
    ]
  }
}
