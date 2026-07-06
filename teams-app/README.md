# Microsoft Teams integration for TripPlannerAgent

This folder contains everything needed to expose the hosted
`TripPlannerAgent` as a Microsoft Teams bot. The bot shares the existing
Container App — no second host is required. Attachments (PDF, images)
sent in Teams are extracted and re-fed to the agent on every turn until
the user says `clear files`, mirroring the web UI's sticky behaviour.

## Architecture in one diagram

```
Teams client ── Bot Framework ──► Azure Bot Service ──► https://<ca-fqdn>/api/messages
                                                                │
                                                    (adapter + TripPlannerBot)
                                                                │
                                                MAFTravelOrchestrator.run_specialist(
                                                    "TripPlannerAgent", ..., attachments=[...])
                                                                │
                                       Foundry hosted TripPlannerAgent (responses API)
```

## One-time setup

You need three things: an Entra ID app registration, generated icons,
and the `azd` environment values set.

### 1. Register the Entra ID application

The Azure Bot Service resource created by Bicep needs an existing
application client id + secret. Bicep will not create the app for you
(subscription-level identity operations are intentionally kept manual).

```powershell
# Log in with an account that can create app registrations.
az login

# Create a multi-tenant app registration.
$app = az ad app create `
  --display-name "Wanderlux Trip Planner Bot" `
  --sign-in-audience AzureADMultipleOrgs `
  --query "{ appId: appId, id: id }" -o json | ConvertFrom-Json

# Create a client secret. Copy the value now -- it cannot be retrieved later.
$secret = az ad app credential reset --id $app.appId --years 1 --query "password" -o tsv

# Create the service principal (required for Bot Service authentication).
az ad sp create --id $app.appId | Out-Null

Write-Host "TEAMS_BOT_APP_ID       = $($app.appId)"
Write-Host "TEAMS_BOT_APP_PASSWORD = $secret"
```

### 2. Generate manifest icons

```powershell
cd teams-app/manifest
python generate_icons.py
```

This writes `color.png` (192×192) and `outline.png` (32×32) alongside
the manifest, using the Wanderlux brand color `#1F3A5F`.

### 3. Build the Teams app package

The checked-in manifest is a template. Keep the placeholders in source
control and let the packaging script render them into the zip file:

```powershell
cd teams-app/manifest
python package_manifest.py `
  --teams-app-id $app.appId `
  --bot-app-id $app.appId
```

This writes `teams-app/dist/wanderlux-tripplanner.zip` for sideloading.
Teams accepts the same UUID for both values:

* `TEAMS_APP_ID` — a UUID that identifies the Teams package itself.
* `TEAMS_BOT_APP_ID` — the Entra ID application id you just created.

### 4. Configure the `azd` environment

```powershell
azd env set TEAMS_BOT_APP_ID       "<TEAMS_BOT_APP_ID from step 1>"
azd env set TEAMS_BOT_APP_PASSWORD "<TEAMS_BOT_APP_PASSWORD from step 1>"
# Optional -- default is "Wanderlux Trip Planner"
azd env set TEAMS_BOT_DISPLAY_NAME "Wanderlux Trip Planner"
```

Leaving `TEAMS_BOT_APP_ID` unset skips the Bot Service module and the
`/api/messages` endpoint returns HTTP 503 — the rest of the accelerator
still deploys normally.

### 5. Deploy

```powershell
azd provision   # creates the Bot Service + Teams channel
azd deploy      # ships the updated container with the bot bridge
```

### 6. Sideload the manifest into Teams

In Teams: **Apps → Manage your apps → Upload an app → Upload a custom
app**, then pick `teams-app/dist/wanderlux-tripplanner.zip`.

## How attachments work

Teams sends three kinds of attachment payload; the bot handles the two
inline ones:

| Case | `contentType`                                          | Handled |
| ---- | ------------------------------------------------------ | ------- |
| A    | `application/vnd.microsoft.teams.file.download.info`   | ✅ Direct download URL, no auth. |
| B    | `image/*` (pasted or dragged image)                    | ✅ Fetched with a bot-service bearer token. |
| C    | SharePoint / OneDrive share link                       | ⚠️ Not supported in v1 — user is asked to attach the file directly. |

After extraction, attachments are stored per `conversation.id` and
re-fed to `TripPlannerAgent` on every subsequent turn. This means the
follow-up question *"any better hotels on the same street?"* still sees
the boarding-pass PDF sent three turns ago. TTL is 30 minutes; use
`clear files` (or `clear`, `/clear`) to purge on demand.

## Local development

Point Teams at your dev machine via the Bot Framework Emulator, or use
`ngrok` and update the Bot Service `messagingEndpoint` temporarily:

```powershell
ngrok http 8000
# Copy the https URL, then:
$rg = "rg-<envname>-<suffix>"
$bot = az bot show --resource-group $rg --name "<botServiceName>" --query name -o tsv
az bot update -g $rg -n $bot --endpoint "https://<ngrok-id>.ngrok.io/api/messages"
```

Remember to restore the Container App endpoint before your next `azd
deploy` (or just re-run `azd provision`, which re-asserts the correct
value from Bicep).

## Sanity checks

* `GET https://<ca-fqdn>/api/messages/health` → `{"configured": true, "app_id_set": true, "init_error": null}` when everything is wired up.
* Bot Framework emulator connects to `http://localhost:8000/api/messages` with app id + secret from step 1.
* First message in a fresh Teams chat should trigger the welcome text from `TripPlannerBot.on_members_added_activity`.
