"""
Internal API — called only by foji-worker (Lambda functions).

Authenticated via X-Internal-Key header (shared secret, not user JWT).
These endpoints are NOT exposed publicly and should be behind a VPC/security group
in production. The internal_api_key provides a software-layer fallback.

Current endpoints:
  POST /internal/whatsapp/chat  — synchronous chat for WhatsApp relay
"""

import logging
import secrets

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
    expected = get_settings().internal_api_key
    if not x_internal_key or not expected or not secrets.compare_digest(x_internal_key, expected):
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
    file_context = await file_ctx_svc.build(agent)

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


# ── CRM email drafting ─────────────────────────────────────────────────────────

class DraftEmailRequest(BaseModel):
    agent_token: str
    contact_name: str | None = None
    goal: str
    tone: str | None = None


class DraftEmailResponse(BaseModel):
    subject: str
    body: str


def _split_subject(text: str) -> tuple[str, str]:
    """Parse a 'Subject: ...' first line from the model output; return (subject, body)."""
    stripped = text.strip()
    lines = stripped.split("\n", 1)
    first = lines[0].strip()
    if first.lower().startswith("subject:"):
        subject = first[len("subject:"):].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        return subject or "Proposal", body or stripped
    return "Proposal", stripped


@router.post(
    "/crm/draft-email",
    response_model=DraftEmailResponse,
    dependencies=[Depends(verify_internal_key)],
    summary="Draft a CRM proposal / follow-up email",
    description="Generates a subject + body for a sales email. Called by FojiApi on behalf of the dashboard.",
)
async def draft_email(
    body: DraftEmailRequest,
    db: AsyncSession = Depends(get_db),
) -> DraftEmailResponse:
    # Validate the agent token (scopes the request to a real company/agent).
    agent_svc = AgentService(db)
    await agent_svc.get_by_token(body.agent_token)

    provider = await ProviderRouter().select(db)

    system_prompt = (
        "You write concise, warm, professional sales and follow-up emails for a business. "
        "Output ONLY the email, nothing else. The first line MUST be exactly 'Subject: <subject line>'. "
        "Then one blank line, then the email body in plain text (no markdown, no placeholders like [Name]). "
        "Keep it under 180 words. Use a generic sign-off; do not invent a sender name or company. "
        "Write in the same language as the goal described by the user."
    )
    user = f"Recipient name: {body.contact_name or 'the customer'}\nGoal of the email: {body.goal}"
    if body.tone:
        user += f"\nDesired tone: {body.tone}"

    chunks: list[str] = []
    async for chunk in provider.stream_chat([{"role": "user", "content": user}], system_prompt):
        chunks.append(chunk)
    text = "".join(chunks).strip()

    if not text:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Provider returned an empty draft",
        )

    subject, email_body = _split_subject(text)
    return DraftEmailResponse(subject=subject, body=email_body)
