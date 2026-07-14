# UserContextAgent

Hosted Foundry agent for authenticated requester identity and future Microsoft Graph profile lookup.

This agent is intentionally separate from `TripPlannerAgent` so Microsoft Graph permissions, OBO token handling, PII logging, and profile lookup behavior can be reviewed independently from travel planning.

## Current behavior

- Answers requester identity questions by explaining that trusted requester identity context is unavailable unless the host provides it through a supported channel.
- Uses the `PIIRedactionGuardrail` policy associated with the hosted agent definition; the app does not perform pre-agent regex redaction.
- Reports Graph profile lookup as not configured until a server-side OBO Graph client is implemented.
- Does not infer identity from user text or attachments.
- Does not reveal tokens, auth headers, secrets, or raw claims.
- May disclose non-personal hosted agent runtime identifiers, such as tenant ID and client ID, when directly asked what identity the agent is running as.

## Deploy

From the repository root:

```powershell
azd deploy UserContextAgent
```

`requirements.txt` is the remote-build dependency set. Do not add `agent-dev-cli` there unless its dependency range is compatible with `agent-framework-foundry-hosting`; current prerelease builds pull conflicting `agent-framework-core` ranges and cause hosted deployment resolution failures.

## Test prompt

```text
Tell me what Microsoft Graph knows about the signed-in user: display name, email, job title, department, office location, and manager. Use only the authenticated request context. If Graph access is not configured, explain what token or permission is missing.
```
