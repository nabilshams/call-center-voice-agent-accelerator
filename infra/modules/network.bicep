@description('Location for all networking resources')
param location string

@description('Environment name used for naming')
param environmentName string

@description('Unique suffix for resource naming')
param uniqueSuffix string

@description('Tags applied to networking resources')
param tags object = {}

@description('Address space for the virtual network')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Address prefix for the Container Apps environment subnet (must be /23 or larger for Consumption)')
param acaSubnetPrefix string = '10.20.0.0/23'

@description('Address prefix for the subnet hosting private endpoints')
param privateEndpointSubnetPrefix string = '10.20.2.0/24'

var sanitizedEnvName = toLower(replace(replace(replace(replace(environmentName, ' ', '-'), '--', '-'), '[^a-zA-Z0-9-]', ''), '_', '-'))
var vnetName = take('vnet-${sanitizedEnvName}-${uniqueSuffix}', 64)
var acaSubnetName = 'snet-aca'
var peSubnetName = 'snet-pe'

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        // Subnet delegated to Container Apps managed environments
        name: acaSubnetName
        properties: {
          addressPrefix: acaSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
      {
        // Subnet for private endpoints (storage, key vault, ACR, etc.)
        name: peSubnetName
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
          privateLinkServiceNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

// Private DNS zone for blob storage private endpoints
resource blobPrivateDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
  tags: tags
}

resource blobPrivateDnsZoneVnetLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobPrivateDnsZone
  name: '${vnetName}-blob-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}

output vnetId string = vnet.id
output vnetName string = vnet.name
output acaSubnetId string = '${vnet.id}/subnets/${acaSubnetName}'
output privateEndpointSubnetId string = '${vnet.id}/subnets/${peSubnetName}'
output blobPrivateDnsZoneId string = blobPrivateDnsZone.id
