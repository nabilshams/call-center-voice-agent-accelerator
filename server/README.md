# Overview
This project is managed using `pyproject.toml` and the [`uv`](https://github.com/astral-sh/uv) package manager for fast Python dependency management.

## 1. Test with Web Client

### Set Up Environment Variables
Based on .env-sample.txt, create and construct your .env file to allow your local app to access your Azure resource.

### Run the App Locally
1. Run the local server:

    ```shell
    uv run server.py
    ```

3. Once the app is running, open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser (or click the printed URL in the terminal).

4. On the page, click **Start** to begin speaking with the agent using your browser’s microphone and speaker.

### Run with Docker (Alternative)

If you prefer Docker or are running in GitHub Codespaces:

1. Build the image:

    ```
    docker build -t voiceagent .
    ```

2. Run the image with local environment variables:

    ```
    docker run --env-file .env -p 8000:8000 -it voiceagent
    ```
3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000) and click **Start** to interact with the agent.

## 2. Test with ACS Client (Phone Call)

To test Azure Communication Services (ACS) locally, we’ll expose the local server using **Azure DevTunnels**.

> DevTunnels allow public HTTP/S access to your local environment — ideal for webhook testing.

1. [Install Azure Dev CLI](https://learn.microsoft.com/azure/developer/dev-tunnels/overview) if not already installed.

2. Log in and create a tunnel:

    ```bash
    devtunnel login
    devtunnel create --allow-anonymous
    devtunnel port create -p 8000
    devtunnel host
    ```

3. The final command will output a URL like:

    ```
    https://<your-tunnel>.devtunnels.ms:8000
    ```

4. Add this URL to your `.env` file under:

    ```
    ACS_DEV_TUNNEL=https://<your-tunnel>.devtunnels.ms:8000
    ```

### Set Up Incoming Call Event

1. Go to your **Communication Services** resource in the Azure Portal.
2. In the left menu, click **Events** → **+ Event Subscription**.
3. Use the following settings:
   - **Event type**: `IncomingCall`
   - **Endpoint type**: `Web Hook`
   - **Endpoint URL**:  
     ```
     https://<your-tunnel>.devtunnels.ms:8000/acs/incomingcall
     ```

> Ensure both your local Python server and DevTunnel are running before creating the subscription.

### Call the Agent

1. [Get a phone number](https://learn.microsoft.com/azure/communication-services/quickstarts/telephony/get-phone-number?tabs=windows&pivots=platform-azp-new) for your ACS resource if not already provisioned.
2. Call the number. Your call will route to your local agent.

## Recap

- Use the **web client** for fast local testing.
- Use **DevTunnel + ACS** to simulate phone calls and test telephony integration.
- Customize the `.env` file, system prompts, and runtime behavior to fit your use case.

## 3. Travel Orchestrator (Foundry / Microsoft Agent Framework + Local Fallback)

The travel support app can orchestrate requests through either Azure AI Foundry workflows or Microsoft Agent Framework workflows, while keeping a local orchestrator fallback for resiliency.

### Configuration

Set these values in `.env`:

- `TRAVEL_ORCHESTRATOR_MODE=foundry|local`
- `FOUNDRY_WORKFLOW_ENDPOINT=https://<your-project>.services.ai.azure.com`
- `FOUNDRY_WORKFLOW_PATH=api/agents/<workflow-or-agent-name>:invoke`
- `FOUNDRY_API_KEY=<optional-if-not-using-managed-identity>`
- `FOUNDRY_WORKFLOW_TIMEOUT_SECONDS=25`
- `MAF_WORKFLOW_ENDPOINT=https://<your-project>.services.ai.azure.com`
- `MAF_WORKFLOW_PATH=api/agents/<maf-workflow-or-agent-name>:invoke`
- `MAF_API_KEY=<optional-if-not-using-managed-identity>`
- `MAF_WORKFLOW_TIMEOUT_SECONDS=25`
- `MAF_NATIVE_SDK_ENABLED=true`
- `MAF_PROJECT_ENDPOINT=https://<your-foundry-service>.services.ai.azure.com/api/projects/<project-name>`
- `MAF_MODEL=gpt-4o-mini`

Use one mode value for interchangeable runtime switching:

- `TRAVEL_ORCHESTRATOR_MODE=foundry`
- `TRAVEL_ORCHESTRATOR_MODE=maf`
- `TRAVEL_ORCHESTRATOR_MODE=maf-local`
- `TRAVEL_ORCHESTRATOR_MODE=local`

### Behavior

- `foundry` mode: server calls Foundry workflow first, then falls back to local orchestrator if Foundry is unavailable.
- `maf` mode: server calls Microsoft Agent Framework workflow first, then falls back to local orchestrator if MAF is unavailable.
- `maf-local` mode: server uses Native MAF SDK agents in-process (`OrchestratorAgent -> If/Else -> SpecialistAgent`) with deterministic fallback if SDK/config is unavailable.
- `local` mode: server uses only local orchestrator.

### Native MAF SDK Notes

- Native local mode uses the Python `agent-framework` package and `FoundryChatClient`.
- If `agent-framework` is not installed or Native MAF configuration is incomplete, `maf-local` transparently falls back to deterministic branch routing to preserve availability.

The API endpoint is:

- `POST /travel/orchestrate`

Request body:

```json
{
    "message": "I need a flight and hotel for Paris next month",
    "context": {
        "origin": "Auckland"
    }
}
```

Response includes:

- `spoken_reply`
- `clarification_question`
- `selected_agents`
- `confidence`
- `next_step`
- `orchestrator_mode` (`foundry`, `maf`, `maf-local`, `local`, or `local-fallback`)

### Quick Local Test

```bash
curl -X POST http://127.0.0.1:8000/travel/orchestrate \
    -H "Content-Type: application/json" \
    -d '{"message":"I need a flight to Paris next month","context":{"origin":"Auckland"}}'
```

### UI Integration

The `/travel-support` page now sends user utterances to `/travel/orchestrate` and shows routing telemetry (selected agents, confidence, mode) in the workflow panel.

### Monitoring

The server logs orchestration mode, selected agents, and confidence for every request. In production, route application logs to Application Insights and query these orchestration events.
