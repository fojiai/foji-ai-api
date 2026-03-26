"""
Internal API — called only by foji-worker (Lambda functions).

Authenticated via X-Internal-Key header (shared secret, not user JWT).
These endpoints are NOT exposed publicly and should be behind a VPC/security group
in production. The internal_api_key provides a software-layer fallback.

Current endpoints:
  POST /internal/whatsapp/chat  — synchronous chat for WhatsApp relay
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.services.agent_service import AgentService
from app.services.chat_history import ChatHistoryService
from app.services.file_context import FileContextService
from app.services.prompt_builder import PromptBuilder
from app.providers.router import ProviderRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["Internal"])


# ── Auth ─────────────────────────────────────────────────────────────────────

def verify_internal_key(x_internal_key: str | None = Header(default=None)) -> None:
    """Validates the shared internal API key."""
    if not x_internal_key or x_internal_key != get_settings().internal_api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid internal key")


# ── Schema ────────────────────────────────────────────────────────────────────

class WhatsAppChatRequest(BaseModel):
    agent_token: str
    session_id: str  # typically "wa:<phone_number>" — namespaced by caller
    message: str


class WhatsAppChatResponse(BaseModel):
    reply: str
    session_id: str


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/whatsapp/chat",
    response_model=WhatsAppChatResponse,
    dependencies=[Depends(verify_internal_key)],
    summary="Synchronous chat for WhatsApp relay",
    description=(
        "Called by foji-worker's WhatsApp Lambda handler. "
        "Returns the full assistant reply as a plain string (no streaming). "
        "History is loaded from DynamoDB and saved back after the response."
    ),
)
async def whatsapp_chat(
    body: WhatsAppChatRequest,
    db: AsyncSession = Depends(get_db),
) -> WhatsAppChatResponse:
    # 1. Load agent
    agent_svc = AgentService(db)
    agent = await agent_svc.get_by_token(body.agent_token)

    # 2. Load chat history
    history_svc = ChatHistoryService()
    history = await history_svc.load(body.session_id)

    # 3. Build file context
    file_ctx_svc = FileContextService()
    file_context = file_ctx_svc.build(agent)

    # 4. Build prompt
    system_prompt, messages = PromptBuilder().build(
        agent=agent,
        user_message=body.message,
        history=history,
        file_context=file_context,
    )

    # 5. Select provider and collect full response (no streaming for WA)
    provider = await ProviderRouter().select(db)
    logger.info(
        "WhatsApp chat: session=%s agent_id=%d provider=%s",
        body.session_id,
        agent.id,
        provider.provider_name,
    )

    chunks: list[str] = []
    async for chunk in provider.stream_chat(messages, system_prompt):
        chunks.append(chunk)
    reply = "".join(chunks).strip()

    if not reply:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider returned an empty response",
        )

    # 6. Save history (best-effort — never fail the request)
    try:
        await history_svc.save(
            session_id=body.session_id,
            user_message=body.message,
            assistant_message=reply,
            provider=provider.provider_name,
            agent_id=agent.id,
            company_id=agent.company_id,
        )
    except Exception:
        logger.warning("Failed to save WhatsApp chat history for session=%s", body.session_id)

    return WhatsAppChatResponse(reply=reply, session_id=body.session_id)
