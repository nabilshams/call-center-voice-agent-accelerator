param identityPrincipalId string
param aiServicesId string
param keyVaultName string

resource aiServicesResource 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: last(split(aiServicesId, '/'))
}

resource aiServicesRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: aiServicesResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource azureAiUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, '53ca6127-db72-4b80-b1b0-d745d6d5456d')
  scope: aiServicesResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '53ca6127-db72-4b80-b1b0-d745d6d5456d')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource aiAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
  scope: aiServicesResource
  properties: {
    principalId: identityPrincipalId
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'acdd72a7-3385-48ef-bd42-f606fba81ae7')
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services User -- required for Document Intelligence access via
// the multi-service AIServices account. Used by the attachment extractor
// (server/app/handler/attachment_extractor.py) to OCR uploaded PDFs / images
// for TripPlannerAgent.
resource cognitiveServicesUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServicesId, identityPrincipalId, 'a97b65f3-24c7-4388-baec-2e87135dc908')
  scope: aiServicesResource
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}


resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' existing = {
  name: keyVaultName
}

resource keyVaultRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identityPrincipalId, 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7')
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}
