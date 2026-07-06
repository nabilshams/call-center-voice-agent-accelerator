// Azure Bot Service registration + Microsoft Teams channel for the
// TripPlanner Teams integration.
//
// The Entra ID application is created out-of-band (via `az ad app create`
// or the Bot Framework registration blade); we only take its client id as
// input. The bot messaging endpoint points at the existing container app
// so no second host is required.
//
// Preconditions:
//   - `microsoftAppId` is a valid Entra ID Application (client) id.
//   - The container app at `messagingEndpoint` accepts POST /api/messages
//     and speaks the Bot Framework activity protocol.

@description('Name for the Azure Bot Service resource.')
param botServiceName string

@description('Display name shown to Teams users.')
param botDisplayName string

@description('Public https URL that receives Bot Framework activities, e.g. https://<ca-fqdn>/api/messages.')
param messagingEndpoint string

@description('Entra ID application (client) id used by the bot for auth.')
param microsoftAppId string

@description('Tenant kind for the bot app registration. Must match the Entra ID app registration sign-in audience.')
@allowed([ 'MultiTenant', 'SingleTenant', 'UserAssignedMSI' ])
param microsoftAppType string = 'MultiTenant'

@description('Entra ID tenant id. Required when microsoftAppType is SingleTenant or UserAssignedMSI. Ignored for MultiTenant.')
param microsoftAppTenantId string = ''

@description('Azure region for the Bot Service resource. Bot Service is global; the API expects "global".')
param location string = 'global'

@description('Tags to apply.')
param tags object = {}

@description('SKU for the Bot Service (F0 = free / dev, S1 = paid).')
@allowed([ 'F0', 'S1' ])
param skuName string = 'F0'

@description('Application Insights instrumentation key. When set, wires the bot resource for Bot Analytics + Application Insights integration in the portal.')
param appInsightsInstrumentationKey string = ''

@description('Application Insights Application ID (visible in Portal -> App Insights -> Configure -> API Access). Required alongside the instrumentation key for the "View in Application Insights" link on the bot resource.')
param appInsightsAppId string = ''

// Base properties always present on the bot resource.
var baseProperties = {
  displayName: botDisplayName
  endpoint: messagingEndpoint
  msaAppId: microsoftAppId
  msaAppType: microsoftAppType
  publicNetworkAccess: 'Enabled'
  disableLocalAuth: false
}

// Tenant id only valid for SingleTenant / UserAssignedMSI apps; setting it
// on a MultiTenant app is rejected by the Bot Service API.
var tenantProperties = microsoftAppType == 'MultiTenant' ? {} : {
  msaAppTenantId: microsoftAppTenantId
}

// Application Insights wiring. Both fields are optional independently but
// only take effect together; setting one without the other is accepted but
// leaves the portal "View in Application Insights" link broken.
var appInsightsProperties = empty(appInsightsInstrumentationKey) ? {} : union(
  {
    developerAppInsightKey: appInsightsInstrumentationKey
  },
  empty(appInsightsAppId) ? {} : {
    developerAppInsightsApplicationId: appInsightsAppId
  }
)

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botServiceName
  location: location
  tags: tags
  kind: 'azurebot'
  sku: {
    name: skuName
  }
  properties: union(baseProperties, tenantProperties, appInsightsProperties)
}

// Enable the Microsoft Teams channel. Without this the bot is registered
// but not surfaced in Teams and manifest sideload will fail auth.
resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  location: location
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
    }
  }
}

// Explicitly declare the Web Chat channel + default site. Web Chat is
// enabled by default at bot creation, but redeclaring it here makes the
// site + key lifecycle idempotent across `azd provision` runs and gives
// operators a predictable place to enable the preview features.
// Fetch the site key from Portal -> Bot resource -> Channels -> Web Chat.
resource webChatChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'WebChatChannel'
  location: location
  properties: {
    channelName: 'WebChatChannel'
    properties: {
      sites: [
        {
          siteName: 'Default Site'
          isEnabled: true
          isWebchatPreviewEnabled: true
        }
      ]
    }
  }
}

output botServiceId string = bot.id
output botServiceName string = bot.name
