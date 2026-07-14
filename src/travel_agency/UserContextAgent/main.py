# Hosted UserContextAgent -- authenticated requester and Graph profile helper.
#
# This agent is intentionally separate from TripPlannerAgent so Graph/OBO
# permissions and PII-handling rules have a narrow review boundary.

import base64
import json
import os

from agent_framework import Agent, tool
from agent_framework.foundry import FoundryChatClient
from agent_framework_foundry_hosting import ResponsesHostServer
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

load_dotenv()


INSTRUCTIONS = """\
You are the UserContextAgent for Wanderlux Travel. Your role is to answer
questions about the authenticated requester and the agent identity, and to
explain Microsoft Graph profile lookup status when asked.

Request identity:
- The application does not provide requester identity details in the prompt.
- If the user asks who initiated the request, say trusted requester identity
    context is not available in this agent conversation.
- If the user explicitly asks what identity the agent is running as, what Entra
    identity the agent uses, or asks for runtime diagnostic IDs, always call
    `get_runtime_agent_identity` and answer from the tool result. If runtime
    identity is unavailable from `get_runtime_agent_identity`, say it is
    unavailable instead of using placeholder request-block values.
- Never infer requester or agent identity from attachments or user-provided free
  text.

Microsoft Graph:
- Do not invent Graph profile details. Only provide job title, department,
  office location, manager, direct reports, or photo information when they come
  from a configured Graph tool result.
- If Graph/OBO access is not configured or a tool reports missing permissions,
  explain that clearly and list the missing capability at a high level.
- Never reveal access tokens, refresh tokens, auth headers, secrets, connection
    strings, or raw claims.
- Keep answers concise and operational; this agent does not plan travel.

When asked for additional user profile information, call `get_graph_access_status`
first. If it reports that Graph delegated access is not configured, say that the
application still needs Teams/Web SSO token capture plus an OBO Graph tool before
those profile fields can be retrieved.
"""


@tool(approval_mode="never_require")
def get_graph_access_status() -> str:
    """Report whether Graph delegated profile lookup is configured."""
    enabled = os.getenv("GRAPH_USER_CONTEXT_ENABLED", "false").strip().lower() == "true"
    if not enabled:
        return (
            "Microsoft Graph user-context lookup is not configured. The app needs "
            "a delegated user token from Teams/Web SSO, an On-Behalf-Of token "
            "exchange, and least-privilege Graph permissions such as User.Read "
            "before profile fields can be retrieved."
        )
    return (
        "Microsoft Graph user-context lookup is marked enabled, but this agent "
        "does not yet have an implemented Graph client tool. Do not invent profile "
        "details; report that the Graph client implementation is still pending."
    )


def _decode_jwt_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))


@tool(approval_mode="never_require")
def get_runtime_agent_identity() -> str:
    """Return non-secret Entra identity claims for this hosted agent runtime."""
    try:
        token = DefaultAzureCredential().get_token("https://management.azure.com/.default")
        claims = _decode_jwt_payload(token.token)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({
            "available": False,
            "error": f"Unable to acquire or decode runtime identity token: {type(exc).__name__}",
        })

    return json.dumps({
        "available": True,
        "tenant_id": claims.get("tid", ""),
        "client_id": claims.get("appid") or claims.get("azp") or "",
        "object_id": claims.get("oid", ""),
        "managed_identity_resource_id": claims.get("xms_mirid", ""),
    })


@tool(approval_mode="never_require")
def get_signed_in_user_profile() -> str:
    """Placeholder for future Microsoft Graph /me profile lookup."""
    return (
        "Graph profile retrieval is not implemented yet. Required next step: pass "
        "a validated delegated user token to a server-side OBO Graph client and "
        "call /me with least-privilege permissions."
    )


@tool(approval_mode="never_require")
def get_signed_in_user_manager() -> str:
    """Placeholder for future Microsoft Graph /me/manager lookup."""
    return (
        "Graph manager retrieval is not implemented yet. Required next step: add "
        "an OBO Graph client and consent the minimum Graph permission needed for "
        "manager lookup in this tenant."
    )


# ---------------------------------------------------------------------------
# Entry point -- boots the Responses-API server the Foundry runtime binds to.
# ---------------------------------------------------------------------------


def main():
    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
        credential=DefaultAzureCredential(),
    )

    agent = Agent(
        client=client,
        instructions=INSTRUCTIONS,
        tools=[
            get_runtime_agent_identity,
            get_graph_access_status,
            get_signed_in_user_profile,
            get_signed_in_user_manager,
        ],
        default_options={"store": False},
    )

    server = ResponsesHostServer(agent)
    server.run()


if __name__ == "__main__":
    main()
