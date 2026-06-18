param environmentName string
param uniqueSuffix string
param identityId string
param tags object
param disableLocalAuth bool = false  // Enable local auth for Speech SDK compatibility

// Voice live api only supported on two regions now 
var location string = 'swedencentral'
var aiServicesName string = 'aiServices-${environmentName}-${uniqueSuffix}'

@allowed([
  'S0'
])
param sku string = 'S0'

resource aiServices 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: aiServicesName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  sku: {
    name: sku
  }
  kind: 'AIServices'
  tags: tags
  properties: {
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
    disableLocalAuth: disableLocalAuth
    customSubDomainName: 'domain-${environmentName}-${uniqueSuffix}' 
  }
}

// Chat completions deployment used by the persona-inference service in the
// live transcription flow. Voice Live itself does not require a deployment,
// but server-side LLM calls (chat/completions) do.
var chatDeploymentName string = 'gpt-4o-mini'
resource chatDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: aiServices
  name: chatDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 50
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o-mini'
      version: '2024-07-18'
    }
  }
}

@secure()
output aiServicesEndpoint string = aiServices.properties.endpoint
output aiServicesId string = aiServices.id
output aiServicesName string = aiServices.name
output aiServicesLocation string = aiServices.location
output chatDeploymentName string = chatDeployment.name
// AI Foundry unified endpoint (services.ai.azure.com) derived from the custom subdomain
output aiFoundryEndpoint string = 'https://${aiServices.properties.customSubDomainName}.services.ai.azure.com'
